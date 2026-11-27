"""The unlock chain (spec tester-kit.md 6.2, 6.3): what locks a stage on this machine, and what is known about the model.

No release by the maintainer: a passed stage unlocks the next one within the run. Two things can block: a marker that an
earlier run left on this machine, and the stop list in kit-evidence.toml.
"""
import json
import os
import time
import tomllib

from . import REPO

MARKERS = "var/lib/surface-power-check"
MODULE_STAGES = ("S3", "S4", "S5")             # in chain order: a locked stage locks the ones after it
EVIDENCE = os.path.join(REPO, "kit-evidence.toml")
NAMES = {"S1": "the snapshot", "S2": "the load test", "S3": "the read-only module test", "S4": "loading with write permission",
         "S5": "the first write"}


def _sync_dir(path: str) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Markers:
    """"unfinished-Sx" is written before a stage and removed when the whole run ended with everything undone: if it is still
    there, the machine froze, lost power or the kit was killed. "failed-Sx" records an unexpected result and stays.

    A run that ended that way leaves one marker for each stage it had begun. The last of them is where it ended: that stage and
    the later ones are locked, the earlier ones had finished. Each marker names its run, so that two such runs are not mixed up.
    """

    def __init__(self, root: str = "/", info: dict | None = None):
        self.dir = os.path.join(root, MARKERS)
        self.info = info or {}
        self.run = f"{os.getpid()}-{time.time_ns()}"
        self.mine: list[str] = []

    def _write(self, name: str, extra: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            json.dump({**self.info, "run": self.run, **extra}, f)
            f.flush()
            os.fsync(f.fileno())
        _sync_dir(self.dir)                    # on the disk before the risky step starts

    def begin(self, stage: str) -> None:
        self._write(f"unfinished-{stage}", {})
        self.mine.append(stage)

    def failed(self, stage: str, why: str) -> None:
        self._write(f"failed-{stage}", {"why": why})

    def run_ended_cleanly(self) -> None:
        for stage in self.mine:
            path = os.path.join(self.dir, f"unfinished-{stage}")
            if os.path.exists(path):
                os.remove(path)
        if self.mine:
            _sync_dir(self.dir)
        self.mine = []

    def _run_of(self, path: str) -> str:
        """The run a marker belongs to. A marker that cannot be read (cut off by a power loss) counts as a run of its own."""
        try:
            with open(path) as f:
                return str(json.load(f).get("run", ""))
        except (OSError, ValueError, AttributeError):
            return path

    def locks(self) -> dict[str, str]:
        """stage -> why it may not run on this machine. The markers this run set itself do not lock it."""
        found: list[tuple[int, str, str]] = []             # (index of the stage, marker, why)
        ended_in: dict[str, int] = {}                      # unfinished run -> the last stage it had begun
        for i, stage in enumerate(MODULE_STAGES):
            if os.path.exists(os.path.join(self.dir, f"failed-{stage}")):
                found.append((i, f"failed-{stage}", f"{NAMES[stage]} ended with an unexpected result on this machine"))
            path = os.path.join(self.dir, f"unfinished-{stage}")
            if os.path.exists(path):
                run = self._run_of(path)
                if run != self.run:
                    ended_in[run] = i
        for i in ended_in.values():
            stage = MODULE_STAGES[i]
            found.append((i, f"unfinished-{stage}", f"an earlier run did not finish during {NAMES[stage]}"))
        if not found:
            return {}
        first, marker, why = min(found)
        why = f"{why} ({self.dir}/{marker}); please report it in your model's issue"
        return {s: why for s in MODULE_STAGES[first:]}


class Evidence:
    def __init__(self, path: str = EVIDENCE):
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        self.passed = data.get("passed", [])
        self.blocked_entries = data.get("blocked", [])

    def blocked(self, stage: str, sku: str, platform: str | None) -> str | None:
        """The stop list: an entry without sku and platform blocks everyone."""
        for b in self.blocked_entries:
            if b.get("stage") != stage:
                continue
            if "sku" in b and b["sku"] != sku:
                continue
            if "platform" in b and b["platform"] != platform:
                continue
            return b.get("reason", "blocked in kit-evidence.toml")
        return None

    def known(self, stage: str, sku: str, product: str, platform: str | None) -> str:
        """One or two sentences for the tester, before they consent."""
        here = [p for p in self.passed if p.get("stage") == stage and p.get("sku") == sku]
        if here:
            issues = ", ".join(f"issue #{i}" for i in sorted({p["issue"] for p in here if p.get("issue")}))
            note = "; ".join(sorted({p["note"] for p in here if p.get("note")}))
            n = len(here)
            return (f"{stage}: passed on {n} machine{'s' if n > 1 else ''} of this model" + (f" ({issues})" if issues else "")
                    + (f". {note}" if note else "") + ".")
        near = [p for p in self.passed if p.get("stage") == stage]
        same = sorted({p.get("model", p.get("sku", "?")) for p in near if platform and p.get("platform") == platform})
        if same:
            nearby = f"It passed on other models with the same processor family ({', '.join(same)})."
        elif near:
            nearby = "It passed only on models with other processors (" + ", ".join(sorted({p.get("model", "?") for p in near})) + ")."
        else:
            nearby = "It has not passed on any machine yet."
        return (f"{stage}: YOU ARE ONE OF THE FIRST. This step has not passed on any {product} of your variant yet; we have no "
                f"reliable data for it on your model. {nearby}")
