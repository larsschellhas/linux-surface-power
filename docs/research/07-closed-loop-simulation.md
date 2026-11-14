# Closed-loop simulation of the SB3 15" (engine + thermal model) against Windows

14.11.2026. Tool: `scripts/simulate-sb3.py` with the device's own blob (UEFI variable, not in the repository), engine variant
`classic`. Plant: the Linux-fitted thermal model from `06`:
- `P_sys = P_pkg + 14.2 W`;
- `I_VTS1` with two time constants (512 s, 78 s), 0.80 K/W in total and 10 s dead time;
- CPU temperature `I_VTS1 + 1.61 K/W · P_pkg + 6.9 K`, limited at 100 °C.

Load: 30 min of full CPU load in Best performance on AC (slider 5), then 5 min idle.

## Result

| | Simulation, Ta 23 °C | Simulation, Ta 26 °C | Windows measurement (`02`) |
|---|---|---|---|
| Cut | after 201 s at `I_VTS1` 47.0 °C, PL1 30 → 21.0 W | after 117 s, 30 → 21.0 W | 30 → ~21 W after 0.5–2.5 min, earlier when warm |
| Steps after the cut | every 10 s, 0.06–0.34 W | every 10 s, 0.06–0.46 W | every ~10 s, 0.4–0.6 W, with holds of minutes |
| Steady state (last 5 min) | `I_VTS1` 48.3 °C, package 17.6 W, system 31.8 W | `I_VTS1` 48.2 °C, package 13.6 W, system 27.8 W | package ~17 W, system 31–32 W |
| After the load | PL1 back to 30 W after 66 s (`I_VTS1` < 45.5 °C) | after 89 s | PL1 jumps back to 30 W |

## Reading
- **Timing, cut and steady state match Windows:**
  - The cut value is U_on of the set, and the 10 s step interval comes from the divider.
  - At Ta 23 °C, package and system power agree with the measurement within 1 W. This supports both the engine semantics and the plant model.
- **The steps are smaller in the simulation** (0.06–0.46 W against 0.4–0.6 W on Windows).
  - With the gains of the set (`K_e` 0.4 W/K, `K_d` 1 W/K), steps of 0.4–0.6 W need a skin error of about 1 K or fast changes.
  - The model has no measurement noise and no load fluctuations, so its error stays small. [H] The real `I_VTS1` is noisier.
  - To check in phase 4 with a real run on Linux (AC-E4, AC-L1).
- **The skin settles 0.2–0.3 K above the setpoint**, inside the ±0.4 K deadband. This is expected from the deadband.
- **Release after the load:** PL1 returns to 30 W when `I_VTS1` falls below 45.5 °C. In the model that takes 1–1.5 min.
  - Windows showed the jump "after the load ends". The logs were not checked for the exact delay.
