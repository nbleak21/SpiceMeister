"""Function remapping pass (AST rewrite driven by ``rules/functions.json``).

Walks every expression's AST and replaces vendor-specific function calls with
LTspice/ngspice-common equivalents, either by renaming (``STP`` -> ``u``) or by
template expansion (``PWRS(x,y)`` -> ``sgn(x)*abs(x)**y``). Expansion wraps each
argument in parentheses before re-parsing, so operator precedence is preserved.
Runs to a fixpoint so a template that introduces a mappable function is itself
re-processed.
"""

from __future__ import annotations

import re
from typing import Dict

from ..core import expr as E
from ..core import ir

_PLACEHOLDER = re.compile(r"\$(\d+)")


def _max_placeholder(template: str) -> int:
    idx = [int(m.group(1)) for m in _PLACEHOLDER.finditer(template)]
    return max(idx) if idx else -1


def _make_rewriter(fmap: Dict[str, dict], used: Dict[str, int], target: str):
    def rewrite(node: E.Node) -> E.Node:
        if not isinstance(node, E.Call):
            return node
        spec = fmap.get(node.name.lower())
        if not spec or node.name.startswith("_"):
            return node
        # A rule may restrict itself to specific output profiles (e.g. if() is
        # only rewritten for the ngspice target; LTspice keeps if()).
        if "targets" in spec and target not in spec["targets"]:
            return node
        if "rename" in spec:
            used[node.name.lower()] = used.get(node.name.lower(), 0) + 1
            node.name = spec["rename"]
            return node
        if "template" in spec:
            tmpl = spec["template"]
            if _max_placeholder(tmpl) >= len(node.args):
                return node  # arity mismatch: leave untouched
            s = tmpl
            for i, arg in enumerate(node.args):
                s = s.replace(f"${i}", "(" + E.unparse(arg) + ")")
            try:
                new = E.parse(s)
            except E.ExprError:
                return node
            used[node.name.lower()] = used.get(node.name.lower(), 0) + 1
            return new
        return node
    return rewrite


def apply(netlist: ir.Netlist, rules: dict, target: str) -> None:
    fmap = {k: v for k, v in rules["functions"].items() if not k.startswith("_")}
    used: Dict[str, int] = {}

    for e in ir.walk_exprs(netlist):
        try:
            tree = e.tree()
        except E.ExprError:
            netlist.warn(f"could not parse expression {e.text!r} — left verbatim")
            continue
        rewriter = _make_rewriter(fmap, used, target)
        for _ in range(6):                       # fixpoint
            before = E.unparse(tree)
            tree = E.map_nodes(tree, rewriter)
            if E.unparse(tree) == before:
                break
        e.set_tree(tree)

    for name, count in sorted(used.items()):
        spec = fmap[name]
        tgt = spec.get("rename") or spec.get("template")
        netlist.log("functions", f"{name}() -> {tgt} (x{count})")
