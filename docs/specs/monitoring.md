# Spec: monitoring

Version 0.1, 14.11.2026. Implements FR-M1 – FR-M5. Tests: AC-L5. Markers as in `blob-format.md`.

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
  "slider": {"state": 5, "profile": "performance", "ac": true},
  "controllers": [{"line": 1, "input": "2E8B14C2/509", "state": "ACTIVE", "set": 5, "x": 48.1, "setpoint": 48.0, "u_w": 21.4}],
  "outputs": [{"channel": "0B7B5350/51", "name": "PL1", "value_w": 21.4, "target": "written", "hw_w": 21.375}],
  "inputs": [{"channel": "2E8B14C2/509", "value": 48.1, "unit": "°C", "valid": true}],
  "events": [{"time": "...", "priority": "warning", "text": "..."}]
}
```
- **Field values:**
  - `target` is one of `written`, `dry-run`, `log-only`, `firmware`;
  - `events` holds the last 20 events.

## 3. Time series (FR-M3)
- **Location:** `/var/lib/surface-power/series-YYYYMMDD.csv`, one row per engine run.
- **Columns:** time, run kind (`async`/`timer`), slider, every input channel the blob reads, every controller (state, set, U), every output (engine value, written value).
- **Retention:** files older than 7 days or beyond 50 MiB in total are deleted, oldest first [D].
- **Format:** the same as the replay input format (`tools.md` 2), so a recorded series can be replayed directly.

## 4. Notifier (FR-M5)
- **Unit:** `surface-power-notify`, a user unit. It reads `status.json` every 5 s and shows desktop notifications through `notify-send`, if present.
- **Notifications:**
  - entering emergency or yielded;
  - return to control;
  - battery discharging while on AC for more than 60 s (the charger is too weak).
- **Repeats:** no notification more than once per 10 minutes for the same cause.
