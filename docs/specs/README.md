# Technical specifications

Written from `../requirements/` once those are agreed. Planned specs, in the order of the phases in `../requirements/architecture.md`:

| Spec | Covers |
|---|---|
| `blob-format.md` | delivery forms A/B/C, header, LZNT1, text forms, preprocessor, expression grammar, line types and fields, validation rules |
| `engine.md` | data model, scheduling, validity, reset, and one section per algorithm GUID with its exact semantics |
| `controller.md` | PID with parameter sets: fields, state machines for both core variants, the law, deadband, divider, mean window |
| `inputs.md` | channel → Linux source mapping, units, polling, EC requests (`0x53`, DPTF), slider mapping |
| `outputs.md` | channel → powercap/MSR mapping, clamping, hard limits, log-only outputs |
| `supervisor.md` | snapshot/restore, emergency rules, foreign changes, suspend/resume, watchdog, dry-run, device detection |
| `monitoring.md` | journal format, `status.json` schema, time series format, notifier |
| `tools.md` | `blob`, `replay` (input file format), `report` |
| `deviations.md` | every intended deviation from Windows behaviour (G-3) |

Each spec states which requirements (FR/NFR) it implements.
