"""PIT-keyed parquet cache for immutable historical data and daily refreshes.

Cache layout:
  {cache_dir}/{dataset}/{as_of_str}/data.parquet

TTL rules:
  - as_of < today  → cached forever (historical data is immutable).
  - as_of == today → 1-day TTL (data may be updated during market hours).
  - as_of > today  → not cached (invalid / future).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

_TODAY_TTL = timedelta(hours=24)


class ParquetCache:
    """讀寫點對時間（PIT）分區的 Parquet 快取。"""

    def __init__(self, cache_dir: str | Path) -> None:
        self._root = Path(cache_dir)

    # ── 路徑邏輯 ────────────────────────────────────────

    def _path(self, dataset: str, as_of: date) -> Path:
        return self._root / dataset / as_of.strftime("%Y-%m-%d") / "data.parquet"

    @staticmethod
    def _is_valid(path: Path, as_of: date) -> bool:
        """Return True if cached data at `path` is still fresh for `as_of`."""
        if not path.exists():
            return False
        today = date.today()
        if as_of < today:
            return True  # 歷史資料永久有效
        # as_of == today: check write time < 24 h
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        age = datetime.now(tz=UTC) - mtime
        return age < _TODAY_TTL

    # ── 公開 API ────────────────────────────────────────

    def get(self, dataset: str, as_of: date) -> pd.DataFrame | None:
        """Return cached DataFrame if present and fresh; None otherwise."""
        path = self._path(dataset, as_of)
        if not self._is_valid(path, as_of):
            return None
        try:
            df: pd.DataFrame = pq.read_table(path).to_pandas()
            logger.debug("快取命中: dataset=%s as_of=%s", dataset, as_of)
            return df
        except Exception as exc:  # noqa: BLE001
            logger.warning("快取讀取失敗，將重新抓取: %s — %s", path, exc)
            return None

    def put(self, dataset: str, as_of: date, df: pd.DataFrame) -> None:
        """Write `df` to the cache.  Creates parent directories as needed."""
        if as_of > date.today():
            logger.debug("跳過快取寫入：未來日期 as_of=%s", as_of)
            return
        path = self._path(dataset, as_of)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            table = pa.Table.from_pandas(df, preserve_index=False)
            pq.write_table(table, path, compression="snappy")
            logger.debug("快取寫入: dataset=%s as_of=%s rows=%d", dataset, as_of, len(df))
        except Exception as exc:  # noqa: BLE001
            logger.warning("快取寫入失敗: %s — %s", path, exc)

    def invalidate(self, dataset: str, as_of: date) -> None:
        """Delete a cached file so the next read forces a fresh fetch."""
        path = self._path(dataset, as_of)
        if path.exists():
            path.unlink()
            logger.debug("快取失效: %s", path)

    def exists(self, dataset: str, as_of: date) -> bool:
        """Return True if a valid (fresh) cache entry exists."""
        return self._is_valid(self._path(dataset, as_of), as_of)
