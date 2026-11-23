# REA bridge

> Status (2026-11-18): working. Drives the live `rea mcp` server over stdio;
> `inspect_byte_entropy` verified end-to-end. Deeper strings/extraction are
> surfaced as available REA capabilities for the agent to drive next.

## What it is

Some vendor models ship as **binary containers** rather than text SPICE —
encrypted libraries, firmware-embedded model tables, compiled OSDI modules,
packaged archives. SpiceMeister cannot translate those as text, but when you are
**authorised to analyse the container for interoperability**, it bridges to
[REA](https://github.com/morluto/rea) to characterise it.

It does **not** defeat vendor encryption. It reports structure and recovers any
plain-text model cards embedded in the container.

## What it does

`spicemeister/opaque/rea_bridge.py` is a minimal MCP stdio client. Given a file:

1. **Local SPICE-marker scan** of the raw bytes — finds embedded `.subckt` /
   `.model` text even before REA runs, so recoverable cards can be fed back into
   `smeister translate`.
2. Spawns `rea mcp`, performs the JSON-RPC handshake, and `tools/list`.
3. Calls **`inspect_byte_entropy`** (path-based, one-shot) → SHA-256, file
   entropy and high/low-entropy runs. Entropy distinguishes *encrypted/compressed*
   (high, ≈8 bits/byte) from *plain-text regions* (low runs = candidate model
   text offsets).
4. Reports which **deeper REA capabilities** are available for the container
   (`list_strings`, `search_strings`, `inspect_artifact`, `extract_artifact`,
   `extract_firmware`, …) so an agent can drive the next step.

It degrades gracefully: if `rea` is not on `PATH` (set `REA_BIN` or
`npm i -g rea-agents`), it returns the local scan with guidance.

## Use it

```bash
smeister scan model.bin --rea
```

or via MCP: the `bridge_opaque_model_to_rea` tool (`{"path": "…"}`).

## Relationship to REA

SpiceMeister is built in REA's toolkit style and *consumes* REA as a service:
REA supplies the reverse-engineering primitives (entropy, strings, extraction,
firmware); SpiceMeister adds the SPICE-aware layer (marker scan, feeding
recovered text back into the translation pipeline). SpiceMeister reuses REA's
install idioms (`install.sh` with a backup-then-symlink `link()`, an isolated
opencode home, an `mcp.*` entry).

## Sources

- REA `README.md` and `rea mcp` tool schemas (`inspect_byte_entropy`, …).
- MCP stdio JSON-RPC (newline-delimited), as used by REA's `measure-families.mjs`.
