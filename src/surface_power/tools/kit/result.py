"""The result of a run (spec tester-kit.md 9): a summary the tester understands, then one JSON block for the evaluation.

Before anything is saved, host name, user names, home paths and MAC addresses are removed.
"""
import copy
import json
import os
import re

from . import SCHEMA

MARKER = "<!-- surface-power-check json -->"
LIMIT = 65000                  # GitHub takes 65,536 characters in an issue or comment [H]
STAGES = (("S1", "Snapshot"), ("S2", "Observe under load"), ("S3", "Module, read only"),
          ("S4", "Module with write permission, no write"), ("S5", "First write (downwards only)"))
# passed: all criteria held. failed: something unexpected, the machine locks this stage. not-passed: no verdict possible or no
# effect, nothing unexpected. skipped: did not run.
STATUS = ("passed", "failed", "not-passed", "skipped")
GENERIC = {"surface", "linux", "root", "user", "localhost", "localdomain", "arch", "fedora", "ubuntu", "debian", "laptop",
           "tablet", "book", "pro", "go", "studio", "microsoft", "test", "admin", "sensor", "name", "value"}


def private_words(root: str, hostname: str, sudo_user: str | None) -> dict[str, str]:
    """word -> replacement."""
    words = {}
    if hostname:
        words[hostname] = "<host>"
        words[hostname.split(".")[0]] = "<host>"
    users = [sudo_user] if sudo_user else []
    try:
        with open(os.path.join(root, "etc/passwd")) as f:
            for line in f:
                p = line.rstrip("\n").split(":")
                if len(p) > 5 and p[2].isdigit() and 1000 <= int(p[2]) < 60000:
                    users.append(p[0])
                    full = p[4].split(",")[0].strip()
                    if full:
                        users.append(full)
    except OSError:
        pass
    for u in users:
        words[u] = "<user>"
    return {w: r for w, r in words.items() if len(w) >= 3 and w.lower() not in GENERIC}


def scrub_text(text: str, words: dict[str, str]) -> str:
    text = re.sub(r"/(home|var/home)/[^/\s'\":]+", r"/\1/<user>", text)
    text = re.sub(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])", "<mac>", text)
    for word in sorted(words, key=len, reverse=True):
        # whole words only: "-" and "_" belong to a word, so surface_thermal or linux-surface stay as they are
        text = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", words[word], text, flags=re.IGNORECASE)
    return text


def scrub(value, words: dict[str, str]):
    """Every string value; keys are ours and stay."""
    if isinstance(value, str):
        return scrub_text(value, words)
    if isinstance(value, dict):
        return {k: scrub(v, words) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, words) for v in value]
    return value


class Result:
    def __init__(self, title: str):
        self.title = title
        self.header: list[str] = []
        self.stages: dict[str, dict] = {}
        self.data: dict = {"schema": SCHEMA}
        self.report_text = ""
        self.notes: list[str] = []
        self.interrupted = False

    def stage(self, sid: str, status: str, summary: str, unlock: str | None = None, details: dict | None = None) -> str:
        assert status in STATUS
        self.stages[sid] = {"status": status, "summary": summary, **({"unlock": unlock} if unlock else {}),
                            **({"details": details} if details else {})}
        return status

    def passed(self, sid: str) -> bool:
        return self.stages.get(sid, {}).get("status") == "passed"

    def summary_lines(self) -> list[str]:
        out = []
        for sid, title in STAGES:
            s = self.stages.get(sid)
            if s is None:
                out.append(f"- **{sid} {title}:** skipped: not reached")
                continue
            out.append(f"- **{sid} {title}:** {s['status']}: {s['summary']}" + (f" To unlock: {s['unlock']}" if s.get("unlock") else ""))
        return out

    def next_steps(self) -> str:
        if any(s["status"] == "failed" for s in self.stages.values()):
            return ("**Something unexpected happened.** Please post this file in the issue and do not repeat the failed step. A reboot brings "
                    "the firmware's own power limits back (measured on a Surface Book 3).")
        if self.interrupted:
            return "The run was interrupted. You only need to post this file if something looked wrong. Thank you."
        return "Please read this file, then post it in the issue for your model. Thank you."

    def render(self, data: dict, with_report: bool = True) -> str:
        data = {**data, "stages": self.stages}
        parts = [f"# {self.title}", "```", *self.header, "```", "## Summary", *self.summary_lines(), "", self.next_steps()]
        if self.notes:
            parts += ["", *[f"- {n}" for n in self.notes]]
        if with_report and self.report_text:
            parts += ["", "<details><summary>Report</summary>", "", "```", self.report_text.rstrip(), "```", "", "</details>"]
        parts += ["", "<details><summary>Data for the evaluation (JSON)</summary>", "", MARKER, "```json",
                  json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str), "```", "", "</details>", ""]
        return "\n".join(parts)

    def finish(self, words: dict[str, str]) -> tuple[str, str]:
        """(text for the issue, within its size limit; full text). Both scrubbed."""
        data = scrub(self.data, words)
        self.stages = scrub(self.stages, words)
        self.header = scrub(self.header, words)
        self.notes = scrub(self.notes, words)
        self.report_text = scrub_text(self.report_text, words)
        full = self.render(data)
        short, thinned = full, copy.deepcopy(data)
        with_report = True
        while len(short) > LIMIT:
            if not _thin(thinned):
                if not with_report:
                    break
                with_report = False
            thinned["thinned"] = True
            short = self.render(thinned, with_report)
        return short, full


def _thin(data: dict) -> bool:
    """Drop every second row of the longest series; shorten long lists. False when nothing is left to thin."""
    tables = []
    load = data.get("load") or {}
    for holder, key in ((load, "samples"), (data.get("dry_run") or {}, "series"), (data.get("write") or {}, "samples")):
        table = holder.get(key)
        if isinstance(table, dict) and len(table.get("rows", [])) > 12:
            tables.append(table)
    if tables:
        t = max(tables, key=lambda t: len(json.dumps(t["rows"])))
        t["rows"] = t["rows"][::2]
        return True
    watch = data.get("watch") or {}
    if len(watch.get("events", [])) > 30:
        watch["dropped"] = watch.get("dropped", 0) + len(watch["events"]) - 30
        watch["events"] = watch["events"][:30]
        return True
    return False


def table(rows: list[dict]) -> dict:
    """A list of samples as columns plus rows: much shorter in JSON."""
    columns: list[str] = []
    for r in rows:
        columns += [k for k in r if k not in columns]
    return {"columns": columns, "rows": [[r.get(c) for c in columns] for r in rows]}
