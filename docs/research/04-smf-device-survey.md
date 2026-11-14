# Device survey: which Surface uses what

The current Microsoft firmware package (MSI) of each model was unpacked and its SMF blobs and SMF core drivers extracted (33 packages,
41 blobs from 26 SMF devices, 15 core versions from 2.51 to 3.205.23, x64 and arm64). The results were cross-checked against
[linux-surface/acpidumps](https://github.com/linux-surface/acpidumps). Evidence: `raw/arbeitsordner/smf-blob/GERAETEVERGLEICH.md`, blobs in
`raw/arbeitsordner/smf-blob/geraete/`, cores and decompiles in `raw/tools/fw/`.

## Three generations [C]

| Generation | Devices |
|---|---|
| Intel DTT/DPTF (ACPI `INT3400`) | Book 2, Pro 5/6/7, Laptop 1/2/3 Intel, Go 1/2, Studio 2 |
| **Surface SMF** (ACPI `MSHW0187`), x64 | Book 3, Pro 7+, Pro 8, Pro 9 Intel, Pro 10 for Business (+5G), Pro 11 Intel, Laptop 3 AMD, Laptop 4 AMD/Intel, Laptop 5, Laptop 6, Laptop 7 Intel (+5G), Laptop Go 1/2/3, Go 3, Go 4, Laptop Studio 1/2 |
| **Surface SMF**, arm64 | Pro 9 5G (SQ3), Laptop 7 Snapdragon, Pro 11 Snapdragon, Laptop 13" 1st Ed., Pro 12" 1st Ed. |
| SMF by ACPI, no package available | Pro X (SQ1/SQ2), Laptop SE, Windows Dev Kit 2023 |
| **Windows MPTF** (no SMF core; blob for `MPTFCore` + Surface thermal service) | Pro 12th Ed. Intel/Snapdragon, Pro 12" 2nd Ed., Laptop 8 Intel/Snapdragon, Laptop 13" 2nd Ed. / Intel for Business, Laptop Ultra |

- Every device with `MSHW0187` that has a package ships an SMF core and a blob.
- `MSHW0216` (GPU power controller) appears on SB3, LS1 and LS2.
- Studio 2+ is not checked.
- The CPU client's INF covers Ice Lake, Tiger Lake, Gemini Lake, Alder Lake, Raptor Lake, Meteor Lake and Qualcomm 8280/8380.

## Blob delivery [C]

| Form | Devices | Layout |
|---|---|---|
| A. Old capsule, uncompressed | Laptop 3 AMD (core 2.51) | 7-word header, UTF-8 text with BOM and `//` comments |
| B. EFI capsule, LZNT1 | most devices up to 2024 | 20-byte header + LZNT1 (as on the SB3); the UEFI variable holds the same |
| C. File in the driver package | Laptop 13" 1st, Pro 12" 1st, Laptop 7 5G | `smfblobpackage/<code>.bin`, raw LZNT1; registry `SmfConfigBlobFilePath`. The UEFI variable is probably empty or stale [H] |
| D. MPTF | 2025/26 models | `mptfblobpackage/*.bin`, LZNT1 macro text, for `MPTFCore` (not SMF) |

## Blob text forms [C]
1. **Plain CSV** (all cores).
2. **Expressions** (core ≥ 3.204.23; used by Laptop 7 Intel, Pro 11 Intel): channels as `{GUID}_N`, parameters as bracketed arithmetic,
   e.g. `(28000+10000)`. Most contain only numbers and + − * /, but Laptop 7 Intel, Pro 11 Intel and the macro-source blobs also
   use internal nodes inside expressions, e.g. `({FB805E77-…}_208+30)` as table bounds (corrected 14.11.2026 evening; found when
   parsing all blobs, `../specs/blob-format.md` 4.2).
3. **Macro source** (form C): `//` comments, `#macro NAME VALUE`, `%NAME%`, built-in `VN<n>` = intermediate node.

The macro blobs carry Microsoft's own names, which confirm the SB3 reading:
- outputs: `O_CPU_AVG` = 51 PL1, `O_CPU_MAX` = 52 PL2, `O_CPU_PEAK` = 53, `O_SYS_AVG` = 54, `O_SYS_MAX` = 55, `O_SYS_PEAK` = 59,
  `O_BATT_MODE` = 70;
- algorithms: `A_TABLE`, `A_PIDARRAYSETUP`, …;
- parameters: `KP`, `KI`, `KD`, `DEADBAND`, `PID_START_OFFSET`, `HYSTERESIS`, `INITIAL_LIMIT`.

## Core versions [C]
- **The core ships with the package, not with the device.** Each package carries the core of its build date; e.g. Pro 7+ and Laptop 4 Intel
  get 3.205.17, the SB3 package 3.140.
- **Controller logic comes in two variants:**
  - ≤ 3.140 (SB3, Laptop Go 1, also 3.137/3.78): OFF → READY → ACTIVE, first step with increment;
  - ≥ 3.159 (to 3.205.23, also arm64): OFF → ACTIVE directly at x ≥ W, a law step on entry, first step without increment.
  - 3.78/3.137 implement only the velocity form (mode 0).
- **New block types in newer cores:** `VARIABLEPID` (set point from an input), `EWMA` (time-based smoothing), conditional table,
  multiply/divide, hysteresis, hysteresis table. 3.205.23 adds one more and shifts all type numbers from 12 up by one. Blobs reference
  GUIDs, so an interpreter must map by GUID.
- arm64 type numbers match x64. Whether the GUID tables of the arm64 cores match too is not read [H].

## Common to all SMF blobs [C]
- **Skin controller** on `I_VTS1` (channel 509) or on a value corrected from it (offset per posture and slider: SB3 13", Laptop 4 Intel,
  Laptop Go 2, Pro 8, LS1, Pro 9 5G; Go 3 on channel 504).
- **Set point:** 44–54 °C.
  - Older blobs pick the set directly by slider state.
  - From 2022 on, a min/max select over slider, posture, dock and display tables picks it.
  - Laptop 13" 1st uses a variable-set-point PID.
- **Tuning:** deadband 0.2–0.5 K, hysteresis_off 1.0–3.0 K, start offset 0.5–2.5 K.
- **Cut-in:** a fixed value, from 5–9 W (Go 3) to 70–95 W system budget (LS2 dGPU).
- **Timing:** pipeline 5 s everywhere; controller step every 5, 10, 15 or 20 s (divider 0–3). Newer devices mostly use 5 s.
- **Battery controller:**
  - SB3 and Laptop 3 AMD use discharge power (`I_BCDR1`, mW);
  - all others use channel 12 (`I_BCDR1_I`, mA per macro name);
  - newer devices add a second battery loop over a map.
- **A second skin sensor** (channel 512) in LS1, LS2 and Laptop 7 Snapdragon.
- **Base loop → dGPU and a second battery:** SB3 only.
- **LS1/LS2 dGPU:** the skin controllers set a shared system budget (outputs 205 + 51); CPU PL1 is derived from it.
- **Outputs:**
  - Intel/AMD: 51/52/53/54/55/59/70, plus 201 (dGPU) on SB3/LS1/LS2;
  - Snapdragon: no PL1/PL2; instead WTMF stages, the fan and the thermal policy. The newest Snapdragon blobs regulate WTMF on smoothed
    system power with a variable-set-point PID.
- **From 2023,** many blobs no longer set PL1 from the slider table (e.g. Pro 10: 30 W in all states but 2/7) but through the system limits
  54/55.

## SB3 13" vs 15" [C]
- **Skin input:** the 13" uses `I_VTS1` corrected per posture (−2 K) and slider (0 / −3 K); set point still 48 °C, cut-in 15 W (10 W in
  state 7).
- **dGPU loop:** 32–35 W, dGPU tables 20–40 W (smaller GPU).
- **psys:** `I_IPL_TOP` + 18 W / + 27 W.
- **Display power factor:** 0.00826.

## Open [H]
- the state of the UEFI variable on form C devices;
- which core version actually ran last on a given device (Windows Update may install a newer one than the package; Linux can't see it);
- the slider state numbering on other devices;
- units of channel 12 and of the WTMF outputs;
- MPTF content (tracked in [linux-surface#2384](https://github.com/linux-surface/linux-surface/issues/2384)).
