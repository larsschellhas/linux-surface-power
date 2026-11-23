# Project plan: from the reference device to a rollout on every SMF Surface

Version 1.3, 23.11.2026. This is the single plan of the project: target, work packages, their state, and what comes next. Read it
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
| **C** | **Reference device Surface Book 3 15"** | 🔄 | |
| C1 | Field tests AC-L1–L3 (skin 48 °C, ~32 W, psys 45/100 W) | ✅ | – |
| C2 | PL3/PL4 measured, all 9 outputs written | ✅ | – |
| C3 | Soak test (AC-L4): first dry-run failed, replaced by a control run since 17.11.2026; watchdog incident 18.11.2026 fixed (1a7520a, 829f24e) | 🔄 | daily evaluation until 21.11.2026 |
| C4 | `tested = true` in the SB3 profile | ⬜ | after C3, maintainer's decision |
| C5 | Surface Book 3 13" (shares the profile) | ⏳ 🙋 | tester report with DMI values |
| **D** | **Kernel interfaces in linux-surface** | 🔄 | |
| D1 | SB3 sensors and fans, main and base ([kernel#190](https://github.com/linux-surface/kernel/pull/190)) | ✅ | merged 17.11.2026 (`v7.3-surface-devel`) |
| D2 | DPTF battery data `0x53` and adapter rating ([kernel#195](https://github.com/linux-surface/kernel/pull/195)) | ✅ | merged 17.11.2026 (`v7.3-surface-devel`) |
| D3 | `pm_prepare` fix of `surface_platform_profile` ([kernel#197](https://github.com/linux-surface/kernel/pull/197)); fixes the suspend bug [linux-surface#2386](https://github.com/linux-surface/linux-surface/issues/2386) | ✅ | merged 16.11.2026 (`v7.3-surface`), released in 7.3.4-2-surface, #2386 closed 17.11.2026 |
| D4 | Kernel attribute for the battery EWMA level and max-charge flag (`28B7BD85/13, 16`; OD-3) | ⬜ | needed by Pro 8, 9, 10, 11 Intel, Laptop 6, 7, Laptop Studio (R3) |
| D5 | PL1/PL2 MMIO write path (`intel-rapl-mmio` without device 00:04.0) | 🔄 ⚠️ | tester module offer on #2383 (18.11.2026, Pro 10) not evaluated yet; blocks Pro 8, Pro 10 (R5) |
| D6 | PL3 with lockdown (MSR writes are blocked there) | ⬜ ⚠️ | kernel path needed; with lockdown `none` it works [M SB3, tester Pro 10] |
| D7 | EC requests without the debug device: fan outputs, PROCHOT release (SAM), sensor offset | 🔄 | draft PR "fan profile instances" exists; other requests not designed |
| D8 | PROCHOT release on ACPI-EC devices (Go 3, Go 4: ACPI `DPHT`) | ⬜ | small kernel driver on `MSHW0301`, or evidence that it is not needed (I2) |
| **E** | **Mainline submission** | ⬜ | |
| E1 | Submit what is merged in linux-surface: SB3 sensor and fan nodes (D1), DPTF battery data and adapter rating (D2) | ⬜ | cover letter draft exists; after some time in a released linux-surface kernel |
| E2 | Battery EWMA level and max-charge attribute (after D4) | ⬜ | with or after E1 |
| E3 | EC requests that replace the debug device (after D7, D8) | ⬜ | – |
| E4 | MMIO RAPL zone without the processor thermal device (after D5) | ⬜ ⚠️ | linux-pm and int340x maintainers; acceptance uncertain, ask early (mail draft exists) |
| E5 | PL3 (MSR `0x615`) through the kernel (after D6) | ⬜ ⚠️ | no powercap constraint for PL3 today; acceptance uncertain, ask early on linux-pm |
| **F** | **Device validation and profiles (community)** | 🔄 | |
| F1 | Tester process: report, dry-run, control, evaluation procedure (`CLAUDE.md`) | ✅ | – |
| F2 | Pro 10: control run (tester); PL1 above the firmware MMIO 28 W has no effect | ⏳ | evaluate tester comment of 18.11.2026; needs D5 |
| F3 | Pro 9 Intel: control run (tester, one SKU) | 🔄 🙋 | 59-hour dry-run reported (F9); consumer SKU added from the catalogue 23.11.2026; D4 for R3 |
| F4 | Pro 8: MMIO copy caps at 30 W (tester) | ⏳ | D5, H1 (`0B7B5350/71`), D4 |
| F5 | Profiles for the devices without one: Pro 7+, Pro 10 5G, Pro 11 Intel, Laptop 4 Intel, Laptop 5, Laptop 6, Laptop 7 Intel, Laptop 7 5G, Laptop Go 2, Laptop Go 3, Go 4 | ✅ 🙋 | created 23.11.2026 with catalogue SKUs and `pl3 = true` (3, F5); reports confirm the SKUs |
| F6 | Profiles without a device check: Laptop Go 1, Go 3, Laptop 3/4 AMD, Laptop Studio 1/2 | ⏳ 🙋 | tester reports (Laptop Go 1, Laptop Studio 2: reports in F9) |
| F7 | Go 3: switch on `pl3` in its profile (I1 verified 19.11.2026) | ✅ 🙋 | on since 23.11.2026; a report with `acpidump` confirms the `_DSM` value [H] |
| F8 | Docs and code consistency | 🔄 | `specs/outputs.md` 4 corrected 19.11.2026; keep README, device pages and specs in step |
| F9 | Evaluate the reports of 18.–21.11.2026 in #2383: Pro 10 MMIO answers (technosf, 18.11.), Laptop 4 Intel 15" (hypergol-d, 20.11.), Laptop Go 1 with kernel#198 (neveltyc, 20.11.), Pro 9 59-hour dry-run (NexGen-3D-Printing, 20.11.), Laptop Studio 2 fans (VibeCodingAnimal, 21.11.) | ⬜ | procedure in `CLAUDE.md`, one device at a time; the Pro 10 answer is F2 |
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
| L4 | Coexistence with thermald, power-profiles-daemon, tuned | 🔄 | thermald handled (`Conflicts=`, `After=`); others not checked |
| L5 | Distribution packages (deb, rpm, Arch) and a linux-surface repository entry | ⬜ | – |
| L6 | Integration with linux-surface (`surface-control` or own service) | ⏳ | talks with the maintainers in kernel#117 / #2383 |
| L7 | Release process: versioning, changelog, supported-device list (FR-D3), AC-S1–S8 on hardware | ⬜ | – |

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

### C. Reference device 🔄
- **C3:** the control run as a service is evaluated daily until 21.11.2026 (scheduled task `sb3-regelbetrieb-auswertung`). Done when
  there is no crash, no unexplained invalid input, CPU < 0.5 % and memory < 50 MB over the period (AC-L4 criteria applied to control).
- **C4:** the maintainer decides on `tested = true` after C3.
- **C5:** the 13" shares the ACPI ID (MSHW0117) and the profile (match on `product_name`); its DMI values are not confirmed by a report.

### D. Kernel interfaces in linux-surface 🔄
Rule (`CLAUDE.md`): every change proven 100 % (command, instance, payload, affected devices); support properties only for proven
devices; only the maintainer signs off.
- **D4:** EC battery requests `0x1A` / `0x69` as attributes next to the `dptf` group. Done when merged in linux-surface.
- **D5:** the Pro 8 and Pro 10 testers measured that the lower enabled copy is in force (DV-8 [M on two devices]). A kernel path that
  creates `intel-rapl-mmio` from MCHBAR without 00:04.0 is the direction [H]; a tester has such a module (not loaded yet). Done when a
  module or kernel patch is in the linux-surface kernel (signed with it, so no own MOK), is checked against the Windows target
  (MCHBAR + 0x59a0, bits 14:0, enable and clamp) and is measured on at least one device with an enabled MMIO copy.
- **D6:** under lockdown `integrity` or `confidentiality` user-space MSR writes are refused (`msr.c`, LOCKDOWN_MSR). Which distribution
  kernels switch lockdown on with Secure Boot is [H] (the linux-surface kernel on the SB3 runs with Secure Boot on and lockdown `none`). Without a
  kernel path PL3 stays log-only there and R4/R5 fail on those systems.
- **D7, D8:** today fans, PROCHOT release and sensor offset go through `/dev/surface/aggregator` (interim, NFR-11); on Go 3/4 the PROCHOT
  release is an ACPI method that user space cannot call.

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
- **MMIO write path** (D5): tests of a module on a Pro 8 or Pro 10 with an enabled MMIO copy.
- **Linux on Snapdragon Surfaces** (K) and owners of the AMD Laptops (J2).
- **Kernel review** of the linux-surface patches before the mainline submission (E).

## 5. Rollout matrix per device

Gates as in 1.1. R1 is met on every device. R6 waits for the safety suite (B7) on every device; the SB3 15" has only its closed-loop
model (`scripts/simulate-sb3.py`) so far. Source: device pages (23.11.2026), profiles, tester reports in #2383.

| Device | R1 Blob | R2 Profile | R3 Inputs | R4 Outputs | R5 Secure Boot | R6 Safety sim | R7 Validation | R8 | 🙋 | Blocking WPs |
|---|---|---|---|---|---|---|---|---|---|---|
| Book 3 15" | ✅ | ✅ | 🔄 brightness fallback (G1) | ✅ 9/9 | 🔄 PL3 needs lockdown `none` | 🔄 closed-loop model only | ✅ field tests, soak 🔄 | ⬜ | – | C3, C4, D6, G1 |
| Book 3 13" | ✅ | 🔄 shared, DMI unconfirmed | 🔄 (G1) | ✅ 9/9 | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | C5 |
| Pro 7+ | ✅ | 🔄 catalogue SKUs, 1960 confirmed | ✅ | ✅ 12/12 | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | – |
| Pro 8 | ✅ | ✅ | 🔄 debug device | ⬜ 13/14 (`/71`) | ⬜ MMIO caps at 30 W | ⬜ | 🔄 reads by tester | ⬜ | – | D4, D5, H1 |
| Pro 9 Intel | ✅ | 🔄 report SKU + catalogue consumer SKU | 🔄 debug device, proximity constant | ✅ | 🔄 | ⬜ | 🔄 control run, 59-h dry-run | ⬜ | 🙋 | D4, F3, F9, G4 |
| Pro 9 5G | ✅ | ⬜ | ✅ | ⬜ (`9BBB8910/103`) | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | H2, K |
| Pro 10 | ✅ | ✅ | 🔄 debug device | ✅ | ⬜ MMIO caps at 28 W | ⬜ | 🔄 control run | ⬜ | – | D4, D5, F2 |
| Pro 10 5G | ✅ | 🔄 catalogue SKUs | 🔄 | ⬜ (`/103–105`) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | H2 |
| Pro 11 Intel | ✅ | ✅ SKU (catalogue, user reports) | 🔄 | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D4 |
| Pro 11 Snapdragon | ✅ | ⬜ | 🔄 (estimate) | ⬜ 11/20 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | H2, H3, K |
| Pro 12" 1st Ed. | ✅ | ⬜ | 🔄 (estimate) | ⬜ 6/16 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | H2, H3, H4, K |
| Laptop 3 AMD | ✅ | ✅ SKU | ✅ | ⬜ 2/5 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | J |
| Laptop 4 AMD | ✅ | ✅ SKU | ✅ | ⬜ 3/8 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | J |
| Laptop 4 Intel | ✅ | ✅ SKUs (kernel, tester report) | ✅ | ✅ 12/12 | 🔄 | ⬜ | 🔄 report 20.11. not evaluated | ⬜ | – | F9 |
| Laptop 5 | ✅ | 🔄 catalogue SKUs, consumer ones confirmed | 🔄 standby constant | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | G4 |
| Laptop 6 | ✅ | 🔄 catalogue SKUs | 🔄 | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D4 |
| Laptop 7 Intel | ✅ | 🔄 catalogue SKUs, 2107 confirmed | 🔄 | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D4 |
| Laptop 7 5G | ✅ | 🔄 catalogue SKU | 🔄 | ⬜ (`/103–105`) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | H2 |
| Laptop 7 Snapdragon | ✅ | ⬜ | 🔄 (estimate) | ⬜ 11/18 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | H2, H3, K |
| Laptop 13" | ✅ | ⬜ | 🔄 (estimate) | ⬜ 11/25 | ➖ | ⬜ | ⬜ | ⬜ | 🙋 | H2, H3, H4, K |
| Laptop Go 1 | ✅ | ✅ SKU | ✅ | ✅ 10/10 | 🔄 | ⬜ | 🔄 report 20.11. not evaluated | ⬜ | – | F9 |
| Laptop Go 2 | ✅ | 🔄 catalogue SKU | ✅ | ⬜ 13/14 (`/71`) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | H1 |
| Laptop Go 3 | ✅ | 🔄 catalogue SKU | 🔄 standby constant | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | G4 |
| Go 3 | ✅ | 🔄 product name [H] | ✅ | ⬜ 10/12 (`/70`, `/71`) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | H1, I2 |
| Go 4 | ✅ | 🔄 catalogue SKU | 🔄 standby constant | ⬜ 11/13 (`/70`) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | I2 |
| Laptop Studio 1 | ✅ | 🔄 product name | 🔄 debug device, NVML | ⬜ (`/71`, dGPU) | 🔄 | ⬜ | ⬜ | ⬜ | 🙋 | D4, G7, H1, H5 |
| Laptop Studio 2 | ✅ | 🔄 product name | 🔄 debug device, NVML | ⬜ (dGPU, `349D0E39`) | 🔄 | ⬜ | 🔄 fan report 21.11. not evaluated | ⬜ | 🙋 | D4, F9, G7, H5 |

Since 23.11.2026 every x64 SMF device has a profile; "catalogue SKU" in R2 means the match is not yet confirmed by a report from the
device. R5 is 🔄 on every Intel device as long as PL3 needs lockdown `none` (D6), the fan and PROCHOT
requests need the debug device (D7) and the MMIO copy has not been read on that device (D5).

## 6. Critical path and next steps

In this order; each step names its work package.

1. **C3/C4:** finish the SB3 control-run evaluation (21.11.2026), then the decision on `tested = true`.
2. **F2/D5:** evaluate the Pro 10 tester comment of 18.11.2026 (procedure in `CLAUDE.md`), then decide on the MMIO write path (tester
   module, own module, linux-pm) and ask linux-pm early (E4). This unblocks the Pro 10, the Pro 8 and every device whose firmware
   enables the MMIO copy.
3. **B7:** decide the safety criteria, then build the suite; from then on no tester is asked to run control on a profile that fails it.
4. **F9:** evaluate the reports of 20.–21.11.2026 (Laptop 4 Intel, Laptop Go 1 with kernel#198, Pro 9 dry-run, Laptop Studio 2 fans);
   ask in #2383 for reports that confirm the new profiles (🙋).
5. **D4, E1:** battery EWMA attribute in linux-surface; submit the merged patches to mainline.
6. **G1, H1–H3:** brightness in nits, classify the remaining outputs.
7. **D6/E5, D7/E3, D8:** PL3 with lockdown, EC requests without the debug device, `DPHT` on Go 3/4.
8. **J, K:** AMD and Snapdragon paths (driver analysis first).
9. **L1 → L2, L5–L7:** Python reference complete, then Rust or C, packages, linux-surface integration, release.

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
