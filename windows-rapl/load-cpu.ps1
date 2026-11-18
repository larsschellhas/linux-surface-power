<#
All-core CPU load for a fixed time, no extra software needed.
  powershell -ExecutionPolicy Bypass -File .\load-cpu.ps1 -Seconds 600
Run it in a second PowerShell window, then start capture-windows.ps1 after 5 minutes.
#>
param([int]$Seconds = 600)
$n = [Environment]::ProcessorCount
$jobs = 1..$n | ForEach-Object { Start-Job -ScriptBlock { while ($true) { [math]::Sqrt(12345.678) | Out-Null } } }
Write-Host "$n load jobs running for $Seconds s"
Start-Sleep -Seconds $Seconds
$jobs | Stop-Job
$jobs | Remove-Job
Write-Host 'load stopped'
