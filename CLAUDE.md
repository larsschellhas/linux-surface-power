# Working rules for Surface power management and kernel patches

Learned from the reviews of linux-surface/kernel#190, #195 and linux-surface/linux-surface#2383 (as of 17 Nov 2026).

## Layout

- This repository holds the daemon (`src/surface_power/`), tests, packaging, specs (`docs/specs/`), device pages
  (`docs/devices/`) and committed research summaries (`docs/research/`).
- `docs/research/raw/` is git-ignored raw material: `raw/tools/` (firmware packages per device, decompiled Windows drivers) and
  `raw/arbeitsordner/` (the working folder: measurements, SMF blobs in `smf-blob/`, drafts, `STAND-UND-PLAN.md`). Never commit it.
  Files there are mostly German working notes.
- Conversation with the maintainer is in German; everything that goes to GitHub, mailing lists or into this repository is English.
- Nothing is posted, pushed, sent or filed without the maintainer's explicit yes. Show the full text first. No AI footer in
  commit messages or posts unless the maintainer asks for one.

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
  maintainer checks and re-adds it (`git rebase --signoff`).

## Procedure: evaluating a test report or checking a device
Always applies, also after context compaction: nothing from memory or by analogy to another device. Every step on the device
itself, in this order. Paths: `raw/` = `docs/research/raw/`, working folder = `raw/arbeitsordner/`.

0. **Start:** read `raw/arbeitsordner/STAND-UND-PLAN.md` and this list. For questions about earlier decisions search the transcript,
   do not guess.
1. **Record the report:** comment ID, tester, device (DMI `product_name`/`sku` from the report), kernel, repo state (commit), mode
   (dry-run/control), what was stopped beforehand (thermald, PPD, tuned ...).
2. **Device page** `docs/devices/<device>.md`: blob(s), core/variant, all inputs and outputs with Linux path, "ready for control".
3. **Profile** `src/surface_power/profiles/`: does it match exactly these DMI values? Which options (pl3, pl4, dGPU, fan)? DMI
   values only from tester reports or proven sources, never derived from drivers (the packages do not contain complete SKUs).
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
    README table, `STAND-UND-PLAN.md`; then the draft of the reply (only what was done, checked per device).

Known traps (from earlier mistakes):
- PL1/PL2: Windows writes the MMIO copy (MCHBAR + 0x59a0), Linux writes the MSR copy through powercap (DV-8). The lower enabled one
  is in force [H as a general rule; measured on a Pro 10 and a Pro 8].
- PL3 only with `pl3 = true`, and only after proof in the driver of this device (DV-9).
- A finding on one device does not apply to others, not even with the same client build: platform type, blob and firmware values
  differ.
- Never read `raw/arbeitsordner/acpi/` (device ACPI dumps). No recursive search over `raw/`, `docs/research/` or the repository
  without an explicit exclusion.
