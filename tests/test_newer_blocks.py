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


if __name__ == "__main__":
    unittest.main()
