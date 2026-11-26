# Spec: monitoring

Version 0.2, 26.11.2026 (0.2: dGPU columns in the time series, a new file for another column set). Implements FR-M1 – FR-M5. Tests: AC-L5. Markers as in `blob-format.md`.

## 1. Journal (FR-M1)
Messages go to stderr; systemd stores them in the journal. Each line starts with a syslog priority prefix (`<3>` error, `<4>` warning, `<5>` notice, `<6>` info, `<7>` debug).

| Event | Priority | Example |
|---|---|---|
| start, device, blob form, core variant, mode | notice | `device "Surface Book 3" (tested), blob form B, 101 lines, variant classic, mode control` |
| supervisor state change | notice / warning (emergency, yielded) | `state control → emergency: skin 60.2 °C` |
| controller state change | info | `controller 1: OFF → READY (x 47.0 °C, U 21 W)` |
| slider change | info | `slider 5 → 6 (performance, battery)` |
| input unavailable (once) | warning | `input 28B7BD85/8 unavailable: no SSAM transport` |
| input invalid for > 10 s | warning | |
| foreign change | warning | `PL1 changed by another process (25.0 → 15.0 W)` |
| write error | error | |

Temperatures are logged in °C, power in W. Blob units are converted for logging only.

## 2. Status file (FR-M2)
- **Location:** `/run/surface-power/status.json`, mode 0644, written atomically once per cycle. It is limited to 64 KiB.
- **Schema (version 1):**
```json
{
  "schema": 1,
  "time": "2026-11-14T20:00:00+01:00",
  "device": {"product": "Surface Book 3", "sku": "...", "tested": true},
  "blob": {"source": "efivar", "form": "B", "lines": 101, "variant": "classic"},
  "state": "control",
  "pl_path": "mmio",
  "pl_path_reason": "intel-rapl-mmio:0 of surface_rapl_mmio, allow_write=Y",
  "slider": {"state": 5, "profile": "performance", "ac": true},
  "controllers": [{"line": 1, "input": "2E8B14C2/509", "state": "ACTIVE", "set": 5, "x": 48.1, "setpoint": 48.0, "u_w": 21.4}],
  "outputs": [{"channel": "0B7B5350/51", "name": "PL1", "value_w": 21.4, "target": "written", "hw_w": 21.375}],
  "inputs": [{"channel": "2E8B14C2/509", "value": 48.1, "unit": "°C", "valid": true}],
  "events": [{"time": "...", "priority": "warning", "text": "..."}]
}
```
- **Field values:**
  - `target` is one of `written`, `dry-run`, `log-only`, `firmware`, `held at firmware value (EC inputs invalid)` (also `... last written value ...`);
  - `value_w` is what is written (control) or would be written (dry-run), after clamping; for a held channel it is the value that stays in place, and `computed_w` holds the engine value;
  - `events` holds the last 20 events;
  - `pl_path` (`mmio` or `msr`) and `pl_path_reason`: where PL1, PL2 and the PL1 window go (`kernel-mmio-daemon.md` 2, 9);
  - `rapl_copies` (every 60 s where readable): `mmio` and `msr` (the copies as text), `mmio_source` (`register` or `zone`), `mmio_unreadable` (why the MMIO copy
    cannot be read, else null), `mmio_below_computed` (msr path) or `msr_below_computed` (mmio path) while the other copy is enabled below the computed value.

## 3. Time series (FR-M3)
- **Location:** `/var/lib/surface-power/series-YYYYMMDD.csv`, one row per engine run. If the day's file has another header (an update,
  another blob), the daemon starts `series-YYYYMMDD-2.csv` (then `-3` …) and never appends under a foreign header.
- **dGPU columns** (only with the stand-in): `dgpu.gate`, `dgpu.cap_mhz` (the cap that is set), `dgpu.power_w` (only from a live
  sample). The computed limit alone did not show that no cap was ever set (B15).
- **Columns:** time, run kind (`async`/`timer`), slider, every input channel the blob reads, every controller (state, set, U), every output (engine value, written value).
- **Retention:** files older than 7 days or beyond 50 MiB in total are deleted, oldest first [D].
- **Format:** the same as the replay input format (`tools.md` 2), so a recorded series can be replayed directly.

## 4. Notifier (FR-M5)
- **Unit:** `surface-power-notify`, a user unit. It reads `status.json` every 5 s and shows desktop notifications through `notify-send`, if present.
- **Notifications:**
  - entering emergency or yielded;
  - return to control;
  - battery discharging while on AC for more than 60 s (the charger is too weak).
  - a hibernate or critical-shutdown request of the blob (`thermal_actions` in the status file, `outputs.md` 7).
- **Repeats:** no notification more than once per 10 minutes for the same cause.
