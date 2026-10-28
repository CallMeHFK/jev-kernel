# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| latest release (`v0.2.x` line) | :white_check_mark: |
| anything older | :x: — upgrade to the latest release |

Fixes ship in a new patch release; old versions are not backported.

## Reporting a Vulnerability

Report privately through GitHub's advisory form:

<https://github.com/CallMeHFK/jev-kernel/security/advisories/new>

Do **not** open a public issue for a vulnerability. Do not include a real
`jev.json`, judge endpoint, API key, or ledger excerpt in a public report; the
advisory is the private channel.

Please include:

- affected `plugin.json` version and the host agent (QwenPaw / hook mode)
- the affected decision point (`tool.risk` / `tool.approval` / `agent_route`)
  and mode (`shadow` / `active`)
- a sanitized config shape (transport types and tier order, no endpoints or keys)
- a minimal reproduction and the impact you observed

What happens next:

1. Acknowledgement within 3 working days in the advisory.
2. Assessment and a fix plan in the same private thread.
3. A fix released together with the advisory disclosure; you are credited
   unless you decline.

## Scope

jev-kernel judges tool calls locally and talks only to the judge endpoints you
configure. The repository ships no endpoints, credentials, or telemetry. Leaked
`jev.json` / ledger content is configuration hygiene rather than a code
vulnerability — see [CONTRIBUTING.md](CONTRIBUTING.md), "Secrets (strict rule)".