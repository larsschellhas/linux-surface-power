"""Local tests against the real SB3 15" blob (AC-E2, AC-E3, AC-E5, AC-E6) and the blob corpus (AC-B2, AC-B3).

The blobs are Microsoft content and are not in the repository. These tests run only when the raw research folder is present
(docs/research/raw/, git-ignored); otherwise they are skipped.
"""
import glob
import os
import unittest

from helpers import LIMITS, POWER, SYSTEM, TEMP, ref

from surface_power.engine.engine import Engine
from surface_power.engine.graph import build_graph
from surface_power.engine.scheduler import Scheduler
from surface_power.loader import container, parser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "docs", "research", "raw")
SB3_EFIVAR = os.path.join(RAW, "arbeitsordner", "smf-blob", "SMF_DATA_BLOB.efivar")
SB3_CAPSULE = glob.glob(os.path.join(RAW, "tools", "fw", "sb3", "keep", "SurfaceUpdate", "surfacesmf15in", "*.bin"))

PL1, PL2, PL4, PSYS1, PSYS2, PL3 = (ref(LIMITS, c) for c in (51, 52, 53, 54, 55, 59))
VTS1, SLIDER, POSTURE = ref(TEMP, 509), ref(SYSTEM, 1), ref("8060B17F-2FFB-4139-82E6-8B4C60F48419", 1)
SOC1, SOC2, PEAK1, MAX1, ROP1, PSU, IPL, BCDR1 = (ref(POWER, c) for c in (1, 101, 2, 3, 6, 7, 8, 10))


def load_sb3():
    with open(SB3_EFIVAR, "rb") as f:
        dec = container.decode(f.read(), efivar=True)
    return build_graph(parser.parse_text(dec.text, dec.form), "classic")


def normal_inputs(over=None):
    """Inputs of a cool SB3 on the Surface PSU, Best performance (state 5). `over` replaces single values."""
    v = {VTS1: 3011, SLIDER: 5, POSTURE: 1, SOC1: 80, SOC2: 90, PEAK1: 47900, MAX1: 35600, ROP1: 9700, PSU: 120000,
         IPL: 73500, BCDR1: 0}
    v.update(over or {})
    return v


@unittest.skipUnless(os.path.exists(SB3_EFIVAR), "raw SB3 blob not present")
class Sb3Test(unittest.TestCase):
    def setUp(self):
        self.graph = load_sb3()
        self.eng = Engine(self.graph)
        self.sched = Scheduler(self.eng)

    def out(self, values):
        """Set inputs (on top of what is already set), run once, return the rounded outputs."""
        self.eng.set_inputs(values)
        self.eng.evaluate(1, False, 0)
        return self.eng.rounded_outputs()

    def test_graph(self):
        self.assertTrue(self.graph.usable_for_control)
        self.assertEqual(self.graph.unsupported, [])
        self.assertEqual(len(self.graph.controllers), 4)

    def test_capsule_equals_efivar(self):  # AC-B1 (offline part)
        if not SB3_CAPSULE:
            self.skipTest("capsule not present")
        with open(SB3_CAPSULE[0], "rb") as f:
            cap = container.decode(f.read())
        with open(SB3_EFIVAR, "rb") as f:
            var = container.decode(f.read(), efivar=True)
        self.assertEqual(cap.text, var.text)

    def test_slider_ceilings(self):  # AC-E2
        pl1 = [25, 13, 25, 25, 30, 25, 10, 25, 25, 25]
        pl2 = [25, 15, 40, 25, 61, 35, 11, 61, 30, 25]
        self.out(normal_inputs())
        for state in range(1, 11):
            with self.subTest(state=state):
                o = self.out({SLIDER: state})
                self.assertEqual(o[PL1], pl1[state - 1] * 1000)
                self.assertEqual(o[PL2], pl2[state - 1] * 1000)

    def test_psys(self):  # AC-E5
        o = self.out(normal_inputs())
        self.assertEqual((o[PSYS1], o[PSYS2]), (45000, 100000))     # Surface PSU: cap 45 W, PL2 at its 100 W clamp
        o = self.out({IPL: 10000})
        self.assertEqual((o[PSYS1], o[PSYS2]), (33000, 44500))      # + 23 W / + 34.5 W
        o = self.out({SOC1: 8})
        self.assertEqual((o[PSYS1], o[PSYS2]), (26100, 33000))      # charge <= 10 %: + 16.1 W / + 23 W
        o = self.out({IPL: None, SOC1: 80})
        self.assertEqual((o[PSYS1], o[PSYS2]), (23000, 34500))      # no EC data: block defaults
        o = self.out({PSU: 65000, IPL: 73500})
        self.assertEqual(o[PSYS1], 45000)                           # the cap is the same for a small PSU

    def test_pl1_default_when_an_input_is_invalid(self):  # AC-E6
        # The PL1 min block reads only internal nodes, which are always valid, so its 15 W default never applies.
        # An invalid posture makes the posture table give its "below" value, 25.001 W.
        self.assertEqual(self.out(normal_inputs())[PL1], 30000)
        self.assertEqual(self.out({POSTURE: None})[PL1], 25001)
        self.assertEqual(self.out({POSTURE: 1})[PL1], 30000)

    def test_skin_controller_ramp(self):  # AC-E3
        trace = []
        self.sched.step(0, normal_inputs())
        t = 1000
        # 46.0 °C up to 48.5 °C in 0.1 K steps every 5 s, hold for 60 s, then down to 45.0 °C.
        temps = list(range(3191, 3237)) + [3236] * 12 + list(range(3236, 3180, -2))
        for x in temps:
            for _ in range(5):
                self.sched.step(t, {VTS1: x})
                t += 1000
            trace.append((x, self.eng.rounded_outputs()[PL1], self.graph.controllers[0].state))
        states = {x: (pl1, st) for x, pl1, st in trace[:46]}
        self.assertEqual(states[3200][1], 0)              # 46.9 °C: OFF
        self.assertEqual(states[3201], (21000, 1))        # 47.0 °C: READY, cut to U_on = 21 W
        self.assertEqual(states[3210], (21000, 1))
        self.assertEqual(states[3211][1], 2)              # 48.0 °C: ACTIVE
        self.assertLess(trace[57][1], 21000)              # above the setpoint PL1 goes down
        self.assertEqual(trace[-1][1:], (30000, 0))       # below 45.5 °C: OFF, 30 W

    def test_battery_controller_limits_pl1(self):
        self.sched.step(0, normal_inputs({SLIDER: 6}))
        for t in range(1000, 60000, 1000):
            self.sched.step(t, {BCDR1: 30000 + (t // 1000) % 3})   # ~30 W discharge on battery, slightly noisy
        self.assertLess(self.eng.rounded_outputs()[PL1], 25000)


@unittest.skipUnless(os.path.isdir(os.path.join(RAW, "tools", "fw")), "raw firmware packages not present")
class CorpusTest(unittest.TestCase):  # AC-B2, AC-B3
    def test_all_blobs_parse_and_build(self):
        files = sorted(glob.glob(os.path.join(RAW, "tools", "fw", "*", "keep", "SurfaceUpdate", "*", "*.bin")))
        files = [f for f in files if "mptf" not in f.lower()]
        self.assertGreaterEqual(len(files), 41)
        for path in files:
            with self.subTest(blob=os.path.relpath(path, RAW)):
                with open(path, "rb") as f:
                    dec = container.decode(f.read())
                graph = build_graph(parser.parse_text(dec.text, dec.form), "direct")
                for u in graph.unsupported:
                    self.assertTrue(u.reason)


if __name__ == "__main__":
    unittest.main()
