[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [object[]]$ArgumentList = @()
)

$ErrorActionPreference = 'Stop'
$PayloadRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Do not use PATH Python: the signed payload owns the runtime and its package.
$MachineArchitecture = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITEW6432')
if (-not $MachineArchitecture) {
    $MachineArchitecture = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE')
}
$RuntimeName = switch ($MachineArchitecture.ToUpperInvariant()) {
    'AMD64' { 'python-x64' }
    'ARM64' { 'python-arm64' }
    default { throw "Unsupported Windows architecture: $MachineArchitecture" }
}
$PythonExe = Join-Path (Join-Path $PayloadRoot $RuntimeName) 'python.exe'
$PackageRoot = Join-Path $PayloadRoot 'latticemind_core'
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Embedded CPython runtime missing: $PythonExe"
}
if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot '__main__.py') -PathType Leaf) -and
    -not (Test-Path -LiteralPath (Join-Path $PackageRoot 'cli.py') -PathType Leaf)) {
    throw "Signed LatticeMind package missing: $PackageRoot"
}
$RuntimeVersion = (& $PythonExe --version 2>&1 | Out-String).Trim()
if ($RuntimeVersion -notmatch '^Python 3\.11\.') {
    throw "Embedded runtime must be CPython 3.11: $RuntimeVersion"
}

# Embedded Python _pth files ignore PYTHONPATH. Insert the sibling package explicitly;
# argv is passed as an argument array, never reconstructed into a shell command.
$Bootstrap = 'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); sys.argv[0]="latticemind"; runpy.run_module("latticemind_core.cli",run_name="__main__")'
& $PythonExe -c $Bootstrap $PayloadRoot @ArgumentList
exit $LASTEXITCODE
