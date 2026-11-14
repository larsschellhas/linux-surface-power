"""surface-power-blob: show what a blob contains and whether it can be used (spec tools 1).

Exit codes: 0 ok (the summary says whether the blob is usable for control), 1 the blob has errors, 2 no blob found.
The output shows parameter values. It is meant for local review; for sharing use surface-power-report.
"""
import argparse
import json
import sys
from collections import Counter

from ..engine.graph import Graph, build_graph
from ..engine.scheduler import PERIOD_MS
from ..loader.errors import BlobError
from ..loader.model import ChannelRef
from ..loader.source import NoBlob, load


def _ref_list(refs) -> str:
    return ", ".join(str(r) for r in refs) or "-"


def _block_counts(graph: Graph) -> Counter:
    c = Counter(b.kind for b in graph.blocks)
    c["pid_parameter_set"] = sum(len(p.sets) for p in graph.controllers) or 0
    if not c["pid_parameter_set"]:
        del c["pid_parameter_set"]
    for u in graph.unsupported:
        c[f"unsupported:{u.guid[:8]}"] += 1
    return c


def summary(loaded, graph: Graph) -> list[str]:
    p = loaded.parsed
    inst = sorted(graph.order)
    lines = [
        f"source: {loaded.source}",
        f"form {loaded.form}, {loaded.size} bytes decoded, {len(p.algorithms)} algorithm lines, "
        f"{len(p.input_channels)} input and {len(p.output_channels)} output channel lines"
        + (f", {len(p.other_channels)} other channel lines" if p.other_channels else ""),
        "instances: " + (", ".join(f"{i} ({PERIOD_MS[i]} ms)" for i in inst) or "-")
        + ("  [instance 0 is untested]" if 0 in inst else ""),
        "blocks: " + ", ".join(f"{k} {n}" for k, n in sorted(_block_counts(graph).items())),
        f"device inputs: {_ref_list(sorted(graph.inputs))}",
        f"device outputs: {_ref_list(sorted(graph.outputs))}",
    ]
    if graph.unsupported:
        lines.append("unsupported lines:")
        for u in graph.unsupported:
            lines.append(f"  line {u.line}: {u.reason} -> reaches {_ref_list(u.reaches)}")
    else:
        lines.append("unsupported lines: none")
    if graph.warnings:
        lines.append("warnings:")
        lines += [f"  {'line ' + str(n) + ': ' if n else ''}{w}" for n, w in graph.warnings]
    lines.append(f"usable for control: {'yes' if graph.usable_for_control else 'no'}")
    return lines


def graph_lines(graph: Graph) -> list[str]:
    out = []
    for i, blocks in graph.order.items():
        out.append(f"instance {i}:")
        for b in blocks:
            ln = b.line
            out.append(f"  stage {ln.stage} line {ln.line} {b.kind} id {ln.id}: {_ref_list(ln.inputs)} -> "
                       f"{_ref_list(ln.outputs)} | {','.join(ln.params)}")
    return out


def _writers(graph: Graph) -> dict[ChannelRef, object]:
    return {r: b for b in graph.blocks for r in b.line.outputs}


def output_lines(graph: Graph) -> list[str]:
    writers = _writers(graph)
    out = []

    def walk(ref: ChannelRef, depth: int, seen: set):
        b = writers.get(ref)
        pad = "  " * depth
        if b is None:
            note = "" if not ref.internal else " (never written: 0)"
            out.append(f"{pad}{ref}{note}")
            return
        out.append(f"{pad}{ref} <- {b.kind} id {b.line.id} (line {b.line.line})")
        if ref in seen:
            out.append(f"{pad}  ... (loop)")
            return
        for r in b.line.inputs:
            walk(r, depth + 1, seen | {ref})

    for ref in sorted(graph.outputs):
        walk(ref, 0, set())
    return out


def dot(graph: Graph) -> str:
    lines = ["digraph smf {", "  rankdir=LR;"]
    for b in graph.blocks:
        node = f"b{b.line.line}"
        lines.append(f'  {node} [shape=box,label="{b.kind}\\nid {b.line.id}"];')
        for r in b.line.inputs:
            lines.append(f'  "{r}" -> {node};')
        for r in b.line.outputs:
            lines.append(f'  {node} -> "{r}";')
    lines.append("}")
    return "\n".join(lines)


def as_json(loaded, graph: Graph) -> dict:
    return {
        "source": loaded.source, "form": loaded.form, "size": loaded.size,
        "lines": len(loaded.parsed.algorithms), "instances": sorted(graph.order),
        "blocks": dict(_block_counts(graph)),
        "inputs": [str(r) for r in sorted(graph.inputs)], "outputs": [str(r) for r in sorted(graph.outputs)],
        "unsupported": [{"line": u.line, "reason": u.reason, "reaches": [str(r) for r in u.reaches]} for u in graph.unsupported],
        "warnings": [{"line": n, "text": w} for n, w in graph.warnings],
        "usable_for_control": graph.usable_for_control,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="surface-power-blob", description="Inspect a Surface SMF configuration blob.")
    ap.add_argument("path", nargs="?", help="blob, capsule or text file (default: the UEFI variable)")
    ap.add_argument("--graph", action="store_true", help="list all blocks in evaluation order")
    ap.add_argument("--outputs", action="store_true", help="show the chain of blocks behind every device output")
    ap.add_argument("--dot", action="store_true", help="print a Graphviz graph")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    ap.add_argument("--variant", default="classic", choices=("classic", "direct"))
    args = ap.parse_args(argv)
    try:
        loaded = load(args.path)
        graph = build_graph(loaded.parsed, args.variant)
    except NoBlob as e:
        print(f"surface-power-blob: {e}", file=sys.stderr)
        return 2
    except BlobError as e:
        print(f"surface-power-blob: blob error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(as_json(loaded, graph), indent=2))
        return 0
    if args.dot:
        print(dot(graph))
        return 0
    text = summary(loaded, graph)
    if args.graph:
        text += [""] + graph_lines(graph)
    if args.outputs:
        text += [""] + output_lines(graph)
    print("\n".join(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
