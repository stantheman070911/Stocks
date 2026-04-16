"""Margin purchase and short sale balances from TaiwanStockMarginPurchaseShortSale.

Replaces the legacy TWSE MI_MARGN position-based column parsing
(strategy_scanner.py get_margin) with typed named-column access.

Key signals derived:
  squeeze_ratio       = ShortSaleTodayBalance / MarginPurchaseTodayBalance
  margin_utilization  = MarginPurchaseTodayBalance / MarginPurchaseLimit
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

# 使用具名欄位，不依賴欄位位置（修正舊版 MI_MARGN 脆弱解析）
_REQUIRED_COLS = [
    "stock_id",
    "date",
    "MarginPurchaseTodayBalance",
    "ShortSaleTodayBalance",
    "MarginPurchaseLimit",
    "ShortSaleLimit",
]


def get_margin(
    stock_ids: list[str],
    as_of: date,
    lookback_days: int,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch margin/short balances for `stock_ids` and derive squeeze_ratio.

    Args:
        stock_ids:    List of 4-digit TWSE stock IDs.
        as_of:        The point-in-time end date (inclusive).
        lookback_days: Calendar days to look back.
        cfg:          AppConfig.

    Returns:
        DataFrame keyed by (date, stock_id) with columns:
            MarginPurchaseTodayBalance, ShortSaleTodayBalance,
            MarginPurchaseLimit, ShortSaleLimit (int64),
            squeeze_ratio, margin_utilization (float64),
            source, dataset, retrieved_at, as_of.

    Raises:
        DataNotAvailable: if no margin data can be fetched.
    """
    from tw_scanner.data.finmind_client import FinMindClient  # 避免循環導入

    start = as_of - timedelta(days=lookback_days)
    frames: list[pd.DataFrame] = []

    with FinMindClient(cfg) as client:
        for sid in stock_ids:
            try:
                df = client.fetch(
                    "TaiwanStockMarginPurchaseShortSale",
                    as_of=as_of,
                    data_id=sid,
                    start_date=start,
                    end_date=as_of,
                )
                if df.empty:
                    continue
                df = _normalise(df, sid, as_of)
                frames.append(df)
            except DataNotAvailable as exc:
                logger.warning("融資融券資料下載失敗: %s — %s", sid, exc)

    if not frames:
        raise DataNotAvailable(
            "TaiwanStockMarginPurchaseShortSale",
            f"所有 {len(stock_ids)} 檔股票均無法取得融資融券資料",
            as_of=as_of,
        )

    result = pd.concat(frames, ignore_index=True)
    logger.info("融資融券資料: %d 筆 (%d 檔, as_of=%s)", len(result), len(frames), as_of)
    return result


# ── 私有輔助函數 ─────────────────────────────────────


def _normalise(df: pd.DataFrame, stock_id: str, as_of: date) -> pd.DataFrame:
    """Cast types, compute derived ratios, attach metadata."""
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].dt.date <= as_of].copy()

    if "stock_id" not in df.columns:
        df["stock_id"] = stock_id

    int_cols = [
        "MarginPurchaseTodayBalance",
        "ShortSaleTodayBalance",
        "MarginPurchaseLimit",
        "ShortSaleLimit",
    ]
    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int64")
        else:
            df[col] = 0

    # 擠壓比率 = 融券餘額 / 融資餘額（融資為 0 時設為 NaN 避免除以零）
    margin_bal = df["MarginPurchaseTodayBalance"].replace(0, pd.NA).astype("float64")
    df["squeeze_ratio"] = df["ShortSaleTodayBalance"].astype("float64") / margin_bal

    # 融資使用率 = 融資餘額 / 融資限額
    margin_limit = df["MarginPurchaseLimit"].replace(0, pd.NA).astype("float64")
    df["margin_utilization"] = df["MarginPurchaseTodayBalance"].astype("float64") / margin_limit

    df = add_metadata_columns(
        df, source="finmind", dataset="TaiwanStockMarginPurchaseShortSale", as_of=as_of
    )

    keep = [
        "date", "stock_id",
        "MarginPurchaseTodayBalance", "ShortSaleTodayBalance",
        "MarginPurchaseLimit", "ShortSaleLimit",
        "squeeze_ratio", "margin_utilization",
        "source", "dataset", "retrieved_at", "as_of",
    ]
    return df[[c for c in keep if c in df.columns]].reset_index(drop=True)
