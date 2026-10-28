# Contributing to jev-kernel

## What this plugin is for

jev-kernel exists to move one decision out of the prompt and onto the tool
seam: *was this tool call judged, by whom, with what probability*. Changes
that keep the question bounded and the verdict measurable are welcome;
changes that turn the kernel into a general policy engine are usually better
as a fork, because every new unbounded question is a new way to stall a
legitimate tool call behind a judge that was never meant to answer it.

## Legal

By submitting a contribution you agree that it is licensed under the project's
[MIT License](LICENSE), the same as the rest of the codebase. No DCO
`Signed-off-by` trailer and no CLA are required.

## Development setup

Prerequisites: Python ≥ 3.10. There are no runtime or test dependencies — the
plugin is stdlib-only and `backend/main.py` degrades its AgentScope/QwenPaw
imports to local stand-ins, so the suite runs on a bare interpreter.

```bash
git clone https://github.com/CallMeHFK/jev-kernel.git
cd jev-kernel
python -m unittest discover -s tests -v
python packaging/build_plugin_zip.py      # dist/jev-kernel.zip
```

Try it against a real host by copying the checkout into the plugin directory
(`~/.qwenpaw/plugins/jev-kernel/`, or
`%USERPROFILE%\.qwenpaw\plugins\jev-kernel\` on Windows) and restarting QwenPaw,
then point `$JEV_KERNEL_CONFIG` at a scratch config or write the durable one
under `<host>/plugin-data/jev-kernel/jev.json`.

## Repository layout

| Path | Role |
|---|---|
| `plugin.json` | QwenPaw manifest; `entry.backend` points at `backend/main.py` |
| `backend/main.py` | middleware on the tool seam: points, modes, escalation, tools |
| `backend/judges.py` | the four transports and the cascade |
| `backend/questions.py` | the three question kinds, thresholds, verdict parsing |
| `backend/session_model.py` | the `session` tier: inherit the running session's model |
| `backend/ledger.py` | single-generation rotation ledger |
| `jev.example.json` | sanitized sample config — the only config in the repo |
| `jev.json` | **your** real config; gitignored, never committed |
| `scripts/systemone_adapter.py` | loopback bridge: gateway dialect ↔ ollama's raw System One |
| `packaging/build_plugin_zip.py` | archive builder + the guard that refuses to ship `jev.json` |
| `tests/` | kernel behaviour (`test_kernel.py`), publish hygiene (`test_packaging.py`) |
| `docs/DESIGN.md` | why the seam looks like this, including the fail-open decisions |

## Secrets (strict rule)

The interesting private data in this repository is the kernel config and the
ledger: judge endpoints name internal hosts, and ledger lines name real tools,
paths, and commands.

- **Never commit `jev.json`**, and never paste a real one into an issue, PR,
  commit message, doc, or test fixture. It is gitignored; if `git status`
  offers to add it, stop and check why.
- Sample configs, README examples, and test fixtures use placeholder
  endpoints (`relay.example.com`, `10.0.0.5:9000`) and placeholder env key
  names (`RELAY_KEY`). Keep it that way.
- Ledger excerpts (`<host>/plugin-data/jev-kernel/ledger.jsonl`) record real
  tool inputs when `recordState` is on. Replace paths, hosts, and command
  strings with placeholders before attaching one to a bug report.
- Denial and escalation strings must not name a person, a team, an internal
  host, or an internal system. Say "the user" and "the judge".
- `build_plugin_zip.py` refuses to build when a real config is present. Do not
  weaken that check to make a local build pass — move the file instead.

## Tests

Every behaviour change needs a test that fails without it. Two conventions
worth knowing before you write one:

- Tests write a real `jev.json` into a temp directory and repoint the kernel
  with `$JEV_KERNEL_CONFIG` (plus `$JEV_KERNEL_DATA_DIR` for the ledger),
  because `load_config()` stats the file before consulting its mtime cache.
  Injecting only the cache silently yields the inert defaults.
- The stub judge server speaks one dialect per path (`/yes`, `/uncertain`,
  `/cf`, `/v1/chat/completions`, `/v1/systemone`, `/ollama`); add a path
  rather than a flag when you need a new wire shape.

## Commit & pull request guidelines

- Branch names: `fix/<topic>`, `feat/<topic>`, `docs/<topic>`.
- Commit messages: imperative summary, body for the *why*. One logical change
  per commit.
- Bump `version` in `plugin.json` and add a `## [X.Y.Z] - YYYY-MM-DD` section
  (newest first) to `CHANGELOG.md` in the same PR — pushing that to `main` releases
  automatically: the workflow tags `vX.Y.Z` (annotated), runs the suite, builds
  the archive and publishes it. The release fails when the changelog section is
  missing. Pushing a `v*` tag by hand is the manual equivalent.
- PR titles follow [Conventional Commits](https://www.conventionalcommits.org)
  (`fix:`, `feat(scope):`, …). The repository squash-merges, so the PR title
  becomes the commit subject on `main`; the PR body becomes the commit body.
  Keep one logical change per PR.
- Use the provided issue and PR templates.

## Code of conduct

All contributions and interactions must follow our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Do not report vulnerabilities in public issues. Use the private advisory form
described in [SECURITY.md](SECURITY.md).

## Where to ask

Open an issue (or a discussion, when you want a design conversation).
Design questions ("why does a dead judge fall open in shadow?") belong in
`docs/DESIGN.md` discussion, not in chat logs.
