$ErrorActionPreference = 'Stop'
$StateRoot = Join-Path $env:LOCALAPPDATA 'LatticeMind'
$ConfigPath = Join-Path $StateRoot 'config.json'
if (-not (Test-Path $ConfigPath)) {
    Write-Host 'LatticeMind is not installed.'
    exit 0
}
$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$ManifestPath = Join-Path $Config.backup 'manifest.json'
$Records = if (Test-Path $ManifestPath) { @(Get-Content $ManifestPath -Raw | ConvertFrom-Json) } else { @() }

for ($Index = $Records.Count - 1; $Index -ge 0; $Index--) {
    $Record = $Records[$Index]
    if ($Record.backup -and (Test-Path $Record.backup)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $Record.output) | Out-Null
        Copy-Item -LiteralPath $Record.backup -Destination $Record.output -Force
    } elseif (Test-Path $Record.output) {
        Remove-Item -LiteralPath $Record.output -Force
    }
}

foreach ($Name in 'Morning', 'Nightly', 'Weekly', 'Freshness', 'Health') {
    Unregister-ScheduledTask -TaskPath '\LatticeMind\' -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
}

foreach ($File in (Join-Path $Config.vault 'AGENTS.md'), (Join-Path $Config.vault 'GEMINI.md')) {
    if (-not (Test-Path $File)) { continue }
    $Text = Get-Content $File -Raw
    $Start = '<!-- LATTICEMIND:START -->'
    $End = '<!-- LATTICEMIND:END -->'
    if ($Text.Contains($Start) -and $Text.Contains($End)) {
        $Before = $Text.Substring(0, $Text.IndexOf($Start)).TrimEnd()
        $AfterAt = $Text.IndexOf($End) + $End.Length
        $After = $Text.Substring($AfterAt).TrimStart([char[]]"`r`n")
        $Result = "$Before`n$After"
        if ($Result.Trim() -eq '# Agent Instructions') {
            Remove-Item -LiteralPath $File -Force
        } else {
            Set-Content -LiteralPath $File -Value $Result -Encoding utf8
        }
    }
}

Remove-Item -LiteralPath $ConfigPath -Force
Write-Host 'LatticeMind integration removed. Vault notes and scaffold folders were preserved.'
Write-Host "Recovery backup remains at: $($Config.backup)"
