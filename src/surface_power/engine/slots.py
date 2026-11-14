"""Value slots (spec engine 2): every node, device input and device output holds one value and a validity flag."""


class Slot:
    __slots__ = ("name", "value", "valid")

    def __init__(self, name: str, value: float = 0.0, valid: bool = False):
        self.name = name
        self.value = value
        self.valid = valid

    def __repr__(self) -> str:
        return f"Slot({self.name}={self.value}{'' if self.valid else ', invalid'})"


class RunContext:
    """Passed to every block in one evaluation run."""

    __slots__ = ("timer", "now_ms", "active")

    def __init__(self, timer: bool, now_ms: int):
        self.timer = timer      # True for a timer run, False for an asynchronous run
        self.now_ms = now_ms    # monotonic time of the run, for time-based blocks (EWMA, phase 5)
        self.active = False     # set by controllers that want the periodic timer
