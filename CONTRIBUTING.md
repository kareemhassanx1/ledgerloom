# Contributing

LedgerLoom supports Python 3.12 and newer. Create an isolated environment and
install the package with its pinned development tools:

```shell
python -m venv .venv
python -m pip install -e ".[dev]"
```

Activate the environment using the command appropriate for your shell, then run
the project quality checks:

```shell
python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest -q --cov=ledgerloom --cov-report=term-missing --cov-fail-under=85
python -m pip wheel --no-deps --wheel-dir dist .
python scripts/verify_wheel.py dist/*.whl
git diff --check
```

Build distribution artifacts in a clean checkout of the intended revision.
The wheel verifier compares package paths and contents with tracked source,
allowing Windows newline conversion for Python files and typing markers. It detects stale build
files, missing modules, duplicate archive entries, and modified package bytes.

Keep changes focused on one coherent behavior. Add or update behavioral tests
for observable changes, document public contracts, and preserve deterministic
ordering and stable diagnostic codes. Commit messages should describe the real
change made. Do not add filler files or commits, duplicate tests for volume,
generated padding, backdated commits, or rewritten published history.

Before committing, inspect the staged diff and include only files belonging to
that change. Never discard unrelated work already present in the working tree.
