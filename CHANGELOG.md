# Changelog

All notable LatticeMind changes are summarized here. Release notes are
published only for artifacts that pass native CI and signed-release
verification.

## Next version

### Added

- Freshness reports now prepare at most 20 overdue claims for revalidation
  against current primary sources, bounded by volatility TTLs; evidence records
  are validated strictly and unsupported evidence is not accepted silently.
- Authenticated lifecycle state for signed update, rollback, and uninstall
  operations, including ownership manifests, snapshots, and fail-closed
  destination checks.
- Native Windows PowerShell installation, status, maintenance, uninstall, and
  Task Scheduler support.
- Native macOS launchd job rendering and installation alongside Linux systemd
  user timers.
- First-run `latticemind validate` and JSON `status` reporting for trust,
  backend capability, job parity, and degraded/blocked states.
- Explicit permission profiles and eight declared agent adapters: GJC, OMP,
  Codex, Claude Code, OpenCode, Pi, Gemini CLI, and Hermes.

### Changed

- Observe-only is now the default: freshness and health are the only enabled
  scheduled report jobs; write-oriented morning, nightly, and weekly jobs are
  opt-in and scheduled jobs remain observe-gated.
- Every backend fails closed unless its exact installed version has a verified
  read-only observe contract; executable discovery alone is insufficient.
- Updates stage and verify signed archives, preserve owned components in
  authenticated snapshots, migrate config, reinstall owned jobs, validate the
  lifecycle, and switch the current version only after those checks pass.
- Uninstall preserves vault notes and backups by default and refuses unsafe
  removal when owned bytes or scheduler markers have changed.
- Configuration and migration use strict `config-v1` data, preserve legacy
  installation bytes through receipts/backups, and expose platform-specific
  config/data paths.
- Documentation now describes one-command Unix/Windows installation, trust
  requirements, permission boundaries, scheduler behavior, exit classes,
  troubleshooting, limitations, and the signing-secret/native-CI production
  release gate.
