[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidateSet('freshness','health','morning','nightly','weekly')] [string]$Mode,
    [Parameter(Mandatory)] [string]$SlotState,
    [string]$ScheduledAt = '',
    [string]$SlotId = '',
    [string]$Grant = ''
)
$ErrorActionPreference = 'Stop'
$PayloadRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArchValue = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITEW6432')
if (-not $ArchValue) { $ArchValue = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE') }
$Arch = switch ($ArchValue.ToUpperInvariant()) {
    'AMD64' { 'x64' }
    'X64' { 'x64' }
    'ARM64' { 'arm64' }
    default { exit 69 }
}
$Runtime = Join-Path (Join-Path $PayloadRoot "python-$Arch") 'python.exe'
$PackageRoot = Join-Path $PayloadRoot 'latticemind_core'
if (-not (Test-Path -LiteralPath $Runtime -PathType Leaf)) { exit 69 }
if (-not (Test-Path -LiteralPath $PackageRoot -PathType Container)) { exit 69 }
if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot 'cli.py') -PathType Leaf)) { exit 69 }
if ([IO.Path]::GetFileName($PayloadRoot) -ne 'payload' -or [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($PayloadRoot)) -ne 'current') { exit 69 }
$Config = Join-Path (Split-Path -Parent (Split-Path -Parent $PayloadRoot)) 'config-v1.json'
if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) { throw 'Canonical config-v1.json is required.' }
if ($Mode -in @('freshness','health')) {
    if ($Grant) { Write-Error 'Observe-only mode rejects write grants.'; exit 73 }
} elseif ($Grant -ne "scheduled-write:$Mode") {
    Write-Error "Write job requires an explicit scheduled-write grant: $Mode"
    exit 73
}
$env:LATTICEMIND_CONFIG = $Config
$Mutex = [System.Threading.Mutex]::new($false, 'Local\LatticeMindMaintenance')
$Acquired = $false
try {
    $Acquired = $Mutex.WaitOne(0)
    if (-not $Acquired) { Write-Error 'maintenance lock contention'; exit 75 }
    $Bootstrap = @'
import subprocess
import sys
from datetime import datetime, timezone

package_root, slot_state, mode, scheduled_arg, slot_arg = sys.argv[1:]
sys.path.insert(0, package_root)
from latticemind_core.jobs import get_job, run_persisted_slot, scheduled_occurrence, slot_identity

job = get_job(mode)
scheduled = datetime.fromisoformat(scheduled_arg) if scheduled_arg else scheduled_occurrence(job, datetime.now().astimezone())
if scheduled.tzinfo is None or scheduled.utcoffset() is None:
    raise SystemExit("scheduled occurrence must include timezone offset")
slot = slot_arg or slot_identity(job, scheduled.date())
if slot != slot_identity(job, scheduled.date()):
    raise SystemExit("slot id does not match scheduled occurrence")
command = ["freshness", "scan"] if job.mode == "freshness" else ["status"]
import os
env = os.environ.copy()
env["PYTHONPATH"] = package_root
result = run_persisted_slot(
    slot_state,
    job,
    slot,
    lambda: subprocess.call([sys.executable, "-m", "latticemind_core.cli", *command], env=env),
    scheduled=scheduled,
    now=datetime.now(timezone.utc),
)
print(result)
raise SystemExit({"succeeded": 0, "degraded": 2, "blocked": 69, "timed_out": 124, "expired": 0, "skipped": 0}.get(result, 78))
'@
    & $Runtime -c $Bootstrap $PackageRoot $SlotState $Mode $ScheduledAt $SlotId
    exit $LASTEXITCODE
} finally {
    if ($Acquired) { $Mutex.ReleaseMutex() }
    $Mutex.Dispose()
}
