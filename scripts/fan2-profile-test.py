#!/usr/bin/env python3
"""Surface Laptop Studio 2 only (run as root): does fan 2 follow the fan profile FAN 0x0e? (linux-surface#2392)

    sudo modprobe surface_aggregator_cdev
    sudo python3 scripts/fan2-profile-test.py OUT            # about 10 minutes, at idle
    python3 scripts/fan2-profile-test.py OUT --dry-run       # prints the frames, opens nothing

Before the run: stop any own fan tool and REBOOT, so that no manual EC value is active. Stock linux-surface kernel.
Steps, each followed by a wait of 60 s and one reading of FAN 0x01 (rpm), 0x0d and 0x05 for fan 1 and fan 2:
  A. baseline, nothing written;
  B. each of the four platform profiles through sysfs (the kernel sends FAN 0x0e to instance 1 only): does fan 2 change too?
  C. the platform profile back to its start value; then FAN 0x0e to instance 2 with each of the values 1-4, the start value
     last: does fan 2, and only fan 2, change?
At the end the platform profile is the start value and fan 2 has the same value (what Windows would hold). Reboot afterwards.

What is known about the commands:
  - 0x0e with instance 2 and a value 1-4 is what the Windows SMF client of the Laptop Studio 2 sends for fan 2 (from the driver;
    the effect on the EC is the open question). Windows sends the same value to both fans; the test sends different ones on purpose.
  - 0x01 is the fan speed read of the kernel's surface_fan driver (instance 1 there).
  - 0x0d and 0x05 as reads of the profile applied to a fan come ONLY from a community decode of the EC firmware (linux-surface#2392).
    The Windows SMF client does not send them. Running this test sends them: at your own risk.
Safety:
  - it refuses to run unless it is root, the device is a Surface Laptop Studio 2, AC is present, surface-powerd*.service is
    inactive, the CPU package is below 70 C and no other fan test holds the lock file; it asks you to confirm the reboot;
  - the only writes are FAN 0x0e to instance 2 with 1-4, and the platform profile. Never 0x0f (fan state), no rpm or noise value;
  - the restore (start profile to fan 2 and to sysfs) runs in a `finally` block and on SIGINT, SIGTERM and SIGHUP; a forked
    watchdog sends the fan 2 restore frame if the process dies or hangs for 60 s;
  - at CPU package >= 85 C, or a lost sensor: restore at once, exit code 2;
  - if something else changes the platform profile during a wait (power-profiles-daemon, tuned, you), the run stops;
  - a read that fails 3 times in a row is not sent again.
Output: OUT/fan2-profile.json (every request and answer as raw bytes) and OUT/fan2-profile.md (table to paste into the issue).
It contains the product name, the kernel version, temperatures and the EC answers. Read it before posting.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from surface_power.adapters import fan  # noqa: E402
from surface_power.adapters.inputs import platform_profile as sysfs_profile  # noqa: E402


def _load_fan_test():
    """scripts/fan-test.py has the machine access, the lock and the watchdog; the hyphen needs a load by path."""
    spec = importlib.util.spec_from_file_location("fan_test", os.path.join(HERE, "fan-test.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ft = _load_fan_test()

PRODUCT = "Laptop Studio 2"
WAIT_S, RUN_S = 60, 1200
START_CPU_C, ABORT_CPU_C = 70.0, 85.0
FAIL_LIMIT = 3
READ_CAPACITY = 256                        # the length of the 0x05 answer is not known
CID_RPM, CID_REC, CID_TARGET, CID_PROFILE = 0x01, 0x05, 0x0D, 0x0E
READS = (CID_RPM, CID_TARGET, CID_REC)
FANS = (1, 2)
PROFILES = ("low-power", "balanced", "balanced-performance", "performance")    # fan values 1..4


def profile_frame(value):
    """The only frame this tool writes: FAN 0x0e, instance 2, one byte 1..4."""
    if value not in (1, 2, 3, 4):
        raise ft.Refuse(f"fan profile value {value} is not 1-4")
    return fan.Frame(fan.TC_FAN, 2, CID_PROFILE, bytes([value]))


def read_frame(cid, iid):
    if cid not in READS or iid not in FANS:
        raise ft.Refuse(f"read 0x{cid:02x} instance {iid} is not on the list of this tool")
    return fan.Frame(fan.TC_FAN, iid, cid, b"", flags=1)


def writes_for(start_value):
    """Phase C: every value once, the start value last, so that the last write is also the restore."""
    return [v for v in (1, 2, 3, 4) if v != start_value] + [start_value]


class Env(ft.Env):
    def profile_choices(self):
        _, path = sysfs_profile(self.root)
        if not path:
            return []
        p = os.path.join(self.root, path.lstrip("/"))
        name = p + "_choices" if p.endswith("platform_profile") else os.path.join(os.path.dirname(p), "choices")
        return (ft._rd(name) or "").split()

    def set_profile(self, name):
        _, path = sysfs_profile(self.root)
        if not path:
            return False
        try:
            with open(os.path.join(self.root, path.lstrip("/")), "w") as f:
                f.write(name)
            return True
        except OSError:
            return False

    def read_fan(self, cid, iid):
        return self.transport.read(fan.TC_FAN, cid, iid, capacity=READ_CAPACITY)

    def thermals(self):
        """Every surface_thermal sensor, for the record only (the sensor names of this device are not checked here)."""
        out = {}
        for d in self._hwmon("surface_thermal"):
            for i in range(1, 17):
                v = ft._num(os.path.join(d, f"temp{i}_input"))
                if v is not None:
                    label = ft._rd(os.path.join(d, f"temp{i}_label")) or ""
                    out[f"temp{i} {label}".strip()] = v / 1000
        return out

    def uptime_s(self):
        v = ft._rd(os.path.join(self.root, "proc/uptime"))
        return int(float(v.split()[0])) if v else None

    def other_services(self):
        """Recorded only: they may change the platform profile, which stops the run."""
        out = {}
        for unit in ("power-profiles-daemon.service", "tuned.service", "tuned-ppd.service", "thermald.service"):
            try:
                r = ft.subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10, check=False)
                out[unit] = r.stdout.strip()
            except (OSError, ft.subprocess.TimeoutExpired):
                out[unit] = "unknown"
        return out

    def confirm(self, question):
        try:
            return input(question + " [y/N] ").strip().lower() in ("y", "yes", "j", "ja")
        except (EOFError, KeyboardInterrupt):
            return False


class Runner:
    def __init__(self, env, start_profile, log=print):
        self.env, self.start = env, start_profile
        self.start_value = fan.PROFILE_VALUES[start_profile]
        self.log = log
        self.steps, self.sent = [], []
        self.fails = {}
        self.wd = None
        self.t0 = 0.0
        self.profile_touched = self.fan2_touched = False

    def refusals(self):
        e, out = self.env, []
        if not e.is_root():
            out.append("needs root")
        if PRODUCT not in e.product():
            out.append(f"this is '{e.product()}', the test is for the Surface {PRODUCT} only")
        if e.ac() is not True:
            out.append("AC power is missing (or unknown)")
        active = e.services_active()
        if active:
            out.append(f"{', '.join(active)} is active; stop it first (systemctl stop ...)")
        pkg = e.temps()[1]
        if pkg is None:
            out.append("the CPU package temperature is not readable")
        elif pkg >= START_CPU_C:
            out.append(f"CPU package is at {pkg:.1f} C, start limit {START_CPU_C:g} C; let the machine idle")
        missing = [p for p in PROFILES if p not in e.profile_choices()]
        if missing:
            out.append(f"platform profile choices missing: {', '.join(missing)}")
        if not e.transport.can_write():
            out.append(f"{e.transport.path} is not available: sudo modprobe surface_aggregator_cdev")
        return out

    # --- waiting and reading ---

    def check_temp(self):
        pkg = self.env.temps()[1]
        if pkg is None:
            raise ft.Abort("the CPU package temperature sensor stopped answering")
        if pkg >= ABORT_CPU_C:
            raise ft.Abort(f"CPU package reached {pkg:.1f} C (limit {ABORT_CPU_C:g} C)")

    def wait(self, expected_profile):
        for _ in range(WAIT_S):
            self.env.sleep(1)
            if self.wd:
                self.wd.beat()
            if self.env.now() - self.t0 >= RUN_S:
                raise ft.Abort(f"the run reached its {RUN_S // 60} minute limit")
            self.check_temp()
            now = self.env.platform_profile()
            if now != expected_profile:
                raise ft.Abort(f"the platform profile changed from {expected_profile} to {now} during a wait: something else sets it "
                               "(power-profiles-daemon, tuned, a desktop setting); stop it and run again")

    def snapshot(self, phase, label):
        reads = []
        for iid in FANS:
            for cid in READS:
                key = (cid, iid)
                frame = read_frame(cid, iid)
                if self.fails.get(key, 0) >= FAIL_LIMIT:
                    reads.append({"cid": cid, "iid": iid, "request": frame.wire().hex(), "response": None, "skipped": True})
                    continue
                ans = self.env.read_fan(cid, iid)
                self.fails[key] = 0 if ans is not None else self.fails.get(key, 0) + 1
                reads.append({"cid": cid, "iid": iid, "request": frame.wire().hex(),
                              "response": None if ans is None else ans.hex()})
                if self.wd:
                    self.wd.beat()
        step = {"t": round(self.env.now() - self.t0, 1), "phase": phase, "label": label,
                "platform_profile": self.env.platform_profile(), "cpu_c": self.env.temps()[1],
                "surface_thermal_c": self.env.thermals(), "reads": reads}
        self.steps.append(step)
        self.log(f"[{phase}] {label}: " + "  ".join(f"fan {i} {_cells(step, i)}" for i in FANS))

    # --- writing ---

    def set_profile(self, name):
        self.profile_touched = True
        if not self.env.set_profile(name):
            raise ft.Abort(f"writing the platform profile {name} failed")
        self.sent.append({"platform_profile": name})
        self.log(f"set platform profile {name} (the kernel sends FAN 0x0e to instance 1)")

    def send_fan2(self, value):
        frame = profile_frame(value)
        self.fan2_touched = True
        if not fan.send_frame(self.env.transport, frame):
            raise ft.Abort(f"the request {frame.label()} value {value} failed")
        self.sent.append({"wire": frame.wire().hex()})
        self.log(f"sent {frame.label()} value {value} (wire {frame.wire().hex()})")

    def restore(self):
        errors = []
        if self.profile_touched and not self.env.set_profile(self.start):
            errors.append(f"platform profile {self.start} not restored")
        if self.fan2_touched:
            frame = profile_frame(self.start_value)
            for _ in range(3):
                if fan.send_frame(self.env.transport, frame):
                    self.sent.append({"wire": frame.wire().hex(), "restore": True})
                    break
                self.env.sleep(0.2)
            else:
                errors.append(f"{frame.label()}: restore frame {frame.payload.hex()} not sent")
        return errors

    def run(self, res):
        self.t0 = self.env.now()
        code, old = 0, {}

        def on_signal(n, _f):
            raise ft.Abort(f"signal {n}")
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old[s] = signal.signal(s, on_signal)
        try:
            self.log(f"A: baseline, {WAIT_S} s, nothing written")
            self.wait(self.start)
            self.snapshot("A", "baseline")
            self.wd = ft.Watchdog(self.env.transport_factory)
            self.wd.start()
            self.log("B: each platform profile through sysfs")
            for name in PROFILES:
                self.set_profile(name)
                self.wait(name)
                self.snapshot("B", f"sysfs {name} ({fan.PROFILE_VALUES[name]})")
            self.log("C: FAN 0x0e to instance 2")
            self.set_profile(self.start)
            self.wait(self.start)
            self.snapshot("C", f"sysfs {self.start}, before any write to fan 2")
            self.wd.add_restore(profile_frame(self.start_value))        # recorded BEFORE the first write to fan 2
            for value in writes_for(self.start_value):
                self.send_fan2(value)
                self.wait(self.start)
                self.snapshot("C", f"fan 2 <- {value}")
        except ft.Abort as a:
            res["aborted"] = str(a)
            self.log(f"ABORTED: {a}")
            code = 2
        finally:
            for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(s, signal.SIG_IGN)                     # nothing may interrupt the restore
            errors = self.restore()
            if self.wd:
                self.wd.stop(clean=not errors)
            res["sent"], res["restore_errors"], res["steps"] = self.sent, errors, self.steps
            if errors:
                self.log("RESTORE FAILED: " + "; ".join(errors))
                code = 1
            for s, h in old.items():
                signal.signal(s, h)
        return code


def _cells(step, iid):
    got = {r["cid"]: r["response"] for r in step["reads"] if r["iid"] == iid}
    raw = got.get(CID_RPM)
    rpm = int.from_bytes(bytes.fromhex(raw)[:2], "little") if raw and len(raw) >= 4 else "-"
    return f"rpm {rpm} 0x0d {got.get(CID_TARGET) or '-'} 0x05 {got.get(CID_REC) or '-'}"


def markdown(res):
    lines = [f"`{res['product']}`, kernel `{res['kernel']}`, start profile `{res['platform_profile']}`, "
             f"uptime at start {res.get('uptime_s')} s", ""]
    if res.get("aborted"):
        lines += [f"**Aborted:** {res['aborted']}", ""]
    if res.get("restore_errors"):
        lines += ["**Restore failed:** " + "; ".join(res["restore_errors"]), ""]
    lines += ["| Step | Fan | rpm | 0x0d (raw) | 0x05 (raw) | CPU °C |", "|---|---|---|---|---|---|"]
    for step in res.get("steps", []):
        for iid in FANS:
            got = {r["cid"]: r["response"] for r in step["reads"] if r["iid"] == iid}
            raw = got.get(CID_RPM)
            rpm = int.from_bytes(bytes.fromhex(raw)[:2], "little") if raw and len(raw) >= 4 else "-"
            lines.append(f"| {step['phase']}: {step['label']} | {iid} | {rpm} | `{got.get(CID_TARGET) or '-'}` | "
                         f"`{got.get(CID_REC) or '-'}` | {step['cpu_c']} |")
    lines += ["", "Requests (category, target, instance, flags, command, length): "
              + ", ".join(f"`{read_frame(c, i).wire().hex()}`" for i in FANS for c in READS)]
    writes = [s["wire"] for s in res.get("sent", []) if "wire" in s]
    lines += ["Writes to fan 2: " + (", ".join(f"`{w}`" for w in writes) or "none"), ""]
    return "\n".join(lines)


def print_plan(start_value, log=print):
    log("reads after every step:")
    for iid in FANS:
        for cid in READS:
            log(f"  read  FAN 0x{cid:02x} iid {iid}   wire {read_frame(cid, iid).wire().hex()}")
    log(f"A: nothing written\nB: platform profile through sysfs: {', '.join(PROFILES)}")
    log("C: platform profile back to the start value, then:")
    for v in writes_for(start_value):
        f = profile_frame(v)
        log(f"  write {f.label()} value {v}   wire {f.wire().hex()}")
    r = profile_frame(start_value)
    log(f"restore: platform profile to the start value; {r.label()} value {start_value}   wire {r.wire().hex()}")


def main(argv=None, env=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", help="directory for the results")
    ap.add_argument("--dry-run", action="store_true", help="print the frames, open nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask: you have rebooted and no own fan tool is running")
    ap.add_argument("--root", default="/", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    env = env or Env(a.root)
    start = env.platform_profile()
    if a.dry_run:
        if start not in PROFILES:
            print("note: platform profile unknown here, assuming balanced for the printout")
            start = "balanced"
        print_plan(fan.PROFILE_VALUES[start])
        print("dry run: nothing sent, device not opened")
        return 0
    if start not in PROFILES:
        sys.exit(f"refusing to start: platform profile '{start}' is not one of {', '.join(PROFILES)}")
    res = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "product": env.product(), "kernel": env.kernel(),
           "platform_profile": start}
    run = Runner(env, start)
    refusals = run.refusals()
    if refusals:
        sys.exit("refusing to start: " + "; ".join(refusals))
    res["uptime_s"], res["services"] = env.uptime_s(), env.other_services()
    print(f"{res['product']}, kernel {res['kernel']}, platform profile {start}")
    print_plan(run.start_value)
    print("0x0d and 0x05 are known from a community decode of the EC firmware only; you send them at your own risk.")
    if not a.yes and not env.confirm("Have you rebooted, is every own fan tool stopped, and is the machine idle? Start?"):
        sys.exit("not started")
    if not env.take_lock():
        sys.exit(f"refusing to start: another run holds {env.lock_path}")
    os.makedirs(a.out, exist_ok=True)
    try:
        code = run.run(res)
    finally:
        env.release_lock()
        with open(os.path.join(a.out, "fan2-profile.json"), "w") as f:
            json.dump(res, f, indent=1)
        with open(os.path.join(a.out, "fan2-profile.md"), "w") as f:
            f.write(markdown(res))
    if code == 0:
        print(f"done. Please reboot, then post {os.path.join(a.out, 'fan2-profile.md')} (and the .json) in linux-surface#2392.")
    return code


if __name__ == "__main__":
    sys.exit(main())
