[CmdletBinding()]
param([Parameter(Mandatory)][string]$InstallRoot)

$ErrorActionPreference = 'Stop'
$Script = Join-Path $InstallRoot 'latticemind-maintain.ps1'
$PowerShell = (Get-Command powershell.exe).Source

$Definitions = @(
    @{ Name = 'Morning'; Mode = 'morning'; Trigger = New-ScheduledTaskTrigger -Daily -At '08:07' },
    @{ Name = 'Nightly'; Mode = 'nightly'; Trigger = New-ScheduledTaskTrigger -Daily -At '22:17' },
    @{ Name = 'Weekly'; Mode = 'weekly'; Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At '18:17' },
    @{ Name = 'Health'; Mode = 'health'; Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '21:17' }
)

foreach ($Definition in $Definitions) {
    $Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Script`" -Mode $($Definition.Mode)"
    $Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Definition.Name `
        -Action $Action -Trigger $Definition.Trigger -Settings $Settings `
        -Description "LatticeMind $($Definition.Mode) knowledge maintenance" -Force | Out-Null
}

Write-Host 'Registered four LatticeMind scheduled tasks.'
