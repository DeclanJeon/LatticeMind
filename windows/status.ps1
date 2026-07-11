$ErrorActionPreference = 'Stop'
$PayloadRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArchValue = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITEW6432')
if (-not $ArchValue) { $ArchValue = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE') }
$Arch = switch ($ArchValue.ToUpperInvariant()) {
    'AMD64' { 'x64' }
    'X64' { 'x64' }
    'ARM64' { 'arm64' }
    default { Write-Output '{"schema":"status-v1","state":"blocked","exit_code":69,"exit_class":"runtime_unavailable"}'; exit 69 }
}
$Runtime = Join-Path (Join-Path $PayloadRoot "python-$Arch") 'python.exe'
$PackageRoot = Join-Path $PayloadRoot 'latticemind_core'
if (-not (Test-Path -LiteralPath $Runtime -PathType Leaf) -or -not (Test-Path -LiteralPath $PackageRoot -PathType Container)) {
    Write-Output '{"schema":"status-v1","state":"blocked","exit_code":69,"exit_class":"runtime_unavailable","message":"embedded runtime or package unavailable"}'
    exit 69
}
if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot 'cli.py') -PathType Leaf)) {
    Write-Output '{"schema":"status-v1","state":"blocked","exit_code":69,"exit_class":"runtime_unavailable","message":"embedded package unavailable"}'
    exit 69
}
$Config = Join-Path (Split-Path -Parent (Split-Path -Parent $PayloadRoot)) 'config-v1.json'
if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) {
    Write-Output '{"schema":"status-v1","state":"blocked","exit_code":78,"exit_class":"config_unavailable"}'
    exit 78
}
$env:LATTICEMIND_CONFIG = $Config
$Bootstrap = @'
import runpy
import sys
package_root = sys.argv.pop(1)
sys.path.insert(0, package_root)
sys.argv[0] = "latticemind-status"
runpy.run_module("latticemind_core.cli", run_name="__main__")
'@
& $Runtime -c $Bootstrap $PackageRoot status --json
exit $LASTEXITCODE
