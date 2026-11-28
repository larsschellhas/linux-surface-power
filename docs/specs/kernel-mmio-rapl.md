# Spec: kernel module `surface_rapl_mmio`

Version 0.5, 28.11.2026 (0.5: Pro 8 and Pro 10 in the table; 0.2 revised after the independent review, 0.3 after test steps 0–3, 0.4 after the tests on the Surface Book 3 15" of
23.–24.11.2026 and their independent re-check against the raw data). Implements KM-1 … KM-13 of `../requirements/kernel-modules.md`; the
daemon side (KM-9) is specified in `kernel-mmio-daemon.md`; evidence in `../research/15-mmio-evidence.md`. Markers [C] source or driver,
[M] measured, [H] hypothesis, [D] design decision. Findings of the first review are marked (R).
Prototype: `kernel/surface-rapl-mmio/surface_rapl_mmio.c` (compiles with `W=1` against 7.3.4-2-surface). **Tested on the Surface Book 3 15"
only** (23.–24.11.2026, read-only and with `allow_write=1`, section 8) [M]; not loaded on any other device.

## 1. Purpose
Provide the powercap zone `intel-rapl-mmio` (package domain, PL1 = `constraint_0` long-term, PL2 = `constraint_1` short-term, with time windows) on Surface devices that
have the MMIO copy of the package power limits but no visible processor thermal device 00:04.0. User space then writes the register that Windows writes (`research/15` 1).

## 2. Interface
- Module `surface_rapl_mmio`, GPL-2.0-only, imports the namespace `INTEL_RAPL`, depends on `intel_rapl_common`.
- Parameters (both read-only after load): `allow_write` (bool, default 0), `unsafe_platform` (string, developers only: skips the DMI table; the MCHBAR check still applies; its use taints the kernel).
  User space learns whether writes are possible from `/sys/module/surface_rapl_mmio/parameters/allow_write` (`Y` or `N`) [M].
- sysfs: `/sys/class/powercap/intel-rapl-mmio/intel-rapl-mmio:0/` with `constraint_0_*` (PL1) and `constraint_1_*` (PL2). **No** `constraint_2` (PL4), no DRAM or other domain [D] (KM-5) [M].
- The zone's files stay 0644 when `allow_write=0`. Every refused write returns an error inside the module, which the powercap core turns into `ENODATA` for a
  constraint file and `ENOSYS` for `enabled`, whatever the cause [C, `powercap_sys.c` store functions] (R); `ENODATA` measured [M].
- Reading `enabled` returns the **PL1 enable bit only** (and 0 when PL1 is locked) [C, `get_domain_enable`]. Writing it is not one bit (5.4).
- Log lines carry the prefix `surface_rapl_mmio:`; refusals are logged (rate-limited; the read-only refusal once).
- Nothing else: no ioctl, no debugfs, no dependence on the daemon. (The test build `-DSRM_TEST_BITS` used for the enable/clamp matrix has a debugfs switch for single
  bits; it is a local test patch, not part of the module.)

## 3. Platform types
`icelake` (MCHBAR `0xfed10000`), `tigerlake`, `alderlake`, `raptorlake`, `meteorlake`, `lunarlake` (`0xfedc0000`): the addresses the Windows client assumes (`research/15` 2). A DMI entry names the
type of its device. Types 3 (Gemini Lake) and the arm64 types do not exist here.

## 4. Discovery
1. DMI match (opt-in table, section 6) or `unsafe_platform`. No match: `-ENODEV`, debug message only (KM-2, A3).
2. If PCI device 00:04.0 exists: log once, `-ENODEV` (KM-3).
3. MCHBAR from the host bridge 00:00.0 (vendor Intel), config `0x48` and `0x4c`; bit 0 must be set; address = bits 41:15 [H: width; the SB3 and the Pro 10 values are consistent with it]. **The address must equal the platform type's constant**, else
   `-ENODEV` with a log line (the module never maps an address it has no evidence for) (KM-7). This check separates Ice Lake (`0xfed10000`) from the others (`0xfedc0000`) only (R); the names `alderlake` … `lunarlake` are labels, not detected. It is a sanity check, not an identification; the DMI entry identifies the device.
4. Map one page, `MCHBAR + 0x5000 … +0x6000`, with `ioremap()` (no resource claim: the range is reserved by `PNP0C02` [M, tester]). Registers used: `0x5938` unit, `0x593c` energy status, `0x58f0` performance status, `0x5930` info, `0x59a0` limit.
5. The power unit register `0x5938` must read a non-zero unit that is not all ones (else `-ENODEV`; the core ignores a failed unit read and later divides by it) (R). The limit register is read and recorded (5.5). `powercap_register_control_type("intel-rapl-mmio")`; on a name conflict the module fails with the error and logs it (another driver owns the name). `rapl_add_package(0, …, false)`; the package has no lead CPU, so the core's MSR side effect of a constraint write (`package_power_limit_irq_save`) does nothing [C]. The core needs a non-zero MMIO energy counter at `0x593c`, else `rapl_add_package` fails with `-ENODEV` (R).
   **SB3 [M]:** the counter runs and equals the MSR zone's counter (+24.18 J in the same 5 s in both zones); `rapl_add_package` succeeds. A module that is not signed with a trusted key is refused when Secure Boot is on (`module.sig_enforce` = Y, lockdown `none`); the SB3's own DKMS key signs the test copy (KM-8).

## 5. Register access
### 5.1 Layout [C kernel source, same as Windows]
`0x59a0`: PL1 bits 14:0, enable 15, clamp 16, window 23:17 (kernel time-window format), lock 31; PL2 the same at +32. Power unit from `0x5938` (read through the core's `check_unit`).
### 5.2 Domains and limits [D]
Package domain only, `limits = {PL2}` (so PL1 and PL2). No PL4 constraint: Windows writes PL4 through MSR `0x601` and psys through `0x65c`; a second PL4 path would be a deviation without evidence (KM-5). The register `0x59b0` (MMIO PL4, 87.625 W on the SB3 [M]) is not touched.
### 5.3 Reads and writes
- `read_raw`: 64-bit read of a register inside the mapped page, masked. Anything outside: `-EINVAL`.
- `write_raw`: `-EPERM` unless `allow_write=1` and the module is not being removed; only the limit register `0x59a0`; the mask must lie inside the writable groups (PL1 field 14:0, enable/clamp 16:15, PL1 window 23:17, PL2 field 46:32, enable/clamp 48:47), not the PL2 window 55:49, never bit 31 or 63; a register found locked: `-EPERM`; a PL1 or PL2 below 3 W or an unreadable power unit: `-EINVAL`. The read-modify-write is serialised by a mutex (the core takes only the shared CPU hotplug lock and the powercap store paths take none (R)).
- Access width: 64-bit read-modify-write [H: Windows uses a 32-bit access at `0x59a4` for PL2 in most tables; the kernel's own zone uses 64-bit accesses and works in the field [C]; on the SB3 64-bit accesses work [M]].
- With `allow_write=1` the module taints the kernel (`TAINT_CPU_OUT_OF_SPEC`) once (KM-4).
- **Measured on the SB3 (copy disabled) [M]:** a write to a constraint file changes only its field (PL1 14:0, PL2 46:32, PL1 window 23:17; checked bit by bit after every write); the PL2 window, PL1 0 / 2 / 2.875 W and PL2 2 W are refused with `ENODATA` and no change; 3 W is accepted; two concurrent writers (400 writes each on PL1 and PL2 in 8 s) leave every other bit untouched and the last values in place (no control run without the mutex).
- **Time window:** the core encodes the window itself (`rapl_default_compute_time_window`: value / 976 µs, exponent and fraction **rounded down**) [C]. Measured: 1 s → `0x0a`, 3 s → `0x4b`, 8 s → `0x0d`, 28 s → `0x6e` (= firmware value), 32 s → `0x0f`, 56 s → `0x6f`; the read-back is 0.9994 of the written value and writes back to the same code [M]. Computed with the core's formula: each of the 28 window steps of the Windows client written as its nominal µs value gives the intended code, read-back ratio 0.999424 [C, computation]. This assumes the time unit 1/1024 s (`0x5938` bits 19:16 = 10 on the SB3 [M]).
### 5.4 Enable and clamp [answered on the SB3 (KM-A4); other CPUs open]
- **Windows** ORs enable and clamp (`0x18000`) into **every** PL1 and PL2 write [C], so after its first writes all four bits are set.
- **Kernel** [C, `intel_rapl_common.c`]: a constraint write changes the limit field only. The zone attribute `enabled` is not one bit: `set_domain_enable(1)` writes PL1 enable, reads it back, then `rapl_default_set_floor_freq()` writes PL1 clamp, PL2 enable and PL2 clamp (four read-modify-writes; PL4 is not a limit of this zone); `enabled=0` clears the same four bits. `set_domain_enable` returns success also when the read-back of PL1 enable differs (debug message only), and the three writes of `set_floor_freq` are not checked, so a successful `enabled=1` proves only that the write was attempted; through the zone only PL1 enable can be read back. There is no way to write one of the four bits alone.
- **Firmware state on the SB3** [M, register `0x000001e800dd00c8`]: PL1 25 W, enable 0, **clamp 1**; PL2 61 W, enable 0, clamp 0; both lock bits 0. `enabled=1` set exactly the four bits (`0x000001e800dc0040` → `0x000181e800dd8040`); `enabled=0` clears all four, **including the PL1 clamp the firmware had set**, so it does not restore the firmware state (only the restore at unload does, 5.5).
- **Enable/clamp matrix on the SB3** [M, 24.11.2026; test build with single-bit writes; platform profile and EPP performance, `scaling_min_freq` 400 MHz, power-profiles-daemon and the daemon stopped, 8 busy loops, limit 8 W, 45 s per cell, mean of the last 25 s; without any bit the package is held at 14.9 W by the platform limit psys 25 W]:

  | Limit 8 W on | E0 C0 | E0 C1 | E1 C0 | E1 C1 |
  |---|---|---|---|---|
  | PL1 | 14.9 W (control) | 14.9 W (the firmware's state) | **8.0 W**, 1031 MHz, from the first sample | 8.1 W |
  | PL2 (PL1 off) | – | 13.4 W; the first 10 s 25–28 W at psys 40 W | **7.9 W** | 7.9 W |
  | PL1, `scaling_min_freq` 2.0 GHz | 13.3 W, 1927 MHz | – | **7.9 W, 999 MHz** | 7.8 W |

  **Enable alone makes a limit effective, for PL1 and PL2 independently; clamp alone does nothing; the clamp was not needed even when the OS asked for at least 2.0 GHz.**
  Limits of this result: only the Ice Lake SB3, one run per cell; whether the 2.0 GHz minimum reached `MSR_HWP_REQUEST` was not read (the kernel path writes it [C, `intel_pstate_hwp_set`]) [H]; the control cell itself ran below 2.0 GHz under the psys limit, so the test shows that the MMIO limit needs no clamp here, not that the clamp means nothing on other CPUs. The first run of the matrix (platform profile low-power) is not evidence.
  The kernel's own comment on the clamp: "always enable clamp such that p-state can go below OS requested range" [C, `rapl_default_set_floor_freq`].
- **Decision [D]:** the module never changes enable or clamp on its own. Whoever wants the copy in force writes `enabled=1`; that is Windows' end state (all four bits) whatever the clamp does on a given CPU, so parity does not depend on the open clamp question. On a device whose firmware already enables the copy (Pro 8, 9, 10 [M]) nobody needs to write `enabled` at all. The daemon's rules are in `kernel-mmio-daemon.md` 4.
### 5.5 Restore (KM-6, KM-11)
At load the module records the limit register (all 64 bits). At unload it first sets a flag that makes `write_raw` refuse (the core's package removal clears enable and clamp of every limit and would switch the firmware's limit off on a device with an enabled copy [C, `rapl_remove_package_cpuslocked`] (R)), then removes the zone, then, with `allow_write=1`, writes the recorded value back if the register is not locked (the log line "limit register restored" appears also when nothing had changed).
**SB3 [M]:** after every test the register was bit-identical to the value at load (`0x000001e800dd00c8`), also after PL1, PL2, the window and the four enable/clamp bits had been changed and the firmware's PL1 clamp had been lost through `enabled=0` (steps 4e, t234, t5b, t6, t7). On the SB3 the enable bits are 0 at load, so a failure of the refusal at removal could not show; KM-A9 needs a device with an enabled copy.
**Cold boot [M, one cycle]:** with a changed register (`0x00000190001b00a0`) and the module left loaded (no restore), a complete shutdown and power-on gave back the firmware value: the firmware re-initialises the copy at a cold boot. **Warm restart [M, one cycle]:** a warm restart (`systemctl reboot`, kernel 7.3.6-1-surface, 24.11.2026) after the DS-5 state `0x000001e800dc00c8` with the module left loaded gave back the firmware value `0x000001e800dd00c8`, PL1 clamp 1 included [M, one cycle].
### 5.6 Bounds [D, measured]
- **Floor:** a PL1 or PL2 below 3 W is refused (smallest minimum of the corpus' blobs) [M].
- **No upper bound in the module:** 200 W is accepted [M]. The RAPL core converts the µW value to units, shifts and **masks it to the field width before `write_raw` is called** (`rapl_write_data_raw`), and `set_power_limit` has no upper check [C]: a value from 4096 W up wraps silently (5 000 000 000 µW became 7232 units = 904 W [M]). The module only sees the wrapped value and cannot detect the wrap; values from 4096 W to below 4099 W wrap below the floor and are refused, 4099 W wraps to exactly 3 W and is accepted [C]. The same holds for every RAPL zone, `intel-rapl:0` included.
- **No bound from the device:** the info register `0x5930` reads thermal spec 15 W, min 0, max 0 on the SB3 [M], so the zone shows `constraint_0_max_power_uw` = 15 000 000 and `constraint_1_max_power_uw` = 0; neither is a usable bound.
- Bounding is user space's job: the daemon applies the blob's ranges and its hard limits (PL1 ≤ 120 W, PL2 ≤ 200 W, `outputs.md` 2; `kernel-mmio-daemon.md` 6). The firmware's own limits and the MSR copy remain a ceiling by the lower-copy rule (DV-8). Whether the upstream form should refuse values above a bound is a question for linux-pm (the kernel's own zones have none [C]).
### 5.7 Suspend and resume [M on the SB3, s2idle]
- The RAPL core saves the limits at `PM_SUSPEND_PREPARE` and writes **only the limit fields** back at `PM_POST_SUSPEND`, and only those it read as non-zero [C, `power_limit_state_save/restore`]; enable and clamp bits are never touched by this path. In read-only mode the write-back is refused and logged once (the log line names the resume as a possible cause).
- Three valid s2idle cycles through `systemctl suspend` with an RTC alarm (about 37 s asleep): read-only; `allow_write=1` with PL1 20 W and the copy disabled; `allow_write=1` with PL1 10 W and `enabled=1`. **The register was unchanged after each cycle**, and with the copy enabled the 10 W limit held after the resume (9.6 W against 9.9 W before) [M]. A direct `rtcwake -m freeze` is not a valid test on this machine (the nvidia driver refuses a suspend that bypasses systemd [M]).
- **Burst after the resume [M]:** with the copy enabled, the first ~10 s of load after the resume ran at 24.6–28.9 W (held by psys PL2 40 W), then the 10 W PL1 held; before the suspend, with the limit switched on just before the load, there was no burst. **Explanation [H, quantitative]:** PL1 limits the average over its time window (28 s); after ~46 s near 0 W (pause, suspend, wait) a running average with τ ≈ 28 s starts at about 2.7 W and reaches 10 W after 28 s · ln(24.3/17) ≈ 10 s at ~27 W, which matches the measured 10–11 s. This is the normal turbo budget after an idle phase, not a resume effect; Windows, whose copy is always enabled, has the same. Open: why no burst followed the switch-on before the suspend (the average may not start empty when the enable bit goes from 0 to 1 [H]). Test without suspend: enable, 60 s idle, then load.
- Not tested: S3, hibernation.

## 6. Opt-in per device
### 6.1 Evidence required for a table entry (all of it, per device; the SB3 entry is the one exception, it is the test bed and gets items 3 and 5 from the test plan before anything else)
1. Windows target for the device's client build: channels 51/52/61 → MMIO `0x59a0`/`0x59a4` (`research/15` 1) [C].
2. Platform type of the device from its integration driver, and the MCHBAR constant of that type [C].
3. Read-only load on the device: MCHBAR from config equals the constant; zone values equal the raw registers read through `/dev/mem` (script `scripts/mmio-pl-read.py`) [M, tester or maintainer].
4. Firmware state of the copy reported (enabled, clamp and lock bits).
5. For the first write test on a device: the effective limit measured against the written value, with the original value restored [M].
### 6.2 Table status (28.11.2026)
| Device | Type | 1 | 2 | 3 | 4 | 5 | State |
|---|---|---|---|---|---|---|---|
| Surface Book 3 15" (`Surface_Book_3_1899`) | icelake | ✓ | ✓ | ✓ [M 23.11.]: MCHBAR `0xfed10000` = constant, zone values equal the register, energy counter runs | enable bits 0, PL1 clamp 1, PL2 clamp 0, lock bits 0 [M] | ✓ [M 23.–24.11.]: 8 W in force with `enabled=1` (8.0 W against 13–15 W), restore bit-identical | in the table as test bed (the SKU matches this machine) |
| Surface Book 3 13" | icelake | ✓ | ✓ | – | – | – | SKU not confirmed (`plan.md` C5) |
| Pro 10 (`Surface_Pro_10_for_Business_2079`) | meteorlake | ✓ | ✓ | ✓ [M, tester, 25.11. and 27.11., issue #3]: MCHBAR check passed (`0xfedc0000`), zone equals the register, energy counter counts, unload bit-identical, also with `allow_write=1` and no write | ✓ enabled, PL1 clamp 1, lock bits 0 (register `0x0000811800dd80e0`) | ✓ [M, tester, 27.11.]: MMIO PL1 28 → 23 W, package 28.0 → 23.0 W, only the PL1 field changed, restore bit-identical, 27.9 W again | **in the table since 28.11.2026** (maintainer) |
| Pro 8 (`Surface_Pro_8_1983`) | tigerlake | ✓ | ✓ (re-checked in the drivers 28.11., `research/15` 2) | ✓ [M, tester, 27.11., issue #4]: as the Pro 10 | ✓ enabled 30/60 W, PL1 clamp 1, PL2 clamp 0, lock bits 0 (register `0x000081e000dd80f0`) | ✓ [M, tester, 27.11.]: 30 → 24 W, package 29.94 → 23.94 W, only the PL1 field changed, restore bit-identical, 29.78 W again. The result names no repository commit | **in the table since 28.11.2026** (maintainer); Wi-Fi model only, the LTE model is not reported |
| Pro 7+ | tigerlake | ✓ | ✓ | – (skipped by the kit, 25.11.) | ✓ enabled, 30/60 W [M, tester, issue #2] | – | candidate |
| Pro 9 Intel | alderlake | ✓ | ✓ | – (headers missing on the tester's image, 28.11., issue #7) | ✓ enabled 30/60 W (register `0x000081e000dd80f0`) [M] | – | candidate |
| others | see `research/15` 2 | ✓ | ✓ | – | – | – | no report yet |
For the Pro 8 and Pro 10 entries the code version: taken as `0ac2dd5`, the commit that was current when the results were posted (maintainer's decision, 28.11.2026); the results themselves say "local changes" (Pro 10) and name no commit (Pro 8). Items 1 and 2 were re-checked in both packages' drivers on 28.11.2026. A table entry only lets the module load without `unsafe_platform`: it stays read-only without `allow_write=1` and has no alias.
### 6.3 Developer override
`unsafe_platform=<type>` lets a tester load the module on a device without an entry, to collect items 3–5. The MCHBAR check stays; the kernel is tainted; on any recent Intel machine whose MCHBAR equals the constant the zone would be created, so the parameter is used only by the maintainer and by the tester kit's unlock chain (decision 27.11.2026, `tester-kit.md` 6.2, which replaces "named testers only"; until its module stages for testers are built, the kit loads read only). The chain does not add a device to the table (6.1 stays). The module's parameter text still says "Developers only". Kit reports go to the device issues of this repository.

## 7. Coexistence
- Kernel's own zone: module declines (4.2). Name conflict: fails (4.5).
- thermald, power-profiles-daemon and the daemon's MSR path keep writing `intel-rapl:0` (MSR copy). The lower enabled copy limits (DV-8).
- **thermald** [C for the source; read 24.11.2026: tag `v2.5.6`, the upstream base of the installed package 2.5.6-2ubuntu0.24.04.6, and master `3bef66a0`; whether the
  installed binary is exactly this code is [H], its strings match]:
  - It creates a cooling device on `intel-rapl-mmio:0` only without `--disable-active-power` and only when its `thermal-conf.xml` matches the platform or `force_mmio_rapl`
    is set, which its adaptive engine does. The adaptive engine (Ubuntu's unit starts thermald with `--adaptive`) needs an INT3400-type device with a GDDV data vault;
    without it thermald falls back to the default engine. The SB3 has no Intel DPTF ACPI device (`outputs.md` 3.3 [M]), so the adaptive path does not apply there [H: its
    `thermal-conf.xml` not checked].
  - **In 2.5.6, adaptive mode with an MMIO zone writes `enabled=0` to `intel-rapl:0`** (log line "Disable rapl-msr interface and use rapl-mmio") and sets a 200 W target on
    the MSR device; this clears enable and clamp of PL1, PL2 and PL4 in the MSR copy. Master no longer writes `enabled=0` there.
  - Its RAPL cooling device writes `enabled=1` when it starts to limit a zone that was disabled at its start, and `enabled=0` when it releases it again; on the MMIO zone
    that would also clear the SB3 firmware's PL1 clamp (5.4).
  - With `--workaround-enabled` (default off) it writes `enabled=0` to the MMIO zone about every 30 s when it has no MMIO cooling device; on Kaby Lake only it falls back to
    clearing bit 15 of MCHBAR `0xfed10000` + `0x59a0` through `/dev/mem`.
  - A restore of RAPL values when thermald exits was **not** found (its terminate path restores thermal-zone policies only).
  - Consequence: thermald must not run next to a zone that someone else writes. `surface-powerd` conflicts with it (`supervisor.md` 2.7); the test plan records and stops
    thermald, power-profiles-daemon and tuned (KM-13). On this machine thermald is disabled.
- **The daemon** uses the zone as specified in `kernel-mmio-daemon.md` (KM-9): the MMIO zone for PL1, PL2 and the PL1 window when it is writable, else the MSR zone, never
  both for the same limit. The daemon never loads the module and never sets `allow_write`.

## 8. Test plan on the Surface Book 3 15" (this machine, kernel 7.3.4-2-surface, lockdown `none`) and results
Needs `sudo` (the maintainer types the password in a terminal tab). In this order; stop at the first deviation, and at any `dmesg` line from the module, thermald or the RAPL
core that is not listed as expected. Raw note `raw/arbeitsordner/km-sb3-test-2026-11-23.md`, scripts and outputs in `raw/arbeitsordner/km-sb3-tests-2026-11-23/`,
independent re-check `raw/arbeitsordner/review-opus-sb3-tests-2026-11-24.md`.

| Step | What | Result [M unless marked] |
|---|---|---|
| 0 | Record services (thermald, power-profiles-daemon, tuned, `surface-rapl.service`, daemon), stop them for write tests (KM-13); record both copies | done before every write test; thermald disabled, tuned and `surface-rapl.service` inactive |
| 1 | Build `W=1`, `checkpatch`, `modinfo` | clean; unsigned module refused under Secure Boot, copy signed with the SB3's DKMS key loads |
| 2 | Read-only load, zone equals the register, unload bit-identical, three cycles (A1, A2) | passed; one read-only refusal logged once (KM-7 fix) |
| 3 | Wrong platform type refused (A3, as far as Ice Lake vs others reaches) | passed; the taint could not be shown (`S` already set) |
| 4a | `allow_write=1`, nothing written, unload | bit-identical; MMIO and MSR energy counters equal |
| 4b–4e | PL1 write, `enabled=1`, load, unload (A4) | field only; four bits; 8 W in force (8.0 W against 13 W); restore bit-identical |
| 4f | s2idle with the zone (A10) | three valid cycles, register unchanged (5.7) |
| 4g | Cold boot after a write (A10) | firmware value back (one cycle) |
| T1 | Register survey (info, units, PL4) | info min/max 0, units 1/8 W, 1/16384 J, 1/1024 s; MMIO PL4 87.625 W |
| T2–T4 | Window, PL2, refusals, two writers (A10, KM-12) | 5.3, 5.6 |
| T5 | Enable/clamp matrix | 5.4 (first run under low-power not evidence) |
| T6 | Suspend with the copy enabled | register and limit kept, ~10 s burst (5.7) |
| T7 | Daemon (control) next to the zone | the daemon never wrote the copy; it cannot read the copy under its units (`kernel-mmio-daemon.md` 8, plan B11) |
| 5 | Daemon in dry-run and control uses the zone | passed 24.11.2026 (plan B12 step 8): dry-run writes nothing, control writes the zone (`enabled=1`, four bits), 9.98 W at a 10 W MMIO PL1 against MSR 30 W, restore at stop and before sleep |
| 8.8a | Burst without suspend (5.7) | after 60 s idle: 3–4 s up to 40 W, package temperature briefly 100 °C, then PL1 |
| 8.8b | Empty DMI table (KM-A3) | `insmod`: No such device, no log line of the module, no zone, register unchanged |

Done on the SB3 24.11.2026: warm restart after a write (firmware value back); module rebuilt and checked for 7.3.6-1-surface (same source, `W=1` and checkpatch clean, `intel_rapl.h` unchanged). On other devices: KM-A6
(read-only report) and KM-A9 (unload with an enabled copy) on a Pro 8, 9 or 10.

## 9. Upstream form
- First a linux-surface patch (like #190/#195), one commit per feature, sign-off by the maintainer after he has read and tested the code, AI assistance disclosed with `Assisted-by: LLM [tools]` and in the changelog (rule read in the linux-surface tree 23.11.2026 [C]; re-read in mainline before submission). Mainline later (`plan.md` E4: linux-pm, int340x maintainers).
- The prototype's primitive table is a copy that follows the 7.3.4 header and must be re-checked for each kernel (KM-A1). Reusing `proc_thermal_rapl_add()` as first suggested does **not** work as is (R): it needs a private header and a `pci_dev`, keeps a static singleton, and registers PL4 and the DRAM domain, which KM-5 excludes. The upstream shape is open: either a stub device that satisfies the native driver (then PL4/DRAM appear and the daemon must not use them) or a new, smaller zone; the linux-pm and int340x maintainers will probably want the same zone as the native driver (`intel-rapl-mmio` with the usual constraints).
- Findings worth reporting upstream with the patch: the silent wrap of values from 4096 W in `rapl_write_data_raw` (all RAPL zones), and that `set_domain_enable` reports success when the enable bit did not stick (5.4, 5.6).

## 10. Open points
- Access width of the Windows write (5.3) [H].
- The clamp on CPUs other than Ice Lake (5.4); not needed for parity.
- Restore on a device with an enabled copy (KM-A9, 5.5); read-only report from such a device (KM-A6).
- Why no burst follows a switch-on, and the burst test without suspend (5.7); S3 and hibernation.
- Lock bits on the Pro 8 (SB3, Pro 9, Pro 10: 0 [M]).
- MCHBAR of types 4–8 beyond the Pro 9/10 (`research/15` 5); width of the address field (4.3).
- Whether the upstream form bounds the values (5.6); in-tree form (9); the exact `Assisted-by` format in mainline (KM-10).
- The default of `allow_write` in the linux-surface form: read-only by default lets the daemon read the copy through the zone (`kernel-mmio-daemon.md` 8.2 C) but not write it.
- Autoload: the prototype has no `MODULE_DEVICE_TABLE` (it would match every Microsoft machine); load it by hand.
