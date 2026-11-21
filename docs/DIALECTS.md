# Dialect handling

> Status (2026-11-18): PSpice/HSPICE/SPICE3 first-class; Spectre structural
> best-effort. This table is the coverage contract — extend it as rules grow.

## What each transform fixes

| Concern | Source forms | LTspice/ngspice output |
|---|---|---|
| Expression delimiter | HSPICE `'expr'`, PSpice `{expr}` | `{expr}` (bare number unbraced) |
| Signed power | PSpice `PWRS(x,y)` | `sgn(x)*abs(x)**y` |
| Abs power | PSpice `PWR(x,y)` | `abs(x)**y` |
| Unit step | PSpice `STP(x)` | `u(x)` |
| Power fn | HSPICE `pow(x,y)` | `x**y` |
| Decibels | HSPICE `db(x)` | `20*log10(abs(x))` |
| Behavioural V | PSpice `E … VALUE={}`, HSPICE `E … VOL=''` | `B … V={…}` |
| Behavioural I | PSpice `G … VALUE={}`, HSPICE `G … CUR=''` | `B … I={…}` |
| Lookup table | `E/G … TABLE {c}=(x,y)…` | `B … V/I={` portable PWL via `max`/`min`; a repeated breakpoint `x` becomes a `u()` step `}` |
| Conditional | PSpice/HSPICE `IF(c,a,b)` | LTspice profile keeps `IF()`; ngspice profile emits `(c)?(a):(b)` (native ngspice has no `if()`) |
| Comments | `;`, `// …`, and `$ …` **in HSPICE/SPICE3 only** | `; …` |
| Micro sign | `4.7µ` | `4.7u` |
| MOSFET level | HSPICE `LEVEL=49/53/54` | `8`/`14` (**warns**; verify by simulation) |
| Global nets | Spectre `gnd!` | `0` |

## Dialect-sensitive lexing

`$` begins an inline comment in **HSPICE** and **SPICE3/ngspice**, but in
**PSpice/OrCAD** a token like `$N_0001` is a *node name*. The PSpice reader
therefore does **not** treat `$` as a comment (it would otherwise silently eat
auto-generated `$N_xxxx` net names). `;` is an inline comment in every dialect;
`//` is Spectre-only (handled in that reader's preprocess so it never truncates
a `//` inside an include path).

Library sections (`.lib NAME` … `.endl`) are passed through verbatim rather
than flattened — a one-argument `.lib NAME` opens a section (kept as-is), while
a two-argument `.lib FILE SECTION` is an include. (`.endl` must not be mistaken
for `.end`, or everything after it is dropped.)

## Validator oracle caveats

`smeister validate` runs the **source** model under ngspice's matching
compatibility mode (`psa`/`hsa`) as the reference. That emulation is not a
perfect stand-in for the real vendor simulator:

- **`PWR` / `PWRS` with negative arguments.** The authoritative PSpice A/D
  reference defines `PWR(x,y)=|x|^y` and `PWRS(x,y)=sgn(x)·|x|^y`; SpiceMeister
  translates to exactly those. ngspice's `psa` mode instead computes
  `pwr(x,y)=pow(x,y)` and `pwrs(x,y)=sgn(x)·pow(x,y)`, i.e. the *opposite* sign
  for a negative base. So a **correct** PWR/PWRS translation can show a spurious
  FAIL when validated against `psa` and the argument goes negative. The
  translation follows the vendor reference; verify by hand (or in real LTspice)
  if in doubt.

## Functions deliberately **not** remapped

Shared built-ins pass through unchanged: `abs sqrt sin cos tan exp ln log
log10 int floor ceil min max if uramp u sgn ddt idt`. `log` is treated as
natural log in all four dialects (no remap). `LIMIT(x,lo,hi)` **is** remapped to
`max(lo,min(hi,x))`, because bare-ngspice `limit()` is not a 3-arg clamp (it is
in LTspice and in ngspice's ps/lt compat modes, but max/min is correct in every
mode). Add a vendor function by adding one entry to `rules/functions.json`.

## Passed through verbatim (with a warning)

`POLY(n)` sources (portable), and `LAPLACE` / `FREQ` / `CHEBYSHEV` sources —
these are kept as-is and flagged for manual review, because their LTspice and
ngspice forms differ and are not yet auto-translated.

## Not translatable as text (detected, reported)

`.PROTECT`/`.UNPROTECT` encrypted spans, encrypted libraries, Verilog-A /
Verilog-AMS source, and compiled OSDI modules. Use `smeister scan` (and
`--rea` for binary containers). Obtain an unencrypted or behavioural model from
the vendor instead.

## Dialect detection

`core/dialect.py` scores weighted markers and reports the evidence. Highlights:
Spectre `simulator lang=spectre`; HSPICE `='…'`, `.option`, `LEVEL=49/53/54`;
PSpice `VALUE={}`, `TABLE{}`, `.PROTECT`, `PWRS/STP`; LTspice `VDMOS`, `B… V=`.
No vendor marker above threshold → `spice3`. Override with `--from`.
