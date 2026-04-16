"""Liquidity and tradeability filters applied to the PIT universe.

Phase 2 stub — implements the filter interface; full execution-block logic
(TaiwanStockSuspended, TaiwanStockDispositionSecuritiesPeriod) lands when
Backer-tier FinMind access is confirmed.

Filters applied here (from plan §2.5):
  - 20-day median NT$ turnover >= cfg.universe.liquidity_turnover_20d_min_ntd
  - Close price >= cfg.universe.price_floor_ntd
  - (Stub) Suspended stocks excluded via TaiwanStockSuspended (Backer tier)
  - (Stub) Disposition stocks excluded via TaiwanStockDispositionSecuritiesPeriod
  - (Stub) Margin-short suspension excluded via TaiwanStockMarginShortSaleSuspension
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

logger = logging.getLogger(__name__)


def apply_liquidity_filter(
    universe: pd.DataFrame,
    prices: pd.DataFrame,
    as_of: date,
    cfg: AppConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Filter `universe` to tradeable stocks; return (eligible, dropped).

    Args:
        universe: Output of data/universe.get_listed_universe().
        prices:   Output of data/prices.get_prices() for the recent window.
        as_of:    The point-in-time date.
        cfg:      AppConfig (provides liquidity thresholds).

    Returns:
        Tuple of (eligible_universe, dropped_df) where dropped_df contains
        excluded stocks with a 'drop_reason' column for dropped.csv.
    """
    dropped_rows: list[dict] = []

    eligible = universe.copy()

    # ── 20-day 中位數成交額篩選 ───────────────────────
    if not prices.empty and "turnover" in prices.columns and "stock_id" in prices.columns:
        recent = prices[prices["date"] >= prices["date"].max() - pd.Timedelta(days=20)]
        turnover_20d = (
            recent.groupby("stock_id")["turnover"]
            .median()
            .rename("turnover_20d_median")
        )
        eligible = eligible.merge(turnover_20d, on="stock_id", how="left")
        min_turnover = cfg.universe.liquidity_turnover_20d_min_ntd
        low_liq = eligible["turnover_20d_median"].fillna(0) < min_turnover
        for sid in eligible.loc[low_liq, "stock_id"]:
            dropped_rows.append({"stock_id": sid, "drop_reason": "low_liquidity_20d"})
        eligible = eligible[~low_liq].copy()
    else:
        eligible["turnover_20d_median"] = pd.NA

    # ── 收盤價下限篩選 ───────────────────────────────
    if not prices.empty and "close" in prices.columns:
        latest_close = (
            prices.sort_values("date")
            .groupby("stock_id")["close"]
            .last()
            .rename("latest_close")
        )
        eligible = eligible.merge(latest_close, on="stock_id", how="left")
        below_floor = eligible["latest_close"].fillna(0) < cfg.universe.price_floor_ntd
        for sid in eligible.loc[below_floor, "stock_id"]:
            dropped_rows.append({"stock_id": sid, "drop_reason": "below_price_floor"})
        eligible = eligible[~below_floor].copy()
    else:
        eligible["latest_close"] = pd.NA

    # ── 暫停交易（Backer tier 佔位實作） ────────────────
    # TODO Phase 2b: fetch TaiwanStockSuspended and exclude active suspensions on as_of

    # ── 處置股（Backer tier 佔位實作） ──────────────────
    # TODO Phase 2b: fetch TaiwanStockDispositionSecuritiesPeriod and exclude active entries

    dropped_df = pd.DataFrame(dropped_rows) if dropped_rows else pd.DataFrame(columns=["stock_id", "drop_reason"])

    logger.info(
        "流動性篩選: %d → %d 檔 (排除 %d 檔, as_of=%s)",
        len(universe),
        len(eligible),
        len(dropped_df),
        as_of,
    )
    return eligible.reset_index(drop=True), dropped_df
