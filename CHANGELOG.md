# Changelog

All notable changes to SpiceMeister are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned (v0.2)
- AC / transient / noise equivalence metrics (current validation is a DC operating
  point only).
- Automated port-role inference.
- Expanded Spectre (Cadence) coverage beyond the current structural best-effort.

## [0.1.0] — 2026-11-19

Initial release.

### Added
- Parse → IR → transform → emit pipeline for **PSpice/OrCAD**, **HSPICE/Synopsys**
  and generic **SPICE3** (first-class), plus **Spectre/Cadence** (structural
  best-effort).
- LTspice-compatible and ngspice-native emitters, with a provenance banner and a
  change log.
- ngspice round-trip **DC operating-point equivalence** validator with conservative
  PASS / INCONCLUSIVE / NOT-VERIFIED outcomes.
- Data-driven function / model-level / behavioural-source mappings in `rules/*.json`.
- Zero-dependency stdio MCP server exposing the same capabilities to coding agents.
- REA bridge for characterising opaque **binary** model containers (entropy +
  embedded SPICE-marker scan); detects and reports vendor encryption, never
  circumvents it.
- CLI: `detect`, `translate`, `validate`, `inspect`, `scan`, `batch`, `mcp`.
- 40-test suite including adversarial regressions and TABLE→PWL ground-truth checks.

[Unreleased]: https://github.com/nbleak21/SpiceMeister/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/nbleak21/SpiceMeister/releases/tag/v0.1.0
