# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""SpiceMeister test suite (stdlib unittest; ngspice tests self-gate).

Run:  python -m unittest discover -s tests   |   python -m pytest tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spicemeister import pipeline, readers                      # noqa: E402
from spicemeister.core import dialect, expr, ir, lexer          # noqa: E402
from spicemeister.opaque import detect as odet                  # noqa: E402
from spicemeister.validate import ngspice_harness as V          # noqa: E402
from spicemeister.writers import base as wbase                  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fix(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


class TestExpr(unittest.TestCase):
    def test_roundtrip_stable(self):
        for s in ["-x**2", "(-x)**2", "2**-3", "2**3**2", "a-b-c", "a/b/c",
                  "sgn(x)*abs(x)**y", "a<b?c+1:d*2", "v(a,b)/r", "!p&&q||r"]:
            u1 = expr.unparse(expr.parse(s))
            u2 = expr.unparse(expr.parse(u1))
            self.assertEqual(u1, u2, f"not stable: {s!r} -> {u1!r} -> {u2!r}")

    def test_power_precedence(self):
        # -x**2 must mean -(x**2)
        t = expr.parse("-x**2")
        self.assertIsInstance(t, expr.Unary)
        self.assertIsInstance(t.operand, expr.Binary)
        self.assertEqual(t.operand.op, "**")

    def test_scale_factors(self):
        self.assertAlmostEqual(expr.parse_number("4.7u"), 4.7e-6)
        self.assertAlmostEqual(expr.parse_number("1Meg"), 1e6)
        self.assertAlmostEqual(expr.parse_number("3m"), 3e-3)
        self.assertAlmostEqual(expr.parse_number("2.5V"), 2.5)  # unit tail ignored


class TestLexer(unittest.TestCase):
    def test_continuation_and_comments(self):
        txt = "* banner\n.model M D(IS=1e-14 $ cmt\n+ N=1.0) ; note\nR1 1 2 1k\n"
        lls = [l for l in lexer.logical_lines(txt) if l.kind == "card"]
        self.assertIn("N=1.0", lls[0].text)
        self.assertNotIn("$ cmt", lls[0].text)
        self.assertEqual(lls[0].comment, "note")

    def test_protect_block_isolated(self):
        lls = lexer.logical_lines(fix("protected.lib"))
        self.assertTrue(any(l.kind == "encrypted" for l in lls))


class TestDialect(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(dialect.detect(fix("pspice_behavioral.lib")).dialect, "pspice")
        self.assertEqual(dialect.detect(fix("hspice_misc.sp")).dialect, "hspice")
        self.assertEqual(dialect.detect(fix("spectre_frag.scs")).dialect, "spectre")
        self.assertEqual(dialect.detect(fix("passive_rc.lib")).dialect, "spice3")


class TestReader(unittest.TestCase):
    def test_pspice_structure(self):
        nl = readers.read(fix("pspice_behavioral.lib"), "pspice")
        sub = nl.subckts()[0]
        self.assertEqual(sub.name, "PSAMP")
        self.assertEqual(sub.nodes, ["INP", "INN", "OUT"])
        self.assertIn("GAIN", sub.params)
        beh = [c for c in sub.body if isinstance(c, ir.InstanceCard) and c.behavioral]
        self.assertTrue(any(b.behavioral.kind == "table" for b in beh))
        self.assertTrue(any(b.behavioral.kind == "value" for b in beh))

    def test_hspice_quotes_stripped(self):
        nl = readers.read(fix("hspice_misc.sp"), "hspice")
        texts = [e.text for e in ir.walk_exprs(nl)]
        self.assertTrue(any("r0" in t for t in texts))
        self.assertFalse(any("'" in t for t in texts))


class TestTransforms(unittest.TestCase):
    def test_functions(self):
        r = pipeline.translate(".param x={PWRS(a,2)+pow(b,3)+STP(c)}\n", from_dialect="pspice")
        exprs = [e.text for e in ir.walk_exprs(r.netlist)]
        joined = " ".join(exprs)
        self.assertIn("sgn(a)", joined)
        self.assertIn("abs(a)**2", joined)
        self.assertIn("b**3", joined)
        self.assertIn("u(c)", joined)
        self.assertNotIn("PWRS", joined.upper().replace("PWRS(A,2)", ""))  # expanded

    def test_behavioral_to_bsource(self):
        r = pipeline.translate(".SUBCKT S a o\nE1 o 0 VALUE={2*V(a)}\n.ENDS\n",
                               from_dialect="pspice")
        self.assertIn("B", r.output)
        self.assertIn("V={", r.output.replace(" ", ""))

    def test_model_level_remap_warns(self):
        r = pipeline.translate(".model nm nmos level=49 vto=0.7\n", from_dialect="hspice")
        self.assertIn("level=8", r.output.lower().replace(" ", ""))
        self.assertTrue(any("LEVEL" in w or "level" in w for w in r.warnings))

    def test_micro_sign(self):
        r = pipeline.translate(".param c={4.7µ}\n", from_dialect="pspice")
        self.assertIn("4.7u", r.output)
        self.assertNotIn("µ", r.output)

    def test_limit_remap_to_minmax(self):
        # LIMIT(x,lo,hi) -> max(lo,min(hi,x)): mode-independent clamp
        r = pipeline.translate(".param y={LIMIT(x,-5,5)}\n", from_dialect="pspice")
        out = "".join(e.text for e in ir.walk_exprs(r.netlist)).replace(" ", "").lower()
        self.assertIn("max(-5", out)
        self.assertIn("min(5", out)
        self.assertNotIn("limit", out)


class TestWriter(unittest.TestCase):
    def test_identifier_is_braced(self):
        # a lone param reference must be braced, a lone number must not
        self.assertEqual(ir.Expr("C").render(), "{C}")
        self.assertEqual(ir.Expr("1e-9").render(), "1e-9")

    def test_pwl_ground_truth_negative_unsorted(self):
        # GROUND TRUTH (not self-comparison): the emitted expression, evaluated
        # directly, must equal the intended clamped-linear interpolation — even
        # with unsorted and negative breakpoints.
        pairs = [(ir.Expr("2"), ir.Expr("1")), (ir.Expr("-2"), ir.Expr("-1")),
                 (ir.Expr("0"), ir.Expr("0"))]            # => clamp(0.5*c, -1, 1)
        e = wbase.pwl_expr("c", pairs)
        self.assertIn("max(", e)
        self.assertNotIn("limit(", e)                     # must not rely on ngspice limit()
        f = lambda c: eval(e, {"max": max, "min": min, "c": c})  # noqa: S307 (trusted)
        for x in (-3, -2, -1, -0.5, 0, 0.5, 1, 2, 3):
            self.assertAlmostEqual(f(x), max(-1.0, min(1.0, 0.5 * x)), places=9,
                                   msg=f"x={x}  expr={e}")


class TestOpaque(unittest.TestCase):
    def test_protect_detected(self):
        rep = odet.scan_text(fix("protected.lib"))
        self.assertTrue(any(f.kind == "encrypted-span" for f in rep.findings))

    def test_binary_container(self):
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
            fh.write(b"\x00\x01\x02\x03" * 100 + b"\n.subckt HID 1 2\n.ends\n")
            path = fh.name
        try:
            rep = odet.scan_file(path)
            self.assertTrue(rep.is_binary)
        finally:
            os.unlink(path)


class TestPipeline(unittest.TestCase):
    def test_translate_each_dialect(self):
        for name in ("pspice_behavioral.lib", "hspice_misc.sp", "passive_rc.lib"):
            r = pipeline.translate(fix(name))
            self.assertTrue(r.output.strip())

    def test_zener_snippet(self):
        r = pipeline.translate(fix("passive_rc.lib"), emit_zener=True, lib_relpath="spice/x.lib")
        self.assertIn("SpiceModel(", r.zener)
        self.assertIn("nets=[A, B]", r.zener)
        self.assertIn("R1", r.zener)


@unittest.skipUnless(V.ngspice_available(), "ngspice not on PATH")
class TestValidationNgspice(unittest.TestCase):
    def test_passive_equivalence(self):
        src = fix("passive_rc.lib")
        tr = pipeline.translate(src, from_dialect="spice3").output
        res = V.validate(src, tr, "spice3", "ltspice", ports={"A": 2.0, "B": 0.0})
        self.assertEqual(res.status, "pass", res.summary())

    def test_pspice_buffer_equivalence(self):
        src = fix("pspice_buffer.lib")
        tr = pipeline.translate(src, from_dialect="pspice").output
        res = V.validate(src, tr, "pspice", "ltspice", ports={"INP": 1.0, "OUT": 0.0})
        self.assertEqual(res.status, "pass", res.summary())
        self.assertLessEqual(res.max_rel, 1e-4)

    def test_table_pwl_equivalence(self):
        src = ("\n.SUBCKT NL IN OUT\nEt OUT 0 TABLE {V(IN)} = (-2,-1)(0,0)(2,1)\n"
               "Rt OUT 0 1k\n.ENDS\n")
        tr = pipeline.translate(src, from_dialect="pspice").output
        for v in (1.5, -1.5, 3.0):
            res = V.validate(src, tr, "pspice", "ltspice", ports={"IN": v, "OUT": 0.0})
            self.assertEqual(res.status, "pass", f"@{v}: {res.summary()}")

    # --- adversarial regressions: a wrong translation must NOT pass ---------
    def test_catches_rail_dependent_mistranslation(self):
        # multiplicative, rail-dependent gain; default (no ports) must still catch it
        src = ".SUBCKT AMP IN VCC OUT\nB1 OUT 0 V={0.1*V(IN)*V(VCC)}\nR1 OUT 0 1k\n.ENDS\n"
        bad = ".SUBCKT AMP IN VCC OUT\nB1 OUT 0 V={0.5*V(IN)*V(VCC)}\nR1 OUT 0 1k\n.ENDS\n"
        res = V.validate(src, bad, "pspice", "ltspice")
        self.assertEqual(res.status, "fail", res.summary())

    def test_catches_internal_node_mistranslation(self):
        # the mistranslated element drives only an INTERNAL node (never a terminal)
        src = (".SUBCKT NL IN OUT\nEt t 0 TABLE {V(IN)} = (-2,-1)(0,0)(2,1)\n"
               "Rt t 0 1k\nRo t OUT 1k\n.ENDS\n")
        bad = ".SUBCKT NL IN OUT\nBt t 0 V={999}\nRt t 0 1k\nRo t OUT 1k\n.ENDS\n"
        res = V.validate(src, bad, "pspice", "ltspice")
        self.assertEqual(res.status, "fail", res.summary())

    def test_not_verified_is_not_pass(self):
        # encrypted source cannot simulate; a plausible translation must NOT read as success
        src = ".SUBCKT DEV A B\n.PROTECT\nzz garbage zz\n.UNPROTECT\n.ENDS\n"
        tr = ".SUBCKT DEV A B\nR1 A B 1k\n.ENDS\n"
        res = V.validate(src, tr, "pspice", "ltspice", ports={"A": 1.0, "B": 0.0})
        self.assertEqual(res.status, "not-verified", res.summary())
        self.assertFalse(res.ok_exit)

    def test_pwl_ground_truth_in_ngspice(self):
        # Run the translated TABLE model in ngspice and check the ACTUAL curve
        # against the intended interpolation (not source-vs-translated).
        import re as _re
        import shutil as _sh
        import subprocess as _sp
        import tempfile as _tf
        src = (".SUBCKT NL IN OUT\nEt OUT 0 TABLE {V(IN)} = (2,1)(-2,-1)(0,0)\n"
               "Rt OUT 0 1k\n.ENDS\n")
        tr = pipeline.translate(src, from_dialect="pspice", banner=False).output
        work = _tf.mkdtemp()
        try:
            with open(os.path.join(work, ".spiceinit"), "w") as fh:
                fh.write("set ngbehavior=ltpsa\n")
            deck = ("* bench\n" + tr + "\nVi in 0 0\nX1 in out NL\n.control\n"
                    "dc Vi -3 3 1\nprint v(out)\n.endc\n.end\n")
            with open(os.path.join(work, "b.cir"), "w") as fh:
                fh.write(deck)
            out = _sp.run(["ngspice", "-b", "b.cir"], cwd=work,
                          capture_output=True, text=True, timeout=30).stdout
        finally:
            _sh.rmtree(work, ignore_errors=True)
        got = {}
        for line in out.splitlines():
            m = _re.match(r"\s*\d+\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*$", line)
            if m:
                got[round(float(m.group(1)))] = float(m.group(2))
        self.assertTrue(got, "ngspice produced no sweep output")
        for x in (-3, -1, 0, 1, 3):
            self.assertAlmostEqual(got[x], max(-1.0, min(1.0, 0.5 * x)), places=6,
                                   msg=f"x={x} got={got.get(x)}")

    def test_inconclusive_when_unexercised(self):
        # both terminals at the same potential: nothing flows, nothing exercised
        rr = ".SUBCKT R2 A B\nR1 A B 1k\n.ENDS\n"
        res = V.validate(rr, rr, "spice3", "ltspice", ports={"A": 1.0, "B": 1.0})
        self.assertEqual(res.status, "inconclusive", res.summary())
        self.assertFalse(res.ok_exit)


class TestCliRobustness(unittest.TestCase):
    """Malformed input must degrade gracefully, never dump a traceback."""

    def _run(self, text):
        import contextlib
        import io
        import tempfile
        from spicemeister import cli
        with tempfile.NamedTemporaryFile("w", suffix=".lib", delete=False) as fh:
            fh.write(text)
            path = fh.name
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                return cli.main(["translate", path, "--no-banner"])
        finally:
            os.unlink(path)

    def test_no_name_subckt(self):
        self.assertEqual(self._run(".subckt\n.ends\n"), 0)   # warns, no crash

    def test_bare_model(self):
        self.assertEqual(self._run(".model\n"), 0)

    def test_deeply_nested_parens(self):
        self.assertEqual(self._run(".subckt X a b\nR1 a b {" + "(" * 300 + "1" + ")" * 300 + "}\n.ends\n"), 0)


class TestFableRegressions(unittest.TestCase):
    """Bugs found by the independent (Fable) adversarial assessment, 2026-11-19."""

    def test_power_left_assoc_grouping_preserved(self):
        # ngspice reads ** LEFT-associative; our AST is right-assoc. The unparser
        # must emit explicit parens so grouping survives (else 2**(x**2) silently
        # became 2**x**2 == (2**x)**2 in ngspice).
        self.assertEqual(expr.unparse(expr.parse("a**b**c")), "a**(b**c)")
        self.assertEqual(expr.unparse(expr.parse("2**(V(in)**2)")), "2**(V(in)**2)")
        self.assertEqual(expr.unparse(expr.parse("(a**b)**c")), "(a**b)**c")

    def test_pspice_dollar_is_node_not_comment(self):
        # PSpice/OrCAD: $N_0001 is a node name; HSPICE: $ starts a comment.
        p = pipeline.translate(".subckt S a b\nR1 a $N_0001 1k\nR2 $N_0001 b 1k\n.ends\n",
                               from_dialect="pspice", banner=False).output
        self.assertIn("$N_0001", p)                       # kept as a node
        h = pipeline.translate("R1 a $N_0001 1k\n", from_dialect="hspice",
                               banner=False).output
        self.assertNotIn("$N_0001", h)                    # HSPICE: $ began a comment

    def test_endl_does_not_truncate(self):
        out = pipeline.translate(
            ".lib ff\n.param r0=1k\n.endl ff\n.subckt S a b\nR1 a b {r0}\n.ends\n",
            from_dialect="pspice").output
        self.assertIn(".subckt s", out.lower())           # subckt after .endl survives
        self.assertIn("r1 a b", out.lower())

    def test_eg_control_node_not_overmatched(self):
        # control node "curr" must NOT be read as a CUR= behavioural source
        out = pipeline.translate("E1 out 0 curr 0 2\n", from_dialect="pspice").output
        self.assertIn("curr", out)
        self.assertNotIn("I={", out.replace(" ", ""))     # stayed a linear VCVS

    def test_if_maps_to_ternary_only_for_ngspice_profile(self):
        src = ".SUBCKT S in out\nE1 out 0 VALUE={IF(V(in)>0.2,2*V(in),0)}\n.ENDS\n"
        ng = pipeline.translate(src, from_dialect="pspice", profile="ngspice",
                                banner=False).output
        self.assertIn("?", ng)
        self.assertIn(":", ng)
        self.assertNotIn("if(", ng.lower())
        lt = pipeline.translate(src, from_dialect="pspice", profile="ltspice",
                                banner=False).output
        self.assertIn("if(", lt.lower())                  # LTspice keeps if()

    def test_table_step_breakpoint_no_divzero(self):
        E = ir.Expr
        pairs = [(E("0"), E("0")), (E("1"), E("0")), (E("1"), E("5")), (E("2"), E("5"))]
        e = wbase.pwl_expr("c", pairs)
        self.assertNotIn("/((1)-(1))", e)                 # no division by zero
        self.assertIn("u(", e)                            # step term emitted
        u = lambda x: 1.0 if x > 0 else 0.0
        f = lambda c: eval(e, {"max": max, "min": min, "u": u, "c": c})  # noqa: S307
        self.assertAlmostEqual(f(0.5), 0.0, places=9, msg=e)
        self.assertAlmostEqual(f(1.5), 5.0, places=9, msg=e)


@unittest.skipUnless(V.ngspice_available(), "ngspice not on PATH")
class TestFableRegressionsNgspice(unittest.TestCase):
    def test_subnano_relative_error_is_not_a_pass(self):
        # a 1000x diode-IS error: sub-nanoamp at default (reverse) bias must NOT
        # read as PASS, and MUST FAIL once the diode is actually forward-biased.
        src = ".subckt DIO a c\nD1 a c DM\n.model DM D(IS=1e-14)\n.ends\n"
        good = pipeline.translate(src, from_dialect="spice3").output
        bad = good.replace("1e-14", "1e-11")
        self.assertNotEqual(V.validate(src, bad, "spice3", "ltspice").status, "pass")
        fwd = {"a": 0.7, "c": 0.0}
        self.assertEqual(V.validate(src, good, "spice3", "ltspice", ports=fwd).status, "pass")
        self.assertEqual(V.validate(src, bad, "spice3", "ltspice", ports=fwd).status, "fail")

    def test_ngspice_profile_if_loads_in_native_ngspice(self):
        import shutil as _sh
        import subprocess as _sp
        import tempfile as _tf
        src = ".SUBCKT S in out\nE1 out 0 VALUE={IF(V(in)>0.2,2*V(in),0)}\n.ENDS\n"
        tr = pipeline.translate(src, from_dialect="pspice", profile="ngspice",
                                banner=False).output
        work = _tf.mkdtemp()
        try:
            deck = ("* bench\n" + tr + "\nV1 in 0 1\nX1 in out S\nR1 out 0 1k\n"
                    ".control\nop\nprint v(out)\n.endc\n.end\n")
            with open(os.path.join(work, "b.cir"), "w") as fh:  # NO .spiceinit => native
                fh.write(deck)
            out = _sp.run(["ngspice", "-b", "b.cir"], cwd=work,
                          capture_output=True, text=True, timeout=30).stdout
        finally:
            _sh.rmtree(work, ignore_errors=True)
        self.assertNotIn("no such function", out.lower())
        self.assertTrue(any("2.00" in l for l in out.splitlines() if "v(out)" in l.lower()),
                        out)

    def test_unknown_port_name_warns(self):
        src = ".SUBCKT A inp out\nE1 out 0 VALUE={10*V(inp)}\n.ENDS\n"
        tr = pipeline.translate(src, from_dialect="pspice").output
        res = V.validate(src, tr, "pspice", "ltspice", ports={"IN": 0.1})
        self.assertTrue(any("match no terminal" in m for m in res.messages), res.messages)


class TestMCP(unittest.TestCase):
    def test_roundtrip(self):
        import io
        import json
        from spicemeister.mcp import server
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "translate_spice_model",
                        "arguments": {"text": ".SUBCKT X A B\nE1 A 0 VALUE={2*V(B)}\n.ENDS\n",
                                      "banner": False}}},
        ]
        out = io.StringIO()
        server.serve(io.StringIO("\n".join(json.dumps(r) for r in reqs) + "\n"), out)
        msgs = [json.loads(l) for l in out.getvalue().splitlines()]
        by_id = {m["id"]: m for m in msgs}
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "spicemeister")
        names = [t["name"] for t in by_id[2]["result"]["tools"]]
        self.assertIn("translate_spice_model", names)
        self.assertIn("B", by_id[3]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
