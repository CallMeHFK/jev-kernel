"""Judge transports and the cascade (mu docs/judges.md, protocol-faithful).

Three transports, same wire questions:

* ``typesafe`` — any service speaking TypeSafe's System One protocol:
  ``POST {model, state, questions}`` -> ``{answers}``. mu's baseUrl
  normalisation is kept: path ``/`` -> ``+/v1/systemone``, path ``/v1`` ->
  ``+/systemone``, anything else used as-is.
* ``http``     — any endpoint taking ``{state, questions}`` and returning
  ``{answers}`` (Cloudflare Workers AI's ``{result: ...}`` envelope is
  unwrapped).
* ``systemone`` — a raw System One server speaking the wire dialect as-is
  (ollama >= v0.35 decision models: nimble, tev1, clef, clef-flash at
  ``POST /v1/systemone``). Same body as ``typesafe`` but boolean questions
  go out as ``noul`` and answers come back translated: ``{"noul": p}`` ->
  ``{"probability": p}``, and a score's continuous expected value plus
  per-level ``probabilities`` -> the argmax level index, so the kernel's
  gateway-shaped verdicts need no change.
* ``llm``      — any OpenAI-compatible chat completions endpoint, prompted
  to answer as JSON. Slower and every decision costs tokens, but it needs
  nothing beyond a model you already have a key for.

``ask_cascade`` walks the point's judge list in order; each later judge
only sees the questions the earlier ones left uncertain (mu's tiers
semantics). A question nobody resolves gets the fallback verdict and the
ledger records why. Network I/O is blocking urllib run in a worker thread
so the agent loop never stalls on a judge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .config import load_config
from .questions import Question, Verdict, fallback_verdict, parse_verdict

logger = logging.getLogger("jev_kernel.judges")

LLM_SYSTEM_PROMPT = (
    "You are jev, a small fast judgment model. You answer bounded questions "
    "about a given state with calibrated probabilities. Reply with JSON only, "
    "no prose, no code fences, shaped exactly like: "
    '{"answers": {"<question key>": ...}} where a boolean answer is '
    '{"type": "boolean", "probability": <P(yes) in 0..1>}, a choice answer is '
    '{"type": "choice", "choice": "<one option>", "probabilities": {"<option>": <p>}}, '
    "and a score answer is "
    '{"type": "score", "score": <index into criteria>, "confidence": <0..1>}.'
)


def _systemone_url(base_url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)
    if parts.path in ("", "/"):
        return base_url.rstrip("/") + "/v1/systemone"
    if parts.path == "/v1":
        return base_url.rstrip("/") + "/systemone"
    return base_url


def _to_systemone_questions(questions: dict[str, Any]) -> dict[str, Any]:
    """Gateway question names -> raw System One names (boolean is `noul`)."""
    out: dict[str, Any] = {}
    for key, question in questions.items():
        if not isinstance(question, dict):
            out[key] = question
            continue
        translated = dict(question)
        if translated.get("type") == "boolean":
            translated["type"] = "noul"
        if translated.get("type") == "choice" and not isinstance(translated.get("criteria"), dict):
            # gateway wire carries an `options` list; raw System One wants a
            # {label: description} object, so label each option with itself.
            options = translated.pop("options", None)
            if isinstance(options, list) and options:
                translated["criteria"] = {str(option): str(option) for option in options}
        out[key] = translated
    return out


def _from_systemone_answers(answers: dict[str, Any], wire: dict[str, Any]) -> dict[str, Any]:
    """Raw System One answers -> gateway-shaped answers the parser expects."""
    out: dict[str, Any] = {}
    for key, answer in answers.items():
        if not isinstance(answer, dict):
            out[key] = answer
            continue
        kind = answer.get("type")
        if kind == "noul":
            probability = answer.get("noul", answer.get("probability"))
            out[key] = {"type": "boolean", "probability": probability}
            continue
        if kind == "score":
            spec = wire.get(key)
            criteria = spec.get("criteria") if isinstance(spec, dict) else None
            levels = len(criteria) if isinstance(criteria, list) and criteria else None
            index = None
            probs = answer.get("probabilities")
            if isinstance(probs, dict) and probs:
                parsed: list[tuple[int, float]] = []
                for level, value in probs.items():
                    try:
                        level_index = int(level)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        if levels is None or 0 <= level_index < levels:
                            parsed.append((level_index, value))
                if parsed:
                    index = max(parsed, key=lambda item: item[1])[0]
            if index is None and isinstance(answer.get("score"), (int, float)):
                index = int(round(float(answer["score"])))
                if levels is not None:
                    index = min(max(index, 0), levels - 1)
            if index is None:
                out[key] = answer
                continue
            confidence = answer.get("confidence")
            if not isinstance(confidence, (int, float)) and isinstance(probs, dict):
                confidence = probs.get(str(index))
            out[key] = {"type": "score", "score": index, "confidence": confidence}
            continue
        out[key] = answer
    return out


def _answers_of(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("answers"), dict):
        return payload["answers"]
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("answers"), dict):
        return result["answers"]
    if isinstance(result, dict):
        return result
    # bare answers map: {"q0": {"type": ...}, ...}
    if any(isinstance(v, dict) and "type" in v for v in payload.values()):
        return payload
    return {}


def _normalize_answers(payload: Any, wire: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Answers map, positional list mapping, or a shape diagnostic."""
    answers = _answers_of(payload)
    if not answers and isinstance(payload, dict):
        listed = payload.get("answers")
        if isinstance(listed, list):  # answers as an ordered list
            # strict=False on purpose: a judge may answer fewer questions than
            # asked (the rest cascade); extra entries have no question to bind.
            answers = dict(zip(wire.keys(), listed, strict=False))
        result = payload.get("result")
        if not answers and isinstance(result, dict) and isinstance(result.get("answers"), list):
            answers = dict(zip(wire.keys(), result["answers"], strict=False))
    if not answers and payload:
        answers = {"_parse_error": json.dumps(payload, ensure_ascii=False)[:200]}
    return answers


def _post_json(url: str, body: dict[str, Any], api_key: str, timeout_s: float) -> Any:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _judge_sync(
    spec: dict[str, Any],
    state: Any,
    questions: dict[str, dict[str, Any]],
    timeout_ms: int,
) -> dict[str, Any]:
    """One judge, one request. Returns the raw answers mapping."""
    jtype = spec.get("type", "http")
    api_key = spec.get("apiKey") or ""
    if not api_key and spec.get("apiKeyEnv"):
        api_key = os.environ.get(spec["apiKeyEnv"], "")
    timeout_s = max(0.25, (timeout_ms or 4000) / 1000.0)

    if jtype == "typesafe":
        url = _systemone_url(spec.get("baseUrl") or "")
        body = {"model": spec.get("model") or "jev-1.13", "state": state, "questions": questions}
        return _normalize_answers(_post_json(url, body, api_key, timeout_s), questions)

    if jtype == "systemone":
        url = _systemone_url(spec.get("baseUrl") or "")
        body = {
            "model": spec.get("model") or "clef-flash",
            "state": state,
            "questions": _to_systemone_questions(questions),
        }
        answers = _normalize_answers(_post_json(url, body, api_key, timeout_s), questions)
        return _from_systemone_answers(answers, questions)

    if jtype == "http":
        url = spec.get("baseUrl") or ""
        body = {"state": state, "questions": questions}
        return _normalize_answers(_post_json(url, body, api_key, timeout_s), questions)

    if jtype == "llm":
        base = spec.get("baseUrl") or os.environ.get("JEV_LLM_BASE_URL", "")
        base = base.rstrip("/")
        if not base.endswith("/chat/completions"):
            base = base + "/chat/completions"
        user = json.dumps(
            {"state": state, "questions": questions}, ensure_ascii=False, indent=1
        )
        body = {
            "model": spec.get("model") or os.environ.get("JEV_LLM_MODEL", "qwen-plus"),
            "temperature": 0,
            "messages": [
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        }
        extra = spec.get("extraBody")
        if isinstance(extra, dict):
            body.update(extra)
        payload = _post_json(base, body, api_key, timeout_s)
        if isinstance(payload, dict) and payload.get("error"):
            return {"_parse_error": json.dumps(payload)[:200]}
        choices = payload.get("choices") or []
        content: Any = ""
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if content in (None, ""):
                content = message.get("stringContent") or ""
        if isinstance(content, list):  # content-blocks shape
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        content = str(content or "").strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        try:
            parsed = json.loads(content.strip())
        except ValueError:
            return {"_parse_error": content[:200] or "<empty content>"}
        return _normalize_answers(parsed, questions)

    return {}


async def ask_cascade(
    point: str,
    state: Any,
    questions: list[Question],
    agent_config: Any = None,
) -> dict[str, Verdict]:
    """Ask every question through the point's judge cascade."""
    cfg = load_config()
    thresholds = cfg.get("thresholds") or {}
    default_timeout = int(cfg.get("timeoutMs") or 4000)

    routes = cfg.get("routes") or {}
    names = routes.get(point)
    if not isinstance(names, list) or not names:
        names = cfg.get("tiers") or []

    pending: dict[str, Question] = {f"q{i}": q for i, q in enumerate(questions)}
    results: dict[str, Verdict] = {}
    tried: list[str] = []
    unresolved: list[str] = []
    errors: list[str] = []

    for name in names:
        if not pending:
            break
        specs = resolve_specs(cfg, name, agent_config)
        if not specs:
            unresolved.append(str(name))
            continue
        for spec in specs:
            if not pending:
                break
            tried.append(spec["name"])
            wire = {key: q.as_dict() for key, q in pending.items()}
            timeout_ms = int(spec.get("timeoutMs") or default_timeout)
            started = time.monotonic()
            try:
                answers = await asyncio.to_thread(
                    _judge_sync, spec, state, wire, timeout_ms
                )
            except Exception as e:  # noqa: BLE001 - a dead judge must not wedge the agent
                err = f"{spec['name']}:error:{type(e).__name__}:{e}"[:240]
                errors.append(err)
                logger.warning("jev-kernel: judge %s failed for point %s: %s",
                               spec["name"], point, err)
                answers = {}
            latency = (time.monotonic() - started) * 1000.0

            still: dict[str, Question] = {}
            for key, question in pending.items():
                verdict = parse_verdict(question, answers.get(key), thresholds, spec["name"], latency)
                if verdict is None:
                    errors.append(
                        f"{spec['name']}:malformed:{json.dumps(answers.get(key))[:120]}"
                    )
                    still[key] = question
                elif verdict.uncertain:
                    errors.append(
                        f"{spec['name']}:uncertain:{verdict.value}@{verdict.probability:.2f}"
                    )
                    still[key] = question
                else:
                    results[key] = verdict
            pending = still

    for key, question in pending.items():
        parts = []
        if unresolved:
            parts.append("unresolved:" + ",".join(unresolved))
        if tried:
            parts.append("uncertain_or_timeout:" + ",".join(tried))
        if errors:
            parts.append("errors:" + " | ".join(errors)[:400])
        reason = ";".join(parts) if parts else "no_judge_configured"
        results[key] = fallback_verdict(question, reason)
    return results


def resolve_specs(cfg: dict[str, Any], name: str, agent_config: Any = None) -> list[dict[str, Any]]:
    from .config import resolve_judge_name

    return resolve_judge_name(cfg, name, agent_config)


async def ask_one(point: str, state: Any, question: Question, agent_config: Any = None) -> Verdict:
    verdicts = await ask_cascade(point, state, [question], agent_config)
    return verdicts["q0"]
