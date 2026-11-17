#!/usr/bin/env python3
"""dGPU check for testers: what can Linux limit on this NVIDIA GPU? (spec outputs 6.1, docs/research/14-laptop-studio-dgpu.md)

Read-only by default. It asks the driver (NVML) for the GPU name, the power it draws, the default and enforced power limit,
the range the driver would accept for a new limit, and the maximum graphics clock, and prints what that means for the dGPU
stand-in. Nothing is written unless you ask for a step:
  --try-limit          write the CURRENT enforced power limit back (same value, so nothing changes) to learn whether the driver
                       accepts a power limit at all (the reported range is no proof: the SB3 reports 1-65 W and refuses). Needs root.
  --try-cap PCT        cap the graphics clock to PCT percent of its maximum (30-100, rounded to 15 MHz), sample power and clock
                       for --seconds, then remove the cap. Needs root and another process that loads the GPU (a game, a CUDA
                       job, `glmark2`); without load the power barely moves. Needs root.
Safety:
  - every NVML session wakes a sleeping GPU; if it is runtime-suspended the script stops unless you pass --wake;
  - a cap is removed in a `finally` block, on SIGINT and on SIGTERM; a failed removal is printed in capitals and sets exit code 1
    (then run `sudo nvidia-smi -rgc` yourself);
  - the cap never goes below 30 percent of the maximum clock and never below 300 MHz;
  - it refuses to run while surface-powerd.service or surface-powerd-dryrun.service is active, because the stand-in would
    fight it for the same cap (stop the service first; the stand-in is off in every shipped profile except the SB3).
Usage: [sudo] python3 -I scripts/dgpu-probe.py [--wake] [--try-limit] [--try-cap PCT] [--seconds 20]
Send the printed text with your report.
"""
import argparse
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from surface_power.adapters import gpu  # noqa: E402

SERVICES = ("surface-powerd.service", "surface-powerd-dryrun.service")
MIN_PERCENT = 30


class Abort(Exception):
    """Raised by a signal: stop, remove the cap, report."""


def _on_signal(signum, frame):
    raise Abort(signal.Signals(signum).name)


def active_service() -> str | None:
    for unit in SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False)
        except OSError:
            return None
        if r.returncode == 0:
            return unit
    return None


def show(c: gpu.Capabilities) -> None:
    def w(v):
        return "unknown" if v is None else f"{v:g} W"
    print(f"GPU:                     {c.name}")
    print(f"power now:               {w(c.power_w)}")
    print(f"default power limit:     {w(c.default_limit_w)}")
    print(f"enforced power limit:    {w(c.enforced_limit_w)}")
    rng = "unknown" if c.limit_min_w is None else f"{c.limit_min_w:g} to {c.limit_max_w:g} W"
    print(f"settable range:          {rng}")
    print(f"maximum graphics clock:  {'unknown' if c.max_clock_mhz is None else str(c.max_clock_mhz) + ' MHz'}")
    print()
    for line in gpu.assess(c):
        print("-", line)


def try_limit(nv: "gpu.Nvml", c: gpu.Capabilities) -> int:
    if os.geteuid() != 0:
        print("--try-limit needs root")
        return 1
    if c.enforced_limit_w is None:
        print("the driver does not report an enforced limit: nothing to write back")
        return 1
    mw = int(round(c.enforced_limit_w * 1000))
    try:
        nv.set_power_limit_mw(mw)
    except gpu.NvmlError as e:
        print(f"power limit write REFUSED ({e}): this GPU takes a clock cap only")
        return 0
    print(f"power limit write of the current value ({c.enforced_limit_w:g} W) was ACCEPTED: the driver takes power limits here.")
    print("A different value was not tried. Report this; a power limit would be a closer copy of the Windows limit.")
    return 0


def try_cap(nv: "gpu.Nvml", c: gpu.Capabilities, pct: int, seconds: int) -> int:
    if os.geteuid() != 0:
        print("--try-cap needs root")
        return 1
    if not MIN_PERCENT <= pct <= 100 or c.max_clock_mhz is None:
        print(f"--try-cap needs {MIN_PERCENT}-100 and a known maximum clock")
        return 1
    cap = max(300, gpu._round_step(c.max_clock_mhz * pct / 100))
    print(f"cap {cap} MHz ({pct} % of {c.max_clock_mhz} MHz), {seconds} s; time  power W  clock MHz")
    code = 0
    old = {s: signal.signal(s, _on_signal) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        nv.set_cap(cap)
        for i in range(seconds):
            s = nv.read()
            print(f"  {i:>3} s  {s.power_w:6.1f}  {s.clock_mhz:6d}")
            time.sleep(1)
    except gpu.NvmlError as e:
        print(f"clock cap REFUSED or failed ({e}): the stand-in cannot work on this GPU")
        code = 1
    except Abort as e:
        print(f"stopped by {e}")
        code = 1
    finally:
        try:
            nv.reset_cap()
            print("cap removed")
        except gpu.NvmlError as e:
            print(f"COULD NOT REMOVE THE CAP ({e}): run `sudo nvidia-smi -rgc` NOW")
            code = 1
        for s, h in old.items():
            signal.signal(s, h)
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wake", action="store_true", help="allow the probe to wake a runtime-suspended GPU")
    ap.add_argument("--try-limit", action="store_true")
    ap.add_argument("--try-cap", type=int, metavar="PCT")
    ap.add_argument("--seconds", type=int, default=20)
    a = ap.parse_args()
    pci = gpu.find_nvidia_gpu()
    if pci is None:
        print("no NVIDIA display controller found")
        return 1
    unit = active_service() if (a.try_limit or a.try_cap) else None
    if unit:
        print(f"{unit} is active: stop it first (two writers on one GPU)")
        return 1
    status = gpu.runtime_status("/", pci)
    print(f"NVIDIA GPU at {pci}, runtime status: {status or 'unknown'}")
    if status == "suspended" and not a.wake:
        print("the GPU sleeps and this probe would wake it; run again with --wake, or start a GPU program first")
        return 1
    try:
        with gpu.Nvml(pci) as nv:
            c = nv.capabilities()
            show(c)
            code = 0
            if a.try_limit:
                print()
                code |= try_limit(nv, c)
            if a.try_cap is not None:
                print()
                code |= try_cap(nv, c, a.try_cap, a.seconds)
            return code
    except gpu.NvmlError as e:
        print(f"NVML failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
