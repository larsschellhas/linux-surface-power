"""Supervisor parts (spec supervisor.md): device detection, profiles, snapshot/restore, emergency rules, foreign changes.

The state machine that ties them together lives in daemon.py.
"""
import glob
import json
import os
import tomllib
from dataclasses import dataclass, field

from .adapters import fan as fanmod
from .adapters import msr
from .adapters.inputs import SSAM_CDEV, CdevTransport
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


# Daemons that write package power limits (RAPL) or frequency caps themselves and so confound a dry-run's measurements.
LIMIT_WRITERS = ("thermald", "tuned", "tlp", "power-profiles-")   # comm is cut at 15 characters: power-profiles-daemon


def limit_writers(root: str = "/") -> list[str]:
    """Names of running processes that write power limits themselves (thermald, tuned, TLP, power-profiles-daemon)."""
    found = set()
    for path in glob.glob(os.path.join(root, "proc/[0-9]*/comm")):
        try:
            with open(path) as f:
                comm = f.read().strip()
        except OSError:
            continue
        for name in LIMIT_WRITERS:
            if comm == name or (name.endswith("-") and comm.startswith(name)):
                found.add(name.rstrip("-") + ("-daemon" if name.endswith("-") else ""))
    return sorted(found)


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
    fan_thresholds: bool = False      # opt-in: send the fan / PROCHOT / charge-rate temperature thresholds (spec outputs 9)
    pl4_requires_ec: bool = False     # control starts only if the EC inputs on the PL4 path are valid in time (spec outputs 3.2)
    pl4_ec_wait_s: float = 10.0       # how long the start waits for them
    notes: tuple = ()                 # lines the report and the daemon log print for this device
    kernel_fan_profiles: int = 1      # fans (1-3) the kernel sends the fan profile to: `fan_count` of its node (spec outputs 9.3)
    dgpu_clock_cap_dry_run: bool = False   # opt-in: the stand-in computes and logs its cap but never sets one (spec outputs 6.2)


def _fan_profiles(v, path: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 3:
        raise ValueError(f"profile {os.path.basename(path)}: outputs.kernel_fan_profiles must be 1, 2 or 3")
    return v


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
                           d.get("inputs", {}).get("panel_max_nits"), bool(o.get("dgpu_clock_cap", False)),
                           bool(o.get("fan_thresholds", False)), bool(o.get("pl4_requires_ec", False)),
                           float(o.get("pl4_ec_wait_s", 10.0)), tuple(d.get("notes", ())),
                           kernel_fan_profiles=_fan_profiles(o.get("kernel_fan_profiles", 1), path),
                           dgpu_clock_cap_dry_run=bool(o.get("dgpu_clock_cap_dry_run", False))))
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
    """Firmware values of every file we may write, saved before the first write (atomic JSON in the run directory).

    The fan and charging outputs cannot be read back (spec outputs 9). For them the snapshot holds the neutral frames of
    every channel that was sent, written before its first send, and the channels that have no neutral value.
    """

    FILE = "snapshot.json"

    def __init__(self, path: str, values: dict[str, str], fan: dict[str, dict] | None = None,
                 fan_unrestorable: list[str] | None = None, fan_device: str = SSAM_CDEV):
        self.path, self.values = path, values
        self.fan = fan or {}                         # key "fan:101" -> frame (Frame.to_dict) to send at the restore
        self.fan_unrestorable = fan_unrestorable or []
        self.fan_device = fan_device
        self.transport = None                        # for tests and the daemon; default: the debug device in `fan_device`

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
        snap = cls(path, values)
        snap.save()
        return snap, False

    def save(self) -> None:
        data = {"version": 1, "values": self.values}
        if self.fan or self.fan_unrestorable:
            data["fan"] = {"device": self.fan_device, "frames": self.fan, "unrestorable": self.fan_unrestorable}
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: str) -> "Snapshot | None":
        try:
            with open(path) as fh:
                data = json.load(fh)
            fan = data.get("fan", {})
            return cls(path, dict(data["values"]), dict(fan.get("frames", {})), list(fan.get("unrestorable", [])),
                       fan.get("device", SSAM_CDEV))
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            return None

    def record_fan(self, key: str, frame: "fanmod.Frame | None") -> None:
        """Note, before the first send of a fan channel, what the restore has to send. Raises OSError if it cannot be saved."""
        if frame is None:
            if key not in self.fan_unrestorable:
                self.fan_unrestorable.append(key)
                self.save()
            return
        if self.fan.get(key) != frame.to_dict():
            self.fan[key] = frame.to_dict()
            self.save()

    def order(self) -> list[str]:
        """Time windows before power limits, so that PL1 never runs with a foreign short window."""
        return sorted(self.values, key=lambda p: (0 if p.endswith("_time_window_us") else 1, p))

    def restore(self) -> list[str]:
        """Write back every value that differs and send the neutral fan frames. Returns error texts (empty on success)."""
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
        if self.fan:
            transport = self.transport or CdevTransport(self.fan_device)
            for key in sorted(self.fan):
                if fanmod.send_frame(transport, fanmod.Frame.from_dict(self.fan[key])):
                    del self.fan[key]
                else:
                    errors.append(f"{key}: neutral frame not sent")
            if os.path.exists(self.path):
                try:
                    self.save()                      # what went out is no longer ours
                except OSError:
                    pass
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
