# Laptop Studio 1 and 2: the dGPU outputs and why the stand-in is off there

17.11.2026. The question: why is the dGPU power control of the Laptop Studio 1 and 2 not addressed (the pages list `0B7B5350/64`, `/201`,
`/205`, `/208`, `349D0E39/1` and `/4` as not written) - only because the stand-in is on only in the SB3 profile? Markers as in
`README.md`. Nothing here ran on a Laptop Studio. Evidence: the dGPU blobs of both devices (engine replay with typical AC and battery
states), the GPU power controller driver of the SB3 (2.43) and of the Laptop Studio 2 package (2.149), the VPU client and the IHV CPU
client of the Laptop Studio 2 package (the CPU client is byte-identical in the Laptop Studio 1 package), the NVML answer of the SB3
(17.11.2026, `scripts/dgpu-probe.py`). The Laptop Studio 1 GPU controller driver is listed in its package but was not extracted; it is
not analysed. Output 202 (fan 2 profile, `B620798F`) is not part of this note; `0B7B5350/202` is a different output (below).

## 1. Short answer
- **Why only the SB3:** the stand-in code is generic, the switch is the profile option `dgpu_clock_cap`, and everything it rests on (the
  Windows hold of 49.64 W, the clock-power curve, the 20 MHz/W gain, the 300 MHz floor) is measured on the SB3's GTX 1660 Ti only [M].
  The Laptop Studio 1 profile never had the option; the Laptop Studio 2 had no profile before 17.11.2026.
- **But the profile switch is not the real gap.** On the Laptop Studio the Windows driver does not use 201 as the power limit it sends
  to the GPU (2). A cap that follows 201 there models the blob's intent; it does not copy Windows [C for the driver, H for the mapping].
- **And 201 alone would not make a device controllable:** besides 201 the Laptop Studio 2 blob has `64`, `205`, `208`, `349D0E39/1` and
  `/4` without a Linux path, the Laptop Studio 1 blob `71` and `205` (3).

## 2. The outputs [C = blob and driver code, H = reading of it]
Units are those of the blob (mW unless noted). Replay: `engine` with the dGPU blob, inputs set as in the field tests.

| Output | Meaning | Value and what drives it | Windows receiver | Linux |
|---|---|---|---|---|
| `0B7B5350/201` | dGPU power target, range LS1 15-50 W, LS2 15-80 W, default 35 W | LS2: minimum of three lookups: slider state (60, 40, 70, 50, 80, 60, 30, 80, 60, 60 W for states 1-10), posture (60 W in posture 3, else 80 W), Modern Standby phase (100 W in phase 4). AC and battery give the same value because the slider already contains it. Replay 30-80 W [C]. LS1: minimum of slider table (25-50 W), posture table (45-50 W), adapter rating tier and a staircase of the battery peak power (17, 34 or 50 W). Replay: 15 W on battery, 34 W on AC with the Surface adapter, 25 W in slider state 7, 50 W with a peak power of 100 W or more [C] | GPU power controller (2). 2.43 (SB3): 201 is sent to the GPU as the power limit. 2.149 (LS2): 201 is an on/off request [C]; mapping by the order of the output table [H] | clock-cap stand-in exists (outputs 6); off, never measured on these GPUs |
| `0B7B5350/202` | GPU power value, 0-200 W, default 100 W | never computed: constant default in all four blobs [C] | 2.149: the only output sent to the GPU's private power control [C, H for the mapping] | log-only; a constant 100 W is 100 % of the GPU's range, no limit [H] |
| `0B7B5350/205` | shared CPU + dGPU power budget, LS1 10-105 W (default 95), LS2 10-120 W (default 95) | minimum of skin-temperature controllers (sensors 509 and 512, shifted by posture, external display, camera, dock, Xbox controller), a battery-discharge controller and, on LS2, a conditional table of battery current and input power. Replay: LS1 55-75 W by slider state, LS2 78-80 W [C]. The same controllers set the CPU PL1 (51) [C, `04`] | GPU power controller 2.149: value minus 100 W, split in a CPU part and a GPU part, sent to the platform as ACPI method arguments in 1/8 W [C]. Dynamic-Boost-like [H] | none known (3) |
| `0B7B5350/208` (LS2) | current limit, mA, 1-9 A, default 9 A | power headroom (battery peak power, rest-of-platform power, display) divided by the battery open-circuit voltage, times 1000; 4 A if an input is invalid. Replay 3.7 A (battery) to 5.5 A (AC) [C] | GPU power controller 2.149: clamped to 4-9 A, sent as an ACPI method argument [C]; meaning "current" is [H] | none known (3) |
| `87008365/210` | not in the blobs | - | fifth output of the 2.149 driver, on the composite interface [C] | - |
| `0B7B5350/64` (LS2) | 0-100, 0 below 50 degC | clamp(f x `1FB53D4C/21`): f = 0 below 50 degC, 0.5 at 50-51 degC, 1 from 52 degC of sensor 509 (quantised in 2 K steps, 1 K in the iGPU blob). `/21` has no Linux source: the blob then gives the constant 1 [C] | VPU client (user-mode DLL): loads a vendor library named by the device registry, which offers set power level, get power level, get power and get chip temperature [C]. `/64` as the level request and `/21` as the level read-back [H] | none known (3) |
| `349D0E39/1` (LS2) | 8 bit, 255 | copy of the input `1FB53D4C/10` ("CPU maximum frequency 2"); 255 while that input is invalid, which is the Linux case [C] | IHV CPU client (same file in LS1 and LS2): channel 1 is bits 7:0 of one MMIO register (offset `0x5B10`) [C] | none known (3) |
| `349D0E39/4` (LS2) | 0 or 1 | 1 while the dGPU draws more than 25 W and `1FB53D4C/10` is 1-254, else 0. Linux: always 0 [C] | IHV CPU client: one bit of an MMIO register (bit 31 of `0x5B10`, or bit 16 of `0x59A0` / `0x70A8` in other CPU tables; the table is chosen by CPU generation, not decoded), inverted on older generations [C] | none known (3) |

### 2.1 The GPU power controller driver [C]
- One driver for the SB3, Laptop Studio 1 and 2: its device list holds Turing, Ampere and Ada mobile GPU IDs (SB3: 2.43; Laptop Studio
  2: 2.149, which is larger and has five outputs instead of one).
- **GPU side:** it opens NVIDIA's private control device from kernel mode (not NVML, not NVAPI: those are user-mode libraries) and reads
  power, temperature and the GPU's power-limit range through it. The write of a power value goes as a percentage of that range:
  (value - minimum) / (maximum - minimum) x 100, clamped. If the GPU interface is missing it falls back to a thermal-cooling callback.
- **Platform side (2.149 only):** ACPI methods of the Surface GPU device, called with 4-byte arguments (enable, mode, values in 1/8 W,
  the current limit). The budget goes through a split: `delta = budget - base + other`; a negative delta is a CPU part, a positive one a
  GPU part, limited to a maximum. Which ACPI method does what is in the DSDT of the machine, which this note does not use.
- The SB3 package has the older single-output version: 201 is written as the power value. That is why 49.64 W was measured there.

## 3. Linux path for each output
| Output | What exists | What is missing |
|---|---|---|
| 201 | the clock-cap stand-in (`adapters/gpu.py`): generic (first NVIDIA display controller, NVML), shared client probe, dry-run, release on every stop | measurement on an Ampere and an Ada GPU: does the lock-clock call work, what is the MHz-per-watt slope, what clock floor; a Windows measurement of the GPU power on the Laptop Studio at known slider states, to see whether Windows follows 201 at all. On the SB3, NVML reports a power-limit range of 1-65 W and still refuses every write [M, 17.11.2026], so a reported range is no evidence that a limit can be set |
| 205, 208 | the CPU share of the same controllers already reaches PL1 and psys through 51-55 | the GPU share: Windows hands it to the platform by ACPI methods. A user-space ACPI call needs an out-of-tree module and the method list of the machine; `nvidia-powerd` (Dynamic Boost) needs NVIDIA's own platform interface, and the SB3 driver reports it as unsupported [M]; it is not known for the Laptop Studio [H]. Check on a Laptop Studio: `nvidia-smi -q` (boost lines) and the ACPI device list for NVIDIA's platform device |
| 64 | nothing | a VPU power interface: `ivpu` covers the newer Intel VPU generations only; the Laptop Studio 2's is not known to have a driver [H]. Check: `ls /sys/class/accel`, `lspci` |
| 349D0E39/1, /4 | nothing | what the register fields do (`/4` is a dGPU-activity flag, `/1` a pass-through of an unknown input); writing unknown MMIO bits is not acceptable. Check, read-only: the value of MSR `0x610` bit 16 and of MMIO `0x5B10` under Linux while the dGPU idles and loads |

None of these is classified "not needed": the receivers write registers or ACPI arguments, and their effect is not known to be nil.
A Laptop Studio therefore stays in dry-run until each output has a path or an owner decision (`supervisor.md` 2.6).

## 4. What it takes to enable the stand-in on the Laptop Studio safely without a test device
1. Done here: the profile options `dgpu_clock_cap` and `dgpu_clock_cap_dry_run` (`outputs.md` 6.1, 6.2), both off in the Laptop Studio
   profiles; `scripts/dgpu-probe.py` (read-only by default).
2. Owner step 1: `python3 -I scripts/dgpu-probe.py --wake`; then as root `--try-limit` (writes the current value back) and `--try-cap 70`
   with a GPU load running; send the output.
3. Owner step 2: `dgpu_clock_cap_dry_run = true`; the status file shows `would_cap_mhz` against the measured power; nothing is set.
4. Only then `dgpu_clock_cap = true` (or `--dgpu-clock-cap`), still with `--control` and `--allow-partial-outputs` because 205 and the
   other outputs are open. The cap is removed on stop, emergency and yield; a failed NVML call switches the stand-in off.
5. Windows data that would settle the meaning: dGPU power under a fixed GPU load at slider states 2, 5 and 7 on AC and battery, with the
   blob's 201 values (LS2: 40, 80, 30 W) beside it.

## 5. Changes needed in `scripts/device-docs.py` (not made here)
- `OUTPUTS[(LIMITS, 64)]`: meaning "VPU level request, 0-100 (Laptop Studio 2)", Linux "-", status `log`.
- `OUTPUTS[(LIMITS, 201)]`: Linux text "clock-cap stand-in (DO-1, profile option, off; observe-only option)".
- `OUTPUTS[(LIMITS, 205)]`: "shared CPU + dGPU power budget (not written)", Linux "- (ACPI methods on Windows)".
- `OUTPUTS[(LIMITS, 208)]`: "dGPU current limit, mA (Laptop Studio 2)", Linux "-".
- `OUTPUTS[("349D0E39", 1)]`: "CPU register field, 8 bit (copy of `1FB53D4C/10`; 255 without it)"; `[("349D0E39", 4)]`: "CPU register flag (1 while the dGPU draws more than 25 W)"; `INTERFACES["349D0E39"]`: "SoC register fields (IHV CPU client)".
- `INPUTS[(STATE, 10)]` and `[(STATE, 21)]`: keep "none", meaning "feeds `349D0E39/1` and `/4`" and "feeds `0B7B5350/64` (VPU level, H)".
- The "not analysed" labels of 205 ("GPU power controller limit") can go; the Laptop Studio pages then say what is open and why.
