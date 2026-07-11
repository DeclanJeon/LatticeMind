<p align="center">
  <img src="assets/latticemind-banner.svg" alt="LatticeMind — the living knowledge layer" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/DeclanJeon/LatticeMind/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/DeclanJeon/LatticeMind/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-22d3ee?style=flat-square" alt="MIT License" /></a>
  <a href="https://github.com/eugeniughelbur/obsidian-second-brain"><img src="https://img.shields.io/badge/powered%20by-obsidian--second--brain-8b5cf6?style=flat-square" alt="Powered by obsidian-second-brain" /></a>
  <a href="https://github.com/DeclanJeon/LatticeMind/releases/latest"><img src="https://img.shields.io/github/v/release/DeclanJeon/LatticeMind?style=flat-square&color=22d3ee" alt="Latest release" /></a>
  <img src="https://img.shields.io/badge/notes-local%20first-111827?style=flat-square" alt="Local-first notes" />
</p>

<p align="center">
  <strong>Turn an existing Obsidian vault into durable memory for AI coding agents — safely, in one install.</strong>
</p>

LatticeMind is an opinionated installer and safety layer around
[obsidian-second-brain](https://github.com/eugeniughelbur/obsidian-second-brain),
inspired by [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).
It connects one local Markdown vault to **GJC, Codex, Claude Code, OpenCode,
Gemini CLI, Pi, OMP, and Hermes**, adds AI-first note rules, and schedules
bounded maintenance without turning an existing vault into an uncontrolled
rewrite experiment. Linux, macOS, and Windows are supported.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/DeclanJeon/LatticeMind/main/install.sh | \
  bash -s -- --vault "$HOME/Documents/Obsidian Vault" --name "Your Name" --preset builder
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/DeclanJeon/LatticeMind/main/install.ps1 |
  iex
```

To select a vault, preset, or agent subset:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/DeclanJeon/LatticeMind/main/install.ps1))) `
  -Vault "$HOME\Documents\My Vault" -Preset builder `
  -Agents codex,claude,opencode,omp
```

The default vault is `~/Documents/Obsidian Vault` when it exists, otherwise
`~/Obsidian/LatticeMind`. For security-sensitive environments, clone the repo,
review `install.sh`, and run it locally.

```bash
git clone https://github.com/DeclanJeon/LatticeMind.git
cd LatticeMind
bash scripts/install-local.sh --vault /path/to/vault --name "Your Name"
```

## What one install wires together

```text
                            local Markdown vault
                       /            |             \
                portable skills  AI-first rules  scheduled care
              /   /   /   /   \       |         /   |   |   \
           GJC OMP Codex Claude OpenCode ...   AM night week fresh health
```

| Layer | Installed behavior |
|---|---|
| Vault | Builder-ready folders, templates, Bases, dashboard, operating manual |
| Knowledge | Source-preserving notes, dated claims, confidence labels, wikilinks |
| Agent layer | GJC · Codex · Claude Code · OpenCode · Gemini CLI · Pi · OMP · Hermes |
| Automation | Morning note, nightly consolidation, weekly review, external freshness audit, health audit |
| Recovery | Timestamped backups and a content-preserving uninstaller |

Representative skills:

```text
obsidian-save        extract durable decisions and tasks from a session
obsidian-ingest      absorb a URL, document, image, transcript, or source
obsidian-find        search the vault with context
obsidian-architect   maintain codebase architecture notes
obsidian-reconcile   surface and resolve evidence-backed contradictions
obsidian-synthesize  discover patterns across linked notes
obsidian-health      audit structure without destructive cleanup
```

Invocation follows each agent's native model:

```text
GJC / OMP       /skill:obsidian-save
Codex           $obsidian-save
Claude Code     /obsidian-save
OpenCode        run the obsidian-save command or describe the task
Gemini CLI      /obsidian-save
Pi              /obsidian-save
Hermes          browse/install the obsidian-save native skill
```

## Safety model

Most second-brain demos assume an empty vault. Real vaults are not empty.
LatticeMind therefore makes these choices by default:

1. **Existing files win.** Scaffold files are copied only when the destination
   does not already exist.
2. **Skill collisions are backed up.** Replaced agent skills and command files
   are copied to a timestamped, file-level recovery directory.
3. **Raw evidence remains immutable.** Maintenance prompts preserve source
   material and user-authored prose.
4. **Automation has a narrow write scope.** Scheduled jobs operate on root
   system notes and dedicated managed folders, treating everything else as
   read-only evidence.
5. **No autonomous deletion.** Nightly and health jobs may report uncertainty;
   they do not delete notes.
6. **Uninstall preserves knowledge.** Vault notes and scaffold folders remain;
   only integration files are removed or restored from backup.

Nightly consolidation keeps the vault internally coherent. The separate freshness
loop checks whether date-sensitive claims still match current primary sources.
Reviewed notes use `last_verified`, `volatility`, and `verification_sources`;
unavailable or inconclusive sources are reported rather than silently accepted.

## Scheduled maintenance

On Linux, systemd user timers are installed. On Windows, equivalent Task
Scheduler jobs are registered:

| Timer | Default time | Purpose |
|---|---:|---|
| Morning | 08:07 | Create or update today's note from known facts |
| Nightly | 22:17 | Reconcile, synthesize, and inspect recent managed notes |
| Weekly | Friday 18:17 | Build an evidence-linked weekly review |
| Freshness | Sunday 19:17 | Revalidate up to 20 overdue claims against current primary sources |
| Health | Sunday 21:17 | Run a report-first structural audit |

GJC is preferred as the unattended backend, followed by OMP, Codex, Claude
Code, OpenCode, and Pi. Disable automation with `--no-schedule` on Unix or
`-NoSchedule` on Windows.

Run the external check manually with `latticemind-maintain freshness`. Volatility
TTLs are `high=7`, `medium=30`, `low=90`, and `static=365` days. The audit updates
`Logs/LatticeMind Freshness.md` in place and never treats URL availability alone
as factual verification.

## Installer options

```text
--vault PATH          Obsidian vault path
--name NAME           Vault owner name
--preset PRESET       default|builder|executive|creator|researcher
--agents LIST         all or gjc,codex,claude,opencode,gemini,pi,omp,hermes
--no-gjc              Skip global GJC skills
--no-codex            Skip Codex vault skills
--no-schedule         Skip systemd user timers
```

Check the installation:

```bash
~/.local/bin/latticemind-status
systemctl --user list-timers 'latticemind-*' --all
```

Remove the integration without deleting notes:

```bash
bash ~/.local/share/latticemind/uninstall.sh
```

```powershell
powershell -File "$env:LOCALAPPDATA\LatticeMind\current\uninstall.ps1"
```

## Requirements

- Linux, macOS, or Windows 10/11
- Unix: Bash, Git, and Python 3
- Windows: PowerShell 5.1+; release bundles require no Bash build step
- An existing or new Obsidian vault
- At least one supported agent CLI
- systemd user services or Windows Task Scheduler for optional maintenance

External freshness audits require the selected agent to have web or provider
access. Without it, claims remain blocked or `needs-review`; they are not marked
verified. Core vault commands do not require extra research API keys.

## Architecture

LatticeMind deliberately does not fork the knowledge engine. At install time it:

1. fetches the selected upstream `obsidian-second-brain` revision;
2. builds all native upstream distributions;
3. stages the chosen vault preset in a temporary directory;
4. merges only missing scaffold files into the target vault;
5. installs agent-specific resources with file-level backups;
6. adapts portable skills to GJC and OMP discovery;
7. installs bounded maintenance through systemd or Windows Task Scheduler.

This keeps the installer small, auditable, and aligned with upstream improvements.
Set `LATTICEMIND_UPSTREAM_REF` to pin a release or commit.

## Versioning and Releases

Every green push to `main` produces a new `v0.2.<run>` GitHub Release. Release
notes are generated from the commits since the previous version, and each
release carries `latticemind-dist.zip`: prebuilt agent distributions, all five
vault presets, and the Windows runtime. The PowerShell installer always consumes
the latest successful release rather than rebuilding Unix shell adapters.

## Development

```bash
bash -n install.sh uninstall.sh bin/* scripts/*.sh tests/*.sh
python3 -m py_compile scripts/*.py
shellcheck install.sh uninstall.sh bin/* scripts/*.sh tests/*.sh
bash tests/install-smoke.sh
```

The smoke test creates an isolated home directory and vault, verifies existing
content hashes, installs every agent integration, runs status checks, uninstalls,
and confirms the original skill and note content are restored.

## Credits

The core commands, schemas, and vault engine come from
[eugeniughelbur/obsidian-second-brain](https://github.com/eugeniughelbur/obsidian-second-brain)
under the MIT License. LatticeMind contributes cross-agent installation,
Windows support, non-destructive merging, GJC/OMP adaptation, recovery
manifests, automatic releases, and bounded scheduled maintenance.

## License

MIT © 2026 DeclanJeon. See [LICENSE](LICENSE).
