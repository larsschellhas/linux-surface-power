"""Every power limit, read once a second during the run; each change is recorded with its time and stage (spec tester-kit.md 6.4).

This finds writers by what they do, a tester's own service included. A change between two samples is missed.
"""
import glob
import os
import threading
import time

from ...adapters import msr, rapl_copies
from . import read

MSR_LIMITS = (0x601, 0x610, 0x615, 0x65C)
MAX_EVENTS = 200


class Watch:
    def __init__(self, root: str = "/", clock=time.monotonic, interval_s: float = 1.0):
        self.root, self.clock, self.interval_s = root, clock, interval_s
        self.stage = "S1"
        self.t0 = clock()
        self.events: list[dict] = []
        self.dropped = 0
        self.last: dict[str, str] | None = None
        self.mchbar = None
        self.read_msr = True
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def values(self) -> dict[str, str]:
        out = {}
        for zone in glob.glob(os.path.join(self.root, "sys/class/powercap/intel-rapl*:*")):
            z = os.path.basename(zone)
            files = glob.glob(os.path.join(zone, "constraint_*_power_limit_uw")) + \
                glob.glob(os.path.join(zone, "constraint_*_time_window_us")) + [os.path.join(zone, "enabled")]
            for f in files:
                v = read("/", f)
                if v is not None:
                    out[f"{z}/{os.path.basename(f)}"] = v
        for reg in MSR_LIMITS if self.read_msr else ():
            v = msr.read(self.root, reg)
            if v is not None:
                out[f"msr {reg:#x}"] = f"{v:#018x}"
        if self.mchbar is None:
            base, _ = rapl_copies.mchbar(self.root)
            self.mchbar = base or 0
        if self.mchbar:
            try:
                out["mmio 0x59a0"] = f"{rapl_copies._mmio64(self.mchbar + rapl_copies.MMIO_PL_OFFSET, self.root):#018x}"
            except OSError:
                self.mchbar = 0
        return out

    def forget_msr(self) -> None:
        """Stop watching the processor registers: their device is about to go."""
        with self._lock:
            self.read_msr = False
            if self.last is not None:
                self.last = {k: v for k, v in self.last.items() if not k.startswith("msr ")}

    def sample(self) -> None:
        now = self.values()
        with self._lock:
            if not self.read_msr:
                now = {k: v for k, v in now.items() if not k.startswith("msr ")}
            if self.last is not None:
                t = round(self.clock() - self.t0, 1)
                for key in sorted(set(self.last) | set(now)):
                    old, new = self.last.get(key), now.get(key)
                    if old == new:
                        continue
                    if len(self.events) < MAX_EVENTS:
                        self.events.append({"t": t, "stage": self.stage, "what": key, "old": old, "new": new})
                    else:
                        self.dropped += 1
            self.last = now

    def start(self) -> None:
        self.sample()

        def loop():
            while not self._stop.wait(self.interval_s):
                self.sample()
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread is not None

    def stop(self) -> dict:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        with self._lock:
            return {"events": list(self.events), "dropped": self.dropped}
