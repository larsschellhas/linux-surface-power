#!/usr/bin/env python3
"""Closed-loop simulation: SMF engine (with a blob) + the measured SB3 thermal model, compared with the Windows behaviour (AC-E4).

The plant model comes from Linux step tests on the SB3 15" (docs/research/06):
    P_sys = P_pkg + 14.2 W
    I_VTS1 = Ta + x1 + x2,  tau_i * dx_i/dt = R_i * P_sys(t - 10 s) - x_i,  R = 0.549 / 0.249 K/W, tau = 512 / 78 s
    T_cpu = I_VTS1 + 1.61 K/W * P_pkg + 6.9 K, at most 100 °C (the hardware throttles there)
Under full load the package takes PL1, unless TjMax limits it first.

Usage: python3 -I scripts/simulate-sb3.py BLOB [--minutes 30] [--ta 23] [--slider 5] [--csv out.csv]
The blob is the device's own (e.g. the UEFI variable); it is not part of the repository.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from surface_power.engine.engine import Engine  # noqa: E402
from surface_power.engine.graph import build_graph  # noqa: E402
from surface_power.engine.scheduler import Scheduler  # noqa: E402
from surface_power.loader.model import ChannelRef  # noqa: E402
from surface_power.loader.source import load  # noqa: E402

TEMP, SYSTEM, POWER = "2E8B14C2-26D6-4119-A62E-9D108C4DDC2A", "2DD6960F-7CB9-4BB8-82BF-97FA5D8BD767", "28B7BD85-B4CD-49F1-B02B-F84E5AA09889"
POSTURE = "8060B17F-2FFB-4139-82E6-8B4C60F48419"
PL1 = ChannelRef("0B7B5350-BB8C-4459-8D4A-A75C57073801", 51)
R1, TAU1, R2, TAU2, DEAD_S, REST_W = 0.549, 512.0, 0.249, 77.7, 10, 14.2
IDLE_PKG_W = 2.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("blob")
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--idle-minutes", type=float, default=5, help="idle time after the load")
    ap.add_argument("--ta", type=float, default=23.0, help="ambient temperature in °C")
    ap.add_argument("--slider", type=int, default=5, help="slider state (5 = Best performance on AC)")
    ap.add_argument("--variant", default="classic")
    ap.add_argument("--csv")
    a = ap.parse_args()

    graph = build_graph(load(a.blob).parsed, a.variant)
    eng = Engine(graph)
    sched = Scheduler(eng)
    vts1 = ChannelRef(TEMP, 509)
    fixed = {ChannelRef(SYSTEM, 1): a.slider, ChannelRef(POSTURE, 1): 1, ChannelRef(POWER, 1): 80, ChannelRef(POWER, 101): 90,
             ChannelRef(POWER, 2): 47900, ChannelRef(POWER, 3): 35600, ChannelRef(POWER, 6): 9700, ChannelRef(POWER, 7): 120000,
             ChannelRef(POWER, 8): 73500, ChannelRef(POWER, 10): 0}

    # Start from idle steady state.
    p_idle = IDLE_PKG_W + REST_W
    x1, x2 = R1 * p_idle, R2 * p_idle
    history = [p_idle] * DEAD_S
    pl1 = 30.0
    rows = []
    load_s, total_s = int(a.minutes * 60), int((a.minutes + a.idle_minutes) * 60)
    for t in range(total_s):
        skin = a.ta + x1 + x2
        inputs = dict(fixed)
        inputs[vts1] = round((2731 + skin * 10))  # the EC reports 0.1 K steps
        sched.step(t * 1000, inputs)
        pl1 = eng.rounded_outputs().get(PL1, 30000) / 1000
        if t < load_s:
            tj_limit = (100 - 6.9 - skin) / 1.61
            pkg = min(pl1, tj_limit)
        else:
            pkg = IDLE_PKG_W
        p_sys = pkg + REST_W
        history.append(p_sys)
        p_in = history.pop(0)
        x1 += (R1 * p_in - x1) / TAU1
        x2 += (R2 * p_in - x2) / TAU2
        c = graph.controllers[0]
        rows.append((t, round(skin, 2), pl1, round(pkg, 2), round(p_sys, 2), c.state))

    # Summary in the terms of the Windows measurements (docs/research/02).
    cut = next((r for r in rows if r[2] < 29.5), None)
    load_rows = rows[:load_s]
    tail = load_rows[-300:]
    changes = [(b[0], round(b[2] - a_[2], 3)) for a_, b in zip(load_rows, load_rows[1:]) if b[2] != a_[2]]
    steps = [d for t_, d in changes if cut and t_ > cut[0]]
    release = next((r for r in rows[load_s:] if r[2] >= 29.9), None)
    print(f"ambient {a.ta} °C, slider {a.slider}, load {a.minutes} min")
    if cut:
        print(f"cut: at {cut[0]} s, skin {cut[1]} °C, PL1 30 -> {cut[2]} W")
    print(f"steps after the cut: {len(steps)}, size {min(map(abs, steps), default=0):.2f}–{max(map(abs, steps), default=0):.2f} W, "
          f"median interval {sorted(b - a_ for a_, b in zip([t_ for t_, _ in changes][1:], [t_ for t_, _ in changes][2:]))[len(changes) // 2 - 1] if len(changes) > 3 else '-'} s")
    print(f"last 5 min of load: skin {min(r[1] for r in tail)}–{max(r[1] for r in tail)} °C, PL1 {tail[-1][2]} W, "
          f"package {sum(r[3] for r in tail) / len(tail):.1f} W, system {sum(r[4] for r in tail) / len(tail):.1f} W")
    if release:
        print(f"after the load: PL1 back to 30 W after {release[0] - load_s} s (skin {release[1]} °C)")
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "skin_c", "pl1_w", "pkg_w", "sys_w", "ctl_state"])
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
