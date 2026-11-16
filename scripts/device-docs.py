#!/usr/bin/env python3
"""Generate the per-device pages in docs/devices/ from the local blob corpus.

The blobs are Microsoft content and are not part of the repository; the pages describe only which channels a blob reads
and writes, which block types it uses, and where Linux gets or puts each value.
Usage: python3 -I scripts/device-docs.py [FOLDER] [--out docs/devices]
FOLDER defaults to docs/research/raw/tools/fw (git-ignored), one sub-folder per driver package with the blobs below it.
"""
import argparse
import glob
import hashlib
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from surface_power.engine.graph import build_graph, outputs_reached_by  # noqa: E402
from surface_power.engine.registry import INTERNAL_NODE  # noqa: E402
from surface_power.loader import container, parser  # noqa: E402
from surface_power.loader.errors import BlobError  # noqa: E402
from surface_power.loader.model import ChannelRef  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEFAULT = os.path.join(ROOT, "docs", "research", "raw", "tools", "fw")
AS_OF = "16.11.2026"

# Package folder -> (page name, title, profile file or None, controller variant). Variants: docs/research/09-newer-blobs.md 3.
# Platform: Intel unless listed in PLATFORM.
DEVICES = {
    "sb3": None,  # split by blob size below
    "pro7p": ("surface-pro-7-plus", "Surface Pro 7+", None, "direct"),
    "pro8": ("surface-pro-8", "Surface Pro 8", "surface-pro-8.toml", "classic"),
    "pro9": ("surface-pro-9-intel", "Surface Pro 9 (Intel)", None, "direct"),
    "pro9_5g": ("surface-pro-9-5g", "Surface Pro 9 5G (arm64)", None, "direct"),
    "pro10": ("surface-pro-10", "Surface Pro 10 for Business", None, "direct"),
    "pro10_5g": ("surface-pro-10-5g", "Surface Pro 10 5G for Business", None, "direct"),
    "pro11_intel": ("surface-pro-11-intel", "Surface Pro 11 (Intel)", None, "direct"),
    "pro11_snap": ("surface-pro-11-snapdragon", "Surface Pro 11 (Snapdragon, arm64)", None, "direct"),
    "pro12_1": ("surface-pro-12-1st-edition", "Surface Pro 12\" 1st Edition (arm64)", None, "direct"),
    "lap3_amd": ("surface-laptop-3-amd", "Surface Laptop 3 (AMD)", "surface-laptop-3-amd.toml", "classic"),
    "lap4_amd": ("surface-laptop-4-amd", "Surface Laptop 4 (AMD)", "surface-laptop-4-amd.toml", "classic"),
    "lap4_intel": ("surface-laptop-4-intel", "Surface Laptop 4 (Intel)", None, "direct"),
    "lap5": ("surface-laptop-5", "Surface Laptop 5", None, "direct"),
    "lap6": ("surface-laptop-6", "Surface Laptop 6 for Business", None, "direct"),
    "lap7_intel": ("surface-laptop-7-intel", "Surface Laptop 7 (Intel)", None, "direct"),
    "lap7_5g_intel": ("surface-laptop-7-5g", "Surface Laptop 7 5G for Business", None, "direct"),
    "lap7_snap": ("surface-laptop-7-snapdragon", "Surface Laptop 7 (Snapdragon, arm64)", None, "direct"),
    "lap13_1_snap": ("surface-laptop-13", "Surface Laptop 13\" (arm64)", None, "direct"),
    "lapgo1": ("surface-laptop-go", "Surface Laptop Go", "surface-laptop-go.toml", "classic"),
    "lapgo2": ("surface-laptop-go-2", "Surface Laptop Go 2", None, "direct"),
    "lapgo3": ("surface-laptop-go-3", "Surface Laptop Go 3", None, "direct"),
    "go3": ("surface-go-3", "Surface Go 3", "surface-go-3.toml", "classic"),
    "go4": ("surface-go-4", "Surface Go 4", None, "direct"),
    "ls1": ("surface-laptop-studio", "Surface Laptop Studio", "surface-laptop-studio.toml", "classic"),
    "ls2": ("surface-laptop-studio-2", "Surface Laptop Studio 2", None, "direct"),
}
PLATFORM = {"lap3_amd": "amd", "lap4_amd": "amd", "pro9_5g": "arm64", "pro11_snap": "arm64", "pro12_1": "arm64",
            "lap7_snap": "arm64", "lap13_1_snap": "arm64"}
PLATFORM_NAMES = {"intel": "Intel", "amd": "AMD", "arm64": "Qualcomm Snapdragon (arm64)"}
SB3 = {"13in": ("surface-book-3-13", "Surface Book 3 13.5\""), "15in": ("surface-book-3-15", "Surface Book 3 15\"")}

TEMP, POWER, SYSTEM, POSTURE, LID = "2E8B14C2", "28B7BD85", "2DD6960F", "8060B17F", "5D0EE597"
STATE, SPT, COMPOSITE, MS, LIMITS = "1FB53D4C", "049E36F2", "87008365", "DEF1AB69", "0B7B5350"
PLACEHOLDER = "99999999"

INTERFACES = {
    TEMP: "temperatures", POWER: "battery and power data", SYSTEM: "system state", POSTURE: "posture and platform events",
    LID: "lid", STATE: "power and display state", SPT: "power tracker items", COMPOSITE: "client-computed channels",
    MS: "Modern Standby", LIMITS: "power limits", "110DF2A4": "thermal notifications",
    "9BBB8910": "thermal throttle requests", "B620798F": "fan and charging", "A79674B4": "sensor offsets",
    "CF2F6076": "SoC (not analysed)", "349D0E39": "SoC (not analysed)", PLACEHOLDER: "placeholder",
}

# Linux status of an input: "sysfs", "ec" (EC data: kernel#195 attributes or the debug device), "debug" (debug device only),
# "estimate", "constant", "nvml", "none".
INPUTS = {
    (POWER, 1): ("I_BATT1_RSOC", "charge level of battery 1, %", "`BAT1/capacity`", "sysfs"),
    (POWER, 2): ("I_BAT_PEAK1", "battery peak power, mW", "EC `0x53`", "ec"),
    (POWER, 3): ("I_BAT_MAX1", "battery sustained power, mW", "EC `0x53`", "ec"),
    (POWER, 4): ("", "battery resistance, mΩ", "EC `0x53`", "ec"),
    (POWER, 5): ("I_BAT_VNL1", "battery open-circuit voltage, mV", "EC `0x53`", "ec"),
    (POWER, 6): ("I_ROP_DYN", "rest-of-platform power, mW", "EC `0x53`", "ec"),
    (POWER, 7): ("I_PSU_SIZE", "power supply rating, mW", "EC `ARTG`", "ec"),
    (POWER, 8): ("I_IPL_TOP", "available input power, mW", "EC `0x53`", "ec"),
    (POWER, 10): ("", "charge/discharge rate of battery 1, mW", "`BAT1/power_now`", "sysfs"),
    (POWER, 12): ("I_BCDR1_I", "battery current, mA (+ discharging)", "`BAT1` current or power/voltage", "sysfs"),
    (POWER, 13): ("I_BDR_EWMA1_LVL", "discharge-rate EWMA level", "EC `0x1A`", "debug"),
    (POWER, 16): ("I_BAT1_MAX_CHARGE", "max charge reached, 0/1", "EC `0x69`", "debug"),
    (POWER, 101): ("", "charge level of battery 2 (base), %", "`BAT2/capacity`", "sysfs"),
    (POWER, 103): ("", "battery 2 sustained power, mW", "EC `0x53` (base)", "ec"),
    (POWER, 110): ("", "charge/discharge rate of battery 2, mW", "`BAT2/power_now`", "sysfs"),
    (SYSTEM, 1): ("I_SLIDER", "power slider 1–10", "`platform_profile` × AC", "sysfs"),
    (POSTURE, 1): ("I_POSTURE", "posture 1–5", "`surface_dtx` + lid", "sysfs"),
    (POSTURE, 2): ("I_EXT_DISPLAY", "external display connected", "DRM connectors", "sysfs"),
    (POSTURE, 3): ("I_CAMERA", "camera in use", "video4linux runtime PM / open nodes", "sysfs"),
    (POSTURE, 9): ("I_SURF_DOCK", "Surface Docks attached", "USB IDs", "sysfs"),
    (LID, 1): ("I_LID", "lid open/closed", "lid switch", "sysfs"),
    (STATE, 3): ("", "dGPU power, mW", "NVML", "nvml"),
    (STATE, 10): ("", "CPU maximum frequency 2", "–", "none"),
    (STATE, 21): ("", "not analysed", "–", "none"),
    (STATE, 31): ("", "on battery, 0/1", "`Mains/online`", "sysfs"),
    (STATE, 32): ("I_NPU_UTIL", "NPU use, %", "–", "none"),
    (STATE, 41): ("", "display brightness, millinits", "– (Windows fallback, DV-3)", "none"),
    (STATE, 42): ("I_DISP_PERCENT1", "display brightness, %", "backlight", "sysfs"),
    (STATE, 43): ("I_DISP_PWR1", "display power", "–", "none"),
    (SPT, 2): ("I_SYS_PWR", "system power from the power meters, mW", "–", "none"),
    (SPT, 6): ("", "power-meter item (not analysed)", "–", "none"),
    (SPT, 9): ("I_USB_SOURCE_PWR", "power out of the USB-C ports, mW", "Type-C ports (upper bound)", "estimate"),
    (COMPOSITE, 5): ("", "Xbox controller connected", "input devices", "sysfs"),
    (COMPOSITE, 112): ("I_CPU_TEMP_AVG", "averaged CPU temperature (client-computed)", "–", "none"),
    (COMPOSITE, 113): ("", "proximity sensor state", "0 (no sensor driver)", "constant"),
    (COMPOSITE, 200): ("I_AMBIENT_AI", "estimated ambient temperature (client-computed)", "–", "none"),
    (MS, 100): ("I_MS_NOTIFY", "Modern Standby phase 1–5", "constant 4", "constant"),
}

OUTPUTS = {
    (LIMITS, 51): ("O_CPU_AVG", "PL1, package long-term", "powercap `package-0/long_term`", "written"),
    (LIMITS, 52): ("O_CPU_MAX", "PL2, package short-term", "powercap `package-0/short_term`", "written"),
    (LIMITS, 53): ("O_CPU_PEAK", "PL4, package peak", "powercap `package-0/peak_power`", "optional"),
    (LIMITS, 54): ("O_SYS_AVG", "psys PL1, platform long-term", "powercap `psys/long_term`", "written"),
    (LIMITS, 55): ("O_SYS_MAX", "psys PL2, platform short-term", "powercap `psys/short_term`", "written"),
    (LIMITS, 59): ("O_SYS_PEAK", "PL3, platform peak with duty cycle", "MSR `0x615`", "optional"),
    (LIMITS, 64): ("", "not analysed", "–", "log"),
    (LIMITS, 70): ("O_BATT_MODE", "battery mode", "–", "log"),
    (LIMITS, 71): ("", "not analysed", "–", "log"),
    (LIMITS, 201): ("", "dGPU power limit", "clock-cap stand-in (DO-1, profile)", "optional"),
    (LIMITS, 205): ("", "GPU power controller limit (not analysed)", "–", "log"),
    (LIMITS, 208): ("", "GPU power controller limit (not analysed)", "–", "log"),
    ("110DF2A4", 1): ("O_HOT", "hot notification", "–", "log"),
    ("110DF2A4", 2): ("O_CRT", "critical notification", "–", "log"),
    ("110DF2A4", 3): ("O_MINPWR2", "minimum-power request", "–", "log"),
    ("A79674B4", 509): ("O_SEN9_OFFSET", "offset for sensor 9", "–", "log"),
    ("CF2F6076", 61): ("", "SoC value (not analysed)", "–", "log"),
    ("349D0E39", 1): ("", "SoC value (not analysed)", "–", "log"),
    ("349D0E39", 4): ("", "SoC value (not analysed)", "–", "log"),
    (COMPOSITE, 220): ("O_FAST_MAXIM_POLLING", "fast power-meter polling", "–", "log"),
    (COMPOSITE, 250): ("", "not analysed", "–", "log"),
    (COMPOSITE, 300): ("O_CAMERA_AINR", "camera AI noise reduction allowed", "–", "log"),
}
WTMF = {1: "O_WTMF_CPU0", 2: "O_WTMF_CPU1", 3: "O_WTMF_CPU2", 4: "O_WTMF_CPU3", 5: "O_WTMF_GFX",
        20: "O_WTMF_E_CORES_C1", 21: "O_WTMF_P_CORES_C2", 22: "O_WTMF_P_CORES_C3", 62: "O_WTMF_PARK",
        103: "O_MDM_SKIN", 104: "O_MDM_MJCT", 105: "O_MDM_MBJ0", 200: "O_WTMF_NPU"}
FANS = {114: "O_FAN1_MAX_SONES", 121: "O_FAN1_ACTIVE_SPL", 124: "O_FAN1_ACTIVE_SONES", 201: "O_FAN1_PROFILE",
        301: "O_FAN_CS_REQUEST", 302: "O_MCU_FAN_ON_TEMP", 304: "O_MCU_FAN_RAMP_TEMP", 305: "O_MCU_FAN_MAX_TEMP",
        307: "O_MCU_FAN_CRITICAL_TEMP", 308: "O_MCU_PROCHOT_TEMP", 309: "O_NORMAL_CHARGE_TEMP", 310: "O_SLOW_CHARGE_TEMP",
        311: "O_TRICKLE_CHARGE_TEMP", 312: "O_MCU_FAN_MAX_SONES"}

WRITTEN = {51, 52, 54, 55}
STATUS_IN = {"sysfs": "provided", "ec": "EC data (kernel#195 or debug device)", "debug": "debug device (root)",
             "estimate": "estimate", "constant": "constant", "nvml": "NVML", "none": "not provided"}
STATUS_OUT = {"written": "written", "optional": "optional (profile)", "noplat": "no Linux path", "log": "logged only"}


def g8(guid: str) -> str:
    return guid[:8].upper()


def input_info(ref: ChannelRef):
    g, c = g8(ref.guid), ref.channel
    if g == TEMP:
        if 500 < c < 600:
            return ("", f"SAM sensor {c - 500}", f"hwmon `surface_thermal` temp{c - 500}", "sysfs")
        if 600 < c < 700:
            return ("", f"KIP (base) sensor {c - 600}", f"hwmon `surface_thermal` (base) temp{c - 600}", "sysfs")
        if c == 101:
            return ("", "dGPU temperature", "NVML", "nvml")
        if c == 102:
            return ("", "CPU package temperature", "hwmon `coretemp`", "sysfs")
    if g == PLACEHOLDER:
        return ("I_NULL", "placeholder, never provided", "–", "constant")
    return INPUTS.get((g, c), ("", "not analysed", "–", "none"))


def output_info(ref: ChannelRef, platform: str = "intel"):
    g, c = g8(ref.guid), ref.channel
    if g == "9BBB8910":
        ms = WTMF.get(c, "")
        if c == 62:
            return (ms, "core parking request", "candidate: take CPUs offline", "log")
        if "CPU" in ms or "CORES" in ms:
            return (ms, "throttle request for a CPU cluster, % (100 = none)", "candidate: cpufreq `scaling_max_freq`", "log")
        if "GFX" in ms:
            return (ms, "throttle request for the GPU, % (100 = none)", "candidate: GPU maximum frequency", "log")
        return (ms, "throttle request, % (100 = none)", "–", "log")
    if g == "B620798F":
        return (FANS.get(c, ""), "fan or charging setting for the EC", "candidate: EC through SAM (commands not known)", "log")
    info = OUTPUTS.get((g, c), ("", "not analysed", "–", "log"))
    if platform != "intel" and g == LIMITS and info[3] in ("written", "optional") and c != 201:
        return (info[0], info[1], f"none on {PLATFORM_NAMES[platform]} (Intel RAPL only)", "noplat")
    return info


def name(ref):
    return f"`{g8(ref.guid)}/{ref.channel}`"


def load(path):
    with open(path, "rb") as f:
        data = f.read()
    dec = container.decode(data)
    parsed = parser.parse_text(dec.text, dec.form)
    return dec, parsed, data


def device_page(key, title, profile, variant, blobs, platform="intel") -> tuple[str, dict]:
    ins, outs, blocks, reach, ranges = {}, {}, Counter(), defaultdict(set), {}
    rows = []
    for path in blobs:
        dec, parsed, data = load(path)
        graph = build_graph(parsed, variant)
        rows.append((os.path.basename(path), dec.form, len(parsed.algorithms),
                     hashlib.sha256(dec.text.encode()).hexdigest()[:16], graph.usable_for_control))
        for b in graph.blocks:
            blocks[b.kind] += 1
        for r in graph.inputs:
            ins[(g8(r.guid), r.channel)] = r
            for o in outputs_reached_by(r, parsed.algorithms):
                if g8(o.guid) == LIMITS:
                    reach[(g8(r.guid), r.channel)].add(o.channel)
        for r in graph.outputs:
            outs[(g8(r.guid), r.channel)] = r
            oc = graph.output_ranges.get(r)
            if oc is not None:
                ranges.setdefault((g8(r.guid), r.channel), set()).add((oc.default, oc.max, oc.min))

    def limit_names(chs):
        names = {51: "PL1", 52: "PL2", 53: "PL4", 54: "psys PL1", 55: "psys PL2", 59: "PL3", 201: "dGPU"}
        return ", ".join(names.get(c, str(c)) for c in sorted(chs) if c in names)

    L = [f"# {title}", "",
         f"Generated by `scripts/device-docs.py` from the local blob corpus, as of {AS_OF}. Status of the Linux side: "
         f"[`../specs/inputs.md`](../specs/inputs.md), [`../specs/outputs.md`](../specs/outputs.md). Nothing on this page "
         f"is verified on the device unless it says so.", "",
         "## Blob", "",
         "| File | Form | Lines | SHA-256 (text, first 16) | Engine runs it |", "|---|---|---|---|---|"]
    for f, form, n, sha, ok in rows:
        L.append(f"| {f} | {form} | {n} | `{sha}` | {'✓' if ok else '–'} |")
    L += ["", f"- **Platform:** {PLATFORM_NAMES[platform]}",
          f"- **Controller variant:** `{variant}`" + (f" (profile `{profile}`)" if profile else " (no profile: default)"),
          "- **Block types:** " + ", ".join(f"{k} ({v})" for k, v in sorted(blocks.items())), ""]

    # Diagram: input groups -> blob -> output groups.
    groups_in = defaultdict(list)
    for k, r in sorted(ins.items()):
        if k[0] == PLACEHOLDER:
            continue
        groups_in[STATUS_IN[input_info(r)[3]]].append(k)
    groups_out = defaultdict(list)
    for k, r in sorted(outs.items()):
        groups_out[STATUS_OUT[output_info(r, platform)[3]]].append(k)
    L += ["## Overview", "", "```mermaid", "flowchart LR"]
    order_in = [STATUS_IN[k] for k in ("sysfs", "ec", "debug", "nvml", "estimate", "constant", "none")]
    for i, grp in enumerate(g for g in order_in if g in groups_in):
        ks = groups_in[grp]
        label = "<br/>".join(f"{g}/{c}" for g, c in ks[:8]) + ("<br/>…" if len(ks) > 8 else "")
        L.append(f'    I{i}["<b>{grp}</b> ({len(ks)})<br/>{label}"] --> B')
    L.append(f'    B(["SMF blob<br/>{sum(r[2] for r in rows)} lines, {len(blocks)} block types"])')
    for i, grp in enumerate(g for g in STATUS_OUT.values() if g in groups_out):
        ks = groups_out[grp]
        label = "<br/>".join(f"{g}/{c}" for g, c in ks[:8]) + ("<br/>…" if len(ks) > 8 else "")
        L.append(f'    B --> O{i}["<b>{grp}</b> ({len(ks)})<br/>{label}"]')
    L += ["```", ""]

    L += ["## Inputs", "", "| Channel | Microsoft name | Meaning | Linux source | Status | Reaches limits |", "|---|---|---|---|---|---|"]
    blocking = []
    for k, r in sorted(ins.items()):
        if k[0] == PLACEHOLDER:
            continue
        ms, what, src, st = input_info(r)
        lim = reach.get(k, set())
        L.append(f"| {name(r)} | {ms or '–'} | {what} | {src} | {STATUS_IN[st]} | {limit_names(lim) or '–'} |")
        if st == "none" and lim & WRITTEN and platform == "intel":
            blocking.append(k)
    L += ["", "## Outputs", "", "| Channel | Microsoft name | Meaning | Linux | Status | Range (default / max / min) |",
          "|---|---|---|---|---|---|"]
    for k, r in sorted(outs.items()):
        ms, what, tgt, st = output_info(r, platform)
        rg = "; ".join(f"{d:g} / {mx:g} / {mn:g}" for d, mx, mn in sorted(ranges.get(k, ()))) or "–"
        L.append(f"| {name(r)} | {ms or '–'} | {what} | {tgt} | {STATUS_OUT[st]} | {rg} |")
    L.append("")
    if blocking:
        L.append("**Missing inputs that reach a written limit:** " + ", ".join(f"`{g}/{c}`" for g, c in blocking)
                 + ". The daemon stays in dry-run on this device.")
        L.append("")
    summary = {"title": title, "blobs": len(rows), "inputs": len(ins), "outputs": len(outs), "blocking": blocking,
               "written": sorted(c for g, c in outs if g == LIMITS and c in WRITTEN) if platform == "intel" else [],
               "platform": platform, "outs": outs, "groups_in": groups_in}
    return "\n".join(L), summary


def catalogue(all_outs: dict) -> list[str]:
    L = ["## All outputs in the corpus", "",
         "Which interface each output belongs to and what Linux does with it on an Intel device (on AMD and Snapdragon "
         "devices Linux has no path for the power limits yet). \"Devices\" counts the driver packages whose blobs write it.", "",
         "| Channel | Interface | Microsoft name | Meaning | Linux | Status | Devices |", "|---|---|---|---|---|---|---|"]
    for k in sorted(all_outs):
        r = all_outs[k][0]
        ms, what, tgt, st = output_info(r)
        L.append(f"| {name(r)} | {INTERFACES.get(k[0], '?')} | {ms or '–'} | {what} | {tgt} | {STATUS_OUT[st]} | "
                 f"{len(all_outs[k])} |")
    return L + [""]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", nargs="?", default=DEFAULT)
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "devices"))
    args = ap.parse_args()
    pages = {}
    for key, spec in DEVICES.items():
        files = []
        for f in sorted(glob.glob(os.path.join(args.folder, key, "keep", "**", "*.bin"), recursive=True)):
            try:
                load(f)
                files.append(f)
            except BlobError:
                continue                               # MPTF, telemetry
        if not files:
            continue
        if key == "sb3":
            for tag, (page, title) in SB3.items():
                sel = [f for f in files if tag in os.path.basename(f)]
                pages[page] = (title, "surface-book-3.toml", "classic", sel, "intel")
        else:
            page, title, profile, variant = spec
            pages[page] = (title, profile, variant, files, PLATFORM.get(key, "intel"))
    os.makedirs(args.out, exist_ok=True)
    summaries, all_outs = {}, defaultdict(list)
    for page, (title, profile, variant, files, platform) in pages.items():
        text, s = device_page(page, title, profile, variant, files, platform)
        with open(os.path.join(args.out, page + ".md"), "w") as f:
            f.write(text)
        summaries[page] = s
        for k, r in s["outs"].items():
            all_outs[k].append(r)
    order = sorted(summaries, key=lambda p: summaries[p]["title"])
    L = ["# Devices", "",
         f"One page per Surface with SMF (2020–2025 models), generated by `scripts/device-docs.py` from the local blob corpus "
         f"(as of {AS_OF}). Each page lists the blob's inputs and outputs, where Linux gets or puts each value, and the "
         f"block types it uses. The current state per device is in the [README overview](../../README.md#device-overview).", "",
         "| Device | Platform | Blobs | Inputs | Outputs | Limits Linux writes | Missing inputs on a written limit |",
         "|---|---|---|---|---|---|---|"]
    names = {51: "PL1", 52: "PL2", 54: "psys PL1", 55: "psys PL2"}
    for p in order:
        s = summaries[p]
        L.append(f"| [{s['title']}]({p}.md) | {PLATFORM_NAMES[s['platform']].split(' (')[0]} | {s['blobs']} | {s['inputs']} | "
                 f"{s['outputs']} | {', '.join(names[c] for c in s['written']) or 'none'} | {', '.join(f'`{g}/{c}`' for g, c in s['blocking']) or 'none'} |")
    L += ["", *catalogue(all_outs)]
    with open(os.path.join(args.out, "README.md"), "w") as f:
        f.write("\n".join(L))
    print(f"{len(pages)} device pages written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
