"""Is the running kernel current enough for a check, and what would an update add on this device (spec tester-kit.md 3a)?

What a release contains is taken from `patches/7.3/0007-surface-sam.patch` at the release tags of linux-surface/linux-surface (read
27.11.2026): 7.3.5-1 (Debian, Arch, Fedora) is the first with the DPTF battery data and adapter rating (kernel#195) and the Surface
Book 3 sensors and fans (kernel#190); 7.3.4-2 has neither [C]. Which device gets what follows the registry of `v7.3-surface` [C].
The kit sends and fetches nothing: NEWEST is what this checkout knows and is raised by hand with new releases.
"""
import glob
import os
import re
from dataclasses import dataclass, field

NEEDED = (7, 3, 5, 1)       # first release with our kernel changes
NEWEST = (7, 3, 6, 1)       # newest linux-surface release known to this checkout (27.11.2026)

# ACPI ids of the Surface platform hub whose registry group has the `has_dptf` property since 7.3.5-1
DPTF_IDS = {"MSHW0110", "MSHW0117", "MSHW0118", "MSHW0119", "MSHW0123", "MSHW0250", "MSHW0263", "MSHW0290", "MSHW0343",
            "MSHW0350", "MSHW0360", "MSHW0440", "MSHW0510", "MSHW0530", "MSHW0583"}
SENSOR_IDS = {"MSHW0117"}   # temperature sensors and fan speeds new in 7.3.5-1 (Surface Book 3, main unit and base)
# A tester's report about this release on this device (plan D12); the cause is not known.
CAUTION = {"MSHW0343": "Surface Pro 9 (Intel): one owner reports Type Cover problems after resume on 7.3.5 and 7.3.6; the cause is "
                       "not known yet (https://github.com/linux-surface/surface-aggregator-module/issues/29)."}
HOWTO = "https://github.com/linux-surface/linux-surface/wiki/Installation-and-Setup"

# 7.3.6-1-surface (Debian), 7.3.4-arch1-2-surface (Arch), 7.3.6-1.surface.fc43.x86_64 (Fedora [H: format not seen in a report])
_RELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-arch\d+)?-(\d+)[.-]surface(?![\w-])")


def parse(release: str) -> tuple[int, int, int, int] | None:
    """(major, minor, patch, package release) of a linux-surface kernel, else None."""
    m = _RELEASE.match(release)
    return tuple(int(g) for g in m.groups()) if m else None


def text(v: tuple[int, int, int, int]) -> str:
    return "{}.{}.{}-{}".format(*v)


def hub_id(root: str) -> str | None:
    """The ACPI id the Surface platform hub was created for, if it is one this module knows something about."""
    for d in sorted(glob.glob(os.path.join(root, "sys/bus/platform/devices/MSHW*:*"))):
        i = os.path.basename(d).split(":")[0]
        if i in DPTF_IDS | SENSOR_IDS:
            return i
    return None


def missing_features(root: str, hub: str | None) -> list[str]:
    """What a current linux-surface kernel provides on this device and this one does not."""
    out = []
    if hub in DPTF_IDS and not glob.glob(os.path.join(root, "sys/class/power_supply/*/dptf")):
        out.append("EC power data (battery peak power, adapter rating) as kernel attributes; without them the daemon needs the "
                   "debug device")
    if hub in SENSOR_IDS:
        names = []
        for f in glob.glob(os.path.join(root, "sys/class/hwmon/hwmon*/name")):
            try:
                with open(f) as fh:
                    names.append(fh.read().strip())
            except OSError:
                pass
        if "surface_thermal" not in names:
            out.append("temperature sensors and fan speeds of the main unit and the base")
    return out


def newer_installed(root: str, running: tuple[int, int, int, int]) -> str | None:
    """The newest installed linux-surface kernel above the running one (installed, not booted)."""
    found = [(v, os.path.basename(d)) for d in glob.glob(os.path.join(root, "lib/modules/*"))
             if (v := parse(os.path.basename(d))) is not None and v > running]
    return max(found)[1] if found else None


@dataclass
class Advice:
    summary: str                                   # one line, always shown and put into the result
    ask: bool = False                              # the tester should update first: ask before going on
    details: list[str] = field(default_factory=list)


def advise(release: str, root: str = "/") -> Advice:
    v, hub = parse(release), hub_id(root)
    missing = missing_features(root, hub)
    gaps = [f"missing here: {m}" for m in missing]
    update = [f"Please update to the current linux-surface kernel ({text(NEWEST)} or newer), reboot, and run this check again. With "
              "the linux-surface repository set up, a normal system update installs it. Keep your old kernel installed, so that you "
              f"can go back. How to: {HOWTO}"] + ([CAUTION[hub]] if hub in CAUTION else [])
    if v is None:
        if hub and not missing:
            return Advice(f"kernel {release}: not a linux-surface release this check knows; what it needs from the kernel is there")
        return Advice(f"kernel {release}: not a linux-surface release this check knows", True,
                      gaps + ["The results would show gaps that the linux-surface kernel may already close."] + update)
    newer = newer_installed(root, v)
    if newer and v < NEWEST:
        return Advice(f"kernel {text(v)} is running, {newer} is installed", True,
                      gaps + [f"Please reboot into {newer} (pick it in the boot menu if it is not the default) and run this check "
                              "again."] + ([CAUTION[hub]] if hub in CAUTION else []))
    if v < NEEDED:
        why = gaps or [f"Releases before {text(NEEDED)} lack our latest Surface changes; none is known to change this check on your "
                       "device, but results from the current kernel are the ones we can compare."]
        return Advice(f"kernel {text(v)} is outdated (current: {text(NEWEST)})", True, why + update)
    if missing:
        return Advice(f"kernel {text(v)} is new enough, but something expected on this device is missing", False,
                      gaps + ["Please go on: this is something we want to see in the result."])
    if v < NEWEST:
        return Advice(f"kernel {text(v)}: a newer linux-surface release exists ({text(NEWEST)}); this check works with yours")
    return Advice(f"kernel {text(v)}: current, as far as this checkout knows")
