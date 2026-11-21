"""Serialise a :class:`Netlist` to LTspice / ngspice text.

A single rendering engine parameterised by a small :class:`Profile` (keyword
case, banner label). Expressions are wrapped in ``{...}`` unless atomic; lines
are wrapped with ``+`` continuations. Behavioural E/G sources marked for
conversion are emitted as B-sources.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import List, Optional

from .. import __version__
from ..core import expr as _exprmod
from ..core import ir

_WIDTH = 120


@dataclass
class Profile:
    name: str = "ltspice"
    kw_upper: bool = False          # emit .SUBCKT vs .subckt
    banner_label: str = "LTspice-compatible"


def _kw(profile: Profile, word: str) -> str:
    return word.upper() if profile.kw_upper else word.lower()


def _const_value(e) -> Optional[float]:
    """Return the constant float value of an Expr, handling a leading sign.

    A negative literal like ``-1`` parses as ``Unary('-', Num)``, not ``Num``,
    so ``is_number()`` alone misses it — which previously left tables with
    negative breakpoints unsorted (reversed segment bounds, wrong curve).
    """
    try:
        node = e.tree()
    except Exception:
        return None
    if isinstance(node, _exprmod.Num):
        return node.value
    if isinstance(node, _exprmod.Unary) and isinstance(node.operand, _exprmod.Num):
        v = node.operand.value
        return -v if node.op == "-" else v
    return None


def pwl_expr(ctrl: str, pairs: List) -> str:
    """Build a portable continuous piecewise-linear expression.

    y(c) = y0 + Σ slope_i * (clamp(c, x_i, x_{i+1}) - x_i),  clamp = max(a,min(b,·))

    Uses only ``max``/``min`` (not the LTspice-only ``table()``, and not
    ``limit()`` whose 3-arg clamp meaning is mode-dependent in bare ngspice), so
    the result is identical and correct in LTspice and in every ngspice mode.
    Numeric breakpoints are sorted ascending (including negatives) so each
    segment has lo < hi; non-numeric (parameter) breakpoints are kept in the
    given order and must already be ascending.
    """
    pts = list(pairs)
    if not pts:
        return "0"
    vals = [_const_value(x) for x, _ in pts]
    if all(v is not None for v in vals):
        pts = [p for _, p in sorted(zip(vals, pts), key=lambda t: t[0])]
    c = f"({ctrl})"
    terms = [f"({pts[0][1].text})"]
    for i in range(len(pts) - 1):
        xi, yi = pts[i][0].text, pts[i][1].text
        xj, yj = pts[i + 1][0].text, pts[i + 1][1].text
        vi, vj = vals[i], vals[i + 1]
        if vi is not None and vj is not None and vi == vj:
            # Repeated breakpoint x == a vertical step (PSpice/HSPICE allow it).
            # A continuous-PWL slope term would divide by zero here; emit a unit
            # step of the jump height instead. u(x) is 1 for x>0 in ngspice and
            # LTspice, so the output jumps by (yj-yi) as the control crosses xi.
            terms.append(f"(({yj})-({yi}))*u({c}-({xi}))")
        else:
            terms.append(
                f"(({yj})-({yi}))/(({xj})-({xi}))*(max({xi},min({xj},{c}))-({xi}))"
            )
    return " + ".join(terms)


def _wrap(tokens: List[str], width: int = _WIDTH) -> List[str]:
    """Join tokens onto lines, breaking with leading '+' continuations."""
    lines: List[str] = []
    cur = ""
    for t in tokens:
        if not cur:
            cur = t
        elif len(cur) + 1 + len(t) <= width:
            cur += " " + t
        else:
            lines.append(cur)
            cur = "+ " + t
    if cur:
        lines.append(cur)
    return lines


class Writer:
    def __init__(self, profile: Profile):
        self.profile = profile

    # public ----------------------------------------------------------------
    def render(self, netlist: ir.Netlist, banner: bool = True) -> str:
        out: List[str] = []
        if banner:
            out.extend(self._banner(netlist))
        for card in netlist.cards:
            out.extend(self._card(card, indent=""))
        text = "\n".join(out).rstrip() + "\n"
        return text

    # banner ----------------------------------------------------------------
    def _banner(self, netlist: ir.Netlist) -> List[str]:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            "* " + "-" * 68,
            f"* Translated by SpiceMeister {__version__} -> {self.profile.banner_label}",
            f"* Source dialect : {netlist.source_dialect}"
            + (f"   ({netlist.source_name})" if netlist.source_name else ""),
            f"* Generated      : {now}",
        ]
        if netlist.changelog:
            lines.append("* Changes        :")
            for ch in netlist.changelog:
                lines.append(f"*   - [{ch.pass_name}] {ch.what}")
        if netlist.warnings:
            lines.append("* Warnings       :")
            for w in netlist.warnings:
                lines.append(f"*   ! {w}")
        lines.append("* " + "-" * 68)
        return lines

    # cards -----------------------------------------------------------------
    def _card(self, card: ir.Card, indent: str) -> List[str]:
        if isinstance(card, ir.CommentCard):
            return [indent + "*" + ("" if card.text.startswith(" ") else " ") + card.text]
        if isinstance(card, ir.RawCard):
            return [indent + line for line in card.text.splitlines()]
        if isinstance(card, ir.EncryptedCard):
            head = indent + "* [spicemeister] untranslated encrypted/protected span (" + card.reason + "):"
            body = [indent + "* | " + ln for ln in card.text.splitlines()]
            return [head] + body
        if isinstance(card, ir.ParamCard):
            toks = [_kw(self.profile, ".param")]
            toks += [f"{k}={v.render()}" for k, v in card.items.items()]
            return self._emit(toks, indent, card.comment)
        if isinstance(card, ir.FuncCard):
            body = card.body.text if card.body else ""
            toks = [_kw(self.profile, ".func"),
                    f"{card.name}({','.join(card.args)})", "{" + body + "}"]
            return self._emit(toks, indent, card.comment)
        if isinstance(card, ir.GlobalCard):
            return self._emit([_kw(self.profile, ".global")] + card.nodes, indent, card.comment)
        if isinstance(card, ir.IncludeCard):
            if card.kind == "lib":
                toks = [_kw(self.profile, ".lib"), card.path] + ([card.section] if card.section else [])
            else:
                toks = [_kw(self.profile, ".include"), card.path]
            return self._emit(toks, indent, card.comment)
        if isinstance(card, ir.OptionCard):
            # Preserve as a reviewable comment: vendor .option rarely maps 1:1.
            return [indent + f"* [spicemeister] source .option (review): {card.text}"]
        if isinstance(card, ir.ModelCard):
            return self._model(card, indent)
        if isinstance(card, ir.SubcktCard):
            return self._subckt(card, indent)
        if isinstance(card, ir.InstanceCard):
            return self._instance(card, indent)
        return [indent + f"* [spicemeister] unhandled card {type(card).__name__}"]

    def _emit(self, tokens: List[str], indent: str, comment: str) -> List[str]:
        lines = _wrap(tokens)
        if comment and lines:
            lines[-1] = lines[-1] + " ; " + comment
        return [indent + ln for ln in lines]

    def _model(self, card: ir.ModelCard, indent: str) -> List[str]:
        params = " ".join(f"{k}={v.render()}" for k, v in card.params.items())
        head = f"{_kw(self.profile, '.model')} {card.name} {card.mtype}"
        toks = [head, "(" + params + ")"] if params else [head, "()"]
        # keep ".model NAME TYPE(" on one token group; wrap params if long
        single = f"{head}({params})"
        if len(indent) + len(single) <= _WIDTH:
            lines = [single]
        else:
            lines = _wrap([head + "("] + [f"{k}={v.render()}" for k, v in card.params.items()])
            lines.append("+ )")
        if card.comment:
            lines[-1] += " ; " + card.comment
        return [indent + ln for ln in lines]

    def _subckt(self, card: ir.SubcktCard, indent: str) -> List[str]:
        toks = [_kw(self.profile, ".subckt"), card.name] + list(card.nodes)
        if card.params:
            toks.append("PARAMS:")
            toks += [f"{k}={v.render()}" for k, v in card.params.items()]
        out = self._emit(toks, indent, card.comment)
        inner = indent + "  "
        for b in card.body:
            out.extend(self._card(b, inner))
        out.extend([indent + f"{_kw(self.profile, '.ends')} {card.name}"])
        return out

    def _instance(self, card: ir.InstanceCard, indent: str) -> List[str]:
        beh = card.behavioral
        if beh and beh.extra.get("emit") == "bsource":
            return self._bsource(card, indent)
        # generic / passthrough: refdes + positional tokens + key=value params
        toks = [card.refdes]
        for p in card.pos:
            toks.append(p.render() if isinstance(p, ir.Expr) else p)
        toks += [f"{k}={v.render()}" for k, v in card.params.items()]
        return self._emit(toks, indent, card.comment)

    def _bsource(self, card: ir.InstanceCard, indent: str) -> List[str]:
        beh = card.behavioral
        name = card.refdes if card.dev == "B" else "B" + card.refdes
        np_, nn = (beh.out + ["0", "0"])[:2]
        if beh.kind == "table":
            ctrl = beh.expr.text if beh.expr else "0"
            expr = pwl_expr(ctrl, beh.table)
        else:
            expr = beh.expr.text if beh.expr else "0"
        toks = [name, np_, nn, f"{beh.out_kind}={{{expr}}}"]
        cmt = card.comment
        note = f"was {card.dev}{card.refdes[1:] if card.dev!='B' else ''} ({beh.kind})"
        cmt = (cmt + "  " if cmt else "") + note
        return self._emit(toks, indent, cmt)
