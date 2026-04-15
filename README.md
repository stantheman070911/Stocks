# Stocks

Phase 0 bootstrap for a Taiwan equity scanner rebuild.

## Install

Local `pre-commit` runs need Python 3.12 and Go 1.24.11+ so the pinned
upstream gitleaks hook can build its managed scanner binary.

```bash
python -m pip install -r requirements.lock.txt
python -m pip install -e . --no-deps
tw-scanner --help
```

Development tools are pinned in `requirements.lock.txt`; install from the lock before
running `pre-commit` or `pytest`.

The legacy single-file scanner remains available:

```bash
python strategy_scanner.py
```

## Secrets

Copy `.env.example` to `.env` and set `FINMIND_TOKEN` locally if fundamentals are needed.
Do not commit `.env` or paste API tokens into source files.
