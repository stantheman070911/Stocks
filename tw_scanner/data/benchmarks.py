"""TAIEX Total Return Index and risk-free rate feed.

Addresses review §2.10:
  - Primary benchmark: TaiwanStockTotalReturnIndex (includes dividends).
    Replaces 0050.TW (price-only, yfinance) as the primary benchmark.
  - Risk-free rate: Taiwan central bank repo rate (stub for now; uses fallback).
  - Empirical trading_days_per_year from calendar replaces hardcoded 252.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from tw_scanner.data.base import DataNotAvailable, add_metadata_columns

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

logger = logging.getLogger(__name__)


def get_taiex_total_return(
    as_of: date,
    lookback_days: int,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch TAIEX Total Return Index (加權報酬指數) from FinMind.

    Args:
        as_of:        The point-in-time end date (inclusive).
        lookback_days: Calendar days to look back.
        cfg:          AppConfig.

    Returns:
        DataFrame with columns:
            date (datetime64), price (float64),
            daily_return (float64, NaN for the first row),
            source, dataset, retrieved_at, as_of.

    Raises:
        DataNotAvailable: if the index cannot be fetched.
    """
    from tw_scanner.data.finmind_client import FinMindClient  # 避免循環導入

    start = as_of - timedelta(days=lookback_days)

    with FinMindClient(cfg) as client:
        df = client.fetch(
            "TaiwanStockTotalReturnIndex",
            as_of=as_of,
            data_id=cfg.data.benchmark_ticker_primary,  # "TAIEX"
            start_date=start,
            end_date=as_of,
        )

    if df.empty:
        raise DataNotAvailable(
            "TaiwanStockTotalReturnIndex",
            f"TAIEX 總報酬指數無資料 ({start} → {as_of})",
            as_of=as_of,
        )

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].dt.date <= as_of].sort_values("date").copy()
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["daily_return"] = df["price"].pct_change()

    df = add_metadata_columns(
        df, source="finmind", dataset="TaiwanStockTotalReturnIndex", as_of=as_of
    )
    return df[["date", "price", "daily_return", "source", "dataset", "retrieved_at", "as_of"]].reset_index(drop=True)


def get_risk_free_rate(as_of: date, cfg: AppConfig) -> float:
    """Return the Taiwan risk-free rate (annual, decimal) for `as_of`.

    Phase 2: returns the configured fallback (default 1.5%).
    Phase 3+: will integrate a Taiwan central bank repo rate feed.

    Args:
        as_of: The point-in-time date (currently unused).
        cfg:   AppConfig (provides risk_free_rate_fallback).

    Returns:
        Annualised risk-free rate as a decimal (e.g. 0.015 = 1.5%).
    """
    _ = as_of  # 未來版本將根據日期查詢利率
    rate = cfg.data.risk_free_rate_fallback
    logger.debug("風險無息利率: %.4f (備用值)", rate)
    return rate
