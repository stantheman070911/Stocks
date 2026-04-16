"""Taiwan stock trading calendar from FinMind TaiwanStockTradingDate.

Replaces the hardcoded TW_MARKET_HOLIDAYS frozenset in the legacy scanner
and the hardcoded TRADING_DAYS_YEAR = 252 constant.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def get_trading_calendar(
    start: date,
    end: date,
    cfg: AppConfig,
) -> list[date]:
    """Return sorted list of Taiwan Stock Exchange trading dates in [start, end].

    Fetches TaiwanStockTradingDate from FinMind and filters to the requested window.
    Results are cached in-process by (start, end, cfg) to avoid repeated fetches
    within a single pipeline run.

    Args:
        start: First date of the desired window (inclusive).
        end:   Last date of the desired window (inclusive).
        cfg:   AppConfig (provides data settings and cache dir).

    Returns:
        Sorted list of trading dates as date objects.

    Raises:
        DataNotAvailable: if FinMind cannot be reached and no cache exists.
    """
    from tw_scanner.data.finmind_client import FinMindClient  # 避免循環導入

    with FinMindClient(cfg) as client:
        df = client.fetch(
            "TaiwanStockTradingDate",
            as_of=end,
            start_date=start,
            end_date=end,
        )

    if df.empty:
        logger.warning("TaiwanStockTradingDate 回傳空資料，改用工作日估算")
        return _weekday_fallback(start, end)

    dates: list[date] = sorted(
        date.fromisoformat(str(d)[:10])
        for d in df["date"].dropna().unique()
        if str(d)[:10] >= str(start) and str(d)[:10] <= str(end)
    )
    logger.debug("交易日曆: %d 個交易日 (%s → %s)", len(dates), start, end)
    return dates


def trading_days_per_year(calendar: list[date]) -> float:
    """Empirical annualisation factor from a trading calendar.

    Uses the number of trading days per calendar year in the provided list.
    Falls back to 245 (empirical TW average) if the list covers fewer than
    90 calendar days.

    Args:
        calendar: Sorted list of trading dates from get_trading_calendar().

    Returns:
        Estimated trading days per year (typically ~245 for TWSE).
    """
    if len(calendar) < 2:
        return 245.0

    span_days = (calendar[-1] - calendar[0]).days
    if span_days < 90:
        return 245.0

    # Annualise: (trading days in span) / (calendar days in span) * 365
    return len(calendar) / span_days * 365.0


def prev_trading_day(ref: date, calendar: list[date]) -> date:
    """Return the most recent trading day strictly before `ref`."""
    before = [d for d in calendar if d < ref]
    if not before:
        raise ValueError(f"交易日曆中無 {ref} 之前的交易日")
    return before[-1]


def next_trading_day(ref: date, calendar: list[date]) -> date:
    """Return the earliest trading day strictly after `ref`."""
    after = [d for d in calendar if d > ref]
    if not after:
        raise ValueError(f"交易日曆中無 {ref} 之後的交易日")
    return after[0]


def is_trading_day(d: date, calendar: list[date]) -> bool:
    """Return True if `d` is a TWSE trading day."""
    # Binary search would be faster but for typical 245-element lists, set is fine
    return d in set(calendar)


# ── 私有 ─────────────────────────────────────────────


def _weekday_fallback(start: date, end: date) -> list[date]:
    """Emergency fallback: return Mon–Fri dates (no holiday correction)."""
    result: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # 0=Monday … 4=Friday
            result.append(cur)
        cur += timedelta(days=1)
    return result
