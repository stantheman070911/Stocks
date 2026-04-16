"""FinMind REST API client with quota tracking, token-bucket rate limiting, and retry.

FinMind REST endpoint: https://api.finmindtrade.com/api/v4/data
Quota check endpoint:  https://api.web.finmindtrade.com/v2/user_info

Rate-limit strategy:
  - Free tier: 600 req/hour.  We stop at 95% (570) to leave headroom.
  - Retry on HTTP 402 (quota exceeded) or 429 (server-side rate limit)
    with exponential backoff up to MAX_RETRIES attempts.
  - Non-TWSE jitter applied via HttpClient.get() (0.05–0.15 s per thread).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from tw_scanner.data.base import DataNotAvailable, HttpClient, add_metadata_columns, as_of_str
from tw_scanner.data.parquet_cache import ParquetCache

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

logger = logging.getLogger(__name__)

_FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
_FINMIND_USER_INFO = "https://api.web.finmindtrade.com/v2/user_info"
_QUOTA_STOP_FRACTION = 0.95  # stop at 95% of quota
_MAX_RETRIES = 4
_RETRY_BASE_DELAY_S = 10.0  # first retry after 10 s; doubles each time


class FinMindClient:
    """FinMind API 客戶端：帶限流、配額追蹤與 Parquet 快取。"""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._http = HttpClient(cfg)
        self._cache = ParquetCache(cfg.data.cache_dir)
        self._token: str = os.environ.get("FINMIND_TOKEN", "").strip()
        self._quota_limit: int = cfg.data.finmind_quota_requests_per_hour
        self._quota_used: int = 0

    # ── 私有輔助 ────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def _check_quota(self) -> None:
        """Check remote quota and raise DataNotAvailable if too close to the limit."""
        if not self._token:
            return  # 無 token 時跳過配額查詢
        try:
            resp = self._http.get(_FINMIND_USER_INFO, headers=self._auth_headers())
            body = resp.json()
            used: int = int(body.get("user_count", 0))
            limit: int = int(body.get("api_request_limit", self._quota_limit))
            self._quota_used = used
            self._quota_limit = limit
            if used >= limit * _QUOTA_STOP_FRACTION:
                raise DataNotAvailable(
                    "FinMind",
                    f"配額接近上限 ({used}/{limit})，停止請求",
                )
        except DataNotAvailable:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("配額查詢失敗，繼續請求: %s", exc)

    def _fetch_raw(
        self,
        dataset: str,
        params: dict[str, str | int | float],
    ) -> pd.DataFrame:
        """Fetch `dataset` from the FinMind API with retry logic.  Returns a DataFrame."""
        headers = self._auth_headers()
        all_params: dict[str, str | int | float] = {"dataset": dataset, **params}
        last_exc: Exception = RuntimeError("未知錯誤")

        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._http.get(_FINMIND_API, params=all_params, headers=headers)
                body = resp.json()

                if resp.status_code in (402, 429) or body.get("status") == 402:
                    delay = _RETRY_BASE_DELAY_S * (2**attempt)
                    logger.warning(
                        "FinMind 配額/速率限制 (attempt %d/%d)，%.0fs 後重試",
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                if body.get("status") != 200:
                    msg = body.get("msg", "未知 API 錯誤")
                    raise DataNotAvailable(dataset, f"API 回傳非 200: {msg}")

                data = body.get("data", [])
                if not data:
                    return pd.DataFrame()
                return pd.DataFrame(data)

            except DataNotAvailable:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                delay = _RETRY_BASE_DELAY_S * (2**attempt)
                logger.warning(
                    "FinMind 請求失敗 (attempt %d/%d)，%.0fs 後重試: %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                    exc,
                )
                time.sleep(delay)

        raise DataNotAvailable(dataset, f"重試 {_MAX_RETRIES} 次後仍失敗: {last_exc}")

    # ── 公開 API ────────────────────────────────────────

    def fetch(
        self,
        dataset: str,
        as_of: date,
        *,
        data_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Fetch `dataset` with optional PIT cache.

        Args:
            dataset:    FinMind dataset name, e.g. "TaiwanStockPrice".
            as_of:      The point-in-time date.  Used as the cache key.
            data_id:    Optional stock_id / data_id for per-ticker queries.
            start_date: Query start date (defaults to as_of if None).
            end_date:   Query end date (defaults to as_of if None).
            use_cache:  If False, skip the cache and always fetch fresh.

        Returns:
            DataFrame with raw API columns plus standard metadata columns
            (source, dataset, retrieved_at, as_of).
        """
        cache_key = f"{dataset}/{data_id or 'all'}"

        if use_cache:
            cached = self._cache.get(cache_key, as_of)
            if cached is not None:
                return cached

        params: dict[str, str | int | float] = {}
        if data_id:
            params["data_id"] = data_id
        if start_date:
            params["start_date"] = as_of_str(start_date)
        if end_date:
            params["end_date"] = as_of_str(end_date)

        self._check_quota()
        df = self._fetch_raw(dataset, params)

        if not df.empty:
            df = add_metadata_columns(df, source="finmind", dataset=dataset, as_of=as_of)
            if use_cache:
                self._cache.put(cache_key, as_of, df)

        return df

    def fetch_no_date_params(self, dataset: str, as_of: date, *, use_cache: bool = True) -> pd.DataFrame:
        """Fetch datasets that take no date parameters (e.g. TaiwanStockInfo, TaiwanStockDelisting)."""
        if use_cache:
            cached = self._cache.get(dataset, as_of)
            if cached is not None:
                return cached

        self._check_quota()
        df = self._fetch_raw(dataset, {})

        if not df.empty:
            df = add_metadata_columns(df, source="finmind", dataset=dataset, as_of=as_of)
            if use_cache:
                self._cache.put(dataset, as_of, df)

        return df

    def close(self) -> None:
        """Release connection pool resources."""
        self._http.close()

    def __enter__(self) -> FinMindClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
