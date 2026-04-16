"""TWSE fallback HTTP client (schema canaries live here).

Primary data source is FinMind (data/finmind_client.py).
TWSE is retained only as an emergency fallback for:
  - T86 (foreign investor daily, if FinMind is unavailable)
  - MI_MARGN (margin balances fallback)
  - MI_INDEX20 (industry volume, if FinMind is unavailable)

Any response sourced from TWSE is tagged with source='twse_fallback' in the
manifest so the operator knows to treat it with lower confidence.

IMPORTANT: TWSE aggressively rate-limits; the RateLimiter serialises all TWSE
requests through a shared lock with a 0.7–1.5 s random delay.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from tw_scanner.data.base import DataNotAvailable, HttpClient, add_metadata_columns

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

logger = logging.getLogger(__name__)

_TWSE_T86 = "https://www.twse.com.tw/fund/T86"
_TWSE_MI_MARGN = "https://www.twse.com.tw/exchangeReport/MI_MARGN"
_TWSE_MI_INDEX20 = "https://www.twse.com.tw/exchangeReport/MI_INDEX20"
_TWSE_STOCK_LIST = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"


class TWSEClient:
    """TWSE 備用客戶端（僅在 FinMind 不可用時使用）。"""

    def __init__(self, cfg: AppConfig) -> None:
        self._http = HttpClient(cfg)
        self._cfg = cfg

    def get_stock_list(self, as_of: date) -> pd.DataFrame:
        """Fetch TWSE listed stock list as a fallback for TaiwanStockInfo.

        Returns:
            DataFrame: stock_id (有價證券代號), stock_name (有價證券名稱).
        """
        try:
            resp = self._http.get(_TWSE_STOCK_LIST)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                raise DataNotAvailable("TWSE/t187ap03_L", "空回應", as_of=as_of)
            df = pd.DataFrame(data)
            # 標準化欄位名稱
            rename = {
                "有價證券代號": "stock_id",
                "有價證券名稱": "stock_name",
                "國際證券辨識號碼(ISIN Code)": "isin",
                "上市日期": "listed_date",
                "市場別": "market",
                "產業別": "industry_category",
                "CFICode": "cfi_code",
                "備註": "note",
            }
            df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
            df = add_metadata_columns(df, source="twse_fallback", dataset="t187ap03_L", as_of=as_of)
            logger.warning("使用 TWSE 備用股票清單 (as_of=%s)", as_of)
            return df
        except DataNotAvailable:
            raise
        except Exception as exc:
            raise DataNotAvailable("TWSE/t187ap03_L", str(exc), as_of=as_of) from exc

    def close(self) -> None:
        """Release connection pool resources."""
        self._http.close()

    def __enter__(self) -> TWSEClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
