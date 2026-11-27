# Working rules for Surface power management and kernel patches

Learned from the reviews of linux-surface/kernel#190, #195 and linux-surface/linux-surface#2383 (as of 17 Nov 2026).

## Layout

- This repository holds the daemon (`src/surface_power/`), tests, packaging, specs (`docs/specs/`), device pages
  (`docs/devices/`), committed research summaries (`docs/research/`), the knowledge base (`docs/knowledge/`: established facts and
  known traps with evidence) and the kernel module prototypes (`kernel/`, GPL-2.0-only, see "Kernel modules" below).
- This file holds working rules only. Facts about devices, kernel behaviour, tools and test practice go into `docs/knowledge/` (or
  the specs and research they belong to), not here.
- `docs/research/raw/` is git-ignored raw material: `raw/tools/` (firmware packages per device, decompiled Windows drivers) and
  `raw/arbeitsordner/` (the working folder: measurements, SMF blobs in `smf-blob/`, drafts, `STAND-UND-PLAN.md`). Never commit it.
  Files there are mostly German working notes.
- Conversation with the maintainer is in German; everything that goes to GitHub, mailing lists or into this repository is English.
- Nothing is posted, pushed, sent or filed without the maintainer's explicit yes. Show the full text first. No AI footer in
  commit messages or posts unless the maintainer asks for one. **Exception: anything that is or becomes a kernel contribution**
  (commits touching `kernel/`, patches, cover letters) follows the kernel's AI rule, see "Kernel modules".

## Project plan (read first)
- **Read `docs/plan.md` first in every session, before anything else** (also after context compaction), then `STAND-UND-PLAN.md`,
  then `docs/knowledge/README.md` and the knowledge files that touch the task (power limits, hardware tests, kernel code).
  It is the single plan of the project: target (rollout on every SMF Surface, Secure Boot without extra setup), rollout gates
  R1-R8 per device, work packages with status, help-wanted items, rollout matrix, critical path.
- Keep it current, always: every change of state (a work package started or finished, a device gate met, a tester report
  evaluated, a PR merged, a new blocker or help-wanted item) updates `docs/plan.md` in the same commit as the change, together
  with the README ("Plan at a glance", "Help wanted", status, device table), the device pages and `STAND-UND-PLAN.md`. Update its
  version, date and change log.
- Status symbols as defined at the top of the plan (✅ 🔄 ⬜ ⏳ ➖ 🙋 ⚠️). New work gets a work-package ID in the matching group;
  finished packages stay with ✅, IDs are not renumbered. Status claims need evidence; [H] otherwise.
- Kernel work counts as done only per stage: merged in linux-surface (group D) and, separately, submitted and merged in mainline
  (group E).
- Before proposing next steps, check them against the critical path in the plan, and change the plan first if the order changes.

## Evidence instead of assumptions
- No assumption about Windows behaviour, EC commands, channels, units or device capabilities that is not backed directly by the
  decompiled Windows drivers (or a measurement). The evidence must come from the build of the affected device or from a provably
  identical build (check version and hash).
- What is not proven is marked [H], named explicitly, and never silently adopted into code or text.
- Subagent results and generated analyses (for example "does not reach any output") count as evidence only once they are
  re-checked in the driver, in the blob or with an engine cross-check.
- "Not needed" for an output only after a complete search over all driver binaries; an output without effect is implausible
  until shown otherwise.

## Kernel changes
- Every proposed kernel change must be proven 100 %: command, instance, payload, affected devices. No PR and no commit to post with
  open [H] points about behaviour; name open points honestly in the commit text and the PR.
- Always think about all Surface devices, not only the SB3: which devices share the ACPI ID or registry group (for example SB3
  13.5" and 15" = MSHW0117)? What happens there?
- Never rely on queries that time out on devices without the command. Do not "ask and see whether an answer comes". Use a support
  property in the registry (like `has_fan`, `has_dptf`, `fan_count`) only for proven devices; without proof, no property.
- The same in the daemon: EC requests only for inputs that the device's blob reads; a command that repeatedly gets no answer is
  stopped.
- Only the maintainer adds `Signed-off-by`. If the content of a commit that carries their sign-off changes, remove the line; the
  maintainer checks and re-adds it (`git rebase --signoff`). The kernel says the same about AI agents
  (`Documentation/process/coding-assistants.rst`): an agent never adds `Signed-off-by`.

## Kernel modules (`kernel/`)
Decided 23.11.2026; concept `docs/requirements/kernel-modules.md`, specs `docs/specs/kernel-*.md`.
- **License:** GPL-2.0-only. Every file under `kernel/` starts with the SPDX line `// SPDX-License-Identifier: GPL-2.0-only` (or `#` in build
  files); the text is in `kernel/LICENSES/preferred/GPL-2.0`. Code from `kernel/` is not copied into the rest of the repository (it would
  take over that code's license); the other direction (MIT/Apache code into `kernel/`) is allowed.
- **AI rule of the kernel (adopted):** a kernel contribution that a coding assistant helped to write carries `Assisted-by: LLM [TOOLS]`
  (format of the linux-surface tree, `submitting-patches.rst` "Using Assisted-by"; re-read the current text in mainline before every
  submission), listing specialised tools only (checkpatch, sparse, coccinelle ...), not git, gcc, make or editors. The assistant never
  adds `Signed-off-by`; the maintainer does, after reading and testing the code (the DCO is his). Cover letter and changelog say what the
  tool did, how the code was tested, and what could not be tested (`Documentation/process/generated-content.rst`). Commits in this
  repository that touch `kernel/` carry the `Assisted-by` line from the start, so a later `git format-patch` is already honest.
- **Prototype first:** the module is built in a copy outside the repository, read-only by default, loaded by hand, never by an alias; a
  device enters the DMI table only with the evidence list of `docs/specs/kernel-mmio-rapl.md` 6. Write tests run step by step on the
  Surface Book 3 with the maintainer's explicit yes before each step; `sudo` only through a terminal tab.
- **Other hardware only through the tester kit's unlock chain** (decided 27.11.2026, `docs/specs/tester-kit.md` 6.2): testers unlock the stages
  themselves, a stage that passed on their machine unlocks the next one, after the disclaimer and his consent; no release by the maintainer per device.
  Testers get a new or changed module or write stage only after the whole chain has passed on the SB3 through the kit. The repository informs and can
  block (`kit-evidence.toml`). Outside this chain (loading by hand, other tools, a control run) nothing changes: not on other hardware until the
  maintainer says so.
- `checkpatch.pl --no-tree -f` and `make W=1` clean before every commit that touches `kernel/`; the checks run against the kernel tree the
  module is meant for (the linux-surface kernel, `v7.3-surface-devel`).

## Procedure: evaluating a test report or checking a device
Always applies, also after context compaction: nothing from memory or by analogy to another device. Every step on the device
itself, in this order. Paths: `raw/` = `docs/research/raw/`, working folder = `raw/arbeitsordner/`.

0. **Start:** read `docs/plan.md` first, then `raw/arbeitsordner/STAND-UND-PLAN.md`, `docs/knowledge/` and this list. For questions about earlier decisions
   search the transcript, do not guess.
1. **Record the report:** comment ID, tester, device (DMI `product_name`/`sku` from the report), kernel, repo state (commit), mode
   (dry-run/control), what was stopped beforehand (thermald, PPD, tuned ...).
2. **Device page** `docs/devices/<device>.md`: blob(s), core/variant, all inputs and outputs with Linux path, "ready for control".
3. **Profile** `src/surface_power/profiles/`: does it match exactly these DMI values? Which options (pl3, pl4, dGPU, fan)? DMI
   values only from tester reports or proven sources, never derived from drivers (the packages do not contain complete SKUs).
   Profiles may be created before a report from a proven source named in the profile (the public driver-pack catalogue, the
   kernel's own matches); the report then confirms them. Options only where the blob uses the output and the driver of this
   device proves the target (decision 19.11.2026, plan F5).
4. **Previous state:** README device table, `STAND-UND-PLAN.md`, earlier comments by the same tester in the issue.
5. **Specs:** `docs/specs/outputs.md`, `inputs.md`, `supervisor.md`, `engine.md` and `deviations.md`: is every deviation from
   Windows that the report touches recorded there?
6. **Research:** `docs/research/09-newer-blobs.md` (core to variant), `10-outputs.md` (outputs, PL3/PL4, MMIO), `11-fan-charging.md`,
   `12-remaining-outputs.md`, `13-pro10-battery-and-timing.md`, `14-laptop-studio-dgpu.md`, depending on the device.
7. **Blob of the device:** `raw/arbeitsordner/smf-blob/geraete/<device>-*.csv` (channels, limits, defaults, inputs); interpretation
   of the nodes in `raw/arbeitsordner/smf-blob/REGLER-ANALYSE.md`. What does the blob read, what does it write, which fallback
   values apply for invalid inputs?
8. **Windows driver of the device:** raw `raw/tools/fw/<device>/` (MSI, `pl3/`, `treiber/`; SB3: `raw/tools/clients/`), decompiled
   code under `raw/tools/fw/decomp/`, notes in `raw/arbeitsordner/` (for example `pl3-treiberbeleg.md`). Always check the sha256
   (same build?). For every output the report touches: platform type of the device, then descriptor table, then the entry of the
   channel: **target (MSR, MMIO or EC), register, bit mask, bits set, unit**. Channel numbers are decimal. In the entry the fields
   come before channel and GUID (write handler: `puVar9[6]` channel, `puVar9[7]` GUID).
9. **Daemon code:** which path is really written on this device (adapter, profile options, hold/guard)? Compare with step 8: same
   register, same bits, same unit? Every deviation goes into `deviations.md`, with its consequences.
10. **Firmware state of the device:** start values differ per device (PL4 0 on the SB3, MMIO copy 28/35 W on a Pro 10, PL3 start
    value). Do not transfer them from the SB3; look in the report or ask.
11. **Measurement against expectation:** for every number in the report, what do blob + Windows path + Linux path predict? A
    deviation needs its cause proven in code or driver, otherwise [H] and a question to the tester.
12. **Update in the same step:** raw note, research and specs, `deviations.md`, profile, `scripts/device-docs.py` (device pages),
    README table, `docs/plan.md` (work packages and rollout matrix), `STAND-UND-PLAN.md`; then the draft of the reply (only what was done, checked per device).

Known traps (rules from earlier mistakes; the facts behind them are in `docs/knowledge/`):
- PL1/PL2: before saying which limit is in force on a device, check which copy (MSR or MMIO) is enabled there and at which value
  (`docs/knowledge/rapl-power-limits.md` 1, DV-8).
- Hardware tests on the SB3 follow `docs/knowledge/sb3-test-practice.md` (services stopped and masked, CPU state recorded, valid
  suspend cycles, every result saved to a file).
- PL3 only with `pl3 = true`, and only after proof in the driver of this device (DV-9).
- A finding on one device does not apply to others, not even with the same client build: platform type, blob and firmware values
  differ.
- For ACPI questions use the public dumps of the linux-surface/acpidumps repository (local clone
  `~/Repositories/linux-surface/acpidumps`, one folder per device); they carry no firmware version, so a value read there is [H] for
  the firmware on a tester's device until a report confirms it.
- Never read `raw/arbeitsordner/acpi/` (device ACPI dumps). No recursive search over `raw/`, `docs/research/` or the repository
  without an explicit exclusion.
