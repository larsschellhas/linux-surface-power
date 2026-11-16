"""Supervisor parts (spec supervisor.md): device detection, profiles, snapshot/restore, emergency rules, foreign changes.

The state machine that ties them together lives in daemon.py.
"""
import glob
import json
import os
import tomllib
from dataclasses import dataclass, field

from .adapters import msr
from .loader.model import ChannelRef

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


# --- detection (spec supervisor 2) ---

@dataclass(frozen=True)
class Device:
    vendor: str
    product: str
    sku: str
    smf: bool

    @property
    def microsoft(self) -> bool:
        return self.vendor == "Microsoft Corporation"

    @property
    def supported(self) -> bool:
        return self.microsoft and self.smf


EFIVAR_GLOB = "sys/firmware/efi/efivars/SMF_DATA_BLOB-*"


def _smf_present(root: str) -> bool:
    """Evidence for Surface SMF (spec supervisor 2): an ACPI device MSHW0187, or the thermal zone \\TPOL that carries this HID
    on the SB3 (Linux enumerates it as LNXTHERM, so the HID is not visible) [M], or a non-empty SMF blob variable."""
    if glob.glob(os.path.join(root, "sys/bus/acpi/devices/MSHW0187:*")):
        return True
    for d in glob.glob(os.path.join(root, "sys/bus/acpi/devices/LNXTHERM:*")):
        if (_read(os.path.join(d, "path")) or "").endswith("TPOL"):
            return True
    for v in glob.glob(os.path.join(root, EFIVAR_GLOB)):
        try:
            if os.path.getsize(v) > 4:
                return True
        except OSError:
            pass
    return False


def thermald_running(root: str = "/") -> bool:
    """True if a thermald process runs (spec supervisor 2.6, FR-S6). Reads /proc/<pid>/comm, so it needs no systemd."""
    for path in glob.glob(os.path.join(root, "proc/[0-9]*/comm")):
        try:
            with open(path) as f:
                if f.read().strip() == "thermald":
                    return True
        except OSError:
            continue        # the process ended while we looked
    return False


def detect(root: str = "/") -> Device:
    dmi = os.path.join(root, "sys/class/dmi/id")
    smf = _smf_present(root)
    return Device(_read(os.path.join(dmi, "sys_vendor")) or "", _read(os.path.join(dmi, "product_name")) or "",
                  _read(os.path.join(dmi, "product_sku")) or "", smf)


@dataclass(frozen=True)
class Profile:
    """A device profile (spec supervisor 6). Holds no limit values."""

    file: str
    product: str | None          # DMI product_name; None matches any
    sku: tuple[str, ...] | None  # DMI product_sku, any of these; None matches any
    variant: str
    tested: bool
    blob_source: str = "efivar"
    pl3: bool | None = None          # None: automatic (spec outputs 3, 4)
    pl4: bool | None = None
    narrow: dict = field(default_factory=dict)
    panel_max_nits: float | None = None
    dgpu_clock_cap: bool = False      # opt-in stand-in DO-1 (spec outputs 6)


def load_profiles(directory: str = PROFILE_DIR) -> list[Profile]:
    out = []
    for path in sorted(glob.glob(os.path.join(directory, "*.toml"))):
        with open(path, "rb") as f:
            d = tomllib.load(f)
        m, e, o = d.get("match", {}), d.get("engine", {}), d.get("outputs", {})
        sku = m.get("sku")
        if isinstance(sku, str):
            sku = (sku,)
        elif sku is not None:
            sku = tuple(sku)
        if m.get("product_name") is None and sku is None:
            raise ValueError(f"profile {os.path.basename(path)}: needs match.product_name or match.sku")
        out.append(Profile(os.path.basename(path), m.get("product_name"), sku, e.get("variant", "direct"),
                           bool(d.get("tested", False)), d.get("blob", {}).get("source", "efivar"),
                           o.get("pl3"), o.get("pl4"), dict(o.get("narrow", {})),
                           d.get("inputs", {}).get("panel_max_nits"), bool(o.get("dgpu_clock_cap", False))))
    return out


def find_profile(device: Device, directory: str = PROFILE_DIR) -> Profile | None:
    for p in load_profiles(directory):
        if (p.product is None or p.product == device.product) and (p.sku is None or device.sku in p.sku):
            return p
    return None


# --- snapshot and restore (spec supervisor 3) ---

def _read_any(key: str) -> str | None:
    """A powercap file's text, or an MSR's value as hex for an `msr:` key."""
    m = msr.parse_key(key)
    if m:
        v = msr.read_dev(*m)
        return None if v is None else f"{v:#018x}"
    return _read(key)


class Snapshot:
    """Firmware values of every file we may write, saved before the first write (atomic JSON in the run directory)."""

    FILE = "snapshot.json"

    def __init__(self, path: str, values: dict[str, str]):
        self.path, self.values = path, values

    @classmethod
    def take(cls, run_dir: str, files: list[str]) -> tuple["Snapshot", bool]:
        """Save the current values. If a predecessor's snapshot exists, keep it: the current values may be ours."""
        path = os.path.join(run_dir, cls.FILE)
        if os.path.exists(path):
            snap = cls.load(path)
            if snap is not None:
                return snap, True
        values = {f: v for f in files if (v := _read_any(f)) is not None}
        os.makedirs(run_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"version": 1, "values": values}, fh, indent=1)
        os.replace(tmp, path)
        return cls(path, values), False

    @classmethod
    def load(cls, path: str) -> "Snapshot | None":
        try:
            with open(path) as fh:
                data = json.load(fh)
            return cls(path, dict(data["values"]))
        except (OSError, ValueError, KeyError):
            return None

    def order(self) -> list[str]:
        """Time windows before power limits, so that PL1 never runs with a foreign short window."""
        return sorted(self.values, key=lambda p: (0 if p.endswith("_time_window_us") else 1, p))

    def restore(self) -> list[str]:
        """Write back every value that differs. Returns error texts (empty on success)."""
        errors = []
        for p in self.order():
            want = self.values[p]
            if _read_any(p) == want:
                continue
            m = msr.parse_key(p)
            if m:
                if not msr.write_dev(m[0], m[1], int(want, 16)):
                    errors.append(f"{p}: MSR write failed")
                continue
            try:
                with open(p, "w") as fh:
                    fh.write(want)
            except OSError as e:
                errors.append(f"{p}: {e.strerror}")
        return errors

    def remove(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


# --- emergency rules (spec supervisor 4) ---

def _dk(celsius: float) -> float:
    return 2731 + celsius * 10


class _Hold:
    """True once a condition has held for `hold_ms`."""

    def __init__(self, hold_ms: int):
        self.hold_ms, self.since = hold_ms, None

    def update(self, now_ms: int, condition: bool) -> bool:
        if not condition:
            self.since = None
            return False
        if self.since is None:
            self.since = now_ms
        return now_ms - self.since >= self.hold_ms


class Guard:
    """Blob-independent emergency rules. check() returns a reason when an emergency starts, else None."""

    def __init__(self, skin: list[ChannelRef], cpu: ChannelRef | None = None):
        self.skin, self.cpu = skin, cpu
        self.emergency = False
        self.kind = ""      # "skin", "cpu" or "inputs" while in emergency
        self.reason = ""
        self._skin_hot, self._skin_ok = _Hold(10000), _Hold(60000)
        self._cpu_hot, self._cpu_ok = _Hold(10000), _Hold(60000)
        self._lost, self._back = _Hold(60000), _Hold(10000)

    def check(self, now_ms: int, values: dict[ChannelRef, float | None], core_invalid: bool) -> str | None:
        skin = [v for r in self.skin if (v := values.get(r)) is not None]
        cpu = values.get(self.cpu) if self.cpu else None
        skin_hot = self._skin_hot.update(now_ms, any(v >= _dk(60) for v in skin))
        cpu_hot = self._cpu_hot.update(now_ms, cpu is not None and cpu >= _dk(105))
        lost = self._lost.update(now_ms, core_invalid)
        if not self.emergency:
            for kind, hit, text in (("skin", skin_hot, "skin temperature >= 60 °C for 10 s"),
                                    ("cpu", cpu_hot, "CPU temperature >= 105 °C for 10 s"),
                                    ("inputs", lost, "controller inputs invalid for 60 s")):
                if hit:
                    self.emergency, self.kind, self.reason = True, kind, text
                    for h in (self._skin_ok, self._cpu_ok, self._back):
                        h.since = None
                    return text
            return None
        # Recovery: the condition that caused the emergency must be gone for its hold time.
        if self.kind == "skin":
            ok = self._skin_ok.update(now_ms, all(v < _dk(55) for v in skin))
        elif self.kind == "cpu":
            ok = self._cpu_ok.update(now_ms, cpu is None or cpu < _dk(95))
        else:
            ok = self._back.update(now_ms, not core_invalid)
        if ok:
            self.emergency, self.kind, self.reason = False, "", ""
        return None


# --- foreign changes (spec supervisor 5) ---

class ForeignWatch:
    """record() returns True on the third foreign change within 60 s: then the daemon yields."""

    WINDOW_MS, LIMIT = 60000, 3

    def __init__(self):
        self.times: list[int] = []

    def record(self, now_ms: int) -> bool:
        self.times = [t for t in self.times if now_ms - t <= self.WINDOW_MS] + [now_ms]
        return len(self.times) >= self.LIMIT
