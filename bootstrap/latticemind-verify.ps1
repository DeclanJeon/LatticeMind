[CmdletBinding()]
param(
    [Parameter(Mandatory=$true,Position=0)][ValidateSet('verify-and-extract','verify')][string]$Command,
    [string]$Manifest, [string]$Signature, [string]$Asset, [string]$Output
)
$ErrorActionPreference = 'Stop'
$Bootstrap = Split-Path -Parent $PSScriptRoot
$reported = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE')
$native = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITEW6432')
$machine = if ($native) { $native } else { $reported }
$runtime = @{
    'AMD64' = @{ Name='x64'; Url='https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip'; Sha256='009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b' }
    'ARM64' = @{ Name='arm64'; Url='https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-arm64.zip'; Sha256='1a6dae49d15320270a7141f93b574ff7686a7a526efa65e63ddbebf9b409929a' }
}
if (-not $runtime.ContainsKey($machine)) { throw "Unsupported Windows architecture: $machine" }
$spec = $runtime[$machine]
$cache = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) "LatticeMind\python-3.11.9-$($spec.Name)"
$Archive = Join-Path $cache 'runtime.zip'
$FileManifest = Join-Path $cache 'runtime-files.json'
function Test-RuntimeCache {
    if (-not (Test-Path -LiteralPath $Archive) -or -not (Test-Path -LiteralPath $FileManifest)) { return $false }
    if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $spec.Sha256) { return $false }
    try { $entries = Get-Content -LiteralPath $FileManifest -Raw | ConvertFrom-Json } catch { return $false }
    foreach ($entry in @($entries)) {
        $file = Join-Path $cache $entry.Path
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $false }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Sha256) { return $false }
    }
    return (Test-Path -LiteralPath $Python -PathType Leaf)
}
$Python = Join-Path $cache 'python.exe'
if (-not (Test-RuntimeCache)) {
    if (Test-Path -LiteralPath $cache) { Remove-Item -LiteralPath $cache -Recurse -Force }
    $parent = Split-Path -Parent $cache
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $tmp = Join-Path $parent ("runtime-$([Guid]::NewGuid().ToString('N'))")
    $zip = "$tmp.zip"
    try {
        Invoke-WebRequest -Uri $spec.Url -OutFile $zip -UseBasicParsing
        $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $spec.Sha256) { throw 'Pinned Python runtime hash mismatch.' }
        New-Item -ItemType Directory -Path $tmp | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $tmp
        if (-not (Test-Path -LiteralPath (Join-Path $tmp 'python.exe'))) { throw 'Pinned Python runtime payload missing.' }
        Move-Item -LiteralPath $tmp -Destination $cache
        Copy-Item -LiteralPath $zip -Destination $Archive
        $files = @(Get-ChildItem -LiteralPath $cache -File -Recurse | Where-Object { $_.FullName -ne $Archive -and $_.FullName -ne $FileManifest } | ForEach-Object {
            @{ Path=$_.FullName.Substring($cache.Length + 1); Sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
        })
        $files | ConvertTo-Json -Compress | Set-Content -LiteralPath $FileManifest -Encoding UTF8
    } finally {
        Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
if (-not (Test-Path -LiteralPath $Python)) { throw 'Pinned bootstrap Python runtime missing.' }
$CoreRoot = $Bootstrap
$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $CoreRoot
    if ($Command -eq 'verify') {
        $verifyCode = 'import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv.pop(1)); from latticemind_core.release import verify_manifest; m,s,a=map(Path,sys.argv[1:]); data=json.loads(m.read_text(encoding="utf-8")); verify_manifest(data,s.read_bytes(),asset=a)'
        & $Python -c $verifyCode $CoreRoot $Manifest $Signature $Asset
        if ($LASTEXITCODE) { exit $LASTEXITCODE }
        exit 0
    }
    foreach ($p in @($Manifest,$Signature,$Asset,$Output)) { if ([string]::IsNullOrWhiteSpace($p)) { throw 'Manifest, signature, asset and output are required.' } }
    $code = @'
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv.pop(1))
from latticemind_core.release import verify_manifest,validate_archive
m,s,a,o=map(Path,sys.argv[1:])
data=json.loads(m.read_text(encoding="utf-8"))
verify_manifest(data,s.read_bytes(),asset=a)
expected_names={"upstream"}
validate_archive(a,o,expected_names=expected_names)
payload=o/"upstream"
if not (payload/"VERSION").is_file() or any(not (payload/name).is_dir() for name in ("dist","scaffolds","windows")):
    raise ValueError("unexpected payload shape")
print(json.dumps({"verified":True,"version":data["version"]},sort_keys=True))
'@
    & $Python -c $code $CoreRoot $Manifest $Signature $Asset $Output
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
} finally {
    $env:PYTHONPATH = $oldPythonPath
}
