# Surface SMF: how it works

The **Surface System Management Framework** is a Windows driver stack:
- a core (`SurfaceSystemManagementFrameworkDriver.sys`);
- clients for system, CPU, display, battery/power and GPU;
- the user-mode `SurfaceService.exe`.

Sources: static analysis of the SB3 drivers with Ghidra 12.2 (headless), the blobs, and the Windows measurements. Evidence:
`raw/arbeitsordner/smf-blob/REGLER-ANALYSE.md`, `BEFUND.md`, `STAND.md`. Everything below is [C] unless marked.

## Configuration blob
- **Source:** UEFI variable `SMF_DATA_BLOB-a3d05fa9-db5e-490c-956e-a57384a4b70b` (attributes NV+BS+RT), readable without root via efivarfs.
  The firmware writes it from an SMF UEFI capsule shipped in Microsoft's firmware packages.
  - On the SB3 15" the variable and the capsule content are byte-identical.
  - Newer models ship the blob as a driver-package file instead (see 04).
- **Format:** 4 bytes of EFI attributes, then a 20-byte header (version, word 2, flags with bit 2 = LZNT1, length), then LZNT1 (Windows
  `RtlDecompressBuffer`, at most 30,720 bytes decompressed).
- **Content:** plain-text CSV with CRLF line endings. Units are mW for power and 0.1 K for temperature (3211 = 48.0 °C).
- **Sections:**
  - input channel lines (sensor channel, EC trip point, poll period, kind, range);
  - output channel lines (channel, default, max, min);
  - algorithm lines (stage, divider, algorithm GUID, id, inputs, parameters);
  - telemetry ranges.

## Evaluation model
- The core instantiates two evaluation instances with fixed periods of **1000 ms and 5000 ms**. Column 0 of each algorithm line picks the
  instance; all known blobs use 5000 ms.
- **Stages run in ascending order**, lines within a stage in file order.
- **Validity:** every block first checks that all its inputs are connected and valid. If not, it outputs its default: a lookup table its
  "low" value, a min/max block its default. For example, the PL1 min node outputs 15 W and psys 45 W.
- **Timer:** the periodic evaluation runs while a controller is active. Otherwise the pipeline is evaluated on input changes (e.g. a slider
  change).
- **Resume from hibernate (D0 entry):** every block is reset. Controllers go off with output = U_off and counters at 0.

## Block types used by the SB3 blob
These are lookup table per slider state, piecewise-linear map, add/subtract/scale, min/max select, pass-through/cap, thresholds with
hysteresis, temperature bands, and the PID controller with parameter sets. The core has 18 runtime types; newer cores add more (see 04).
Blocks are identified by GUID in the blob; type numbers are internal and shift between core versions.

## The controller (PID with parameter sets)
Inputs: the measured value x and a set selector (rounded to a set number). Parameters per set:
- U_off, U_max, U_min, U_on;
- set point W and start offset ΔW_on;
- gains K_e, K_Δe, K_m.

Constraint: U_min ≤ U_on ≤ U_off ≤ U_max.

State machine (core ≤ 3.140, e.g. SB3):

```
x < W − ΔW_on − hysteresis_off        → OFF:    U = U_off (no limit in practice)
OFF and x ≥ W − ΔW_on                 → READY:  U = U_on  (fixed cut-in)
READY and x ≥ W                       → ACTIVE: control from U_on, error memory cleared
```

Law (mode 0, velocity form), with e = W − x:

`U_k = clamp(U_{k−1} + K_e·e_k + K_Δe·(e_k − e_{k−1}) + K_m·mean(e over N+1 samples), U_min, U_max)`

- With |x − W| < deadband, U is held.
- K_Δe acts as the proportional part, K_e as the integral part per step, and K_m as a small extra integral on the moving mean.
- A step is taken every (divider+1) timer runs. With divider 1 and 5 s, that is **10 s per step**.

Cores ≥ 3.159 go from OFF directly to ACTIVE at x ≥ W, take a law step on entry, and take the first step without increment (see 04).

## SB3 15" parameter chain
- **PL1 (output 51)** = clamp(min(skin controller, slider PL1, posture PL1, battery controller), 8.5 W, 30 W); the default without inputs
  is 15 W.
  - **Skin controller (id 1):**
    - input `I_VTS1` of the display part (SAM, channel 509);
    - set selected by slider state; W = 48.0 °C (45.0 °C in state 7); U_on = 18/20/21 W (16 W in state 7);
    - U_min 7.5 W, U_off/U_max 30 W, ΔW_on 1 K, deadband 0.4 K, hysteresis 1.5 K;
    - K_e 0.4 W/K, K_Δe 1 W/K, K_m 0.004 W/K, N = 20.
  - **Slider PL1:** 25/13/25/25/30/25/10/25/25/25 W for states 1–10.
  - **Posture PL1:** 1 laptop → 30 W, 2 laptop with lid closed → 15 W, 3 no form factor → 15 W, 4 canvas with lid closed → 15 W,
    5 canvas → 25 W. The posture comes from the Surface hot-plug driver.
  - **Battery controller (id 2):**
    - input: display battery discharge power `I_BCDR1`;
    - set by charge level in tenths; W = 16…23 W;
    - U_on 12–14.5 W, U_min 9 W, deadband 0.6 W, hysteresis 3 W.
- **PL2 (output 52)** = slider PL2: 25/15/40/25/61/35/11/61/30/25 W.
- **psys:**
  - PL1 (54) = clamp(min(`I_IPL_TOP` + 23 W (≤ 10 % charge: + 16.1 W), K68), 10 W, 100 W), where K68 is switched by PSU size at 95 W;
  - PL2 (55) = `I_IPL_TOP` + 34.5 W (≤ 10 %: + 23 W);
  - default without inputs: 45 W.
- **PL3 (59)** = clamp(`I_BAT_MAX1` + `I_IPL_TOP`, 12 W, 100 W).
- **PL4 (53)** = clamp(`I_BAT_PEAK1` + `I_IPL_TOP` − `I_ROP_DYN` − P_display, 15 W, 129 W). P_display is estimated from the panel brightness
  (k = 0.00986 [H for the exact field order]); it is 3 W without a brightness value.
- **dGPU (201)** = min(base skin controller (id 4, `I_VTS1` of the base, channel 609, W 45–48 °C, U_on 50–57 W), base battery controller
  (id 3, `I_BCDR2`), tables), 15–70 W.
  - The GPU client applies it to the NVIDIA GPU as a percentage through a Windows-only NVIDIA admin interface. This covers 8 GPU IDs,
    including the SB3's 10de:2191.
- **Temperature bands** (54–56 °C …) report to the Windows thermal policy, not to PL1.

Slider states 1–10, as mapped by `SurfaceService.exe` for the SB3:
- 1/2 = Recommended AC/battery;
- 3/4 = Better performance AC/battery;
- 5/6 = Best performance AC/battery;
- 7 = battery saver on battery;
- 8–10 = further states.

Energy saver on AC maps to state 1.

## Inputs fed by the system
- **System client:** registers slider, lid (Windows power setting), posture 2/3 and one unnamed input. It accepts slider and external display
  from the service.
- **Service:** also sends SSD over-temperature, NVIDIA Dynamic Boost and game controller. The SB3 client rejects all three.
- **Display client:** sends brightness for PL4.
- **Camera scenario:** changes only Windows processor settings (EPP), not the SMF chain.
- **Temperatures:** the client sets an EC trip point at 42 °C (TMP CID 0x09). Above it, it reads every 1 s; below trip − 2 K for 4 polls in a
  row, it stops. An EC event (TMP 0x0b) restarts the polling.

## Matches with the Windows measurements
| Measured [M] | Explained by [C] |
|---|---|
| cut 30 → 21 W (Best performance), 25 → 18 W (energy saver) | U_on of the active set |
| cut earlier when warm, independent of CPU temperature | trigger is `I_VTS1` ≥ 47 °C |
| 0.4–0.6 W steps with long holds | K_e 0.4 W/K per 10 s step, deadband ±0.4 K |
| ~32 W system plateau | skin regulated to 48 °C on `I_VTS1` |
| jump back to 30 W after the load | off below 45.5 °C → U_off |
| PL1/PL2 per mode and source | slider table |
| psys never binds | psys = available power + 23 W |
| CPU at 95–100 °C before the cut | no CPU-temperature loop in the chain |
