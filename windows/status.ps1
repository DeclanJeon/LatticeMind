$ErrorActionPreference = 'Stop'
$StateRoot = Join-Path $env:LOCALAPPDATA 'LatticeMind'
$ConfigPath = Join-Path $StateRoot 'config.json'
if (-not (Test-Path $ConfigPath)) {
    Write-Host 'LatticeMind: not installed'
    exit 1
}
$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$Vault = $Config.vault

function Count-Files([string]$Path, [string]$Filter) {
    if (-not (Test-Path $Path)) { return 0 }
    return @(Get-ChildItem -LiteralPath $Path -Recurse -File -Filter $Filter).Count
}

Write-Host 'LatticeMind'
Write-Host "  Version: $($Config.version)"
Write-Host "  Vault: $Vault"
Write-Host "  Codex: $(Count-Files (Join-Path $Vault '.agents\skills') 'SKILL.md') skills"
Write-Host "  GJC: $(Count-Files (Join-Path $HOME '.gjc\skills') 'SKILL.md') skills"
Write-Host "  Claude Code: $(Count-Files (Join-Path $HOME '.claude\commands') 'obsidian-*.md') commands"
Write-Host "  OpenCode: $(Count-Files (Join-Path $Vault '.opencode\commands') '*.md') commands"
Write-Host "  Gemini CLI: $(Count-Files (Join-Path $Vault '.gemini\commands') '*.md') commands"
Write-Host "  Pi: $(Count-Files (Join-Path $Vault '.pi\prompts') '*.md') prompts"
Write-Host "  OMP: $(Count-Files (Join-Path $HOME '.omp\agent\managed-skills') 'SKILL.md') skills"
Write-Host "  Hermes: $(Count-Files (Join-Path $HOME '.hermes\skills\obsidian-second-brain') 'SKILL.md') skills"
$FreshnessReport = Join-Path $Vault 'Logs\LatticeMind Freshness.md'
Write-Host "  Freshness report: $(if (Test-Path $FreshnessReport) { 'ready' } else { 'not run yet' })"
$Tasks = @(Get-ScheduledTask -TaskPath '\LatticeMind\' -ErrorAction SilentlyContinue)
Write-Host "  Scheduled tasks: $($Tasks.Count)/5"
