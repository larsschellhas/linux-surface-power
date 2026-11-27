"""The kit's kernel advice (spec tester-kit.md 3a) on a fake sysfs, with the kernel strings of the first device reports."""
import io
import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts src on the path)
from fakesys import FakeSys

from surface_power.tools import check, kernelcheck


class ParseTest(unittest.TestCase):
    def test_release_strings(self):
        for release, want in (("7.3.6-1-surface", (7, 3, 6, 1)),
                              ("7.3.4-arch1-2-surface", (7, 3, 4, 2)),        # issue #2
                              ("7.2.15-1-surface", (7, 2, 15, 1)),            # issue #4
                              ("7.3.6-1.surface.fc43.x86_64", (7, 3, 6, 1)),
                              ("7.0.0-44-generic", None),
                              ("6.18.8-1-surface-lts", None),
                              ("7.3.0-surface-custom", None)):
            with self.subTest(release):
                self.assertEqual(kernelcheck.parse(release), want)


class AdviceTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        import shutil
        for sub in ("sys/class/hwmon", "sys/class/power_supply", "sys/bus/platform/devices", "lib/modules"):
            shutil.rmtree(self.fs.path(sub), ignore_errors=True)

    def tearDown(self):
        self.fs.cleanup()

    def hub(self, acpi_id):
        os.makedirs(self.fs.path(f"sys/bus/platform/devices/{acpi_id}:00"))

    def dptf(self):
        os.makedirs(self.fs.path("sys/class/power_supply/BAT1/dptf"))

    def sensors(self):
        self.fs.write("sys/class/hwmon/hwmon0/name", "surface_thermal")

    def advise(self, release):
        return kernelcheck.advise(release, self.fs.root)

    def test_outdated_names_what_the_update_adds_on_this_device(self):
        self.hub("MSHW0119")                                       # Pro 7+, issue #2
        a = self.advise("7.3.4-arch1-2-surface")
        self.assertTrue(a.ask)
        self.assertIn("outdated", a.summary)
        text = "\n".join(a.details)
        self.assertIn("EC power data", text)
        self.assertIn("Keep your old kernel installed", text)
        self.assertNotIn("temperature sensors", text)              # the update adds no sensor node on a Pro 7+

    def test_surface_book_3_also_misses_sensors_and_fans(self):
        self.hub("MSHW0117")
        text = "\n".join(self.advise("7.3.4-2-surface").details)
        self.assertIn("EC power data", text)
        self.assertIn("temperature sensors and fan speeds", text)

    def test_outdated_without_a_known_gain_says_so(self):
        a = self.advise("7.2.15-1-surface")
        self.assertTrue(a.ask)
        self.assertIn("none is known to change this check on your device", "\n".join(a.details))

    def test_current_is_quiet(self):
        self.hub("MSHW0510")
        self.dptf()
        a = self.advise("7.3.6-1-surface")
        self.assertEqual((a.ask, a.details), (False, []))
        self.assertIn("current", a.summary)

    def test_new_enough_but_not_newest_does_not_ask(self):
        self.hub("MSHW0510")
        self.dptf()
        a = self.advise("7.3.5-1-surface")
        self.assertEqual((a.ask, a.details), (False, []))
        self.assertIn("a newer linux-surface release exists", a.summary)

    def test_new_enough_with_a_missing_feature_is_a_note_not_a_question(self):
        self.hub("MSHW0117")
        self.dptf()
        a = self.advise("7.3.6-1-surface")
        self.assertFalse(a.ask)
        self.assertIn("temperature sensors", "\n".join(a.details))

    def test_installed_but_not_booted_asks_for_a_reboot(self):
        os.makedirs(self.fs.path("lib/modules/7.3.6-1-surface"))
        os.makedirs(self.fs.path("lib/modules/7.0.0-44-generic"))
        a = self.advise("7.3.4-2-surface")
        self.assertTrue(a.ask)
        self.assertIn("reboot into 7.3.6-1-surface", "\n".join(a.details))
        self.assertNotIn("Please update", "\n".join(a.details))

    def test_other_kernel_asks_unless_everything_needed_is_there(self):
        self.hub("MSHW0510")
        self.assertTrue(self.advise("7.0.0-44-generic").ask)
        self.dptf()
        self.assertFalse(self.advise("7.0.0-44-generic").ask)

    def test_pro_9_gets_the_open_regression_report(self):
        self.hub("MSHW0343")
        self.assertIn("surface-aggregator-module/issues/29", "\n".join(self.advise("7.3.4-2-surface").details))


class GateTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()

    def tearDown(self):
        self.fs.cleanup()

    def gate(self, advice, answer):
        c = check.Check(self.fs.root, out=io.StringIO())
        c.kernel = advice
        with mock.patch("builtins.input", side_effect=lambda q: answer):
            return c.kernel_gate(), c

    def test_default_answer_stops(self):
        ok, c = self.gate(kernelcheck.Advice("old", True, ["update"]), "")
        self.assertFalse(ok)
        self.assertIn("PLEASE UPDATE FIRST: old", c.out.getvalue())

    def test_going_on_is_recorded_in_the_result(self):
        ok, c = self.gate(kernelcheck.Advice("old", True, ["update"]), "y")
        self.assertTrue(ok)
        self.assertIn("the tester chose to go on", c.kernel.summary)

    def test_a_note_does_not_ask(self):
        ok, c = self.gate(kernelcheck.Advice("fine but", False, ["something"]), "n")
        self.assertTrue(ok)
        self.assertIn("NOTE: fine but", c.out.getvalue())


if __name__ == "__main__":
    unittest.main()
