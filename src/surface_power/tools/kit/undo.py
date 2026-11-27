"""Everything the kit changes is undone, also when the kit dies (spec tester-kit.md 7).

Every change is entered here with its undo command before it is made. The pending undo commands are also written to a shell
script; a watchdog in its own systemd unit runs that script when the kit's process is gone or has run too long. So Ctrl+C, a
closed terminal and SIGKILL end the same way. A freeze or power loss cannot be undone this way: a reboot ends the module and the
pause of the services (`mask --runtime` does not survive it).
"""
import fcntl
import os
import shlex
import signal

from . import sh

SCRIPT = "run/surface-power-check-undo.sh"
WATCH = "run/surface-power-check-watchdog.sh"
LOCK = "run/surface-power-check.lock"
WATCHDOG_UNIT = "surface-power-check-watchdog"
MAX_RUN_S = 40 * 60                    # longer than every stage together [D]
# Stopped for measurements and module tests, in this order; started again in the reverse order (spec 6.4, decision E2).
SERVICES = ("surface-powerd", "surface-powerd-dryrun", "thermald", "tuned", "tuned-ppd", "tlp", "power-profiles-daemon")
BUS_ACTIVATED = {"tuned", "tuned-ppd", "power-profiles-daemon"}      # come back through D-Bus unless masked (plan L4 [M, tester])


class Busy(Exception):
    pass


# Undo commands must succeed when there is nothing left to undo: an interrupt can fall between entering one and making the
# change, and a transient unit that ended by itself is gone. A failure would keep the script and block the next run.
def stop_unit(name: str) -> list[str]:
    return ["/bin/sh", "-c", f"systemctl stop {shlex.quote(name)} 2>/dev/null; ! systemctl is-active --quiet {shlex.quote(name)}"]


def remove_tree(path: str) -> list[str]:
    """For the kit's own working directories only: their path is made by the kit, never taken from outside."""
    if os.path.basename(path.rstrip("/")).split("-")[:3] != ["surface", "power", "check"]:
        raise ValueError(f"not a working directory of the kit: {path}")
    return ["rm", "-rf", "--", path]


def unload_module(name: str, sys_module: str = "/sys/module") -> list[str]:
    return ["/bin/sh", "-c", f"[ ! -d {shlex.quote(sys_module + '/' + name)} ] || rmmod {shlex.quote(name)}"]


class Undo:
    def __init__(self, root: str = "/"):
        self.root = root
        self.pending: list[list[str]] = []     # oldest first
        self.failures: list[str] = []
        self.watchdog_running = False
        self._lock = None

    def path(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    # --- one kit at a time, and what an earlier one left behind ---
    def lock(self) -> None:
        os.makedirs(os.path.dirname(self.path(LOCK)), exist_ok=True)
        self._lock = open(self.path(LOCK), "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise Busy("another surface-power-check is running") from e

    def leftover(self) -> str | None:
        """The undo script of a run that did not clean up, if any."""
        try:
            with open(self.path(SCRIPT)) as f:
                return f.read()
        except OSError:
            return None

    def run_leftover(self) -> tuple[bool, str]:
        code, text = sh.run(["/bin/sh", self.path(SCRIPT)])
        if code == 0:
            os.remove(self.path(SCRIPT))
        return code == 0, text

    # --- the stack ---
    def push(self, cmd: list[str]) -> list[str]:
        self.pending.append(cmd)
        self._persist()
        return cmd

    def drop(self, cmd: list[str]) -> None:
        """The change was never made."""
        if cmd in self.pending:
            self.pending.remove(cmd)
            self._persist()

    def run_one(self, cmd: list[str]) -> tuple[int, str]:
        """Run one pending undo command now."""
        code, text = sh.run(cmd)
        if code != 0:
            self.failures.append(f"{shlex.join(cmd)}: {text}")
        self.drop(cmd)
        return code, text

    def restore(self) -> bool:
        """Run everything pending, newest first. Safe to call twice. True if nothing ever failed."""
        while self.pending:
            self.run_one(self.pending[-1])
        return not self.failures

    def _persist(self) -> None:
        path = self.path(SCRIPT)
        if not self.pending:
            if os.path.exists(path):
                os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = ["#!/bin/sh", "# surface-power-check: undo what the run changed, newest first", "rc=0"]
        lines += [f"{shlex.join(c)} || rc=1" for c in reversed(self.pending)] + ["exit $rc", ""]
        with open(path + ".tmp", "w") as f:
            f.write("\n".join(lines))
        os.replace(path + ".tmp", path)

    # --- watchdog ---
    def start_watchdog(self, pid: int, max_s: int = MAX_RUN_S) -> tuple[bool, str]:
        """A transient unit that runs the undo script once the kit is gone. A script file, because systemd would expand "$"
        in a command line."""
        script, watch = self.path(SCRIPT), self.path(WATCH)
        os.makedirs(os.path.dirname(watch), exist_ok=True)
        with open(watch, "w") as f:
            f.write("#!/bin/sh\n"
                    f"end=$(($(date +%s) + {max_s}))\n"
                    f"while kill -0 {pid} 2>/dev/null && [ \"$(date +%s)\" -lt \"$end\" ]; do sleep 5; done\n"
                    f"if [ -f {shlex.quote(script)} ]; then /bin/sh {shlex.quote(script)} && rm -f {shlex.quote(script)}; fi\n"
                    f"rm -f {shlex.quote(watch)}\n")
        sh.run(["systemctl", "stop", f"{WATCHDOG_UNIT}.service"])
        sh.run(["systemctl", "reset-failed", f"{WATCHDOG_UNIT}.service"])
        code, text = sh.run(["systemd-run", f"--unit={WATCHDOG_UNIT}", "--collect", "--quiet", "/bin/sh", watch])
        self.watchdog_running = code == 0
        return self.watchdog_running, text

    def stop_watchdog(self) -> None:
        if self.watchdog_running:
            sh.run(["systemctl", "stop", f"{WATCHDOG_UNIT}.service"])
            self.watchdog_running = False
        if os.path.exists(self.path(WATCH)):
            os.remove(self.path(WATCH))

    # --- services ---
    def pause_services(self, states: dict) -> tuple[list[str], list[str]]:
        """Stop the active ones. (paused, could not be paused). Only what was running before is started again."""
        paused, failed = [], []
        for unit in SERVICES:
            state = states.get(unit)
            if not state or state.get("active") not in ("active", "activating", "reloading"):
                continue
            name = f"{unit}.service"
            start = self.push(["systemctl", "start", name])             # runs after the unmask (newest first)
            unmask = None
            if unit in BUS_ACTIVATED and not str(state.get("enabled")).startswith("masked"):
                unmask = self.push(["systemctl", "unmask", "--runtime", name])
                if sh.run(["systemctl", "mask", "--runtime", name])[0] != 0:
                    self.drop(unmask)
                    unmask = None
            if sh.run(["systemctl", "stop", name])[0] == 0:
                paused.append(unit)
            else:
                failed.append(unit)
                if unmask:
                    self.run_one(unmask)
                self.drop(start)                                        # it is still running
        return paused, failed


def interruptible() -> None:
    """A closed terminal or `kill` undo like Ctrl+C."""
    def raise_interrupt(signum, frame):
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, raise_interrupt)
