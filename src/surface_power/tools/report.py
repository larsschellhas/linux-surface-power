"""surface-power-report: what other device owners can share for validation (FR-M7, spec tools 3).

Contains no blob content (no parameter values, no decoded text) and no serial numbers. A SHA-256 of the decoded text tells blob
versions apart.
"""
import argparse
import hashlib
import os
import platform
import sys

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


def fan_probe(transport) -> list[str]:
    """Read-only probe of the fan commands (11-fan-charging.md 8.1): FAN 0x01 iid 1 (rpm) and FAN 0x13 iid 0 (system SPL)."""
    import struct
    if not transport.can_write():
        return ["fan probe: skipped (the debug device is missing or needs root)"]
    rpm = transport.read(TC_FAN, 0x01, 1)
    spl = transport.read(TC_FAN, 0x13, 0)
    return ["fan probe (read only): "
            f"FAN 0x01 iid 1 rpm: {struct.unpack('<H', rpm[:2])[0] if rpm and len(rpm) >= 2 else 'no answer'}; "
            f"FAN 0x13 iid 0 system SPL: {round(struct.unpack('<f', spl[:4])[0], 3) if spl and len(spl) >= 4 else 'no answer'}"]


def main(argv=None, fan_transport=None) -> int:
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
    # EC power data (spec inputs 6): from the kernel attributes if present, else only through the debug device.
    pp = inp.PlatformPower(root)
    if not pp.needs_cdev:
        lines.append("EC power data: kernel attributes (power_supply dptf_battery / dptf_power)")
        provided |= pp.channels
        via_cdev = set()
    else:
        lines.append("EC power data: debug device only (needs root and surface_aggregator_cdev; "
                     "kernel attributes missing)")
        via_cdev = set(pp.channels)
    ec_state = {inp.ChannelRef(inp.POWER, ch) for ch, _ in inp.BatteryEcState.QUERIES}   # SAM 0x1A / 0x69: cdev only
    via_cdev |= ec_state
    if args.with_ec and ec_state & set(graph.inputs):
        got = inp.BatteryEcState(root).read(0)
        lines.append("EC battery level/max-charge answers: " + ", ".join(f"{r.channel}={got.get(r)}" for r in sorted(ec_state)))
        if all(got.get(r) is not None for r in ec_state):
            provided |= ec_state
            via_cdev -= ec_state
    if args.with_ec and via_cdev - ec_state:
        pp.refresh()
        lines.append(f"EC 0x53 request answers: {pp.ok}; PSU size answers: {pp.values.get(inp.ChannelRef(inp.POWER, 7)) is not None}")
        if pp.ok:
            provided |= pp.channels
            via_cdev -= pp.channels
    if args.with_ec:
        lines += fan_probe(fan.transport)
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
    dgpu_ref = inp.ChannelRef("0B7B5350-BB8C-4459-8D4A-A75C57073801", 201)
    stand_in = gpu.find_nvidia_gpu(root) is not None          # the stand-in needs an NVIDIA dGPU
    lines.append("outputs:")
    missing = []
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
        elif fan_handles(ref):
            kind = fan.describe(ref)
            if not fan.has_path(ref, report=True):
                missing.append(ref)
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
    for note in stage.notes + throttle.notes + fan.notes + prochot.notes:
        lines.append(f"  note: {note}")
    lines.append("ready for control: " + ("yes" if not missing else
                 f"no, {len(missing)} of {len(graph.outputs)} outputs have no Linux path"))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
