"""Blocks of newer blobs (spec engine 5). Same conventions as blocks.py."""
from .blocks import Block
from .registry import register

TOLERANCE = 0.001  # the core's tolerance for "equal" comparisons [C]


def near(a: float, b: float) -> bool:
    return abs(a - b) < TOLERANCE


@register("conditional_table")
class ConditionalTable(Block):
    """First row whose cells all match the inputs gives y; no match -> the last result (initially `default`) (spec engine 5.1)."""

    def parse(self, p):
        n = p.int(0, "N")
        if n <= 0:
            raise p.fail("N must be positive")
        k = len(self.inputs)
        p.count(2 + 2 * n * k + n)
        self.default = p.real(1, "default")
        self.lo, self.hi = [], []
        for j in range(k):
            base = 2 + 2 * n * j
            self.lo.append(p.reals(base, n, f"lo[{j}]"))
            self.hi.append(p.reals(base + n, n, f"hi[{j}]"))
        self.y = p.reals(2 + 2 * n * k, n, "y")
        for j in range(k):
            for i in range(n):
                if self.lo[j][i] > self.hi[j][i]:
                    raise p.fail(f"row {i}, input {j}: lo > hi")
        self.last = self.default

    def _matches(self, i: int) -> bool:
        for j, s in enumerate(self.inputs):
            x, lo, hi = s.value, self.lo[j][i], self.hi[j][i]
            if near(lo, hi):
                if not near(x, hi):
                    return False
            elif x < lo or x > hi:
                return False
        return True

    def run(self, ctx):
        if not self.outputs:
            return
        # No validity check: the core compares the stored input values [C].
        for i in range(len(self.y)):
            if self._matches(i):
                self.last = self.y[i]
                break
        self.write(self.last)


@register("hysteresis_table")
class HysteresisTable(Block):
    """Step switch over overlapping bands [down[i], up[i]] -> y[i]; holds inside overlaps (spec engine 5.2)."""

    def parse(self, p):
        n = p.int(0, "N")
        if n <= 0:
            raise p.fail("N must be positive")
        p.count(3 * n + 3)
        self.below, self.above = p.real(1, "below"), p.real(2, "above")
        self.down = [p.real(3 + 2 * i, f"down[{i}]") for i in range(n)]
        self.up = [p.real(4 + 2 * i, f"up[{i}]") for i in range(n)]
        self.y = p.reals(3 + 2 * n, n, "y")
        lo, hi = self.down[0], self.up[-1]
        for i in range(n):
            if self.down[i] > self.up[i]:
                raise p.fail(f"band {i}: down > up")
            if self.down[i] < lo or self.up[i] > hi:
                raise p.fail(f"band {i}: outside [down[0], up[N-1]]")
            if i and (self.down[i] < self.down[i - 1] or self.down[i] > self.up[i - 1]):
                raise p.fail(f"band {i}: must start inside band {i - 1}")
        self.last_x, self.last_y = 0.0, self.below

    def _result(self, x: float) -> tuple[float, bool]:
        """(new result, whether x is stored) as in the core."""
        n = len(self.y)
        if x > self.up[-1]:
            return self.above, True
        if x < self.down[0]:
            return self.below, True
        if near(x, self.last_x) or any(self.down[i + 1] < x < self.up[i] for i in range(n - 1)):
            return self.last_y, False
        if x < self.last_x:
            for i in range(n):
                if x <= self.down[i]:
                    return (self.below if i == 0 else self.y[i - 1]), True
            return self.y[-1], True
        for i in range(n - 1, -1, -1):
            if x >= self.up[i]:
                return (self.above if i == n - 1 else self.y[i + 1]), True
        return self.y[0], True

    def run(self, ctx):
        if not self.outputs or not self.inputs_ok():
            return  # the core writes nothing then [C]
        x = self.inputs[0].value
        self.last_y, store = self._result(x)
        if store:
            self.last_x = x
        self.write(self.last_y)
