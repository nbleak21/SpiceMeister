# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""SpiceMeister MCP server — stdio JSON-RPC 2.0, zero dependencies.

Exposes the translation toolkit to coding agents (Claude Code, opencode) the
same way REA exposes its tools. Messages are newline-delimited JSON objects on
stdin/stdout. Tools:

  detect_spice_dialect, translate_spice_model, validate_translation,
  inspect_spice_model, scan_for_encrypted_models, bridge_opaque_model_to_rea

Each tool accepts ``text`` (inline model) or ``path`` (a file to read).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

from .. import __version__

_PROTO = "2024-11-05"


# --------------------------------------------------------------------------- #
# tool implementations
# --------------------------------------------------------------------------- #
def _get_text(args: Dict[str, Any]) -> str:
    if args.get("text"):
        return args["text"]
    if args.get("path"):
        with open(args["path"], "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    raise ValueError("provide either 'text' or 'path'")


def _t_detect(args):
    from ..core.dialect import detect
    from ..opaque import detect as odet
    text = _get_text(args)
    d = detect(text)
    scan = odet.scan_text(text)
    return (f"dialect: {d.dialect} (confidence {d.confidence:.0%})\n"
            f"evidence: {'; '.join(d.evidence)}\n"
            f"content: {scan.summary()}")


def _t_translate(args):
    from .. import pipeline, report as _report
    text = _get_text(args)
    res = pipeline.translate(
        text, from_dialect=args.get("from", "auto"),
        profile=args.get("profile", "ltspice"),
        source_name=os.path.basename(args.get("path", "") or "model"),
        banner=args.get("banner", True),
        emit_zener=args.get("emit_zener", False),
    )
    out = res.output
    if args.get("report"):
        out += "\n\n----- REPORT -----\n" + _report.build_report(
            res.netlist, res.detection, res.profile)
    if res.zener:
        out += "\n\n----- ZENER SNIPPET -----\n" + res.zener
    return out


def _t_validate(args):
    from ..validate import ngspice_harness as V
    src = args.get("source") or args.get("source_text")
    tr = args.get("translated") or args.get("translated_text")
    if src and os.path.exists(src):
        with open(src, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    if tr and os.path.exists(tr):
        with open(tr, encoding="utf-8", errors="replace") as fh:
            tr = fh.read()
    ports = args.get("ports") or {}
    res = V.validate(src, tr, source_dialect=args.get("from", "auto"),
                     translated_profile=args.get("profile", "ltspice"),
                     ports=ports, timeout=args.get("timeout", 60.0))
    lines = [f"subckt: {res.subckt}", f"result: {res.summary()}",
             f"ngbehavior source={res.mode_source} translated={res.mode_translated}"]
    for name, a, b, da in res.deviations:
        lines.append(f"  {name}: src={a:.6g} tr={b:.6g} |d|={da:.3g}")
    lines += [f"  note: {m}" for m in res.messages]
    return "\n".join(lines)


def _t_inspect(args):
    from .. import readers
    from ..core.dialect import detect
    from ..core import ir
    text = _get_text(args)
    dialect = detect(text).dialect if args.get("from", "auto") == "auto" else args["from"]
    nl = readers.read(text, dialect=dialect)
    out = [f"dialect: {nl.source_dialect}", f"subckts: {len(nl.subckts())}"]
    for s in nl.subckts():
        out.append(f"  .subckt {s.name} [{' '.join(s.nodes)}] params={{{', '.join(s.params)}}}")
    out.append(f"models: {len(nl.models())}")
    for m in nl.models():
        out.append(f"  .model {m.name} {m.mtype}")
    return "\n".join(out)


def _t_scan(args):
    from ..opaque import detect as odet
    if args.get("path"):
        rep = odet.scan_file(args["path"])
    else:
        rep = odet.scan_text(_get_text(args))
    out = [f"status: {rep.summary()}",
           f"printable={rep.printable_ratio:.0%} binary={rep.is_binary}"]
    for f in rep.findings:
        out.append(f"  [{f.kind}] {f.detail}  -> {f.suggestion}")
    return "\n".join(out)


def _t_bridge(args):
    from ..opaque import rea_bridge
    path = args.get("path")
    if not path:
        raise ValueError("bridge_opaque_model_to_rea requires 'path'")
    br = rea_bridge.bridge_opaque_model(path, timeout=args.get("timeout", 60.0))
    out = [f"summary: {br.summary()}", f"rea_available: {br.rea_available}"]
    if br.embedded_subckts:
        out.append(f"embedded .subckt: {', '.join(br.embedded_subckts)}")
    if br.embedded_models:
        out.append(f"embedded .model: {', '.join(br.embedded_models)}")
    if br.entropy:
        out.append(f"entropy: {br.entropy[:1500]}")
    out += [f"note: {n}" for n in br.notes]
    return "\n".join(out)


_STR = {"type": "string"}
TOOLS = [
    {"name": "detect_spice_dialect",
     "description": "Identify the SPICE dialect (pspice/hspice/spectre/spice3/ltspice) of a model and report encrypted/opaque content.",
     "inputSchema": {"type": "object", "properties": {"text": _STR, "path": _STR}},
     "fn": _t_detect},
    {"name": "translate_spice_model",
     "description": "Translate a vendor SPICE model to LTspice-compatible (profile=ltspice) or ngspice-native (profile=ngspice) form. Optionally emit a pcb-stack Zener SpiceModel snippet and a change report.",
     "inputSchema": {"type": "object", "properties": {
         "text": _STR, "path": _STR, "from": _STR,
         "profile": {"type": "string", "enum": ["ltspice", "ngspice"]},
         "emit_zener": {"type": "boolean"}, "report": {"type": "boolean"}}},
     "fn": _t_translate},
    {"name": "validate_translation",
     "description": "Round-trip a source model and its translation through ngspice and compare terminal currents to prove behavioural equivalence. source/translated may be file paths or inline text.",
     "inputSchema": {"type": "object", "properties": {
         "source": _STR, "translated": _STR, "from": _STR,
         "profile": {"type": "string", "enum": ["ltspice", "ngspice"]},
         "ports": {"type": "object"}}},
     "fn": _t_validate},
    {"name": "inspect_spice_model",
     "description": "Dump the parsed structure (subcircuits, terminals, params, models) of a SPICE model.",
     "inputSchema": {"type": "object", "properties": {"text": _STR, "path": _STR, "from": _STR}},
     "fn": _t_inspect},
    {"name": "scan_for_encrypted_models",
     "description": "Detect .PROTECT/encrypted spans, Verilog-A, OSDI, and binary containers in a model file.",
     "inputSchema": {"type": "object", "properties": {"text": _STR, "path": _STR}},
     "fn": _t_scan},
    {"name": "bridge_opaque_model_to_rea",
     "description": "Characterise a binary model container by driving the REA toolkit (entropy, embedded strings, available REA capabilities). Does not defeat encryption.",
     "inputSchema": {"type": "object", "properties": {"path": _STR}, "required": ["path"]},
     "fn": _t_bridge},
]
_BY_NAME = {t["name"]: t for t in TOOLS}


# --------------------------------------------------------------------------- #
# JSON-RPC plumbing
# --------------------------------------------------------------------------- #
def _handle(msg: Dict[str, Any]):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": _PROTO,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "spicemeister", "version": __version__}}}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "tools/list":
        tools = [{"name": t["name"], "description": t["description"],
                  "inputSchema": t["inputSchema"]} for t in TOOLS]
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        tool = _BY_NAME.get(name)
        if not tool:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"unknown tool {name}"}}
        try:
            text = tool["fn"](args)
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": text}]}}
        except Exception as exc:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": f"ERROR: {exc}"}],
                               "isError": True}}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"unknown method {method}"}}
    return None


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
