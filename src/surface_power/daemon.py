"""surface-powerd: the control daemon (spec supervisor 1, 7-9; monitoring).

States:
    control    engine outputs are written through the output stage
    dry-run    everything is computed and logged, nothing is written (untested devices, --dry-run)
    emergency  firmware values are written once; the engine keeps running so that recovery is possible
    yielded    another process changes our limits: firmware values once, then hands off until restart
    paused     between "before sleep" and "after resume"

Signals: SIGTERM/SIGINT stop (restore firmware values); SIGUSR2 = before sleep; SIGUSR1 = after resume. The systemd sleep hook
sends the two USR signals. Without the hook, a jump between CLOCK_BOOTTIME and CLOCK_MONOTONIC is taken as a resume.
"""
import argparse
import datetime as dt
import os
import signal
import socket
import sys
import time

from . import monitor, supervisor
from .adapters import inputs as inp
from .adapters.actions import ThermalActions
from .adapters import gpu
from .adapters.gpu import ClockCapStandIn
from .adapters.outputs import OutputStage
from .engine.engine import Engine
from .engine.graph import build_graph, outputs_reached_by
from .engine.pid import STATE_NAMES
from .engine.registry import INTERNAL_NODE
from .engine.scheduler import Scheduler
from .loader.errors import BlobError
from .loader.model import ChannelRef

# Inputs whose absence is a documented deviation with a Windows fallback (spec deviations): they never block control.
ACCEPTED_ABSENT = frozenset({ChannelRef(inp.STATE, 41)})  # display brightness, DV-3
from .loader.source import EFIVAR, NoBlob, load

LIMITS_GUID = "0B7B5350-BB8C-4459-8D4A-A75C57073801"
DGPU = ChannelRef(LIMITS_GUID, 201)    # dGPU limit; written only through the opt-in stand-in (DO-1)
OUTPUT_NAMES = {51: "PL1", 52: "PL2", 53: "PL4", 54: "psys PL1", 55: "psys PL2", 59: "PL3", 70: "battery mode", 201: "dGPU"}


class Idle(Exception):
    """Nothing to do on this device (FR-D2). The daemon exits without writing anything."""


class _NoTransport:
    def request(self, tid, cid, iid=1):
        return None


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="surface-powerd", description="Surface SMF power management daemon.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="compute and log, write nothing")
    mode.add_argument("--control", action="store_true", help="write limits even on an untested device")
    ap.add_argument("--blob", help="blob file (default: the UEFI variable)")
    ap.add_argument("--variant", choices=("classic", "direct"), help="override the controller variant")
    ap.add_argument("--root", default="/", help=argparse.SUPPRESS)          # fake sysfs root for tests
    ap.add_argument("--run-dir", default="/run/surface-power")
    ap.add_argument("--state-dir", default="/var/lib/surface-power")
    ap.add_argument("--dgpu-clock-cap", action="store_true",
                    help="enable the dGPU clock-cap stand-in (deviation DO-1) even if the profile does not")
    ap.add_argument("--allow-partial-outputs", action="store_true",
                    help="control even if some outputs of the blob have no Linux path (field tests only)")
    ap.add_argument("--no-ec", action="store_true", help="do not send EC requests (platform power data stays invalid)")
    return ap.parse_args(argv)


def _sd_notify(message: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(message.encode(), addr)
    except OSError:
        pass


def _celsius(dk: float) -> float:
    return round((dk - 2731) / 10, 1)


class Daemon:
    def __init__(self, args, journal: monitor.Journal | None = None):
        self.args = args
        self.journal = journal or monitor.Journal()
        self.log = self.journal.log
        root = args.root
        self.device = supervisor.detect(root)
        if not self.device.supported:
            raise Idle(f"not a Surface SMF device (vendor {self.device.vendor!r}, SMF {self.device.smf})")
        self.profile = supervisor.find_profile(self.device)
        blob_path = args.blob or (EFIVAR if root == "/" else os.path.join(root, EFIVAR.lstrip("/")))
        try:
            self.loaded = load(blob_path if (args.blob or root != "/") else None)
        except NoBlob as e:
            raise Idle(str(e)) from None
        except BlobError as e:
            raise Idle(f"blob not usable: {e}") from None
        variant = args.variant or (self.profile.variant if self.profile else "direct")
        try:
            self.graph = build_graph(self.loaded.parsed, variant)
        except BlobError as e:
            raise Idle(f"blob not usable: {e}") from None
        self.engine = Engine(self.graph)
        self.sched = Scheduler(self.engine)

        # Mode (spec supervisor 2, FR-D3).
        if args.dry_run:
            self.mode = "dry-run"
        elif args.control or (self.profile and self.profile.tested):
            self.mode = "control"
        else:
            self.mode = "dry-run"
        if self.mode == "control" and supervisor.thermald_running(root):
            raise Idle("thermald is running and would fight over PL1; stop it (systemd: Conflicts=) or use --dry-run")
        if not self.graph.usable_for_control and self.mode == "control":
            self.log("warning", "blob has unsupported blocks on an output path; dry-run only")
            self.mode = "dry-run"
        self.state = self.mode

        # Inputs.
        transport = _NoTransport() if args.no_ec else None
        dtx = (lambda: None) if root != "/" else inp.dtx_device_mode
        self.platform = inp.PlatformPower(root, transport=transport)
        self.battery_ec = inp.BatteryEcState(root, transport=transport)
        self.gpu_probe = gpu.ClientProbe(root, gpu.find_nvidia_gpu(root))   # shared: one /proc scan per 10 s at most
        sources = [inp.HwmonTemperatures(root), inp.SliderState(root), inp.Batteries(root), inp.Posture(root, dtx),
                   self.platform, inp.DisplayBrightness(root), inp.PlatformEvents(root), inp.BatteryCurrent(root),
                   self.battery_ec, inp.StandbyPhase(root), inp.UsbSourcePower(root),
                   inp.DgpuOnBattery(root), inp.ProximitySensor(root), inp.XboxController(root),
                   inp.DgpuSensors(root, probe=self.gpu_probe)]
        self.cpu_ref = ChannelRef(inp.TEMP, 102)
        # Skin channels that controllers regulate on; the emergency rules watch them (spec supervisor 4).
        self.skin_refs = sorted({c.line.inputs[0] for c in self.graph.controllers if c.line.inputs[0].guid == inp.TEMP})
        self.reader = inp.InputReader(set(self.graph.inputs) | {self.cpu_ref}, sources)
        self.slider = next(s for s in sources if isinstance(s, inp.SliderState))

        # Outputs.
        p = self.profile
        kw = dict(narrow=p.narrow if p else None, pl3=p.pl3 if p else None, pl4=p.pl4 if p else None)
        self.outputs = OutputStage(root, self.graph.output_ranges, dry_run=(self.mode != "control"), **kw)
        self.preview = OutputStage(root, self.graph.output_ranges, dry_run=True, **kw)

        # dGPU stand-in (DO-1, per profile): with it, the dGPU limit counts as a written output.
        self.dgpu = None
        self.dgpu_note = ""
        written = set(self.outputs.targets)
        # Thermal-policy actions (hibernate / power off, spec outputs 7): handled wherever the blob has them.
        self.actions = ThermalActions()
        written |= self.actions.channels & set(self.graph.outputs)
        # PL4 and PL3 keep their value while an EC input on their path is invalid: their fallback would be far below the
        # firmware value (Pro 10: PL4 20 W instead of 45 W) [D].
        lines_ = self.loaded.parsed.algorithms
        ec_inputs = [r for r in self.graph.inputs if r.guid == inp.POWER and r.channel % 100 not in (1, 10, 12, 13, 16)]
        self.ec_guard = {out: [r for r in ec_inputs if out in outputs_reached_by(r, lines_)]
                         for out in (ChannelRef(LIMITS_GUID, 53), ChannelRef(LIMITS_GUID, 59)) if out in self.outputs.targets}
        if (args.dgpu_clock_cap or (p and p.dgpu_clock_cap)) and DGPU in self.graph.outputs:
            self.dgpu = ClockCapStandIn(root, dry_run=(self.mode != "control"), probe=self.gpu_probe)
            written.add(DGPU)

        # Inputs this system never provides (spec supervisor 2). They are fine as long as they only reach log-only outputs
        # (SB3: base sensors 605/609 -> dGPU limit). If one reaches a limit that Linux writes, the blob would run on a
        # fallback the designers did not intend for normal operation: then control is not allowed, only dry-run.
        self.mode_reason = ""
        lines = self.loaded.parsed.algorithms
        blocking = {ref: [o for o in outputs_reached_by(ref, lines) if o in written]
                    for ref in self.reader.unavailable if ref not in ACCEPTED_ABSENT}
        blocking = {ref: outs for ref, outs in blocking.items() if outs}
        # A missing input of the dGPU limit only switches the stand-in off; the other limits are still controlled
        # (SB3 without the base sensor nodes: no dGPU parity, but CPU and psys as on Windows).
        if self.dgpu and any(DGPU in outs for outs in blocking.values()):
            missing = sorted(str(r) for r, outs in blocking.items() if DGPU in outs)
            self.dgpu_note = f"dGPU stand-in off: input {', '.join(missing)} missing"
            self.dgpu = None
            blocking = {r: [o for o in outs if o != DGPU] for r, outs in blocking.items()}
            blocking = {r: outs for r, outs in blocking.items() if outs}
        # Every output must have a Linux path before the blob may control (spec supervisor 2.6) [D].
        if not self.dgpu:
            written.discard(DGPU)
        self.unwritten = sorted(set(self.graph.outputs) - written)
        reasons = []
        if blocking:
            reasons.append("; ".join(f"input {r} missing, reaches {', '.join(map(str, o))}" for r, o in sorted(blocking.items())))
        if self.unwritten and not args.allow_partial_outputs:
            reasons.append("outputs without a Linux path: " + ", ".join(
                OUTPUT_NAMES.get(r.channel, str(r)) if r.guid == LIMITS_GUID else str(r) for r in self.unwritten))
        if reasons and self.mode == "control":
            self.mode_reason = "; ".join(reasons)
            self.mode = self.state = "dry-run"
            self.outputs.dry_run = True
            if self.dgpu:
                self.dgpu.dry_run = True
        self.snapshot = None
        if self.mode == "control":
            keys = [k for k in self.outputs.snapshot_keys() if k.startswith("msr:") or os.path.exists(k)]
            self.snapshot, inherited = supervisor.Snapshot.take(args.run_dir, keys)
            if inherited:
                self.log("warning", "kept the firmware snapshot of a predecessor that did not stop cleanly")

        self.guard = supervisor.Guard(self.skin_refs, self.cpu_ref)
        self.foreign = supervisor.ForeignWatch()
        self.series = monitor.Series(args.state_dir, self._series_columns())
        self.ctl_states = {c.line.id: c.state for c in self.graph.controllers}
        self.last_slider = None
        self.results = {}
        self.values = {}
        self.before_pause = None

        tested = "tested" if (p and p.tested) else ("untested profile" if p else "no profile")
        self.log("notice", f"device {self.device.product!r} ({tested}), blob form {self.loaded.form}, "
                           f"{len(self.loaded.parsed.algorithms)} lines, variant {variant}, mode {self.mode}")
        for ref in self.reader.unavailable:
            self.log("warning", f"input {ref} unavailable on this system; it stays invalid")
        if self.mode_reason:
            self.log("warning", f"dry-run only: {self.mode_reason}")
        if self.dgpu_note:
            self.log("warning", self.dgpu_note)
        # The EC power data come through the SSAM debug device for now (NFR-11). Without it they stay invalid and the
        # blob falls back (SB3: psys 23/34.5 W), so say clearly what is missing.
        available = getattr(self.platform.transport, "available", None)
        if (self.platform.needs_cdev and available and not available()
                and any(r.guid == inp.POWER for r in self.graph.inputs)):
            self.log("warning", f"{self.platform.transport.path} missing and no kernel DPTF attributes: load the module "
                                "surface_aggregator_cdev; until then the EC power data are invalid")
        for note in self.outputs.notes:
            self.log("info", note)

    # --- helpers ---
    def _series_columns(self) -> list[str]:
        cols = ["t_ms", "kind", "state"] + [str(r) for r in sorted(self.graph.inputs)]
        for c in self.graph.controllers:
            cols += [f"ctl{c.line.id}.state", f"ctl{c.line.id}.set", f"ctl{c.line.id}.u"]
        cols += [str(r) for r in sorted(self.graph.outputs)]
        return cols

    def _core_invalid(self) -> bool:
        """True if a core input this system provides is invalid (spec supervisor 4, "core inputs lost").

        Inputs that are unavailable from the start (SB3: base sensors 605/609) are logged once at start and do not count:
        their controllers stay OFF, as on Windows when a sensor is missing. Power inputs from the EC do not count either.
        Internal nodes (e.g. a smoothed battery current on the Pro 10) are not device inputs: the reader never has them,
        and their validity follows the device inputs that are checked here.
        """
        refs = [c.line.inputs[0] for c in self.graph.controllers]
        slider = ChannelRef(inp.SYSTEM, 1)
        if slider in self.graph.inputs:
            refs.append(slider)
        absent = set(self.reader.unavailable)
        return any(self.values.get(r) is None for r in refs
                   if r.guid not in (inp.POWER, INTERNAL_NODE) and r not in absent)

    def _enter(self, state: str, reason: str) -> None:
        if state == self.state:
            return
        prio = "warning" if state in ("emergency", "yielded") else "notice"
        self.log(prio, f"state {self.state} -> {state}: {reason}")
        if state in ("emergency", "yielded", "paused") and self.state == "control" and self.snapshot:
            errors = self.snapshot.restore()
            self.outputs.forget()
            for e in errors:
                self.log("error", f"restore failed: {e}")
        self.state = state

    def _log_changes(self) -> None:
        s = self.values.get(ChannelRef(inp.SYSTEM, 1))
        if s != self.last_slider:
            self.log("info", f"slider {self.last_slider} -> {s} ({self.slider.profile}, {'AC' if self.slider.ac else 'battery'})")
            self.last_slider = s
        for c in self.graph.controllers:
            old = self.ctl_states.get(c.line.id)
            if old != c.state:
                x = c.inputs[0]
                xs = f"{_celsius(x.value)} °C" if c.line.inputs[0].guid == inp.TEMP else f"{x.value:g}"
                self.log("info", f"controller {c.line.id}: {STATE_NAMES.get(old, old)} -> {STATE_NAMES.get(c.state)} "
                                 f"(x {xs}, U {c.u / 1000:.2f} W)")
                self.ctl_states[c.line.id] = c.state

    # --- one cycle (spec supervisor 9) ---
    def cycle(self, now_ms: int) -> None:
        if self.state == "paused":
            _sd_notify("WATCHDOG=1")               # freezing tasks can take 20 s; the watchdog must not fire meanwhile
            return
        self.values = self.reader.read(now_ms)
        runs = self.sched.step(now_ms, self.values)
        rounded = self.engine.rounded_outputs()

        reason = self.guard.check(now_ms, self.values, self._core_invalid())
        if reason:
            self._enter("emergency", reason)
        elif self.state == "emergency" and not self.guard.emergency:
            self._enter(self.mode, "emergency condition cleared")

        if self.state == "control":
            changed = self.outputs.foreign_changes()
            if changed:
                names = ", ".join(f"{OUTPUT_NAMES.get(c.ref.channel, c.ref)} "
                                  f"({(c.expected_uw or 0) / 1e6:g} -> {(c.found_uw or 0) / 1e6:g} W)" for c in changed)
                self.log("warning", f"limit changed by another process: {names}")
                if self.foreign.record(now_ms):
                    self._enter("yielded", "three foreign changes within 60 s")
        hold = frozenset(out for out, refs in self.ec_guard.items() if any(self.values.get(r) is None for r in refs))
        if self.state == "control":
            self.results = self.outputs.apply(rounded, hold)
        else:
            self.results = self.preview.apply(rounded, hold)
        for prio, text in self.actions.step(now_ms, rounded, armed=(self.mode == "control")):
            self.log(prio, text)
        if self.dgpu:
            if self.state in ("control", "dry-run"):
                self.dgpu.step(now_ms, rounded[DGPU] / 1000 if DGPU in rounded else None)
            else:
                self.dgpu.release()                 # emergency, yielded: firmware state = no cap

        self._log_changes()
        for r in runs:
            self._series_row(r)
        self._write_status()
        _sd_notify("WATCHDOG=1")

    def _series_row(self, run) -> None:
        row = [run.t_ms, run.kind, self.state]
        row += ["" if self.values.get(r) is None else self.values[r] for r in sorted(self.graph.inputs)]
        for c in self.graph.controllers:
            row += [STATE_NAMES.get(c.state), c.cur, round(c.u, 3)]
        rounded = self.engine.rounded_outputs()
        row += [rounded.get(r, "") for r in sorted(self.graph.outputs)]
        self.series.write(row)

    def _write_status(self) -> None:
        def target(ref):
            if ref in self.actions.channels:
                return "thermal action" if self.mode == "control" else "thermal action (not armed)"
            res = self.results.get(ref)
            if res is not None and res.kind == "held":
                return "held (EC inputs invalid)"
            if self.state == "control":
                return self.outputs.kind(ref)
            if self.state in ("emergency", "yielded", "paused") and ref in self.outputs.targets:
                return "firmware"
            return self.preview.kind(ref)

        p = self.profile
        status = {
            "schema": 1,
            "time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "device": {"product": self.device.product, "sku": self.device.sku, "tested": bool(p and p.tested)},
            "blob": {"source": self.loaded.source, "form": self.loaded.form, "lines": len(self.loaded.parsed.algorithms),
                     "variant": self.graph.variant},
            "state": self.state,
            "slider": {"state": self.values.get(ChannelRef(inp.SYSTEM, 1)), "profile": self.slider.profile,
                       "ac": self.slider.ac},
            "controllers": [{"line": c.line.id, "input": str(c.line.inputs[0]), "state": STATE_NAMES.get(c.state),
                             "set": c.cur, "x": c.inputs[0].value if c.inputs[0].valid else None,
                             "setpoint": c.sets[c.cur].w, "u_w": round(c.u / 1000, 3)} for c in self.graph.controllers],
            "outputs": [self._output_status(ref, v, target(ref)) for ref, v in sorted(self.engine.rounded_outputs().items())],
            "inputs": [{"channel": str(r), "value": v, "valid": v is not None} for r, v in sorted(self.values.items())],
            "events": list(self.journal.events),
        }
        if self.dgpu:
            status["dgpu"] = self.dgpu.status()
        if self.actions.channels & set(self.graph.outputs):
            status["thermal_actions"] = self.actions.status()
        if self.unwritten:
            status["outputs_without_linux_path"] = [str(r) for r in self.unwritten]
        monitor.write_status(self.args.run_dir, status)

    def _output_status(self, ref: ChannelRef, value: int, target: str) -> dict:
        """Power limits in W; other outputs (thermal policy, ...) as raw blob values."""
        entry = {"channel": str(ref), "name": OUTPUT_NAMES.get(ref.channel, str(ref)) if ref.guid == LIMITS_GUID else str(ref),
                 "target": target}
        if ref.guid == LIMITS_GUID:
            t = self.outputs.targets.get(ref)
            entry.update(value_w=round(value / 1000, 3), hw_w=t.last_uw / 1e6 if t and t.last_uw else None)
        else:
            entry["value"] = value
        return entry

    # --- sleep, stop ---
    def before_sleep(self) -> None:
        if self.dgpu:
            self.dgpu.release()
        self.before_pause = self.state
        self._enter("paused", "system goes to sleep")

    def after_resume(self, now_ms: int) -> None:
        if self.state != "paused":
            self.before_pause = self.state
        self.log("notice", "resume: engine reset")
        self.sched.reset(now_ms)
        self.platform.request_refresh()
        self.battery_ec.request_refresh()
        self.state = self.before_pause or self.mode
        if self.state == "control":
            self.outputs.forget()

    def stop(self) -> None:
        if self.dgpu:
            self.dgpu.release()
        if self.snapshot:
            errors = self.snapshot.restore()
            for e in errors:
                self.log("error", f"restore failed: {e}")
            if not errors:
                self.snapshot.remove()
        self.series.close()
        self.log("notice", "stopped; firmware values restored" if self.snapshot else "stopped")


def run(argv=None) -> int:
    args = parse_args(argv)
    journal = monitor.Journal()
    try:
        d = Daemon(args, journal)
    except Idle as e:
        journal.log("notice", f"idle: {e}")
        return 0
    stop = {"flag": False}
    pending = {"sleep": False, "resume": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGUSR2, lambda *_: pending.update(sleep=True))
    signal.signal(signal.SIGUSR1, lambda *_: pending.update(resume=True))
    _sd_notify("READY=1")
    offset = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()
    try:
        while not stop["flag"]:
            now = int(time.monotonic() * 1000)
            new_offset = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()
            if pending["sleep"]:
                pending["sleep"] = False
                d.before_sleep()
            if pending["resume"] or new_offset - offset > 5:
                pending["resume"] = False
                d.after_resume(now)
            offset = new_offset
            d.cycle(now)
            time.sleep(1 - (time.monotonic() % 1))
    except Exception as e:  # restore on any unhandled error (FR-S1)
        journal.log("error", f"unhandled error: {e!r}")
        d.stop()
        raise
    _sd_notify("STOPPING=1")
    d.stop()
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
