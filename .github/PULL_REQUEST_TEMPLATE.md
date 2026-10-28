## What

<!-- One paragraph: what changes, and which decision point / layer it touches. -->

## Why

<!-- The problem or issue this closes. Bug fixes: state the root cause and its
mechanism (file:line), not only the symptom. Link with "Fixes: #123". -->

## How tested

<!-- Commands and observed output, e.g. `python -m unittest discover -s tests -v`,
`ruff check .`, plus any live host run and the sanitized ledger record it produced. -->

## Checklist

- [ ] `python -m unittest discover -s tests -v` passes on a bare Python ≥ 3.10
- [ ] `ruff check .` passes
- [ ] No real judge endpoints, internal hosts, API keys, ledger excerpts,
      workspace paths, or people's names added anywhere (docs, tests, commit
      messages included)
- [ ] `jev.example.json` still installs and configures cleanly from scratch
- [ ] README and `docs/DESIGN.md` updated if user-facing behavior changed
- [ ] `version` in `plugin.json` bumped and `CHANGELOG.md` has a matching
      `## [X.Y.Z] - YYYY-MM-DD` section
- [ ] [CONTRIBUTING.md](CONTRIBUTING.md) (including the Secrets rule) and
      [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) read and followed