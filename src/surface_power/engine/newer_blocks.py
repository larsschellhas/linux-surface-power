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
