"""Industry classification from TaiwanStockInfo.industry_category.

Replaces the legacy BFIAMU_TO_TWSE_CODES mapping table (54 rows,
strategy_scanner.py lines 129–182) with a live PIT query against the
authoritative TaiwanStockInfo source.

Top-N industry by trading volume is computed in data/prices.py by grouping
Trading_money by industry_category; this module provides the stock→industry
mapping needed for that aggregation and for sector-neutral z-scoring.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from tw_scanner.data.base import DataNotAvailable

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

logger = logging.getLogger(__name__)


def get_industry_classification(as_of: date, cfg: AppConfig) -> pd.DataFrame:
    """Return stock_id → industry_category mapping as of `as_of`.

    Args:
        as_of: The point-in-time date.
        cfg:   AppConfig.

    Returns:
        DataFrame with columns:
            stock_id (str), industry_category (str).

    Raises:
        DataNotAvailable: if TaiwanStockInfo cannot be fetched.
    """
    from tw_scanner.data.universe import get_listed_universe  # 避免循環導入

    universe = get_listed_universe(as_of, cfg)

    if "industry_category" not in universe.columns:
        raise DataNotAvailable(
            "TaiwanStockInfo",
            "industry_category 欄位不存在",
            as_of=as_of,
        )

    mapping = (
        universe[["stock_id", "industry_category"]]
        .dropna(subset=["industry_category"])
        .drop_duplicates(subset=["stock_id"])
        .reset_index(drop=True)
    )
    logger.debug("產業分類: %d 檔股票, %d 個產業", len(mapping), mapping["industry_category"].nunique())
    return mapping


def get_top_industries_by_volume(
    prices: pd.DataFrame,
    industry_map: pd.DataFrame,
    top_n: int = 5,
) -> list[str]:
    """Return top `top_n` industries by total trading turnover.

    Args:
        prices:       Output of data/prices.get_prices() — must contain
                      'stock_id' and 'turnover' columns.
        industry_map: Output of get_industry_classification() — must contain
                      'stock_id' and 'industry_category' columns.
        top_n:        Number of top industries to return.

    Returns:
        List of industry_category strings, ordered by descending total turnover.
    """
    if prices.empty or industry_map.empty:
        return []

    merged = prices[["stock_id", "turnover"]].merge(
        industry_map[["stock_id", "industry_category"]], on="stock_id", how="left"
    )
    by_industry = (
        merged.groupby("industry_category")["turnover"]
        .sum()
        .sort_values(ascending=False)
        .head(top_n)
    )
    top = list(by_industry.index)
    logger.debug("成交量前 %d 大產業: %s", top_n, top)
    return top
