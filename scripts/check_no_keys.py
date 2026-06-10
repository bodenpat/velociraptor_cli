#!/usr/bin/env python3
"""Pre-commit hook: reject files containing Insight-API-key-shaped strings.

Rapid7 Insight platform API keys are UUID-formatted. Any UUID in the repo is
rejected unless it is an obvious placeholder — every hex group a single
repeated character (e.g. 11111111-2222-3333-4444-555555555555), which is the
required style for keys/org IDs in docs and tests.

Exit 0 = clean, exit 1 = potential key found (blocks the commit).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def is_placeholder(uuid: str) -> bool:
    return all(len(set(group.lower())) == 1 for group in uuid.split("-"))


def scan(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in UUID_RE.finditer(line):
            if not is_placeholder(match.group()):
                hits.append((lineno, match.group()))
    return hits


def main(argv: list[str]) -> int:
    failed = False
    for name in argv:
        for lineno, uuid in scan(Path(name)):
            redacted = uuid[:8] + "-…"
            print(f"{name}:{lineno}: possible Insight API key / real UUID: {redacted}", file=sys.stderr)
            failed = True
    if failed:
        print(
            "\nReal UUIDs are blocked to keep API keys out of the repo. Use "
            "placeholder UUIDs (each group one repeated character, e.g. "
            "11111111-2222-3333-4444-555555555555) in docs and tests.",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
