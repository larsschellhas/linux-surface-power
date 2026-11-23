# Requirements and concept: kernel modules for the Surface power limits

Version 0.2, 23.11.2026 (after the independent review of the same day). Decisions of the maintainer of 23.11.2026 (`plan.md` D5, D6). Markers as in `requirements.md`.

## 1. Concept
The SMF daemon (`src/`) computes PL1/PL2/PL3 like Windows' SMF and writes them through Linux interfaces. Windows writes PL1/PL2 to the MMIO copy
(MCHBAR + `0x59a0`) and PL3 to MSR `0x615`; Linux has no interface for the first on devices whose firmware hides the processor thermal device 00:04.0,
and none for the second under kernel lockdown. Two small kernel modules close these gaps. They are **independent of the daemon**: they only provide
interfaces that any user-space program can use. The daemon uses them when present and falls back to what it does today.

| Module | Provides | Windows target [C] | Status |
|---|---|---|---|
| `surface_rapl_mmio` | powercap zone `intel-rapl-mmio` (PL1, PL2, PL1 window) | MMIO copy `0x59a0`/`0x59a4` | prototype in `kernel/surface-rapl-mmio/`; spec `specs/kernel-mmio-rapl.md` |
| `surface_pl3` | PL3 attribute | MSR `0x615` bits 12:0 | concept only; `specs/kernel-pl3.md` |

Development path: prototype in this repository (testers build it) → patches for the linux-surface kernel (like kernel#190/#195) → mainline (`plan.md` E4, E5).
The prototype is licensed GPL-2.0-only (it uses GPL-only kernel interfaces) and lives in its own directory with its own license notice.

## 2. Requirements
| ID | Requirement | Reason |
|---|---|---|
| KM-1 | Each module is independent of the daemon and of every Surface-specific user-space component. | Reuse; upstream. |
| KM-2 | **Opt-in per device**: a module acts only on devices in its DMI table; every entry needs the evidence of `specs/kernel-mmio-rapl.md` 6. No generic activation by CPU model or PCI ID. | `CLAUDE.md` kernel rules: proven command, instance, payload, affected devices. |
| KM-3 | A module that finds the kernel's own interface (00:04.0 present, `intel-rapl-mmio` registered) does nothing. | No duplicate zones. |
| KM-4 | Read-only by default; writing needs an explicit module parameter and taints the kernel (`TAINT_CPU_OUT_OF_SPEC`). | Safe first load on a tester's device. |
| KM-5 | No access to firmware lock bits, to registers outside the evidence table, or to PL4/psys through the MMIO module. | Windows writes PL4 and psys through the MSR. |
| KM-6 | The module records the limit register as found at load and, after the zone is gone, writes it back if it changed (KM-11). | Same promise as the daemon (FR-S1). |
| KM-7 | Every refusal is logged once with the reason (not in table, MCHBAR mismatch, device present, register locked). | Support. |
| KM-8 | Works with Secure Boot and the default lockdown **once it is in the linux-surface kernel**; the prototype needs the tester's own module signing and does not meet gate R5 (`plan.md` 1.1). | Principle "no setup" is a goal of the final form, not of the prototype. |
| KM-9 | The daemon prefers the MMIO zone for PL1/PL2/window where it exists **and is writable**, and writes the MSR zone (powercap) otherwise; it never writes both for the same limit. It knows that the zone is writable from `/sys/module/surface_rapl_mmio/parameters/allow_write` (the zone's files stay 0644 when it is not, and a refused write reaches user space as `ENODATA` or `ENOSYS`). When it switches to the MMIO zone, the MSR copy is first back at its firmware value (snapshot). The blob gives the PL1 window in ms; the kernel's coding truncates, so the daemon reproduces Windows' raw bits (4 steps per octave, `outputs.md` 3.2) and accepts quantisation on read-back. | Windows parity (DV-8) with a fallback. |
| KM-10 | Patches for the kernel are separate per feature (MMIO zone, PL3). The kernel's rule (`Documentation/process/coding-assistants.rst`, read in the linux-surface tree 23.11.2026 [C]): an AI agent never adds `Signed-off-by`, the human submitter does; a contribution carries `Assisted-by: LLM [tools]`. This differs from `CLAUDE.md` ("no AI footer" in commits and posts): **Decided 23.11.2026: the kernel's rule is adopted** for everything that is or becomes a kernel contribution (`CLAUDE.md` "Kernel modules"); commits that touch `kernel/` carry `Assisted-by: LLM [tools]` from the start, the maintainer adds `Signed-off-by`, and the changelog says what the tool did and how the code was tested. The exact tag format is that of the linux-surface tree and is re-read in mainline before every submission. | Review; a rejection of one must not block the other. |
| KM-11 | After unload no register differs from its value at load (bit-identical), and the module's own teardown never reaches the register (the RAPL core clears enable and clamp of every limit when a package is removed [C, `intel_rapl_common.c`]; the module refuses writes first). | The firmware's limit must survive `rmmod`. |
| KM-12 | Writes touch only listed bit groups (PL1 field, PL1 window, PL1 and PL2 enable and clamp, PL2 field; not the PL2 window, not lock bits), are serialised, and refuse a limit below 3 W or a unit register that reads 0. | Bounded, race-free writes. |
| KM-13 | The zone's presence must not silently change the behaviour of other services (thermald in particular, which knows `intel-rapl-mmio` [H, strings in the installed binary]): the test plan records which services run and what they do with the zone before and after. | No hidden interaction. |

## 3. Acceptance criteria
| ID | Criterion | Level |
|---|---|---|
| KM-A1 | The module builds without warnings (`W=1`) against the headers of the target kernel and loads and unloads repeatedly (read-only mode) without leaving a zone, a mapping or a changed register (an out-of-tree module sets the `O` taint flag for the life of the boot; that is expected). | D (SB3) |
| KM-A2 | On the Surface Book 3 15" the zone's PL1/PL2/window values equal what `scripts/mmio-pl-read.py` reads from the registers, in read-only mode. | D (SB3) |
| KM-A3 | On a device that is not in the DMI table, the module returns `-ENODEV` and logs nothing above debug level (tested with a build whose table is empty, because the SB3 is in the table); with `unsafe_platform` a wrong platform type is refused by the MCHBAR check, and the use of `unsafe_platform` taints the kernel. | D |
| KM-A4 | With `allow_write=1` on the SB3 (steps in `kernel-mmio-rapl.md` 8): a write to PL1 of the zone changes the limit field only (enable, clamp, window, PL2 and lock bits unchanged), the effect is measured (package power, `turbostat`) and unload restores the register. The enable-bit question (`specs/kernel-mmio-rapl.md` 5.4) is answered by measurement. | D (SB3) |
| KM-A5 | A write that would touch a lock bit, or a locked register, returns `-EPERM` and changes nothing. | U (test in user space on a fake register block) |
| KM-A6 | Report on a Pro 8, Pro 9 or Pro 10 (read-only first): zone values equal the raw registers, MCHBAR check passes. Only then the device is added to the table. | D (community) |
| KM-A7 | The daemon's MMIO preference (KM-9) is covered by unit tests on a fake sysfs; snapshot and restore of the MMIO copy included. | U |
| KM-A8 | Deviation DV-8 is updated with the measured effective limits of at least one device with an enabled MMIO copy. | D |
| KM-A9 | `allow_write=1`, no write, unload: the limit register is bit-identical to its value before load, first checked on a device with an **enabled** copy (Pro 8, 9, 10), because the SB3's enable bits are already 0 and cannot show the failure of KM-11. | D (community) |
| KM-A10 | Two concurrent writers to PL1 and PL2 lose no update; writes outside the listed bit groups, below the floor or into a locked register are refused; a suspend and resume cycle (s2idle) leaves the register as the zone's last write; a cold boot after a write shows the firmware value again. | D, U |

## 4. Out of scope
- PL4 (MSR `0x601`) and psys (MSR `0x65c`): written through the MSR zone by the daemon today. Whether the kernel's MSR zone offers PL4 on every CPU model of the corpus is not established (the kernel table has models without PL4 support) [H]; that is a separate question.
- AMD and Snapdragon devices (no RAPL MMIO there; `plan.md` J, K).
- Making 00:04.0 visible again by writing DEVEN: unproven, may be locked, the firmware may hide it for a reason [H].
- Changing the SB3's working MSR path: the SB3 is the test bed for the module, not a device that needs it (its MMIO copy is disabled).
