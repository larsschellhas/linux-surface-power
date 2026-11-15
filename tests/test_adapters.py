"""Tests for the input and output adapters on a fake sysfs (spec inputs, outputs; AC-I4, AC-O1, AC-S7)."""
import os
import unittest

from fakesys import FakeSys
from helpers import LIMITS, POWER, SYSTEM, TEMP, ref

from surface_power.adapters import inputs, outputs
from surface_power.loader.model import ChannelRef, OutputChannel

POSTURE = ref("8060B17F-2FFB-4139-82E6-8B4C60F48419", 1)
LID = ref("5D0EE597-8C3D-4305-A812-05CDFAFB1C66", 1)


class FakeTransport:
    """Stands in for the SSAM cdev: answers 0x53 and the DPTF reads."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def request(self, tid, cid, iid=1):
        self.calls += 1
        if self.fail:
            return None
        if cid == 0x53:
            import struct
            ipl = 73500 if tid == 1 else 119200
            return bytes([1]) + struct.pack("<IIHHII", 47900, 35600, 170, 8030, ipl, 9700)
        if cid == 0x0F:
            return (120000).to_bytes(4, "little")
        return None


class InputsTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()

    def tearDown(self):
        self.fs.cleanup()

    def test_temperatures(self):
        t = inputs.HwmonTemperatures(self.fs.root)
        self.fs.write("sys/class/hwmon/hwmon2/temp9_input", 48000)
        v = t.read(0)
        self.assertEqual(v[ref(TEMP, 509)], 3211)
        self.assertEqual(v[ref(TEMP, 505)], 3031)
        self.assertEqual(v[ref(TEMP, 102)], 2731 + 500)
        self.assertNotIn(ref(TEMP, 609), v)          # KIP sensors are not available
        self.fs.write("sys/class/hwmon/hwmon2/temp9_input", "garbage")
        self.assertIsNone(t.read(0)[ref(TEMP, 509)])

    def test_base_temperatures(self):
        """With the base sensors (TMP on KIP), the second surface_thermal maps to 601-609; the display part stays 501-509."""
        self.fs.add_base_sensors(40.0)
        self.fs.write("sys/class/hwmon/hwmon2/temp9_input", 48000)
        v = inputs.HwmonTemperatures(self.fs.root).read(0)
        self.assertEqual(v[ref(TEMP, 509)], 3211)
        self.assertEqual(v[ref(TEMP, 609)], 3131)
        self.assertEqual(v[ref(TEMP, 605)], 3131)

    def test_slider_mapping(self):
        s = inputs.SliderState(self.fs.root)
        cases = [("low-power", 1, 1), ("low-power", 0, 7), ("balanced", 1, 1), ("balanced", 0, 2),
                 ("balanced-performance", 1, 3), ("balanced-performance", 0, 4), ("performance", 1, 5),
                 ("performance", 0, 6), ("quiet", 1, None)]
        for profile, ac, want in cases:
            with self.subTest(profile=profile, ac=ac):
                self.fs.write("sys/firmware/acpi/platform_profile", profile)
                self.fs.write("sys/class/power_supply/ADP1/online", ac)
                self.assertEqual(s.read(0)[ref(SYSTEM, 1)], want)

    def test_batteries(self):
        b = inputs.Batteries(self.fs.root)
        v = b.read(0)
        self.assertEqual((v[ref(POWER, 1)], v[ref(POWER, 101)]), (80, 90))
        self.assertEqual((v[ref(POWER, 10)], v[ref(POWER, 110)]), (0, 0))   # charging: 0
        self.fs.write("sys/class/power_supply/BAT1/status", "Discharging")
        self.fs.write("sys/class/power_supply/BAT1/power_now", 21500000)
        self.assertEqual(b.read(0)[ref(POWER, 10)], 21500)

    def test_posture_and_lid(self):
        p = inputs.Posture(self.fs.root, dtx_mode=lambda: 1)
        self.assertEqual(p.read(0)[POSTURE], 1)
        self.assertEqual(p.read(0)[LID], 1)
        self.fs.write("proc/acpi/button/lid/LID0/state", "state:      closed")
        self.assertEqual(p.read(0)[POSTURE], 2)
        self.assertEqual(p.read(0)[LID], 2)
        p = inputs.Posture(self.fs.root, dtx_mode=lambda: 2)
        self.assertEqual(p.read(0)[POSTURE], 4)
        p = inputs.Posture(self.fs.root, dtx_mode=lambda: 0)
        self.assertEqual(p.read(0)[POSTURE], 3)
        p = inputs.Posture(self.fs.root, dtx_mode=lambda: None)   # no dtx: laptop
        self.fs.write("proc/acpi/button/lid/LID0/state", "state:      open")
        self.assertEqual(p.read(0)[POSTURE], 1)

    def test_platform_power(self):
        tr = FakeTransport()
        pp = inputs.PlatformPower(self.fs.root, transport=tr)
        v = pp.read(0)
        self.assertEqual(v[ref(POWER, 8)], 73500)
        self.assertEqual(v[ref(POWER, 108)], 119200)
        self.assertEqual(v[ref(POWER, 2)], 47900)
        self.assertEqual(v[ref(POWER, 7)], 120000)
        calls = tr.calls
        pp.read(1000)
        self.assertEqual(tr.calls, calls)                          # cached until a refresh is due
        self.fs.write("sys/class/power_supply/ADP1/online", 0)
        pp.read(2000)
        self.assertGreater(tr.calls, calls)                        # AC change: refresh
        calls = tr.calls
        pp.read(62000)
        self.assertGreater(tr.calls, calls)                        # every 60 s

    def test_platform_power_from_sysfs(self):
        """With the kernel attributes (surface_battery dptf_battery, surface_charger dptf_power) no cdev request is made,
        and an EC event that the kernel has applied is seen at the next read."""
        for bat, ipl in (("BAT1", 73113), ("BAT2", 119250)):
            d = f"sys/class/power_supply/{bat}/dptf_battery"
            for name, value in (("max_platform_power_mw", 41440), ("max_steady_state_power_mw", 34130),
                                ("high_freq_impedance_mohm", 174), ("no_load_voltage_mv", 8082),
                                ("available_input_power_mw", ipl), ("rest_of_platform_power_mw", 9700)):
                self.fs.write(f"{d}/{name}", value)
        self.fs.write("sys/class/power_supply/ADP1/dptf_power/adapter_rating_mw", 120000)
        tr = FakeTransport()
        pp = inputs.PlatformPower(self.fs.root, transport=tr)
        v = pp.read(0)
        self.assertEqual((v[ref(POWER, 2)], v[ref(POWER, 4)], v[ref(POWER, 5)]), (41440, 174, 8082))
        self.assertEqual((v[ref(POWER, 8)], v[ref(POWER, 108)], v[ref(POWER, 7)]), (73113, 119250, 120000))
        self.assertEqual(tr.calls, 0)
        self.assertFalse(pp.needs_cdev)
        self.fs.write("sys/class/power_supply/BAT2/dptf_battery/available_input_power_mw", 65000)   # event: base on USB-C
        self.assertEqual(pp.read(1000)[ref(POWER, 108)], 65000)

    def test_platform_power_unknown_adapter_rating(self):
        """ENODATA (no rating known) reads as missing; the attribute's existence still means: no cdev for it."""
        self.fs.write("sys/class/power_supply/BAT1/dptf_battery/available_input_power_mw", 45720)
        os.makedirs(self.fs.path("sys/class/power_supply/ADP1/dptf_power"), exist_ok=True)
        os.makedirs(self.fs.path("sys/class/power_supply/ADP1/dptf_power/adapter_rating_mw"))   # a read fails like ENODATA
        tr = FakeTransport()
        v = inputs.PlatformPower(self.fs.root, transport=tr).read(0)
        self.assertIsNone(v[ref(POWER, 7)])
        self.assertEqual(v[ref(POWER, 8)], 45720)
        self.assertEqual(v[ref(POWER, 108)], 119200)                     # BAT2 has no attributes here: cdev

    def test_platform_power_without_transport_is_invalid(self):
        pp = inputs.PlatformPower(self.fs.root, transport=FakeTransport(fail=True))
        v = pp.read(0)
        self.assertIsNone(v[ref(POWER, 8)])
        self.assertIsNone(v[ref(POWER, 7)])

    def test_reader_reports_missing_once(self):
        wanted = {ref(TEMP, 509), ref(TEMP, 609), ref(SYSTEM, 1), ref("99999999-9999-9999-9999-999999999999", 1)}
        r = inputs.InputReader(wanted, [inputs.HwmonTemperatures(self.fs.root), inputs.SliderState(self.fs.root)])
        self.assertEqual(r.unavailable, [ref(TEMP, 609)])           # the placeholder is not reported
        v = r.read(0)
        self.assertEqual(set(v), wanted)
        self.assertIsNone(v[ref(TEMP, 609)])


class OutputsTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.ranges = {ref(LIMITS, 51): OutputChannel(1, ref(LIMITS, 51), 25000, 31000, 7500)}

    def tearDown(self):
        self.fs.cleanup()

    def stage(self, **kw):
        return outputs.OutputStage(self.fs.root, self.ranges, **kw)

    def test_targets(self):
        st = self.stage()
        self.assertEqual(st.targets[ref(LIMITS, 51)].path.split("/")[-2:],
                         ["intel-rapl:0", "constraint_0_power_limit_uw"])
        self.assertEqual(st.targets[ref(LIMITS, 54)].path.split("/")[-2:], ["intel-rapl:1", "constraint_0_power_limit_uw"])
        self.assertEqual(st.kind(ref(LIMITS, 53)), "log-only")       # PL4 is off by default
        st4 = self.stage(pl4=True)
        self.assertEqual(st4.targets[ref(LIMITS, 53)].path.split("/")[-1], "constraint_2_power_limit_uw")
        self.assertEqual(st.kind(ref(LIMITS, 201)), "log-only")
        self.assertEqual(st.kind(ref(LIMITS, 59)), "log-only")       # PL3 is off by default

    def test_write_clamped_and_only_on_change(self):
        st = self.stage()
        res = st.apply({ref(LIMITS, 51): 21000, ref(LIMITS, 52): 61000})
        self.assertEqual(self.fs.read("sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"), "21000000")
        self.assertEqual(res[ref(LIMITS, 51)].written, True)
        res = st.apply({ref(LIMITS, 51): 21000})
        self.assertEqual(res[ref(LIMITS, 51)].written, False)
        st.apply({ref(LIMITS, 51): 40000})                            # above the blob range (31 W); max_power_uw is ignored
        self.assertEqual(self.fs.read("sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"), "31000000")
        st.apply({ref(LIMITS, 51): 1000})                             # below the blob min 7.5 W
        self.assertEqual(self.fs.read("sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"), "7500000")

    def test_hard_limits(self):
        st = self.stage()
        st.apply({ref(LIMITS, 54): 400000})
        self.assertEqual(self.fs.read("sys/class/powercap/intel-rapl:1/constraint_0_power_limit_uw"), "250000000")

    def test_narrowing(self):
        st = self.stage(narrow={"pl1": 20.0})
        st.apply({ref(LIMITS, 51): 25000})
        self.assertEqual(self.fs.read("sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"), "20000000")

    def test_narrowing_cannot_widen(self):
        with self.assertRaises(ValueError):
            self.stage(narrow={"pl1": 500.0})

    def test_dry_run_writes_nothing(self):
        st = self.stage(dry_run=True)
        res = st.apply({ref(LIMITS, 51): 21000})
        self.assertEqual(self.fs.read("sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"), "30000000")
        self.assertEqual(res[ref(LIMITS, 51)].value_uw, 21000000)
        self.assertFalse(res[ref(LIMITS, 51)].written)

    def test_foreign_change_detected(self):
        st = self.stage()
        st.apply({ref(LIMITS, 51): 21000})
        self.assertEqual(st.foreign_changes(), [])
        self.fs.write("sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw", 15000000)
        self.assertEqual([c.ref for c in st.foreign_changes()], [ref(LIMITS, 51)])

    def test_missing_zone_is_log_only(self):
        import shutil
        shutil.rmtree(self.fs.path("sys/class/powercap/intel-rapl:1"))
        st = self.stage()
        self.assertEqual(st.kind(ref(LIMITS, 54)), "log-only")
        st.apply({ref(LIMITS, 54): 45000})                            # no error

    def test_pl3_without_msr_is_skipped(self):
        st = self.stage(pl3=True)
        self.assertEqual(st.kind(ref(LIMITS, 59)), "log-only")       # no /dev/cpu/0/msr in the fake tree
        self.assertIn("PL3", " ".join(st.notes))


if __name__ == "__main__":
    unittest.main()
