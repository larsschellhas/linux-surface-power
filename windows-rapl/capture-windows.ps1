<#
Step 1 Windows capture for the SP10 SMF port. Read-only: changes no settings.
Run from an elevated PowerShell:

  powershell -ExecutionPolicy Bypass -File .\capture-windows.ps1 -Label performance-ac-idle [-Seconds 60] [-Interval 2]

Output: runs\windows-LABEL-<timestamp>\ {snapshot.txt, samples.csv, events-*.csv (since boot, InWindow column), *.evtx}
Raw register values are NOT read by this script (Windows needs a kernel driver for that); see README.md.
#>
param(
    [Parameter(Mandatory = $true)][string]$Label,
    [int]$Seconds = 60,
    [double]$Interval = 2
)
$ErrorActionPreference = 'SilentlyContinue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = Join-Path $PSScriptRoot "runs\windows-$Label-$stamp"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$start = Get-Date

# ---- snapshot ---------------------------------------------------------------
$snap = Join-Path $out 'snapshot.txt'
"label $Label"                         | Out-File $snap
"time  $(Get-Date -Format o)"          | Out-File $snap -Append
"os    $((Get-CimInstance Win32_OperatingSystem).Version)" | Out-File $snap -Append
"bios  $((Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion)"  | Out-File $snap -Append
"`n== power scheme and overlay"        | Out-File $snap -Append
powercfg /getactivescheme              | Out-File $snap -Append
Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes' |
    Select-Object ActivePowerScheme, ActiveOverlayAcPowerScheme, ActiveOverlayDcPowerScheme |
    Format-List | Out-File $snap -Append
"== battery / AC"                      | Out-File $snap -Append
Get-CimInstance Win32_Battery | Select-Object BatteryStatus, EstimatedChargeRemaining | Format-List | Out-File $snap -Append
Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus |
    Select-Object PowerOnline, Charging, Discharging, ChargeRate, DischargeRate, Voltage, RemainingCapacity |
    Format-List | Out-File $snap -Append
"== Surface SMF driver versions"       | Out-File $snap -Append
Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceName -match 'Surface' -and $_.DeviceName -match 'SMF|Thermal|Integration|Platform Extension' } |
    Select-Object DeviceName, DriverVersion, HardwareID | Format-Table -AutoSize | Out-String -Width 300 | Out-File $snap -Append
"== SMF firmware resource (ESRT)"      | Out-File $snap -Append
Get-ItemProperty 'HKLM:\HARDWARE\UEFI\ESRT\{9EE178D4-270E-45E9-86CB-F6F4D64D9EAA}' | Format-List | Out-File $snap -Append
"== counter sets available"            | Out-File $snap -Append
(Get-Counter -ListSet 'Energy Meter', 'Thermal Zone Information', 'Processor Information').Paths | Out-File $snap -Append

# ---- energy saver and power source (sampled every interval) ----------------------
# EnergySaverStatus through the WinRT PowerManager (Windows PowerShell 5.1); "unknown" if the API cannot be loaded.
$esOk = $false
try { $null = [Windows.System.Power.PowerManager, Windows.System.Power, ContentType = WindowsRuntime]; $esOk = $true } catch { }
function Get-EnergySaver { if ($esOk) { try { return [string][Windows.System.Power.PowerManager]::EnergySaverStatus } catch { } } return 'unknown' }
function Get-PowerState {
    $b = Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue | Select-Object -First 1
    $p = (Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1).EstimatedChargeRemaining
    return @{ ac = if ($b) { [string]$b.PowerOnline } else { 'unknown' }; pct = $p }
}
"`n== energy saver at start: $(Get-EnergySaver)" | Out-File $snap -Append
"== energy saver settings (powercfg, current scheme)" | Out-File $snap -Append
powercfg /qh scheme_current sub_energysaver | Out-File $snap -Append

# ---- sampling ---------------------------------------------------------------
$paths = @(
    '\Energy Meter(*)\Power',
    '\Thermal Zone Information(*)\Temperature',
    '\Thermal Zone Information(*)\% Passive Limit',
    '\Thermal Zone Information(*)\Throttle Reasons',
    '\Processor Information(_Total)\% Processor Performance',
    '\Processor Information(_Total)\% Processor Utility',
    '\Processor Information(_Total)\Processor Frequency',
    '\Processor Information(_Total)\% Performance Limit'
)
$valid = @()
foreach ($p in $paths) { if (Get-Counter -Counter $p -MaxSamples 1) { $valid += $p } }
"`n== counters sampled`n$($valid -join "`n")" | Out-File $snap -Append

$rows = New-Object System.Collections.Generic.List[object]
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
    $t0 = Get-Date
    $row = [ordered]@{ t = $t0.ToString('o') }
    $ps = Get-PowerState
    $row['energy_saver'] = Get-EnergySaver; $row['ac_online'] = $ps.ac; $row['battery_pct'] = $ps.pct
    if ($valid.Count) {
        foreach ($s in (Get-Counter -Counter $valid -MaxSamples 1).CounterSamples) {
            $row[$s.Path -replace '^\\\\[^\\]+', ''] = [math]::Round($s.CookedValue, 3)
        }
    }
    $rows.Add([pscustomobject]$row)
    $wait = $Interval - ((Get-Date) - $t0).TotalSeconds
    if ($wait -gt 0) { Start-Sleep -Milliseconds ([int]($wait * 1000)) }
}
$cols = $rows | ForEach-Object { $_.PSObject.Properties.Name } | Select-Object -Unique
$rows | Select-Object $cols | Export-Csv -NoTypeInformation -Path (Join-Path $out 'samples.csv')

# ---- Surface event channels ---------------------------------------------------
# The Surface drivers log mostly on state changes, so the run window itself is often empty. Export everything since this boot and flag the rows that
# fall inside the run (InWindow). Errors are reported in snapshot.txt instead of being silenced; "no events" is not an error.
$ErrorActionPreference = 'Continue'
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$since = if ($boot) { $boot } else { (Get-Date).AddHours(-24) }   # since this boot (falls back to 24 h)
$channels = Get-WinEvent -ListLog 'Microsoft-Surface-*' | Select-Object -ExpandProperty LogName
"`n== Surface event channels`n$($channels -join "`n")" | Out-File $snap -Append

function Export-Events($filter, $file) {
    $ev = @()
    try { $ev = @(Get-WinEvent -FilterHashtable $filter -ErrorAction Stop) }
    catch {
        if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') {
            "event export error $(Split-Path $file -Leaf): $($_.Exception.Message)" | Out-File $snap -Append
        }
    }
    $ev | ForEach-Object {
        $msg = $_.Message
        if (-not $msg) { $msg = ($_.Properties | ForEach-Object { $_.Value }) -join ' | ' }
        [pscustomobject]@{
            TimeCreated = $_.TimeCreated.ToString('o'); InWindow = ($_.TimeCreated -ge $start)
            Id = $_.Id; Level = $_.LevelDisplayName; Provider = $_.ProviderName; Message = $msg
        }
    } | Export-Csv -NoTypeInformation -Path $file
    $inwin = @($ev | Where-Object { $_.TimeCreated -ge $start }).Count
    "events $(Split-Path $file -Leaf): $($ev.Count) since boot, $inwin in the run window" | Out-File $snap -Append
}

foreach ($c in $channels) {
    $safe = $c -replace '[\\/]', '_'
    wevtutil epl $c (Join-Path $out "$safe.evtx") /ow:true
    Export-Events @{ LogName = $c; StartTime = $since } (Join-Path $out "events-$safe.csv")
}
Export-Events @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-Power', 'Microsoft-Windows-Kernel-Processor-Power'; StartTime = $since } (Join-Path $out 'events-kernel-power.csv')

"`n== energy saver at end: $(Get-EnergySaver)" | Out-File $snap -Append
Write-Host "$($rows.Count) samples -> $out"
