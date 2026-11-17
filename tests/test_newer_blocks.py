"""Unit tests for the blocks of newer blobs (spec engine 5). Synthetic parameters only."""
import unittest

from helpers import SYSTEM, TEMP, VN, BlobBuilder, G, build, ref

from surface_power.loader.errors import BlobError

X = (SYSTEM, 1)
Y = (TEMP, 509)


def single(guid, inputs, params):
    b = BlobBuilder()
    b.line(guid, 1, inputs, [(VN, 1)], params)
    return build(b)


def run(eng, values):
    eng.set_inputs({ref(*k): v for k, v in values.items()})
    eng.evaluate(1, timer=False, now_ms=0)
    return eng.node(1)


class ConditionalTableTest(unittest.TestCase):
    # 3 rows, inputs X and Y, default 7:
    #   row 0: X in [0, 10], Y == 2      -> 100
    #   row 1: X in [0, 10], Y in [3, 5] -> 200
    #   row 2: X in [10, 20], Y in [0, 9] -> 300
    PARAMS = [3, 7,
              0, 0, 10,  10, 10, 20,    # lo, hi of X
              2, 3, 0,   2, 5, 9,       # lo, hi of Y
              100, 200, 300]

    def setUp(self):
        _, self.eng = single(G["conditional_table"], [X, Y], self.PARAMS)

    def test_first_matching_row(self):
        for x, y, out in [(5, 2, 100), (5, 4, 200), (10, 2, 100), (10, 4, 200), (15, 4, 300), (20, 9, 300)]:
            with self.subTest(x=x, y=y):
                self.assertEqual(run(self.eng, {X: x, Y: y}), out)

    def test_no_match_gives_default_then_holds_last_value(self):
        self.assertEqual(run(self.eng, {X: 30, Y: 4}), 7)
        self.assertEqual(run(self.eng, {X: 15, Y: 4}), 300)
        self.assertEqual(run(self.eng, {X: 30, Y: 4}), 300)
        self.assertEqual(run(self.eng, {X: 5, Y: 6}), 300)

    def test_equal_bounds_compare_with_tolerance(self):
        self.assertEqual(run(self.eng, {X: 5, Y: 2.0009}), 100)
        self.assertEqual(run(self.eng, {X: 5, Y: 1.9991}), 100)
        self.assertEqual(run(self.eng, {X: 5, Y: 2.5}), 100)      # no row matches: holds 100
        self.assertEqual(run(self.eng, {X: 15, Y: 2.5}), 300)

    def test_tolerance_is_strict(self):
        _, eng = single(G["conditional_table"], [X], [1, 7, 2, 2, 100])
        self.assertEqual(run(eng, {X: 2.002}), 7)
        self.assertEqual(run(eng, {X: 2.0}), 100)

    def test_invalid_input_takes_part_with_last_value(self):
        self.assertEqual(run(self.eng, {X: 5, Y: 4}), 200)
        self.assertEqual(run(self.eng, {X: 15, Y: None}), 300)   # Y keeps 4
        self.assertTrue(self.eng.graph.nodes[1].valid)

    def test_input_never_set_counts_as_zero(self):
        self.assertEqual(run(self.eng, {X: 15}), 300)             # Y is 0 and invalid

    def test_checks(self):
        cases = {
            "lo > hi": [1, 0, 5, 4, 1],
            "too few": [2, 0, 0, 0, 1, 1, 9],
            "too many": [1, 0, 0, 1, 9, 9],
            "N = 0": [0, 0],
        }
        for name, params in cases.items():
            with self.subTest(name), self.assertRaises(BlobError):
                single(G["conditional_table"], [X], params)

    def test_counts_as_supported(self):
        graph, _ = single(G["conditional_table"], [X, Y], self.PARAMS)
        self.assertEqual(graph.unsupported, [])


class HysteresisTableTest(unittest.TestCase):
    # 3 bands: [10, 20] -> 1, [15, 30] -> 2, [25, 40] -> 3; below 10 -> 0, above 40 -> 9.
    PARAMS = [3, 0, 9, 10, 20, 15, 30, 25, 40, 1, 2, 3]

    def setUp(self):
        _, self.eng = single(G["hysteresis_table"], [X], self.PARAMS)

    def seq(self, xs):
        return [run(self.eng, {X: x}) for x in xs]

    def test_rising_and_falling(self):
        self.assertEqual(self.seq([12, 19, 20, 22, 30, 35, 40, 41]), [1, 1, 2, 2, 3, 3, 9, 9])
        self.assertEqual(self.seq([36, 26, 25, 24, 15, 14, 10, 9]), [3, 3, 2, 2, 1, 1, 0, 0])

    def test_overlap_holds(self):
        self.assertEqual(self.seq([12, 17, 18]), [1, 1, 1])     # 17, 18 lie inside the overlap (15, 20)
        self.assertEqual(self.seq([22, 17, 16]), [2, 2, 2])

    def test_first_value_below_the_bands(self):
        self.assertEqual(self.seq([5]), [0])

    def test_first_value_inside_rises_from_zero(self):
        self.assertEqual(self.seq([22]), [2])                   # last x starts at 0, so 22 counts as rising past up[0]

    def test_same_value_holds(self):
        self.assertEqual(self.seq([22, 22.0005]), [2, 2])

    def test_invalid_input_writes_nothing(self):
        graph, eng = single(G["hysteresis_table"], [X], self.PARAMS)
        eng.set_inputs({ref(*X): None})
        eng.evaluate(1, timer=False, now_ms=0)
        self.assertEqual(eng.node(1), 0)
        self.assertEqual(run(eng, {X: 35}), 3)
        self.assertEqual(run(eng, {X: None}), 3)

    def test_checks(self):
        cases = {
            "down > up": [1, 0, 9, 5, 4, 1],
            "down below L": [2, 0, 9, 10, 20, 5, 30, 1, 2],
            "up above H": [2, 0, 9, 10, 50, 15, 30, 1, 2],
            "gap between bands": [2, 0, 9, 10, 20, 25, 30, 1, 2],
            "count": [2, 0, 9, 10, 20, 15, 30, 1],
            "N = 0": [0, 0, 9],
        }
        for name, params in cases.items():
            with self.subTest(name), self.assertRaises(BlobError):
                single(G["hysteresis_table"], [X], params)


class HysteresisQuantiserTest(unittest.TestCase):
    PARAMS = [100, 0, 10, 0, 5]   # max, min, step, unused, default

    def setUp(self):
        _, self.eng = single(G["hysteresis_quantiser"], [X], self.PARAMS)

    def test_trace(self):
        trace = [(33, 40), (35, 40), (41, 40), (52, 50), (47, 50), (38, 40), (150, 100), (95, 100), (-5, 0),
                 (None, 5), (33, 30)]
        for x, out in trace:
            with self.subTest(x=x):
                self.assertEqual(run(self.eng, {X: x}), out)

    def test_invalid_first_gives_default(self):
        self.assertEqual(run(self.eng, {X: None}), 5)

    def test_checks(self):
        for params in ([100, 0, 0, 0, 5], [100, 0, -1, 0, 5], [0, 100, 10, 0, 5], [100, 0, 10, 0, 101],
                       [100, 0, 10, 0, -1], [100, 0, 10, 5]):
            with self.subTest(params=params), self.assertRaises(BlobError):
                single(G["hysteresis_quantiser"], [X], params)


class MultiplyDivideTest(unittest.TestCase):
    def test_product_and_quotient(self):
        _, eng = single(G["multiply_divide"], [X, Y], [7, 1000, -1000, 0, 0])
        self.assertEqual(run(eng, {X: 3, Y: 4}), 12)
        _, eng = single(G["multiply_divide"], [X, Y], [7, 1000, -1000, 1, 0])
        self.assertEqual(run(eng, {X: 4, Y: 10}), 2.5)

    def test_clamped(self):
        _, eng = single(G["multiply_divide"], [X, Y], [50, 100, 10, 0, 0])
        self.assertEqual(run(eng, {X: 30, Y: 4}), 100)
        self.assertEqual(run(eng, {X: 1, Y: 4}), 10)

    def test_near_zero_divisor(self):
        _, eng = single(G["multiply_divide"], [X, Y], [7, 1e9, -1e9, 1, 0])
        self.assertEqual(run(eng, {X: 0.0005, Y: 2}), 2000)
        self.assertEqual(run(eng, {X: -0.0005, Y: 2}), -2000)
        self.assertEqual(run(eng, {X: 0, Y: 2}), 2000)

    def test_invalid_input_gives_default(self):
        _, eng = single(G["multiply_divide"], [X, Y], [50, 100, 10, 0, 0])
        self.assertEqual(run(eng, {X: 30, Y: None}), 50)

    def test_checks(self):
        for params in ([7, 10, 100, 0, 0], [7, 5, 0, 0, 0], [7, 100, 10, 0, 0], [7, 100, 0, 0]):
            with self.subTest(params=params), self.assertRaises(BlobError):
                single(G["multiply_divide"], [X, Y], params)


class EwmaTest(unittest.TestCase):
    PARAMS = [1000, -1000, 5000, 10]   # max, min, tau_ms, default

    def setUp(self):
        _, self.eng = single(G["ewma"], [X], self.PARAMS)

    def at(self, t, x):
        self.eng.set_inputs({ref(*X): x})
        self.eng.evaluate(1, timer=True, now_ms=t)
        return self.eng.node(1)

    def test_first_run_keeps_default_then_smooths(self):
        self.assertEqual(self.at(1000, 110), 10)
        self.assertEqual(self.at(2000, 110), 30)       # 10 + 1/5 * 100
        self.assertEqual(self.at(4500, 110), 70)       # 30 + 1/2 * 80

    def test_long_gap_takes_the_input(self):
        self.at(0, 110)
        self.assertEqual(self.at(5000, 110), 110)
        self.assertEqual(self.at(20000, -50), -50)

    def test_clamped_output_unclamped_state(self):
        _, eng = single(G["ewma"], [X], [100, 0, 1000, 0])
        for t, x, out in [(0, 500, 0), (2000, 500, 100), (2500, 0, 100)]:   # state 500 -> 250 after half a tau
            eng.set_inputs({ref(*X): x})
            eng.evaluate(1, timer=True, now_ms=t)
            self.assertEqual(eng.node(1), out)

    def test_invalid_gives_default_and_keeps_state(self):
        self.at(0, 110)
        self.at(5000, 110)
        self.assertEqual(self.at(6000, None), 10)
        self.assertEqual(self.at(7000, 60), 110 + 2000 / 5000 * (60 - 110))   # last update was at 5000

    def test_only_first_input_counts(self):
        _, eng = single(G["ewma"], [X, Y], self.PARAMS)
        eng.set_inputs({ref(*X): 110, ref(*Y): -1000})
        eng.evaluate(1, timer=True, now_ms=0)
        eng.evaluate(1, timer=True, now_ms=10000)
        self.assertEqual(eng.node(1), 110)

    def test_checks(self):
        for params in ([0, 100, 5000, 50], [100, 0, 5000, 101], [100, 0, 0, 50], [100, 0, 120001, 50], [100, 0, 5000]):
            with self.subTest(params=params), self.assertRaises(BlobError):
                single(G["ewma"], [X], params)


class DynamicParameterTest(unittest.TestCase):
    """Parameters with channel references, evaluated on every run (spec engine 3.6)."""

    def lookup_blob(self, ref_text):
        b = BlobBuilder()
        # N=1, below 100, above 50, band [W+30, W+45] -> 50; W is VN2, written later in the same run.
        b.line(G["lookup_table"], 1, [X], [(VN, 1)], [1, 100, 50, f"({ref_text}+30)", f"({ref_text}+45)", 50])
        b.line(G["pass_through"], 2, [Y], [(VN, 2)], [0], stage=1)
        return build(b)

    def test_bounds_follow_the_node(self):
        graph, eng = self.lookup_blob(f"{{{VN}}}_2")
        self.assertTrue(graph.usable_for_control)
        self.assertEqual(graph.unsupported, [])
        # First run: VN2 is still 0, so the band is [30, 45].
        self.assertEqual(run(eng, {X: 40, Y: 3000}), 50)
        # From now on VN2 = 3000: the band is [3030, 3045].
        self.assertEqual(run(eng, {X: 40, Y: 3000}), 100)
        self.assertEqual(run(eng, {X: 3040, Y: 3000}), 50)
        self.assertEqual(run(eng, {X: 3050, Y: 3000}), 50)     # above the band
        self.assertEqual(run(eng, {X: 3010, Y: 3000}), 100)

    def test_invalid_channel_skips_the_block(self):
        graph, eng = self.lookup_blob(f"{{{TEMP}}}_505")
        self.assertIn(ref(TEMP, 505), graph.inputs)
        self.assertEqual(run(eng, {X: 40, Y: 0}), 0)            # 505 invalid: nothing written, VN1 stays 0
        eng.set_inputs({ref(TEMP, 505): 10})
        self.assertEqual(run(eng, {X: 45}), 50)
        eng.set_inputs({ref(TEMP, 505): None})
        self.assertEqual(run(eng, {X: 0}), 50)                  # skipped again: keeps 50, not the 'below' 100

    def test_division_by_zero_at_run_time_skips(self):
        b = BlobBuilder()
        b.line(G["multiply_divide"], 1, [X], [(VN, 1)], [f"(100/{{{VN}}}_2)", 1000, 0, 0])
        graph, eng = build(b)
        self.assertEqual(run(eng, {X: None}), 0)                # default is 100/0: the block is skipped

    def test_reach_follows_expression_references(self):
        """A node that only appears in a parameter expression still carries its input to the output (graph reach analysis)."""
        from helpers import LIMITS
        from surface_power.engine.graph import outputs_reached_by
        out = (LIMITS, 51)
        b = BlobBuilder()
        b.line(G["pass_through"], 2, [Y], [(VN, 2)], [0])
        b.line(G["lookup_table"], 1, [X], [out], [1, 100, 50, f"({{{VN}}}_2+30)", f"({{{VN}}}_2+45)", 50], stage=1)
        graph, _ = build(b)
        self.assertEqual(outputs_reached_by(ref(*Y), graph.parsed.algorithms), [ref(*out)])
        self.assertEqual(outputs_reached_by(ref(*X), graph.parsed.algorithms), [ref(*out)])

    def test_other_blocks_stay_unsupported(self):
        b = BlobBuilder()
        b.line(G["conditional_table"], 1, [X], [(VN, 1)], [1, f"({{{VN}}}_2)", 0, 9, 5])
        graph, _ = build(b)
        self.assertEqual(len(graph.unsupported), 1)


class VariablePidTest(unittest.TestCase):
    # N, U_max, U_min, W, K_e, K_d, K_m, deadband, dW_on, off hysteresis, U_on, U_off
    PARAMS = [2, 100, 10, 50, 1, 0, 0, 0, 5, 5, 80, 90]
    E = (TEMP, 505)

    def setUp(self):
        self.graph, self.eng = single(G["variable_pid"], [X, Y], self.PARAMS)

    def at(self, values, timer=True):
        self.eng.set_inputs({ref(*k): v for k, v in values.items()})
        self.eng.evaluate(1, timer=timer, now_ms=0)
        return self.eng.node(1)

    def test_setpoint_from_input_or_default(self):
        self.assertEqual(self.at({X: 40, Y: None}), 90)       # W = 50 (default): below W - dW_on -> U_off
        self.assertEqual(self.at({X: 52}), 80)                # x >= W: ACTIVE with U_on, no increment (direct)
        self.assertEqual(self.at({X: 52}), 78)                # law step: e = 50 - 52
        self.assertEqual(self.at({X: 52, Y: 60}), 86)         # W from input 2: e = 60 - 52
        self.assertEqual(self.at({X: None}), 90)              # x invalid -> OFF, U_off

    def test_async_run_does_no_step(self):
        self.at({X: 52, Y: None})
        self.assertEqual(self.at({X: 53}, timer=False), 80)

    def test_enable_input(self):
        graph, eng = single(G["variable_pid"], [X, Y, self.E], self.PARAMS)
        eng.set_inputs({ref(*X): 52, ref(*Y): 50, ref(*self.E): 1})
        eng.evaluate(1, timer=True, now_ms=0)
        self.assertEqual(eng.node(1), 90)
        eng.set_inputs({ref(*self.E): 2})
        eng.evaluate(1, timer=True, now_ms=0)
        self.assertEqual(eng.node(1), 80)

    def test_is_supported_and_counts_as_controller(self):
        self.assertEqual(self.graph.unsupported, [])
        self.assertEqual(len(self.graph.controllers), 1)

    def test_parameter_sets_do_not_attach(self):
        b = BlobBuilder()
        b.line(G["variable_pid"], 1, [X, Y], [(VN, 1)], self.PARAMS)
        b.line(G["pid_parameter_set"], 2, [], [], [1, 0, 0, 90, 100, 10, 80, 50, 5, 1, 0, 0])
        graph, _ = build(b)
        self.assertTrue(any("unknown controller id 1" in w for _, w in graph.warnings))

    def test_checks(self):
        bad = {
            "negative gain": [2, 100, 10, 50, -1, 0, 0, 0, 5, 5, 80, 90],
            "hysteresis 0": [2, 100, 10, 50, 1, 0, 0, 0, 5, 0, 80, 90],
            "deadband too large": [2, 100, 10, 50, 1, 0, 0, 10, 5, 5, 80, 90],
            "U_on > U_off": [2, 100, 10, 50, 1, 0, 0, 0, 5, 5, 95, 90],
            "U_off > U_max": [2, 100, 10, 50, 1, 0, 0, 0, 5, 5, 80, 110],
            "mode 0 column": [2, 100, 10, 50, 1, 0, 0, 0, 5, 5, 80, 90, 0],
            "count": [2, 100, 10, 50, 1, 0, 0, 0, 5, 5, 80],
        }
        for name, params in bad.items():
            with self.subTest(name), self.assertRaises(BlobError):
                single(G["variable_pid"], [X, Y], params)
        with self.assertRaises(BlobError):
            single(G["variable_pid"], [X], self.PARAMS)

    def test_positional_modes_are_unsupported(self):
        graph, _ = single(G["variable_pid"], [X, Y], self.PARAMS + [1])
        self.assertEqual(len(graph.unsupported), 1)


class WeightedMeanTest(unittest.TestCase):
    def test_mean_with_offset_and_clamp(self):
        _, eng = single(G["weighted_mean"], [X, Y], [100, 0, 50, 2, 0, 1, 3])
        self.assertEqual(run(eng, {X: 10, Y: 30}), 27)          # (10 + 90) / 4 + 2
        self.assertEqual(run(eng, {X: 200, Y: 200}), 100)

    def test_invalid_input(self):
        _, eng = single(G["weighted_mean"], [X, Y], [100, 0, 50, 0, 0, 1, 1])
        self.assertEqual(run(eng, {X: 10, Y: None}), 50)        # strict 0: default
        _, eng = single(G["weighted_mean"], [X, Y], [100, 0, 50, 0, 1, 1, 1])
        self.assertEqual(run(eng, {X: 10, Y: 30}), 20)
        self.assertEqual(run(eng, {X: 20, Y: None}), 25)        # strict 1: Y keeps 30

    def test_zero_weights_give_default(self):
        _, eng = single(G["weighted_mean"], [X, Y], [100, 0, 50, 0, 0, 0, 0])
        self.assertEqual(run(eng, {X: 10, Y: 30}), 50)

    def test_checks(self):
        for params in ([0, 100, 50, 0, 0, 1, 1], [100, 0, 101, 0, 0, 1, 1], [100, 0, 50, 0, 0, 1]):
            with self.subTest(params=params), self.assertRaises(BlobError):
                single(G["weighted_mean"], [X, Y], params)


if __name__ == "__main__":
    unittest.main()
