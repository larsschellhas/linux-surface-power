"""Tests for the blob and replay tools (spec tools 1, 2; AC-B3, AC-E9)."""
import contextlib
import io
import json
import os
import tempfile
import unittest

from helpers import LIMITS, SYSTEM, TEMP, VN, BlobBuilder, G, form_b

from surface_power.tools import blob_tool, replay


def sample_blob():
    b = BlobBuilder()
    b.input_channel(TEMP, 509, 3681, 2731)
    b.output_channel(LIMITS, 51, 25000, 31000, 7500)
    b.line(G["pid_sets"], 1, [(TEMP, 509), (SYSTEM, 1)], [(VN, 1)], [20, 1, 15, 4, 0], divider=1)
    b.line(G["pid_parameter_set"], 1, [], [], [1, 1, 1, 30000, 30001, 7500, 21000, 3211, 10, 40, 100, 0.4])
    b.line(G["lookup_table"], 2, [(SYSTEM, 1)], [(VN, 2)], [2, 25000, 25000, 1, 2, 1, 2, 25000, 30000])
    b.line(G["min_max"], 3, [(VN, 1), (VN, 2)], [(LIMITS, 51)], [30000, 8500, 15000, 0], stage=1)
    return b


def run_main(main, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class BlobToolTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "blob.bin")
        with open(self.path, "wb") as f:
            f.write(form_b(sample_blob().text()))

    def tearDown(self):
        self.dir.cleanup()

    def test_summary(self):
        code, out, _ = run_main(blob_tool.main, [self.path])
        self.assertEqual(code, 0)
        self.assertIn("form B", out)
        self.assertIn("pid_sets", out)
        self.assertIn("usable for control: yes", out)

    def test_graph_and_outputs(self):
        code, out, _ = run_main(blob_tool.main, [self.path, "--graph", "--outputs"])
        self.assertEqual(code, 0)
        self.assertIn("0B7B5350/51 <- min_max id 3", out)
        self.assertIn("2E8B14C2/509", out)

    def test_json(self):
        code, out, _ = run_main(blob_tool.main, [self.path, "--json"])
        data = json.loads(out)
        self.assertEqual(data["form"], "B")
        self.assertTrue(data["usable_for_control"])
        self.assertEqual(data["outputs"], ["0B7B5350/51"])

    def test_dot(self):
        code, out, _ = run_main(blob_tool.main, [self.path, "--dot"])
        self.assertTrue(out.startswith("digraph"))

    def test_unknown_guid_report(self):
        b = sample_blob()
        b.line("12345678-0000-0000-0000-000000000000", 9, [(VN, 2)], [(VN, 1)], [1])
        with open(self.path, "wb") as f:
            f.write(form_b(b.text()))
        code, out, _ = run_main(blob_tool.main, [self.path])
        self.assertEqual(code, 0)
        self.assertIn("usable for control: no", out)
        self.assertIn("0B7B5350/51", out)

    def test_bad_blob_exit_code(self):
        with open(self.path, "wb") as f:
            f.write(b"\x01\x02\x03" * 50)
        code, _, err = run_main(blob_tool.main, [self.path])
        self.assertEqual(code, 1)
        self.assertIn("unknown blob format", err)

    def test_missing_file_exit_code(self):
        code, _, _ = run_main(blob_tool.main, [os.path.join(self.dir.name, "nope")])
        self.assertEqual(code, 2)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.blob = os.path.join(self.dir.name, "blob.txt")
        with open(self.blob, "w") as f:
            f.write(sample_blob().text())
        self.inp = os.path.join(self.dir.name, "in.csv")
        rows = ["t_ms,2E8B14C2/509,2DD6960F/1,event"]
        t = 0
        for x in list(range(3190, 3230)) + [3225] * 30:
            rows.append(f"{t},{x},1,")
            t += 1000
        rows.append(f"{t},3225,1,reset")
        rows.append(f"{t + 1000},,1,")
        with open(self.inp, "w") as f:
            f.write("\n".join(rows) + "\n")

    def tearDown(self):
        self.dir.cleanup()

    def replay(self, *extra):
        out = os.path.join(self.dir.name, f"out{len(extra)}.csv")
        code, _, err = run_main(replay.main, [self.blob, self.inp, "--out", out, *extra])
        self.assertEqual(code, 0, err)
        with open(out) as f:
            return f.read()

    def test_output_format(self):
        text = self.replay()
        lines = text.splitlines()
        self.assertEqual(lines[0], "t_ms,kind,ctl1.state,ctl1.set,ctl1.u,0B7B5350/51")
        kinds = {line.split(",")[1] for line in lines[1:]}
        self.assertEqual(kinds, {"async", "timer", "reset"})
        self.assertIn(",READY,", text)
        self.assertIn(",ACTIVE,", text)

    def test_deterministic(self):  # AC-E9
        self.assertEqual(self.replay(), self.replay())

    def test_unknown_column_is_ignored_with_warning(self):
        with open(self.inp) as f:
            text = f.read().replace("event", "event,foo", 1)
        with open(self.inp, "w") as f:
            f.write(text)
        code, _, err = run_main(replay.main, [self.blob, self.inp, "--out", os.path.join(self.dir.name, "x.csv")])
        self.assertEqual(code, 0)
        self.assertIn("foo", err)


if __name__ == "__main__":
    unittest.main()
