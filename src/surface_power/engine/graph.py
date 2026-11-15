"""Graph build (spec blob-format 6, 7): slots for nodes and device channels, one block per algorithm line, checks.

Unsupported lines (unknown GUID, dynamic expressions, controller modes 1/2) do not run; their outputs keep their value (0 for
internal nodes). If such a line feeds a device output, the graph is not usable for control (FR-B8).
"""
from collections import defaultdict
from dataclasses import dataclass, field

from ..loader.errors import BlobError
from ..loader.model import AlgorithmLine, ChannelRef, InputChannel, OutputChannel, ParsedBlob
from . import blocks as _blocks  # noqa: F401  (registers the stateless blocks)
from . import pid as _pid
from .blocks import Block
from .registry import BLOCKS, name_of
from .slots import Slot


@dataclass(frozen=True)
class Unsupported:
    line: int
    guid: str
    reason: str
    reaches: list[ChannelRef]  # device outputs this line feeds, directly or through other blocks


@dataclass
class Graph:
    parsed: ParsedBlob
    variant: str
    nodes: dict[int, Slot] = field(default_factory=dict)
    inputs: dict[ChannelRef, Slot] = field(default_factory=dict)
    outputs: dict[ChannelRef, Slot] = field(default_factory=dict)
    input_ranges: dict[ChannelRef, InputChannel] = field(default_factory=dict)
    output_ranges: dict[ChannelRef, OutputChannel] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)              # runnable blocks, file order
    order: dict[int, list[Block]] = field(default_factory=dict)    # instance -> blocks in evaluation order
    reads: dict[ChannelRef, set[int]] = field(default_factory=dict)  # device input -> instances that read it
    unsupported: list[Unsupported] = field(default_factory=list)
    warnings: list[tuple[int, str]] = field(default_factory=list)

    @property
    def usable_for_control(self) -> bool:
        return not any(u.reaches for u in self.unsupported)

    @property
    def controllers(self) -> list[_pid.PidWithSets]:
        return [b for b in self.blocks if isinstance(b, _pid.PidWithSets)]


def _slot(graph: Graph, ref: ChannelRef, as_output: bool) -> Slot:
    if ref.internal:
        if ref.channel not in graph.nodes:
            graph.nodes[ref.channel] = Slot(str(ref), 0.0, True)  # internal nodes: always valid, start at 0 [C]
        return graph.nodes[ref.channel]
    table = graph.outputs if as_output else graph.inputs
    if ref not in table:
        table[ref] = Slot(str(ref), 0.0, False)
    return table[ref]


def _reaches(start: AlgorithmLine, lines: list[AlgorithmLine]) -> list[ChannelRef]:
    """Device outputs reachable from the outputs of `start` through internal nodes."""
    consumers = defaultdict(list)
    for ln in lines:
        for r in ln.inputs:
            if r.internal:
                consumers[r.channel].append(ln)
    found, seen, todo = set(), set(), list(start.outputs)
    while todo:
        r = todo.pop()
        if not r.internal:
            found.add(r)
            continue
        if r.channel in seen:
            continue
        seen.add(r.channel)
        for ln in consumers[r.channel]:
            todo.extend(ln.outputs)
    return sorted(found)


def outputs_reached_by(ref: ChannelRef, lines: list[AlgorithmLine]) -> list[ChannelRef]:
    """Device outputs that a device input `ref` can influence, directly or through internal nodes."""
    found: set[ChannelRef] = set()
    for ln in lines:
        if ref in ln.inputs:
            found.update(_reaches(ln, lines))
    return sorted(found)


def build_graph(parsed: ParsedBlob, variant: str = "classic") -> Graph:
    """Build the block graph. Raises BlobError (with the line number) for any line a supported block cannot accept."""
    if variant not in _pid.VARIANTS:
        raise BlobError(f"unknown controller variant {variant!r}")
    g = Graph(parsed, variant)
    g.warnings.extend(parsed.warnings)
    g.input_ranges = {c.ref: c for c in parsed.input_channels}
    g.output_ranges = {c.ref: c for c in parsed.output_channels}

    controllers: dict[int, _pid.PidWithSets] = {}
    set_lines: list[_pid.ParameterSetLine] = []
    skipped: list[tuple[AlgorithmLine, str]] = []
    built: list[tuple[int, Block]] = []

    for index, line in enumerate(parsed.algorithms):
        ins = [_slot(g, r, as_output=False) for r in line.inputs]
        outs = [_slot(g, r, as_output=True) for r in line.outputs]
        cls = BLOCKS.get(line.guid)
        if cls is None:
            skipped.append((line, f"unknown algorithm {line.guid} ({name_of(line.guid)})"))
            continue
        if line.dynamic:
            skipped.append((line, f"{cls.kind}: parameter expression with channel references (not implemented yet)"))
            continue
        block = cls(line, ins, outs, variant)
        if isinstance(block, _pid.ParameterSetLine):
            set_lines.append(block)
            continue
        if isinstance(block, _pid.PidWithSets):
            if not block.supported:
                skipped.append((line, f"{cls.kind}: controller mode {block.mode} is not implemented"))
                continue
            controllers[line.id] = block
        built.append((index, block))

    for s in set_lines:
        ctl = controllers.get(s.ctl_id)
        if ctl is None:
            g.warnings.append((s.line.line, f"parameter set for unknown controller id {s.ctl_id}"))
            continue
        ctl.attach(s.set)
    for ctl in controllers.values():
        ctl.finish()
        if len(ctl.sets) != ctl.n_sets:
            g.warnings.append((ctl.line.line, f"controller id {ctl.line.id} declares {ctl.n_sets} sets, has {len(ctl.sets)}"))

    for line, reason in skipped:
        g.unsupported.append(Unsupported(line.line, line.guid, reason, _reaches(line, parsed.algorithms)))

    # Evaluation order: per instance, by stage, then file order (spec engine 3.1).
    g.blocks = [b for _, b in built]
    by_instance = defaultdict(list)
    for index, b in built:
        by_instance[b.line.instance].append((b.line.stage, index, b))
    g.order = {i: [b for _, _, b in sorted(items, key=lambda t: (t[0], t[1]))] for i, items in sorted(by_instance.items())}

    for i, blocks in g.order.items():
        for b in blocks:
            for r in b.line.inputs:
                if not r.internal:
                    g.reads.setdefault(r, set()).add(i)

    written = {r.channel for ln in parsed.algorithms for r in ln.outputs if r.internal}
    read = {r.channel for ln in parsed.algorithms for r in ln.inputs if r.internal}
    for n in sorted(read - written):
        g.warnings.append((0, f"VN{n} is read but never written; it stays 0"))
    writers = defaultdict(int)
    for ln in parsed.algorithms:
        for r in ln.outputs:
            if not r.internal:
                writers[r] += 1
    for r, n in sorted(writers.items()):
        if n > 1:
            g.warnings.append((0, f"device output {r} has {n} writers"))
    return g
