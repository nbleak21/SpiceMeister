"""Bridge to the REA toolkit for opaque *binary* model containers.

SpiceMeister drives REA's MCP server (``rea mcp``) over stdio JSON-RPC to
characterise a binary container the user is entitled to analyse for
interoperability — entropy (is it encrypted or just compressed?), and the set
of deeper REA capabilities available for strings/inventory/extraction. It also
does a local SPICE-marker scan of the raw bytes so embedded plain-text model
text can be recovered back into the normal pipeline.

It never defeats vendor encryption. It degrades gracefully: if ``rea`` is not
installed or the handshake fails, it returns a report saying so, plus whatever
the local byte scan found.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_PROTO = "2024-11-05"
_SUBCKT = re.compile(r"\.subckt\s+(\S+)", re.I)
_MODEL = re.compile(r"\.model\s+(\S+)", re.I)


def rea_available() -> Optional[str]:
    return os.environ.get("REA_BIN") or shutil.which("rea")


# --------------------------------------------------------------------------- #
# minimal MCP stdio client
# --------------------------------------------------------------------------- #
class _ReaMCP:
    def __init__(self, rea_bin: str):
        self.proc = subprocess.Popen(
            [rea_bin, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
            env={**os.environ},
        )
        self._id = 0
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        assert self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def _send(self, method: str, params: dict = None, notify: bool = False):
        msg = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self._id += 1
            msg["id"] = self._id
        if params is not None:
            msg["params"] = params
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return None if notify else self._id

    def _wait(self, want_id: int, timeout: float) -> dict:
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self._q.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                break
            if msg.get("id") == want_id:
                return msg
        raise TimeoutError(f"no response to request {want_id} within {timeout}s")

    def initialize(self, timeout: float = 20.0):
        rid = self._send("initialize", {
            "protocolVersion": _PROTO,
            "capabilities": {},
            "clientInfo": {"name": "SpiceMeister", "version": "0.1.0"},
        })
        resp = self._wait(rid, timeout)
        self._send("notifications/initialized", {}, notify=True)
        return resp

    def tools_list(self, timeout: float = 20.0) -> List[str]:
        rid = self._send("tools/list", {})
        resp = self._wait(rid, timeout)
        tools = resp.get("result", {}).get("tools", [])
        return [t.get("name", "") for t in tools]

    def call(self, name: str, args: dict, timeout: float = 60.0) -> dict:
        rid = self._send("tools/call", {"name": name, "arguments": args})
        return self._wait(rid, timeout)

    def close(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _text_of(resp: dict) -> str:
    result = resp.get("result", resp.get("error", {}))
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        return json.dumps(result)[:4000]
    return str(result)[:4000]


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
@dataclass
class BridgeReport:
    path: str
    rea_available: bool = False
    rea_error: str = ""
    tools: List[str] = field(default_factory=list)
    entropy: str = ""
    embedded_subckts: List[str] = field(default_factory=list)
    embedded_models: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.rea_available:
            return f"REA unavailable ({self.rea_error}); local scan only"
        return (f"REA drove {len(self.tools)} tools; "
                f"{len(self.embedded_subckts)} embedded .subckt, "
                f"{len(self.embedded_models)} .model found")


def _local_scan(path: str) -> Dict[str, List[str]]:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return {"subckts": [], "models": []}
    text = data.decode("utf-8", errors="replace")
    subs = list(dict.fromkeys(_SUBCKT.findall(text)))
    mods = list(dict.fromkeys(_MODEL.findall(text)))
    return {"subckts": subs[:50], "models": mods[:50]}


def bridge_opaque_model(path: str, timeout: float = 60.0) -> BridgeReport:
    """Characterise a binary model container by driving REA, plus a local scan."""
    path = os.path.abspath(path)
    rep = BridgeReport(path=path)

    local = _local_scan(path)
    rep.embedded_subckts = local["subckts"]
    rep.embedded_models = local["models"]
    if rep.embedded_subckts or rep.embedded_models:
        rep.notes.append("Embedded plain-text SPICE found in the container — the recoverable "
                         "cards can be fed back into `smeister translate`.")

    rea = rea_available()
    if not rea:
        rep.rea_error = "`rea` not on PATH (set REA_BIN or `npm i -g rea-agents`)"
        rep.notes.append("Install REA to add entropy/strings/extraction characterisation.")
        return rep

    client = None
    try:
        client = _ReaMCP(rea)
        client.initialize(timeout=min(20.0, timeout))
        rep.rea_available = True
        try:
            rep.tools = client.tools_list(timeout=min(20.0, timeout))
        except Exception as exc:  # noqa: BLE001
            rep.notes.append(f"tools/list failed: {exc}")
        if "inspect_byte_entropy" in rep.tools:
            try:
                resp = client.call("inspect_byte_entropy",
                                   {"path": path, "detail": "summary"},
                                   timeout=timeout)
                rep.entropy = _text_of(resp)[:3000]
            except Exception as exc:  # noqa: BLE001
                rep.notes.append(f"inspect_byte_entropy failed: {exc}")
        else:
            rep.notes.append("REA build lacks inspect_byte_entropy; available tools listed.")
        deeper = [t for t in rep.tools
                  if any(k in t for k in ("string", "artifact", "firmware", "extract"))]
        if deeper:
            rep.notes.append("Deeper REA capabilities available for this container: "
                             + ", ".join(sorted(deeper)[:12]))
    except Exception as exc:  # noqa: BLE001
        rep.rea_error = str(exc)
        rep.notes.append("REA handshake failed; returned local scan only.")
    finally:
        if client:
            client.close()
    return rep
