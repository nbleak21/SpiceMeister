# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Base SPICE reader: folded logical lines -> :class:`Netlist` IR.

Handles the grammar shared by SPICE3 / ngspice / PSpice / HSPICE / LTspice:
``.model``, ``.subckt``/``.ends``, ``.param``, ``.func``, ``.global``,
``.include``/``.lib``, ``.option``, instances, and E/G/B behavioural sources.
Expression delimiters ``{...}`` and ``'...'`` are both accepted and stripped so
the IR stores brace-free expressions; dialect subclasses tweak only the bits
that genuinely differ.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import List, Optional, Tuple

from ..core import ir
from ..core.lexer import LLine, logical_lines

_WORD = r"[A-Za-z_][\w.#]*"
_BEHAV_KW = {"VALUE", "VOL", "CUR", "TABLE", "POLY", "LAPLACE", "FREQ", "CHEBYSHEV"}


# --------------------------------------------------------------------------- #
# field splitting helpers
# --------------------------------------------------------------------------- #
def split_fields(s: str) -> List[str]:
    """Split on whitespace while keeping {...} (...) '...' \"...\" balanced."""
    out: List[str] = []
    buf: List[str] = []
    depth = 0
    quote = None
    for c in s:
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
            continue
        if c in "{(":
            depth += 1
            buf.append(c)
            continue
        if c in "})":
            depth = max(0, depth - 1)
            buf.append(c)
            continue
        if c.isspace() and depth == 0:
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(c)
    if buf:
        out.append("".join(buf))
    return out


def merge_eq(tokens: List[str]) -> List[str]:
    """Merge ``a = b`` / ``a=`` ``b`` / ``a`` ``=b`` into ``a=b``."""
    out: List[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "=" and out and i + 1 < len(tokens):
            out[-1] = out[-1] + "=" + tokens[i + 1]
            i += 2
            continue
        if t.endswith("=") and i + 1 < len(tokens) and "=" not in tokens[i + 1]:
            out.append(t + tokens[i + 1])
            i += 2
            continue
        if t.startswith("=") and out:
            out[-1] = out[-1] + t
            i += 1
            continue
        out.append(t)
        i += 1
    return out


def strip_delims(tok: str) -> Tuple[str, bool]:
    """Strip an outer {...} or '...' / "..." wrapper. Returns (inner, was_expr)."""
    t = tok.strip()
    if len(t) >= 2 and t[0] == "{" and t[-1] == "}":
        return t[1:-1].strip(), True
    if len(t) >= 2 and t[0] in "'\"" and t[-1] == t[0]:
        return t[1:-1].strip(), True
    return t, False


def as_expr(tok: str) -> ir.Expr:
    inner, was = strip_delims(tok)
    return ir.Expr(inner, forced_brace=False)


def parse_kv(fields: List[str]) -> "OrderedDict[str, ir.Expr]":
    """Parse ``key=value`` fields into an ordered {name: Expr} map."""
    out: "OrderedDict[str, ir.Expr]" = OrderedDict()
    for f in fields:
        if "=" not in f:
            continue
        k, v = f.split("=", 1)
        out[k.strip()] = as_expr(v)
    return out


# --------------------------------------------------------------------------- #
# reader
# --------------------------------------------------------------------------- #
class BaseReader:
    dialect = "spice3"
    dollar_comment = True       # '$' starts an inline comment (HSPICE/SPICE3/ngspice)

    def __init__(self):
        self.netlist = ir.Netlist(source_dialect=self.dialect)

    # public ----------------------------------------------------------------
    def read(self, text: str, source_name: str = "") -> ir.Netlist:
        self.netlist = ir.Netlist(source_dialect=self.dialect, source_name=source_name)
        lines = logical_lines(self.preprocess(text), dollar_comment=self.dollar_comment)
        self._consume(lines, 0, self.netlist.cards, top=True)
        return self.netlist

    def preprocess(self, text: str) -> str:
        """Dialect hook to rewrite raw text before lexing (default: no-op)."""
        return text

    # core recursive consumer ------------------------------------------------
    def _consume(self, lines: List[LLine], start: int, dest: List[ir.Card], top: bool) -> int:
        i = start
        while i < len(lines):
            ll = lines[i]
            if ll.kind == "comment":
                dest.append(ir.CommentCard(lineno=ll.no, text=ll.comment))
                i += 1
                continue
            if ll.kind == "encrypted":
                dest.append(ir.EncryptedCard(lineno=ll.no, text=ll.text,
                                             reason=".PROTECT/.UNPROTECT encrypted span"))
                i += 1
                continue
            if ll.kind == "raw":
                dest.append(ir.RawCard(lineno=ll.no, text=ll.text, note=".control block"))
                i += 1
                continue

            code = ll.text
            low = code.lower()

            # Section/scope terminators. Match EXACT tokens — a prefix test like
            # ``.startswith(".end")`` wrongly swallows ``.endl`` (end of a .lib
            # section) and would truncate everything after it.
            if low == ".ends" or low.startswith(".ends "):
                if not top:
                    return i + 1          # consumed the subckt's .ends
                i += 1                    # stray top-level .ends: ignore
                continue
            if low == ".end":
                return i + 1              # end of deck — unwinds to the top loop
            # ``.endl`` / other ``.end*`` fall through to directive passthrough.

            if low.startswith(".subckt"):
                card, body_start = self._parse_subckt_header(ll)
                i += 1
                i = self._consume(lines, i, card.body, top=False)
                dest.append(card)
                continue

            if code.startswith("."):
                dest.append(self._parse_directive(ll))
                i += 1
                continue

            dest.append(self._parse_instance(ll))
            i += 1
        return i

    # directives -------------------------------------------------------------
    def _parse_subckt_header(self, ll: LLine):
        fields = merge_eq(split_fields(ll.text))
        # fields[0] == '.subckt'
        if len(fields) < 2:
            self.netlist.warn(f"line {ll.no}: .subckt with no name — using 'UNNAMED'")
            name = "UNNAMED"
        else:
            name = fields[1]
        nodes: List[str] = []
        params: "OrderedDict[str, ir.Expr]" = OrderedDict()
        j = 2
        in_params = False
        while j < len(fields):
            f = fields[j]
            if f.upper().rstrip(":") == "PARAMS":
                in_params = True
                j += 1
                continue
            if "=" in f:
                in_params = True
            if in_params and "=" in f:
                k, v = f.split("=", 1)
                params[k.strip()] = as_expr(v)
            elif not in_params:
                nodes.append(f)
            j += 1
        return ir.SubcktCard(lineno=ll.no, name=name, nodes=nodes,
                             params=params, body=[], comment=ll.comment), j

    def _parse_directive(self, ll: LLine) -> ir.Card:
        fields = split_fields(ll.text)
        head = fields[0].lower()
        rest = ll.text[len(fields[0]):].strip()

        if head == ".model":
            return self._parse_model(ll, fields)
        if head == ".param":
            items = parse_kv(merge_eq(fields[1:]))
            return ir.ParamCard(lineno=ll.no, items=items, comment=ll.comment)
        if head == ".func":
            return self._parse_func(ll, rest)
        if head == ".global":
            return ir.GlobalCard(lineno=ll.no, nodes=fields[1:], comment=ll.comment)
        if head in (".include", ".inc"):
            path, _ = strip_delims(rest)
            return ir.IncludeCard(lineno=ll.no, path=path, kind="include", comment=ll.comment)
        if head == ".lib":
            parts = merge_eq(split_fields(rest))
            # Two forms: `.lib FILE SECTION` includes a section (2+ args), while
            # `.lib SECTION` OPENS a named section block closed by `.endl`. The
            # opener (and its `.endl`) are valid ngspice/LTspice cards, so pass
            # them through verbatim rather than mangling the opener into a
            # (broken) single-file include.
            if len(parts) >= 2:
                path, _ = strip_delims(parts[0])
                return ir.IncludeCard(lineno=ll.no, path=path, section=parts[1],
                                      kind="lib", comment=ll.comment)
            return ir.RawCard(lineno=ll.no, text=ll.text, comment=ll.comment,
                              note="library section opener (.lib SECTION)")
        if head == ".endl":
            return ir.RawCard(lineno=ll.no, text=ll.text, comment=ll.comment,
                              note="library section end (.endl)")
        if head in (".option", ".options"):
            return ir.OptionCard(lineno=ll.no, text=rest, comment=ll.comment)
        # unknown directive: keep verbatim
        return ir.RawCard(lineno=ll.no, text=ll.text, comment=ll.comment,
                          note=f"passthrough directive {head}")

    def _parse_model(self, ll: LLine, fields: List[str]):
        # .model NAME TYPE (p=v ...)  |  .model NAME TYPE p=v ...
        if len(fields) < 3:
            self.netlist.warn(f"line {ll.no}: malformed .model (need NAME and TYPE) — kept verbatim")
            return ir.RawCard(lineno=ll.no, text=ll.text, comment=ll.comment,
                              note="malformed .model")
        name = fields[1]
        # everything after name, re-joined, to deal with "TYPE(" glued forms
        after = ll.text.split(None, 2)[2] if len(ll.text.split(None, 2)) > 2 else ""
        m = re.match(r"\s*([A-Za-z]\w*)\s*(.*)$", after, re.S)
        mtype = m.group(1) if m else ""
        body = m.group(2).strip() if m else ""
        # strip wrapping parens independently: a trailing comment can consume the
        # closing ')' on the physical line, leaving only the leading '('.
        if body.startswith("("):
            body = body[1:]
        if body.endswith(")"):
            body = body[:-1]
        params = parse_kv(merge_eq(split_fields(body)))
        return ir.ModelCard(lineno=ll.no, name=name, mtype=mtype,
                            params=params, comment=ll.comment)

    def _parse_func(self, ll: LLine, rest: str) -> ir.Card:
        # .func name(a,b) {body}  |  .func name(a,b)=body  |  .func name(a,b) body
        m = re.match(r"\s*(" + _WORD + r")\s*\(([^)]*)\)\s*=?\s*(.*)$", rest, re.S)
        if not m:
            return ir.RawCard(lineno=ll.no, text=ll.text, note="unparsed .func")
        name = m.group(1)
        args = [a.strip() for a in m.group(2).split(",") if a.strip()]
        body_tok = m.group(3).strip()
        inner, _ = strip_delims(body_tok)
        return ir.FuncCard(lineno=ll.no, name=name, args=args,
                           body=ir.Expr(inner), comment=ll.comment)

    # instances --------------------------------------------------------------
    def _parse_instance(self, ll: LLine) -> ir.Card:
        dev = ll.text[0].upper()
        if dev in ("E", "G", "B"):
            card = self._parse_behavioral(ll, dev)
            if card is not None:
                return card
        # generic instance: refdes + positional tokens + key=value params
        fields = merge_eq(split_fields(ll.text))
        refdes = fields[0]
        pos: List[ir.PosTok] = []
        params: "OrderedDict[str, ir.Expr]" = OrderedDict()
        for f in fields[1:]:
            if "=" in f and not f.startswith("{") and not f.startswith("("):
                k, v = f.split("=", 1)
                params[k.strip()] = as_expr(v)
            else:
                inner, was = strip_delims(f)
                pos.append(as_expr(f) if was else f)
        return ir.InstanceCard(lineno=ll.no, refdes=refdes, dev=dev,
                               pos=pos, params=params, comment=ll.comment)

    def _parse_behavioral(self, ll: LLine, dev: str) -> Optional[ir.InstanceCard]:
        text = ll.text
        fields0 = split_fields(text)
        refdes = fields0[0]
        up = text.upper()

        # --- B-source: Bname n+ n- V={..}/I={..} ---------------------------
        if dev == "B":
            m = re.match(r"\S+\s+(\S+)\s+(\S+)\s+(.*)$", text, re.S)
            if not m:
                return None
            np_, nn = m.group(1), m.group(2)
            body = m.group(3).strip()
            km = re.match(r"([IViv])\s*=\s*(.*)$", body)
            if not km:
                return None
            out_kind = km.group(1).upper()
            inner, _ = strip_delims(km.group(2).strip())
            beh = ir.Behavioral(kind="bsource", out=[np_, nn], out_kind=out_kind,
                                expr=ir.Expr(inner))
            return ir.InstanceCard(lineno=ll.no, refdes=refdes, dev=dev,
                                   pos=[np_, nn], behavioral=beh, comment=ll.comment)

        # --- E / G ---------------------------------------------------------
        default_kind = "V" if dev == "E" else "I"

        # TABLE: Exxx n+ n- TABLE {ctrl} = (x,y)(x,y)...
        # ``\b`` after TABLE so a control node merely starting with "table"
        # (e.g. ``tableref``) is not mistaken for the keyword.
        mt = re.match(r"\S+\s+(\S+)\s+(\S+)\s+TABLE\b\s*(\{[^}]*\}|'[^']*'|\S+)\s*=\s*(.*)$",
                      text, re.I | re.S)
        if mt:
            np_, nn = mt.group(1), mt.group(2)
            ctrl_inner, _ = strip_delims(mt.group(3).strip())
            pairs = re.findall(r"\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)", mt.group(4))
            table = [(ir.Expr(strip_delims(x)[0]), ir.Expr(strip_delims(y)[0]))
                     for x, y in pairs]
            beh = ir.Behavioral(kind="table", out=[np_, nn], out_kind=default_kind,
                                expr=ir.Expr(ctrl_inner), table=table)
            return ir.InstanceCard(lineno=ll.no, refdes=refdes, dev=dev,
                                   pos=[np_, nn], behavioral=beh, comment=ll.comment)

        # VALUE / VOL / CUR = {expr}
        # ``\b`` + a required ``=`` so a plain VCVS/VCCS whose control node
        # starts with these letters (e.g. ``E1 out 0 curr 0 2``, node "curr")
        # is NOT misread as a behavioural source — that silently turned a linear
        # controlled source into a B-source with a garbage expression.
        mv = re.match(r"\S+\s+(\S+)\s+(\S+)\s+(VALUE|VOL|CUR)\b\s*=\s*(.*)$",
                      text, re.I | re.S)
        if mv:
            np_, nn = mv.group(1), mv.group(2)
            key = mv.group(3).upper()
            out_kind = "I" if key == "CUR" else ("V" if key == "VOL" else default_kind)
            inner, _ = strip_delims(mv.group(4).strip())
            beh = ir.Behavioral(kind="value", out=[np_, nn], out_kind=out_kind,
                                expr=ir.Expr(inner))
            return ir.InstanceCard(lineno=ll.no, refdes=refdes, dev=dev,
                                   pos=[np_, nn], behavioral=beh, comment=ll.comment)

        # POLY / LAPLACE / FREQ / CHEBYSHEV: pass through verbatim with a note
        for kw in ("POLY", "LAPLACE", "FREQ", "CHEBYSHEV"):
            if re.search(r"\b" + kw + r"\b", up):
                note = {"POLY": "POLY source kept verbatim (portable).",
                        "LAPLACE": "LAPLACE source kept verbatim — verify in LTspice.",
                        "FREQ": "FREQ source kept verbatim — verify in LTspice.",
                        "CHEBYSHEV": "CHEBYSHEV filter kept verbatim — verify in LTspice."}[kw]
                if kw != "POLY":
                    self.netlist.warn(f"{refdes}: {note}")
                return ir.InstanceCard(
                    lineno=ll.no, refdes=refdes, dev=dev,
                    pos=[as_expr(f) if strip_delims(f)[1] else f for f in fields0[1:]],
                    behavioral=ir.Behavioral(kind=kw.lower(), note=note), comment=ll.comment)

        # linear VCVS/VCCS: Exxx out+ out- in+ in- gain  -> keep as generic
        return None
