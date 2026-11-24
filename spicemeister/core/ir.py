# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Intermediate representation for SPICE netlists / model libraries.

Readers produce a :class:`Netlist` of typed cards; transforms rewrite it in
place; writers serialise it. Every piece of text that is a *math expression*
is wrapped in :class:`Expr` so a single :func:`walk_exprs` pass can reach all
of them (function remapping, unit normalisation) without each transform
re-walking the card types.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from . import expr as _expr


# --------------------------------------------------------------------------- #
# Expression wrapper
# --------------------------------------------------------------------------- #
class Expr:
    """A SPICE math expression (stored without surrounding braces/quotes)."""

    __slots__ = ("text", "_tree", "forced_brace")

    def __init__(self, text: str, forced_brace: bool = False):
        self.text = text.strip()
        self._tree = None
        self.forced_brace = forced_brace

    def tree(self) -> _expr.Node:
        if self._tree is None:
            self._tree = _expr.parse(self.text)
        return self._tree

    def set_tree(self, node: _expr.Node) -> None:
        self._tree = node
        self.text = _expr.unparse(node)

    def is_atomic(self) -> bool:
        """True when the expression is a lone number or identifier."""
        try:
            node = self.tree()
        except _expr.ExprError:
            # Unparseable (e.g. still contains vendor-only syntax) -> treat as
            # non-atomic so it is wrapped and visible.
            return False
        return isinstance(node, (_expr.Num, _expr.Ref))

    def is_number(self) -> bool:
        try:
            return isinstance(self.tree(), _expr.Num)
        except _expr.ExprError:
            return False

    def render(self, always_brace: bool = False) -> str:
        """Render for output, wrapping in ``{...}`` unless a bare number.

        A lone *identifier* (a parameter reference such as ``C``) must stay
        braced: unbraced, SPICE would read it as a model name, not a value.
        Only a lone numeric literal is safe to emit without braces.
        """
        if self.forced_brace or always_brace or not self.is_number():
            return "{" + self.text + "}"
        return self.text

    def __repr__(self) -> str:
        return f"Expr({self.text!r})"


# A positional token on an instance line is either plain text (node name or
# model reference) or an Expr (a braced value such as ``{r0*2}``).
PosTok = Union[str, Expr]


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
@dataclass
class Card:
    lineno: int = 0
    comment: str = ""          # trailing inline comment, preserved verbatim

    def iter_exprs(self) -> List[Expr]:
        return []


@dataclass
class CommentCard(Card):
    text: str = ""             # full-line comment body (no leading '*')


@dataclass
class RawCard(Card):
    """Verbatim passthrough: unknown directives, .control blocks, etc."""
    text: str = ""
    note: str = ""             # why it was passed through (for the report)


@dataclass
class EncryptedCard(Card):
    """A .PROTECT/.UNPROTECT span or otherwise opaque block."""
    text: str = ""
    reason: str = ""


@dataclass
class ParamCard(Card):
    items: "OrderedDict[str, Expr]" = field(default_factory=OrderedDict)

    def iter_exprs(self) -> List[Expr]:
        return list(self.items.values())


@dataclass
class FuncCard(Card):
    name: str = ""
    args: List[str] = field(default_factory=list)
    body: Optional[Expr] = None

    def iter_exprs(self) -> List[Expr]:
        return [self.body] if self.body else []


@dataclass
class GlobalCard(Card):
    nodes: List[str] = field(default_factory=list)


@dataclass
class IncludeCard(Card):
    path: str = ""
    section: str = ""          # for `.lib file section`
    kind: str = "include"      # 'include' | 'lib'


@dataclass
class OptionCard(Card):
    text: str = ""             # remainder after .option(s)


@dataclass
class ModelCard(Card):
    name: str = ""
    mtype: str = ""            # D, NPN, PNP, NMOS, PMOS, VDMOS, R, C, SW, ...
    params: "OrderedDict[str, Expr]" = field(default_factory=OrderedDict)
    # bare valueless flags, verbatim and in order (e.g. VDMOS `pchan`); dropping
    # one silently changes the device (pchan -> N-channel)
    flags: List[str] = field(default_factory=list)

    def iter_exprs(self) -> List[Expr]:
        return list(self.params.values())


@dataclass
class SubcktCard(Card):
    name: str = ""
    nodes: List[str] = field(default_factory=list)
    params: "OrderedDict[str, Expr]" = field(default_factory=OrderedDict)
    body: List[Card] = field(default_factory=list)

    def iter_exprs(self) -> List[Expr]:
        return list(self.params.values())


@dataclass
class Behavioral:
    """Parsed behavioral/controlled-source payload for E / G / B devices."""
    kind: str = "value"        # value|poly|table|laplace|freq|cheby|linear|bsource
    out: List[str] = field(default_factory=list)      # output node pair
    expr: Optional[Expr] = None                        # value / laplace core
    table: List["tuple"] = field(default_factory=list)  # (x_expr, y_expr) pairs
    ctrl: List[str] = field(default_factory=list)      # controlling nodes/srcs (poly/linear)
    coeffs: List[Expr] = field(default_factory=list)   # poly coefficients
    gain: Optional[Expr] = None                        # linear gain
    out_kind: str = "V"        # 'V' (voltage) or 'I' (current) output
    extra: Dict[str, str] = field(default_factory=dict)
    note: str = ""


@dataclass
class InstanceCard(Card):
    refdes: str = ""
    dev: str = ""              # device class, upper-case first letter
    pos: List[PosTok] = field(default_factory=list)    # nodes + model ref, verbatim
    params: "OrderedDict[str, Expr]" = field(default_factory=OrderedDict)
    behavioral: Optional[Behavioral] = None

    def iter_exprs(self) -> List[Expr]:
        out: List[Expr] = [v for v in self.pos if isinstance(v, Expr)]
        out.extend(self.params.values())
        b = self.behavioral
        if b:
            if b.expr:
                out.append(b.expr)
            if b.gain:
                out.append(b.gain)
            out.extend(b.coeffs)
            for xy in b.table:
                out.extend(e for e in xy if isinstance(e, Expr))
        return out


# --------------------------------------------------------------------------- #
# Netlist container
# --------------------------------------------------------------------------- #
@dataclass
class Change:
    pass_name: str
    what: str
    where: str = ""


@dataclass
class Netlist:
    cards: List[Card] = field(default_factory=list)
    source_dialect: str = "spice3"
    source_name: str = ""
    changelog: List[Change] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def log(self, pass_name: str, what: str, where: str = "") -> None:
        self.changelog.append(Change(pass_name, what, where))

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    # convenience accessors --------------------------------------------------
    def subckts(self) -> List[SubcktCard]:
        return [c for c in self.cards if isinstance(c, SubcktCard)]

    def models(self) -> List[ModelCard]:
        out: List[ModelCard] = []

        def rec(cards):
            for c in cards:
                if isinstance(c, ModelCard):
                    out.append(c)
                elif isinstance(c, SubcktCard):
                    rec(c.body)
        rec(self.cards)
        return out


def walk_cards(netlist: Netlist):
    """Yield every card, descending into subcircuit bodies."""
    def rec(cards):
        for c in cards:
            yield c
            if isinstance(c, SubcktCard):
                yield from rec(c.body)
    yield from rec(netlist.cards)


def walk_exprs(netlist: Netlist):
    """Yield every :class:`Expr` in the netlist (for transforms)."""
    for c in walk_cards(netlist):
        yield from c.iter_exprs()
