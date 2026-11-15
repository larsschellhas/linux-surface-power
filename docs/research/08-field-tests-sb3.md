# Field tests on the SB3 15" (control mode, 15.11.2026)

Setup:
- **Device:** Surface Book 3 15", i7-1065G7, GTX 1660 Ti, kernel 7.2.15-1-surface, ambient about 23 °C.
- **Daemon:** `surface-powerd --control`, run from the checkout by `scripts/field-test.py`. That script stops thermald, sets the platform profile, generates the load, logs once per second, and restores everything at the end.
- **Load:**
  - CPU: x265 encode.
  - AC-L3 adds the dGPU: CUDA filter plus NVENC.
- **Analysis:** `scripts/field-test-report.py`. The raw data stays local, in the working folder.

## Results

| | AC-L1: Best performance, AC, 30 min | AC-L3: as AC-L1 plus dGPU, 20 min | AC-L2: Recommended, AC, 15 min | AC-L2: Recommended, battery |
|---|---|---|---|---|
| Ceiling PL1/PL2 | 30/61 W | 30/61 W | 25/25 W | 13/15 W |
| psys PL1/PL2 | 45/100 W | 45/100 W | 45/100 W | 45/70.4 W |
| Cut | after 81 s at 47.0 °C, 30 → 21 W | after 107 s at 47.0 °C, 30 → 21 W | after 76 s at 47.1 °C, 25 → 18 W | none: skin 43.9 °C after 10.5 min, run stopped |
| Steps after the cut | 0.12–0.62 W (median 0.25 W), every 10 s | 0.25–0.62 W (median 0.50 W) | – | – |
| Skin, last 5 min of load | 47.6–48.1 °C (mean 47.9) | 47.7–48.3 °C (mean 48.0) | 47.6–47.9 °C | – |
| Package / system, last 5 min | 17.7 / 32.2 W | 17.0 / 32.1 W | 16.1 / 30.3 W | 12.9 / 24.9 W |
| CPU, last 5 min | ≤ 89 °C | ≤ 86 °C | – | ≤ 74 °C |
| Release after the load | PL1 back to 30 W after 68 s | after 85 s | after 58 s | – |
| Emergency | none | none | none | none |

## Against the acceptance criteria and Windows
- **AC-L1, passed:**
  - Skin 47.9 °C (criterion 48.0 ± 0.5 °C).
  - System power 32.2 W (criterion 32 ± 3 W; Windows 31–32 W). Package 17.7 W (Windows ~17 W).
  - The cut to 21 W and the 10 s step interval are as on Windows (`02`).
  - The closed-loop simulation (`07`) predicted the release after 66 s; the device took 68 s.
- **AC-L3, passed:**
  - psys at 45/100 W no longer binds. System power reached 58 W (old limit 40 W), and the skin limit held.
  - The psys domain does not include the dGPU: with the dGPU at about 62 W, psys power stayed at the CPU-only level.
  - The dGPU itself ran at its Linux driver limit of 65 W. On Windows it is held at 49.6 W with the Surface PSU, and the blob computes 50.0 W for it.
  - This output has no Linux target: `nvidia-smi -pl` is refused (DV-5). The battery did not drain on the Surface PSU.
- **AC-L2:**
  - **Ceilings:** confirmed on AC and on battery.
  - **Cut on AC:** at U_on of the set (18 W), when `I_VTS1` reached 47 °C, as required.
  - **Cut on battery:** not reachable at this ambient temperature with 13 W. The mechanism is the same as on AC.
  - **psys on battery:** the blob computes 45/70.4 W from the battery data. Linux used fixed 25/40 W before.
- **Step size (open question from `07`):**
  - On the device, the steps are larger than in the noise-free simulation and reach the Windows range of 0.4–0.6 W. With dGPU load the median is 0.50 W.
  - This supports the explanation that sensor noise and load fluctuations set the step size.

## Findings that led to changes
1. **First run, emergency after 60 s.** The dGPU skin loop reads a base sensor (609) that Linux did not provide, and the core-inputs-lost rule counted it as lost. Changes:
   - Inputs missing from the start no longer count for that rule.
   - Control is only allowed if every missing input reaches log-only outputs (`specs/supervisor.md` 2.5).
2. **The CPU sits at 99–100 °C under load before the cut, as on Windows.** The extra safety stop of the test script had to move from 100 °C to 105 °C, the same value as the guard.
3. **Base sensors and fans.** The SB3 base (KIP) has the same nine sensors as the display part and two fans of its own. Both work with:
   - registry nodes below the base hub;
   - match entries for KIP in `surface_temp` and `surface_fan`.
   This was tested with an out-of-tree module and rebuilt drivers, and submitted in linux-surface/kernel#190.

   Base fan behaviour under dGPU load: both base fans start after about 2 min, at a GPU temperature of 86 °C, and then run at about 2700 rpm. The NVIDIA driver reports 0 % fan.
4. **Two `surface_thermal` devices.** With both present, a sensor must be picked by its SSAM device (`01:03:01:00:02` display part, `01:03:02:00:02` base), not by name. Both the daemon and the field-test script now do that.

## dGPU limit on Linux (GTX 1660 Ti Max-Q, driver 595.99.02)
- **Power limit (`nvidia-smi -pl`):** refused ("Changing power management limit is not supported"), with and without persistence mode.
  - Forum reports put this restriction on notebook GPUs since about driver 530/550. NVIDIA does not document it.
  - The notebook route on Linux is Dynamic Boost (`nvidia-powerd`). It needs Ampere or newer GPUs and SBIOS support, and the driver reports "Notebook Dynamic Boost: Not Supported" here.
  - The SB3 ACPI tables have no SAN-based dGPU limit method either. That method exists on the SB2 and Studio 2.
- **Clock cap (`nvidia-smi -lgc 300,<max>`):** accepted. Measured under the same CUDA + NVENC load:

  | Max clock | dGPU power | Clock | GPU max |
  |---|---|---|---|
  | none | 62.6 W | 1512 MHz | 76 °C |
  | 1500 MHz | 58.5 W | 1392 MHz | 87 °C |
  | 1300 MHz | 52.5 W | 1290 MHz | 84 °C |
  | 1100 MHz | 40.5 W | 1095 MHz | 70 °C |
  | 900 MHz | 33.6 W | 900 MHz | 62 °C |

  - The clock can be set in 15 MHz steps. At 1500 MHz the GPU ran below the cap because it reached its own 87 °C target, so power is not a fixed function of the cap.
- **Persistence and RTD3:** RTD3 is enabled (fine-grained). Measured:
  - A clock cap stays in force after the GPU has been in D3cold and wakes up again, without persistence mode.
  - Persistence mode does not keep the GPU out of D3cold.
  - Not tested: system suspend/resume and driver reload. A reboot clears the cap.
- **Consequence:** a dGPU stand-in would regulate the measured dGPU power to the blob's output 201 by moving the clock cap. It is kept as a backup concept (I-4), opt-in under G-3.
  - Sampling must not wake the GPU: NVML queries do. So it should only run while `power/runtime_status` is already `active`.

## dGPU stand-in in control mode (DO-1, 15.11.2026)
Run: Best performance on the Surface PSU, CPU + dGPU load for 10 min, then 3 min idle, with `--dgpu-clock-cap`.
- **Target:** output 201 was 50.0 W throughout.
  - The base skin loop (sensor 609) went READY at 47.2 °C with 57 W. That is above the 50 W table value, so the table bound.
  - The base peaked at 47.5 °C and cooled again while its fans ran.
- **Regulation:**
  - Within 20 s the cap went to 1350 MHz.
  - Last 5 min of load: dGPU power 48.0–50.7 W (mean 49.2 W; Windows 49.6 W), cap 1290–1305 MHz.
  - The CPU side was unchanged: skin 48.2 °C, system power 32.1 W, steps 0.38–0.50 W.
- **Idle:** the cap stayed (1305 MHz), and the GPU went to D3cold. The stand-in did not keep it awake.
- **Stop:** the cap was released. Under load afterwards the GPU ran at 1590 MHz and 63.5 W.
- **dGPU limit in the blob for all states** (base cool, Surface PSU):
  - Recommended and Better/Best performance, on AC and on battery: 50 W. Exception: Recommended on battery, 40 W.
  - Battery saver: 35 W.
  - The same for every posture. No state allows more than 50 W.
- **Open:** on Windows, the NVIDIA limit read 65 W with only USB-C connected (`02`).
  - The blob gives 50 W when only the base input power is changed to 65 W.
  - [H] With USB-C only, other inputs change too, or Windows sets no limit in that case. This needs a Windows trace.
