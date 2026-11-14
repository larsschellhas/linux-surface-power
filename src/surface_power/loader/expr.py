"""Safe evaluator for bracketed parameter expressions (spec blob-format 4.2, AC-B5).

Grammar:
    expr   := term (('+' | '-') term)*
    term   := factor (('*' | '/') factor)*
    factor := number | '(' expr ')' | '-' factor
Only numbers, + - * / and brackets are accepted. Nothing is ever passed to Python's eval().
"""
import re

from .errors import BlobError

MAX_LENGTH = 256   # [D]
MAX_DEPTH = 32     # bracket and unary-minus nesting [D]

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)|([-+*/()]))")


def _tokenize(text: str) -> list[str]:
    tokens, pos = [], 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m:
            raise BlobError(f"invalid character in expression {text!r} at position {pos}")
        tokens.append(m.group(1) or m.group(2))
        pos = m.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[str], text: str):
        self.tokens, self.pos, self.text, self.depth = tokens, 0, text, 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        tok = self.peek()
        if tok is None:
            raise BlobError(f"expression {self.text!r} ends too early")
        self.pos += 1
        return tok

    def expr(self) -> float:
        value = self.term()
        while self.peek() in ("+", "-"):
            if self.take() == "+":
                value += self.term()
            else:
                value -= self.term()
        return value

    def term(self) -> float:
        value = self.factor()
        while self.peek() in ("*", "/"):
            op = self.take()
            rhs = self.factor()
            if op == "*":
                value *= rhs
            elif rhs == 0:
                raise BlobError(f"division by zero in expression {self.text!r}")
            else:
                value /= rhs
        return value

    def factor(self) -> float:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise BlobError(f"expression {self.text!r} is nested too deeply")
        try:
            tok = self.take()
            if tok == "(":
                value = self.expr()
                if self.take() != ")":
                    raise BlobError(f"missing ')' in expression {self.text!r}")
                return value
            if tok == "-":
                return -self.factor()
            if tok[0].isdigit() or tok[0] == ".":
                return float(tok)
            raise BlobError(f"unexpected {tok!r} in expression {self.text!r}")
        finally:
            self.depth -= 1


def evaluate(text: str) -> float:
    """Evaluate a constant expression such as "(28000+10000)". Raises BlobError for anything else."""
    if len(text) > MAX_LENGTH:
        raise BlobError(f"expression longer than {MAX_LENGTH} characters")
    p = _Parser(_tokenize(text), text)
    value = p.expr()
    if p.peek() is not None:
        raise BlobError(f"unexpected {p.peek()!r} in expression {text!r}")
    return value
