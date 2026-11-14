"""Scheduling of evaluation runs (spec engine 3.3, 3.4).

- An instance gets an asynchronous run (timer=False) when one of its inputs changes, and once at the very first step.
- While a controller of the instance is ACTIVE, the instance gets a timer run (timer=True) every period, at fixed rate.
- If a timer run is due in the same step as an input change, only the timer run happens.
The caller passes a monotonic time in ms; the scheduler never reads a clock.
"""
from collections.abc import Mapping
from dataclasses import dataclass

from ..loader.model import ChannelRef
from .engine import Engine

PERIOD_MS = {0: 1000, 1: 5000}  # fixed in the Windows core [C]


@dataclass(frozen=True)
class Run:
    t_ms: int
    kind: str          # "async" or "timer"
    instance: int
    active: bool
    outputs: dict[ChannelRef, float]


@dataclass
class _Timer:
    period: int
    running: bool = False
    next_due: int = 0


class Scheduler:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.timers = {i: _Timer(PERIOD_MS[i]) for i in engine.graph.order}
        self.started = False

    def timer_running(self, instance: int) -> bool:
        return self.timers[instance].running

    def _run(self, instance: int, timer: bool, now_ms: int) -> Run:
        res = self.engine.evaluate(instance, timer, now_ms)
        t = self.timers[instance]
        if res.active and not t.running:
            t.running, t.next_due = True, now_ms + t.period
        elif not res.active:
            t.running = False
        return Run(now_ms, "timer" if timer else "async", instance, res.active, res.outputs)

    def step(self, now_ms: int, inputs: Mapping[ChannelRef, float | None]) -> list[Run]:
        """Apply new input values and do the runs that are due at `now_ms`."""
        changed = self.engine.set_inputs(inputs)
        runs = []
        for i, t in self.timers.items():
            if t.running and now_ms >= t.next_due:
                runs.append(self._run(i, True, now_ms))
                t.next_due += t.period
                if t.next_due <= now_ms:          # more than one period missed: re-anchor [D]
                    t.next_due = now_ms + t.period
            elif i in changed or not self.started:
                runs.append(self._run(i, False, now_ms))
        self.started = True
        return runs

    def reset(self, now_ms: int) -> list[Run]:
        """Resume: reset the engine; instances whose timer was running get one timer run at once (spec engine 3.4)."""
        was_running = [i for i, t in self.timers.items() if t.running]
        self.engine.reset()
        runs = []
        for i in was_running:
            t = self.timers[i]
            t.next_due = now_ms + t.period
            runs.append(self._run(i, True, now_ms))
        return runs
