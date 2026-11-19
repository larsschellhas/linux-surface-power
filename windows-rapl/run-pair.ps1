<#
One idle run and one loaded run for the current power mode and power source.
Run from an elevated PowerShell in this folder:

  powershell -ExecutionPolicy Bypass -File .\run-pair.ps1 -Mode best -Power ac

Mode  : efficiency | recommended | better | best   (whatever is set in Settings right now; this is only the label)
Power : ac | bat                      (label only; plug or unplug the charger yourself first)
Takes about 12 minutes: 1 min idle capture, 5 min warm-up under load, 5 min loaded capture.
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('efficiency', 'recommended', 'better', 'best')][string]$Mode,
    [Parameter(Mandatory = $true)][ValidateSet('ac', 'bat')][string]$Power,
    [int]$WarmupSeconds = 300,
    [int]$LoadedSeconds = 300
)
$cap = Join-Path $PSScriptRoot 'capture-windows.ps1'

Write-Host "[1/3] idle capture ($Mode, $Power)"
& $cap -Label "$Mode-$Power-idle" -Seconds 60

Write-Host "[2/3] starting all-core load, warming up $WarmupSeconds s"
$n = [Environment]::ProcessorCount
$jobs = 1..$n | ForEach-Object { Start-Job -ScriptBlock { while ($true) { [math]::Sqrt(12345.678) | Out-Null } } }
try {
    Start-Sleep -Seconds $WarmupSeconds
    Write-Host "[3/3] loaded capture, $LoadedSeconds s"
    & $cap -Label "$Mode-$Power-cpu" -Seconds $LoadedSeconds
}
finally {
    $jobs | Stop-Job
    $jobs | Remove-Job
    Write-Host 'load stopped'
}
