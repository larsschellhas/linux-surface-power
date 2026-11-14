"""Arithmetic helpers with the exact semantics of the Windows core (spec engine 3.2)."""
import math


def clamp(x: float, hi: float, lo: float) -> float:
    """Clamp with the upper bound tested first: x > hi -> hi; else x < lo -> lo; else x. Note the argument order (value, max, min)."""
    if x > hi:
        return hi
    if x < lo:
        return lo
    return x


def round_half_up(x: float) -> int:
    """trunc(x + 0.5): rounds halves up for positive x, but is not symmetric for negative x (round_half_up(-0.7) == 0)."""
    return math.trunc(x + 0.5)
