#!/usr/bin/env python3
"""Corpus report: for each SMF blob in a folder, whether the engine can control with it and what is missing.

The blobs are Microsoft content and are not part of the repository; point the script at your own copies.
Usage: python3 -I scripts/corpus-report.py [FOLDER] [--variant direct|classic] [--detail]
FOLDER defaults to docs/research/raw/tools/fw (git-ignored). All *.bin files below it are read; MPTF and telemetry
packages are reported as "not SMF".
Prints per blob: status, unsupported algorithm GUIDs (with the number of lines and whether they reach device outputs).
At the end: which unsupported GUIDs block how many blobs.
"""
import argparse
import glob
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from surface_power.engine.graph import build_graph  # noqa: E402
from surface_power.loader import container, parser  # noqa: E402
from surface_power.loader.errors import BlobError  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEFAULT = os.path.join(ROOT, "docs", "research", "raw", "tools", "fw")


def reason_key(u) -> str:
    """Group key for an unsupported line: the GUID, or 'dynamic <kind>' / 'mode <kind>' for known blocks."""
    if u.reason.startswith("unknown algorithm"):
        return u.guid
    return f"{u.guid} ({u.reason.split(':')[0]}: {'dynamic' if 'expression' in u.reason else 'mode'})"


def report(path: str, variant: str) -> tuple[str, dict[str, list]]:
    with open(path, "rb") as f:
        data = f.read()
    try:
        dec = container.decode(data)
    except BlobError as e:
        return f"not SMF ({e})", {}
    try:
        graph = build_graph(parser.parse_text(dec.text, dec.form), variant)
    except BlobError as e:
        return f"parse error: {e}", {}
    groups = defaultdict(list)
    for u in graph.unsupported:
        groups[reason_key(u)].append(u)
    status = "control" if graph.usable_for_control else "dry-run only"
    return status, groups


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", nargs="?", default=DEFAULT)
    ap.add_argument("--variant", default="direct", choices=("direct", "classic"))
    ap.add_argument("--detail", action="store_true", help="list the reached outputs per GUID")
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.folder, "**", "*.bin"), recursive=True))
    if not files:
        print(f"no *.bin files below {args.folder}")
        return 1
    blocking = Counter()
    statuses = Counter()
    for path in files:
        status, groups = report(path, args.variant)
        statuses[status.split(" (")[0].split(":")[0]] += 1
        print(f"{os.path.relpath(path, args.folder)}: {status}")
        for key, us in sorted(groups.items()):
            reached = sorted({r for u in us for r in u.reaches})
            if reached:
                blocking[key] += 1
            mark = f"reaches {len(reached)} outputs" if reached else "log only"
            print(f"    {key}: {len(us)} lines, {mark}")
            if args.detail and reached:
                print("        " + ", ".join(str(r) for r in reached))
    print("\nStatus:", ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())))
    print("Blocking control (number of blobs):")
    for key, n in blocking.most_common():
        print(f"    {n:3d}  {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
