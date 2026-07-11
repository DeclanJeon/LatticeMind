#!/usr/bin/env python3
"""Merge a tree with file-level backups and an uninstall manifest."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--backup", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    source = Path(args.source)
    destination = Path(args.destination)
    backup = Path(args.backup)
    manifest = Path(args.manifest)
    records = json.loads(manifest.read_text()) if manifest.exists() else []

    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        output = destination / relative
        saved = backup / relative
        existed = output.exists()
        if existed:
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output, saved)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, output)
        records.append(
            {
                "output": str(output),
                "backup": str(saved) if existed else "",
            }
        )

    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(records, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
