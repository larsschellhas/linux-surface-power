# Architecture design

Version 0.2, 14.11.2026. Status: agreed direction (see requirements, section 6). High-level design only; details go into `../specs/`.

## Overview

```
                 ┌──────────────────────────── surface-powerd (root, systemd, Type=notify) ───────────────────────────┐
 efivarfs / file │ loader      blob → decode (A/B/C) → preprocess (macros, expressions) → parse → validate → graph    │
                 │   │                                                                                                │
 hwmon, SSAM,    │ adapters-in  channel → Linux source, 1 Hz sensors, EC requests, validity    ──► engine ──► outputs │
 power_supply,   │              (temperatures, slider state, platform power, battery, posture, brightness)    │         │
 platform_profile│                                                                                            ▼         │
 dtx, lid        │ engine      stages, 1000/5000 ms instances, dividers, timer start/stop, validity, reset, PID variant │
                 │                                                                                            │         │
                 │ supervisor  firmware snapshot/restore, hard limits, emergency rules, foreign changes,       ◄───────┘
                 │             suspend/resume, watchdog, dry-run                                                         │
                 │   │                                                                                                   │
 powercap, MSR ◄─┤ adapters-out clamped writes: PL1/PL2/PL4, psys, PL3 (if allowed); log-only outputs (dGPU, battery mode)│
                 │ monitor     journal, status.json, time series                                                        │
                 └──────────────────────────────────────────────────────────────────────────────────────────────────────┘
 ExecStopPost: surface-power-reset (restores the firmware snapshot)        user session: surface-power-notify (status.json → notifications)
 tools (no root): surface-power-blob (dump/inspect/diagram), surface-power-replay, surface-power-report
```

## Components

| Component | Responsibility | Requirements | Notes |
|---|---|---|---|
| **loader** | Get the blob (efivarfs, file), decode forms A/B/C, run the preprocessor and expression evaluator, parse lines, validate, build the graph | FR-B1–B9 | Pure, no hardware; used by the daemon and all tools |
| **engine** | Evaluate the graph: block implementations by GUID, scheduling (instances, stages, dividers, timer start/stop, input-change evaluation), validity propagation, reset, PID variant per core version | FR-E1–E7 | Pure and deterministic; time is passed in, never read from a clock |
| **adapters-in** | Map each input channel to a Linux source; read, convert units, mark validity; request EC data on events | FR-I1–I8 | One small module per source; a device profile can override the mapping |
| **adapters-out** | Map output channels to powercap/MSR; one clamped write path; change detection; log-only outputs | FR-O1–O6, NFR-2 | The only component that writes limits |
| **supervisor** | Device detection, firmware snapshot/restore, hard limits, emergency rules, foreign-change detection, suspend/resume handling, watchdog, dry-run gate | FR-D1–D3, FR-S1–S7, FR-M4 | Taken over from the prototype `dienst/` (state machine regulate/fallback/emergency/off) |
| **monitor** | Journal with priorities, `status.json`, time series with rotation | FR-M1–M3 | Taken over from the prototype |
| **reset tool** | `ExecStopPost`: restore the snapshot if the daemon died | FR-S2 | Separate, minimal, no dependency on the engine |
| **notifier** | User-session service: shows warnings from `status.json` | FR-M5 | Taken over from the prototype |
| **tools** | `blob` (dump, graph, diagram), `replay` (engine on recorded inputs), `report` (validation data for other devices) | FR-B9, FR-M6, FR-M7 | No root, except reading EC data for the report |

## Data model (outline)
- **Value:** number + valid flag, in blob units (mW, 0.1 K, …). Units are converted only in the adapters.
- **Node:** id (channel or `VN<n>`), current value.
- **Block:** GUID, line id, stage, instance, divider, inputs (node ids), parameters (numbers after expression evaluation), state.
- **Graph:** ordered blocks per instance and stage, input and output channel tables with ranges and defaults.
- **Device profile:** DMI match, framework, blob source, PID variant, input mapping overrides, slider mapping, supported/tested flag.
  It holds no limit values (G-2).

## Timing
- Sensor adapters poll at 1 Hz.
- The engine runs the 5000 ms instance (and the 1000 ms instance if a blob uses it) on a monotonic clock, plus extra evaluations when an
  input changes, as Windows does.
- Suspend/resume is detected (logind `PrepareForSleep`, or a `CLOCK_BOOTTIME` jump) → firmware values before sleep, reset and restart after.

## Process and deployment
- **`surface-powerd.service`:**
  - `Type=notify`, `WatchdogSec`, `Restart=on-failure` with a start limit, `ExecStopPost=surface-power-reset`;
  - `Conflicts=thermald.service`;
  - sandboxing: `ProtectSystem=strict`, minimal capabilities, `RuntimeDirectory` for snapshot and status, `StateDirectory` for the
    time series.
- **`surface-power-notify.service`:** a user unit.
- **Packaging:** a Python package first; distro packages and a linux-surface repo entry later.

## Language and implementation plan
- **Reference implementation in Python** (standard library only, so it runs on any distro without dependencies). The prototype service code
  is reused for supervisor, monitor, notifier and reset.
- **Production version in Rust** (OD-2), using the same specs and the same test corpus (unit tests, synthetic blobs, replay files).
- The engine has no I/O, so it can be ported and cross-checked block by block.

## Phases (link to acceptance criteria)

| Phase | Content | Done when |
|---|---|---|
| 1 | loader + `blob` tool | AC-B1–B6 |
| 2 | engine for the SB3 blob (core ≤ 3.140) + `replay` | AC-E1–E7, AC-E9 |
| 3 | adapters + supervisor + monitor, dry-run on the SB3 | AC-I1–I4, AC-O1–O2, AC-S1–S8, AC-L4 |
| 4 | control on the SB3 | AC-L1–L3, AC-L5 |
| 5 | variant ≥ 3.159, newer block types, `report`, community dry-runs | AC-E8, AC-M1–M2 |
| 6 | Rust version, packaging, kernel SSAM client for `0x53` (upstream via linux-surface), integration talks | OD-2, OD-3 |
| later | MPTF format and engine (L4), fan/WTMF outputs, opt-in improvements from `ideas.md` | L3/L4, G-3 |

## Risks
| Risk | Effect | Mitigation |
|---|---|---|
| Misread semantics of a block | wrong limits | replay against measurements, unit tests from the analysis, dry-run before control |
| Unknown real core version (C-2) | wrong PID variant | default from the package; replay comparison; make it configurable |
| `0x53` field mapping wrong (C-3) | psys/PL3/PL4 off | validate with plug/unplug tests; invalid → defaults |
| No `0x53` answer | psys at 23/34.5 W (block defaults) | same as Windows' fallback; report it |
| Debug cdev / MSR not available (lockdown) | missing inputs/outputs | marked interim (NFR-11); propose kernel interfaces |
| Blob content in the repo by mistake | licensing problem | `.gitignore`, CI check (AC-X1), synthetic test blobs |
