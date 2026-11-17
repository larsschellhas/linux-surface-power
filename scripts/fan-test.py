#!/usr/bin/env python3
"""Fan write test (run as root) for a Surface with fan outputs in its blob: does one fan channel do anything, and does it restore?

For testers who own a Pro 10, Laptop 6 or a newer device. It sends the same SAM frames as the daemon (`src/surface_power/adapters/fan.py`)
through the SSAM debug device, one channel per run, as in docs/research/11-fan-charging.md section 8. Steps:
  1. baseline: temperatures and the fan speed (FAN 0x01) for 2 minutes, nothing written;
  2. the test writes of the chosen --channel, each held for 30 s:
       201 fan profile: the value of the current platform profile, then one step up   (restore: the platform profile value)
       121 noise floor 20 | 124 active sones 3 | 111 noise limit 25 (light load only) | 114 sones limit 8 | 101 minimum 3000 rpm
       (restore of all these: 0 = neutral);
  3. restore, then 60 s of logging to check that the fan speed follows the temperature again.
Safety:
  - --dry-run prints the frames and opens nothing. --probe only reads (FAN 0x01 rpm, FAN 0x13 system SPL) and logs; it works on any Surface;
  - it refuses to write unless it is root, AC is present, the battery is at least 50 %, surface-powerd*.service is inactive, no other
    run holds the lock file, the skin sensor is below 40 C and the CPU package below 70 C (--skin-start / --cpu-start can only lower this);
  - the restore frame of a channel is recorded BEFORE the write and sent in a `finally` block and on SIGINT, SIGTERM and SIGHUP;
  - a forked watchdog holds a pipe; if this process dies (SIGKILL, crash) or sends no heartbeat for 60 s, the watchdog sends the restore frames;
  - a write is held at most 30 s, a run lasts at most 10 minutes;
  - at skin >= 44 C, CPU package >= 85 C, or a sensor that stops answering: restore at once, exit code 2;
  - it never sends 301 = 2, never writes 302-311 or any TMP frame, never writes above the range of the blob (read from the blob;
    an output without a range in the blob, or --no-blob without a blob: the hard bounds of the adapter apply). The last fallback is a reboot.
Results go to OUT/fan.json and stdout; send the file with your report. Restore errors are printed in capitals and set exit code 1.
Usage: sudo python3 -I scripts/fan-test.py OUT --channel 201|121|124|111|114|101 [--dry-run] [--blob FILE | --no-blob]
       python3 -I scripts/fan-test.py OUT --probe [--seconds 120]      (read only; the two requests need root)
"""
import argparse
import dataclasses
import fcntl
import glob
import json
import os
import select
import signal
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from surface_power.adapters import fan  # noqa: E402
from surface_power.adapters.inputs import CdevTransport  # noqa: E402

START_SKIN_C, START_CPU_C = 40.0, 70.0
ABORT_SKIN_C, ABORT_CPU_C = 44.0, 85.0
MIN_BATTERY = 50
HOLD_S, RUN_S, WATCHDOG_S = 30, 600, 60
BASELINE_S, SETTLE_S, VERIFY_S = 120, 30, 60
LOCK = "/run/lock/surface-fan-test.lock"
SERVICE_PATTERN = "surface-powerd*.service"

# What a run may write (the whitelist): the profile, the loudness and rpm limits of fan 1. Never 0x0f (state), 0x19-0x1d, TMP.
WRITABLE = {201: 0x0E, 121: 0x12, 124: 0x14, 111: 0x0C, 114: 0x15, 101: 0x0B}
TEST_VALUE = {121: 20, 124: 3, 111: 25, 114: 8, 101: 3000}
LIGHT_LOAD_ONLY = (111, 114)


class Abort(Exception):
    """Raised by a signal, a limit or a sensor loss: restore, report, exit code 2."""


class Refuse(Exception):
    """A start condition is not met."""


# --- frames ---

def check_frame(frame):
    """The guard behind every write: only the whitelist, never state 2, never TMP, never above the adapter's hard bound."""
    if frame.tc != fan.TC_FAN or frame.cid not in WRITABLE.values() or frame.iid != 1:
        raise Refuse(f"frame {frame.label()} is not on the list of frames this tool may send")
    spec = next(s for ch, s in fan.SPECS.items() if s.cid == frame.cid and s.iid == 1 and ch in WRITABLE)
    if spec.fmt == "u8":
        v = frame.payload[0]
    elif spec.fmt == "u16":
        v = int.from_bytes(frame.payload, "little")
    else:
        import struct
        v = struct.unpack("<f", frame.payload)[0]
    if v > spec.hi:
        raise Refuse(f"frame {frame.label()} value {v} is above the bound {spec.hi:g}")


class Step:
    def __init__(self, label, frame, hold_s):
        self.label, self.frame, self.hold_s = label, frame, min(hold_s, HOLD_S)


class Plan:
    def __init__(self, channel, steps, restore, notes=()):
        self.channel, self.steps, self.restore, self.notes = channel, steps, restore, list(notes)
        for s in steps:
            check_frame(s.frame)
        check_frame(restore)


def build_plan(channel, profile_value, ranges):
    """The frames of one run. `ranges`: {channel: (min, max)} from the blob, or None (--no-blob)."""
    spec = fan.SPECS[channel]
    notes = []
    if ranges is not None and channel not in ranges:
        raise Refuse(f"channel {channel} is not an output of this device's blob")

    if ranges is not None and ranges[channel] is None:
        notes.append(f"channel {channel} has no value range in the blob: the hard bounds of the adapter apply (as in the daemon)")

    def clamp(v):
        if ranges is not None and ranges[channel] is not None:
            lo, hi = ranges[channel]
            if v > hi or v < lo:
                notes.append(f"value {v} clamped into the blob range {lo:g}..{hi:g}")
            v = min(max(v, lo), hi)
        return v

    if channel == 201:
        if profile_value is None:
            raise Refuse("the platform profile is unknown: the profile restore value cannot be determined")
        values = [profile_value] + ([profile_value + 1] if profile_value < 4 else [])
        steps = [Step(f"fan profile {clamp(v)}", fan.frame_for(spec, clamp(v)), HOLD_S) for v in values]
        return Plan(channel, steps, fan.frame_for(spec, clamp(profile_value)), notes)
    steps = [Step(f"channel {channel} = {clamp(TEST_VALUE[channel])}", fan.frame_for(spec, clamp(TEST_VALUE[channel])), HOLD_S)]
    return Plan(channel, steps, fan.frame_for(spec, 0), notes)


# --- the watchdog ---

class Watchdog:
    """A forked process that holds the read end of a pipe. It sends the restore frames when the pipe closes without a clean stop,
    or when no heartbeat arrives for `timeout_s`. Messages: R<json frame>, B (beat), D (done: nothing to restore)."""

    def __init__(self, transport_factory, timeout_s=WATCHDOG_S):
        self.factory, self.timeout_s = transport_factory, timeout_s
        self.pid = None
        self.w = None

    def start(self):
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:                                           # the watchdog
            try:
                os.close(w)
                for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    signal.signal(s, signal.SIG_IGN)
                os.setsid()                                    # outlive the terminal
                self._loop(r)
            finally:
                os._exit(0)
        os.close(r)
        self.pid, self.w = pid, w

    def _loop(self, r):
        frames, buf, last = {}, b"", time.monotonic()
        while True:
            left = last + self.timeout_s - time.monotonic()
            if left <= 0:
                break
            if not select.select([r], [], [], left)[0]:
                continue
            data = os.read(r, 4096)
            if not data:
                break                                          # the parent is gone
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line == b"B":
                    last = time.monotonic()
                elif line == b"D":
                    return
                elif line[:1] == b"R":
                    fr = fan.Frame.from_dict(json.loads(line[1:]))
                    frames[(fr.tc, fr.iid, fr.cid)] = fr
        t = self.factory()
        for fr in frames.values():
            for _ in range(3):
                if fan.send_frame(t, fr):
                    break
                time.sleep(1)

    def _msg(self, line):
        if self.w is not None:
            try:
                os.write(self.w, line + b"\n")
            except OSError:
                pass

    def add_restore(self, frame):
        self._msg(b"R" + json.dumps(frame.to_dict()).encode())

    def beat(self):
        self._msg(b"B")

    def stop(self, clean=True):
        """clean: the restore went through, the watchdog has nothing to do. Else it sees the closed pipe and sends the frames."""
        if self.w is not None:
            if clean:
                self._msg(b"D")
            os.close(self.w)
            self.w = None
        if self.pid is not None:
            try:
                os.waitpid(self.pid, 0)
            except OSError:
                pass
            self.pid = None


# --- the machine ---

def _rd(p):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _num(p):
    v = _rd(p)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class Env:
    """Everything the run reads from the machine. Tests replace it."""

    def __init__(self, root="/", lock=LOCK):
        self.root, self.lock_path = root, lock
        self.transport = CdevTransport(os.path.join(root, fan.SSAM_CDEV.lstrip("/")))
        self._lock_fd = None

    def now(self):
        return time.monotonic()

    def sleep(self, s):
        time.sleep(s)

    def is_root(self):
        return os.geteuid() == 0

    def transport_factory(self):
        return CdevTransport(self.transport.path)

    def _hwmon(self, name):
        return [d for d in sorted(glob.glob(os.path.join(self.root, "sys/class/hwmon/hwmon*"))) if _rd(os.path.join(d, "name")) == name]

    def skin_c(self):
        """Hottest skin sensor (labels I_VTS*; without labels every surface_thermal sensor), C; None if none answers."""
        vals, any_label = [], False
        for d in self._hwmon("surface_thermal"):
            for p in glob.glob(os.path.join(d, "temp*_input")):
                lab = _rd(p[:-len("_input")] + "_label") or ""
                if lab.startswith("I_VTS"):
                    any_label = True
        for d in self._hwmon("surface_thermal"):
            for p in glob.glob(os.path.join(d, "temp*_input")):
                lab = _rd(p[:-len("_input")] + "_label") or ""
                if any_label and not lab.startswith("I_VTS"):
                    continue
                v = _num(p)
                if v is None:
                    return None                                  # one sensor that stops answering is a lost sensor
                vals.append(v / 1000)
        return max(vals) if vals else None

    def package_c(self):
        for d in self._hwmon("coretemp"):
            for lab in glob.glob(os.path.join(d, "temp*_label")):
                if _rd(lab) == "Package id 0":
                    v = _num(lab[:-len("_label")] + "_input")
                    return None if v is None else v / 1000
        return None

    def temps(self):
        return self.skin_c(), self.package_c()

    def rpm(self):
        for d in self._hwmon("surface_fan"):
            v = _num(os.path.join(d, "fan1_input"))
            if v is not None:
                return v
        ans = self.transport.read(fan.TC_FAN, 0x01, 1) if self.transport.can_write() else None
        return int.from_bytes(ans[:2], "little") if ans and len(ans) >= 2 else None

    def system_spl(self):
        import struct
        ans = self.transport.read(fan.TC_FAN, 0x13, 0) if self.transport.can_write() else None
        return round(struct.unpack("<f", ans[:4])[0], 3) if ans and len(ans) >= 4 else None

    def ac(self):
        for d in sorted(glob.glob(os.path.join(self.root, "sys/class/power_supply/*"))):
            if _rd(os.path.join(d, "type")) == "Mains":
                v = _num(os.path.join(d, "online"))
                if v is not None:
                    return v == 1
        return None

    def battery_pct(self):
        caps = [_num(os.path.join(d, "capacity")) for d in sorted(glob.glob(os.path.join(self.root, "sys/class/power_supply/*")))
                if _rd(os.path.join(d, "type")) == "Battery"]
        caps = [c for c in caps if c is not None]
        return min(caps) if caps else None

    def services_active(self):
        try:
            out = subprocess.run(["systemctl", "list-units", "--plain", "--no-legend", "--all", SERVICE_PATTERN],
                                 capture_output=True, text=True, timeout=10, check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []                                            # no systemd: nothing runs as a service
        found = []
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 3 and p[2] in ("active", "activating", "reloading"):
                found.append(p[0])
        return found

    def platform_profile(self):
        return fan.platform_profile(self.root)

    def loadavg(self):
        return os.getloadavg()[0]

    def product(self):
        return _rd(os.path.join(self.root, "sys/class/dmi/id/product_name")) or "unknown"

    def kernel(self):
        return os.uname().release

    def take_lock(self):
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_fd = fd
        return True

    def release_lock(self):
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None


def blob_ranges(path):
    """{channel: (min, max) or None} of the B620798F outputs of the blob, or None if the blob cannot be read."""
    try:
        from surface_power.engine.graph import build_graph
        from surface_power.loader.source import load
        loaded = load(path)
        try:
            graph = build_graph(loaded.parsed, "direct")
        except Exception:
            graph = build_graph(loaded.parsed, "classic")
        # Every fan output of the blob; a blob may list an output without a value range (Pro 9): then None, and the
        # adapter's hard bounds apply, as in the daemon.
        rng = {r.channel: (oc.min, oc.max) for r, oc in graph.output_ranges.items() if r.guid == fan.FAN_GUID}
        return {r.channel: rng.get(r.channel) for r in graph.outputs if r.guid == fan.FAN_GUID}
    except Exception:
        return None


# --- the run ---

class Runner:
    def __init__(self, env, plan, skin_start, cpu_start, log=print):
        self.env, self.plan, self.log = env, plan, log
        self.skin_start, self.cpu_start = skin_start, cpu_start
        self.samples = []
        self.sent, self.restore_frames = [], []
        self.wd = None
        self.t0 = None
        self.flag = False

    # start conditions
    def refusals(self):
        e, out = self.env, []
        if not e.is_root():
            out.append("needs root")
        if e.ac() is not True:
            out.append("AC power is missing (or unknown)")
        b = e.battery_pct()
        if b is None or b < MIN_BATTERY:
            out.append(f"battery is {b if b is not None else 'unknown'} %, needs at least {MIN_BATTERY} %")
        active = e.services_active()
        if active:
            out.append(f"{', '.join(active)} is active; stop it first (systemctl stop ...)")
        skin, pkg = e.temps()
        if skin is None or pkg is None:
            out.append(f"a temperature sensor is missing (skin {skin}, CPU package {pkg}); no write test without both")
        else:
            if skin >= self.skin_start:
                out.append(f"skin sensor is at {skin:.1f} C, start limit {self.skin_start:g} C; let it cool down")
            if pkg >= self.cpu_start:
                out.append(f"CPU package is at {pkg:.1f} C, start limit {self.cpu_start:g} C; let it cool down")
        if self.plan.channel in LIGHT_LOAD_ONLY and e.loadavg() >= 1.0:
            out.append(f"channel {self.plan.channel} needs a light load (load average {e.loadavg():.2f} >= 1.0)")
        if not e.transport.can_write():
            out.append(f"{e.transport.path} is not available: load the module surface_aggregator_cdev")
        return out

    # logging and limits
    def sample(self, phase):
        e = self.env
        skin, pkg = e.temps()
        rpm = e.rpm()
        t = round(e.now() - self.t0, 1)
        self.samples.append([t, skin, pkg, rpm, phase])
        if phase not in ("verify", "probe"):
            if skin is None or pkg is None:
                raise Abort("a temperature sensor stopped answering")
            if skin >= ABORT_SKIN_C:
                raise Abort(f"skin sensor reached {skin:.1f} C (limit {ABORT_SKIN_C:g} C)")
            if pkg >= ABORT_CPU_C:
                raise Abort(f"CPU package reached {pkg:.1f} C (limit {ABORT_CPU_C:g} C)")
        return rpm

    def span(self, seconds, phase, writing=False):
        for _ in range(int(seconds)):
            if self.flag and phase == "verify":
                return
            self.env.sleep(1)
            if writing and self.wd:
                self.wd.beat()
            if self.env.now() - self.t0 >= RUN_S and phase not in ("verify", "probe"):
                raise Abort(f"the run reached its {RUN_S // 60} minute limit")
            self.sample(phase)

    def write(self, frame):
        check_frame(frame)
        if not fan.send_frame(self.env.transport, frame):
            raise Abort(f"the request {frame.label()} failed")
        self.sent.append(frame.to_dict())
        self.log(f"sent {frame.label()} payload {frame.payload.hex()}")

    def restore(self):
        errors = []
        seen = {}
        for fr in self.restore_frames:
            seen[(fr.tc, fr.iid, fr.cid)] = fr
        for fr in seen.values():
            for _ in range(3):
                if fan.send_frame(self.env.transport, fr):
                    break
                self.env.sleep(0.2)
            else:
                errors.append(f"{fr.label()}: restore frame {fr.payload.hex()} not sent")
        return errors

    def verify(self):
        base = [s[3] for s in self.samples if s[4] == "baseline" and s[3] is not None]
        after = [s[3] for s in self.samples if s[4] == "verify" and s[3] is not None]
        if not base or not after:
            return {"result": "cannot verify (no fan speed readings)"}
        b, a = statistics.median(base), statistics.median(after[-20:])
        ok = abs(a - b) <= max(300, 0.3 * b)
        return {"baseline_rpm": b, "after_restore_rpm": a,
                "result": "fan speed is back near the baseline" if ok else
                          "fan speed does NOT look like the baseline: reboot to be safe"}

    def run(self, res):
        e = self.env
        self.t0 = e.now()
        code = 0
        old = {}

        def on_signal(n, _f):
            raise Abort(f"signal {n}")
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old[s] = signal.signal(s, on_signal)
        restore_errors = []
        restored = False
        try:
            self.log(f"baseline: {BASELINE_S} s, nothing written")
            self.span(BASELINE_S, "baseline")
            self.wd = Watchdog(e.transport_factory)
            self.wd.start()
            self.restore_frames.append(self.plan.restore)           # recorded BEFORE the first write
            self.wd.add_restore(self.plan.restore)
            for st in self.plan.steps:
                self.log(f"write: {st.label}, held {st.hold_s} s")
                self.write(st.frame)
                self.span(st.hold_s, "write", writing=True)
            self.log("restore")
            restore_errors = self.restore()
            restored = not restore_errors
            self.span(SETTLE_S, "settle", writing=True)
        except Abort as a:
            res["aborted"] = str(a)
            self.log(f"ABORTED: {a}")
            code = 2
        finally:
            for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(s, signal.SIG_IGN)                     # nothing may interrupt the restore
            if self.restore_frames and not restored:
                restore_errors = self.restore()
            if self.wd:
                self.wd.stop(clean=not restore_errors)
            res["sent"], res["restore_errors"] = self.sent, restore_errors
            if restore_errors:
                self.log("RESTORE FAILED: " + "; ".join(restore_errors))
                code = 1
            for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                def stop_logging(n, _f):
                    self.flag = True
                signal.signal(s, stop_logging)
            self.log(f"logging {VERIFY_S} s after the restore")
            try:
                self.span(VERIFY_S, "verify")
            except Abort:
                pass
            res["verify"] = self.verify()
            self.log("verify: " + res["verify"]["result"])
            for s, h in old.items():
                signal.signal(s, h)
            res["log"] = self.samples
        return code


def print_plan(plan, log=print):
    for st in plan.steps:
        log(f"  write   {st.frame.label():<22} payload {st.frame.payload.hex():<8} wire {st.frame.wire().hex()}   ({st.label}, {st.hold_s} s)")
    log(f"  restore {plan.restore.label():<22} payload {plan.restore.payload.hex():<8} wire {plan.restore.wire().hex()}")
    for n in plan.notes:
        log(f"  note: {n}")


def main(argv=None, env=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--channel", type=int, choices=sorted(WRITABLE), help="the channel to test (one per run)")
    ap.add_argument("--probe", action="store_true", help="read only: FAN 0x01 and 0x13, and a log of rpm and temperatures")
    ap.add_argument("--dry-run", action="store_true", help="print the frames, open nothing")
    ap.add_argument("--blob", help="blob file for the output ranges (default: the UEFI variable)")
    ap.add_argument("--no-blob", action="store_true", help="no blob: only the hard bounds of the adapter limit the values")
    ap.add_argument("--seconds", type=int, default=BASELINE_S, help="--probe: length of the log")
    ap.add_argument("--skin-start", type=float, default=START_SKIN_C, help="start limit for the skin sensor, C (lower only)")
    ap.add_argument("--cpu-start", type=float, default=START_CPU_C, help="start limit for the CPU package, C (lower only)")
    ap.add_argument("--root", default="/", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.skin_start > START_SKIN_C or a.cpu_start > START_CPU_C:
        sys.exit(f"the start limits can only be lowered (skin {START_SKIN_C:g} C, CPU package {START_CPU_C:g} C)")
    if not a.probe and a.channel is None:
        sys.exit("give --channel (one of %s) or --probe" % ", ".join(map(str, sorted(WRITABLE))))
    env = env or Env(a.root)
    res = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "product": env.product(), "kernel": env.kernel(),
           "platform_profile": env.platform_profile(), "kernel_path": dataclasses.asdict(fan.kernel_path(a.root)),
           "dry_run": a.dry_run, "probe": a.probe}
    print(f"{res['product']}, kernel {res['kernel']}, platform profile {res['platform_profile']}")

    if a.probe:
        res["probe_reads"] = {"fan_rpm": env.rpm(), "system_spl": env.system_spl(), "cdev": env.transport.can_write()}
        print("probe:", json.dumps(res["probe_reads"]))
        if not env.transport.can_write():
            print("note: the debug device is missing or needs root; only sysfs values were read")
        env.t0 = env.now()
        run = Runner(env, None, a.skin_start, a.cpu_start)
        run.t0 = env.now()
        try:
            run.span(a.seconds, "probe")
        except Abort as e:
            print("stopped:", e)
        res["log"] = run.samples
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "fan.json"), "w") as f:
            json.dump(res, f, indent=1)
        return 0

    profile_value = fan.PROFILE_VALUES.get(res["platform_profile"] or "")
    ranges = None
    if not a.no_blob:
        ranges = blob_ranges(a.blob)
        if ranges is None and not a.dry_run:
            sys.exit("cannot read the blob, so the value ranges are unknown: give --blob FILE, or --no-blob to use the hard bounds only")
    if a.dry_run and profile_value is None and a.channel == 201:
        profile_value = 2
        print("note: platform profile unknown, assuming balanced (2) for the printout")
    try:
        plan = build_plan(a.channel, profile_value, ranges)
    except Refuse as e:
        sys.exit(f"refusing: {e}")
    print(f"channel {a.channel}, frames:")
    print_plan(plan)
    if a.dry_run:
        print("dry run: nothing sent, device not opened")
        return 0
    run = Runner(env, plan, a.skin_start, a.cpu_start)
    refusals = run.refusals()
    if refusals:
        sys.exit("refusing to start: " + "; ".join(refusals))
    if not env.take_lock():
        sys.exit(f"refusing to start: another run holds {env.lock_path}")
    os.makedirs(a.out, exist_ok=True)
    try:
        code = run.run(res)
    finally:
        env.release_lock()
        res["frames"] = {"steps": [s.frame.to_dict() for s in plan.steps], "restore": plan.restore.to_dict()}
        with open(os.path.join(a.out, "fan.json"), "w") as f:
            json.dump(res, f, indent=1)
    if code == 0:
        print("done; send OUT/fan.json with your report")
    return code


if __name__ == "__main__":
    sys.exit(main())
