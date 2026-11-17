"""surface-power-report: what other device owners can share for validation (FR-M7, spec tools 3).

Contains no blob content (no parameter values, no decoded text) and no serial numbers. A SHA-256 of the decoded text tells blob
versions apart.
"""
import argparse
import hashlib
import os
import platform
import sys
import time

from .. import supervisor
from ..adapters import gpu
from ..adapters import inputs as inp
from ..adapters.actions import ThermalActions
from ..adapters.cpufreq import THROTTLE, ThrottleStage
from ..adapters import notneeded
from ..adapters.prochot import BATT_MODE, ProchotStage
from ..adapters.fan import TC_FAN, FanStage, handles as fan_handles
from ..adapters.outputs import OutputStage
from ..engine.graph import build_graph
from ..loader import container, parser
from ..loader.errors import BlobError
from ..loader.source import EFIVAR, NoBlob, read_raw

MODULES = ("surface_aggregator", "surface_aggregator_cdev", "surface_temp", "surface_fan", "surface_dtx", "surface_battery",
           "surface_charger", "surface_platform_profile")


def fan_probe(transport, want_spl: bool = False) -> list[str]:
    """Read-only probe of FAN 0x01 iid 1 (rpm), once. FAN 0x13 iid 0 (system SPL) only if a blob output needs it (`want_spl`):
    the Pro 9 does not answer it and no blob output uses it (11-fan-charging.md 8.1)."""
    import struct
    if not transport.can_write():
        return ["fan probe: skipped (the debug device is missing or needs root)"]
    rpm = transport.read(TC_FAN, 0x01, 1)
    text = ("fan probe (read only): "
            f"FAN 0x01 iid 1 rpm: {struct.unpack('<H', rpm[:2])[0] if rpm and len(rpm) >= 2 else 'no answer'}")
    if want_spl:
        spl = transport.read(TC_FAN, 0x13, 0)
        text += (f"; FAN 0x13 iid 0 system SPL: "
                 f"{round(struct.unpack('<f', spl[:4])[0], 3) if spl and len(spl) >= 4 else 'no answer'}")
    return [text]


SLEEP = time.sleep
FIELD_LABELS = {"max_platform_power_mw": "peak power", "max_steady_state_power_mw": "sustained power",
                "high_freq_impedance_mohm": "resistance", "no_load_voltage_mv": "open-circuit voltage",
                "available_input_power_mw": "available input power", "rest_of_platform_power_mw": "rest of platform"}


def ec_power_lines(pp, ac) -> list[str]:
    """What the 0x53 request and the adapter rating request answered, per field (a 0 is an answer, not a missing one)."""
    out = []
    for tid, name, base in pp._batteries():
        if pp._sysfs_battery(name):
            continue
        data = pp.answers.get(tid)
        who = f"battery {tid} ({'SAM' if tid == 1 else 'KIP'})"
        if not data:
            out.append(f"EC 0x53 request, {who}: no answer")
            continue
        fields = []
        for _, fmt, attr, ch in pp.FIELDS:
            v = pp.values.get(inp.ChannelRef(inp.POWER, base + ch))
            unit = {"mw": "mW", "mv": "mV", "mohm": "mOhm"}[attr.rsplit("_", 1)[1]]
            fields.append(f"{FIELD_LABELS[attr]} {v} {unit}" + (" (zero)" if v == 0 else ""))
        out.append(f"EC 0x53 request, {who}: answered {len(data)} bytes; " + "; ".join(fields))
    if pp._want_artg() and not pp._sysfs_artg():
        raw = pp.artg_raw
        if raw is None:
            out.append("EC adapter rating request: no answer")
        elif raw == 0x80000000:
            out.append("EC adapter rating request: answered \"not present\" (0x80000000), the daemon keeps the input invalid")
        elif raw == 0:
            out.append("EC adapter rating request: answered 0 mW" + (
                " while on AC: the daemon counts this as unknown (input invalid) and asks again" if ac else
                " (on battery 0 is a valid value: no adapter)"))
        else:
            out.append(f"EC adapter rating request: answered {raw} mW")
    return out


def main(argv=None, fan_transport=None, ec_transport=None) -> int:
    ap = argparse.ArgumentParser(prog="surface-power-report", description="Validation report without blob content.")
    ap.add_argument("--blob", help="blob file (default: the UEFI variable)")
    ap.add_argument("--with-ec", action="store_true", help="also check whether EC requests answer (needs root)")
    ap.add_argument("--root", default="/", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    root = args.root
    lines = []
    dev = supervisor.detect(root)
    prof = supervisor.find_profile(dev)
    lines += [f"device: {dev.product}", f"sku: {dev.sku}", f"vendor ok: {dev.microsoft}", f"SMF present: {dev.smf}",
              f"profile: {prof.file + (' (tested)' if prof.tested else ' (untested)') if prof else 'none'}",
              f"kernel: {platform.release()}",
              "modules: " + ", ".join(m for m in MODULES if os.path.isdir(os.path.join(root, "sys/module", m)))]
    profile, profile_src = inp.platform_profile(root)
    lines.append(f"platform profile: {profile or 'not found'}" + (f" ({profile_src})" if profile else ""))
    writers = supervisor.limit_writers(root)
    if writers:
        lines.append(f"WARNING: {', '.join(writers)} running: it writes power limits itself; measured limits then differ "
                     "from the computed ones (stop it for a test)")
    path = args.blob or os.path.join(root, EFIVAR.lstrip("/"))
    try:
        data, source, is_var = read_raw(path)
        dec = container.decode(data, efivar=is_var or path.endswith(os.path.basename(EFIVAR)))
        parsed = parser.parse_text(dec.text, dec.form)
        graph = build_graph(parsed, prof.variant if prof else "direct")
    except NoBlob as e:
        lines.append(f"blob: not found ({e})")
        print("\n".join(lines))
        return 0
    except BlobError as e:
        lines.append(f"blob: found but not usable: {e.message}" + (f" (line {e.line})" if e.line else ""))
        print("\n".join(lines))
        return 0
    lines += [f"blob: form {dec.form}, {len(dec.text)} bytes, sha256 {hashlib.sha256(dec.text.encode()).hexdigest()}",
              f"blob lines: {len(parsed.algorithms)}, instances {sorted(graph.order)}",
              "block types: " + ", ".join(sorted({b.kind for b in graph.blocks})),
              f"usable for control: {graph.usable_for_control}"]
    for u in graph.unsupported:
        lines.append(f"  unsupported: {u.guid[:8]} ({u.reason.split(':')[0]}) -> {', '.join(str(r) for r in u.reaches) or 'no output'}")
    sources = [inp.HwmonTemperatures(root), inp.SliderState(root), inp.Batteries(root), inp.Posture(root, lambda: None),
               inp.DisplayBrightness(root), inp.PlatformEvents(root), inp.BatteryCurrent(root), inp.StandbyPhase(root),
               inp.UsbSourcePower(root), inp.DgpuOnBattery(root), inp.ProximitySensor(root),
               inp.XboxController(root), inp.DgpuSensors(root), inp.CpuTempAverage(root)]
    provided = set().union(*(s.channels for s in sources))
    # EC power data (spec inputs 6): from the kernel attributes if present, else only through the debug device. Only what the
    # blob reads is asked for: a command the device does not know times out (kernel#195).
    wanted = set(graph.inputs)
    pp = inp.PlatformPower(root, transport=ec_transport)
    pp.restrict(wanted)
    be = inp.BatteryEcState(root, transport=ec_transport)
    be.restrict(wanted)
    if not pp.needs_cdev:
        lines.append("EC power data: kernel attributes (power_supply dptf group)")
        provided |= pp.channels
        via_cdev = set()
    else:
        lines.append("EC power data: debug device only (needs root and surface_aggregator_cdev; "
                     "kernel attributes missing)")
        via_cdev = set(pp.channels)
    ec_state = {inp.ChannelRef(inp.POWER, ch) for ch, _ in be.queries}   # SAM 0x1A / 0x69: cdev only
    via_cdev |= ec_state
    if args.with_ec and pp.needs_cdev:
        ac = inp._ac_online(root)
        pp.refresh(ac)
        if pp.retry_soon:                                    # the daemon asks again after 5 s; so does the report, once
            SLEEP(2)
            first = pp.artg_raw
            pp.refresh(ac)
            lines.append(f"EC adapter rating: first request {first} mW on AC, second request after 2 s {pp.artg_raw} mW")
        lines += ec_power_lines(pp, ac)
        for tid, name, base in pp._batteries():
            if pp.answers.get(tid):
                provided |= {inp.ChannelRef(inp.POWER, base + f[3]) for f in pp.FIELDS}   # a field that reads 0 still answered
        if pp.values.get(pp.ARTG) is not None:
            provided.add(pp.ARTG)
        via_cdev -= provided
    if args.with_ec and be.queries:
        got = be.read(0)
        lines.append("EC battery state answers: " + ", ".join(f"{r.channel}={got.get(r, 'no answer')}" for r in sorted(ec_state))
                     + ("" if len(be.queries) == len(inp.BatteryEcState.QUERIES) else
                        " (the other query is not sent: the blob does not read it)"))
        for r in ec_state:
            if got.get(r) is not None:
                provided.add(r)
        via_cdev -= provided
    ec_pos = len(lines)
    lines.append("inputs:")
    for ref in sorted(graph.inputs):
        if ref.guid == inp.PLACEHOLDER:
            continue
        state = "provided" if ref in provided else "needs the debug device" if ref in via_cdev else "not available"
        lines.append(f"  {ref}: {state}")
    stage = OutputStage(root, graph.output_ranges, dry_run=True, pl3=prof.pl3 if prof else None,
                        pl4=prof.pl4 if prof else None)
    throttle = ThrottleStage(root, [r for r in graph.outputs if r.guid == THROTTLE], dry_run=True)
    fan = FanStage(root, [r for r in graph.outputs if fan_handles(r)], graph.output_ranges, transport=fan_transport,
                   dry_run=True, thresholds=bool(prof and prof.fan_thresholds))
    prochot = ProchotStage(root, [r for r in graph.outputs if r == BATT_MODE], transport=fan_transport, dry_run=True)
    if args.with_ec and fan.chans:                          # the fan commands are asked only if the blob has fan outputs
        probe = fan_probe(fan.transport, want_spl=any(c.spec.tc == TC_FAN and c.spec.cid == 0x13 for c in fan.chans.values()))
        lines[:] = lines[:ec_pos] + probe + lines[ec_pos:]
    dgpu_ref = inp.ChannelRef("0B7B5350-BB8C-4459-8D4A-A75C57073801", 201)
    stand_in = gpu.find_nvidia_gpu(root) is not None          # the stand-in needs an NVIDIA dGPU
    lines.append("outputs:")
    missing = []
    cdev_wait = []                      # outputs with a SAM path that is not usable in this run
    for ref in sorted(graph.outputs):
        t = stage.targets.get(ref)
        if t is not None:
            kind = "writable (MSR, needs root)" if t.name == "pl3" else "writable (powercap)"
        elif ref.channel == 59 and ref.guid == dgpu_ref.guid and os.geteuid() != 0 and os.path.exists(
                os.path.join(root, "dev/cpu/0/msr")) and not (prof and prof.pl3 is False):
            kind = "writable as root (MSR 0x615)"
        elif ref in ThermalActions.channels:
            kind = "thermal action (hibernate / power off)"
        elif ref.guid == THROTTLE:
            what = throttle.describe(ref)
            kind = f"writable ({what})" if throttle.has_target(ref) else "no Linux path (logged only)"
            if not throttle.has_target(ref):
                missing.append(ref)
        elif ref in prochot.refs:
            kind = prochot.describe(ref)
            if not prochot.has_path(ref, report=True):
                missing.append(ref)
                if prochot.needs_cdev(ref):
                    cdev_wait.append(ref)
        elif fan_handles(ref):
            kind = fan.describe(ref)
            if not fan.has_path(ref, report=True):
                missing.append(ref)
                if fan.needs_cdev(ref):
                    cdev_wait.append(ref)
        elif notneeded.reason(ref, root):
            kind = "not needed on Linux: " + notneeded.reason(ref, root)
        elif ref == dgpu_ref and stand_in:
            kind = "clock-cap stand-in (DO-1)" + ("" if prof and prof.dgpu_clock_cap else ", off in this profile")
            if not (prof and prof.dgpu_clock_cap):
                missing.append(ref)
        else:
            kind = "no Linux path (logged only)"
            missing.append(ref)
        lines.append(f"  {ref}: {kind}")
    for note in stage.notes + throttle.notes + fan.notes + prochot.notes + list(prof.notes if prof else ()):
        lines.append(f"  note: {note}")
    if not fan.transport.can_write():
        outs = sum(1 for r in graph.outputs if fan.needs_cdev(r) or prochot.needs_cdev(r))
        hint = inp.cdev_hint(outs, len(via_cdev & set(graph.inputs)))
        if hint:
            lines.append(hint)
    rest = len(missing) - len(cdev_wait)
    if not missing:
        lines.append("ready for control: yes")
    else:
        parts = ([f"{len(cdev_wait)} need root and surface_aggregator_cdev"] if cdev_wait else []) + \
                ([f"{rest} have no Linux path"] if rest else [])
        lines.append(f"ready for control: no, not ready in this run: {len(missing)} of {len(graph.outputs)} outputs "
                     f"({', '.join(parts)})")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
