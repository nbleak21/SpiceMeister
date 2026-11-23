# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Normalise expression delimiters.

Readers already strip the outer ``{...}`` / ``'...'`` wrapper, so IR
expressions are brace-free. This pass cleans up any *residual* single-quoted
sub-expression text that slipped through (e.g. an HSPICE value nested inside a
token), converting ``'expr'`` to bare ``expr`` since neither LTspice nor
ngspice uses single quotes for expressions.
"""

from __future__ import annotations

import re

from ..core import ir

_QUOTED = re.compile(r"'([^']*)'")


def apply(netlist: ir.Netlist, rules: dict, target: str) -> None:
    changed = 0
    for e in ir.walk_exprs(netlist):
        if "'" in e.text:
            new = _QUOTED.sub(r"\1", e.text)
            if new != e.text:
                e.text = new
                e._tree = None
                changed += 1
    if changed:
        netlist.log("expr_delimiters", f"removed single-quote delimiters in {changed} expression(s)")
