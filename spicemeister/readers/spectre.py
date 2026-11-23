# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Spectre (Cadence) reader — structural best-effort (v0.1 partial).

Spectre's native language is not SPICE. This reader performs a conservative,
line-oriented lift of the *structural* subset into SPICE form so the rest of
the pipeline can process it, and warns about anything it does not confidently
map (notably primitive-device instances and SKILL/analysis statements, which
are v0.2 work). Spectre libraries shipped with a ``simulator lang=spice``
section are handled well because that section is already SPICE.
"""

from __future__ import annotations

import re

from .base import BaseReader


class SpectreReader(BaseReader):
    dialect = "spectre"

    def preprocess(self, text: str) -> str:
        # strip /* ... */ block comments, then // line comments (Spectre-only)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        out_lines = []
        for raw in text.splitlines():
            line = re.sub(r"//.*$", "", raw)
            low = line.strip().lower()

            # drop language pragmas
            if low.startswith("simulator lang"):
                out_lines.append("* [spicemeister] dropped: " + line.strip())
                continue

            # global nets:  gnd!  ->  0
            line = re.sub(r"\bgnd!\b", "0", line)
            line = re.sub(r"(\w+)!", r"\1", line)   # other global '!' suffixes

            # parameters a=1 b=2  ->  .param a=1 b=2
            if re.match(r"\s*parameters\s+\w", line, re.I):
                line = re.sub(r"^\s*parameters\b", ".param", line, flags=re.I)
                out_lines.append(line)
                continue

            # (inline) subckt name (n1 n2 ...)  ->  .subckt name n1 n2 ...
            m = re.match(r"\s*(?:inline\s+)?subckt\s+(\S+)\s*(.*)$", line, re.I)
            if m:
                name, nodes = m.group(1), m.group(2).strip()
                nodes = nodes.strip("()")
                out_lines.append(f".subckt {name} {nodes}".rstrip())
                continue

            # ends [name]  ->  .ends [name]
            m = re.match(r"\s*ends\b\s*(.*)$", line, re.I)
            if m:
                out_lines.append(f".ends {m.group(1).strip()}".rstrip())
                continue

            # model name type p=v ...  ->  .model name type (p=v ...)
            m = re.match(r"\s*model\s+(\S+)\s+(\S+)\s*(.*)$", line, re.I)
            if m:
                name, mtype, params = m.group(1), m.group(2), m.group(3).strip()
                out_lines.append(f".model {name} {mtype} ({params})")
                continue

            # include "file"  ->  .include file
            m = re.match(r'\s*include\s+"([^"]+)"', line, re.I)
            if m:
                out_lines.append(f".include {m.group(1)}")
                continue

            out_lines.append(line)
        return "\n".join(out_lines)

    def read(self, text: str, source_name: str = ""):
        nl = super().read(text, source_name)
        nl.warn("Spectre support is structural best-effort (v0.1): primitive-"
                "device instances and analyses may need manual review.")
        return nl
