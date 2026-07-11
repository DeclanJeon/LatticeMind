#!/usr/bin/env python3
"""Insert or replace a named managed Markdown block without clobbering a file."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--heading", required=True)
    parser.add_argument("--body", required=True)
    args = parser.parse_args()

    path = Path(args.file)
    start = f"<!-- {args.name}:START -->"
    end = f"<!-- {args.name}:END -->"
    block = f"{start}\n## {args.heading}\n\n{args.body}\n{end}"
    text = path.read_text() if path.exists() else "# Agent Instructions\n"
    if start in text and end in text:
        text = text[: text.index(start)] + block + text[text.index(end) + len(end) :]
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
