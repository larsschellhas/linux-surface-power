"""Input adapters (spec inputs.md): read Linux sources and return values in blob units. None means invalid.

Every adapter takes `root` (default "/") so that tests can run on a fake sysfs tree.
"""
import glob
import os
import struct
from collections.abc import Callable, Iterable

from ..loader.model import ChannelRef

TEMP = "2E8B14C2-26D6-4119-A62E-9D108C4DDC2A"
SYSTEM = "2DD6960F-7CB9-4BB8-82BF-97FA5D8BD767"
POWER = "28B7BD85-B4CD-49F1-B02B-F84E5AA09889"
POSTURE = "8060B17F-2FFB-4139-82E6-8B4C60F48419"
LID = "5D0EE597-8C3D-4305-A812-05CDFAFB1C66"
STATE = "1FB53D4C-5BDE-4B4A-B3C4-C23F7872DA1F"   # power/state values; 41 = brightness (nits), 42 = brightness (%)
MS_NOTIFY = "DEF1AB69-2537-4018-A676-ED20162BCFDE"  # Modern Standby phase
SPT = "049E36F2-906D-47F2-ACA4-610552BFB1FA"        # power tracker items; 9 = USB source power
COMPOSITE = "87008365-EA3A-461C-B83D-6AC6E8DB2F8A"  # client-computed channels (ambient estimate, sensor states)
PLACEHOLDER = "99999999-9999-9999-9999-999999999999"

ZERO_C = 2731  # 0 °C in 0.1 K


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _int(path: str) -> int | None:
    text = _read(path)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def mc_to_dk(millicelsius: int) -> float:
    """m°C (hwmon) -> 0.1 K (blob). Exact for the 0.1 K steps the EC reports."""
    return ZERO_C + millicelsius / 100


class Source:
    """Base class: `channels` lists what this adapter can provide; read() returns {channel: value | None}."""

    channels: set[ChannelRef] = set()

    def read(self, now_ms: int) -> dict[ChannelRef, float | None]:
        raise NotImplementedError


def _hwmon_dirs(root: str, name: str) -> list[str]:
    out = []
    for d in sorted(glob.glob(os.path.join(root, "sys/class/hwmon/hwmon*"))):
        if _read(os.path.join(d, "name")) == name:
            out.append(d)
    return out


# Channel base per SSAM target of a `surface_thermal` device (spec inputs 2): SAM (display part) 500 + iid, KIP (base) 600 + iid.
THERMAL_CHANNEL_BASE = {1: 500, 2: 600}


def _ssam_target(hwmon_dir: str) -> int | None:
    """SSAM target ID from the hwmon's device name (`01:03:<tid>:00:02`); None if there is no SSAM device link."""
    parts = os.path.basename(os.path.realpath(os.path.join(hwmon_dir, "device"))).split(":")
    if len(parts) == 5:
        try:
            return int(parts[2], 16)
        except ValueError:
            return None
    return None


class HwmonTemperatures(Source):
    """SAM sensors 501-509 and KIP (base) sensors 601-609 from hwmon `surface_thermal` (temp<iid>_input),
    CPU package 102 from `coretemp`. Without an SSAM device link, a `surface_thermal` counts as SAM."""

    def __init__(self, root: str = "/"):
        self.paths: dict[ChannelRef, str] = {}
        for d in _hwmon_dirs(root, "surface_thermal"):
            base = THERMAL_CHANNEL_BASE.get(_ssam_target(d) or 1)
            if base is None:
                continue                        # a target we do not know: not mapped
            for p in glob.glob(os.path.join(d, "temp*_input")):
                iid = int(os.path.basename(p)[4:-len("_input")])
                self.paths[ChannelRef(TEMP, base + iid)] = p
        for d in _hwmon_dirs(root, "coretemp"):
            for lab in glob.glob(os.path.join(d, "temp*_label")):
                if _read(lab) == "Package id 0":
                    self.paths[ChannelRef(TEMP, 102)] = lab[:-len("_label")] + "_input"
        self.channels = set(self.paths)

    def restrict(self, wanted: set[ChannelRef]) -> None:
        """Read only the channels the blob uses: each surface_thermal read is an EC request (~2 ms) [M]."""
        self.paths = {r: p for r, p in self.paths.items() if r in wanted}

    def read(self, now_ms):
        out = {}
        for ref, p in self.paths.items():
            v = _int(p)
            out[ref] = mc_to_dk(v) if v is not None else None
        return out


_MAINS: dict[str, str] = {}      # root -> the Mains supply's directory, found once (the glob runs twice per second otherwise)


def _ac_online(root: str) -> bool | None:
    d = _MAINS.get(root)
    if d is not None:
        v = _int(os.path.join(d, "online"))
        if v is not None:
            return v == 1
        del _MAINS[root]                                # gone (e.g. USB-C PD supply re-enumerated): search again
    for d in sorted(glob.glob(os.path.join(root, "sys/class/power_supply/*"))):
        if _read(os.path.join(d, "type")) == "Mains":
            v = _int(os.path.join(d, "online"))
            if v is not None:
                _MAINS[root] = d
                return v == 1
    return None


# platform_profile -> (slider state on AC, on battery); spec inputs 3.
SLIDER_MAP = {
    "low-power": (1, 7),
    "balanced": (1, 2),
    "balanced-performance": (3, 4),
    "performance": (5, 6),
}


def platform_profile(root: str) -> tuple[str | None, str]:
    """The platform profile and where it came from. `/sys/firmware/acpi/platform_profile` is the legacy attribute that the
    kernel keeps for the registered handlers (Fedora/Bazzite devices show only this one); else the first class device
    `/sys/class/platform-profile/*/profile`, a Surface one first."""
    legacy = os.path.join(root, "sys/firmware/acpi/platform_profile")
    v = _read(legacy)
    if v:
        return v, "/sys/firmware/acpi/platform_profile"
    cls = sorted(glob.glob(os.path.join(root, "sys/class/platform-profile/*")),
                 key=lambda d: ("surface" not in (_read(os.path.join(d, "name")) or ""), d))
    for d in cls:
        v = _read(os.path.join(d, "profile"))
        if v:
            return v, "/sys/class/platform-profile/" + os.path.basename(d)
    return None, ""


class SliderState(Source):
    def __init__(self, root: str = "/"):
        self.root = root
        self.ref = ChannelRef(SYSTEM, 1)
        self.channels = {self.ref}
        self.profile: str | None = None
        self.profile_source = ""
        self.ac: bool | None = None

    def read(self, now_ms):
        self.profile, self.profile_source = platform_profile(self.root)
        self.ac = _ac_online(self.root)
        pair = SLIDER_MAP.get(self.profile or "")
        if pair is None or self.ac is None:
            return {self.ref: None}
        return {self.ref: pair[0] if self.ac else pair[1]}


class Batteries(Source):
    """Charge level (%) and discharge rate (mW) of battery 1 (BAT1, display part) and 2 (BAT2, base)."""

    def __init__(self, root: str = "/"):
        self.root = root
        self.bats = {1: "BAT1", 2: "BAT2"}
        self.channels = {ChannelRef(POWER, c) for c in (1, 10, 101, 110)}

    def read(self, now_ms):
        out = {}
        for n, name in self.bats.items():
            d = os.path.join(self.root, "sys/class/power_supply", name)
            base = 0 if n == 1 else 100
            out[ChannelRef(POWER, base + 1)] = _int(os.path.join(d, "capacity"))
            status = _read(os.path.join(d, "status"))
            if status is None:
                out[ChannelRef(POWER, base + 10)] = None
            elif status == "Discharging":
                uw = _int(os.path.join(d, "power_now"))
                out[ChannelRef(POWER, base + 10)] = uw / 1000 if uw is not None else None
            else:
                out[ChannelRef(POWER, base + 10)] = 0  # [H] 0 while charging or full
        return out


DTX_PATH = "/dev/surface/dtx"
SDTX_IOCTL_GET_DEVICE_MODE = (2 << 30) | (2 << 16) | (0xA5 << 8) | 0x2A  # _IOR(0xa5, 0x2a, __u16) [H]


def dtx_device_mode() -> int | None:
    """0 tablet, 1 laptop, 2 studio; None if surface_dtx is not available."""
    import fcntl
    try:
        fd = os.open(DTX_PATH, os.O_RDONLY)
    except OSError:
        return None
    try:
        buf = bytearray(2)
        fcntl.ioctl(fd, SDTX_IOCTL_GET_DEVICE_MODE, buf)
        return struct.unpack("<H", buf)[0]
    except OSError:
        return None
    finally:
        os.close(fd)


class Posture(Source):
    """Posture 1-5 from the dtx device mode and the lid (spec inputs 4), and the lid state (1 open, 2 closed)."""

    MAP = {1: (1, 2), 2: (5, 4), 0: (3, 3), None: (1, 2)}  # mode -> (lid open, lid closed)

    def __init__(self, root: str = "/", dtx_mode: Callable[[], int | None] = dtx_device_mode):
        self.root = root
        self.dtx_mode = dtx_mode
        self.posture, self.lid = ChannelRef(POSTURE, 1), ChannelRef(LID, 1)
        self.channels = {self.posture, self.lid}

    def _lid_open(self) -> bool | None:
        for p in sorted(glob.glob(os.path.join(self.root, "proc/acpi/button/lid/*/state"))):
            text = _read(p) or ""
            if "open" in text:
                return True
            if "closed" in text:
                return False
        return None

    def read(self, now_ms):
        lid = self._lid_open()
        mode = self.dtx_mode()
        pair = self.MAP.get(mode, self.MAP[None])
        lid_closed = lid is False
        return {self.posture: pair[1] if lid_closed else pair[0],
                self.lid: None if lid is None else (1 if lid else 2)}


# --- platform power data from the EC (spec inputs 6) ---

SSAM_CDEV = "/dev/surface/aggregator"


def sam_present(root: str = "/") -> bool:
    """True on a SAM machine: the SSAM controller registered its bus (the hub, registry and clients hang on it)."""
    return os.path.isdir(os.path.join(root, "sys/bus/surface_aggregator/devices"))


def cdev_hint(outputs: int, inputs: int) -> str | None:
    """One line for the report and the daemon log: what root and the debug device would add."""
    if not outputs and not inputs:
        return None
    return (f"run as root with surface_aggregator_cdev loaded: {outputs} more output{'s' if outputs != 1 else ''} and "
            f"{inputs} EC input{'s' if inputs != 1 else ''} become available")


class CdevTransport:
    """Requests through the SSAM debug character device. Interim (NFR-11): needs root and surface_aggregator_cdev."""

    def __init__(self, path: str = SSAM_CDEV):
        self.path = path

    def available(self) -> bool:
        """False if the device node is missing (module surface_aggregator_cdev not loaded)."""
        return os.path.exists(self.path)

    def request(self, tid: int, cid: int, iid: int = 1) -> bytes | None:
        import ctypes
        import fcntl

        class Buf(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("data", ctypes.c_uint64), ("length", ctypes.c_uint16), ("pad", ctypes.c_uint8 * 6)]

        class Req(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("tc", ctypes.c_uint8), ("tid", ctypes.c_uint8), ("cid", ctypes.c_uint8), ("iid", ctypes.c_uint8),
                        ("flags", ctypes.c_uint16), ("status", ctypes.c_int16), ("payload", Buf), ("response", Buf)]

        ioctl_request = (3 << 30) | (ctypes.sizeof(Req) << 16) | (0xA5 << 8) | 1
        try:
            fd = os.open(self.path, os.O_RDWR)
        except OSError:
            return None
        try:
            buf = ctypes.create_string_buffer(64)
            r = Req(tc=0x02, tid=tid, cid=cid, iid=iid, flags=1)  # target category BAT, response expected
            r.response.data = ctypes.addressof(buf)
            r.response.length = 64
            fcntl.ioctl(fd, ioctl_request, r)
            if r.status != 0:
                return None
            return buf.raw[:r.response.length]
        except OSError:
            return None
        finally:
            os.close(fd)

    # --- generic requests (fan and charging outputs, adapters/fan.py) ---

    def can_write(self) -> bool:
        """True if this process may open the device node for writing (root, module loaded)."""
        return os.path.exists(self.path) and os.access(self.path, os.W_OK)

    def needs_root(self) -> bool:
        """The node exists, but this user may not open it."""
        return os.path.exists(self.path) and not os.access(self.path, os.W_OK) and os.geteuid() != 0

    def _call(self, tc: int, tid: int, cid: int, iid: int, payload: bytes, want_response: bool) -> bytes | None:
        """One request. Returns the answer (b"" without one), or None on any error or a non-zero status."""
        import ctypes
        import fcntl

        class Buf(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("data", ctypes.c_uint64), ("length", ctypes.c_uint16), ("pad", ctypes.c_uint8 * 6)]

        class Req(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("tc", ctypes.c_uint8), ("tid", ctypes.c_uint8), ("cid", ctypes.c_uint8), ("iid", ctypes.c_uint8),
                        ("flags", ctypes.c_uint16), ("status", ctypes.c_int16), ("payload", Buf), ("response", Buf)]

        ioctl_request = (3 << 30) | (ctypes.sizeof(Req) << 16) | (0xA5 << 8) | 1
        try:
            fd = os.open(self.path, os.O_RDWR)
        except OSError:
            return None
        try:
            out = ctypes.create_string_buffer(64)
            data = ctypes.create_string_buffer(bytes(payload), max(1, len(payload)))
            r = Req(tc=tc, tid=tid, cid=cid, iid=iid, flags=1 if want_response else 0)   # flags 0: write, no response
            r.payload.data = ctypes.addressof(data)
            r.payload.length = len(payload)
            if want_response:
                r.response.data = ctypes.addressof(out)
                r.response.length = 64
            fcntl.ioctl(fd, ioctl_request, r)
            if r.status != 0:
                return None
            return out.raw[:r.response.length] if want_response else b""
        except OSError:
            return None
        finally:
            os.close(fd)

    def send(self, tc: int, cid: int, iid: int, payload: bytes, tid: int = 1) -> bool:
        """A write request without a response (flags 0). True if the EC acknowledged the frame on the transport; that does
        not show that the EC knows the command."""
        return self._call(tc, tid, cid, iid, payload, False) is not None

    def read(self, tc: int, cid: int, iid: int = 1, tid: int = 1) -> bytes | None:
        """A read request with a response, for any target category."""
        return self._call(tc, tid, cid, iid, b"", True)


class RequestGate:
    """Consecutive failures per EC request. A command the EC does not know times out on every request (kernel#195 review),
    so after FAIL_LIMIT failures in a row it is not sent again until reset() (resume)."""

    FAIL_LIMIT = 3

    def __init__(self):
        self.fails: dict[str, int] = {}

    def open(self, key: str) -> bool:
        return self.fails.get(key, 0) < self.FAIL_LIMIT

    def record(self, key: str, ok: bool) -> bool:
        """Note one outcome. True if this failure closed the gate."""
        if ok:
            self.fails[key] = 0
            return False
        self.fails[key] = self.fails.get(key, 0) + 1
        return self.fails[key] == self.FAIL_LIMIT

    def reset(self) -> None:
        self.fails.clear()


def ask(transport, gate: RequestGate, key: str, what: str, log, tid: int, cid: int, min_len: int = 1) -> bytes | None:
    """One EC request through the gate. None without an answer (also while the gate is closed, or the device node is
    missing, which the daemon reports once at start)."""
    available = getattr(transport, "available", None)
    if (available is not None and not available()) or not gate.open(key):
        return None
    data = transport.request(tid, cid)
    if data is not None and len(data) >= min_len:
        gate.record(key, True)
        return data
    if gate.record(key, False) and log:
        log("warning", f"{what}: no answer {gate.FAIL_LIMIT} times in a row; the command is not sent again until the next "
                       "resume and its inputs stay invalid")
    return None


class PlatformPower(Source):
    """`0x53` fields of SAM (battery 1) and KIP (battery 2) and the PSU size (spec inputs 6).

    Preferred source: the kernel attributes `power_supply/BAT<n>/dptf/*` (surface_battery) and
    `power_supply/ADP1/dptf/adapter_rating_mw` (surface_charger); older kernels name the groups `dptf_battery` and
    `dptf_power`, which are read as well. The kernel updates the battery values on the EC
    event, so they are read on every call; the rating costs an EC request per read and is read on source changes and
    every 60 s. Without these attributes, the values are requested through the SSAM debug
    device (interim, NFR-11): at start, on AC changes and every 60 s. The two paths can be mixed per battery.
    """

    REFRESH_MS = 60000
    ADAPTER_RETRY_MS = 5000          # an adapter rating of 0 on AC is asked again after this long, three times
    ADAPTER_RETRIES = 3
    # (offset in the 0x53 answer, struct format, kernel attribute, channel offset within the battery's block)
    FIELDS = ((1, "<I", "max_platform_power_mw", 2), (5, "<I", "max_steady_state_power_mw", 3),
              (9, "<H", "high_freq_impedance_mohm", 4), (11, "<H", "no_load_voltage_mv", 5),
              (17, "<I", "rest_of_platform_power_mw", 6), (13, "<I", "available_input_power_mw", 8))
    BATTERIES = ((1, "BAT1", 0), (2, "BAT2", 100))      # (SSAM target, power_supply name, channel base)
    ARTG = ChannelRef(POWER, 7)
    BATTERY_GROUPS = ("dptf", "dptf_battery")       # new kernel name first; the old one is on installed systems
    ADAPTER_GROUPS = ("dptf", "dptf_power")

    def __init__(self, root: str = "/", transport=None):
        self.root = root
        self.transport = transport if transport is not None else CdevTransport(os.path.join(root, SSAM_CDEV.lstrip("/")))
        self.channels = {ChannelRef(POWER, base + f[3]) for *_, base in self.BATTERIES for f in self.FIELDS} | {self.ARTG}
        self.values: dict[ChannelRef, float | None] = {ref: None for ref in self.channels}
        self.wanted: set[ChannelRef] | None = None
        self.gate = RequestGate()
        self.log = None                                  # set by the daemon: log(priority, text)
        self.answers: dict[int, bytes | None] = {}       # last 0x53 answer per SSAM target (report)
        self.artg_raw: int | None = None                 # last answer to the adapter rating request, as sent (report)
        self.zero_tries = 0
        self.retry_soon = False
        self.next_ms: int | None = None
        self.artg_next_ms: int | None = None
        self.ac: bool | None = None
        self.ok = False

    def restrict(self, wanted: set[ChannelRef]) -> None:
        """Ask the EC only for what the blob reads: a battery or command the device does not have times out."""
        self.wanted = set(wanted)

    def _batteries(self):
        return [b for b in self.BATTERIES
                if self.wanted is None or any(ChannelRef(POWER, b[2] + f[3]) in self.wanted for f in self.FIELDS)]

    def _want_artg(self) -> bool:
        return self.wanted is None or self.ARTG in self.wanted

    def _psy(self, *parts: str) -> str:
        return os.path.join(self.root, "sys/class/power_supply", *parts)

    def _sysfs_battery(self, name: str) -> str | None:
        for group in self.BATTERY_GROUPS:
            d = self._psy(name, group)
            if os.path.isdir(d):
                return d
        return None

    def _sysfs_artg(self) -> str | None:
        for group in self.ADAPTER_GROUPS:
            p = self._psy("ADP1", group, "adapter_rating_mw")
            if os.path.exists(p):
                return p
        return None

    @property
    def needs_cdev(self) -> bool:
        """True if any value still has to come through the SSAM debug device."""
        return ((self._want_artg() and self._sysfs_artg() is None)
                or any(self._sysfs_battery(n) is None for _, n, _ in self._batteries()))

    def cdev_channels(self) -> set[ChannelRef]:
        """The channels that come only through the debug device (no kernel attribute for them)."""
        out = {ChannelRef(POWER, base + f[3]) for _, n, base in self._batteries() if self._sysfs_battery(n) is None
               for f in self.FIELDS}
        if self._want_artg() and self._sysfs_artg() is None:
            out.add(self.ARTG)
        return out

    def _adapter(self, v: int | None, ac: bool | None) -> int | None:
        """The adapter rating. 0 means "no adapter" and is valid on battery (the blob reads exactly 0 there). On AC it
        cannot be true: the EC does not know the rating yet [H], so it counts as unknown and is asked again soon."""
        if v == 0 and ac is True:
            self.zero_tries += 1
            self.retry_soon = self.zero_tries <= self.ADAPTER_RETRIES
            if self.zero_tries == self.ADAPTER_RETRIES + 1 and self.log:
                self.log("warning", "adapter rating reads 0 mW while on AC; treated as unknown (input invalid), asked again "
                                    "every 60 s")
            return None
        self.zero_tries = 0
        return v

    def _read_sysfs(self, now_ms: int, ac: bool | None) -> None:
        before = tuple(self.values[ChannelRef(POWER, base + 8)] for *_, base in self.BATTERIES)
        for _, name, base in self.BATTERIES:
            d = self._sysfs_battery(name)
            if d:
                for *_, attr, ch in self.FIELDS:
                    self.values[ChannelRef(POWER, base + ch)] = _int(os.path.join(d, attr))
        p = self._sysfs_artg()
        if not p:
            return
        # Every read of adapter_rating_mw is an EC request, while the battery values are cached by the driver and updated
        # on the EC event. So the rating is read again only when the power source changes (AC online, or the available
        # input power of a battery moves) and otherwise every 60 s.
        after = tuple(self.values[ChannelRef(POWER, base + 8)] for *_, base in self.BATTERIES)
        if self.artg_next_ms is None or now_ms >= self.artg_next_ms or ac != self.ac or after != before:
            self.retry_soon = False
            self.values[self.ARTG] = self._adapter(_int(p), ac)            # ENODATA (no rating known) reads as None
            self.artg_next_ms = now_ms + (self.ADAPTER_RETRY_MS if self.retry_soon else self.REFRESH_MS)

    def refresh(self, ac: bool | None = None) -> None:
        """Requests through the debug device for everything the kernel does not provide and the blob reads."""
        self.retry_soon = False
        for tid, name, base in self._batteries():
            if self._sysfs_battery(name):
                continue
            data = ask(self.transport, self.gate, f"0x53/{tid}", f"EC request 0x53 (battery target {tid})", self.log,
                       tid, 0x53, 21)
            self.answers[tid] = data
            for off, fmt, _, ch in self.FIELDS:
                self.values[ChannelRef(POWER, base + ch)] = struct.unpack_from(fmt, data, off)[0] if data else None
        if self._want_artg() and not self._sysfs_artg():
            artg = ask(self.transport, self.gate, "0x0f", "EC request 0x0f (adapter rating)", self.log, 1, 0x0F, 4)
            raw = struct.unpack_from("<I", artg, 0)[0] if artg else None
            self.artg_raw = raw
            self.values[self.ARTG] = None if raw in (None, 0x80000000) else self._adapter(raw, ac)
        self.ok = self.values[ChannelRef(POWER, 8)] is not None

    def request_refresh(self) -> None:
        """Force a refresh at the next read, e.g. after resume. A command that stopped answering is tried again once."""
        self.next_ms = None
        self.artg_next_ms = None
        self.zero_tries = 0
        self.gate.reset()

    def read(self, now_ms):
        ac = _ac_online(self.root)
        if ac != self.ac:
            self.zero_tries = 0
        if self.needs_cdev and (self.next_ms is None or now_ms >= self.next_ms or ac != self.ac):
            self.refresh(ac)
            self.next_ms = now_ms + (self.ADAPTER_RETRY_MS if self.retry_soon else self.REFRESH_MS)
        self._read_sysfs(now_ms, ac)
        self.ac = ac
        self.ok = self.values[ChannelRef(POWER, 8)] is not None
        return dict(self.values)


# --- inputs of the newer blobs (spec inputs 8) ---

def _backlight(root: str) -> str | None:
    """The panel backlight: `firmware` before `platform` before `raw`, as the desktop tools pick it."""
    dirs = sorted(glob.glob(os.path.join(root, "sys/class/backlight/*")))
    order = {"firmware": 0, "platform": 1, "raw": 2}
    dirs.sort(key=lambda d: order.get(_read(os.path.join(d, "type")) or "", 3))
    return dirs[0] if dirs else None


class DisplayBrightness(Source):
    """`I_DISP_PERCENT1`: brightness setting of the panel in percent, 0-100 (spec inputs 8.1)."""

    def __init__(self, root: str = "/"):
        self.dir = _backlight(root)
        self.ref = ChannelRef(STATE, 42)
        self.channels = {self.ref} if self.dir else set()

    def read(self, now_ms):
        b = _int(os.path.join(self.dir, "brightness"))
        m = _int(os.path.join(self.dir, "max_brightness"))
        return {self.ref: round(100 * b / m) if b is not None and m else None}


INTERNAL_CONNECTORS = ("eDP", "LVDS", "DSI")
SURFACE_DOCK_IDS = {("045e", "0900"), ("045e", "0857"), ("045e", "09a9")}


def _connectors(root: str) -> list[tuple[str, str]]:
    """(connector type, connector dir) for every DRM connector, e.g. ("DP", ".../card1-DP-1")."""
    out = []
    for d in sorted(glob.glob(os.path.join(root, "sys/class/drm/card[0-9]*-*"))):
        name = os.path.basename(d).split("-", 1)[1]
        out.append((name.rsplit("-", 1)[0], d))
    return out


def _runtime_pm(dev: str) -> tuple[str, str] | None:
    """(power/control, power/runtime_status) of the nearest ancestor of `dev` that has runtime PM."""
    d = os.path.realpath(dev)
    while d not in ("/", ""):
        status = _read(os.path.join(d, "power/runtime_status"))
        if status is not None and status != "unsupported":
            return _read(os.path.join(d, "power/control")) or "", status
        d = os.path.dirname(d)
    return None


class PlatformEvents(Source):
    """Channels the Windows Surface service reports (spec inputs 8.2): external display (2), camera in use (3),
    number of Surface Dock hubs (9)."""

    def __init__(self, root: str = "/"):
        self.root = root
        self.ext, self.camera, self.dock = (ChannelRef(POSTURE, c) for c in (2, 3, 9))
        self.channels = {self.camera, self.dock}
        if os.path.isdir(os.path.join(root, "sys/class/drm")):
            self.channels.add(self.ext)

    def external_display(self) -> int:
        return int(any(t not in INTERNAL_CONNECTORS and _read(os.path.join(d, "status")) == "connected"
                       for t, d in _connectors(self.root)))

    def _video_nodes_open(self) -> set[str]:
        """Names of the video nodes some process has open (needs root to see every process)."""
        nodes = set()
        for fd in glob.glob(os.path.join(self.root, "proc/[0-9]*/fd/*")):
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("/dev/video"):
                nodes.add(target[len("/dev/"):])
        return nodes

    def camera_in_use(self) -> int:
        nodes = sorted(glob.glob(os.path.join(self.root, "sys/class/video4linux/video*")))
        opened = None
        for n in nodes:
            pm = _runtime_pm(os.path.join(n, "device"))
            if pm and pm[0] == "auto":
                if pm[1] == "active":
                    return 1
                continue
            if opened is None:                           # runtime PM tells nothing: look for an open node
                opened = self._video_nodes_open()
            if os.path.basename(n) in opened:
                return 1
        return 0

    def docks(self) -> int:
        n = 0
        for d in glob.glob(os.path.join(self.root, "sys/bus/usb/devices/*")):
            ids = (_read(os.path.join(d, "idVendor")), _read(os.path.join(d, "idProduct")))
            n += ids in SURFACE_DOCK_IDS
        return n

    def read(self, now_ms):
        out = {self.camera: self.camera_in_use(), self.dock: self.docks()}
        if self.ext in self.channels:
            out[self.ext] = self.external_display()
        return out


class BatteryCurrent(Source):
    """`I_BCDR1_I`: current of battery 1 in mA, positive while discharging, negative otherwise (spec inputs 8.3)."""

    def __init__(self, root: str = "/"):
        self.dir = os.path.join(root, "sys/class/power_supply/BAT1")
        self.ref = ChannelRef(POWER, 12)
        self.channels = {self.ref} if os.path.isdir(self.dir) else set()

    def read(self, now_ms):
        status = _read(os.path.join(self.dir, "status"))
        ua = _int(os.path.join(self.dir, "current_now"))
        if ua is None:
            uw, uv = _int(os.path.join(self.dir, "power_now")), _int(os.path.join(self.dir, "voltage_now"))
            ua = uw / uv * 1e6 if uw is not None and uv else None
        if status is None or ua is None:
            return {self.ref: None}
        ma = abs(ua) / 1000
        return {self.ref: round(ma) if status == "Discharging" else -round(ma)}


class BatteryEcState(Source):
    """`I_BDR_EWMA1_LVL` (13) and `I_BAT1_MAX_CHARGE` (16): one-byte SAM battery queries 0x1A and 0x69
    (spec inputs 8.4). Interim through the SSAM debug device, like the 0x53 data."""

    REFRESH_MS = 10000
    QUERIES = ((13, 0x1A), (16, 0x69))

    def __init__(self, root: str = "/", transport=None):
        self.transport = transport if transport is not None else CdevTransport(os.path.join(root, SSAM_CDEV.lstrip("/")))
        available = getattr(self.transport, "available", None)
        ok = available() if available else True
        self.channels = {ChannelRef(POWER, ch) for ch, _ in self.QUERIES} if ok else set()
        self.queries = self.QUERIES
        self.values: dict[ChannelRef, float | None] = {ref: None for ref in self.channels}
        self.next_ms: int | None = None
        self.gate = RequestGate()
        self.log = None                                  # set by the daemon: log(priority, text)

    def restrict(self, wanted: set[ChannelRef]) -> None:
        """Send only the queries whose channel the blob reads (0x69 on a device whose blob never reads it would time out)."""
        self.queries = tuple(q for q in self.QUERIES if ChannelRef(POWER, q[0]) in wanted)
        self.values = {ChannelRef(POWER, ch): None for ch, _ in self.queries if ChannelRef(POWER, ch) in self.channels}

    def request_refresh(self) -> None:
        self.next_ms = None
        self.gate.reset()

    def read(self, now_ms):
        if self.next_ms is None or now_ms >= self.next_ms:
            for ch, cid in self.queries:
                data = ask(self.transport, self.gate, f"0x{cid:02x}", f"EC request 0x{cid:02x} (battery state {ch})", self.log,
                           1, cid)
                self.values[ChannelRef(POWER, ch)] = data[0] if data else None
            self.next_ms = now_ms + self.REFRESH_MS
        return dict(self.values)


class StandbyPhase(Source):
    """`I_MS_NOTIFY`: constant 4 (normal) while the daemon runs (spec inputs 8.5). Windows leaves 4 only on Modern
    Standby notifications while the user counts as present; idle display-off comes after "not present", and the
    sleep phases (2, 3, 5) happen while the daemon is suspended."""

    def __init__(self, root: str = "/"):
        self.ref = ChannelRef(MS_NOTIFY, 100)
        self.channels = {self.ref}

    def read(self, now_ms):
        return {self.ref: 4}


class DgpuOnBattery(Source):
    """`1FB53D4C/31` (Laptop Studio dGPU blobs): 1 on battery, 0 on AC, as the Windows GPU power controller reports
    the AC/DC power source (spec inputs 8.7)."""

    def __init__(self, root: str = "/"):
        self.root = root
        self.ref = ChannelRef(STATE, 31)
        self.channels = {self.ref}

    def read(self, now_ms):
        ac = _ac_online(self.root)
        return {self.ref: None if ac is None else int(not ac)}


class ProximitySensor(Source):
    """`87008365/113` (Pro 9 Intel, Laptop 5): state of the Windows proximity-envelope custom sensor. 0 = no valid
    reading, which is what Windows sends without the sensor; Linux has no driver for it (spec inputs 8.8)."""

    def __init__(self, root: str = "/"):
        self.ref = ChannelRef(COMPOSITE, 113)
        self.channels = {self.ref}

    def read(self, now_ms):
        return {self.ref: 0}


class CpuTempAverage(Source):
    """`87008365/112` (I_CPU_TEMP_AVG): the Windows SoC client reads the package thermal status (MSR 0x1B1, bits 22:16) [C] once per
    request, without averaging [C], and converts it with TjMax. Linux `coretemp` "Package id 0" reads the same register
    (deviation DV-7, spec inputs 8.11). The blob only uses it to raise the fan noise floor above ~71 C."""

    def __init__(self, root: str = "/"):
        self.ref = ChannelRef(COMPOSITE, 112)
        self.path = None
        for d in _hwmon_dirs(root, "coretemp"):
            for lab in glob.glob(os.path.join(d, "temp*_label")):
                if _read(lab) == "Package id 0":
                    self.path = lab[:-len("_label")] + "_input"
        self.channels = {self.ref} if self.path else set()

    def read(self, now_ms):
        v = _int(self.path) if self.path else None
        return {self.ref: mc_to_dk(v) if v is not None else None}


class XboxController(Source):
    """`87008365/5` (Laptop Studio 2): 1 while an Xbox controller is connected, else 0. Windows reports "controller
    active (Xbox connection)" through the SMF client; a joystick input device from Microsoft (vendor 045e) or one
    named "Xbox" counts here (spec inputs 8.9) [H: the exact Windows criterion]."""

    def __init__(self, root: str = "/"):
        self.root = root
        self.ref = ChannelRef(COMPOSITE, 5)
        self.channels = {self.ref}

    def read(self, now_ms):
        for d in glob.glob(os.path.join(self.root, "sys/class/input/input*")):
            if not glob.glob(os.path.join(d, "js*")):
                continue                                    # not a joystick
            name = _read(os.path.join(d, "name")) or ""
            if _read(os.path.join(d, "id/vendor")) == "045e" or "xbox" in name.lower():
                return {self.ref: 1}
        return {self.ref: 0}


class DgpuSensors(Source):
    """dGPU power `1FB53D4C/3` (mW) and temperature `2E8B14C2/101` (0.1 K) of the Laptop Studio (spec inputs 8.10).
    Windows gets both from the NVIDIA driver. Here NVML is only asked while another process keeps the GPU awake,
    at most every 2 s; a runtime-suspended GPU reads as 0 W with an invalid temperature."""

    SAMPLE_MS = 2000

    def __init__(self, root: str = "/", nvml_factory=None, probe=None):
        from . import gpu
        self.gpu = gpu
        self.root = root
        self.factory = nvml_factory or gpu.Nvml
        self.pci = gpu.find_nvidia_gpu(root)
        self.probe = probe or gpu.ClientProbe(root, self.pci)
        self.power, self.temp = ChannelRef(STATE, 3), ChannelRef(TEMP, 101)
        self.channels = {self.power, self.temp} if self.pci else set()
        self.values: dict[ChannelRef, float | None] = {self.power: None, self.temp: None}
        self.next_ms = 0

    def read(self, now_ms):
        if self.gpu.runtime_status(self.root, self.pci) == "suspended":
            self.values = {self.power: 0, self.temp: None}
        elif now_ms >= self.next_ms and self.probe.has_client(now_ms):
            self.next_ms = now_ms + self.SAMPLE_MS
            try:
                with self.factory(self.pci) as nv:
                    self.values = {self.power: nv.read().power_w * 1000, self.temp: ZERO_C + 10 * nv.temperature_c()}
            except self.gpu.NvmlError:
                self.values = {self.power: None, self.temp: None}
        return dict(self.values)


# Current a Type-C source offers without PD, per power_operation_mode (USB 3 default: 900 mA).
TYPEC_MODE_MA = {"default": 900, "1.5A": 1500, "3.0A": 3000}


class UsbSourcePower(Source):
    """`I_USB_SOURCE_PWR`: upper bound of the power this device supplies through its USB-C ports, mW
    (spec inputs 8.6). Windows measures it; Linux only knows what each port offers."""

    def __init__(self, root: str = "/"):
        self.root = root
        self.ref = ChannelRef(SPT, 9)
        self.channels = {self.ref} if os.path.isdir(os.path.join(root, "sys/class/typec")) else set()

    def _pd_max_mw(self, port: str) -> int | None:
        best = None
        for pdo in glob.glob(os.path.join(port, "usb_power_delivery/source-capabilities/*:fixed_supply")):
            mv, ma = _int(os.path.join(pdo, "voltage")), _int(os.path.join(pdo, "maximum_current"))
            if mv is not None and ma is not None:
                best = max(best or 0, mv * ma // 1000)
        return best

    def read(self, now_ms):
        total = 0
        for port in sorted(glob.glob(os.path.join(self.root, "sys/class/typec/port[0-9]*"))):
            name = os.path.basename(port)
            if "-" in name or not os.path.isdir(os.path.join(self.root, "sys/class/typec", f"{name}-partner")):
                continue
            if "[source]" not in (_read(os.path.join(port, "power_role")) or ""):
                continue
            mode = _read(os.path.join(port, "power_operation_mode")) or ""
            mw = self._pd_max_mw(port) if mode == "usb_power_delivery" else None
            total += mw if mw is not None else 5 * TYPEC_MODE_MA.get(mode, 3000)
        return {self.ref: total}


def default_sources(root: str = "/") -> list[Source]:
    return [HwmonTemperatures(root), SliderState(root), Batteries(root), Posture(root), PlatformPower(root),
            DisplayBrightness(root), PlatformEvents(root), BatteryCurrent(root), BatteryEcState(root),
            StandbyPhase(root), UsbSourcePower(root), DgpuOnBattery(root), ProximitySensor(root),
            XboxController(root), DgpuSensors(root)]


class InputReader:
    """Collects all sources for the channels a blob reads. Channels nobody provides are reported once and stay invalid."""

    def __init__(self, wanted: Iterable[ChannelRef], sources: list[Source]):
        self.wanted = set(wanted)
        self.sources = sources
        provided = set().union(*(s.channels for s in sources)) if sources else set()
        self.unavailable = sorted(r for r in self.wanted - provided if r.guid != PLACEHOLDER)
        for s in sources:
            if hasattr(s, "restrict"):
                s.restrict(self.wanted)

    def read(self, now_ms: int) -> dict[ChannelRef, float | None]:
        out: dict[ChannelRef, float | None] = {r: None for r in self.wanted}
        for s in self.sources:
            if not (s.channels & self.wanted):
                continue
            for ref, v in s.read(now_ms).items():
                if ref in self.wanted:
                    out[ref] = v
        return out
