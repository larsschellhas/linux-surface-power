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
STATE = "1FB53D4C-5BDE-4B4A-B3C4-C23F7872DA1F"   # power/state values; channel 41 = display brightness
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


class HwmonTemperatures(Source):
    """SAM sensors 501-599 from hwmon `surface_thermal` (temp<iid>_input), CPU package 102 from `coretemp`."""

    def __init__(self, root: str = "/"):
        self.paths: dict[ChannelRef, str] = {}
        for d in _hwmon_dirs(root, "surface_thermal"):
            for p in glob.glob(os.path.join(d, "temp*_input")):
                iid = int(os.path.basename(p)[4:-len("_input")])
                self.paths[ChannelRef(TEMP, 500 + iid)] = p
        for d in _hwmon_dirs(root, "coretemp"):
            for lab in glob.glob(os.path.join(d, "temp*_label")):
                if _read(lab) == "Package id 0":
                    self.paths[ChannelRef(TEMP, 102)] = lab[:-len("_label")] + "_input"
        self.channels = set(self.paths)

    def read(self, now_ms):
        out = {}
        for ref, p in self.paths.items():
            v = _int(p)
            out[ref] = mc_to_dk(v) if v is not None else None
        return out


def _ac_online(root: str) -> bool | None:
    for d in sorted(glob.glob(os.path.join(root, "sys/class/power_supply/*"))):
        if _read(os.path.join(d, "type")) == "Mains":
            v = _int(os.path.join(d, "online"))
            if v is not None:
                return v == 1
    return None


# platform_profile -> (slider state on AC, on battery); spec inputs 3.
SLIDER_MAP = {
    "low-power": (1, 7),
    "balanced": (1, 2),
    "balanced-performance": (3, 4),
    "performance": (5, 6),
}


class SliderState(Source):
    def __init__(self, root: str = "/"):
        self.root = root
        self.ref = ChannelRef(SYSTEM, 1)
        self.channels = {self.ref}
        self.profile: str | None = None
        self.ac: bool | None = None

    def read(self, now_ms):
        self.profile = _read(os.path.join(self.root, "sys/firmware/acpi/platform_profile"))
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


class CdevTransport:
    """Requests through the SSAM debug character device. Interim (NFR-11): needs root and surface_aggregator_cdev."""

    def __init__(self, path: str = SSAM_CDEV):
        self.path = path

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


class PlatformPower(Source):
    """`0x53` fields of SAM (battery 1) and KIP (battery 2) and the PSU size, requested at start, on AC changes, every 60 s."""

    REFRESH_MS = 60000
    # (offset, struct format, channel offset within the battery's block: 0 for SAM -> channels 2..8, 100 for KIP)
    FIELDS = ((1, "<I", 2), (5, "<I", 3), (9, "<H", 4), (11, "<H", 5), (17, "<I", 6), (13, "<I", 8))

    def __init__(self, root: str = "/", transport=None):
        self.root = root
        self.transport = transport if transport is not None else CdevTransport()
        self.channels = {ChannelRef(POWER, base + ch) for base in (0, 100) for *_, ch in self.FIELDS} | {ChannelRef(POWER, 7)}
        self.values: dict[ChannelRef, float | None] = {ref: None for ref in self.channels}
        self.next_ms: int | None = None
        self.ac: bool | None = None
        self.ok = False

    def refresh(self) -> None:
        for tid, base in ((1, 0), (2, 100)):
            data = self.transport.request(tid, 0x53)
            for off, fmt, ch in self.FIELDS:
                ref = ChannelRef(POWER, base + ch)
                self.values[ref] = struct.unpack_from(fmt, data, off)[0] if data and len(data) >= 21 else None
        artg = self.transport.request(1, 0x0F)
        v = struct.unpack_from("<I", artg, 0)[0] if artg and len(artg) >= 4 else None
        self.values[ChannelRef(POWER, 7)] = None if v in (None, 0x80000000) else v
        self.ok = self.values[ChannelRef(POWER, 8)] is not None

    def request_refresh(self) -> None:
        """Force a refresh at the next read, e.g. after resume."""
        self.next_ms = None

    def read(self, now_ms):
        ac = _ac_online(self.root)
        if self.next_ms is None or now_ms >= self.next_ms or ac != self.ac:
            self.ac = ac
            self.refresh()
            self.next_ms = now_ms + self.REFRESH_MS
        return dict(self.values)


def default_sources(root: str = "/") -> list[Source]:
    return [HwmonTemperatures(root), SliderState(root), Batteries(root), Posture(root), PlatformPower(root)]


class InputReader:
    """Collects all sources for the channels a blob reads. Channels nobody provides are reported once and stay invalid."""

    def __init__(self, wanted: Iterable[ChannelRef], sources: list[Source]):
        self.wanted = set(wanted)
        self.sources = sources
        provided = set().union(*(s.channels for s in sources)) if sources else set()
        self.unavailable = sorted(r for r in self.wanted - provided if r.guid != PLACEHOLDER)

    def read(self, now_ms: int) -> dict[ChannelRef, float | None]:
        out: dict[ChannelRef, float | None] = {r: None for r in self.wanted}
        for s in self.sources:
            if not (s.channels & self.wanted):
                continue
            for ref, v in s.read(now_ms).items():
                if ref in self.wanted:
                    out[ref] = v
        return out
