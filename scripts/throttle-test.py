#!/usr/bin/env python3
"""Throttle-cap check (run as root): can Linux cap the CPU and GPU frequency the way the daemon does for `9BBB8910`?

For testers who own a Surface. It uses the same target discovery and the same percent -> frequency translation as the daemon
(`src/surface_power/adapters/cpufreq.py`), and nothing else. Steps:
  1. print the targets found (cpufreq policies, Intel i915 / xe or devfreq GPU) and the values that would be written (--dry-run stops here);
  2. baseline: all-core load for a few seconds, effective frequency per policy from `scaling_cur_freq`;
  3. cap every CPU policy to PCT percent of its maximum frequency (default 50), the same load, the same measurement;
  4. cap the GPU (if there is a target) and read the value back; no GPU load is generated;
  5. restore every value.
Safety:
  - every file that is written is read first, and put back in a `finally` block, on SIGINT and on SIGTERM;
  - it stops and restores at once if any thermal zone reaches --max-temp (default 85 C), and it does not start above --max-temp - 15;
  - the value is never below 20 % of the maximum frequency or below the user's `scaling_min_freq`;
  - it refuses to run while surface-powerd.service or surface-powerd-dryrun.service is active (two writers on one file).
Results go to OUT/throttle.json and stdout; send the file with your report. Restore errors are printed in capitals and set exit code 1.
Usage: sudo python3 -I scripts/throttle-test.py OUT [--pct 50] [--seconds 6] [--max-temp 85] [--dry-run] [--no-gpu]
"""
import argparse
import glob
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from surface_power.adapters import cpufreq  # noqa: E402

SERVICES = ("surface-powerd.service", "surface-powerd-dryrun.service")


class Abort(Exception):
    """Raised by a signal or by the temperature check: stop, restore, report."""


def rd(p):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def wr(p, v):
    with open(p, "w") as f:
        f.write(str(v))


def hottest_c(root):
    """Highest thermal zone temperature in C, with its zone name; (None, None) if there are no zones."""
    best = (None, None)
    for z in glob.glob(os.path.join(root, "sys/class/thermal/thermal_zone*")):
        v = rd(os.path.join(z, "temp"))
        if v and v.lstrip("-").isdigit():
            c = int(v) / 1000
            if best[0] is None or c > best[0]:
                best = (c, rd(os.path.join(z, "type")) or os.path.basename(z))
    return best


def services_active():
    out = []
    for s in SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", "--quiet", s], check=False, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue                               # no systemd: nothing can be running as a service
        if r.returncode == 0:
            out.append(s)
    return out


def _spin(stop_at):
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    x = 0
    while time.monotonic() < stop_at:
        x += 1


def load_and_sample(policies, root, max_c, seconds):
    """All-core busy loop; average of scaling_cur_freq per policy over 100 ms samples after a 1 s ramp; checks the temperature."""
    stop_at = time.monotonic() + seconds
    procs = [multiprocessing.Process(target=_spin, args=(stop_at,)) for _ in range(os.cpu_count() or 4)]
    for p in procs:
        p.start()
    sums = {p.name: [] for p in policies}
    peak = (None, None)
    try:
        time.sleep(1.0)
        n = 0
        while time.monotonic() < stop_at - 0.2:
            for p in policies:
                v = rd(os.path.join(os.path.dirname(p.path), "scaling_cur_freq"))
                if v and v.isdigit():
                    sums[p.name].append(int(v))
            if n % 5 == 0:
                c, z = hottest_c(root)
                if c is not None and (peak[0] is None or c > peak[0]):
                    peak = (c, z)
                if c is not None and c >= max_c:
                    raise Abort(f"thermal zone {z} reached {c:.1f} C (limit {max_c:g} C)")
            n += 1
            time.sleep(0.1)
    finally:
        for p in procs:
            p.terminate()
            p.join()
    return {"avg_khz": {k: round(sum(v) / len(v)) for k, v in sums.items() if v},
            "max_khz": {k: max(v) for k, v in sums.items() if v}, "samples": min((len(v) for v in sums.values()), default=0),
            "peak_temp_c": peak[0], "peak_zone": peak[1]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--pct", type=float, default=50.0, help="request in percent of the maximum frequency (default 50)")
    ap.add_argument("--seconds", type=float, default=6.0, help="length of each load step")
    ap.add_argument("--max-temp", type=float, default=85.0, help="abort and restore at this thermal-zone temperature, C")
    ap.add_argument("--dry-run", action="store_true", help="only print the targets and the values that would be written")
    ap.add_argument("--no-gpu", action="store_true", help="leave the GPU alone")
    ap.add_argument("--root", default="/", help=argparse.SUPPRESS)       # for tests on a fake tree
    a = ap.parse_args()
    if not 20 <= a.pct <= 100:
        sys.exit("--pct must be between 20 and 100")
    policies = cpufreq.find_cpu_policies(a.root)
    gpu = None if a.no_gpu else cpufreq.find_gpu(a.root)
    targets = policies + ([gpu] if gpu else [])
    res = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "pct": a.pct, "dry_run": a.dry_run, "steps": {},
           "targets": [{"name": t.name, "path": t.path, "value": t.read(), "hw_lo": t.hw_lo, "hw_hi": t.hw_hi,
                        "would_write": t.desired(a.pct)} for t in targets]}
    print("targets:")
    for t in res["targets"]:
        print(f"  {t['name']}: {t['path']} = {t['value']} (range {t['hw_lo']}..{t['hw_hi']}), {a.pct:g} % -> {t['would_write']}")
    if not targets:
        sys.exit("no cpufreq policy and no GPU frequency interface found: nothing to test")
    c, z = hottest_c(a.root)
    print(f"hottest thermal zone: {z} {c} C" if c is not None else "no thermal zones found (no temperature guard!)")
    if a.dry_run:
        print("dry run: nothing written")
        return 0
    if os.geteuid() != 0:
        sys.exit("needs root (or use --dry-run)")
    active = services_active()
    if active:
        sys.exit(f"refusing to run: {', '.join(active)} is active; stop it first (systemctl stop ...)")
    if c is not None and c > a.max_temp - 15:
        sys.exit(f"refusing to start: {z} is at {c:.1f} C, limit {a.max_temp:g} C minus 15 C margin; let it cool down")
    if c is None:
        sys.exit("refusing to run without any thermal zone to watch")

    original = {t.path: rd(t.path) for t in targets}
    os.makedirs(a.out, exist_ok=True)

    def on_signal(signum, _frame):
        raise Abort(f"signal {signum}")
    old = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}
    exit_code = 0
    try:
        res["steps"]["baseline"] = load_and_sample(policies, a.root, a.max_temp, a.seconds)
        print("baseline:", json.dumps(res["steps"]["baseline"]))
        for t in policies:
            wr(t.path, t.desired(a.pct))
        res["steps"]["cap_written"] = {t.name: rd(t.path) for t in policies}
        res["steps"]["cap"] = load_and_sample(policies, a.root, a.max_temp, a.seconds)
        print(f"cap {a.pct:g} %:", json.dumps(res["steps"]["cap"]))
        base, cap = res["steps"]["baseline"]["avg_khz"], res["steps"]["cap"]["avg_khz"]
        res["steps"]["ratio_cap_to_baseline"] = {k: round(cap[k] / base[k], 3) for k in cap if k in base and base[k]}
        print("effective frequency, cap / baseline:", json.dumps(res["steps"]["ratio_cap_to_baseline"]))
        if gpu:
            wr(gpu.path, gpu.desired(a.pct))
            res["steps"]["gpu_cap"] = {"written": gpu.desired(a.pct), "readback": rd(gpu.path)}
            print("GPU cap:", json.dumps(res["steps"]["gpu_cap"]))
    except Abort as e:
        res["aborted"] = str(e)
        print("ABORTED:", e)
        exit_code = 2
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)       # nothing may interrupt the restore
        errors = []
        for t in targets:
            if original[t.path] is None:
                continue
            try:
                wr(t.path, original[t.path])
            except OSError as e:
                errors.append(f"{t.path}: {e}")
        now = {t.path: rd(t.path) for t in targets}
        errors += [f"{p}: is {now[p]}, was {original[p]}" for p in original if original[p] is not None and now[p] != original[p]]
        res["restore_errors"] = errors
        res["restored"] = now
        if errors:
            print("RESTORE FAILED:", errors)
            exit_code = 1
        else:
            print("restored:", json.dumps(now))
        for s, h in old.items():
            signal.signal(s, h)
        with open(os.path.join(a.out, "throttle.json"), "w") as f:
            json.dump(res, f, indent=1)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
