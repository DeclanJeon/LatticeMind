#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/latticemind-test.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
export HOME="$SANDBOX/home"
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_DATA_HOME="$HOME/.local/share"
export PATH="$HOME/bin:$PATH"
VAULT="$SANDBOX/vault"
mkdir -p "$HOME/bin" "$HOME/.gjc/skills/obsidian-save" "$VAULT"
printf '#!/usr/bin/env bash\nexit 0\n' > "$HOME/bin/gjc"
chmod 755 "$HOME/bin/gjc"
printf '%s\n' '# Existing vault instructions' > "$VAULT/AGENTS.md"
printf '%s\n' '# User-authored note' 'This must survive installation.' > "$VAULT/existing.md"
printf '%s\n' '---' 'name: obsidian-save' 'description: old user skill' '---' > "$HOME/.gjc/skills/obsidian-save/SKILL.md"
BEFORE="$(sha256sum "$VAULT/existing.md" | cut -d' ' -f1)"

bash "$ROOT/scripts/install-local.sh" \
  --vault "$VAULT" --name "Test User" --preset builder --no-schedule

[[ -f "$VAULT/.agents/skills/obsidian-save/SKILL.md" ]]
[[ -f "$HOME/.gjc/skills/obsidian-save/SKILL.md" ]]
[[ -f "$VAULT/.codex/references/ai-first-rules.md" ]]
[[ -f "$VAULT/Templates/Daily Note.md" ]]
[[ -x "$HOME/.local/bin/latticemind-status" ]]
[[ "$(sha256sum "$VAULT/existing.md" | cut -d' ' -f1)" == "$BEFORE" ]]
[[ "$(grep -c 'LATTICEMIND:START' "$VAULT/AGENTS.md")" -eq 1 ]]
grep -Fq "The canonical vault root is \`$VAULT\`" "$HOME/.gjc/skills/obsidian-save/SKILL.md"
grep -Fq "$VAULT/.codex/references/ai-first-rules.md" "$HOME/.gjc/skills/obsidian-save/SKILL.md"
"$HOME/.local/bin/latticemind-status"

bash "$HOME/.local/share/latticemind/uninstall.sh"
[[ -f "$VAULT/existing.md" ]]
[[ "$(sha256sum "$VAULT/existing.md" | cut -d' ' -f1)" == "$BEFORE" ]]
grep -q 'description: old user skill' "$HOME/.gjc/skills/obsidian-save/SKILL.md"
if grep -q 'LATTICEMIND:START' "$VAULT/AGENTS.md"; then
  printf 'managed AGENTS block survived uninstall\n' >&2
  exit 1
fi
printf 'install/uninstall smoke test: ok\n'
