# Source

Implementation of `../docs/specs/`. Nothing here yet.

Planned layout (Python reference implementation, standard library only):

```
src/surface_power/
  loader/      blob sources, decoding, preprocessor, expressions, parser, validation
  engine/      graph, scheduler, blocks (one module per algorithm GUID), PID
  adapters/    inputs (hwmon, ssam, power_supply, profile, dtx, backlight), outputs (powercap, msr)
  supervisor/  device detection, snapshot/restore, emergency rules, suspend/resume
  monitor/     journal, status, time series
  tools/       blob, replay, report, reset, notify
tests/         unit tests, synthetic blobs, replay files
```
