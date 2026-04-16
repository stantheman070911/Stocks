"""PIT-safe fundamental data with announce-date embargo enforcement.

Phase 2 stub — defines the accessor interface and embargo logic.
Data fetching bodies land when FinMind fundamentals datasets are confirmed
and the full embargo engine is tested (Phase 3 / Phase 4 sign-off).

Embargo rules (plan §1.3):
  - Monthly revenue:    available_date = release_date OR period_end + 15 cal days
  - Quarterly reports:  available_date = period_end + 60 cal days
  - Annual reports:     available_date = fiscal_year_end + 90 cal days

Every row returned has `available_date <= as_of` enforced.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

logger = logging.getLogger(__name__)


def get_per_pbr(
    stock_ids: list[str],
    as_of: date,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch daily P/E and P/B ratios (TaiwanStockPER) — inherently PIT.

    TaiwanStockPER is a daily dataset updated after market close; no embargo
    required beyond filtering date <= as_of.

    Args:
        stock_ids: List of 4-digit TWSE stock IDs.
        as_of:     The point-in-time end date.
        cfg:       AppConfig.

    Returns:
        DataFrame: date, stock_id, PER, PBR, dividend_yield.
    """
    raise NotImplementedError(
        "get_per_pbr: Phase 3 implementation pending. "
        "Will fetch TaiwanStockPER per ticker and filter date <= as_of."
    )


def get_financial_statements(
    stock_ids: list[str],
    as_of: date,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch quarterly income statement, balance sheet, and cash flow — PIT with embargo.

    Args:
        stock_ids: List of 4-digit TWSE stock IDs.
        as_of:     The point-in-time end date.
        cfg:       AppConfig (provides fundamentals_embargo_quarterly_days).

    Returns:
        DataFrame with type/value pivoted to wide format;
        all rows have available_date <= as_of enforced.
    """
    raise NotImplementedError(
        "get_financial_statements: Phase 3 implementation pending. "
        "Will fetch TaiwanStockFinancialStatements + BalanceSheet + CashFlows "
        f"with {cfg.backtest.fundamentals_embargo_quarterly_days}-day quarterly embargo."
    )


def get_monthly_revenue(
    stock_ids: list[str],
    as_of: date,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch monthly revenue (TaiwanStockMonthRevenue) — PIT with 15-day embargo.

    Args:
        stock_ids: List of 4-digit TWSE stock IDs.
        as_of:     The point-in-time end date.
        cfg:       AppConfig (provides fundamentals_embargo_monthly_days).

    Returns:
        DataFrame: date, stock_id, revenue, yoy_growth, mom_growth.
        Rows with available_date > as_of are excluded.
    """
    raise NotImplementedError(
        "get_monthly_revenue: Phase 3 implementation pending. "
        "Will fetch TaiwanStockMonthRevenue and apply "
        f"{cfg.backtest.fundamentals_embargo_monthly_days}-day monthly embargo."
    )


# ── 輔助：embargo 日期計算 ─────────────────────────────


def apply_quarterly_embargo(period_end: date, cfg: AppConfig) -> date:
    """Return the earliest date on which a quarterly report is considered available."""
    return period_end + timedelta(days=cfg.backtest.fundamentals_embargo_quarterly_days)


def apply_annual_embargo(fiscal_year_end: date, cfg: AppConfig) -> date:
    """Return the earliest date on which an annual report is considered available."""
    return fiscal_year_end + timedelta(days=cfg.backtest.fundamentals_embargo_annual_days)


def apply_monthly_revenue_embargo(period_end: date, cfg: AppConfig) -> date:
    """Return the earliest date on which monthly revenue is considered available."""
    return period_end + timedelta(days=cfg.backtest.fundamentals_embargo_monthly_days)
