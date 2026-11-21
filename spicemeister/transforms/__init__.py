"""Transform pipeline: pure IR -> IR passes driven by ``rules/*.json``.

Each pass takes ``(netlist, rules)`` and mutates the netlist in place, logging
what it changed on ``netlist.changelog`` and raising nothing (it records
warnings on ``netlist.warnings`` instead). ``run_all`` applies them in order.
"""

from __future__ import annotations

import json
import os
from typing import Dict

from ..core import ir
from . import (behavioral_sources, comments_units, expr_delimiters, functions,
               model_levels, nodes)

_RULES_DIR = os.path.join(os.path.dirname(__file__), "..", "rules")


def load_rules() -> Dict[str, dict]:
    rules: Dict[str, dict] = {}
    for name in ("functions", "models", "behavioral"):
        path = os.path.join(_RULES_DIR, name + ".json")
        with open(path, "r", encoding="utf-8") as fh:
            rules[name] = json.load(fh)
    return rules


# ordered pipeline
_PASSES = [
    expr_delimiters,
    functions,
    behavioral_sources,
    model_levels,
    comments_units,
    nodes,
]


def run_all(netlist: ir.Netlist, target: str = "ltspice",
            rules: Dict[str, dict] = None) -> ir.Netlist:
    if rules is None:
        rules = load_rules()
    for mod in _PASSES:
        mod.apply(netlist, rules, target)
    return netlist
