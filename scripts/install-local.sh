#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_VAULT="$HOME/Documents/Obsidian Vault"
[[ -d "$DEFAULT_VAULT" ]] || DEFAULT_VAULT="$HOME/Obsidian/LatticeMind"
VAULT="$DEFAULT_VAULT"
OWNER="$(git config --global user.name 2>/dev/null || true)"
OWNER="${OWNER:-${USER:-Vault Owner}}"
PRESET="default"
PROFILE="observe"
INSTALL_GJC=1
INSTALL_CODEX=1
INSTALL_SCHEDULE=1
AGENT_LIST="all"

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
  --profile PROFILE     observe|safe-write|managed-write|full (interactive only)
EOF
}

while (($#)); do
  case "$1" in
    --vault) VAULT="${2:?missing vault path}"; shift 2 ;;
    --name) OWNER="${2:?missing owner name}"; shift 2 ;;
    --preset) PRESET="${2:?missing preset}"; shift 2 ;;
    --profile) PROFILE="${2:?missing profile}"; shift 2 ;;
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
case "$PROFILE" in
  observe|safe-write|managed-write|full) ;;
  *) printf 'Invalid profile: %s\n' "$PROFILE" >&2; exit 64 ;;
esac

agent_enabled() {
  local name="$1"
  [[ ",$AGENT_LIST," == *,all,* || ",$AGENT_LIST," == *",$name,"* ]]
}

for cmd in python3 curl; do
  command -v "$cmd" >/dev/null || { printf 'Missing required command: %s\n' "$cmd" >&2; exit 69; }
done

VAULT="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$VAULT")"
preflight_destinations() {
  local path
  for path in "$VAULT/.codex" "$VAULT/.agents" "$VAULT/.opencode" \
              "$VAULT/.gemini" "$VAULT/.pi" \
              "$HOME/.claude/skills/obsidian-second-brain" \
              "$HOME/.hermes/skills/obsidian-second-brain"; do
    if [[ -e "$path" ]]; then
      if [[ -d "$path" && ! -f "$path/.latticemind-owned" ]] ||
         [[ -f "$path" ]] && ! grep -Eq 'LATTICEMIND:(START|END)|latticemind-owned' "$path"; then
        printf 'unowned integration collision: %s\n' "$path" >&2
        exit 73
      fi
    fi
  done
}
preflight_destinations
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/latticemind"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/latticemind"
BIN_DIR="$HOME/.local/bin"
VERSIONS_DIR="$DATA_DIR/versions"
CURRENT_POINTER="$DATA_DIR/current"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$DATA_DIR/backups/$STAMP"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/latticemind-install.XXXXXX")"
mkdir -p "$VAULT" "$CONFIG_DIR" "$DATA_DIR" "$BIN_DIR" "$BACKUP_DIR"
TRANSACTION_LOG="$DATA_DIR/transactions-$STAMP.jsonl"
: > "$TRANSACTION_LOG"
# shellcheck disable=SC2034
INSTALL_COMMITTED=0
rollback_transaction() {
  python3 -I - "$TRANSACTION_LOG" <<'PY'
import json, shutil, sys
from pathlib import Path
log=Path(sys.argv[1])
if not log.exists(): raise SystemExit(0)
for rec in reversed([json.loads(x) for x in log.read_text().splitlines() if x.strip()]):
    p=Path(rec["output"])
    try:
        if rec.get("backup"):
            b=Path(rec["backup"])
            if b.is_file() and (p.exists() or p.is_symlink()): p.unlink()
            if b.is_file(): p.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(b,p)
        elif rec.get("created") and (p.exists() or p.is_symlink()):
            if p.is_dir() and not p.is_symlink(): shutil.rmtree(p)
            else: p.unlink()
    except OSError:
        pass
PY
}
# shellcheck disable=SC2154
trap 'status=$?; if (( status != 0 && INSTALL_COMMITTED == 0 )); then rollback_transaction || true; fi; rm -rf "$TMP_DIR"; exit "$status"' EXIT
: > "$TRANSACTION_LOG"
record_operation() {
  local output="$1" type="$2" created="$3" backup="${4:-}" target="${5:-}" marker="${6:-}"
  python3 - "$TRANSACTION_LOG" "$output" "$type" "$created" "$backup" "$target" "$marker" <<'PY'
import hashlib,json,os,sys
from pathlib import Path
log,output,typ,created,backup,target,marker=sys.argv[1:]
p=Path(output).absolute()
rec={"output":str(p),"type":typ,"owner":"latticemind","created":created=="1","replaced":bool(backup)}
if typ=="symlink": rec["target"]=os.readlink(p)
elif p.is_file(): rec["sha256"]=hashlib.sha256(p.read_bytes()).hexdigest()
if backup:
 b=Path(backup).absolute(); rec["backup"]=str(b)
 if b.is_file(): rec["backup_sha256"]=hashlib.sha256(b.read_bytes()).hexdigest()
if target: rec["target"]=target
if marker: rec["marker"]=marker
with Path(log).open("a",encoding="utf-8") as f: f.write(json.dumps(rec,sort_keys=True)+"\n")
PY
}
record_extra_manifest() {
  [[ -f "$EXTRA_MANIFEST" ]] || return 0
  while IFS= read -r item; do
    output="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["output"])' "$item")"
    backup="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("backup",""))' "$item")"
    if [[ -n "$backup" ]]; then record_operation "$output" file 0 "$backup"; else record_operation "$output" file 1; fi
  done < <(python3 - "$EXTRA_MANIFEST" <<'PY'
import json,sys
for item in json.loads(open(sys.argv[1],encoding="utf-8").read()): print(json.dumps(item))
PY
)
}

printf '[1/6] Authenticating release metadata and bundle...\n'
VERIFIER="$ROOT_DIR/scripts/latticemind-verify"
[[ -x "$VERIFIER" ]] || { printf 'Pinned release verifier missing: %s\n' "$VERIFIER" >&2; exit 65; }
: "${LATTICEMIND_MANIFEST_URL:?LATTICEMIND_MANIFEST_URL is required}"
: "${LATTICEMIND_SIGNATURE_URL:?LATTICEMIND_SIGNATURE_URL is required}"
: "${LATTICEMIND_ASSET_URL:?LATTICEMIND_ASSET_URL is required}"
curl --fail --location --proto '=https' --tlsv1.2 "$LATTICEMIND_MANIFEST_URL" -o "$TMP_DIR/release-manifest-v1.json"
curl --fail --location --proto '=https' --tlsv1.2 "$LATTICEMIND_SIGNATURE_URL" -o "$TMP_DIR/release-manifest-v1.sig"
curl --fail --location --proto '=https' --tlsv1.2 "$LATTICEMIND_ASSET_URL" -o "$TMP_DIR/latticemind-dist.zip"
"$VERIFIER" verify-and-extract --manifest "$TMP_DIR/release-manifest-v1.json" \
  --signature "$TMP_DIR/release-manifest-v1.sig" --asset "$TMP_DIR/latticemind-dist.zip" \
  --output "$TMP_DIR" --manifest-url "$LATTICEMIND_MANIFEST_URL" \
  --asset-url "$LATTICEMIND_ASSET_URL" > "$TMP_DIR/verifier-receipt.json"
[[ -d "$TMP_DIR/upstream" ]] || { printf 'Verified payload extraction missing\n' >&2; exit 65; }
SIGNED_SCRIPTS="$TMP_DIR/upstream/latticemind/scripts"
[[ -d "$SIGNED_SCRIPTS" ]] || { printf 'Verified lifecycle scripts missing\n' >&2; exit 65; }
# From this point onward, lifecycle and control-plane imports must resolve only
# from the authenticated extracted payload, never from the checkout/bootstrap.
unset PYTHONPATH PYTHONHOME
export LATTICEMIND_INSTALL_PROFILE="$PROFILE"
VERSION="$(python3 -I - "$TMP_DIR/release-manifest-v1.json" <<'PY'
import json,sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
if m.get("schema") != "release-manifest-v1": raise SystemExit("invalid verified release manifest")
print(m["version"])
PY
)"
python3 -I - "$CONFIG_DIR" "$VAULT" "$VERSION" "$TMP_DIR/release-manifest-v1.json" "$TMP_DIR/verifier-receipt.json" "$TMP_DIR/release-manifest-v1.sig" "$TMP_DIR/upstream" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[7])
from latticemind_core.migrate import migrate_install
config, vault, version, manifest, receipt, signature = sys.argv[1:7]
data = json.loads(open(manifest, encoding="utf-8").read())
verified = json.loads(open(receipt, encoding="utf-8").read())
if verified.get("verified") is not True or verified.get("version") != version:
    raise SystemExit("invalid verifier receipt")
migrate_install(
    config,
    vault,
    platform="unix",
    install_version=version,
    manifest=data,
    signature=Path(signature).read_bytes(),
    compatible_version=version,
)
PY
python3 - "$CONFIG_DIR/config-v1.json" "$PROFILE" <<'PY'
import json, os, sys
from pathlib import Path
path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
data["profile"] = sys.argv[2]
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
os.replace(tmp, path)
PY
python3 - "$VAULT" <<'PY'
import sys
from pathlib import Path
vault = Path(sys.argv[1])
for rel in (".codex", ".agents", ".opencode", ".gemini", ".pi"):
    path = vault / rel
    if path.exists() and not (path / ".latticemind-owned").is_file():
        raise SystemExit(f"unowned integration collision: {path}")
PY

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
python3 - "$TMP_DIR/scaffold" "$VAULT" "$TRANSACTION_LOG" <<'PY'
import hashlib, json, os, shutil, sys
from pathlib import Path
src, dst, log = map(Path, sys.argv[1:])
for root, dirs, files in os.walk(src):
    rel = Path(root).relative_to(src)
    (dst / rel).mkdir(parents=True, exist_ok=True)
    for name in files:
        source = Path(root) / name
        out = dst / rel / name
        if out.exists():
            continue
        shutil.copy2(source, out)
        rec = {
            "output": str(out.absolute()),
            "type": "file",
            "owner": "latticemind",
            "created": True,
            "replaced": False,
            "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        }
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(rec, sort_keys=True) + "\n")
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
  AGENTS_BACKUP=""
  if [[ -f "$VAULT/AGENTS.md" ]]; then
    AGENTS_BACKUP="$BACKUP_DIR/instructions/AGENTS.md"
    mkdir -p "$(dirname "$AGENTS_BACKUP")"
    cp "$VAULT/AGENTS.md" "$AGENTS_BACKUP"
  fi
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
  if [[ -n "$AGENTS_BACKUP" ]]; then
    record_operation "$VAULT/AGENTS.md" managed-block 0 "$AGENTS_BACKUP" "" "<!-- LATTICEMIND:START -->"
  else
    record_operation "$VAULT/AGENTS.md" managed-block 1 "" "" "<!-- LATTICEMIND:START -->"
  fi
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
  python3 "$SIGNED_SCRIPTS/copy_tree.py" --source "$source" --destination "$destination" \
    --backup "$BACKUP_DIR/extra/$label" --manifest "$EXTRA_MANIFEST"
  record_extra_manifest
}
record_named_manifest() {
  local list="$1" root="$2" backup_root="$3"
  python3 - "$list" "$root" "$backup_root" "$TRANSACTION_LOG" <<'PY'
import hashlib,json,sys
from pathlib import Path
listing,root,backup,log=map(Path,sys.argv[1:])
if not listing.exists(): raise SystemExit(0)
items=json.loads(listing.read_text(encoding="utf-8"))
with log.open("a",encoding="utf-8") as out:
  for rel in items:
    base=root / rel
    paths=[base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
    for p in paths:
      relpath=p.relative_to(root)
      b=backup / relpath
      rec={"output":str(p.absolute()),"type":"file","owner":"latticemind",
           "created":not b.is_file(),"replaced":b.is_file(),
           "sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
      if b.is_file():
        rec["backup"]=str(b.absolute())
        rec["backup_sha256"]=hashlib.sha256(b.read_bytes()).hexdigest()
      out.write(json.dumps(rec,sort_keys=True)+"\n")
PY
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
  if [[ -f "$VAULT/AGENTS.md" ]]; then cp "$VAULT/AGENTS.md" "$BACKUP_DIR/AGENTS.md.pre"; fi
  python3 "$SIGNED_SCRIPTS/upsert_block.py" \
    --file "$VAULT/AGENTS.md" --name LATTICEMIND \
    --heading "LatticeMind knowledge layer" \
    --body "Use the Obsidian commands installed under \`.opencode/commands/\`. Read \`_CLAUDE.md\` before vault writes and preserve existing prose and raw sources."
  if [[ -f "$BACKUP_DIR/AGENTS.md.pre" ]]; then record_operation "$VAULT/AGENTS.md" managed-block 0 "$BACKUP_DIR/AGENTS.md.pre" "" "<!-- LATTICEMIND:START -->"; else record_operation "$VAULT/AGENTS.md" managed-block 1 "" "" "<!-- LATTICEMIND:START -->"; fi
fi

if agent_enabled gemini; then
  printf '      Installing Gemini CLI commands...\n'
  merge_tree "$TMP_DIR/upstream/dist/gemini-cli/.gemini" \
    "$VAULT/.gemini" "gemini"
  if [[ -f "$VAULT/GEMINI.md" ]]; then cp "$VAULT/GEMINI.md" "$BACKUP_DIR/GEMINI.md.pre"; fi
  python3 "$SIGNED_SCRIPTS/upsert_block.py" \
    --file "$VAULT/GEMINI.md" --name LATTICEMIND \
    --heading "LatticeMind knowledge layer" \
    --body "Use the Obsidian commands installed under \`.gemini/commands/\`. Read \`_CLAUDE.md\` before vault writes and preserve existing prose and raw sources."
  if [[ -f "$BACKUP_DIR/GEMINI.md.pre" ]]; then record_operation "$VAULT/GEMINI.md" managed-block 0 "$BACKUP_DIR/GEMINI.md.pre" "" "<!-- LATTICEMIND:START -->"; else record_operation "$VAULT/GEMINI.md" managed-block 1 "" "" "<!-- LATTICEMIND:START -->"; fi
fi

if agent_enabled pi; then
  printf '      Installing Pi package resources...\n'
  merge_tree "$TMP_DIR/upstream/dist/pi/.pi" "$VAULT/.pi" "pi"
fi

if agent_enabled omp; then
  printf '      Installing OMP managed skills...\n'
  merge_tree "$TMP_DIR/upstream/dist/codex-cli/.agents/skills" \
    "$HOME/.omp/agent/managed-skills" "omp"
  python3 "$SIGNED_SCRIPTS/adapt_skills.py" \
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

record_named_manifest "$BACKUP_DIR/installed-shared.json" "$VAULT" "$BACKUP_DIR/vault"
record_named_manifest "$BACKUP_DIR/installed-codex.json" "$VAULT/.agents/skills" "$BACKUP_DIR/codex-skills"
record_named_manifest "$BACKUP_DIR/installed-gjc.json" "$HOME/.gjc/skills" "$BACKUP_DIR/gjc-skills"
VERSION="$(python3 - "$TMP_DIR/release-manifest-v1.json" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
if m.get("schema") != "release-manifest-v1" or not isinstance(m.get("version"), str):
    raise SystemExit("verified manifest missing canonical version")
print(m["version"])
PY
)"

STAGED_VERSION="$TMP_DIR/version-$VERSION"
mkdir "$STAGED_VERSION"
cp "$TMP_DIR/upstream/bin/latticemind" "$STAGED_VERSION/latticemind"
cp "$TMP_DIR/upstream/bin/latticemind-maintain" "$STAGED_VERSION/latticemind-maintain"
cp "$TMP_DIR/upstream/bin/latticemind-status" "$STAGED_VERSION/latticemind-status"
cp "$TMP_DIR/upstream/uninstall.sh" "$STAGED_VERSION/uninstall.sh"
cp -R "$TMP_DIR/upstream/latticemind_core" "$STAGED_VERSION/latticemind_core"
chmod 755 "$STAGED_VERSION/latticemind" "$STAGED_VERSION/latticemind-maintain" "$STAGED_VERSION/latticemind-status" "$STAGED_VERSION/uninstall.sh"
python3 -I - "$STAGED_VERSION" "$VERSIONS_DIR/$VERSION" <<'PY'
import hashlib, os, shutil, sys
from pathlib import Path
src, dst = map(Path, sys.argv[1:])
def digest(root):
    return sorted((str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest())
                  for p in root.rglob("*") if p.is_file())
if dst.exists():
    if not dst.is_dir() or digest(src) != digest(dst):
        raise SystemExit(f"immutable version collision: {dst}")
    shutil.rmtree(src)
else:
    os.replace(src, dst)
PY
ln -sfn "$VERSIONS_DIR/$VERSION" "$CURRENT_POINTER"
ln -sfn "$CURRENT_POINTER/latticemind" "$BIN_DIR/latticemind"
ln -sfn "$CURRENT_POINTER/latticemind-maintain" "$BIN_DIR/latticemind-maintain"
ln -sfn "$CURRENT_POINTER/latticemind-status" "$BIN_DIR/latticemind-status"
ln -sfn "$CURRENT_POINTER/uninstall.sh" "$DATA_DIR/uninstall.sh"
if [[ ! -e "$CONFIG_DIR/config-v1.json" ]]; then
  python3 - "$CONFIG_DIR/config-v1.json" "$VAULT" "$VERSION" <<'PY'
import json, os, sys
from pathlib import Path
p, vault, version = map(Path, sys.argv[1:])
data = {"schema":"config-v1","vault_path":str(vault),"profile":"observe",
        "enabled_jobs":[],"install_version":str(version)}
tmp = p.with_name(p.name + ".tmp")
tmp.write_text(json.dumps(data, sort_keys=True, separators=(",",":"))+"\\n")
os.replace(tmp, p)
PY
fi
append_transaction_records() {
  [[ -f "$EXTRA_MANIFEST" ]] || return 0
  while IFS= read -r item; do
    output="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["output"])' "$item")"
    backup="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("backup",""))' "$item")"
    if [[ -n "$backup" ]]; then record_operation "$output" file 0 "$backup"; else record_operation "$output" file 1; fi
  done < <(python3 - "$EXTRA_MANIFEST" <<'PY'
import json,sys
for item in json.loads(open(sys.argv[1],encoding="utf-8").read()): print(json.dumps(item))
PY
)
}

printf '[6/6] Configuring maintenance timers...\n'
if [[ "$PROFILE" == observe && "$INSTALL_SCHEDULE" -eq 1 ]]; then
  if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
    bash "$SIGNED_SCRIPTS/install-systemd.sh"
  elif command -v launchctl >/dev/null && [[ "$(uname -s)" == "Darwin" ]]; then
    bash "$SIGNED_SCRIPTS/install-launchd.sh"
  else
    printf '      Timers skipped (unsupported).\n'
  fi
else
  printf '      Timers skipped (non-observe profile or --no-schedule).\n'
fi
for p in "$VERSIONS_DIR/$VERSION/latticemind" "$VERSIONS_DIR/$VERSION/latticemind-maintain" "$VERSIONS_DIR/$VERSION/latticemind-status" "$VERSIONS_DIR/$VERSION/uninstall.sh"; do
  record_operation "$p" file 1
done
for p in "$BIN_DIR/latticemind" "$BIN_DIR/latticemind-maintain" "$BIN_DIR/latticemind-status" "$DATA_DIR/uninstall.sh"; do
  [[ -L "$p" ]] && record_operation "$p" symlink 1
done
[[ -L "$CURRENT_POINTER" ]] && record_operation "$CURRENT_POINTER" symlink 1
append_transaction_records
python3 - "$DATA_DIR/manifest-v1.json" "$TRANSACTION_LOG" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
out, log = map(Path, sys.argv[1:])
records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
for candidate in (Path(os.environ.get("LATTICEMIND_JOB_EXPORT", "")),
                  Path(os.environ.get("XDG_CONFIG_HOME", Path.home()/".config"))/"systemd/user/latticemind-jobs.json",
                  Path.home()/"Library/LaunchAgents/latticemind-jobs.json"):
    if candidate.is_file():
        for job in json.loads(candidate.read_text(encoding="utf-8")).get("jobs", []):
            p = Path(job["path"])
            if p.is_file():
                records.append({"output":str(p.absolute()),"type":"scheduler","owner":job["owner"],
                    "job_id":job.get("job_id"),"identity":job,
                    "sha256":hashlib.sha256(p.read_bytes()).hexdigest(),
                    "marker":"owner="+job["owner"]+" schema=job-definition-v1"})
        break
by_output = {}
order = []
for record in records:
    key = record["output"]
    if key not in by_output:
        by_output[key] = dict(record)
        order.append(key)
        continue
    original = by_output[key]
    latest = dict(record)
    latest["created"] = bool(original.get("created", False))
    latest["replaced"] = bool(original.get("replaced", False))
    if original.get("backup"):
        latest["backup"] = original["backup"]
        latest["backup_sha256"] = original.get("backup_sha256", "")
    by_output[key] = latest
records = [by_output[key] for key in order]
tmp = out.with_suffix(".tmp")
tmp.write_text(json.dumps({"schema":"manifest-v1","owned":records}, sort_keys=True, separators=(",",":"))+"\n")
os.replace(tmp, out)
PY
# shellcheck disable=SC2034
INSTALL_COMMITTED=1

printf '\nLatticeMind is ready.\n'
printf '  Vault:   %s\n' "$VAULT"
printf '  Status:  %s/latticemind-status\n' "$BIN_DIR"
printf '  Backup:  %s\n' "$BACKUP_DIR"
printf '  Remove:  bash %s/uninstall.sh\n\n' "$DATA_DIR"
