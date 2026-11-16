"""Battery mode `0B7B5350/70` (spec outputs 10, docs/research/12 2.2): one EC request when the output turns 1.

On Windows the Intel SoC client has no register write for this output. It turns a value of 1 into a one-shot request to the EC and
sends nothing when the value falls back to 0: on SAM machines a SAM TMP request `0x1b` without payload (target ID 1, instance 0,
flags 0), on ACPI-EC machines the ACPI method `DPHT` [C]. In the DSDT of the ACPI-EC machines `DPHT` sends the EC command that
de-asserts PROCHOT [C]; the TMP request is its SAM counterpart in the same code position [H]. The blob makes the value 1 while the
adapter rating is 0 (no supply connected, or no rating known), i.e. on battery.

Linux: the same SAM request through the SSAM debug device (the stage of adapters/fan.py sends it), once when the value turns 1 and
again after a resume. Nothing to restore: the request releases a state, it sets no level; the EC asserts PROCHOT again by itself if
the cause is still there [H]. Not available: ACPI-EC machines (no ACPI method call from user space) and AMD or Qualcomm CPUs (their
clients were not analysed).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..loader.model import ChannelRef
from .fan import TC_TMP, Frame, send_frame
from .inputs import SSAM_CDEV, CdevTransport, sam_present
from .notneeded import cpu_vendor

LIMITS = "0B7B5350-BB8C-4459-8D4A-A75C57073801"
BATT_MODE = ChannelRef(LIMITS, 70)
ACTIVE = 1                      # the value at which the Windows client sends the request [C]
CID = 0x1B                      # TMP, no payload [C]; Windows sends it for value 1 only
RATE_MS = 5000                  # retry no faster than the fan stage [D]
MAX_FAILS = 3                   # then log-only [D]
FRAME = Frame(TC_TMP, iid=0, cid=CID, payload=b"")


@dataclass(frozen=True)
class ProchotResult:
    ref: ChannelRef
    kind: str            # "written", "pending", "dry-run", "idle", "log-only"
    value: int
    frame: Frame | None
    written: bool        # the request went out in this cycle


class ProchotStage:
    """The write path of the battery-mode output. Built only if the blob has it."""

    def __init__(self, root: str, channels, transport=None, dry_run: bool = False):
        self.root, self.dry_run = root, dry_run
        self.transport = transport if transport is not None else CdevTransport(os.path.join(root, SSAM_CDEV.lstrip("/")))
        self.notes: list[str] = []
        self.events: list[tuple[str, str]] = []
        self.refs = [r for r in channels if r == BATT_MODE]
        self.sent = False              # the request went out for the current episode of value 1
        self.last_try: int | None = None
        self.fails = 0
        self.off = False
        self.would: Frame | None = None
        self.path = "none"
        if not self.refs:
            return
        if cpu_vendor(root) not in (None, "GenuineIntel"):
            self.notes.append(f"{BATT_MODE}: only the Intel SoC client is analysed; no Linux path on this CPU")
            return
        if self.transport.can_write():
            self.path = "sam"
        elif getattr(self.transport, "needs_root", lambda: False)():
            self.path = "needs-root"
        elif sam_present(root):
            self.path = "needs-cdev"
        if self.path == "needs-cdev":
            self.notes.append(f"{BATT_MODE}: needs root and surface_aggregator_cdev (debug device not available)")
        elif self.path == "none":
            self.notes.append(f"{BATT_MODE}: no SSAM debug device; Windows sends the request through the ACPI method DPHT on "
                              "ACPI-EC machines, which user space cannot call: no Linux path")
        else:
            self.notes.append(f"{BATT_MODE}: sends SAM TMP 0x1b once when the value turns 1 (de-assert PROCHOT [H]); nothing "
                              "to restore")

    def handles(self, ref: ChannelRef) -> bool:
        return ref in self.refs

    def has_path(self, ref: ChannelRef, report: bool = False) -> bool:
        if ref not in self.refs or self.off:
            return False
        return self.path == "sam" or (report and self.path == "needs-root")

    def needs_cdev(self, ref: ChannelRef) -> bool:
        return ref in self.refs and not self.off and self.path in ("needs-root", "needs-cdev")

    def sends(self, ref: ChannelRef) -> bool:
        return ref in self.refs and self.path == "sam" and not self.off

    def describe(self, ref: ChannelRef) -> str:
        if ref not in self.refs or self.off:
            return "no Linux path" + (" (log-only after failed requests)" if self.off else "")
        if self.path == "sam":
            return "SAM TMP 0x1b (cdev)"
        if self.path == "needs-root":
            return "SAM TMP 0x1b (cdev, needs root)"
        if self.path == "needs-cdev":
            return "SAM TMP 0x1b (needs root and surface_aggregator_cdev)"
        return "no Linux path"

    def drain_events(self) -> list[tuple[str, str]]:
        out, self.events = self.events, []
        return out

    def apply(self, values: dict[ChannelRef, float], now_ms: int) -> dict[ChannelRef, ProchotResult]:
        out: dict[ChannelRef, ProchotResult] = {}
        for ref in self.refs:
            if ref not in values:
                continue
            x = int(round(values[ref]))
            if x != ACTIVE:
                self.sent, self.last_try, self.would = False, None, None      # the next 1 is a new episode
                out[ref] = ProchotResult(ref, "idle" if self.path in ("sam", "needs-root", "needs-cdev") else "log-only", x, None, False)
                continue
            if self.path != "sam" or self.off:
                out[ref] = ProchotResult(ref, "log-only", x, FRAME, False)
                continue
            if self.dry_run:
                self.would = FRAME
                out[ref] = ProchotResult(ref, "dry-run", x, FRAME, False)
                continue
            if self.sent:
                out[ref] = ProchotResult(ref, "written", x, FRAME, False)
                continue
            if self.last_try is not None and now_ms - self.last_try < RATE_MS:
                out[ref] = ProchotResult(ref, "pending", x, FRAME, False)
                continue
            self.last_try = now_ms
            ok = send_frame(self.transport, FRAME)
            if ok:
                self.sent, self.fails = True, 0
                self.events.append(("info", f"output {ref}: sent SAM TMP 0x1b (battery mode, de-assert PROCHOT [H])"))
            else:
                self.fails += 1
                if self.fails >= MAX_FAILS:
                    self.off = True
                    self.notes.append(f"{ref}: {self.fails} requests failed, log-only from now on")
                    self.events.append(("warning", f"battery-mode output {ref}: {self.fails} requests failed in a row, "
                                                   "log-only from now on"))
            out[ref] = ProchotResult(ref, "written" if ok else ("log-only" if self.off else "pending"), x, FRAME, ok)
        return out

    def forget(self) -> None:
        """After a resume: a value of 1 is sent again on the next cycle."""
        self.sent, self.last_try, self.would, self.fails = False, None, None, 0

    def status(self) -> list[dict]:
        return [{"channel": str(r), "path": self.describe(r), "sent": self.sent, "failures": self.fails,
                 "would": self.would.wire().hex() if self.would else None} for r in self.refs]
