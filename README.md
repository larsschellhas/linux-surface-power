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
| `docs/specs/` | Technical specifications, derived from the requirements (next step) |
| `src/` | Implementation of the specs |

## Status

Research done, requirements written. No code yet. See `docs/requirements/README.md`.

## Confidentiality

Raw research material includes Microsoft firmware, drivers, configuration blobs and decompiled code. It stays local in
`docs/research/raw/` and is never committed. Committed documents describe behaviour and parameter values only.
The `acpi/` dumps in the raw folder contain a Windows product key (MSDM table) and must never be shared.

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT license ([LICENSE-MIT](LICENSE-MIT))

at your option. Unless you explicitly state otherwise, any contribution you intentionally submit for inclusion in this project, as defined
in the Apache-2.0 license, is dual licensed as above, without any additional terms or conditions.

This covers the project's own code and documents only. It does not cover Microsoft firmware, drivers or configuration blobs, which are
not part of this repository.

## AI assistance

This work is done with the help of an AI assistant (Claude, in Claude Code). It ran analysis tools and drafted texts and code.
The direction, measurements on the device and review are by the maintainer.
