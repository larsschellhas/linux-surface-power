# linux-surface-power

Power and thermal management for Microsoft Surface devices on Linux, driven by the device's own firmware configuration.

On Windows, newer Surface devices regulate their CPU power limits at runtime with the **Surface System Management Framework (SMF)**.
SMF evaluates a per-device configuration blob that the firmware provides. Linux has no counterpart, so these devices run on boot-time
defaults and throttle, run hot or leave performance unused. This project reads the installed blob and evaluates it the way Windows does.

- Long-term goal: clean power management for all Surface devices.
- Mid-term scope: devices controlled by Surface SMF (2020–2025 models).
- First reference device: Surface Book 3 15".

Discussion upstream: [linux-surface#2383](https://github.com/linux-surface/linux-surface/issues/2383) (SMF),
[linux-surface#2384](https://github.com/linux-surface/linux-surface/issues/2384) (MPTF, 2025/26 models),
[kernel#117](https://github.com/linux-surface/kernel/issues/117) (Surface Book 3 psys limit).

## Layout

| Path | Content |
|---|---|
| `docs/research/` | Findings so far: measurements, SMF analysis, device survey, Linux interfaces. Only summaries are committed; raw material lives in `docs/research/raw/` (git-ignored) |
| `docs/requirements/` | Requirements, acceptance criteria and the architecture design |
| `docs/specs/` | Technical specifications, derived from the requirements |
| `src/` | Python reference implementation of the specs (see `src/README.md`) |
| `packaging/` | systemd units and the sleep hook |
| `scripts/check-repo.py` | CI check that no Microsoft content gets into the repository (AC-X1) |

## Status

- **Specs and a Python reference implementation** (`docs/specs/`, `src/`, 246 unit tests):
  - Loader: reads all known blob forms; MPTF configurations (2025/26 models) are recognised and rejected.
  - Engine: all block types of the SMF blobs from 2020 to 2025, including expressions with channel references and the variable-setpoint PID, with the PID in both core variants. All 41 SMF blobs of the local corpus build completely (`docs/research/09-newer-blobs.md`).
  - Around it: adapters, supervisor, monitoring, the daemon and tools.
- **Surface Book 3 15": control works and matches Windows** (`docs/research/08-field-tests-sb3.md`):
  - skin held at 48 °C and ~32 W system power under 30 min of full load;
  - the same cut and control steps as on Windows;
  - psys at 45/100 W instead of the 25/40 W boot value;
  - dGPU held at the Windows limit (~50 W) through a clock-cap stand-in.
  - A multi-day dry-run (AC-L4) runs before the device profile is marked tested. Until then the default is dry-run.
- **Kernel side** (submitted, not merged yet):
  - SB3 sensor and fan nodes, main and base: [kernel#190](https://github.com/linux-surface/kernel/pull/190).
  - The EC's DPTF battery data and adapter rating in sysfs: [kernel#195](https://github.com/linux-surface/kernel/pull/195). Without it, the daemon reads these values through the SSAM debug device, which needs root.
- **Inputs of the newer blobs** (`docs/specs/inputs.md` 8): display brightness, external display, camera, docks, battery current, the
  EC's battery EWMA level and max-charge flag, standby phase, USB-C source power (estimate), and the Laptop Studio's dGPU inputs. Every
  SMF blob now has a source for each input that reaches a limit. They are tested on a fake sysfs and partly on the SB3, not yet on the
  devices that need them.
- **Next:** reports from devices other than the SB3, and a kernel attribute for the EC's battery EWMA level and max-charge flag.

## Device overview

What is done per device, as of 16.11.2026. Source: the local blob corpus, one or more blob versions per device from the Surface driver
packages (`scripts/corpus-report.py`, `docs/research/09-newer-blobs.md`). Only the Surface Book 3 15" has been run and measured.

- **Blob runs:** the engine builds and runs every line of the device's blob(s).
- **Profile:** device profile in `src/surface_power/profiles/`, with the PID variant (`classic` / `direct`). Without a profile: `direct`.
- **Inputs:** where the inputs come from that the blob needs for PL1, PL2, PL4 or psys (`docs/specs/inputs.md` 8). An input without a
  source keeps the daemon in dry-run on that device. "Debug device" means `/dev/surface/aggregator`, which needs root and the
  `surface_aggregator_cdev` module until a kernel attribute exists.
- **On device:** Linux side (sensors, batteries, outputs) checked on real hardware and compared with Windows measurements.

| Device | Blob runs | Profile | Inputs | On device | State |
|---|---|---|---|---|---|
| Book 3 15" | ✓ | classic, tested after AC-L4 | all from sysfs and the EC data (kernel#195 or debug device) | ✓ field tests match Windows; kernel#190, #195 pending | controls; default dry-run until AC-L4 |
| Book 3 13" | ✓ | classic (shared with 15") | all from sysfs | – | could control; untested |
| Pro 7+ | ✓ | – (direct) | all from sysfs | – | could control; untested |
| Pro 8 | ✓ | classic, untested | battery EWMA level: debug device (root) | – | could control with root; untested |
| Pro 9 Intel | ✓ | – (direct) | battery EWMA level: debug device (root) | – | could control with root; untested |
| Pro 9 5G (arm64) | ✓ | – (direct) | all from sysfs | – | could control; untested |
| Pro 10, Pro 10 5G | ✓ | – (direct) | battery EWMA level and max charge: debug device (root) | – | could control with root; untested |
| Pro 11 Intel | ✓ | – (direct) | as Pro 10 | – | could control with root; untested |
| Pro 11 Snapdragon (arm64) | ✓ | – (direct) | USB-C source power: upper-bound estimate | – | could control; untested |
| Pro 12" 1st Ed. (arm64) | ✓ | – (direct) | USB-C source power: upper-bound estimate | – | could control; untested |
| Laptop 3 AMD | ✓ | classic, untested (core 2.51 not compared) | all from sysfs | – | could control; untested |
| Laptop 4 AMD | ✓ | classic, untested | all from sysfs | – | could control; untested |
| Laptop 4 Intel | ✓ | – (direct) | all from sysfs | – | could control; untested |
| Laptop 5 | ✓ | – (direct) | all from sysfs | – | could control; untested |
| Laptop 6 | ✓ | – (direct) | as Pro 10 | – | could control with root; untested |
| Laptop 7 Intel, Laptop 7 5G | ✓ | – (direct) | as Pro 10 | – | could control with root; untested |
| Laptop 7 Snapdragon (arm64) | ✓ | – (direct) | USB-C source power: upper-bound estimate | – | could control; untested |
| Laptop 13" (arm64) | ✓ | – (direct) | USB-C source power: upper-bound estimate | – | could control; untested |
| Laptop Go 1 | ✓ | classic, untested | all from sysfs | – | could control; untested |
| Laptop Go 2 | ✓ | – (direct) | all from sysfs | – | could control; untested |
| Laptop Go 3 | ✓ | – (direct) | all from sysfs | – | could control; untested |
| Go 3 | ✓ | classic, untested | all from sysfs | – | could control; untested |
| Go 4 | ✓ | – (direct) | all from sysfs | – | could control; untested |
| Laptop Studio 1 | ✓ | classic, untested | battery EWMA level: debug device (root); dGPU power and temperature: NVML | – | could control with root; untested |
| Laptop Studio 2 | ✓ | – (direct) | as Laptop Studio 1 | – | could control with root; untested |
| 2025/26 models (MPTF) | – | – | – | – | not covered (Windows MPTF, [linux-surface#2384](https://github.com/linux-surface/linux-surface/issues/2384)) |

Notes:
- For devices with several blobs (sizes, LTE/Wi-Fi, iGPU/dGPU, LCD/OLED), the table covers all of them.
- "Could control" means the daemon has a source for every input the blob needs for the limits. Nothing beyond the SB3 15" has run on
  a device. The temperature sensors the blob reads (SAM sensors 5, 9 and 12 on most devices) still have to show up in Linux on that
  device, and the newer input adapters are only tested on a fake sysfs and on this SB3. Only a report from the device can tell.
- Older devices (Book 2, Pro 7, Laptop 3 Intel, …) use Intel DTT, not SMF, and are out of scope.

## Try it on your Surface

For any Surface from the Book 3 / Pro 7+ (2020) to the Laptop 7 / Pro 11 generation (2024). The report reads, writes nothing and needs
no root. It contains no blob content and no serial numbers. A SHA-256 hash tells blob versions apart.

```bash
git clone https://github.com/larsschellhas/linux-surface-power
cd linux-surface-power
PYTHONPATH=src python3 -m surface_power.tools.report
```

Python 3.11 or newer is needed. Nothing has to be installed. Please post the output in
[linux-surface#2383](https://github.com/linux-surface/linux-surface/issues/2383) and add your Surface model.

The report shows:
- whether the device has SMF and a blob, and which block types the blob uses;
- whether the engine can run it (`usable for control`), and which blocks it does not support yet;
- which inputs Linux provides (temperatures, slider, batteries, posture, EC power data) and which outputs it can write.

**Optional: a few minutes of dry-run.** This computes the limits the way Windows would and writes nothing:

```bash
PYTHONPATH=src python3 -m surface_power.daemon --dry-run --run-dir /tmp/sp-run --state-dir /tmp/sp-state
```

Stop it with Ctrl-C. `/tmp/sp-run/status.json` then shows the computed limits and the controller states. Without root, the EC power data
stay invalid unless your kernel has kernel#195, so some limits show their fallback values.

## Confidentiality

Raw research material includes Microsoft firmware, drivers, configuration blobs and decompiled code. It stays local in
`docs/research/raw/` and is never committed. Committed documents describe behaviour and parameter values only.
The `acpi/` dumps in the raw folder contain a Windows product key (MSDM table) and must never be shared.

## License

Licensed under either of

- MIT license ([LICENSE](LICENSE))
- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))

at your option. Unless you explicitly state otherwise, any contribution you intentionally submit for inclusion in this project, as defined
in the Apache-2.0 license, is dual licensed as above, without any additional terms or conditions.

This covers the project's own code and documents only. It does not cover Microsoft firmware, drivers or configuration blobs, which are
not part of this repository.

## AI assistance

This work is done with the help of an AI assistant (Claude, in Claude Code). It ran analysis tools and drafted texts and code.
The direction, measurements on the device and review are by the maintainer.
