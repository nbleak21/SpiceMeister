# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Unit / glyph normalisation.

Converts the Unicode micro sign ``µ`` (and ``μ``) to ASCII ``u`` throughout
expressions and positional values, so the output is plain-ASCII and loads
identically everywhere. (Inline-comment delimiters were already normalised by
the lexer.)
"""

from __future__ import annotations

from ..core import ir

_MICRO = ("µ", "μ")   # MICRO SIGN, GREEK SMALL LETTER MU


def _fix(s: str) -> str:
    for ch in _MICRO:
        s = s.replace(ch, "u")
    return s


def apply(netlist: ir.Netlist, rules: dict, target: str) -> None:
    n = 0
    for e in ir.walk_exprs(netlist):
        new = _fix(e.text)
        if new != e.text:
            e.text = new
            e._tree = None
            n += 1
    for card in ir.walk_cards(netlist):
        if isinstance(card, ir.InstanceCard):
            for i, p in enumerate(card.pos):
                if isinstance(p, str) and any(ch in p for ch in _MICRO):
                    card.pos[i] = _fix(p)
                    n += 1
    if n:
        netlist.log("comments_units", f"normalised Unicode micro sign to 'u' in {n} place(s)")
