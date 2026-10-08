# Design notes

A port of [qybaihe/mu](https://github.com/qybaihe/mu)'s jev integration to
QwenPaw. Where mu documents a rule, this file records how and why it was
ported; where the host forced a deviation, it records the deviation.

## The seam

mu puts its decision points on the tool seam because prompt-level judgment
decays: rules in a system prompt compete with the tool list and lose. QwenPaw
exposes that seam as AgentScope middleware (`on_acting`), the same seam
dispatch-guard uses, so the kernel attaches there: one middleware instance,
two points, three modes. `active` may deny or escalate; `shadow` ledger-only;
`off` skips. Defaults are inert on purpose — a fresh install must not contact
any judge and must not change any behaviour until the operator opts in.

## Bounded questions, calibrated answers

Every question is one of three kinds (noul / choice / score) and every answer
carries a probability, because the artefact worth ledgering is not the verdict
but the calibration: `P(yes)=0.986` on a read-only call and `0.964` on
`rm -rf ~/Documents` is a curve you can audit, while a bare yes/no is a coin
flip you cannot. Thresholds (`yes`, `no`, `minProbability`, `risk`) are
configuration, not code: what counts as confident, and what counts as
uncertain, is an operator decision.

## The cascade

`tiers` (or a per-point `routes` override) is an ordered judge list. Each later
judge only sees the questions earlier ones left uncertain — mu's semantics —
so a cheap local decision model can answer the easy calls and the session's
own model catches the residue. A judge that errors, times out, or answers
malformedly is recorded (`errors:` / `uncertain_or_timeout:` in the fallback
reason) and the cascade continues; a question nobody resolves falls open with
its reason ledgered. Fail-open in `shadow` is obvious; fail-open in `active`
is the deliberate mu rule: a dead judge degrades the host to "no kernel",
never to "everything denied".

## Transports and dialects

mu's wire is `{model, state, questions}` → `{answers}` (System One). Three
transports port directly (`typesafe`, `http`, `llm`). The fourth,
`systemone`, exists because ollama's decision models (v0.35+: nimble, tev1,
clef, clef-flash) speak System One **raw**: booleans are `noul`, choice
criteria are `{label: description}` objects rather than `options` lists, and
scores come back as a continuous expected value with per-level probabilities
and a legend. The transport translates both directions — request:
`boolean → noul`, `options → criteria`; response: `noul → probability`,
expected value → argmax level index (clamped to the criteria length) — so the
kernel's verdict parsing stays gateway-shaped and one parser serves every
judge. `scripts/systemone_adapter.py` is the same translation as a loopback
HTTP bridge, for gateway-dialect clients that predate the transport (including
a hot-reloaded host still running an older kernel module).

## The session tier

mu's `mu setup --judge model` points the kernel at the session's own model.
QwenPaw exposes the running session's provider base URL and an in-memory
credential through its provider manager (OpenAI-compatible providers only), so
the `session` tier inherits them per call and sends `enable_thinking: false`:
a thinking model spends the judge latency budget thinking about thinking.
Keys are read from the provider manager's memory, never from disk, never
logged.

## Hot reload and stale modules

QwenPaw hot reload re-executes the plugin entry module but keeps the package's
cached sibling submodules in `sys.modules`, so `from . import judges` after a
reinstall binds the *previous* code — observed live as a new transport
silently returning empty answers (`malformed:null` in the ledger) while the
on-disk module was correct. `backend/main.py` therefore purges its own
package's submodules (never itself, never the package) before importing them.
New sessions get fresh code either way; long-lived sessions get it at the next
reload instead of the next restart.

## Ledger

One JSON line per judged question, single-generation rotation (mu's rule:
evidence, not a database). The line records the point, mode, tool, question,
verdict (kind, value, probability, uncertain, judge, latency) or the fallback
reason, and whether it was enforced — enough to replay the kernel's behaviour
against your own tool history, which is the only calibration test that
matters.

## What this kernel is not

Not a policy engine: two points, bounded questions, no rule language. Not a
sandbox: it judges calls, it does not confine them. Not a replacement for
human approval where policy requires it — `tool.approval` in `active` mode
escalates to the host's human flow instead of deciding alone.
