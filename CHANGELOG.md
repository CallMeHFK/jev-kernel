# Changelog

Newest first. The release workflow tags `vX.Y.Z` from `plugin.json` and uses
the matching section below as the release notes.

## [0.2.4] - 2026-10-28

### Fixed

- `scripts/route_shadow_report.py`: the report read only `ledger.jsonl` and
  returned "no jev_route records" after every rotation, because the ledger
  rotates in a single generation to `ledger.jsonl.1`. It now reads the rotated
  generation first and then the live file, so a daily review sees the whole
  window instead of an empty one. Explicit `--ledger PATH` still reads exactly
  that one file.
- `packaging/release_notes.py`: a `### Added` / `### Fixed` sub-heading inside a
  version section ended the section early, because any line starting with `##`
  was treated as the next version. Only level-2 headings end a section now.

### Added

- Repository hygiene for public contribution: `CODE_OF_CONDUCT.md`,
  `SECURITY.md` (private advisory channel, latest-only support table),
  `.github/CODEOWNERS`, `.github/dependabot.yml`, issue forms with a triage
  label, and a scheduled `stale` workflow pinned to a commit SHA.
- `CHANGELOG.md` moved to the repository root and re-headed as
  `## [X.Y.Z] - YYYY-MM-DD`; `release_notes.py` accepts both this form and the
  legacy `## vX.Y.Z`.

### Changed

- `ci.yml` declares `permissions: contents: read`; the release workflow now
  creates an annotated tag.

## [0.2.3] - 2026-10-27

Shadow evidence you can act on, before handing the router live traffic.

- `backend/routing.py`: an abstention now says which kind it is.
  `no_verdict` means no judge answered (dead, timeout, malformed);
  `uncertain` means a judge answered below `thresholds.minProbability`. The
  cascade discards that answer, so the decision recovers the judge's pick
  and probability from the fallback string into `detail` (e.g.
  `default@0.43`) and sets `confidence` to the real probability instead of
  `0.0`. Behaviour is unchanged — both still return `ask_human` — but a
  shadow review can now tell a calibration gap from an infrastructure gap.
- `backend/tools.py`: the ledger records `detail` alongside `reason`.
- `scripts/route_shadow_report.py`: new `uncertain_picks` breakdown (the
  judge's discarded answer), rendered as one line and available in `--json`.
- `scripts/route_replay_eval.py`: labeled replay through the same
  `_route_instructions` → judge → `decide_route` path, for fresh installs
  whose live ledger has too few records to judge. 28 cases in
  `scripts/route_cases.json` cover every candidate, one cross-owner pair
  (must abstain), one ambiguous request (must abstain) and the disabled-`SE`
  MIS gap (unlabeled, reported but not scored). Exits non-zero below
  `--min-agreement` (default 0.9) or `--min-scored` (default 20).
- Tests: 65 total; new coverage for `uncertain` vs `no_verdict`, the
  ledger's `detail`, and the replay case set.
- Measured on the live `lux` judge: 23/23 scored cases agree (100%), 2/25
  labeled cases abstain at p=0.43 / p=0.37, median 120 ms, and `default`
  grabbed no delegated task.

## [0.2.2] - 2026-10-27

Agent routing on the same judgment seam, without touching the QwenPaw kernel.

- `backend/routing.py`: one choice verdict → `dispatch` or `ask_human`. A
  choice is actionable only above `agentRoute.threshold` (default 0.6) and
  with a lead over the runner-up of at least `agentRoute.margin` (default
  0.1); between `askMin` (0.45) and the threshold, on a close call, on a
  judge abstention, or on a fallback the answer is `ask_human`.
- `backend/tools.py`: new `jev_route(task, candidates, shadow_expected)`
  tool. It returns only `agent_id` / `action` / `reason` / `mode` (plus
  `expected`/`agree` when shadowing) and never calls QwenPaw, so the
  orchestrator keeps its native `chat_with_agent` dispatch and the target
  agent keeps its own model.
- Shadow support: pass the agent the dispatch table chose as
  `shadow_expected`; the ledger records `recommended`, `expected`, `agree`,
  `confidence` and `margin`, and `enforced` stays false until the point is
  `active`.
- `backend/config.py`: `points.agent_route` defaults to `shadow`;
  `agentRoute` carries the candidate roster, per-agent descriptions, and the
  three gates; `skipTools` gains `jev_route` so the router is never judged
  by the middleware (no recursion).
- `backend/main.py`: registers `jev_route` for governance, `register_tool`,
  bootstrap injection and the workspace mode, and passes
  `tool_type="internal"` on all three tools (the default `network` conflicted
  with the governance pre-registration).
- `scripts/route_shadow_report.py`: agreement report over the ledger —
  scored dispatches only, abstentions by reason, disagreement pairs,
  confidence buckets.
- Tests: 61 total; new coverage for dispatch, judge abstention, close-call
  margin, low-confidence fallback, shadow agreement/disagreement, active
  enforcement, single-candidate short-circuit, reserved `ask_human`
  filtering, empty candidates and `off`.
- Docs: README decision-point table, agent-routing contract, tools list and
  `jev.example.json` updated.

## [0.2.1] - 2026-10-12

Hardening pass over the kernel, the adapter and the docs.

- `scripts/systemone_adapter.py`: `do_POST` wraps request parsing — a
  non-numeric `Content-Length` or a truncated/malformed JSON body now gets a
  `400` instead of an unhandled exception wedging the connection.
- `backend/config.py`: `DEFAULTS` gains an explicit `enabled: true` (the
  middleware factory's kill-switch no longer relies on an absent key), and
  `ledgerTail` (default 20) is a first-class knob.
- `backend/tools.py`: `jev_ledger` honors `ledgerTail` when no `limit` is
  passed.
- `backend/hook.py`: `envelope_for` handles both host payload shapes.
- `approvalTools` default gains the hook hosts' command tool names (`Bash`,
  `shell`).
- `backend/judges.py`: the unused `judges_for_point` helper is removed.
- Tests: per-run `mkdtemp` isolation with class-level server teardown
  (`server_close`), plus new coverage for the adapter's 400 paths, the
  `ledgerTail` default, and ledger rotation — 46 tests.
- Docs: the transport count says four everywhere; the Privacy section states
  plainly that the judged state (tool name + truncated input snippet) is
  always sent to the configured judge and `recordState` only gates the ledger
  copy; `jev.example.json` uses placeholder endpoints only.

## [0.2.0] - 2026-10-09

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

## [0.1.0] - 2026-10-09

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
