"""B9 and F10: PL3 when MSR 0x615 is not readable (Pro 9: EIO although 0x606 reads [M, tester]), and the read-only MSR probe of the report."""
import contextlib
import io
import os
import unittest
from unittest import mock

from fakesys import FakeSys
from helpers import LIMITS, SYSTEM, BlobBuilder, G

from surface_power.adapters import msr, outputs
from surface_power.adapters.outputs import OutputStage
from surface_power.loader.model import ChannelRef
from surface_power.tools import report

PL3 = ChannelRef(outputs.LIMITS, 59)


def eio_for(*regs):
    real = msr.probe

    def probe(root, reg, cpu=0):
        if reg in regs:
            return None, "EIO"
        return real(root, reg, cpu)
    return probe


class Pl3StageTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.fs.add_msr({0x615: 0x8000 | 800})

    def tearDown(self):
        self.fs.cleanup()

    def test_probe_reads_and_names_the_error(self):
        self.assertEqual(msr.probe(self.fs.root, 0x615), (0x8000 | 800, None))
        v, why = msr.probe(self.fs.root, 0x2000)                 # beyond the fake file: a short read
        self.assertIsNone(v)
        self.assertEqual(why, "short read")
        v, why = msr.probe(self.fs.path("nowhere"), 0x615)
        self.assertIsNone(v)
        self.assertEqual(why, "ENOENT")

    def test_pl3_target_only_when_0x615_reads(self):
        self.assertIn(PL3, OutputStage(self.fs.root, {}, pl3=True).targets)
        with mock.patch.object(msr, "probe", eio_for(0x615)):
            st = OutputStage(self.fs.root, {}, pl3=True)
        self.assertNotIn(PL3, st.targets)
        self.assertTrue(any("MSR 0x615 not readable (EIO) although 0x606 is" in n for n in st.notes), st.notes)

    def test_a_failing_write_is_an_event_not_only_a_start_note(self):
        st = OutputStage(self.fs.root, {}, pl3=True)
        with mock.patch.object(msr, "write", lambda *a, **k: False):
            res = st.apply({PL3: 90000})
        self.assertEqual(res[PL3].kind, "log-only")
        ev = st.drain_events()
        self.assertTrue(any(p == "warning" and "pl3: write failed" in t for p, t in ev), ev)


class ReportProbeTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.fs.add_msr({0x17: 1, 0x610: 0x004281e800dd80f0, 0x611: 5, 0x614: 7, 0x615: 0x8000 | 800})
        b = BlobBuilder()
        b.line(G["pass_through"], 1, [(SYSTEM, 1)], [(LIMITS, 59)], [100000])
        self.blob = self.fs.path("blob.txt")
        with open(self.blob, "w") as f:
            f.write(b.text())

    def tearDown(self):
        self.fs.cleanup()

    def run_report(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report.main(["--root", self.fs.root, "--blob", self.blob])
        return out.getvalue()

    def test_msr_probe_lines(self):
        self.fs.write("proc/cpuinfo", "processor\t: 0\nvendor_id\t: GenuineIntel\nflags\t\t: fpu msr hypervisor\n")
        with mock.patch.object(msr, "probe", eio_for(0x611, 0x614, 0x615)):
            text = self.run_report()
        self.assertIn("MSRs on CPU 0 (read only): 0x17 platform ID ok; 0x606 RAPL units ok; 0x610 PL1/PL2 ok; "
                      "0x611 package energy EIO; 0x614 package power info EIO; 0x615 PL3 EIO", text)
        self.assertIn("CPU flag hypervisor: yes (Linux runs in a virtual machine)", text)
        self.assertIn("0B7B5350/59: no Linux path", text)                      # the daemon has no PL3 target then

    def test_no_probe_without_the_msr_device(self):
        os.remove(self.fs.path("dev/cpu/0/msr"))
        self.assertIn("MSRs: not readable (needs root and the msr module)", self.run_report())


if __name__ == "__main__":
    unittest.main()
