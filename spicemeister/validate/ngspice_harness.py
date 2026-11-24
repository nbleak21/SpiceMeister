# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Round-trip validation through ngspice.

Checks whether a translation preserves behaviour by building an operating-point
bench around a ``.subckt`` and comparing the *source* model (run under its
native ``ngbehavior`` compat mode) against the *translated* model (run under
``ltpsa`` for the LTspice profile). ``ngbehavior`` is set via a ``.spiceinit``
written beside the deck, exactly as ``pcb sim`` does, because it must take
effect at parse time.

What it observes, and what a verdict means
------------------------------------------
Every ``.subckt`` terminal is driven by a DC source through a series resistor
(so the bench never fights an internal ideal source → no singular matrix), and
each terminal is biased to a **distinct non-zero** voltage by default so that
rail-dependent and multiplicative behaviour is actually exercised. The bench
compares BOTH terminal currents AND internal node voltages, so a mistranslation
on an internal node (not just a terminal) is visible.

Verdicts are deliberately conservative — a PASS only ever means "verified
equivalent at this bias":

* ``pass``          — source and translation both simulated, a material signal
                      was exercised, and every material probe agrees within tol.
* ``fail``          — a material probe diverged.
* ``inconclusive``  — nothing material was exercised (e.g. a rail left unbiased);
                      supply ``--ports``. **Not** a pass.
* ``not-verified``  — the source is not natively simulable (Spectre, encrypted,
                      vendor primitives): the translation loads, but NO
                      equivalence was established. **Not** a pass.
* ``error``         — the translation failed to simulate.
* ``skipped``       — ngspice unavailable.

Files with no ``.subckt`` but bare semiconductor ``.model`` cards (D, NPN/PNP,
NMOS/PMOS, NJF/PJF, VDMOS) are benched by instantiating each model in a
generated fixture at several bias points and comparing DC terminal currents
plus small-signal AC currents (so capacitance/transit-time params are checked);
see ``_validate_models``. Other model types still get only a load check.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .. import readers
from ..core import ir

_MODE = {"pspice": "psa", "hspice": "hsa", "ltspice": "ltpsa",
         "spice3": "spice3", "spectre": "", "ngspice": "", "": ""}

_VAL = re.compile(r"^\s*([a-zA-Z0-9_().,@#]+)\s*=\s*([-+0-9.eE]+)\s*$")

# Phrases that mean ngspice actually ABORTED (as opposed to a recoverable
# "Warning: singular matrix" during gmin stepping, which still converges).
_FATAL = re.compile(
    r"timestep too small|no simulations run|aborted|cannot find|can't find model|"
    r"unknown subckt|no such|incomplete or empty netlist|fatal", re.I)

# "material" floors: a probe below this magnitude is treated as not-exercised.
_FLOOR_I = 1e-12      # amps
_FLOOR_V = 1e-6       # volts
_GROUND = {"0", "gnd", "gnd!"}


def ngspice_available() -> Optional[str]:
    return os.environ.get("NGSPICE_BIN") or shutil.which("ngspice")


@dataclass
class RunResult:
    ok: bool
    returncode: int
    stdout: str
    probes: Dict[str, float] = field(default_factory=dict)
    error: str = ""


@dataclass
class ValidationResult:
    status: str                       # pass|fail|inconclusive|not-verified|error|skipped
    subckt: str = ""
    models: List[str] = field(default_factory=list)   # bare .model cards benched
    mode_source: str = ""
    mode_translated: str = ""
    max_abs: float = 0.0
    max_rel: float = 0.0
    compared: int = 0                 # material probes compared (trivial 0==0 excluded)
    deviations: List[Tuple[str, float, float, float]] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    source_run: Optional[RunResult] = None
    translated_run: Optional[RunResult] = None

    @property
    def ok_exit(self) -> bool:
        return self.status == "pass"

    def summary(self) -> str:
        return {
            "pass": f"PASS — verified equivalent at this bias: {self.compared} material "
                    f"probe(s) agree (max rel {self.max_rel:.2e}, max abs {self.max_abs:.2e})",
            "fail": f"FAIL — divergence over {self.compared} material probe(s) "
                    f"(max rel {self.max_rel:.2e}, max abs {self.max_abs:.2e})",
            "inconclusive": "INCONCLUSIVE — the bench exercised no material signal; nothing "
                            "was actually tested. Supply --ports to bias rails/inputs.",
            "not-verified": "NOT VERIFIED — source not natively simulable; translation loads "
                            "but NO equivalence was established (this is not a pass).",
            "error": "ERROR — translated model failed to simulate.",
            "skipped": "SKIPPED — ngspice unavailable.",
        }.get(self.status, self.status)


def _run_ngspice(deck: str, mode: str, timeout: float = 60.0) -> RunResult:
    exe = ngspice_available()
    if not exe:
        return RunResult(False, -1, "", error="ngspice not found on PATH")
    work = tempfile.mkdtemp(prefix="smeister-val-")
    try:
        if mode:
            with open(os.path.join(work, ".spiceinit"), "w") as fh:
                fh.write(f"set ngbehavior={mode}\n")
        with open(os.path.join(work, "bench.cir"), "w") as fh:
            fh.write(deck)
        try:
            proc = subprocess.run([exe, "-b", "bench.cir"], cwd=work,
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return RunResult(False, -1, "", error=f"ngspice timed out after {timeout}s")
        out = proc.stdout + "\n" + proc.stderr
        probes: Dict[str, float] = {}
        for line in out.splitlines():
            m = _VAL.match(line)
            if m:
                try:
                    probes[m.group(1).lower()] = float(m.group(2))
                except ValueError:
                    pass
        # A real failure aborts AND produces no probe values. A recoverable
        # "Warning: singular matrix" that still converges yields probes -> ok.
        fatal = bool(_FATAL.search(out))
        ok = (proc.returncode == 0) and bool(probes) and not fatal
        err = "" if ok else _first_error(out)
        return RunResult(ok, proc.returncode, out, probes, err)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _first_error(out: str) -> str:
    for line in out.splitlines():
        if re.search(r"error|fatal|can't|cannot|unknown|no such|timestep too small", line, re.I):
            return line.strip()[:200]
    return "no probe values produced"


def _first_subckt(text: str, dialect: str) -> Optional[ir.SubcktCard]:
    try:
        nl = readers.read(text, dialect=dialect)
    except Exception:
        return None
    subs = nl.subckts()
    return subs[0] if subs else None


def _internal_nodes(sub: ir.SubcktCard, limit: int = 40) -> List[str]:
    """Best-effort internal node names (excluding terminals and ground)."""
    terms = {n for n in sub.nodes}
    out: List[str] = []
    seen = set()

    def add(nm: str):
        if not isinstance(nm, str):
            return
        if nm in terms or nm.lower() in _GROUND or nm in seen:
            return
        seen.add(nm)
        out.append(nm)

    for c in sub.body:
        if not isinstance(c, ir.InstanceCard):
            continue
        if c.behavioral and c.behavioral.out:
            for nm in c.behavioral.out:
                add(nm)
        if c.dev in ("R", "C", "L", "D", "V", "I", "B", "E", "G"):
            nds = [p for p in c.pos if isinstance(p, str)][:2]
            for nm in nds:
                add(nm)
    return out[:limit]


def _default_bias(i: int) -> float:
    # distinct, non-zero, modest voltages so multiplicative/rail terms are exercised
    return round(0.5 + 0.5 * i, 3)


def _bench(sub: ir.SubcktCard, model_text: str, ports: Dict[str, float],
           rseries: float = 1000.0) -> Tuple[str, List[str]]:
    terms = sub.nodes
    inst_nodes = " ".join(f"t{i}" for i in range(len(terms)))
    body = "\n".join(l for l in model_text.splitlines() if l.strip().lower() != ".end")
    lines = ["* SpiceMeister validation bench", "", body, "",
             f"X1 {inst_nodes} {sub.name}"]
    probes: List[str] = []
    for i, term in enumerate(terms):
        v = ports.get(term, ports.get(f"t{i}", _default_bias(i)))
        lines.append(f"Vt{i} s{i} 0 DC {v}")
        lines.append(f"Rt{i} s{i} t{i} {rseries}")
        probes.append(f"i(vt{i})")
        probes.append(f"v(t{i})")
    for nd in _internal_nodes(sub):
        probes.append(f"v(x1.{nd})")
    lines += ["", ".control", "op"]
    lines += [f"print {p}" for p in probes]
    lines += [".endc", ".end"]
    return "\n".join(lines), probes


def _unit_floor(name: str) -> float:
    return _FLOOR_I if name.startswith("i(") else _FLOOR_V


# --------------------------------------------------------------------------- #
# Bare .model validation
# --------------------------------------------------------------------------- #
# A file holding only `.model` cards (the usual shape of a vendor diode or
# transistor model) has no .subckt to bench. For the common semiconductor
# primitives we instantiate the model in a generated fixture and compare the
# source against the translation at several bias points, each as an isolated
# circuit in ONE deck:
#   * DC operating point: every terminal current (static params: IS, N, RS, BV,
#     BF, VTO, ...).
#   * Small-signal AC at _AC_FREQ on the control terminal: real AND imaginary
#     terminal currents, so charge-storage params (CJO, TT, CJE, CGSO, ...) that
#     never show up at DC are compared too.
# Terminal currents go through _RSERIES so a forward-biased junction or a
# breakdown bias settles at a finite, material current.

_AC_FREQ = "10meg"
_RSERIES = 1000.0

# mtype -> (instance letter, terminal names, index of the AC-driven terminal)
_PRIMITIVES = {
    "D": ("D", ["a", "c"], 0),
    "NPN": ("Q", ["c", "b", "e"], 1),
    "PNP": ("Q", ["c", "b", "e"], 1),
    "NMOS": ("M", ["d", "g", "s", "b"], 1),
    "PMOS": ("M", ["d", "g", "s", "b"], 1),
    "NJF": ("J", ["d", "g", "s"], 1),
    "PJF": ("J", ["d", "g", "s"], 1),
    "VDMOS": ("M", ["d", "g", "s"], 1),
}

# N-polarity bias points (label, terminal voltages in _PRIMITIVES order,
# series resistance per terminal); P-polarity devices use the negation. Each
# family has one low-resistance "high-current" point: behind _RSERIES the
# current is set by the bench resistor and ohmic params (RS, RB, RD, ...) are
# invisible (a 15% RS error moved a diode's current by <1e-5 relative).
_BIASES = {
    "D": [("fwd-low", [0.7, 0.0], _RSERIES), ("fwd-high", [5.0, 0.0], _RSERIES),
          ("rev", [-5.0, 0.0], _RSERIES), ("high-current", [2.0, 0.0], 1.0)],
    "BJT": [("active", [5.0, 0.8, 0.0], _RSERIES), ("active-high", [5.0, 2.0, 0.0], _RSERIES),
            ("saturated", [0.3, 2.0, 0.0], _RSERIES), ("off", [5.0, 0.0, 0.0], _RSERIES),
            ("high-current", [5.0, 1.2, 0.0], 10.0)],
    "MOS": [("on", [5.0, 3.0, 0.0, 0.0], _RSERIES), ("triode", [0.3, 5.0, 0.0, 0.0], _RSERIES),
            ("off", [5.0, 0.0, 0.0, 0.0], _RSERIES), ("high-current", [5.0, 5.0, 0.0, 0.0], 10.0)],
    "JF": [("on", [5.0, 0.0, 0.0], _RSERIES), ("pinching", [5.0, -1.0, 0.0], _RSERIES),
           ("triode", [0.3, 0.0, 0.0], _RSERIES), ("high-current", [5.0, 0.0, 0.0], 10.0)],
    "VDMOS": [("on", [10.0, 5.0, 0.0], _RSERIES), ("triode", [1.0, 10.0, 0.0], _RSERIES),
              ("off", [10.0, 0.0, 0.0], _RSERIES), ("high-current", [10.0, 10.0, 0.0], 1.0)],
}
_FAMILY = {"D": "D", "NPN": "BJT", "PNP": "BJT", "NMOS": "MOS", "PMOS": "MOS",
           "NJF": "JF", "PJF": "JF", "VDMOS": "VDMOS"}


def _num(card: ir.ModelCard, key: str) -> Optional[float]:
    from ..core.expr import parse_number
    for k, v in card.params.items():
        if k.lower() == key.lower():
            try:
                return parse_number(v.text)
            except Exception:
                return None
    return None


def _is_pchan_vdmos(text: str, name: str) -> bool:
    """VDMOS polarity is a bare `pchan` flag; read it from the raw text."""
    joined = re.sub(r"\n\s*\+", " ", text)
    m = re.search(rf"^\s*\.model\s+{re.escape(name)}\s+vdmos\b(.*)$", joined, re.I | re.M)
    return bool(m and re.search(r"\bpchan\b", m.group(1), re.I))


Bias = Tuple[str, List[float], float]


def _model_biases(card: ir.ModelCard, src_text: str) -> List[Bias]:
    mtype = card.mtype.upper()
    biases = list(_BIASES[_FAMILY[mtype]])
    if mtype == "D":
        bv = _num(card, "BV")
        if bv and 0 < bv <= 1000:
            biases.append(("breakdown", [-(1.2 * bv + 5.0), 0.0], _RSERIES))
    neg = mtype in ("PNP", "PMOS", "PJF") or (
        mtype == "VDMOS" and _is_pchan_vdmos(src_text, card.name))
    if neg:
        biases = [(lbl, [-v for v in vs], r) for lbl, vs, r in biases]
    return biases


def _model_bench(model_text: str, card: ir.ModelCard,
                 biases: List[Bias]) -> Tuple[str, List[Tuple[str, str]]]:
    """One deck: an isolated copy of the device per bias point.

    Returns the deck and [(probe, human label)] pairs.
    """
    letter, terms, ac_i = _PRIMITIVES[card.mtype.upper()]
    body = "\n".join(l for l in model_text.splitlines() if l.strip().lower() != ".end")
    wrap = f"smv_{re.sub(r'[^a-z0-9_]', '_', card.name.lower())}"
    lines = ["* SpiceMeister .model validation bench", "", body, "",
             f".subckt {wrap} {' '.join(terms)}",
             f"{letter}1 {' '.join(terms)} {card.name}",
             ".ends", ""]
    probes: List[Tuple[str, str]] = []
    ac_lets: List[str] = []
    for k, (label, volts, rser) in enumerate(biases):
        nodes = []
        for i, (term, v) in enumerate(zip(terms, volts)):
            src = f"vb{k}t{i}"
            ac = " AC 1" if i == ac_i else ""
            lines.append(f"{src} sb{k}t{i} 0 DC {v}{ac}")
            lines.append(f"rb{k}t{i} sb{k}t{i} nb{k}t{i} {rser}")
            nodes.append(f"nb{k}t{i}")
            where = f"{card.name}@{label} {term}"
            probes.append((f"i({src})", f"{where} I(dc)"))
            ac_lets.append(f"let acre_{src} = real(i({src}))")
            ac_lets.append(f"let acim_{src} = imag(i({src}))")
            probes.append((f"acre_{src}", f"{where} I(ac re)"))
            probes.append((f"acim_{src}", f"{where} I(ac im)"))
        lines.append(f"x{k} {' '.join(nodes)} {wrap}")
    lines += ["", ".control", "op"]
    lines += [f"print {p}" for p, _ in probes if p.startswith("i(")]
    lines += [f"ac lin 1 {_AC_FREQ} {_AC_FREQ}"] + ac_lets
    lines += [f"print {p}" for p, _ in probes if p.startswith("ac")]
    lines += [".endc", ".end"]
    return "\n".join(lines), probes


def _validate_models(source_text: str, translated_text: str, source_dialect: str,
                     translated_profile: str, ports: Dict[str, float],
                     abs_tol: float, rel_tol: float,
                     timeout: float) -> Optional[ValidationResult]:
    """Bench every bare semiconductor `.model`; None if there is none to bench."""
    try:
        tr_models = [m for m in readers.read(translated_text, dialect="spice3").cards
                     if isinstance(m, ir.ModelCard)]
    except Exception:
        return None
    benchable = [m for m in tr_models if m.mtype.upper() in _PRIMITIVES]
    if not benchable:
        return None
    try:
        src_by_name = {m.name.lower(): m for m in
                       readers.read(source_text, dialect=source_dialect).cards
                       if isinstance(m, ir.ModelCard)}
    except Exception:
        src_by_name = {}

    mode_t = _MODE.get(translated_profile, "ltpsa")
    mode_s = _MODE.get(source_dialect, "")
    res = ValidationResult(status="", mode_source=mode_s or "(default)",
                           mode_translated=mode_t or "(default)")
    skipped = [f"{m.name} ({m.mtype})" for m in tr_models if m not in benchable]
    if skipped:
        res.messages.append(f"model type not benchable, not compared: {', '.join(skipped)}")

    lports = {k.lower(): v for k, v in ports.items()}
    statuses: List[str] = []
    max_abs = max_rel = 0.0
    material = 0
    worst_fail = None
    worst_rel = 0.0
    for card in benchable:
        res.models.append(card.name)
        src_card = src_by_name.get(card.name.lower())
        if src_card is None:
            statuses.append("not-verified")
            res.messages.append(f"{card.name}: no .model of that name in the source — "
                                "not compared")
            continue
        if src_card.mtype.upper() != card.mtype.upper():
            statuses.append("fail")
            res.messages.append(f"{card.name}: device type changed "
                                f"{src_card.mtype} -> {card.mtype}")
            continue
        _, terms, _ = _PRIMITIVES[card.mtype.upper()]
        if any(t in lports for t in terms):
            defaults = _model_biases(src_card, source_text)[0][1]
            biases = [("ports", [lports.get(t, d) for t, d in zip(terms, defaults)], _RSERIES)]
        else:
            biases = _model_biases(src_card, source_text)

        deck_t, probes = _model_bench(translated_text, card, biases)
        tr = _run_ngspice(deck_t, mode_t, timeout)
        if not tr.ok:
            statuses.append("error")
            res.messages.append(f"{card.name}: translated model failed to simulate: "
                                f"{tr.error}")
            res.translated_run = tr
            continue
        deck_s, _ = _model_bench(source_text, src_card, biases)
        sr = _run_ngspice(deck_s, mode_s, timeout)
        if not sr.ok:
            statuses.append("not-verified")
            res.messages.append(f"{card.name}: source not natively simulable under "
                                f"ngbehavior={mode_s or 'default'} ({sr.error}) — "
                                "translation simulates but equivalence NOT checked")
            res.source_run = sr
            continue

        exercised = False
        model_failed = False
        for probe, label in probes:
            if probe not in sr.probes or probe not in tr.probes:
                res.messages.append(f"{label}: absent from one run — not compared")
                continue
            a, b = sr.probes[probe], tr.probes[probe]
            da = abs(a - b)
            signal = max(abs(a), abs(b))
            noise = max(_FLOOR_I, abs_tol)
            if signal <= noise:
                if da > noise:
                    model_failed = True
                    res.deviations.append((label, a, b, da))
                    worst_fail = worst_fail or (label, a, b, da)
                continue
            exercised = True
            material += 1
            dr = da / signal
            max_abs, max_rel = max(max_abs, da), max(max_rel, dr)
            if dr > rel_tol:
                model_failed = True
                res.deviations.append((label, a, b, da))
                if dr > worst_rel:
                    worst_rel, worst_fail = dr, (label, a, b, da)
        if model_failed:
            statuses.append("fail")
        elif exercised:
            statuses.append("pass")
            res.messages.append(f"{card.name}: {len(biases)} bias point(s) "
                                f"[{', '.join(b[0] for b in biases)}], DC + AC @ {_AC_FREQ} "
                                "agree")
        else:
            statuses.append("inconclusive")
            res.messages.append(f"{card.name}: no material current at any bias point")

    res.max_abs, res.max_rel, res.compared = max_abs, max_rel, material
    for s in ("error", "fail", "not-verified", "inconclusive"):
        if s in statuses:
            res.status = s
            break
    else:
        res.status = "pass"
    if res.status == "fail" and worst_fail is not None:
        n, a, b, _ = worst_fail
        res.messages.append(f"worst divergence at {n}: source={a:.6g} translated={b:.6g}")
    if res.status == "inconclusive":
        res.messages.append("supply --ports with the device's terminal names "
                            "to choose a bias")
    return res


def validate(source_text: str, translated_text: str, source_dialect: str = "auto",
             translated_profile: str = "ltspice", ports: Optional[Dict[str, float]] = None,
             abs_tol: float = 1e-9, rel_tol: float = 1e-4,
             timeout: float = 60.0) -> ValidationResult:
    ports = ports or {}
    if not ngspice_available():
        return ValidationResult("skipped", messages=["ngspice not found on PATH"])

    if source_dialect == "auto":
        from ..core.dialect import detect
        source_dialect = detect(source_text).dialect

    sub = _first_subckt(translated_text, "spice3")
    if sub is None:
        model_res = _validate_models(source_text, translated_text, source_dialect,
                                     translated_profile, ports, abs_tol, rel_tol, timeout)
        if model_res is not None:
            return model_res
        dummy = (translated_text + "\nV_smoke n_smoke 0 1\nR_smoke n_smoke 0 1\n"
                 ".control\nop\nprint v(n_smoke)\n.endc\n.end\n")
        mode_t = _MODE.get(translated_profile, "ltpsa")
        tr = _run_ngspice(dummy, mode_t, timeout)
        res = ValidationResult("not-verified" if tr.ok else "error",
                               mode_translated=mode_t, translated_run=tr)
        res.messages.append("no .subckt and no benchable .model (D/NPN/PNP/NMOS/PMOS/NJF/PJF/"
                            "VDMOS) found; ran a load/parse check on the translation only "
                            "(no equivalence established)")
        if not tr.ok:
            res.messages.append(f"translation failed to load: {tr.error}")
        return res

    # Surface two silent trust-eroders: --ports keys that match no terminal
    # (the bias was ignored, default used), and a multi-subckt library of which
    # only the first is benched.
    pre_msgs: List[str] = []
    if ports:
        known = set(sub.nodes) | {f"t{i}" for i in range(len(sub.nodes))}
        unknown = [k for k in ports if k not in known]
        if unknown:
            pre_msgs.append(
                f"--ports {sorted(unknown)} match no terminal of '{sub.name}' "
                f"(terminals: {list(sub.nodes)}); those bias values were IGNORED "
                "and the default bias used — check the name/case.")
    try:
        _all_subs = readers.read(translated_text, dialect="spice3").subckts()
    except Exception:
        _all_subs = []
    if len(_all_subs) > 1:
        pre_msgs.append(
            f"translation defines {len(_all_subs)} subckts "
            f"({', '.join(s.name for s in _all_subs)}); only the first "
            f"('{sub.name}') was benched — validate the others separately.")

    mode_t = _MODE.get(translated_profile, "ltpsa")
    deck_t, probes = _bench(sub, translated_text, ports)
    tr = _run_ngspice(deck_t, mode_t, timeout)

    sub_src = _first_subckt(source_text, source_dialect) or sub
    mode_s = _MODE.get(source_dialect, "")
    deck_s, _ = _bench(sub_src, source_text, ports)
    sr = _run_ngspice(deck_s, mode_s, timeout)

    res = ValidationResult(status="", subckt=sub.name,
                           mode_source=mode_s or "(default)",
                           mode_translated=mode_t or "(default)",
                           source_run=sr, translated_run=tr)
    res.messages.extend(pre_msgs)

    if not tr.ok:
        res.status = "error"
        res.messages.append(f"translated model failed to simulate: {tr.error}")
        return res

    if not sr.ok:
        res.status = "not-verified"
        res.messages.append(
            f"source not natively simulable under ngbehavior={mode_s or 'default'} "
            f"({sr.error}). The translation loads and simulates, but behavioural "
            "equivalence could NOT be checked — translate from a simulable source, or "
            "compare against a reference by hand.")
        return res

    # Compare every shared probe. A probe reflects MODEL behaviour when it is a
    # terminal current i(vtN) or an internal node voltage v(x1.*); terminal node
    # voltages v(tN) sit at the bench bias by construction, so they decide
    # divergence but do NOT, on their own, prove the model was exercised.
    max_abs = max_rel = 0.0
    material = 0
    model_exercised = False
    worst_fail = None
    for name in probes:
        if name not in sr.probes or name not in tr.probes:
            res.messages.append(f"probe {name} absent from one run — not compared")
            continue
        a, b = sr.probes[name], tr.probes[name]
        da = abs(a - b)
        signal = max(abs(a), abs(b))
        # Below `noise`, a value cannot be told from numerical noise, so it is
        # neither "material" nor verifiable. It is tied to abs_tol: previously a
        # probe between the tiny material floor (1e-12) and abs_tol (1e-9) was
        # counted as exercised yet could NEVER trip the fail test (which also
        # required da > abs_tol) — so a sub-nanoamp current with 95% relative
        # error passed. Aligning the two closes that false-PASS hole.
        noise = max(_unit_floor(name), abs_tol)
        is_model = name.startswith("i(vt") or name.startswith("v(x1.")
        if signal <= noise:
            if da > noise:                        # near floor but genuinely split
                worst_fail = worst_fail or (name, a, b, da)
            continue
        if is_model:
            model_exercised = True
        material += 1
        dr = da / signal
        res.deviations.append((name, a, b, da))
        max_abs = max(max_abs, da)
        max_rel = max(max_rel, dr)
        if dr > rel_tol:                          # material signal disagrees
            worst_fail = (name, a, b, da)
    res.max_abs, res.max_rel, res.compared = max_abs, max_rel, material

    if not model_exercised:
        res.status = "inconclusive"
        res.messages.append(
            "no terminal current or internal node voltage was materially exercised — the "
            "model did nothing observable at this bias (e.g. a DC-blocked path, or rails left "
            "unbiased). Supply --ports to drive it (e.g. --ports 'VCC=5,IN=0.1') or add "
            "AC/transient stimulus; a translation cannot be verified against an idle bench.")
        return res

    if worst_fail is not None:
        res.status = "fail"
        n, a, b, _ = worst_fail
        res.messages.append(f"worst divergence at {n}: source={a:.6g} translated={b:.6g}")
    else:
        res.status = "pass"
    return res
