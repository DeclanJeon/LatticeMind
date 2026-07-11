#!/usr/bin/env python3
"""Bind portable Agent Skills to one canonical vault."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills", required=True)
    parser.add_argument("--vault", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--source")
    args = parser.parse_args()

    root = Path(args.skills)
    vault = str(Path(args.vault).expanduser().resolve())
    paths = (
        [root / item.name / "SKILL.md" for item in Path(args.source).iterdir() if item.is_dir()]
        if args.source
        else list(root.glob("*/SKILL.md"))
    )
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text()
        preamble = (
            f"## LatticeMind {args.label} binding\n\n"
            f"The canonical vault root is `{vault}`. Resolve every relative vault "
            "path against this root regardless of the current working directory. "
            "Preserve existing user-authored prose and immutable raw sources.\n\n"
        )
        text = text.replace("---\n\n", "---\n\n" + preamble, 1)
        text = text.replace("references/", f"{vault}/.codex/references/")
        text = text.replace("scripts/", f"{vault}/.codex/scripts/")
        path.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
