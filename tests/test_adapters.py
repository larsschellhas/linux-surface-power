"""Tests for the input and output adapters on a fake sysfs (spec inputs, outputs; AC-I4, AC-O1, AC-S7)."""
import os
import struct
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
        self.sent = []                                             # (target, command) of every request

    def request(self, tid, cid, iid=1):
        self.calls += 1
        self.sent.append((tid, cid))
        if self.fail:
            return None
        if cid == 0x53:
            import struct
            ipl = 73500 if tid == 1 else 119200
            return bytes([1]) + struct.pack("<IIHHII", 47900, 35600, 170, 8030, ipl, 9700)
        if cid == 0x0F:
            return (120000).to_bytes(4, "little")
        if cid == 0x1A:
            return bytes([7])                                      # discharge-rate EWMA level
        if cid == 0x69:
            return bytes([1])                                      # max charge reached
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

    def test_platform_power_from_the_new_dptf_group(self):
        """Kernel#195 names the groups `dptf`; it wins over the old names, which installed systems still have."""
        self.fs.write("sys/class/power_supply/BAT1/dptf/available_input_power_mw", 73113)
        self.fs.write("sys/class/power_supply/BAT1/dptf_battery/available_input_power_mw", 11111)
        self.fs.write("sys/class/power_supply/ADP1/dptf/adapter_rating_mw", 120000)
        self.fs.write("sys/class/power_supply/ADP1/dptf_power/adapter_rating_mw", 22222)
        pp = inputs.PlatformPower(self.fs.root, transport=FakeTransport())
        pp.restrict({ref(POWER, 8), ref(POWER, 7)})
        v = pp.read(0)
        self.assertEqual((v[ref(POWER, 8)], v[ref(POWER, 7)]), (73113, 120000))
        self.assertFalse(pp.needs_cdev)
        self.assertEqual(pp.cdev_channels(), set())

    def test_platform_power_adapter_rating_read_rarely(self):
        """Each read of adapter_rating_mw is an EC request: read on source changes and every 60 s, not every cycle."""
        bat = "sys/class/power_supply/BAT1/dptf_battery/available_input_power_mw"
        artg = "sys/class/power_supply/ADP1/dptf_power/adapter_rating_mw"
        self.fs.write(bat, 73113)
        self.fs.write(artg, 120000)
        pp = inputs.PlatformPower(self.fs.root, transport=FakeTransport())
        self.assertEqual(pp.read(0)[ref(POWER, 7)], 120000)
        self.fs.write(artg, 65000)                          # rating changed without an event: not seen before 60 s
        self.assertEqual(pp.read(1000)[ref(POWER, 7)], 120000)
        self.assertEqual(pp.read(59000)[ref(POWER, 7)], 120000)
        self.assertEqual(pp.read(60000)[ref(POWER, 7)], 65000)    # fallback after 60 s
        self.fs.write(artg, 120000)
        self.fs.write(bat, 73000)                           # battery event: the source changed, read the rating at once
        self.assertEqual(pp.read(61000)[ref(POWER, 7)], 120000)
        self.fs.write(artg, 65000)
        pp.request_refresh()                                # resume
        self.assertEqual(pp.read(62000)[ref(POWER, 7)], 65000)

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

    # --- inputs of the newer blobs (spec inputs 8) ---

    def test_display_brightness_percent(self):
        self.fs.write("sys/class/backlight/acpi_video0/type", "firmware")
        self.fs.write("sys/class/backlight/acpi_video0/brightness", 5)
        self.fs.write("sys/class/backlight/acpi_video0/max_brightness", 10)
        self.fs.write("sys/class/backlight/intel_backlight/type", "raw")
        self.fs.write("sys/class/backlight/intel_backlight/brightness", 87446)
        self.fs.write("sys/class/backlight/intel_backlight/max_brightness", 96000)
        self.assertEqual(inputs.DisplayBrightness(self.fs.root).read(0)[ref(inputs.STATE, 42)], 50)   # firmware first
        os.remove(self.fs.path("sys/class/backlight/acpi_video0/type"))
        self.assertEqual(inputs.DisplayBrightness(self.fs.root).read(0)[ref(inputs.STATE, 42)], 91)

    def test_display_brightness_without_backlight_not_provided(self):
        self.assertEqual(inputs.DisplayBrightness(self.fs.root).channels, set())

    def _connector(self, name, status, enabled="enabled", dpms="On"):
        d = f"sys/class/drm/card1-{name}"
        self.fs.write(f"{d}/status", status)
        self.fs.write(f"{d}/enabled", enabled)
        self.fs.write(f"{d}/dpms", dpms)

    def test_external_display(self):
        ext = ref(inputs.POSTURE, 2)
        self._connector("eDP-1", "connected")
        self._connector("DP-1", "disconnected")
        self.assertEqual(inputs.PlatformEvents(self.fs.root).read(0)[ext], 0)
        self._connector("DP-1", "connected", "disabled", "Off")          # plugged in counts, lit or not
        self.assertEqual(inputs.PlatformEvents(self.fs.root).read(0)[ext], 1)

    def test_camera_from_runtime_pm(self):
        cam = ref(inputs.POSTURE, 3)
        usb = "sys/devices/pci0000:00/usb1/1-1"
        self.fs.write(f"{usb}/power/control", "auto")
        self.fs.write(f"{usb}/power/runtime_status", "suspended")
        self.fs.link("sys/class/video4linux/video0/device", f"{usb}/1-1:1.0")
        pe = inputs.PlatformEvents(self.fs.root)
        self.assertEqual(pe.read(0)[cam], 0)
        self.fs.write(f"{usb}/power/runtime_status", "active")
        self.assertEqual(pe.read(0)[cam], 1)

    def test_camera_from_open_node_without_runtime_pm(self):
        cam = ref(inputs.POSTURE, 3)
        dev = "sys/devices/platform/cam0"
        self.fs.write(f"{dev}/power/control", "on")
        self.fs.write(f"{dev}/power/runtime_status", "active")      # always active: says nothing about use
        self.fs.link("sys/class/video4linux/video2/device", dev)
        pe = inputs.PlatformEvents(self.fs.root)
        self.assertEqual(pe.read(0)[cam], 0)
        self.fs.link_raw("proc/4242/fd/7", "/dev/video2")
        self.assertEqual(pe.read(0)[cam], 1)

    def test_surface_docks_counted(self):
        dock = ref(inputs.POSTURE, 9)
        for n, (vid, pid) in enumerate((("045e", "0900"), ("045e", "09a9"), ("045e", "07a5"), ("8087", "0900"))):
            self.fs.write(f"sys/bus/usb/devices/1-{n}/idVendor", vid)
            self.fs.write(f"sys/bus/usb/devices/1-{n}/idProduct", pid)
        self.assertEqual(inputs.PlatformEvents(self.fs.root).read(0)[dock], 2)

    def test_battery_current_sign(self):
        cur = ref(POWER, 12)
        bat = "sys/class/power_supply/BAT1"
        self.fs.write(f"{bat}/status", "Discharging")
        self.fs.write(f"{bat}/power_now", 16000000)        # 16 W
        self.fs.write(f"{bat}/voltage_now", 8000000)       # 8 V
        bc = inputs.BatteryCurrent(self.fs.root)
        self.assertEqual(bc.read(0)[cur], 2000)
        self.fs.write(f"{bat}/status", "Charging")
        self.assertEqual(bc.read(0)[cur], -2000)
        self.fs.write(f"{bat}/current_now", 1500000)       # preferred where present (µA)
        self.assertEqual(bc.read(0)[cur], -1500)
        self.fs.write(f"{bat}/status", "Full")
        self.fs.write(f"{bat}/current_now", 0)
        self.assertEqual(bc.read(0)[cur], 0)

    def test_battery_ec_state(self):
        tr = FakeTransport()
        be = inputs.BatteryEcState(self.fs.root, transport=tr)
        v = be.read(0)
        self.assertEqual((v[ref(POWER, 13)], v[ref(POWER, 16)]), (7, 1))
        calls = tr.calls
        be.read(5000)
        self.assertEqual(tr.calls, calls)                  # cached
        be.read(10000)
        self.assertGreater(tr.calls, calls)                # every 10 s
        v = inputs.BatteryEcState(self.fs.root, transport=FakeTransport(fail=True)).read(0)
        self.assertIsNone(v[ref(POWER, 13)])

    def test_battery_ec_state_without_debug_device_not_provided(self):
        class Absent(FakeTransport):
            def available(self):
                return False
        self.assertEqual(inputs.BatteryEcState(self.fs.root, transport=Absent()).channels, set())

    # --- EC requests: only what the blob reads, and no endless retries of a command that times out (kernel#195) ---

    def test_ec_requests_only_for_wanted_channels(self):
        """Pro 9 blob: battery 1 fields, adapter rating and 0x1A, but no base battery and no 0x69."""
        tr = FakeTransport()
        pp = inputs.PlatformPower(self.fs.root, transport=tr)
        be = inputs.BatteryEcState(self.fs.root, transport=tr)
        wanted = {ref(POWER, c) for c in (1, 2, 3, 6, 7, 8, 13)}
        pp.restrict(wanted)
        be.restrict(wanted)
        pp.read(0)
        be.read(0)
        self.assertEqual(sorted(tr.sent), [(1, 0x0F), (1, 0x1A), (1, 0x53)])
        self.assertEqual(be.read(20000)[ref(POWER, 13)], 7)
        self.assertNotIn(ref(POWER, 16), be.read(20000))

    def test_failing_command_is_given_up_until_resume(self):
        class Dead(FakeTransport):
            def request(self, tid, cid, iid=1):
                if cid == 0x69:
                    self.sent.append((tid, cid))
                    return None
                return super().request(tid, cid, iid)
        tr, logs = Dead(), []
        be = inputs.BatteryEcState(self.fs.root, transport=tr)
        be.log = lambda prio, text: logs.append((prio, text))
        for t in range(10):
            v = be.read(t * 10000)
        self.assertEqual(tr.sent.count((1, 0x69)), 3)              # not every 10 s forever
        self.assertEqual(tr.sent.count((1, 0x1A)), 10)             # a command that answers is not affected
        self.assertIsNone(v[ref(POWER, 16)])
        self.assertEqual(v[ref(POWER, 13)], 7)
        self.assertEqual(len(logs), 1)
        self.assertIn("0x69", logs[0][1])
        be.request_refresh()                                       # resume: one more chance
        be.read(200000)
        self.assertEqual(tr.sent.count((1, 0x69)), 4)

    def test_platform_power_gives_up_on_a_silent_battery(self):
        tr = FakeTransport(fail=True)
        pp = inputs.PlatformPower(self.fs.root, transport=tr)
        logs = []
        pp.log = lambda prio, text: logs.append(text)
        for t in range(8):
            pp.read(t * 60000)
        self.assertEqual(len([1 for tid, cid in tr.sent if cid == 0x53 and tid == 1]), 3)
        self.assertEqual(len([1 for tid, cid in tr.sent if cid == 0x0F]), 3)
        self.assertEqual(len(logs), 3)                             # once per command: two batteries, adapter rating

    def test_missing_debug_device_is_not_a_failure(self):
        class Absent(FakeTransport):
            def available(self):
                return False
        tr = Absent()
        pp = inputs.PlatformPower(self.fs.root, transport=tr)
        pp.log = lambda *a: self.fail("reported at start by the daemon, not per request")
        pp.read(0)
        self.assertEqual(tr.sent, [])

    def test_zero_fields_in_the_0x53_answer_are_values(self):
        """Pro 9 (field report): available input power and rest of platform read 0 on AC; the rest of the answer is fine."""
        class Pro9(FakeTransport):
            def request(self, tid, cid, iid=1):
                self.sent.append((tid, cid))
                if cid == 0x53:
                    return bytes([1]) + struct.pack("<IIHHII", 142770, 82840, 100, 8000, 0, 0)
                return None
        pp = inputs.PlatformPower(self.fs.root, transport=Pro9())
        pp.restrict({ref(POWER, c) for c in (2, 3, 6, 8)})
        v = pp.read(0)
        self.assertEqual((v[ref(POWER, 2)], v[ref(POWER, 3)], v[ref(POWER, 6)], v[ref(POWER, 8)]), (142770, 82840, 0, 0))
        self.assertEqual(pp.answers[1][0], 1)

    def test_adapter_rating_zero_on_ac_is_unknown_on_battery_valid(self):
        class Rating(FakeTransport):
            value = 0

            def request(self, tid, cid, iid=1):
                if cid == 0x0F:
                    self.sent.append((tid, cid))
                    return self.value.to_bytes(4, "little")
                return super().request(tid, cid, iid)
        tr = Rating()
        pp = inputs.PlatformPower(self.fs.root, transport=tr)
        pp.restrict({ref(POWER, 7)})
        self.assertIsNone(pp.read(0)[ref(POWER, 7)])               # AC (fake system) and 0: not known yet
        self.assertIsNone(pp.read(4000)[ref(POWER, 7)])
        pp.read(5000)                                              # asked again after 5 s ...
        pp.read(10000)
        pp.read(15000)
        n = len(tr.sent)
        self.assertEqual(n, 4)                                     # ... three more times, then every 60 s again
        pp.read(20000)
        self.assertEqual(len(tr.sent), n)
        tr.value = 38745
        self.assertEqual(pp.read(75000)[ref(POWER, 7)], 38745)
        self.fs.write("sys/class/power_supply/ADP1/online", 0)     # battery: 0 means no adapter and counts
        tr.value = 0
        self.assertEqual(pp.read(76000)[ref(POWER, 7)], 0)
        tr.value = 0x80000000                                      # "not present": invalid
        pp.request_refresh()
        self.assertIsNone(pp.read(77000)[ref(POWER, 7)])

    def test_platform_profile_legacy_and_class_path(self):
        """Fedora/Bazzite shows only /sys/firmware/acpi/platform_profile, other kernels the class device."""
        self.assertEqual(inputs.platform_profile(self.fs.root), ("performance", "/sys/firmware/acpi/platform_profile"))
        os.remove(self.fs.path("sys/firmware/acpi/platform_profile"))
        self.assertEqual(inputs.platform_profile(self.fs.root)[0], None)
        self.fs.write("sys/class/platform-profile/platform-profile-0/name", "thinkpad")
        self.fs.write("sys/class/platform-profile/platform-profile-0/profile", "low-power")
        self.fs.write("sys/class/platform-profile/platform-profile-1/name", "surface_platform_profile")
        self.fs.write("sys/class/platform-profile/platform-profile-1/profile", "balanced")
        self.assertEqual(inputs.platform_profile(self.fs.root),
                         ("balanced", "/sys/class/platform-profile/platform-profile-1"))
        self.assertEqual(inputs.SliderState(self.fs.root).read(0)[ref(SYSTEM, 1)], 1)    # balanced on AC

    def test_standby_phase_is_normal(self):
        self._connector("eDP-1", "connected", "disabled", "Off")
        self.assertEqual(inputs.StandbyPhase(self.fs.root).read(0)[ref(inputs.MS_NOTIFY, 100)], 4)

    def test_dgpu_on_battery(self):
        r = ref(inputs.STATE, 31)
        self.assertEqual(inputs.DgpuOnBattery(self.fs.root).read(0)[r], 0)        # FakeSys: on AC
        self.fs.write("sys/class/power_supply/ADP1/online", 0)
        self.assertEqual(inputs.DgpuOnBattery(self.fs.root).read(0)[r], 1)

    def test_proximity_sensor_absent(self):
        self.assertEqual(inputs.ProximitySensor(self.fs.root).read(0)[ref(inputs.COMPOSITE, 113)], 0)

    def test_xbox_controller(self):
        r = ref(inputs.COMPOSITE, 5)
        xc = inputs.XboxController(self.fs.root)
        self.fs.write("sys/class/input/input3/name", "AT Translated Set 2 keyboard")
        self.fs.write("sys/class/input/input3/id/vendor", "0001")
        self.fs.write("sys/class/input/input9/name", "Microsoft X-Box 360 pad")   # a Microsoft device, not a joystick yet
        self.fs.write("sys/class/input/input9/id/vendor", "045e")
        self.assertEqual(xc.read(0)[r], 0)
        os.makedirs(self.fs.path("sys/class/input/input9/js0"))
        self.assertEqual(xc.read(0)[r], 1)

    def test_usb_source_power(self):
        usb = ref(inputs.SPT, 9)
        self.assertEqual(inputs.UsbSourcePower(self.fs.root).channels, set())   # no Type-C class: not provided
        tc = "sys/class/typec"
        self.fs.write(f"{tc}/port0/power_role", "[source] sink")
        self.fs.write(f"{tc}/port0/power_operation_mode", "1.5A")
        self.fs.write(f"{tc}/port1/power_role", "source [sink]")         # charging the device: not counted
        self.fs.write(f"{tc}/port1/power_operation_mode", "usb_power_delivery")
        self.fs.write(f"{tc}/port2/power_role", "[source] sink")         # no partner: not counted
        up = inputs.UsbSourcePower(self.fs.root)
        self.assertEqual(up.read(0)[usb], 0)
        os.makedirs(self.fs.path(f"{tc}/port0-partner"))
        os.makedirs(self.fs.path(f"{tc}/port1-partner"))
        self.assertEqual(up.read(0)[usb], 7500)
        self.fs.write(f"{tc}/port0/power_operation_mode", "usb_power_delivery")
        pdo = f"{tc}/port0/usb_power_delivery/source-capabilities/1:fixed_supply"
        self.fs.write(f"{pdo}/voltage", 5000)
        self.fs.write(f"{pdo}/maximum_current", 3000)
        self.assertEqual(up.read(0)[usb], 15000)

    def test_reader_reads_only_wanted_temperatures(self):
        """Every surface_thermal read is an EC request: sensors the blob does not use are not read."""
        t = inputs.HwmonTemperatures(self.fs.root)
        self.assertGreater(len(t.paths), 2)
        inputs.InputReader({ref(TEMP, 509)}, [t])
        self.assertEqual(set(t.paths), {ref(TEMP, 509)})

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
        # PL4 is automatic: written where peak_power reports a value (the fake tree has 100 W).
        self.assertEqual(st.targets[ref(LIMITS, 53)].path.split("/")[-1], "constraint_2_power_limit_uw")
        self.assertEqual(self.stage(pl4=False).kind(ref(LIMITS, 53)), "log-only")       # off in the profile
        self.assertEqual(st.kind(ref(LIMITS, 201)), "log-only")
        self.assertEqual(st.kind(ref(LIMITS, 59)), "log-only")       # PL3: no MSR device in the fake tree

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
        self.assertIn("pl3", " ".join(st.notes))

    def test_pl4_reading_zero_is_log_only_unless_the_profile_enables_it(self):
        self.fs.write("sys/class/powercap/intel-rapl:0/constraint_2_power_limit_uw", 0)      # as on the SB3
        self.assertEqual(self.stage().kind(ref(LIMITS, 53)), "log-only")
        self.assertIn("peak_power reads 0", " ".join(self.stage().notes))
        self.assertEqual(self.stage(pl4=True).kind(ref(LIMITS, 53)), "written")

    def _msr(self, pl3_raw):
        """A fake MSR device: 0x606 with power unit 3 (1/8 W), 0x615 as given."""
        import struct
        dev = self.fs.path("dev/cpu/0/msr")
        os.makedirs(os.path.dirname(dev), exist_ok=True)
        with open(dev, "wb") as f:
            f.write(bytes(0x620))
        with open(dev, "r+b") as f:
            f.seek(0x606); f.write(struct.pack("<Q", 0x000A0E03))
            f.seek(0x615); f.write(struct.pack("<Q", pl3_raw))
        return dev

    def _read_pl3(self, dev):
        import struct
        with open(dev, "rb") as f:
            f.seek(0x615)
            return struct.unpack("<Q", f.read(8))[0]

    def test_pl3_through_msr_keeps_window_and_enable(self):
        self.ranges[ref(LIMITS, 59)] = OutputChannel(1, ref(LIMITS, 59), 65000, 101000, 20000)
        dev = self._msr(0x0012_8000 | 400)                 # enabled, some time-window bits, 50 W
        st = self.stage()
        self.assertEqual(st.kind(ref(LIMITS, 59)), "written")
        self.assertEqual(st.targets[ref(LIMITS, 59)].read_uw(), 50_000_000)
        st.apply({ref(LIMITS, 59): 72500})
        raw = self._read_pl3(dev)
        self.assertEqual(raw & 0x7FFF, 580)                 # 72.5 W in 1/8 W
        self.assertEqual(raw & ~0x7FFF, 0x0012_8000)        # window and enable bit untouched
        self.assertEqual(self.stage(pl3=False).kind(ref(LIMITS, 59)), "log-only")

    def test_failed_msr_write_turns_pl3_log_only(self):
        self.ranges[ref(LIMITS, 59)] = OutputChannel(1, ref(LIMITS, 59), 65000, 101000, 20000)
        dev = self._msr(0x8000 | 400)
        st = self.stage()
        os.chmod(dev, 0o444)                                  # e.g. CAP_SYS_RAWIO missing at write time
        res = st.apply({ref(LIMITS, 59): 90000})
        self.assertEqual(res[ref(LIMITS, 59)].kind, "log-only")
        self.assertEqual(st.kind(ref(LIMITS, 59)), "log-only")
        self.assertIn("write failed", " ".join(st.notes))

    def test_msr_snapshot_restores_the_register(self):
        from surface_power import supervisor
        self.ranges[ref(LIMITS, 59)] = OutputChannel(1, ref(LIMITS, 59), 65000, 101000, 20000)
        dev = self._msr(0x8000 | 400)
        st = self.stage()
        snap, _ = supervisor.Snapshot.take(self.fs.path("run"), st.snapshot_keys())
        st.apply({ref(LIMITS, 59): 90000})
        self.assertNotEqual(self._read_pl3(dev), 0x8000 | 400)
        self.assertEqual(snap.restore(), [])
        self.assertEqual(self._read_pl3(dev), 0x8000 | 400)

    def test_held_value_is_not_written(self):
        st = self.stage()
        before = self.fs.read("sys/class/powercap/intel-rapl:0/constraint_2_power_limit_uw")
        res = st.apply({ref(LIMITS, 53): 20000}, hold=frozenset({ref(LIMITS, 53)}))
        self.assertEqual(res[ref(LIMITS, 53)].kind, "held")
        self.assertEqual(self.fs.read("sys/class/powercap/intel-rapl:0/constraint_2_power_limit_uw"), before)


class ThermalActionsTest(unittest.TestCase):
    """O_HOT / O_CRT at 2 start hibernate / power off once, after 5 s (spec outputs 7)."""

    def setUp(self):
        from surface_power.adapters import actions
        self.a = actions
        self.calls = []
        self.ok = {"systemctl hibernate": False, "systemctl suspend": True, "systemctl poweroff": True}
        self.t = actions.ThermalActions(runner=lambda cmd: (self.calls.append(" ".join(cmd)), self.ok[" ".join(cmd)])[1])

    def test_hot_falls_back_to_suspend_and_fires_once(self):
        v = {self.a.HOT: 2, self.a.CRT: 1}
        self.assertEqual(self.t.step(0, v, armed=True), [])
        self.t.step(4000, v, armed=True)
        self.assertEqual(self.calls, [])                       # not yet held for 5 s
        ev = self.t.step(5000, v, armed=True)
        self.assertEqual(self.calls, ["systemctl hibernate", "systemctl suspend"])
        self.assertIn("systemctl suspend", ev[0][1])
        self.t.step(10000, v, armed=True)
        self.assertEqual(len(self.calls), 2)                   # once per request
        self.t.step(11000, {self.a.HOT: 1}, armed=True)        # request gone: re-armed
        self.t.step(12000, v, armed=True)
        self.t.step(17000, v, armed=True)
        self.assertEqual(len(self.calls), 4)

    def test_critical_wins_and_unarmed_only_logs(self):
        v = {self.a.HOT: 2, self.a.CRT: 2}
        self.t.step(0, v, armed=False)
        ev = self.t.step(5000, v, armed=False)
        self.assertEqual(self.calls, [])
        self.assertIn("not armed", ev[0][1])
        t2 = self.a.ThermalActions(runner=lambda cmd: (self.calls.append(" ".join(cmd)), True)[1])
        t2.step(0, v, armed=True)
        t2.step(5000, v, armed=True)
        self.assertEqual(self.calls, ["systemctl poweroff"])


if __name__ == "__main__":
    unittest.main()
