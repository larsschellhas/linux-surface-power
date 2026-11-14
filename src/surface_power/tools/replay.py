"""surface-power-replay: run the engine on a recorded input series (spec tools 2, FR-M6).

Input CSV: header `t_ms` plus channel columns (e.g. `2E8B14C2/509`, `2DD6960F/1`), one row per second, values in blob units,
an empty cell = invalid. An optional `event` column may say `reset` (resume). Unknown columns are ignored with a warning.
Output CSV: one row per engine run (and one `reset` row per reset), with controller states and rounded device outputs.
The result is deterministic: the same blob and input give the same file.
"""
import argparse
import csv
import sys

from ..engine.engine import Engine
from ..engine.graph import build_graph
from ..engine.helpers import round_half_up
from ..engine.pid import STATE_NAMES
from ..engine.scheduler import Scheduler
from ..loader.errors import BlobError
from ..loader.model import ChannelRef
from ..loader.source import NoBlob, load

IGNORED = {"t_ms", "event", "kind"}


def _match_column(name: str, graph) -> ChannelRef | None:
    """'2E8B14C2/509' or 'GUID/509' -> the matching device input of the graph."""
    if "/" not in name:
        return None
    prefix, _, ch = name.partition("/")
    try:
        channel = int(ch)
    except ValueError:
        return None
    for ref in graph.inputs:
        if ref.channel == channel and ref.guid.startswith(prefix.upper()):
            return ref
    return None


def _fmt(v: float) -> str:
    return repr(round(v, 6)) if v != int(v) else str(int(v))


def replay(graph, rows, out):
    eng = Engine(graph)
    sched = Scheduler(eng)
    ctls = graph.controllers
    outs = sorted(graph.outputs)
    header = ["t_ms", "kind"]
    for c in ctls:
        header += [f"ctl{c.line.id}.state", f"ctl{c.line.id}.set", f"ctl{c.line.id}.u"]
    header += [str(r) for r in outs]
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)

    def emit(t, kind):
        row = [t, kind]
        for c in ctls:
            row += [STATE_NAMES.get(c.state, c.state), c.cur, _fmt(c.u)]
        values = eng.outputs()
        row += [round_half_up(values[r]) if r in values else "" for r in outs]
        w.writerow(row)

    for t, inputs, event in rows:
        if event == "reset":
            runs = sched.reset(t)
            emit(t, "reset")
            for r in runs:
                emit(t, r.kind)
        for r in sched.step(t, inputs):
            emit(t, r.kind)


def read_rows(path, graph, warn):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "t_ms" not in reader.fieldnames:
            raise ValueError("input CSV needs a t_ms column")
        cols = {}
        for name in reader.fieldnames:
            if name in IGNORED:
                continue
            ref = _match_column(name, graph)
            if ref is None:
                warn(f"column {name!r} is not an input of this blob; ignored")
            else:
                cols[name] = ref
        rows = []
        for rec in reader:
            inputs = {ref: (float(rec[name]) if rec[name] not in ("", None) else None) for name, ref in cols.items()}
            rows.append((int(float(rec["t_ms"])), inputs, (rec.get("event") or "").strip()))
        return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="surface-power-replay", description="Run the SMF engine on recorded inputs.")
    ap.add_argument("blob")
    ap.add_argument("input")
    ap.add_argument("--variant", default="classic", choices=("classic", "direct"))
    ap.add_argument("--out", help="output CSV (default: stdout)")
    args = ap.parse_args(argv)
    try:
        graph = build_graph(load(args.blob).parsed, args.variant)
        rows = read_rows(args.input, graph, lambda m: print(f"surface-power-replay: {m}", file=sys.stderr))
    except (NoBlob, BlobError, OSError, ValueError) as e:
        print(f"surface-power-replay: {e}", file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, "w", newline="") as f:
            replay(graph, rows, f)
    else:
        replay(graph, rows, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
