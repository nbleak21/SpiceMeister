"""Detect encrypted / protected / opaque model content.

SpiceMeister **detects and reports** vendor encryption; it does not attempt to
circumvent it. Findings flag:
  * ``.PROTECT``/``.UNPROTECT`` (PSpice) and HSPICE ``.prot`` spans,
  * Verilog-A / Verilog-AMS source (does not run in LTspice/ngspice),
  * compiled OSDI / OpenVAF modules,
  * binary containers (low printable ratio) -> candidates for the REA bridge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

_RE_PROTECT = re.compile(r"^\s*\.(protect|prot)\b", re.I | re.M)
_RE_VAMS = re.compile(r"`include\s+\"disciplines\.vams\"|(^|\n)\s*analog\s+(begin|function)|"
                      r"(^|\n)\s*module\s+\w+\s*\(", re.I)
_RE_OSDI = re.compile(r"\.osdi\b|openvaf", re.I)
# Only flag a .va/.vams reference in an include context, so a mention like
# "* see model.va" in a comment does not trip a false Verilog-A finding.
_RE_VA_INC = re.compile(r"(?:ahdl_include|include|\.hdl)\s+[\"']?[\w./-]+\.va(?:ms)?\b", re.I)
_RE_SPICE = re.compile(r"^\s*\.(subckt|model)\b", re.I | re.M)


@dataclass
class Finding:
    kind: str
    detail: str
    where: str = ""
    suggestion: str = ""


@dataclass
class ScanReport:
    path: str = ""
    findings: List[Finding] = field(default_factory=list)
    printable_ratio: float = 1.0
    is_binary: bool = False
    has_spice_text: bool = False

    @property
    def clean(self) -> bool:
        return not self.findings and not self.is_binary

    def summary(self) -> str:
        if self.clean:
            return "clean: plain-text SPICE, no encrypted/opaque content detected"
        bits = [f"{f.kind}" for f in self.findings]
        if self.is_binary:
            bits.append(f"binary container (printable {self.printable_ratio:.0%})")
        return "; ".join(bits)


def scan_text(text: str, path: str = "") -> ScanReport:
    rep = ScanReport(path=path)
    rep.has_spice_text = bool(_RE_SPICE.search(text))

    prot = list(_RE_PROTECT.finditer(text))
    if prot:
        rep.findings.append(Finding(
            "encrypted-span",
            f"{len(prot)} .PROTECT/.prot span(s): model body is vendor-encrypted and will "
            "not simulate.",
            suggestion="Obtain an unencrypted/behavioral model from the vendor, or if this is "
                       "a binary container you are entitled to analyse, use the REA bridge "
                       "(`smeister scan --rea <file>`)."))
    if _RE_VAMS.search(text) or _RE_VA_INC.search(text):
        rep.findings.append(Finding(
            "verilog-a",
            "Verilog-A / Verilog-AMS source detected: does not run in LTspice/ngspice "
            "without a compiled OSDI module.",
            suggestion="Use the vendor's SPICE (macromodel) equivalent, or compile the "
                       "Verilog-A with OpenVAF to OSDI for ngspice (not LTspice)."))
    if _RE_OSDI.search(text):
        rep.findings.append(Finding(
            "osdi-compiled",
            "Reference to a compiled OSDI/OpenVAF module: binary, not translatable as text.",
            suggestion="Inspect the .osdi container with the REA bridge if interop analysis "
                       "is authorised."))
    return rep


def scan_file(path: str) -> ScanReport:
    with open(path, "rb") as fh:
        data = fh.read()
    if not data:
        return ScanReport(path=path)
    # Printable ratio: count valid non-ASCII UTF-8 (µ, Ω, Greek) as text, not
    # binary — only truly non-text bytes should pull a text model below 0.85.
    printable_ascii = sum(1 for b in data if b in (9, 10, 13) or 32 <= b < 127)
    try:
        data.decode("utf-8")
        ratio = (printable_ascii + sum(1 for b in data if b >= 128)) / len(data)
    except UnicodeDecodeError:
        ratio = printable_ascii / len(data)
    # decode leniently for text scanning
    text = data.decode("utf-8", errors="replace")
    rep = scan_text(text, path=path)
    rep.printable_ratio = ratio
    rep.is_binary = ratio < 0.85
    if rep.is_binary:
        rep.findings.append(Finding(
            "binary-container",
            f"File is {1 - ratio:.0%} non-printable — a binary/compiled container, not a "
            "text SPICE model.",
            suggestion="Characterise it with the REA bridge (`smeister scan --rea <file>`): "
                       "entropy, embedded strings, and extractable members."))
    return rep
