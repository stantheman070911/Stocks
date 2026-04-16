# AGENTS.md — AI Assistant Guide for `Stocks`

> This file is a mirror of [CLAUDE.md](CLAUDE.md) so any AI assistant (Claude, Codex, etc.) that looks for `AGENTS.md` by convention picks up the same guidance. Keep the two files in sync; prefer editing `CLAUDE.md` first and copying the result.

## Project Overview

**台股波段選股系統 (Taiwan Swing Trading Stock Scanner)** — a Taiwan equity scanner undergoing a staged rebuild from a single-file heuristic screener into a modular, point-in-time-safe, statistically validated research pipeline.

Two codebases live side-by-side during the migration:

| Path | Role | Status |
|---|---|---|
| `strategy_scanner.py` | Legacy v1 monolith (~1,790 lines). Weighted heuristic screener. Still the shippable path today. | Frozen for bug fixes only; retires at Phase 11 of the rebuild. |
| `tw_scanner/` | New v2 package. PIT-safe, pydantic-validated, typer CLI (`tw-scanner`). | **Phase 0 + Phase 1 complete.** Phase 2 (PIT data layer) in progress in the working tree. Phases 3–11 pending. |

- **Language**: Python 3.12+
- **Legacy entry point**: `python strategy_scanner.py`
- **New entry point**: `tw-scanner --help` (after `pip install -e .`)

---

## Roadmap & Reference Documents

These two files are the source of truth for where the project is going and how to talk to its primary data vendor. Keep them open while working on any non-trivial change.

### `theplan.md` — the rebuild roadmap (1,109 lines)

The **institutional-grade rebuild plan**, a reconciled superset of a Claude-authored review and a Codex-authored rewrite proposal. It defines:

- The target package architecture (every directory and module in `tw_scanner/` exists because this plan specified it).
- **11 phases** from security bootstrap through legacy retirement, each with exit criteria.
- Reconciled default values (turnover floors, embargo windows, winsorization bounds, transaction costs).
- Explicit non-goals (no broker, no portfolio optimizer, no guarantee of alpha before OOS validation).
- The verification strategy and the 10-question institutional acceptance test.

**How it helps the agent**: when asked to implement "the next phase", "Phase N", or any structural change, `theplan.md` is the authoritative spec. Do not invent architecture or defaults; quote the plan. When in doubt about a threshold or policy, `theplan.md §1.3` is the canonical answer. When the plan and the legacy code disagree, the plan wins — it is the reviewed target state.

**How it helps the codebase**: every config default in `tw_scanner/config/*.yaml` traces back to a line in `theplan.md`. The docstring in most new modules (e.g. `tw_scanner/data/universe.py`) references the plan section it addresses (e.g. "§2.1 survivorship").

### `doesthishelp.md` — the FinMind dataset catalog (728 lines)

A hand-curated reference for **FinMind's RESTful API and the 75+ datasets** used by the Phase 2 data layer. Contents:

- Base URL, authentication, rate limits (600 req/hr with token, 300 without).
- Quota-check endpoint (`/v2/user_info`).
- Full dataset catalog grouped by category (Technical, Chip/Institutional, Fundamental, Derivative, Real-Time, Convertible Bond, International, Global Macro).
- For each dataset: required tier, update frequency, column names, sample payloads.

**How it helps the agent**: when wiring up any `tw_scanner/data/*_client.py` or accessor (`prices.py`, `fundamentals.py`, `flows.py`, `margin.py`, etc.), **consult `doesthishelp.md` before calling FinMind**. It tells you (a) which dataset name to pass as the `dataset` parameter, (b) what tier the dataset requires, (c) what columns to expect, and (d) the PIT-relevant field (announce date, ex-date, etc.). Getting dataset names and column expectations wrong is a common failure mode; this catalog prevents it.

**How it helps the codebase**: the Phase 2 data layer replaces TWSE scraping with FinMind dataset pulls. Every FinMind dataset referenced in `tw_scanner/data/` (e.g. `TaiwanStockTradingDate`, `TaiwanStockDelisting`, `TaiwanStockInstitutionalInvestorsBuySell`, `TaiwanStockTotalReturnIndex`) is documented in this file. Phase 11 renames this file to `docs/references/finmind_catalog.md` for discoverability.

---

## Repository Structure

```
Stocks/
├── strategy_scanner.py            # Legacy v1 monolith — still shippable (retires at Phase 11)
├── theplan.md                     # Rebuild roadmap (see above)
├── doesthishelp.md                # FinMind catalog (see above)
├── CLAUDE.md                      # AI assistant guide (authoritative)
├── AGENTS.md                      # This file — mirror of CLAUDE.md
├── README.md                      # Human-facing quickstart
├── pyproject.toml                 # Single source of truth for deps + packaging
├── requirements.lock.txt          # pip-compile output, pinned for reproducibility
├── requirements.in                # Runtime constraint file for pip-compile
├── requirements-dev.in            # Dev constraint file for pip-compile
├── .env.example                   # Copy to .env and set FINMIND_TOKEN
├── .gitleaks.toml                 # Allowlist for gitleaks (cache / output dirs)
├── .pre-commit-config.yaml        # ruff + mypy + gitleaks hooks
├── .github/workflows/ci.yml       # CI: install → CLI help → pre-commit → lint → mypy → pytest → gitleaks
│
├── tw_scanner/                    # New v2 package (Phase 0+1 complete; Phase 2 in progress)
│   ├── __version__.py             # Single source of truth for package version
│   ├── cli.py                     # typer CLI: tw-scanner [--seed N] screen --dry-run
│   ├── config/
│   │   ├── schema.py              # pydantic AppConfig + 6 sub-configs (UniverseConfig, DataConfig, …)
│   │   ├── loader.py              # Merges package + user YAMLs; resolved_config_hash() for manifest
│   │   ├── __init__.py            # Public API: load_config, resolved_config_hash, AppConfig
│   │   ├── default.yaml           # Top-level composition file
│   │   ├── universe.yaml          # Liquidity cutoffs, price floor, industry exclusions
│   │   ├── data.yaml              # Cache dir, throttle params, FinMind quota, benchmark tickers
│   │   ├── signals.yaml           # Lookback windows, oscillator parameters
│   │   ├── scoring.yaml           # Missing-data mode, winsorize bounds, weights file pointer
│   │   ├── backtest.yaml          # IS/OOS split, horizons, fundamentals embargo (15/60/90-day)
│   │   ├── costs.yaml             # Broker 14.25 bps, tax 30 bps, half-spread slippage
│   │   └── weights_frozen_YYYYMMDD.yaml  # Empty stub — populated by Phase 5 calibration
│   ├── data/                      # Phase 2: PIT data layer (scaffolded; many modules partially implemented)
│   │   ├── base.py                # HttpClient + RateLimiter + DataNotAvailable exception (no monkey-patch)
│   │   ├── finmind_client.py      # FinMind REST with token-bucket + quota tracking + retry
│   │   ├── twse_client.py         # TWSE fallback only (T86, MI_MARGN, BFIAMU canaries)
│   │   ├── parquet_cache.py       # PIT-keyed parquet cache (replaces legacy pickle)
│   │   ├── calendar.py            # TaiwanStockTradingDate-based calendar (replaces hardcoded holidays)
│   │   ├── universe.py            # Merged TaiwanStockInfo + TaiwanStockDelisting (survivorship-aware)
│   │   ├── liquidity.py           # 20d/60d turnover, suspension, disposition, price-limit filters
│   │   ├── prices.py              # Unadjusted OHLCV + in-code PIT corp-action adjustment
│   │   ├── corp_actions.py        # Dividends / splits / capital reduction / par change
│   │   ├── fundamentals.py        # Financial statements + revenue with announce-date or embargo gating
│   │   ├── flows.py               # InstitutionalInvestorsBuySell + Shareholding (replaces T86 scrape)
│   │   ├── margin.py              # MarginPurchaseShortSale with typed columns (no position-based parsing)
│   │   ├── short_balance.py       # TaiwanDailyShortSaleBalances
│   │   ├── securities_lending.py  # TaiwanStockSecuritiesLending
│   │   ├── market_value.py        # TaiwanStockMarketValue
│   │   ├── industry.py            # industry_category authoritative (drops BFIAMU mapping table)
│   │   └── benchmarks.py          # TAIEX TotalReturnIndex + risk-free rate feed
│   ├── signals/                   # Phase 3: cross-sectional z-scored signals (stubs)
│   │   ├── base.py                # SignalFrame dataclass + Signal ABC + registry
│   │   └── foreign_flow.py, momentum.py, mean_reversion.py, trend.py, volatility.py,
│   │       quality.py, value.py, margin_pressure.py, liquidity.py, short_lending.py,
│   │       risk_metrics.py        # Each signal family — all stubs for now
│   ├── scoring/                   # Phase 4: composite score + missing policy (stubs)
│   │   ├── cleaning.py            # winsorize, clip, NaN policy
│   │   ├── normalize.py           # z-score, sector-neutral z-score
│   │   ├── composite.py           # weighted sum with frozen weights
│   │   ├── missing_policy.py      # strict | penalized | legacy_reweighted
│   │   └── model_card.md          # Living artifact — regenerated on calibration
│   ├── research/                  # Phase 5: OFFLINE — not imported by pipeline.screen (stubs)
│   │   └── ic.py, backtest.py, execution_model.py, calibrate.py, challengers.py,
│   │       regime.py, validation.py
│   ├── entry_signals/             # Phase 6: entry-timing overlay (separate from alpha scoring; stubs)
│   │   └── pullback.py            # MA20 touch + next-day confirmation state machine
│   ├── diagnostics/               # Phase 7: turnover, coverage, HHI, IC drift (stubs)
│   │   └── turnover.py, coverage.py, concentration.py, ic_drift.py, html_dashboard.py
│   ├── pipeline/                  # Phase 8: orchestration
│   │   ├── screen.py              # Current: loads config + emits config_hash; real flow arrives in later phases
│   │   └── report.py              # CSV + chart + manifest emission (stub)
│   ├── governance/                # Phase 10: manifest + lineage + changelog (scaffolded)
│   │   └── manifest.py, lineage.py, changelog.py
│   └── utils/
│       ├── logging.py             # JSON structured logger — log_stage(stage, rows_in, rows_out, duration_ms, warnings)
│       └── io.py                  # parquet + utf-8-sig CSV helpers (stub)
│
├── config/                        # User-override YAMLs mirroring tw_scanner/config/*.yaml
│   └── {default,universe,data,signals,scoring,backtest,costs,weights_frozen_YYYYMMDD}.yaml
│
├── tests/
│   ├── conftest.py
│   ├── unit/{data,signals,scoring,diagnostics,utils}/     # .gitkeep — populated by later phases
│   ├── integration/
│   │   └── test_cli_smoke.py      # CLI help + --dry-run round-trip (2 tests, green)
│   ├── property/                  # .gitkeep — hypothesis tests land in Phase 9
│   ├── data_contract/             # .gitkeep — @pytest.mark.live schema canaries
│   └── fixtures/                  # .gitkeep — parquet snapshots
│
├── docs/                          # Scaffolded for Phase 10
│   └── README.md, QUICKSTART.md, DATA_SOURCES.md, MODEL_CARD.md, SIGNALS_REFERENCE.md,
│       BACKTEST_METHODOLOGY.md, DATA_LINEAGE.md, GOVERNANCE.md, VALIDATION_LOG.md,
│       INTERPRETING_OUTPUT.md, CHANGELOG.md, adr/
│
├── strategy_output/               # Legacy scanner outputs (auto-created by v1)
│   ├── candidates.csv
│   ├── entry_signals.csv
│   └── score_chart.png
│
└── .cache/                        # Parquet cache (replaces legacy pickle at .cache/finmind/...)
```

---

## Install & Run

### New package (v2)

```bash
python -m pip install -r requirements.lock.txt
python -m pip install -e . --no-deps
tw-scanner --help
tw-scanner --seed 7 screen --dry-run          # Phase 1 exit gate: emits config_hash
```

Local dev note: if the editable install goes stale after a package-data edit (seen occasionally with setuptools `__editable__.*.pth`), run `pip install -e . --no-deps --force-reinstall`.

### Legacy script (v1)

```bash
python strategy_scanner.py
```

Produces `strategy_output/candidates.csv`, `entry_signals.csv`, `score_chart.png`, plus a `run_manifest.json` and `dropped.csv` from the audit-era hardening work.

### Secrets

Copy `.env.example` → `.env` and paste your FinMind token (finmindtrade.com, free tier). Both v1 and v2 read the token from `FINMIND_TOKEN`. The file is gitignored; never commit tokens. Pre-commit and CI both run `gitleaks`.

---

## Configuration System (v2 — Phase 1)

All tunables live in YAML files loaded by `tw_scanner/config/loader.py` and validated by the pydantic schemas in `tw_scanner/config/schema.py`.

| File | Schema class | Governs |
|---|---|---|
| `universe.yaml` | `UniverseConfig` | Market (twse/tpex), liquidity turnover floor, price floor, top_n, industry exclusions |
| `data.yaml` | `DataConfig` | Cache dir, FinMind quota, throttle windows, max_workers, benchmarks, risk-free fallback |
| `signals.yaml` | `SignalsConfig` | MA periods, ATR/RSI/ADX/Bollinger params, momentum windows, foreign-flow windows, vol/drawdown windows |
| `scoring.yaml` | `ScoringConfig` | missing_mode (strict/penalized/legacy_reweighted), winsorize bounds, z-clip, weights file pointer |
| `backtest.yaml` | `BacktestConfig` | Rebalance cadence, horizons, IS/OOS split, purge embargo, fundamentals embargo (15/60/90-day) |
| `costs.yaml` | `CostsConfig` | Commission (14.25 bps), transaction tax (30 bps), slippage model |

Resolution: **package defaults** at `tw_scanner/config/*.yaml` are merged with **user overrides** at `config/*.yaml` (top-level wins). `resolved_config_hash(cfg)` returns the sha256 of the validated config; this hash is emitted to logs and must appear in every `run_manifest.json`.

Cross-field validators enforce invariants: throttle min ≤ max, MA periods strictly increasing, IS/OOS dates ordered, winsorize lower < upper, mom_12_1_skip < mom_12_1_long.

---

## Legacy Configuration (v1 — for strategy_scanner.py only)

The legacy `CONFIG` block at [strategy_scanner.py:117-140](strategy_scanner.py:117) still uses module-level constants. The only change from the audit era is `FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "").strip()` — the hardcoded token was removed and git history was scrubbed (verified via `gitleaks` across all 16 commits).

Leave the legacy constants alone unless the user explicitly asks to change v1 behavior.

---

## Phase Progress Tracker

Source of truth: `theplan.md`. Status as of this session (2026-04-16):

| Phase | Scope | Status |
|---|---|---|
| 0 | Security, packaging, bootstrap | ✅ Complete (commits `ece816b`, `2ff5c10`) |
| 1 | Pydantic config system | ✅ Complete (uncommitted working-tree changes flush Phase 1; `tw-scanner screen --dry-run` green) |
| 2 | PIT-safe data layer | 🚧 In progress — `data/base.py`, `finmind_client.py`, `calendar.py`, `universe.py`, `prices.py`, `flows.py`, `margin.py`, `parquet_cache.py` partially implemented (uncommitted) |
| 3 | Data cleaning + signal library | ⏳ Stubs only |
| 4 | Scoring v2 (composite_rank, missing policy) | ⏳ Stubs only |
| 5 | Research: IC, backtest, calibration, challengers | ⏳ Stubs only |
| 6 | Entry-timing overlay | ⏳ Stub (`entry_signals/pullback.py`) |
| 7 | Diagnostics + warnings | ⏳ Stubs only |
| 8 | Daily pipeline + manifest | ⏳ `pipeline/screen.py` is a Phase 1 placeholder |
| 9 | Testing + CI | 🟡 CI green; 2 smoke tests; broader coverage lands with feature phases |
| 10 | Documentation + governance | 🟡 Docs tree scaffolded with placeholders |
| 11 | Legacy retirement | ⏳ `strategy_scanner.py` stays live until all prior phases pass acceptance |

Update memory (`memory/project_audit_status.md`) whenever a phase closes.

---

## Legacy Execution Pipeline (for reference while v1 is still shippable)

`main()` in `strategy_scanner.py` runs 10 sequential steps with `tqdm` progress bars:

| Step | Function | Data Source | Description |
|------|----------|-------------|-------------|
| 1 | `get_stock_list()` | TWSE openapi | Fetch ~1,600 listed stocks; exclude financials |
| 2 | `get_foreign_ranking()` | TWSE T86 | 30+ days of foreign buy/sell, ranked by cumulative net buying + consecutive buy days |
| 3 | `download_prices()` | yfinance | Parallel batched OHLCV download |
| 4 | `analyze_tech()` | Local prices | MA20/60/120, KD, Fibonacci, volume ratio → `tech_score` (0–25) |
| 5 | `get_top_industries()` | TWSE MI_INDEX20 | Top 5 sectors by trading volume |
| 6 | `get_margin()` | TWSE MI_MARGN | Margin/short balances, `squeeze_ratio` |
| 7 | `get_fundamentals()` | FinMind (optional) | P/E, P/B, ROE, GPM |
| 8 | `calc_risk()` | yfinance + 0050 | Beta, Sharpe, Sortino |
| 9 | `detect_entry()` | Local | MA20 touch + foreign support + volume surge |
| 10 | `score_stock()` + `save_output()` | Aggregated | Composite 0–100, CSV + chart |

This pipeline is the **challenger model** in Phase 5 — its scores will be compared against the calibrated v2 composite on out-of-sample data. Do not delete or refactor v1 without user approval; it is load-bearing for the rebuild's validation gate (`theplan.md §5.3`).

---

## Code Conventions

Inherited from v1, preserved in v2:

- **Naming**: `snake_case` for functions/variables; `UPPER_CASE` for module-level constants.
- **Type hints**: Python 3.10+ union syntax (`dict | None`, `list[str]`). No `from __future__ import annotations` in v2 (v1 legacy files may have it). New code uses modern syntax only.
- **Docstrings**: Mixed English + Traditional Chinese (繁體中文) — the technical overview in English, domain-specific remarks in Chinese. Maintain this bilingual style when editing existing modules; English-only is fine for new modules.
- **Section headers**: `═` box-drawing separators in legacy files and larger v2 modules. Not required for short files.
- **Error handling**:
  - v1: safe fallbacks (`return {}`, `return pd.DataFrame()`) on failure.
  - v2: raise `DataNotAvailable` at accessor boundaries; pipeline catches and records in `data_quality.csv`. Never silently return a neutral value that passes filters — this was audit finding §2.11.

---

## Testing & CI

- **Run tests**: `pytest` (from the repo root, with the venv activated).
- **Current coverage**: 2 smoke tests in `tests/integration/test_cli_smoke.py` exercising `--help` and `--dry-run`. Property / unit / data-contract tests land with their feature phases per `theplan.md §9`.
- **Lint / type / secret gates**: `ruff check tw_scanner tests`, `mypy tw_scanner`, `gitleaks detect`. All three green as of Phase 1 exit.
- **CI**: `.github/workflows/ci.yml` — `pip install -r requirements.lock.txt` → `pip install -e . --no-deps` → `tw-scanner --help` → `pre-commit run --all-files` → `ruff` → `mypy` → `pytest` → `gitleaks`. Python 3.12, ubuntu-latest.
- **Pre-commit**: local-system `ruff`, `ruff-format`, `mypy` hooks + upstream `gitleaks` (v8.30.1 pinned). `language: system` requires the venv activated when invoking `pre-commit run` locally.

---

## Common Tasks (v2)

### Add or change a config default
1. Edit both `tw_scanner/config/<section>.yaml` (package default) and `config/<section>.yaml` (user override) if the new default should be visible to both code paths.
2. If the change requires a new field or validator, update `tw_scanner/config/schema.py`.
3. Run `tw-scanner --seed 7 screen --dry-run` to confirm the config loads and the hash is stable.
4. Add a row to `docs/CHANGELOG.md`.

### Add a new data accessor (Phase 2)
1. Look up the dataset in **`doesthishelp.md`** — confirm name, tier, columns, PIT field.
2. Create or extend a module in `tw_scanner/data/`. Follow the contract in `data/base.py`: signature `(stock_ids, as_of, lookback_days, cfg) -> pd.DataFrame`, raise `DataNotAvailable` on failure, cache raw + processed in `parquet_cache`.
3. Add a live canary test under `tests/data_contract/` marked `@pytest.mark.live`.
4. Document the dataset and its PIT rule in `docs/DATA_SOURCES.md`.

### Add a new signal (Phase 3)
1. Follow `signals/base.py::SignalFrame` and the production catalog in `theplan.md §3.3`.
2. Return NaN when uncomputable — **never a neutral default**.
3. Add unit tests asserting NaN behavior, monotonicity on a toy dataset, and sector-neutrality invariants.
4. Run Phase 5 IC validation before promoting to production scoring.

### Add output columns
Modify the relevant stage in `pipeline/screen.py` and the manifest schema in `governance/manifest.py`. Column-order conventions are in `docs/INTERPRETING_OUTPUT.md` (Phase 10).

### Enable fundamentals
Set `FINMIND_TOKEN` in `.env`. No code changes. A Backer/Sponsor tier unlocks the bulk endpoints referenced in `doesthishelp.md` — reflected in `data.yaml::finmind_quota_requests_per_hour`.

---

## Known Limitations

- **Two codebases live at once.** Until Phase 11, both `strategy_scanner.py` and `tw_scanner/` must keep working. Do not delete legacy helpers without an ADR.
- **Phase 2 is in flight.** Many `tw_scanner/data/*.py` modules are partially implemented but not yet wired into `pipeline/screen.py`. The CLI `--dry-run` exits after the config step; the full screening flow arrives in Phase 8.
- **No production weights.** `config/weights_frozen_YYYYMMDD.yaml` is an empty stub. Until Phase 5 produces a signed frozen-weights file, any v2 screening output must be labeled **discovery / research only**.
- **FinMind rate limits.** Free tier is 600 req/hr. The token-bucket limiter in `data/finmind_client.py` enforces this; backtests requiring deeper history may need a Backer+ tier or a patient per-ticker pull.
- **TWSE fragility.** The fallback TWSE scrape in `data/twse_client.py` is retained only as an emergency canary. Schema drift is caught by the weekly `@pytest.mark.live` canaries in `tests/data_contract/` (Phase 2 exit criterion).

---

## Memory

Auto-memory for this project lives in `~/.claude/projects/-Users-stanleylu-Desktop-alex-Stocks-main/memory/`. Key file: `project_audit_status.md` — records the v1 audit findings already implemented and the current rebuild-phase status. Update it when a phase closes or when you discover a new non-obvious fact worth persisting.
