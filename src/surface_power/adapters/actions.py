"""Thermal-policy actions (spec outputs 7): the blob's hibernate and critical-shutdown requests.

On Windows the Surface thermal-policy driver passes `O_HOT` (110DF2A4/1) and `O_CRT` (110DF2A4/2) to the thermal zone `TPOL`; at 2
Windows hibernates or shuts down (docs/research/10-outputs.md). Here a request that holds for CONFIRM_MS starts the matching systemd
action once; it re-arms when the request is gone. The daemon arms the actions only in control mode; otherwise they are logged.
"""
import subprocess
from collections.abc import Callable

from ..loader.model import ChannelRef

THERMAL = "110DF2A4-5995-473C-92FD-3CAC40EE2E6C"
HOT = ChannelRef(THERMAL, 1)
CRT = ChannelRef(THERMAL, 2)
CONFIRM_MS = 5000        # one evaluation of the 5000 ms instance [D]
ACTIVE = 2               # the value at which the Windows driver sets the policy flag [C]

COMMANDS = {
    CRT: [["systemctl", "poweroff"]],
    HOT: [["systemctl", "hibernate"], ["systemctl", "suspend"]],   # suspend where hibernation is not set up [D]
}
WHAT = {CRT: "power off (critical temperature)", HOT: "hibernate (hot)"}


def _run(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, check=False, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class ThermalActions:
    channels = frozenset({HOT, CRT})

    def __init__(self, runner: Callable[[list[str]], bool] = _run):
        self.runner = runner
        self.since: dict[ChannelRef, int | None] = {HOT: None, CRT: None}
        self.fired: set[ChannelRef] = set()
        self.last: str | None = None

    def step(self, now_ms: int, values: dict[ChannelRef, float], armed: bool) -> list[tuple[str, str]]:
        """Called every cycle with the engine outputs. Returns (priority, message) pairs to log."""
        events = []
        for ref in (CRT, HOT):                      # critical first: no hibernate on the way to a shutdown
            v = values.get(ref)
            active = v is not None and round(v) == ACTIVE
            if not active:
                self.since[ref] = None
                self.fired.discard(ref)
                continue
            if self.since[ref] is None:
                self.since[ref] = now_ms
            if ref in self.fired or now_ms - self.since[ref] < CONFIRM_MS:
                continue
            self.fired.add(ref)
            if not armed:
                events.append(("warning", f"blob requests {WHAT[ref]}; not armed outside control mode"))
                continue
            for cmd in COMMANDS[ref]:
                if self.runner(cmd):
                    events.append(("crit", f"blob requests {WHAT[ref]}: {' '.join(cmd)}"))
                    self.last = " ".join(cmd)
                    break
            else:
                events.append(("crit", f"blob requests {WHAT[ref]}, but every action failed"))
            if ref is CRT:
                break
        return events

    def status(self) -> dict:
        return {"hot_active": self.since[HOT] is not None, "critical_active": self.since[CRT] is not None,
                "last_action": self.last}
