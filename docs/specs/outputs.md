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
| | 53 | PL4 | `intel-rapl:0/constraint_2_power_limit_uw` if its name is `peak_power`. Automatic: written where it reads a value above 0; a device profile can switch it on or off [D]. On the SB3 Linux reports 0 for it; what writing it does there is not verified, so the SB3 profile switches it off. While an EC input on its path is invalid, the value is held (3.1). |
| | 54 | psys PL1 | `intel-rapl:1/constraint_0_power_limit_uw` (zone named `psys`) |
| | 55 | psys PL2 | `intel-rapl:1/constraint_1_power_limit_uw` |
| | 59 | PL3 | MSR 0x615, see 4 |
| | 70 | battery mode | log-only |
| | 201 | dGPU limit | log-only (FR-O5): `nvidia-smi -pl` is not supported on the SB3 GPU [M]. Opt-in stand-in: clock cap (6, DO-1) |
| | 205 | system budget (Laptop Studio 2) | log-only |
| `9BBB8910` | 1–5, 20–22, 62 | throttle requests for CPUs, clusters, GPU and the aggregator device | cpufreq or GPU frequency cap (8) |
| | 103–105, 200 | modem sensors, NPU | log-only (no Linux interface known) |
| `110DF2A4` | 1, 2 | thermal policy: hibernate (`O_HOT`) and critical shutdown (`O_CRT`) requests | thermal actions (7) |
| | 3 | minimum-power request | log-only |
| other | – | fan, WTMF and so on (L3/L4) | log-only (FR-O6, FR-O7) |

- **Zone lookup:** zones are found by name (`package-0`, `psys`), not by index. A missing zone makes its channels log-only, with one message.
- **PL1 time window:** the daemon does not write it. It checks it against the firmware value from the snapshot and restores it if someone else changed it, because a foreign short window would change PL1's effect (`supervisor.md` 3).
- **Enable bits:** writes never change `enabled`. A disabled zone is reported. The daemon does not enable it.

### 3.1 Holding PL4 and PL3 while their EC inputs are invalid [D]
PL4 and PL3 are computed from the EC power data on most devices. Without them the blob falls back to values far below the firmware
value (Pro 10: PL4 20 W instead of the firmware's 45 W). While any EC input (`28B7BD85`, the `0x53` and `ARTG` fields) on the path to
PL4 or PL3 is invalid, the output stage keeps the current value: the firmware value before the first write, the last written value
after it. The status file reports such a channel as `held`. psys keeps the Windows behaviour (its fallback is what Windows does without
EC data, `engine.md` 7).

## 4. PL3 through MSR (FR-O2)
- **Encoding:** MSR 0x615 holds PL3 in power units from MSR 0x606 (`1/2^PU W`). The adapter changes only the power-limit field (bits 0–14) and keeps the time window and the enable bit as read.
- **Availability:** this needs `/dev/cpu/0/msr` and kernel lockdown `none`. If either is missing, PL3 is log-only and reported once (AC-O1).
- **Default:** automatic: PL3 is written where the MSR device is writable (root, lockdown `none`). A device profile can switch it off. The SB3 profile does, until it is measured: the field tests ran without it [H: it does not bind in normal use, because the blob value is the battery sustained power plus the available input power].
- **Snapshot:** the firmware value of MSR 0x615 is part of the snapshot (`supervisor.md` 3) and restored with the powercap values.
- **Read-back:** the limit field is read back like a powercap file; a foreign change of the field counts as a foreign change.

## 5. Log-only outputs
Log-only outputs are computed, published in the status file and the time series, and never written (AC-O2). A blob with a log-only output is not allowed to control (`supervisor.md` 2.6).

## 6. dGPU stand-in through a clock cap (opt-in, DO-1, idea I-4)
Windows limits the dGPU power. On Linux, the SB3's NVIDIA driver refuses a power limit, and Dynamic Boost is not
supported (`08-field-tests-sb3.md`). A cap on the graphics clock (`nvidia-smi -lgc`, NVML `SetGpuLockedClocks`) works.
This stand-in uses that cap to regulate the **measured** dGPU power to the blob's output 201. It restores the Windows
*behaviour* (G-1) with a different *mechanism*, so it is a named deviation (DO-1). It is enabled per device profile
(`[outputs] dgpu_clock_cap = true`). It is on in the SB3 profile: without it the Linux driver lets the dGPU draw 65 W,
while no Windows state in the SB3 blob allows more than 50 W.

- **Never wake the GPU.** Every NVML session opens the GPU and restarts its idle timer.
  - The stand-in therefore only opens NVML while another process already has an NVIDIA device node open (`/proc/<pid>/fd` → `/dev/nvidia*`). That process keeps the GPU awake anyway.
  - **Cost of that check:** a scan of `/proc/*/fd` costs about 25 ms CPU [M, AC-L4 run 16.11.2026]. It is skipped while the GPU's
    `power/runtime_status` is `suspended`. When the GPU wakes up (usually because a client opened it) it runs at once,
    otherwise at most every 10 s; the stand-in and the dGPU sensors (`inputs.md` 8.10) share the result. Regulation comes first: a
    client that opens an already awake GPU is seen within 10 s, and after the last client exits the stand-in may keep its NVML
    session, and so the GPU, awake for up to 10 s [D]. The NVML session stays open between samples while a client is there (init costs ~20 ms CPU [M]).
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

## 7. Thermal actions (`110DF2A4/1`, `/2`) [C for the meaning, D for the Linux action]
- **Windows:** the Surface thermal-policy driver turns `O_HOT` = 2 into the "hibernate" flag and `O_CRT` = 2 into the "critical" flag of
  the thermal zone `TPOL`; Windows then hibernates or shuts down (`../research/10-outputs.md`).
- **Linux:** a request that holds for 5 s (one evaluation of the 5000 ms instance) starts once:
  - `O_CRT`: `systemctl poweroff`;
  - `O_HOT`: `systemctl hibernate`, and `systemctl suspend` if hibernation fails (it is not set up on many systems, e.g. the SB3 here).
  The critical request wins; no hibernate is started on the way to a shutdown. The action re-arms when the request is gone.
- **Armed only in control mode.** In dry-run the request is logged as a warning. The notifier shows a notification in both cases
  (`monitoring.md` 4).
- These two outputs count as written for the control rule (`supervisor.md` 2.6).

## 8. Throttle requests (`9BBB8910`) as frequency caps [C for the meaning, D for the Linux translation, H for the device mapping]
- **Windows:** the Surface thermal-policy driver passes one passive-cooling request in percent (100 = no throttling) to each device that the ACPI method `OPTS` of the thermal zone `TPOL` (`MSHW0187`) lists, through `PoSetThermalPassiveCooling`. `9BBB8910/62` (`O_WTMF_PARK`) is the request for the processor aggregator device (`../research/10-outputs.md` 2).
- **What `OPTS` returns** [M, public ACPI dumps of 23 products, `linux-surface/acpidumps`]:
  - **x86 (Intel and AMD)** (Book 3, Go 3/4, Laptop 4/5/6, Laptop Go 1–3, Laptop Studio 1/2, Laptop SE, Pro 7+/8/9/10): one entry, `\_SB.PAGD`, which is an `ACPI000C` Processor Aggregator Device. Their blobs have at most `/62`. The Laptop 3 AMD dump has no `OPTS`; its blob has no `9BBB8910` output.
  - **Pro X (SQ1, SQ2):** `CLUS.CPU4`–`CPU7`, `GPU0`.
  - **Pro 9 5G, Dev Kit 2023:** `CLUS.CPU0`–`CPU7`, `GPU0`, `PAGD`, `MSKN` (skin sensor).
  - **Pro 11 and Laptop 7 (Snapdragon):** `CLS0.CPU0`, `CLS1.CPU0`, `CLS2.CPU0`, `GPU0`, `AGR0` (also `ACPI000C`), `MSKN`, `CSW0`, `MJCT`, `MBJ0`.
  - The data are in `adapters/cpufreq.py` (`OPTS`).
- **Channel → device** [H]. The channel numbers are not `OPTS` indices (an Intel blob has `/62` for a one-entry list). The role comes from the output names in the blobs; the match with the `OPTS` entry is by name, and the aggregator device is matched with `/62` by elimination (it is the only entry without another channel):

  | Channel | Name | Device | Linux target |
  |---|---|---|---|
  | 1–4 | `O_WTMF_CPU0`–`CPU3` | `CLUS.CPU0`–`CPU3` | `scaling_max_freq` of the cpufreq policy that contains Linux CPU n |
  | 20, 21, 22 | `O_WTMF_E_CORES_C1`, `P_CORES_C2`, `P_CORES_C3` | `CLS0`–`CLS2` | the 1st, 2nd, 3rd cpufreq policy in numeric order |
  | 5 | `O_WTMF_GFX` | `GPU0` | Intel i915 `gt_max_freq_mhz` (or the per-GT `rps_max_freq_mhz`), xe `freq0/max_freq`, else the devfreq device with `gpu` in its name (Adreno); NVIDIA and AMD: none |
  | 62 | `O_WTMF_PARK` | aggregator (`PAGD`, `AGR0`) | all cpufreq policies, see below |
  | 103–105, 200 | `O_MDM_*`, `O_WTMF_NPU` | modem sensors, NPU | none: log-only |

  A channel whose target does not exist on this machine (no cpufreq, no GPU interface, fewer policies than the channel needs) is log-only. Only then it counts as without a Linux path (`supervisor.md` 2.6).
- **Translation [D]:** cap = request % of `cpuinfo_max_freq` (GPU: of its highest frequency), clamped to [floor, baseline].
  - **Percent of the maximum, not of the span above the minimum.** The Windows request is a duty-style value: 50 means about half of the throughput. On a CPU the throughput scales with the frequency, so the cap at *p* % of the maximum gives roughly *p* % of the throughput. The span formula `min + p·(max − min)` would give more than *p* %. The cap is a model of the Windows request, not a copy of it [H].
  - **Floor:** 20 % of the maximum, and never below `cpuinfo_min_freq` or the user's `scaling_min_freq` (GPU: `RPn`, the minimum). No request, not even 0, takes a device lower [D].
  - **Baseline:** the value of the file before the first write, from the snapshot (`supervisor.md` 3). A request of 100 % writes the baseline back, so a limit that the user had set before stays in force. No cap goes above it.
  - **Rounding:** `scaling_max_freq` is written in kHz, the GPU file in its own unit (MHz, devfreq Hz). The driver rounds to its steps; the read-back value counts.
- **Combining:** requests on the same target combine by the minimum. `/62` applies to every cpufreq policy; with a per-CPU or per-cluster request the lower value wins. A target without any request in a cycle is not touched.
- **Rate limit:** a new cap is written only if it differs from the current value by at least 2 % of the maximum frequency, or if it is the baseline (release). The blob changes these outputs every few seconds at most, but several steps of 1 % are of no use.
- **Failures:** a write error (not "file gone", which is a CPU that went offline and is retried) turns that target log-only for the rest of the run, with one message. In dry-run the cap is computed and published as `would` in the status file, and nothing is written. The target must exist before the output counts as written (FR-O7).
- **Snapshot and restore:** the `scaling_max_freq` files of all policies and the GPU file are in the snapshot. They are restored on exit, crash (`reset` tool and predecessor snapshot), sleep, emergency and yield. Restoring to the baseline in an emergency leaves the protection to the firmware, as for the power limits (`supervisor.md` 4).
- **Other writers (conflict):** `scaling_max_freq` and the GPU maximum are also written by users (`cpupower frequency-set -u`, `echo`), TLP and some desktop tools. thermald and power-profiles-daemon mostly limit through the cpufreq cooling device or `energy_performance_preference`, which does not touch `scaling_max_freq`, but they can change the effective maximum all the same. The daemon does not see such limits [H]. It detects a change of its own files: it logs a warning, writes its cap again, and three changes within 60 s make it yield like for power limits (`supervisor.md` 5). thermald is already refused at start (FR-S6).
- **Status:** `status.json` shows the target of a throttle output as `cpufreq cap`, `GPU freq cap` (with ` (dry-run)` in dry-run, `firmware` in emergency, yielded and paused) or `no Linux path`. The list `throttle` gives, per target, the file, baseline, current value and the dry-run value. `surface-power-report` prints `writable (cpufreq cap)`, `writable (GPU freq cap)` or `no Linux path (logged only)`.
- **Not verified:** no device in our corpus with a `9BBB8910` output other than the Intel `/62` is available to us. The SB3 15" blob has none. The cpufreq mechanism itself is the same on the SB3, so `scripts/throttle-test.py` checks the write path, the restore and the effective frequency there and on any other device (the tester needs root and stops the daemon first).
