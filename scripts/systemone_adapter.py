#!/usr/bin/env python3
"""Gateway-dialect bridge for raw System One servers (ollama decision models).

Old jev-kernel modules (and mu's ``typesafe`` transport) speak the gateway
dialect: questions typed ``boolean`` with choice ``options`` lists. ollama's
``/v1/systemone`` (v0.35+, decision models nimble/tev1/clef/clef-flash) wants
``noul`` and a ``criteria`` object, and answers booleans as ``{"noul": p}``
and scores as a continuous expected value with per-level probabilities.

This loopback adapter translates both directions so any gateway-dialect
client can use a local decision model:

    POST <adapter>/v1/systemone  {"model", "state", "questions"}  (gateway dialect)
      -> forwards to $OLLAMA_SYSTEMONE_URL (default http://127.0.0.1:11434/v1/systemone)
      <- {"answers": ...} (gateway dialect)

Run: python3 systemone_adapter.py [--port 47824]
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("OLLAMA_SYSTEMONE_URL", "http://127.0.0.1:11434/v1/systemone")


def to_raw(questions: dict) -> dict:
    out = {}
    for key, question in questions.items():
        if not isinstance(question, dict):
            out[key] = question
            continue
        translated = dict(question)
        if translated.get("type") == "boolean":
            translated["type"] = "noul"
        if translated.get("type") == "choice" and not isinstance(translated.get("criteria"), dict):
            options = translated.pop("options", None)
            if isinstance(options, list) and options:
                translated["criteria"] = {str(o): str(o) for o in options}
        out[key] = translated
    return out


def from_raw(answers: dict, questions: dict) -> dict:
    out = {}
    for key, answer in answers.items():
        if not isinstance(answer, dict):
            out[key] = answer
            continue
        kind = answer.get("type")
        if kind == "noul":
            out[key] = {"type": "boolean", "probability": answer.get("noul", answer.get("probability"))}
            continue
        if kind == "score":
            spec = questions.get(key) or {}
            criteria = spec.get("criteria") if isinstance(spec, dict) else None
            levels = len(criteria) if isinstance(criteria, list) and criteria else None
            index = None
            probs = answer.get("probabilities")
            if isinstance(probs, dict) and probs:
                parsed = []
                for level, value in probs.items():
                    try:
                        level = int(level)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        if levels is None or 0 <= level < levels:
                            parsed.append((level, value))
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"status": "ok", "upstream": UPSTREAM})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        questions = payload.get("questions") or {}
        body = {
            "model": payload.get("model") or os.environ.get("OLLAMA_MODEL", "clef-flash:latest"),
            "state": payload.get("state"),
            "questions": to_raw(questions),
        }
        if payload.get("images"):
            body["images"] = payload["images"]
        req = urllib.request.Request(
            UPSTREAM,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=float(os.environ.get("ADAPTER_TIMEOUT", "120"))) as resp:
                upstream = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - surface upstream errors verbatim
            self._send({"error": f"{type(e).__name__}: {e}"}, 502)
            return
        self._send({"answers": from_raw(upstream.get("answers") or {}, questions),
                    "usage": upstream.get("usage")})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47824)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
