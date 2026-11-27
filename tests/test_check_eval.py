"""The evaluation side of the tester kit (spec tester-kit.md 9) on a made-up blob; a result is data from a public issue."""
import hashlib
import io
import json
import os
import tempfile
import unittest

from fakemachine import FakeMachine
from helpers import LIMITS, SYSTEM, TEMP, VN, BlobBuilder, G

from surface_power.loader import container
from surface_power.tools import check, check_eval
from surface_power.tools.kit import result


def blob_text():
    b = BlobBuilder()
    b.input_channel(TEMP, 509, 3681, 2731)
    b.line(G["pid_sets"], 1, [(TEMP, 509), (SYSTEM, 1)], [(VN, 1)], [20, 1, 15, 4, 0], divider=1)
    b.line(G["pid_parameter_set"], 1, [], [], [1, 1, 1, 30000, 30001, 7500, 21234, 3211, 10, 40, 100, 0.4])
    b.line(G["pass_through"], 2, [(VN, 1)], [(LIMITS, 51)], [15678], stage=1)
    return b.text()


class Extract(unittest.TestCase):
    def test_only_our_data_block_with_our_schema(self):
        for text, word in (("nothing", "no data block"), (result.MARKER + "\n```json\n{", "cut off"),
                           (result.MARKER + "\n```json\n{oops}\n```", "not valid JSON"),
                           (result.MARKER + '\n```json\n{"schema": 99}\n```', "unknown schema")):
            with self.subTest(word), self.assertRaisesRegex(check_eval.NotAResult, word):
                check_eval.extract(text)

    def test_the_last_block_wins_over_a_quoted_one(self):
        text = f'{result.MARKER}\n```json\n{{"schema": 1, "n": 1}}\n```\n{result.MARKER}\n```json\r\n{{"schema": 1, "n": 2}}\r\n```'
        self.assertEqual(check_eval.extract(text)["n"], 2)


class Skeleton(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def corpus(self):
        path = os.path.join(self.tmp.name, "x.efivar")
        from helpers import efivar
        with open(path, "wb") as f:
            f.write(efivar(blob_text()))
        with open(path, "rb") as f:
            sha = hashlib.sha256(container.decode(f.read(), efivar=True).text.encode()).hexdigest()
        return sha, (os.path.join(self.tmp.name, "*.efivar"),)

    def test_an_unknown_blob_stops_the_computation(self):
        text = check_eval.skeleton({"schema": 1, "blob": {"sha256": "0" * 64}}, patterns=(os.path.join(self.tmp.name, "*"),))
        self.assertIn("NOT IN THE CORPUS", text)
        self.assertNotIn("per slider state", text)

    def test_garbage_in_every_field_gives_a_skeleton_and_runs_nothing(self):
        hostile = {"schema": 1, "machine": "$(rm -rf /)", "blob": {"sha256": "../../etc/passwd"}, "limits": [1, 2],
                   "watch": {"events": ["x", {"t": "\x1b[2J", "what": "a\nb"}]}, "stages": {"S5": {"details": {"writes": [1]}}},
                   "dry_run": {"status": {"inputs": "no"}}, "sensor_probe": {"sensors": 5}}
        text = check_eval.skeleton(hostile, patterns=())
        self.assertNotIn("\x1b", text)
        self.assertIn("Open: manual", text)

    def test_replay_table_and_comparison_with_the_testers_values(self):
        sha, patterns = self.corpus()
        data = {"schema": 1, "blob": {"sha256": sha}, "machine": {"product": "Surface Book 3", "sku": "Surface_Book_3_1899"},
                "dry_run": {"status": {"slider": {"state": 5},
                                       "inputs": [{"channel": "2E8B14C2/509", "value": 3081, "valid": True}],
                                       "outputs": [{"channel": "0B7B5350/51", "value_w": 99.0}]}},
                "stages": {"S1": {"status": "passed", "summary": "collected."}}}
        text = check_eval.skeleton(data, issue=7, patterns=patterns)
        self.assertIn("per slider state", text)
        self.assertRegex(text, r"PL1: replay [\d.]+ W, tester 99 W  \*\*differs")
        self.assertIn('stage = "S1"', text)
        self.assertIn("issue = 7", text)

    def test_a_result_of_the_kit_itself_reads_back_and_the_module_check_agrees(self):
        m = FakeMachine()
        patches = m.patches(os.path.join(self.tmp.name, "work"))
        for p in patches:
            p.start()
        try:
            answers = {"i accept": "I accept", "collect": "y", "load test": "y", "kernel module": "y", "write test": "write"}
            c = check.Check(m.root, out=io.StringIO(), release="7.3.6-1-surface",
                            ask_line=lambda q: next((v for k, v in answers.items() if k in q.lower()), ""))
            c.transport.can_write = lambda: False
            check.run_all(c, c.ask_everything())
            c.preflight_lines = []
            path = c.save(self.tmp.name)
        finally:
            for p in reversed(patches):
                p.stop()
            m.cleanup()
        with open(path) as f:
            data = check_eval.extract(f.read())
        text = check_eval.skeleton(data, patterns=())
        self.assertIn("S3: zone against register, recomputed: equal", text)
        self.assertIn("S5: register after the unload equals the one before", text)
        self.assertIn("S5: write enabled = 1", text)
        self.assertIn("MMIO 0x59a0: PL1 25 W (off, clamp)", text)


if __name__ == "__main__":
    unittest.main()
