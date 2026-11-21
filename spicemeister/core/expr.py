"""SPICE expression parser and AST.

Parses the arithmetic/behavioral expression grammar shared by PSpice, HSPICE,
LTspice and ngspice into a small typed AST, and unparses it back to text with
precedence-aware parenthesisation so transforms (function remapping, operator
normalisation) are structural rather than regex-based.

Grammar (loosest to tightest binding):

    expr      := ternary
    ternary   := or ( '?' expr ':' expr )?
    or        := and ( '||' and )*
    and       := equality ( '&&' equality )*
    equality  := relational ( ('=='|'!=') relational )*
    relational:= additive ( ('<'|'>'|'<='|'>=') additive )*
    additive  := mul ( ('+'|'-') mul )*
    mul       := power ( ('*'|'/'|'%') power )*
    power     := unary ( ('**'|'^') power )?        # right associative
    unary     := ('-'|'+'|'!') unary | primary
    primary   := number | ident | ident '(' [expr (',' expr)*] ')' | '(' expr ')'

Numbers accept SPICE engineering suffixes (T G Meg K M U N P F and trailing
unit letters, which are ignored for value but preserved in source text).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


class ExprError(ValueError):
    """Raised when an expression cannot be parsed."""


# --------------------------------------------------------------------------- #
# AST nodes
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    pass


@dataclass
class Num(Node):
    text: str          # original source token, e.g. "4.7u", "1e-12"
    value: float       # numeric value after scale factor


@dataclass
class Ref(Node):
    name: str          # parameter / node / builtin reference, e.g. "temp", "x"


@dataclass
class Call(Node):
    name: str
    args: List[Node] = field(default_factory=list)


@dataclass
class Unary(Node):
    op: str
    operand: Node


@dataclass
class Binary(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Ternary(Node):
    cond: Node
    a: Node
    b: Node


# --------------------------------------------------------------------------- #
# Scale factors (classic SPICE; case-insensitive, 'meg' before 'm')
# --------------------------------------------------------------------------- #
_SCALE = {
    "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3,
    "m": 1e-3, "mil": 25.4e-6, "u": 1e-6, "µ": 1e-6,
    "n": 1e-9, "p": 1e-12, "f": 1e-15, "a": 1e-18,
}


def parse_number(tok: str) -> float:
    """Parse a SPICE number token (with optional scale factor + unit tail)."""
    s = tok.strip()
    i = 0
    n = len(s)
    if i < n and s[i] in "+-":
        i += 1
    seen_digit = False
    while i < n and (s[i].isdigit()):
        i += 1
        seen_digit = True
    if i < n and s[i] == ".":
        i += 1
        while i < n and s[i].isdigit():
            i += 1
            seen_digit = True
    if not seen_digit:
        raise ExprError(f"not a number: {tok!r}")
    # exponent
    if i < n and s[i] in "eE":
        j = i + 1
        if j < n and s[j] in "+-":
            j += 1
        if j < n and s[j].isdigit():
            j += 1
            while j < n and s[j].isdigit():
                j += 1
            i = j
    mantissa = float(s[:i])
    tail = s[i:].lower()
    if not tail:
        return mantissa
    # longest matching scale prefix ('meg'/'mil' before single letters)
    for suf in ("meg", "mil"):
        if tail.startswith(suf):
            return mantissa * _SCALE[suf]
    first = tail[0]
    if first in _SCALE:
        return mantissa * _SCALE[first]
    return mantissa  # unit-only tail (e.g. "5V"): scale factor absent


# --------------------------------------------------------------------------- #
# Tokeniser
# --------------------------------------------------------------------------- #
_TWO_CHAR = {"**", "<=", ">=", "==", "!=", "&&", "||"}
_PUNCT = set("+-*/%^(),?:<>!")


def _tokenize(s: str) -> List[str]:
    toks: List[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        # number: digit, or '.' followed by digit
        if c.isdigit() or (c == "." and i + 1 < n and s[i + 1].isdigit()):
            j = i + 1
            while j < n and (s[j].isdigit() or s[j] == "."):
                j += 1
            if j < n and s[j] in "eE":
                k = j + 1
                if k < n and s[k] in "+-":
                    k += 1
                if k < n and s[k].isdigit():
                    j = k
                    while j < n and s[j].isdigit():
                        j += 1
            # trailing unit/scale letters (alnum)
            while j < n and (s[j].isalpha()):
                j += 1
            toks.append(s[i:j])
            i = j
            continue
        # identifier (param/node/function name); SPICE allows . _ and digits
        if c.isalpha() or c == "_":
            j = i + 1
            while j < n and (s[j].isalnum() or s[j] in "_.#"):
                j += 1
            toks.append(s[i:j])
            i = j
            continue
        # two-char operators
        if s[i:i + 2] in _TWO_CHAR:
            toks.append(s[i:i + 2])
            i += 2
            continue
        if c in _PUNCT:
            toks.append(c)
            i += 1
            continue
        raise ExprError(f"unexpected character {c!r} in expression {s!r}")
    return toks


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
class _Parser:
    MAX_DEPTH = 200

    def __init__(self, toks: List[str], src: str):
        self.toks = toks
        self.src = src
        self.i = 0
        self.depth = 0

    def peek(self) -> Optional[str]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> str:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, t: str) -> None:
        if self.peek() != t:
            raise ExprError(f"expected {t!r} in {self.src!r}, got {self.peek()!r}")
        self.next()

    def parse(self) -> Node:
        node = self.ternary()
        if self.peek() is not None:
            raise ExprError(f"trailing tokens {self.toks[self.i:]} in {self.src!r}")
        return node

    def ternary(self) -> Node:
        self.depth += 1
        if self.depth > self.MAX_DEPTH:
            raise ExprError(f"expression nesting too deep (>{self.MAX_DEPTH}) in {self.src!r}")
        try:
            cond = self.logic_or()
            if self.peek() == "?":
                self.next()
                a = self.ternary()
                self.expect(":")
                b = self.ternary()
                return Ternary(cond, a, b)
            return cond
        finally:
            self.depth -= 1

    def _binary_left(self, ops, sub):
        node = sub()
        while self.peek() in ops:
            op = self.next()
            node = Binary(op, node, sub())
        return node

    def logic_or(self):
        return self._binary_left({"||"}, self.logic_and)

    def logic_and(self):
        return self._binary_left({"&&"}, self.equality)

    def equality(self):
        return self._binary_left({"==", "!="}, self.relational)

    def relational(self):
        return self._binary_left({"<", ">", "<=", ">="}, self.additive)

    def additive(self):
        return self._binary_left({"+", "-"}, self.mul)

    def mul(self):
        return self._binary_left({"*", "/", "%"}, self.unary)

    def unary(self):
        # Power binds tighter than a prefix sign, so `-x**2` == `-(x**2)`.
        t = self.peek()
        if t in ("-", "+", "!"):
            self.next()
            return Unary(t, self.unary())
        return self.power()

    def power(self):
        node = self.primary()
        if self.peek() in ("**", "^"):
            self.next()
            # right associative; exponent may carry its own sign (2**-3)
            return Binary("**", node, self.unary())
        return node

    def primary(self):
        t = self.peek()
        if t is None:
            raise ExprError(f"unexpected end of expression {self.src!r}")
        if t == "(":
            self.next()
            node = self.ternary()
            self.expect(")")
            return node
        # number
        if t[0].isdigit() or (t[0] == "." and len(t) > 1 and t[1].isdigit()):
            self.next()
            return Num(t, parse_number(t))
        # identifier or call
        if t[0].isalpha() or t[0] == "_":
            name = self.next()
            if self.peek() == "(":
                self.next()
                args: List[Node] = []
                if self.peek() != ")":
                    args.append(self.ternary())
                    while self.peek() == ",":
                        self.next()
                        args.append(self.ternary())
                self.expect(")")
                return Call(name, args)
            return Ref(name)
        raise ExprError(f"unexpected token {t!r} in {self.src!r}")


def parse(src: str) -> Node:
    """Parse an expression string into an AST."""
    toks = _tokenize(src)
    if not toks:
        raise ExprError("empty expression")
    try:
        return _Parser(toks, src).parse()
    except RecursionError:
        raise ExprError(f"expression too deeply nested in {src!r}")


# --------------------------------------------------------------------------- #
# Unparser (precedence-aware)
# --------------------------------------------------------------------------- #
_PREC = {
    "?:": 1, "||": 2, "&&": 3,
    "==": 4, "!=": 4,
    "<": 5, ">": 5, "<=": 5, ">=": 5,
    "+": 6, "-": 6,
    "*": 7, "/": 7, "%": 7,
    "u-": 7.5, "u+": 7.5, "u!": 7.5,   # prefix sign: looser than ** , tighter than *
    "**": 8,
}
_ATOM = 10


def _prec(node: Node) -> int:
    if isinstance(node, Binary):
        return _PREC[node.op]
    if isinstance(node, Ternary):
        return _PREC["?:"]
    if isinstance(node, Unary):
        return _PREC["u" + node.op]
    return _ATOM


def unparse(node: Node) -> str:
    """Render an AST back to a canonical expression string (no outer braces)."""
    if isinstance(node, Num):
        return node.text
    if isinstance(node, Ref):
        return node.name
    if isinstance(node, Call):
        return node.name + "(" + ",".join(unparse(a) for a in node.args) + ")"
    if isinstance(node, Unary):
        inner = unparse(node.operand)
        if _prec(node.operand) < _prec(node):
            inner = "(" + inner + ")"
        return node.op + inner
    if isinstance(node, Ternary):
        return f"{_wrap(node.cond, node, False)} ? {unparse(node.a)} : {unparse(node.b)}"
    if isinstance(node, Binary):
        p = _PREC[node.op]
        left = unparse(node.left)
        right = unparse(node.right)
        lp = _prec(node.left)
        rp = _prec(node.right)
        if node.op == "**":
            # Our parser treats '**' as right-associative (so `a**b**c` ==
            # `a**(b**c)`), but ngspice's B-source parser evaluates '**'
            # LEFT-associatively (`a**b**c` == `(a**b)**c`, verified: 2**3**2==64).
            # To keep the AST's grouping intact under BOTH readers, fully
            # parenthesise any non-atomic operand so the meaning is explicit.
            if lp <= p:
                left = "(" + left + ")"
            if rp <= p:
                right = "(" + right + ")"
            return f"{left}**{right}"
        if lp < p:
            left = "(" + left + ")"
        if rp <= p:                      # left-associative: equal-prec right needs parens
            right = "(" + right + ")"
        return f"{left} {node.op} {right}"
    raise ExprError(f"cannot unparse node {node!r}")


def _wrap(child: Node, parent: Node, right: bool) -> str:
    s = unparse(child)
    if _prec(child) <= _PREC["?:"]:
        return "(" + s + ")"
    return s


# --------------------------------------------------------------------------- #
# AST walking / rewriting helpers (used by transforms)
# --------------------------------------------------------------------------- #
def map_nodes(node: Node, fn):
    """Bottom-up rewrite: apply ``fn`` to every node after its children.

    ``fn(node)`` returns a replacement node (or the same node).
    """
    if isinstance(node, Call):
        node.args = [map_nodes(a, fn) for a in node.args]
    elif isinstance(node, Unary):
        node.operand = map_nodes(node.operand, fn)
    elif isinstance(node, Binary):
        node.left = map_nodes(node.left, fn)
        node.right = map_nodes(node.right, fn)
    elif isinstance(node, Ternary):
        node.cond = map_nodes(node.cond, fn)
        node.a = map_nodes(node.a, fn)
        node.b = map_nodes(node.b, fn)
    return fn(node)


def find_calls(node: Node):
    """Yield the lowercased names of every function call in the tree."""
    if isinstance(node, Call):
        yield node.name.lower()
        for a in node.args:
            yield from find_calls(a)
    elif isinstance(node, Unary):
        yield from find_calls(node.operand)
    elif isinstance(node, Binary):
        yield from find_calls(node.left)
        yield from find_calls(node.right)
    elif isinstance(node, Ternary):
        yield from find_calls(node.cond)
        yield from find_calls(node.a)
        yield from find_calls(node.b)
