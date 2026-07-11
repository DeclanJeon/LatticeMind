[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('morning', 'nightly', 'weekly', 'health')]
    [string]$Mode
)

$ErrorActionPreference = 'Stop'
$StateRoot = Join-Path $env:LOCALAPPDATA 'LatticeMind'
$Config = Get-Content (Join-Path $StateRoot 'config.json') -Raw | ConvertFrom-Json
$Vault = $Config.vault
$Scope = 'root system notes plus Daily/, Dev Logs/, Knowledge/, Ideas/, Tasks/, Architecture/, Debugging/, Boards/, and Logs/'

switch ($Mode) {
    'morning' {
        $Skills = 'obsidian-daily'
        $Prompt = "Use obsidian-daily to update today's note from known vault facts. Do not invent events, tasks, or status."
    }
    'nightly' {
        $Skills = 'obsidian-reconcile,obsidian-synthesize,obsidian-health'
        $Prompt = "Run bounded nightly maintenance on $Scope and notes changed in the last 14 days. Treat every other folder as read-only evidence. Preserve raw sources and prose. Never delete notes."
    }
    'weekly' {
        $Skills = 'obsidian-review'
        $Prompt = "Create this week's evidence-linked review from notes changed in the last seven days under $Scope. Leave unknowns explicit."
    }
    'health' {
        $Skills = 'obsidian-health'
        $Prompt = "Run a report-first audit limited to $Scope. Fix only deterministic structural defects. Never delete or rewrite user-authored prose."
    }
}

$Mutex = [System.Threading.Mutex]::new($false, 'Local\LatticeMindMaintenance')
if (-not $Mutex.WaitOne(0)) { exit 75 }
try {
    Push-Location $Vault
    if (Get-Command gjc -ErrorAction SilentlyContinue) {
        & gjc -p --no-session --skills $Skills $Prompt
    } elseif (Get-Command omp -ErrorAction SilentlyContinue) {
        & omp -p --no-session --skills $Skills --approval-mode write $Prompt
    } elseif (Get-Command codex -ErrorAction SilentlyContinue) {
        & codex exec --ephemeral --skip-git-repo-check --sandbox workspace-write -C $Vault $Prompt
    } elseif (Get-Command claude -ErrorAction SilentlyContinue) {
        & claude -p --permission-mode acceptEdits $Prompt
    } elseif (Get-Command opencode -ErrorAction SilentlyContinue) {
        & opencode run --dir $Vault $Prompt
    } elseif (Get-Command pi -ErrorAction SilentlyContinue) {
        & pi -p --no-session --approve $Prompt
    } else {
        throw 'No supported agent CLI is available.'
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
    $Mutex.ReleaseMutex()
    $Mutex.Dispose()
}
