#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/latticemind"
CONFIG_FILE="$CONFIG_DIR/config"
[[ -r "$CONFIG_FILE" ]] || { printf 'LatticeMind is not installed.\n'; exit 0; }
# shellcheck disable=SC1090
source "$CONFIG_FILE"

if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user disable --now \
    latticemind-morning.timer latticemind-nightly.timer \
    latticemind-weekly.timer latticemind-health.timer >/dev/null 2>&1 || true
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/"latticemind-{morning,nightly,weekly,health}.{service,timer}
  systemctl --user daemon-reload
fi

python3 - "$VAULT" "$HOME" "$BACKUP_DIR" <<'PY'
import json, shutil, sys
from pathlib import Path
vault, home, backup = map(Path, sys.argv[1:])

def load(name):
    path = backup / name
    return json.loads(path.read_text()) if path.exists() else []

for name in load("installed-codex.json"):
    out = vault / ".agents/skills" / name
    if out.exists():
        shutil.rmtree(out)
    old = backup / "codex-skills" / name
    if old.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(old, out)

for name in load("installed-gjc.json"):
    out = home / ".gjc/skills" / name
    if out.exists():
        shutil.rmtree(out)
    old = backup / "gjc-skills" / name
    if old.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(old, out)

for rel in load("installed-shared.json"):
    out = vault / rel
    old = backup / "vault" / rel
    if old.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, out)
    elif out.exists():
        out.unlink()

agents = vault / "AGENTS.md"
if agents.exists():
    text = agents.read_text()
    start, end = "<!-- LATTICEMIND:START -->", "<!-- LATTICEMIND:END -->"
    if start in text and end in text:
        text = text[:text.index(start)].rstrip() + "\n" + text[text.index(end)+len(end):].lstrip("\n")
        if text.strip() == "# Agent Instructions":
            agents.unlink()
        else:
            agents.write_text(text)
PY

rm -f "$HOME/.local/bin/latticemind-maintain" "$HOME/.local/bin/latticemind-status"
rm -f "$CONFIG_FILE"
printf 'LatticeMind integration removed. Vault notes and scaffold folders were preserved.\n'
printf 'Recovery backup remains at: %s\n' "$BACKUP_DIR"
