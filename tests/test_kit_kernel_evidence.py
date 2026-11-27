"""A whole kit run on a device whose kernel has no temperature sensor node (plan D9-D11): the probe runs, the tester sees it, and
the evaluation derives the kernel change from the result.

The report and the daemon's own view run for real, on a fake sysfs with a made-up blob; only the controller is simulated.
"""
import io
import os
import shutil
import struct
import tempfile
import unittest

from fakemachine import FakeMachine
from helpers import LIMITS, POWER, SYSTEM, TEMP, VN, BlobBuilder, G, efivar

from surface_power.tools import check, check_eval
from surface_power.tools.kit import evidence_ec

EFIVAR = "sys/firmware/efi/efivars/SMF_DATA_BLOB-a3d05fa9-db5e-490c-956e-a57384a4b70b"


class Controller:
    """A SAM that answers the temperature, fan and battery requests, and gets warmer with every reading."""

    path = "/fake/aggregator"

    def __init__(self, mask=0b1_0001_0000, fan=True):
        self.mask, self.fan, self.asked, self.sent, self.readings = mask, fan, [], [], 0

    def available(self):
        return True

    def can_write(self):
        return True

    def needs_root(self):
        return False

    def send(self, *a, **kw):
        self.sent.append(a)
        return True

    def request(self, tid, cid, iid=1):
        self.asked.append((2, cid, iid, tid))
        if cid == 0x53:
            return bytes([1]) + struct.pack("<IIHHII", 60000, 59000, 115, 7878, 60000, 9900)
        return struct.pack("<I", 60000) if cid == 0x0F else None

    def read(self, tc, cid, iid=1, tid=1, capacity=64):
        self.asked.append((tc, cid, iid, tid))
        if tc == 0x05:
            self.readings += 1
            return struct.pack("<H", 2000 + 20 * self.readings) if self.fan and cid == 0x01 else None
        if tc != 0x03:
            return None
        if cid == 0x04:
            return None if self.mask is None else struct.pack("<H", self.mask)
        if cid == 0x0E:
            return struct.pack("<Hb", 0, 0) + f"I_VTS{iid}".encode().ljust(18, b"\0")
        if cid == 0x01:
            self.readings += 1
            return struct.pack("<H", 3042 + self.readings // 3)
        return None


class NoSensorNode(unittest.TestCase):
    def run_kit(self, controller, hub="MSHW0119", product="Surface Pro 7+", sku="Surface_Pro_7+_1960"):
        m = FakeMachine()
        self.addCleanup(m.cleanup)
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        fs = m.fs
        fs.write("sys/class/dmi/id/product_name", product)
        fs.write("sys/class/dmi/id/product_sku", sku)
        shutil.rmtree(fs.path("sys/class/hwmon/hwmon2"))                        # no surface_thermal: no sensor node
        shutil.rmtree(fs.path("sys/class/power_supply/BAT1/dptf"))              # and no DPTF attributes
        os.makedirs(fs.path(f"{evidence_ec.HUB_DRIVER}/{hub}:00"))
        os.makedirs(fs.path("sys/bus/surface_aggregator/devices/01:03:01:00:01"))
        b = BlobBuilder()
        b.input_channel(TEMP, 509, 3681, 2731)
        b.line(G["pid_sets"], 1, [(TEMP, 509), (SYSTEM, 1)], [(VN, 1)], [20, 1, 15, 4, 0], divider=1)
        b.line(G["pid_parameter_set"], 1, [], [], [1, 1, 1, 30000, 30001, 7500, 21234, 3211, 10, 40, 100, 0.4])
        b.line(G["pass_through"], 2, [(VN, 1)], [(LIMITS, 51)], [15678], stage=1)
        b.line(G["pass_through"], 3, [(TEMP, 505)], [(LIMITS, 54)], [45000], stage=1)
        b.line(G["pass_through"], 4, [(POWER, 2)], [(LIMITS, 53)], [100000], stage=1)       # battery peak power: EC data
        fs.write(EFIVAR, "")
        with open(fs.path(EFIVAR), "wb") as f:
            f.write(efivar(b.text()))
        m.real_sampler = True
        patches = m.patches(os.path.join(work.name, "work"), real_report=True)
        for p in patches:
            p.start()
        try:
            answers = {"i accept": "I accept", "collect": "y", "load test": "y", "kernel module": "n"}
            out = io.StringIO()
            c = check.Check(m.root, out=out, release="7.3.6-1-surface",
                            ask_line=lambda q: next((v for k, v in answers.items() if k in q.lower()), ""))
            c.transport = controller
            check.run_all(c, c.ask_everything())
            c.preflight_lines = []
            with open(c.save(work.name)) as f:
                text = f.read()
        finally:
            for p in reversed(patches):
                p.stop()
        return c, " ".join(out.getvalue().split()), text, check_eval.extract(text)

    def test_the_probe_runs_once_and_the_tester_sees_it(self):
        ec = Controller()
        c, console, text, data = self.run_kit(ec)
        self.assertNotIn("The check itself failed", console, c.result.notes)
        self.assertIn("Your kernel has no temperature sensors for this device, so the check asked the controller", console)
        self.assertIn("2 of 2 sensors answered", console)
        self.assertIn("sensor 5 I_VTS5", console)
        self.assertIn("sensors your device needs (5, 9) answered", console)
        self.assertIn("Sensor probe: 2 sensors answered; the sensors your device needs (5, 9) answered.",
                      c.result.stages["S1"]["summary"])
        self.assertEqual(sum(1 for a in ec.asked if a[:2] == (3, 0x04)), 1, "the mask is asked once")
        self.assertEqual(sum(1 for a in ec.asked if a[:2] == (3, 0x0E)), 2, "each name is asked once")
        self.assertEqual(ec.sent, [])                                            # reads only
        self.assertIn("TMP 0x04 mask 0x0110", text)                              # also in the report part
        self.assertEqual(data["sensor_probe"]["wanted"], [5, 9])
        self.assertEqual(data["kernel_nodes"], {"hub_id": "MSHW0119", "temperature_node": False, "fan_node": False, "hwmon": []})
        columns = data["load"]["samples"]["columns"]
        self.assertIn("sam5_c", columns)                                         # followed under load
        self.assertIn("sam9_c", columns)

    def test_the_evaluation_derives_the_sensor_node_with_its_proof(self):
        _, _, _, data = self.run_kit(Controller())
        out = "\n".join(check_eval.kernel_changes(data))
        self.assertIn("hub `MSHW0119` (Surface Pro 7+), node group `ssam_node_group_sp7_dptf`; SKU Surface_Pro_7+_1960", out)
        self.assertIn("**supports adding the node** for this device", out)
        self.assertIn("add `&ssam_node_tmp_sensors,`", out)
        self.assertIn("The group serves MSHW0119 only", out)
        self.assertIn("TC 0x03, target 0x01, command 0x04, instance 0x00, no payload -> 1001", out)
        self.assertRegex(out, r"\| 9 \| I_VTS9 \| [\d.]+ °C \| [\d.]+ \.\. [\d.]+ °C \| yes \|")
        self.assertIn("has `has_dptf` for this group (since linux-surface 7.3.5-1)", out)    # EC data: an update, no patch
        self.assertIn("TC 0x02, target 0x01, command 0x53", out)
        self.assertIn("## Kernel changes this result gives evidence for", check_eval.skeleton(data, patterns=()))

    def test_a_shared_group_is_named_and_not_called_proven_for_the_others(self):
        _, _, _, data = self.run_kit(Controller(), hub="MSHW0250", product="Surface Laptop 4", sku="Surface_Laptop_4_1950:1951")
        out = "\n".join(check_eval.kernel_changes(data))
        self.assertIn("**The group is shared with MSHW0110", out)

    def test_a_sensor_the_blob_needs_that_is_missing_gives_no_support(self):
        _, console, _, data = self.run_kit(Controller(mask=0b1_0000))
        self.assertIn("9 did not answer", console)
        out = "\n".join(check_eval.kernel_changes(data))
        self.assertIn("**does not yet support it:** sensor 9, which the blob reads, did not answer", out)

    def test_a_controller_that_does_not_answer_is_said_and_gives_no_support(self):
        ec = Controller(mask=None)
        _, console, _, data = self.run_kit(ec)
        self.assertIn("the controller did not answer the question for them", console)
        self.assertEqual([a for a in ec.asked if a[0] == 3], [(3, 0x04, 0, 1)])      # nothing else is sent, also under load
        self.assertIn("did not answer the mask request", "\n".join(check_eval.kernel_changes(data)))

    def test_hostile_records_are_dropped(self):
        data = {"kernel_nodes": {"hub_id": "MSHW0119", "temperature_node": False},
                "ec_requests": ["x", {"tc": "3"}, {"tc": 3, "tid": 1, "cid": 4, "iid": 0, "answer": "zz; rm -rf /"}]}
        out = "\n".join(check_eval.kernel_changes(data))
        self.assertNotIn("rm -rf", out)
        self.assertIn("nothing to add", out)


if __name__ == "__main__":
    unittest.main()
