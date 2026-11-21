# SpiceMeister architecture

> Status (2026-11-18): v0.1. Core pipeline, PSpice/HSPICE/SPICE3 readers, ngspice
> validator, REA bridge and MCP server implemented and tested. Spectre is partial.

The pipeline is **parse → IR → transform → emit → verify**. Each stage is a
small, independently testable module.

## 1. Lexer (`core/lexer.py`)

Physical lines → folded logical lines. Folds `+` continuations onto the
preceding card; strips and captures comments (`*` full-line; inline `;`, `//`,
and `$`-preceded-by-space) while respecting quotes; and hands `.control`/`.endc`
and `.protect`/`.unprotect` spans back verbatim as `raw`/`encrypted` units.

## 2. Expression engine (`core/expr.py`)

A recursive-descent parser builds a typed AST (`Num/Ref/Call/Unary/Binary/
Ternary`) over the shared SPICE expression grammar, with a precedence-aware
unparser. `-x**2` parses as `-(x**2)`; unparse→reparse is a fixed point. This is
why transforms are **structural**: a function remap edits `Call` nodes, not text.
Numbers keep their source token and a parsed value (engineering suffixes:
`T G Meg K M U N P F`, `Meg`/`Mil` before single letters).

## 3. IR (`core/ir.py`)

A `Netlist` of typed cards: `Comment/Raw/Encrypted/Param/Func/Global/Include/
Option/Model/Subckt/Instance`. Every math expression is wrapped in `Expr`
(brace-free text + lazy AST), so `walk_exprs(netlist)` reaches all of them in one
pass. `Expr.render()` wraps in `{...}` unless the expression is a bare number —
a lone identifier (a parameter reference) **must** stay braced or SPICE reads it
as a model name. Behavioural E/G/B payloads are parsed into a `Behavioral`
record (kind, output nodes, control expr, table pairs).

## 4. Readers (`readers/`)

`base.py` implements the shared grammar; dialect classes specialise only what
differs. Both `{...}` and `'...'` are accepted as expression delimiters and
stripped on read, so the IR is dialect-neutral. Spectre uses a `preprocess()`
that lifts the structural subset (`subckt`/`ends`/`parameters`/`model`/`gnd!`,
`//` and `/* */` comments) into SPICE before the shared parse.

## 5. Transforms (`transforms/`, data in `rules/*.json`)

Ordered IR→IR passes, each logging to `netlist.changelog`:

1. `expr_delimiters` — residual `'...'` cleanup.
2. `functions` — AST rewrite from `rules/functions.json` (rename or template
   expansion with `$0,$1` placeholders), run to a fixpoint.
3. `behavioral_sources` — mark E/G `VALUE/VOL/CUR`/`TABLE` for B-source emission.
4. `model_levels` — type aliases and `LEVEL` remap from `rules/models.json`
   (always warns, since BSIM parameter sets are not guaranteed identical).
5. `comments_units` — Unicode `µ`→`u`.
6. `nodes` — Spectre global-net `!` suffix cleanup.

## 6. Writers (`writers/`)

A single engine parameterised by a `Profile` (keyword case, banner label):
`ltspice` (default) and `ngspice` (uppercase + stdlib house style). Controlled
sources emit as B-sources; `TABLE` becomes a portable `max`/`min`-clamp
piecewise-linear expression (ngspice lacks `table()`, and bare-ngspice `limit()`
is not a 3-arg clamp). `zener_snippet.py` emits a
pcb-stack `SpiceModel(...)` block.

## 7. Validator (`validate/ngspice_harness.py`)

Builds an operating-point bench: one DC source per `.subckt` terminal **through a
series resistor** (so the bench never fights an internal ideal source → no
singular matrix), each terminal biased to a **distinct non-zero** voltage so
rail-dependent and multiplicative behaviour is exercised. It compares **terminal
currents and internal node voltages** between the source model (native
`ngbehavior`) and the translation (`ltpsa`). `ngbehavior` is set via a
`.spiceinit` written beside the deck, because it must take effect at parse time.

A PASS means "verified equivalent **at this bias**": it requires a material
signal to have been exercised and every material probe to agree within tolerance
(relative agreement on material probes, not an absolute floor that could mask
large errors). When nothing material is exercised → `inconclusive`; when the
source is not natively simulable (the usual reason to translate) → `not-verified`
(a load check only, NO equivalence established). Neither is a pass, and the CLI
exits non-zero for both.

**Scope (v0.1).** This is a single DC operating point of the **first** `.subckt`.
It therefore does not see reactive (C/L) or frequency-domain differences, nor
behaviour outside the region the chosen bias reaches, nor later subcircuits in a
multi-model library (those are flagged for separate validation). A probe below
`max(unit-floor, abs_tol)` is treated as numerical noise — not "material" — so a
sub-nanoamp current can never pass on relative agreement alone; material probes
must agree *relatively*. AC/transient equivalence is v0.2. The reference run uses
ngspice's `psa`/`hsa` emulation, which is imperfect (see `DIALECTS.md` →
*Validator oracle caveats*).

## 8. Surfaces

- **CLI** (`cli.py`): detect, translate, validate, inspect, scan, batch, mcp.
- **MCP server** (`mcp/server.py`): the same capabilities as tools for agents.
- **REA bridge** (`opaque/rea_bridge.py`): drives `rea mcp` for binary containers.

## Sources

- pcb-stack `skills/spice-sim/SKILL.md` — `pcb sim`, `.spiceinit`/`ngbehavior`.
- ngspice manual — behavioural B-sources, compatibility modes, functions.
