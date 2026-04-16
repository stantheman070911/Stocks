"""Daily short sale balance from TaiwanDailyShortSaleBalances (信用額度總量管制餘額表).

Phase 2 stub — defines the accessor interface.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

logger = logging.getLogger(__name__)


def get_short_balance(
    stock_ids: list[str],
    as_of: date,
    lookback_days: int,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch daily short sale balance for `stock_ids`.

    Args:
        stock_ids:    List of 4-digit TWSE stock IDs.
        as_of:        The point-in-time end date (inclusive).
        lookback_days: Calendar days to look back.
        cfg:          AppConfig.

    Returns:
        DataFrame: date, stock_id, SBLShortSalesCurrentDayBalance,
                   MarginShortSalesCurrentDayBalance, total_short_balance.
    """
    raise NotImplementedError(
        "get_short_balance: Phase 3 implementation pending. "
        "Will fetch TaiwanDailyShortSaleBalances per ticker."
    )
