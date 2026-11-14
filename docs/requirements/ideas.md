# Ideas beyond Windows parity

Collected ideas for later. **None of these is a requirement.** Each one would be a named, documented deviation from Windows,
off by default (G-3), and needs its own requirement and acceptance criteria before it is built.

| # | Idea | Motivation | Notes |
|---|---|---|---|
| I-1 | CPU temperature limit loop below TjMax (e.g. 90–95 °C) | Under load, SMF leaves the CPU at its hardware limit (TjMax/PROCHOT, 95–100 °C) for 0.5–2.5 min until the skin loop engages, which gives an on/off sawtooth | The prototype controller V2 had one (target 75 °C); for parity the target must sit above what Windows reaches |
| I-2 | Bumpless start of the skin controller instead of the fixed cut-in step | The SMF state machine drops PL1 at once (e.g. 30 → 21 W) and releases to full power below W − ΔW − hysteresis, which causes steps and overshoot | Predictive start (skin slope), as in prototype V2 |
| I-3 | Skin temperature prediction (model with two time constants and dead time) | Act before the set point is reached, so less overshoot | Model in `research/raw/arbeitsordner/strecke-2tau.json` |
| I-4 | dGPU stand-in limit via clock caps | Windows limits the SB3 dGPU (49.6 W on the Surface PSU), Linux can't set a power limit | Needs a supported NVIDIA interface on Linux; test `nvidia-smi -lgc` |
| I-5 | Charger warning: battery draining on AC | A USB-C 65 W charger with GPU load drains the base battery (~30 W) on Linux and Windows | Exists in the prototype notifier; may also become a requirement (FR-M5) |
| I-6 | psys/limit choice by charger strength in balanced/low-power profiles | Avoid draining on weak chargers | Asked in kernel#117 (comment 6774217179) |
| I-7 | Quieter fan at idle where the fan is controllable | Windows is quieter at idle than Linux in `performance` | SB3 fan is EC-owned; relevant for devices whose blob drives the fan |
| I-8 | User-tunable comfort mode (lower skin set point) | Lap use | Must only narrow limits (FR-S7) |
