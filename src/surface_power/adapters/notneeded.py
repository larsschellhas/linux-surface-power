"""Outputs that need no Linux path, with the evidence (spec outputs 10, docs/research/12).

An output is listed here only if the Windows receivers do nothing with it that Linux could do: no register, SAM or ACPI write, or no
receiver at all. The readiness rule counts such an output as covered, but never silently: status, report and device pages show
"not needed on Linux: <reason>". Evidence markers: [C] read from the drivers, [H] hypothesis.
"""
import os

from ..loader.model import ChannelRef

MINPWR2 = ChannelRef("110DF2A4-5995-473C-92FD-3CAC40EE2E6C", 3)

# ref -> (short reason, intel_only). Intel-only: the evidence comes from the Intel SoC client; AMD and Qualcomm clients are not analysed.
NOT_NEEDED: dict[ChannelRef, tuple[str, bool]] = {
    MINPWR2: ("no Windows client takes it: the thermal-policy client registers channels 1 and 2 only and its policy structure has "
              "no field for it; only the power tracker records it (telemetry) [C]", False),
}


def cpu_vendor(root: str) -> str | None:
    try:
        with open(os.path.join(root, "proc/cpuinfo")) as f:
            for line in f:
                if line.startswith("vendor_id"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def reason(ref: ChannelRef, root: str = "/") -> str | None:
    """Why `ref` needs no Linux path on this machine, or None."""
    entry = NOT_NEEDED.get(ref)
    if entry is None:
        return None
    text, intel_only = entry
    if intel_only and cpu_vendor(root) not in (None, "GenuineIntel"):   # unreadable cpuinfo only in fake roots
        return None
    return text


def static_reason(ref: ChannelRef, platform: str = "intel") -> str | None:
    """Same for the device pages, which know the platform of the blob instead of the machine."""
    entry = NOT_NEEDED.get(ref)
    if entry is None or (entry[1] and platform != "intel"):
        return None
    return entry[0]
