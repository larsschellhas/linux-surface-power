"""S1: everything an evaluation needs to know about the machine, read only (spec tester-kit.md 9.1).

Only named fields are collected: no serial numbers, no whole command line, environment or kernel log, no blob content.
"""
import glob
import hashlib
import os
import re

from ... import supervisor
from ...adapters import msr, rapl_copies
from ...loader import container, parser
from ...loader.errors import BlobError
from ...loader.source import EFIVAR, NoBlob, read_raw
from . import MCHBAR_BY_PLATFORM, REPO, SECURE_BOOT, read, read_int, sh

# Command-line options that change what the check can do or measure; nothing else of the command line is taken.
CMDLINE = re.compile(r"^(lockdown|module\.sig_enforce|module_blacklist|modprobe\.blacklist|intel_pstate|msr\.[\w]+|iomem|"
                     r"mem_sleep_default|intel_idle\.[\w]+|processor\.[\w]+)(=|$)")
# Units that manage power or write limits (spec tester-kit.md 6.4; surface-rapl: plan L8)
UNITS = ("surface-powerd", "surface-powerd-dryrun", "thermald", "tuned", "tuned-ppd", "tlp", "power-profiles-daemon", "surface-rapl")
MSRS = ((0x17, "platform ID"), (0x606, "RAPL units"), (0x610, "PL1/PL2"), (0x601, "PL4"), (0x611, "package energy"),
        (0x614, "package power info"), (0x615, "PL3"), (0x65C, "platform PL1/PL2"))
KERNEL_LINES = re.compile(r"\bmsr\b|\bmce\b|machine check", re.IGNORECASE)


def repo_state() -> dict:
    """The checkout this run came from (an evaluation needs it)."""
    git = ["git", "-c", f"safe.directory={REPO}", "-C", REPO]
    code, head = sh.run(git + ["rev-parse", "--short=12", "HEAD"])
    if code != 0:
        return {"commit": None, "dirty": None, "time": None}
    code, changed = sh.run(git + ["status", "--porcelain", "--untracked-files=no"])
    dirty = code == 0 and bool(changed)
    code, stamp = sh.run(git + ["log", "-1", "--format=%ct"])
    return {"commit": head, "dirty": dirty, "time": int(stamp) if code == 0 and stamp.isdigit() else None}


def repo_text(state: dict) -> str:
    if not state.get("commit"):
        return "unknown (no git checkout)"
    return state["commit"] + (" + local changes" if state.get("dirty") else "")


def machine(root: str) -> dict:
    dmi = "sys/class/dmi/id/"
    cpu_model = vendor = None
    cpus = 0
    for line in (read(root, "proc/cpuinfo") or "").splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "model name" and cpu_model is None:
            cpu_model = value.strip()
        elif key == "vendor_id" and vendor is None:
            vendor = value.strip()
        elif key == "processor":
            cpus += 1
    return {"product": read(root, dmi + "product_name"), "sku": read(root, dmi + "product_sku"),
            "bios_version": read(root, dmi + "bios_version"), "bios_date": read(root, dmi + "bios_date"),
            "cpu_model": cpu_model, "cpu_vendor": vendor, "cpus": cpus}


def secure_boot(root: str = "/") -> bool | None:
    try:
        with open(os.path.join(root, SECURE_BOOT), "rb") as f:
            return f.read()[-1] == 1
    except (OSError, IndexError):
        return None


def os_release(root: str) -> dict:
    text = read(root, "etc/os-release") or read(root, "usr/lib/os-release") or ""
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))
    return {k.lower(): fields[k].strip().strip('"') for k in ("ID", "ID_LIKE", "VERSION_ID", "PRETTY_NAME") if k in fields}


def distro_family(root: str) -> str | None:
    r = os_release(root)
    words = f"{r.get('id', '')} {r.get('id_like', '')}".split()
    for family, names in (("debian", ("debian", "ubuntu")), ("arch", ("arch",)), ("fedora", ("fedora",))):
        if any(n in words for n in names):
            return family
    return None


def platform_state(root: str, release: str) -> dict:
    version = read(root, "proc/version") or ""
    compiler = "clang" if "clang" in version else "gcc" if "gcc" in version else None     # the line also names a builder: not kept
    cmdline = [t for t in (read(root, "proc/cmdline") or "").split() if CMDLINE.match(t)]
    up = (read(root, "proc/uptime") or "").split()
    return {"kernel": release, "compiler": compiler, "os": os_release(root), "secure_boot": secure_boot(root),
            "lockdown": read(root, "sys/kernel/security/lockdown"), "sig_enforce": read(root, "sys/module/module/parameters/sig_enforce"),
            "proc_thermal": rapl_copies.has_proc_thermal(root), "cmdline": cmdline,
            "uptime_s": int(float(up[0])) if up else None, "suspends": read_int(root, "sys/power/suspend_stats/success"),
            "tainted": read_int(root, "proc/sys/kernel/tainted")}


def _thermald_options(root: str) -> list[str]:
    """The option names thermald runs with, without their values (a value can be a path)."""
    for path in glob.glob(os.path.join(root, "proc/[0-9]*/comm")):
        if read("/", path) == "thermald":
            try:
                with open(os.path.join(os.path.dirname(path), "cmdline"), "rb") as f:
                    args = f.read().decode("utf-8", "replace").split("\0")
            except OSError:
                return []
            return [a.split("=", 1)[0] for a in args if a.startswith("--")]
    return []


def unit_states() -> dict:
    out = {}
    for u in UNITS:
        code, text = sh.run(["systemctl", "show", f"{u}.service", "-p", "LoadState", "-p", "ActiveState", "-p", "UnitFileState"])
        fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        if code == 0 and fields.get("LoadState") not in (None, "not-found"):
            out[u] = {"active": fields.get("ActiveState"), "enabled": fields.get("UnitFileState")}
    return out


def software(root: str) -> dict:
    return {"writers": supervisor.limit_writers(root), "units": unit_states(), "thermald_options": _thermald_options(root)}


def _zone(path: str) -> dict:
    z = {"name": read(path, "name"), "enabled": read_int(path, "enabled"), "constraints": []}
    for i in range(8):
        name = read(path, f"constraint_{i}_name")
        if name is None:
            break
        z["constraints"].append({"name": name, **{k: read_int(path, f"constraint_{i}_{k}")
                                                  for k in ("power_limit_uw", "time_window_us", "max_power_uw")}})
    return z


def powercap_zones(root: str) -> dict:
    out = {}
    for d in sorted(glob.glob(os.path.join(root, "sys/class/powercap/intel-rapl*:*"))):
        if read(d, "name") is not None:
            out[os.path.basename(d)] = _zone(d)
    return out


def mmio_registers(root: str, platform: str | None) -> dict:
    """The kit's own read of the MMIO copy, without any module: MCHBAR against the type's constant, limit and unit register."""
    expected = MCHBAR_BY_PLATFORM.get(platform)
    out = {"mchbar": None, "mchbar_expected": None if expected is None else f"{expected:#x}", "limit": None, "unit": None,
           "reason": None}
    base, why = rapl_copies.mchbar(root)
    if base is None:
        out["reason"] = why
        return out
    out["mchbar"] = f"{base:#x}"
    if expected is not None and base != expected:
        out["reason"] = "MCHBAR differs from the constant of this platform type: nothing is read there"
        return out
    try:
        out["limit"] = f"{rapl_copies._mmio64(base + rapl_copies.MMIO_PL_OFFSET, root):#018x}"
        out["unit"] = f"{rapl_copies._mmio64(base + rapl_copies.MMIO_UNIT_OFFSET, root) & 0xFFFFFFFF:#010x}"
    except OSError as e:
        out["reason"] = f"/dev/mem not readable ({e.strerror or e})"
    return out


def limits(root: str, platform: str | None) -> dict:
    """Every limit the machine shows: powercap, the raw MSRs, the raw MMIO registers (procedure step 10)."""
    msrs = {}
    for reg, name in MSRS:
        value, why = msr.probe(root, reg)
        msrs[f"{reg:#x}"] = {"name": name, **({"value": f"{value:#018x}"} if value is not None else {"error": why})}
    return {"zones": powercap_zones(root), "msr": msrs, "mmio": mmio_registers(root, platform)}


def sam_devices(root: str) -> dict:
    """Bus id -> bound driver of every SAM device the kernel created (no serial numbers there)."""
    out = {}
    for d in sorted(glob.glob(os.path.join(root, "sys/bus/surface_aggregator/devices/*"))):
        drv = os.path.join(d, "driver")
        out[os.path.basename(d)] = os.path.basename(os.path.realpath(drv)) if os.path.exists(drv) else None
    return out


def sensors(root: str) -> list[dict]:
    out = []
    for d in sorted(glob.glob(os.path.join(root, "sys/class/hwmon/hwmon*"))):
        name = read(d, "name")
        if name not in ("surface_thermal", "surface_fan", "coretemp"):
            continue
        device = os.path.basename(os.path.realpath(os.path.join(d, "device")))
        values = {}
        for f in sorted(glob.glob(os.path.join(d, "temp*_input")) + glob.glob(os.path.join(d, "fan*_input"))):
            key = os.path.basename(f)[:-len("_input")]
            v = read_int("/", f)
            label = read(d, f"{key}_label") or key
            if v is not None:
                values[f"{key} {label}"] = round(v / 1000, 1) if key.startswith("temp") else v
        out.append({"name": name, "device": device if re.fullmatch(r"[0-9a-f:.]+|coretemp\.\d+", device) else None,
                    "values": values})
    return out


def ec_attributes(root: str) -> dict:
    """The DPTF values where the kernel provides them (kernel#195)."""
    out: dict = {}
    for f in sorted(glob.glob(os.path.join(root, "sys/class/power_supply/*/dptf/*"))):
        supply = os.path.basename(os.path.dirname(os.path.dirname(f)))
        out.setdefault(supply, {})[os.path.basename(f)] = read("/", f)
    return out


def kernel_messages() -> dict:
    """Kernel lines about MSRs and machine checks of this boot (plan F10). The journal first: `dmesg` loses old lines."""
    code, text = sh.run(["journalctl", "-k", "-b", "--no-pager", "-o", "short-monotonic"])
    source = "journal"
    if code != 0 or not text:
        code, text = sh.run(["dmesg"])
        source = "dmesg (older lines can be missing)"
    lines = [ln for ln in text.splitlines() if KERNEL_LINES.search(ln)]
    # the journal puts the host name in front of "kernel:": only what follows it is kept
    lines = [re.sub(r"^(\[\s*[\d.]+\])\s+\S+\s+kernel:", r"\1", ln) for ln in lines]
    return {"source": source, "lines": lines[-20:]}


def blob(root: str) -> tuple[dict, object]:
    """(what may be shared about the blob, the parsed blob or None). Never its content."""
    try:
        data, _, is_var = read_raw(os.path.join(root, EFIVAR.lstrip("/")))
        dec = container.decode(data, efivar=is_var)
        parsed = parser.parse_text(dec.text, dec.form)
    except (NoBlob, BlobError, OSError) as e:
        return {"error": str(e)}, None
    return {"form": dec.form, "bytes": len(dec.text), "sha256": hashlib.sha256(dec.text.encode()).hexdigest(),
            "lines": len(parsed.algorithms)}, parsed
