"""Read-only probe of the SAM temperature sensors on devices without the kernel's sensor node (plan D9-D11, deviation DV-10).

It sends the three requests the kernel driver `surface_temp` sends at its probe and on a read [C, drivers/hwmon/surface_temp.c], to
target 1 (SAM): TMP 0x04 (instance 0, a 16-bit mask of the sensors; bit n = sensor n+1), then for each sensor in the mask 0x0E (name,
21 bytes: u16, char, name[18]) and 0x01 (temperature, u16 in 0.1 K). Without an answer to 0x04 nothing else is sent. It writes
nothing. It answers whether a device may get the sensor node; it is not an input source of the daemon.
"""
import struct

from ..adapters import inputs as inp

TC_TMP = 0x03
CID_TEMPERATURE, CID_SENSORS, CID_NAME = 0x01, 0x04, 0x0E
NAME_ANSWER_LEN = 21
SAM_CHANNELS = range(501, 517)          # blob channel 500 + sensor number


def wanted_sensors(refs) -> list[int]:
    """The SAM sensor numbers among a blob's inputs."""
    return sorted(r.channel - 500 for r in refs if r.guid == inp.TEMP and r.channel in SAM_CHANNELS)


def probe(transport) -> tuple[int | None, list[dict]]:
    """(mask, one entry per sensor in the mask with `sensor`, `name`, `celsius`); mask None without a usable answer to 0x04."""
    raw = transport.read(TC_TMP, CID_SENSORS, 0)
    if raw is None or len(raw) < 2:
        return None, []
    mask = struct.unpack("<H", raw[:2])[0]
    sensors = []
    for sensor in (bit + 1 for bit in range(16) if mask >> bit & 1):
        name = transport.read(TC_TMP, CID_NAME, sensor)
        text = None
        if name is not None and len(name) >= NAME_ANSWER_LEN:
            text = "".join(c for c in name[3:NAME_ANSWER_LEN].split(b"\0")[0].decode("ascii", "replace") if c.isprintable())
        temp = transport.read(TC_TMP, CID_TEMPERATURE, sensor)
        celsius = None if temp is None or len(temp) < 2 else round((struct.unpack("<H", temp[:2])[0] - 2731) / 10, 1)
        sensors.append({"sensor": sensor, "name": text, "celsius": celsius})
    return mask, sensors


def lines(transport, wanted: list[int], reason: str) -> list[str]:
    """The report's lines."""
    if not transport.can_write():
        return ["sensor probe: skipped (the debug device is missing or needs root)"]
    mask, sensors = probe(transport)
    if mask is None:
        return [f"sensor probe (read only, SAM TMP; {reason}): no answer to TMP 0x04; nothing else was sent"]
    out = [f"sensor probe (read only, SAM TMP; {reason}): TMP 0x04 mask {mask:#06x}"]
    for s in sensors:
        temp = "no answer" if s["celsius"] is None else f"{s['celsius']:.1f} °C"
        name = "no answer" if s["name"] is None else f"'{s['name']}'"
        out.append(f"  sensor {s['sensor']}: name {name}, {temp}")
    if wanted:
        out.append("  the blob reads " + ", ".join(
            f"sensor {n} ({'in the mask' if mask >> (n - 1) & 1 else 'NOT in the mask'})" for n in wanted))
    return out
