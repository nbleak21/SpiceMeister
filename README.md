# SpiceMeister

Translate vendor SPICE models — **PSpice/OrCAD**, **HSPICE/Synopsys**, **Spectre/Cadence**, generic **SPICE3** — into clean **LTspice-compatible** (or ngspice-native) models, and **check the translation reproduces the original's behaviour at a tested DC operating point** by round-tripping both through [`ngspice`](https://ngspice.sourceforge.io/). (The check is a DC bias-point comparison, not a full proof of equivalence — see [Verified](#verified) for exactly what it does and does not cover.) Built in the same toolkit style as [REA](https://github.com/morluto/rea): a CLI plus an MCP server exposed to coding agents. For opaque *binary* model containers, SpiceMeister **bridges to the REA toolkit** to characterise them — it detects and reports vendor encryption rather than circumventing it.

Why: vendor models silently misbehave or fail to load in LTspice because of dialect differences — expression delimiters (`'x'` vs `{x}`), behavioural-source syntax (`VALUE=`, `VOL=`, `TABLE`), function names (`PWRS`, `STP`, `pow`), model `LEVEL` numbers, comment characters (`$`, `//`), node naming (`gnd!`), and encrypted blocks. SpiceMeister normalises all of that structurally (parse → IR → transform → emit) and then verifies the result numerically.

## Layout

| Path | What |
|---|---|
| `spicemeister/core/` | lexer, expression parser/AST, IR, dialect detection |
| `spicemeister/readers/` | per-dialect front-ends (pspice, hspice, spice3, spectre) → IR |
| `spicemeister/transforms/` | IR→IR passes (functions, behavioural sources, model levels, …) driven by `rules/*.json` |
| `spicemeister/writers/` | LTspice & ngspice emitters, Zener `SpiceModel(...)` snippet |
| `spicemeister/validate/` | ngspice round-trip equivalence harness |
| `spicemeister/opaque/` | encrypted/Verilog-A/binary detection + REA bridge |
| `spicemeister/rules/*.json` | data-driven function / model / behavioural mappings (extend without code) |
| `spicemeister/mcp/server.py` | stdio JSON-RPC MCP server (zero-dependency) |
| `spicemeister/cli.py`, `bin/smeister` | the `smeister` command |
| `scripts/` | `install.sh` and env / opencode launcher scripts (REA-style) |
| `opencode/opencode.json` | opencode config: Gemma provider + `spicemeister` MCP entry |
| `tests/`, `examples/`, `docs/` | test suite + fixtures, runnable demo, design docs |

## Usage

```bash
smeister detect   model.lib                         # identify dialect + encrypted content
smeister translate model.lib -o out.lib             # -> LTspice (default profile)
smeister translate model.lib --profile ngspice      # -> ngspice-native .SUBCKT (pcb-stack style)
smeister translate model.lib --report r.md --emit-zener   # change report + pcb-stack snippet
smeister validate model.lib out.lib --ports "VCC=5,IN=0.1"  # prove equivalence via ngspice
smeister inspect  model.lib                         # dump parsed structure
smeister scan     model.lib --rea                   # encryption scan; drive REA on binaries
smeister batch    ./models/ -o ./translated/        # translate a whole tree
smeister mcp                                        # run the MCP server over stdio
```

No install needed to try it: `./bin/smeister …` or `./examples/demo.sh`.

## Install

```bash
scripts/install.sh            # symlinks `smeister` into ~/.local/bin, wires up opencode + MCP
smeister --help
```

`install.sh` is idempotent (timestamped `*.bak.*` backups, early-return if already linked). It registers the `spicemeister` MCP server with Claude Code via `claude mcp add` when the `claude` CLI is present; otherwise it prints the one-line command to run.

## What is installed / required (2026-11-18)

| Item | Version | Role |
|---|---|---|
| Python | ≥ 3.8 (tested 3.15) | the whole toolkit — **standard library only**, zero pip deps |
| ngspice | 47 (tested) | backend for `smeister validate`; `set ngbehavior=psa/hsa/ltpsa` compat modes |
| REA (`rea`) | 6.9.0 (optional) | `smeister scan --rea` — entropy/strings/extraction on binary containers |
| Claude Code / opencode | optional | consume the MCP server; opencode uses local Gemma |

Not required: PyYAML (rules are JSON), pytest (tests also run under `unittest`), LTspice itself (output targets LTspice but is verified with ngspice's LTspice-compat mode).

## How it works

```
read(text) ──detect──▶ IR (cards + expression ASTs)
        │                   │
        │          transforms (rules/*.json):
        │            expr-delimiters · functions · behavioural-sources
        │            · model-levels · units · nodes
        ▼                   ▼
   writers: ltspice | ngspice ──▶ clean model + provenance banner + change log
                                     │
                    validate: ngspice(source, native mode) vs
                              ngspice(translated, ltpsa) ──▶ PASS / FAIL / INCONCLUSIVE
                                                              / NOT-VERIFIED + deviations
```

Key choices: expressions are parsed to an **AST** so function/operator remapping is structural, not regex (`PWRS(x,y)` → `sgn(x)*abs(x)**y`); controlled sources are normalised to **B-sources** (`V=`/`I={…}`) that both simulators share; `TABLE` becomes a **portable piecewise-linear expression** using `max`/`min` clamps (ngspice has no `table()`, and bare-ngspice `limit()` is not a 3-arg clamp) so it is verified correct in ngspice and identical in LTspice. A validator PASS is deliberately conservative — it means "verified equivalent **at this bias**"; when the bench exercises nothing material it returns `INCONCLUSIVE`, and when the source can't be simulated natively it returns `NOT-VERIFIED` (neither is a pass). **Scope:** the comparison is a single **DC operating point** of the **first** `.subckt`, so it catches DC/behavioural errors that the chosen bias reaches but **not** reactive (C/L) or frequency-domain differences, and not later subcircuits in a multi-model library (those are flagged for separate validation). AC/transient equivalence metrics are v0.2.

## Verified (2026-11-19)

- 40/40 tests pass (`python -m unittest discover -s tests`), including **ngspice round-trip equivalence** checks and **adversarial regressions** that confirm a *wrong* translation does not pass: a rail-dependent gain error is caught at default bias, an error on an internal (non-terminal) node is caught via node-voltage probing, a `not-verified` (non-simulable source) does **not** read as success, an unexercised bench returns `inconclusive` — not a false pass, and a sub-nanoamp probe with large *relative* error no longer slips through the absolute-tolerance floor.
- `TABLE`→PWL is checked against **ground truth** (the actual interpolated curve in ngspice, and a direct evaluation of the emitted expression), including unsorted and negative breakpoints.
- PSpice behavioural op-amp fixture: `VALUE`/`LIMIT`→`max/min`, `PWRS`, `TABLE`, `.FUNC` all translate and load under ngspice `ltpsa`.
- HSPICE fixture: `'expr'` quotes stripped, `pow()`→`**`, `VOL=`/`CUR=`→B-source, `LEVEL=49`→`8` (with a verify-by-simulation warning).
- REA bridge drove the live `rea mcp` server (255 tools), ran `inspect_byte_entropy`, and recovered embedded `.subckt` text from a synthetic binary container; it degrades cleanly (and without hangs or zombie processes) when `rea` is unavailable.
- MCP server: `initialize` → `tools/list` → `tools/call` round-trips over stdio; malformed model input degrades gracefully instead of dumping a traceback.

This codebase was adversarially reviewed by three independent agents (translation engine, ngspice validator, integration surfaces) and then re-assessed by a fourth independent agent. Confirmed findings are fixed and pinned by the regression tests above. The most consequential: validator false-passes (under-driven bench, internal-node blindness, and a sub-nanoamp/absolute-tolerance hole); a mode-dependent clamp in `TABLE` output; **ngspice reads `**` left-associatively** (the unparser now emits explicit grouping so `2**(x**2)` is not silently miscompiled); PSpice `$N_0001` node names were eaten as HSPICE-style comments; `.endl` truncated multi-section libraries; a linear controlled source whose control node started `cur`/`vol`/`value` was mis-read as a behavioural source; duplicate TABLE breakpoints divided by zero; and the ngspice-native profile emitted `if()`, which native ngspice rejects (now mapped to the `?:` ternary, which is valid in native ngspice and LTspice alike). The remaining known limits are the DC-operating-point validator scope noted above and the dialect-semantics items in [`docs/DIALECTS.md`](docs/DIALECTS.md).

## Rebuild from scratch

```bash
brew install ngspice            # validation backend
# optional: npm install -g rea-agents --allow-scripts=frida   # REA bridge
git clone https://github.com/nbleak21/SpiceMeister.git
cd SpiceMeister
python -m unittest discover -s tests          # 40 tests
scripts/install.sh
```

## pcb-stack integration

> SpiceMeister is fully standalone. This section applies only if you also use **pcb-stack**, a separate PCB/simulation toolchain — skip it otherwise.

Output is consumed by `pcb sim` via a Zener `SpiceModel("<file>", "<SUBCKT>", nets=[...], args={...})`. `--emit-zener` prints a ready-to-paste block with `nets` in exact `.subckt` terminal order and every `PARAMS:` entry in `args` (pcb-stack applies no defaults). Translated models belong at `components/<Part>/spice/<Part>.lib`; the `--profile ngspice` output matches the stdlib's native `.SUBCKT` house style and loads without a `.spiceinit`/`ngbehavior` setting. See [`docs/`](docs/).

## Notes

- **Scope**: PSpice / HSPICE / SPICE3 are first-class; Spectre is structural best-effort (v0.1). AC/noise equivalence metrics and automated port-role inference are v0.2.
- **IP / ethics**: translates models you are entitled to use; detects and **reports** encryption (never circumvents). See `LICENSE`.
- Add new function/model mappings by editing `spicemeister/rules/*.json` — no code change.
