# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Heuristic SPICE-dialect detection.

Scores a model/netlist against weighted marker patterns and returns the most
likely source dialect together with the evidence lines, so a human (or the
report) can see *why* a dialect was chosen. ``spice3`` is the fallback when no
vendor marker scores above threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .lexer import logical_lines

# (compiled pattern, weight, human description) per dialect.
_MARKERS: Dict[str, List[Tuple[re.Pattern, float, str]]] = {
    "spectre": [
        (re.compile(r"\bsimulator\s+lang\s*=\s*spectre", re.I), 10.0, "simulator lang=spectre"),
        (re.compile(r"^\s*parameters\s+\w", re.I | re.M), 3.0, "spectre `parameters` block"),
        (re.compile(r"^\s*inline\s+subckt\b", re.I | re.M), 4.0, "inline subckt"),
        (re.compile(r"^\s*ends\s+\w", re.I | re.M), 2.0, "bare `ends name` (no dot)"),
        (re.compile(r"\bgnd!\b"), 2.0, "global net `gnd!`"),
        (re.compile(r"^\s*\w[\w]*\s*\([^)]*\)\s+\w+\s", re.M), 1.0, "name (nodes) master instance"),
    ],
    "hspice": [
        (re.compile(r"=\s*'"), 4.0, "single-quoted expression (='…')"),
        (re.compile(r"^\s*\.option[s]?\b", re.I | re.M), 2.0, ".option/.options"),
        (re.compile(r"\blevel\s*=\s*(49|53|54|57|64|66|69|70|72)\b", re.I), 3.0, "HSPICE BSIM level"),
        (re.compile(r"^\s*\.global\b", re.I | re.M), 1.5, ".global"),
        (re.compile(r"\$\s"), 1.0, "`$` inline comment style"),
        (re.compile(r"^\s*\.lib\s+'[^']+'\s+\w", re.I | re.M), 3.0, ".lib 'file' section"),
        (re.compile(r"\.temp\b", re.I), 0.5, ".temp"),
    ],
    "pspice": [
        (re.compile(r"\bvalue\s*=\s*\{", re.I), 4.0, "VALUE={…} behavioral source"),
        (re.compile(r"\btable\s*\{", re.I), 3.0, "TABLE{…}=(…) source"),
        (re.compile(r"^\s*\.func\b", re.I | re.M), 2.0, ".func"),
        (re.compile(r"^\s*\.(protect|prot)\b", re.I | re.M), 5.0, ".PROTECT (encrypted block)"),
        (re.compile(r"\blaplace\b", re.I), 2.0, "LAPLACE source"),
        (re.compile(r"\bchebyshev\b", re.I), 2.0, "CHEBYSHEV filter"),
        (re.compile(r"\b(pwrs|stp)\s*\(", re.I), 2.0, "PSpice fn PWRS()/STP()"),
        (re.compile(r"^\s*\.(probe|stimulus|stmlib|inc)\b", re.I | re.M), 1.0, "PSpice directive"),
        (re.compile(r"\{[^}']*\}"), 0.5, "brace-delimited expression"),
    ],
    "ltspice": [
        (re.compile(r"\bvdmos\b", re.I), 6.0, "VDMOS power-MOSFET model"),
        (re.compile(r"^\s*[bB]\w*\s+\S+\s+\S+\s+[IV]\s*=", re.M), 3.0, "B-source V=/I="),
        (re.compile(r"µ"), 1.0, "unicode µ scale factor"),
        (re.compile(r"\bLaplace\s*=", re.I), 1.0, "Laplace= on B/E source"),
        (re.compile(r"\.model\b.*\b(VDMOS|SW|CSW)\b", re.I), 1.5, "LTspice model type"),
    ],
}

_THRESHOLD = 3.0


@dataclass
class Detection:
    dialect: str
    confidence: float                       # 0..1
    scores: Dict[str, float] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)

    def summary(self) -> str:
        ev = "; ".join(self.evidence) if self.evidence else "no vendor markers"
        return f"{self.dialect} (confidence {self.confidence:.0%}) — {ev}"


def detect(text: str) -> Detection:
    """Detect the SPICE dialect of ``text``."""
    # Use folded code only (comments removed) so comment text does not vote.
    code = "\n".join(
        ll.text for ll in logical_lines(text) if ll.kind in ("card", "encrypted")
    )
    scores: Dict[str, float] = {d: 0.0 for d in _MARKERS}
    evidence: Dict[str, List[str]] = {d: [] for d in _MARKERS}

    for dialect, markers in _MARKERS.items():
        for pat, weight, desc in markers:
            hits = len(pat.findall(code))
            if hits:
                # diminishing returns: first hit full weight, extra hits half
                scores[dialect] += weight + 0.5 * weight * min(hits - 1, 4)
                evidence[dialect].append(f"{desc} (x{hits})")

    best = max(scores, key=lambda d: scores[d])
    best_score = scores[best]

    if best_score < _THRESHOLD:
        return Detection(
            dialect="spice3",
            confidence=0.5 if best_score else 0.9,
            scores=scores,
            evidence=["generic SPICE3: " + (evidence[best][0] if evidence[best] else "no strong markers")],
        )

    # confidence: margin of winner over runner-up, squashed to 0..1
    ordered = sorted(scores.values(), reverse=True)
    runner = ordered[1] if len(ordered) > 1 else 0.0
    margin = (best_score - runner) / best_score if best_score else 0.0
    confidence = min(1.0, 0.55 + 0.45 * margin)
    return Detection(best, confidence, scores, evidence[best])
