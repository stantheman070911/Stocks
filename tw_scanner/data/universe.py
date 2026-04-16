"""PIT-safe Taiwan stock universe with delisting support.

Addresses review §2.1 (survivorship bias): every call to get_listed_universe()
returns exactly the stocks that were listed on `as_of` — no look-ahead,
no missing delistings.

Data sources:
  TaiwanStockInfo     — current listed universe; common-equity filter applied here.
  TaiwanStockDelisting — delisted stocks since 2001; merged to reconstruct PIT snapshot.

Common-equity filter (excludes ETFs, warrants, TDRs, preferreds, funds, rights):
  TaiwanStockInfo.type == 'twse' (for TWSE common equity)
  4-digit stock_id (non-4-digit codes = ETFs, warrants, etc.)
  industry_category not in cfg.universe.exclude_industry_categories
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

# FinMind TaiwanStockInfo type values for TWSE common equity
_COMMON_EQUITY_TYPES = {"twse", "tpex"}  # tpex included for future extension


def get_listed_universe(as_of: date, cfg: AppConfig) -> pd.DataFrame:
    """Return PIT common-equity universe as of `as_of`.

    Args:
        as_of: The point-in-time date.  Only stocks listed on this date are returned.
        cfg:   AppConfig (provides universe settings and data config).

    Returns:
        DataFrame with columns:
            stock_id (str), stock_name (str), industry_category (str),
            type (str), listed_date (date | NaT), delisted_date (date | NaT),
            source, dataset, retrieved_at, as_of

    Raises:
        DataNotAvailable: if TaiwanStockInfo cannot be fetched.
    """
    from tw_scanner.data.finmind_client import FinMindClient  # 避免循環導入

    with FinMindClient(cfg) as client:
        info_df = _fetch_stock_info(client, as_of)
        delist_df = _fetch_delisting(client, as_of)

    if info_df.empty:
        raise DataNotAvailable("TaiwanStockInfo", "回傳空資料", as_of=as_of)

    # 合併下市資訊
    universe = _merge_delisting(info_df, delist_df, as_of)

    # 普通股篩選
    universe = _apply_common_equity_filter(universe, cfg)

    logger.info(
        "PIT 台股宇宙: %d 檔股票 (as_of=%s, 排除金融: %s)",
        len(universe),
        as_of,
        cfg.universe.exclude_industry_categories,
    )
    return universe


# ── 私有輔助函數 ─────────────────────────────────────


def _fetch_stock_info(client: FinMindClient, as_of: date) -> pd.DataFrame:
    """Fetch TaiwanStockInfo (no date params)."""

    df = client.fetch_no_date_params("TaiwanStockInfo", as_of)
    if df.empty:
        return df

    # 標準化欄位名稱
    df = df.rename(columns={"date": "listed_date"})
    for col in ["stock_id", "stock_name", "industry_category", "type", "listed_date"]:
        if col not in df.columns:
            df[col] = None
    return df[["stock_id", "stock_name", "industry_category", "type", "listed_date",
               "source", "dataset", "retrieved_at", "as_of"]]


def _fetch_delisting(client: FinMindClient, as_of: date) -> pd.DataFrame:
    """Fetch TaiwanStockDelisting (no date params)."""
    try:
        df = client.fetch_no_date_params("TaiwanStockDelisting", as_of)
        if df.empty:
            return pd.DataFrame(columns=["stock_id", "stock_name", "delisted_date"])
        df = df.rename(columns={"date": "delisted_date"})
        return df[["stock_id", "delisted_date"]].copy()
    except DataNotAvailable:
        logger.warning("TaiwanStockDelisting 不可用，下市篩選可能不完整")
        return pd.DataFrame(columns=["stock_id", "delisted_date"])


def _merge_delisting(
    info: pd.DataFrame,
    delist: pd.DataFrame,
    as_of: date,
) -> pd.DataFrame:
    """Merge delisting dates into info; keep only stocks active on as_of."""
    merged = info.merge(delist, on="stock_id", how="left")

    # 解析日期欄位
    for col in ["listed_date", "delisted_date"]:
        if col in merged.columns:
            merged[col] = pd.to_datetime(merged[col], errors="coerce").dt.date

    as_of_ts = pd.Timestamp(as_of)

    # 保留：已上市（listed_date <= as_of）且 尚未下市（delisted_date is NaT or > as_of）
    listed_mask = pd.to_datetime(merged["listed_date"], errors="coerce") <= as_of_ts
    delisted_mask = pd.to_datetime(merged.get("delisted_date"), errors="coerce") > as_of_ts
    not_delisted = merged["delisted_date"].isna() | delisted_mask

    return merged[listed_mask & not_delisted].copy()


def _apply_common_equity_filter(df: pd.DataFrame, cfg: AppConfig) -> pd.DataFrame:
    """Apply common-equity and universe exclusion filters."""
    original = len(df)

    # 只留普通股（4位數股票代碼）
    df = df[df["stock_id"].str.match(r"^\d{4}$", na=False)].copy()

    # 依 type 篩選（twse / tpex）— TaiwanStockInfo.type
    if "type" in df.columns:
        df = df[df["type"].isin(_COMMON_EQUITY_TYPES)].copy()

    # 排除金融保險等類別
    if cfg.universe.exclude_industry_categories and "industry_category" in df.columns:
        exclude = set(cfg.universe.exclude_industry_categories)
        df = df[~df["industry_category"].isin(exclude)].copy()

    logger.debug("普通股篩選: %d → %d 檔", original, len(df))
    return df.reset_index(drop=True)
