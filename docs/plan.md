# Project plan: from the reference device to a rollout on every SMF Surface

Version 1.23, 25.11.2026. This is the single plan of the project: target, work packages, their state, and what comes next. Read it
first; it is kept current with every change of state (section 7). Evidence markers as in `docs/README.md`: [C] driver code or ACPI
table, [M] measured, [H] hypothesis, [D] design decision.

**Status symbols:** ✅ done · 🔄 in progress or partly done · ⬜ open · ⏳ waiting for others (testers, maintainers, upstream) ·
➖ not applicable yet · 🙋 **help wanted** (something a tester or contributor can do) · ⚠️ hard or uncertain (upstream acceptance, risk)

## 1. Target

**A daemon that can be rolled out on every Surface device whose power and thermal management runs on Surface SMF under Windows**
(scope levels L1–L3 of `requirements/requirements.md` 1.2: the 2020–2025 models, x64 and arm64), with Windows parity (G-1), driven by
the device's own blob (G-2), fail-safe (G-4) and upstream-ready (G-5). MPTF devices (2025/26, L4) are not part of this target; they are
tracked in [linux-surface#2384](https://github.com/linux-surface/linux-surface/issues/2384).

Principles for the rollout:
- **Secure Boot without extra setup.** Everything works with Secure Boot on and the distribution's default lockdown, without anything
  beyond the normal linux-surface installation: no own MOK enrolment, no DKMS module, no change of the lockdown mode, no debug device to
  load by hand. For mainline kernels the target is no setup at all.
- **Kernel first in linux-surface, then in mainline.** Every kernel interface the daemon needs goes to linux-surface first and is then
  submitted to mainline (groups D and E). Merged in linux-surface is not the end state.
- **Safety proven per device before anyone runs it.** Every profile passes a simulated extreme-scenario suite (B7) before a tester is
  asked to run control.
- **Python reference first.** The production version (Rust or C, decision open) starts only after the Python reference is complete (L1, L2).

### 1.1 Rollout gates per device
A device is **rollout-ready** when all gates hold. They are evaluated per device in the matrix (section 5).

| Gate | Condition | Where it is checked |
|---|---|---|
| R1 Blob | The engine builds and runs every line of every blob of the device (all sizes and variants). | `scripts/corpus-report.py`, device page |
| R2 Profile | A profile matches exactly the device's DMI values, taken from a tester report or a proven source (never derived from drivers). Variant and options (`pl3`, `pl4`, dGPU, fan) are proven in the driver of this device. | `src/surface_power/profiles/`, `CLAUDE.md` procedure steps 3 and 8 |
| R3 Inputs | Every input that reaches a written output has a Linux source that matches Windows, or a documented, evidence-backed substitute (constant, estimate, fallback) in `specs/deviations.md`. No debug device. | `specs/inputs.md` 8, device page, group G |
| R4 Outputs | Every output is written by Linux or classified "not needed on Linux" with evidence (device page: "Ready for control: yes"). | `specs/outputs.md`, `research/10`, `12`, device page, group H |
| R5 Secure Boot | All write paths and inputs work with Secure Boot and the default lockdown, without extra setup (principle above). The effective limit is the one the blob computes (no firmware copy caps it, DV-8). | `specs/outputs.md` 3.3, 4 |
| R6 Safety simulation | The device's blobs and profile pass the extreme-scenario suite (B7): limits in range, temperatures in the defined bands, fail-safe on lost inputs. | `tests/` (B7) |
| R7 Validation | A tester ran dry-run and control on the device, the result is compared with Windows (AC-M1 style), every deviation is in `specs/deviations.md`. | `CLAUDE.md` procedure, `research/` |
| R8 Release | `tested = true` in the profile; README table and device page updated. | profile, README |

Global gates for a release (all devices): packaged for the common distros and installable from the linux-surface repository; the
kernel interfaces it needs are in the released linux-surface kernel; fail-safe criteria AC-S1–S8 verified on hardware.

## 2. Overview

| WP | Work package | Status | Next step / blocker |
|---|---|---|---|
| **A** | **Foundations** | ✅ | |
| A1 | SB3 root cause (psys 25/40 W, no SMF on Linux) | ✅ | – |
| A2 | Windows measurements on the SB3 (modes × sources) | ✅ | – |
| A3 | SMF decoded: blob in EFI variable, core semantics | ✅ | – |
| A4 | Device survey and blob corpus (42 files, 41 SMF) | ✅ | – |
| A5 | Requirements, acceptance criteria, architecture | ✅ | update requirements to v0.3 (Secure Boot without setup, Rust or C, B7) |
| A6 | Specs v0.1 (nine specs) | ✅ | keep in sync with the code (F8) |
| **B** | **Reference implementation (Python)** | 🔄 | |
| B1 | Loader: forms A/B/C, macros, expressions, MPTF rejected | ✅ | – |
| B2 | Engine: all block types 2020–2025, both PID variants | ✅ | – |
| B3 | Input adapters for all blob inputs | 🔄 | tested on fakes and the SB3 only; device checks in F, open inputs in G |
| B4 | Output adapters: powercap PL1/PL2/psys, PL4, PL3 (MSR), thermal policy, throttle caps, fans, PROCHOT, sensor offset, dGPU stand-in | 🔄 | throttle caps, fans, PROCHOT, sensor offset untested on a device |
| B5 | Supervisor, monitor, daemon, tools, units, sleep hook | ✅ | – |
| B6 | Report tool, tester guide, device pages generator | ✅ | – |
| B7 | **Safety suite per device:** every profile and blob through extreme scenarios in simulation, run locally | ⬜ | criteria agreed 19.11.2026 (3, B7); fix the numbers, then write the script |
| B8 | **CPU budget of the daemon:** about 1 % of one core on two devices (SB3 control run, Pro 9 dry-run 0.98 % over 59 h) against the AC-L4 criterion of 0.5 % | ⬜ | **0.56 % accepted for now** (maintainer, 24.11.2026, after C3): does not block C4/C6; stays open as an optimisation for later: profile first (EC requests every 2 s, copy reads every 60 s, interpreter); cause unproven [H] |
| B9 | **PL3 stage when MSR `0x615` is not readable** although `0x606` is (Pro 9: EIO on `0x17`, `0x611`, `0x614`, `0x615`, F10): the setup reads only `0x606`, the supervisor's reaction to a failing write is not checked | ⬜ | check the behaviour, then test `0x615` before creating the target (code change, maintainer's yes) |
| B10 | **Report tool vs daemon:** the report calls `0B7B5350/59` "writable as root" and the device "ready for control: yes" without a profile that switches PL3 on, while the daemon treats PL3 as log-only and stays in dry-run (Laptop 4 Intel report, 20.11.); it also says nothing about inputs without a source that reach written outputs (505/509 "not available") | ⬜ | make the report say what the daemon does (code change, maintainer's yes) |
| B11 | **Daemon cannot read the MMIO copy under its systemd units** [M, SB3, 24.11.2026]: `status.json` of the running daemon shows `"mmio": null` in every state and the 60-second warning ("MMIO copy … below the computed …") never fires, although the copy was enabled at 20 W and `surface-power-report` run as root reads it. Cause: without `CAP_SYS_ADMIN` the kernel shows only the first 64 bytes of a PCI config [C, `pci-sysfs.c`], so reading the MCHBAR at offset 0x48 of 00:00.0 returns 0 bytes; both units (`packaging/systemd/surface-powerd.service`, `…-dryrun.service`) allow only `CAP_DAC_OVERRIDE CAP_SYS_RAWIO`; with `CAP_SYS_ADMIN` added the same sandbox reads the register. The daemon fails silently (`None`), and testers who run the dry-run unit would see no MMIO warning either [H, not checked on their devices] | ✅ | decided 24.11.2026 (maintainer, `specs/kernel-mmio-daemon.md` 8.2, 13): E (log once with the reason) now, C (read the copy through the zone) as the target, B (privileged `ExecStartPre=+` helper) only as an optional temporary aid, A and D not used; implemented in B12 steps 1 and 5. **E and C implemented 24.11.2026** (B12 steps 1 and 5; C: the copy is read from an MMIO zone wherever one exists, so the warning works there under the units): the reason why the copy is not readable is logged once per run and shown in `status.json` (`rapl_copies.mmio_unreadable`) and in `surface-power-report`; address mask bits 41:15 as in the module; tests MB-1 … MB-4, MB-9; not yet checked under the unit on the SB3 (step 8) | **Checked on the SB3 24.11.2026 [M]:** under the unit the reason is logged once (no zone) and the copy is read from the zone (module loaded).
| B12 | **Daemon side of the MMIO path (KM-9)**: choose the MMIO zone for PL1, PL2 and the window where it is writable (module zone with `allow_write=Y`, or the kernel's own zone), MSR copy back at its firmware value and left alone, `enabled=1` only where the copy is off, snapshot and restore of the MMIO copy, bounds, resume, foreign changes, dry-run; spec `specs/kernel-mmio-daemon.md` 0.2, tests MD-1 … MD-24, MB-1 … MB-9 (KM-A7) | ✅ | implemented and tested on a fake sysfs (468 tests) and **checked on the SB3 24.11.2026** (step 8, raw note `b12-sb3-test-2026-11-24.md`): path choice, dry-run without writes, control on the zone (package 9.98 W at a 10 W MMIO PL1 with the MSR copy at 30 W [M]), restore at stop and before sleep, one s2idle cycle; warm restart after a write: firmware value back incl. PL1 clamp (8.8d) [M]; open and moved to D5: devices with an enabled firmware copy (KM-A6, KM-A9) |
| B13 | **Base detached, base sensors lost:** on 19.11.2026 the SB3 base was detached and re-attached; the inputs `2E8B14C2/605` and `/609` stayed invalid and the supervisor's rule "controller inputs invalid for 60 s" put the daemon into `emergency` (firmware values for every output) for 39 minutes until it was stopped [M journal and series, `raw/…/c3-abschlussauswertung-2026-11-24.md` 3]. Wanted (maintainer, 24.11.2026): the way Windows handles it, e.g. only the outputs reached by the lost inputs degrade | ✅ | analysis done 24.11.2026 (raw note `b13-basis-sensoren-2026-11-24.md`, knowledge `knowledge/sb3-detach-and-base.md`): 605/609 reach no power limit, Windows has no device-wide fallback and removes the base client with the base [C]; on 7.3.6-1-surface the base device comes back after about 10 s under another hwmon index [M]. **Implemented 25.11.2026** (22d4f62, `supervisor.md` 4.1): base channels absent instead of lost, looked up again, emergency ends, dGPU stand-in waits for 609; tested on a fake sysfs. **Checked by a real detach 25.11.2026** (daemon in control throughout, 605/609 absent 24 s, back under `hwmon8`, no emergency [M]); the thermal-action rule approved by the maintainer. Next: C6 |
| **C** | **Reference device Surface Book 3 15"** | 🔄 | |
| C1 | Field tests AC-L1–L3 (skin 48 °C, ~32 W, psys 45/100 W) | ✅ | – |
| C2 | PL3/PL4 measured, all 9 outputs written | ✅ | – |
| C3 | Soak test (AC-L4): first dry-run failed, replaced by a control run since 17.11.2026; watchdog incident 18.11.2026 fixed (1a7520a, 829f24e) | ✅ | ended by the maintainer 24.11.2026 15:43 and evaluated (raw note `c3-abschlussauswertung-2026-11-24.md`, separate session; numbers not re-checked in the main session): no daemon crash after the fix, memory ≤ 31 MB, skin controller 1 active 8 times (≤ 49.05 °C at a 48 °C set point); CPU 0.56 % (accepted for now, B8); **not sufficient for C4:** about 20 h awake against 3 days, controllers 2–4 never active, 39 min `emergency` after the base was detached (B13) → C6 |
| C4 | `tested = true` in the SB3 profile | ⬜ | after C6 (not recommended on the C3 data), maintainer's decision |
| C5 | Surface Book 3 13" (shares the profile) | ⏳ 🙋 | tester report with DMI values |
| C6 | **Second soak (control) on the SB3, 24 h, on the MMIO path** (maintainer's decision 25.11.2026: 24 h are enough for now; the module `surface_rapl_mmio` loads at every boot on this machine with `allow_write=1`, through `/etc/modules-load.d` and `/etc/modprobe.d`, signed with the machine's DKMS key; local configuration on the reference device only, no alias, no other device). Criteria as C3 (no crash, no unexplained invalid input, memory < 50 MB; CPU per B8), plus: the path stays `mmio` after reboots and resumes, PL4 after every resume (L8), a base detach in normal use (B13) | 🔄 | **running since 25.11.2026 10:29** (raw `raw/arbeitsordner/c6-2026-11/`; undo `scripts/c6-remove.sh`; a kernel update needs a rebuild of the module); evaluation after 26.11.2026 10:30 |
| **D** | **Kernel interfaces in linux-surface** | 🔄 | |
| D1 | SB3 sensors and fans, main and base ([kernel#190](https://github.com/linux-surface/kernel/pull/190)) | ✅ | merged 17.11.2026 (`v7.3-surface-devel`) |
| D2 | DPTF battery data `0x53` and adapter rating ([kernel#195](https://github.com/linux-surface/kernel/pull/195)) | ✅ | merged 17.11.2026 (`v7.3-surface-devel`) |
| D3 | `pm_prepare` fix of `surface_platform_profile` ([kernel#197](https://github.com/linux-surface/kernel/pull/197)); fixes the suspend bug [linux-surface#2386](https://github.com/linux-surface/linux-surface/issues/2386) | ✅ | merged 16.11.2026 (`v7.3-surface`), released in 7.3.4-2-surface, #2386 closed 17.11.2026 |
| D4 | Kernel attribute for the battery EWMA level and max-charge flag (`28B7BD85/13, 16`; OD-3) | ⬜ | needed by Pro 8, 9, 10, 11 Intel, Laptop 6, 7, Laptop Studio (R3) |
| D5 | **PL1/PL2 through the MMIO copy, as Windows does it, on every Intel device** (decided 23.11.2026): kernel module independent of the daemon, prototype first in this repository (`kernel/`), later a patch for the linux-surface kernel and then mainline (E4); opt-in per proven device; the daemon prefers a writable `intel-rapl-mmio` zone and falls back to MSR (powercap) | 🔄 ⚠️ | module: requirements (`requirements/kernel-modules.md` 0.3), spec (`specs/kernel-mmio-rapl.md` 0.4), evidence (`research/15`), prototype (`kernel/surface-rapl-mmio/`); **tested on the Surface Book 3 15" 23.–24.11.2026 [M]**: read-only and writable loads, restore at unload bit-identical, a constraint write changes only its field, `enabled=1` sets four bits and an 8 W PL1 then holds the package at 8.0 W against the MSR copy's 30 W, enable alone makes a limit effective and clamp alone does nothing (Ice Lake), values from 4096 W wrap silently in the RAPL core, two writers lose no update, the register survives three s2idle cycles, a cold boot restores the firmware value; findings re-checked independently against the raw data on 24.11.2026 (corrections: thermald 2.5.6 does write `enabled=0` to `intel-rapl:0` in adaptive mode; the burst after a resume is the normal PL1 average after idle [H]). Daemon side KM-9 and B11 implemented 24.11.2026 (`specs/kernel-mmio-daemon.md` 0.3, B12 steps 1–7), tested on a fake sysfs only. Checked on the SB3 24.11.2026 (B12 step 8, MMIO PL1 10 W held the package at 9.98 W against the MSR copy's 30 W [M]); the empty-table build refuses to load (KM-A3) [M]; a warm restart after a write brings the firmware value back (DS-5 ends there too) [M]; the module rebuilt and checked for 7.3.6-1-surface; KM-A6/KM-A9 on a device whose firmware enables the copy (Pro 8, 9, 10: enabled [M]; SB3: disabled [M]) |
| D6 | **PL3 (MSR `0x615`, bits 12:0) through a kernel path** (decided 23.11.2026: separate feature, separate patch, same prototype module directory; MSR writes from user space are blocked under lockdown) | ⬜ ⚠️ | with lockdown `none` user space works [M SB3, tester Pro 10]; before building: clarify the Pro 9 EIO on `0x615` (F10, B9); no powercap constraint for PL3 exists, so an own attribute is needed (E5) |
| D7 | EC requests without the debug device: fan outputs, PROCHOT release (SAM), sensor offset | 🔄 | draft PR "fan profile instances" exists; other requests not designed |
| D8 | PROCHOT release on ACPI-EC devices (Go 3, Go 4: ACPI `DPHT`) | ⬜ | small kernel driver on `MSHW0301`, or evidence that it is not needed (I2) |
| D9 | TMP sensor node for `MSHW0250` (Laptop 4 Intel, reported on a 15 inch) in `ssam_node_group_sl3_dptf`, which `MSHW0110` (Laptop 3/4 AMD) shares | ⬜ 🙋 | proof per device needed (mask, names, temperature); the group cannot be split by SKU; no `fan_speed` without proof |
| D10 | Review of [kernel#198](https://github.com/linux-surface/kernel/pull/198) (Laptop Go sensors, fan, `tempX_offset`; neveltyc): not ready by the rules: group `slg1` is shared with Go 2 and Go 3 without proof, the offset frame contradicts the Windows client (H6), ±100 K bound against ±2 K in the corpus | ⏳ | read only, nothing commented; the maintainer decides what to say |
| D11 | TMP sensor nodes for the other groups without one (Pro 7+ `sp7_dptf`, Pro 8 `sp8`, Laptop 7 `sl7`, Studio 1 `sls1`) | ⬜ 🙋 | no evidence yet that the EC of these devices answers the sensor requests; ask for a reading from one tester per device first |
| **E** | **Mainline submission** | ⬜ | |
| E1 | Submit what is merged in linux-surface: SB3 sensor and fan nodes (D1), DPTF battery data and adapter rating (D2) | ⬜ | cover letter draft exists; after some time in a released linux-surface kernel |
| E2 | Battery EWMA level and max-charge attribute (after D4) | ⬜ | with or after E1 |
| E3 | EC requests that replace the debug device (after D7, D8) | ⬜ | – |
| E4 | MMIO RAPL zone without the processor thermal device (after D5) | ⬜ ⚠️ | linux-pm and int340x maintainers; acceptance uncertain, ask early (mail draft exists) |
| E5 | PL3 (MSR `0x615`) through the kernel (after D6) | ⬜ ⚠️ | no powercap constraint for PL3 today; acceptance uncertain, ask early on linux-pm |
| **F** | **Device validation and profiles (community)** | 🔄 | |
| F1 | Tester process: report, dry-run, control, evaluation procedure (`CLAUDE.md`) | ✅ | – |
| F2 | Pro 10: control run (tester); PL1 above the firmware MMIO 28 W has no effect | 🔄 | evaluated 23.11.2026: the lower copy is in force, measured with four MSR values [M]; questions to the tester open; needs D5 |
| F3 | Pro 9 Intel: control run (tester, one SKU) | 🔄 🙋 | 59-hour dry-run evaluated 23.11.2026: stable, CPU 0.98 % over the criterion (B8), MMIO copy 30/60 W enabled (D5), no Windows comparison; consumer SKU added from the catalogue; D4 for R3 |
| F4 | Pro 8: MMIO copy caps at 30 W (tester) | ⏳ | D5, H1 (`0B7B5350/71`), D4 |
| F5 | Profiles for the devices without one: Pro 7+, Pro 10 5G, Pro 11 Intel, Laptop 4 Intel, Laptop 5, Laptop 6, Laptop 7 Intel, Laptop 7 5G, Laptop Go 2, Laptop Go 3, Go 4 | ✅ 🙋 | created 23.11.2026 with catalogue SKUs and `pl3 = true` (3, F5); reports confirm the SKUs |
| F6 | Profiles without a device check: Laptop Go 1, Go 3, Laptop 3/4 AMD, Laptop Studio 1/2 | ⏳ 🙋 | tester reports (Laptop Go 1, Laptop Studio 2: reports in F9) |
| F7 | Go 3: switch on `pl3` in its profile (I1 verified 19.11.2026) | ✅ 🙋 | on since 23.11.2026; a report with `acpidump` confirms the `_DSM` value [H] |
| F8 | Docs and code consistency | 🔄 | `specs/outputs.md` 4 corrected 19.11.2026; keep README, device pages and specs in step |
| F9 | Evaluate the reports of 18.–21.11.2026 in #2383: Pro 10 MMIO answers (technosf, 18.11.), Laptop 4 Intel 15" (hypergol-d, 20.11.), Laptop Go 1 with kernel#198 (neveltyc, 20.11.), Pro 9 59-hour dry-run (NexGen-3D-Printing, 20.11.), Laptop Studio 2 fans (VibeCodingAnimal, 21.11.) | 🔄 | evaluated 23.11.2026 (raw notes `f9-1` … `f9-5`); reply drafts exist, nothing posted; follow-ups: B8–B10, D9, D10, F10, H6, H7, L8 |
| F10 | Pro 9: MSR reads of `0x17`, `0x611`, `0x614`, `0x615` fail with EIO, also after a clean reboot and before the daemon starts [M, tester]; cause unknown | ⏳ 🙋 | questions to the tester (`rdmsr -p 0`, `dmesg`, hypervisor flag, the reading script); B9 |
| **G** | **Inputs to settle** | 🔄 | |
| G1 | Display brightness in millinits (`1FB53D4C/41`, SB3 13"/15"): no source, block default 3 W display power (DV-3); reaches PL4 | ⬜ | where Windows takes the value from (display client), and whether Linux can give nits |
| G2 | Battery EWMA level and max-charge flag (`28B7BD85/13, 16`): debug device only | 🔄 | D4 |
| G3 | EC power data `0x53` and adapter rating: kernel attributes (D2), debug device as fallback | ✅ | in linux-surface; mainline E1 |
| G4 | Constants by design: standby phase (`DEF1AB69/100` = 4, reaches PL1/PL2 on 11 devices), proximity (`87008365/113` = 0, Pro 9 Intel) | 🔄 | Windows behaviour in `specs/inputs.md` 8.5, 8.8; confirm in a Windows comparison on one of these devices |
| G5 | Estimates: USB-C source power (`049E36F2/9`, arm64, psys PL2: upper bound), averaged CPU temperature (`87008365/112`: [C] on the Pro 10 client only) | 🔄 | exact source for USB-C; check `/112` in the clients of the other devices |
| G6 | Not provided and reaching no limit: `049E36F2/2, /6`, `1FB53D4C/32, /43`, `87008365/200` (arm64, Pro 10); `1FB53D4C/10, /21` (Laptop Studio 2, feed `349D0E39` and `/64`) | 🔄 | confirm "reaches no output" per blob; `/10`, `/21` with H5 |
| G7 | dGPU power and temperature through NVML (Laptop Studio 1/2) | ⏳ 🙋 | untested on a device |
| **H** | **Outputs to classify** | ⬜ | |
| H1 | `0B7B5350/71` (Pro 8, Go 3, Laptop Go 2, Laptop Studio 1): no register in the CPU client [C]; receiver unknown | ⬜ | full search over all driver binaries of these devices |
| H2 | `9BBB8910/103–105` (Pro 10 5G, Laptop 7 5G, Pro 9 5G, Pro 11 Snapdragon) and `/200` (arm64) | ⬜ | ACPI `OPTS` list and thermal-policy driver of these devices |
| H3 | `87008365/220, 250, 300` (Surface service): evidence for "not needed" | ⬜ | driver check per device |
| H4 | `B620798F/302–311` temperature thresholds (arm64 Laptop 13", Pro 12") | ⬜ | off unless a profile opts in |
| H5 | Laptop Studio dGPU outputs (`/64, /201, /205, /208`, `349D0E39/1, /4`, `B620798F/202`) | 🔄 | analysis in `research/14`; no Linux path yet |
| H6 | `A79674B4/509` frame: the Windows clients (Pro 10 and Go 1 builds) put the sensor number in the SSAM instance field and in the payload; a tester on a Go 1 measures an effect only with instance 0 [M]; the effect of the offset is measured on no device (DV-11) | ⬜ | questions to the tester; read the serial-hub layer of the Windows client; no code change before |
| H7 | Unit of `B620798F/111–113`: documented as SPL "[H: dB(A)]"; a tester's decode of the Laptop Studio 2 EC says `FAN 0x0C` is a target-temperature override in °C (f32) [H, tester]; the bytes sent are the same either way | ⬜ | documentation only; ask for a measured effect |
| **I** | **Go 3 / Go 4 (ACPI EC)** | 🔄 | |
| I1 | CPU platform type from ACPI `_DSM` (PL3 target) | ✅ | Go 3 → 1, Go 4 → 5, both MSR `0x615` [C, public DSDT dumps + client `0d5efa50`, 19.11.2026]; same value on current firmware [H] |
| I2 | `O_BATT_MODE` (`0B7B5350/70`): Windows calls ACPI `DPHT` → EC command `0x36` "de-assert PROCHOT" [C, DSDT]; no user-space path | ⬜ | D8 |
| **J** | **AMD (Laptop 3 AMD, Laptop 4 AMD)** | ⬜ | |
| J1 | What the AMD SMF driver does with `0B7B5350/51–53`, `/70`, `CF2F6076/61` | ⬜ | driver analysis [no research yet] |
| J2 | Linux write path for the AMD limits | ⬜ 🙋 | after J1; owners of these devices for tests |
| **K** | **arm64 Snapdragon (L3): Pro 9 5G, Pro 11, Laptop 7, Laptop 13", Pro 12"** | ⬜ | |
| K1 | Throttle requests as cpufreq and GPU caps (implemented) on a device | ⏳ 🙋 | tester with Linux on the device |
| K2 | `O_SYS_AVG` / `O_SYS_MAX` (`0B7B5350/54, 55`) and `/70`: Linux path | ⬜ | analysis of the Qualcomm client |
| K3 | USB-C source power: exact value instead of the upper bound (G5) | ⬜ | – |
| **L** | **Production and rollout** | 🔄 | |
| L1 | **Python reference complete:** every device class has its paths (B, D, G–K), B7 passes for every profile, specs v1.0 | ⬜ | gates the production version |
| L2 | Production version in **Rust or C** (OD-2, decision open), ported 1:1 against the same specs and tests | ⬜ | after L1 |
| L3 | Installer and systemd units (control, dry-run next to thermald) | ✅ | – |
| L4 | Coexistence with thermald, power-profiles-daemon, tuned | 🔄 | thermald handled (`Conflicts=`, `After=`); tuned restarts itself by D-Bus on Bazzite-SDX (six times in 59 h, tester): stopping is not enough, it has to be masked; ppd not checked |
| L5 | Distribution packages (deb, rpm, Arch) and a linux-surface repository entry | ⬜ | – |
| L6 | Integration with linux-surface (`surface-control` or own service) | ⏳ | talks with the maintainers in kernel#117 / #2383 |
| L7 | Release process: versioning, changelog, supported-device list (FR-D3), AC-S1–S8 on hardware | ⬜ | – |
| L8 | Coexistence with `surface-rapl.service` (package `surface-control`): after every resume it writes `0` to `constraint_2_power_limit_uw` (PL4), the daemon reports a foreign change "58.6 W → 0 W" [C script, M journal 21.11.]; whether PL4 is restored afterwards is not shown | ⬜ | decision: order or conflict with the unit, or write PL4 again after resume; check on 23.–25.11. with `cat constraint_2_power_limit_uw` after a resume |

## 3. Work packages in detail

### A. Foundations ✅
Research summaries `research/01`–`08`, requirements v0.2, acceptance criteria v0.1, architecture v0.3, specs v0.1. Done when the SMF
behaviour of the SB3 is explained and specified (reached 14.11.2026). Open: requirements v0.3 with the principles of section 1.

### B. Reference implementation 🔄
- **Done:** loader and engine for every block type of the corpus (all 41 SMF blobs build, `research/09`), adapters for every input
  (`specs/inputs.md` 8), supervisor with "control only when every output is written" (`specs/supervisor.md` 2.6), monitor, tools,
  416 unit tests (19.11.2026), CI repo check (AC-X1).
- **Open (B3, B4):** every adapter that the SB3 does not use is tested on fakes only. They are validated device by device in F; a
  finding on one device does not carry over to others.
- **B7 safety suite.** Today there is a heat-up sweep over all blobs (finite outputs, one controller becomes active;
  `tests/test_local_sb3.py`) and a closed-loop model of the SB3 only (`scripts/simulate-sb3.py`). B7 runs every profile and every blob
  of its device through extreme scenarios and checks fixed criteria. **The criteria are to be decided with the maintainer**; proposal:
  - *Scenarios:* each slider state × AC/battery × posture; ambient 10–40 °C; sustained full load and load steps; charger plug and
    unplug, weak charger; each input invalid on its own and all EC inputs invalid; sensor stuck high and low; suspend/resume in the
    middle of a controller cut-in; foreign writes.
  - *Open-loop criteria:* every write inside the blob range and the hard limits (AC-I4); a rising skin or CPU temperature never raises
    a limit; above each controller's set point the limit falls; `O_HOT`/`O_CRT` trip at the blob's thresholds; lost inputs give the
    block defaults or firmware values, never a higher limit.
  - *Closed-loop criteria* (a generic two-time-constant plant per device class, parameters as worst case [H until measured]): skin
    temperature settles at or below the blob's set point plus a margin; no sustained oscillation; CPU at TjMax no longer than Windows
    allows on the SB3 measurement.
  - **Decided 19.11.2026:** these criteria are agreed. The suite is a local script (`scripts/`), run by hand from time to time
    against the local corpus. It is not part of the unit tests and not run in CI (GitHub Actions) for now; that can come later.
  - Done when the criteria have numbers in `requirements/acceptance-criteria.md`, the script exists and every profile passes.

- **B11/B12: implementation plan (decided 24.11.2026).** Spec `specs/kernel-mmio-daemon.md` 0.2; each step writes its tests first (section 12 of the spec),
  then the code, with the maintainer's yes before the code and one commit per step; all 416 existing tests stay green.
  1. ✅ (24.11.2026) **B11-E:** `rapl_copies.read_mmio()` returns the reason with `None`; one log line per run; `status.json` `rapl_copies.mmio_unreadable`; one address mask
     (bits 41:15). Tests MB-1 … MB-4, MB-9 (the existing silent-when-unreadable test changes).
  2. ✅ (24.11.2026) **Fake sysfs:** MMIO zone, module parameter file, optional 00:04.0, fake register; no change of behaviour.
  3. ✅ (24.11.2026) **Path choice and status:** spec 2 and 9, dry-run shows the path. Tests MD-1 … MD-6, MD-23.
  4. ✅ (24.11.2026) **Writing:** MSR copy back at its firmware value (3), first write and `enabled=1` (4.1, 4.2), window on the MMIO zone and the time-unit check (4.3),
     bounds (6). Tests MD-7 … MD-12, MD-18.
  5. ✅ (24.11.2026) **Snapshot, restore, reading through the zone:** spec 5 including `surface-power-reset`, 8.3 C, the warning about a lower MSR copy. Tests MD-13 … MD-17,
     MD-24, MB-8.
  6. ✅ (24.11.2026) **Foreign changes, lost zone, resume:** spec 10, 2.6, 7. Tests MD-19 … MD-22.
  7. ✅ (24.11.2026) **Documents to "implemented":** `outputs.md` 3.3, `supervisor.md`, `monitoring.md`, `tools.md` (the report shows the PL path), `deviations.md` DV-8, this plan.
  8. ✅ (24.11.2026, raw note `b12-sb3-test-2026-11-24.md`) **Hardware checks on the SB3** (the maintainer's yes before each sub-step, `sudo` in a terminal tab, module rebuilt and signed, `knowledge/sb3-test-practice.md`):
     dry-run with the module zone (path `mmio`, copy read from the zone under the unit); control on the `mmio` path with a narrowed PL1 below the MSR copy
     (`outputs.narrow`) and the effect measured; restore at stop and before sleep (register against the snapshot, the residual clamp line); one suspend cycle
     with the daemon. In the same session the open module tests: a warm restart after a write, the burst test without suspend, a build with an empty DMI table
     (KM-A3).
  9. **Optional, temporary:** B (`ExecStartPre=+` helper) for diagnosis on devices without a zone, only if the maintainer asks for it.
  Then: KM-A6/KM-A9 on a Pro 8, 9 or 10 (tester, 🙋), the tester's module source, linux-pm (E4).

### C. Reference device 🔄
- **C3:** the control run 17.–24.11.2026 is evaluated (raw note `c3-abschlussauswertung-2026-11-24.md`); criteria were: no crash, no unexplained
  invalid input, CPU < 0.5 % and memory < 50 MB over the period (AC-L4 criteria applied to control). Too short and too little load for C4.
- **C6:** the same criteria over three awake days, after B12 and B13; CPU 0.56 % is accepted for now (maintainer, 24.11.2026, B8 stays open).
- **C4:** the maintainer decides on `tested = true` after C6.
- **C5:** the 13" shares the ACPI ID (MSHW0117) and the profile (match on `product_name`); its DMI values are not confirmed by a report.

### D. Kernel interfaces in linux-surface 🔄
Rule (`CLAUDE.md`): every change proven 100 % (command, instance, payload, affected devices); support properties only for proven
devices; only the maintainer signs off.
- **D4:** EC battery requests `0x1A` / `0x69` as attributes next to the `dptf` group. Done when merged in linux-surface.
- **Decided 23.11.2026 (D5, D6):**
  - MMIO is the preferred write path for PL1/PL2 on Intel devices, because Windows writes it on every Intel platform type [C]; MSR (powercap) stays the fallback. Windows leaves the MSR copy at the firmware value [C: no blob uses channels 56/57], so parity means writing MMIO only and not touching the MSR copy; writing both would lift the firmware's own MSR limit, which is a new deviation and needs its own proof.
  - The module is independent of the daemon (the daemon only uses what powercap shows), is opt-in per device with a DMI match and a proof per platform type (MCHBAR base, register offsets, enable and clamp bits; `CLAUDE.md` kernel rules), and is meant for linux-surface first (as kernel#190/#195 were), then mainline. Where `00:04.0` exists the kernel already provides `intel-rapl-mmio` and no module is needed.
  - PL3 is a separate feature (other register, other subsystem, other upstream question) and a separate patch, so a rejection of one does not block the other.
  - The prototype lives in this repository (`kernel/`, license GPL-2.0-only because it uses GPL-only kernel interfaces, stated in the README next to MIT/Apache) so testers can build it. License: only `kernel/` is GPL-2.0-only, the rest of the repository stays MIT OR Apache-2.0 (decided 23.11.2026). It does **not** meet gate R5 (Secure Boot without extra setup): a tester needs their own signing until it is in the linux-surface kernel. Kernel rules on AI-assisted patches and the maintainer's sign-off must be checked before any submission [H, not verified].
- **D5 history:** the Pro 8 and Pro 10 testers measured that the lower enabled copy is in force (DV-8 [M on two devices]). A kernel path that
  creates `intel-rapl-mmio` from MCHBAR without 00:04.0 is the direction [H]; a tester has such a module (not loaded yet). Done when a
  module or kernel patch is in the linux-surface kernel (signed with it, so no own MOK), is checked against the Windows target
  (MCHBAR + 0x59a0, bits 14:0, enable and clamp) and is measured on at least one device with an enabled MMIO copy.
- **D6:** under lockdown `integrity` or `confidentiality` user-space MSR writes are refused (`msr.c`, LOCKDOWN_MSR). Which distribution
  kernels switch lockdown on with Secure Boot is [H] (the linux-surface kernel on the SB3 runs with Secure Boot on and lockdown `none`). Without a
  kernel path PL3 stays log-only there and R4/R5 fail on those systems.
- **D7, D8:** today fans, PROCHOT release and sensor offset go through `/dev/surface/aggregator` (interim, NFR-11); on Go 3/4 the PROCHOT
  release is an ACPI method that user space cannot call.

- **D9–D11 (sensor nodes):** per `CLAUDE.md` every registry change needs command, instance, payload and all devices of the group. A report exists for one device (Laptop 4 Intel 15", mask `0x07f6`, `I_VTS1` = sensor 9). `ssam_node_group_sl3_dptf` is shared by `MSHW0110` and `MSHW0250`, `ssam_node_group_slg1` by Go 1, 2 and 3. Evidence for a device is a reading from that device, not the Windows driver (the blob reads the sensors, the EC answer on Linux is what is missing).
- **D10 (kernel#198):** read only. Findings: group shared with Go 2/3 without proof; the offset frame contradicts the Windows client (H6); bound ±100 K on every sensor against ±2 K (sensor 9 only) in the corpus; the EC's own protection may use the offset-shifted value [H]; offsets are reset only when the driver is removed.

### E. Mainline ⬜
Order: what is merged and stable first (E1, E2), then the EC requests (E3). E4 and E5 touch the Intel RAPL and int340x code, whose
maintainers decide on new zones and constraints; their acceptance is uncertain (⚠️). Plan for both outcomes: ask on linux-pm early
with the measurements, and keep the linux-surface patch as the path for linux-surface users if mainline says no. The goal of "no setup
on mainline kernels" (section 1) is reached only for what mainline accepts.

### F. Device validation 🔄
Each report is evaluated with the procedure in `CLAUDE.md` (steps 0–12), nothing by analogy to another device. Done per device when
gates R2–R8 hold (section 5).

**F5: why these devices have no profile.** Not because something is technically missing: they use the `direct` controller variant,
which is the default without a profile, so the engine runs correctly without one. A profile is needed for the options (above all
`pl3 = true`; Windows' PL3 write is verified in the driver of each of these devices) and for `tested = true`. The only missing piece is
the DMI match: by the project rule, DMI values come from a tester report or a proven source, never from the drivers (the packages do
not contain complete SKUs). The three SKU profiles (Laptop Go 1, Laptop 3/4 AMD) come from a public catalogue of the Surface driver
packs that agrees with the kernel's own matches (`research/09` 3).

**Decided 19.11.2026:** profiles are created now, without waiting for reports, from what is proven about each device:
- **DMI match** from a proven source: the public driver-pack catalogue (SKU), the product names the kernel and linux-surface match on,
  or a tester report. The source is named in the profile. Where two models share a product name (Laptop 4 Intel/AMD), match by SKU.
- **Options only where the blob uses the output and the driver of this device proves the target** (platform type, descriptor
  table, register, bits, unit; `CLAUDE.md` step 8). Nothing by analogy to another device.
- `tested = false` until a report confirms the DMI values and R7 is met; the daemon stays in dry-run by default there.

**Done 23.11.2026:** eleven new profiles (Pro 7+, Pro 10 5G, Pro 11 Intel, Laptop 4 Intel, Laptop 5, Laptop 6, Laptop 7 Intel,
Laptop 7 5G, Laptop Go 2, Laptop Go 3, Go 4), all matched by the SKUs of the OSDeploy catalogue (commit `1fcc2829`), each with the
source per SKU in the file; `pl3 = true` on each from the PL3 evidence in the device's own driver (`research/10` 4.1), and on the
Go 3 (F7). The Pro 9 profile also matches the catalogue's consumer SKU. Confirmed by the kernel or user reports: Pro 7+ 1960, Pro 11
Intel 2103, Laptop 4 1950:1951 and 1978:1979, Laptop 5 1950:1951 and 1979, Laptop 7 Intel 2107; the other SKUs are catalogue only.
No other option (PL4 handling, dGPU, fan) was set: none is proven for these devices. With this, seven more devices write every
output of their blob (R4): Pro 7+, Pro 11 Intel, Laptop 4 Intel, Laptop 5, Laptop 6, Laptop 7 Intel, Laptop Go 3.

### G, H. Inputs and outputs to settle 🔄
Each input without a Windows-equal source and each output without a Linux path blocks R3 or R4 on its devices. Per item: complete
search over all driver binaries of the affected devices, then either a Linux path (spec, tests, code, device check) or a documented
substitute or "not needed on Linux" with evidence (`research/12`, `specs/deviations.md`).

### I–K. Device classes without a write path
- **I (Go 3/4):** the PL3 target is settled (I1, details in `research/10` 4.1). Left: `O_BATT_MODE` through `DPHT` (I2 → D8).
- **J (AMD):** no analysis of the AMD SMF driver exists yet; powercap has no AMD limits.
- **K (Snapdragon):** needs Linux running on these devices at all; testers wanted.

### L. Production 🔄
The Python version stays the reference until L1 is reached. Then the production language is decided (Rust or C, OD-2) and the code is
ported 1:1 against the same specs and tests. Packaging and the linux-surface integration can start earlier with the Python version.

## 4. Help wanted 🙋

What contributors can do now (details for testers: README "Try it on your Surface"; reports go to
[linux-surface#2383](https://github.com/linux-surface/linux-surface/issues/2383)):
- **Reports that confirm the new profiles** (F5): Pro 7+, Pro 10 5G, Pro 11 Intel, Laptop 5, Laptop 6, Laptop 7 Intel, Laptop 7 5G,
  Laptop Go 2, Laptop Go 3, Go 4, and the Book 3 13" (C5). Most of their SKUs come from a catalogue; the report confirms them.
- **Reports and dry-runs from devices with an untested profile** (F6): Laptop Go 1, Go 3 (with `acpidump`), Laptop 3/4 AMD, Laptop Studio 1/2, further Pro 9 SKUs.
- **Windows comparisons** (HWiNFO PL1/PL2 per mode and source) on any SMF device (R7).
- **MMIO write path** (D5): tests of a module on a Pro 8, Pro 9 or Pro 10 with an enabled MMIO copy (the tester's module on the Pro 10, once its source has been seen).
- **Sensor readings** (D9–D11): from a Pro 7+, Pro 8, Laptop 3/4 AMD, Laptop 4 Intel 13 inch, Laptop 7, Laptop Go 2/3 or Laptop Studio 1: does the EC answer the TMP sensor requests (a few read-only requests through the debug device)?
- **Pro 9:** the output of `rdmsr` for `0x17`, `0x611`, `0x615`, `dmesg | grep -i msr` (F10).
- **Linux on Snapdragon Surfaces** (K) and owners of the AMD Laptops (J2).
- **Kernel review** of the linux-surface patches before the mainline submission (E).

## 5. Rollout matrix per device

Gates as in 1.1. R1 is met on every device. R6 waits for the safety suite (B7) on every device; the SB3 15" has only its closed-loop
model (`scripts/simulate-sb3.py`) so far. Source: device pages (23.11.2026), profiles, tester reports in #2383. R3 corrected on 23.11.2026: the kernel registry has no TMP sensor node for several devices (DV-10), so their temperature inputs are invalid on Linux until nodes exist.

| Device | R1 Blob | R2 Profile | R3 Inputs | R4 Outputs | R5 Secure Boot | R6 Safety sim | R7 Validation | R8 | 🙋 | Blocking WPs |
|---|---|---|---|---|---|---|---|---|---|---|
| Book 3 15" | ✅ | ✅ | 🔄 brightness fallback (G1) | ✅ 9/9 | 🔄 PL3 needs lockdown `none` | 🔄 closed-loop model only | ✅ field tests, soak C3 not sufficient, C6 ⬜ | ⬜ | – | C6, C4, B13, D6, G1, B8, L8 |
| Book 3 13" | ✅ | 🔄 shared, DMI unconfirmed | 🔄 (G1) | ✅ 9/9 | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | C5 |
| Pro 7+ | ✅ | 🔄 catalogue SKUs, 1960 confirmed | 🔄 no sensor node in the kernel (D11) | ✅ 12/12 | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D11 |
| Pro 8 | ✅ | ✅ | 🔄 debug device; no sensor node (D11) | ⬜ 13/14 (`/71`) | ⬜ MMIO caps at 30 W | ⬜ | 🔄 reads by tester | ⬜ | – | D4, D5, D11, H1 |
| Pro 9 Intel | ✅ | 🔄 report SKU + catalogue consumer SKU | 🔄 debug device, proximity constant | ✅ | ⬜ MMIO 30/60 W enabled, caps Slider 5 at 30 W [H] | ⬜ | 🔄 control run, 59-h dry-run evaluated (CPU 0.98 % > 0.5 %), no Windows comparison | ⬜ | 🙋 | D4, D5, B8, F3, F10, G4 |
| Pro 9 5G | ✅ | ⬜ | ✅ | ⬜ (`9BBB8910/103`) | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | H2, K |
| Pro 10 | ✅ | ✅ | 🔄 debug device | ✅ | ⬜ MMIO caps at 28 W | ⬜ | 🔄 control run; MMIO series evaluated 23.11. | ⬜ | – | D4, D5, F2 |
| Pro 10 5G | ✅ | 🔄 catalogue SKUs | 🔄 | ⬜ (`/103–105`) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | H2 |
| Pro 11 Intel | ✅ | ✅ SKU (catalogue, user reports) | 🔄 | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D4 |
| Pro 11 Snapdragon | ✅ | ⬜ | 🔄 (estimate) | ⬜ 11/20 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | H2, H3, K |
| Pro 12" 1st Ed. | ✅ | ⬜ | 🔄 (estimate) | ⬜ 6/16 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | H2, H3, H4, K |
| Laptop 3 AMD | ✅ | ✅ SKU | 🔄 no sensor node in the kernel (D9) | ⬜ 2/5 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | D9, J |
| Laptop 4 AMD | ✅ | ✅ SKU | 🔄 no sensor node in the kernel (D9) | ⬜ 3/8 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | D9, J |
| Laptop 4 Intel | ✅ | ✅ SKUs (kernel, tester report) | 🔄 no sensor node in the kernel (D9); debug device for EC data | ✅ 12/12 | 🔄 | ⬜ | 🔄 report 20.11. evaluated; EC clamp seen at 52.1 °C, no control run on a linux-surface kernel | ⬜ | – | D9, B10 |
| Laptop 5 | ✅ | 🔄 catalogue SKUs, consumer ones confirmed | 🔄 standby constant | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | G4 |
| Laptop 6 | ✅ | 🔄 catalogue SKUs | 🔄 | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D4 |
| Laptop 7 Intel | ✅ | 🔄 catalogue SKUs, 2107 confirmed | 🔄 no sensor node (D11) | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D4, D11 |
| Laptop 7 5G | ✅ | 🔄 catalogue SKU | 🔄 | ⬜ (`/103–105`) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | H2 |
| Laptop 7 Snapdragon | ✅ | ⬜ | 🔄 (estimate) | ⬜ 11/18 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | H2, H3, K |
| Laptop 13" | ✅ | ⬜ | 🔄 (estimate) | ⬜ 11/25 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | H2, H3, H4, K |
| Laptop Go 1 | ✅ | ✅ SKU | 🔄 no sensor node in the kernel (D10) | ✅ 10/10 | 🔄 | ⬜ | 🔄 report 20.11. evaluated (kernel#198, own controller, no daemon run) | ⬜ | – | D10, H6 |
| Laptop Go 2 | ✅ | 🔄 catalogue SKU | 🔄 no sensor node in the kernel (D10) | ⬜ 13/14 (`/71`) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D10, H1 |
| Laptop Go 3 | ✅ | 🔄 catalogue SKU | 🔄 standby constant; no sensor node (D10) | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D10, G4 |
| Go 3 | ✅ | 🔄 product name [H] | ✅ | ⬜ 10/12 (`/70`, `/71`) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | H1, I2 |
| Go 4 | ✅ | 🔄 catalogue SKU | 🔄 standby constant | ⬜ 11/13 (`/70`) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | I2 |
| Laptop Studio 1 | ✅ | 🔄 product name | 🔄 debug device, NVML; no sensor node (D11) | ⬜ (`/71`, dGPU) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D4, D11, G7, H1, H5 |
| Laptop Studio 2 | ✅ | 🔄 product name | 🔄 debug device, NVML | ⬜ (dGPU, `349D0E39`) | 🔄 | ⬜ | 🔄 fan report 21.11. evaluated: `FAN 0x0B` per fan measured [M], no daemon run | ⬜ | 🙋 | D4, G7, H5, H7 |

Since 23.11.2026 every x64 SMF device has a profile; "catalogue SKU" in R2 means the match is not yet confirmed by a report from the
device. R5 is 🔄 on every Intel device as long as PL3 needs lockdown `none` (D6), the fan and PROCHOT
requests need the debug device (D7) and the MMIO copy has not been read on that device (D5).

## 6. Critical path and next steps

In this order; each step names its work package. **Changed on 23.11.2026 (1.4)** after the evaluation of the reports (F9): the SB3
soak is extended to 25.11. (maintainer); the MMIO copy is enabled on a third device (Pro 9), which keeps D5 second; two checks of
the daemon itself (B8, B9, B10) come before the safety suite because they decide what a tester may be asked to run; the sensor nodes
(D9–D11) and the offset frame (H6) are new and come after the F9 follow-ups.

1. **B13, then C6/C4:** C3 ended 24.11.2026 without enough data for C4 (about 20 h awake, three controllers never active, `emergency`
   after a base detach). The MMIO path is checked on the SB3 (B12, 24.11.2026); the base-detach fix is checked (B13, 25.11.2026); the second soak (C6, 24 h on the MMIO path) runs since 25.11.2026 10:29 and the decision on
   `tested = true` (C4). CPU (B8) is accepted for now.
2. **F2/D5:** the prototype module is built and tested on the SB3 (23.–24.11.2026, `specs/kernel-mmio-rapl.md` 8), the daemon side KM-9 and B11 is
   implemented and checked on the SB3 (`specs/kernel-mmio-daemon.md` 0.3, B12, 24.11.2026). Next: a read-only test of the module on a device with an enabled copy (KM-A9,
   tester), the tester's module source, MCHBAR and offsets per platform type for the devices beyond the SB3; ask linux-pm early (E4). PL3 (D6) follows
   as a separate feature after F10/B9. This unblocks the Pro 8, Pro 9, Pro 10 and every device whose firmware enables the MMIO copy.
3. **B8, B9, B10, B11:** measure where the CPU time goes (about 1 % on two devices against 0.5 %), check what happens when MSR `0x615` is not
   readable, make the report say what the daemon does. Small code changes, each needs the maintainer's yes.
4. **B7:** fix the numbers of the safety criteria, then build the suite; from then on no tester is asked to run control on a profile that
   fails it.
5. **F9 follow-ups:** reply to the five reports (drafts exist), ask the questions (F10, H6, H7), ask for reports that confirm the new
   profiles (🙋).
6. **D9–D11, D10:** sensor nodes with proof per device; tell the author of kernel#198 what the rules need (maintainer decides).
7. **D4, E1:** battery EWMA attribute in linux-surface; submit the merged patches to mainline.
8. **H6, G1, H1–H3, H7:** offset frame, brightness in nits, classify the remaining outputs.
9. **D6/E5, D7/E3, D8:** PL3 with lockdown, EC requests without the debug device, `DPHT` on Go 3/4.
10. **J, K:** AMD and Snapdragon paths (driver analysis first).
11. **L1 → L2, L5–L8:** Python reference complete, then Rust or C, packages, linux-surface integration, release.

## 7. Keeping this plan current

- Read this plan **first** in every session, before `raw/arbeitsordner/STAND-UND-PLAN.md`.
- It is updated **in the same commit** as any change of state: a work package started or finished, a gate of a device met, a tester
  report evaluated, a PR merged, a new blocker or a new help-wanted item. The README status, plan summary and device table, the device
  pages and `STAND-UND-PLAN.md` change in the same step (`CLAUDE.md`, procedure step 12).
- New work gets a work-package ID in the matching group; finished packages stay in the table with ✅ (no renumbering from 1.1 on).
- Every status claim needs its evidence (commit, measurement, driver, PR); what is not proven is marked [H].
- The version and date at the top and the change log below are updated with each change.

## 8. Change log

| Date | Change |
|---|---|
| 19.11.2026 | 1.0: plan written from the state of the repository, the working notes and #2383. |
| 19.11.2026 | 1.1: emoji status and help-wanted markers; mainline as its own group (E) with the hard parts marked; suspend bug fixed (D3); inputs as their own group (G); Go 3/4 platform type settled from the public ACPI dumps (I1); safety suite per device (B7, gate R6); Secure Boot without extra setup as principle and gate R5; production version Rust or C after the Python reference (L1, L2); why devices have no profile (F5). Groups renamed from 1.0 (F→H, G→I, H→J, I→K, J→L). |
| 19.11.2026 | 1.2: rollout matrix with R1 and R6; profiles are created from proven sources without waiting for reports (F5); safety-suite criteria agreed, run locally, not in CI for now (B7). |
| 23.11.2026 | 1.3: eleven new profiles from catalogue SKUs with `pl3 = true` (F5 ✅), PL3 on the Go 3 (F7 ✅), Pro 9 consumer SKU; seven more devices write every output (matrix R2/R4); new reports of 18.–21.11. to evaluate (F9). |
| 23.11.2026 | 1.4: reports of 18.–21.11. evaluated (F9, F2, F3): Pro 10 lower-copy measurement and module design checked against kernel and driver (D5); Pro 9 stability, MMIO 30/60 W enabled, MSR EIO (F10); Laptop 4 Intel registry gap (D9) and report/daemon mismatch (B10); kernel#198 reviewed read-only (D10), offset frame contradiction (H6); Laptop Studio 2 fan report (H7); daemon CPU ≈1 % on two devices (B8); PL3 EIO behaviour (B9); `surface-rapl.service` (L8); tuned must be masked (L4); sensor nodes missing for several devices, R3 corrected in the matrix (DV-10, D11); SB3 soak extended to 25.11. (C3). Critical path reordered, reasons in section 6. |
| 23.11.2026 | 1.5: decisions of the maintainer: MMIO is the preferred PL1/PL2 path on Intel devices with MSR as fallback, via a daemon-independent module (prototype in `kernel/`, later linux-surface and mainline), opt-in per proven device; PL3 as a separate feature and patch (D5, D6, section 6 step 2). |
| 23.11.2026 | 1.6: D5 prepared for development: requirements and concept (`requirements/kernel-modules.md`), spec 0.2, evidence summary `research/15`, module skeleton in `kernel/`, independent review done (findings applied: restore at unload, bit-group bounds, locking, thermald as open point, KM-11 … KM-13); the kernel's AI-assistance rule is adopted for kernel contributions (`CLAUDE.md` "Kernel modules"), `kernel/` carries its own GPL-2.0-only license text; licence of the rest of the repository stays MIT OR Apache-2.0 (maintainer, 23.11.2026; revisit if the daemon itself is proposed to linux-surface). |
| 23.11.2026 | 1.7: D5: test steps 0–3 of the module on the Surface Book 3 15 inch passed (read-only, signed with the machine's own DKMS key because Secure Boot refuses an unsigned module; zone equals the registers, MMIO energy counter runs, register bit-identical after three unloads, wrong platform type refused); new facts: PL1 clamp is 1 and both lock bits are 0 in the SB3's disabled copy; a refused write is not logged (KM-7, fixed). Evening: with `allow_write=1` a limit write changes only the PL1 field, `enabled=1` sets four bits and the MMIO PL1 takes effect against the MSR copy (package 13 W → 8.0 W; the 13 W is the platform limit psys 25/40 W that applies without the daemon), unload restores the register bit-identical; open: s2idle, cold boot, concurrent writers, a device with an enabled copy, the daemon side. |
| 24.11.2026 | 1.8: D5 tests on the SB3 continued (windows, PL2, refusals, concurrent writers, enable/clamp matrix, suspend with the copy enabled, daemon next to the zone); new B11: the daemon's own MMIO check does not work under its systemd units (no `CAP_SYS_ADMIN` for the PCI config read), found in test 7. |
| 24.11.2026 | 1.9: independent re-check of the SB3 module tests against the raw data (raw note `review-opus-sb3-tests-2026-11-24.md`): findings 1–6 and 8 confirmed, thermald corrected for the installed 2.5.6 (adaptive mode writes `enabled=0` to `intel-rapl:0`), burst after a resume explained as the normal PL1 average [H], B11 cause proven in the kernel source (64-byte PCI config view); `specs/kernel-mmio-rapl.md` 0.4 (consistent sections 5.3–5.7, 7, 8, 10); new `specs/kernel-mmio-daemon.md` 0.1 (KM-9 and B11 with options, tests MD/MB, decisions for the maintainer); `requirements/kernel-modules.md` 0.3 (state per KM-A criterion), `deviations.md` 0.2 (DV-8, DS-5), `research/15`, `outputs.md` 3.3, `supervisor.md` pointers. B11 🔄 (spec). |
| 24.11.2026 | 1.10: decisions of the maintainer on the daemon side (`specs/kernel-mmio-daemon.md` 0.2, 13): B11 = log once + read through the zone, the privileged helper only as an optional temporary aid; the kernel's own MMIO zone is used automatically; the residual clamp after a restore is accepted (DS-5). New work package B12 (daemon side, KM-9) with an implementation plan in nine steps (3). Knowledge base `docs/knowledge/` started (power-limit facts, SB3 test practice); `CLAUDE.md` keeps working rules only. |
| 24.11.2026 | 1.11: B12 step 1 (B11-E) implemented: the daemon logs once per run why the MMIO copy is not readable and puts the reason into `status.json`; the report names it; one address mask (bits 41:15) in the daemon and `scripts/mmio-pl-read.py`; tests MB-1 … MB-4, MB-9 (423 tests). B12 🔄. |
| 24.11.2026 | 1.12: B12 step 2: the fake sysfs of the tests has an MMIO zone (module or kernel), the module parameter, the MSR file and a fake `/dev/mem` register read through the real path; no change of behaviour (427 tests). |
| 24.11.2026 | 1.13: B12 step 3: the daemon chooses the PL path at start (`adapters/plpath.py`, spec 2): `mmio` with a writable module zone or the kernel's zone, no failure marker and no `outputs.mmio = false`; PL1, PL2 and the window then target the MMIO zone, PL4 and psys stay on the MSR zones; path and reason in the log and `status.json`; a writable zone that appears later is logged once (2.5); tests MD-1 … MD-6, MD-23 (436 tests). |
| 24.11.2026 | 1.14: B12 step 4: writing on the mmio path (spec 3, 4, 6): MSR copy back at its snapshot value once before the first MMIO write, window → PL1 → PL2 → `enabled=1` where the firmware copy was off, read back; a failed write or an enable that does not stick restores, creates the marker and exits with an error; time unit checked (window log-only if not 10); hard limits logged once when they cut. Fixed on both paths: a window change of one step (e.g. 28 → 32 s, 14 %) was not written because the read-back tolerance (15 %) was also used as the change threshold. Tests MD-7 … MD-12, MD-18 (448 tests). |
| 24.11.2026 | 1.15: B12 step 5: snapshot with the MMIO keys and the raw register (info only), predecessor kept and missing keys added, restore and `surface-power-reset` switch an MMIO copy that was off off first, then window, PL1, PL2, never touch an `enabled` that was 1, skip a zone that is gone; residual clamp (DS-5) logged once; the 60-second check reads the MMIO copy from the zone where the register is not readable (C, source in `status.json`), and on the mmio path warns about a lower MSR copy instead. Tests MD-13 … MD-17, MD-24, MB-8 (460 tests). |
| 24.11.2026 | 1.16: B12 step 6: on the mmio path `enabled` reading 0 after the daemon set it and a changed MSR copy (PL1, PL2, window) are foreign changes, overwritten, the third in 60 s yields; a zone that disappears ends the run with an error and without the marker (the restart chooses again), no spurious foreign-change line; sleep restores the MMIO copy and the first cycle after the resume writes it and `enabled=1` again. Tests MD-19 … MD-22 (465 tests). |
| 24.11.2026 | 1.17: B12 step 7: specs and documents on "implemented": `kernel-mmio-daemon.md` 0.3 (state, section 15 implementation notes), `outputs.md` 3.3, `supervisor.md` 3, 5, 6, 7 (profile option `outputs.mmio`), `monitoring.md` (`pl_path`, `rapl_copies`), `tools.md` and the report (PL path and the copy from the zone), `deviations.md` 0.3 (DV-8, DS-5), knowledge base, KM-A7 ✅; B11 E and C implemented; README. |
| 24.11.2026 | 1.18: C3 ended by the maintainer and evaluated (not sufficient for C4: about 20 h awake, controllers 2–4 never active, 39 min `emergency` after a base detach); CPU 0.56 % accepted for now, B8 stays as a later optimisation; new B13 (base detached, sensors 605/609 lost; analysis started) and C6 (second soak after B12 and B13); C4 after C6. |
| 24.11.2026 | 1.19: B12 step 8 on the SB3 done (raw note `b12-sb3-test-2026-11-24.md`): B11 ✅ and B12 ✅ on the device; the empty-table build refuses to load (KM-A3); log line for a narrowed limit corrected; the warm restart after a write was not conclusive (the daemon ran in dry-run before the reboot) and stays open under D5; kernel 7.3.6-1-surface with built-in Surface modules running. |
| 24.11.2026 | 1.20: B12 step 8.8d: the module rebuilt for 7.3.6-1-surface; a warm restart after the DS-5 state brings the firmware value back, PL1 clamp included (one cycle) [M]; DS-5 settled on the SB3. |
| 25.11.2026 | 1.21: B13 implemented and tested on a fake sysfs (base sensors absent instead of lost, hwmon look-up again, a running emergency ends, the dGPU stand-in waits for 609; thermal actions do not count for absent inputs); specs `supervisor.md` 0.2, `inputs.md` 0.3, `deviations.md` 0.4 (DV-2, DV-12), knowledge `sb3-detach-and-base.md`, SB3 device pages. Open: a real detach with the daemon in control. |
| 25.11.2026 | 1.22: B13 ✅: a real detach and re-attach of the SB3 base with the daemon in control gave no emergency, the base sensors were absent for 24 s and found again under another index [M]; the thermal-action rule for absent inputs approved by the maintainer. Next C6. |
| 25.11.2026 | 1.23: C6 started 10:29 on the SB3: 24 h, MMIO path, module loaded at boot (maintainer's decision; local configuration of the reference device). |
