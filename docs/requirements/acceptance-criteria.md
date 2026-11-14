# Acceptance criteria

Version 0.1, 14.11.2026. Each criterion names the requirements it verifies. "Reference runs" are the Windows measurements on the
SB3 15" (`../research/02-windows-measurements-sb3.md`, raw data in `../research/raw/arbeitsordner/windows-ergebnis/`).

Test levels:
- **U** unit test (no hardware);
- **P** parser corpus (local blobs, not in the repo);
- **R** replay against measurements;
- **D** on the device;
- **X** review or inspection.

## Blob acquisition and parsing

| ID | Criterion | Verifies | Level |
|---|---|---|---|
| AC-B1 | On the SB3 15", the blob read from efivarfs is byte-identical to the blob in the SB3 15" capsule `Smf_15in_39.0.1.4`. | FR-B1, FR-B3 | D |
| AC-B2 | All 41 known SMF blobs (forms A, B, C) decode and parse without error. | FR-B2–FR-B5 | P |
| AC-B3 | For each of the 41 blobs, every algorithm GUID is either implemented or listed as unsupported, and the report names the outputs it affects. | FR-B7, FR-B8 | P |
| AC-B4 | Corrupted input fails safe with the line number and reason. Covered cases: truncated LZNT1, a wrong length field, a bad CSV line, U_on outside U_min…U_off, and an unknown macro. No limit is written. | FR-B5, FR-B6, G-4 | U, D |
| AC-B5 | The expression evaluator accepts only numbers, + − * / and brackets. Any other token is rejected. | FR-B4 | U |
| AC-B6 | Macro expansion of the three macro-source blobs gives the same graph as hand expansion (spot check of 3 outputs each). | FR-B4 | P |

## Engine

| ID | Criterion | Verifies | Level |
|---|---|---|---|
| AC-E1 | Every implemented block type has unit tests for normal values, the limits, invalid inputs (outputs its default) and reset. | FR-E1, FR-E3, FR-E5 | U |
| AC-E2 | **Slider table:** for all six measured mode × source pairs on the SB3, the engine outputs the measured PL1/PL2 ceilings exactly: 25/25, 13/15, 25/40, 25/25, 30/61, 25/35 W. | FR-E1, FR-I3 | R |
| AC-E3 | **Skin controller (SB3, core ≤ 3.140), synthetic input:** a rising `I_VTS1` ramp gives OFF → READY at 47.0 °C with PL1 = U_on of the set, then ACTIVE at 48.0 °C, steps every 10 s, no change inside ±0.4 K of 48.0 °C, and OFF with PL1 = 30 W below 45.5 °C. | FR-E1, FR-E2, FR-E4 | U |
| AC-E4 | **Replay, Best performance on AC:** fed with the `I_VTS1` series from a Linux run with matching load, the PL1 trajectory matches the Windows reference run. The cut 30 → 21 W happens in one step; then come steps of 0.4–0.6 W at 10 s intervals; the plateau value is within ±1 W of the measured steady PL1. The exact load-to-skin mapping differs between OSes, so the shape and the parameters are compared, not the timestamps. | FR-E1, FR-E2, NFR-1 | R |
| AC-E5 | **psys:** with `I_IPL_TOP` = 73.5 W and charge > 10 %, psys PL1 = min(73.5 + 23 W, 45 W) = 45 W and PL2 = min(73.5 + 34.5 W, 100 W) = 100 W, for both PSU sizes. With `I_IPL_TOP` invalid, PL1 = 23 W and PL2 = 34.5 W (block defaults). With charge ≤ 10 %, + 16.1 W / + 23 W apply. The numbers are fixed in `../specs/engine.md` 7. | FR-E1, FR-E3, FR-I4 | U |
| AC-E6 | **Validity:** when any input of the PL1 min node is invalid, PL1 = 15 W (its default). When it becomes valid again, normal values return within one evaluation. | FR-E3 | U |
| AC-E7 | **Resume:** after a simulated resume, every PID is OFF with output U_off and the timer restarts if it was running. | FR-E5 | U |
| AC-E8 | **Variant ≥ 3.159:** with the same synthetic ramp, OFF → ACTIVE directly at x ≥ W with a law step on entry, and the first step without increment. | FR-E4 | U |
| AC-E9 | **Determinism:** two replays of the same input file give byte-identical output files. | FR-E6 | U |

## Inputs and outputs (on the device)

| ID | Criterion | Verifies | Level |
|---|---|---|---|
| AC-I1 | On the SB3, every input channel the blob uses is either provided (with its value in the status file) or reported once as unavailable. | FR-I1, FR-I8 | D |
| AC-I2 | The `0x53` data is read at start and after each plug/unplug of the PSU. The reported available power changes accordingly: display part 45.7 W on battery, ~73.5 W with the Surface PSU. | FR-I4 | D |
| AC-I3 | A profile change through `powerprofilesctl` changes the slider state within one evaluation (≤ 5 s), and the PL1/PL2 ceilings follow AC-E2. | FR-I3 | D |
| AC-I4 | All writes are within the blob's output ranges and the hard limits. This is checked by a test that feeds extreme inputs and asserts every write. | FR-O3, NFR-2 | U |
| AC-O1 | With lockdown active (or MSR unavailable), the service runs; PL3 is reported as skipped and nothing else changes. | FR-O2, NFR-6 | D (or U with a mocked MSR) |
| AC-O2 | The dGPU limit (201) is visible in the status file and the time series, and nothing is written to the GPU. | FR-O5 | D |

## Safety

| ID | Criterion | Verifies | Level |
|---|---|---|---|
| AC-S1 | After a clean stop (SIGTERM), all saved limits are back to the firmware values within 2 s. | FR-S1 | D |
| AC-S2 | After `kill -9`, `ExecStopPost` restores all firmware values, and the saved state file is removed afterwards. | FR-S2 | D |
| AC-S3 | A simulated hang (loop blocked > watchdog time) leads to a restart. Three failures within 10 min leave the device on firmware values with thermald available as a fallback. | FR-S3 | D |
| AC-S4 | Suspend/resume: firmware values are written before suspend; after resume the engine is reset (AC-E7) and control continues without a restart. | FR-S1, FR-E5, NFR-5 | D |
| AC-S5 | Three foreign PL1 changes within 60 s make the service yield and log it. | FR-S5 | U, D |
| AC-S6 | The service does not start while thermald is active (`Conflicts=`). | FR-S6 | D |
| AC-S7 | A configuration that tries to raise any limit above the blob or the hard limits is rejected or clamped, and this is logged. | FR-S7 | U |
| AC-S8 | On an unsupported device, no limit is written (verified with a mocked DMI). | FR-D2 | U |

## Behaviour on the reference device (L1 release)

| ID | Criterion | Verifies | Level |
|---|---|---|---|
| AC-L1 | **Best performance, Surface PSU, 30 min sustained CPU load:** `I_VTS1` settles at 48.0 ± 0.5 °C, system power plateaus at 32 ± 3 W, no emergency state, and PL1 releases to 30 W after the load ends. | G-1, NFR-1 | D |
| AC-L2 | **Recommended on AC and on battery, 15 min load each:** the ceilings are 25/25 and 13/15 W. The skin controller cuts in at the U_on of the set when `I_VTS1` reaches 47 °C. | G-1 | D |
| AC-L3 | **psys on Linux no longer binds with the Surface PSU:** system power under CPU+GPU load exceeds 40 W (the old firmware PL2), while the skin and battery limits hold. | G-1, FR-I4 | D |
| AC-L4 | **Dry-run for 3 days of normal use:** no crash, no invalid core input except as documented, CPU < 0.5 %, memory < 50 MB. | FR-M4, NFR-4, NFR-5 | D |
| AC-L5 | The status file and the journal show the controller states and limits during AC-L1, and the replay tool reproduces the same limits from the recorded inputs. | FR-M1, FR-M2, FR-M6 | D, R |

## Mid-term (L2 release)

| ID | Criterion | Verifies | Level |
|---|---|---|---|
| AC-M1 | At least two further SMF x64 devices (one with a core ≥ 3.159) run in dry-run on real hardware. Their computed ceilings match the slider table of their blob, and their owners confirm the values against Windows (HWiNFO PL1/PL2 per mode). | FR-D3, FR-E4, NFR-1 | D (community) |
| AC-M2 | The report tool output from those devices is enough to add them to the supported list without access to the device. | FR-M7 | X |

## Process and repository

| ID | Criterion | Verifies | Level |
|---|---|---|---|
| AC-X1 | The repository contains no Microsoft binaries, decompiled code or blob content. This is checked by CI (forbidden patterns, file types, and a check that the blob signature is absent from test data). | NFR-8 | X, CI |
| AC-X2 | Every FR/NFR has at least one AC, and every spec references its FR/NFR. | NFR-10 | X |
| AC-X3 | Every deviation from Windows behaviour is listed in one document with its reason and default (off). | G-3, NFR-10 | X |
