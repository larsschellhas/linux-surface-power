"""Tests for the preprocessor, expressions, numbers and the line parser (spec blob-format 4-6; AC-B4, AC-B5)."""
import unittest

from helpers import LIMITS, SYSTEM, TEMP, VN, BlobBuilder, G

from surface_power.loader import expr, numbers, parser, preprocess
from surface_power.loader.errors import BlobError


class PreprocessTest(unittest.TestCase):
    def test_comments_and_line_numbers(self):
        lines = preprocess.run("a,b // comment\r\n// whole line\r\nc,d\r\n")
        self.assertEqual([(l.number, l.text) for l in lines], [(1, "a,b"), (2, ""), (3, "c,d")])

    def test_macros_nested(self):
        text = "#macro A 5\n#macro B %A%,%A%\nx,%B%\n"
        lines = preprocess.run(text)
        self.assertEqual([(l.number, l.text) for l in lines], [(3, "x,5,5")])

    def test_builtin_vn_macro(self):
        lines = preprocess.run("1,%VN12%\n")
        self.assertEqual(lines[0].text, f"1,{{{VN}}}_12")

    def test_unknown_macro_fails_with_line(self):
        with self.assertRaises(BlobError) as cm:
            preprocess.run("ok\n%NOPE%\n")
        self.assertEqual(cm.exception.line, 2)

    def test_recursive_macro_fails(self):
        with self.assertRaises(BlobError):
            preprocess.run("#macro A %B%\n#macro B %A%\n%A%\n")

    def test_macro_name_length(self):
        with self.assertRaises(BlobError):
            preprocess.run("#macro " + "X" * 64 + " 1\n")


class ExprTest(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(expr.evaluate("(28000+10000)"), 38000)
        self.assertAlmostEqual(expr.evaluate("(5700*0.7)"), 3990)
        self.assertEqual(expr.evaluate("((2+3)*4-6/3)"), 18)
        self.assertEqual(expr.evaluate("(-3*-2)"), 6)
        self.assertEqual(expr.evaluate("(1.5e3)"), 1500)

    def test_rejects_other_tokens(self):
        for bad in ("(__import__)", "(1+x)", "(2**3)", "(1;2)", "(1+)", "((1)", "(1/0)", "(" + "1+" * 2000 + "1)"):
            with self.subTest(bad=bad), self.assertRaises(BlobError):
                expr.evaluate(bad)


class NumbersTest(unittest.TestCase):
    def test_atoi_like_c(self):
        self.assertEqual(numbers.atoi(" 42"), 42)
        self.assertEqual(numbers.atoi("-7"), -7)
        self.assertEqual(numbers.atoi("0.4"), 0)
        self.assertEqual(numbers.atoi("12abc"), 12)
        with self.assertRaises(ValueError):
            numbers.atoi("")

    def test_real(self):
        self.assertEqual(numbers.real("0.4"), 0.4)
        self.assertEqual(numbers.real("-10000"), -10000)
        self.assertEqual(numbers.real("0.00986"), 0.00986)
        for bad in ("", "abc", "1,5", "1e"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                numbers.real(bad)


class ParserTest(unittest.TestCase):
    def build(self):
        b = BlobBuilder()
        b.input_channel(TEMP, 509, 3681, 2731, trip=3151)
        b.output_channel(LIMITS, 51, 25000, 31000, 7500)
        b.line(G["pass_through"], 1, [(SYSTEM, 1)], [(VN, 2)], [7])
        b.line(G["pass_through"], 2, [(VN, 2)], [(LIMITS, 51)], [25000], stage=1)
        return b

    def test_sections(self):
        blob = parser.parse_text(self.build().text())
        self.assertEqual(len(blob.input_channels), 1)
        self.assertEqual(len(blob.output_channels), 1)
        self.assertEqual(len(blob.algorithms), 2)
        ic = blob.input_channels[0]
        self.assertEqual((str(ic.ref), ic.max, ic.min, ic.kind), ("2E8B14C2/509", 3681, 2731, 1))
        oc = blob.output_channels[0]
        self.assertEqual((oc.default, oc.max, oc.min), (25000, 31000, 7500))
        a = blob.algorithms[1]
        self.assertEqual((a.instance, a.stage, a.divider, a.guid, a.id), (1, 1, 0, G["pass_through"], 2))
        self.assertEqual([str(r) for r in a.inputs], ["VN2"])
        self.assertEqual([str(r) for r in a.outputs], ["0B7B5350/51"])
        self.assertEqual(a.params, ("25000",))
        self.assertEqual(a.line, 5)  # 1-based line in the text
        self.assertTrue(blob.telemetry)

    def test_lf_only_and_lowercase_guid(self):
        text = self.build().text(crlf=False).replace(G["pass_through"], G["pass_through"].lower())
        blob = parser.parse_text(text)
        self.assertEqual(blob.algorithms[0].guid, G["pass_through"])

    def test_expression_parameter(self):
        b = self.build()
        b.line(G["pass_through"], 3, [(VN, 2)], [(VN, 3)], ["(20000+5000)"])
        blob = parser.parse_text(b.text())
        self.assertEqual(blob.algorithms[2].params, ("25000",))

    def test_expression_with_channel_is_dynamic(self):
        b = self.build()
        b.line(G["pass_through"], 3, [(VN, 2)], [(VN, 3)], [f"({{{VN}}}_2+1)"])
        line = parser.parse_text(b.text()).algorithms[2]
        self.assertTrue(line.dynamic)
        self.assertEqual(line.params, (f"({{{VN}}}_2+1)",))

    def test_expression_with_other_names_rejected(self):
        b = self.build()
        b.line(G["pass_through"], 3, [(VN, 2)], [(VN, 3)], ["(x+1)"])
        with self.assertRaises(BlobError):
            parser.parse_text(b.text())

    def test_braced_channels_in_references(self):
        b = self.build()
        b.algorithms.append(f"1,0,0,{G['pass_through']},3,1,1,{{{TEMP.lower()}}}_509,{{{VN}}}_9,5")
        line = parser.parse_text(b.text()).algorithms[2]
        self.assertEqual([str(r) for r in line.inputs + line.outputs], ["2E8B14C2/509", "VN9"])

    def test_bad_lines_report_line_number(self):
        cases = {
            "instance": "2,0,0," + G["pass_through"] + ",1,1,1," + VN + ",1," + VN + ",2,5",
            "too few refs": "1,0,0," + G["pass_through"] + ",1,1,1," + VN + ",1",
            "bad guid": "1,0,0,XYZ,1,1,1," + VN + ",1," + VN + ",2,5",
            "not a number": "1,x,0," + G["pass_through"] + ",1,1,1," + VN + ",1," + VN + ",2,5",
        }
        for name, bad in cases.items():
            b = self.build()
            b.algorithms.insert(1, bad)
            with self.subTest(name=name), self.assertRaises(BlobError) as cm:
                parser.parse_text(b.text())
            self.assertEqual(cm.exception.line, 5)

    def test_bad_channel_lines(self):
        b = self.build()
        b.channels.append(f"{TEMP},505,3151,5,1000,1,1,4,3681,2731")  # kind 4
        with self.assertRaises(BlobError):
            parser.parse_text(b.text())
        b = self.build()
        b.channels.append(f"{LIMITS},52,62000,46000,15000")  # default > max
        with self.assertRaises(BlobError):
            parser.parse_text(b.text())

    def test_missing_algorithm_section(self):
        with self.assertRaises(BlobError):
            parser.parse_text(f"{LIMITS},51,25000,31000,7500\r\n")


if __name__ == "__main__":
    unittest.main()
