# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""SpiceMeister command-line interface.

Subcommands: detect, translate, validate, inspect, scan, batch.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional

from . import __version__
from .core import ir


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _parse_ports(spec: Optional[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not spec:
        return out
    for item in spec.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        try:
            out[k.strip()] = float(v)
        except ValueError:
            pass
    return out


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_detect(args) -> int:
    from .core.dialect import detect
    from .opaque import detect as odet
    text = _read(args.file)
    d = detect(text)
    print(f"dialect:    {d.dialect}")
    print(f"confidence: {d.confidence:.0%}")
    print("evidence:")
    for e in d.evidence:
        print(f"  - {e}")
    scan = odet.scan_text(text, path=args.file)
    print(f"content:    {scan.summary()}")
    return 0


def cmd_translate(args) -> int:
    from . import pipeline, report
    text = _read(args.file)
    src_name = os.path.basename(args.file) if args.file != "-" else "stdin"
    res = pipeline.translate(
        text, from_dialect=args.from_dialect, profile=args.profile,
        source_name=src_name, banner=not args.no_banner,
        emit_zener=args.emit_zener,
        lib_relpath=args.lib_relpath or (f"spice/{os.path.splitext(src_name)[0]}.lib"),
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(res.output)
        print(f"[smeister] {res.detection.dialect} -> {args.profile}: wrote {args.output}",
              file=sys.stderr)
    else:
        sys.stdout.write(res.output)

    if args.report:
        rep = report.build_report(res.netlist, res.detection, args.profile, source_name=src_name)
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(rep)
        print(f"[smeister] report -> {args.report}", file=sys.stderr)

    if res.zener:
        if args.emit_zener_file:
            with open(args.emit_zener_file, "w", encoding="utf-8") as fh:
                fh.write(res.zener + "\n")
            print(f"[smeister] zener snippet -> {args.emit_zener_file}", file=sys.stderr)
        else:
            print("\n" + res.zener, file=sys.stderr)

    for w in res.warnings:
        print(f"[smeister] warning: {w}", file=sys.stderr)
    return 0


def cmd_validate(args) -> int:
    from .validate import ngspice_harness as V
    if not V.ngspice_available():
        print("ngspice not found on PATH — cannot validate.", file=sys.stderr)
        return 3
    src = _read(args.source)
    tr = _read(args.translated)
    res = V.validate(
        src, tr, source_dialect=args.from_dialect, translated_profile=args.profile,
        ports=_parse_ports(args.ports), abs_tol=args.abs_tol, rel_tol=args.rel_tol,
        timeout=args.timeout,
    )
    print(f"subckt:      {res.subckt or '(none)'}")
    print(f"source mode: ngbehavior={res.mode_source}")
    print(f"target mode: ngbehavior={res.mode_translated}")
    print(f"result:      {res.summary()}")
    for name, a, b, da in res.deviations:
        print(f"  {name}: source={a:.6g}  translated={b:.6g}  |Δ|={da:.3g}")
    for m in res.messages:
        print(f"  note: {m}")
    # Only a genuine 'pass' is success. 'not-verified'/'inconclusive' are NOT
    # passes and must not exit 0; 'skipped' uses its own code.
    if res.status == "pass":
        return 0
    if res.status == "skipped":
        return 3
    return 1


def cmd_inspect(args) -> int:
    from . import readers
    from .core.dialect import detect
    text = _read(args.file)
    dialect = detect(text).dialect if args.from_dialect == "auto" else args.from_dialect
    nl = readers.read(text, dialect=dialect, source_name=os.path.basename(args.file))
    print(f"dialect: {nl.source_dialect}")
    print(f"subckts: {len(nl.subckts())}")
    for s in nl.subckts():
        print(f"  .subckt {s.name}  terminals=[{' '.join(s.nodes)}]"
              f"  params={{{', '.join(s.params)}}}")
        body_inst = [c for c in s.body if isinstance(c, ir.InstanceCard)]
        print(f"      {len(body_inst)} instance(s), "
              f"{sum(1 for c in body_inst if c.behavioral)} behavioural")
    print(f"models:  {len(nl.models())}")
    for m in nl.models():
        print(f"  .model {m.name} {m.mtype}  ({len(m.params)} params)")
    return 0


def cmd_scan(args) -> int:
    from .opaque import detect as odet
    rep = odet.scan_file(args.file)
    print(f"file:     {args.file}")
    print(f"printable:{rep.printable_ratio:6.0%}   binary={rep.is_binary}   "
          f"spice-text={rep.has_spice_text}")
    print(f"status:   {rep.summary()}")
    for f in rep.findings:
        print(f"  [{f.kind}] {f.detail}")
        if f.suggestion:
            print(f"       -> {f.suggestion}")
    if args.rea:
        from .opaque import rea_bridge
        print("\n[REA bridge]")
        br = rea_bridge.bridge_opaque_model(args.file, timeout=args.timeout)
        print(f"  {br.summary()}")
        if br.embedded_subckts:
            print(f"  embedded .subckt: {', '.join(br.embedded_subckts)}")
        if br.embedded_models:
            print(f"  embedded .model:  {', '.join(br.embedded_models)}")
        if br.entropy:
            print(f"  entropy: {br.entropy[:200]}")
        for n in br.notes:
            print(f"  note: {n}")
    return 0


def cmd_mcp(args) -> int:
    from .mcp.server import serve
    return serve()


def cmd_batch(args) -> int:
    from . import pipeline
    exts = {".lib", ".mod", ".sub", ".cir", ".inc", ".txt", ".sp", ".net"}
    root = args.directory
    out_root = args.output or os.path.join(root, "_translated")
    os.makedirs(out_root, exist_ok=True)
    n_ok = n_fail = 0
    print(f"{'file':<40} {'dialect':<9} {'status'}")
    print("-" * 70)
    for dirpath, _, files in os.walk(root):
        if os.path.abspath(dirpath).startswith(os.path.abspath(out_root)):
            continue
        for fn in sorted(files):
            if os.path.splitext(fn)[1].lower() not in exts:
                continue
            path = os.path.join(dirpath, fn)
            try:
                text = _read(path)
                res = pipeline.translate(text, from_dialect=args.from_dialect,
                                         profile=args.profile, source_name=fn)
                rel = os.path.relpath(path, root)
                dest = os.path.join(out_root, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(res.output)
                status = "ok" + (f" ({len(res.warnings)} warn)" if res.warnings else "")
                print(f"{rel:<40} {res.detection.dialect:<9} {status}")
                n_ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"{fn:<40} {'?':<9} FAILED: {exc}")
                n_fail += 1
    print("-" * 70)
    print(f"{n_ok} translated -> {out_root}  ({n_fail} failed)")
    return 0 if n_fail == 0 else 1


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="smeister",
        description="SpiceMeister — translate vendor SPICE models to LTspice-compatible form.")
    p.add_argument("--version", action="version", version=f"SpiceMeister {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    common_from = dict(default="auto",
                       help="source dialect: auto|pspice|hspice|spectre|spice3 (default auto)")

    sp = sub.add_parser("detect", help="identify the SPICE dialect of a model")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_detect)

    sp = sub.add_parser("translate", help="translate a model to LTspice/ngspice form")
    sp.add_argument("file")
    sp.add_argument("-o", "--output")
    sp.add_argument("--from", dest="from_dialect", **common_from)
    sp.add_argument("--profile", choices=["ltspice", "ngspice"], default="ltspice")
    sp.add_argument("--report", help="write a Markdown translation report to this path")
    sp.add_argument("--no-banner", action="store_true", help="omit the provenance header")
    sp.add_argument("--emit-zener", action="store_true",
                    help="also emit a Zener SpiceModel(...) snippet for pcb-stack")
    sp.add_argument("--emit-zener-file", help="write the Zener snippet to this path")
    sp.add_argument("--lib-relpath", help="lib path used inside the Zener snippet")
    sp.set_defaults(func=cmd_translate)

    sp = sub.add_parser("validate", help="round-trip a source vs translated model through ngspice")
    sp.add_argument("source")
    sp.add_argument("translated")
    sp.add_argument("--from", dest="from_dialect", **common_from)
    sp.add_argument("--profile", choices=["ltspice", "ngspice"], default="ltspice")
    sp.add_argument("--ports", help="bias map, e.g. 'VCC=5,VEE=-5,IN=0.1'")
    sp.add_argument("--abs-tol", type=float, default=1e-9)
    sp.add_argument("--rel-tol", type=float, default=1e-4)
    sp.add_argument("--timeout", type=float, default=60.0)
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("inspect", help="dump the parsed structure of a model")
    sp.add_argument("file")
    sp.add_argument("--from", dest="from_dialect", **common_from)
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("scan", help="detect encrypted/opaque content (optionally via REA)")
    sp.add_argument("file")
    sp.add_argument("--rea", action="store_true", help="drive the REA toolkit for binary containers")
    sp.add_argument("--timeout", type=float, default=60.0)
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("batch", help="translate every model file under a directory")
    sp.add_argument("directory")
    sp.add_argument("-o", "--output", help="output root (default <dir>/_translated)")
    sp.add_argument("--from", dest="from_dialect", **common_from)
    sp.add_argument("--profile", choices=["ltspice", "ngspice"], default="ltspice")
    sp.set_defaults(func=cmd_batch)

    sp = sub.add_parser("mcp", help="run the SpiceMeister MCP server over stdio")
    sp.set_defaults(func=cmd_mcp)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"smeister: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 — never dump a raw traceback to users
        print(f"smeister: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
