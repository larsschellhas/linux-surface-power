"""The tester kit (spec tester-kit.md, plan F11): its pure parts on a fake sysfs; nothing here runs a command on the host."""
import glob
import io
import os
import unittest
from unittest import mock

from fakesys import FakeSys

from surface_power import supervisor
from surface_power.tools import check


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.patch = mock.patch.object(check, "run", lambda cmd, timeout=120, **kw: (0, "inactive"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.fs.cleanup()

    def cpu(self, vendor):
        self.fs.write("proc/cpuinfo", f"processor\t: 0\nvendor_id\t: {vendor}\n")

    def test_every_intel_profile_has_a_platform_type(self):
        """A new Intel profile must get its type here, or the kit skips the module test without saying why."""
        names = {os.path.basename(p) for p in glob.glob(os.path.join(supervisor.PROFILE_DIR, "*.toml"))}
        amd = {"surface-laptop-3-amd.toml", "surface-laptop-4-amd.toml"}
        self.assertEqual(set(check.PLATFORM), names - amd)

    def test_module_test_only_on_intel_with_a_profile(self):
        self.cpu("GenuineIntel")
        self.assertEqual(check.Check(self.fs.root).module_allowed(), (True, "icelake"))
        self.cpu("AuthenticAMD")
        self.assertEqual(check.Check(self.fs.root).module_allowed()[0], False)
        self.cpu("GenuineIntel")
        os.makedirs(self.fs.path("sys/bus/pci/devices/0000:00:04.0"))
        ok, why = check.Check(self.fs.root).module_allowed()
        self.assertFalse(ok)
        self.assertIn("00:04.0", why)

    def test_unknown_device_has_no_module_test(self):
        self.cpu("GenuineIntel")
        self.fs.write("sys/class/dmi/id/product_name", "Surface Something")
        self.fs.write("sys/class/dmi/id/product_sku", "Surface_Something_1")
        self.assertFalse(check.Check(self.fs.root).module_allowed()[0])

    def test_kernel_messages_keep_only_msr_and_mce_lines(self):
        dmesg = "[1.0] usb 3-2: new device\n[2.0] msr: Write to unrecognized MSR 0x615 by x\n[3.0] mce: CPU0: Machine Check\n"
        with mock.patch.object(check, "run", lambda cmd, timeout=120, **kw: (0, dmesg)):
            c = check.Check(self.fs.root, out=io.StringIO())
            c.step_kernel_messages()
        text = "\n".join(c.result)
        self.assertIn("msr: Write to unrecognized MSR 0x615", text)
        self.assertIn("mce: CPU0", text)
        self.assertNotIn("usb 3-2", text)

    def interrupted(self, c, calls, step):
        def fake_run(cmd, timeout=120, **kw):
            calls.append(cmd[0] if cmd[0] != "systemctl" else " ".join(cmd[:2]))
            return 0, ""
        with mock.patch.object(check, "run", fake_run), self.assertRaises(KeyboardInterrupt):
            step()

    def test_ctrl_c_during_the_module_test_unloads_the_module(self):
        c, calls = check.Check(self.fs.root, out=io.StringIO()), []
        with mock.patch.object(c, "_register", lambda: (None, "test")), \
                mock.patch.object(c, "_zone_values", side_effect=KeyboardInterrupt):
            self.interrupted(c, calls, lambda: c.load_and_compare("x.ko", ["allow_write=0"]))
        self.assertEqual(calls, ["insmod", "dmesg", "rmmod"])
        self.assertEqual(c.cleanup, [])

    def test_a_refused_module_is_not_unloaded_and_the_reason_is_kept(self):
        c = check.Check(self.fs.root, out=io.StringIO())
        calls = []

        def fake_run(cmd, timeout=120, **kw):
            calls.append(cmd[0])
            if cmd[0] == "insmod":
                return 1, "insmod: ERROR: Key was rejected by service"
            return 0, "[1.0] Loading of module with unavailable key is rejected\n[2.0] usb 3-2: new device"
        with mock.patch.object(check, "run", fake_run), mock.patch.object(c, "_register", lambda: (None, "test")):
            c.load_and_compare("x.ko", ["allow_write=0"])
        text = "\n".join(c.result)
        self.assertIn("unavailable key is rejected", text)
        self.assertNotIn("usb 3-2", text)
        self.assertNotIn("rmmod", calls)
        self.assertEqual(c.cleanup, [])

    def test_restore_undoes_everything_newest_first_and_only_once(self):
        c, calls = check.Check(self.fs.root, out=io.StringIO()), []
        c.undo_later(["modprobe", "-r", "surface_aggregator_cdev"])
        c.undo_later(["systemctl", "stop", "surface-power-check"])
        with mock.patch.object(check, "run", lambda cmd, timeout=120, **kw: calls.append(cmd[0]) or (0, "")):
            c.restore()
            c.restore()
        self.assertEqual(calls, ["systemctl", "modprobe"])

    def test_the_result_names_the_checkout(self):
        with mock.patch.object(check, "run", lambda cmd, timeout=120, **kw: (0, "abc123def456" if "rev-parse" in cmd else "")):
            self.assertEqual(check.repo_state(), "abc123def456")
        with mock.patch.object(check, "run", lambda cmd, timeout=120, **kw: (0, "abc123def456" if "rev-parse" in cmd else " M x")):
            self.assertEqual(check.repo_state(), "abc123def456 + local changes")

    def test_issue_title_and_links(self):
        self.assertEqual(check.issue_title("Surface Pro 9"), "Device report: Surface Pro 9")
        search, new = check.issue_links("Surface Pro 9")
        self.assertTrue(search.startswith("https://github.com/larsschellhas/linux-surface-power/issues?q="))
        self.assertIn("Device%20report%3A%20Surface%20Pro%209", search)
        self.assertIn("template=device-report.yml", new)

    def test_preflight_and_plan(self):
        self.cpu("GenuineIntel")
        c = check.Check(self.fs.root, out=io.StringIO())
        text = "\n".join(c.preflight())
        self.assertIn("device: Surface Book 3 (sku Surface_Book_3_1899)", text)
        self.assertIn("Secure Boot: unknown", text)
        plan = "\n".join(c.plan())
        self.assertIn("read-only test of the kernel module", plan)
        self.assertIn("writes nothing", plan)

    def test_result_file_has_no_serial(self):
        self.fs.write("sys/class/dmi/id/product_serial", "SERIAL123456")
        c = check.Check(self.fs.root, out=io.StringIO())
        c.add("# x")
        c.result += c.preflight()
        cwd = os.getcwd()
        os.chdir(self.fs.root)
        try:
            path = c.finish()
        finally:
            os.chdir(cwd)
        with open(path) as f:
            self.assertNotIn("SERIAL123456", f.read())
        self.assertTrue(os.path.basename(path).startswith("surface-power-check-surface-book-3-"))


if __name__ == "__main__":
    unittest.main()
