# Spec: kernel module `surface_rapl_mmio`

Version 0.3, 23.11.2026 (evening; 0.2 revised after the independent review, 0.3 after test steps 0–3 on the Surface Book 3 15"). Implements KM-1 … KM-13 of `../requirements/kernel-modules.md`; evidence in `../research/15-mmio-evidence.md`. Markers [C] source or driver, [M] measured, [H] hypothesis, [D] design decision.
Prototype: `kernel/surface-rapl-mmio/surface_rapl_mmio.c` (compiles with `W=1` against 7.3.4-2-surface; **loaded read-only on the Surface Book 3 15" on 23.11.2026, three cycles, steps 0–3 of section 8 passed** [M]; never loaded with `allow_write=1`). Findings of the review are marked (R).

## 1. Purpose
Provide the powercap zone `intel-rapl-mmio` (package domain, PL1 = `constraint_0` long-term, PL2 = `constraint_1` short-term, with time windows) on Surface devices that
have the MMIO copy of the package power limits but no visible processor thermal device 00:04.0. User space then writes the register that Windows writes (`research/15` 1).

## 2. Interface
- Module `surface_rapl_mmio`, GPL-2.0-only, imports the namespace `INTEL_RAPL`, depends on `intel_rapl_common`.
- Parameters (both read-only after load): `allow_write` (bool, default 0), `unsafe_platform` (string, developers only: skips the DMI table; the MCHBAR check still applies; its use taints the kernel).
- The zone's sysfs files stay 0644 when `allow_write=0`; a refused write returns `-EPERM` inside the module, which sysfs shows as `ENODATA` (constraint files) or `ENOSYS` (`enabled`) (R). User space learns whether writes are possible from `/sys/module/surface_rapl_mmio/parameters/allow_write`.
- Log lines carry the prefix `surface_rapl_mmio:`; refusals are logged (rate-limited).
- sysfs: `/sys/class/powercap/intel-rapl-mmio/intel-rapl-mmio:0/` with `constraint_0_*` (PL1) and `constraint_1_*` (PL2). **No** `constraint_2` (PL4), no DRAM or other domain [D] (KM-5).
- Nothing else: no ioctl, no debugfs, no dependence on the daemon.

## 3. Platform types
`icelake` (MCHBAR `0xfed10000`), `tigerlake`, `alderlake`, `raptorlake`, `meteorlake`, `lunarlake` (`0xfedc0000`): the addresses the Windows client assumes (`research/15` 2). A DMI entry names the
type of its device. Types 3 (Gemini Lake) and the arm64 types do not exist here.

## 4. Discovery
1. DMI match (opt-in table, section 6) or `unsafe_platform`. No match: `-ENODEV`, debug message only (KM-2, A3).
2. If PCI device 00:04.0 exists: log once, `-ENODEV` (KM-3).
3. MCHBAR from the host bridge 00:00.0 (vendor Intel), config `0x48` and `0x4c`; bit 0 must be set; address = bits 41:15 [H: width; the SB3 and the Pro 10 values are consistent with it]. **The address must equal the platform type's constant**, else
   `-ENODEV` with a log line (the module never maps an address it has no evidence for) (KM-7). Note (R): this check separates Ice Lake (`0xfed10000`) from the others (`0xfedc0000`) only; the names `alderlake` … `lunarlake` are labels, not detected. It is a sanity check, not an identification; the DMI entry identifies the device.
4. Map one page, `MCHBAR + 0x5000 … +0x6000`, with `ioremap()` (no resource claim: the range is reserved by `PNP0C02` [M, tester]). Registers used: `0x5938` unit, `0x593c` energy status, `0x58f0` performance status, `0x5930` info, `0x59a0` limit.
5. The power unit register `0x5938` must read a non-zero unit (else `-ENODEV`; the core ignores a failed unit read and later divides by it) (R). The limit register is read and recorded (5.5). `powercap_register_control_type("intel-rapl-mmio")`; on a name conflict the module fails with the error and logs it (another driver owns the name). `rapl_add_package(0, …)`. The core needs a non-zero MMIO energy counter at `0x593c`, else `rapl_add_package` fails with `-ENODEV` (R). **On the SB3 the counter runs** (`energy_uj` +17.0, +16.0, +13.2 J in about 2 s each, about 6.6–8.5 W; not cross-checked against the MSR zone yet) and `rapl_add_package` succeeds [M, 23.11.2026]. A module that is not signed with a trusted key is refused by the kernel when Secure Boot is on (`module.sig_enforce` = Y, lockdown `none`); the SB3's own DKMS key signs the test copy (KM-8) [M].

## 5. Register access
### 5.1 Layout [C kernel source, same as Windows]
`0x59a0`: PL1 bits 14:0, enable 15, clamp 16, window 23:17 (kernel time-window format), lock 31; PL2 the same at +32. Power unit from `0x5938` (read through the core's `check_unit`).
### 5.2 Domains and limits [D]
Package domain only, `limits = {PL2}` (so PL1 and PL2). No PL4 constraint: Windows writes PL4 through MSR `0x601` and psys through `0x65c`; a second PL4 path would be a deviation without evidence (KM-5).
### 5.3 Reads and writes
- `read_raw`: 64-bit read of a register inside the mapped page, masked. Anything outside: `-EINVAL`.
- `write_raw`: `-EPERM` unless `allow_write=1` and the module is not being removed; only the limit register `0x59a0`; the mask must lie inside the writable groups (PL1 field 14:0, enable/clamp 16:15, PL1 window 23:17, PL2 field 46:32, enable/clamp 48:47), not the PL2 window 55:49, never bit 31 or 63; a register found locked: `-EPERM`; a PL1 or PL2 below 3 W or an unreadable power unit: `-EINVAL`. The read-modify-write is serialised by a mutex (the core takes only the shared CPU hotplug lock and the powercap store paths take none (R)). Read-modify-write, 64 bit [H: Windows uses a 32-bit access at `0x59a4` for PL2 in most tables; the kernel's own zone uses 64-bit accesses and works in the field [C]].
- With `allow_write=1` the module taints the kernel (`TAINT_CPU_OUT_OF_SPEC`) once (KM-4).
### 5.4 Enable and clamp [open, answered by KM-A4]
Windows ORs enable and clamp (`0x18000`) into **every** PL1/PL2 write [C]. The kernel zone writes the limit field with a write to a constraint; the zone attribute `enabled` is **not** one bit: `set_domain_enable` sets PL1 enable and, through `rapl_default_set_floor_freq`, PL1 clamp and PL2 enable and clamp (four bits); reading it reports the PL1 enable bit only [C, `intel_rapl_common.c`, verified 23.11.2026] (R). On the SB3 both enable bits are 0 [M] (register `0x000001e800dd00c8` read on 23.11.2026: PL1 25 W, **PL1 clamp 1**, PL2 61 W, PL2 clamp 0, both lock bits 0), so a limit written there has no effect until
the copy is enabled [H, not measured]; `enabled=1` would therefore also set the PL2 clamp, which the firmware left at 0 (Windows sets both clamp bits with every write [C]); on the Pro 8, 9, 10 the enable bits are 1 [M].
**Measured on the SB3 (23.11.2026, step 4, raw note `km-sb3-test-2026-11-23.md`) [M]:** a constraint write changes the limit field only (`0x…00c8` → `0x…00a0` → `0x…0040`); with enable 0 and clamp 0 a PL1 of 8 W has no effect (package 13.0 W, held there by the platform limit psys 25/40 W that the firmware applies without the daemon); `enabled=1` sets exactly four bits (`0x000181e800dd8040`: PL1 enable and clamp, PL2 enable and clamp) and the package drops at once to 8.0 W (mean of the last 30 s, 1236 MHz): the MMIO limit is in force against the MSR copy's 30 W (DV-8). `enabled=0` clears all four bits, **including the PL1 clamp that the firmware had set** (`0x…dd…` → `0x…dc…`), so it does not restore the firmware state; only the restore at unload does. Enable alone without clamp was not tested (the zone offers no such write); whether the clamp is needed stays [H]. Decision for the prototype: do not change enable or clamp from the module. The test on the SB3 (A4) decides whether the daemon sets `enabled` (and whether the clamp is needed) or the module does it on the first write;
whichever it is, it is restored at unload (6.3).
### 5.5 Restore (KM-6, KM-11) [implemented in the skeleton, untested]
At load the module records the limit register (all 64 bits). At unload it first sets a flag that makes `write_raw` refuse (the core's package removal clears enable and clamp of every limit and would switch the firmware's limit off on a device with an enabled copy [C, `intel_rapl_common.c` `rapl_remove_package_cpuslocked`] (R)), then removes the zone, then, with `allow_write=1`, writes the recorded value back if the register changed and is not locked. The SB3 cannot show a failure of this (its enable bits are 0): KM-A9 needs a device with an enabled copy.
### 5.6 Bounds [D]
A PL1 or PL2 below 3 W is refused (smallest minimum of the corpus' blobs). No upper bound is applied by the module: the daemon applies the blob's ranges and the hard limits (`outputs.md` 1); a bound against the info register or the firmware's value is an open question. The firmware's own limit and the MSR copy remain the ceiling by the lower-copy rule (DV-8).
### 5.7 Suspend and resume
The core rewrites the limits after every resume through `write_raw` (R); the register after a s2idle cycle is part of KM-A10.

## 6. Opt-in per device
### 6.1 Evidence required for a table entry (all of it, per device; the SB3 entry is the one exception, it is the test bed and gets items 3 and 5 from the test plan before anything else)
1. Windows target for the device's client build: channels 51/52/61 → MMIO `0x59a0`/`0x59a4` (`research/15` 1) [C].
2. Platform type of the device from its integration driver, and the MCHBAR constant of that type [C].
3. Read-only load on the device: MCHBAR from config equals the constant; zone values equal the raw registers read through `/dev/mem` (script `scripts/mmio-pl-read.py`) [M, tester or maintainer].
4. Firmware state of the copy reported (enabled, lock bits).
5. For the first write test on a device: the effective limit measured against the written value, with the original value restored [M].
### 6.2 Table status (23.11.2026)
| Device | Type | 1 | 2 | 3 | 4 | 5 | State |
|---|---|---|---|---|---|---|---|
| Surface Book 3 15" (`Surface_Book_3_1899`) | icelake | ✓ | ✓ | ✓ [M 23.11.]: MCHBAR `0xfed10000` = constant, zone values equal the register, energy counter runs | enable bits 0, PL1 clamp 1, PL2 clamp 0, lock bits 0 [M] | – | in the table as test bed (the SKU matches this machine) |
| Surface Book 3 13" | icelake | ✓ | ✓ | – | – | – | SKU not confirmed (`plan.md` C5) |
| Pro 10 | meteorlake | ✓ | ✓ | – | ✓ | measured through MSR only | candidate, tester has an own module |
| Pro 8, Pro 7+ | tigerlake | ✓ | ✓ | – | Pro 8 ✓ | – | candidates |
| Pro 9 Intel | alderlake | ✓ | ✓ | – | ✓ | – | candidate |
| others | see `research/15` 2 | ✓ | ✓ | – | – | – | no report yet |
### 6.3 Developer override
`unsafe_platform=<type>` lets a tester load the module on a device without an entry, to collect items 3–5. The MCHBAR check stays; the kernel is tainted; on any recent Intel machine whose MCHBAR equals the constant the zone would be created, so the parameter is for the maintainer and named testers only (R). The report goes to `linux-surface#2383`.

## 7. Coexistence
- Kernel's own zone: module declines (4.2). Name conflict: fails (4.5).
- thermald, power-profiles-daemon and the daemon's MSR path keep writing `intel-rapl:0` (MSR copy). The lower enabled copy limits (DV-8).
- **thermald [H]:** the installed binary contains the strings `intel-rapl-mmio`, "Disable rapl-msr interface and use rapl-mmio" and `workaround_rapl_mmio_power` (reviewer, 23.11.2026). It may disable the MSR zone `intel-rapl:0` once an MMIO zone exists. On the SB3, whose MMIO copy is disabled, that would leave no enabled PL1 in either copy. Not verified (thermald source or behaviour not read); the test plan records and stops thermald, power-profiles-daemon and tuned (KM-13).
- The daemon (KM-9, `supervisor.md` to be extended): per limit it uses the MMIO zone when it exists and is writable (module loaded with `allow_write=1`, or the kernel's own zone), else the MSR zone. Snapshot and restore include the MMIO copy; a foreign write to it is reported like one to the MSR zone.
  The daemon never loads the module and never sets `allow_write`.

## 8. Test plan on the Surface Book 3 15" (this machine, kernel 7.3.4-2-surface, lockdown `none`)
Needs `sudo` (the maintainer types the password in a terminal tab). In this order; stop at the first deviation, and at any `dmesg` line from the module, thermald or the RAPL core that is not listed as expected.
0. Record which of thermald, power-profiles-daemon, tuned, `surface-rapl.service` and the daemon run; stop them for the test and note it (KM-13). Record the limit register and the MSR copy with `scripts/mmio-pl-read.py`.
1. Build in a copy of the directory (`make W=1`), check `modinfo`. [done for the skeleton, 23.11.2026]
2. Read-only: `sudo insmod surface_rapl_mmio.ko`; `dmesg`; zone present (if `rapl_add_package` fails with `-ENODEV` the energy counter at `0x593c` does not run: stop and report, do not work around it); values equal `sudo python3 scripts/mmio-pl-read.py` (A2). `sudo rmmod`; zone gone; register bit-identical (A1, repeat three times).
3. Wrong platform: `unsafe_platform=tigerlake` is refused by the MCHBAR check (A3).
4. Writable, no daemon, in sub-steps with the register read after each: (a) `allow_write=1`, write nothing, unload: bit-identical (A9, on this machine only a weak check); (b) write PL1 of the zone to a value below the MSR copy (constraint write only); (c) `enabled=1` (four bits, 5.4); (d) measured package power under load against the written value; (e) restore, unload, bit-identical (A4); (f) a s2idle cycle with the zone loaded (A10); (g) a cold boot shows the firmware value.
5. Daemon in dry-run reads the zone (after the daemon side exists).
The SB3 has the copy disabled, so step 4 is the only place the enable question can be answered; a device with an enabled copy (Pro 10) answers the rest.

**Result of steps 0–3 (23.11.2026, raw note `km-sb3-test-2026-11-23.md`) [M]:** build `W=1` clean, `checkpatch.pl` 0 errors and 0 warnings (39 line-length checks under `--strict`); the unsigned module is refused (Secure Boot), a copy signed with the SB3's own DKMS key loads; step 2 three cycles: zone `intel-rapl-mmio:0` with `constraint_0` (25 W, window 27 983 872 µs = raw 0x6e) and `constraint_1` (61 W, window 976 µs = raw 0), no `constraint_2`, no sub-zone; values equal the register (A2); register bit-identical after every `rmmod` (A1; weak on the SB3, see 5.5); energy counter runs. Step 3: `unsafe_platform=nonsense` and `=tigerlake` are refused with `-ENODEV` and a log line (A3 only as far as the Ice Lake/other boundary reaches; the taint check is not visible on this machine because `S` was already set). **Two findings:** (1) a refused write in read-only mode left no log line, against KM-7: fixed (one `pr_warn_once` for a user-space write; the core's own clean-up writes at removal stay silent) and checked on 23.11.2026 with a second build (sha256 `af376e8f…`, same three cycles plus two refused 20 W writes to `constraint_0`: `ENODATA`, one log line, register unchanged) [M]; (2) PL1 clamp is 1 in the disabled firmware copy (5.4). The same build also checks the return of the MCHBAR config reads and rejects a unit register that reads all ones (checked by code review only, not provoked). **Step 4 (23.11.2026, evening) [M]:** (a) loaded with `allow_write=1`, nothing written, register identical, MMIO and MSR energy counters equal (+24.18 J in 5 s each); (b) PL1 written to 20 W: only the PL1 field changed; (c) `enabled=1`: exactly four bits set; (d) load measurement, see 5.4: with the copy enabled the 8 W PL1 holds the package at 8.0 W against 13 W without; (e) `rmmod` restores the register to the value found at load (`0x000001e800dd00c8`), bit-identical although PL1 and the clamp bit had been changed. (f) **s2idle** [M, 24.11.2026]: two valid cycles through `systemctl suspend` (about 37 s asleep, RTC alarm), read-only and with `allow_write=1` and PL1 = 20 W: the register is unchanged by the cycle (the core's write-back of the saved limits after resume changes nothing, the firmware does not reset the copy); a direct `rtcwake -m freeze` fails on this machine because the nvidia driver refuses a suspend that bypasses systemd [H], so it is not a valid test. Not tested: suspend with the copy enabled. Not yet done: (g) cold boot after a write, concurrent writers (KM-A10), the daemon-side behaviour with a zone present (steps 2–3 ran with the daemon and power-profiles-daemon active, thermald off).

## 9. Upstream form
- First a linux-surface patch (like #190/#195), one commit per feature, sign-off by the maintainer after he has read and tested the code, AI assistance disclosed with `Assisted-by: LLM [tools]` and in the changelog (rule read in the linux-surface tree 23.11.2026 [C]; re-read in mainline before submission). Mainline later (`plan.md` E4: linux-pm, int340x maintainers).
- The prototype's primitive table is a copy that follows the 7.3.4 header and must be re-checked for each kernel (KM-A1). Reusing `proc_thermal_rapl_add()` as first suggested does **not** work as is (R): it needs a private header and a `pci_dev`, keeps a static singleton, and registers PL4 and the DRAM domain, which KM-5 excludes. The upstream shape is open: either a stub device that satisfies the native driver (then PL4/DRAM appear and the daemon must not use them) or a new, smaller zone; the linux-pm and int340x maintainers will probably want the same zone as the native driver (`intel-rapl-mmio` with the usual constraints).

## 10. Open points
Access width (5.3), enable/clamp (5.4), restore on a device with an enabled copy (5.5), thermald (7), lock bits on the Pro 8 (SB3: 0 [M]; energy counter on the SB3: runs [M]), unlogged refusal in read-only mode (KM-7), MCHBAR of types 4–8 beyond the Pro 9/10, in-tree form (9), the exact `Assisted-by` format in mainline (KM-10, decided). Autoload: the prototype has no `MODULE_DEVICE_TABLE` (it would match every Microsoft machine); load it by hand.
