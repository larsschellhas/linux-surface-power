"""The kit's record of what it asked the Surface's controller (SAM), and what the answers say about the kernel (plan D9-D11).

Every request of the report goes through a recorder: category, target, command, instance (no payload: the kit only reads) and the
raw answer. A kernel change must be proven by command, instance, payload and the devices it affects (CLAUDE.md "Kernel changes");
this is the first three, from the device itself.
"""
import glob
import os
import struct

from .. import sensorprobe
from . import read

TC_BAT, TC_TMP, TC_FAN = 0x02, 0x03, 0x05
TEMP_NODE = "01:03:01:00:02"          # `surface_temp` binds to it [C, drivers/hwmon/surface_temp.c]
FAN_NODE = "01:05:01:01:01"           # `surface_fan`, first fan of the main unit [C, drivers/hwmon/surface_fan.c]
HUB_DRIVER = "sys/bus/platform/drivers/surface_aggregator_platform_hub"


class Recorder:
    """Passes everything to the real transport and keeps each request with its answer."""

    def __init__(self, transport):
        self.transport = transport
        self.records: list[dict] = []

    def __getattr__(self, name):                       # path, available(), can_write(), needs_root(), send()
        return getattr(self.transport, name)

    def _keep(self, tc: int, tid: int, cid: int, iid: int, answer: bytes | None) -> bytes | None:
        self.records.append({"tc": tc, "tid": tid, "cid": cid, "iid": iid, "answer": None if answer is None else answer.hex()})
        return answer

    def request(self, tid: int, cid: int, iid: int = 1):
        return self._keep(TC_BAT, tid, cid, iid, self.transport.request(tid, cid, iid))

    def read(self, tc: int, cid: int, iid: int = 1, tid: int = 1, capacity: int = 64):
        return self._keep(tc, tid, cid, iid, self.transport.read(tc, cid, iid, tid, capacity))


def _bytes(record: dict) -> bytes | None:
    try:
        return None if record.get("answer") is None else bytes.fromhex(record["answer"])
    except (TypeError, ValueError):
        return None


def find(records: list, tc: int, cid: int, iid: int | None = None, tid: int = 1) -> dict | None:
    for r in records:
        if isinstance(r, dict) and r.get("tc") == tc and r.get("cid") == cid and r.get("tid") == tid and (iid is None or r.get("iid") == iid):
            return r
    return None


def sensor_probe(records: list) -> tuple[bool, int | None, list[dict]]:
    """(was the mask asked, the mask or None, the sensors) from the recorded requests, decoded as the probe does."""
    asked = find(records, TC_TMP, sensorprobe.CID_SENSORS, 0)
    if asked is None:
        return False, None, []

    class Replayed:
        def read(self, tc, cid, iid=1, tid=1, capacity=64):
            r = find(records, tc, cid, iid, tid)
            return None if r is None else _bytes(r)
    mask, sensors = sensorprobe.probe(Replayed())
    return True, mask, sensors


def fan_rpm(records: list) -> tuple[bool, int | None]:
    r = find(records, TC_FAN, 0x01, 1)
    if r is None:
        return False, None
    raw = _bytes(r)
    return True, struct.unpack("<H", raw[:2])[0] if raw and len(raw) >= 2 else None


def hub_id(root: str) -> str | None:
    """The ACPI id the Surface platform hub was created for: the kernel's registry picks the device group by it."""
    for d in sorted(glob.glob(os.path.join(root, HUB_DRIVER, "MSHW*:*"))):
        return os.path.basename(d).split(":")[0]
    return None


def kernel_nodes(root: str, sam_devices: dict) -> dict:
    return {"hub_id": hub_id(root), "temperature_node": TEMP_NODE in sam_devices, "fan_node": FAN_NODE in sam_devices,
            "hwmon": sorted({n for d in glob.glob(os.path.join(root, "sys/class/hwmon/hwmon*"))
                             if (n := read(d, "name")) in ("surface_thermal", "surface_fan")})}


def console_lines(records: list, wanted: list[int]) -> tuple[list[str], str]:
    """(lines for the console, a short phrase for the S1 summary) about the temperature sensors."""
    asked, mask, sensors = sensor_probe(records)
    if not asked:
        return [], ""
    if mask is None:
        return (["Your kernel has no temperature sensors for this device, and the controller did not answer the question for them."],
                "Sensor probe: no answer.")
    answered = [s for s in sensors if s["celsius"] is not None]
    lines = [f"Your kernel has no temperature sensors for this device, so the check asked the controller (reads only). "
             f"{len(answered)} of {len(sensors)} sensors answered:"]
    lines += [f"sensor {s['sensor']}  {s['name'] or '(no name)'}  "
              + ("no answer" if s["celsius"] is None else f"{s['celsius']:.1f} °C") for s in sensors]
    missing = [n for n in wanted if n not in {s["sensor"] for s in answered}]
    need = ", ".join(map(str, wanted))
    verdict = (f"the sensors your device needs ({need}) answered" if wanted and not missing else
               f"of the sensors your device needs ({need}), {', '.join(map(str, missing))} did not answer" if missing else "")
    if verdict:
        lines.append(verdict[:1].upper() + verdict[1:] + ".")
    return lines, f"Sensor probe: {len(answered)} sensors answered" + (f"; {verdict}" if verdict else "") + "."


# --- what the answers mean for the kernel (our side, tools/check_eval.py) ---

# linux-surface/kernel `v7.3-surface` (2cd9dd3ce4cd), surface_aggregator_registry.c, read 27.11.2026 [C]:
# ACPI id -> (node group, has the temperature node, has the first fan node, has_dptf, the registry's comment)
GROUPS = {
    "MSHW0081": ("gen5", False, False, False, "Surface Pro 4, 5, 6"), "MSHW0111": ("gen5", False, False, False, "Surface Pro 6"),
    "MSHW0116": ("sp7", False, False, False, "Surface Pro 7"), "MSHW0119": ("sp7_dptf", False, False, True, "Surface Pro 7+"),
    "MSHW0263": ("sp8", False, False, True, "Surface Pro 8"), "MSHW0343": ("sp9_dptf", True, True, True, "Surface Pro 9"),
    "MSHW0510": ("sp9_dptf", True, True, True, "Surface Pro 10"), "MSHW0583": ("sp9_dptf", True, True, True, "Surface Pro 11"),
    "MSHW0743": ("sp12", True, True, False, "Surface Pro 12 (Intel)"), "MSHW0107": ("gen5", False, False, False, "Surface Book 2"),
    "MSHW0117": ("sb3", True, True, True, "Surface Book 3"), "MSHW0086": ("gen5", False, False, False, "Surface Laptop 1"),
    "MSHW0112": ("gen5", False, False, False, "Surface Laptop 2"), "MSHW0114": ("sl3", False, False, False, "Surface Laptop 3 13 Intel"),
    "MSHW0110": ("sl3_dptf", False, False, True, "Surface Laptop 3 and 4 AMD"),
    "MSHW0250": ("sl3_dptf", False, False, True, "Surface Laptop 4 13 Intel"),
    "MSHW0350": ("sl5", True, True, True, "Surface Laptop 5"), "MSHW0530": ("sl6", True, True, True, "Surface Laptop 6"),
    "MSHW0551": ("sl7", False, True, False, "Surface Laptop 7"), "MSHW0118": ("slg1", False, False, True, "Surface Laptop Go 1"),
    "MSHW0290": ("slg1", False, False, True, "Surface Laptop Go 2"), "MSHW0440": ("slg1", False, False, True, "Surface Laptop Go 3"),
    "MSHW0123": ("sls1", False, False, True, "Surface Laptop Studio 1"), "MSHW0360": ("sls2", True, True, True, "Surface Laptop Studio 2"),
}
PLAUSIBLE_C = (-20.0, 120.0)


def shared_with(hub: str) -> list[str]:
    group = GROUPS[hub][0]
    return [f"{i} ({g[4]})" for i, g in GROUPS.items() if g[0] == group and i != hub]


def request_text(r: dict) -> str:
    return (f"TC {r['tc']:#04x}, target {r['tid']:#04x}, command {r['cid']:#04x}, instance {r['iid']:#04x}, no payload -> "
            + ("no answer" if r.get("answer") is None else r["answer"]))
