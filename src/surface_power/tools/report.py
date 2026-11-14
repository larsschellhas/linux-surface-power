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
from ..adapters import inputs as inp
from ..adapters.outputs import OutputStage
from ..engine.graph import build_graph
from ..loader import container, parser
from ..loader.errors import BlobError
from ..loader.source import EFIVAR, NoBlob, read_raw

MODULES = ("surface_aggregator", "surface_aggregator_cdev", "surface_temp", "surface_dtx", "surface_battery",
           "surface_platform_profile")


def main(argv=None) -> int:
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
    sources = [inp.HwmonTemperatures(root), inp.SliderState(root), inp.Batteries(root), inp.Posture(root, lambda: None)]
    provided = set().union(*(s.channels for s in sources))
    if args.with_ec:
        pp = inp.PlatformPower(root)
        pp.refresh()
        lines.append(f"EC 0x53 request answers: {pp.ok}; PSU size answers: {pp.values.get(inp.ChannelRef(inp.POWER, 7)) is not None}")
        if pp.ok:
            provided |= pp.channels
    else:
        provided |= inp.PlatformPower(root, transport=None).channels
    lines.append("inputs:")
    for ref in sorted(graph.inputs):
        if ref.guid == inp.PLACEHOLDER:
            continue
        lines.append(f"  {ref}: {'provided' if ref in provided else 'not available'}")
    stage = OutputStage(root, graph.output_ranges, dry_run=True)
    lines.append("outputs:")
    for ref in sorted(graph.outputs):
        lines.append(f"  {ref}: {'writable (powercap)' if ref in stage.targets else 'log-only'}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
