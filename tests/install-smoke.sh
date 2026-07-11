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
printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" > "$HOME/gjc-args"\nexit 0\n' > "$HOME/bin/gjc"
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
[[ -f "$HOME/.claude/commands/obsidian-save.md" ]]
[[ -f "$VAULT/.opencode/commands/obsidian-save.md" ]]
[[ -f "$VAULT/.gemini/commands/obsidian-save.md" ]]
[[ -f "$VAULT/.pi/prompts/obsidian-save.md" ]]
[[ -f "$HOME/.omp/agent/managed-skills/obsidian-save/SKILL.md" ]]
[[ -f "$HOME/.hermes/skills/obsidian-second-brain/vault/obsidian-save/SKILL.md" ]]
[[ -f "$VAULT/Templates/Daily Note.md" ]]
[[ -x "$HOME/.local/bin/latticemind-status" ]]
[[ -x "$HOME/.local/bin/latticemind-maintain" ]]
[[ "$(sha256sum "$VAULT/existing.md" | cut -d' ' -f1)" == "$BEFORE" ]]
[[ "$(grep -c 'LATTICEMIND:START' "$VAULT/AGENTS.md")" -eq 1 ]]
grep -Fq "The canonical vault root is \`$VAULT\`" "$HOME/.gjc/skills/obsidian-save/SKILL.md"
grep -Fq "$VAULT/.codex/references/ai-first-rules.md" "$HOME/.gjc/skills/obsidian-save/SKILL.md"
"$HOME/.local/bin/latticemind-status"
"$HOME/.local/bin/latticemind-maintain" freshness
grep -Fq -- '--skills obsidian-research,obsidian-reconcile,obsidian-health' "$HOME/gjc-args"
grep -Fq 'external freshness audit for at most 20 notes' "$HOME/gjc-args"
grep -Fq 'A reachable URL alone is not verification' "$HOME/gjc-args"
grep -Fq 'last_verified' "$HOME/gjc-args"
grep -Fq 'verification_sources' "$HOME/gjc-args"
grep -Fq 'write_service freshness' "$ROOT/scripts/install-systemd.sh"
grep -Fq 'latticemind-freshness.timer' "$ROOT/scripts/install-systemd.sh"
grep -Fq "Mode = 'freshness'" "$ROOT/windows/register-tasks.ps1"
grep -Fq "'freshness'" "$ROOT/windows/latticemind-maintain.ps1"

bash "$HOME/.local/share/latticemind/uninstall.sh"
[[ -f "$VAULT/existing.md" ]]
[[ "$(sha256sum "$VAULT/existing.md" | cut -d' ' -f1)" == "$BEFORE" ]]
grep -q 'description: old user skill' "$HOME/.gjc/skills/obsidian-save/SKILL.md"
[[ ! -f "$HOME/.claude/commands/obsidian-save.md" ]]
[[ ! -f "$VAULT/.opencode/commands/obsidian-save.md" ]]
[[ ! -f "$VAULT/.gemini/commands/obsidian-save.md" ]]
[[ ! -f "$VAULT/.pi/prompts/obsidian-save.md" ]]
[[ ! -f "$HOME/.omp/agent/managed-skills/obsidian-save/SKILL.md" ]]
[[ ! -f "$HOME/.hermes/skills/obsidian-second-brain/vault/obsidian-save/SKILL.md" ]]
if grep -q 'LATTICEMIND:START' "$VAULT/AGENTS.md"; then
  printf 'managed AGENTS block survived uninstall\n' >&2
  exit 1
fi
printf 'install/uninstall smoke test: ok\n'
