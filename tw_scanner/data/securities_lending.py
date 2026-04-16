"""Securities lending data from TaiwanStockSecuritiesLending (借券成交明細).

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


def get_securities_lending(
    stock_ids: list[str],
    as_of: date,
    lookback_days: int,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch securities lending transactions for `stock_ids`.

    Args:
        stock_ids:    List of 4-digit TWSE stock IDs.
        as_of:        The point-in-time end date (inclusive).
        lookback_days: Calendar days to look back.
        cfg:          AppConfig.

    Returns:
        DataFrame: date, stock_id, volume, fee_rate, lending_balance_proxy.
    """
    raise NotImplementedError(
        "get_securities_lending: Phase 3 implementation pending. "
        "Will fetch TaiwanStockSecuritiesLending per ticker."
    )
