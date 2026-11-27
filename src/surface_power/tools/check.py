"""surface-power-check: one command for a tester's device check (spec tester-kit.md, plan F11).

Run as root from a checkout: `sudo ./scripts/surface-power-check`. Everything is asked first, after a disclaimer; then the stages
run by themselves, each one unlocked by the one before: S1 snapshot, S2 observe under load, S3 the module read only, S4 the
module with write permission and no write, S5 a first write that only lowers a limit. Whatever it changes is undone, also when
the kit is killed. The result is one text file; with `gh` the tester can put it into the device's issue, else the script prints
the links. The kit installs nothing and sends nothing on its own.
"""
import argparse
import contextlib
import csv
import datetime as dt
import glob
import io
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import traceback

from .. import supervisor
from ..adapters import inputs as inp
from ..loader.model import ChannelRef
from . import kernelcheck, report, sensorprobe
from .kit import (KIT_VERSION, PLATFORM, chain, consent, evidence_ec, load, module, result, sh, signing, system, term, undo,
                  watch, write)

REPO_SLUG = "larsschellhas/linux-surface-power"
WORK = "/run/surface-power-check"
DRY_RUN_UNIT = "surface-power-check"
INHIBIT_UNIT = "surface-power-check-inhibit"
DRY_RUN_S = 120                    # without the load test
CHECKOUT_DAYS = 14                 # [D]


def issue_title(product: str) -> str:
    """One issue per device model (decision 25.11.2026)."""
    return f"Device report: {product}"


def issue_links(product: str) -> tuple[str, str]:
    """(search link, new-issue link with the template and the title); the body is pasted by the tester (a URL cannot carry the report)."""
    from urllib.parse import quote
    title = issue_title(product)
    query = 'is:issue in:title "' + title + '"'          # built outside the f-string: Python 3.11 has no backslash there (PEP 701)
    search = f"https://github.com/{REPO_SLUG}/issues?q={quote(query)}"
    new = f"https://github.com/{REPO_SLUG}/issues/new?template=device-report.yml&title={quote(title)}"
    return search, new


def sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + "."


class Check:
    def __init__(self, root: str = "/", assume_yes: bool = False, out=None, ask_line=None, release: str | None = None):
        self.root = root
        self.out = out or sys.stdout
        self.term = term.Term(self.out)
        self.release = release or os.uname().release
        self.device = supervisor.detect(root)
        self.profile = supervisor.find_profile(self.device)
        self.platform = PLATFORM.get(self.profile.file) if self.profile else None
        self.kernel = kernelcheck.advise(self.release, root)
        self.repo = system.repo_state()
        self.consent = consent.Consent(ask_line or self._input, self.term, assume_yes)
        self.undo = undo.Undo(root)
        self.markers = chain.Markers(root, {"commit": self.repo.get("commit"), "kernel": self.release})
        self.evidence = chain.Evidence()
        self.watch = watch.Watch(root)
        self.result = result.Result(f"{issue_title(self.device.product)}: check of {dt.datetime.now():%Y-%m-%d %H:%M}")
        self.preflight_lines: list[str] = []
        self.writers_quiet = False
        self.s2_plateau = None
        self.skin_refs: list = []
        self.probe_sensors: list[int] = []
        self.probe_fan = False
        self.transport = inp.CdevTransport(os.path.join(root, "dev/surface/aggregator"))

    # --- talking ---
    def say(self, text: str = "") -> None:
        """One message, wrapped to the terminal; leading spaces are kept as its indent."""
        if not text.strip():
            self.term.line()
            return
        self.term.text(text.strip(), 2 + len(text) - len(text.lstrip(" ")))

    def live(self, phase: str, sample: dict, done: float, total: float) -> None:
        self.term.progress(phase, done, total, term.readings(sample))

    def show_preflight(self) -> None:
        self.term.heading("This machine")
        rows, notes = [], []
        for line in self.preflight_lines:
            label, _, value = line.partition(": ")
            if " running" in label:                  # a program that writes power limits: a note, not a property
                notes.append(line)
                continue
            if label == "device":                    # that it is a Surface with SMF is why the kit runs at all
                value = value.replace(", Microsoft True, SMF True", "")
            if label == "Secure Boot":               # three properties, three rows
                boot, _, rest = value.partition(", lockdown: ")
                lockdown, _, enforced = rest.partition(", module signatures enforced: ")
                rows += [("Secure Boot", boot), ("Lockdown", lockdown),
                         ("Signatures enforced", {"Y": "yes", "N": "no"}.get(enforced, enforced))]
                continue
            rows.append((label[:1].upper() + label[1:], value))
        self.term.rows(rows)
        for note in notes:
            self.term.line()
            self.term.bullet(note)

    def show_summary(self) -> None:
        self.term.heading("Summary")
        for sid, title in result.STAGES:
            s = self.result.stages.get(sid) or {"status": "skipped", "summary": "not reached."}
            self.term.stage(sid, title, s["status"], s["summary"], s.get("unlock"))
        self.term.line()
        self.term.text(self.result.next_steps().replace("**", ""))

    def _input(self, prompt: str) -> str:
        self.term.end_progress()
        try:
            return input(prompt)
        except EOFError:
            return ""

    def yes(self, question: str) -> bool:
        if self.consent.assume_yes:
            return True
        self.term.line()
        self.term.text(question, 2, "bold")
        return self.consent.ask_line("  > [y/N] ").strip().lower() in ("y", "yes", "j", "ja")

    # --- before anything runs ---
    def preflight(self) -> list[str]:
        d, p = self.device, self.profile
        state = system.platform_state(self.root, self.release)
        lines = [f"repository: {system.repo_text(self.repo)}, kit {KIT_VERSION}",
                 f"device: {d.product} (sku {d.sku}), Microsoft {d.microsoft}, SMF {d.smf}",
                 f"profile: {p.file + (' (tested)' if p.tested else ' (untested)') if p else 'none'}",
                 f"kernel: {self.release}, CPU {report.cpu_vendor(self.root) or 'unknown'}",
                 f"kernel check: {self.kernel.summary}",
                 f"Secure Boot: {({True: 'on', False: 'off', None: 'unknown'})[state['secure_boot']]}, "
                 f"lockdown: {state['lockdown'] or 'not available'}, module signatures enforced: {state['sig_enforce'] or 'unknown'}",
                 f"module signing: {signing.decide(self.root).describe()}"]
        writers = supervisor.limit_writers(self.root)
        if writers:
            lines.append(supervisor.describe_writers(writers))
        return lines

    def kernel_gate(self) -> bool:
        """Shown before anything runs. For an outdated kernel the tester is asked whether to go on (default: no)."""
        if not self.kernel.details:
            return True
        self.term.heading("Your kernel")
        self.say(("PLEASE UPDATE FIRST: " if self.kernel.ask else "NOTE: ") + self.kernel.summary)
        for line in self.kernel.details:
            self.term.bullet(line)
        self.say()
        if not self.kernel.ask:
            return True
        if self.yes("Run the check with this kernel anyway?"):
            self.kernel.summary += "; the tester chose to go on"
            return True
        return False

    def checkout_gate(self, now: float | None = None) -> bool:
        """The kit fetches nothing, so a block in the repository only reaches a tester who pulls."""
        stamp = self.repo.get("time")
        days = None if stamp is None else int(((now or time.time()) - stamp) // 86400)
        if days is None or days <= CHECKOUT_DAYS:
            return True
        self.say()
        self.say(f"This checkout is {days} days old. Please run `git pull` first: newer versions can block a step where a problem was "
                 "found.")
        return self.yes("Run this old version anyway?")

    def module_reason(self) -> tuple[str | None, str | None]:
        """(why the module stages cannot run here, what would unlock them); (None, None) if they can."""
        state = system.platform_state(self.root, self.release)
        if report.cpu_vendor(self.root) != "GenuineIntel":
            return "not an Intel CPU", None
        if self.profile is None or self.platform is None:
            return "no profile with a platform type for this device", None
        if state["proc_thermal"]:
            return "the processor thermal device 00:04.0 is present: the kernel's own MMIO zone exists, the module would decline", None
        sig = signing.decide(self.root)
        if sig.case == "E":
            return sig.describe(), None
        if not sig.usable:
            return sig.describe(), "a signing key that your system trusts (the setup help is not built yet)"
        if module.loaded(self.root):
            return "the module surface_rapl_mmio is already loaded", "unload it first (and stop what uses it)"
        missing = module.prerequisites(self.root, self.release, state["compiler"])
        if missing:
            return "the module cannot be built", "; ".join(missing)
        return None, None

    def stage_lock(self, stage: str) -> str | None:
        locks = self.markers.locks()
        if stage in locks:
            return locks[stage]
        blocked = self.evidence.blocked(stage, self.device.sku, self.platform)
        return None if blocked is None else f"blocked in the repository: {blocked}"

    def known(self, stage: str) -> str:
        lock = self.stage_lock(stage)
        if lock:
            return f"{stage}: will not run: {lock}"
        return self.evidence.known(stage, self.device.sku, self.device.product, self.platform)

    def load_reason(self) -> str | None:
        if inp._ac_online(self.root) is not True:
            return "the charger is not connected (or its state is not readable)"
        if "cpu_c" not in load.Sampler(self.root).sample():
            return "the CPU temperature is not readable, so a load could not be watched"
        return None

    def ask_everything(self, no_load: bool = False, no_module: bool = False) -> dict[str, bool] | None:
        """The disclaimer and one question per risk class, all before the first test. None: nothing runs."""
        if not self.consent.disclaimer():
            return None
        tested = "tested" if self.profile and self.profile.tested else "not marked as tested"
        if not self.consent.ask("read", [f"Your device: {self.device.product}. Its profile is {tested} (that is about letting the daemon "
                                         "control it, not about this check)."]):
            return None
        agreed = {"read": True, "load": False, "module": False, "write": False}
        why = "switched off with --no-load" if no_load else self.load_reason()
        if why:
            self.result.stage("S2", "skipped", why + ".")
            self.term.heading("Load test: not possible now")
            self.say(f"{why}.")
        else:
            running = [u for u in undo.SERVICES if system.unit_states().get(u, {}).get("active") == "active"]
            notes = [self.known("S2"), "Close other programs first.",
                     "During the test, power, temperatures, fan speed and processor speed are recorded every second."]
            if running:
                notes.append("For the measurement these services are paused and started again afterwards: " + ", ".join(running) + ".")
            agreed["load"] = self.consent.ask("load", notes)
            if not agreed["load"]:
                self.result.stage("S2", "skipped", "the tester said no.")
        why, unlock = self.module_reason()
        if no_module:
            why, unlock = "switched off with --no-module", None
        if why:
            for stage in chain.MODULE_STAGES:
                self.result.stage(stage, "skipped", why + ".", unlock)
            self.term.heading("Kernel module tests: not possible here")
            self.say(f"Reason: {why}.")
            if unlock:
                self.say(f"To unlock: {unlock}.")
            return agreed
        if self.stage_lock("S3"):
            for stage in chain.MODULE_STAGES:
                self.result.stage(stage, "skipped", self.stage_lock(stage) + ".")
            self.term.heading("Kernel module tests: locked")
            self.say(sentence(self.stage_lock("S3")))
            return agreed
        agreed["module"] = self.consent.ask("module", [self.known("S3"), self.known("S4"),
                                                       "S3 loads the module read only; S4 loads it with write permission and writes "
                                                       "nothing. Each runs only if the one before passed.",
                                                       "Services that manage power are paused meanwhile and started again."])
        if not agreed["module"]:
            for stage in chain.MODULE_STAGES:
                self.result.stage(stage, "skipped", "the tester said no.")
        elif self.stage_lock("S5"):
            self.result.stage("S5", "skipped", self.stage_lock("S5") + ".")
            self.term.heading("First write test: locked")
            self.say(sentence(self.stage_lock("S5")))
        elif not agreed["load"]:
            self.result.stage("S5", "skipped", "it needs the load test (S2).")
        else:
            agreed["write"] = self.consent.ask("write", [self.known("S5"), "It runs only if S2, S3 and S4 passed just before. It "
                                                         "writes a value below your current limits and checks that the old one is back."])
            if not agreed["write"]:
                self.result.stage("S5", "skipped", "the tester said no.")
        return agreed

    def show_stage(self, sid: str) -> None:
        s = self.result.stages[sid]
        self.term.line()
        self.term.stage(sid, dict(result.STAGES)[sid], s["status"], s["summary"], s.get("unlock"))

    # --- S1 ---
    def cdev(self) -> None:
        if os.path.isdir(os.path.join(self.root, "sys/module/surface_aggregator_cdev")):
            return
        unload = self.undo.push(["modprobe", "-r", "surface_aggregator_cdev"])
        code, text = sh.run(["modprobe", "surface_aggregator_cdev"])
        if code == 0:
            self.say("Loaded surface_aggregator_cdev for this run (unloaded at the end).")
        else:
            self.undo.drop(unload)
            self.say(f"surface_aggregator_cdev could not be loaded ({text}); the EC parts stay unchecked.")

    def snapshot(self) -> None:
        self.term.heading("S1  Collecting data (reads only, up to a minute)")
        data = self.result.data
        data["run"] = {"kit": KIT_VERSION, "repo": self.repo, "time": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        data["machine"] = system.machine(self.root)
        data["system"] = {**system.platform_state(self.root, self.release), "kernel_check": self.kernel.summary,
                          "signing": signing.decide(self.root).describe()}
        data["software"] = system.software(self.root)
        self.cdev()
        buf = io.StringIO()
        asked = evidence_ec.Recorder(self.transport)       # every question to the controller, with its raw answer
        with contextlib.redirect_stdout(buf):
            try:
                report.main(["--root", self.root, "--with-ec"], fan_transport=asked, ec_transport=asked)
            except Exception as e:  # a crash of the report is a result too
                print(f"report failed: {e!r}")
        self.result.report_text = buf.getvalue()
        data["limits"] = system.limits(self.root, self.platform)
        data["blob"], parsed = system.blob(self.root)
        data["sam_devices"] = system.sam_devices(self.root)
        data["kernel_nodes"] = evidence_ec.kernel_nodes(self.root, data["sam_devices"])
        data["ec_requests"] = asked.records
        data["sensors"] = system.sensors(self.root)
        data["ec_attributes"] = system.ec_attributes(self.root)
        data["kernel_messages"] = system.kernel_messages()
        view, idle = report.daemon_view(self.root, None)
        wanted: list[int] = []
        if view is None:
            data["daemon"] = {"idle": idle}
        else:
            self.skin_refs = list(view.skin_refs)
            data["daemon"] = {"control_reasons": view.control_reasons, "variant": view.graph.variant}
            wanted = sensorprobe.wanted_sensors(view.graph.inputs)
        summary = "collected."
        probed, mask, found = evidence_ec.sensor_probe(asked.records)
        if probed:                                  # the report probes where the kernel has no sensor node (DV-10)
            data["sensor_probe"] = {"mask": None if mask is None else f"{mask:#06x}", "sensors": found, "wanted": wanted}
            self.probe_sensors = [s["sensor"] for s in found if s["celsius"] is not None]
            lines, phrase = evidence_ec.console_lines(asked.records, wanted)
            self.term.line()
            self.say(lines[0])
            for line in lines[1:]:
                self.term.text(line, 4)
            summary = f"collected. {phrase}"
        elif wanted:
            self.say("Temperature sensors: your kernel provides them, so the controller was not asked for them.")
        answered, rpm = evidence_ec.fan_rpm(asked.records)
        # the fan command answered and the kernel has no fan reading: sample it under load, to show that the value follows
        self.probe_fan = answered and rpm is not None and "surface_fan" not in data["kernel_nodes"]["hwmon"]
        self.result.stage("S1", "passed", summary)
        self.show_stage("S1")

    # --- the daemon's dry-run in its sandbox ---
    def start_dry_run(self, max_s: int) -> list[str] | None:
        shutil.rmtree(WORK, ignore_errors=True)
        self.undo.push(undo.remove_tree(WORK))      # also the watchdog's job, if the kit dies
        shutil.copytree(os.path.join(system.REPO, "src"), os.path.join(WORK, "src"))     # ProtectHome hides a checkout under /home
        props = ["ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes", "PrivateNetwork=yes", "NoNewPrivileges=yes",
                 "RestrictAddressFamilies=AF_UNIX", "CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_SYS_RAWIO", "MemoryMax=100M",
                 f"ReadWritePaths={WORK}", f"RuntimeMaxSec={max_s}", f"Environment=PYTHONPATH={WORK}/src"]
        cmd = ["systemd-run", f"--unit={DRY_RUN_UNIT}", "--collect", "--quiet"] + [f"--property={p}" for p in props] + \
            [sys.executable, "-m", "surface_power.daemon", "--dry-run", "--run-dir", f"{WORK}/run", "--state-dir", f"{WORK}/state"]
        self.dry_run_since = int(time.time())
        stop = self.undo.push(undo.stop_unit(f"{DRY_RUN_UNIT}.service"))
        code, text = sh.run(cmd)
        if code != 0:
            self.undo.drop(stop)
            self.result.data["dry_run"] = {"error": f"could not start the transient unit: {text}"}
            return None
        return stop

    def stop_dry_run(self, stop: list[str]) -> None:
        _, usage = sh.run(["systemctl", "show", f"{DRY_RUN_UNIT}.service", "-p", "CPUUsageNSec", "-p", "MemoryPeak"])
        self.undo.run_one(stop)                    # a clean stop (SIGTERM); RuntimeMaxSec is only the safety net
        _, journal = sh.run(["journalctl", "-u", DRY_RUN_UNIT, "--since", f"@{self.dry_run_since}", "--no-pager", "-o", "cat"])
        out: dict = {"seconds": int(time.time()) - self.dry_run_since, "journal": journal.splitlines()[-60:],
                     "usage": dict(ln.split("=", 1) for ln in usage.splitlines() if "=" in ln)}
        try:
            with open(os.path.join(WORK, "run", "status.json")) as f:
                status = json.load(f)
            out["status"] = {k: status.get(k) for k in ("state", "pl_path", "slider", "controllers", "outputs", "inputs",
                                                        "inputs_absent", "competing_writers", "outputs_without_linux_path")}
        except (OSError, ValueError) as e:
            out["error"] = f"status.json not readable: {e}"
        rows = []
        for path in sorted(glob.glob(os.path.join(WORK, "state", "series-*.csv"))):
            with open(path, newline="") as f:
                rows += list(csv.DictReader(f))
        if rows:                             # all of it: the result is thinned only if it gets too long for an issue
            out["series"] = result.table(rows)
        self.result.data["dry_run"] = out
        shutil.rmtree(WORK, ignore_errors=True)

    def short_dry_run(self) -> None:
        self.watch.stage = "dry-run"
        self.term.heading("Dry-run of the daemon (writes nothing)")
        stop = self.start_dry_run(DRY_RUN_S + 60)
        if stop:
            for second in range(DRY_RUN_S):
                time.sleep(1)
                self.term.progress("dry-run", second + 1, DRY_RUN_S)
            self.term.end_progress()
            self.stop_dry_run(stop)

    # --- pausing ---
    def pause(self) -> None:
        self.watch.stage = "pause"             # stopping a service can change limits: they are recorded under this name
        paused, failed = self.undo.pause_services(system.unit_states())
        if paused:
            self.say()
            self.say("Paused: " + ", ".join(paused) + " (started again at the end).")
            time.sleep(5)
        self.watch.sample()
        still = supervisor.limit_writers(self.root)
        self.writers_quiet = not failed and not still
        self.result.data["paused"] = {"services": paused, "could_not_stop": failed, "still_running": still}
        if not self.writers_quiet:
            self.say("Still running and able to write limits: " + ", ".join(failed + still))
        stop = self.undo.push(undo.stop_unit(f"{INHIBIT_UNIT}.service"))
        code, _ = sh.run(["systemd-run", f"--unit={INHIBIT_UNIT}", "--collect", "--quiet", "systemd-inhibit",
                          "--what=sleep:handle-lid-switch", "--who=surface-power-check", "--why=a hardware test is running",
                          "sleep", str(undo.MAX_RUN_S)])
        if code != 0:
            self.undo.drop(stop)
            self.result.notes.append("Sleep could not be blocked during the tests.")

    # --- S2 ---
    def observe_under_load(self) -> None:
        self.watch.stage = "S2"
        total = sum(p[1] for p in load.PHASES)
        self.term.heading(f"S2  Observe under load ({total // 60} to {total // 60 + 1} minutes)")
        self.say("Idle, load and cool-down. Please leave the machine alone.")
        stop = self.start_dry_run(total + 120)
        sampler = load.Sampler(self.root, self.transport, self.probe_sensors, probe_fan=self.probe_fan)
        try:
            got = load.run_phases(load.PHASES, sampler, load.Load(), self.skin_refs,
                                  on_phase=lambda name, s: self.term.end_progress(), on_sample=self.live)
        finally:
            self.term.end_progress()
            if stop:
                self.stop_dry_run(stop)
        top = load.plateau(got["samples"])
        mmio = (self.result.data.get("limits", {}).get("mmio") or {}).get("limit")
        reg = module.Register.read(self.root, self.platform)[0] if self.platform else None
        switched_on = load.enabled_limits(self.root, reg.raw if reg else (int(mmio, 16) if mmio else None))
        verdict = load.in_force(top, switched_on)
        self.result.data["load"] = {"method": load.LOAD_NAME, "phases": [list(p) for p in load.PHASES], "aborted": got["aborted"],
                                    "writers_quiet": self.writers_quiet, "plateau": top, "limits_switched_on": switched_on,
                                    "in_force": verdict, "samples": result.table(got["samples"])}
        beside = "" if self.writers_quiet else " Measured next to programs that write limits."
        if got["aborted"]:
            self.result.stage("S2", "not-passed", f"the load was ended early: {got['aborted']}.{beside}")
        else:
            self.s2_plateau = top
            self.result.stage("S2", "passed", verdict + "." + beside)
        self.show_stage("S2")

    # --- S3, S4, S5 ---
    def _stage(self, sid: str, test) -> None:
        """Mark, run, record. A failure also stays on the machine."""
        self.watch.stage = sid
        self.markers.begin(sid)
        try:
            out = test()
        except Exception as e:                      # a bug of the kit must not skip the undo or hide itself
            out = {"status": "not-passed", "summary": f"the kit failed: {e!r}", "details": {}}
        self.result.stage(sid, out["status"], out["summary"], details=out.get("details"))
        self.show_stage(sid)
        if out["status"] == "failed":
            self.markers.failed(sid, out["summary"])

    def _skip(self, sid: str, why: str) -> None:
        if sid not in self.result.stages:
            self.result.stage(sid, "skipped", why)
            self.show_stage(sid)

    def module_stages(self, want_write: bool) -> None:
        self.term.heading("S3 to S5  Kernel module")
        if "thermald" in supervisor.limit_writers(self.root):
            for sid in chain.MODULE_STAGES:
                self._skip(sid, "thermald is running; with an MMIO zone it can switch limits off (spec kernel-mmio-rapl 7).")
            return
        if not self.undo.watchdog_running:
            for sid in chain.MODULE_STAGES:
                self._skip(sid, "the watchdog could not be started, so a killed run would not be cleaned up.")
            return
        state = system.platform_state(self.root, self.release)
        sig = signing.decide(self.root)
        tmp = tempfile.mkdtemp(prefix="surface-power-check-")
        remove = self.undo.push(undo.remove_tree(tmp))
        try:
            self.say("Building the module ...")
            ko, text = module.build(tmp, self.release, state["compiler"], sig)
            self.result.data["module"] = {"signing": sig.describe(), "build": text}
            if ko is None:
                for sid in chain.MODULE_STAGES:
                    self._skip(sid, text.splitlines()[0] + ".")
                return
            args = (self.root, self.undo, ko, self.platform, self.device.sku)
            self._stage("S3", lambda: module.test(*args, allow_write=False))
            if not self.result.passed("S3"):
                self._skip("S4", "S3 did not pass.")
            elif not self.writers_quiet:
                self._skip("S4", "programs that write limits are still running.")
            elif self.stage_lock("S4"):
                self._skip("S4", self.stage_lock("S4") + ".")
            else:
                self._stage("S4", lambda: module.test(*args, allow_write=True))
            if not want_write:
                self._skip("S5", "not agreed to.")
                return
            if not self.result.passed("S4"):
                self._skip("S5", "S4 did not pass.")
            elif not self.result.passed("S2") or not self.s2_plateau or not self.s2_plateau["steady"]:
                self._skip("S5", "the load test did not reach a steady plateau, so an effect could not be judged.")
            elif inp._ac_online(self.root) is not True:
                self._skip("S5", "the charger is not connected.")
            elif self.stage_lock("S5"):
                self._skip("S5", self.stage_lock("S5") + ".")
            else:
                sampler = load.Sampler(self.root, self.transport, self.probe_sensors, probe_fan=self.probe_fan)

                def first_write():
                    out = write.test(*args, self.s2_plateau, sampler, load.Load, self.skin_refs, say=self.say, on_sample=self.live)
                    self.term.end_progress()
                    samples = out.get("details", {}).pop("samples", [])
                    if samples:
                        self.result.data["write"] = {"samples": result.table(samples)}
                    return out
                self._stage("S5", first_write)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            self.undo.drop(remove)

    # --- the whole run ---
    def execute(self, agreed: dict[str, bool]) -> None:
        self.watch.start()
        self.snapshot()
        changes = agreed["load"] or agreed["module"]
        if changes:
            ok, text = self.undo.start_watchdog(os.getpid())
            if not ok:
                self.result.notes.append(f"The watchdog could not be started ({text}): no service was paused and no module loaded.")
            else:
                self.pause()
        if agreed["load"]:
            self.observe_under_load()
        else:
            self.short_dry_run()
        if agreed["module"]:
            self.module_stages(agreed["write"])

    def close(self) -> None:
        """Undo, stop watching, and forget the markers of a run that left nothing behind. Safe to call twice."""
        self.watch.stage = "restore"
        paused = bool((self.result.data.get("paused") or {}).get("services"))
        undone = self.undo.restore()
        if paused and self.watch.running:
            time.sleep(3)                      # a restarted service writes its limits in its first seconds
            self.watch.sample()
        if not module.loaded(self.root):
            self.markers.run_ended_cleanly()
        self.undo.stop_watchdog()
        if self.watch.running:
            self.result.data["watch"] = self.watch.stop()
        if not undone:
            note = "NOT everything could be undone: " + "; ".join(self.undo.failures)
            if note not in self.result.notes:
                self.result.notes.append(note)
                self.say(note)
        shutil.rmtree(WORK, ignore_errors=True)

    def tester_note(self) -> None:
        if self.consent.assume_yes:
            return
        self.term.heading("One last question")
        self.say("Anything unusual, for example noise, heat, or own tools that change power limits?")
        text = self.consent.ask_line("  > Describe it, or press Enter to skip: ").strip()
        if text:
            self.result.data["tester_note"] = text[:1000]

    def save(self, directory: str | None = None) -> str:
        words = result.private_words(self.root, os.uname().nodename, os.environ.get("SUDO_USER"))
        self.result.header = self.preflight_lines
        short, full = self.result.finish(words)
        model = re.sub(r"[^A-Za-z0-9]+", "-", self.device.product or "device").strip("-").lower()
        base = os.path.join(directory or os.getcwd(), f"surface-power-check-{model}-{dt.datetime.now():%Y%m%d-%H%M}")
        files = [(base + ".md", short)] + ([(base + "-full.md", full)] if full != short else [])
        uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
        for path, text in files:
            with open(path, "w") as f:
                f.write(text)
            if uid and gid:
                os.chown(path, int(uid), int(gid))
        if len(files) > 1:
            self.say(f"The full data are in {files[1][0]} (the issue file is thinned to fit; we ask for the full one only if needed).")
        return files[0][0]

    def issue(self, path: str) -> None:
        product = self.device.product or "unknown"
        title = issue_title(product)
        search, new = issue_links(product)
        user = os.environ.get("SUDO_USER")
        gh = shutil.which("gh")
        if gh and user:
            code, _ = sh.run(["sudo", "-u", user, gh, "auth", "status"])
            if code == 0:
                self.say(f"Put the result into the issue \"{title}\" of {REPO_SLUG} with gh (as {user})?")
            if code == 0 and self.consent.ask_line("  > Put it into the issue? [y/N] ").strip().lower() in ("y", "yes"):
                code, found = sh.run(["sudo", "-u", user, gh, "issue", "list", "-R", REPO_SLUG, "--state", "all", "--search",
                                      f"\"{title}\" in:title", "--json", "number,title"])
                number = None
                if code == 0:
                    try:
                        number = next((i["number"] for i in json.loads(found) if i["title"] == title), None)
                    except ValueError:
                        number = None
                if number:
                    code, text = sh.run(["sudo", "-u", user, gh, "issue", "comment", str(number), "-R", REPO_SLUG, "--body-file", path])
                else:
                    code, text = sh.run(["sudo", "-u", user, gh, "issue", "create", "-R", REPO_SLUG, "--title", title, "--body-file", path])
                self.say(text if code == 0 else f"gh failed: {text}")
                if code == 0:
                    return
        self.say(f"Please look whether the issue \"{title}\" exists: {search}")
        self.say(f"If it does, paste the file as a comment there; else open it here and paste the file: {new}")


def run_all(c: Check, agreed: dict[str, bool]) -> bool:
    """Run the agreed stages and undo everything on every way out. True if the tester or a signal interrupted."""
    interrupted = False
    try:
        undo.interruptible()                      # a closed terminal or `kill` undoes like Ctrl+C
        c.execute(agreed)
    except KeyboardInterrupt:
        interrupted = True
        c.result.interrupted = True
        c.result.notes.append("The run was interrupted by the tester or a signal.")
        running = c.watch.stage                    # the stage the interrupt fell into, if it has no outcome yet
        if running in dict(result.STAGES) and running not in c.result.stages:
            c.result.stage(running, "not-passed", "interrupted before it ended.")
    except Exception:                             # a bug of the kit: undo, and keep what was collected
        c.say("The check itself failed; undoing and saving what was collected.")
        c.result.notes.append("The kit failed:\n```\n" + traceback.format_exc()[-1500:] + "```")
    finally:
        # a second Ctrl+C, a closed terminal or `kill` must not cut the undo in half
        before = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            c.close()
        finally:
            for sig, handler in before.items():
                signal.signal(sig, handler)
    return interrupted


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="surface-power-check", description="One command for a tester's device check.")
    ap.add_argument("--yes", action="store_true",
                    help="answer the read and load questions with yes (never the disclaimer, the module tests or the writing)")
    ap.add_argument("--no-load", action="store_true", help="skip the load test")
    ap.add_argument("--no-module", action="store_true", help="skip the kernel module tests")
    ap.add_argument("--no-issue", action="store_true", help="do not offer to put the result into an issue")
    args = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("run as root: sudo ./scripts/surface-power-check", file=sys.stderr)
        return 1
    c = Check(assume_yes=args.yes)
    if not c.device.supported:
        print(f"not a Surface SMF device (vendor {c.device.vendor!r}, SMF {c.device.smf}): nothing to check")
        return 0
    try:
        c.undo.lock()
    except undo.Busy as e:
        c.say(f"{e}: wait for it to end.")
        return 1
    if c.undo.leftover():
        c.say("An earlier run did not clean up. Undoing that now ...")
        ok, text = c.undo.run_leftover()
        if not ok:
            c.say(f"That did not work completely: {text}\nPlease reboot, then start the check again.")
            return 1
    c.term.line(c.term.paint(f"surface-power-check {KIT_VERSION}", "bold"))
    try:                                          # until the answers are in, nothing has been changed
        c.preflight_lines = c.preflight()
        c.show_preflight()
        if not c.kernel_gate() or not c.checkout_gate():
            c.say("Nothing was run. Update, reboot if the kernel changed, and start the check again.")
            return 0
        c.preflight_lines = [f"kernel check: {c.kernel.summary}" if ln.startswith("kernel check: ") else ln
                             for ln in c.preflight_lines]
        agreed = c.ask_everything(args.no_load, args.no_module)
    except KeyboardInterrupt:
        c.say()
        c.say("Stopped. Nothing was run.")
        return 130
    if agreed is None:
        c.say("Nothing was run.")
        return 0
    c.term.heading("Thank you. The check runs by itself now.")
    interrupted = run_all(c, agreed)
    if interrupted:
        c.term.heading("Interrupted")
        c.say("What the check had changed is undone.")
    else:
        c.tester_note()
    c.show_summary()
    path = c.save()
    c.term.heading("Result file")
    c.term.line("  " + c.term.paint(path, "bold"))
    c.say("Please read it before you share it.")
    if not args.no_issue and not interrupted:
        c.issue(path)
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
