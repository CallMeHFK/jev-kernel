# Changelog

Newest first. The release workflow tags `vX.Y.Z` from `plugin.json` and uses
the matching section below as the release notes.

## v0.1.0

First public release: a mu-style judgment kernel on QwenPaw's tool seam.

- Decision points `tool.risk` and `tool.approval` on the AgentScope middleware
  seam, each with mu's three modes (`active` / `shadow` / `off`); defaults are
  inert (`shadow` / `off`, empty tiers — no judge contacted until configured).
- Bounded typed questions (noul / choice / score) answered with calibrated
  probabilities; thresholds turn them into verdicts and define "uncertain".
- Judge cascade over `tiers` / `routes`: later judges only see the questions
  earlier ones left uncertain; a question nobody resolves gets a fallback
  verdict whose reason is ledgered (a dead judge never wedges the agent and
  never silently passes as `no`).
- Four transports: `typesafe` (System One protocol), `http` (plain
  `{state, questions}` endpoints, Cloudflare envelope unwrapped), `llm` (any
  OpenAI-compatible chat model prompted to answer as JSON), and `systemone`
  (raw System One servers — ollama ≥ v0.35 decision models nimble / tev1 /
  clef / clef-flash — with the dialect translated both ways: `noul`,
  `criteria` objects, expected-value scores → argmax level).
- Built-in tiers: `jev` (mu's provider order from env keys, free OpenCode Zen
  fallback) and `session` (inherit the running QwenPaw session's model,
  thinking disabled).
- Single-generation rotation ledger at
  `<host>/plugin-data/jev-kernel/ledger.jsonl`; `shadow` mode records without
  enforcing.
- Agent tools `jev_ask` (one bounded question → calibrated verdict) and
  `jev_ledger` (read recent records).
- `scripts/systemone_adapter.py`: loopback bridge translating gateway ↔ raw
  System One dialect, so local decision models work with any gateway-dialect
  client.
- Config hot-reload by mtime; `jev.json` precedence: `$JEV_KERNEL_CONFIG` →
  plugin dir → plugin data dir. `main.py` purges its package's cached sibling
  submodules on import so QwenPaw hot reload cannot judge with stale code.
- Packaging: `packaging/build_plugin_zip.py` builds the installable archive
  and refuses to ship a real `jev.json`; release workflow auto-tags and
  publishes from `plugin.json` version bumps.
- 14 unit tests on a bare interpreter, including a stub server per wire
  dialect and publish-hygiene checks.
