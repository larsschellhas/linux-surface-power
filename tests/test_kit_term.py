"""The kit's console (spec tester-kit.md 6.3): wrapped to the width, a live line only on a terminal, plain where it must be."""
import io
import unittest

import helpers  # noqa: F401  (puts src on the path)

from surface_power.tools.kit import term


class Tty(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True


class Ascii(io.StringIO):
    encoding = "ascii"


class Gone(io.StringIO):
    def write(self, *a):
        raise OSError("terminal gone")


class TermTest(unittest.TestCase):
    def test_everything_wraps_to_the_width(self):
        out = io.StringIO()
        t = term.Term(out, width=60)
        t.heading("A heading")
        t.text("word " * 60)
        t.bullet("word " * 60)
        t.rows([("Label", "value " * 30), ("A longer label", "x")])
        t.stage("S2", "Observe under load", "passed", "word " * 40, "word " * 20)
        self.assertTrue(all(len(line) <= 60 for line in out.getvalue().splitlines()), out.getvalue())
        self.assertNotIn("\033", out.getvalue())

    def test_the_live_line_overwrites_itself_and_ends_before_other_text(self):
        out = Tty()
        t = term.Term(out, width=80, colour=False)
        t.progress("load", 1, 150, "28.1 W")
        t.progress("load", 2, 150, "28.2 W")
        t.line("next")
        text = out.getvalue()
        self.assertEqual(text.count("\r"), 2)
        self.assertEqual(text.count("\n"), 2)                 # the live line is ended once, then "next"
        self.assertTrue(text.endswith("\nnext\n"))

    def test_without_a_terminal_one_line_every_30_s_and_no_repeats(self):
        out = io.StringIO()
        t = term.Term(out)
        for done in list(range(1, 91)) + [90, 90]:
            t.progress("load", done, 90)
        self.assertEqual(out.getvalue().splitlines(), [f"    load: {s}/90 s" for s in (30, 60, 90)])

    def test_plain_marks_where_the_terminal_cannot_show_others(self):
        out = Ascii()
        t = term.Term(out, width=70)
        t.heading("x")
        t.bullet("y")
        for status in term.STATUS:
            t.stage("S1", "Snapshot", status, "done.")
        out.getvalue().encode("ascii")                        # raises if anything else slipped through

    def test_a_terminal_that_is_gone_stops_nothing(self):
        t = term.Term(Gone())
        t.heading("x")
        t.progress("load", 30, 60)
        t.end_progress()

    def test_readings_in_a_fixed_order(self):
        self.assertEqual(term.readings({"cpu_c": 98.4, "pkg_w": 28.06, "fan_rpm": 4500}), " 28.1 W  CPU  98 °C  fan 4500 rpm")
        self.assertEqual(term.readings({"pkg_w": None}), "")


if __name__ == "__main__":
    unittest.main()
