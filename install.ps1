[CmdletBinding()]
param(
    [string]$Vault = "$HOME\Documents\Obsidian Vault",
    [string]$Name = $env:USERNAME,
    [ValidateSet('default', 'builder', 'executive', 'creator', 'researcher')]
    [string]$Preset = 'builder',
    [string[]]$Agents = @('all'),
    [switch]$NoSchedule
)

$ErrorActionPreference = 'Stop'
$Repo = 'DeclanJeon/LatticeMind'
$StateRoot = Join-Path $env:LOCALAPPDATA 'LatticeMind'
$InstallRoot = Join-Path $StateRoot 'current'
$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$BackupRoot = Join-Path $StateRoot "backups\$Stamp"
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "latticemind-$([guid]::NewGuid())"
$Manifest = [System.Collections.Generic.List[object]]::new()

function Test-Agent([string]$Agent) {
    return $Agents -contains 'all' -or $Agents -contains $Agent
}

function Copy-TreeWithBackup([string]$Source, [string]$Destination, [string]$Label) {
    if (-not (Test-Path $Source)) { return }
    Get-ChildItem -LiteralPath $Source -Recurse -File | ForEach-Object {
        $Relative = $_.FullName.Substring($Source.Length).TrimStart('\', '/')
        $Output = Join-Path $Destination $Relative
        $Saved = Join-Path (Join-Path $BackupRoot $Label) $Relative
        $HadOriginal = Test-Path $Output
        if ($HadOriginal) {
            New-Item -ItemType Directory -Force -Path (Split-Path $Saved) | Out-Null
            Copy-Item -LiteralPath $Output -Destination $Saved -Force
        }
        New-Item -ItemType Directory -Force -Path (Split-Path $Output) | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $Output -Force
        $Manifest.Add([pscustomobject]@{
            output = $Output
            backup = $(if ($HadOriginal) { $Saved } else { '' })
        })
    }
}

function Copy-MissingTree([string]$Source, [string]$Destination) {
    Get-ChildItem -LiteralPath $Source -Recurse -File | ForEach-Object {
        $Relative = $_.FullName.Substring($Source.Length).TrimStart('\', '/')
        $Output = Join-Path $Destination $Relative
        if (-not (Test-Path $Output)) {
            New-Item -ItemType Directory -Force -Path (Split-Path $Output) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $Output
        }
    }
}

function Add-ManagedBlock([string]$Path, [string]$Body) {
    $Start = '<!-- LATTICEMIND:START -->'
    $End = '<!-- LATTICEMIND:END -->'
    $Block = "$Start`n## LatticeMind knowledge layer`n`n$Body`n$End"
    $Text = if (Test-Path $Path) { Get-Content -LiteralPath $Path -Raw } else { "# Agent Instructions`n" }
    if ($Text.Contains($Start) -and $Text.Contains($End)) {
        $Before = $Text.Substring(0, $Text.IndexOf($Start))
        $AfterAt = $Text.IndexOf($End) + $End.Length
        $Text = $Before + $Block + $Text.Substring($AfterAt)
    } else {
        $Text = $Text.TrimEnd() + "`n`n$Block`n"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
    Set-Content -LiteralPath $Path -Value $Text -Encoding utf8
}

function Adapt-PortableSkills([string]$Root, [string]$Source, [string]$Label) {
    $VaultPosix = $Vault.Replace('\', '/')
    Get-ChildItem -LiteralPath $Source -Directory | ForEach-Object {
        $Skill = Join-Path (Join-Path $Root $_.Name) 'SKILL.md'
        if (-not (Test-Path $Skill)) { return }
        $Text = Get-Content -LiteralPath $Skill -Raw
        $Preamble = "## LatticeMind $Label binding`n`nThe canonical vault root is ``$VaultPosix``. Resolve relative vault paths against this root. Preserve existing prose and immutable raw sources.`n`n"
        $Marker = "---`n`n"
        $At = $Text.IndexOf($Marker)
        if ($At -ge 0) {
            $Text = $Text.Substring(0, $At + $Marker.Length) + $Preamble + $Text.Substring($At + $Marker.Length)
        }
        $Text = $Text.Replace('references/', "$VaultPosix/.codex/references/")
        $Text = $Text.Replace('scripts/', "$VaultPosix/.codex/scripts/")
        Set-Content -LiteralPath $Skill -Value $Text -Encoding utf8
    }
}

try {
    New-Item -ItemType Directory -Force -Path $TempRoot, $Vault, $BackupRoot, $InstallRoot | Out-Null
    Write-Host 'LatticeMind for Windows - downloading the latest release...'
    $Release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
    $Asset = $Release.assets | Where-Object name -eq 'latticemind-dist.zip' | Select-Object -First 1
    if (-not $Asset) { throw 'The latest release has no latticemind-dist.zip asset.' }
    $Zip = Join-Path $TempRoot 'latticemind-dist.zip'
    Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $Zip
    Expand-Archive -LiteralPath $Zip -DestinationPath $TempRoot -Force

    $Payload = Join-Path $TempRoot 'payload'
    Copy-MissingTree (Join-Path $Payload "scaffolds\$Preset") $Vault
    Copy-TreeWithBackup (Join-Path $Payload 'dist\codex-cli\.codex') (Join-Path $Vault '.codex') 'shared'

    if (Test-Agent 'codex') {
        Copy-TreeWithBackup (Join-Path $Payload 'dist\codex-cli\.agents') (Join-Path $Vault '.agents') 'codex'
    }
    if (Test-Agent 'claude') {
        Copy-TreeWithBackup (Join-Path $Payload 'dist\claude-code') (Join-Path $HOME '.claude\skills\obsidian-second-brain') 'claude-skill'
        Copy-TreeWithBackup (Join-Path $Payload 'dist\claude-code\commands') (Join-Path $HOME '.claude\commands') 'claude-commands'
    }
    if (Test-Agent 'opencode') {
        Copy-TreeWithBackup (Join-Path $Payload 'dist\opencode\.opencode') (Join-Path $Vault '.opencode') 'opencode'
    }
    if (Test-Agent 'gemini') {
        Copy-TreeWithBackup (Join-Path $Payload 'dist\gemini-cli\.gemini') (Join-Path $Vault '.gemini') 'gemini'
        Add-ManagedBlock (Join-Path $Vault 'GEMINI.md') 'Use `.gemini/commands/` and read `_CLAUDE.md` before vault writes.'
    }
    if (Test-Agent 'pi') {
        Copy-TreeWithBackup (Join-Path $Payload 'dist\pi\.pi') (Join-Path $Vault '.pi') 'pi'
    }
    if (Test-Agent 'gjc') {
        $Target = Join-Path $HOME '.gjc\skills'
        Copy-TreeWithBackup (Join-Path $Payload 'dist\codex-cli\.agents\skills') $Target 'gjc'
        Adapt-PortableSkills $Target (Join-Path $Payload 'dist\codex-cli\.agents\skills') 'GJC'
    }
    if (Test-Agent 'omp') {
        $Target = Join-Path $HOME '.omp\agent\managed-skills'
        Copy-TreeWithBackup (Join-Path $Payload 'dist\codex-cli\.agents\skills') $Target 'omp'
        Adapt-PortableSkills $Target (Join-Path $Payload 'dist\codex-cli\.agents\skills') 'OMP'
    }
    if (Test-Agent 'hermes') {
        $Target = Join-Path $HOME '.hermes\skills\obsidian-second-brain'
        Copy-TreeWithBackup (Join-Path $Payload 'dist\hermes\skills') $Target 'hermes-skills'
        Copy-TreeWithBackup (Join-Path $Payload 'dist\hermes\references') (Join-Path $Target 'references') 'hermes-references'
        Copy-TreeWithBackup (Join-Path $Payload 'dist\hermes\scripts') (Join-Path $Target 'scripts') 'hermes-scripts'
    }

    Add-ManagedBlock (Join-Path $Vault 'AGENTS.md') 'Use installed LatticeMind skills. Read `_CLAUDE.md` and `.codex/references/ai-first-rules.md` before vault writes. Preserve existing prose and raw sources.'
    Copy-Item (Join-Path $Payload 'windows\*') -Destination $InstallRoot -Recurse -Force
    $Manifest | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $BackupRoot 'manifest.json') -Encoding utf8
    [pscustomobject]@{
        version = $Release.tag_name
        vault = $Vault
        backup = $BackupRoot
        agents = $Agents
    } | ConvertTo-Json | Set-Content (Join-Path $StateRoot 'config.json') -Encoding utf8

    if (-not $NoSchedule) {
        & (Join-Path $InstallRoot 'register-tasks.ps1') -InstallRoot $InstallRoot
    }

    Write-Host "LatticeMind $($Release.tag_name) installed."
    Write-Host "Vault: $Vault"
    Write-Host "Status: powershell -File `"$(Join-Path $InstallRoot 'status.ps1')`""
    Write-Host "Remove: powershell -File `"$(Join-Path $InstallRoot 'uninstall.ps1')`""
}
finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
