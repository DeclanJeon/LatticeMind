$ErrorActionPreference = 'Stop'
$PurgeState = ($args -contains '--purge-state')
$StateRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'LatticeMind')).TrimEnd('\')
$ConfigPath = Join-Path $StateRoot 'config-v1.json'
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { Write-Host 'LatticeMind is not installed.'; exit 0 }
$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($Config.schema -ne 'config-v1') { throw 'unsupported config schema' }
$ManifestPath = Join-Path $StateRoot 'manifest-v1.json'
if ($Config.manifest_path) { $ManifestPath = [IO.Path]::GetFullPath($Config.manifest_path) }
if (-not $ManifestPath.StartsWith($StateRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'manifest path is outside state root' }
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'manifest-v1.json is required' }
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($Manifest.schema -ne 'manifest-v1' -or -not $Manifest.owned) { throw 'invalid manifest-v1' }
if ([IO.Path]::GetFullPath([string]$Manifest.state_root).TrimEnd('\') -ne $StateRoot) { throw 'manifest is not bound to install state' }
if ($Manifest.install_id -and $Config.install_id -and $Manifest.install_id -ne $Config.install_id) { throw 'manifest install identity mismatch' }
$VaultRoot = if ($Manifest.vault_root) { [IO.Path]::GetFullPath([string]$Manifest.vault_root).TrimEnd('\') } elseif ($Config.vault_path) { [IO.Path]::GetFullPath([string]$Config.vault_path).TrimEnd('\') } else { throw 'manifest vault root missing' }
$AllowedRoots = @($StateRoot, $VaultRoot, [IO.Path]::GetFullPath((Join-Path $HOME '.claude')), [IO.Path]::GetFullPath((Join-Path $HOME '.hermes')), [IO.Path]::GetFullPath((Join-Path $HOME '.gjc')), [IO.Path]::GetFullPath((Join-Path $HOME '.omp')))
function Resolve-ManifestPath([string]$Value, [bool]$Backup) {
  if ([string]::IsNullOrWhiteSpace($Value) -or -not [IO.Path]::IsPathRooted($Value)) { throw 'invalid manifest path' }
  $Full = [IO.Path]::GetFullPath($Value)
  $Roots = if ($Backup) { @($StateRoot + '\backups') } else { $AllowedRoots }
  if (-not ($Roots | Where-Object { $Full.Equals($_,[StringComparison]::OrdinalIgnoreCase) -or $Full.StartsWith($_.TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase) })) { throw "manifest path outside approved roots: $Value" }
  $Probe = $Full
  while ($Probe -and -not (Test-Path -LiteralPath $Probe)) { $Next = Split-Path -Parent $Probe; if ($Next -eq $Probe) { break }; $Probe = $Next }
  if ($Probe -and ((Get-Item -LiteralPath $Probe -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "reparse path rejected: $Value" }
  return $Full
}
function Restore-Metadata($Path, $Metadata) {
  if (-not $Metadata) { return }
  if ($Metadata.acl_sddl) {
    $Acl = New-Object System.Security.AccessControl.FileSecurity
    $Acl.SetSecurityDescriptorSddlForm([string]$Metadata.acl_sddl)
    Set-Acl -LiteralPath $Path -AclObject $Acl
  }
  $Item = Get-Item -LiteralPath $Path -Force
  $Item.Attributes = [IO.FileAttributes][int]$Metadata.attributes
  $Item.CreationTimeUtc = [datetime]::Parse($Metadata.creation_time_utc).ToUniversalTime()
  $Item.LastWriteTimeUtc = [datetime]::Parse($Metadata.last_write_time_utc).ToUniversalTime()
  $Item.LastAccessTimeUtc = [datetime]::Parse($Metadata.last_access_time_utc).ToUniversalTime()
}
$Schedulers = @()
foreach ($Record in @($Manifest.owned)) {
  if ($Record.owner -notin @('latticemind','latticemind-job-v1')) { throw 'unowned manifest collision' }
  $PathValue = if ($Record.output) { $Record.output } else { $Record.path }
  $PathValue = Resolve-ManifestPath ([string]$PathValue) $false
  if ($Record.backup) { $Record.backup = Resolve-ManifestPath ([string]$Record.backup) $true }
  if ($Record.type -eq 'scheduler' -or $Record.kind -eq 'scheduler') { $Schedulers += $Record; continue }
  if ($Record.type -eq 'symlink' -or $Record.kind -eq 'symlink') { if (Test-Path -LiteralPath $PathValue) { $Item=Get-Item -LiteralPath $PathValue -Force; if (-not $Item.LinkType -or $Item.Target -ne $Record.target) { throw "unsafe owned symlink: $PathValue" } }; continue }
  if (Test-Path -LiteralPath $PathValue) {
    if ((Get-Item -LiteralPath $PathValue -Force).PSIsContainer -or -not $Record.sha256) { throw "unsafe owned path: $PathValue" }
    if ((Get-FileHash -LiteralPath $PathValue -Algorithm SHA256).Hash.ToLower() -ne $Record.sha256.ToLower()) { throw "modified owned path: $PathValue" }
  }
}
foreach ($Record in $Schedulers) {
  $PathValue = Resolve-ManifestPath ([string](if ($Record.output) {$Record.output} else {$Record.path})) $false
  if (Test-Path -LiteralPath $PathValue -PathType Leaf -and (-not $Record.marker -or -not (Select-String -LiteralPath $PathValue -Pattern $Record.marker -Quiet))) { throw "marker mismatch: $PathValue" }
}
foreach ($Record in @($Manifest.owned)) {
  if ($Record.type -eq 'scheduler' -or $Record.kind -eq 'scheduler') { continue }
  $PathValue = Resolve-ManifestPath ([string](if ($Record.output) {$Record.output} else {$Record.path})) $false
  if ($Record.type -eq 'symlink' -or $Record.kind -eq 'symlink') { if (Test-Path -LiteralPath $PathValue) { Remove-Item -LiteralPath $PathValue -Force }; continue }
  if (Test-Path -LiteralPath $PathValue) {
    if ($Record.created) { Remove-Item -LiteralPath $PathValue -Force }
    elseif ($Record.backup) {
      if (-not (Test-Path -LiteralPath $Record.backup -PathType Leaf) -or (Get-FileHash -LiteralPath $Record.backup -Algorithm SHA256).Hash.ToLower() -ne $Record.backup_sha256.ToLower()) { throw "invalid backup: $($Record.backup)" }
      Remove-Item -LiteralPath $PathValue -Force
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $PathValue) | Out-Null
      Copy-Item -LiteralPath $Record.backup -Destination $PathValue -Force
      Restore-Metadata $PathValue $Record.backup_metadata
    }
  }
}
foreach ($Record in $Schedulers) {
  $Name = [string]$Record.job_id
  if ($Name) {
    $Task = Get-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Name -ErrorAction SilentlyContinue
    if ($Task -and $Task.Description -notlike '*job-definition-v1*') { throw "scheduler identity mismatch: $Name" }
    if ($Task) { Unregister-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Name -Confirm:$false }
    if (Get-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Name -ErrorAction SilentlyContinue) { throw "failed to remove scheduled task: $Name" }
  }
  $PathValue = Resolve-ManifestPath ([string](if ($Record.output) {$Record.output} else {$Record.path})) $false
  if (Test-Path -LiteralPath $PathValue -PathType Leaf) { Remove-Item -LiteralPath $PathValue -Force; if (Test-Path -LiteralPath $PathValue) { throw "failed to remove task export: $PathValue" } }
}
Remove-Item -LiteralPath $ConfigPath -Force
if ($PurgeState) { Remove-Item -LiteralPath $StateRoot -Recurse -Force; if (Test-Path -LiteralPath $StateRoot) { throw 'state purge failed' }; Write-Host 'State purged.' }
Write-Host 'LatticeMind integration removed; vault and backups preserved.'
