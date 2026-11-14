# Source

Python reference implementation of `../docs/specs/` (standard library only, Python ≥ 3.11). The Rust version (OD-2) will follow the
same specs and test corpus.

```
src/surface_power/
  loader/       blob sources, delivery forms, LZNT1, preprocessor, expressions, line parser   (spec blob-format)
  engine/       graph build, blocks, PID with parameter sets, engine, scheduler              (spec engine, controller)
  adapters/     inputs (hwmon, profile, power_supply, dtx/lid, SSAM) and the output stage     (spec inputs, outputs)
  supervisor.py detection, profiles, snapshot/restore, emergency rules, foreign changes       (spec supervisor)
  monitor.py    journal, status.json, time series                                            (spec monitoring)
  daemon.py     surface-powerd: states, main loop, sleep handling
  tools/        blob, replay, report, reset, notify                                          (spec tools)
  profiles/     device profiles (TOML, no limit values)
tests/          unit tests on synthetic blobs and a fake sysfs; local tests against real blobs (skipped without the raw folder)
```

Run the tests:

```bash
PYTHONPATH=src:tests python3 -m unittest discover -s tests
```

Inspect the device's blob (no root needed):

```bash
PYTHONPATH=src python3 -m surface_power.tools.blob_tool --outputs
```

Dry-run of the daemon (writes nothing; EC data needs root, without it psys shows the Windows fallback):

```bash
sudo PYTHONPATH=src python3 -m surface_power.daemon --dry-run --run-dir /tmp/sp-run --state-dir /tmp/sp-state
```
