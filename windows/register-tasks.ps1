[CmdletBinding()]
param([Parameter(Mandatory)][string]$InstallRoot)
$ErrorActionPreference = 'Stop'
$Export = if ($env:LATTICEMIND_JOB_EXPORT) { $env:LATTICEMIND_JOB_EXPORT } else { Join-Path $InstallRoot 'latticemind-jobs.json' }
$Records = @()
$Owner = 'latticemind-job-v1'
$Schema = 'job-definition-v1'
# latticemind-job-v1: native tasks are owned exclusively by this installer.
$Script = Join-Path $InstallRoot 'latticemind-maintain.ps1'
$PowerShell = (Get-Command powershell.exe).Source
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType InteractiveToken -RunLevel Limited
function Test-Ownership($Name) {
    $existing = Get-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -ne $existing -and $existing.Description -notmatch "^$Owner schema=$Schema mode=(morning|nightly|weekly|freshness|health)$") {
        Export-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Name | Set-Content "$InstallRoot\$Name.pre-latticemind-export.xml"
        throw "ownership collision for $Name; exported existing task"
    }
    $ExistingXml = if ($existing) { [string](Export-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Name) } else { '' }
    if ($existing -and ($ExistingXml -notmatch [regex]::Escape($Owner) -or
        $ExistingXml -notmatch [regex]::Escape("mode=$($Name)") -or
        $ExistingXml -notmatch [regex]::Escape($PowerShell) -or
        $ExistingXml -notmatch [regex]::Escape($Name) -or
        $ExistingXml -notmatch 'InteractiveToken')) {
        Export-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Name | Set-Content "$InstallRoot\$Name.pre-latticemind-export.xml"
        throw "exact task ownership/action/principal collision for $Name"
    }
}
$Definitions = @(
    @{ Name='morning'; Mode='morning'; Trigger=New-ScheduledTaskTrigger -Daily -At '08:07'; Enabled=$false },
    @{ Name='nightly'; Mode='nightly'; Trigger=New-ScheduledTaskTrigger -Daily -At '22:17'; Enabled=$false },
    @{ Name='weekly'; Mode='weekly'; Trigger=New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At '18:17'; Enabled=$false },
    @{ Name='freshness'; Mode='freshness'; Trigger=New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '19:17'; Enabled=$true },
    @{ Name='health'; Mode='health'; Trigger=New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '21:17'; Enabled=$true }
)
foreach ($Definition in $Definitions) {
    Test-Ownership $Definition.Name
    $Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Script`" -Mode $($Definition.Mode) -SlotState `"$env:USERPROFILE\.local\state\latticemind\slots.json`""
    $Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 900)
    # job-definition-v1: timeout=900, killGraceSeconds=10, overlapPolicy=skip, catchUpWindowSeconds=21600
    $Task = Register-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Definition.Name -Action $Action -Trigger $Definition.Trigger -Settings $Settings -Principal $Principal -Description "$Owner schema=$Schema mode=$($Definition.Mode)" -Force
    if ($Definition.Enabled) { Enable-ScheduledTask -InputObject $Task | Out-Null } else { Disable-ScheduledTask -InputObject $Task | Out-Null }
    $xml = [string](Export-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Definition.Name)
    if ($xml -notmatch [regex]::Escape("$Owner schema=$Schema mode=$($Definition.Mode)") -or
        $xml -notmatch [regex]::Escape("\LatticeMind\$($Definition.Name)")) {
        throw "registered task export failed ownership verification for $($Definition.Name)"
    }
    $path = Join-Path $InstallRoot "$($Definition.Name).task.xml"
    $tmp = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllText($tmp, $xml, [Text.UTF8Encoding]::new($false))
    Move-Item -Force $tmp $path
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    $Records += @{ job_id=$Definition.Name; platform='windows'; path=$path; owner=$Owner; schema=$Schema; enabled=$Definition.Enabled; sha256=$hash }
}
Write-Host 'Registered five LatticeMind scheduled tasks (observe: freshness and health enabled).'
$tmpExport = [IO.Path]::GetTempFileName()
$Records | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $tmpExport
Move-Item -Force $tmpExport $Export
