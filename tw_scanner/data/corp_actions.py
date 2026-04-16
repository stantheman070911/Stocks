"""PIT corporate action data and in-code price adjustment.

Phase 2 stub — defines the adjustment interface.
Full implementation lands in Phase 2b after the price validation canary
(--validate-prices flag) is tested against TaiwanStockPriceAdj.

Events handled (plan §2.6):
  - TaiwanStockDividendResult   — cash + stock dividends
  - TaiwanStockSplitPrice       — stock splits and reverse splits
  - TaiwanStockCapitalReductionReferencePrice — capital reductions
  - TaiwanStockParValueChange   — par value changes

PIT rule: apply adjustment factors only for events with ex_date <= as_of.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

logger = logging.getLogger(__name__)


def get_corp_actions(
    stock_ids: list[str],
    as_of: date,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch all corporate action events for `stock_ids` up to `as_of`.

    Args:
        stock_ids: List of 4-digit TWSE stock IDs.
        as_of:     The point-in-time date; only events with ex_date <= as_of returned.
        cfg:       AppConfig.

    Returns:
        DataFrame: stock_id, ex_date, event_type, adjustment_factor.
    """
    raise NotImplementedError(
        "get_corp_actions: Phase 2b implementation pending. "
        "Will fetch TaiwanStockDividendResult, TaiwanStockSplitPrice, "
        "TaiwanStockCapitalReductionReferencePrice, TaiwanStockParValueChange."
    )


def apply_price_adjustment(
    prices: pd.DataFrame,
    corp_actions: pd.DataFrame,
    as_of: date,
) -> pd.DataFrame:
    """Apply PIT corporate action factors to unadjusted close prices.

    Args:
        prices:       Output of data/prices.get_prices() — must have raw_close column.
        corp_actions: Output of get_corp_actions().
        as_of:        Point-in-time date; only events <= as_of applied.

    Returns:
        prices with 'close' column replaced by adjusted close;
        'raw_close' retains the original unadjusted value.
    """
    raise NotImplementedError(
        "apply_price_adjustment: Phase 2b implementation pending. "
        "Computes cumulative adjustment factor from corp_actions and applies "
        "to prices.close, preserving raw_close."
    )
