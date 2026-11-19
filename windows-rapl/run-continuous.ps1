<#
One continuous loaded run from a COOL start, recorded from the first second (no unrecorded warm-up).
Shows when and how the Windows limit steps down under sustained load, to compare with the Linux dry-run (PL1 30 W for about 12 minutes,
then 25.5 W when the skin sensor reaches 47 C).
Run from an elevated PowerShell in this folder, after the machine has been idle for 10 minutes and the charger/mode are as wanted:

  powershell -ExecutionPolicy Bypass -File .\run-continuous.ps1 -Mode best -Power ac

Mode  : efficiency | recommended | better | best  (label only; set the mode in Settings first)
Power : ac | bat                                   (label only)
Takes Seconds (default 1200 = 20 min) plus a few seconds. Reads counters only; changes no setting.
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('efficiency', 'recommended', 'better', 'best')][string]$Mode,
    [Parameter(Mandatory = $true)][ValidateSet('ac', 'bat')][string]$Power,
    [int]$Seconds = 1200
)
$cap = Join-Path $PSScriptRoot 'capture-windows.ps1'
Write-Host "starting all-core load and recording $Seconds s ($Mode, $Power)"
$n = [Environment]::ProcessorCount
$jobs = 1..$n | ForEach-Object { Start-Job -ScriptBlock { while ($true) { [math]::Sqrt(12345.678) | Out-Null } } }
try {
    & $cap -Label "$Mode-$Power-cold20" -Seconds $Seconds
}
finally {
    $jobs | Stop-Job
    $jobs | Remove-Job
    Write-Host 'load stopped'
}
