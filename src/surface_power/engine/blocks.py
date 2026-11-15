"""Stateless blocks (spec engine 4). One class per algorithm GUID; each parses and checks its own parameters.

Common pattern, as in the Windows core: a block first writes a fallback value to its output, then overwrites it if its inputs
allow. Blocks write only their first output. A block without outputs does nothing.
"""
from ..loader.errors import BlobError
from ..loader.expr import Expression, Unavailable
from ..loader.model import AlgorithmLine, ChannelRef
from ..loader.numbers import atoi, real
from .helpers import clamp, round_half_up
from .registry import register
from .slots import RunContext, Slot


class Params:
    """Reads the parameter fields of one line, with errors that name the line."""

    def __init__(self, line: AlgorithmLine, kind: str, fields: list[str] | None = None, checks: bool = True):
        self.line, self.kind = line, kind
        self.fields = line.params if fields is None else fields
        self.checks = checks   # False for lines with expressions: the core skips its order checks then [C]

    def fail(self, message: str) -> BlobError:
        return BlobError(f"{self.kind}: {message}", self.line.line)

    def count(self, *allowed: int) -> int:
        if len(self.fields) not in allowed:
            want = " or ".join(str(n) for n in allowed)
            raise self.fail(f"expected {want} parameters, found {len(self.fields)}")
        return len(self.fields)

    def int(self, i: int, what: str) -> int:
        try:
            return atoi(self.fields[i])
        except (ValueError, IndexError):
            raise self.fail(f"{what}: not an integer") from None

    def real(self, i: int, what: str) -> float:
        try:
            return real(self.fields[i])
        except (ValueError, IndexError):
            raise self.fail(f"{what}: not a number") from None

    def reals(self, start: int, n: int, what: str) -> list[float]:
        return [self.real(start + k, f"{what}[{k}]") for k in range(n)]


class Block:
    """Base class. Subclasses set `kind` and `guid` through @register and implement parse() and run()."""

    kind = "block"
    guid = ""
    dynamic_ok = False   # True for blocks whose handling of expressions with channels was read from the core (engine 3.6)

    def __init__(self, line: AlgorithmLine, inputs: list[Slot], outputs: list[Slot], variant: str = "classic",
                 refs: dict[ChannelRef, Slot] | None = None):
        self.line = line
        self.inputs = inputs
        self.outputs = outputs
        self.variant = variant
        self.check_shape(len(inputs), len(outputs))
        # Parameters with channel references (engine 3.6): compiled once, evaluated before every run.
        self.exprs: dict[int, Expression] = {}
        if line.dynamic:
            for i, text in enumerate(line.params):
                if text.startswith("(") and "{" in text:
                    try:
                        self.exprs[i] = Expression(text)
                    except BlobError as e:
                        raise BlobError(e.message, line.line) from None
        self.refs = refs or {}
        self.parse(Params(line, self.kind, self._fields(None), checks=not self.exprs))

    def _fields(self, get) -> list[str]:
        """Parameter fields with the expressions evaluated. With `get` None (at load), expressions count as 0."""
        fields = list(self.line.params)
        for i, e in self.exprs.items():
            fields[i] = "0" if get is None else repr(e.value(get))
        return fields

    def _lookup(self, ref: ChannelRef) -> float | None:
        slot = self.refs.get(ref)
        return slot.value if slot is not None and slot.valid else None

    def execute(self, ctx: RunContext) -> None:
        """One run. With expressions, they are evaluated first; if one cannot be (a channel without a valid value, or a
        division by zero), the block writes nothing in this run [C for missing or invalid channels, H for division]."""
        if self.exprs:
            if not self.outputs:
                return
            try:
                fields = self._fields(self._lookup)
            except (Unavailable, BlobError):
                return
            self.parse(Params(self.line, self.kind, fields, checks=False))
        self.run(ctx)

    # --- to override ---
    def check_shape(self, n_in: int, n_out: int) -> None:
        """Header checks of the Windows core (spec blob-format 5.3). Default: no restriction."""

    def parse(self, p: Params) -> None:
        raise NotImplementedError

    def run(self, ctx: RunContext) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        """Called on resume (spec engine 3.4). Stateless blocks do nothing."""

    # --- helpers ---
    def inputs_ok(self) -> bool:
        """True if the block has inputs and all of them are valid."""
        return bool(self.inputs) and all(s.valid for s in self.inputs)

    def write(self, value: float) -> None:
        out = self.outputs[0]
        out.value = value
        out.valid = True

    def describe(self) -> str:
        return f"{self.kind} id {self.line.id}"


def _one_input(n_in: int, kind: str, line: AlgorithmLine) -> None:
    if n_in != 1:
        raise BlobError(f"{kind}: needs exactly 1 input, has {n_in}", line.line)


@register("lookup_table")
class LookupTable(Block):
    """x in [lo_i, hi_i] -> y_i (first match); below lo_0 or in a gap -> `below`; above hi_{N-1} -> `above` (spec engine 4.1)."""

    dynamic_ok = True

    def check_shape(self, n_in, n_out):
        _one_input(n_in, self.kind, self.line)

    def parse(self, p):
        n = p.int(0, "N")
        if n <= 0:
            raise p.fail("N must be positive")
        p.count(3 * n + 3)
        self.below = p.real(1, "below")
        self.above = p.real(2, "above")
        self.lo = p.reals(3, n, "lo")
        self.hi = p.reals(3 + n, n, "hi")
        self.y = p.reals(3 + 2 * n, n, "y")
        for i in range(n if p.checks else 0):
            if self.hi[i] < self.lo[i]:
                raise p.fail(f"interval {i}: hi < lo")
            if i < n - 1 and self.lo[i + 1] < self.hi[i]:
                raise p.fail(f"intervals {i} and {i + 1} overlap")

    def run(self, ctx):
        if not self.outputs:
            return
        result = self.below
        if self.inputs_ok():
            x = self.inputs[0].value
            if x > self.hi[-1]:
                result = self.above
            elif x >= self.lo[0]:
                for lo, hi, y in zip(self.lo, self.hi, self.y):
                    if lo <= x <= hi:
                        result = y
                        break
        self.write(result)


@register("min_max")
class MinMax(Block):
    """Minimum (flag 0) or maximum (flag != 0) of all inputs, clamped; default if any input is invalid (spec engine 4.2)."""

    def parse(self, p):
        p.count(4)
        self.max, self.min, self.default = p.real(0, "max"), p.real(1, "min"), p.real(2, "default")
        self.use_max = p.int(3, "flag") != 0
        if not (self.min <= self.max and self.default <= self.max and self.min <= self.default):
            raise p.fail("needs min <= default <= max")

    def run(self, ctx):
        if not self.outputs:
            return
        if not self.inputs_ok():
            self.write(self.default)
            return
        values = [s.value for s in self.inputs]
        m = max(values) if self.use_max else min(values)
        self.write(clamp(m, self.max, self.min))


@register("add_sub_scale")
class AddSubScale(Block):
    """clamp((sum of the first k inputs - sum of the rest + offset) / divisor, max, min) (spec engine 4.3)."""

    def parse(self, p):
        p.count(7)
        self.strict = p.int(0, "strict") == 0
        self.k = p.int(1, "k")
        self.max, self.min = p.real(2, "max"), p.real(3, "min")
        self.offset, self.divisor, self.default = p.real(4, "offset"), p.real(5, "divisor"), p.real(6, "default")
        if self.k > len(self.inputs):
            raise p.fail(f"k = {self.k} is larger than the number of inputs")
        if not (self.default <= self.max and self.min <= self.default):
            raise p.fail("needs min <= default <= max")
        if self.divisor == 0:
            raise p.fail("divisor must not be 0")

    def run(self, ctx):
        if not self.outputs:
            return
        if self.strict and not all(s.valid for s in self.inputs):
            self.write(self.default)
            return
        # Without `strict`, invalid inputs take part with their last value [C].
        plus = sum(s.value for s in self.inputs[:self.k])
        minus = sum(s.value for s in self.inputs[self.k:])
        self.write(clamp((plus - minus + self.offset) / self.divisor, self.max, self.min))


@register("multiplexer")
class Multiplexer(Block):
    """Output = in[round_half_up(sel)], clamped; default if the index is out of range or the chosen input invalid (spec engine 4.4)."""

    def check_shape(self, n_in, n_out):
        if n_in < 3:
            raise BlobError(f"{self.kind}: needs at least 3 inputs, has {n_in}", self.line.line)

    def parse(self, p):
        p.count(3)
        self.max, self.min, self.default = p.real(0, "max"), p.real(1, "min"), p.real(2, "default")
        if not (self.min <= self.max and self.default <= self.max and self.min <= self.default):
            raise p.fail("needs min <= default <= max")

    def run(self, ctx):
        if not self.outputs:
            return
        sel = self.inputs[0]
        if not sel.valid:
            self.write(self.default)  # not clamped [C]
            return
        i = round_half_up(sel.value)
        choices = self.inputs[1:]
        value = self.default
        if 0 <= i < len(choices) and choices[i].valid:
            value = choices[i].value
        self.write(clamp(value, self.max, self.min))


@register("pass_through")
class PassThrough(Block):
    """Input value if valid, else default. No clamping (spec engine 4.5)."""

    def check_shape(self, n_in, n_out):
        _one_input(n_in, self.kind, self.line)

    def parse(self, p):
        p.count(1)
        self.default = p.real(0, "default")

    def run(self, ctx):
        if self.outputs:
            self.write(self.inputs[0].value if self.inputs_ok() else self.default)


@register("linear_map")
class LinearMap(Block):
    """mode 0: k*x + offset; mode != 0: k/x + offset with |x| >= 0.001; clamped; default if invalid (spec engine 4.6)."""

    def check_shape(self, n_in, n_out):
        _one_input(n_in, self.kind, self.line)

    def parse(self, p):
        p.count(6)
        self.max, self.min = p.real(0, "max"), p.real(1, "min")
        self.k, self.offset, self.default = p.real(2, "k"), p.real(3, "offset"), p.real(4, "default")
        self.reciprocal = p.int(5, "mode") != 0
        if self.min > self.max:
            raise p.fail("min > max")

    def run(self, ctx):
        if not self.outputs:
            return
        if not self.inputs_ok():
            self.write(self.default)
            return
        x = self.inputs[0].value
        if self.reciprocal:
            if abs(x) < 0.001:
                x = -0.001 if x < 0 else 0.001
            y = self.k * (1.0 / x)
        else:
            y = self.k * x
        self.write(clamp(y + self.offset, self.max, self.min))


@register("window_comparator")
class WindowComparator(Block):
    """1 if any input meets its condition (kind 1: x >= a, 2: x <= a, 3: outside [a, b]), else 2; 1 if invalid (spec engine 4.7)."""

    def parse(self, p):
        n = len(self.inputs)
        p.count(3 * n)
        # Kinds other than 1-3 are stored as 0, which never matches [C].
        self.kinds = [k if k in (1, 2, 3) else 0 for k in (p.int(i, f"kind[{i}]") for i in range(n))]
        self.a = p.reals(n, n, "a")
        self.b = p.reals(2 * n, n, "b")
        for i, k in enumerate(self.kinds):
            if k == 3 and self.b[i] < self.a[i]:
                raise p.fail(f"window {i}: b < a")

    def run(self, ctx):
        if not self.outputs:
            return
        if not self.inputs_ok():
            self.write(1)
            return
        hit = False
        for s, k, a, b in zip(self.inputs, self.kinds, self.a, self.b):
            x = s.value
            if (k == 1 and x >= a) or (k == 2 and x <= a) or (k == 3 and (x < a or x > b)):
                hit = True
        self.write(1 if hit else 2)
