"""The signing setup and removal (spec tester-kit.md 8): real openssl for the certificates, a simulated mokutil.

The rule under test: only a certificate limited to kernel modules is ever handed to mokutil for enrolment.
"""
import io
import os
import shutil
import subprocess
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts src on the path)
from fakesys import FakeSys

from surface_power.tools import check
from surface_power.tools.kit import sh, signing, signing_setup as setup, term

REAL_RUN = sh.run
RELEASE = "7.3.6-1-surface"


@unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
class Base(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.addCleanup(self.fs.cleanup)
        self.root = self.fs.root
        self.fs.write("sys/kernel/security/lockdown", "[none] integrity confidentiality")
        self.fs.write("sys/module/module/parameters/sig_enforce", "Y")
        self.fs.write(setup.MOK_LIST, "x")
        self.fs.write(f"boot/config-{RELEASE}", "CONFIG_INTEGRITY_MACHINE_KEYRING=y\n# CONFIG_INTEGRITY_CA_MACHINE_KEYRING is not set\n")
        # the machine's owner-key list as mokutil sees it
        self.enrolled, self.new, self.delete = [], [], []
        self.handed_to_mokutil, self.mokutil_fails, self.unclear = [], False, False
        self.out = io.StringIO()
        for target, repl in (("run", self.fake_run), ("interactive", self.interactive)):
            p = mock.patch.object(sh, target, repl)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(setup.shutil, "which", lambda name: f"/usr/bin/{name}")
        p.start()
        self.addCleanup(p.stop)

    # --- the simulated mokutil; openssl is real ---
    def fake_run(self, cmd, timeout=120, **kw):
        if cmd[0] == "openssl":
            return REAL_RUN(cmd, timeout, **kw)
        if cmd[0] == "mokutil":
            if cmd[1] == "--test-key":
                if self.unclear:
                    return 1, "Failed to access the keyring"
                return 1, f"{cmd[2]} is already enrolled" if setup.fingerprint(cmd[2]) in self.enrolled else f"{cmd[2]} is not enrolled"
            which = {"--list-new": self.new, "--list-delete": self.delete, "--list-enrolled": self.enrolled}[cmd[1]]
            return 0, "\n".join("SHA1 Fingerprint: " + ":".join(m[i:i + 2] for i in range(0, len(m), 2)) for m in which)
        return 0, ""

    def interactive(self, cmd):
        self.handed_to_mokutil.append(cmd[1:])
        if self.mokutil_fails:
            return 1
        if cmd[1] == "--import":
            self.new.append(setup.fingerprint(cmd[2]))
        elif cmd[1] == "--delete":
            self.delete.append(setup.fingerprint(cmd[2]))
        elif cmd[1] == "--revoke-import":
            self.new.clear()
        return 0

    def reboot(self):
        """The tester confirms at the blue screen."""
        self.enrolled = [m for m in self.enrolled + self.new if m not in self.delete]
        self.new, self.delete = [], []

    def key_pair(self, key_rel, cert_rel, usage):
        key, cert = self.fs.path(key_rel), self.fs.path(cert_rel)
        os.makedirs(os.path.dirname(cert), exist_ok=True)
        extra = ["-addext", f"extendedKeyUsage={usage}"] if usage else []
        subprocess.run(["openssl", "req", "-new", "-x509", "-nodes", "-days", "2", "-subj", "/CN=test", "-newkey", "rsa:2048",
                        "-keyout", key, "-outform", "DER", "-out", cert, *extra], capture_output=True, check=True)
        return key, cert

    def go(self, answer="enrol", own=False):
        return setup.setup(self.root, RELEASE, term.Term(self.out, width=90), lambda q: answer, sh.interactive, own=own)

    def go_remove(self, answer="remove"):
        return setup.remove(self.root, term.Term(self.out, width=90), lambda q: answer, sh.interactive)

    @property
    def text(self):
        return " ".join(self.out.getvalue().split())


class TheRule(Base):
    def test_a_dkms_key_without_the_restriction_is_never_enrolled(self):
        for usage in ("codeSigning", None):                 # current DKMS, and the fallback without any usage
            with self.subTest(usage):
                self.setUp()
                _, other = self.key_pair("var/lib/dkms/mok.key", "var/lib/dkms/mok.pub", usage)
                self.assertEqual(self.go(), 0, self.text)
                self.assertIn("is left alone: it is not limited to kernel modules, so it could also sign boot programs", self.text)
                (option, cert), = self.handed_to_mokutil
                self.assertEqual((option, cert), ("--import", self.fs.path(setup.OWN_CERT)))
                self.assertTrue(setup.module_only(cert))
                self.assertNotEqual(setup.fingerprint(other), self.new[0])

    def test_the_certificate_is_checked_again_right_before_mokutil(self):
        key, cert = self.key_pair("var/lib/dkms/mok.key", "var/lib/dkms/mok.pub", "codeSigning")
        with mock.patch.object(setup, "choose_key", lambda *a, **kw: (key, cert, "existing")):
            self.assertEqual(self.go(), 1)
        self.assertEqual(self.handed_to_mokutil, [])
        self.assertIn("is not limited to kernel modules: it is not enrolled", self.text)
        self.assertIsNone(setup.load_state(self.root))

    def test_an_existing_restricted_key_is_used_as_it_is(self):
        _, cert = self.key_pair("var/lib/shim-signed/mok/MOK.priv", "var/lib/shim-signed/mok/MOK.der",
                                f"codeSigning,{setup.MODSIGN_OID}")
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.handed_to_mokutil, [["--import", cert]])
        self.assertEqual(setup.load_state(self.root)["made_by"], "existing")
        self.assertFalse(os.path.exists(self.fs.path(setup.OWN_CERT)))

    def test_the_kits_own_key_is_restricted_private_and_without_a_host_name(self):
        key, cert = setup.create_own(self.root)
        self.assertTrue(setup.module_only(cert))
        self.assertEqual((os.stat(os.path.dirname(key)).st_mode & 0o777, os.stat(key).st_mode & 0o777), (0o700, 0o600))
        _, subject = REAL_RUN(["openssl", "x509", "-inform", "der", "-in", cert, "-noout", "-subject"])
        self.assertNotIn(os.uname().nodename, subject)


class Setup(Base):
    def test_without_the_word_nothing_changes(self):
        for answer in ("", "y", "yes", "enroll please"):
            with self.subTest(answer):
                self.assertEqual(self.go(answer), 0)
                self.assertEqual(self.handed_to_mokutil, [])
                self.assertFalse(os.path.exists(self.fs.path(setup.OWN_DIR)))
        self.assertIn("anyone who has root here can use it", self.text)
        self.assertIn("kexec", self.text)
        self.assertIn("Have the passphrase or recovery key at hand BEFORE you go on", self.text)
        self.assertIn("Tried so far on one machine", self.text)
        self.assertIn("As long as the key stays enrolled, expect to type the passphrase at every boot", self.text)

    def test_the_disk_warning_comes_before_the_question_when_adding_and_when_removing(self):
        seen = []

        def ask(prompt):
            seen.append(" ".join(self.out.getvalue().split()))
            return "enrol" if "enrol" in prompt else "remove"
        setup.setup(self.root, RELEASE, term.Term(self.out, width=90), ask, sh.interactive)
        self.reboot()
        self.out.seek(0)
        self.out.truncate()
        setup.remove(self.root, term.Term(self.out, width=90), ask, sh.interactive)
        for before in seen:
            self.assertIn("IS YOUR DISK UNLOCKED AUTOMATICALLY BY THE TPM?", before)
        self.assertEqual(len(seen), 2)

    def test_the_whole_way_and_the_check_then_finds_the_key(self):
        self.assertEqual(self.go(), 0, self.text)
        self.assertIn("Perform MOK management", self.text)
        self.assertEqual(signing.decide(self.root).case, "C")             # there, not enrolled until the reboot
        self.reboot()
        found = signing.decide(self.root)
        self.assertEqual((found.case, found.cert), ("B", self.fs.path(setup.OWN_CERT)))

    def test_the_blue_screen_steps_name_the_key_to_compare_before_confirming(self):
        self.go()
        self.assertIn("Choose Enroll MOK, then View key 0. It must show CN=surface-power-check module signing key.", self.text)
        self.assertIn("do not confirm: choose Continue boot", self.text)
        self.reboot()
        self.out.seek(0)
        self.out.truncate()
        self.go_remove()
        self.assertIn("Choose Delete MOK, then View key 0. It must show CN=surface-power-check module signing key, and there "
                      "must be no further key.", self.text)
        self.assertIn("removing the wrong key can stop your system from starting", self.text)

    def test_the_name_comes_from_the_certificate_that_was_enrolled(self):
        self.key_pair("var/lib/shim-signed/mok/MOK.priv", "var/lib/shim-signed/mok/MOK.der", f"codeSigning,{setup.MODSIGN_OID}")
        self.go()
        self.assertIn("View key 0. It must show CN=test.", self.text)

    def test_a_second_call_before_the_reboot_does_not_import_twice(self):
        self.go()
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.handed_to_mokutil), 1)
        self.assertIn("already waiting", self.text)

    def test_a_mokutil_that_fails_leaves_no_record(self):
        self.mokutil_fails = True
        self.assertEqual(self.go(), 1)
        self.assertIsNone(setup.load_state(self.root))

    def test_nothing_to_set_up_or_no_way_to(self):
        cases = {"signature enforcement is off": lambda: self.fs.write("sys/module/module/parameters/sig_enforce", "N"),
                 "locked down": lambda: self.fs.write("sys/kernel/security/lockdown", "none [integrity] confidentiality"),
                 "does not seem to start through shim": lambda: os.remove(self.fs.path(setup.MOK_LIST)),
                 "only trusts certificate-authority keys": lambda: self.fs.write(f"boot/config-{RELEASE}",
                                                                                 "CONFIG_INTEGRITY_CA_MACHINE_KEYRING=y\n")}
        for words, change in cases.items():
            with self.subTest(words):
                self.setUp()
                change()
                self.go()
                self.assertIn(words, self.text)
                self.assertEqual(self.handed_to_mokutil, [])

    def test_the_maintainers_second_key_still_needs_shim(self):
        self.key_pair("var/lib/shim-signed/mok/MOK.priv", "var/lib/shim-signed/mok/MOK.der", f"codeSigning,{setup.MODSIGN_OID}")
        self.enrolled = [setup.fingerprint(self.fs.path("var/lib/shim-signed/mok/MOK.der"))]
        self.assertEqual(self.go(), 0)
        self.assertIn("Nothing to set up: there is a key your system trusts already", self.text)
        self.assertEqual(self.handed_to_mokutil, [])
        os.remove(self.fs.path(setup.MOK_LIST))
        self.assertEqual(self.go(own=True), 1)
        os.makedirs(os.path.dirname(self.fs.path(setup.MOK_LIST)), exist_ok=True)
        self.fs.write(setup.MOK_LIST, "x")
        self.assertEqual(self.go(own=True), 0)
        self.assertEqual(self.handed_to_mokutil, [["--import", self.fs.path(setup.OWN_CERT)]])


class Removal(Base):
    def enrol(self):
        self.go()
        self.reboot()
        self.handed_to_mokutil.clear()

    def test_nothing_of_its_own_nothing_removed(self):
        self.assertEqual(self.go_remove(), 0)
        self.assertIn("enrolled no key on this machine", self.text)
        self.assertEqual(self.handed_to_mokutil, [])

    def test_the_kits_key_goes_the_private_part_at_once_the_rest_after_the_check(self):
        self.enrol()
        self.assertEqual(self.go_remove(), 0, self.text)
        self.assertEqual(self.handed_to_mokutil[0][0], "--delete")
        self.assertFalse(os.path.exists(self.fs.path(setup.OWN_KEY)))
        self.assertTrue(os.path.exists(self.fs.path(setup.OWN_CERT)))      # needed if the blue screen is skipped
        self.reboot()
        self.assertEqual(self.go_remove(), 0)
        self.assertFalse(os.path.exists(self.fs.path(setup.OWN_DIR)))

    def test_an_unclear_answer_of_mokutil_removes_and_deletes_nothing(self):
        self.enrol()
        self.unclear = True
        self.assertEqual(self.go_remove(), 1)
        self.assertIn("does not say clearly", self.text)
        self.assertEqual(self.handed_to_mokutil, [])
        self.assertTrue(os.path.exists(self.fs.path(setup.OWN_KEY)))

    def test_a_distributions_key_keeps_its_files(self):
        key, cert = self.key_pair("var/lib/shim-signed/mok/MOK.priv", "var/lib/shim-signed/mok/MOK.der",
                                  f"codeSigning,{setup.MODSIGN_OID}")
        self.enrol()
        self.go_remove()
        self.reboot()
        self.go_remove()
        self.assertTrue(os.path.exists(key) and os.path.exists(cert))
        self.assertIsNone(setup.load_state(self.root))

    def test_a_waiting_request_is_withdrawn_unless_others_wait_too(self):
        self.go()
        self.new.append("aa" * 20)                                          # somebody else's key waits as well
        self.handed_to_mokutil.clear()
        self.assertEqual(self.go_remove(), 1)
        self.assertEqual(self.handed_to_mokutil, [])
        self.new.pop()
        self.assertEqual(self.go_remove(), 0)
        self.assertEqual(self.handed_to_mokutil, [["--revoke-import"]])


class Command(unittest.TestCase):
    def test_it_needs_a_terminal_and_the_check_offers_it(self):
        fs = FakeSys()
        self.addCleanup(fs.cleanup)
        fs.write("proc/cpuinfo", "vendor_id\t: GenuineIntel\n")
        fs.write("sys/module/module/parameters/sig_enforce", "Y")
        with mock.patch.object(sh, "run", lambda cmd, timeout=120, **kw: (0, "")):
            c = check.Check(fs.root, out=io.StringIO(), release=RELEASE)
            with mock.patch.object(check.sys.stdin, "isatty", lambda: False), \
                    mock.patch.object(setup, "setup", side_effect=AssertionError("must not run")):
                self.assertEqual(check.signing_command(c, mock.Mock(remove_signing=False, own_key=False)), 1)
            why, unlock = c.module_reason()
        self.assertIn("case D", why)
        self.assertIn("--setup-signing", unlock)


if __name__ == "__main__":
    unittest.main()
