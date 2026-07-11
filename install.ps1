[CmdletBinding()]
param(
    [string]$Vault = "$HOME\Documents\Obsidian Vault",
    [string]$Name = $env:USERNAME,
    [ValidateSet('default', 'builder', 'executive', 'creator', 'researcher')]
    [string]$Preset = 'default',
    [string[]]$Agents = @('all'),
    [switch]$NoSchedule,
    [ValidateSet('x64','arm64')]
[string]$Architecture = $(switch ((if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }).ToUpperInvariant()) { 'AMD64' { 'x64' } 'ARM64' { 'arm64' } default { throw "Unsupported Windows architecture." } }),
    [ValidateSet('observe', 'safe-write', 'managed-write', 'full')]
    [string]$Profile = 'observe'
)

$ErrorActionPreference = 'Stop'
$Repo = 'DeclanJeon/LatticeMind'
$StateRoot = Join-Path $env:LOCALAPPDATA 'LatticeMind'
$InstallRoot = Join-Path $StateRoot 'current'
$VersionsRoot = Join-Path $StateRoot 'versions'
$CurrentPointer = Join-Path $StateRoot 'current'
$InstallRoot = Join-Path $CurrentPointer 'payload'
$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$BackupRoot = Join-Path $StateRoot "backups\$Stamp"
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "latticemind-$([guid]::NewGuid())"
$Manifest = [System.Collections.Generic.List[object]]::new()
$Verifier = $null
$ManifestUrl = Join-Path $TempRoot 'release-manifest-v1.json'
$SignatureUrl = Join-Path $TempRoot 'release-manifest-v1.sig'
$AssetUrl = Join-Path $TempRoot 'latticemind-dist.zip'
$BootstrapRoot = if ($PSScriptRoot) { $PSScriptRoot } else { $TempRoot }
$Verifier = Join-Path $BootstrapRoot 'bootstrap\latticemind-verify.ps1'
if (-not $PSScriptRoot) {
    New-Item -ItemType Directory -Force -Path (Join-Path $BootstrapRoot 'bootstrap'), (Join-Path $BootstrapRoot 'latticemind_core') | Out-Null
    $BootstrapBase = if ($env:LATTICEMIND_BOOTSTRAP_BASE) { $env:LATTICEMIND_BOOTSTRAP_BASE } else { 'https://raw.githubusercontent.com/DeclanJeon/LatticeMind/main' }
    Invoke-WebRequest -Uri "$BootstrapBase/bootstrap/latticemind-verify.ps1" -OutFile $Verifier -UseBasicParsing
    Invoke-WebRequest -Uri "$BootstrapBase/latticemind_core/release.py" -OutFile (Join-Path $BootstrapRoot 'latticemind_core\release.py') -UseBasicParsing
    Invoke-WebRequest -Uri "$BootstrapBase/latticemind_core/trust_root.py" -OutFile (Join-Path $BootstrapRoot 'latticemind_core\trust_root.py') -UseBasicParsing
    Invoke-WebRequest -Uri "$BootstrapBase/latticemind_core/__init__.py" -OutFile (Join-Path $BootstrapRoot 'latticemind_core\__init__.py') -UseBasicParsing
    $BootstrapPins = @{
        $Verifier = '67c919617ee354825374516574219a0b1774aabdd50a9069c32060a5225a94dd'
        (Join-Path $BootstrapRoot 'latticemind_core\release.py') = 'd4c1f8c1cc5d45998cda64c3739bb2be6c5a929fe6f1387b25122127c6867758'
        (Join-Path $BootstrapRoot 'latticemind_core\trust_root.py') = '0d005eab9b2f4df946e90ed0db6e44ad1320309023a05d228a79ce8ba40f0f11'
        (Join-Path $BootstrapRoot 'latticemind_core\__init__.py') = '9d7c4b56155e3f94a61293858aea80fa312975bd92ca2d413f95c7f1f0f5d536'
    }
    foreach ($PinnedFile in $BootstrapPins.Keys) {
        if ((Get-FileHash $PinnedFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $BootstrapPins[$PinnedFile]) {
            throw "Pinned bootstrap hash mismatch: $PinnedFile"
        }
    }
}

function Test-Agent([string]$Agent) {
    return $Agents -contains 'all' -or $Agents -contains $Agent
}

function Get-FileMetadata([string]$Path) {
    $Item = Get-Item -LiteralPath $Path -Force
    $Acl = Get-Acl -LiteralPath $Path
    return @{
        attributes = [int]$Item.Attributes
        creation_time_utc = $Item.CreationTimeUtc.ToString('o')
        last_write_time_utc = $Item.LastWriteTimeUtc.ToString('o')
        last_access_time_utc = $Item.LastAccessTimeUtc.ToString('o')
        acl_sddl = $Acl.Sddl
    }
}
function Copy-TreeWithBackup([string]$Source, [string]$Destination, [string]$Label) {
    if (-not (Test-Path $Source)) { throw "Signed payload member missing: $Source" }
    Get-ChildItem -LiteralPath $Source -Recurse -File | ForEach-Object {
        $Relative = $_.FullName.Substring($Source.Length).TrimStart('\', '/')
        $Output = Join-Path $Destination $Relative
        $Saved = Join-Path (Join-Path $BackupRoot $Label) $Relative
        $HadOriginal = Test-Path $Output
        $Metadata = if ($HadOriginal) { Get-FileMetadata $Output } else { @{} }
        if ($HadOriginal) {
            New-Item -ItemType Directory -Force -Path (Split-Path $Saved) | Out-Null
            Copy-Item -LiteralPath $Output -Destination $Saved -Force
        }
        New-Item -ItemType Directory -Force -Path (Split-Path $Output) | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $Output -Force
        $Manifest.Add([pscustomobject]@{
            output = $Output; type = 'file'; owner = 'latticemind'
            created = -not $HadOriginal; replaced = $HadOriginal
            sha256 = (Get-FileHash $Output -Algorithm SHA256).Hash.ToLowerInvariant()
            backup = $(if ($HadOriginal) { $Saved } else { '' })
            backup_sha256 = $(if ($HadOriginal) { (Get-FileHash $Saved -Algorithm SHA256).Hash.ToLowerInvariant() } else { '' })
            backup_metadata = $(if ($HadOriginal) { $Metadata } else { $null })
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
            $Manifest.Add([pscustomobject]@{
                output = $Output; type = 'file'; owner = 'latticemind'; created = $true; replaced = $false
                sha256 = (Get-FileHash $Output -Algorithm SHA256).Hash.ToLowerInvariant(); backup = ''; backup_sha256 = ''
            })
        }
    }
}

function Add-ManagedBlock([string]$Path, [string]$Body) {
    $Start = '<!-- LATTICEMIND:START -->'
    $End = '<!-- LATTICEMIND:END -->'
    $Block = "$Start`n## LatticeMind knowledge layer`n`n$Body`n$End"
    $Text = if (Test-Path $Path) { Get-Content -LiteralPath $Path -Raw } else { "# Agent Instructions`n" }
    $HadOriginal = Test-Path -LiteralPath $Path
    $Saved = Join-Path $BackupRoot ("managed-" + [IO.Path]::GetFileName($Path) + ".bak")
    $Metadata = if ($HadOriginal) { Get-FileMetadata $Path } else { @{} }
    if ($HadOriginal) {
        New-Item -ItemType Directory -Force -Path (Split-Path $Saved) | Out-Null
        Copy-Item -LiteralPath $Path -Destination $Saved -Force
    }
    if ($Text.Contains($Start) -and $Text.Contains($End)) {
        $Before = $Text.Substring(0, $Text.IndexOf($Start))
        $AfterAt = $Text.IndexOf($End) + $End.Length
        $Text = $Before + $Block + $Text.Substring($AfterAt)
    } else {
        $Text = $Text.TrimEnd() + "`n`n$Block`n"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
    Set-Content -LiteralPath $Path -Value $Text -Encoding utf8
    $Manifest.Add([pscustomobject]@{
        output = $Path; type = 'managed-block'; owner = 'latticemind'
        created = -not $HadOriginal; replaced = $HadOriginal
        sha256 = (Get-FileHash $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        backup = $(if ($HadOriginal) { $Saved } else { '' })
        backup_sha256 = $(if ($HadOriginal) { (Get-FileHash $Saved -Algorithm SHA256).Hash.ToLowerInvariant() } else { '' })
        backup_metadata = $(if ($HadOriginal) { $Metadata } else { $null })
        marker = '<!-- LATTICEMIND:START -->'
    })
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
function Assert-Preflight {
    $Destinations = @(
        (Join-Path $Vault '.codex'), (Join-Path $Vault '.agents'),
        (Join-Path $Vault '.opencode'), (Join-Path $Vault '.gemini'),
        (Join-Path $Vault '.pi'),
        (Join-Path $HOME '.claude\skills\obsidian-second-brain'),
        (Join-Path $HOME '.hermes\skills\obsidian-second-brain'))
    foreach ($Destination in $Destinations) {
        if (-not (Test-Path -LiteralPath $Destination)) { continue }
        $Item = Get-Item -LiteralPath $Destination
        $Marker = if ($Item.PSIsContainer) { Join-Path $Destination '.latticemind-owned' } else { $Destination }
        if ($Item.PSIsContainer -and -not (Test-Path -LiteralPath $Marker)) {
            throw "Unowned integration collision: $Destination"
        }
        if (-not $Item.PSIsContainer -and -not (Select-String -LiteralPath $Destination -Pattern 'LATTICEMIND:(START|END)|latticemind-owned' -Quiet)) {
            throw "Unowned integration collision: $Destination"
        }
    }
}
Assert-Preflight

try {
    New-Item -ItemType Directory -Force -Path $TempRoot, $Vault, $BackupRoot, $InstallRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $TempRoot, $Vault, $BackupRoot, $VersionsRoot | Out-Null
    Write-Host 'LatticeMind for Windows - downloading the latest release...'
    if (-not (Test-Path -LiteralPath $Verifier)) { throw 'Pinned release verifier missing; refusing bootstrap.' }
    $Release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
    $Asset = $Release.assets | Where-Object name -eq 'latticemind-dist.zip' | Select-Object -First 1
    $ManifestAsset = $Release.assets | Where-Object name -eq 'release-manifest-v1.json' | Select-Object -First 1
    $SignatureAsset = $Release.assets | Where-Object name -eq 'release-manifest-v1.sig' | Select-Object -First 1
    if (-not $Asset -or -not $ManifestAsset -or -not $SignatureAsset) { throw 'Release signed assets missing.' }
    $AssetUrl = $Asset.browser_download_url
    $ManifestUrl = $ManifestAsset.browser_download_url
    $SignatureUrl = $SignatureAsset.browser_download_url
    $Zip = Join-Path $TempRoot 'latticemind-dist.zip'
    $ManifestPath = Join-Path $TempRoot 'release-manifest-v1.json'
    $SignaturePath = Join-Path $TempRoot 'release-manifest-v1.sig'
    Invoke-WebRequest -Uri $AssetUrl -OutFile $Zip
    Invoke-WebRequest -Uri $ManifestUrl -OutFile $ManifestPath
    Invoke-WebRequest -Uri $SignatureUrl -OutFile $SignaturePath
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Verifier verify-and-extract --manifest $ManifestPath --signature $SignaturePath --asset $Zip `
        --output $TempRoot
    if ($LASTEXITCODE -ne 0) { throw 'Pinned verifier rejected release.' }

    $Payload = Join-Path $TempRoot 'upstream'
    foreach ($Required in @('dist','scaffolds','windows','VERSION')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Payload $Required) -PathType $(if ($Required -eq 'VERSION') { 'Leaf' } else { 'Container' }))) {
            throw "Verified release payload is missing expected member: $Required"
        }
    }
    $PythonExe = Join-Path $Payload "windows\python-$Architecture\python.exe"
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Pinned embedded Python runtime missing; PATH fallback forbidden.' }
    $PythonVersion = & $PythonExe --version 2>&1
    if ($PythonVersion -notmatch '^Python 3\.11\.') { throw 'Embedded runtime is not CPython 3.11.' }
    $ManifestData = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $ReleaseTag = $ManifestData.version
$OldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $Payload
    $ManifestSha = & $PythonExe -c "import hashlib,json,sys; sys.path.insert(0, sys.argv.pop(1)); from latticemind_core.release import canonical_manifest; print(hashlib.sha256(canonical_manifest(json.load(open(sys.argv[1],encoding='utf-8')))).hexdigest())" $Payload $ManifestPath
    & $PythonExe -c "import json,sys; sys.path.insert(0, sys.argv.pop(1)); from latticemind_core.migrate import migrate_install; migrate_install(sys.argv[1], sys.argv[2], platform='windows', install_version=sys.argv[3], manifest=json.load(open(sys.argv[4],encoding='utf-8')), signature=open(sys.argv[5],'rb').read(), compatible_version=sys.argv[3])" $Payload $StateRoot $Vault $ReleaseTag $ManifestPath $SignaturePath
    if ($LASTEXITCODE -ne 0) { throw 'Signed payload migration failed.' }
    $ConfigPath = Join-Path $StateRoot 'config-v1.json'
    $ConfigData = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    $ConfigData.profile = $Profile
    $ConfigData.install_version = $ReleaseTag
    $ConfigData | ConvertTo-Json -Depth 8 | Set-Content $ConfigPath -Encoding utf8
} finally {
    $env:PYTHONPATH = $OldPythonPath
}
    foreach ($Relative in @('.codex','.agents','.opencode','.gemini','.pi')) {
        $Destination = Join-Path $Vault $Relative
        if ((Test-Path -LiteralPath $Destination) -and -not (Test-Path -LiteralPath (Join-Path $Destination '.latticemind-owned'))) {
            throw "Unowned integration collision: $Destination"
        }
    }
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
    $VersionRoot = Join-Path (Join-Path $VersionsRoot $ReleaseTag) 'payload'
    New-Item -ItemType Directory -Force -Path $VersionRoot | Out-Null
    Copy-Item (Join-Path $Payload 'windows\*') -Destination $VersionRoot -Recurse -Force
    Copy-Item (Join-Path $Payload 'latticemind_core') -Destination $VersionRoot -Recurse -Force
    if (-not (Test-Path (Join-Path $VersionRoot "python-$Architecture\python.exe"))) {
        throw "Release does not contain the pinned embedded CPython 3.11 $Architecture runtime."
    }
    $PythonExe = Join-Path $VersionRoot "python-$Architecture\python.exe"
    if (-not (Test-Path $PythonExe)) { throw "Embedded runtime missing from signed payload." }
    $PythonVersion = & $PythonExe --version 2>&1
    if ($PythonVersion -notmatch '^Python 3\.11\.') { throw "Embedded runtime must be CPython 3.11." }
    $PointerTemp = "$CurrentPointer.tmp-$([guid]::NewGuid())"
    $PointerBackup = "$CurrentPointer.old-$([guid]::NewGuid())"
    try {
        New-Item -ItemType Junction -Path $PointerTemp -Target (Join-Path $VersionsRoot $ReleaseTag) | Out-Null
        if (Test-Path -LiteralPath $CurrentPointer) {
            Move-Item -LiteralPath $CurrentPointer -Destination $PointerBackup
        }
        Move-Item -LiteralPath $PointerTemp -Destination $CurrentPointer
        $Published = Get-Item -LiteralPath $CurrentPointer -Force
        if ($Published.LinkType -ne 'Junction' -or [IO.Path]::GetFullPath([string]$Published.Target) -ne [IO.Path]::GetFullPath((Join-Path $VersionsRoot $ReleaseTag))) {
            throw 'current junction publication verification failed.'
        }
        if (Test-Path -LiteralPath $PointerBackup) {
            Remove-Item -LiteralPath $PointerBackup -Force
        }
    } catch {
        if (Test-Path -LiteralPath $PointerTemp) { Remove-Item -LiteralPath $PointerTemp -Force }
        if (Test-Path -LiteralPath $PointerBackup) {
            if (Test-Path -LiteralPath $CurrentPointer) { Remove-Item -LiteralPath $CurrentPointer -Force }
            Move-Item -LiteralPath $PointerBackup -Destination $CurrentPointer
        }
        throw
    }
    $InstallRoot = Join-Path $CurrentPointer 'payload'
    foreach ($RuntimeFile in @(Get-ChildItem -LiteralPath $VersionRoot -Recurse -File)) {
        $Manifest.Add([pscustomobject]@{
            output = $RuntimeFile.FullName; type = 'file'; owner = 'latticemind'
            created = $true; replaced = $false
            sha256 = (Get-FileHash $RuntimeFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            backup = ''; backup_sha256 = ''
        })
    }
    foreach ($RuntimeFile in @(Get-ChildItem -LiteralPath $CurrentPointer -Recurse -File)) {
        $Manifest.Add([pscustomobject]@{
            output = $RuntimeFile.FullName; type = 'file'; owner = 'latticemind'
            created = $true; replaced = $false
            sha256 = (Get-FileHash $RuntimeFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            backup = ''; backup_sha256 = ''
        })
    }
    $Manifest | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $BackupRoot 'manifest.json') -Encoding utf8
if (-not (Test-Path (Join-Path $StateRoot 'config-v1.json'))) {
    [pscustomobject]@{
        schema = 'config-v1'
        vault_path = $Vault
        profile = $Profile
        enabled_jobs = @()
        install_version = $ReleaseTag
        migration = @{ preservation = 'existing-bytes-win' }
    } | ConvertTo-Json | Set-Content (Join-Path $StateRoot 'config-v1.json') -Encoding utf8
}

    if ($Profile -eq 'observe' -and -not $NoSchedule) {
        & (Join-Path $InstallRoot 'register-tasks.ps1') -InstallRoot $InstallRoot
    }
    $ExportPath = if ($env:LATTICEMIND_JOB_EXPORT) { $env:LATTICEMIND_JOB_EXPORT } else { Join-Path $InstallRoot 'latticemind-jobs.json' }
    if (Test-Path -LiteralPath $ExportPath) {
        foreach ($Job in @(Get-Content -LiteralPath $ExportPath -Raw | ConvertFrom-Json)) {
            $Manifest.Add([pscustomobject]@{
                output = $Job.path; type = 'scheduler'; owner = $Job.owner
                identity = $Job; job_id = $Job.job_id
                marker = 'job-definition-v1'
            })
        }
    }
    $ManifestPath = Join-Path $StateRoot 'manifest-v1.json'
    $ManifestTemp = "$ManifestPath.tmp-$([guid]::NewGuid())"
    [pscustomobject]@{
        schema = 'manifest-v1'
        state_root = [IO.Path]::GetFullPath($StateRoot)
        vault_root = [IO.Path]::GetFullPath($Vault)
        install_version = $ReleaseTag
        install_id = $ConfigData.install_id
        owned = @($Manifest)
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ManifestTemp -Encoding utf8
    Move-Item -LiteralPath $ManifestTemp -Destination $ManifestPath -Force

    Write-Host "LatticeMind $ReleaseTag installed."
    Write-Host "Vault: $Vault"
    Write-Host "Status: powershell -File `"$(Join-Path $InstallRoot 'status.ps1')`""
    Write-Host "Remove: powershell -File `"$(Join-Path $InstallRoot 'uninstall.ps1')`""
    Write-Host "CLI: powershell -File `"$(Join-Path $InstallRoot 'latticemind.ps1')`" validate"
}
finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
