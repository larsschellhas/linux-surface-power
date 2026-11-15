# Spec: output adapters

Version 0.1, 14.11.2026. Implements FR-O1 – FR-O7, NFR-2, FR-S7. Tests: AC-I4, AC-O1, AC-O2, AC-S7. Markers as in `blob-format.md`.

## 1. Single write path (NFR-2)
All limit writes go through one function, `OutputStage.apply(values)`, where `values` maps a channel to its rounded engine value (`engine.md` 6). For each channel it does:
1. **Clamp** the value, in order:
   - to the output-line range of the blob (`min ≤ v ≤ max`; channels without a line are not clamped here);
   - to the hard limits in the code (2), which a configuration can only narrow (FR-S7);
   - to the user's narrowing configuration, if any.
2. **Map and convert:** map the channel to its target (3) and convert units (mW → µW).
3. **Compare** with the last value written. Write only on a change (FR-O4).
4. **Read back** after writing and keep the read-back value. The hardware rounds, for example PL1 to 1/8 W.

In dry-run mode (FR-M4) steps 1–2 run and the results are logged; steps 3–4 are skipped.

## 2. Hard limits [D]
These are independent of any blob. A value above the hard maximum is cut to it and logged once.

| Target | Hard min | Hard max |
|---|---|---|
| package PL1 | 3 W | 120 W |
| package PL2 | 3 W | 200 W |
| package PL4 | 5 W | 250 W |
| psys PL1, PL2 | 5 W | 250 W |
| PL3 | 5 W | 250 W |

The powercap value `constraint_*_max_power_uw` is **not** used as a bound. On the SB3 it reports the 15 W TDP, while the firmware PL1 is 30 W and Windows sets up to 30 W [M].

## 3. Channel → target (FR-O1, FR-O2, FR-O5)

| Interface | Channel | Meaning | Target |
|---|---|---|---|
| `0B7B5350` | 51 | package PL1 | `/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw` (`long_term`) |
| | 52 | package PL2 | `intel-rapl:0/constraint_1_power_limit_uw` (`short_term`) |
| | 53 | PL4 | `intel-rapl:0/constraint_2_power_limit_uw` if its name is `peak_power`, and only if enabled in the device profile. Default: log-only [D]. On the SB3 Linux reports 0 for it; what writing it does there is not verified. |
| | 54 | psys PL1 | `intel-rapl:1/constraint_0_power_limit_uw` (zone named `psys`) |
| | 55 | psys PL2 | `intel-rapl:1/constraint_1_power_limit_uw` |
| | 59 | PL3 | MSR 0x615, see 4 |
| | 70 | battery mode | log-only |
| | 201 | dGPU limit | log-only (FR-O5): `nvidia-smi -pl` is not supported on the SB3 GPU [M]. Opt-in stand-in: clock cap (6, DO-1) |
| | 205 | system budget (Laptop Studio 2) | log-only |
| `110DF2A4` | 1 – 3 | Windows thermal policy (hot, critical, min power) | log-only |
| other | – | fan, WTMF and so on (L3/L4) | log-only (FR-O6, FR-O7) |

- **Zone lookup:** zones are found by name (`package-0`, `psys`), not by index. A missing zone makes its channels log-only, with one message.
- **PL1 time window:** the daemon does not write it. It checks it against the firmware value from the snapshot and restores it if someone else changed it, because a foreign short window would change PL1's effect (`supervisor.md` 3).
- **Enable bits:** writes never change `enabled`. A disabled zone is reported. The daemon does not enable it.

## 4. PL3 through MSR (FR-O2)
- **Encoding:** MSR 0x615 holds PL3 in power units from MSR 0x606 (`1/2^PU W`). The adapter changes only the power-limit field (bits 0–14) and keeps the time window and the enable bit as read.
- **Availability:** this needs `/dev/cpu/0/msr` and kernel lockdown `none`. If either is missing, PL3 is log-only and reported once (AC-O1).
- **Default:** PL3 writes are off by default [D], so that the default path needs no raw MSR access (NFR-3, NFR-11). Whether PL3 binds in normal use on the SB3 is not measured [H: it does not, because the blob value is the battery sustained power plus the available input power]. Enabling it is an option in the device profile.

## 5. Log-only outputs
Log-only outputs are computed, published in the status file and the time series, and never written (AC-O2).

## 6. dGPU stand-in through a clock cap (opt-in, DO-1, idea I-4)
Windows limits the dGPU power. On Linux, the SB3's NVIDIA driver refuses a power limit, and Dynamic Boost is not
supported (`08-field-tests-sb3.md`). A cap on the graphics clock (`nvidia-smi -lgc`, NVML `SetGpuLockedClocks`) works.
This stand-in uses that cap to regulate the **measured** dGPU power to the blob's output 201. It restores the Windows
*behaviour* (G-1) with a different *mechanism*, so it is a named deviation (DO-1). It is enabled per device profile
(`[outputs] dgpu_clock_cap = true`). It is on in the SB3 profile: without it the Linux driver lets the dGPU draw 65 W,
while no Windows state in the SB3 blob allows more than 50 W.

- **Never wake the GPU.** Every NVML session opens the GPU and restarts its idle timer.
  - The stand-in therefore only opens NVML while another process already has an NVIDIA device node open (`/proc/<pid>/fd` → `/dev/nvidia*`). That process keeps the GPU awake anyway.
  - If only the daemon itself has it open, that does not count.
  - `power/runtime_usage` turned out not to be a reliable indicator [M]: it read 2 with a CUDA client once and 1 another time.
  - Monitoring tools that hold the device open (e.g. GNOME Resources) count as clients. The GPU does not sleep while they run, with or without the stand-in.
- **Cap persistence:** a cap set while the GPU is awake stays in force across D3cold [M]. While no client runs, the stand-in does nothing and keeps its last cap.
- **Control law [D], once per sample (every 2 s, while a client runs).** The blob gives only the target. On Windows, the NVIDIA driver enforces the limit with its own internal loop, so there is no Windows model for these parameters. They were chosen from the SB3 clock-cap measurement:
  - Inputs: the measured power `P` (NVML power usage, W) and the target `T` (output 201, W).
  - If `P > T + 0.5 W`: lower the cap by `k · (P − T)`, at least one step.
  - If `P < T − 2 W` and the GPU runs at the cap (clock ≥ cap − 30 MHz): raise the cap by one step of 15 MHz.
  - Otherwise: hold.
  - `k = 20 MHz/W`, from the SB3 measurement: about 10 MHz/W between 900 and 1300 MHz, doubled for a fast first approach.
  - The cap stays within the supported clock range. It is rounded to 15 MHz steps.
  - If `T` is at least the GPU's own default power limit, the cap is released.
- **Release:** the cap is removed when:
  - the daemon stops;
  - it enters emergency, yields or pauses for sleep;
  - the profile option is off;
  - the target is invalid.
  The original state (no cap) is the firmware value.
- **Missing inputs:** with the stand-in on, the dGPU limit counts as a written output, so its inputs are required (`supervisor.md` 2.5).
  - If one is missing, only the stand-in is switched off, with a warning. The other limits are still controlled.
  - Example: the SB3 without the base sensor nodes, as with current kernels.
- **Dry-run:** computes and logs the cap it would set, sets nothing, and still only samples under the rule above.
- **Failures:** an NVML error disables the stand-in for the rest of the run and is logged once. Examples: no library, no permission, unsupported call.
