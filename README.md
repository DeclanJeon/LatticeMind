<p align="center">
  <img src="assets/latticemind-banner.svg" alt="LatticeMind — the living knowledge layer" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/DeclanJeon/LatticeMind/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/DeclanJeon/LatticeMind/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-22d3ee?style=flat-square" alt="MIT License" /></a>
  <a href="https://github.com/eugeniughelbur/obsidian-second-brain"><img src="https://img.shields.io/badge/powered%20by-obsidian--second--brain-8b5cf6?style=flat-square" alt="Powered by obsidian-second-brain" /></a>
  <img src="https://img.shields.io/badge/notes-local%20first-111827?style=flat-square" alt="Local-first notes" />
</p>

<p align="center">
  <strong>Turn an existing Obsidian vault into durable memory for AI coding agents — safely, in one install.</strong>
</p>

LatticeMind is an opinionated installer and safety layer around
[obsidian-second-brain](https://github.com/eugeniughelbur/obsidian-second-brain),
inspired by [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).
It connects one local Markdown vault to **GJC** and **Codex**, adds AI-first note
rules, and schedules bounded maintenance that can compound knowledge without
turning an existing vault into an uncontrolled rewrite experiment.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/DeclanJeon/LatticeMind/main/install.sh | \
  bash -s -- --vault "$HOME/Documents/Obsidian Vault" --name "Your Name" --preset builder
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
                    /           |           \
             native skills   AI-first rules   scheduled care
              /       \            |          /    |    |    \
            GJC      Codex       sources     AM  nightly week health
```

| Layer | Installed behavior |
|---|---|
| Vault | Builder-ready folders, templates, Bases, dashboard, operating manual |
| Knowledge | Source-preserving notes, dated claims, confidence labels, wikilinks |
| GJC | 43 globally discoverable user skills under `~/.gjc/skills/` |
| Codex | 43 native Agent Skills under `<vault>/.agents/skills/` |
| Automation | Morning note, nightly consolidation, weekly review, health audit |
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

With GJC:

```text
/skill:obsidian-save
/skill:obsidian-ingest https://example.com/article
/skill:obsidian-architect /path/to/codebase
```

With Codex, start in the vault and invoke a native skill:

```text
$obsidian-save
$obsidian-find "authentication decisions"
```

## Safety model

Most second-brain demos assume an empty vault. Real vaults are not empty.
LatticeMind therefore makes these choices by default:

1. **Existing files win.** Scaffold files are copied only when the destination
   does not already exist.
2. **Skill collisions are backed up.** Replaced GJC and Codex skills are copied
   to a timestamped recovery directory.
3. **Raw evidence remains immutable.** Maintenance prompts preserve source
   material and user-authored prose.
4. **Automation has a narrow write scope.** Scheduled jobs operate on root
   system notes and dedicated managed folders, treating everything else as
   read-only evidence.
5. **No autonomous deletion.** Nightly and health jobs may report uncertainty;
   they do not delete notes.
6. **Uninstall preserves knowledge.** Vault notes and scaffold folders remain;
   only integration files are removed or restored from backup.

## Scheduled maintenance

On Linux with a user systemd session, four persistent timers are enabled:

| Timer | Default time | Purpose |
|---|---:|---|
| Morning | 08:07 | Create or update today's note from known facts |
| Nightly | 22:17 | Reconcile, synthesize, and inspect recent managed notes |
| Weekly | Friday 18:17 | Build an evidence-linked weekly review |
| Health | Sunday 21:17 | Run a report-first structural audit |

GJC is preferred as the unattended agent backend. Codex is used as a fallback.
Disable automation during installation with `--no-schedule`.

## Installer options

```text
--vault PATH          Obsidian vault path
--name NAME           Vault owner name
--preset PRESET       default|builder|executive|creator|researcher
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

## Requirements

- Linux or macOS
- Bash, Git, and Python 3
- An existing or new Obsidian vault
- GJC and/or [Codex CLI](https://github.com/openai/codex)
- systemd user services for scheduled maintenance; optional

Research commands may require provider keys supported by the upstream project.
Core vault commands do not require extra research API keys.

## Architecture

LatticeMind deliberately does not fork the knowledge engine. At install time it:

1. fetches the selected upstream `obsidian-second-brain` revision;
2. builds its native Codex skill distribution;
3. stages the chosen vault preset in a temporary directory;
4. merges only missing scaffold files into the target vault;
5. installs shared references and scripts with backups;
6. adapts the generated skills to GJC's user-skill discovery;
7. installs bounded, lock-protected maintenance timers.

This keeps the installer small, auditable, and aligned with upstream improvements.
Set `LATTICEMIND_UPSTREAM_REF` to pin a release or commit.

## Development

```bash
bash -n install.sh uninstall.sh bin/* scripts/*.sh tests/*.sh
shellcheck install.sh uninstall.sh bin/* scripts/*.sh tests/*.sh
bash tests/install-smoke.sh
```

The smoke test creates an isolated home directory and vault, verifies existing
content hashes, installs both agent integrations, runs status checks, uninstalls,
and confirms the original skill and note content are restored.

## Credits

The core commands, schemas, and vault engine come from
[eugeniughelbur/obsidian-second-brain](https://github.com/eugeniughelbur/obsidian-second-brain)
under the MIT License. LatticeMind contributes cross-agent installation,
non-destructive merging, GJC adaptation, recovery manifests, and bounded systemd
automation.

## License

MIT © 2026 DeclanJeon. See [LICENSE](LICENSE).
