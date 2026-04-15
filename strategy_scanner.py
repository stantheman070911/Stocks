"""
台股波段選股系統 v1.0
策略：外資買超中後段排名 + 技術面 + 融資融券 + 基本面 + 風險指標
Python 3.12+ | 執行前確認已安裝：pip install lxml openpyxl finmind
"""

import warnings
warnings.filterwarnings("ignore")

import argparse
import json
import logging
import random
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
import pandas as pd
import numpy as np
import yfinance as yf
from tqdm import tqdm

# ────────────────────────────────────────────────────────────────
# 技術指標：優先使用 pandas_ta，若未安裝則 fallback 至內建計算
# （pandas_ta 在 Python < 3.12 已從 PyPI 撤下，此 fallback 保障可執行）
# ────────────────────────────────────────────────────────────────
try:
    import pandas_ta as _ta  # noqa: F401
    _HAS_TA = True
except Exception:
    # 可能的失敗：未安裝 / numpy 2.x 相容性（np.NaN 被移除）/ setuptools 版本
    _HAS_TA = False


def _stoch_fallback(high: pd.Series, low: pd.Series, close: pd.Series,
                    k: int = 9, d: int = 3, smooth_k: int = 3) -> pd.DataFrame:
    """KD 隨機指標（%K/%D）內建實作。回傳兩欄 DataFrame。"""
    ll = low.rolling(k).min()
    hh = high.rolling(k).max()
    denom = (hh - ll).replace(0, np.nan)
    fast_k = 100 * (close - ll) / denom
    slow_k = fast_k.rolling(smooth_k).mean()
    slow_d = slow_k.rolling(d).mean()
    return pd.DataFrame({
        f"STOCHk_{k}_{d}_{smooth_k}": slow_k,
        f"STOCHd_{k}_{d}_{smooth_k}": slow_d,
    })


def _stoch(high, low, close, k=9, d=3, smooth_k=3):
    """優先走 pandas_ta，否則使用 fallback。"""
    if _HAS_TA:
        try:
            return _ta.stoch(high, low, close, k=k, d=d, smooth_k=smooth_k)
        except Exception as e:
            log.debug("pandas_ta.stoch 失敗，改用 fallback：%s", e)
    return _stoch_fallback(high, low, close, k=k, d=d, smooth_k=smooth_k)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = [
    "PingFang TC",
    "Heiti TC",
    "Microsoft JhengHei",
    "Noto Sans CJK TC",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

# ════════════════════════════════════════════════════════════════
#  ▌ Logging — 取代 print 中的錯誤訊息，保留管線進度輸出
# ════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("stocks")

# ════════════════════════════════════════════════════════════════
#  ▌ HTTP / 執行設定
# ════════════════════════════════════════════════════════════════

# FinMind（可選，用於基本面）
try:
    from finmind.data import DataLoader as FMLoader
    FINMIND_OK = True
except ImportError:
    FINMIND_OK = False

# ════════════════════════════════════════════════════════════════
#  ▌ CONFIG — 只需修改這區
# ════════════════════════════════════════════════════════════════

FINMIND_TOKEN = "REMOVED_FINMIND_TOKEN"  # finmindtrade.com 免費註冊後填入（可空白，跳過基本面）
LOOKUP_DAYS   = 30                               # 外資統計天數
LOOKBACK_DAYS = 730                              # 價量回看天數
BATCH_SIZE    = 50                               # yfinance 批次下載檔數

OUTPUT_DIR    = Path("strategy_output")
CACHE_DIR     = Path(".cache")

# 外資排名百分位篩選（0=第一名, 1=最後）
RANK_LOW  = 0.25    # 前25%以後（排除已被拉抬注意）
RANK_HIGH = 0.80    # 前80%以前（確保仍有資金流入）
MIN_CONSEC_BUY = 5  # 最少連續外資買超天數

MA_PERIOD          = 20
TOP_INDUSTRY_COUNT = 5    # 產業別成交量前N名
TARGET             = 200  # 輸出目標筆數
MAX_WORKERS        = 15   # 平行下載執行緒

# 金融股排除關鍵字
FIN_KW = ["金融", "銀行", "保險", "證券", "票券", "投信", "期貨", "壽險", "產險", "租賃"]
FIN_KW_PATTERN = re.compile("|".join(map(re.escape, FIN_KW)))
TWSE_SOURCE_LOOKBACK = 45

# BFIAMU 類股名稱（去除「指數」後綴）→ TWSE t187ap03_L 產業別代碼
# 修正 FIND-2：top_ind 是中文類別名稱，stock_df["sector"] 是數字代碼，
# 直接做子字串比對永遠 False；改用此對照表轉換為代碼集合再比對。
BFIAMU_TO_TWSE_CODES: dict[str, set[str]] = {
    "水泥工業":         {"01"},
    "食品工業":         {"02"},
    "塑膠工業":         {"03"},
    "紡織纖維":         {"04"},
    "電機機械":         {"05"},
    "電器電纜":         {"06"},
    "化學工業":         {"07", "21"},
    "生技醫療業":       {"22"},
    "生技醫療":         {"22"},
    "玻璃陶瓷":         {"08"},
    "造紙工業":         {"09"},
    "鋼鐵工業":         {"10"},
    "橡膠工業":         {"11"},
    "汽車工業":         {"12"},
    # 電子工業（BFIAMU 廣義類）涵蓋 t187ap03_L 的舊電子代碼及所有電子子板塊
    "電子工業":         {"13", "24", "25", "26", "27", "28", "29", "30", "31"},
    "建材營造":         {"14"},
    "航運業":           {"15"},
    "航運":             {"15"},
    "觀光事業":         {"16"},
    "觀光":             {"16"},
    "金融保險業":       {"17"},
    "金融保險":         {"17"},
    "貿易百貨":         {"18"},
    "油電燃氣業":       {"23"},
    "油電燃氣":         {"23"},
    "半導體業":         {"24"},
    "半導體":           {"24"},
    "電腦及周邊設備業": {"25"},
    "電腦及周邊設備":   {"25"},
    "光電業":           {"26"},
    "光電":             {"26"},
    "通信網路業":       {"27"},
    "通信網路":         {"27"},
    "電子零組件業":     {"28"},
    "電子零組件":       {"28"},
    "電子通路業":       {"29"},
    "電子通路":         {"29"},
    "資訊服務業":       {"30"},
    "資訊服務":         {"30"},
    "其他電子業":       {"31"},
    "其他電子":         {"31"},
    "文化創意業":       {"32"},
    "文化創意":         {"32"},
    "農業科技業":       {"33"},
    "農業科技":         {"33"},
    "電子商務業":       {"34"},
    "電子商務":         {"34"},
    "綠能環保":         {"35"},
    "數位雲端":         {"36"},
    "運動休閒":         {"37"},
    "居家生活":         {"38"},
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

# 風險指標常數（集中，避免在函式內硬編碼）
RISK_FREE_RATE     = 0.015          # 年化無風險利率
TRADING_DAYS_YEAR  = 252
BENCHMARK_TICKER   = "0050.TW"     # 台灣50 ETF — 大盤代理
MIN_RISK_OBS       = 60            # 計算風險指標所需最少觀察值
MIN_RISK_RET_OBS   = max(MIN_RISK_OBS - 1, 1)


@dataclass(frozen=True)
class RunConfig:
    """單次執行設定。"""
    as_of: date
    output_dir: Path = OUTPUT_DIR
    cache_dir: Path = CACHE_DIR
    lookback_days: int = LOOKBACK_DAYS

    @property
    def start_date(self) -> date:
        return self.as_of - timedelta(days=self.lookback_days)

    @property
    def end_exclusive(self) -> date:
        return self.as_of + timedelta(days=1)


class Throttle:
    """節流器。"""

    def __init__(self, lo: float, hi: float, lock: "threading.Lock | None" = None):
        self.lo = lo
        self.hi = hi
        self.lock = lock

    def wait(self) -> None:
        if self.lock is None:
            if self.hi > 0:
                time.sleep(random.uniform(self.lo, self.hi))
            return

        with self.lock:
            time.sleep(random.uniform(self.lo, self.hi))


class HttpClient:
    """帶節流與重試的 JSON client。"""

    def __init__(self, throttle: Throttle, timeout: int = 15, retries: int = 3):
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=0)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.throttle = throttle
        self.timeout = timeout
        self.retries = retries

    def get_json(self, url: str, params: dict | None = None) -> dict | list | None:
        last_err: Exception | None = None
        for i in range(self.retries):
            try:
                self.throttle.wait()
                r = self.session.get(url, params=params, headers=HEADERS, timeout=self.timeout)
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as e:
                        last_err = e
                        log.debug("JSON 解析失敗 %s: %s", url, e)
                else:
                    log.debug("HTTP %s 於 %s", r.status_code, url)
            except requests.RequestException as e:
                last_err = e
                log.debug("請求失敗 %s（第 %d 次）：%s", url, i + 1, e)
            time.sleep(1.5 * (2 ** i) + random.uniform(0, 0.5))
        if last_err:
            log.debug("放棄 %s：%s", url, last_err)
        return None


TWSE_HTTP = HttpClient(Throttle(0.7, 1.5, threading.Lock()))

# ════════════════════════════════════════════════════════════════
#  ▌ UTILITIES
# ════════════════════════════════════════════════════════════════

def parse_args() -> RunConfig:
    """解析 CLI 參數。"""
    parser = argparse.ArgumentParser(description="台股波段選股系統")
    parser.add_argument("--as-of", dest="as_of", help="指定執行基準日（YYYY-MM-DD）")
    args = parser.parse_args()

    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else datetime.today().date()
    return RunConfig(as_of=as_of)


def _cache_file(cfg: RunConfig, namespace: str, key: str, suffix: str) -> Path:
    """組合快取路徑。"""
    safe_key = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in key)
    return cfg.cache_dir / namespace / f"{safe_key}.{suffix}"


def read_json_cache(cfg: RunConfig, namespace: str, key: str) -> dict | list | None:
    """讀取 JSON 快取。"""
    path = _cache_file(cfg, namespace, key, "json")
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.debug("讀取 JSON 快取失敗 %s: %s", path, e)
        return None


def write_json_cache(cfg: RunConfig, namespace: str, key: str, data: dict | list) -> None:
    """寫入 JSON 快取。"""
    path = _cache_file(cfg, namespace, key, "json")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log.debug("寫入 JSON 快取失敗 %s: %s", path, e)


def read_frame_cache(cfg: RunConfig, namespace: str, key: str) -> pd.DataFrame | None:
    """讀取 DataFrame 快取。"""
    path = _cache_file(cfg, namespace, key, "pkl")
    if not path.exists():
        return None
    try:
        cached = pd.read_pickle(path)
        return cached if isinstance(cached, pd.DataFrame) and not cached.empty else None
    except (OSError, ValueError, TypeError) as e:
        log.debug("讀取 DataFrame 快取失敗 %s: %s", path, e)
        return None


def write_frame_cache(cfg: RunConfig, namespace: str, key: str, df: pd.DataFrame) -> None:
    """寫入 DataFrame 快取。"""
    path = _cache_file(cfg, namespace, key, "pkl")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_pickle(path)
    except OSError as e:
        log.debug("寫入 DataFrame 快取失敗 %s: %s", path, e)


# 台股已知休市日（補班日則不列入）。
# TWSE 會在休市日對 T86/BFIAMU/MI_MARGN 回傳 stat != "OK"，
# 因此加入此表後，recent_weekdays 就能直接跳過，減少不必要的 API 往返。
# 如遇未涵蓋的假日，各呼叫端的 reason != "ok" 迴圈仍會正確略過。
# 資料來源：TWSE 休市公告 / 中華民國年曆（2023–2027）
TW_MARKET_HOLIDAYS: frozenset[date] = frozenset([
    # 2023
    date(2023, 1,  2), date(2023, 1, 20), date(2023, 1, 23),
    date(2023, 1, 24), date(2023, 1, 25), date(2023, 1, 26), date(2023, 1, 27),
    date(2023, 2, 27), date(2023, 2, 28),
    date(2023, 4,  3), date(2023, 4,  4), date(2023, 4,  5),
    date(2023, 6, 22), date(2023, 6, 23),
    date(2023, 9, 29),
    date(2023, 10, 9), date(2023, 10, 10),
    # 2024
    date(2024, 1,  1),
    date(2024, 2,  8), date(2024, 2,  9), date(2024, 2, 12),
    date(2024, 2, 13), date(2024, 2, 14),
    date(2024, 2, 28),
    date(2024, 4,  4), date(2024, 4,  5),
    date(2024, 6, 10),
    date(2024, 9, 17),
    date(2024, 10, 10),
    # 2025
    date(2025, 1,  1),
    date(2025, 1, 27), date(2025, 1, 28), date(2025, 1, 29),
    date(2025, 1, 30), date(2025, 1, 31),
    date(2025, 2, 28),
    date(2025, 4,  3), date(2025, 4,  4),
    date(2025, 5,  1),
    date(2025, 5, 30), date(2025, 5, 31),
    date(2025, 10, 6), date(2025, 10, 10),
    # 2026
    date(2026, 1,  1), date(2026, 1,  2),
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20),
    date(2026, 2, 27), date(2026, 2, 28),
    date(2026, 4,  3),
    date(2026, 6, 19),
    date(2026, 9, 25),
    date(2026, 10, 9), date(2026, 10, 10),
    # 2027
    date(2027, 1,  1),
    date(2027, 2,  5), date(2027, 2,  8), date(2027, 2,  9),
    date(2027, 2, 10), date(2027, 2, 11),
    date(2027, 2, 26), date(2027, 2, 28),
    date(2027, 4,  5),
    date(2027, 5, 28),
    date(2027, 10, 11),
])


def recent_weekdays(as_of: date, n: int = 50) -> list[str]:
    """回傳最近 n 個台股交易日字串（YYYYMMDD），由新到舊。
    跳過週末及 TW_MARKET_HOLIDAYS 中已知休市日。
    未涵蓋的假日由各呼叫端的 reason != 'ok' 迴圈兜底略過。
    """
    result, d = [], as_of
    while len(result) < n:
        if d.weekday() < 5 and d not in TW_MARKET_HOLIDAYS:
            result.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return result


def _twse_payload_reason(data: dict | list | None, *, payload_keys: tuple[str, ...] = ("data",)) -> str:
    """判讀 TWSE 回應狀態。"""
    if not data or not isinstance(data, dict):
        return "http_fail"
    stat_reason = _foreign_fetch_reason(data.get("stat"))
    if stat_reason != "ok":
        return stat_reason
    return "ok" if any(data.get(key) for key in payload_keys) else "empty"


def safe_get(url: str, cfg: RunConfig, params: dict | None = None,
             cache_namespace: str | None = None, cache_key: str | None = None) -> dict | list | None:
    """TWSE JSON 讀取：優先讀快取，失敗則走網路並回寫快取。"""
    if cache_namespace and cache_key:
        cached = read_json_cache(cfg, cache_namespace, cache_key)
        if cached is not None:
            return cached

    data = TWSE_HTTP.get_json(url, params=params)
    if data is not None and cache_namespace and cache_key:
        write_json_cache(cfg, cache_namespace, cache_key, data)
    return data


def to_numeric_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace(",", "").str.replace(" ", ""),
        errors="coerce"
    )


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 1 ▸ 上市股票清單
# ════════════════════════════════════════════════════════════════

def _normalize_col_name(name: str) -> str:
    """標準化欄位名稱，降低格式差異影響。"""
    return re.sub(r"[\s_:/()（）-]+", "", str(name)).lower()


def _find_col(columns, exact_keywords: list[str], fuzzy_keywords: list[str] | None = None) -> str | None:
    """優先精準命中欄位名稱，其次才做模糊比對。"""
    norm_cols = {col: _normalize_col_name(col) for col in columns}
    exact_targets = {_normalize_col_name(k) for k in exact_keywords}
    exact_hits = [col for col, norm in norm_cols.items() if norm in exact_targets]
    if exact_hits:
        if len(exact_hits) > 1:
            log.warning("欄位精準命中多個候選 %s → 採用 %s", exact_hits, exact_hits[0])
        return exact_hits[0]

    fuzzy_targets = [_normalize_col_name(k) for k in (fuzzy_keywords or [])]
    if not fuzzy_targets:
        return None

    fuzzy_hits = [
        col for col, norm in norm_cols.items()
        if any(target in norm for target in fuzzy_targets)
    ]
    if len(fuzzy_hits) > 1:
        log.warning("欄位模糊命中多個候選 %s → 採用 %s", fuzzy_hits, fuzzy_hits[0])
    return fuzzy_hits[0] if fuzzy_hits else None


def get_stock_list(cfg: RunConfig) -> tuple[pd.DataFrame, str | None]:
    print("═" * 60)
    print("【Step 1】取得上市股票清單")
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    data = safe_get(
        url,
        cfg,
        cache_namespace="twse/t187ap03_L",
        cache_key=cfg.as_of.strftime("%Y%m%d"),
    )
    if not data:
        log.warning("TWSE 股票清單 API 無回應，且快取不可用")
        empty = pd.DataFrame(columns=["id", "name", "sector", "capital"])
        return empty, None

    df = pd.DataFrame(data)
    print(f"  API 原始欄位：{list(df.columns)}")   # 除錯用：印出實際欄位名

    # ── 關鍵字模糊偵測欄位（不受 TWSE 改欄名影響）────────────────
    id_col = _find_col(
        df.columns,
        ["證券代號", "有價證券代號", "公司代號", "股票代號", "代號", "代碼"],
        ["code"],
    )
    name_col = _find_col(
        df.columns,
        ["公司名稱", "股票名稱", "名稱"],
        ["name"],
    )
    sector_col = _find_col(
        df.columns,
        ["產業別", "產業類別", "類股類別", "類股", "產業"],
        ["industry", "sector"],
    )
    capital_col = _find_col(
        df.columns,
        ["實收資本額", "資本總額", "資本額", "資本"],
        ["capital"],
    )

    rename_map = {}
    if id_col:      rename_map[id_col]      = "id"
    if name_col:    rename_map[name_col]    = "name"
    if sector_col:  rename_map[sector_col]  = "sector"
    if capital_col: rename_map[capital_col] = "capital"

    df = df.rename(columns=rename_map)

    # 補齊可能缺少的欄位，避免後續 KeyError
    for col, default in [("id", ""), ("name", ""), ("sector", ""), ("capital", np.nan)]:
        if col not in df.columns:
            print(f"  ⚠ 欄位 '{col}' 未在 API 回傳中找到，以空值補齊")
            df[col] = default

    # 只保留4碼純數字股票（排除ETF、權證、TDR）
    df["id"] = df["id"].astype(str).str.strip()
    df = df[df["id"].str.match(r"^\d{4}$", na=False)].copy()

    # 排除金融股
    # 修正：TWSE t187ap03_L API 的 `產業別` 是「數字代碼」（如 '17' = 金融保險業），
    # 不是中文名稱；原程式用 FIN_KW 比對數字欄位，結果一檔都濾不掉。
    # 改用「公司名稱」做關鍵字比對，並額外以 TWSE 產業代碼 17（金融保險業）兜底。
    FIN_SECTOR_CODES = {"17"}
    before = len(df)
    df["sector"] = df["sector"].fillna("").astype(str)
    df["name"]   = df["name"].fillna("").astype(str)
    name_has_fin = df["name"].str.contains(FIN_KW_PATTERN, regex=True, na=False)
    sector_is_fin = df["sector"].isin(FIN_SECTOR_CODES)
    df = df[~(name_has_fin | sector_is_fin)].copy()
    print(f"  上市總數 {before} → 排除金融後 {len(df)} 檔")

    return df[["id", "name", "sector", "capital"]].reset_index(drop=True), cfg.as_of.isoformat()


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 2 ▸ 外資30日買超排行（逐日爬取累加）
# ════════════════════════════════════════════════════════════════

def _foreign_fetch_reason(stat: str | None) -> str:
    """判讀 T86 失敗原因。"""
    text = str(stat or "").strip().upper()
    if not text:
        return "empty"
    if any(k in text for k in ["OK", "SUCCESS"]):
        return "ok"
    if any(k in text for k in ["很抱歉", "查無資料", "休市", "NO DATA", "HOLIDAY"]):
        return "holiday"
    return "http_fail"


def _fetch_foreign_day(date_str: str, cfg: RunConfig) -> tuple[str, pd.DataFrame | None]:
    """取得單日所有個股外資買賣超（TWSE T86 — 三大法人買賣超日報）。
    舊版 TWT53U 端點 TWSE 已下線並改導向 HTML 首頁，改用 T86。
    """
    data = safe_get(
        "https://www.twse.com.tw/fund/T86",
        cfg,
        params={"response": "json", "date": date_str, "selectType": "ALLBUT0999"},
        cache_namespace="twse/T86",
        cache_key=date_str,
    )
    if not data:
        return "http_fail", None
    if not isinstance(data, dict):
        return "http_fail", None
    if data.get("stat") != "OK" or not data.get("data"):
        return _foreign_fetch_reason(data.get("stat")), None

    cols  = [h.strip() for h in data["fields"]]
    df    = pd.DataFrame(data["data"], columns=cols)
    id_c  = next((c for c in cols if "代號" in c or "代碼" in c), None)
    net_c = next((c for c in cols if "買賣超" in c and "股" in c), None)
    if not id_c or not net_c:
        return "schema_fail", None

    df = df[[id_c, net_c]].rename(columns={id_c: "id", net_c: f"d{date_str}"})
    df["id"]         = df["id"].astype(str).str.strip()
    df[f"d{date_str}"] = to_numeric_series(df[f"d{date_str}"])
    return "ok", df.set_index("id")


def _count_consecutive_positive(row: pd.Series) -> int:
    """計算由近到遠的連續買超天數（忽略未上市缺值）。"""
    count = 0
    for value in row.tolist():
        if pd.isna(value):
            continue
        if value > 0:
            count += 1
            continue
        break
    return count


def get_foreign_ranking(valid_ids: set, cfg: RunConfig) -> tuple[pd.DataFrame, dict]:
    print("\n【Step 2】爬取外資近30日買超排行")
    dates = recent_weekdays(cfg.as_of, max(LOOKUP_DAYS + 35, LOOKUP_DAYS * 3))
    frames = []
    ok_dates: list[str] = []
    fetch_stats: Counter[str] = Counter()
    for date_str in tqdm(dates, total=len(dates), desc="  外資日資料", ncols=72):
        reason, res = _fetch_foreign_day(date_str, cfg)
        fetch_stats[reason] += 1
        if res is not None:
            frames.append(res)
            ok_dates.append(date_str)
        if len(frames) >= LOOKUP_DAYS:
            break

    if not frames:
        log.warning("外資資料全部取得失敗，請稍後再試")
        return pd.DataFrame(columns=["id", "cum_net", "consec_buy", "rank", "rank_pct"]), {
            "ok_dates": ok_dates,
            "fetch_stats": dict(fetch_stats),
        }

    master = pd.concat(frames, axis=1)
    day_cols = sorted([c for c in master.columns if c.startswith("d")], reverse=True)  # 新→舊

    # FIND-6：強制 float dtype，避免 object 欄位在 > 0 比較時靜默強制轉換
    # _fetch_foreign_day 已對每欄執行 to_numeric_series，但 concat 後個別欄若全為
    # NaN 有機會落為 object；此處統一轉換，確保 cum_net / consec_buy 計算正確。
    master[day_cols] = master[day_cols].apply(pd.to_numeric, errors="coerce")

    # 累計買超（萬股）
    master["obs_days"] = master[day_cols].notna().sum(axis=1).astype(int)
    master["cum_net"] = master[day_cols].sum(axis=1, skipna=True) / 10000

    # 連續買超天數（從最近有效交易日往前數，忽略缺值）
    master["consec_buy"] = master[day_cols].apply(_count_consecutive_positive, axis=1).astype(int)

    # ── 排名在全 T86 宇宙上計算（修正 FIND-15）─────────────────────
    # 原程式先過濾 valid_ids 再排名，導致 rank_pct 僅反映
    # 已排除金融股的子集，而非整體市場的外資偏好。
    # 改為先排名（含所有 T86 個股），再過濾合法個股，
    # 使 RANK_LOW/RANK_HIGH 的含義與「全市場排名百分位」一致。
    universe_size = len(master)
    master = master.sort_values("cum_net", ascending=False)
    master["rank"]     = range(1, len(master) + 1)
    master["rank_pct"] = master["rank"] / len(master)

    # 只保留合法個股（過濾後不重新計算排名）
    filt_valid = master[master.index.isin(valid_ids)]

    # 篩選條件：中後段排名 + 連續買超達標
    filt = filt_valid[
        (filt_valid["rank_pct"] >= RANK_LOW) &
        (filt_valid["rank_pct"] <= RANK_HIGH) &
        (filt_valid["consec_buy"] >= MIN_CONSEC_BUY)
    ].copy()

    print(
        f"  T86全宇宙 {universe_size} 檔 → 合法個股 {len(filt_valid)} 檔 → "
        f"排名中後段({RANK_LOW*100:.0f}%~{RANK_HIGH*100:.0f}%) + 連買≥{MIN_CONSEC_BUY}天 → {len(filt)} 檔"
    )
    return (
        filt[["cum_net", "consec_buy", "rank", "rank_pct"]].reset_index().rename(columns={"index": "id"}),
        {"ok_dates": ok_dates, "fetch_stats": dict(fetch_stats), "universe_size": universe_size},
    )


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 3 ▸ 下載價量資料（平行）
# ════════════════════════════════════════════════════════════════

def _yf_symbol(ticker: str) -> str:
    """補齊台股 ticker 尾碼。"""
    return ticker if "." in ticker else f"{ticker}.TW"


def _normalize_yf_frame(raw: pd.DataFrame, min_rows: int = 60) -> pd.DataFrame | None:
    """清理 yfinance 回傳欄位。"""
    if raw is None or raw.empty or len(raw) < min_rows:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(-1)
    need = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in raw.columns]
    if "Close" not in need:
        return None
    frame = raw[need].dropna(how="all")
    return frame if len(frame) >= min_rows else None


def _download_single_price(ticker: str, cfg: RunConfig, min_rows: int = 60, retries: int = 2) -> pd.DataFrame | None:
    """單檔 fallback 下載。"""
    for i in range(retries + 1):
        try:
            raw = yf.download(
                tickers=_yf_symbol(ticker),
                start=cfg.start_date,
                end=cfg.end_exclusive,
                progress=False,
                auto_adjust=True,
                timeout=20,
            )
            frame = _normalize_yf_frame(raw, min_rows=min_rows)
            if frame is not None:
                return frame
        # FIND-11：使用明確例外類型（yfinance 底層是 requests，
        # 加上 ValueError/KeyError 覆蓋 JSON 解析與欄位缺失情況）
        except (OSError, requests.RequestException, ValueError,
                KeyError, AttributeError, TypeError) as e:
            log.debug("yf.download(%s) 第 %d 次失敗 [%s]：%s",
                      ticker, i + 1, type(e).__name__, e)
        time.sleep(0.8 * (i + 1))
    return None


def download_prices(tickers: list[str], cfg: RunConfig) -> tuple[dict[str, pd.DataFrame], dict]:
    print(f"\n【Step 3】批次下載 {len(tickers)} 檔價量資料")
    out = {}
    missing: list[str] = []
    cached = 0
    as_of_key = cfg.as_of.strftime("%Y%m%d")

    for ticker in tickers:
        cache_key = f"{ticker}_{as_of_key}"
        cached_df = read_frame_cache(cfg, "yf", cache_key)
        normalized = _normalize_yf_frame(cached_df, min_rows=60) if cached_df is not None else None
        if normalized is not None:
            out[ticker] = normalized
            cached += 1
        else:
            missing.append(ticker)

    total_batches = (len(missing) + BATCH_SIZE - 1) // BATCH_SIZE
    for idx in tqdm(range(0, len(missing), BATCH_SIZE), total=total_batches, desc="  下載中", ncols=72):
        batch = missing[idx:idx + BATCH_SIZE]
        symbols = [_yf_symbol(t) for t in batch]
        try:
            raw = yf.download(
                tickers=" ".join(symbols),
                start=cfg.start_date,
                end=cfg.end_exclusive,
                progress=False,
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                timeout=20,
            )
        # FIND-11：明確例外類型（與 _download_single_price 保持一致）
        except (OSError, requests.RequestException, ValueError,
                KeyError, AttributeError, TypeError) as e:
            log.warning("yfinance 批次失敗（%d 檔）[%s]：%s",
                        len(batch), type(e).__name__, e)
            raw = pd.DataFrame()

        for ticker, symbol in zip(batch, symbols):
            sub = None
            if raw is not None and not raw.empty:
                try:
                    sub = raw[symbol] if isinstance(raw.columns, pd.MultiIndex) else raw
                except KeyError:
                    sub = None
            frame = _normalize_yf_frame(sub, min_rows=60) if sub is not None else None
            if frame is None:
                frame = _download_single_price(ticker, cfg, min_rows=60)
            if frame is not None:
                out[ticker] = frame
                write_frame_cache(cfg, "yf", f"{ticker}_{as_of_key}", frame)

    print(f"  成功 {len(out)} 檔 / 失敗 {len(tickers)-len(out)} 檔（快取 {cached} 檔）")
    return out, {"requested": len(tickers), "downloaded": len(out), "cached": cached}


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 4 ▸ 技術面分析
# ════════════════════════════════════════════════════════════════

def analyze_tech(t: str, df: pd.DataFrame) -> dict | None:
    """
    計算：MA趨勢 / KD / Fibonacci / 量比
    回傳技術面分數（滿分 25）及各指標
    """
    need = {"Close", "High", "Low", "Volume"}
    missing = need.difference(df.columns)
    if missing:
        log.warning("%s 技術分析缺欄位：%s", t, ",".join(sorted(missing)))
        return None
    if len(df) < 30:
        return None

    close = df["Close"]
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()
    vol_ma20 = df["Volume"].rolling(20).mean()

    if pd.isna(ma20.iloc[-1]):
        return None

    last_close = float(close.iloc[-1])
    last_ma20 = float(ma20.iloc[-1])
    last_ma60 = float(ma60.iloc[-1]) if pd.notna(ma60.iloc[-1]) else np.nan

    above_ma20 = int(last_close > last_ma20)
    ma20_prev = ma20.shift(5).iloc[-1]
    ma60_prev = ma60.shift(10).iloc[-1]
    ma20_up = int(pd.notna(ma20_prev) and ma20.iloc[-1] > ma20_prev)
    ma60_up = int(pd.notna(ma60.iloc[-1]) and pd.notna(ma60_prev) and ma60.iloc[-1] > ma60_prev)
    days_on_ma20 = int((close.iloc[-20:] > ma20.iloc[-20:]).fillna(False).sum())

    stoch = _stoch(df["High"], df["Low"], close, k=9, d=3, smooth_k=3)
    if stoch is not None and not stoch.empty:
        k_val = stoch.iloc[-1, 0]
        d_val = stoch.iloc[-1, 1]
        K = float(k_val) if pd.notna(k_val) else 50.0
        D = float(d_val) if pd.notna(d_val) else 50.0
    else:
        K = D = 50.0
    kd_golden = int(K > D and K < 80)

    seg = df.tail(60)
    s_hi = float(seg["High"].max())
    s_lo = float(seg["Low"].min())
    # D16：明確排除 NaN（高/低點若因全 NaN 欄而為 nan，運算會傳播 NaN）
    rng = (s_hi - s_lo) if (pd.notna(s_hi) and pd.notna(s_lo)) else 0.0
    fib50 = s_hi - 0.500 * rng if rng > 0 else last_close
    fib38 = s_hi - 0.382 * rng if rng > 0 else last_close
    fib62 = s_hi - 0.618 * rng if rng > 0 else last_close

    near_fib50 = int(rng > 0 and fib50 > 0 and abs(last_close - fib50) / fib50 < 0.025)
    ret_from_low = (last_close - s_lo) / s_lo if s_lo > 0 else 0.0
    minor_lift = int(0.05 <= ret_from_low <= 0.45)
    last_vol_ma20 = vol_ma20.iloc[-1]
    vol_ratio = float(df["Volume"].iloc[-1] / last_vol_ma20) if pd.notna(last_vol_ma20) and last_vol_ma20 > 0 else 1.0

    score = (
        above_ma20 * 5 +
        ma20_up * 5 +
        ma60_up * 3 +
        kd_golden * 4 +
        near_fib50 * 3 +
        minor_lift * 3 +
        min(days_on_ma20 // 5, 2)
    )

    return {
        "ticker": t,
        "close": round(last_close, 2),
        "ma20": round(last_ma20, 2),
        "ma60": round(last_ma60, 2) if not np.isnan(last_ma60) else np.nan,
        "ma120": round(float(ma120.iloc[-1]), 2) if pd.notna(ma120.iloc[-1]) else np.nan,
        "above_ma20": above_ma20,
        "ma20_up": ma20_up,
        "ma60_up": ma60_up,
        "K": round(K, 1),
        "D": round(D, 1),
        "kd_golden": kd_golden,
        "swing_hi": round(s_hi, 2),
        "swing_lo": round(s_lo, 2),
        "fib50": round(fib50, 2),
        "fib38": round(fib38, 2),
        "fib62": round(fib62, 2),
        "near_fib50": near_fib50,
        "vol_ratio": round(vol_ratio, 2),
        "ret_from_low": round(ret_from_low * 100, 1),
        "tech_score": min(score, 25),
    }


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 5 ▸ 產業別成交量排名
# ════════════════════════════════════════════════════════════════

def get_top_industries(cfg: RunConfig) -> tuple[set, dict]:
    """取得類股別（industry class）成交金額前 N 大。
    修正：原使用的 MI_INDEX20 其實是「個股」成交量前20，並非類股分類；
    正確端點應為 BFIAMU（類股別成交資訊）。
    """
    print("\n【Step 5】取得產業別成交量前5排名")
    fetch_stats: Counter[str] = Counter()
    parse_failures = 0
    for date_str in recent_weekdays(cfg.as_of, TWSE_SOURCE_LOOKBACK):
        data = safe_get(
            "https://www.twse.com.tw/exchangeReport/BFIAMU",
            cfg,
            params={"response": "json", "date": date_str},
            cache_namespace="twse/BFIAMU",
            cache_key=date_str,
        )
        reason = _twse_payload_reason(data)
        fetch_stats[reason] += 1
        if reason != "ok":
            continue
        cols = data.get("fields") or []
        rows = data.get("data") or []
        vol_c = next((c for c in cols if "成交金額" in c or "成交值" in c), None)
        idx_c = next((c for c in cols if "指數" in c or "類別" in c or "類股" in c), None)
        if not vol_c or not idx_c:
            parse_failures += 1
            continue
        df = pd.DataFrame(rows, columns=cols)
        df[vol_c] = to_numeric_series(df[vol_c])
        df[idx_c] = df[idx_c].astype(str).str.strip()
        df = df.dropna(subset=[vol_c]).sort_values(vol_c, ascending=False)
        if df.empty:
            parse_failures += 1
            continue
        tops = set(df[idx_c].str.replace("指數", "", regex=False).head(TOP_INDUSTRY_COUNT).tolist())
        print(f"  前{TOP_INDUSTRY_COUNT}大產業：{tops}")
        return tops, {
            "source_date": date_str,
            "fetch_stats": dict(fetch_stats),
            "parse_failures": parse_failures,
        }
    print("  ⚠ 產業成交量 API 失敗，跳過此過濾")
    return set(), {
        "source_date": None,
        "fetch_stats": dict(fetch_stats),
        "parse_failures": parse_failures,
    }


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 6 ▸ 融資融券分析
# ════════════════════════════════════════════════════════════════

def get_margin(tickers: list[str], cfg: RunConfig) -> tuple[pd.DataFrame, dict]:
    """融資融券彙總。
    修正：TWSE 已將舊 `exchangeReport/MI_MARGN` 的 flat `data[]` schema
    重構為 `rwd/zh/marginTrading/MI_MARGN`，回應裡是 `tables[]`，
    第二張表才是「融資融券彙總(全部)」明細。舊端點現在雖 HTTP 200
    卻回傳 0 列，所以原本的流程從未成功。
    同時：欄位有重複名稱（融資/融券各一組「買進/賣出/今日餘額」），
    需以「位置」索引取得今日餘額，而非以欄名 next() 搜尋。
    """
    print("\n【Step 6】取得融資融券餘額")
    ticker_set = set(tickers)
    fetch_stats: Counter[str] = Counter()
    parse_failures = 0
    for date_str in recent_weekdays(cfg.as_of, TWSE_SOURCE_LOOKBACK):
        data = safe_get(
            "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
            cfg,
            params={"response": "json", "date": date_str, "selectType": "ALL"},
            cache_namespace="twse/MI_MARGN",
            cache_key=date_str,
        )
        reason = _twse_payload_reason(data, payload_keys=("tables",))
        fetch_stats[reason] += 1
        if reason != "ok":
            continue

        # 找出「融資融券彙總」細明表：優先用表名，否則退回欄位最多者
        tables = data.get("tables") or []
        detail = next(
            (
                t for t in tables
                if "融資融券彙總" in str(t.get("title", ""))
                or "融資融券彙總" in str(t.get("name", ""))
            ),
            None,
        )
        if detail is None:
            detail = max(tables, key=lambda t: len(t.get("fields", [])), default=None)
        if not detail or not detail.get("data"):
            parse_failures += 1
            continue

        cols = detail["fields"]
        rows = detail["data"]
        # 標準 schema（16 欄）：代號, 名稱,
        #   [融資] 買進,賣出,現金償還,前日餘額,今日餘額,次一限額,
        #   [融券] 買進,賣出,現券償還,前日餘額,今日餘額,次一限額,
        #   資券互抵, 註記
        if len(cols) < 13:
            log.debug("MI_MARGN 欄位數不足（%d）", len(cols))
            parse_failures += 1
            continue
        if "今日餘額" not in str(cols[6]) or "今日餘額" not in str(cols[12]):
            log.warning("MI_MARGN schema 疑似漂移：欄位6=%s, 欄位12=%s", cols[6], cols[12])
            parse_failures += 1
            continue

        df = pd.DataFrame(rows, columns=[f"c{i}" for i in range(len(cols))])
        df["id"] = df["c0"].astype(str).str.strip()
        df = df[df["id"].isin(ticker_set)].copy()
        if df.empty:
            continue

        # 位置索引取今日餘額（融資第 6 欄 / 融券第 12 欄）
        mb = to_numeric_series(df["c6"]).fillna(0.0)
        ms = to_numeric_series(df["c12"]).fillna(0.0)
        # 嘎空比 = 融券/融資
        squeeze = np.divide(
            ms.to_numpy(dtype=float),
            mb.to_numpy(dtype=float),
            out=np.zeros(len(df), dtype=float),
            where=mb.to_numpy(dtype=float) > 0,
        )

        result = pd.DataFrame({
            "id":            df["id"].values,
            "margin_buy":    mb.values,
            "margin_short":  ms.values,
            "squeeze_ratio": np.round(squeeze, 4),
        })
        print(f"  取得 {len(result)} 檔資料")
        return result, {
            "source_date": date_str,
            "fetch_stats": dict(fetch_stats),
            "parse_failures": parse_failures,
        }

    print("  ⚠ 融資融券 API 失敗，跳過")
    return pd.DataFrame(), {
        "source_date": None,
        "fetch_stats": dict(fetch_stats),
        "parse_failures": parse_failures,
    }


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 7 ▸ 基本面（FinMind，選用）
# ════════════════════════════════════════════════════════════════

FINMIND_MAX_WORKERS = 4   # FinMind 並發 worker 上限（避免超過 API 速率限制）
_FINMIND_SEMAPHORE  = threading.Semaphore(FINMIND_MAX_WORKERS)


def _fetch_one_fundamental(
    t: str,
    api,
    start_q: str,
    end_q: str,
    cfg: RunConfig,
    as_of_key: str,
) -> tuple[dict, str]:
    """取得單檔基本面（P/E, P/B, ROE, GPM）；回傳 (row_dict, status)。
    status ∈ {"cached", "network", "failed"}
    以 _FINMIND_SEMAPHORE 控制最大並發，並在每次網路呼叫後休眠 0.25 s。
    """
    cache_key = f"{t}_{as_of_key}"
    cached_df = read_frame_cache(cfg, "finmind", cache_key)
    if cached_df is not None and {"id", "PE", "PB", "ROE", "GPM"} <= set(cached_df.columns):
        return cached_df.iloc[-1][["id", "PE", "PB", "ROE", "GPM"]].to_dict(), "cached"

    with _FINMIND_SEMAPHORE:
        try:
            per_df = api.taiwan_stock_per(stock_id=t, start_date=start_q, end_date=end_q)
            pe, pb = np.nan, np.nan
            if per_df is not None and not per_df.empty:
                last_per = per_df.sort_values("date").iloc[-1]
                pe = last_per.get("PER", np.nan)
                pb = last_per.get("PBR", np.nan)

            fs = api.taiwan_stock_financial_statement(stock_id=t, start_date=start_q, end_date=end_q)
            roe, gpm = np.nan, np.nan
            if fs is not None and not fs.empty:
                fs = fs.sort_values("date")
                r = fs[fs["type"] == "ReturnOnEquity"]
                g = fs[fs["type"] == "GrossProfitMargin"]
                roe = float(r["value"].iloc[-1]) if not r.empty else np.nan
                gpm = float(g["value"].iloc[-1]) if not g.empty else np.nan

            row = {"id": t, "PE": pe, "PB": pb, "ROE": roe, "GPM": gpm}
            write_frame_cache(cfg, "finmind", cache_key, pd.DataFrame([row]))
            time.sleep(0.25)   # FinMind API 速率限制 — 4 worker × 0.25 s ≈ 16 req/s
            return row, "network"
        except (KeyError, ValueError, IndexError, TypeError, AttributeError) as e:
            log.warning("FinMind 基本面失敗 %s: %s", t, e.__class__.__name__)
            row = {"id": t, "PE": np.nan, "PB": np.nan, "ROE": np.nan, "GPM": np.nan}
            write_frame_cache(cfg, "finmind", cache_key, pd.DataFrame([row]))
            return row, "failed"


def get_fundamentals(tickers: list[str], cfg: RunConfig) -> tuple[pd.DataFrame, dict]:
    if not FINMIND_OK or not FINMIND_TOKEN:
        print("\n【Step 7】FinMind token 未設定 → 跳過基本面（可在 CONFIG 填入）")
        return pd.DataFrame(), {"cached": 0, "network_fetch": 0, "failures": 0}

    print(f"\n【Step 7】取得基本面資料（FinMind，{FINMIND_MAX_WORKERS} 並發）")
    try:
        api = FMLoader()
        api.login_by_token(api_token=FINMIND_TOKEN)
        start_q   = (cfg.as_of - timedelta(days=450)).strftime("%Y-%m-%d")
        end_q     = cfg.as_of.strftime("%Y-%m-%d")
        as_of_key = cfg.as_of.strftime("%Y%m%d")

        rows: list[dict] = []
        cached_count = network_count = failed_count = 0

        with ThreadPoolExecutor(max_workers=FINMIND_MAX_WORKERS) as pool:
            future_to_ticker = {
                pool.submit(_fetch_one_fundamental, t, api, start_q, end_q, cfg, as_of_key): t
                for t in tickers
            }
            pbar = tqdm(as_completed(future_to_ticker), total=len(tickers),
                        desc="  基本面", ncols=72)
            for future in pbar:
                t = future_to_ticker[future]
                try:
                    row, status = future.result()
                    rows.append(row)
                    if status == "cached":
                        cached_count += 1
                    elif status == "network":
                        network_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    log.warning("FinMind worker 未預期失敗 %s: %s", t, e.__class__.__name__)
                    rows.append({"id": t, "PE": np.nan, "PB": np.nan, "ROE": np.nan, "GPM": np.nan})
                    failed_count += 1

        df = pd.DataFrame(rows)
        print(
            f"  取得 {df['PE'].notna().sum()} 檔 P/E、"
            f"{df['ROE'].notna().sum()} 檔 ROE"
            f"（快取 {cached_count} 檔 / 網路 {network_count} 檔 / 失敗 {failed_count} 檔）"
        )
        return df, {"cached": cached_count, "network_fetch": network_count, "failures": failed_count}
    except (requests.RequestException, ValueError, TypeError, AttributeError) as e:
        log.warning("FinMind 初始化失敗：%s", e.__class__.__name__)
        print(f"  ⚠ FinMind 錯誤：{e}")
        return pd.DataFrame(), {"cached": 0, "network_fetch": 0, "failures": len(tickers)}


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 8 ▸ 風險指標：Beta / Sharpe / Sortino
# ════════════════════════════════════════════════════════════════

def calc_risk(t: str, df: pd.DataFrame, bench: pd.DataFrame) -> dict:
    """Beta / Sharpe / Sortino（年化，rf=RISK_FREE_RATE）。失敗回傳 {}."""
    if "Close" not in df.columns or "Close" not in bench.columns:
        return {}
    try:
        r = df["Close"].pct_change().dropna()
        b = bench["Close"].pct_change().dropna()
    except (KeyError, ValueError, TypeError) as e:
        log.warning("calc_risk(%s) 前處理失敗：%s", t, e.__class__.__name__)
        return {}

    if getattr(r.index, "tz", None) is not None:
        r.index = r.index.tz_localize(None)
    if getattr(b.index, "tz", None) is not None:
        b.index = b.index.tz_localize(None)

    aligned = pd.concat([r.rename("stock"), b.rename("bench")], axis=1, join="inner").dropna()
    if len(aligned) < MIN_RISK_RET_OBS:
        log.debug("calc_risk(%s) 對齊樣本不足：%d", t, len(aligned))
        return {}

    sr = aligned["stock"].to_numpy()
    br = aligned["bench"].to_numpy()

    # D24：ddof 選擇有意設計，非疏忽（audit 項目 D24）
    # Beta 使用母體統計量（ddof=0）：Beta 是歷史期間的參數估計值，
    #   cov/var 基於同一樣本計算，ddof=0 避免小樣本時分母不同所致的比值偏差。
    # Sharpe / Sortino 使用樣本標準差（ddof=1）：將歷史報酬視為
    #   未來波動率的估計，ddof=1 是 Bessel 修正，符合金融標準實作。
    cov = float(np.cov(sr, br, ddof=0)[0, 1])
    vb = float(np.var(br, ddof=0))
    beta = cov / vb if vb > 0 else np.nan

    rf_d = RISK_FREE_RATE / TRADING_DAYS_YEAR
    excess = sr - rf_d
    ex_mean = float(excess.mean())
    ex_std = float(excess.std(ddof=1))   # 樣本標準差 — 波動率估計
    sq_root = np.sqrt(TRADING_DAYS_YEAR)
    sharpe = (ex_mean / ex_std * sq_root) if ex_std > 0 else np.nan

    downside = excess[excess < 0]
    dstd = float(downside.std(ddof=1)) if len(downside) >= 10 else np.nan  # 樣本標準差
    sortino = (ex_mean / dstd * sq_root) if dstd and dstd > 0 else np.nan

    return {
        "beta": round(float(beta), 3) if not np.isnan(beta) else np.nan,
        "sharpe": round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        "sortino": round(float(sortino), 3) if not np.isnan(sortino) else np.nan,
        "obs_count": int(len(aligned)),
    }


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 9 ▸ 進場訊號偵測
# ════════════════════════════════════════════════════════════════

def detect_entry(t: str, df: pd.DataFrame, tech: dict, consec: int) -> dict:
    """
    進場條件（對應策略原文）：
    1. 外資+主力連續買超（用連買天數代理嗨投資數字）
    2. 股價觸碰20MA（低點≤MA20且收盤≥MA20）
    3. 斐波納契0.5回測位附近（±2.5%）
    """
    if len(df) < 2 or "Close" not in df.columns or "Low" not in df.columns:
        return {}
    ma20 = tech.get("ma20", np.nan)
    if pd.isna(ma20):
        return {}

    last, prev = df.iloc[-1], df.iloc[-2]

    # ATR-based tolerance（修正 D26）
    # 固定 ±0.5% 窗口在高波動股上太緊、低波動股上太鬆。
    # 改用 ATR(14) 作為絕對容差，fallback 至 0.5% 相對窗口。
    _atr_tol: float = float(ma20) * 0.005   # fallback
    if len(df) >= 16 and {"High", "Low", "Close"} <= set(df.columns):
        _prev_c = df["Close"].shift(1)
        _tr = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - _prev_c).abs(),
            (df["Low"]  - _prev_c).abs(),
        ], axis=1).max(axis=1)
        _atr14 = float(_tr.rolling(14).mean().iloc[-1])
        if pd.notna(_atr14) and _atr14 > 0:
            _atr_tol = _atr14

    touch_ma20 = (
        float(last["Low"]) <= float(ma20) + _atr_tol and
        float(last["Close"]) >= float(ma20) - _atr_tol
    )
    foreign_ok = consec >= MIN_CONSEC_BUY
    near_fib = tech.get("near_fib50", 0) == 1
    vol_ok = tech.get("vol_ratio", 1.0) >= 0.7
    trend_ok = float(last["Close"]) > float(prev["Close"])

    signal = touch_ma20 and foreign_ok and vol_ok
    # 強訊號：同時需要「Fib50附近」AND「當日收漲」（修正 FIND-14）
    # 原 OR 邏輯使 trend_ok（任何收漲日）即可觸發強訊號，過於寬鬆。
    # 改為 AND：需 Fibonacci 支撐 + 日線收紅雙重確認。
    strong_signal = signal and near_fib and trend_ok
    flags = {
        "touch_ma20": int(touch_ma20),
        "foreign_ok": int(foreign_ok),
        "near_fib50": int(near_fib),
        "vol_ok": int(vol_ok),
        "trend_ok": int(trend_ok),
    }

    return {
        **flags,
        "entry_signal": int(signal),
        "strong_signal": int(strong_signal),
        "entry_reason": ",".join(k for k, v in flags.items() if v) or "none",
        "price": round(float(last["Close"]), 2),
        "ma20": round(float(ma20), 2),
        "fib50_target": tech.get("fib50", np.nan),
        "fib_1to1_tp": round(tech.get("swing_hi", float(last["Close"])), 2),
    }


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 10 ▸ 綜合評分（滿分 100）
# ════════════════════════════════════════════════════════════════
#
#  外資籌碼面  30分  ─ 連買天數 + 排名百分位
#  技術面      25分  ─ MA趨勢/KD/Fib/量比
#  融資融券    15分  ─ 嘎空潛力 + 融資健康度
#  基本面      15分  ─ ROE/GPM（需FinMind）
#  風險指標    15分  ─ Beta/Sharpe/Sortino
# ════════════════════════════════════════════════════════════════

SCORE_WEIGHTS = {
    "foreign": 30.0,
    "technical": 25.0,
    "margin": 15.0,
    "fundamentals": 15.0,
    "risk": 15.0,
}


def _component_foreign(flow: dict | None) -> float | None:
    """外資分數（0..1）。"""
    if not flow:
        return None
    points = min(float(flow.get("consec_buy", 0)) * 2, 16)
    rank_pct = float(flow.get("rank_pct", np.nan))
    if np.isnan(rank_pct):
        return None
    if 0.35 <= rank_pct <= 0.70:
        points += 14
    elif 0.25 <= rank_pct < 0.35 or 0.70 < rank_pct <= 0.80:
        points += 8
    return min(points / SCORE_WEIGHTS["foreign"], 1.0)


def _component_technical(tech: dict | None) -> float | None:
    """技術分數（0..1）。"""
    if not tech:
        return None
    score = tech.get("tech_score", np.nan)
    if pd.isna(score):
        return None
    return min(float(score) / SCORE_WEIGHTS["technical"], 1.0)


def _component_margin(margin: dict | None) -> float | None:
    """融資融券分數（0..1）。

    使用明確互斥分區，避免雙重計分（修正 FIND-13）：
    原程式 `if sr >= 0.20: +8` 與 `if 0.05 < sr < 0.30: +7` 在
    0.20 <= sr < 0.30 範圍同時觸發，造成隱含的 15 分滿分由兩個
    獨立條件疊加產生。改為明確四段定義，意圖一目了然。
    輸出數值與原邏輯完全相同：
      [0.20, 0.30) → 15   (甜蜜區：強嘎空 + 健康融資)
      [0.30, ∞)   → 8    (融券佔比過高，嘎空強但偏險)
      [0.10, 0.20) → 12  (中等嘎空 + 健康融資)
      (0.05, 0.10) → 9   (輕微嘎空 + 適中融資)
      sr == 0.05  → 2    (僅觸碰下限，無健康加分)
      sr < 0.05   → 0
    """
    if not margin:
        return None
    sr = margin.get("squeeze_ratio", np.nan)
    if pd.isna(sr):
        return None
    if 0.20 <= sr < 0.30:
        points = 15.0
    elif sr >= 0.30:
        points = 8.0
    elif 0.10 <= sr < 0.20:
        points = 12.0
    elif 0.05 < sr < 0.10:
        points = 9.0
    elif sr >= 0.05:        # sr == 精確 0.05
        points = 2.0
    else:
        points = 0.0
    return min(points / SCORE_WEIGHTS["margin"], 1.0)


def _component_fundamentals(fund: dict | None) -> float | None:
    """基本面分數（0..1）。"""
    if not fund:
        return None
    points = 0.0
    roe = fund.get("ROE", np.nan)
    gpm = fund.get("GPM", np.nan)
    available = False
    if not np.isnan(roe):
        available = True
        points += 8 if roe >= 15 else (5 if roe >= 10 else (2 if roe >= 5 else 0))
    if not np.isnan(gpm):
        available = True
        points += 7 if gpm >= 30 else (4 if gpm >= 15 else (1 if gpm >= 5 else 0))
    if not available:
        return None
    return min(points / SCORE_WEIGHTS["fundamentals"], 1.0)


def _component_risk(risk: dict | None) -> float | None:
    """風險分數（0..1）。"""
    if not risk:
        return None
    sharpe = risk.get("sharpe", np.nan)
    sortino = risk.get("sortino", np.nan)
    beta = risk.get("beta", np.nan)
    points = 0.0
    available = False
    if not np.isnan(sharpe):
        available = True
        points += 5 if sharpe >= 1.5 else (3 if sharpe >= 0.8 else (1 if sharpe >= 0.3 else 0))
    if not np.isnan(sortino):
        available = True
        points += 5 if sortino >= 2.0 else (3 if sortino >= 1.0 else (1 if sortino >= 0.5 else 0))
    if not np.isnan(beta):
        available = True
        points += 5 if 0.8 <= beta <= 1.5 else (2 if 0.5 <= beta < 0.8 else 0)
    if not available:
        return None
    return min(points / SCORE_WEIGHTS["risk"], 1.0)


def score_stock(flow: dict | None, tech: dict | None, margin: dict | None,
                fund: dict | None, risk: dict | None) -> tuple[float, dict]:
    """回傳總分與分項貢獻。"""
    components = {
        "foreign": _component_foreign(flow),
        "technical": _component_technical(tech),
        "margin": _component_margin(margin),
        "fundamentals": _component_fundamentals(fund),
        "risk": _component_risk(risk),
    }
    available = {k: v for k, v in components.items() if v is not None}
    missing = sorted(k for k, v in components.items() if v is None)
    if not available:
        return 0.0, {"available": "", "missing": ",".join(missing), "points": {}}

    total_weight = sum(SCORE_WEIGHTS[k] for k in available)
    points = {
        k: round(100 * (SCORE_WEIGHTS[k] / total_weight) * v, 1)
        for k, v in available.items()
    }
    total = round(sum(points.values()), 1)
    return total, {
        "available": ",".join(sorted(available)),
        "missing": ",".join(missing),
        "points": points,
    }


# ════════════════════════════════════════════════════════════════
#  ▌ OUTPUT ▸ 圖表 + CSV
# ════════════════════════════════════════════════════════════════

def save_output(result: pd.DataFrame, entry: pd.DataFrame, cfg: RunConfig,
                manifest: dict | None = None, dropped: list[dict] | None = None):
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    # ── CSV ───────────────────────────────────────────────────────
    result.to_csv(cfg.output_dir / "candidates.csv", index=False, encoding="utf-8-sig")
    entry.to_csv(cfg.output_dir / "entry_signals.csv", index=False, encoding="utf-8-sig")

    if dropped is not None:
        dropped_df = pd.DataFrame(dropped, columns=["id", "name", "sector", "stage", "reason"])
        dropped_df.to_csv(cfg.output_dir / "dropped.csv", index=False, encoding="utf-8-sig")
    if manifest is not None:
        (cfg.output_dir / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 評分散點圖 ────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(f"台股波段選股系統 — 候選股分析  ({cfg.as_of.strftime('%Y-%m-%d')})", fontsize=13)

    # 左：分數直方圖
    axes[0].hist(result["total_score"].dropna(), bins=20, color="#4A7FC1", edgecolor="white", alpha=0.85)
    axes[0].set_xlabel("綜合評分")
    axes[0].set_ylabel("股票數量")
    axes[0].set_title("評分分佈")

    # 右：技術面 vs 外資連買天數（顏色=總分）— 修正：原程式檢查英文欄名
    # 但 DataFrame 以中文命名，導致散點圖從未被繪製。改為檢查實際存在的欄位。
    tech_col  = "技術面分數"
    consec_col = "連續外資買超天數"
    if tech_col in result.columns and consec_col in result.columns:
        sc = axes[1].scatter(
            result[tech_col], result[consec_col],
            c=result["total_score"], cmap="RdYlGn", alpha=0.75, s=45, vmin=0, vmax=100
        )
        plt.colorbar(sc, ax=axes[1], label="綜合評分")
        axes[1].set_xlabel("技術面分數（/25）")
        axes[1].set_ylabel("連續外資買超天數")
        axes[1].set_title("技術面 vs 外資連買（顏色=總分）")

    plt.tight_layout()
    plt.savefig(cfg.output_dir / "score_chart.png", dpi=140, bbox_inches="tight")
    plt.close()

    print(f"\n📁 輸出目錄：./{cfg.output_dir}/")
    print(f"   ├── candidates.csv      ← 前{TARGET}名候選股（含所有指標）")
    print(f"   ├── entry_signals.csv   ← 目前有進場訊號者")
    print(f"   └── score_chart.png     ← 評分分佈圖")
    if manifest is not None:
        print("   ├── run_manifest.json   ← 執行摘要 / 資料品質")
    if dropped is not None:
        print("   └── dropped.csv         ← 被排除股票與原因")


# ════════════════════════════════════════════════════════════════
#  ▌ MAIN
# ════════════════════════════════════════════════════════════════

def _append_drop(drop_rows: list[dict], meta: dict, ticker: str, stage: str, reason: str) -> None:
    """記錄被排除個股。"""
    info = meta.get(ticker, {})
    drop_rows.append({
        "id": ticker,
        "name": info.get("name", ""),
        "sector": info.get("sector", ""),
        "stage": stage,
        "reason": reason,
    })


def _push_warning(warnings_list: list[str], message: str) -> None:
    """記錄警告並同步輸出 log。"""
    warnings_list.append(message)
    log.warning(message)


def main(cfg: RunConfig):
    t_start = time.time()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    print("═" * 60)
    print("  台股波段選股系統 v1.0")
    print(f"  執行基準日：{cfg.as_of.strftime('%Y-%m-%d')}")
    print("═" * 60)

    manifest: dict = {
        "as_of": cfg.as_of.isoformat(),
        "lookback_days": cfg.lookback_days,
        "warnings": [],
        "steps": {},
    }
    drop_rows: list[dict] = []

    # ── 1. 股票清單 ───────────────────────────────────────────────
    stock_df, stock_source = get_stock_list(cfg)
    manifest["steps"]["stock_list"] = {
        "count_out": int(len(stock_df)),
        "source_date": stock_source,
    }
    if stock_df.empty:
        print("❌ 股票清單取得失敗，程序中止")
        return
    valid_ids = set(stock_df["id"].tolist())
    meta_idx = stock_df.set_index("id")[["name", "sector"]].to_dict("index")

    # ── 2. 外資排行 ───────────────────────────────────────────────
    foreign_df, foreign_meta = get_foreign_ranking(valid_ids, cfg)
    ok_days = len(foreign_meta.get("ok_dates", []))
    manifest["steps"]["foreign"] = {
        "count_out": int(len(foreign_df)),
        "ok_days": ok_days,
        "fetch_stats": foreign_meta.get("fetch_stats", {}),
        "source_dates": foreign_meta.get("ok_dates", []),
    }
    if ok_days < LOOKUP_DAYS * 0.6:
        _push_warning(manifest["warnings"], f"外資有效交易日不足：{ok_days}/{LOOKUP_DAYS}")
    if len(foreign_df) < 10:
        print("❌ 外資候選股不足，程序中止"); return

    cand_ids = foreign_df["id"].tolist()

    # ── 3. 價量資料 ───────────────────────────────────────────────
    price_data, price_stats = download_prices(cand_ids, cfg)
    manifest["steps"]["prices"] = price_stats
    if cand_ids and price_stats["downloaded"] / len(cand_ids) < 0.8:
        _push_warning(
            manifest["warnings"],
            f"價量下載成功率偏低：{price_stats['downloaded']}/{len(cand_ids)}",
        )
    for ticker in cand_ids:
        if ticker not in price_data:
            _append_drop(drop_rows, meta_idx, ticker, "prices", "download_failed")

    # 大盤基準（0050）— 帶重試，避免一次失敗就讓所有風險指標為 NaN
    bench_map, bench_stats = download_prices([BENCHMARK_TICKER], cfg)
    bench_raw = bench_map.get(BENCHMARK_TICKER)
    if bench_raw is None or "Close" not in bench_raw.columns:
        _push_warning(manifest["warnings"], f"無法取得 {BENCHMARK_TICKER} 基準資料，風險分數將重新正規化")
        benchmark = pd.DataFrame()
    else:
        benchmark = bench_raw[["Close"]].copy()
    manifest["steps"]["benchmark"] = bench_stats | {"ok": bool(not benchmark.empty)}

    # ── 4. 技術分析 ───────────────────────────────────────────────
    print("\n【Step 4】技術面分析")
    tech_map = {}
    for t in tqdm(cand_ids, desc="  技術指標", ncols=72):
        if t in price_data:
            r = analyze_tech(t, price_data[t])
            if r:
                tech_map[t] = r
            else:
                _append_drop(drop_rows, meta_idx, t, "technical", "insufficient_features")
    print(f"  完成 {len(tech_map)} 檔")
    manifest["steps"]["technical"] = {"count_out": int(len(tech_map))}

    # ── 5. 產業排名 ───────────────────────────────────────────────
    top_ind, top_ind_meta = get_top_industries(cfg)
    manifest["steps"]["industries"] = {
        "count_out": int(len(top_ind)),
        "source_date": top_ind_meta.get("source_date"),
        "top_industries": sorted(top_ind),
        "fetch_stats": top_ind_meta.get("fetch_stats", {}),
        "parse_failures": int(top_ind_meta.get("parse_failures", 0)),
    }
    if not top_ind_meta.get("source_date"):
        _push_warning(manifest["warnings"], "產業成交量資料取得失敗，產業過濾已停用")

    # ── 6. 融資融券 ───────────────────────────────────────────────
    margin_df, margin_meta = get_margin(cand_ids, cfg)
    # FIND-22：合併前驗證 id 欄位格式，防止爬蟲回傳非4碼代號混入索引
    if not margin_df.empty and "id" in margin_df.columns:
        bad_ids = margin_df[~margin_df["id"].str.match(r"^\d{4}$", na=True)]
        if not bad_ids.empty:
            log.warning("MI_MARGN 含非4碼代號 %d 筆，已過濾", len(bad_ids))
            margin_df = margin_df[margin_df["id"].str.match(r"^\d{4}$", na=False)].copy()
    margin_map = {} if margin_df.empty else margin_df.set_index("id").to_dict("index")
    manifest["steps"]["margin"] = {
        "count_out": int(len(margin_df)),
        "source_date": margin_meta.get("source_date"),
        "fetch_stats": margin_meta.get("fetch_stats", {}),
        "parse_failures": int(margin_meta.get("parse_failures", 0)),
    }
    if cand_ids and not margin_meta.get("source_date"):
        _push_warning(manifest["warnings"], "融資融券資料取得失敗，融資分數將重新正規化")

    # ── 7. 基本面 ─────────────────────────────────────────────────
    fund_df, fund_meta = get_fundamentals(cand_ids, cfg)
    # FIND-22：合併前驗證 id 欄位格式
    if not fund_df.empty and "id" in fund_df.columns:
        bad_ids = fund_df[~fund_df["id"].str.match(r"^\d{4}$", na=True)]
        if not bad_ids.empty:
            log.warning("FinMind 基本面含非4碼代號 %d 筆，已過濾", len(bad_ids))
            fund_df = fund_df[fund_df["id"].str.match(r"^\d{4}$", na=False)].copy()
    fund_map = {} if fund_df.empty else fund_df.set_index("id").to_dict("index")
    manifest["steps"]["fundamentals"] = {
        "count_out": int(len(fund_df)),
        "roe_count": int(fund_df["ROE"].notna().sum()) if not fund_df.empty and "ROE" in fund_df.columns else 0,
        "cached": int(fund_meta.get("cached", 0)),
        "network_fetch": int(fund_meta.get("network_fetch", 0)),
        "failures": int(fund_meta.get("failures", 0)),
    }

    # ── 8. 風險指標 ───────────────────────────────────────────────
    print("\n【Step 8】計算 Beta / Sharpe / Sortino")
    risk_map: dict[str, dict] = {}
    # 記錄每檔交集樣本數（FIND-19）：追蹤因交集不足而被捨棄的個股
    risk_obs_counts: dict[str, int] = {}
    risk_dropped_count = 0
    for t in tqdm(cand_ids, desc="  風險指標", ncols=72):
        if t in price_data and not benchmark.empty:
            risk_result = calc_risk(t, price_data[t], benchmark)
            if risk_result:
                risk_map[t] = risk_result
                risk_obs_counts[t] = int(risk_result.get("obs_count", 0))
            else:
                risk_dropped_count += 1
                log.debug("calc_risk(%s) 被捨棄（樣本不足或計算失敗）", t)
    risk_available = sum(1 for t in cand_ids if risk_map.get(t))
    obs_vals = list(risk_obs_counts.values())
    manifest["steps"]["risk"] = {
        "count_out": int(risk_available),
        "dropped_insufficient_obs": risk_dropped_count,
        "obs_min":    int(min(obs_vals)) if obs_vals else 0,
        "obs_median": int(sorted(obs_vals)[len(obs_vals) // 2]) if obs_vals else 0,
        "obs_max":    int(max(obs_vals)) if obs_vals else 0,
    }
    if cand_ids and benchmark.empty:
        pass
    elif cand_ids and (len(cand_ids) - risk_available) / len(cand_ids) > 0.2:
        _push_warning(
            manifest["warnings"],
            f"風險指標缺失比例偏高：{len(cand_ids) - risk_available}/{len(cand_ids)}，"
            f"其中樣本不足捨棄 {risk_dropped_count} 檔",
        )

    # ── 9 & 10. 訊號 + 評分 ─────────────────────────────────────
    print("\n【Step 9/10】進場訊號 + 綜合評分")

    # 預先建立 dict 索引：O(1) 查詢取代 O(N) DataFrame 搜尋
    f_idx = foreign_df.set_index("id").to_dict("index")
    top_codes: set[str] = set().union(*(BFIAMU_TO_TWSE_CODES.get(n, set()) for n in top_ind))

    all_rows = []
    sig_rows = []

    for t in cand_ids:
        if t not in price_data or t not in tech_map:
            continue

        f_dict = f_idx.get(t, {})
        tech   = tech_map[t]
        mg     = margin_map.get(t)
        fu     = fund_map.get(t)
        risk   = risk_map.get(t, {})
        consec = int(f_dict.get("consec_buy", 0))

        score, score_meta = score_stock(f_dict, tech, mg, fu, risk)
        sig = detect_entry(t, price_data[t], tech, consec)

        meta   = meta_idx.get(t, {})
        name   = meta.get("name", "")
        sector = meta.get("sector", "")
        ind_ok = int(not top_codes or sector in top_codes)

        row = {
            "股票代號":      t,
            "股票名稱":      name,
            "產業別":        sector,
            "產業達標(前5)": ind_ok,
            # 外資
            "外資累計淨買(萬股)": round(float(f_dict.get("cum_net", 0)), 1),
            "連續外資買超天數":    consec,
            "外資排名百分位":      round(float(f_dict.get("rank_pct", 1.0)), 3),
            # 技術
            "收盤價":   tech["close"],
            "MA20":     tech["ma20"],
            "MA20向上": tech["ma20_up"],
            "K值":      tech["K"],
            "D值":      tech["D"],
            "KD金叉":   tech["kd_golden"],
            "Fib50位":  tech["fib50"],
            "近Fib50%": tech["near_fib50"],
            "量比":     tech["vol_ratio"],
            "低點漲幅%": tech["ret_from_low"],
            # 融資融券
            "嘎空比":   mg.get("squeeze_ratio", np.nan) if mg else np.nan,
            # 基本面
            "PE":       fu.get("PE",  np.nan) if fu else np.nan,
            "PB":       fu.get("PB",  np.nan) if fu else np.nan,
            "ROE%":     fu.get("ROE", np.nan) if fu else np.nan,
            "毛利率%":  fu.get("GPM", np.nan) if fu else np.nan,
            # 風險
            "Beta":    risk.get("beta",    np.nan),
            "Sharpe":  risk.get("sharpe",  np.nan),
            "Sortino": risk.get("sortino", np.nan),
            "風險樣本數": risk.get("obs_count", np.nan),
            # 分數
            "技術面分數": tech["tech_score"],
            "外資加權分數": score_meta["points"].get("foreign", np.nan),
            "技術加權分數": score_meta["points"].get("technical", np.nan),
            "融資融券加權分數": score_meta["points"].get("margin", np.nan),
            "基本面加權分數": score_meta["points"].get("fundamentals", np.nan),
            "風險加權分數": score_meta["points"].get("risk", np.nan),
            "可用評分模組": score_meta["available"],
            "缺失評分模組": score_meta["missing"],
            "有進場訊號": int(sig.get("entry_signal", 0)),
            "total_score": score,
        }
        all_rows.append(row)

        # 進場訊號
        if sig.get("entry_signal"):
            sig_rows.append({
                "股票代號": t, "股票名稱": name,
                "收盤價": sig.get("price"),
                "MA20": sig.get("ma20"),
                "Fib50目標": sig.get("fib50_target"),
                "1:1停利目標": sig.get("fib_1to1_tp"),
                "強訊號": sig.get("strong_signal"),
                "外資連買天數": consec,
                "entry_reason": sig.get("entry_reason"),
                "touch_ma20": sig.get("touch_ma20"),
                "foreign_ok": sig.get("foreign_ok"),
                "near_fib50": sig.get("near_fib50"),
                "vol_ok": sig.get("vol_ok"),
                "trend_ok": sig.get("trend_ok"),
                "total_score": score,
            })

    if not all_rows:
        print("❌ 無符合條件的股票，請調寬篩選參數"); return

    result_df = (pd.DataFrame(all_rows)
                 .sort_values("total_score", ascending=False)
                 .head(TARGET)
                 .reset_index(drop=True))
    result_df.index += 1

    entry_df = (pd.DataFrame(sig_rows)
                .sort_values("total_score", ascending=False)
                .reset_index(drop=True) if sig_rows else pd.DataFrame())

    manifest["result_count"] = int(len(result_df))
    manifest["entry_count"] = int(len(entry_df))
    manifest["truncated_count"] = max(len(all_rows) - len(result_df), 0)
    manifest["drop_reason_counts"] = dict(Counter(row["reason"] for row in drop_rows))
    manifest["score_stats"] = {
        "min": round(float(result_df["total_score"].min()), 2),
        "median": round(float(result_df["total_score"].median()), 2),
        "max": round(float(result_df["total_score"].max()), 2),
        "n": int(len(result_df)),
    }

    # ── 輸出 ─────────────────────────────────────────────────────
    save_output(result_df, entry_df, cfg, manifest=manifest, dropped=drop_rows)

    # ── 終端機摘要 ────────────────────────────────────────────────
    elapsed = (time.time() - t_start) / 60
    print(f"\n{'═'*60}")
    print(f"✅ 完成！耗時 {elapsed:.1f} 分鐘")
    if manifest["warnings"]:
        print("⚠ 本次資料品質警告：")
        for msg in manifest["warnings"]:
            print(f"  - {msg}")

    display_cols = ["股票代號","股票名稱","產業別","連續外資買超天數",
                    "收盤價","MA20向上","KD金叉","嘎空比","total_score"]
    avail_cols   = [c for c in display_cols if c in result_df.columns]
    print(f"\n🏆 前20名候選股（滿分100）：")
    print(result_df[avail_cols].head(20).to_string())

    if not entry_df.empty:
        print(f"\n🚨 進場訊號（共 {len(entry_df)} 檔）：")
        ecols = [c for c in ["股票代號","股票名稱","收盤價","MA20",
                              "Fib50目標","1:1停利目標","強訊號","total_score"]
                 if c in entry_df.columns]
        print(entry_df[ecols].to_string(index=False))
    else:
        print("\n📌 今日無明顯進場訊號，可明日再次執行")

    print("═" * 60)


if __name__ == "__main__":
    main(parse_args())
