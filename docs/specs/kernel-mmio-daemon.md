# Spec: daemon side of the MMIO write path (KM-9) and reading the MMIO copy (B11)

Version 0.3, 24.11.2026 (0.1 written after the re-check of the SB3 tests; 0.2 with the maintainer's decisions of the same day, section 13; 0.3 implemented, section 15).
Implements KM-9 and KM-A7 of `../requirements/kernel-modules.md` and work packages B11 and B12 of `../plan.md`. Builds on
`kernel-mmio-rapl.md` 0.4 (the module), `outputs.md` 1–3 (output stage), `supervisor.md` 3, 5, 7 (snapshot, foreign changes, suspend) and
`monitoring.md` (status). Markers [C] source, [M] measured, [H] hypothesis, [D] design decision. **State: implemented 24.11.2026 (plan B12
steps 1–6) and tested on a fake sysfs (`tests/test_mmio_path.py`, `tests/test_rapl_copies.py`); **checked on the Surface Book 3 15" on 24.11.2026** (plan B12 step 8, raw note `b12-sb3-test-2026-11-24.md`): path choice, dry-run without a write, control on the module zone (package 9.98 W at a 10 W MMIO PL1, MSR copy at 30 W [M]), restore at stop and before sleep, one s2idle cycle; the window path cannot be checked there (the SB3 blob has no `CF2F6076/61`).** B (8.2) is not
built. Where the code settles a point the text left open, section 15 says how.

**Why a separate spec [D].** The path depends on a module outside the daemon and is switched per boot, not per channel. It touches the
target selection (`outputs.md` 3), the snapshot (`supervisor.md` 3), foreign changes (`supervisor.md` 5), suspend (`supervisor.md` 7), the
status file and the systemd units. Writing it into each of these would put a conditional branch into five specs; one spec keeps it
testable as one unit (KM-A7) and versioned together with the module's spec. The general specs point here (`outputs.md` 3.3,
`supervisor.md` 3, 5, 7).

## 1. Terms
- **MSR zone:** `intel-rapl:0` (`package-0`), the MSR copy `0x610`. **MMIO zone:** `intel-rapl-mmio:0` (`package-0`), the MMIO copy MCHBAR + `0x59a0`.
- **Module zone:** an MMIO zone created by `surface_rapl_mmio` (`/sys/module/surface_rapl_mmio/` exists). **Kernel zone:** an MMIO zone of the processor
  thermal driver (PCI device 00:04.0 present). Both cannot exist together: the module declines when 00:04.0 exists (`kernel-mmio-rapl.md` 4.2) [C].
- **PL path:** where PL1 (`0B7B5350/51`), PL2 (`/52`) and the PL1 time window (`CF2F6076/61`) go: `mmio` or `msr`.

## 2. Choosing the PL path [D]
Once at start, before the snapshot; not changed while the daemon runs.
1. `mmio` when all of these hold:
   - an MMIO zone named `package-0` exists with constraints named `long_term` and `short_term`;
   - it is writable: for a module zone `/sys/module/surface_rapl_mmio/parameters/allow_write` reads `Y`; a kernel zone counts as writable (decided
     24.11.2026, 13.1: it writes the same register as Windows, MCHBAR + `0x59a0` [C, `research/15` 3]); no SMF device with 00:04.0 is measured yet, so the
     first such device is checked with a report before its profile gets `tested = true`;
   - the marker `/run/surface-power/mmio-unusable` does not exist (4.1);
   - the device profile does not set `outputs.mmio = false` (an emergency switch for a device where the path misbehaves).
2. Otherwise `msr` (the behaviour before this spec).
3. **Never both for the same limit:** PL1, PL2 and the PL1 window go to exactly one zone. PL4 (`intel-rapl:0` `constraint_2`, channel 53) and psys (`intel-rapl:1`,
   channels 54/55) stay on their MSR zones on either path: Windows writes them through MSR `0x601` and `0x65c` [C, `research/15` 1]. A kernel zone also
   offers a PL4 constraint and a DRAM sub-zone [C, `processor_thermal_rapl.c`]; the daemon never writes them.
4. The choice and its reason are logged once and put into `status.json` (9).
5. A writable MMIO zone that appears after the start (module loaded later) is noticed by the 60-second check and logged once ("restart the daemon to use it");
   the path is not switched at run time.
6. If the MMIO zone disappears while the path is `mmio` (module unloaded; the module restores the register itself), the next write fails with `ENOENT`. The daemon
   then logs it, restores its snapshot (the MSR keys; the MMIO keys are gone with the zone) and exits with an error; systemd restarts it (`Restart=on-failure`) and
   the new start chooses again. Three such exits in 600 s leave the unit failed on firmware values (`supervisor.md` 8).

## 3. The MSR copy on the `mmio` path [D]
Windows leaves `0x610` at the firmware value (channels 56/57 are used by no blob [C, `research/15` 1]); parity means writing the MMIO copy and not the MSR copy
(maintainer's decision 23.11.2026, `plan.md` 3 D).
- The snapshot (`supervisor.md` 3) holds the MSR PL1, PL2 and window as before; a predecessor's snapshot is kept and used as is.
- Before the first MMIO write, MSR PL1, PL2 and window are compared with the snapshot; values that differ beyond the tolerance (0.2 W, window 15 %) are written back
  once from the snapshot, with one log line. This covers a previous run on the `msr` path that crashed without restore.
- After that the daemon never writes PL1, PL2 or the window of the MSR zone on the `mmio` path; it watches them (10). Its enable bits are never changed, as before
  (`outputs.md` 3).
- That the restored values are the firmware's is as good as the snapshot: a fresh start after a clean stop reads the firmware values [H: the restore by
  `surface-power-reset` was not checked on its own, raw note step 4b].
- **Consequence for the limit in force:** with the MSR copy at its firmware value, the lower-copy rule caps the blob's PL1/PL2 at the firmware MSR value. On the
  SB3 the blob's highest values (30/61 W, Best performance on AC [M, `research/02`]) equal the firmware MSR values (30/61 W [M]), on the Pro 8 and Pro 9 the
  blob's 35/60 W on AC in the highest slider state (engine replay with assumed inputs) equal theirs (35/60 W [M, testers]); nothing is lost there. On the Pro 10 the blob allows up to 65/100 W against a firmware MSR
  of 35/64 W [M, tester], so the MSR copy caps there, as it presumably does under Windows [H, `outputs.md` 3.3 "Windows' own limit"]. The status reports a
  computed value above the MSR copy (9).

## 4. Writing the MMIO copy
### 4.1 First write [D]
In this order, once per start (and after each restore that cleared `enabled`, 5.2):
1. Snapshot of the MMIO copy (5.1).
2. Window (where the blob has `CF2F6076/61`), then PL1, then PL2, through the zone's constraint files, each read back (`outputs.md` 1).
3. If the zone's `enabled` reads 0: write `enabled=1` once and read it back.
If a write in step 2 or 3 fails, or `enabled` reads back 0: the daemon logs the error once, restores its snapshot, creates `/run/surface-power/mmio-unusable`
(the runtime directory survives a restart, `RuntimeDirectoryPreserve=yes`) and exits with an error; the restart then chooses `msr` (2.1) for the rest of the
boot. Reasons this can happen: a locked register (the module refuses, `ENODATA`), `allow_write` changed by a reload, a firmware that refuses the enable bit.
### 4.2 Enable and clamp
- `enabled=1` sets PL1 enable and clamp and PL2 enable and clamp (four bits) [C, M on the SB3]: the state Windows reaches with its `0x18000` on every write [C]. The
  daemon writes no single bits; there is no interface for them (`kernel-mmio-rapl.md` 5.4).
- Where the firmware's copy is already enabled (`enabled` reads 1: Pro 8, Pro 9, Pro 10 [M, register reads by testers]) the daemon never writes `enabled`. Their
  clamp bits are then whatever the firmware set [H: not reported]; Windows would set them with its first write. This difference is recorded in `deviations.md` DV-8.
- A successful `enabled=1` proves only PL1 enable: the core reports success also when the read-back differs and ignores the result of the other three writes [C,
  `kernel-mmio-rapl.md` 5.4]. Where the register is readable (8), the daemon checks all four bits after the write and logs a difference once.
- `enabled=0` is written only by the restore (5.2).
### 4.3 Values
- Same output stage, same rules: blob range, hard limits (6), write on change only, read back.
- **Window:** the existing window target (28 steps, rounded up as the Windows client does, `outputs.md` 3.2), with the path
  `intel-rapl-mmio:0/constraint_0_time_window_us`. The core rounds down and every step lands on the intended code; the read-back is 0.9994 of the written value
  [C, computed for all 28 steps; M for six]; the existing tolerance (max(0.2 s, 15 %)) covers it. No change of the window code.
- The window steps assume the time unit 1/1024 s (the Windows client assumes it too and does not read it [C, `research/12` 2.1]). The daemon reads the time unit
  (bits 19:16) from `0x5938` where the register is readable (8), else from MSR `0x606` (which it already reads for the power unit), and makes the window
  log-only, with one message, where it is not 10 [D]. On the SB3 `0x5938` reads `0x000a0e03`, time unit 10 [M]; that `0x606` holds the same unit is [H].
- The window rounding of the Windows client was read in the Pro 10 client (and the same descriptors in the Laptop Studio 2 client), not in the SB3 client
  (`research/12` 2.1): for the SB3 the 28-step rounding is [H] until that table is read.

## 5. Snapshot and restore of the MMIO copy
### 5.1 Snapshot [D]
- Keys `mmio:<zone path>/constraint_0_power_limit_uw`, `…/constraint_1_power_limit_uw`, `…/constraint_0_time_window_us`, `…/enabled`, in `snapshot.json` like the other
  powercap keys (written atomically, before the first write, predecessor kept). Where the register is readable (8), also `mmio-raw@0x59a0` (64 bits, hex) for the
  report and the bit check; it is never written back by the daemon.
### 5.2 Restore [D]
When the supervisor restores (exit, unhandled exception, `PrepareForSleep`/`SIGUSR2`, emergency, yielded; `supervisor.md` 3) and in `surface-power-reset`:
1. If the snapshot's `enabled` is 0 and the zone's is 1: write `enabled=0` first (the limit stops at once).
2. Window, then PL1, then PL2 from the snapshot, only where they differ.
3. If the snapshot's `enabled` is 1: `enabled` is not touched.
- **Residual difference [M on the SB3]:** where the firmware left the copy disabled with PL1 clamp 1 (SB3), `enabled=0` also clears that clamp; the zone cannot set it
  again. The copy is then PL1 enable 0 / clamp 0 instead of 0 / 1. On the SB3 both states have the same effect (no limit: 14.9 W in both cells of the matrix,
  `kernel-mmio-rapl.md` 5.4); the bit comes back with the module's restore at unload, a cold boot or a warm restart (`kernel-mmio-rapl.md` 5.5). The daemon logs it once where the
  register is readable. Recorded as DS-5 in `deviations.md`. **Accepted by the maintainer on 24.11.2026 (13.3).** A cold boot and a warm restart
  were tested (one cycle each, SB3) [M].
- Where the firmware's copy is enabled (`enabled` is 1 in the snapshot) the restore writes only the fields, so the register ends bit-identical to the snapshot: the
  power fields are exact multiples of the unit and the window code writes back to itself [C, M on the SB3].
- The MSR keys are restored as before; on the `mmio` path they were not written (3), so their restore changes nothing unless someone else wrote them.

## 6. Bounds
- The hard limits of `outputs.md` 2 apply to the MMIO targets unchanged: PL1 3–120 W, PL2 3–200 W; a configuration can only narrow them.
- Why the daemon must bound: the module has no upper bound, the RAPL core wraps values from 4096 W silently to the 15-bit field (5 kW became 904 W [M]), and the
  device gives no bound (info register min/max 0 on the SB3 [M]; `constraint_1_max_power_uw` reads 0) (`kernel-mmio-rapl.md` 5.6). The hard maximum keeps every
  write far below the wrap; the hard minimum equals the module's floor (3 W), so a write the module refuses cannot come from the output stage.
- `constraint_*_max_power_uw` of the MMIO zone is not used as a bound (as for the MSR zone, `outputs.md` 2).

## 7. Suspend and resume
- Before sleep the supervisor restores the snapshot including the MMIO keys (5.2); after the resume it writes again in its first cycle and, on a disabled firmware
  copy, writes `enabled=1` again (4.1) (`supervisor.md` 7).
- The register survives s2idle unchanged and an enabled limit holds after the resume [M, SB3, three cycles], so no other handling is needed. The RAPL core writes the
  limit fields it saved at `PM_SUSPEND_PREPARE` back after the resume [C]; the sleep hook signals the daemon and waits 1 s before the kernel suspends
  (`packaging/system-sleep/surface-power`), so the saved values are the restored firmware values and the write-back changes nothing the daemon would see as foreign
  [H: a restore that takes longer than 1 s would let the core save the daemon's values; the next cycle overwrites them anyway].
- About 10 s above PL1 after a resume is the normal turbo budget after an idle phase, bounded by PL2 and psys PL2 [M, H for the explanation,
  `kernel-mmio-rapl.md` 5.7]; Windows has the same. Not a fault, not a foreign change.

## 8. Reading the MMIO copy (B11)
### 8.1 Problem [M, C]
The daemon reads the MMIO copy every 60 s for the report, the status and the warning "the MMIO copy of PL1 is … below the computed …" (`outputs.md` 3.3). It finds the
address in the PCI config of the host bridge 00:00.0 at offset `0x48`. Without `CAP_SYS_ADMIN` the kernel lets a process read only the first **64 bytes** of a PCI
config (128 for CardBus) and returns 0 bytes beyond, without an error [C, `drivers/pci/pci-sysfs.c` `pci_read_config`; the same rule in `drivers/pci/proc.c`]. Both
units allow only `CAP_DAC_OVERRIDE CAP_SYS_RAWIO`, so the read returns 0 bytes and `rapl_copies.mchbar()` returns `None` without a log line [C]; `status.json` shows
`"mmio": null` [M, SB3, test 7, in every phase]. With `CAP_SYS_ADMIN` added, the same sandbox reads the address and the register [M, diagnosis (b)]; the rest of the
sandbox is not the cause [M, (c)]. Opening `/dev/mem` needs only `CAP_SYS_RAWIO` and lockdown `none` [C, `drivers/char/mem.c` `open_port`]: once the address is known,
the daemon can read the register with the capabilities it has.

### 8.2 Ways to the address
| Option | How | Consequences | Risks |
|---|---|---|---|
| A. `CAP_SYS_ADMIN` in the units | add it to `CapabilityBoundingSet=` of both units | one token; today's code works [M] | `CAP_SYS_ADMIN` is the broadest capability (mounts, many ioctls, namespaces …), held for the whole life of a process that parses blob data every second, on every device, also without an MMIO copy; a packager or the linux-surface maintainers will question it |
| B. Privileged helper at start | `ExecStartPre=+/usr/bin/surface-power-mchbar`: reads the 8 bytes at `0x48` once per start and writes the address (or `none` and the reason) to `/run/surface-power/mchbar`; `+` runs one command "with full privileges", `CapabilityBoundingSet=` still applies to `ExecStart=` [C, `systemd.service(5)`, systemd 255; the `!` prefix does not lift capabilities] | the daemon keeps its capabilities; the privileged part is a few lines with no blob input | one more program and packaging entry; the address is read once per start (firmware sets MCHBAR at boot [H: constant for the boot]); both units need the line |
| C. Values from the zone | read PL1, PL2, window and `enabled` from the MMIO zone | no address needed; used anyway on the `mmio` path | only where the module (or a kernel zone) exists, today nowhere but the SB3 test bed; `enabled` is PL1 enable only, no PL2 enable, clamp or lock [C, `get_domain_enable`]; does not fix B11 on the Pro 8, 9, 10, where the warning matters |
| D. Constant per platform type | the address the Windows client assumes (`0xfed10000` Ice Lake, `0xfedc0000` the others, `research/15` 2), type from the profile | no capability | not checkable at run time (the module checks it because it can read the config); a wrong type maps a wrong physical page (read only, but reads of unknown MMIO can have side effects [H]); needs the type in every profile with per-device evidence (`CLAUDE.md`) |
| E. Only make it visible | log once and put the reason into the status | no new privilege | the copy stays unread under the units, the warning stays dead |
Not usable: `/proc/iomem` (addresses read 0 without `CAP_SYS_ADMIN` [C, `kernel/resource.c` `r_show`]); `lspci` and `/proc/bus/pci` (same 64-byte rule); the `PNP0C02`
resources in `/sys/bus/pnp/devices/*/resources` are readable without a capability [C, `drivers/pnp/interface.c`] but do not say which range is MCHBAR (picking one
would be a guess [H]).

**Decided 24.11.2026 (maintainer, 13.2):** **E** now and in any case; **C** is the target: the copy is read through the zone wherever the module (or a
kernel zone) exists, which is also the form the linux-surface kernel patch is meant to have; **B** is allowed as a temporary aid for diagnosis and debugging on
devices without a zone, not as the long-term solution, and is not on the critical path; **A** and **D** are not used.
What C does not cover, and stays so: a device without a zone (not yet in the module's table) has no daemon-side reading of the copy; `surface-power-report`
run as root reads it there (it is not sandboxed), which covers the diagnosis for testers; PL2 enable, the clamp bits and the lock bits are not visible
through the zone (only through the report or B).

### 8.3 What changes (in this order: E, C, then B only if wanted; A is described for completeness)
- **E (in any case):** `rapl_copies.read_mmio()` returns a reason with `None` (`"PCI config beyond 64 bytes needs CAP_SYS_ADMIN"`, `"MCHBAR not enabled"`,
  `"/dev/mem not readable"`); the daemon logs the first reason once per run and puts it into `status.json` (`rapl_copies.mmio_unreadable`). The test
  `test_silent_when_the_mmio_copy_is_off_or_unreadable` (`tests/test_rapl_copies.py`) changes: silent for an off copy, one line for an unreadable one.
  **Implemented 24.11.2026:** `read_mmio()` and `mchbar()` return `(value, reason)`; a failure of `/dev/mem` carries the system's message
  (`"/dev/mem not readable (Operation not permitted)"`), a config that cannot be opened `"PCI config of 00:00.0 not readable (…)"`; the line is logged at
  `notice`; `surface-power-report` prints the reason in place of its general text.
- **Address mask:** one definition, bits 41:15 as in the module (`rapl_copies.MCHBAR_ADDR_MASK`; before 24.11.2026 the daemon and `scripts/mmio-pl-read.py`
  masked bits 38:15, the module 41:15; equal for the known addresses [C]). Implemented 24.11.2026.
- **B (optional, temporary):** new script `surface-power-mchbar` (reads, checks bit 0, writes `/run/surface-power/mchbar` atomically, exits 0 also on failure so that the daemon starts);
  `ExecStartPre=+/usr/bin/surface-power-mchbar` in `surface-powerd.service` and `surface-powerd-dryrun.service`; `rapl_copies.mchbar()` reads that file first, then
  the config (which still works as root without a sandbox, for `surface-power-report`). `CapabilityBoundingSet=` unchanged.
- **A (not chosen):** `CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_SYS_RAWIO CAP_SYS_ADMIN` in both units.
- **C:** wherever a zone exists (on either path, also a read-only module zone) the 60-second check takes PL1, PL2, window and PL1 enable from the zone (source
  `zone`) and the raw register only where it is readable; the warning "MMIO copy below the computed value" works from the zone values on the `msr` path.
- Testers without a zone (today all of them) see no MMIO warning from the daemon under its units; `surface-power-report` as root shows the copy. With B the
  warning would work there too [H: not checked on their devices; same unit].

## 9. Status and log [D]
- `status.json`: `pl_path` (`"mmio"` or `"msr"`) and `pl_path_reason`; the MMIO targets appear under their channels like other powercap targets; `rapl_copies.mmio`
  with its source (`"zone"` or `"register"`) and `rapl_copies.mmio_unreadable` with the reason (8.3).
- Journal, once each: the chosen path and why; `enabled=1` written; the residual clamp difference after a restore (5.2); the MMIO copy not readable and why; a
  writable MMIO zone that appeared after the start.
- On the `mmio` path the warning "MMIO copy below the computed value" does not apply (the daemon writes that copy); instead a lower **MSR** copy is reported the same way
  (the lower enabled copy is in force, DV-8): "the MSR copy of PL1 is … below the computed …".

## 10. Foreign changes
`supervisor.md` 5 applies to the MMIO targets as to every owned target: compared before each write cycle with the last value written or read back, PL1/PL2 beyond 0.2 W,
the window beyond the window tolerance; two changes in 60 s are logged and overwritten, the third makes the daemon yield.
In addition, on the `mmio` path:
- `enabled` reading 0 after the daemon set 1 is a foreign change (thermald's RAPL device or its workaround write `enabled=0` [C, `kernel-mmio-rapl.md` 7]);
- the MSR zone's PL1, PL2 and window are compared with the snapshot (3): a change is a foreign change, because through the lower-copy rule it can lower the limit in force;
- where the register is readable (8), the 60-second check compares the four enable/clamp bits with the expected state; a difference is logged once (not counted for the
  yield rule: the zone cannot show these bits, so the daemon could not have caused or corrected them through the zone).

## 11. Dry-run
Dry-run chooses the path, computes and logs the values for it and shows them in the status; it writes nothing: no MMIO value, no `enabled`, no MSR restore of section 3.

## 12. Tests (written before the code; all exist since 24.11.2026 except MB-5 … MB-7, which belong to the optional B)
On the fake sysfs of `tests/fakesys.py`, extended by an MMIO zone (`sys/class/powercap/intel-rapl-mmio:0` with `name`, `enabled`, `constraint_0/1_*`), the module's
parameter file `sys/module/surface_rapl_mmio/parameters/allow_write`, an optional `sys/bus/pci/devices/0000:04.0` and a fake register file for `/dev/mem` reads.

| ID | Covers | Setup | Expected |
|---|---|---|---|
| MD-1 | 2.1 | module zone, `allow_write` = `Y` | path `mmio`; PL1/PL2/window targets on the MMIO zone; PL4 and psys on the MSR zones |
| MD-2 | 2.1 | module zone, `allow_write` = `N` | path `msr`, reason "read-only" |
| MD-3 | 2.1 | no MMIO zone | path `msr`, behaviour as before (existing tests unchanged) |
| MD-4 | 2.1, 2.3, 13.1 | kernel zone (00:04.0 present, zone with `constraint_2` and a DRAM sub-zone) | `mmio`; PL4 stays on `intel-rapl:0`, the kernel zone's `constraint_2` and DRAM are never written |
| MD-5 | 2.1 | marker `mmio-unusable` present; profile `outputs.mmio = false` | `msr` in both cases |
| MD-6 | 2.3 | any `mmio` run | no write to `intel-rapl:0` `constraint_0/1` or its window after 3; never both zones for one channel |
| MD-7 | 3 | `mmio`, MSR PL1 differs from the snapshot | one write of the snapshot value to the MSR zone before the first MMIO write, one log line; none afterwards |
| MD-8 | 4.1 | `mmio`, zone `enabled` 0 | order window, PL1, PL2, then `enabled=1`; read back |
| MD-9 | 4.2 | `mmio`, zone `enabled` 1 | `enabled` never written |
| MD-10 | 4.1 | `enabled` reads back 0, or a constraint write fails with `ENODATA` | snapshot restored, marker created, exit with error |
| MD-11 | 4.3 | blob windows 1000, 8000, 28000, 30000 ms | written 1 000 000, 8 000 000, 28 000 000, 32 000 000 µs; read-back 0.9994 × value is not a foreign change |
| MD-12 | 4.3 | time unit (fake `0x5938`, else `0x606`) not 10 | window log-only, one message |
| MD-13 | 5.1 | first write | snapshot holds the four MMIO keys (and `mmio-raw@0x59a0` when the fake register is readable) before any MMIO write |
| MD-14 | 5.2 | snapshot `enabled` 0, zone `enabled` 1 | restore writes `enabled=0` first, then window, PL1, PL2 |
| MD-15 | 5.2 | snapshot `enabled` 1 | restore writes fields only, `enabled` untouched |
| MD-16 | 5.2 | `surface-power-reset` with MMIO keys in the file | same order as MD-14/15; file removed |
| MD-17 | 5.2 | predecessor snapshot with MMIO keys | kept and used as is |
| MD-18 | 6 | engine value 5000 W, 1 W, blob range above the hard limit | written ≤ 120 W (PL1) / 200 W (PL2), ≥ 3 W; one log line for the cut |
| MD-19 | 7 | `SIGUSR2`, then `SIGUSR1` | restore incl. MMIO keys before sleep; after resume the first cycle writes again and `enabled=1` again where the firmware copy was off |
| MD-20 | 10 | zone `enabled` set to 0 by a "foreign" write | counted as a foreign change; third in 60 s → yield, restore |
| MD-21 | 10 | MSR PL1 changed by a foreign write on the `mmio` path | foreign change |
| MD-22 | 2.6 | MMIO zone removed during the run | restore of the MSR keys, exit with error, no traceback loop |
| MD-23 | 11 | dry-run on `mmio` | no write at all (fake files unchanged), status shows the path and the values |
| MD-24 | 9 | `mmio` path, MSR copy below the computed PL1 | warning about the MSR copy once; no MMIO warning |
| MB-1 | 8.3 E | config file of 64 bytes (the view without `CAP_SYS_ADMIN`) | `read_mmio` gives `None` with the reason "beyond 64 bytes"; one log line per run; status `mmio_unreadable` set |
| MB-2 | 8.3 E | config readable, MCHBAR bit 0 clear | reason "MCHBAR not enabled", one line |
| MB-3 | 8.3 E | `/dev/mem` not readable | reason "/dev/mem not readable", one line |
| MB-4 | 8.3 | config value with address bits above bit 38 | mask 41:15 applied |
| MB-5 | 8.3 B (optional) | `/run/surface-power/mchbar` holds an address | used without reading the config |
| MB-6 | 8.3 B (optional) | the file holds `none` and a reason | `None`, the reason from the file is logged once |
| MB-7 | 8.3 B (optional) | helper with a fake config: 8 bytes, bit 0 set / clear / short read | writes the address / `none` with the reason; exit code 0 in every case |
| MB-8 | 8.3 C | a zone exists (read-only module zone on the `msr` path, and on the `mmio` path), register not readable | the 60-second check reports the copy from the zone (source `zone`); on the `msr` path the MMIO warning works from the zone values |
| MB-9 | 8.3 | existing test `test_silent_when_the_mmio_copy_is_off_or_unreadable` | split: silent for an off copy, one line for an unreadable one |
Hardware checks after the code (the maintainer's yes before each, SB3, `kernel-mmio-rapl.md` 8 step 5): dry-run and control with the module zone; `status.json` shows the
copy under the unit; restore at stop and before sleep; the residual clamp line; the effect measured as in the matrix (daemon PL1 below the MSR copy).

## 13. Decisions (maintainer, 24.11.2026)
1. **Kernel zone: used automatically** like a module zone with `allow_write=Y` (2.1). Reason: it writes the same register as Windows [C]. Risks named and accepted:
   no SMF device with 00:04.0 is measured (the first one is checked with a report); such a device probably has the Intel DPTF stack, where thermald in adaptive
   mode also uses the zone (the daemon conflicts with thermald anyway, `kernel-mmio-rapl.md` 7); the kernel zone has PL4 and DRAM, which the daemon leaves alone
   (2.3); unloading the processor thermal driver clears the enable bits through the RAPL core (kernel behaviour, not the daemon's) [C]. Switch-off per device:
   `outputs.mmio = false`.
2. **B11: E + C, B only as a temporary aid** (8.2).
3. **Residual clamp after restore (DS-5): accepted, logged once** (5.2). A module interface to restore single bits is not built (extra kernel code, not wanted upstream).
4. **Order of work:** B11-E first (small), then the fake sysfs and the path choice, then writing, snapshot and restore, then foreign changes and resume, then the
   hardware checks on the SB3 (`plan.md` B12). B is not on the critical path.

## 14. Open points
- The clamp on CPUs other than Ice Lake (no effect on parity, 4.2); the clamp bits of the Pro 8/9/10 firmware copies.
- The 28-step rounding of the SB3 client (4.3) [H].
- The restore before sleep within the hook's 1 s (7): one s2idle cycle on the SB3 showed the restore and no foreign change after the resume [M, 24.11.2026]; more cycles not run.
- Whether MCHBAR is constant for a boot (8.2 B, only if B is built) [H].
- The first SMF device with 00:04.0 (13.1); the residual clamp (5.2) is settled on the SB3: unload, cold boot and warm restart bring it back [M].
- The window path on a device whose blob has `CF2F6076/61` (not the SB3).

## 15. Implementation notes (24.11.2026)
Points the code settles where the text above leaves room; none changes a decision of section 13.
- **Modules:** `adapters/plpath.py` (choice, 2), `adapters/outputs.py` (targets on the MMIO zone, order, enable, MSR copy, `PathFailure`), `adapters/rapl_copies.py`
  (reasons, mask, `time_unit`, `read_zone`), `supervisor.py` (snapshot `info`, restore order), `daemon.py` (log, status, foreign changes, exit), `tools/report.py`.
- **Snapshot keys (5.1):** the MMIO keys are the plain powercap paths (`…/intel-rapl-mmio:0/constraint_0_power_limit_uw`, …, `…/enabled`), like every other powercap
  key, not prefixed with `mmio:`; the restore recognises the zone by its path. The raw register is kept in a separate `info` section of `snapshot.json`, which is never
  written back. Keys a predecessor's snapshot does not have (it ran on the other path) are added with their current values: that predecessor never wrote them.
- **Restore (5.2):** an `enabled` key that was 0 is written first where it reads 1; one that was 1 is never touched, also when it reads 0. Keys of a zone that is gone
  are skipped (2.6). The DS-5 line is logged once after a restore where the register is readable.
- **Enable ownership (4.2, 10):** the daemon writes `enabled=1` only when the snapshot's `enabled` is 0, after a cycle that wrote a PL target; `enabled` reading 0
  after that is a foreign change and is set again in the same cycle. A firmware-enabled copy's `enabled` is neither written nor watched.
- **Order (4.1):** every apply writes the window, then PL1, then PL2, then the rest, on both paths.
- **Time unit (4.3):** where neither MCHBAR + `0x5938` nor MSR `0x606` is readable, the window is log-only too (no assumption).
- **MSR copy (3, 10):** checked against the snapshot every cycle after the first; a change is a foreign change and is set back to the snapshot value.
- **Failure (4.1, 2.6):** `PathFailure` from the output stage; the daemon logs it, writes the marker (not for a zone that is gone), and `run()` restores through
  `stop()` and returns 1. A zone that is gone is detected before the foreign-change check, so it does not show as a foreign change.
- **Bounds (6):** the line for a value cut by the hard limits is logged once per MMIO target per run (warning); a value cut by the profile's `outputs.narrow` is logged as a narrowing (notice), corrected after the SB3 check.
- **Reading (8.3 C):** the register is preferred where readable (source `register`), else the zone (source `zone`). The zone shows no PL2 enable, so the warning about
  a lower copy covers PL1 only when it comes from the zone. On the mmio path the MSR copy comes from MSR `0x610`, else from `intel-rapl:0`.
- **Window step (found while writing MD-11):** the window target wrote a new step only when it differed from the last one by more than the read-back tolerance (15 %),
  so a change of one step (28 s → 32 s, 14 %) was not written, on the msr path too. It now writes whenever the step differs; the tolerance applies to the read-back only.
- **Report:** `surface-power-report` prints the path the daemon would choose and its reason, and the MMIO copy from the zone where the register is not readable.
