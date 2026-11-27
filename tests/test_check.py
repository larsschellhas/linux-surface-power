"""The tester kit's entry point (spec tester-kit.md, plan F11) on a fake sysfs; nothing here runs a command on the host."""
import glob
import io
import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts src on the path)
from fakesys import FakeSys

from surface_power import supervisor
from surface_power.tools import check
from surface_power.tools.kit import sh


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.patch = mock.patch.object(sh, "run", lambda cmd, timeout=120, **kw: (0, "inactive"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.fs.cleanup()

    def cpu(self, vendor):
        self.fs.write("proc/cpuinfo", f"processor\t: 0\nvendor_id\t: {vendor}\n")

    def make(self):
        return check.Check(self.fs.root, out=io.StringIO(), release="7.3.6-1-surface")

    def test_every_intel_profile_has_a_platform_type(self):
        """A new Intel profile must get its type here, or the kit skips the module stages."""
        names = {os.path.basename(p) for p in glob.glob(os.path.join(supervisor.PROFILE_DIR, "*.toml"))}
        amd = {"surface-laptop-3-amd.toml", "surface-laptop-4-amd.toml"}
        self.assertEqual(set(check.PLATFORM), names - amd)

    def test_module_stages_only_on_intel_with_a_profile_and_without_00_04_0(self):
        self.cpu("AuthenticAMD")
        self.assertEqual(self.make().module_reason()[0], "not an Intel CPU")
        self.cpu("GenuineIntel")
        os.makedirs(self.fs.path("sys/bus/pci/devices/0000:00:04.0"))
        self.assertIn("00:04.0", self.make().module_reason()[0])

    def test_unknown_device_has_no_module_stages(self):
        self.cpu("GenuineIntel")
        self.fs.write("sys/class/dmi/id/product_name", "Surface Something")
        self.fs.write("sys/class/dmi/id/product_sku", "Surface_Something_1")
        self.assertIn("no profile", self.make().module_reason()[0])

    def test_lockdown_and_missing_headers_name_their_reason(self):
        self.cpu("GenuineIntel")
        self.fs.write("sys/module/module/parameters/sig_enforce", "N")
        self.fs.write("etc/os-release", "ID=arch\n")
        why, unlock = self.make().module_reason()
        self.assertEqual(why, "the module cannot be built")
        self.assertIn("sudo pacman -S linux-surface-headers", unlock)
        self.fs.write("sys/kernel/security/lockdown", "none [integrity] confidentiality")
        self.assertIn("case E", self.make().module_reason()[0])

    def test_no_load_without_the_charger(self):
        self.fs.write("sys/class/power_supply/ADP1/online", 0)
        from surface_power.adapters import inputs
        inputs._MAINS.clear()
        self.assertIn("charger", self.make().load_reason())

    def test_issue_title_and_links(self):
        self.assertEqual(check.issue_title("Surface Pro 9"), "Device report: Surface Pro 9")
        search, new = check.issue_links("Surface Pro 9")
        self.assertTrue(search.startswith("https://github.com/larsschellhas/linux-surface-power/issues?q="))
        self.assertIn("Device%20report%3A%20Surface%20Pro%209", search)
        self.assertIn("template=device-report.yml", new)

    def test_ctrl_c_at_a_question_ends_quietly_before_anything_runs(self):
        out = io.StringIO()

        def interrupt(prompt):
            raise KeyboardInterrupt
        real = check.Check
        with mock.patch.object(check.os, "geteuid", lambda: 0), mock.patch.object(check.undo.Undo, "lock", lambda self: None), \
                mock.patch.object(check.undo.Undo, "leftover", lambda self: None), \
                mock.patch.object(check, "Check", lambda **kw: real(self.fs.root, out=out, ask_line=interrupt,
                                                                    release="7.3.6-1-surface", **kw)), \
                mock.patch.object(check, "run_all", side_effect=AssertionError("must not run")):
            self.assertEqual(check.main([]), 130)
        self.assertIn("Stopped. Nothing was run.", out.getvalue())

    def test_preflight(self):
        self.cpu("GenuineIntel")
        text = "\n".join(self.make().preflight())
        self.assertIn("device: Surface Book 3 (sku Surface_Book_3_1899)", text)
        self.assertIn("Secure Boot: unknown", text)
        self.assertIn("kernel check: ", text)
        self.assertIn("module signing: case ", text)

    def test_an_old_checkout_is_asked_about(self):
        c = self.make()
        c.repo = {"commit": "abc", "dirty": False, "time": 1000000}
        with mock.patch("builtins.input", return_value=""):
            self.assertFalse(c.checkout_gate(now=1000000 + 20 * 86400))
        self.assertIn("20 days old", c.out.getvalue())
        self.assertTrue(c.checkout_gate(now=1000000 + 3 * 86400))

    def test_result_file_has_no_serial_and_no_private_names(self):
        self.fs.write("sys/class/dmi/id/product_serial", "SERIAL123456")
        self.fs.write("etc/passwd", "root:x:0:0::/root:/bin/sh\nmartha:x:1000:1000:Martha Example,,,:/home/martha:/bin/bash\n")
        c = self.make()
        c.preflight_lines = c.preflight()
        c.result.data["x"] = {"path": "/home/martha/checkout", "who": "martha on marthas-surface, aa:bb:cc:dd:ee:ff"}
        c.result.report_text = "Martha Example ran this; surface_thermal and linux-surface stay"
        with mock.patch.object(check.os, "uname", lambda: os.uname_result(("Linux", "marthas-surface", "r", "v", "m"))):
            path = c.save(self.fs.root)
        with open(path) as f:
            text = f.read()
        for secret in ("SERIAL123456", "martha", "Martha", "aa:bb:cc:dd:ee:ff"):
            self.assertNotIn(secret, text)
        self.assertIn("surface_thermal and linux-surface stay", text)
        self.assertTrue(os.path.basename(path).startswith("surface-power-check-surface-book-3-"))


if __name__ == "__main__":
    unittest.main()
