# Stocks

Phase 0 bootstrap for a Taiwan equity scanner rebuild.

## Install

```bash
python -m pip install -e .
tw-scanner --help
```

The legacy single-file scanner remains available:

```bash
python strategy_scanner.py
```

## Secrets

Copy `.env.example` to `.env` and set `FINMIND_TOKEN` locally if fundamentals are needed.
Do not commit `.env` or paste API tokens into source files.
