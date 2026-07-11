<p align="center">
  <img src="assets/latticemind-banner.svg" alt="LatticeMind — the local knowledge layer" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/DeclanJeon/LatticeMind/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/DeclanJeon/LatticeMind/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <a href="https://github.com/DeclanJeon/LatticeMind/releases"><img src="https://img.shields.io/github/v/release/DeclanJeon/LatticeMind?style=flat-square&color=22d3ee" alt="Latest release" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-22d3ee?style=flat-square" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/local--first-notes-111827?style=flat-square" alt="Local-first notes" />
</p>

<p align="center"><strong>Install a cautious, local knowledge layer for AI coding agents.</strong></p>

LatticeMind connects an existing Markdown/Obsidian vault to a small, explicit
maintenance control plane. It preserves source material, records evidence, and
fails closed when an agent or release cannot be trusted. It does not replace
Obsidian or your agent CLI.

## Install

The supported install path is one command on Unix or Windows. Release payloads
are accepted only when the signed manifest, signature, and archive pass the
pinned trust check; an unsigned or tampered release is rejected.
The piped entry scripts fetch only the pinned verifier and bootstrap support from
the repository. After verification, every installed lifecycle, runtime, and
control-plane byte is copied from the signed extracted `upstream/` payload;
download failures or missing payload members abort the install.

Unix (Linux and macOS):

```bash
curl -fsSL https://raw.githubusercontent.com/DeclanJeon/LatticeMind/main/install.sh | \
  bash -s -- --vault "$HOME/Documents/Obsidian Vault" --name "Your Name"
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/DeclanJeon/LatticeMind/main/install.ps1 | iex
```

Use `--preset`, `--agents`, and `--no-schedule` on Unix, or `-Preset`,
`-Agents`, and `-NoSchedule` on Windows. For a security-sensitive install,
clone the repository, review the installer, and run `bash scripts/install-local.sh`
(or the local PowerShell installer) instead of piping a remote script.

## First run

The first checks are deliberately read-only:

```bash
latticemind validate
latticemind status --json
```

`validate` checks the command contract. `status` reports trust, vault access,
backend capabilities, desired/effective jobs, and an exit class. A newly staged
or unverified installation is `degraded`/`blocked`, not silently operational.
The normal default is the **observe** permission profile: scheduled jobs can
read evidence and write reports only where their explicit job contract allows.
On Windows, the public CLI entry point is the signed embedded-runtime wrapper:
`powershell -File "$env:LOCALAPPDATA\LatticeMind\current\payload\latticemind.ps1"`.
Pass the same lifecycle commands (`validate`, `status`, `freshness`, `schedule`,
`update`, `rollback`, and `migrate`) to this wrapper; it never falls back to
PATH Python.

## What is installed

- A vault scaffold, templates, Bases, dashboard, and operating guidance.
- Native integrations for **GJC, OMP, Codex, Claude Code, OpenCode, Pi,
  Gemini CLI, and Hermes** (selected with `--agents`).
- A signed release manifest and an ownership/backup manifest for integration
  files.
- Five deterministic job definitions. Only these two report jobs are enabled
  by default:

| Job | Default | Permission and purpose |
|---|---:|---|
| `freshness` | enabled | Observe current primary sources and report overdue claims |
| `health` | enabled | Observe structure and report health findings |
| `morning` | disabled | Optional scheduled write: create/update the daily note |
| `nightly` | disabled | Optional scheduled write: reconcile managed notes |
| `weekly` | disabled | Optional scheduled write: build a weekly review |

Freshness is bounded to overdue claims and prepares at most 20 candidates for
revalidation against current primary sources. Volatility TTLs are 7/30/90/365
days (`high`/`medium`/`low`/`static`). URL availability is not factual
verification; unavailable or inconclusive sources remain reported for review.

## Permission profiles

Profiles are explicit and never grant deletion:

| Profile | Read vault | Metadata write | Create | Delete | Managed write |
|---|---:|---:|---:|---:|---:|
| `observe` | yes | no | no | no | no |
| `safe-write` | yes | no | yes | no | no |
| `managed-write` | yes | yes | yes | no | yes |
| `full` | yes | yes | yes | no | yes |

Scheduled jobs require `observe`; write-capable profiles are for explicit,
interactive workflows. Agent execution is also observe-only: every backend is
blocked unless its exact installed version has a verified observe contract.
No version is treated as verified merely because the executable is present.
Installers accept an explicit `--profile` (Unix) or `-Profile` (Windows);
the default is `observe` and the selected value is persisted in `config-v1.json`.
Non-`observe` profiles are interactive-only: installers never schedule write
jobs (and observe scheduling remains limited to the freshness and health report
jobs). Unix-created LatticeMind state, config, transaction, manifest, backup,
and installed executable directories/files use `umask 077` (directories 0700,
files 0600 unless executable). Shared integration parent directories are merged
per product-owned child output and unrelated user content is preserved with
backups.

## Scheduling

The installer renders native user-level jobs, with ownership markers and a
15-minute execution limit:

- Linux: systemd user timers (`~/.config/systemd/user`).
- macOS: launchd agents (`~/Library/LaunchAgents`).
- Windows: Task Scheduler tasks under `\\LatticeMind\\` at least privilege.

Schedules use local time, one run per slot, bounded catch-up, and no overlapping
runs. Unix scheduling can be skipped with `--no-schedule`; Windows with
`-NoSchedule`. Inspect definitions with:

```bash
latticemind schedule status
latticemind schedule render --platform systemd --job freshness
```

Run a report manually with `latticemind freshness scan --vault /path/to/vault`.
On Windows, use the installed wrapper for the same lifecycle operations:
`powershell -File "$env:LOCALAPPDATA\LatticeMind\current\payload\latticemind.ps1" schedule status`.

## Files and paths

| Purpose | Unix default | Windows default |
|---|---|---|
| Config | `${XDG_CONFIG_HOME:-~/.config}/latticemind/config-v1.json` | `%LOCALAPPDATA%\\LatticeMind\\config-v1.json` |
| State, versions, snapshots, logs | `${XDG_DATA_HOME:-~/.local/share}/latticemind` | `%LOCALAPPDATA%\\LatticeMind` |
| Vault | installer selection (fallback `~/Obsidian`) | installer selection |
| User command/status | `~/.local/bin/latticemind` | `latticemind.ps1` under `%LOCALAPPDATA%\\LatticeMind\\current\\payload` |

`LATTICEMIND_CONFIG`, `LATTICEMIND_STATE_ROOT`, and `LATTICEMIND_VAULT` can
select Unix locations. Configuration is strict `config-v1` JSON; paths with
traversal or unknown fields are rejected.

## Updates, rollback, and uninstall

Lifecycle operations require a signed manifest and matching archive. `update
--check` verifies trust and reports whether a newer version is available;
`update --apply` stages the archive, snapshots owned components, migrates
configuration, reinstalls owned jobs, validates the lifecycle, and switches the
current pointer atomically. `rollback` accepts only authenticated trusted
snapshot state and a compatible signed version. These operations fail closed on
missing signatures, mismatched versions, altered snapshots, or untrusted
destinations.

Remove the integration and restore owned files from their verified backups:

```bash
bash ~/.local/share/latticemind/uninstall.sh
# Add --purge-state only when the release state and snapshots should also go.
```

On Windows:

```powershell
powershell -File "$env:LOCALAPPDATA\LatticeMind\current\payload\uninstall.ps1"
```

Uninstall does not delete vault notes or backups by default. It refuses to
remove an owned file whose bytes or ownership marker changed unexpectedly.

## Supported agents and limitations

The eight declared backends are GJC, OMP, Codex, Claude Code, OpenCode, Pi,
Gemini CLI, and Hermes. They are not interchangeable: each has an explicit
read-only argv contract, environment allowlist, output schema, and optional
network-research flag. The current safe default is blocked until a backend
version is explicitly verified. Network research is provider-dependent and
requires the selected agent's own access; LatticeMind does not supply API keys.

LatticeMind cannot prove that an external source is true, prevent a user from
changing a vault outside its ownership manifest, or make an unverified agent
safe. It reports uncertainty instead of guessing. Existing files win during
installation; collisions are backed up before replacement, and scheduled
maintenance does not autonomously delete notes.

## Troubleshooting and exit codes

Start with `latticemind validate` and `latticemind status --json`. A `blocked`
or `degraded` result is a safety signal, not a successful run. Check the
reported `message`, `trust`, `backend`, `jobs`, and `log` fields; inspect the
native scheduler and confirm the vault path is readable. Common classes are:

| Code | Meaning |
|---:|---|
| 0 | OK |
| 2 | degraded status |
| 3 | disabled profile |
| 65 | corrupt/invalid input |
| 69 | runtime unavailable or blocked backend |
| 73 | permission denied |
| 74 | I/O failure |
| 75 | lock already held |
| 76 | release trust/signature failure |
| 77 | security policy blocked the operation |
| 78 | migration or operation failure |
| 124 | timed out |

Do not bypass signature verification or run an unknown backend with write
permissions to work around a failure.

## Contributing and release readiness

Contributors should verify their change with the repository's documented
syntax, compile, lint, and smoke checks, and inspect `validate`/`status` output
for a clean isolated install. Native Unix, macOS, and Windows paths must be
covered before claiming cross-platform support.

Production releases are intentionally not reproducible from a public checkout:
publishing requires the repository's signing secret and successful native Linux,
macOS, and Windows CI. The release workflow publishes only after those gates
and attaches the signed manifest, signature, and archive. Do not claim a
production release without both the secret-backed signing step and native CI.
Windows release bundles include official Python 3.11.9 embedded x64 and ARM64
packages. CI executes the x64 package on its Windows x64 runner and verifies
the ARM64 package's identity and selection; it does not claim ARM64 execution
because that runner cannot execute ARM64 binaries.

## Credits and license

The knowledge engine and schemas are from
[obsidian-second-brain](https://github.com/eugeniughelbur/obsidian-second-brain),
under MIT. LatticeMind adds cross-agent installation, signed lifecycle
management, native scheduling, migration, and bounded reports.

MIT © 2026 DeclanJeon. See [LICENSE](LICENSE).
