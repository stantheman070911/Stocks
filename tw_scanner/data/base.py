"""Base HTTP client, rate limiter, and universal accessor contract for tw_scanner data layer.

Design rules:
  - No global monkeypatching of requests.get.  All HTTP flows through HttpClient instances.
  - TWSE domain: serialised through a shared lock with a random delay (0.7–1.5 s).
  - All other domains: per-thread jitter (0.05–0.15 s), no shared lock.
  - Every accessor signature: (stock_ids, as_of, lookback_days, cfg) -> pd.DataFrame.
  - On unrecoverable failure: raise DataNotAvailable (never silently return empty data).
"""

from __future__ import annotations

import random
import threading
import time
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

if TYPE_CHECKING:
    from tw_scanner.config.schema import AppConfig

# ══════════════════════════════════════════════════════
# 例外
# ══════════════════════════════════════════════════════


class DataNotAvailable(Exception):
    """Raised when a data accessor cannot return any usable data.

    The pipeline catches this at the stage boundary and records the failure
    in data_quality.csv rather than aborting the run.
    """

    def __init__(self, dataset: str, reason: str, *, as_of: date | None = None) -> None:
        self.dataset = dataset
        self.reason = reason
        self.as_of = as_of
        super().__init__(f"[{dataset}] {reason}" + (f" (as_of={as_of})" if as_of else ""))


# ══════════════════════════════════════════════════════
# 速率限制器
# ══════════════════════════════════════════════════════


class RateLimiter:
    """Domain-aware per-thread rate limiter.  TWSE uses a shared lock; others use per-thread jitter."""

    _twse_lock: threading.Lock = threading.Lock()
    _TWSE_DOMAINS = ("twse.com.tw", "openapi.twse.com.tw")

    def __init__(self, cfg: AppConfig) -> None:
        self._twse_min = cfg.data.twse_throttle_min_s
        self._twse_max = cfg.data.twse_throttle_max_s
        self._other_min = cfg.data.other_throttle_min_s
        self._other_max = cfg.data.other_throttle_max_s

    @staticmethod
    def _is_twse(url: str) -> bool:
        return any(d in url for d in RateLimiter._TWSE_DOMAINS)

    def throttle(self, url: str) -> None:
        """Sleep for the appropriate duration before making a request to `url`."""
        if self._is_twse(url):
            with self._twse_lock:
                time.sleep(random.uniform(self._twse_min, self._twse_max))
        else:
            time.sleep(random.uniform(self._other_min, self._other_max))


# ══════════════════════════════════════════════════════
# HTTP 客戶端
# ══════════════════════════════════════════════════════


class HttpClient:
    """Thread-safe HTTP client with connection pooling and domain-aware rate limiting.

    Uses a shared requests.Session (pool size 32) for connection reuse.
    Rate limiting is applied before each request via RateLimiter.throttle().
    """

    _DEFAULT_TIMEOUT = 30  # seconds

    def __init__(self, cfg: AppConfig, *, pool_size: int = 32) -> None:
        self._rate_limiter = RateLimiter(cfg)
        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def get(
        self,
        url: str,
        *,
        params: dict[str, str | int | float] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> requests.Response:
        """Throttle, then GET.  Returns the Response; caller checks status."""
        self._rate_limiter.throttle(url)
        return self._session.get(url, params=params, headers=headers, timeout=timeout)

    def close(self) -> None:
        """Release connection pool resources."""
        self._session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ══════════════════════════════════════════════════════
# 通用輔助函數
# ══════════════════════════════════════════════════════


def utc_now() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(tz=UTC)


def as_of_str(d: date) -> str:
    """Format a date as YYYY-MM-DD for API calls."""
    return d.strftime("%Y-%m-%d")


def add_metadata_columns(
    df: pd.DataFrame,
    *,
    source: str,
    dataset: str,
    as_of: date,
) -> pd.DataFrame:
    """Attach standard provenance columns to an accessor DataFrame.

    Every accessor result must carry these columns so the pipeline can
    emit data_quality.csv with source, staleness, and retrieval metadata.
    """
    df = df.copy()
    df["source"] = source
    df["dataset"] = dataset
    df["retrieved_at"] = pd.Timestamp(utc_now())
    df["as_of"] = pd.Timestamp(as_of)
    return df
