## Summary

<!-- What does this PR change, and why? -->

## Affected area

<!-- decision points / modes / cascade / thresholds / transports / ledger /
tools / session tier / config loading / packaging / CI / docs / tests -->

## How it was tested

<!-- e.g. `python -m unittest discover -s tests -v`, plus any live QwenPaw run
and the ledger lines it produced (sanitized) -->

## Checklist

- [ ] `python -m unittest discover -s tests` passes on a bare Python ≥ 3.10
- [ ] `ruff check .` passes
- [ ] No real judge endpoints, internal hosts, API keys, ledger excerpts,
      workspace paths, or people's names added anywhere (docs, tests, commit
      messages included)
- [ ] `jev.example.json` still installs and configures cleanly from scratch
- [ ] README and `docs/DESIGN.md` updated if user-facing behavior changed
- [ ] `version` in `plugin.json` bumped and `docs/CHANGELOG.md` has a matching
      `## vX.Y.Z` section
