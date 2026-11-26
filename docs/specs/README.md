# Technical specifications

Derived from `../requirements/`. Version 0.1 of all specs, 14.11.2026. Each spec names the requirements it implements.

| Spec | Covers | Requirements |
|---|---|---|
| [`blob-format.md`](blob-format.md) | sources, delivery forms A/B/C, LZNT1, text forms, preprocessor, expressions, line types, graph build, block registry | FR-B1 – B9 |
| [`engine.md`](engine.md) | data model, evaluation order, scheduling, validity, reset, variants, the block types, SB3 reference values | FR-E1 – E7 |
| [`controller.md`](controller.md) | PID with parameter sets: set switching, both state machines, the law, divider, reset | FR-E1, E2, E4, E5 |
| [`inputs.md`](inputs.md) | channel → Linux source, units, slider and posture mapping, EC requests | FR-I1 – I8, NFR-11 |
| [`outputs.md`](outputs.md) | the single clamped write path, hard limits, channel → powercap/MSR, log-only outputs | FR-O1 – O7, NFR-2, FR-S7 |
| [`supervisor.md`](supervisor.md) | detection, snapshot/restore, emergency rules, foreign changes, profiles, suspend, watchdog, main loop | FR-D, FR-S, FR-M4, NFR-5 |
| [`dgpu-standin-service.md`](dgpu-standin-service.md) | the dGPU stand-in under the unit: idle gate instead of the client search, helper that sets the clock cap, measurements, tests (0.6, implemented and tested under the units on the SB3) | plan B15 |
| [`monitoring.md`](monitoring.md) | journal, `status.json`, time series, notifier | FR-M1 – M5 |
| [`tools.md`](tools.md) | `blob`, `replay` (input format), `report` | FR-B9, FR-M6, FR-M7 |
| [`tester-kit.md`](tester-kit.md) | one `sudo` command for a device check, result into a device issue (draft 0.1) | F11 |
| [`kernel-mmio-rapl.md`](kernel-mmio-rapl.md) | kernel module for the MMIO copy of PL1/PL2: discovery, register access, opt-in per device, test plan (prototype in `../../kernel/`) | `../requirements/kernel-modules.md` KM-1 – KM-13 |
| [`kernel-mmio-daemon.md`](kernel-mmio-daemon.md) | daemon side of the MMIO path: zone choice, MSR copy at firmware value, snapshot/restore and enable of the MMIO copy, bounds, resume, foreign changes, reading the copy under the units (B11), tests | KM-9, KM-A7, plan B11 |
| [`kernel-pl3.md`](kernel-pl3.md) | concept for PL3 from the kernel (MSR `0x615`) | KM-1, 2, 4, 6 |
| [`deviations.md`](deviations.md) | every difference from Windows behaviour | G-3, NFR-10 |

Evidence markers:
- [C]: from the Windows driver code;
- [M]: measured;
- [H]: hypothesis;
- [D]: our design decision.

The block semantics in `engine.md` and `controller.md` were re-read from the SB3 core (3.140.139.0) for this version. Two statements of the earlier research summary were corrected along the way:
- Internal nodes are always valid. Without EC data the psys fallback is therefore 23/34.5 W, not 45 W.
- A resume does not clear the controller's divider counter.
