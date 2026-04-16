"""Taiwan stock market capitalisation from TaiwanStockMarketValue (Backer/Sponsor tier).

Phase 2 stub — defines the accessor interface.

Note: This dataset requires Backer/Sponsor tier FinMind access.
      For free-tier users, market_value will be estimated from
      close_price × shares_outstanding where shares data is available.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

logger = logging.getLogger(__name__)


def get_market_value(
    stock_ids: list[str],
    as_of: date,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch market capitalisation for `stock_ids` as of `as_of`.

    Args:
        stock_ids: List of 4-digit TWSE stock IDs.
        as_of:     The point-in-time date.
        cfg:       AppConfig.

    Returns:
        DataFrame: stock_id (str), date (datetime64), market_value (int64).

    Note:
        Requires Backer/Sponsor FinMind tier.  Raises NotImplementedError
        until gated tier access is confirmed.
    """
    raise NotImplementedError(
        "get_market_value: Phase 3 implementation pending (Backer tier required). "
        "Will fetch TaiwanStockMarketValue; falls back to price × float_shares estimate."
    )
