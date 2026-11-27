# Spec: tools

Version 0.1, 14.11.2026. Implements FR-B9, FR-M6, FR-M7. Tests: AC-B2, AC-B3, AC-E9, AC-M2. Markers as in `blob-format.md`.
All tools run without root, except reading EC data for the report.

## 1. `surface-power-blob`
```
surface-power-blob [PATH] [--graph] [--outputs] [--dot] [--json]
```
- **Input:** `PATH` defaults to the UEFI variable. It may be a raw blob, a capsule or a text file (`blob-format.md` 2).
- **Default output:** source, form, decoded size, line counts, the block types with counts, unknown GUIDs with the outputs they reach (AC-B3), instances used, and errors and warnings with line numbers.
- **`--graph`:** one line per block, with stage, id, inputs → outputs and parameters, in evaluation order.
- **`--outputs`:** for every device output, the chain of blocks that feeds it.
- **`--dot`:** a Graphviz graph of the blocks and nodes.
- **`--json`:** a machine-readable version of the above.
- **Exit codes:** 0 ok; 1 the blob has errors; 2 no blob found.
- **Privacy:** these outputs show parameter values. They are meant for local review. The report tool (3) is the one meant for sharing.

## 2. `surface-power-replay`
```
surface-power-replay BLOB INPUT.csv [--variant classic|direct] [--out OUT.csv]
```
- **Input CSV:**
  - Header: `t_ms` followed by channel names (`2E8B14C2/509`, `2DD6960F/1`, …). Other columns, such as the run kind or output columns from a recorded series, are ignored.
  - One row per second. An empty cell means invalid. Values are in blob units.
  - The special column `event` may hold `reset`, which applies a resume reset at that time.
- **Run:** the replay runs the scheduler exactly as the daemon does, with the row times as `now`.
- **Output CSV:**
  - Columns: `t_ms`, `kind`, then every controller (`ctl<id>.state`, `ctl<id>.set`, `ctl<id>.u`) and every device output (rounded, as delivered to the adapters).
  - One row per engine run. The output is deterministic (AC-E9).
- **Helper:** `--from-linux-log` converts a prototype or measurement log (°C, W) into the input format.

## 3. `surface-power-report` (FR-M7)
```
surface-power-report [--with-ec]
```
Prints a short text that contains no blob content and no serial numbers:
- the device (DMI product name and SKU), the kernel version, linux-surface modules present;
- the blob: found yes/no, form, size, a SHA-256 of the decoded text (to tell versions apart), the block types used, unknown GUIDs, the outputs and the instances used;
- the inputs: for each input channel the blob reads, whether a Linux source exists; an absent one (base detached, `supervisor.md` 4.1); for a missing one
  whether it is an accepted deviation (DV-3), reaches an output written on Linux (then the daemon stays in dry-run) or reaches none;
- the outputs: for each output, its target as the daemon sees it at its start (powercap zone or MMIO zone, MSR, stand-in, thermal action, fan path,
  not needed with the reason);
- the default mode (control only with a tested profile, else dry-run unless `--control`) and **the daemon's own verdict** whether control is possible on
  this machine, with its reasons (25.11.2026, plan B10: the report builds the daemon in dry-run, writing nothing, and takes the verdict from it, so that
  both cannot disagree); as a normal user it says that the answer under the unit needs root;
- with `--with-ec` (root): whether the `0x53` and DPTF requests answer, and through the debug device their values;
- with `--with-ec`, where the blob reads SAM temperature sensors and the kernel has no sensor node (DV-10): the read-only sensor probe (mask, names,
  temperatures; `tester-kit.md` 1); `--probe-sensors` forces it on a device with the node. The probe is not an input source of the daemon;
- the PL1/PL2 copies: presence of 00:04.0 and of `intel-rapl-mmio`, the PL path the daemon would choose at its start with the reason (`kernel-mmio-daemon.md` 2),
  the MMIO copy (register as root, else the zone, else why it is not readable) and the MSR copy; on an AMD CPU only "not applicable";
- running writers (thermald, tuned, TLP, power-profiles-daemon) with what each of them changes (ppd: platform profile and EPP/EPB, not the power limits [M]);
- as root, a read-only probe of the RAPL MSRs on CPU 0 (`0x17`, `0x606`, `0x610`, `0x611`, `0x614`, `0x615`, each "ok" or the error, e.g. `EIO`) and the CPU's
  `hypervisor` flag (plan F10); PL3 shows "no Linux path: MSR 0x615 not readable" where the profile switches it on but the register does not read.
The tester kit (`tester-kit.md`) runs this report as one part of its snapshot.

## Tester kit and its evaluation
- `scripts/surface-power-check` (`tools/check.py`, `tools/kit/`): the staged device check, spec `tester-kit.md`. Not read only: it pauses services,
  loads a test module and, in its last stage, lowers the MMIO PL1 for a few minutes, as the spec says.
- `scripts/check-eval.py FILE | --issue N` (`tools/check_eval.py`, no root): from a result of the kit (JSON block, treated as data) the skeleton of the
  evaluation along the procedure of `CLAUDE.md`: stages, profile, the blob found by its hash in the local corpus (else it stops there), firmware state,
  changes seen, the tester's computed limits against our replay, the blob's limits per slider state, the module comparison recomputed from the raw
  registers, the kernel registry changes the controller's answers give evidence for (D9–D11), proposed `kit-evidence.toml` entries, and the manual steps that remain.
