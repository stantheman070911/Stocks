"""Institutional investor flow data from FinMind TaiwanStockInstitutionalInvestorsBuySell.

Replaces the legacy TWSE T86 scrape (strategy_scanner.py get_foreign_ranking).

Key columns returned:
  foreign_net   = foreign_buy - foreign_sell (shares)
  foreign_buy   = 外資買超
  foreign_sell  = 外資賣超
  trust_net     = 投信淨買
  dealer_net    = 自營商淨買

Investor name values in the `name` column of TaiwanStockInstitutionalInvestorsBuySell:
  '外資及陸資'         — Foreign + Chinese investors (primary foreign signal)
  '外資及陸資(不含自營商)' — sometimes present as alternative
  '投信'              — Trust funds
  '自營商'            — Proprietary dealers
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

# 外資名稱前綴（FinMind 使用不同寫法）
_FOREIGN_NAME_PREFIX = "外資"
_TRUST_NAME = "投信"
_DEALER_NAME = "自營商"


def get_institutional_flows(
    stock_ids: list[str],
    as_of: date,
    lookback_days: int,
    cfg: AppConfig,
) -> pd.DataFrame:
    """Fetch institutional investor buy/sell flows for `stock_ids`.

    Args:
        stock_ids:    List of 4-digit TWSE stock IDs.
        as_of:        The point-in-time end date (inclusive).
        lookback_days: Calendar days to look back.
        cfg:          AppConfig.

    Returns:
        Wide-format DataFrame keyed by (date, stock_id) with columns:
            foreign_net, foreign_buy, foreign_sell (shares, int64),
            trust_net, dealer_net (shares, int64),
            source, dataset, retrieved_at, as_of.

    Raises:
        DataNotAvailable: if the API cannot be reached for all tickers.
    """
    from tw_scanner.data.finmind_client import FinMindClient  # 避免循環導入

    start = as_of - timedelta(days=lookback_days)
    frames: list[pd.DataFrame] = []

    with FinMindClient(cfg) as client:
        for sid in stock_ids:
            try:
                df = client.fetch(
                    "TaiwanStockInstitutionalInvestorsBuySell",
                    as_of=as_of,
                    data_id=sid,
                    start_date=start,
                    end_date=as_of,
                )
                if df.empty:
                    continue
                df = _pivot_flows(df, sid, as_of)
                frames.append(df)
            except DataNotAvailable as exc:
                logger.warning("三大法人資料下載失敗: %s — %s", sid, exc)

    if not frames:
        raise DataNotAvailable(
            "TaiwanStockInstitutionalInvestorsBuySell",
            f"所有 {len(stock_ids)} 檔股票均無法取得三大法人資料",
            as_of=as_of,
        )

    result = pd.concat(frames, ignore_index=True)
    logger.info("三大法人資料: %d 筆 (%d 檔, as_of=%s)", len(result), len(frames), as_of)
    return result


# ── 私有輔助函數 ─────────────────────────────────────


def _pivot_flows(raw: pd.DataFrame, stock_id: str, as_of: date) -> pd.DataFrame:
    """Pivot long-format investor rows into wide format with net columns."""
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw = raw[raw["date"].dt.date <= as_of].copy()

    for col in ["buy", "sell"]:
        raw[col] = pd.to_numeric(raw.get(col, 0), errors="coerce").fillna(0).astype("int64")

    raw["net"] = raw["buy"] - raw["sell"]

    if "stock_id" not in raw.columns:
        raw["stock_id"] = stock_id

    # 外資行（名稱以 '外資' 開頭）
    foreign = raw[raw["name"].str.startswith(_FOREIGN_NAME_PREFIX, na=False)]
    # 投信
    trust = raw[raw["name"] == _TRUST_NAME]
    # 自營商
    dealer = raw[raw["name"] == _DEALER_NAME]

    # 按日期彙總（同一名稱可能有多行）
    def agg(subset: pd.DataFrame, prefix: str) -> pd.DataFrame:
        if subset.empty:
            return pd.DataFrame(columns=["date", "stock_id", f"{prefix}_buy", f"{prefix}_sell", f"{prefix}_net"])
        return (
            subset.groupby(["date", "stock_id"], as_index=False)
            .agg(
                **{
                    f"{prefix}_buy": ("buy", "sum"),
                    f"{prefix}_sell": ("sell", "sum"),
                    f"{prefix}_net": ("net", "sum"),
                }
            )
        )

    fa = agg(foreign, "foreign")
    ta = agg(trust, "trust")
    da = agg(dealer, "dealer")

    # 以外資為基礎合併其他投資人
    if fa.empty:
        # 至少有日期 + stock_id 索引
        dates = raw[["date", "stock_id"]].drop_duplicates()
        merged = dates
    else:
        merged = fa

    for extra in [ta, da]:
        if not extra.empty:
            key_cols = ["date", "stock_id"]
            merged = merged.merge(extra.drop(columns=[c for c in extra.columns if c not in key_cols + list(extra.columns)]), on=key_cols, how="left")

    # 填充缺失欄位
    for col in ["foreign_net", "foreign_buy", "foreign_sell", "trust_net", "dealer_net"]:
        if col not in merged.columns:
            merged[col] = 0

    merged = add_metadata_columns(
        merged, source="finmind", dataset="TaiwanStockInstitutionalInvestorsBuySell", as_of=as_of
    )
    return merged.reset_index(drop=True)
