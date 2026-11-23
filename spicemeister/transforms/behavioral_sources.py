# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Behavioural / controlled-source normalisation.

Decides the LTspice/ngspice target form for parsed E/G/B sources and records
it on the ``Behavioral`` payload for the writer:

  * E ``VALUE``/``VOL`` and G ``VALUE``/``CUR``  -> B-source ``V=``/``I={...}``
  * E/G ``TABLE {ctrl}=(x,y)...``                -> B-source ``V/I={table(ctrl, ...)}``
  * B-source                                      -> kept (ensured braced)
  * POLY / LAPLACE / FREQ / CHEBYSHEV             -> kept verbatim (warned on read)

B-sources are the native behavioural element in both simulators, which avoids
the ``VALUE=`` dialect quirks that silently misbehave across ngbehavior modes.
"""

from __future__ import annotations

from ..core import ir

_CONVERT = {"value", "table"}


def apply(netlist: ir.Netlist, rules: dict, target: str) -> None:
    policy = rules["behavioral"]
    emit_b = policy.get("emit_as_bsource", True)
    converted = 0
    for card in ir.walk_cards(netlist):
        if not isinstance(card, ir.InstanceCard) or not card.behavioral:
            continue
        beh = card.behavioral
        if beh.kind in _CONVERT and emit_b:
            beh.extra["emit"] = "bsource"
            kind_desc = "TABLE" if beh.kind == "table" else beh.out_kind
            netlist.log("behavioral_sources",
                        f"{card.refdes}: {card.dev}-source ({kind_desc}) -> B-source "
                        f"{beh.out_kind}={{...}}")
            converted += 1
        elif beh.kind == "bsource":
            beh.extra["emit"] = "bsource"
    if converted:
        netlist.log("behavioral_sources", f"converted {converted} controlled source(s) to B-sources")
