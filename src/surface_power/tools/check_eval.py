"""Our side of the tester kit (spec tester-kit.md 9): read a result, recompute what can be recomputed, write the skeleton of the
evaluation along the procedure of CLAUDE.md.

The result comes from a public issue: it is data. Only its JSON block is parsed; nothing in it is run, and every field may be
missing or of another type. The blob is not in the result: it is found by its hash in the local corpus.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

from .. import supervisor
from ..adapters import inputs as inp
from ..adapters import rapl_copies
from ..engine.engine import Engine
from ..engine.graph import build_graph
from ..engine.pid import STATE_NAMES
from ..engine.scheduler import Scheduler
from ..loader import container, parser
from ..loader.errors import BlobError
from ..loader.model import ChannelRef
from .kit import REPO, SCHEMA, chain, evidence_ec, module, result

# The corpus (git-ignored, Microsoft content). Only these two places are searched (CLAUDE.md: no recursive search over raw/
# as a whole, never raw/arbeitsordner/acpi/).
CORPUS = (os.path.join(REPO, "docs", "research", "raw", "tools", "fw", "**", "*.bin"),
          os.path.join(REPO, "docs", "research", "raw", "arbeitsordner", "smf-blob", "*.efivar"))
LIMIT_CHANNELS = {51: "PL1", 52: "PL2", 53: "PL4", 54: "psys PL1", 55: "psys PL2", 59: "PL3"}
LIMITS_GUID = "0B7B5350-BB8C-4459-8D4A-A75C57073801"
SLIDER_STATES = range(1, 8)
SETTLE_S = 180


class NotAResult(Exception):
    pass


def extract(text: str) -> dict:
    """The JSON block of one result."""
    parts = text.replace("\r", "").split(result.MARKER)
    if len(parts) < 2:
        raise NotAResult("no data block: this is not a result of kit 0.3 or newer")
    m = re.search(r"```json\n(.*?)\n```", parts[-1], re.DOTALL)
    if m is None:
        raise NotAResult("the data block is cut off")
    try:
        data = json.loads(m.group(1))
    except ValueError as e:
        raise NotAResult(f"the data block is not valid JSON ({e})") from e
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise NotAResult(f"unknown schema {data.get('schema') if isinstance(data, dict) else type(data).__name__}")
    return data


def get(data, *path, kind=None, default=None):
    """data[path...] if every step exists and the end has the wanted type, else the default."""
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data if kind is None or isinstance(data, kind) else default


def clean(value, limit: int = 300) -> str:
    """Text from the result for our skeleton: one line, printable, short."""
    return "".join(c if c.isprintable() else " " for c in str(value))[:limit]


def find_blob(sha256: str, patterns=CORPUS):
    """(path, parsed blob) of the corpus file with this decoded text, else (None, None)."""
    for pattern in patterns:
        for path in sorted(glob.glob(pattern, recursive=True)):
            try:
                with open(path, "rb") as f:
                    dec = container.decode(f.read(), efivar=path.endswith(".efivar"))
                if hashlib.sha256(dec.text.encode()).hexdigest() == sha256:
                    return path, parser.parse_text(dec.text, dec.form)
            except (BlobError, OSError, ValueError):
                continue
    return None, None


def reported_inputs(data: dict, graph) -> dict[ChannelRef, float]:
    """The valid inputs of the dry-run's last status, as channels of this blob."""
    by_text = {str(r): r for r in graph.inputs}
    out = {}
    for entry in get(data, "dry_run", "status", "inputs", kind=list, default=[]):
        ref = by_text.get(get(entry, "channel", kind=str))
        value = get(entry, "value", kind=(int, float))
        if ref is not None and value is not None and get(entry, "valid") is True:
            out[ref] = float(value)
    return out


def replay(graph, values: dict, state: int, seconds: int = SETTLE_S) -> tuple[dict, dict]:
    """The blob's outputs and controller states after `seconds` with constant inputs at slider state `state`."""
    engine = Engine(graph)
    sched = Scheduler(engine)
    inputs = {ref: values.get(ref) for ref in graph.inputs}
    slider = ChannelRef(inp.SYSTEM, 1)
    if slider in graph.inputs:
        inputs[slider] = float(state)
    for t in range(seconds):
        sched.step(t * 1000, inputs)
    controllers = {c.line.id: STATE_NAMES.get(c.state, c.state) for c in graph.controllers}
    return engine.rounded_outputs(), controllers


def slider_table(graph, values: dict) -> list[str]:
    refs = [r for r in sorted(graph.outputs) if r.guid == LIMITS_GUID and r.channel in LIMIT_CHANNELS]
    out = ["| Slider state | " + " | ".join(LIMIT_CHANNELS[r.channel] for r in refs) + " | Controllers not off |",
           "|---" * (len(refs) + 2) + "|"]
    for state in SLIDER_STATES:
        outputs, controllers = replay(graph, values, state)
        active = ", ".join(f"{k}: {v}" for k, v in controllers.items() if str(v).lower() != "off") or "–"
        cells = [f"{outputs[r] / 1000:g} W" if r in outputs else "–" for r in refs]
        out.append(f"| {state} | " + " | ".join(cells) + f" | {active} |")
    return out


def compare_dry_run(graph, data: dict, values: dict) -> list[str]:
    """The tester's computed outputs against ours for the same inputs. Differences can come from the controllers' history."""
    state = get(data, "dry_run", "status", "slider", "state", kind=(int, float))
    if state is None:
        return ["- slider state not in the result: not compared"]
    ours, _ = replay(graph, values, int(state))
    theirs = {}
    for o in get(data, "dry_run", "status", "outputs", kind=list, default=[]):
        w = get(o, "computed_w", kind=(int, float))
        if w is None:
            w = get(o, "value_w", kind=(int, float))
        if w is not None:
            theirs[get(o, "channel", kind=str)] = w
    out = []
    for ref in sorted(graph.outputs):
        if ref.guid != LIMITS_GUID or ref.channel not in LIMIT_CHANNELS or str(ref) not in theirs or ref not in ours:
            continue
        a, b = ours[ref] / 1000, theirs[str(ref)]
        out.append(f"- {LIMIT_CHANNELS[ref.channel]}: replay {a:g} W, tester {b:g} W" +
                   ("" if abs(a - b) <= 0.5 else "  **differs: check controller history, inputs and clamping**"))
    return out or ["- no common limit outputs to compare"]


def firmware_state(data: dict) -> list[str]:
    out = []
    try:
        unit_msr = int(get(data, "limits", "msr", "0x606", "value", kind=str, default="x"), 16)
        unit = 1.0 / (1 << (unit_msr & 0xF))
    except ValueError:
        return ["- MSR 0x606 not in the result"]
    for name, raw in (("MSR 0x610", get(data, "limits", "msr", "0x610", "value", kind=str)),
                      ("MMIO 0x59a0", get(data, "limits", "mmio", "limit", kind=str))):
        try:
            out.append(f"- {name}: {rapl_copies.Copy(int(raw, 16), unit).describe()} (`{clean(raw)}`)")
        except (TypeError, ValueError):
            out.append(f"- {name}: not in the result ({clean(get(data, 'limits', 'mmio', 'reason', default='no reason given'))})")
    for reg, name in (("0x601", "PL4"), ("0x615", "PL3"), ("0x65c", "platform PL1/PL2")):
        entry = get(data, "limits", "msr", reg, kind=dict, default={})
        out.append(f"- MSR {reg} ({name}), raw: {clean(entry.get('value') or entry.get('error') or 'not in the result')}")
    zones = get(data, "limits", "zones", kind=dict, default={})
    for zone, z in zones.items():
        cons = ", ".join(f"{clean(get(c, 'name'))} {get(c, 'power_limit_uw', kind=int, default=0) / 1e6:g} W"
                         for c in get(z, "constraints", kind=list, default=[]))
        out.append(f"- {clean(zone)} ({clean(get(z, 'name'))}, enabled {clean(get(z, 'enabled'))}): {cons}")
    return out


def check_module(data: dict) -> list[str]:
    """Recompute what the kit judged, from the raw registers it recorded."""
    out = []
    for sid in ("S3", "S4", "S5"):
        d = get(data, "stages", sid, "details", kind=dict)
        if d is None:
            continue
        before, after = get(d, "register_before", kind=str), get(d, "register_after", kind=str)
        if before and after:
            out.append(f"- {sid}: register after the unload " + ("equals the one before" if before == after else
                                                                f"**differs**: {clean(before)} -> {clean(after)}"))
        zone, unit = get(d, "zone", kind=dict), get(d, "unit", kind=str)
        if sid == "S3" and zone and before and unit:
            try:
                reg = module.Register(int(before, 16), int(unit, 16), 0)
                differences = module.compare(zone, reg)
                out.append("- S3: zone against register, recomputed: " + ("equal" if not differences else
                                                                        "**" + clean("; ".join(differences)) + "**"))
            except (KeyError, TypeError, ValueError) as e:
                out.append(f"- S3: could not be recomputed ({clean(e)})")
        for w in get(d, "writes", kind=list, default=[]):
            try:
                changed = int(w["register_before"], 16) ^ int(w["register_after"], 16)
                out.append(f"- S5: write {clean(w['file'])} = {int(w['value'])}: bits changed {changed:#018x}")
            except (KeyError, TypeError, ValueError):
                out.append("- S5: a write record is incomplete")
    return out or ["- no module stage ran"]


def _samples(data: dict, column: str) -> list[float]:
    table = get(data, "load", "samples", kind=dict, default={})
    columns, rows = get(table, "columns", kind=list, default=[]), get(table, "rows", kind=list, default=[])
    if column not in columns:
        return []
    i = columns.index(column)
    return [r[i] for r in rows if isinstance(r, list) and len(r) > i and isinstance(r[i], (int, float))]


def _plausible(celsius: float) -> bool:
    return evidence_ec.PLAUSIBLE_C[0] <= celsius <= evidence_ec.PLAUSIBLE_C[1]


def _group_note(hub: str) -> list[str]:
    others = evidence_ec.shared_with(hub)
    if not others:
        return [f"- The group serves {hub} only."]
    return [f"- **The group is shared with {', '.join(others)}.** This result proves nothing about them: readings from those devices "
            "are needed, or the group has to be split (CLAUDE.md \"Kernel changes\")."]


def kernel_changes(data: dict) -> list[str]:
    """Which changes to the kernel's registry this result gives evidence for (plan D9-D11), with the requests that prove them."""
    hub = get(data, "kernel_nodes", "hub_id", kind=str)
    records = [r for r in get(data, "ec_requests", kind=list, default=[])
               if isinstance(r, dict) and all(isinstance(r.get(k), int) for k in ("tc", "tid", "cid", "iid"))
               and (r.get("answer") is None or re.fullmatch(r"(?:[0-9a-f]{2}){0,64}", str(r.get("answer"))))]
    if get(data, "kernel_nodes") is None:
        return ["- the result has no kernel data (older kit)"]
    if hub not in evidence_ec.GROUPS:
        return [f"- the hub id {clean(hub)} is not in our copy of the registry: look it up in the current kernel first"]
    group, has_temp, has_fan, has_dptf, comment = evidence_ec.GROUPS[hub]
    sku, kernel = clean(get(data, "machine", "sku", default="?"), 80), clean(get(data, "system", "kernel", default="?"), 80)
    out = [f"- hub `{hub}` ({comment}), node group `ssam_node_group_{group}`; SKU {sku}, kernel {kernel}"]
    found = False

    asked, mask, sensors = evidence_ec.sensor_probe(records)
    if not get(data, "kernel_nodes", "temperature_node") and asked:
        found = True
        wanted = [n for n in get(data, "sensor_probe", "wanted", kind=list, default=[]) if isinstance(n, int)]
        problems = []
        if mask is None:
            problems.append("the controller did not answer the mask request")
        by_number = {s["sensor"]: s for s in sensors}
        for n in wanted:
            s = by_number.get(n)
            if s is None or s["celsius"] is None:
                problems.append(f"sensor {n}, which the blob reads, did not answer")
        for s in sensors:
            if s["celsius"] is not None and not _plausible(s["celsius"]):
                problems.append(f"sensor {s['sensor']} reads {s['celsius']} °C: not plausible")
            if not s["name"]:
                problems.append(f"sensor {s['sensor']} gave no name")
        if has_temp:
            problems.append("our copy of the registry has the node in this group already: the tester's kernel may be older")
        out += ["", "### Temperature sensor node",
                "- " + ("**supports adding the node** for this device" if not problems else
                        "**does not yet support it:** " + "; ".join(problems)),
                f"- change: add `&ssam_node_tmp_sensors,` (`ssam:{evidence_ec.TEMP_NODE}`, driver `surface_temp`) to "
                f"`ssam_node_group_{group}[]`", *_group_note(hub),
                "", "During the load test (idle, load, cool-down) the controller was asked every 5 s.", "",
                "| Sensor | Name | At the snapshot | During the load test: lowest .. highest | Blob reads it |", "|---|---|---|---|---|"]
        for s in sensors:
            under = _samples(data, f"sam{s['sensor']}_c")
            out.append(f"| {s['sensor']} | {clean(s['name'] or '-', 20)} | {'-' if s['celsius'] is None else s['celsius']} °C | "
                       + (f"{min(under)} .. {max(under)} °C" if under else "not sampled (no load test)") +
                       f" | {'yes' if s['sensor'] in wanted else 'no'} |")
        out += ["", "Requests and raw answers (the driver sends the same [C, surface_temp.c]):", "```"]
        out += [evidence_ec.request_text(r) for r in records if r["tc"] == evidence_ec.TC_TMP]
        out.append("```")

    answered, rpm = evidence_ec.fan_rpm(records)
    if not get(data, "kernel_nodes", "fan_node") and answered:
        found = True
        under = _samples(data, "fan_rpm")
        follows = bool(under) and max(under) - min(under) >= 200
        out += ["", "### Fan speed node",
                "- " + ("**the speed follows the load:** supports adding the node for the first fan of this device" if follows else
                        "**not enough:** " + ("no answer" if rpm is None else f"one reading of {rpm} rpm" if not under else
                                              f"{min(under)} .. {max(under)} rpm under load, it hardly moved")),
                f"- change: add `&ssam_node_fan_speed,` (`ssam:{evidence_ec.FAN_NODE}`, driver `surface_fan`) to "
                f"`ssam_node_group_{group}[]`; a second fan and `has_fan` of the profile node need evidence of their own",
                *_group_note(hub), "```"]
        out += [evidence_ec.request_text(r) for r in records if r["tc"] == evidence_ec.TC_FAN and r["cid"] == 0x01]
        out.append("```")

    power = [r for r in records if r["tc"] == evidence_ec.TC_BAT and r["cid"] in (0x53, 0x0F)]
    if power:
        found = True
        full = any(r["cid"] == 0x53 and r.get("answer") and len(r["answer"]) >= 42 for r in power)
        out += ["", "### DPTF battery data (`has_dptf`)"]
        if has_dptf:
            out.append("- the current registry has `has_dptf` for this group (since linux-surface 7.3.5-1): the tester's kernel "
                       "lacks it. No kernel change; ask for an update.")
        else:
            out += ["- " + ("**the EC answers the battery data request:** supports `has_dptf` for this device" if full else
                            "**no full answer to the battery data request:** no support"),
                    f"- change: the `_dptf` battery and AC nodes in `ssam_node_group_{group}[]`", *_group_note(hub)]
        out.append("```")
        out += [evidence_ec.request_text(r) for r in power]
        out.append("```")
    if not found:
        out.append("- nothing to add: the kernel has the sensor and fan nodes for this device, and no question went to the controller")
    else:
        out += ["", "Before any patch (CLAUDE.md): checked against the current `v7.3-surface-devel`, every device of the group named, "
                    "`Assisted-by` on the commit, never a `Signed-off-by` from an assistant, nothing sent without the maintainer's yes."]
    return out


def evidence_entries(data: dict, issue: int | None, platform: str | None) -> list[str]:
    sku = clean(get(data, "machine", "sku", default="?"), 80)
    model = clean(get(data, "machine", "product", default="?"), 80)
    done = {(p.get("stage"), p.get("sku"), p.get("issue")) for p in chain.Evidence().passed}
    out = []
    for sid in ("S1", "S2", "S3", "S4", "S5"):
        if get(data, "stages", sid, "status") == "passed" and (sid, sku, issue) not in done:
            out += ["[[passed]]", f"stage = {json.dumps(sid)}", f"sku = {json.dumps(sku)}", f"model = {json.dumps(model)}",
                    f"platform = {json.dumps(platform or '?')}"] + ([f"issue = {issue}"] if issue else []) + [""]
    return out


def skeleton(data: dict, issue: int | None = None, patterns=CORPUS) -> str:
    from .kit import PLATFORM
    m = lambda key: clean(get(data, "machine", key, default="?"), 120)  # noqa: E731
    sha = get(data, "blob", "sha256", kind=str, default="")
    path, parsed = find_blob(sha, patterns) if re.fullmatch(r"[0-9a-f]{64}", sha) else (None, None)
    device = supervisor.Device("Microsoft Corporation", get(data, "machine", "product", kind=str, default=""),
                               get(data, "machine", "sku", kind=str, default=""), True)
    profile = supervisor.find_profile(device)
    platform = PLATFORM.get(profile.file) if profile else None
    out = [f"# Evaluation skeleton: {m('product')}" + (f" (issue #{issue})" if issue else ""), "",
           "Made by scripts/check-eval.py. Values from the report are the tester's measurements [M, tester]. Everything under",
           "\"open\" is manual and not done by this script.", "",
           "## 1. The report (step 1)",
           f"- {m('product')}, SKU {m('sku')}, BIOS {m('bios_version')} of {m('bios_date')}, {m('cpu_model')}",
           f"- kernel {clean(get(data, 'system', 'kernel', default='?'))} ({clean(get(data, 'system', 'kernel_check', default='?'))}); "
           f"{clean(get(data, 'system', 'os', 'pretty_name', default='?'))}",
           f"- kit {clean(get(data, 'run', 'kit', default='?'))}, commit {clean(get(data, 'run', 'repo', 'commit', default='?'))}"
           f"{' with local changes' if get(data, 'run', 'repo', 'dirty') else ''}, {clean(get(data, 'run', 'time', default='?'))}",
           f"- paused: {clean(get(data, 'paused', default='nothing'))}",
           f"- tester's note: {clean(get(data, 'tester_note', default='none'), 1000)}"]
    out += ["", "### Stages"]
    for sid, title in result.STAGES:
        out.append(f"- {sid} {title}: {clean(get(data, 'stages', sid, 'status', default='?'))}: "
                   f"{clean(get(data, 'stages', sid, 'summary', default=''))}")
    out += ["", "## 3. Profile (step 3)",
            f"- matches: {profile.file if profile else 'NO PROFILE'}" + (f", variant {profile.variant}" if profile else "")]
    out += ["", "## 7. Blob (step 7)", f"- sha256 {clean(sha) or 'missing'}"]
    graph = None
    if path is None:
        out.append("- **NOT IN THE CORPUS.** Stop: nothing below that needs the blob was computed. Find the firmware package of this "
                   "version first.")
    else:
        out.append(f"- corpus file: {os.path.relpath(path, REPO)}")
        graph = build_graph(parsed, profile.variant if profile else "direct")
    out += ["", "## 10. Firmware state of the device (step 10)",
            "- The values are those at the time of the run; writers that ran before may have changed them.", *firmware_state(data)]
    changes = get(data, "watch", "events", kind=list, default=[])
    out += ["", "### Changes of limits seen during the run"]
    out += [f"- {clean(get(e, 't'))} s, {clean(get(e, 'stage'))}: {clean(get(e, 'what'))}: {clean(get(e, 'old'))} -> {clean(get(e, 'new'))}"
            for e in changes[:40]] or ["- none"]
    probe = get(data, "sensor_probe", kind=dict)
    if probe:
        out += ["", "## Sensor probe (D9-D11)", f"- mask {clean(probe.get('mask'))}, the blob reads sensors {clean(probe.get('wanted'))}"]
        out += [f"- sensor {clean(get(s, 'sensor'))}: {clean(get(s, 'name'))}, {clean(get(s, 'celsius'))} °C"
                for s in get(probe, "sensors", kind=list, default=[])[:16]]
    out += ["", "## 11. Measurement against expectation (step 11)",
            f"- load: {clean(get(data, 'load', 'in_force', default='no load test'))}",
            f"- plateau: {clean(get(data, 'load', 'plateau', default='-'))}"]
    if graph is not None:
        values = reported_inputs(data, graph)
        missing = [str(r) for r in sorted(graph.inputs) if r not in values and r.guid != inp.PLACEHOLDER]
        out += [f"- inputs from the dry-run's last status: {len(values)} valid; without a value: {', '.join(missing) or 'none'}",
                "", "### Tester's computed limits against our replay at the same inputs", *compare_dry_run(graph, data, values),
                "", f"### What the blob computes with these inputs, per slider state (after {SETTLE_S} s of constant inputs)",
                *slider_table(graph, values)]
    out += ["", "## Module stages, recomputed", *check_module(data)]
    out += ["", "## Kernel changes this result gives evidence for (D9-D11)", *kernel_changes(data)]
    entries = evidence_entries(data, issue, platform)
    out += ["", "## Proposed entries for kit-evidence.toml (check before committing)", "```toml", *entries, "```"] if entries else \
        ["", "## kit-evidence.toml: nothing new"]
    out += ["", "## Open: manual (CLAUDE.md procedure)",
            "- [ ] 0, 4: plan, STAND-UND-PLAN, knowledge, earlier comments of this tester",
            "- [ ] 2: device page docs/devices/   - [ ] 5: specs and deviations   - [ ] 6: research files for this device",
            "- [ ] 7: blob nodes and fallback values (raw CSV, REGLER-ANALYSE)",
            "- [ ] 8: the Windows driver of this device: platform type, descriptor table, register, bits, unit",
            "- [ ] 9: the daemon's write path against step 8   - [ ] 12: update docs, profile, matrix; draft the reply"]
    return "\n".join(out) + "\n"


def from_issue(number: int, repo: str) -> str:
    """The newest comment or the body of the issue that holds a result."""
    p = subprocess.run(["gh", "issue", "view", str(number), "-R", repo, "--json", "body,comments"],
                       capture_output=True, text=True, check=True, timeout=60)
    issue = json.loads(p.stdout)
    for text in [c["body"] for c in reversed(issue["comments"])] + [issue["body"]]:
        if result.MARKER in text:
            return text
    raise NotAResult(f"no result of kit 0.3 or newer in issue #{number}")


def main(argv=None) -> int:
    from .check import REPO_SLUG
    ap = argparse.ArgumentParser(prog="check-eval", description="Skeleton of an evaluation from a tester kit result.")
    ap.add_argument("file", nargs="?", help="a result file (surface-power-check-*.md)")
    ap.add_argument("--issue", type=int, help="read the newest result in this issue (with gh)")
    ap.add_argument("--out", help="write the skeleton here (default: stdout)")
    args = ap.parse_args(argv)
    if (args.file is None) == (args.issue is None):
        ap.error("give a result file or --issue N")
    try:
        if args.file:
            with open(args.file, errors="replace") as f:
                text = f.read()
        else:
            text = from_issue(args.issue, REPO_SLUG)
        out = skeleton(extract(text), args.issue)
    except (NotAResult, OSError, subprocess.SubprocessError, ValueError) as e:
        print(f"check-eval: {e}", file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
