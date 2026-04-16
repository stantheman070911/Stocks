# Stocks — Taiwan Swing Trading Stock Scanner

A Taiwan equity scanner currently undergoing a staged rebuild from a single-file heuristic screener (v1) into a modular, point-in-time-safe, statistically validated research pipeline (v2).

**Status (2026-04-16)**: Phase 0 (security + packaging) and Phase 1 (pydantic config system) of the rebuild are complete. The new `tw-scanner` CLI boots, loads a validated config, and emits a reproducible config hash. The legacy `strategy_scanner.py` still runs end-to-end and remains the shippable scoring path until the full rebuild passes acceptance (Phase 11).

---

## Two Codebases, One Repo

| Path | What it does | When to use it |
|---|---|---|
| [`strategy_scanner.py`](strategy_scanner.py) | v1 single-file pipeline. Fetches TWSE + yfinance + FinMind, scores ~1,600 listed stocks on a 100-point composite, exports `candidates.csv` + `entry_signals.csv` + `score_chart.png`. | Today — if you want a ranked shortlist now. |
| [`tw_scanner/`](tw_scanner/) | v2 package. Target state: PIT-safe data layer → calibrated composite → backtest-validated → governance-wrapped outputs. | Under construction; use for research / development. |

The CLI lives in the new package:

```bash
tw-scanner --help
tw-scanner --seed 7 screen --dry-run
```

The legacy scanner still runs as-is:

```bash
python strategy_scanner.py
```

---

## Project Roadmap & References

Two files at the repo root drive everything else. Read them before making architectural changes.

### [`theplan.md`](theplan.md) — the rebuild roadmap

The authoritative **institutional-grade rebuild plan** (1,100+ lines). It reconciles a Claude-authored review of the legacy scanner with a Codex-authored rewrite proposal into a single 11-phase program:

0. Security, packaging, bootstrap
1. Pydantic configuration system
2. Point-in-time safe data layer
3. Signal library + cleaning + missing-data policy
4. Composite scoring v2 (`composite_rank`, not weighted sum)
5. Research harness — IC, backtest, challengers, calibration
6. Entry-timing overlay (separate from alpha scoring)
7. Diagnostics + warnings (turnover, HHI, IC drift)
8. Daily pipeline + run manifest
9. Testing + CI
10. Documentation + governance
11. Legacy retirement

Every directory in `tw_scanner/` exists because `theplan.md` specified it. Every config default in `tw_scanner/config/*.yaml` traces back to a line in the plan. When in doubt about a threshold, embargo window, or policy — the plan is canonical.

### [`doesthishelp.md`](doesthishelp.md) — the FinMind dataset catalog

A hand-curated reference for the **FinMind REST API** and its 75+ Taiwan-market datasets. For each dataset: name, required tier (Free / Backer / Sponsor), update frequency, column schema, and PIT-relevant date fields (announce date, ex-date, period end).

The Phase 2 data layer replaces most TWSE scraping with FinMind pulls. When building or debugging any `tw_scanner/data/*.py` module, consult this file first to confirm the dataset name, columns, and PIT rule. Getting dataset names wrong is the most common failure mode — this catalog prevents it.

`theplan.md` §11 renames this file to `docs/references/finmind_catalog.md` once Phase 10 documentation lands.

---

## Install

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate                        # Windows: .venv\Scripts\activate
python -m pip install -r requirements.lock.txt   # pinned deps for reproducibility
python -m pip install -e . --no-deps             # editable install of tw_scanner
tw-scanner --help
```

Secrets (for fundamentals via FinMind):

```bash
cp .env.example .env
# edit .env — register at finmindtrade.com, paste the token into FINMIND_TOKEN
```

Pre-commit (for local dev):

```bash
pre-commit install
pre-commit run --all-files
```

Local pre-commit needs Python 3.12 and Go 1.24.11+ so the pinned upstream `gitleaks` hook can build its managed scanner binary.

---

## Run

### v2 — new package (Phase 0 + 1)

```bash
tw-scanner --help
tw-scanner --seed 7 screen --dry-run
```

The `--dry-run` command loads every YAML, validates it through pydantic, computes the sha256 `config_hash`, and exits. This is the Phase 1 exit gate — from here the daily pipeline is filled in phase by phase.

### v1 — legacy scanner

```bash
python strategy_scanner.py
```

Produces under `strategy_output/`:

| File | Encoding | Description |
|---|---|---|
| `candidates.csv` | UTF-8 BOM | Top ~200 stocks sorted by `total_score` desc |
| `entry_signals.csv` | UTF-8 BOM | Subset with active buy signals |
| `score_chart.png` | PNG, 140 dpi | Score histogram + tech vs foreign-buy scatter |
| `run_manifest.json` | JSON | Audit-era manifest with drop-reason counts |
| `dropped.csv` | CSV | Exclusions with reason |

Expected runtime: 10–30 minutes depending on network conditions (rate-limited by TWSE throttles).

---

## Repository Layout

```
Stocks/
├── strategy_scanner.py        # v1 legacy single-file pipeline
├── tw_scanner/                # v2 package (Phase 0+1 done; Phase 2 in progress)
│   ├── cli.py                 # typer CLI entrypoint (tw-scanner)
│   ├── config/                # pydantic schema + YAML configs + loader.resolved_config_hash()
│   ├── data/                  # PIT-safe accessors (HttpClient, FinMind client, universe, prices, flows, …)
│   ├── signals/               # cross-sectional z-scored signals (scaffolded for Phase 3)
│   ├── scoring/               # composite_rank + missing-data policy (scaffolded for Phase 4)
│   ├── research/              # offline IC + backtest + calibration (scaffolded for Phase 5)
│   ├── pipeline/              # daily orchestration
│   ├── entry_signals/         # entry-timing overlay (scaffolded for Phase 6)
│   ├── diagnostics/           # turnover, HHI, IC drift (scaffolded for Phase 7)
│   ├── governance/            # manifest + lineage + changelog (scaffolded for Phase 10)
│   └── utils/                 # structured JSON logging + I/O helpers
├── config/                    # user-override YAMLs (top-level wins over package defaults)
├── tests/                     # 2 smoke tests today; grows with each phase
├── docs/                      # Phase 10 docs tree (placeholders today)
├── theplan.md                 # the rebuild roadmap (see above)
├── doesthishelp.md            # the FinMind dataset catalog (see above)
├── CLAUDE.md / AGENTS.md      # AI assistant guides — read before making large changes
├── pyproject.toml             # single source of truth for deps + packaging
├── requirements.lock.txt      # pip-compile output; pin for reproducibility
├── .pre-commit-config.yaml    # ruff + mypy + gitleaks
├── .github/workflows/ci.yml   # install → CLI help → pre-commit → lint → mypy → pytest → gitleaks
└── strategy_output/           # v1 outputs (auto-created)
```

---

## Development

```bash
ruff check tw_scanner tests    # lint
mypy tw_scanner                # types
pytest                         # tests (currently 2 smoke tests)
gitleaks detect --source .     # secret scan (clean across all 16 commits)
```

See [`CLAUDE.md`](CLAUDE.md) for the AI-assistant guide: conventions, phase tracker, common tasks, and how to add a new data accessor or signal.

---

## Limitations

- **v2 is incomplete.** Use for research only. The `composite_rank` will not exist until Phase 4, and frozen production weights will not exist until Phase 5 validation passes. Until then, treat `tw_scanner` outputs as **discovery**, not buy signals.
- **v1 is shippable but heuristic.** Its 100-point composite is not backtest-validated; the rebuild exists precisely because the scoring weights lack IC evidence.
- **FinMind rate limits.** Free tier is 600 req/hr. Backtests across deep history benefit from a Backer+ tier; without one, expect long per-ticker pulls.
- **Not a broker.** No order placement, no position sizing, no portfolio optimizer — by design. See `theplan.md` "Explicit Non-Goals".
