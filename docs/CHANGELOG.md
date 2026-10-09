# Changelog

Newest first. The release workflow tags `vX.Y.Z` from `plugin.json` and uses
the matching section below as the release notes.

## v0.2.0

The kernel learns to live outside QwenPaw: the same two decision points now
run as a `PreToolUse` hook in ZCode, Qoder, Claude Code and Codex.

- `backend/decide.py`: the judgment (both questions, thresholds, ledger
  write) extracted from the middleware into one host-neutral function; the
  middleware and the hook CLI are now thin delivery layers over it.
- `backend/hook.py`: `python3 backend/hook.py --host zcode|qoder|claude|codex`
  reads the host's PreToolUse event on stdin (snake_case and camelCase
  spellings) and answers in the host's verdict dialect. All four hosts read
  `hookSpecificOutput.permissionDecision` (allow/deny/ask); Codex denials
  also emit the top-level `{"decision": "block", "reason": …}` it
  additionally honours; ZCode's strict schema gets exactly the keys it knows.
  Contracts verified against the qodercli 1.1.64 binary, ZCode's app.asar and
  codex 0.159.2. Fail-open everywhere: any error exits 0 with empty stdout.
- `tool.approval` in `active` mode maps to `permissionDecision: "ask"` (the
  host's own human confirmation flow) instead of a denied tool response.
- `scripts/install_hook.py`: deploys a self-contained copy into each host's
  own dot-folder (`~/.zcode/jev-kernel/`, `~/.qoder-cn/jev-kernel/`,
  `~/.claude/jev-kernel/`, `~/.codex/jev-kernel/` — `backend/`, `jev.json`
  and `ledger.jsonl` co-live there, so the install survives the dev repo
  moving) and merges the hook entry into the host's hook config,
  idempotently, preserving existing hooks and backing up first. Re-running
  is the upgrade path; `--uninstall` removes only the hook entry and keeps
  the deployed folder (it holds the ledger).
- Running the hook straight from the repo (no installer) falls back to a
  shared `~/.jev-kernel/` data home; the `session` tier is QwenPaw-only and
  degrades to `unresolved:session` elsewhere. Ledger records carry a `host`
  field.
- `scripts/install_hook.py --format plugin` (zcode pilot): the deploy dir
  becomes a native plugin package (`.zcode-plugin/plugin.json` +
  `hooks/hooks.json`) registered via `plugins.dirs` + `enabledPlugins` in
  `~/.zcode/cli/config.json`, replacing the legacy manual hook entry. ZCode
  has no plugin-root variable in hook commands, so packaged hooks carry
  absolute paths.
- 43 unit tests (was 14), adding per-host payload spellings and verdict
  dialects, ZCode's strict key set, shadow pass-through, garbage-stdin
  fail-open, the installed invocation shape as a real subprocess, and the
  installer's deploy / upgrade / seed / plugin-format / uninstall paths on a
  fake HOME.

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
