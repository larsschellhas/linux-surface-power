"""The evaluation engine (spec engine 3). Pure: no I/O, no clock. Inputs, the timer flag and the time are passed in."""
from collections.abc import Mapping
from dataclasses import dataclass

from ..loader.model import ChannelRef
from .graph import Graph
from .helpers import round_half_up
from .slots import RunContext


@dataclass(frozen=True)
class RunResult:
    instance: int
    timer: bool
    active: bool                       # True if any controller asks for the periodic timer
    outputs: dict[ChannelRef, float]   # all device outputs written so far


class Engine:
    def __init__(self, graph: Graph):
        self.graph = graph

    def set_inputs(self, values: Mapping[ChannelRef, float | None]) -> set[int]:
        """Set device inputs. None means invalid; the slot keeps its last value. Returns the instances whose inputs changed."""
        changed: set[int] = set()
        for ref, value in values.items():
            slot = self.graph.inputs.get(ref)
            if slot is None:
                continue
            if value is None:
                if slot.valid:
                    slot.valid = False
                    changed |= self.graph.reads.get(ref, set())
            elif not slot.valid or slot.value != value:
                slot.value, slot.valid = float(value), True
                changed |= self.graph.reads.get(ref, set())
        return changed

    def evaluate(self, instance: int, timer: bool, now_ms: int) -> RunResult:
        """One evaluation run of an instance: all its blocks, by stage, then file order."""
        ctx = RunContext(timer, now_ms)
        for block in self.graph.order.get(instance, ()):
            block.execute(ctx)
        return RunResult(instance, timer, ctx.active, self.outputs())

    def reset(self) -> None:
        """Resume reset (spec engine 3.4): reset every block. Stateless blocks ignore it."""
        for block in self.graph.blocks:
            block.reset()

    def node(self, n: int) -> float:
        return self.graph.nodes[n].value

    def outputs(self) -> dict[ChannelRef, float]:
        """Device outputs that have been written at least once."""
        return {ref: s.value for ref, s in self.graph.outputs.items() if s.valid}

    def rounded_outputs(self) -> dict[ChannelRef, int]:
        """Outputs as the Windows core hands them to its clients: rounded half up to integers (spec engine 6)."""
        return {ref: round_half_up(v) for ref, v in self.outputs().items()}
