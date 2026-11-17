# Research

Findings from the Surface Book 3 investigation (08.–14.11.2026). Each file is a summary. The evidence (logs, CSVs, scripts, blobs, decompiled
code) is in `raw/`, which is git-ignored.

Markers used in the summaries:
- **[M]** measured;
- **[C]** read from code, a blob or package contents;
- **[H]** hypothesis, not yet proven.

| File | Topic |
|---|---|
| [01-sb3-root-cause.md](01-sb3-root-cause.md) | Why the Surface Book 3 throttles on Linux (psys 25/40 W) |
| [02-windows-measurements-sb3.md](02-windows-measurements-sb3.md) | What Windows does on the SB3, measured |
| [03-smf-architecture.md](03-smf-architecture.md) | How Surface SMF works: blob, blocks, controller, timing |
| [04-smf-device-survey.md](04-smf-device-survey.md) | All Surface devices: SMF / MPTF / DTT, blobs, core versions |
| [05-linux-interfaces.md](05-linux-interfaces.md) | Where SMF's inputs and outputs are on Linux, and what is missing |
| [06-prototype-and-related-work.md](06-prototype-and-related-work.md) | The existing prototype controller and service, related projects and issues |
| [07-closed-loop-simulation.md](07-closed-loop-simulation.md) | Engine + measured thermal model of the SB3 against the Windows behaviour (cut, steps, steady state) |
| [08-field-tests-sb3.md](08-field-tests-sb3.md) | Control mode on the SB3 15" (AC-L1 – AC-L3): results against Windows, findings, base sensors and fans |
| [09-newer-blobs.md](09-newer-blobs.md) | Block types of the newer blobs, corpus check, core variant per device, where the inputs come from |
| [10-outputs.md](10-outputs.md) | What the outputs do on Windows (thermal policy: throttle, hibernate, shutdown), rule for control |
| [11-fan-charging.md](11-fan-charging.md) | The fan and charging outputs `B620798F`: channel meaning, Windows receiver, SAM commands, Linux path, safe test procedure |
| [12-remaining-outputs.md](12-remaining-outputs.md) | PL1 time window, sensor offset, battery mode and `O_MINPWR2` (the last four Pro 10 outputs): Windows receivers, Linux paths, "not needed" evidence, Pro 10 tester checklist |
| [13-pro10-battery-and-timing.md](13-pro10-battery-and-timing.md) | Pro 10: where PL1 comes from on battery (controllers 9, 6, slider table), AC step-down timing, PL4 hold and `pl4_requires_ec` |

## Raw material (`raw/`, local only)

| Path | Content |
|---|---|
| `raw/arbeitsordner/` | The complete working folder (German file names). Main hand-overs: `STAND-UND-PLAN.md`, `smf-blob/STAND.md`, `smf-blob/REGLER-ANALYSE.md`, `smf-blob/GERAETEVERGLEICH.md`, `windows-ergebnis/ZUSAMMENFASSUNG.md`, `windows-ergebnis/profile/ZUSAMMENFASSUNG-profile.md`, `Ergebnis-psys-Test.md` |
| `raw/arbeitsordner/acpi/` | ACPI dump of the SB3. **Contains the Windows product key. Never share.** |
| `raw/arbeitsordner/smf-blob/` | SB3 blob, extraction and analysis scripts (`lznt1.py`, `smf-kapsel.py`, `smf-uebersicht.py`, `smf-baum.py`, `smf-vergleich.py`), Ghidra output, blobs of all devices (`geraete/`) |
| `raw/arbeitsordner/dienst/` | Prototype service (Python) with simulation tests |
| `raw/arbeitsordner/ec-modul/` | Out-of-tree module exposing SB3 EC sensors as hwmon (DKMS) |
| `raw/arbeitsordner/registry-patch/` | Kernel patch, submitted as linux-surface/kernel#190 |
| `raw/arbeitsordner/windows-test/`, `windows-ergebnis/` | Windows measurement scripts and results |
| `raw/tools/fw/` | Firmware packages per device (extracted capsules and SMF core drivers), decompiled cores (`decomp/`) |
| `raw/tools/clients/`, `raw/tools/gscripts/` | SMF client drivers of the SB3, Ghidra headless scripts |

Ghidra 12.2 and JDK 25 are installed in `~/tools/` and not copied. The originals of the working folder are in
`~/Schreibtisch/Surface-Book-3-Drosselung/`; this copy is from 14.11.2026.
