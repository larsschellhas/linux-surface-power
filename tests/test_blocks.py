"""Unit tests for the stateless blocks (spec engine 4; AC-E1, AC-E6) and the graph build (blob-format 6, 7)."""
import unittest

from helpers import LIMITS, SYSTEM, TEMP, VN, BlobBuilder, G, build, ref

from surface_power.engine.helpers import clamp, round_half_up
from surface_power.loader.errors import BlobError

X = (SYSTEM, 1)
Y = (TEMP, 509)
Z = (TEMP, 505)


def single(guid, inputs, params, n_inputs_valid=True):
    """A blob with one block writing VN1."""
    b = BlobBuilder()
    b.line(guid, 1, inputs, [(VN, 1)], params)
    graph, eng = build(b)
    return graph, eng


def run(eng, values, instance=1):
    eng.set_inputs({ref(*k): v for k, v in values.items()})
    eng.evaluate(instance, timer=False, now_ms=0)
    return eng.node(1)


class HelperTest(unittest.TestCase):
    def test_clamp_checks_upper_first(self):
        self.assertEqual(clamp(5, 10, 0), 5)
        self.assertEqual(clamp(11, 10, 0), 10)
        self.assertEqual(clamp(-1, 10, 0), 0)
        self.assertEqual(clamp(5, 0, 10), 0)    # lo > hi: above hi -> hi
        self.assertEqual(clamp(-5, 0, 10), 10)
        self.assertEqual(clamp(20, 0, 10), 0)

    def test_round_half_up_truncates(self):
        self.assertEqual(round_half_up(0.49), 0)
        self.assertEqual(round_half_up(0.5), 1)
        self.assertEqual(round_half_up(2.5), 3)
        self.assertEqual(round_half_up(-0.7), 0)
        self.assertEqual(round_half_up(-1.0), 0)
        self.assertEqual(round_half_up(-1.6), -1)


class LookupTableTest(unittest.TestCase):
    def setUp(self):
        _, self.eng = single(G["lookup_table"], [X], [3, -1, 99, 0, 10, 20, 5, 15, 30, 1, 2, 3])

    def test_values(self):
        for x, y in [(-0.1, -1), (0, 1), (5, 1), (7, -1), (10, 2), (15, 2), (16, -1), (20, 3), (30, 3), (30.1, 99)]:
            with self.subTest(x=x):
                self.assertEqual(run(self.eng, {X: x}), y)

    def test_invalid_input_gives_below(self):
        self.assertEqual(run(self.eng, {X: None}), -1)

    def test_checks(self):
        for params in ([2, 0, 0, 5, 0, 1, 9, 1, 2],      # lo > hi
                       [2, 0, 0, 0, 5, 6, 9, 1, 2],      # hi[0] > lo[1]
                       [2, 0, 0, 0, 5, 5, 9, 1],         # one column missing
                       [0, 0, 0]):                      # N = 0
            with self.subTest(params=params), self.assertRaises(BlobError):
                single(G["lookup_table"], [X], params)

    def test_touching_intervals_allowed(self):
        _, eng = single(G["lookup_table"], [X], [2, 0, 0, 0, 5, 5, 9, 1, 2])
        self.assertEqual(run(eng, {X: 5}), 1)  # the first matching interval wins

    def test_needs_one_input(self):
        with self.assertRaises(BlobError):
            single(G["lookup_table"], [X, Y], [1, 0, 0, 0, 1, 1])


class MinMaxTest(unittest.TestCase):
    def test_min_and_clamp(self):
        _, eng = single(G["min_max"], [X, Y, Z], [100, 10, 50, 0])
        self.assertEqual(run(eng, {X: 30, Y: 20, Z: 40}), 20)
        self.assertEqual(run(eng, {X: 30, Y: 5, Z: 40}), 10)
        self.assertEqual(run(eng, {X: 30, Y: None, Z: 40}), 50)

    def test_max(self):
        _, eng = single(G["min_max"], [X, Y], [100, 10, 50, 1])
        self.assertEqual(run(eng, {X: 30, Y: 20}), 30)
        self.assertEqual(run(eng, {X: 300, Y: 20}), 100)

    def test_checks(self):
        for params in ([10, 100, 50, 0], [100, 10, 150, 0], [100, 10, 5, 0], [100, 10, 50]):
            with self.subTest(params=params), self.assertRaises(BlobError):
                single(G["min_max"], [X, Y], params)


class AddSubScaleTest(unittest.TestCase):
    def test_strict(self):
        _, eng = single(G["add_sub_scale"], [X, Y, Z], [0, 2, 1000, -1000, 6, 2, 7])
        self.assertEqual(run(eng, {X: 10, Y: 20, Z: 4}), 16)          # (10 + 20 - 4 + 6) / 2
        self.assertEqual(run(eng, {X: 3000, Y: 0, Z: 0}), 1000)
        self.assertEqual(run(eng, {X: 10, Y: None, Z: 4}), 7)          # default

    def test_lenient_uses_last_value_of_invalid_input(self):
        _, eng = single(G["add_sub_scale"], [X, Y], [1, 1, 1000, -1000, 0, 1, 7])
        self.assertEqual(run(eng, {X: 10, Y: 4}), 6)
        self.assertEqual(run(eng, {X: 10, Y: None}), 6)                # Y keeps 4

    def test_checks(self):
        for params in ([0, 3, 100, 0, 0, 1, 5],     # k > n_in
                       [0, 1, 100, 0, 0, 0, 5],     # divisor 0
                       [0, 1, 100, 0, 0, 1, 500],   # default > max
                       [0, 1, 100, 0, 0, 1, -5]):   # default < min
            with self.subTest(params=params), self.assertRaises(BlobError):
                single(G["add_sub_scale"], [X, Y], params)


class MultiplexerTest(unittest.TestCase):
    def setUp(self):
        _, self.eng = single(G["multiplexer"], [X, Y, Z], [1000, 0, 500])

    def test_select(self):
        for sel, want in [(0, 11), (0.49, 11), (0.5, 22), (1, 22), (2, 500), (-0.7, 11), (-1.0, 11), (-1.6, 500)]:
            with self.subTest(sel=sel):
                self.assertEqual(run(self.eng, {X: sel, Y: 11, Z: 22}), want)

    def test_clamps_selected_value(self):
        self.assertEqual(run(self.eng, {X: 1, Y: 11, Z: 2000}), 1000)

    def test_invalid(self):
        self.assertEqual(run(self.eng, {X: None, Y: 11, Z: 22}), 500)
        self.assertEqual(run(self.eng, {X: 1, Y: 11, Z: None}), 500)

    def test_needs_three_inputs(self):
        with self.assertRaises(BlobError):
            single(G["multiplexer"], [X, Y], [1000, 0, 500])


class PassThroughTest(unittest.TestCase):
    def test_values(self):
        _, eng = single(G["pass_through"], [X], [25])
        self.assertEqual(run(eng, {X: 99999}), 99999)   # not clamped
        self.assertEqual(run(eng, {X: None}), 25)

    def test_exactly_one_parameter(self):
        with self.assertRaises(BlobError):
            single(G["pass_through"], [X], [25, 1])


class LinearMapTest(unittest.TestCase):
    def test_linear(self):
        _, eng = single(G["linear_map"], [X], [15000, 0, 0.00986, 5, 3000, 0])
        self.assertAlmostEqual(run(eng, {X: 400000}), 0.00986 * 400000 + 5)
        self.assertEqual(run(eng, {X: 2e9}), 15000)
        self.assertEqual(run(eng, {X: None}), 3000)

    def test_reciprocal(self):
        _, eng = single(G["linear_map"], [X], [1e9, -1e9, 2, 1, 0, 1])
        self.assertEqual(run(eng, {X: 4}), 2 / 4 + 1)
        self.assertEqual(run(eng, {X: 0}), 2 / 0.001 + 1)
        self.assertEqual(run(eng, {X: -0.0005}), 2 / -0.001 + 1)


class WindowComparatorTest(unittest.TestCase):
    def test_kinds(self):
        _, eng = single(G["window_comparator"], [X, Y], [1, 3, 50, 10, 0, 20])
        self.assertEqual(run(eng, {X: 49, Y: 15}), 2)    # neither
        self.assertEqual(run(eng, {X: 50, Y: 15}), 1)    # kind 1: x >= a
        self.assertEqual(run(eng, {X: 0, Y: 25}), 1)     # kind 3: outside [10, 20]
        self.assertEqual(run(eng, {X: 0, Y: None}), 1)   # invalid -> 1

    def test_kind_2_and_unknown_kind(self):
        _, eng = single(G["window_comparator"], [X, Y], [2, 4, 50, 0, 0, 0])
        self.assertEqual(run(eng, {X: 50, Y: 0}), 1)     # kind 2: x <= a
        self.assertEqual(run(eng, {X: 51, Y: 0}), 2)     # kind 4 is stored as 0: never true

    def test_window_check(self):
        with self.assertRaises(BlobError):
            single(G["window_comparator"], [X], [3, 20, 10])


class GraphTest(unittest.TestCase):
    def test_unwritten_node_is_valid_zero(self):
        b = BlobBuilder()
        b.line(G["pass_through"], 1, [(VN, 99)], [(VN, 1)], [25])
        graph, eng = build(b)
        self.assertEqual(run(eng, {}), 0)
        self.assertTrue(any("VN99" in w for _, w in graph.warnings))

    def test_stage_order(self):
        b = BlobBuilder()
        b.line(G["pass_through"], 1, [(VN, 5)], [(VN, 1)], [0], stage=1)   # reads VN5, written later in the file
        b.line(G["pass_through"], 2, [X], [(VN, 5)], [0], stage=0)
        _, eng = build(b)
        self.assertEqual(run(eng, {X: 7}), 7)

    def test_file_order_within_stage(self):
        b = BlobBuilder()
        b.line(G["pass_through"], 1, [(VN, 5)], [(VN, 1)], [0])
        b.line(G["pass_through"], 2, [X], [(VN, 5)], [0])
        _, eng = build(b)
        self.assertEqual(run(eng, {X: 7}), 0)  # sees the previous value
        self.assertEqual(run(eng, {X: 8}), 7)

    def test_device_slots_and_output_ranges(self):
        b = BlobBuilder()
        b.output_channel(LIMITS, 51, 25000, 31000, 7500)
        b.line(G["pass_through"], 1, [X], [(LIMITS, 51)], [25000])
        graph, eng = build(b)
        self.assertEqual(set(graph.inputs), {ref(*X)})
        self.assertEqual(set(graph.outputs), {ref(LIMITS, 51)})
        eng.set_inputs({ref(*X): 20000})
        eng.evaluate(1, False, 0)
        self.assertEqual(eng.outputs(), {ref(LIMITS, 51): 20000})
        self.assertEqual(graph.output_ranges[ref(LIMITS, 51)].max, 31000)

    def test_unknown_guid_on_output_path_blocks_control(self):
        b = BlobBuilder()
        b.line("12345678-0000-0000-0000-000000000000", 1, [X], [(VN, 7)], [1, 2])
        b.line(G["pass_through"], 2, [(VN, 7)], [(LIMITS, 51)], [0], stage=1)
        graph, _ = build(b)
        self.assertFalse(graph.usable_for_control)
        self.assertEqual(graph.unsupported[0].reaches, [ref(LIMITS, 51)])

    def test_unknown_guid_off_path_is_a_warning(self):
        b = BlobBuilder()
        b.line("12345678-0000-0000-0000-000000000000", 1, [X], [(VN, 7)], [1, 2])
        b.line(G["pass_through"], 2, [X], [(LIMITS, 51)], [0])
        graph, _ = build(b)
        self.assertTrue(graph.usable_for_control)
        self.assertEqual(graph.unsupported[0].reaches, [])

    def test_dynamic_parameters_are_unsupported_for_now(self):
        b = BlobBuilder()
        b.line(G["pass_through"], 1, [X], [(LIMITS, 51)], [f"({{{VN}}}_3+1)"])
        graph, _ = build(b)
        self.assertFalse(graph.usable_for_control)
        self.assertIn("expression", graph.unsupported[0].reason)

    def test_parameter_error_names_the_line(self):
        b = BlobBuilder()
        b.line(G["pass_through"], 1, [X], [(VN, 1)], [5])
        b.line(G["min_max"], 2, [X], [(VN, 2)], [1, 2, 3])
        with self.assertRaises(BlobError) as cm:
            build(b)
        self.assertEqual(cm.exception.line, 3)  # 1-based, after the '$' line


if __name__ == "__main__":
    unittest.main()
