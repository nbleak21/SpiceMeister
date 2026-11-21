"""Emit a ready-to-paste Zener ``SpiceModel(...)`` block for pcb-stack.

Given a translated ``.subckt``, produce the ``spice_model=SpiceModel(...)``
snippet a user drops into a ``components/<Part>/<Part>.zen`` wrapper: ``nets``
in exact ``.subckt`` terminal order, ``args`` carrying every ``PARAMS:`` entry
(pcb-stack applies no defaults, so all must be passed).
"""

from __future__ import annotations

import re

from ..core import ir


def _ident(node: str) -> str:
    s = re.sub(r"[^0-9A-Za-z_]", "_", node)
    if s and s[0].isdigit():
        s = "n" + s
    return s.upper() or "NET"


def render_snippet(subckt: ir.SubcktCard, lib_relpath: str, part: str = "") -> str:
    order = " ".join(subckt.nodes)
    nets = ", ".join(_ident(n) for n in subckt.nodes)
    args_items = ", ".join(f'"{k}": "{v.text}"' for k, v in subckt.params.items())
    part = part or subckt.name
    lines = [
        f"# --- SpiceMeister: paste into components/{part}/{part}.zen ---",
        f"# .subckt {subckt.name} terminal order: {order}",
        "# Replace the placeholder NET names below with your actual design nets,",
        "# keeping them in the terminal order shown above.",
        "spice_model=SpiceModel(",
        f'    "{lib_relpath}",',
        f'    "{subckt.name}",',
        f"    nets=[{nets}],",
        f"    args={{{args_items}}},",
        "),",
    ]
    return "\n".join(lines)
