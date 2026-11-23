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
| | 70 | battery mode (1 = adapter rating 0) | one SAM TMP `0x1b` request through the SSAM debug device when it turns 1 (9.9) |
| | 201 | dGPU limit | log-only (FR-O5): `nvidia-smi -pl` is not supported on the SB3 GPU [M]. Opt-in stand-in: clock cap (6, DO-1) |
| | 205 | shared CPU + dGPU power budget (Laptop Studio 1 and 2) | log-only; no Linux path known (`../research/14-laptop-studio-dgpu.md`) |
| | 208, 64 | dGPU current limit (Laptop Studio 2); VPU level request (Laptop Studio 2) | log-only; no Linux path known (`../research/14-laptop-studio-dgpu.md`) |
| `9BBB8910` | 1–5, 20–22, 62 | throttle requests for CPUs, clusters, GPU and the aggregator device | cpufreq or GPU frequency cap (8) |
| | 103–105, 200 | modem sensors, NPU | log-only (no Linux interface known) |
| `110DF2A4` | 1, 2 | thermal policy: hibernate (`O_HOT`) and critical shutdown (`O_CRT`) requests | thermal actions (7) |
| | 3 | minimum-power flags (`O_MINPWR2`) | not needed on Linux (10) |
| `B620798F` | 101–103, 111–116, 121–126, 306, 312 | fan limits | SAM request through the SSAM debug device (9) |
| | 201–203, 301 | fan profile, fan state | the kernel sends them (9.3) |
| | 302–311 | fan, PROCHOT and charge-rate temperatures | off unless the profile opts in (9.4) |
| `CF2F6076` | 61 | PL1 time window, ms | powercap `package-0/long_term` `constraint_0_time_window_us` (3.2) |
| `A79674B4` | 501-512 (509 in the corpus) | virtual-temperature sensor offset, 0.1 K | SAM TMP `0x24` through the SSAM debug device (9.8) |
| other | – | WTMF and so on (L3/L4) | log-only (FR-O6, FR-O7) |

- **Zone lookup:** zones are found by name (`package-0`, `psys`), not by index. A missing zone makes its channels log-only, with one message.
- **PL1 time window:** written only where the blob has `CF2F6076/61` (3.2); otherwise the daemon leaves it alone, and the snapshot restores it.
- **Enable bits:** writes never change `enabled`. A disabled zone is reported. The daemon does not enable it.

### 3.3 PL1/PL2: Linux writes the MSR copy, Windows the MMIO copy [C for Windows, M on one Pro 10, D for Linux]
- **Windows:** the CPU client writes channels 51/52 (and the PL1 time window `CF2F6076/61`) to the MMIO copy of the package power
  limit, MCHBAR + `0x59a0` (PL2 in its upper half; Gemini Lake: `0x70a8`), bits 14:0, with enable and clamp set, in every descriptor
  table of every client build of the corpus. MCHBAR is a fixed address per platform type in the client (`0xfed10000` Ice Lake,
  `0xfedc0000` the others). The MSR copy (`0x610`) has channels of its own (56/57); no blob of the corpus uses them, so Windows does not
  write it (`../research/10-outputs.md` 4.1).
- **Linux:** `intel-rapl:0` is the MSR copy. The MMIO copy is reachable only through `intel-rapl-mmio`, which needs the processor
  thermal PCI device (00:04.0). The driver ships with the kernel, but the device is absent from the PCI bus on the SB3 [M] and on a
  tester's Pro 10 (8086:7d03); the SB3 has no Intel DPTF ACPI devices either. The daemon therefore writes the MSR copy.
- **Effect:** an enabled MMIO copy below the MSR copy caps the package [M on a Pro 10 and a Pro 8, #2383]; that the lower of the two always wins (and not the MMIO copy always) is a general rule [H]. So:
  - lowering PL1/PL2 (the skin controllers, the battery limits) works as on Windows;
  - raising them above the firmware's MMIO value has no effect. On the Pro 10 the firmware sets 28/35 W there, the blob 30/55 W on AC:
    package power stayed at 28 W while the MSR said 30 W;
  - the firmware's MMIO copy is never changed or restored by the daemon, and a write to it by another agent is not seen.
- **On the Pro 8** (tester, i5-1135G7, 18.11.2026, daemon stopped, `surface-power-report` and turbostat) [M]: 00:04.0 and
  `intel-rapl-mmio` absent; firmware MMIO copy PL1 30 W and PL2 60 W, both enabled; firmware MSR copy PL1 35 W, PL2 60 W. Package power
  under three load phases of ~6.5 minutes settled at 29.9-30.0 W (peaks of 32.7 and 39 W only at a load change, inside PL2), not at 35 W:
  the enabled MMIO PL1 caps. This does not show whether the lower copy wins or the MMIO copy always does (the MSR copy was not set below
  30 W), nor that no other limit sits at 30 W. The blob's own value there is PL1 35 W / PL2 60 W (AC, performance), equal to the firmware
  MSR copy, so Windows (MMIO written, MSR left at 35/60 W) would reach 35 W where Linux (MSR written) stays at 30 W [H, not measured].
  The tester needed `lockdown` = none (Secure Boot off) to read `/dev/mem`.
- **On the SB3 15" (18.11.2026, daemon in control, AC, `scripts/mmio-pl-read.py`) [M]: MCHBAR `0xfed10000` (the client's Ice Lake
  constant); MMIO copy PL1 25 W, PL2 61 W, **both enable bits 0**; MSR copy PL1 30 W, PL2 61 W, both enabled. A disabled MMIO copy
  does not cap, so on the SB3 the MSR copy alone is in force, which fits the measured control runs. Windows' write (base `0x18000`)
  would set enable and clamp there. On the Pro 10 the MMIO copy (28/35 W) is enabled (tester, 18.11.2026, below).
- **Windows' own limit [H]:** Windows leaves the MSR copy at the firmware value, so its effective limit is the lower of the blob
  value and the *firmware MSR* value, not the blob value alone. On the Pro 10 the firmware MSR is 35/64 W [M, tester] while the blob
  allows PL1 up to 65 W and PL2 up to 100 W (channel ranges), so the MSR copy may limit under Windows too. What Windows really
  delivers there is not measured. Writing the MMIO copy and leaving the MSR alone would reproduce Windows; it is not the same as
  "no second limit".
- **On the Pro 10** (tester, 18.11.2026, daemon stopped) [M]: MMIO copy PL1 28 W and PL2 35 W, both enabled; MSR copy PL1 35 W, PL2 64 W; 00:04.0 and `intel-rapl-mmio` absent; MCHBAR `0xfedc0000` (the client's constant). Writing the MSR PL1 at 30.0, 29.2, 27.3 and 25.5 W gave package power of 28.0, 28.0, 27.6 and 25.5 W: the lower copy is in force (measuring method not stated). Windows holds about 30 W on AC in Best performance for the first 3.5 minutes (tester's observation), which fits the blob's 30 W written to the MMIO copy [C blob, engine replay with assumed EC inputs]. The Windows write goes to `0x59a0`/`0x59a4` of this device's client (build `463d42bc`) with mask `0x7fff` and enable and clamp set [C, read 23.11.2026].
- **On the Pro 9 Intel** (tester, 18.–21.11.2026, read-only, unchanged over 59 hours) [M]: MMIO copy PL1 30 W and PL2 60 W, both enabled; MSR copy PL1 35 W, PL2 60 W; no 00:04.0, no `intel-rapl-mmio`. The blob's Slider 5 on AC is PL1 35 W / PL2 60 W (engine replay with assumed inputs), so Linux would give 30 W where Windows gives 35 W [H]. After the Pro 8 and Pro 10 this is the third device other than the SB3 whose firmware enables the MMIO copy.
- **Per device:** the firmware state of the copies differs. Read-only visibility, no write:
  - `surface-power-report` (as root) prints the presence of 00:04.0 and of `intel-rapl-mmio`, and both copies (PL1/PL2, enable, clamp, lock);
  - the daemon reads the copies every 60 s (`adapters/rapl_copies.py`), logs a warning when an *enabled* MMIO half is below the
    value computed here, and puts both copies into `status.json` (`rapl_copies`, with `mmio_below_computed` while one limits);
  - `scripts/mmio-pl-read.py` does the same standalone. Without root, `/dev/mem` or the msr module the copies show as "not readable".
- **Not done [D]:** writing the MMIO copy from user space (`/dev/mem`, root, fails under lockdown and with `IO_STRICT_DEVMEM`) or a
  kernel path for it. Open until the SB3 values are known and the question is put to the platform maintainers.

### 3.2 PL1 time window (`CF2F6076/61`) [C for the meaning, H for the unit]
- Windows: the Intel SoC client writes it into bits 23:17 of the PL1 register, rounded up to 4 steps per octave (`../research/12-remaining-outputs.md` 2.1).
- Linux: `OutputStage` target `pl1_window` on `package-0/long_term`'s `constraint_0_time_window_us`. The blob's ms become µs, are clamped to 1.0-112 s and
  rounded up to the same 28 steps (1.0 s, 1.25 s, ... 112 s), so 30 000 ms gives 32 s as on Windows. Written on change only, read back; the read-back
  may be coarser, so a foreign change needs a difference above 15 %. The file is in the snapshot (restored on exit, emergency, yield) and the output is
  log-only where the file is missing. Dry-run computes the value and writes nothing.

### 3.1 Holding PL4 and PL3 while their EC inputs are invalid [D]
PL4 and PL3 are computed from the EC power data on most devices. Without them the blob falls back to values far below the firmware
value (Pro 10: the blob's PL4 fallback is 20 W, the firmware value is 45 W, `peak_power` and MSR `0x601` on UEFI 23.104.143 [M, tester]). While any EC input (`28B7BD85`, the `0x53` and `ARTG` fields) on the path to
PL4 or PL3 is invalid, the output stage keeps the current value: the firmware value before the first write, the last written value
after it. The status file reports such a channel with the target `held at firmware value (EC inputs invalid)` (or `held at last written value ...`); its `value_w` is the value that stays in place, `computed_w` the engine's value, which is not written. psys keeps the Windows behaviour (its fallback is what Windows does without
EC data, `engine.md` 7).

### 3.2 Profile option `pl4_requires_ec` [D]
A profile can set `outputs.pl4_requires_ec = true` (and `outputs.pl4_ec_wait_s`, default 10). Then `--control` starts only if every EC input on the
PL4 path is valid within that time after start (the EC data are asked again every 2 s); otherwise the daemon stays in dry-run and
names the inputs in the log and `mode_reason`. The hold of 3.1 applies in any case. The Pro 10 profile sets it: PL4 also limits the
iGPU there (tester report [H]); its `notes` line says so in the report and the log. See `docs/research/13-pro10-battery-and-timing.md`.

## 4. PL3 through MSR (FR-O2)
- **Encoding:** MSR 0x615 holds PL3 in power units from MSR 0x606 (`1/2^PU W`). The adapter changes only bits 0–12, the field Windows writes (`../research/10-outputs.md` 4.1 [C]); bits 13–14, the time window and the enable bit stay as read.
- **Availability:** this needs `/dev/cpu/0/msr` and kernel lockdown `none`. If either is missing, PL3 is log-only and reported once (AC-O1).
- **Default:** opt-in per device profile (`outputs.pl3 = true`), only where Windows' write of MSR 0x615 is verified in the driver of that device (DV-9, since 17.11.2026). Without it PL3 is log-only. The profiles of the SB3, Pro 8, Pro 9 Intel, Pro 10, Laptop Go 1, Laptop Studio 1/2 and (since 23.11.2026) the Pro 7+, Pro 10 5G, Pro 11 Intel, Laptop 4 Intel, Laptop 5, Laptop 6, Laptop 7 Intel, Laptop 7 5G, Laptop Go 2/3, Go 3 and Go 4 switch it on (`../research/10-outputs.md` 4.1).
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

### 6.1 Other devices (Laptop Studio 1 and 2) [C for the blob and the Windows driver, H for the rest]
The stand-in is generic (first NVIDIA display controller, NVML limits), but everything measured about it is from the SB3's GTX 1660 Ti.
It is therefore off in every other profile.
- **What is not known:** whether the Laptop Studio GPU (Ampere, Ada) takes the clock cap; the MHz-per-watt slope (the 20 MHz/W gain is the SB3's); whether `nvmlDeviceSetPowerManagementLimit` works there (on the SB3 NVML reports a range of 1 to 65 W and still refuses every write [M], so the range proves nothing).
- **What Windows does with 201 there:** on the Laptop Studio driver the numeric dGPU limit is not 201 alone. The GPU power controller of the 2.149 driver treats 201 as an on/off request, takes a shared budget (205) and a current limit (208) through ACPI methods, and sends a power value to the GPU through NVIDIA's private control device only for 202 (constant 100 W in every blob). The older SB3 driver (2.43) sends 201 itself as the power limit. The Laptop Studio 1 driver was not available [C for 2.149 and 2.43, H for the mapping of the five outputs]. So a clock cap that follows 201 on the Laptop Studio is a model of the blob's intent, not a copy of what Windows does (`../research/14-laptop-studio-dgpu.md`).
- **Check before trying:** `python3 -I scripts/dgpu-probe.py --wake` (read-only; `--try-limit` and `--try-cap PCT` write and need root) reports what the driver says and, with `--try-cap`, measures the cap against load, always removing it afterwards.

### 6.2 Observe only (profile option `dgpu_clock_cap_dry_run`)
With `outputs.dgpu_clock_cap_dry_run = true` (and `dgpu_clock_cap` off) the stand-in is created in dry-run mode in every daemon mode: it samples under the same rules as in 6, computes the cap and publishes `would_cap_mhz` in the status file, and sets nothing. The dGPU limit stays a log-only output (it does not count as written, so it does not change readiness). `dgpu_clock_cap = true` or `--dgpu-clock-cap` wins over it. Both options are off in every shipped profile except that the SB3 sets `dgpu_clock_cap`.

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
- **Channel → device** [C for the table, H for the Linux side]. The channel numbers are not `OPTS` indices (an Intel blob has `/62` for a one-entry list). The thermal-policy driver holds a fixed table "name of an `OPTS` entry → channel" and compares each entry of the list with it [C, driver binaries of the Pro 10 and Pro 9 packages]. So `PAGD` and `AGR0` are `/62` by that table, not by elimination. The blob names `O_WTMF_CPU0`–`CPU3` for channels 1–4 are labels only: those channels belong to `CLUS.CPU4`–`CPU7` (6–9 to `CPU0`–`CPU3`). That Windows CPU n is Linux CPU n, and that clusters follow the numeric order of the cpufreq policies, is [H].

  | Channel | Name | Device | Linux target |
  |---|---|---|---|
  | 1–4 | `O_WTMF_CPU0`–`CPU3` | `CLUS.CPU4`–`CPU7` | `scaling_max_freq` of the cpufreq policy that contains Linux CPU 4–7 |
  | 6–9 | (not named in the blobs seen) | `CLUS.CPU0`–`CPU3` | the policy that contains Linux CPU 0–3 |
  | 20, 21, 22 | `O_WTMF_E_CORES_C1`, `P_CORES_C2`, `P_CORES_C3` | `CLS0`–`CLS2` | the 1st, 2nd, 3rd cpufreq policy in numeric order |
  | 5 | `O_WTMF_GFX` | `GPU0` | Intel i915 `gt_max_freq_mhz` (or the per-GT `rps_max_freq_mhz`), xe `freq0/max_freq`, else the devfreq device with `gpu` in its name (Adreno); NVIDIA and AMD: none |
  | 62 | `O_WTMF_PARK` | aggregator (`PAGD`, `AGR0`) | all cpufreq policies, see below |
  | 103–105, 200 | `O_MDM_*`, `O_WTMF_NPU` | modem sensors, NPU | none: log-only |

  A channel whose target does not exist on this machine (no cpufreq, no GPU interface, fewer policies than the channel needs) is log-only. Only then it counts as without a Linux path (`supervisor.md` 2.6).
- **Translation [D]:** cap = request % of `cpuinfo_max_freq` (GPU: of its highest frequency), clamped to [floor, baseline].
  - **Percent of the maximum, not of the span above the minimum.** The documented meaning of the request is "percent of full performance of the device, 100 = no throttling, 0 = lowest level" [C, Microsoft driver documentation of the passive-cooling callback]; how the processor stack turns it into a frequency or performance cap (percent of nominal or of maximum, core parking) is not documented and not visible in the drivers [H]. For `/62` (the aggregator device) the effect of the Windows request may be idle injection or parking rather than a frequency cap; a frequency cap is our stand-in [H]. The 20 % floor is our own safety bound [D]. On a CPU the throughput scales with the frequency, so the cap at *p* % of the maximum gives roughly *p* % of the throughput. The span formula `min + p·(max − min)` would give more than *p* %. The cap is a model of the Windows request, not a copy of it [H].
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

## 9. Fan and charging outputs (`B620798F`) [C for the commands, H for their effect]
The commands, units and send rules come from the analysis of the Windows receiver (`../research/11-fan-charging.md`). Nothing here has run on a device that has such an output. The SB3 blob has none.

### 9.1 Send rules (`adapters/fan.py`)
- **One request per channel,** a write without a response: category FAN `0x05` (TMP `0x03` for 308–311), target ID 1, instance = the fan number (1 for 301 and 302–312), flags 0, payload u8, u16 little endian or float32 little endian (temperatures in 0.1 K).
- **Clamp:** the value is clamped to the blob's output range, then to a hard bound per type (rpm 10000, SPL 100, u8 255, u16 65535).
- **Dead band:** a new request goes out only if the value moved by at least 100 rpm (101–103) or 1 (all others).
- **Rate limit:** at most one request per channel per 5 s. The first cycle after start, after resume and after an emergency sends everything again.
- **Failure:** a failed request leaves the last-sent value unchanged and is tried again after the rate limit. Three failures in a row make the channel log-only, with a note in the log and in the status file.
- **No read-back:** status 0 only shows that the EC acknowledged the frame, not that it knows the command [M, SB3].

### 9.2 Channels and paths
| Channel | Command | Path |
|---|---|---|
| 101–103 | FAN `0x0b`, u16, rpm | `SAM FAN 0x0b (cdev)` |
| 111–113, 121–123 | FAN `0x0c`, `0x12`, float32, SPL | `SAM FAN 0x0c (cdev)`, `SAM FAN 0x12 (cdev)` |
| 114–116, 124–126 | FAN `0x15`, `0x14`, u8, sones | `SAM FAN 0x15 (cdev)`, `SAM FAN 0x14 (cdev)` |
| 306, 312 | FAN `0x1c` float32, `0x16` u8 | `SAM FAN 0x1c (cdev)`, `SAM FAN 0x16 (cdev)` |
| 201 | FAN `0x0e`, profile 1–4 | `kernel (platform profile)` |
| 202, 203 | FAN `0x0e` for fan 2 and 3 | `kernel (platform profile, fan 2)` / `fan 3` where the device profile says the kernel sends them (`outputs.kernel_fan_profiles`, below); otherwise `no Linux path` [C: stock kernels send instance 1 only] |
| 301 | FAN `0x0f`, state | `kernel (suspend)` |
| 302, 304, 305, 307, 308–311 | FAN `0x19`, `0x1a`, `0x1b`, `0x1d`; TMP `0x25`–`0x28`, u16 | `disabled (profile opt-in)` |

The path is a property of this machine. An output counts as written (`supervisor.md` 2.6) only if its path exists: the debug device must be writable (root, module `surface_aggregator_cdev` loaded), or, for 201 (202, 203 where the profile says so) and 301, the kernel path must exist. Otherwise the output is `no Linux path`, and a blob that has it cannot control. `--no-ec` switches the debug device off.

### 9.3 Fan profile and fan state are the kernel's
`surface_platform_profile` (linux-surface kernel) sends FAN `0x0e` at every platform profile change on nodes with `has_fan`, and FAN `0x0f` (standby / normal) in its suspend hooks. These are channels 201 and 301 with the Windows numbers. The daemon does **not** send them. It never sends 301 = 2, because standby while the system runs may stop the fan.
- **Detection:** the kernel path exists if the driver `surface_platform_profile` is bound to its SSAM device (`/sys/bus/surface_aggregator/drivers/surface_platform_profile/<device>`, or a `platform-profile` class node of that name). The property `has_fan` is not visible in sysfs. A hwmon `surface_fan` is only evidence for it, so the path counts as present when the driver is bound, and the fan support is [H].
- **More than one fan:** the instance ID of the request is the fan number. A kernel with the node property `fan_count = N` (`surface_aggregator_registry`, set to 2 for the Laptop Studio 2, MSHW0360) sends the profile to the fans 1..N, so channels 201..200+N are the kernel's. The property is not visible in sysfs. The device profile therefore says it: `outputs.kernel_fan_profiles = N` (1–3, default 1). Only channels up to N get `kernel (platform profile, fan n)`; the others stay `no Linux path`. A profile with N = 2 on a kernel without the property is wrong: fan 2 gets nothing [H]. The daemon does not send the profile to fan 2 itself, not even as a fallback (see `research/11-fan-charging.md` 9). Shipped: `surface-laptop-studio-2.toml` (N = 2, untested on a device). The fan state 301 stays with fan 1.
- **Log:** when the blob's 201 differs from the value the kernel sends for the current platform profile (low-power 1, balanced 2, balanced-performance 3, performance 4), the daemon logs it once per pair. The platform profile is the one the slider input uses (`inputs.md` 3).

### 9.4 Temperature thresholds are off
Channels 302–311 (fan-on, ramp, maximum and critical temperature, PROCHOT, charge-rate temperatures) exist only on the arm64 Laptop 13" and Pro 12". The frames are built, but the stage sends them only if the device profile sets `outputs.fan_thresholds = true`. No shipped profile does. Disabled means log-only, the status shows `disabled (profile opt-in)`, and the output does not count as written, so such a blob stays in dry-run. A wrong value can hurt the hardware or the battery. These channels have no neutral value: once sent, only a reboot undoes them [H]. The daemon logs this when it sends one.

### 9.5 Restore without read-back
- **Neutral frames:** 0 for the rpm, SPL and sones channels (0 means "no floor" or "no limit" [H]).
- **Record first:** before the first send of a channel the neutral frame goes into the snapshot (`supervisor.md` 3). If it cannot be saved, nothing is sent.
- **When:** on exit, on an unhandled exception, on sleep, in emergency and when yielding, by `Snapshot.restore()`. After a crash the reset tool (`surface-power-reset`) or a successor daemon sends the recorded frames. A frame that could not be sent stays in the snapshot.
- **Last resort:** a reboot [H: the EC returns to its defaults at boot].

### 9.6 Dry-run and status
Dry-run computes the frames and sends nothing. The status file shows `would send SAM FAN 0x12 (cdev)`, `kernel (platform profile)`, `kernel (suspend)`, `disabled (profile opt-in)`, `needs root and surface_aggregator_cdev` (SAM machine, debug device not usable in this run) or `no Linux path` (ACPI-EC machine), and the list `fan` with the sent value and the frame (`would`, hex) per channel. `surface-power-report` prints the same kinds. With `--with-ec` it also sends the read-only request FAN `0x01` (instance 1, rpm) once, and FAN `0x13` (instance 0, system SPL) only if a blob output needs it (the Pro 9 does not answer it).

### 9.7 Test tool
`scripts/fan-test.py` runs the safe procedure of `../research/11-fan-charging.md` 8 for one channel (or only `--probe` on any Surface). It uses the same frames as the adapter.

### 9.8 Sensor offsets (`A79674B4/5xx`) [C for the command, H for the effect]
Sent by the same stage as the fan outputs (9.1): TMP `0x24`, instance = sensor number (channel - 500), payload = sensor number (u8) + float32 K (value x 0.1), flags 0.
Hard bound +-5 K [D] (the blob has no range for it; the corpus has -2 K to +2 K), neutral frame = offset 0 (recorded in the snapshot as `sam:A79674B4/509`), status target `SAM TMP 0x24 (cdev)`.
In the Pro 10 blob it is constantly 0; other blobs use -2.0 to +1.5 K. Effect on the EC not measured with this frame.

**Open contradiction (23.11.2026, `plan.md` H6):** the frame above (the sensor number in the SSAM instance field and again in the payload) is what the Windows SMF client builds, read in two builds: the Pro 10/Laptop Studio 2/Laptop 6 client (sha256 `0f18750c…`) and the Laptop Go 1 client (sha256 `68f6cffd…`) [C]. A tester on a Laptop Go 1 reports [M, one device, one tester] that this frame has no effect on `I_VTS1`, while the instance field 0 with the sensor number only in the payload shifts it by the float on the next 5 s EC tick. A write with flags 0 is acknowledged on the link whether or not the EC acts on it (DV-11), so "status 0" proves nothing. Whether Windows' own write has no effect on that EC, whether the Windows serial-hub layer changes the instance, or whether the EC firmware differs is not known. The daemon keeps the Windows frame until this is settled; the offset output is therefore sent on every device but its effect is measured on none.

### 9.9 Battery mode (`0B7B5350/70`) [C for the request, H for its meaning] (`adapters/prochot.py`)
Value 1 sends one SAM request: category TMP `0x03`, target ID 1, instance 0, flags 0, command `0x1b`, no payload; the Windows Intel client does the same and sends nothing for 0.
Windows' ACPI-EC machines call the method `DPHT` instead, which the DSDT labels "de-assert PROCHOT"; TMP `0x1b` is probably its SAM form [H].
Linux sends it once per episode of value 1 (first cycle, after a value 0, after a resume), rate limit and failure rule as 9.1, nothing to restore (no snapshot entry),
dry-run by default, status target `SAM TMP 0x1b (cdev)`. No path (log-only, blocks readiness): no SSAM debug device (ACPI-EC machines), AMD or Qualcomm CPU.

## 10. Outputs that need no Linux path (`adapters/notneeded.py`)
An output is listed only with [C] evidence that no Windows receiver does anything with it that Linux could do. The readiness rule counts it as covered
(`supervisor.md` 2.6) but the status file, `surface-power-report`, the daemon log and the device pages say "not needed on Linux: <reason>"; the status file
target is that text. Nothing is ever dropped silently.

| Output | Reason | Scope |
|---|---|---|
| `110DF2A4/3` `O_MINPWR2` | no client registers it (thermal policy takes 1 and 2, its policy structure has no field for it); only the power tracker records it (telemetry) [C] | all platforms |

Details and checked drivers: `../research/12-remaining-outputs.md`. New entries need the same evidence.
