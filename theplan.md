# Plan — Institutional-Grade Rebuild of the Taiwan Swing Stock Scanner (Combined Final)

## Context

The existing [strategy_scanner.py](../../Desktop/alex/Stocks-main/strategy_scanner.py) (1790 lines, single file) is a weighted heuristic screener that an institutional-style review flagged on research, implementation, operational, and governance risk grounds. The review ran against a detailed institutional standard; a separate Codex-authored plan proposed a parallel rebuild. This plan is the **reconciled superset** of both: every finding from the Claude review and every scope item from the Codex plan is included, with differences explicitly resolved in favour of the more conservative / more rigorous option.

**Intended outcome**: a modular, point-in-time-safe, survivorship-aware, statistically validated Taiwan equity screener whose `candidates.csv` a discretionary retail user can trust. Final sizing, execution, and portfolio-level risk management remain explicitly out of scope (the user does these).

**What is in scope**: universe construction, liquidity/tradeability filtering, PIT data, PIT corporate actions, PIT fundamentals, data cleaning, signal construction, scoring, IC/backtest validation, weight calibration, entry-timing overlay as a *separate* module, diagnostics, tests, CI, governance, docs, legacy retirement.

**What is out of scope (retail non-goals)**: broker integration, automatic order placement, position sizing, portfolio optimizer, tax-lot accounting, market-impact modelling, OMS/PMS stack, any guarantee of alpha before OOS validation, treating any entry flag as a "buy" instruction without independent validation.

---

## Execution Principles

1. **No big-bang.** Every phase leaves the repo runnable; the legacy `strategy_scanner.py` stays green until the new pipeline passes acceptance.
2. **Freeze-then-validate.** No weight change, no new production signal, no threshold tweak ships without IC + OOS evidence. Signals with non-positive OOS IC get weight 0.
3. **PIT or nothing.** Every accessor takes `as_of` and returns only information knowable at that date.
4. **Silent-neutral defaults are bugs.** Missing signal → NaN → row excluded (strict mode default) or explicitly penalized, never defaulted to a "neutral" value that passes filters.
5. **Output comparability.** `composite_rank` must be strictly comparable across rows; no apples-to-oranges reweighting when modules are missing.
6. **Retail simplifications are explicit.** Anything IB-shops do that we skip (portfolio optimizer, OMS, market-impact model) is listed in the Non-Goals section, not silently dropped.
7. **Every phase is reversible via git** until Phase 11 legacy retirement.

---

## Target Architecture

```text
tw_scanner/
├── __init__.py
├── __version__.py                 # single source of truth; referenced in manifest
├── cli.py                         # typer/argparse single entrypoint
├── config/
│   ├── __init__.py
│   ├── schema.py                  # pydantic models — validated at startup
│   ├── loader.py                  # resolves env vars → config hash sha256
│   ├── default.yaml
│   ├── universe.yaml
│   ├── data.yaml
│   ├── signals.yaml
│   ├── scoring.yaml
│   ├── backtest.yaml
│   ├── costs.yaml                 # transaction cost model
│   └── weights_frozen_YYYYMMDD.yaml  # versioned, committed
├── data/
│   ├── __init__.py
│   ├── base.py                    # HttpClient, RateLimiter, Throttle — no monkeypatch
│   ├── finmind_client.py          # wraps FinMind DataLoader; quota tracking
│   ├── twse_client.py             # fallback only; schema canaries live here
│   ├── parquet_cache.py           # PIT-keyed cache; replaces pickle
│   ├── calendar.py                # TaiwanStockTradingDate
│   ├── universe.py                # TaiwanStockInfo + TaiwanStockDelisting; PIT snapshots
│   ├── liquidity.py               # turnover, suspension, disposition, price-limit filters
│   ├── prices.py                  # unadjusted OHLCV + in-code PIT adjustment
│   ├── corp_actions.py            # dividends/splits/capital reductions/par changes
│   ├── fundamentals.py            # announce-date or embargoed period-end
│   ├── flows.py                   # institutional buy/sell + foreign ownership
│   ├── margin.py                  # margin/short typed columns (no position parsing)
│   ├── short_balance.py           # TaiwanDailyShortSaleBalances
│   ├── securities_lending.py      # TaiwanStockSecuritiesLending
│   ├── market_value.py            # TaiwanStockMarketValue
│   ├── industry.py                # industry classification; drops BFIAMU mapping table
│   └── benchmarks.py              # TAIEX total-return + risk-free rate feed
├── signals/
│   ├── __init__.py
│   ├── base.py                    # SignalFrame dataclass + Signal ABC + registry
│   ├── foreign_flow.py
│   ├── momentum.py
│   ├── mean_reversion.py
│   ├── trend.py
│   ├── volatility.py
│   ├── quality.py
│   ├── value.py
│   ├── margin_pressure.py
│   ├── liquidity.py
│   ├── short_lending.py
│   └── risk_metrics.py            # Dimson beta, Sharpe, Sortino — diagnostics only, NOT scoring
├── scoring/
│   ├── __init__.py
│   ├── cleaning.py                # winsorize, clip, NaN policy
│   ├── normalize.py               # z-score, sector-neutral z-score
│   ├── composite.py               # weighted sum with frozen weights
│   ├── missing_policy.py          # strict | penalized | legacy_reweighted (warns)
│   └── model_card.md              # living artifact; updated each calibration
├── research/                      # OFFLINE ONLY — never imported by pipeline.screen
│   ├── __init__.py
│   ├── ic.py                      # pearson + spearman + decay + rolling + by-sector/size/regime
│   ├── backtest.py                # walk-forward, purged, embargoed; costs-aware
│   ├── execution_model.py         # next-open fill, limit-up/down non-fill, suspension block
│   ├── calibrate.py               # constrained ridge / IC-weighted optimization
│   ├── challengers.py             # equal-weight / random-weight / flow-only / momentum-only / legacy
│   ├── regime.py                  # vol and margin-maintenance regime tagging
│   ├── validation.py              # purged KFold + embargo + bootstrap CI + deflated Sharpe
│   └── notebooks/                 # exploratory; outputs gitignored
├── pipeline/
│   ├── __init__.py
│   ├── screen.py                  # replaces main() in strategy_scanner.py
│   └── report.py                  # CSV + chart + manifest emission
├── diagnostics/
│   ├── __init__.py
│   ├── turnover.py                # Jaccard top-N vs previous run
│   ├── coverage.py                # signal coverage trends
│   ├── concentration.py           # sector HHI, beta/vol/liquidity distributions
│   ├── ic_drift.py                # rolling IC vs calibration baseline; σ-threshold alert
│   └── html_dashboard.py          # plotly static HTML (optional)
├── entry_signals/                 # kept separate from alpha scoring
│   ├── __init__.py
│   └── pullback.py                # MA20 touch + next-day confirmation
├── governance/
│   ├── __init__.py
│   ├── manifest.py                # git SHA + config hash + weights hash + env + coverage
│   ├── lineage.py                 # column → source dataset mapping emitted to docs
│   └── changelog.py               # programmatic changelog helper
└── utils/
    ├── __init__.py
    ├── logging.py                 # structured JSON per stage: rows_in/rows_out/duration_ms
    └── io.py                      # parquet + utf-8-sig CSV helpers

tests/
├── conftest.py                    # fixtures: mocked FinMind responses, frozen as-of dates
├── unit/
│   ├── data/
│   ├── signals/
│   ├── scoring/
│   ├── diagnostics/
│   └── utils/
├── integration/
│   ├── test_pipeline_smoke.py     # full synthetic 10-ticker 1-year run
│   ├── test_pipeline_regression.py # frozen 2023-06-30 as-of; hash-asserted output
│   ├── test_backtest_smoke.py
│   └── test_calibration_smoke.py
├── property/                      # hypothesis-based
│   ├── test_scoring_invariants.py
│   └── test_normalize_invariants.py
├── data_contract/                 # live; @pytest.mark.live; weekly CI
│   ├── test_finmind_schemas.py
│   └── test_twse_schemas.py
└── fixtures/                      # parquet snapshots of PIT data

docs/
├── README.md
├── QUICKSTART.md
├── DATA_SOURCES.md                # per-dataset PIT rules, tiers, update times
├── MODEL_CARD.md
├── SIGNALS_REFERENCE.md           # formula, lookback, expected IC sign, validation status
├── BACKTEST_METHODOLOGY.md        # assumptions, leakage controls, costs, metrics
├── DATA_LINEAGE.md                # CSV columns → source dataset + transformation
├── GOVERNANCE.md                  # model-change rules
├── VALIDATION_LOG.md              # dated IS/OOS metrics per calibration
├── INTERPRETING_OUTPUT.md         # how to read candidates.csv for discretionary sizing
├── CHANGELOG.md
└── adr/                           # Architecture Decision Records

config/                            # top-level YAML also lives here for CLI discoverability
legacy/
└── strategy_scanner.py            # moved here only after Phase 11 acceptance

.cache/
├── finmind/raw/{dataset}/...
├── finmind/processed/{table}/...
└── twse/{dataset}/...             # fallback cache
```

---

## Target End State — CLI & Outputs

**Single entrypoint** (`tw_scanner.cli`):

```bash
tw-scanner screen     --as-of YYYY-MM-DD [--top-n 200] [--missing-mode strict]
tw-scanner data       refresh --as-of YYYY-MM-DD [--mode bulk|per-ticker]
tw-scanner signals    compute --as-of YYYY-MM-DD
tw-scanner research   ic --signal NAME --horizon 20
tw-scanner research   backtest --start YYYY-MM-DD --end YYYY-MM-DD
tw-scanner research   calibrate --is-end YYYY-MM-DD
tw-scanner diagnose   turnover --from YYYY-MM-DD --to YYYY-MM-DD
tw-scanner diagnose   coverage --as-of YYYY-MM-DD
tw-scanner --legacy   screen                         # invokes old strategy_scanner.py until Phase 11
```

**Output artifacts** under `strategy_output/`:

| File | Phase owner | Purpose |
|---|---|---|
| `candidates.csv` | 8 | Ranked shortlist with `composite_rank`, per-signal z-scores, tilt columns |
| `score_decomposition.csv` | 8 | Per-candidate raw/cleaned/z/contribution per signal |
| `entry_signals.csv` | 6 | Entry-state overlay (`none`/`touched`/`confirmed`/`invalidated`) |
| `dropped.csv` | 8 | Exclusions with reason |
| `data_quality.csv` | 8 | Per-source coverage, staleness, warnings |
| `run_manifest.json` | 8 | git SHA + config hash + weights hash + env + diagnostics + tilt summary |
| `score_chart.png` | 8 | Rank distribution + IC trend + sector/beta histograms |
| `diagnostics.html` | 7 | Plotly static dashboard (optional) |
| `backtest_summary.csv` | 5 | Rank-IC, Sharpe, decile spreads, hit rate, turnover |
| `backtest_deciles.csv` | 5 | Decile forward returns by horizon |
| `turnover_diagnostics.csv` | 7 | Daily Jaccard top-N stability |
| `docs/MODEL_CARD.md` | 10 | Living model doc; regenerated on calibration |

---

## Phase 0 — Security, Packaging, Bootstrap

**Addresses**: review §2.14 (hardcoded token), operational gaps (no deps pinning, no versioning).

### 0.1 Security (do first)

- **Revoke** the FinMind token at [strategy_scanner.py:103](../../Desktop/alex/Stocks-main/strategy_scanner.py:103) immediately. Issue a new token.
- **Remove** the hardcoded token from source; replace with `os.environ.get("FINMIND_TOKEN")` fallback to empty string (preserves current "skip fundamentals" semantics).
- **Scrub git history** with `git filter-repo --replace-text` (or BFG) — coordinate with user before force-push. This is the only destructive operation in the plan; it requires explicit user confirmation at execution time.
- Add `.env` (gitignored) + `.env.example` (committed).
- Install `gitleaks` as a pre-commit hook and CI step; fail builds on any detected secret.
- Update `.gitignore` to explicitly cover `.env`, `.venv/`, `.cache/`, `strategy_output/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.coverage`, `htmlcov/`, `*.pyc`.

### 0.2 Packaging

- `pyproject.toml` as single source of truth.
- Python `>=3.12`.
- **Runtime deps**: `requests`, `pandas`, `numpy`, `scipy`, `tqdm`, `matplotlib`, `lxml`, `openpyxl`, `finmind`, `python-dotenv`, `pyarrow`, `pydantic`, `pyyaml`, `typer` (CLI).
- **Research/dev deps**: `pytest`, `pytest-cov`, `pytest-mock`, `ruff`, `mypy`, `pandas-stubs`, `types-requests`, `pre-commit`, `gitleaks`, `statsmodels`, `scikit-learn`, `plotly`, `hypothesis`, `ipykernel`.
- `yfinance` demoted to optional validation fallback — not runtime.
- `pip-compile` generates `requirements.lock.txt`.

### 0.3 Bootstrap

- `tw_scanner.__version__` single source of truth; referenced in manifest and help.
- `cli.py` with typer; `--help` prints usage; `--seed N` CLI flag seeds Python `random` and NumPy `numpy.random` for deterministic jitter and stochastic steps.
- Structured logging via stdlib `logging` with JSON formatter; each stage logs `{stage, rows_in, rows_out, duration_ms, warnings}`.

### 0.4 Package skeleton

Create the full directory tree from the "Target Architecture" section with empty `__init__.py` and stub files. This unlocks subsequent phases to commit without needing full implementations.

**Exit criteria**: `pip install -e .` succeeds in a fresh venv; `tw-scanner --help` prints; `pre-commit run --all-files` green; `gitleaks` finds no secrets; CI placeholder workflow runs.

---

## Phase 1 — Configuration System

**Addresses**: review recommendation #24 (magic numbers); Codex Phase 1.

### 1.1 Typed YAML configs

- `config/default.yaml` — top-level composition; includes other files.
- `config/universe.yaml` — liquidity cutoffs, exclusions, price floor.
- `config/data.yaml` — per-source paths, TTLs, quota limits.
- `config/signals.yaml` — per-signal lookback windows, parameters.
- `config/scoring.yaml` — weights (pointer to frozen file), missing-data mode, winsorization bounds, sector-neutralization rules.
- `config/backtest.yaml` — horizons, rebalance cadence, IS/OOS split, purge/embargo.
- `config/costs.yaml` — TW equity transaction cost model (see §1.3).
- `config/weights_frozen_YYYYMMDD.yaml` — versioned, produced by Phase 5 calibration.

### 1.2 Pydantic validation

- `config/schema.py` — pydantic models for each YAML; validated at startup, fails fast on invalid config.
- `config/loader.py::resolved_config_hash()` — sha256 of the fully resolved config (after env-var substitution); included in every run manifest.

### 1.3 Default values (reconciled)

- **Universe**: TWSE common stock only (TPEx schema-ready but not activated in v2).
- **Liquidity**: 20-day median NT$ turnover ≥ NT$50M; 60-day median kept as diagnostic.
- **Price floor**: close ≥ NT$10 (conservative — Codex value, stricter than original NT$5 suggestion; reduces pump-and-dump exposure).
- **Financial sector exclusion**: via `TaiwanStockInfo.industry_category`, not name keyword.
- **Benchmark**: TAIEX total return; 0050 retained as secondary.
- **Missing-data mode**: `strict` (default).
- **Backtest rebalance**: weekly.
- **Backtest horizons**: 5 / 10 / 20 / 60 trading days.
- **IS/OOS split**: train 2018-01 to 2022-12; test 2023-01 onward.
- **Purge + embargo**: max signal lookback + 20 trading days.
- **Fundamentals embargo** (Codex's granular version, preferred over my flat 45-day):
  - Monthly revenue: release date or `period_end + 15 calendar days`, whichever available.
  - Quarterly statements: `period_end + 60 calendar days`.
  - Annual statements: `fiscal_year_end + 90 calendar days`.
- **Winsorization**: 1st/99th percentile on raw signal values.
- **Z-score clip**: [-3, 3].
- **Transaction costs** (`config/costs.yaml`):
  - Broker commission: 0.1425% buy+sell (discountable).
  - Securities transaction tax: 0.3% sell side.
  - Slippage proxy: half-spread estimated from high-low range; configurable.
  - Round-trip default: ~0.585% + slippage.

**Exit criteria**: `tw-scanner screen --dry-run` loads config successfully; config hash emitted; pydantic rejects malformed YAML.

---

## Phase 2 — PIT-Safe Data Layer

**Addresses**: review §2.1 (survivorship), §2.2 (fundamentals look-ahead), §2.3 (yfinance retroactive), §2.4 (liquidity), §2.10 (benchmark), §2.16 (TWSE fragility); FinMind catalog findings.

### 2.0 Universal accessor contract

Every data accessor in this phase:
- Signature: `fn(stock_ids: list[str] | None, as_of: date, lookback_days: int | None, cfg) -> pd.DataFrame`.
- Returns columns include: `source`, `dataset`, `retrieved_at`, `as_of`, `available_date` where applicable, `data_quality_flags`.
- Caches immutable raw response + processed PIT table under `.cache/finmind/{raw|processed}/{dataset}/`.
- Parquet via pyarrow (replaces pickle — portable, schema-evolvable).
- Raises `DataNotAvailable` on failure; pipeline catches and degrades gracefully with explicit manifest warning.
- `retrieved_at` + dataset immutability: any `as_of < today` is cached forever; `as_of == today` has 1-day TTL.

### 2.1 FinMind client (`data/finmind_client.py`)

- Wraps `finmind.data.DataLoader` with:
  - Token-bucket rate limiter: 600 req/hr free-tier default; configurable for Backer/Sponsor.
  - Quota tracking via `GET /v2/user_info`; stops cleanly at 95% of limit.
  - Retry with exponential backoff on HTTP 402 / 429.
  - Per-ticker mode (free tier) and bulk/all-stocks mode (Backer+) with same interface.

### 2.2 TWSE fallback client (`data/twse_client.py`)

- Keeps T86, MI_MARGN, BFIAMU only as emergency fallback — tagged in manifest with `source=twse_fallback`.
- Preserves domain-aware throttle from [strategy_scanner.py:219-272](../../Desktop/alex/Stocks-main/strategy_scanner.py:219) but **removes** the `requests.get` global monkeypatch described in [AGENTS.md:103-105](../../Desktop/alex/Stocks-main/AGENTS.md:103). All calls go through explicit `HttpClient` instances.

### 2.3 Trading calendar (`data/calendar.py`)

- `get_trading_calendar(start, end)` → `TaiwanStockTradingDate` (free tier).
- Replaces the hardcoded `TW_MARKET_HOLIDAYS` frozenset at [strategy_scanner.py:344-388](../../Desktop/alex/Stocks-main/strategy_scanner.py:344).
- Empirical `trading_days_per_year` replaces `TRADING_DAYS_YEAR = 252` (TW ≈ 245).

### 2.4 PIT universe (`data/universe.py`)

**Addresses**: review §2.1 (survivorship).

- `get_listed_universe(as_of)`:
  - Daily snapshot of `TaiwanStockInfo` persisted to `.cache/finmind/processed/universe/YYYY-MM-DD.parquet`.
  - **Monthly historical backfill**: persist `data/snapshots/universe_YYYYMM.parquet` going back 5 years so the first historical backtest has coverage even without prior daily runs (my addition — Codex did not address the cold-start problem).
  - Merge `TaiwanStockDelisting` (free tier, 2001–present) so delisted names remain queryable for historical `as_of`.
  - Exclude: ETFs, warrants, TDRs, preferreds, funds, rights, convertible bonds. Retain non-4-digit codes only if `TaiwanStockInfo.type` is common equity.
  - Exclude financials via `industry_category == "Finance/Insurance"` (drops the `FIN_KW_PATTERN` name-match brittleness).

### 2.5 Liquidity / tradeability (`data/liquidity.py`)

**Addresses**: review §2.4; Codex Phase 3.

- `apply_liquidity_filter(universe, as_of, cfg)`:
  - 20-day median `Trading_money` ≥ NT$50M (configurable).
  - 60-day median retained as diagnostic.
  - Close ≥ NT$10 (configurable).
  - Exclude `TaiwanStockSuspended` entries active on `as_of`.
  - Exclude `TaiwanStockDispositionSecuritiesPeriod` (Backer tier; document free-tier fallback).
  - Exclude `TaiwanStockMarginShortSaleSuspension` entries active on `as_of` when margin signals are required.
  - Exclude 全額交割股 via `TaiwanStockInfo.type` or equivalent.
  - Exclude leveraged/inverse ETFs via `TaiwanStockPriceLimit.limit_up == 0 or limit_down == 0`.
  - Mark limit-up/limit-down names as `non_executable=True` for entry overlay use (Codex addition).
  - Emit `dropped.csv` row with reason for every exclusion.

### 2.6 PIT prices + corporate actions (`data/prices.py`, `data/corp_actions.py`)

**Addresses**: review §2.3.

- `get_prices(stock_ids, as_of, lookback)`:
  - Primary: `TaiwanStockPrice` unadjusted.
  - In-code adjustment from `TaiwanStockDividendResult`, `TaiwanStockSplitPrice`, `TaiwanStockCapitalReductionReferencePrice`, `TaiwanStockParValueChange`.
  - Apply factors only for events with `ex_date <= as_of`.
  - Keep both `close` (adjusted) and `raw_close` (unadjusted) columns.
- Validation (fails loud): duplicate dates, missing sessions per trading calendar, stale close, zero volume, OHLC inconsistency (`low > high`, `close > high`, etc.), extreme unexplained returns, missing adjustment factor on corp-action dates.
- `--validate-prices` flag cross-checks our adjusted series against `TaiwanStockPriceAdj` and optional yfinance on no-event dates; diverge > 0.5% → canary alert.
- **Removes** yfinance from the primary price path (referenced at [strategy_scanner.py:694-711](../../Desktop/alex/Stocks-main/strategy_scanner.py:694)).

### 2.7 PIT fundamentals (`data/fundamentals.py`)

**Addresses**: review §2.2.

- Every row gets `report_period` + `available_date`.
- `available_date` = FinMind `AnnouncementDate` if provided; otherwise `period_end + embargo` per Phase 1 defaults.
- Filter `available_date <= as_of` before exposing any fundamental row.
- Accessors:
  - `get_per_pbr(stock_ids, as_of)` → `TaiwanStockPER` (daily, inherently PIT).
  - `get_financial_statements(...)` → `TaiwanStockFinancialStatements` + `TaiwanStockBalanceSheet` + `TaiwanStockCashFlowsStatement`.
  - `get_monthly_revenue(...)` → `TaiwanStockMonthRevenue`.
  - `get_dividends(...)` → `TaiwanStockDividend` (uses `AnnouncementDate` explicitly).
- Compute TTM ROE, TTM GPM, TTM operating margin, revenue YoY, revenue acceleration — all PIT filtered.
- Explicit handling for negative / invalid earnings in P/E (Codex addition): emit `NaN` with `warning=negative_eps`, never compute a bogus P/E.

### 2.8 Flows (`data/flows.py`)

**Addresses**: review §2.16 (T86 fragility), Codex expansion.

- `get_institutional_flows(...)` → `TaiwanStockInstitutionalInvestorsBuySell` (replaces T86 scrape).
- `get_foreign_ownership(...)` → `TaiwanStockShareholding` (structural flow signal).
- `get_government_bank_flows(...)` → `TaiwanstockGovernmentBankBuySell` (sponsor tier; gated on config).

### 2.9 Margin / short / lending (`data/margin.py`, `data/short_balance.py`, `data/securities_lending.py`)

**Addresses**: review §2.16 (MI_MARGN column-position parsing).

- `get_margin(...)` → `TaiwanStockMarginPurchaseShortSale` — typed columns: `MarginPurchaseTodayBalance`, `ShortSaleTodayBalance`, `MarginPurchaseLimit`, `ShortSaleLimit`. No position-based column lookup.
- `squeeze_ratio = ShortSaleTodayBalance / MarginPurchaseTodayBalance`.
- `margin_utilization = MarginPurchaseTodayBalance / MarginPurchaseLimit`.
- `get_short_balance(...)` → `TaiwanDailyShortSaleBalances` (Codex addition; I missed this dataset).
- `get_securities_lending(...)` → `TaiwanStockSecuritiesLending` (Codex addition).

### 2.10 Benchmark + risk-free rate (`data/benchmarks.py`)

**Addresses**: review §2.10, §2.9.

- `get_taiex_total_return(as_of, lookback)` → `TaiwanStockTotalReturnIndex` with `data_id="TAIEX"` (includes dividends — replaces 0050 as primary; 0050 retained as secondary).
- Replace hardcoded `RISK_FREE_RATE = 0.015` at [strategy_scanner.py:195](../../Desktop/alex/Stocks-main/strategy_scanner.py:195) with Taiwan central-bank repo rate feed; fallback 0.015 if feed unavailable.
- Empirical `trading_days_per_year` (from calendar) replaces hardcoded 252.

### 2.11 Industry classification (`data/industry.py`)

- `TaiwanStockInfo.industry_category` is authoritative.
- **Drops** the 54-row `BFIAMU_TO_TWSE_CODES` dictionary at [strategy_scanner.py:129-182](../../Desktop/alex/Stocks-main/strategy_scanner.py:129).
- Top-N industry volume computed from aggregated `TaiwanStockPrice.Trading_money` grouped by industry.

### 2.12 Data-contract canaries (`tests/data_contract/`)

- `@pytest.mark.live` — excluded from default CI; run weekly via scheduled workflow.
- Per FinMind dataset: fetch one day, assert expected columns + types.
- Per TWSE fallback: same for T86, MI_MARGN, BFIAMU.
- Column drift → loud CI failure.

**Exit criteria**: `tw-scanner data refresh --as-of 2024-06-30` populates parquet cache; all unit tests in `tests/unit/data/` green; weekly live-canary workflow green.

---

## Phase 3 — Data Cleaning, Missing-Data Policy, Signal Library

**Addresses**: review §2.8 (weak technicals), §2.11 (silent-neutral), recommendation #8 (z-scores), #13 (drop Fibonacci); Codex Phases 6–7.

### 3.1 Cleaning and missing-data rules (`scoring/cleaning.py`, `scoring/missing_policy.py`)

**Addresses**: review §2.11.

- True zero vs missing are distinct; numeric parsers preserve missingness.
- Signals return `NaN` when uncomputable — **no neutral defaults** (kills `K=D=50` at [strategy_scanner.py:813-816](../../Desktop/alex/Stocks-main/strategy_scanner.py:813); `fib50=last_close` at [strategy_scanner.py:823-826](../../Desktop/alex/Stocks-main/strategy_scanner.py:823); `vol_ratio=1.0` at [strategy_scanner.py:831-832](../../Desktop/alex/Stocks-main/strategy_scanner.py:831)).
- Winsorize raw values at 1st/99th percentile before z-scoring.
- Clip final z-scores to [-3, 3].
- Three modes:
  - `strict` (default): required signal missing → row excluded from ranking.
  - `penalized`: missing signal → sector-median z − 0.5 (explicit penalty, logged).
  - `legacy_reweighted`: preserves old apples-to-oranges behavior, never default, warns loudly (comparison only).
- Row-level fields: `signal_coverage_pct`, `data_coverage_score` (Codex addition).

### 3.2 SignalFrame data contract (`signals/base.py`)

Codex's explicit structure, adopted:

```python
@dataclass
class SignalFrame:
    stock_id: str
    signal_name: str
    raw_value: float          # pre-cleaning
    cleaned_value: float      # winsorized
    z_score: float
    sector_neutral_z: float   # if applicable for this signal
    missing_flag: bool
    warning: str | None       # e.g. 'negative_eps', 'stale_data'
    as_of: date
```

Every signal function: `compute(as_of, universe, data_bundle, cfg) -> list[SignalFrame]` or equivalent vectorized `DataFrame` with these columns.

### 3.3 Signal disposition matrix

**Signals REMOVED from production scoring** (still computable behind feature flag for research):

| Old signal | Disposition | Reason |
|---|---|---|
| KD stochastic golden cross ([strategy_scanner.py:809-817](../../Desktop/alex/Stocks-main/strategy_scanner.py:809)) | Drop | Weak IC in literature; research flag only |
| Fibonacci 50% proximity ([strategy_scanner.py:819-828](../../Desktop/alex/Stocks-main/strategy_scanner.py:819)) | Drop | No academic support |
| `minor_lift` 5-45% band ([strategy_scanner.py:829-830](../../Desktop/alex/Stocks-main/strategy_scanner.py:829)) | Drop | Arbitrary |
| "Mid-rank 35-70%" sweet spot ([strategy_scanner.py:1278-1281](../../Desktop/alex/Stocks-main/strategy_scanner.py:1278)) | Drop as production rule; keep as research hypothesis | Unvalidated folk wisdom |
| Squeeze-ratio buckets ([strategy_scanner.py:1315-1326](../../Desktop/alex/Stocks-main/strategy_scanner.py:1315)) | Replace | Double-counting; see v2 signals |
| ROE/GPM tier buckets ([strategy_scanner.py:1340-1343](../../Desktop/alex/Stocks-main/strategy_scanner.py:1340)) | Replace with z-scored PIT versions | Arbitrary thresholds |
| Sharpe/Sortino/Beta scoring ([strategy_scanner.py:1360-1366](../../Desktop/alex/Stocks-main/strategy_scanner.py:1360)) | **Remove from scoring** | Risk metrics are diagnostics, not alpha signals |

**Production v2 signal catalog** (superset of my + Codex's lists):

| Family | Signal | Source |
|---|---|---|
| Foreign flow | `foreign_net_5d_adv_z` | InstitutionalInvestorsBuySell + Prices |
| Foreign flow | `foreign_net_20d_adv_z` | ↑ |
| Foreign flow | `foreign_net_20d_float_z` | ↑ + float-normalized |
| Foreign flow | `foreign_consec_z` | ↑ |
| Foreign flow | `foreign_ownership_delta_z` | Shareholding |
| Foreign flow | `flow_acceleration_z` | ↑ (5d − 20d flow) |
| Momentum | `mom_1m_z` | Prices |
| Momentum | `mom_3m_z` | Prices |
| Momentum | `mom_6m_z` | Prices |
| Momentum | `mom_12_1_z` | Prices (12M skipping last month) |
| Trend | `trend_ma_slope_z` | Prices (60d MA slope) |
| Trend | `adx14_z` | Prices |
| Mean reversion | `rsi14_z` | Prices |
| Mean reversion | `bollinger_z` | Prices |
| Volatility | `realized_vol_60d_z` | Prices (sign flipped) |
| Volatility | `drawdown_120d_z` | Prices (sign flipped) |
| Volatility | `vol_surge_z` | Prices (volume vs 20d MA) |
| Margin pressure | `squeeze_z` | Margin |
| Margin pressure | `margin_health_z` | Margin (utilization) |
| Margin pressure | `short_balance_adv_z` | DailyShortSaleBalances |
| Margin pressure | `securities_lending_adv_z` | SecuritiesLending |
| Quality | `roe_ttm_z` | Financial statements PIT |
| Quality | `gpm_ttm_z` | Financial statements PIT |
| Quality | `operating_margin_ttm_z` | Financial statements PIT |
| Quality | `revenue_yoy_z` | MonthRevenue |
| Quality | `revenue_acceleration_z` | MonthRevenue |
| Value | `pbr_sector_z` | PER + industry |
| Value | `pe_sector_z` | PER + industry (NaN on neg EPS) |
| Liquidity tilt | `turnover_20d_z` | Prices |
| Regime (gate, not weight) | `margin_maintenance_regime` | TotalExchangeMarginMaintenance (Backer) |

**Production-eligible subset** starts smaller: only signals that pass Phase 5 IC gates get weight > 0. Unvalidated signals are computed and stored in `signal_decomposition.csv` as research diagnostics with `weight_production=0`.

### 3.4 Normalization (`scoring/normalize.py`)

- `winsorize(series, 0.01, 0.99)`.
- Cross-sectional z-score within current eligible universe, per as-of date.
- `sector_neutral_z(series, sectors)` — z-score within each industry group. Applied to: quality, value, momentum, mean-reversion, trend signals. Flow signals remain cross-sectional raw z unless Phase 5 validation says otherwise.
- Risk penalties sign-flipped (low vol / low drawdown = high z).

### 3.5 Dimson beta (diagnostic only)

- `signals/risk_metrics.py::dimson_beta(r, b, lags=1)` — single-lag Dimson adjustment for thin trading.
- Surfaced in `candidates.csv` alongside raw beta as descriptive columns.
- **Not** used in scoring (fixes review §2.9 recommendation to remove risk metrics from alpha score).

**Exit criteria**: every signal has a unit test asserting NaN handling, monotonicity on a toy dataset, and sector-neutrality invariants; `tw-scanner signals compute --as-of X` emits `signals_panel.parquet`.

---

## Phase 4 — Scoring v2

**Addresses**: review §2.5 (arbitrary weights), §2.6 (apples-to-oranges), recommendation #8.

### 4.1 Composite score (`scoring/composite.py`)

- `composite_z = Σᵢ wᵢ · zᵢ` where weights come from `config/weights_frozen_YYYYMMDD.yaml` (Phase 5 output).
- `composite_rank` = cross-sectional percentile [0, 100] within the current as-of eligible universe.
- `composite_rank` becomes the primary display metric in `candidates.csv`; old `total_score` (0-100) preserved only in a legacy comparison output labeled deprecated.

### 4.2 Provisional weights (pre-calibration)

Codex's provisional anchoring for the first research run (before Phase 5 produces a frozen file):

- Foreign flow: 30%
- Momentum + trend: 20%
- Quality + fundamentals: 15%
- Margin + short pressure: 15%
- Valuation: 10%
- Liquidity + risk penalties: 10%

These are **research-mode only** and cannot be used for production. Production requires a signed frozen-weights file from Phase 5.

### 4.3 Weight constraints (enforced in calibration)

- All `w_i >= 0` unless explicitly approved via config override.
- `sum(w_i) = 1`.
- No single signal > 25% of total.
- No family > 35% of total.
- Signals with non-positive OOS IC must have `w_i = 0`.

### 4.4 Missing-data policy at scoring layer

- `strict` (default): require all signals with `w_i > 0` present → else row dropped.
- `penalized`: missing signal z replaced with `sector_median_z − 0.5`; logged.
- `legacy_reweighted`: only for comparison CSV; never production.

### 4.5 Outputs per candidate

- `stock_id`, `stock_name`, `industry_category`, `market_cap_bucket`.
- `composite_rank`, `composite_z`.
- Per-signal: `raw_value`, `cleaned_value`, `z_score`, `sector_neutral_z`, `contribution = w_i * z_i`, `missing_flag`, `warning`.
- `signal_coverage_pct`, `data_coverage_score`, `model_confidence`.
- Tilt columns: `dimson_beta_1y`, `raw_beta_1y`, `realized_vol_60d`, `turnover_20d_median`, `max_drawdown_120d`.

**Exit criteria**: `tw-scanner score --as-of X` produces a `candidates.csv` whose `composite_rank` is strictly comparable across rows; strict missing-data mode is default; unit tests assert `sum(contributions) ≈ composite_z` invariant.

---

## Phase 5 — Research, IC, Backtest, Calibration

**Addresses**: review §2 "no backtest" critical; Codex Phases 9–10. This is the single biggest gap in the current code.

### 5.1 IC framework (`research/ic.py`)

- `rank_ic(signal, forward_returns)` — spearman per date, time-averaged with t-stat.
- `pearson_ic(...)` — same for pearson.
- `ic_decay(signal, returns_panel, horizons=[1,5,10,20,60])`.
- `ic_rolling(signal, window=63)` — detects regime dependence.
- `ic_by_sector(signal, returns, sectors)` + by market-cap bucket + by liquidity bucket + by regime (vol regime, margin-maintenance percentile).

### 5.2 Backtest harness (`research/backtest.py`, `research/execution_model.py`)

Execution model (Codex's explicit version, adopted):

- Signal date: close of `as_of`.
- Execution date: next valid trading day (per TW calendar).
- Execution price: **next open** by default.
- Optional research-only close-to-close mode, clearly labeled non-executable.
- Limit-up / limit-down at execution → non-fill (skip or next-day retry per config).
- Suspended / disposition / margin-suspended at execution → skip.
- Transaction costs from `config/costs.yaml` applied per trade.

Reports:

- Top-decile (D10) minus bottom-decile (D1) forward-return spreads at 5/10/20/60 days.
- Long-only top-N cumulative return vs TAIEX-TR.
- Annualized Sharpe of top-decile.
- **Deflated Sharpe** (Bailey & López de Prado, 2014) to penalize multi-testing — named explicitly.
- Max drawdown.
- Hit-rate vs TAIEX-TR by horizon.
- Jaccard similarity of top-N across consecutive rebalances (addresses review §2.13 — turnover).
- IC decay curve.
- Sector, size, beta, liquidity attribution.
- Per-regime performance.

Leakage controls:

- Purged + embargoed walk-forward; embargo = max signal lookback + 20 trading days.
- Purged K-fold within IS.
- Bootstrap confidence intervals on rank-IC.
- Fixed IS/OOS split per Phase 1 config.

### 5.3 Challenger models (`research/challengers.py`)

Five challengers (Codex's expanded set, adopted):

1. Equal-weighted composite.
2. Random-weighted (Monte Carlo ensemble).
3. Foreign-flow-only composite.
4. Momentum-only composite.
5. Legacy heuristic model (current `strategy_scanner.py` score as challenger).

Production composite must beat 1, 2, and 5 on OOS Sharpe and rank-IC. Losing to 3 or 4 is informative but not disqualifying — it indicates a single-factor model would serve the user better and should be reported transparently in the model card.

### 5.4 Calibration (`research/calibrate.py`)

- Method: constrained ridge regression of forward returns on z-scored signals, IS only.
- Alternate: IC-weighted objective (maximize weighted-sum IC subject to constraints).
- Constraints per Phase 4.3.
- Unvalidated signals forced to `w_i = 0` (Codex rule, adopted).

Output: `config/weights_frozen_YYYYMMDD.yaml` with:
- Model version, training window, OOS window.
- Signal list + weights.
- IS and OOS rank-IC + t-stat + decile spreads.
- Deflated Sharpe (OOS).
- Challenger comparison table.
- Config hash at calibration time.
- Signed (git author + date).

### 5.5 Regime analysis (`research/regime.py`)

- Tag each `as_of` with: TAIEX trailing 60d Sharpe sign; `TaiwanTotalExchangeMarginMaintenance` percentile.
- IC by regime; if a signal works only in one regime, gate it at inference time (`margin_maintenance_regime` signal = `NaN` in off-regime).

### 5.6 Validation gates (before any `weights_frozen_*` ships)

- Composite OOS rank-IC > 0 (hard floor).
- Target OOS rank-IC ≥ 0.02.
- IC sign stable across subperiods.
- Decile forward returns roughly monotonic.
- Top-decile beats TAIEX-TR *after* costs.
- Beats equal-weight and random-weight challengers on OOS.
- Deflated Sharpe positive.
- No single sector or year explains > 50% of performance.

Failure in any gate → weights file not committed; model card updated with the failure reason.

**Exit criteria Phase 5**: at least one `config/weights_frozen_YYYYMMDD.yaml` exists; `MODEL_CARD.md` is filled in with IS/OOS metrics; all six validation gates green.

---

## Phase 6 — Entry Timing Overlay

**Addresses**: review §2.12 (mechanical trap-bait entry signal); Codex Phase 11.

Explicitly **separate module**, does not feed `composite_rank`.

### 6.1 Entry state machine

Per candidate, per as-of:

- `entry_state ∈ {none, touched, confirmed, invalidated}`
- **Touched**: `Low ≤ MA20 + ATR(14)` and `Close ≥ MA20 − ATR(14)` on `as_of`; candidate already in top-N by `composite_rank`.
- **Confirmed** (requires T+1 observation): next trading-day close `>` MA20 AND next-day close `>` touch-day close AND volume ≥ 0.7× 20d MA AND not limit-up/down blocked AND not suspended/disposition at T+1.
- **Invalidated**: T+1 close < touch-day close AND close < MA20 − 0.5·ATR(14).

Removed from production entry: Fibonacci 50 near check, KD golden cross, `strong_signal` old OR-logic at [strategy_scanner.py:1229](../../Desktop/alex/Stocks-main/strategy_scanner.py:1229).

### 6.2 Backtest entry overlay separately

- Forward returns for `touched` vs `confirmed` vs `invalidated`.
- False-positive rate (touched that decayed).
- Non-fill rate (limit-up blocks, suspensions).
- Effect on turnover vs pure score-based rebalance.

If overlay fails OOS validation, output is labeled "research diagnostic" in `entry_signals.csv` and not promoted as a buy signal.

**Exit criteria**: unit tests for each state transition (synthetic touch→confirm, touch→invalidate, touch→suspension); backtest for overlay independently reported.

---

## Phase 7 — Diagnostics & Warnings

**Addresses**: review recommendation #14 (turnover), #15 (tilt disclosure); Codex Phase 12.

### 7.1 Per-row diagnostics (added to `candidates.csv`)

- Sector, industry, market cap, market-cap bucket.
- Liquidity tier (20-day median trading value bucket).
- 20-day + 60-day median trading value.
- Realized vol 60d.
- Raw beta, Dimson beta.
- Max drawdown 120d.
- Short/margin pressure composite.
- Foreign ownership delta.
- Top-3 contributing signals.

### 7.2 Top-N aggregate diagnostics (added to `run_manifest.json`)

- Sector HHI, sector concentration %.
- Beta quantiles (10/25/50/75/90).
- Volatility distribution.
- Liquidity-tier distribution.
- Market-cap distribution.
- Average + median trading value.
- Top 10 signal contributors.
- Top 10 risk warnings.
- Jaccard similarity vs previous run.

### 7.3 Warning defaults (Codex's concrete thresholds, adopted)

Fire a manifest-level warning when any of:

- Any single sector > 35% of top 50.
- Average beta of top 50 > 1.4.
- Median 20-day trading value of top 50 < NT$100M.
- More than 20% of top 50 in weakest liquidity tier.
- Top-200 Jaccard similarity < 0.6 vs prior run.
- Signal coverage < 90% for any core signal.
- IC drift > 1σ vs calibration baseline (my addition — Codex had IC-drift but no σ threshold).

### 7.4 IC drift monitor (`diagnostics/ic_drift.py`)

- Rolling 20-day rank-IC of `composite_z` vs forward 20-day returns.
- Baseline = OOS IC from the calibration run.
- Warn if rolling IC drops below `baseline - 1σ`.

### 7.5 HTML dashboard (optional; `diagnostics/html_dashboard.py`)

- Plotly static HTML: top-N stability over 60d, rolling rank-IC, signal coverage trends, sector HHI trend, beta/vol histograms.

**Exit criteria**: `tw-scanner diagnose turnover --from X --to Y` emits `turnover_diagnostics.csv`; warnings fire on synthetic inputs engineered to exceed thresholds.

---

## Phase 8 — Daily Pipeline & Reports

**Addresses**: Codex Phase 13.

### 8.1 Flow (`pipeline/screen.py`) — replaces `main()` at [strategy_scanner.py:1480](../../Desktop/alex/Stocks-main/strategy_scanner.py:1480)

```
1. Load config + secrets; compute config_hash.
2. Build trading calendar from FinMind.
3. Build PIT universe (snapshot + delistings).
4. Apply liquidity/tradeability gates; emit dropped.csv.
5. Load data bundle (prices, fundamentals, flows, margin, etc.) — all PIT.
6. Validate data; emit data_quality.csv.
7. Compute signal panel.
8. Clean + normalize + sector-neutralize.
9. Score with frozen weights; compute composite_rank.
10. Compute diagnostics (per-row + aggregate + warnings).
11. Compute entry overlay (state + confirmation).
12. Emit candidates.csv, score_decomposition.csv, entry_signals.csv, data_quality.csv, dropped.csv, diagnostics.html, score_chart.png.
13. Emit run_manifest.json.
```

### 8.2 Manifest schema (`governance/manifest.py`)

```json
{
  "as_of": "YYYY-MM-DD",
  "run_id": "uuid",
  "package_version": "tw_scanner.__version__",
  "model_version": "from weights_frozen",
  "git_sha": "git rev-parse HEAD",
  "config_hash": "sha256(resolved yaml)",
  "weights_file": "config/weights_frozen_YYYYMMDD.yaml",
  "weights_hash": "sha256(weights file)",
  "python_version": "...",
  "dependency_versions": { "pandas": "x.y", "numpy": "x.y", ... },
  "data_sources": [
    { "source": "finmind", "dataset": "TaiwanStockPrice", "rows": N, "retrieved_at": "ts", "cache_hit": bool, "freshness_hours": N }
  ],
  "universe_counts": { "listed": N, "eligible_post_liquidity": N, "scored": N, "top_n": N },
  "signal_coverage_pct": { "foreign_net_20d_adv_z": 0.98, ... },
  "diagnostics": {
    "jaccard_vs_prior": 0.83,
    "sector_hhi": 0.14,
    "beta_quantiles": [0.6, 0.8, 1.0, 1.2, 1.5],
    "ic_drift_sigma": 0.3
  },
  "warnings": [],
  "output_hashes": { "candidates.csv": "sha256", ... }
}
```

### 8.3 Output file inventory

Per the "Target End State" section above; filenames preserved for user continuity where possible.

**Exit criteria**: full `tw-scanner screen --as-of TODAY` produces all listed outputs; manifest validates against JSON schema.

---

## Phase 9 — Testing & CI

**Addresses**: review recommendation #12, §2.15; Codex Phase 14.

### 9.1 Unit tests (target: ≥ 80% coverage on data/signals/scoring)

- Config loading + validation.
- Secret loading (env var absent/present).
- FinMind client: happy path, 402 rate limit, schema drift.
- Quota tracking.
- Universe inclusion/exclusion, delisting merge.
- Liquidity filter (every exclusion rule).
- Trading calendar.
- Corporate-action adjustment (each event type).
- PIT fundamentals embargo (per report type).
- Flow / margin / short / lending calculations.
- Signal NaN behavior (missing → NaN, not neutral).
- Winsorization.
- Z-scoring + sector-neutralization.
- Composite scoring (`sum(contribs) ≈ composite_z`).
- Missing-data modes.
- Entry-state transitions (touched → confirmed / invalidated / timeout).
- Manifest fields + schema validation.

### 9.2 Integration tests

- `test_pipeline_smoke.py` — 10 synthetic tickers, 1 year, full pipeline end-to-end.
- `test_pipeline_regression.py` — frozen `as_of=2023-06-30` run; compare against committed expected `candidates.csv` hash. Any change forces an explicit expected-file update (prevents silent behavioral drift).
- `test_backtest_smoke.py` — small backtest config completes and reports plausible metrics.
- `test_calibration_smoke.py` — calibration run produces a weights file satisfying constraints.

### 9.3 Property tests (`tests/property/`, `hypothesis`)

- `composite_rank` monotone in `composite_z`.
- Winsorizer never expands range.
- Sector-neutral z has mean ≈ 0 per sector.
- Missing-data strict mode never scores incomplete rows.
- `sum(contributions) == composite_z` to floating-point tolerance.
- Higher `z_score` with positive weight monotonically increases `composite_z`.

### 9.4 Data-contract canaries (`tests/data_contract/`, `@pytest.mark.live`)

Per Phase 2.12.

### 9.5 CI

- `.github/workflows/ci.yml`:
  - Python 3.12.
  - `ruff check` + `ruff format --check`.
  - `mypy tw_scanner`.
  - `pytest -m "not live"`.
  - Coverage report, artifact upload.
  - `gitleaks` secret scan.
- `.github/workflows/live-canary.yml`: weekly, `pytest -m live`.
- Branch protection on `main`: CI + code-owner review required.

### 9.6 Pre-commit (`.pre-commit-config.yaml`)

- `ruff`, `ruff-format`, `mypy`, `gitleaks`.
- Trailing whitespace, end-of-file, YAML/JSON validity.

**Exit criteria**: CI green on a PR; coverage ≥ 80% on `data/`, `signals/`, `scoring/`; pre-commit enforced.

---

## Phase 10 — Documentation & Governance

**Addresses**: review §2.15 (no governance); Codex Phase 15.

### 10.1 Docs

Every file in `docs/` per the "Target Architecture" tree. Key highlights:

- `README.md` — replaces the current placeholder; quickstart, install, CLI, output interpretation, limitations, link to model card.
- `MODEL_CARD.md` — purpose, universe, signals, weights, calibration method, IS/OOS metrics, known limitations, last validation date, next recalibration schedule.
- `DATA_SOURCES.md` — dataset map with FinMind tiers, update times, PIT rules.
- `SIGNALS_REFERENCE.md` — per signal: formula, lookback, expected IC sign, validation status, current weight.
- `BACKTEST_METHODOLOGY.md` — assumptions, leakage controls, costs, metrics.
- `DATA_LINEAGE.md` — every column in `candidates.csv` traced to source + transformation.
- `GOVERNANCE.md` — model-change rules.
- `VALIDATION_LOG.md` — dated IS/OOS metrics for every calibration.
- `INTERPRETING_OUTPUT.md` — how the discretionary retail user should read `candidates.csv`.
- `CHANGELOG.md` — every behavioral change.
- `adr/` — Architecture Decision Records (dropping KD, requiring next-day confirmation, switching benchmark, etc.).

### 10.2 Governance rules (enforced as process, not code)

- No signal enters production scoring without IC evidence and OOS validation.
- No weight change ships without OOS validation + challenger comparison + model-card update.
- No schema change ships without data-contract tests.
- No production output ships without manifest.
- No hardcoded secrets.
- Legacy model retained only as challenger.
- Model card updated every production model version.
- Every commit touching `scoring/` or `config/weights_*.yaml` requires a linked ADR.

**Exit criteria**: all docs exist and are linked from README; governance rules documented and referenced in CONTRIBUTING.md.

---

## Phase 11 — Legacy Retirement

**Addresses**: transition discipline.

### 11.1 Retention policy

- Keep `strategy_scanner.py` runnable via `tw-scanner --legacy screen` throughout Phases 0–10.
- Compare new vs legacy outputs on shared as-of dates; document differences in `docs/LEGACY_COMPARISON.md`.

### 11.2 Retirement criteria

Retire legacy only when ALL of:

- New pipeline passes acceptance criteria (see Verification section).
- ≥ 4 weeks of shadow-running both pipelines side-by-side with documented diff.
- New outputs cover all columns of legacy `candidates.csv` (rename old `total_score` → `legacy_score_deprecated` in a comparison output only).
- Model card signed off.

### 11.3 File moves

- `strategy_scanner.py` → `legacy/strategy_scanner.py`.
- `doesthishelp.md` → `docs/references/finmind_catalog.md` (rename for discoverability).
- Update `CLAUDE.md` and `AGENTS.md` to point at the package + remove the single-file + monkeypatch sections.

**Exit criteria**: legacy path archived; new CLI is the only documented path; CHANGELOG has a retirement entry; README updated.

---

## Cross-Cutting Items

### Code conventions (preserve from current codebase)

- Traditional Chinese docstrings + inline comments — preserved per [AGENTS.md:126-128](../../Desktop/alex/Stocks-main/AGENTS.md:126).
- Box-drawing `═` section headers — preserved.
- Python 3.12+ union syntax; no `from __future__ import annotations`.
- snake_case functions, UPPER_CASE constants.
- Safe fallbacks on failure preserved; scoring layer now treats NaN as exclude, not neutral.

### Performance & caching

- Parquet + pyarrow replaces pickle throughout.
- Bulk endpoints (Backer+) reduce full-run time from 10–30 min to < 2 min and make multi-year backtests tractable.
- Cache TTL: `as_of < today` forever; `as_of == today` 1-day.
- Benchmark-mode CLI: `--mode bulk|per-ticker` respects user's FinMind tier.

### Observability

- Every CLI command emits a JSON log line per stage with `{stage, rows_in, rows_out, duration_ms, warnings}`.
- `--log-format json` for downstream piping.

### Security (ongoing)

- Pre-commit `gitleaks` + CI `gitleaks`.
- No tokens written to cache or outputs.
- `.env` listed in README as the only place to set secrets.

### TPEx support

- Config and schema are TPEx-ready (every `market` field supports `twse` / `tpex`) but TPEx is **not activated** in v2 (out of scope; Codex non-goal).

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| FinMind free-tier 600 req/hr quota exceeded during backtest | High | High | Parquet cache + bulk endpoints on Backer + quota watchdog |
| Fundamentals `date` is period-end, not announce-date | Medium | High | Conservative embargo (15/60/90-day); validation against known earnings calendars |
| OOS rank-IC < 0.02 | Medium | High | Expand signal universe; ship as discovery-only; refuse to promote to production scoring |
| Too many signals → overfitting | High | High | Family caps + per-signal cap + deflated Sharpe + challenger gate |
| TWSE fallback schema drifts | Medium | Medium | Weekly live canary; tagged manifest warnings |
| Legacy users depend on old column meanings | Medium | Medium | Preserve column names; add new ones; CHANGELOG documents every change |
| PIT price-adjustment bugs | Medium | High | Corp-action unit tests + cross-check vs `TaiwanStockPriceAdj` + optional yfinance on no-event dates |
| Paid-tier data unavailable | Medium | Medium | Free-tier per-ticker adapters preserved; sponsor-tier signals gated |
| Secret leak recurs | Low | High | Pre-commit + CI `gitleaks`; periodic manual audit |
| Rewrite timeline longer than expected | High | Medium | Phase exits keep repo runnable; legacy retained through Phase 10 |
| Cold-start backtest has insufficient universe coverage | Medium | Medium | Monthly universe-snapshot backfill going 5 years back (Phase 2.4) |

---

## Explicit Non-Goals (retail simplifications)

- No broker integration, no automatic order placement.
- No final position sizing, no portfolio optimizer.
- No tax-lot accounting, no after-tax return modeling.
- No market-impact model.
- No OMS / PMS / order router.
- No guarantee of alpha before OOS validation; until validated, the tool is a **discovery** filter, not a trusted ranker.
- No entry flag treated as a "buy" instruction without independent validation.
- TPEx not activated in v2 (schema-ready).

---

## Assumptions

- Python 3.12+ remains mandatory.
- FinMind is the primary data source; free-tier support is required; Backer/Sponsor designed in, optional.
- TWSE remains only a fallback, not a primary source.
- TWSE universe is the v2 production scope.
- User remains responsible for final judgment, sizing, and portfolio-level risk.
- User has access to either a Backer/Sponsor FinMind tier for efficient backtest, or is willing to wait on per-ticker free-tier pulls for calibration.

---

## Verification Strategy

### Per-phase exit checks

- **Phase 0**: fresh-venv install; CLI help; no secrets detected.
- **Phase 1**: config loads; config hash emitted; pydantic rejects malformed YAML.
- **Phase 2**: data refresh populates parquet; unit tests on every accessor green; weekly live canary green.
- **Phase 3**: signal panel emits expected SignalFrames with correct NaN and warning fields.
- **Phase 4**: scored output is cross-row-comparable; property tests green.
- **Phase 5**: IC + backtest reports generated; at least one frozen weights file satisfies all six validation gates.
- **Phase 6**: entry-state transitions pass synthetic tests; overlay backtested independently.
- **Phase 7**: warnings fire on engineered inputs; IC drift detected.
- **Phase 8**: full `screen` run emits every listed output; manifest validates.
- **Phase 9**: CI green; coverage targets met.
- **Phase 10**: all docs exist; model card current; governance rules enforced.
- **Phase 11**: legacy archived; README is authoritative.

### End-to-end institutional acceptance — the system must answer all ten:

1. What exact universe was ranked, and was it survivorship-correct as of the run date?
2. What data was known as of that date, and what was excluded as unavailable?
3. What is the OOS rank-IC of the composite and of each production signal?
4. What are the top-ranked stock's raw values, z-scores, and weighted contributions per signal?
5. What are today's top-N sector, beta, size, volatility, and liquidity exposures?
6. How stable is today's top-N vs the previous run? (Jaccard)
7. What transaction-cost assumptions were used in validation?
8. Which data sources were stale, missing, or degraded?
9. Which model version, config hash, and weights file produced the output?
10. Is the score validated for stock selection, or only for discovery?

All ten must be answerable from `candidates.csv` + `score_decomposition.csv` + `run_manifest.json` + `data_quality.csv` + `docs/MODEL_CARD.md` + `docs/VALIDATION_LOG.md`, without manual code inspection.

### Final acceptance criteria

The rebuild is complete when:

- No secrets exist in source, git history, or committed configs.
- The package installs cleanly in a fresh venv.
- `tw-scanner screen --as-of YYYY-MM-DD` runs and produces all listed artifacts.
- Universe is PIT and survivorship-aware.
- Liquidity / tradeability filters execute before scoring.
- Prices are PIT-adjusted with no future corp actions leaking.
- Fundamentals are filtered by announce date or conservative embargo.
- Missing data cannot inflate scores (strict mode default; reweighting never on by default).
- Production scoring uses frozen, calibrated weights.
- `composite_rank` is strictly cross-row comparable.
- Backtest reports rank-IC, decile spread, hit rate, turnover, drawdown, post-cost returns, deflated Sharpe.
- OOS validation beats equal-weight, random-weight, and legacy challengers.
- Outputs include decomposition, risk diagnostics, and data-quality reports.
- Tests cover data, PIT logic, signals, scoring, backtest alignment, and entry overlay.
- CI + secret scanning are active.
- Documentation includes model card, data lineage, backtest methodology, governance.
- Legacy `strategy_scanner.py` is retired only after the new system demonstrably performs better on shadow-running.

---

## File-Level Impact Summary

### Files created
- `pyproject.toml`, `requirements.lock.txt`, `.env.example`, `.pre-commit-config.yaml`, `.github/workflows/{ci,live-canary}.yml`, `CONTRIBUTING.md`
- Entire `tw_scanner/` package per target architecture
- Entire `tests/` tree
- Entire `docs/` tree (plus `docs/adr/`)
- `config/{default,universe,data,signals,scoring,backtest,costs}.yaml` + `config/weights_frozen_YYYYMMDD.yaml`

### Files modified
- `.gitignore` — expand coverage per 0.1
- `README.md` — replace placeholder
- `CLAUDE.md`, `AGENTS.md` — rewrite for package layout; remove monkeypatch + single-file + BFIAMU mapping sections

### Files moved / retired (Phase 11 only)
- `strategy_scanner.py` → `legacy/strategy_scanner.py`
- `doesthishelp.md` → `docs/references/finmind_catalog.md`

### Files referenced but not changed
- `.cache/` — migrates pickle → parquet; old cache invalidated by version bump
- `strategy_output/` — existing filenames preserved; new files added

---

## Prioritized Execution Order

All items above are in scope (user requested "literally everything"). If execution must be phased:

- **P0 — Safety & foundations**: Phase 0 (security + packaging + bootstrap) + Phase 1 (config).
- **P1 — PIT data layer**: Phase 2 (universe + delisting + liquidity + PIT prices + PIT fundamentals + flows + margin + benchmark).
- **P2 — Signals + cleaning**: Phase 3.
- **P3 — Research harness (the biggest gap)**: Phase 5 (IC + backtest + calibration + challengers).
- **P4 — Scoring v2**: Phase 4 (depends on Phase 5 frozen weights for production).
- **P5 — Entry overlay**: Phase 6.
- **P6 — Diagnostics**: Phase 7.
- **P7 — Pipeline + reports**: Phase 8.
- **P8 — Tests + CI**: Phase 9 (runs continuously from Phase 0 onward, but acceptance gate here).
- **P9 — Docs + governance**: Phase 10.
- **P10 — Legacy retirement**: Phase 11.

Note: Phase 4 (scoring) and Phase 5 (research) are mutually entangled — provisional weights (Phase 4.2) enable initial research; frozen weights (Phase 5.4) enable production scoring. Execute in tandem.