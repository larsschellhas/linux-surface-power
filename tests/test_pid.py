"""Unit tests for the PID with parameter sets (spec controller; AC-E3, AC-E7, AC-E8). Synthetic parameters, SB3-like layout."""
import unittest

from helpers import LIMITS, SYSTEM, TEMP, VN, BlobBuilder, G, build, ref

from surface_power.loader.errors import BlobError

X = ref(TEMP, 509)
SEL = ref(SYSTEM, 1)
W1 = 3211          # 48.0 °C in 0.1 K
W2 = 3181          # 45.0 °C
SETS = [  # stage, U_on, W
    (0, 18000, W1),
    (1, 21000, W1),
    (2, 16000, W2),
]


def pid_blob(divider=1, n_sets=None, extra_sets=(), ctl_params=None, set_rows=None):
    b = BlobBuilder()
    params = ctl_params or [20, n_sets or len(SETS), 15, 4, 0]
    b.line(G["pid_sets"], 1, [(TEMP, 509), (SYSTEM, 1)], [(VN, 1)], params, divider=divider)
    rows = set_rows or [[1, st, st, 30000, 30001, 7500, u_on, w, 10, 40, 100, 0.4] for st, u_on, w in SETS]
    for row in list(rows) + list(extra_sets):
        b.line(G["pid_parameter_set"], 1, [], [], row)
    return b


class Rig:
    """Engine plus a few shortcuts for driving one controller."""

    def __init__(self, variant="classic", **kw):
        self.graph, self.eng = build(pid_blob(**kw), variant=variant)
        self.pid = self.graph.blocks[0]

    def run(self, x, sel=1, timer=False):
        self.eng.set_inputs({X: x, SEL: sel})
        res = self.eng.evaluate(1, timer=timer, now_ms=0)
        return self.eng.node(1), self.pid.state, res.active


def law(base, e, buf, k_e=40, k_d=100, k_m=0.4, n=20):
    """Reference for one normal law step (spec controller 4.5). `buf` is the error history before the step."""
    s = sum(buf)
    d = (e - buf[0]) * k_d
    return base + k_e * e + d + k_m * (s + e) / (n + 1)


class ClassicTest(unittest.TestCase):
    def test_ramp_up_and_down(self):
        r = Rig()
        self.assertEqual(r.run(3100), (30000, 0, False))          # below off: U_off
        self.assertEqual(r.run(3190), (30000, 0, False))          # off <= x < on: unchanged
        self.assertEqual(r.run(3201), (21000, 1, False))          # x >= on: READY with U_on of set 1
        self.assertEqual(r.run(3205), (21000, 1, False))          # READY below W: holds U_on
        u, state, active = r.run(3216)                            # x >= W: ACTIVE, first law step at once
        want = law(21000, -5, [0] * 20)
        self.assertAlmostEqual(u, want)
        self.assertEqual((state, active), (2, True))
        self.assertEqual(r.run(3216)[0], u)                       # async run: no law step
        self.assertEqual(r.run(3216, timer=True)[0], u)           # divider 1: first timer run only counts
        u2 = r.run(3216, timer=True)[0]                           # second timer run: law step
        self.assertAlmostEqual(u2, law(u, -5, [-5] + [0] * 19))
        u3 = r.run(3213, timer=True)[0]                           # counts
        self.assertEqual(u3, u2)
        u4 = r.run(3213, timer=True)[0]                           # step inside the deadband (|e| = 2 < 4): U holds
        self.assertEqual(u4, u2)
        self.assertEqual(r.pid.buf[:3], [-2, -5, -5])             # but the history moved on
        self.assertEqual(r.run(3185), (30000, 0, False))          # below off: OFF with U_off

    def test_invalid_x_outputs_u_off_and_keeps_u(self):
        r = Rig()
        r.run(3201)
        u, _, _ = r.run(3216)
        self.assertEqual(r.run(None), (30000, 0, False))
        self.assertAlmostEqual(r.run(3190)[0], u)                 # quirk: OFF in the band, last U comes back

    def test_invalid_selector_uses_default_set(self):
        r = Rig()
        self.assertEqual(r.run(3201, sel=None), (18000, 1, False))  # default set 0 has U_on 18000
        self.assertEqual(r.pid.cur, 0)

    def test_set_switch_to_lower_setpoint(self):
        r = Rig()
        r.run(3201)
        u, _, _ = r.run(3216)
        r.pid.buf[1] = 7
        u_after, state, _ = r.run(3216, sel=2)                    # W 48 -> 45 °C
        self.assertEqual(r.pid.cur, 2)
        self.assertEqual(r.pid.buf[:2], [-5 - 30, 7 - 30])
        self.assertAlmostEqual(u_after, u)                        # U <= new U_off: unchanged; async: no step
        self.assertEqual(state, 2)

    def test_set_switch_to_higher_setpoint(self):
        r = Rig()
        r.run(3100, sel=2)                                        # set 2, OFF, U = 30000
        u, state, _ = r.run(3205, sel=1)                          # W 45 -> 48; x < 48 and > 47 -> U_on, then READY
        self.assertEqual(r.pid.cur, 1)
        self.assertEqual((u, state), (21000, 1))

    def test_same_setpoint_only_switches(self):
        r = Rig()
        r.run(3201, sel=1)
        r.run(3216, sel=1)
        buf = list(r.pid.buf)
        r.run(3216, sel=0)
        self.assertEqual(r.pid.cur, 0)
        self.assertEqual(r.pid.buf, buf)

    def test_unknown_stage_keeps_set(self):
        r = Rig()
        r.run(3100, sel=2)
        r.run(3100, sel=9)
        self.assertEqual(r.pid.cur, 2)

    def test_reset(self):
        r = Rig()
        r.run(3201)
        r.run(3216)
        r.run(3216, timer=True)                                   # counter = 1
        r.eng.reset()
        self.assertEqual((r.eng.node(1), r.pid.state, r.pid.u), (30000, 0, 30000))
        self.assertEqual(r.pid.counter, 1)                        # kept [C]
        self.assertEqual(r.pid.cur, 1)

    def test_initial_u_is_zero(self):
        r = Rig()
        self.assertEqual(r.run(3190), (0, 0, False))              # [C] zeroed memory at boot

    def test_divider_zero_steps_every_timer_run(self):
        r = Rig(divider=0)
        r.run(3201)
        u = r.run(3216)[0]
        self.assertNotEqual(r.run(3216, timer=True)[0], u)


class DirectTest(unittest.TestCase):
    def test_off_to_active_directly(self):
        r = Rig("direct")
        self.assertEqual(r.run(3216), (21000, 2, False))         # U_on, no increment; timer not yet requested
        self.assertEqual(r.pid.buf, [0] * 20)
        self.assertEqual(r.run(3216), (21000, 2, True))          # next run requests the timer
        r.run(3216, timer=True)
        u = r.run(3216, timer=True)[0]
        self.assertAlmostEqual(u, law(21000, -5, [0] * 20))

    def test_ready_path(self):
        r = Rig("direct")
        self.assertEqual(r.run(3205), (21000, 1, False))         # on <= x < W: READY with U_on
        self.assertEqual(r.run(3190), (21000, 1, False))         # off <= x < W: holds
        self.assertEqual(r.run(3216), (21000, 2, True))          # READY -> ACTIVE without step: U_on
        self.assertEqual(r.run(3180), (30000, 0, False))         # below off

    def test_off_below_on_gives_u_off(self):
        r = Rig("direct")
        self.assertEqual(r.run(3195), (30000, 0, False))         # differs from classic


class ParseTest(unittest.TestCase):
    def test_window_limits(self):
        r = Rig(ctl_params=[500, 3, 15, 4, 0])
        self.assertEqual(r.pid.n, 300)
        r = Rig(ctl_params=[1, 3, 15, 4, 0])
        self.assertEqual(r.pid.n, 2)

    def test_checks(self):
        bad_ctl = ([20, 3, 0, 4, 0], [20, 3, 15, -1, 0], [20, 0, 15, 4, 0], [20, 3, 15, 4, 0, 0], [20, 3, 15, 4, 0, 3],
                   [20, 3, 15, 4, 7])
        for params in bad_ctl:
            with self.subTest(ctl=params), self.assertRaises(BlobError):
                build(pid_blob(ctl_params=params))
        good = [1, 0, 0, 30000, 30001, 7500, 21000, W1, 10, 40, 100, 0.4]
        for i, value in [(6, 31000), (6, 7000), (5, 30001), (3, 30002), (9, -1), (8, -1)]:
            row = list(good)
            row[i] = value
            with self.subTest(index=i, value=value), self.assertRaises(BlobError):
                build(pid_blob(n_sets=1, set_rows=[row]))

    def test_needs_two_inputs(self):
        b = BlobBuilder()
        b.line(G["pid_sets"], 1, [(TEMP, 509)], [(VN, 1)], [20, 1, 15, 4, 0])
        b.line(G["pid_parameter_set"], 1, [], [], [1, 0, 0, 30000, 30001, 7500, 21000, W1, 10, 40, 100, 0.4])
        with self.assertRaises(BlobError):
            build(b)

    def test_controller_without_sets(self):
        b = BlobBuilder()
        b.line(G["pid_sets"], 1, [(TEMP, 509), (SYSTEM, 1)], [(VN, 1)], [20, 1, 15, 4, 0])
        with self.assertRaises(BlobError):
            build(b)

    def test_mode_column(self):
        # mode 1/2 parse, but are not implemented: the blob must not be used for control.
        b = pid_blob(ctl_params=[20, 3, 15, 4, 0, 1])
        b.line(G["pass_through"], 2, [(VN, 1)], [(LIMITS, 51)], [15000], stage=1)
        graph, _ = build(b)
        self.assertFalse(graph.usable_for_control)
        self.assertIn("mode 1", graph.unsupported[0].reason)


if __name__ == "__main__":
    unittest.main()
