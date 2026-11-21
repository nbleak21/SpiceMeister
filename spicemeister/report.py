"""Human-readable Markdown translation report."""

from __future__ import annotations

from typing import List

from . import __version__
from .core import ir
from .core.dialect import Detection


def build_report(netlist: ir.Netlist, detection: Detection, profile: str,
                 source_name: str = "") -> str:
    out: List[str] = []
    out.append(f"# SpiceMeister translation report")
    out.append("")
    out.append(f"- **Source**: `{source_name or netlist.source_name or '(stdin)'}`")
    out.append(f"- **Detected dialect**: {detection.summary()}")
    out.append(f"- **Output profile**: `{profile}`")
    out.append(f"- **SpiceMeister**: v{__version__}")
    out.append("")

    subs = netlist.subckts()
    models = netlist.models()
    out.append("## Inventory")
    out.append("")
    out.append(f"- Subcircuits: {len(subs)}")
    for s in subs:
        params = ", ".join(s.params) if s.params else "—"
        out.append(f"  - `{s.name}` ({len(s.nodes)} terminals: {' '.join(s.nodes)}) · params: {params}")
    out.append(f"- Models: {len(models)}")
    for m in models:
        out.append(f"  - `.model {m.name} {m.mtype}` ({len(m.params)} params)")
    out.append("")

    out.append("## Changes applied")
    out.append("")
    if netlist.changelog:
        for ch in netlist.changelog:
            where = f" — {ch.where}" if ch.where else ""
            out.append(f"- **[{ch.pass_name}]** {ch.what}{where}")
    else:
        out.append("- (none — source already compatible)")
    out.append("")

    enc = [c for c in ir.walk_cards(netlist) if isinstance(c, ir.EncryptedCard)]
    if enc:
        out.append("## Encrypted / protected spans")
        out.append("")
        out.append(f"- {len(enc)} span(s) detected and **not** translated (kept verbatim, "
                   "commented). Run `smeister scan` for details / REA bridge.")
        out.append("")

    out.append("## Warnings")
    out.append("")
    if netlist.warnings:
        for w in netlist.warnings:
            out.append(f"- ⚠️ {w}")
    else:
        out.append("- (none)")
    out.append("")
    return "\n".join(out)
