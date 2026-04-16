"""PIT-safe unadjusted OHLCV price fetching from FinMind TaiwanStockPrice.

Phase 2 delivers unadjusted close prices.  Corporate-action adjustments
(dividends, splits, capital reductions, par-value changes) are applied in
data/corp_actions.py (Phase 2 follow-up) — a 'raw_close' column is always
present alongside 'close' so callers can detect when adjustment is pending.

Validation applied to each ticker's price series:
  - No duplicate dates.
  - No zero-volume sessions beyond expected non-trading days.
  - OHLC consistency: low <= open, close, high; high >= open, close, low.
  - Stale close: close unchanged for > 5 consecutive sessions is flagged.
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


def get_prices(
    stock_ids: list[str],
    as_of: date,
    lookback_days: int,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch unadjusted OHLCV for `stock_ids` over a lookback window ending on `as_of`.

    Args:
        stock_ids:    List of 4-digit TWSE stock IDs to fetch.
        as_of:        The point-in-time end date (inclusive).
        lookback_days: Calendar days to look back from as_of.
        cfg:          AppConfig.

    Returns:
        Long-format DataFrame with columns:
            date (datetime64), stock_id (str),
            open, high, low, close, raw_close (float64),
            volume (int64), turnover (int64),
            data_quality_flags (str),
            source, dataset, retrieved_at, as_of.

    Raises:
        DataNotAvailable: if no price data can be fetched for any ticker.
    """
    from tw_scanner.data.finmind_client import FinMindClient  # 避免循環導入

    start = as_of - timedelta(days=lookback_days)
    frames: list[pd.DataFrame] = []

    with FinMindClient(cfg) as client:
        for sid in stock_ids:
            try:
                df = client.fetch(
                    "TaiwanStockPrice",
                    as_of=as_of,
                    data_id=sid,
                    start_date=start,
                    end_date=as_of,
                )
                if df.empty:
                    logger.debug("TaiwanStockPrice 無資料: %s", sid)
                    continue
                df = _normalise_price_df(df, sid, as_of)
                frames.append(df)
            except DataNotAvailable as exc:
                logger.warning("股價下載失敗: %s — %s", sid, exc)

    if not frames:
        raise DataNotAvailable(
            "TaiwanStockPrice",
            f"所有 {len(stock_ids)} 檔股票均無價格資料",
            as_of=as_of,
        )

    result = pd.concat(frames, ignore_index=True)
    logger.info("股價下載完成: %d 筆 (%d 檔, as_of=%s)", len(result), len(frames), as_of)
    return result


# ── 私有輔助函數 ─────────────────────────────────────


def _normalise_price_df(df: pd.DataFrame, stock_id: str, as_of: date) -> pd.DataFrame:
    """Rename columns, cast types, validate, and attach metadata."""
    # 欄位重命名（FinMind TaiwanStockPrice 命名）
    rename = {
        "max": "high",
        "min": "low",
        "Trading_Volume": "volume",
        "Trading_money": "turnover",
    }
    df = df.rename(columns=rename)

    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.warning("%s: 缺少欄位 %s", stock_id, missing)
        return pd.DataFrame()

    # 確保 stock_id 欄位存在
    if "stock_id" not in df.columns:
        df["stock_id"] = stock_id

    # 型別轉換
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["volume", "turnover"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # 只保留 as_of 當天及之前的資料（PIT 安全）
    df = df[df["date"].dt.date <= as_of].copy()

    # 去除重複日期（保留最後一筆）
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")

    # 原始收盤（調整前）= 調整後（Phase 2 尚未實作企業行動調整）
    df["raw_close"] = df["close"]

    # 資料品質旗標
    df["data_quality_flags"] = _validate(df, stock_id)

    # 加入元資料欄位
    df = add_metadata_columns(df, source="finmind", dataset="TaiwanStockPrice", as_of=as_of)

    cols = [
        "date", "stock_id", "open", "high", "low", "close", "raw_close",
        "volume", "turnover", "data_quality_flags",
        "source", "dataset", "retrieved_at", "as_of",
    ]
    present = [c for c in cols if c in df.columns]
    return df[present].reset_index(drop=True)


def _validate(df: pd.DataFrame, stock_id: str) -> pd.Series:
    """Return a string Series of comma-separated quality flags per row."""
    flags: pd.Series = pd.Series("", index=df.index)

    # OHLC 一致性
    ohlc_bad = (df["low"] > df["close"]) | (df["high"] < df["close"]) | (df["low"] > df["open"]) | (df["high"] < df["open"])
    flags = flags.where(~ohlc_bad, flags + "ohlc_inconsistent,")

    # 零成交量
    if "volume" in df.columns:
        zero_vol = df["volume"].fillna(0) == 0
        flags = flags.where(~zero_vol, flags + "zero_volume,")

    # 停滯收盤（連續 5 天不變）
    stale = df["close"].diff().fillna(1) == 0
    stale_streak = stale.rolling(5, min_periods=5).sum() == 5
    flags = flags.where(~stale_streak, flags + "stale_close,")

    if flags.str.len().max() > 0:
        logger.debug("%s: 資料品質警告 %s", stock_id, flags[flags != ""].unique()[:3])

    return flags.str.rstrip(",")
