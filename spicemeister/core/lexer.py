# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""SPICE-aware lexing: physical lines -> folded logical lines.

Responsibilities:
  * fold ``+`` continuation lines onto the preceding card,
  * strip and capture comments (full-line ``*``; inline ``;`` ``//`` ``$``)
    without cutting inside ``{...}``, ``'...'`` or ``(...)``,
  * hand back ``.control``/``.endc`` and ``.protect``/``.unprotect`` spans
    verbatim as single raw/encrypted units so later stages do not mangle them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class LLine:
    no: int                 # 1-based physical line number where the card starts
    text: str               # folded code (comments removed) OR verbatim block
    kind: str = "card"      # card | comment | raw | encrypted
    comment: str = ""       # trailing inline comment captured from the card


def strip_comment(line: str, dollar_comment: bool = True) -> Tuple[str, str, bool]:
    """Return (code, comment, is_full_line_comment) for one physical line.

    A comment delimiter (``;`` anywhere, ``$`` preceded by space) is only
    protected when inside quotes — brace/paren nesting does NOT shield it, so
    ``.model M D(IS=1 $ note)`` strips ``$ note`` correctly. ``//`` is left
    alone here (Spectre-only; handled in the Spectre reader's preprocess).

    ``dollar_comment`` gates the ``$`` delimiter: it is a comment in HSPICE and
    SPICE3/ngspice, but in PSpice/OrCAD ``$`` begins a node name (``$N_0001``),
    so a PSpice reader passes ``False`` to avoid eating node names.
    """
    stripped = line.lstrip()
    if stripped[:1] == "*":
        return "", stripped[1:].rstrip(), True
    # A comment delimiter is only protected inside quotes; brace/paren nesting
    # does not shield it (e.g. `.model M D(IS=1 $ note)` -> `$ note` is a comment).
    quote = None           # active quote char or None
    i = 0
    n = len(line)
    prev_space = True      # start-of-line counts as preceded by space
    while i < n:
        c = line[i]
        if quote:
            if c == quote:
                quote = None
            i += 1
            prev_space = False
            continue
        if c in "'\"":
            quote = c
            i += 1
            prev_space = False
            continue
        if c == ";":
            return line[:i].rstrip(), line[i + 1:].strip(), False
        # Note: `//` is NOT treated as a comment here — only Spectre uses it, and
        # stripping it universally would truncate `//` inside e.g. an include path.
        # The Spectre reader strips `//` in its preprocess step.
        if c == "$" and prev_space and dollar_comment:
            return line[:i].rstrip(), line[i + 1:].strip(), False
        prev_space = c.isspace()
        i += 1
    return line.rstrip(), "", False


def _is_block_start(code: str) -> Optional[str]:
    low = code.lstrip().lower()
    if low.startswith(".control"):
        return "control"
    if low.startswith(".protect") or low.startswith(".prot "):
        return "protect"
    return None


def logical_lines(text: str, dollar_comment: bool = True) -> List[LLine]:
    """Tokenise a model/netlist into folded logical lines.

    ``dollar_comment`` is forwarded to :func:`strip_comment` (off for PSpice,
    where ``$`` starts a node name rather than a comment).
    """
    phys = text.splitlines()
    out: List[LLine] = []
    i = 0
    n = len(phys)
    last_card_idx: Optional[int] = None   # index into `out` of the last card

    while i < n:
        raw = phys[i]
        lineno = i + 1
        code, comment, is_full = strip_comment(raw, dollar_comment=dollar_comment)

        # Verbatim blocks: .control/.endc and .protect/.unprotect
        block = _is_block_start(code) if code else None
        if block:
            buf = [raw]
            end = ".endc" if block == "control" else ".unprotect"
            j = i + 1
            while j < n:
                buf.append(phys[j])
                if phys[j].lstrip().lower().startswith(end):
                    break
                j += 1
            kind = "raw" if block == "control" else "encrypted"
            out.append(LLine(lineno, "\n".join(buf), kind=kind))
            last_card_idx = None
            i = j + 1
            continue

        if is_full:
            out.append(LLine(lineno, "", kind="comment", comment=comment))
            # a comment does not break a pending continuation target
            i += 1
            continue

        if not code:
            i += 1
            continue

        # continuation: '+' as first non-space char
        if code.lstrip().startswith("+"):
            cont = code.lstrip()[1:].strip()
            if last_card_idx is not None:
                prev = out[last_card_idx]
                prev.text = (prev.text + " " + cont).strip()
                if comment:
                    prev.comment = comment     # trailing comment of the folded card
            else:
                out.append(LLine(lineno, cont, kind="card", comment=comment))
                last_card_idx = len(out) - 1
            i += 1
            continue

        out.append(LLine(lineno, code.strip(), kind="card", comment=comment))
        last_card_idx = len(out) - 1
        i += 1

    return out
