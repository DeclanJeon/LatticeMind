# Changelog

All notable LatticeMind changes are summarized here. GitHub Releases are
created automatically after every successful `main` CI run and contain the
exact commit delta since the prior version.

## Unreleased

### Added

- Native Windows PowerShell installer and Task Scheduler maintenance.
- Claude Code, OpenCode, Gemini CLI, Pi, OMP, and Hermes integrations.
- Prebuilt multi-agent release bundle with all five vault presets.
- Automatic `v0.2.<run>` versioning, generated release notes, and latest asset.
- Cross-platform file-level backup and uninstall manifests.
- Weekly external freshness audits revalidate overdue claims against current primary sources with volatility-based TTLs.

### Changed

- Unix installation now builds every upstream agent adapter.
- Scheduled maintenance can use GJC, OMP, Codex, Claude Code, OpenCode, or Pi.
- Status output reports every supported agent integration and installed version.
- Scheduled maintenance now reports and installs five jobs, including the separate freshness loop.
