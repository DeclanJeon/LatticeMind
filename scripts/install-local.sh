#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_VAULT="$HOME/Documents/Obsidian Vault"
[[ -d "$DEFAULT_VAULT" ]] || DEFAULT_VAULT="$HOME/Obsidian/LatticeMind"
VAULT="$DEFAULT_VAULT"
OWNER="$(git config --global user.name 2>/dev/null || true)"
OWNER="${OWNER:-${USER:-Vault Owner}}"
PRESET="builder"
INSTALL_GJC=1
INSTALL_CODEX=1
INSTALL_SCHEDULE=1
AGENT_LIST="all"
UPSTREAM_URL="${LATTICEMIND_UPSTREAM_URL:-https://github.com/eugeniughelbur/obsidian-second-brain.git}"
UPSTREAM_REF="${LATTICEMIND_UPSTREAM_REF:-main}"

usage() {
  cat <<'EOF'
Usage: install.sh [options]
  --vault PATH          Obsidian vault path
  --name NAME           Vault owner name
  --preset PRESET       default|builder|executive|creator|researcher
  --agents LIST         all or comma list: gjc,codex,claude,opencode,gemini,pi,omp,hermes
  --no-gjc              Skip GJC user skills
  --no-codex            Skip Codex vault skills
  --no-schedule         Skip systemd maintenance timers
  -h, --help            Show this help
EOF
}

while (($#)); do
  case "$1" in
    --vault) VAULT="${2:?missing vault path}"; shift 2 ;;
    --name) OWNER="${2:?missing owner name}"; shift 2 ;;
    --preset) PRESET="${2:?missing preset}"; shift 2 ;;
    --agents) AGENT_LIST="${2:?missing agent list}"; shift 2 ;;
    --no-gjc) INSTALL_GJC=0; shift ;;
    --no-codex) INSTALL_CODEX=0; shift ;;
    --no-schedule) INSTALL_SCHEDULE=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 64 ;;
  esac
done

case "$PRESET" in
  default|builder|executive|creator|researcher) ;;
  *) printf 'Invalid preset: %s\n' "$PRESET" >&2; exit 64 ;;
esac

agent_enabled() {
  local name="$1"
  [[ ",$AGENT_LIST," == *,all,* || ",$AGENT_LIST," == *",$name,"* ]]
}

for cmd in git python3; do
  command -v "$cmd" >/dev/null || { printf 'Missing required command: %s\n' "$cmd" >&2; exit 69; }
done

VAULT="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$VAULT")"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/latticemind"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/latticemind"
BIN_DIR="$HOME/.local/bin"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$DATA_DIR/backups/$STAMP"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/latticemind-install.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$VAULT" "$CONFIG_DIR" "$DATA_DIR" "$BIN_DIR" "$BACKUP_DIR"

printf '[1/6] Fetching obsidian-second-brain...\n'
git clone --quiet --depth 1 --branch "$UPSTREAM_REF" "$UPSTREAM_URL" "$TMP_DIR/upstream"
bash "$TMP_DIR/upstream/scripts/build.sh" >/dev/null

printf '[2/6] Staging a safe %s vault layout...\n' "$PRESET"
mkdir -p "$TMP_DIR/scaffold"
python3 - "$TMP_DIR/upstream" "$TMP_DIR/scaffold" "$OWNER" "$PRESET" <<'PY'
import sys
from pathlib import Path
upstream, target, owner, preset = sys.argv[1:]
sys.path.insert(0, str(Path(upstream) / "scripts"))
import bootstrap_vault as b
b.bootstrap(Path(target), owner, preset, "personal", "", [], False)
PY

# Copy only files that do not already exist. Existing notes and settings win.
python3 - "$TMP_DIR/scaffold" "$VAULT" <<'PY'
import os, shutil, sys
from pathlib import Path
src, dst = map(Path, sys.argv[1:])
for root, dirs, files in os.walk(src):
    rel = Path(root).relative_to(src)
    (dst / rel).mkdir(parents=True, exist_ok=True)
    for name in files:
        out = dst / rel / name
        if not out.exists():
            shutil.copy2(Path(root) / name, out)
PY

printf '[3/6] Installing shared references and helper scripts...\n'
python3 - "$TMP_DIR/upstream/dist/codex-cli" "$VAULT" "$BACKUP_DIR" <<'PY'
import json, shutil, sys
from pathlib import Path
src, vault, backup = map(Path, sys.argv[1:])
installed = []
for relroot in (Path(".codex"),):
    for item in (src / relroot).rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        out = vault / rel
        if out.exists():
            old = backup / "vault" / rel
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out, old)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, out)
        installed.append(str(rel))
(backup / "installed-shared.json").write_text(json.dumps(installed, indent=2))
PY

if ((INSTALL_CODEX)) && agent_enabled codex; then
  printf '[4/6] Installing 43 native Codex skills...\n'
  python3 - "$TMP_DIR/upstream/dist/codex-cli/.agents/skills" "$VAULT/.agents/skills" "$BACKUP_DIR" "$VAULT/AGENTS.md" <<'PY'
import json, shutil, sys
from pathlib import Path
src, dst, backup, agents = map(Path, sys.argv[1:])
names = []
for skill in src.iterdir():
    if not skill.is_dir():
        continue
    out = dst / skill.name
    if out.exists():
        old = backup / "codex-skills" / skill.name
        old.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(out, old)
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill, out)
    names.append(skill.name)
start, end = "<!-- LATTICEMIND:START -->", "<!-- LATTICEMIND:END -->"
block = f'''{start}\n## LatticeMind knowledge layer\n\nThis vault has native Codex skills under `.agents/skills/`. Read `_CLAUDE.md` before vault writes and obey `.codex/references/ai-first-rules.md`. Preserve existing prose and raw sources; use dated claims, confidence labels, and `[[wikilinks]]`.\n{end}'''
text = agents.read_text() if agents.exists() else "# Agent Instructions\n"
if start in text and end in text:
    text = text[:text.index(start)] + block + text[text.index(end)+len(end):]
else:
    text = text.rstrip() + "\n\n" + block + "\n"
agents.write_text(text)
(backup / "installed-codex.json").write_text(json.dumps(sorted(names), indent=2))
PY
else
  printf '[4/6] Codex skills skipped.\n'
fi

if ((INSTALL_GJC)) && agent_enabled gjc; then
  if command -v gjc >/dev/null; then
    printf '[5/6] Installing globally available GJC skills...\n'
    python3 - "$TMP_DIR/upstream/dist/codex-cli/.agents/skills" "$HOME/.gjc/skills" "$BACKUP_DIR" "$VAULT" <<'PY'
import json, shutil, sys
from pathlib import Path
src, dst, backup = map(Path, sys.argv[1:4])
vault = sys.argv[4]
dst.mkdir(parents=True, exist_ok=True)
names = []
for skill in src.iterdir():
    if not skill.is_dir():
        continue
    out = dst / skill.name
    if out.exists():
        old = backup / "gjc-skills" / skill.name
        old.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(out, old)
        shutil.rmtree(out)
    shutil.copytree(skill, out)
    path = out / "SKILL.md"
    text = path.read_text()
    preamble = ("## LatticeMind vault binding\n\n"
                f"The canonical vault root is `{vault}`. Resolve every relative vault path against this root regardless of the current working directory. Preserve existing user-authored prose and immutable raw sources.\n\n")
    text = text.replace("---\n\n", "---\n\n" + preamble, 1)
    text = text.replace("references/", f"{vault}/.codex/references/")
    text = text.replace("scripts/", f"{vault}/.codex/scripts/")
    path.write_text(text)
    names.append(skill.name)
(backup / "installed-gjc.json").write_text(json.dumps(sorted(names), indent=2))
PY
  else
    printf '[5/6] GJC not found; global GJC skills skipped.\n'
    INSTALL_GJC=0
  fi
else
  printf '[5/6] GJC skills skipped.\n'
fi

EXTRA_MANIFEST="$BACKUP_DIR/installed-extra.json"
merge_tree() {
  local source="$1" destination="$2" label="$3"
  python3 "$ROOT_DIR/scripts/copy_tree.py" \
    --source "$source" \
    --destination "$destination" \
    --backup "$BACKUP_DIR/extra/$label" \
    --manifest "$EXTRA_MANIFEST"
}

if agent_enabled claude; then
  printf '      Installing Claude Code skill and slash commands...\n'
  merge_tree "$TMP_DIR/upstream/dist/claude-code" \
    "$HOME/.claude/skills/obsidian-second-brain" "claude-skill"
  merge_tree "$TMP_DIR/upstream/dist/claude-code/commands" \
    "$HOME/.claude/commands" "claude-commands"
fi

if agent_enabled opencode; then
  printf '      Installing OpenCode commands...\n'
  merge_tree "$TMP_DIR/upstream/dist/opencode/.opencode" \
    "$VAULT/.opencode" "opencode"
  python3 "$ROOT_DIR/scripts/upsert_block.py" \
    --file "$VAULT/AGENTS.md" --name LATTICEMIND \
    --heading "LatticeMind knowledge layer" \
    --body "Use the Obsidian commands installed under \`.opencode/commands/\`. Read \`_CLAUDE.md\` before vault writes and preserve existing prose and raw sources."
fi

if agent_enabled gemini; then
  printf '      Installing Gemini CLI commands...\n'
  merge_tree "$TMP_DIR/upstream/dist/gemini-cli/.gemini" \
    "$VAULT/.gemini" "gemini"
  python3 "$ROOT_DIR/scripts/upsert_block.py" \
    --file "$VAULT/GEMINI.md" --name LATTICEMIND \
    --heading "LatticeMind knowledge layer" \
    --body "Use the Obsidian commands installed under \`.gemini/commands/\`. Read \`_CLAUDE.md\` before vault writes and preserve existing prose and raw sources."
fi

if agent_enabled pi; then
  printf '      Installing Pi package resources...\n'
  merge_tree "$TMP_DIR/upstream/dist/pi/.pi" "$VAULT/.pi" "pi"
fi

if agent_enabled omp; then
  printf '      Installing OMP managed skills...\n'
  merge_tree "$TMP_DIR/upstream/dist/codex-cli/.agents/skills" \
    "$HOME/.omp/agent/managed-skills" "omp"
  python3 "$ROOT_DIR/scripts/adapt_skills.py" \
    --skills "$HOME/.omp/agent/managed-skills" \
    --source "$TMP_DIR/upstream/dist/codex-cli/.agents/skills" \
    --vault "$VAULT" --label OMP
fi

if agent_enabled hermes; then
  printf '      Installing Hermes native skills...\n'
  merge_tree "$TMP_DIR/upstream/dist/hermes/skills" \
    "$HOME/.hermes/skills/obsidian-second-brain" "hermes-skills"
  merge_tree "$TMP_DIR/upstream/dist/hermes/references" \
    "$HOME/.hermes/skills/obsidian-second-brain/references" "hermes-references"
  merge_tree "$TMP_DIR/upstream/dist/hermes/scripts" \
    "$HOME/.hermes/skills/obsidian-second-brain/scripts" "hermes-scripts"
fi

VERSION="${LATTICEMIND_VERSION:-$(python3 - <<'PY'
import json
import urllib.request
try:
    with urllib.request.urlopen(
        "https://api.github.com/repos/DeclanJeon/LatticeMind/releases/latest",
        timeout=5,
    ) as response:
        print(json.load(response)["tag_name"])
except Exception:
    print("main")
PY
)}"

cp "$ROOT_DIR/bin/latticemind-maintain" "$BIN_DIR/latticemind-maintain"
cp "$ROOT_DIR/bin/latticemind-status" "$BIN_DIR/latticemind-status"
cp "$ROOT_DIR/uninstall.sh" "$DATA_DIR/uninstall.sh"
chmod 755 "$BIN_DIR/latticemind-maintain" "$BIN_DIR/latticemind-status" "$DATA_DIR/uninstall.sh"
printf 'VAULT=%q\nBACKUP_DIR=%q\nINSTALL_GJC=%q\nINSTALL_CODEX=%q\nAGENT_LIST=%q\nVERSION=%q\n' \
  "$VAULT" "$BACKUP_DIR" "$INSTALL_GJC" "$INSTALL_CODEX" "$AGENT_LIST" "$VERSION" \
  > "$CONFIG_DIR/config"

printf '[6/6] Configuring maintenance timers...\n'
if ((INSTALL_SCHEDULE)) && command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  bash "$ROOT_DIR/scripts/install-systemd.sh"
else
  printf '      Timers skipped (unsupported or --no-schedule).\n'
fi

printf '\nLatticeMind is ready.\n'
printf '  Vault:   %s\n' "$VAULT"
printf '  Status:  %s/latticemind-status\n' "$BIN_DIR"
printf '  Backup:  %s\n' "$BACKUP_DIR"
printf '  Remove:  bash %s/uninstall.sh\n\n' "$DATA_DIR"
