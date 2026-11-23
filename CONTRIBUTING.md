# Contributing to SpiceMeister

Thanks for your interest in improving SpiceMeister. This is a small,
dependency-free toolkit, so contributing is deliberately low-ceremony.

## Development setup

```bash
git clone https://github.com/nbleak21/SpiceMeister.git
cd SpiceMeister
python -m unittest discover -s tests          # run the suite
```

No build or install step is required to work on the code — the package is pure
Python standard library. `./bin/smeister …` runs the CLI straight from a checkout.

### Optional tooling

- **ngspice** (`brew install ngspice` / `apt-get install ngspice`) — required to
  run the round-trip validation tests. Without it those tests **self-skip** (you
  will see `OK (skipped=11)`), so the suite still passes on a bare checkout; CI
  installs ngspice so every test runs.
- **pytest** (`pip install -e '.[dev]'`) — optional; the suite also runs under it.

## Running tests

```bash
python -m unittest discover -s tests -v    # stdlib
pytest                                      # if installed
```

Please add or update tests for any behaviour change. The suite includes
**adversarial regressions** that assert a *wrong* translation does not validate —
if you touch the translation engine or the validator, keep those meaningful.

## Adding dialect mappings (no code needed)

Most function / model / behavioural-source mappings are **data**, not code. Edit
the JSON under `spicemeister/rules/`:

- `functions.json` — rename a function or expand it to a template (`$0,$1` args).
- `models.json` — `.model` type aliases and `LEVEL` remaps (these always emit a
  verify-by-simulation warning on purpose).
- `behavioral.json` — behavioural-source keyword handling.

Add a fixture under `tests/fixtures/` and a test that proves the mapping.

## Code conventions

- **Zero runtime dependencies** — standard library only. Don't add a
  `dependencies` entry to `pyproject.toml`.
- Support Python ≥ 3.8.
- Keep transforms **structural** (operate on the expression AST / IR), not regex
  over raw text, consistent with the existing passes.

## Scope / ethics

SpiceMeister translates models you are entitled to use and **detects and reports**
vendor encryption — it never circumvents it. Please keep contributions within that
line. See [`LICENSE`](LICENSE).

## Submitting changes

1. Branch off `main`.
2. Make the change with tests; ensure `python -m unittest discover -s tests` passes.
3. Open a pull request describing the dialect / behaviour affected.
