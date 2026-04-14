# CLAUDE.md — AI Assistant Guide for `Stocks`

## Project Overview

**台股波段選股系統 v1.0** (Taiwan Swing Trading Stock Scanner) — a single-file Python pipeline that combines foreign investor flow data, technical indicators, margin/short data, fundamentals, and risk metrics to rank Taiwan-listed stocks and surface swing-trade candidates.

- **Language**: Python 3.12+
- **Entry point**: `strategy_scanner.py` (844 lines)
- **Run command**: `python strategy_scanner.py`

---

## Repository Structure

```
Stocks/
├── strategy_scanner.py     # Entire application — one file, 10-step pipeline
├── README.md               # Placeholder only ("# Stocks")
├── CLAUDE.md               # This file
└── strategy_output/        # Auto-created at runtime
    ├── candidates.csv      # Top N scored stocks with all indicators
    ├── entry_signals.csv   # Subset with active buy signals
    └── score_chart.png     # 2-panel score distribution + scatter chart
```

---

## Dependencies

No `requirements.txt` exists. Install manually:

```bash
pip install lxml openpyxl requests pandas numpy yfinance pandas_ta scipy tqdm matplotlib
pip install finmind   # optional — required only for fundamentals (P/E, ROE, GPM)
```

Requires Python 3.12+ for `dict | None` and `list[str]` type hints without `from __future__ import annotations`.

---

## Configuration (lines 52–78)

All user-tunable parameters live in the `CONFIG` block at the top of `strategy_scanner.py`. **Never change these via env vars** — the file must be edited directly.

| Constant | Default | Purpose |
|---|---|---|
| `FINMIND_TOKEN` | `""` | FinMind API token; empty string skips fundamentals step |
| `END_DATE` | `datetime.today()` | Analysis end date |
| `START_DATE` | `END_DATE - 730 days` | Historical lookback window |
| `LOOKUP_DAYS` | `30` | Days of foreign investor data to aggregate |
| `RANK_LOW` / `RANK_HIGH` | `0.25` / `0.80` | Percentile band for foreign ranking filter |
| `MIN_CONSEC_BUY` | `5` | Minimum consecutive foreign net-buy days |
| `MA_PERIOD` | `20` | Short moving average period |
| `TOP_INDUSTRY_COUNT` | `5` | Number of top-volume industries to tag |
| `TARGET` | `200` | Max rows in output `candidates.csv` |
| `MAX_WORKERS` | `15` | Thread pool size for parallel downloads |
| `FIN_KW` | (list) | Keywords for filtering out financial-sector stocks |

---

## Execution Pipeline

`main()` (line 671) runs 10 sequential steps with `tqdm` progress bars:

| Step | Function | Data Source | Description |
|------|----------|-------------|-------------|
| 1 | `get_stock_list()` | TWSE openapi | Fetch ~1,600 listed stocks; exclude financials via `FIN_KW` |
| 2 | `get_foreign_ranking()` | TWSE T86 | Scrape 30+ days of foreign buy/sell, rank by cumulative net buying + consecutive buy days (legacy TWT53U endpoint is dead) |
| 3 | `download_prices()` | Yahoo Finance (yfinance) | Parallel OHLCV download for filtered candidates |
| 4 | `analyze_tech()` | Local price data | MA20/60/120, KD stochastic, Fibonacci levels, volume ratio → `tech_score` (0–25) |
| 5 | `get_top_industries()` | TWSE MI_INDEX20 | Identify top 5 sectors by trading volume |
| 6 | `get_margin()` | TWSE MI_MARGN | Margin-buy / short balances; compute `squeeze_ratio` |
| 7 | `get_fundamentals()` | FinMind (optional) | P/E, P/B, ROE, gross profit margin |
| 8 | `calc_risk()` | yfinance (local price + 0050 benchmark) | Beta, Sharpe ratio, Sortino ratio |
| 9 | `detect_entry()` | Local price + foreign data | Buy-signal detection: MA20 touch + foreign support + volume surge |
| 10 | `score_stock()` + `save_output()` | Aggregated DataFrame | Composite score (0–100), CSV export, matplotlib chart |

---

## Scoring Model (`score_stock`, lines 579–624)

Maximum score: **100 points**

| Category | Max | Key Signals |
|---|---|---|
| Foreign investor flow (外資) | 30 | Consecutive buy days (×2, cap 16) + percentile rank sweet spot (35–70%) |
| Technical (技術面) | 25 | From `analyze_tech()` sub-scores |
| Margin/Short (融資融券) | 15 | `squeeze_ratio` thresholds + healthy margin structure |
| Fundamentals (基本面) | 15 | ROE tiers + Gross Profit Margin tiers (requires FinMind) |
| Risk metrics (風險指標) | 15 | Sharpe, Sortino, Beta tiers |

---

## Rate Limiting

`requests.get` is monkey-patched globally at import time with a **domain-aware** throttle:

- **TWSE hosts** (`twse.com.tw`, `openapi.twse.com.tw`): serialized through a shared lock with a random `0.7–1.5 s` delay — TWSE aggressively blocks rapid crawlers.
- **All other hosts** (Yahoo Finance, FinMind, etc.): light `0.05–0.15 s` jitter per thread, no shared lock. This preserves the `MAX_WORKERS` parallelism in Step 3.

All requests also flow through a shared `requests.Session` backed by an `HTTPAdapter` (pool size 32) for connection reuse. Do not remove this — TWSE blocks rapid crawlers.

```python
requests.get = _smart_delayed_get   # applied at module level
```

---

## Data Sources & API Endpoints

| Source | URL Pattern | Auth |
|---|---|---|
| TWSE stock list | `openapi.twse.com.tw/v1/opendata/t187ap03_L` | None |
| TWSE foreign investor daily | `www.twse.com.tw/fund/T86?response=json&date=YYYYMMDD&selectType=ALLBUT0999` | None |
| TWSE industry volume | `www.twse.com.tw/exchangeReport/MI_INDEX20` | None |
| TWSE margin balances | `www.twse.com.tw/exchangeReport/MI_MARGN` | None |
| Yahoo Finance | via `yfinance` library | None |
| FinMind | via `finmind.data.DataLoader` | Token in `FINMIND_TOKEN` |

Benchmark ticker: `0050.TW` (Taiwan 50 ETF) — used for Beta, Sharpe, Sortino calculations.

---

## Code Conventions

- **Naming**: `snake_case` for functions and variables; `UPPER_CASE` for module-level config constants.
- **Type hints**: Python 3.10+ union syntax (`dict | None`, `list[str]`). Do not add `from __future__ import annotations` or downgrade the style.
- **Docstrings**: Single-line, in Traditional Chinese (繁體中文). Keep this convention when adding functions.
- **Comments**: Inline comments in Traditional Chinese. Section headers use `═` box-drawing separators.
- **Error handling**: Functions return safe fallbacks (`return {}`, `return None`, `return pd.DataFrame()`) on failure. Avoid raising exceptions that would abort the pipeline.
- **No database**: All data lives in Pandas DataFrames in memory for one run. There is no persistence layer to update.

---

## Output Files

All outputs go to `strategy_output/` (auto-created):

| File | Encoding | Description |
|---|---|---|
| `candidates.csv` | UTF-8 BOM (`utf-8-sig`) | Top `TARGET` stocks sorted by `total_score` desc |
| `entry_signals.csv` | UTF-8 BOM | Subset of candidates with active buy signals |
| `score_chart.png` | PNG, 140 dpi | Left: score histogram; Right: tech score vs foreign buy days, colored by total score |

The `utf-8-sig` encoding is intentional — it ensures the CSVs open correctly in Microsoft Excel on Windows/macOS.

---

## Testing

There is no test suite. The project has no `pytest`, `unittest`, or test files. When making changes:

1. Run the script end-to-end: `python strategy_scanner.py`
2. Verify `strategy_output/` contains non-empty `candidates.csv` and `entry_signals.csv`
3. Inspect column counts and `total_score` distribution in the chart

Do not add a test framework without being asked.

---

## Common Tasks

### Change the analysis window
Edit `LOOKUP_DAYS` (foreign data) and `START_DATE` (price history) in the CONFIG block.

### Add a new scoring component
1. Add data fetching function following the existing pattern (retry logic via `safe_get`, fallback `return {}`)
2. Call it in `main()` at the appropriate step
3. Add point logic to `score_stock()` — document the max points in the scoring comment block (lines 571–577)
4. Update the `MAX_SCORE` comment if total exceeds 100

### Adjust candidate count
Change `TARGET` in CONFIG.

### Enable fundamentals
Register at finmindtrade.com, get a free token, paste it into `FINMIND_TOKEN`.

### Add a new output column
Add the field to the `result` DataFrame built in `main()` before calling `save_output()`. Column order matters for readability in the CSV.

---

## Known Limitations

- **No `.env` / environment variable support** — config requires direct file edits.
- **No tests** — correctness is verified by manual inspection of output files.
- **Single-file architecture** — all 844 lines are in one file; refactor into modules only if explicitly requested.
- **Rate limiting is global** — the monkey-patch slows down all HTTP in the process, including yfinance. Estimated full run time: 10–30 minutes depending on network conditions.
- **TWSE API fragility** — TWSE occasionally changes response formats; the JSON parsing code may need updates if fields change.
- **FinMind optional** — if `FINMIND_TOKEN` is empty or `finmind` is not installed, `get_fundamentals()` returns `None` for all tickers and the 15-point fundamental category is skipped for all stocks.
