# Windows measurements on the Surface Book 3 15"

Windows 11 (builds 22631/26300), HVCI on, HWiNFO logging (2–5 s) plus PowerShell scripts reading MSRs, battery and NVIDIA limits.
Runs from 08.11. and 13.11.2026. Evidence: `raw/arbeitsordner/windows-ergebnis/` (`ZUSAMMENFASSUNG.md`, `profile/ZUSAMMENFASSUNG-profile.md`).
These runs are the reference for replay and acceptance tests.

## PL1/PL2 ceilings per power mode and source [M]

| Mode (effective mode) | AC PL1 / PL2 | Battery PL1 / PL2 |
|---|---|---|
| Recommended (`Balanced`) | 25 / 25 W | 13 / 15 W |
| Better performance (`HighPerformance`) | 25 / 40 W | 25 / 25 W |
| Best performance (`MaxPerformance`) | 30 / 61 W | 25 / 35 W |
| Energy saver on (`BetterBattery`) | 25 / 25 W | 13 / 15 W |

- The values switch at once with the mode.
- Energy saver is a fixed mode, and it locks the mode selection.
- These ceilings match the SB3 blob's slider table exactly (see 03).

## Dynamic behaviour under sustained CPU load (Best performance, AC) [M]
- The run starts at PL1 = 30 W.
- For 0.5–2.5 min the CPU sits at 95–100 °C, held only by its own hardware limit (TjMax/PROCHOT).
- Then PL1 is cut to ~21 W in one step. The cut comes earlier when the device is warm and later when cold, independent of CPU temperature.
- Afterwards PL1 moves in steps of 0.4–0.6 W about every 10 s, with holds of several minutes.
- It settles after 5–8 min. Steady state: package ~17 W, system ~31–32 W, ~1.9 GHz, CPU 80–88 °C. Bright or dark display gives the
  same system power.
- After the load ends, PL1 jumps back to 30 W.
- In the energy-saver states the cut goes to ~18 W instead.
- **psys never binds on Windows.** System power reaches 45–52 W.
  - Once, a fixed psys cap of 23.0 W was seen, with PL1 unchanged; it disappeared abruptly. [H: the EC had not reported available power
    yet, so psys = 0 + 23 W.]
- Charging the base battery costs ~2–3 W of package budget.

## Battery [M]
On battery (Best performance), PL1/PL2 start at 25/35 W. For ~2.5 min Windows allows more than Linux on AC (~25 W package, ~40 W system,
2.3–2.5 GHz); in steady state the same as on AC. The base battery discharges at 45–59 W under that load.

## Fan [M, by ear]
Windows sets a base level per power mode and also controls by temperature. At idle it is clearly quieter than Linux in `performance`
(which runs ~8,000 rpm constantly).

## Skin temperature on Windows
Not readable on Windows: no EC sensors in HWiNFO, and the ETW providers emitted nothing. Linux measurements give the mapping from load to
`I_VTS1`. Model: two time constants (512 s and 78 s), 10 s dead time; `raw/arbeitsordner/strecke-2tau.json`.
