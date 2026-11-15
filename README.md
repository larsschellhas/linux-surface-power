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

- **Specs and a Python reference implementation** (`docs/specs/`, `src/`, 179 unit tests):
  - Loader: reads all known blob forms (all 42 local blobs parse).
  - Engine: the block types of the 2020–2023 blobs, with the PID in both core variants.
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
- **Next:** the block types of the newer blobs (Pro 9 and later, Laptop 5 and later), so that those devices can be controlled too.

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
