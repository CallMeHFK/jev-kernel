#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the changelog section for a release tag, for use as release notes."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

FALLBACK = "See `CHANGELOG.md` for {tag}."

_HEADING = re.compile(r"^#{2,3}\s+")
# Keep a Changelog (`[0.2.4] - 2026-10-28`) and the legacy `v0.2.3` form.
_VERSION = re.compile(r"^\[?[vV]?(\d+\.\d+\.\d+)\]?(?:\s*-\s*\S+)?\s*$")


def _version_of(line: str) -> str | None:
    match = _HEADING.match(line)
    if not match:
        return None
    version = _VERSION.match(line[match.end():].strip())
    return version.group(1) if version else None


def extract(changelog: Path, tag: str) -> tuple[str, bool]:
    """Return (section text, found).

    Sub-headings (`### Added`, `### Fixed`) belong to the section: only a
    level-2 heading or another version heading ends it. Treating every
    `##`-prefixed line as a boundary truncated the notes at the first
    `### Fixed` — caught by tests/test_release_notes.py.
    """
    wanted = tag.strip().lstrip("vV")
    try:
        lines = changelog.read_text(encoding="utf-8").splitlines()
    except OSError:
        return FALLBACK.format(tag=tag), False
    for index, line in enumerate(lines):
        if _version_of(line) != wanted:
            continue
        notes: list[str] = []
        for body in lines[index + 1:]:
            if re.match(r"^##\s", body) or _version_of(body) is not None:
                break
            notes.append(body)
        text = "\n".join(notes).strip()
        if text:
            return text, True
        break
    return FALLBACK.format(tag=tag), False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="release tag, e.g. v0.2.4")
    parser.add_argument(
        "--changelog",
        default=str(REPO / "CHANGELOG.md"),
        help="changelog to read (default: CHANGELOG.md)",
    )
    args = parser.parse_args()

    text, found = extract(Path(args.changelog), args.tag)
    if not found:
        sys.stderr.write(f"no {args.tag} section in {args.changelog}\n")
    print(text)
    # Non-zero on a missing section: the release workflow runs under `set -e`,
    # so a tag without changelog notes fails the publish step instead of
    # shipping a release page full of placeholder text.
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())