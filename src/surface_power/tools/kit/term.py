"""How the kit talks on the console: headings, aligned rows, wrapped text, a live progress line.

Only the console: the result file keeps its own format. Colours and the live line only on a terminal (and not with NO_COLOR); plain
ASCII marks where the terminal cannot show others. A terminal that is gone must not stop a run, so nothing here raises on output.
"""
import os
import shutil
import textwrap

MAX_WIDTH = 96
BAR = 24
PIPE_EVERY_S = 30                 # without a terminal: one progress line this often
COLOURS = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}
# status -> (mark, ASCII mark, colour, words)
STATUS = {"passed": ("✔", "[ok]", "green", "passed"), "failed": ("✘", "[!!]", "red", "FAILED"),
          "not-passed": ("!", "[ ?]", "yellow", "not passed"), "skipped": ("–", "[--]", "dim", "skipped")}


class Term:
    def __init__(self, out, width: int | None = None, colour: bool | None = None):
        self.out = out
        try:
            self.tty = bool(out.isatty())
        except (AttributeError, ValueError, OSError):
            self.tty = False
        self.width = width or min(MAX_WIDTH, max(60, shutil.get_terminal_size((MAX_WIDTH, 24)).columns - 1))
        self.colour = colour if colour is not None else (self.tty and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb")
        self.unicode = "utf" in (getattr(out, "encoding", None) or "utf-8").lower()
        self.live = 0                  # length of the progress line on screen
        self._piped = None             # the last progress line written without a terminal

    def paint(self, text: str, *styles: str) -> str:
        if not self.colour or not styles:
            return text
        return "".join(f"\033[{COLOURS[s]}m" for s in styles) + text + "\033[0m"

    def _emit(self, text: str, end: str = "\n") -> None:
        try:
            print(text, end=end, file=self.out, flush=True)
        except OSError:
            pass

    def line(self, text: str = "") -> None:
        self.end_progress()
        self._emit(text)

    def wrap(self, text: str, indent: int = 2, hang: int | None = None) -> list[str]:
        return textwrap.wrap(text, self.width, initial_indent=" " * indent,
                             subsequent_indent=" " * (indent if hang is None else hang), break_long_words=False,
                             break_on_hyphens=False) or [""]

    def text(self, text: str, indent: int = 2, *styles: str) -> None:
        for ln in self.wrap(text, indent):
            self.line(self.paint(ln, *styles))

    def bullet(self, text: str, indent: int = 2, mark: str | None = None) -> None:
        mark = mark or ("•" if self.unicode else "-")
        lines = self.wrap(f"{mark} {text}", indent, indent + len(mark) + 1)
        for ln in lines:
            self.line(ln)

    def heading(self, title: str) -> None:
        rule = "─" if self.unicode else "-"
        self.line()
        self.line(self.paint(f"{rule * 2} {title} " + rule * max(3, self.width - len(title) - 4), "bold", "cyan"))

    def rows(self, pairs: list[tuple[str, str]], indent: int = 2) -> None:
        """label and value in two columns; long values wrap under the value."""
        if not pairs:
            return
        label_width = max(len(k) for k, _ in pairs) + 2
        for label, value in pairs:
            lines = textwrap.wrap(value, max(20, self.width - indent - label_width), break_long_words=False,
                                  break_on_hyphens=False) or [""]
            self.line(" " * indent + self.paint(label.ljust(label_width), "dim") + lines[0])
            for ln in lines[1:]:
                self.line(" " * (indent + label_width) + ln)

    def stage(self, sid: str, title: str, status: str, summary: str, unlock: str | None = None) -> None:
        mark, ascii_mark, colour, words = STATUS[status]
        mark = mark if self.unicode else ascii_mark
        self.line("  " + self.paint(f"{mark} {sid}  {title}: {words}", colour, "bold"))
        self.text(summary[:1].upper() + summary[1:], 6)
        if unlock:
            self.text(f"To unlock: {unlock}", 6)

    def progress(self, label: str, done: float, total: float, extra: str = "") -> None:
        done = min(max(done, 0), total)
        if not self.tty:
            mark = (label, int(done))
            if int(done) and int(done) % PIPE_EVERY_S == 0 and mark != self._piped:
                self._piped = mark
                self._emit(f"    {label}: {int(done)}/{int(total)} s  {extra}".rstrip())
            return
        filled = int(BAR * done / total) if total else BAR
        full, empty = ("█", "░") if self.unicode else ("#", ".")
        plain = f"    {label:<10}{full * filled}{empty * (BAR - filled)} {int(done):>3}/{int(total)} s  {extra}"
        plain = plain[:self.width]
        self._emit("\r" + plain + " " * max(0, self.live - len(plain)), end="")
        self.live = len(plain)

    def end_progress(self) -> None:
        if self.live:
            self.live = 0
            self._emit("")


def readings(sample: dict) -> str:
    """The live values of one sample, in the order a person looks for them."""
    parts = []
    if sample.get("pkg_w") is not None:
        parts.append(f"{sample['pkg_w']:5.1f} W")
    if "cpu_c" in sample:
        parts.append(f"CPU {sample['cpu_c']:3.0f} °C")
    if "fan_rpm" in sample:
        parts.append(f"fan {sample['fan_rpm']} rpm")
    return "  ".join(parts)
