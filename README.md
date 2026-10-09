# jev-kernel

[![CI](https://github.com/CallMeHFK/jev-kernel/actions/workflows/ci.yml/badge.svg)](https://github.com/CallMeHFK/jev-kernel/actions/workflows/ci.yml)
[![QwenPaw](https://img.shields.io/badge/QwenPaw-2.2.x-green)](https://github.com/agentscope-ai/QwenPaw)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

QwenPaw middleware plugin that puts a **mu-style judgment kernel on the tool
seam**. At each decision point (`tool.risk`, `tool.approval`) a small fast
judge answers **one bounded typed question** — noul (yes/no), choice, or score
— about a small state, with a **calibrated probability**. Verdicts **cascade**
through judge tiers (later judges only see the questions earlier ones left
uncertain), land in a **ledger**, and in `active` mode can deny a tool call or
escalate it to the human. `shadow` mode records without enforcing, so you can
read the ledger before handing a point to a judge. A port of
[qybaihe/mu](https://github.com/qybaihe/mu)'s jev integration
(`docs/judges.md`, `docs/reference/decision-points.md`); the wire format was
verified against mu-agent 0.1.8. Stdlib-only, no pip dependencies, no
telemetry.

The same kernel also runs as a **PreToolUse hook** in other agent hosts —
ZCode, Qoder, Claude Code and Codex share one verdict dialect — see
[Other agent hosts](#other-agent-hosts-pretooluse-hook-mode).

Defaults are inert: `tool.risk` starts in `shadow`, `tool.approval` in `off`,
and `tiers` empty — **no judge is contacted until you configure one**. See
[Quick start](#quick-start).

```bash
qwenpaw plugin install ~/.qwenpaw/plugins/jev-kernel   # or a local checkout / release zip, see below
```

<details>
<summary>Contents</summary>

- [Why](#why)
- [Quick start](#quick-start) — install · configure · read the ledger · go active
- [Other agent hosts](#other-agent-hosts-pretooluse-hook-mode) — ZCode · Qoder · Claude Code · Codex
- [How it works](#how-it-works) — decision points, three modes, the cascade, thresholds
- [Judges and transports](#judges-and-transports) — `typesafe` · `http` · `llm` · `systemone` · built-in `jev`/`session` tiers
- [Configuration](#configuration) — `jev.json`
- [Ledger](#ledger)
- [Tools](#tools) — `jev_ask`, `jev_ledger`
- [Does it work?](#does-it-work)
- [Development](#development) · [Privacy](#privacy) · [License](#license)

</details>

## Why

"Think before you act" lives in prompts, and prompts decay: the more rules you
stack, the more the model learns to skim them. mu's answer is to move the
judgment **out of the prompt and onto the tool seam**, and to make it small and
measurable: one bounded question per decision point, answered with a
probability a threshold can act on, cascaded across judges so a cheap local
model can answer the easy calls and an uncertain one falls through to a
stronger tier, and ledgered either way so the kernel's own calibration is
something you can read instead of trust. This plugin ports that pattern to
QwenPaw's AgentScope middleware seam, the same seam dispatch-guard uses.

It exists to fix a specific failure pattern: agents approving their own
destructive tool calls because nothing between the decision and the execution
ever asked a bounded question with a number attached.

## Quick start

1. Install:

   ```bash
   qwenpaw plugin install https://github.com/CallMeHFK/jev-kernel/releases/latest/download/jev-kernel.zip
   # or from a checkout:
   qwenpaw plugin install /path/to/jev-kernel
   ```

2. Configure. The durable home is `<host>/plugin-data/jev-kernel/jev.json`
   (survives reinstalls); a `jev.json` next to `plugin.json` overrides it, and
   `$JEV_KERNEL_CONFIG` overrides both (tests use it). Start from
   [`jev.example.json`](jev.example.json). The smallest useful config exports
   one provider key and names the built-in tier:

   ```json
   { "tiers": ["jev"], "points": { "tool.risk": "shadow" } }
   ```

   with e.g. `TYPESAFE_API_KEY` exported — or point a named judge at anything
   you already run, including a local decision model (see
   [Judges and transports](#judges-and-transports)).

3. Read the ledger at `<host>/plugin-data/jev-kernel/ledger.jsonl`: one line
   per judged question, with the verdict, its probability, the judge that
   answered, latency, and — when nobody answered — the fallback reason.

4. When the shadow ledger looks calibrated, flip the point:
   `"points": { "tool.risk": "active" }`. From then on a confident `yes` on
   `tool.risk` denies the call, and `tool.approval` escalates to the human
   instead of running.

## Other agent hosts (PreToolUse hook mode)

ZCode, Qoder, Claude Code and Codex all expose the tool seam as a
`PreToolUse` hook: the host pipes one JSON event to a command's stdin and
reads the verdict off stdout. All four read the same envelope —
`{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision":
"allow|deny|ask", "permissionDecisionReason": …}}` (Codex additionally
honours a top-level `{"decision": "block", "reason": …}`, which the hook
emits for denials) — verified against the qodercli 1.1.64 binary, ZCode's
app.asar, and codex 0.159.2's wire schema. `backend/hook.py` is that command:

```bash
python3 scripts/install_hook.py --host zcode    # or: qoder | claude | codex
```

The installer gives each host a **self-contained copy in its own
dot-folder** — `~/.zcode/jev-kernel/`, `~/.qoder-cn/jev-kernel/`,
`~/.claude/jev-kernel/`, `~/.codex/jev-kernel/` — holding `backend/`,
`jev.json` and `ledger.jsonl` together (nothing rebuilds those folders, so
the dev repo can move or be deleted afterwards; QwenPaw's own install path
`~/.qwenpaw/plugins/jev-kernel/` already works this way). It merges one hook
entry into the host's hook config (`~/.zcode/hooks.json`,
`~/.qoder-cn/settings.json`, `~/.claude/settings.json`,
`~/.codex/hooks.json`), preserves hooks already there, backs the file up to
`<path>.jev-bak`, and is idempotent — re-running it is also the upgrade path
(re-deploys the code, rewrites a drifted command). `--uninstall` removes only
jev-kernel's hook entry and keeps the deployed folder (it holds the ledger);
`--dry-run` prints without writing. ZCode asks you to trust new hooks on
first run (`workspace_hooks_execute_code`) — approve it there.

**Native plugin format (zcode pilot).** All four hosts cloned the same
claude-derived plugin store (`~/.<host>/plugins/`, `.<host>-plugin/plugin.json`
manifest, `enabledPlugins` gating). `--format plugin` turns the deploy dir
into that package — `.zcode-plugin/plugin.json` + `hooks/hooks.json` inside
`~/.zcode/jev-kernel/` — and registers it via `plugins.dirs` +
`enabledPlugins["jev-kernel@inline"]` in `~/.zcode/cli/config.json`, removing
the legacy manual hook entry so the call is judged once. Verified for zcode
only so far; the other hosts stay on `--format hook` until piloted. Note:
ZCode has no `${PLUGIN_ROOT}`-style variable in hook commands, so the
packaged hooks.json carries absolute paths — fine for local installs, a
known limitation for marketplace distribution.

Hook mode differences from the QwenPaw plugin:

- Config and ledger are per host: `<host folder>/jev-kernel/jev.json` and
  `ledger.jsonl`. `$JEV_KERNEL_CONFIG` / `$JEV_KERNEL_DATA_DIR` still
  override; a first install seeds `jev.json` from a legacy shared
  `~/.jev-kernel/jev.json` if one exists.
- The `session` tier is QwenPaw-only — elsewhere it resolves to nothing and
  the cascade records `unresolved:session`. Use named judges or `jev`.
- `tool.approval` in `active` mode maps to `permissionDecision: "ask"`, i.e.
  the host's own human confirmation flow, instead of a denied tool response.
- Empty stdout means "no opinion": shadow mode, fallback verdicts, skipTools
  and every error path all exit 0 silently, so a dead kernel never blocks a
  tool call.

## How it works

Two decision points sit on the tool seam, each with mu's three modes:

| Point | Question | `active` behaviour on a confident verdict |
|---|---|---|
| `tool.risk` | will this call destroy or irreversibly change user data or system state? | deny the tool call |
| `tool.approval` | should this call wait for the human? | escalate to the human approval flow |

Modes: `active` (verdicts can change what happens), `shadow` (verdicts are
ledgered, behaviour unchanged), `off` (point not evaluated).

Every question is one of three kinds — **noul** (yes/no with `P(yes)`),
**choice** (one option with per-option probabilities), **score** (index into
criteria with confidence) — and every answer carries a probability. Thresholds
(`yes`, `no`, `minProbability`, `risk`) turn probabilities into verdicts and
decide what "uncertain" means; uncertain questions fall through to the next
judge in the tier list, and a question nobody resolves gets a fallback verdict
whose reason is ledgered (mu's rule: a dead judge never wedges the agent and
never silently passes as a `no`).

## Judges and transports

Four wire transports, same bounded questions:

| `type` | Speaks |
|---|---|
| `typesafe` | TypeSafe's System One protocol: `POST {model, state, questions}` → `{answers}` (mu's baseUrl normalisation: `/` → `+/v1/systemone`, `/v1` → `+/systemone`) |
| `http` | any endpoint taking `{state, questions}` → `{answers}` (Cloudflare Workers AI's `{result: …}` envelope is unwrapped) |
| `llm` | any OpenAI-compatible chat completions endpoint, prompted to answer as JSON; slower and every decision costs tokens, but needs nothing beyond a model you already have a key for |
| `systemone` | a raw System One server: ollama ≥ v0.35 **decision models** (nimble, tev1, clef, clef-flash) at `POST /v1/systemone`. The dialect is translated both ways — boolean goes out as `noul`, choice `options` become a `criteria` object, and answers come back as gateway shapes (`noul` → `probability`, a score's continuous expected value → the argmax level index) |

Two built-in tier names need no `judges` entry: `jev` resolves mu's provider
order from environment keys (`TYPESAFE_API_KEY`, `MU_JUDGE_OPENROUTER_API_KEY`,
`AI_GATEWAY_API_KEY`, `OPENCODE_API_KEY`, `CLOUDFLARE_API_KEY` +
`CLOUDFLARE_ACCOUNT_ID`) and falls back to the free OpenCode Zen tier when no
key is set; `session` inherits the QwenPaw session's own model (OpenAI-compatible
providers only, thinking disabled — thinking models blow the judge latency
budget), mu's `mu setup --judge model` equivalent.

**Local decision models.** ollama's decision models answer System One
natively but in the raw dialect (`noul`, `criteria` objects, expected-value
scores). Either use `type: systemone` directly, or run the bundled bridge
`scripts/systemone_adapter.py`, which translates gateway ↔ raw dialect on
loopback so any gateway-dialect client (including older kernel versions) can
use them:

```bash
OLLAMA_SYSTEMONE_URL=http://127.0.0.1:11434/v1/systemone python3 scripts/systemone_adapter.py --port 47824
```

```json
{ "judges": { "clef": { "type": "systemone", "baseUrl": "http://127.0.0.1:11434",
                        "model": "clef-flash:latest", "timeoutMs": 120000 } },
  "tiers": ["clef", "session"] }
```

A cheap local judge first, the session model catching what it leaves uncertain
— the cascade is where the tiering pays for itself.

## Configuration

`jev.json` mirrors mu's judge section: `points` (point → mode), `tiers`
(default judge order), `routes` (per-point override), `judges` (name →
`{type, baseUrl, model, apiKeyEnv, timeoutMs}`), `thresholds`
(`yes`/`no`/`minProbability`/`risk`), `approvalTools`, `skipTools`,
`timeoutMs`, `recordState`, `stateChars`. It is hot-reloaded by mtime — edit
and the next decision point picks it up, no restart. [`jev.example.json`](jev.example.json)
documents every knob with placeholder endpoints; the repo never ships a real
config.

## Ledger

`<host>/plugin-data/jev-kernel/ledger.jsonl`, one JSON line per judged
question: timestamp, point, mode, tool, the question asked, the verdict
(kind, value, probability, uncertain, judge, latency) or the fallback reason,
and whether it was enforced. Rotation is single-generation (mu's rule: the
ledger is evidence, not a database). In `shadow` mode this file is the whole
point — read it before you flip a point to `active`.

## Tools

- `jev_ask` — ask the kernel one bounded typed question about a given state
  and get the calibrated verdict back (agents and scripts can use the same
  seam the middleware uses).
- `jev_ledger` — read recent ledger records.

## Does it work?

```bash
python -m unittest discover -s tests -v   # 43 tests, bare interpreter
python packaging/build_plugin_zip.py      # dist/jev-kernel.zip
```

The suite covers the wire shapes, uncertainty margins, cascade fall-through,
all four transports (against a local stub server speaking each dialect,
including ollama's raw System One), dead-judge fallback, active denial,
shadow pass-through, approval escalation, session-tier model inheritance,
publish hygiene, and the hook front end: each host's payload spelling and
verdict dialect, ZCode's strict-schema key set, and fail-open on garbage
stdin — including the installed invocation shape (`python3 backend/hook.py
--host …`) run as a real subprocess. A shadow-mode session against a local `clef-flash` judged
`get_current_time` at `P(yes)=0.016` and a self-created scratch-dir cleanup at
`P(no)=0.99`, while probes of `rm -rf ~/Documents` and `git push --force`
scored `0.96` and `0.89` — the calibration curve is the artefact to check in
your own ledger.

## Development

Prerequisites: Python ≥ 3.10, no runtime or test dependencies —
`backend/main.py` degrades its AgentScope/QwenPaw imports to local stand-ins,
so the suite runs on a bare interpreter.

```bash
git clone https://github.com/CallMeHFK/jev-kernel.git
cd jev-kernel
python -m unittest discover -s tests -v
ruff check .
python packaging/build_plugin_zip.py
```

One host quirk worth knowing: QwenPaw hot reload re-executes the plugin entry
module but keeps the package's cached sibling submodules, so `main.py` purges
its own package's submodules from `sys.modules` before importing them —
without that, a reinstall keeps judging with the previous code. See
[`docs/DESIGN.md`](docs/DESIGN.md).

## Privacy

The kernel sends one small state and one question per decision point to the
judges you configure, and nothing else — no telemetry, no phone-home. The
state is the tool name plus (only when `recordState: true`) a truncated input
snippet; the default keeps it off. Your `jev.json` names your judge endpoints
and is gitignored; the ledger records your tool calls and lives outside the
repo. Sanitize ledger excerpts before pasting them anywhere public.

## License

MIT — see [LICENSE](LICENSE).
