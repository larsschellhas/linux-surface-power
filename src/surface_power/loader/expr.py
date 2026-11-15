"""Safe evaluator for bracketed parameter expressions (spec blob-format 4.2, AC-B5).

Grammar:
    expr   := term (('+' | '-') term)*
    term   := factor (('*' | '/') factor)*
    factor := number | channel | '(' expr ')' | '-' factor
    channel := '{' GUID '}_' digits
Only numbers, channel references, + - * / and brackets are accepted. Nothing is ever passed to Python's eval().
An expression is compiled once; one with channel references is evaluated on every run (spec engine 3.6).
"""
import re
from collections.abc import Callable

from .errors import BlobError
from .model import ChannelRef

MAX_LENGTH = 256   # [D]
MAX_DEPTH = 32     # bracket and unary-minus nesting [D]

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)|([-+*/()])|"
                    r"\{([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\}_(\d+))")

# A compiled node: a function of the channel lookup. The lookup returns the channel value, or None if it has none.
Node = Callable[[Callable[[ChannelRef], float | None]], float]


class Unavailable(Exception):
    """A referenced channel has no valid value (the core then skips the block for this run [C])."""


def _tokenize(text: str) -> list[object]:
    tokens, pos = [], 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m:
            raise BlobError(f"invalid character in expression {text!r} at position {pos}")
        if m.group(3):
            tokens.append(ChannelRef(m.group(3).upper(), int(m.group(4))))
        else:
            tokens.append(m.group(1) or m.group(2))
        pos = m.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[object], text: str):
        self.tokens, self.pos, self.text, self.depth = tokens, 0, text, 0
        self.refs: list[ChannelRef] = []

    def peek(self) -> object | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> object:
        tok = self.peek()
        if tok is None:
            raise BlobError(f"expression {self.text!r} ends too early")
        self.pos += 1
        return tok

    def expr(self) -> Node:
        node = self.term()
        while self.peek() in ("+", "-"):
            op, lhs, rhs = self.take(), node, self.term()
            node = (lambda a, b: lambda get: a(get) + b(get))(lhs, rhs) if op == "+" else \
                (lambda a, b: lambda get: a(get) - b(get))(lhs, rhs)
        return node

    def term(self) -> Node:
        node = self.factor()
        while self.peek() in ("*", "/"):
            op, lhs, rhs = self.take(), node, self.factor()
            node = (lambda a, b: lambda get: a(get) * b(get))(lhs, rhs) if op == "*" else self._divide(lhs, rhs)
        return node

    def _divide(self, a: Node, b: Node) -> Node:
        text = self.text

        def div(get):
            d = b(get)
            if d == 0:
                raise BlobError(f"division by zero in expression {text!r}")
            return a(get) / d
        return div

    def factor(self) -> Node:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise BlobError(f"expression {self.text!r} is nested too deeply")
        try:
            tok = self.take()
            if isinstance(tok, ChannelRef):
                self.refs.append(tok)

                def channel(get, ref=tok):
                    v = get(ref)
                    if v is None:
                        raise Unavailable(str(ref))
                    return v
                return channel
            if tok == "(":
                node = self.expr()
                if self.take() != ")":
                    raise BlobError(f"missing ')' in expression {self.text!r}")
                return node
            if tok == "-":
                inner = self.factor()
                return lambda get: -inner(get)
            if tok[0].isdigit() or tok[0] == ".":
                value = float(tok)
                return lambda get: value
            raise BlobError(f"unexpected {tok!r} in expression {self.text!r}")
        finally:
            self.depth -= 1


class Expression:
    """A compiled expression. `refs` lists the channels it reads."""

    def __init__(self, text: str):
        if len(text) > MAX_LENGTH:
            raise BlobError(f"expression longer than {MAX_LENGTH} characters")
        p = _Parser(_tokenize(text), text)
        self._node = p.expr()
        if p.peek() is not None:
            raise BlobError(f"unexpected {p.peek()!r} in expression {text!r}")
        self.text, self.refs = text, tuple(p.refs)

    def value(self, get: Callable[[ChannelRef], float | None] = lambda ref: None) -> float:
        """Evaluate. Raises Unavailable if a channel has no value, BlobError on division by zero."""
        return self._node(get)


def evaluate(text: str) -> float:
    """Evaluate a constant expression such as "(28000+10000)". Raises BlobError for anything else."""
    e = Expression(text)
    if e.refs:
        raise BlobError(f"expression {text!r} references channels")
    return e.value()
