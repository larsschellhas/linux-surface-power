"""surface-power-check: one command for a tester's device check (spec tester-kit.md, plan F11).

Run as root from a checkout: `sudo ./scripts/surface-power-check`. It runs the report with EC data, a short dry-run of the daemon in a transient unit
with the dry-run unit's sandbox, and, on Intel devices with a profile, a read-only test of the module `surface_rapl_mmio` (allow_write=0). It changes
nothing for good: no limit, no `enabled`, no unit, no MOK, nothing sent to the internet. The result is one text file; with `gh` the tester can put it into
the device's issue of this repository, else the script prints the links.
"""
import argparse
import contextlib
import datetime as dt
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .. import supervisor
from ..adapters import msr, rapl_copies
from . import report

REPO_SLUG = "larsschellhas/linux-surface-power"
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MODULE_SRC = os.path.join(REPO, "kernel", "surface-rapl-mmio")
ZONE = "sys/class/powercap/intel-rapl-mmio:0"
SECURE_BOOT = "sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
# Keys a distribution's DKMS uses to sign modules (decision 25.11.2026: no new MOK enrolment).
DKMS_KEYS = (("var/lib/shim-signed/mok/MOK.priv", "var/lib/shim-signed/mok/MOK.der"),     # Ubuntu
             ("var/lib/dkms/mok.key", "var/lib/dkms/mok.pub"))                               # Fedora, Arch with DKMS
# Platform type per profile for the read-only module test (`kernel-mmio-rapl.md` 6.1 item 2; types from `research/15` 2, device-to-type mapping from the
# earlier driver notes, not re-verified there [H]). A wrong type only makes the module refuse: its MCHBAR check compares the config with the constant.
PLATFORM = {
    "surface-book-3.toml": "icelake", "surface-laptop-go.toml": "icelake", "surface-go-3.toml": "icelake",
    "surface-pro-7-plus.toml": "tigerlake", "surface-pro-8.toml": "tigerlake", "surface-laptop-4-intel.toml": "tigerlake",
    "surface-laptop-go-2.toml": "tigerlake", "surface-laptop-studio.toml": "tigerlake",
    "surface-pro-9.toml": "alderlake", "surface-laptop-5.toml": "alderlake", "surface-laptop-go-3.toml": "alderlake",
    "surface-go-4.toml": "alderlake", "surface-laptop-studio-2.toml": "raptorlake",
    "surface-pro-10.toml": "meteorlake", "surface-pro-10-5g.toml": "meteorlake", "surface-laptop-6.toml": "meteorlake",
    "surface-laptop-7-5g.toml": "meteorlake", "surface-pro-11-intel.toml": "lunarlake", "surface-laptop-7-intel.toml": "lunarlake",
}
IN_MODULE_TABLE = {"Surface_Book_3_1899"}      # SKUs in the module's DMI table: no unsafe_platform needed
DRY_RUN_S = 120


def _read(root: str, rel: str) -> str | None:
    try:
        with open(os.path.join(root, rel)) as f:
            return f.read().strip()
    except OSError:
        return None


def run(cmd: list[str], timeout: int = 120, **kw) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def secure_boot(root: str = "/") -> bool | None:
    try:
        with open(os.path.join(root, SECURE_BOOT), "rb") as f:
            return f.read()[-1] == 1
    except (OSError, IndexError):
        return None


def issue_title(product: str) -> str:
    """One issue per device model (decision 25.11.2026)."""
    return f"Device report: {product}"


def issue_links(product: str) -> tuple[str, str]:
    """(search link, new-issue link with the template and the title); the body is pasted by the tester (a URL cannot carry the report)."""
    from urllib.parse import quote
    title = issue_title(product)
    search = f"https://github.com/{REPO_SLUG}/issues?q={quote(f'is:issue in:title \"{title}\"')}"
    new = f"https://github.com/{REPO_SLUG}/issues/new?template=device-report.yml&title={quote(title)}"
    return search, new


class Check:
    def __init__(self, root: str = "/", assume_yes: bool = False, out=None):
        self.root, self.assume_yes = root, assume_yes
        self.out = out or sys.stdout
        self.result: list[str] = []
        self.device = supervisor.detect(root)
        self.profile = supervisor.find_profile(self.device)
        self.cleanup: list[list[str]] = []

    # --- output ---
    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def add(self, text: str = "") -> None:
        self.result.append(text)

    def ask(self, question: str) -> bool:
        if self.assume_yes:
            return True
        try:
            return input(f"{question} [y/N] ").strip().lower() in ("y", "yes", "j", "ja")
        except EOFError:
            return False

    # --- steps ---
    def preflight(self) -> list[str]:
        d, p = self.device, self.profile
        vendor = report.cpu_vendor(self.root)
        lines = [f"device: {d.product} (sku {d.sku}), Microsoft {d.microsoft}, SMF {d.smf}",
                 f"profile: {p.file + (' (tested)' if p.tested else ' (untested)') if p else 'none'}",
                 f"kernel: {os.uname().release}, CPU {vendor or 'unknown'}",
                 f"Secure Boot: {({True: 'on', False: 'off', None: 'unknown'})[secure_boot(self.root)]}, "
                 f"lockdown: {_read(self.root, 'sys/kernel/security/lockdown') or 'not available'}, "
                 f"module signatures enforced: {_read(self.root, 'sys/module/module/parameters/sig_enforce') or 'unknown'}"]
        writers = supervisor.limit_writers(self.root)
        if writers:
            lines.append(supervisor.describe_writers(writers))
        for u in ("surface-powerd", "surface-powerd-dryrun"):
            lines.append(f"{u}: {run(['systemctl', 'is-active', u])[1] or 'unknown'}")
        return lines

    def plan(self) -> list[str]:
        steps = ["1. the report with EC data (as root; reads, writes nothing)",
                 f"2. a dry-run of the daemon for {DRY_RUN_S} s in a transient unit with the sandbox of surface-powerd-dryrun.service (writes nothing)"]
        if self.module_allowed()[0]:
            steps.append("3. a read-only test of the kernel module surface_rapl_mmio: build it for this kernel, load it with allow_write=0, compare "
                         "its values with the register, unload it (the kernel is marked tainted until the next boot; asked separately)")
        return steps

    def module_allowed(self) -> tuple[bool, str]:
        if report.cpu_vendor(self.root) != "GenuineIntel":
            return False, "not an Intel CPU"
        if self.profile is None or self.profile.file not in PLATFORM:
            return False, "no profile with a platform type for this device"
        if rapl_copies.has_proc_thermal(self.root):
            return False, "the processor thermal device 00:04.0 is present: the kernel's own MMIO zone exists, the module would decline"
        return True, PLATFORM[self.profile.file]

    def step_report(self) -> None:
        self.add("## Report (`surface-power-report --with-ec`, as root)")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                report.main(["--root", self.root, "--with-ec"])
            except Exception as e:  # a crash of the report is a result too
                print(f"report failed: {e!r}")
        self.add("```")
        self.add(buf.getvalue().rstrip())
        self.add("```")

    def step_dry_run(self) -> None:
        self.add(f"## Dry-run ({DRY_RUN_S} s, transient unit with the dry-run sandbox)")
        work = "/run/surface-power-check"
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(os.path.join(REPO, "src"), os.path.join(work, "src"))     # ProtectHome hides a checkout under /home
        unit = "surface-power-check"
        props = ["ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes", "PrivateNetwork=yes", "NoNewPrivileges=yes",
                 "RestrictAddressFamilies=AF_UNIX", "CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_SYS_RAWIO", "MemoryMax=100M",
                 f"ReadWritePaths={work}", f"RuntimeMaxSec={DRY_RUN_S + 60}", f"Environment=PYTHONPATH={work}/src"]
        cmd = ["systemd-run", f"--unit={unit}", "--collect", "--quiet"] + [f"--property={p}" for p in props] + \
            [sys.executable, "-m", "surface_power.daemon", "--dry-run", "--run-dir", f"{work}/run", "--state-dir", f"{work}/state"]
        t0 = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        code, text = run(cmd)
        if code != 0:
            self.add(f"could not start the transient unit: {text}")
            return
        self.say(f"   dry-run running for {DRY_RUN_S} s ...")
        time.sleep(DRY_RUN_S)
        run(["systemctl", "stop", unit])       # a clean stop (SIGTERM); RuntimeMaxSec is only the safety net
        _, journal = run(["journalctl", "-u", unit, "--since", t0, "--no-pager", "-o", "cat"])
        self.add("```")
        self.add(journal)
        self.add("```")
        status = os.path.join(work, "run", "status.json")
        try:
            with open(status) as f:
                s = json.load(f)
            bad = [i["channel"] for i in s.get("inputs", []) if not i.get("valid") and not i["channel"].startswith("99999999")]
            self.add(f"status: state {s.get('state')}, pl_path {s.get('pl_path')}, invalid inputs {', '.join(bad) or 'none'}, "
                     f"absent {', '.join(s.get('inputs_absent', [])) or 'none'}")
        except (OSError, ValueError) as e:
            self.add(f"status.json not readable: {e}")
        shutil.rmtree(work, ignore_errors=True)

    def _zone_values(self) -> dict | None:
        z = os.path.join(self.root, ZONE)
        vals = {}
        for k in ("enabled", "constraint_0_power_limit_uw", "constraint_1_power_limit_uw", "constraint_0_time_window_us"):
            v = _read(self.root, f"{ZONE}/{k}")
            if v is None:
                return None
            vals[k] = int(v)
        return vals if os.path.isdir(z) else None

    def _register(self):
        unit = msr.power_unit_w(self.root)
        copy, why = rapl_copies.read_mmio(self.root, unit) if unit else (None, "MSR 0x606 not readable")
        return copy, why

    def step_module(self) -> None:
        ok, platform = self.module_allowed()
        self.add("## Module surface_rapl_mmio, read only (`kernel-mmio-rapl.md` 6.1 item 3)")
        if not ok:
            self.add(f"skipped: {platform}")
            return
        if os.path.isdir(os.path.join(self.root, "sys/module/surface_rapl_mmio")):
            self.add("skipped: the module is already loaded (allow_write="
                     f"{_read(self.root, 'sys/module/surface_rapl_mmio/parameters/allow_write')}); its zone: {self._zone_values()}")
            return
        build = f"/lib/modules/{os.uname().release}/build"
        if not os.path.isdir(build):
            self.add(f"skipped: no kernel headers for {os.uname().release} ({build} missing); install the headers of the running kernel")
            return
        if not shutil.which("make") or not shutil.which("gcc"):
            self.add("skipped: make or gcc missing")
            return
        if not self.ask("   Run the read-only module test? It loads a module that is not part of your kernel (allow_write=0, the kernel is "
                        "marked tainted until the next boot)."):
            self.add("skipped: the tester said no")
            return
        tmp = tempfile.mkdtemp(prefix="surface-power-check-")
        try:
            for f in ("surface_rapl_mmio.c", "Kbuild", "Makefile"):
                shutil.copy(os.path.join(MODULE_SRC, f), tmp)
            code, text = run(["make", "-C", build, f"M={tmp}", "modules"], timeout=300)
            ko = os.path.join(tmp, "surface_rapl_mmio.ko")
            if code != 0 or not os.path.exists(ko):
                self.add("build failed:\n```\n" + text[-2000:] + "\n```")
                return
            self.add(f"built for {os.uname().release}")
            if secure_boot(self.root) or _read(self.root, "sys/module/module/parameters/sig_enforce") == "Y":
                key = next(((os.path.join(self.root, k), os.path.join(self.root, c)) for k, c in DKMS_KEYS
                            if os.path.exists(os.path.join(self.root, k)) and os.path.exists(os.path.join(self.root, c))), None)
                if key is None:
                    self.add("skipped: Secure Boot needs a signed module and no DKMS key of the distribution was found "
                             "(the kit does not enrol a new key)")
                    return
                code, text = run([os.path.join(build, "scripts/sign-file"), "sha512", key[0], key[1], ko])
                if code != 0:
                    self.add(f"signing failed: {text}")
                    return
                self.add(f"signed with the DKMS key {os.path.dirname(key[0])}")
            before, why = self._register()
            params = ["allow_write=0"] + ([] if self.device.sku in IN_MODULE_TABLE else [f"unsafe_platform={platform}"])
            code, text = run(["insmod", ko] + params)
            _, dmesg = run(["dmesg"])
            mod_lines = [ln for ln in dmesg.splitlines()[-40:] if "surface_rapl_mmio" in ln or "intel_rapl" in ln]
            if code != 0:
                self.add(f"insmod {' '.join(params)}: refused ({text})")
                self.add("```\n" + "\n".join(mod_lines) + "\n```")
                return
            self.add(f"insmod {' '.join(params)}: loaded")
            self.add("```\n" + "\n".join(mod_lines) + "\n```")
            zone = self._zone_values()
            self.add(f"zone: {zone}")
            if before is not None:
                self.add(f"register: {before.raw:#018x} ({before.describe()})")
                if zone:
                    same = (abs(zone["constraint_0_power_limit_uw"] - before.pl1[0] * 1e6) < 1
                            and abs(zone["constraint_1_power_limit_uw"] - before.pl2[0] * 1e6) < 1
                            and bool(zone["enabled"]) == before.pl1[1])
                    self.add("zone equals the register: " + ("yes" if same else "NO"))
            else:
                self.add(f"register not readable: {why}")
            code, text = run(["rmmod", "surface_rapl_mmio"])
            after, _ = self._register()
            self.add(f"rmmod: {'ok' if code == 0 else text}")
            if before is not None and after is not None:
                self.add("register after unload bit-identical: " + ("yes" if after.raw == before.raw else f"NO ({after.raw:#018x})"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def cdev(self) -> None:
        if os.path.isdir(os.path.join(self.root, "sys/module/surface_aggregator_cdev")):
            return
        code, text = run(["modprobe", "surface_aggregator_cdev"])
        if code == 0:
            self.cleanup.append(["modprobe", "-r", "surface_aggregator_cdev"])
            self.say("   loaded surface_aggregator_cdev for this run (unloaded at the end)")
        else:
            self.say(f"   surface_aggregator_cdev could not be loaded ({text}); the EC parts stay unchecked")

    def finish(self) -> str:
        for cmd in self.cleanup:
            run(cmd)
        model = re.sub(r"[^A-Za-z0-9]+", "-", self.device.product or "device").strip("-").lower()
        name = f"surface-power-check-{model}-{dt.datetime.now():%Y%m%d-%H%M}.md"
        user_dir = os.getcwd()
        path = os.path.join(user_dir, name)
        with open(path, "w") as f:
            f.write("\n".join(self.result) + "\n")
        uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
        if uid and gid:
            os.chown(path, int(uid), int(gid))
        return path

    def issue(self, path: str) -> None:
        product = self.device.product or "unknown"
        title = issue_title(product)
        search, new = issue_links(product)
        user = os.environ.get("SUDO_USER")
        gh = shutil.which("gh")
        if gh and user:
            code, _ = run(["sudo", "-u", user, gh, "auth", "status"])
            if code == 0 and self.ask(f"Put the result into the issue \"{title}\" of {REPO_SLUG} with gh (as {user})?"):
                code, found = run(["sudo", "-u", user, gh, "issue", "list", "-R", REPO_SLUG, "--state", "all", "--search",
                                   f"\"{title}\" in:title", "--json", "number,title"])
                number = None
                if code == 0:
                    try:
                        number = next((i["number"] for i in json.loads(found) if i["title"] == title), None)
                    except ValueError:
                        number = None
                if number:
                    code, text = run(["sudo", "-u", user, gh, "issue", "comment", str(number), "-R", REPO_SLUG, "--body-file", path])
                else:
                    code, text = run(["sudo", "-u", user, gh, "issue", "create", "-R", REPO_SLUG, "--title", title, "--body-file", path])
                self.say(text if code == 0 else f"gh failed: {text}")
                if code == 0:
                    return
        self.say(f"Please look whether the issue \"{title}\" exists: {search}")
        self.say(f"If it does, paste the file as a comment there; else open it here and paste the file: {new}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="surface-power-check", description="One command for a tester's device check (writes nothing for good).")
    ap.add_argument("--yes", action="store_true", help="answer every question with yes")
    ap.add_argument("--no-dry-run", action="store_true", help="skip the dry-run")
    ap.add_argument("--no-issue", action="store_true", help="do not offer to put the result into an issue")
    args = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("run as root: sudo ./scripts/surface-power-check", file=sys.stderr)
        return 1
    c = Check(assume_yes=args.yes)
    if not c.device.supported:
        print(f"not a Surface SMF device (vendor {c.device.vendor!r}, SMF {c.device.smf}): nothing to check")
        return 0
    c.say("surface-power-check: this machine")
    for line in c.preflight():
        c.say("  " + line)
    c.say("It will run, writing nothing for good:")
    for s in c.plan():
        c.say("  " + s)
    if not c.ask("Start?"):
        return 0
    c.add(f"# {issue_title(c.device.product)}: check of {dt.datetime.now():%Y-%m-%d %H:%M}")
    c.add("```")
    c.result += c.preflight()
    c.add("```")
    c.cdev()
    c.say("1. report ...")
    c.step_report()
    if not args.no_dry_run:
        c.say("2. dry-run ...")
        c.step_dry_run()
    c.say("3. module ...")
    c.step_module()
    path = c.finish()
    c.say(f"Result: {path} (please read it before you share it)")
    if not args.no_issue:
        c.issue(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
