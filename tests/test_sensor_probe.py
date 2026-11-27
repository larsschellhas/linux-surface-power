"""The read-only SAM sensor probe (plan D9-D11, spec tools 3) on a fake transport and a fake sysfs."""
import contextlib
import io
import struct
import unittest

from fakesys import FakeSys
from helpers import LIMITS, TEMP, BlobBuilder, G

from surface_power.adapters.inputs import ChannelRef
from surface_power.tools import report, sensorprobe


class Ec:
    """Answers TMP 0x04, 0x0E and 0x01 and records every request as (category, command, instance, target)."""

    path = "/fake/aggregator"

    def __init__(self, mask=0b1_0001_0000, temps=None, writable=True):
        self.mask, self.writable, self.reads, self.sent = mask, writable, [], []
        self.temps = temps if temps is not None else {5: 3042, 9: 3081}

    def can_write(self):
        return self.writable

    def needs_root(self):
        return False

    def available(self):
        return True

    def request(self, tid, cid, iid=1):
        return None

    def send(self, *a, **kw):
        self.sent.append(a)
        return True

    def read(self, tc, cid, iid=1, tid=1, capacity=64):
        self.reads.append((tc, cid, iid, tid))
        if tc != 0x03:
            return None
        if cid == 0x04:
            return None if self.mask is None else struct.pack("<H", self.mask)
        if cid == 0x0E:
            return struct.pack("<Hb", 0, 0) + f"I_VTS{iid}".encode().ljust(18, b"\0")
        if cid == 0x01 and iid in self.temps:
            return struct.pack("<H", self.temps[iid])
        return None


class ProbeTest(unittest.TestCase):
    def test_only_the_sensors_in_the_mask_are_asked(self):
        ec = Ec()
        mask, sensors = sensorprobe.probe(ec)
        self.assertEqual(mask, 0b1_0001_0000)
        self.assertEqual(sensors, [{"sensor": 5, "name": "I_VTS5", "celsius": 31.1},
                                   {"sensor": 9, "name": "I_VTS9", "celsius": 35.0}])
        self.assertEqual(ec.reads, [(3, 0x04, 0, 1), (3, 0x0E, 5, 1), (3, 0x01, 5, 1), (3, 0x0E, 9, 1), (3, 0x01, 9, 1)])
        self.assertEqual(ec.sent, [])                                      # nothing is written

    def test_no_answer_to_the_mask_request_ends_the_probe(self):
        ec = Ec(mask=None)
        out = sensorprobe.lines(ec, [5, 9], "test")
        self.assertEqual(len(ec.reads), 1)
        self.assertIn("no answer to TMP 0x04; nothing else was sent", out[0])

    def test_lines_name_the_sensors_the_blob_reads(self):
        out = "\n".join(sensorprobe.lines(Ec(mask=0b1_0000, temps={}), [5, 9], "test"))
        self.assertIn("sensor 5: name 'I_VTS5', no answer", out)
        self.assertIn("sensor 5 (in the mask), sensor 9 (NOT in the mask)", out)

    def test_without_the_debug_device_nothing_is_sent(self):
        ec = Ec(writable=False)
        self.assertIn("skipped", sensorprobe.lines(ec, [5], "test")[0])
        self.assertEqual(ec.reads, [])

    def test_wanted_sensors_are_the_sam_channels_only(self):
        refs = [ChannelRef(TEMP, 509), ChannelRef(TEMP, 505), ChannelRef(TEMP, 605), ChannelRef(TEMP, 102), ChannelRef(LIMITS, 505)]
        self.assertEqual(sensorprobe.wanted_sensors(refs), [5, 9])


class ReportProbeTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        b = BlobBuilder()
        b.input_channel(TEMP, 509, 3681, 2731)
        b.line(G["pass_through"], 1, [(TEMP, 509)], [(LIMITS, 51)], [15678], stage=1)
        self.blob = self.fs.path("blob.txt")
        with open(self.blob, "w") as f:
            f.write(b.text())

    def tearDown(self):
        self.fs.cleanup()

    def run_report(self, ec, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report.main(["--root", self.fs.root, "--blob", self.blob, *extra], fan_transport=ec, ec_transport=ec)
        return out.getvalue()

    def tmp_reads(self, ec):
        return [r for r in ec.reads if r[0] == 0x03]

    def remove_sensor_node(self):
        import glob
        import shutil
        for d in glob.glob(self.fs.path("sys/class/hwmon/*")):
            try:
                with open(d + "/name") as f:
                    name = f.read().strip()
            except OSError:
                continue
            if name == "surface_thermal":
                shutil.rmtree(d)

    def test_probed_where_the_kernel_has_no_node(self):
        self.remove_sensor_node()
        ec = Ec()
        text = self.run_report(ec, "--with-ec")
        self.assertIn("the kernel has no sensor node here): TMP 0x04 mask 0x0110", text)
        self.assertIn("2E8B14C2/509: not available", text)               # the probe is no input source

    def test_not_probed_with_a_node_unless_forced(self):
        ec = Ec()
        self.assertNotIn("sensor probe", self.run_report(ec, "--with-ec"))
        self.assertEqual(self.tmp_reads(ec), [])
        self.assertIn("forced with --probe-sensors", self.run_report(ec, "--with-ec", "--probe-sensors"))

    def test_not_probed_without_with_ec(self):
        self.remove_sensor_node()
        ec = Ec()
        self.assertNotIn("sensor probe", self.run_report(ec))
        self.assertEqual(self.tmp_reads(ec), [])


if __name__ == "__main__":
    unittest.main()
