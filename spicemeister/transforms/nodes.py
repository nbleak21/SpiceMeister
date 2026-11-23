# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Node-name legalisation (conservative).

Strips Spectre-style global-net ``!`` suffixes consistently at the IR level
(``gnd!`` -> ``0``, ``vdd!`` -> ``vdd``) across subcircuit headers, globals and
instance ports, so references stay consistent. Non-Spectre netlists are
unaffected. Deeper renaming (illegal-character legalisation) is deferred to
v0.2 to avoid silently breaking cross-references.
"""

from __future__ import annotations

from ..core import ir


def _fix(name: str) -> str:
    if name == "gnd!":
        return "0"
    if name.endswith("!"):
        return name[:-1]
    return name


def apply(netlist: ir.Netlist, rules: dict, target: str) -> None:
    n = 0
    for card in ir.walk_cards(netlist):
        if isinstance(card, ir.SubcktCard):
            for i, nd in enumerate(card.nodes):
                f = _fix(nd)
                if f != nd:
                    card.nodes[i] = f
                    n += 1
        elif isinstance(card, ir.GlobalCard):
            for i, nd in enumerate(card.nodes):
                f = _fix(nd)
                if f != nd:
                    card.nodes[i] = f
                    n += 1
        elif isinstance(card, ir.InstanceCard):
            for i, p in enumerate(card.pos):
                if isinstance(p, str):
                    f = _fix(p)
                    if f != p:
                        card.pos[i] = f
                        n += 1
            if card.behavioral:
                card.behavioral.out = [_fix(x) for x in card.behavioral.out]
    if n:
        netlist.log("nodes", f"normalised {n} global-net '!' suffix(es)")
