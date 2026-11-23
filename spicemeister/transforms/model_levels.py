# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Model type-alias and LEVEL normalisation (driven by ``rules/models.json``).

A LEVEL remap is always reported as a warning: BSIM parameter *sets* are not
guaranteed identical across HSPICE and ngspice/LTspice, so a remapped level is
a verified-by-simulation starting point, not a certified equivalence.
"""

from __future__ import annotations

from ..core import ir


def _find_key(d, key):
    kl = key.lower()
    for k in d:
        if k.lower() == kl:
            return k
    return None


def apply(netlist: ir.Netlist, rules: dict, target: str) -> None:
    mrules = rules["models"]
    type_alias = {k.lower(): v for k, v in mrules.get("type_alias", {}).items()}
    level_map = {k.lower(): v for k, v in mrules.get("level_map", {}).items()}

    for m in netlist.models():
        mt = m.mtype.lower()
        if mt in type_alias and m.mtype != type_alias[mt]:
            old = m.mtype
            m.mtype = type_alias[mt]
            netlist.log("model_levels", f".model {m.name}: type {old} -> {m.mtype}")

        lk = _find_key(m.params, "level")
        if lk and mt in level_map:
            cur = m.params[lk].text.strip()
            tbl = level_map[mt]
            if cur in tbl and tbl[cur] != cur:
                new = tbl[cur]
                m.params[lk] = ir.Expr(new)
                netlist.log("model_levels",
                            f".model {m.name} ({m.mtype}): LEVEL {cur} -> {new}")
                netlist.warn(
                    f".model {m.name}: LEVEL remapped {cur}->{new}; verify BSIM "
                    "parameter compatibility by simulation before trusting results.")
