"""
台股波段選股系統 v1.0
策略：外資買超中後段排名 + 技術面 + 融資融券 + 基本面 + 風險指標
Python 3.12+ | 執行前確認已安裝：pip install lxml openpyxl finmind
"""

import warnings
warnings.filterwarnings("ignore")

import logging
import random
import threading
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

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
#  ▌ 智慧隨機頻率調節器 (Domain-aware Monkey Patch)
#     - TWSE：嚴格序列化 + 延遲（防止被封鎖）
#     - 其它網域（yfinance / FinMind）：輕量 jitter、不共用全域鎖
#     - 所有請求共用 Session → 連線池 / TCP keepalive
# ════════════════════════════════════════════════════════════════

_original_get = requests.get
_twse_lock    = threading.Lock()   # 僅 TWSE 共用的序列化鎖

# 共用 Session（連線池 + Keep-Alive）大幅降低 TLS 建立成本
_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=0)
_session.mount("https://", _adapter)
_session.mount("http://",  _adapter)

_TWSE_HOSTS = ("twse.com.tw", "openapi.twse.com.tw")


def _smart_delayed_get(*args, **kwargs):
    """網域感知的節流器：TWSE 加鎖、其它加微抖動。"""
    url  = args[0] if args else kwargs.get("url", "")
    host = urlparse(url).netloc if url else ""

    # 自動改用 Session（若呼叫端未指定）
    if "timeout" not in kwargs:
        kwargs["timeout"] = 15

    if any(h in host for h in _TWSE_HOSTS):
        with _twse_lock:
            time.sleep(random.uniform(0.7, 1.5))
            return _session.get(*args, **kwargs)

    # 非 TWSE：輕量 jitter 避免與 yfinance 內部節流衝突
    time.sleep(random.uniform(0.05, 0.15))
    return _session.get(*args, **kwargs)


# 覆寫 requests.get，所有子模組（含 yfinance 內部）皆套用
requests.get = _smart_delayed_get

# FinMind（可選，用於基本面）
try:
    from finmind.data import DataLoader as FMLoader
    FINMIND_OK = True
except ImportError:
    FINMIND_OK = False

# ════════════════════════════════════════════════════════════════
#  ▌ CONFIG — 只需修改這區
# ════════════════════════════════════════════════════════════════

FINMIND_TOKEN = ""           # finmindtrade.com 免費註冊後填入（可空白，跳過基本面）

END_DATE      = datetime.today()
START_DATE    = END_DATE - timedelta(days=730)   # 2年資料
LOOKUP_DAYS   = 30                               # 外資統計天數

OUTPUT_DIR    = Path("strategy_output")
OUTPUT_DIR.mkdir(exist_ok=True)

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

# ════════════════════════════════════════════════════════════════
#  ▌ UTILITIES
# ════════════════════════════════════════════════════════════════

def recent_weekdays(n: int = 50) -> list[str]:
    """回傳最近n個工作日的日期字串（YYYYMMDD），由新到舊"""
    result, d = [], END_DATE
    while len(result) < n:
        if d.weekday() < 5:
            result.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return result


def safe_get(url: str, params: dict | None = None, retries: int = 3) -> dict | None:
    """帶指數退避與 JSON 解析防禦的 GET。失敗回傳 None 不拋例外。"""
    last_err: Exception | None = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
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
        # 指數退避（1.5s → 3s → 6s）+ 抖動
        time.sleep(1.5 * (2 ** i) + random.uniform(0, 0.5))
    if last_err:
        log.debug("放棄 %s：%s", url, last_err)
    return None


def to_numeric_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace(",", "").str.replace(" ", ""),
        errors="coerce"
    )


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 1 ▸ 上市股票清單
# ════════════════════════════════════════════════════════════════

def _find_col(columns, keywords: list[str]) -> str | None:
    """從欄位清單中，用關鍵字模糊比對找出目標欄位名稱"""
    for col in columns:
        if any(k in str(col) for k in keywords):
            return col
    return None


def get_stock_list() -> pd.DataFrame:
    print("═" * 60)
    print("【Step 1】取得上市股票清單")
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    data = safe_get(url)
    if not data:
        raise RuntimeError("TWSE 股票清單 API 無回應，請確認網路連線")

    df = pd.DataFrame(data)
    print(f"  API 原始欄位：{list(df.columns)}")   # 除錯用：印出實際欄位名

    # ── 關鍵字模糊偵測欄位（不受 TWSE 改欄名影響）────────────────
    id_col      = _find_col(df.columns, ["代號", "代碼", "Code", "ID"])
    name_col    = _find_col(df.columns, ["名稱", "Name"])
    sector_col  = _find_col(df.columns, ["產業", "類別", "類股", "Industry", "Sector"])
    capital_col = _find_col(df.columns, ["資本額", "資本", "Capital"])

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
    name_has_fin = df["name"].apply(lambda x: any(k in x for k in FIN_KW))
    sector_is_fin = df["sector"].isin(FIN_SECTOR_CODES)
    df = df[~(name_has_fin | sector_is_fin)].copy()
    print(f"  上市總數 {before} → 排除金融後 {len(df)} 檔")

    return df[["id", "name", "sector", "capital"]].reset_index(drop=True)


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 2 ▸ 外資30日買超排行（逐日爬取累加）
# ════════════════════════════════════════════════════════════════

def _fetch_foreign_day(date_str: str) -> pd.DataFrame | None:
    """取得單日所有個股外資買賣超（TWSE TWT53U）"""
    data = safe_get(
        "https://www.twse.com.tw/fund/TWT53U",
        params={"response": "json", "date": date_str, "selectType": "ALLBUT0999"}
    )
    if not data or data.get("stat") != "OK" or not data.get("data"):
        return None

    cols  = [h.strip() for h in data["fields"]]
    df    = pd.DataFrame(data["data"], columns=cols)
    id_c  = next((c for c in cols if "代號" in c or "代碼" in c), None)
    net_c = next((c for c in cols if "買賣超" in c and "股" in c), None)
    if not id_c or not net_c:
        return None

    df = df[[id_c, net_c]].rename(columns={id_c: "id", net_c: f"d{date_str}"})
    df["id"]         = df["id"].astype(str).str.strip()
    df[f"d{date_str}"] = to_numeric_series(df[f"d{date_str}"])
    return df.set_index("id")


def get_foreign_ranking(valid_ids: set) -> pd.DataFrame:
    print("\n【Step 2】爬取外資近30日買超排行")
    dates = recent_weekdays(LOOKUP_DAYS + 12)[:LOOKUP_DAYS + 12]

    frames = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(_fetch_foreign_day, d): d for d in dates}
        for f in tqdm(as_completed(futs), total=len(futs), desc="  外資日資料", ncols=72):
            res = f.result()
            if res is not None:
                frames.append(res)

    if not frames:
        raise RuntimeError("外資資料全部取得失敗，請稍後再試")

    master  = pd.concat(frames, axis=1).fillna(0)
    day_cols = sorted([c for c in master.columns if c.startswith("d")], reverse=True)  # 新→舊

    # 累計買超（萬股）
    master["cum_net"] = master[day_cols].sum(axis=1) / 10000

    # 連續買超天數（從最近日往前數，碰到非正即停）— 全向量化
    # day_cols 已依「新→舊」排序，對每列計算前綴全 True 的長度：
    #   running_prod 為 bool 逐列累積 AND，True 代表自最新日起仍連續買超
    bool_mat      = master[day_cols].to_numpy() > 0
    running_prod  = np.logical_and.accumulate(bool_mat, axis=1)
    master["consec_buy"] = running_prod.sum(axis=1).astype(int)

    # 只保留合法個股
    master = master[master.index.isin(valid_ids)]

    # 排名
    master = master.sort_values("cum_net", ascending=False)
    master["rank"]     = range(1, len(master) + 1)
    master["rank_pct"] = master["rank"] / len(master)

    # 篩選條件：中後段排名 + 連續買超達標
    filt = master[
        (master["rank_pct"] >= RANK_LOW) &
        (master["rank_pct"] <= RANK_HIGH) &
        (master["consec_buy"] >= MIN_CONSEC_BUY)
    ].copy()

    print(f"  排名中後段({RANK_LOW*100:.0f}%~{RANK_HIGH*100:.0f}%) + 連買≥{MIN_CONSEC_BUY}天 → {len(filt)} 檔")
    return filt[["cum_net", "consec_buy", "rank", "rank_pct"]].reset_index().rename(columns={"index": "id"})


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 3 ▸ 下載價量資料（平行）
# ════════════════════════════════════════════════════════════════

def _yf_download(ticker: str, min_rows: int = 60, retries: int = 2) -> pd.DataFrame | None:
    """yfinance 封裝：自動展平 MultiIndex、列數不足視為失敗。"""
    for i in range(retries + 1):
        try:
            raw = yf.download(ticker, start=START_DATE, end=END_DATE,
                              progress=False, auto_adjust=True, timeout=20)
            if raw is None or raw.empty or len(raw) < min_rows:
                return None
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            need = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in raw.columns]
            return raw[need].copy() if need else None
        except Exception as e:
            log.debug("yf.download(%s) 第 %d 次失敗：%s", ticker, i + 1, e)
            time.sleep(0.8 * (i + 1))
    return None


def _dl_one(t: str) -> tuple:
    df = _yf_download(f"{t}.TW", min_rows=60)
    return t, df


def download_prices(tickers: list[str]) -> dict:
    print(f"\n【Step 3】平行下載 {len(tickers)} 檔價量資料")
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_dl_one, t): t for t in tickers}
        for f in tqdm(as_completed(futs), total=len(futs), desc="  下載中", ncols=72):
            t, df = f.result()
            if df is not None:
                out[t] = df
    print(f"  成功 {len(out)} 檔 / 失敗 {len(tickers)-len(out)} 檔")
    return out


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 4 ▸ 技術面分析
# ════════════════════════════════════════════════════════════════

def analyze_tech(t: str, df: pd.DataFrame) -> dict | None:
    """
    計算：MA趨勢 / KD / Fibonacci / 量比
    回傳技術面分數（滿分 25）及各指標
    """
    try:
        d = df.copy()
        d["ma20"]  = d["Close"].rolling(20).mean()
        d["ma60"]  = d["Close"].rolling(60).mean()
        d["ma120"] = d["Close"].rolling(120).mean()
        d = d.dropna()
        if len(d) < 25:
            return None

        last  = d.iloc[-1]
        close = last["Close"]

        # ── MA 多頭條件 ───────────────────────────────────────────
        above_ma20  = int(close > last["ma20"])
        ma20_up     = int(d["ma20"].iloc[-1] > d["ma20"].iloc[-5])
        ma60_up     = int(d["ma60"].iloc[-1] > d["ma60"].iloc[-10])
        days_on_ma20 = int((d["Close"].iloc[-20:] > d["ma20"].iloc[-20:]).sum())

        # ── KD（9日）───────────────────────────────────────────────
        stoch = _stoch(d["High"], d["Low"], d["Close"], k=9, d=3, smooth_k=3)
        if stoch is not None and not stoch.empty and len(stoch) >= 1:
            k_val = stoch.iloc[-1, 0]
            d_val = stoch.iloc[-1, 1]
            K = float(k_val) if pd.notna(k_val) else 50.0
            D = float(d_val) if pd.notna(d_val) else 50.0
        else:
            K = D = 50.0
        kd_golden = int(K > D and K < 80)  # 金叉 + 未超買

        # ── Fibonacci（近60日）─────────────────────────────────────
        seg   = d.iloc[-60:]
        s_hi  = seg["High"].max()
        s_lo  = seg["Low"].min()
        rng   = s_hi - s_lo
        fib50 = s_hi - 0.500 * rng  if rng > 0 else close
        fib38 = s_hi - 0.382 * rng  if rng > 0 else close
        fib62 = s_hi - 0.618 * rng  if rng > 0 else close

        near_fib50 = int(rng > 0 and abs(close - fib50) / fib50 < 0.025)

        # 從低點回彈幅度（判斷「小幅拉抬」偏好）
        ret_from_low = (close - s_lo) / s_lo if s_lo > 0 else 0
        minor_lift   = int(0.05 <= ret_from_low <= 0.45)

        # ── 量比 ───────────────────────────────────────────────────
        vol_ma20  = d["Volume"].rolling(20).mean().iloc[-1]
        vol_ratio = float(last["Volume"] / vol_ma20) if vol_ma20 > 0 else 1.0

        # ── 技術面分數（滿分 25）──────────────────────────────────
        score = (
            above_ma20  * 5 +
            ma20_up     * 5 +
            ma60_up     * 3 +
            kd_golden   * 4 +
            near_fib50  * 3 +
            minor_lift  * 3 +
            min(days_on_ma20 // 5, 2)   # 穩定在MA20上，最多+2
        )

        return {
            "ticker":        t,
            "close":         round(close, 2),
            "ma20":          round(float(last["ma20"]), 2),
            "ma60":          round(float(last["ma60"]), 2),
            "above_ma20":    above_ma20,
            "ma20_up":       ma20_up,
            "ma60_up":       ma60_up,
            "K":             round(K, 1),
            "D":             round(D, 1),
            "kd_golden":     kd_golden,
            "swing_hi":      round(s_hi, 2),
            "swing_lo":      round(s_lo, 2),
            "fib50":         round(fib50, 2),
            "fib38":         round(fib38, 2),
            "fib62":         round(fib62, 2),
            "near_fib50":    near_fib50,
            "vol_ratio":     round(vol_ratio, 2),
            "ret_from_low":  round(ret_from_low * 100, 1),
            "tech_score":    min(score, 25),
        }
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 5 ▸ 產業別成交量排名
# ════════════════════════════════════════════════════════════════

def get_top_industries() -> set:
    print("\n【Step 5】取得產業別成交量前5排名")
    for date_str in recent_weekdays(5):
        data = safe_get(
            "https://www.twse.com.tw/exchangeReport/MI_INDEX20",
            params={"response": "json", "date": date_str}
        )
        if data and data.get("stat") == "OK" and data.get("data"):
            cols   = data["fields"]
            df     = pd.DataFrame(data["data"], columns=cols)
            vol_c  = next((c for c in cols if "成交金額" in c or "成交值" in c), None)
            idx_c  = next((c for c in cols if "指數" in c or "類別" in c or "類股" in c), None)
            if vol_c and idx_c:
                df[vol_c] = to_numeric_series(df[vol_c])
                df = df.dropna(subset=[vol_c]).sort_values(vol_c, ascending=False)
                tops = set(df[idx_c].head(TOP_INDUSTRY_COUNT).tolist())
                print(f"  前{TOP_INDUSTRY_COUNT}大產業：{tops}")
                return tops
    print("  ⚠ 產業成交量 API 失敗，跳過此過濾")
    return set()


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 6 ▸ 融資融券分析
# ════════════════════════════════════════════════════════════════

def get_margin(tickers: list[str]) -> pd.DataFrame:
    print("\n【Step 6】取得融資融券餘額")
    ticker_set = set(tickers)
    for date_str in recent_weekdays(5):
        data = safe_get(
            "https://www.twse.com.tw/exchangeReport/MI_MARGN",
            params={"response": "json", "date": date_str, "selectType": "ALL"}
        )
        if not (data and data.get("stat") == "OK" and data.get("data")):
            continue

        cols = data["fields"]
        df   = pd.DataFrame(data["data"], columns=cols)
        id_c = next((c for c in cols if "代號" in c or "代碼" in c), cols[0])
        df["id"] = df[id_c].astype(str).str.strip()
        df = df[df["id"].isin(ticker_set)].copy()
        if df.empty:
            continue

        # 融資 / 融券餘額欄位
        mb_c = next((c for c in cols if "融資" in c and "餘額" in c and "股數" not in c), None)
        ms_c = next((c for c in cols if "融券" in c and "餘額" in c and "股數" not in c), None)

        # 僅轉換真正需要的欄位（避免整表 str→float 轉換成本）
        mb = to_numeric_series(df[mb_c]).fillna(0.0) if mb_c else pd.Series(0.0, index=df.index)
        ms = to_numeric_series(df[ms_c]).fillna(0.0) if ms_c else pd.Series(0.0, index=df.index)
        squeeze = np.where(mb > 0, ms / mb.replace(0, np.nan), 0.0)

        result = pd.DataFrame({
            "id":            df["id"].values,
            "margin_buy":    mb.values,
            "margin_short":  ms.values,
            "squeeze_ratio": np.round(squeeze, 4),
        })
        print(f"  取得 {len(result)} 檔資料")
        return result

    print("  ⚠ 融資融券 API 失敗，跳過")
    return pd.DataFrame()


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 7 ▸ 基本面（FinMind，選用）
# ════════════════════════════════════════════════════════════════

def get_fundamentals(tickers: list[str]) -> pd.DataFrame:
    if not FINMIND_OK or not FINMIND_TOKEN:
        print("\n【Step 7】FinMind token 未設定 → 跳過基本面（可在 CONFIG 填入）")
        return pd.DataFrame()

    print(f"\n【Step 7】取得基本面資料（FinMind）")
    try:
        api = FMLoader()
        api.login_by_token(api_token=FINMIND_TOKEN)
        start_q = (END_DATE - timedelta(days=450)).strftime("%Y-%m-%d")
        end_q   = END_DATE.strftime("%Y-%m-%d")

        rows = []
        for t in tqdm(tickers, desc="  基本面", ncols=72):
            try:
                # P/E, P/B
                per_df = api.taiwan_stock_per(stock_id=t, start_date=start_q, end_date=end_q)
                pe, pb = np.nan, np.nan
                if per_df is not None and not per_df.empty:
                    last_per = per_df.sort_values("date").iloc[-1]
                    pe = last_per.get("PER", np.nan)
                    pb = last_per.get("PBR", np.nan)

                # ROE, GPM
                fs = api.taiwan_stock_financial_statement(stock_id=t, start_date=start_q, end_date=end_q)
                roe, gpm = np.nan, np.nan
                if fs is not None and not fs.empty:
                    r = fs[fs["type"] == "ReturnOnEquity"]
                    g = fs[fs["type"] == "GrossProfitMargin"]
                    roe = float(r["value"].iloc[-1]) if not r.empty else np.nan
                    gpm = float(g["value"].iloc[-1]) if not g.empty else np.nan

                rows.append({"id": t, "PE": pe, "PB": pb, "ROE": roe, "GPM": gpm})
                time.sleep(0.25)
            except Exception:
                rows.append({"id": t, "PE": np.nan, "PB": np.nan, "ROE": np.nan, "GPM": np.nan})

        df = pd.DataFrame(rows)
        print(f"  取得 {df['PE'].notna().sum()} 檔 P/E、{df['ROE'].notna().sum()} 檔 ROE")
        return df
    except Exception as e:
        print(f"  ⚠ FinMind 錯誤：{e}")
        return pd.DataFrame()


# ════════════════════════════════════════════════════════════════
#  ▌ STEP 8 ▸ 風險指標：Beta / Sharpe / Sortino
# ════════════════════════════════════════════════════════════════

def calc_risk(t: str, df: pd.DataFrame, bench: pd.DataFrame) -> dict:
    """Beta / Sharpe / Sortino（年化，rf=RISK_FREE_RATE）。失敗回傳 {}."""
    try:
        r = df["Close"].pct_change().dropna()
        b = bench["Close"].pct_change().dropna()
        aligned = pd.concat([r, b], axis=1, join="inner").dropna()
        if len(aligned) < MIN_RISK_OBS:
            return {}

        sr = aligned.iloc[:, 0].to_numpy()
        br = aligned.iloc[:, 1].to_numpy()

        # Beta（cov/var）— 使用 ddof=0 保持和 np.var 一致
        cov  = float(np.cov(sr, br, ddof=0)[0, 1])
        vb   = float(np.var(br, ddof=0))
        beta = cov / vb if vb > 0 else np.nan

        # 年化超額收益
        rf_d     = RISK_FREE_RATE / TRADING_DAYS_YEAR
        excess   = sr - rf_d
        ex_mean  = float(excess.mean())
        ex_std   = float(excess.std(ddof=1))
        sq_root  = np.sqrt(TRADING_DAYS_YEAR)

        sharpe = (ex_mean / ex_std * sq_root) if ex_std > 0 else np.nan

        # Sortino（只取下行波動）
        downside = excess[excess < 0]
        dstd     = float(downside.std(ddof=1)) if len(downside) > 5 else np.nan
        sortino  = (ex_mean / dstd * sq_root) if dstd and dstd > 0 else np.nan

        return {
            "beta":    round(float(beta),    3) if not np.isnan(beta)    else np.nan,
            "sharpe":  round(float(sharpe),  3) if not np.isnan(sharpe)  else np.nan,
            "sortino": round(float(sortino), 3) if not np.isnan(sortino) else np.nan,
        }
    except Exception as e:
        log.debug("calc_risk(%s) 失敗：%s", t, e)
        return {}


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
    try:
        d = df.copy()
        d["ma20"] = d["Close"].rolling(MA_PERIOD).mean()
        d = d.dropna()
        if len(d) < 2:
            return {}
        last, prev = d.iloc[-1], d.iloc[-2]

        touch_ma20 = (
            float(last["Low"])   <= float(last["ma20"]) * 1.005 and
            float(last["Close"]) >= float(last["ma20"]) * 0.995
        )
        foreign_ok   = consec >= MIN_CONSEC_BUY
        near_fib     = tech.get("near_fib50", 0) == 1
        vol_ok       = tech.get("vol_ratio", 1.0) >= 0.7

        # 趨勢確認：今日收盤高於前日
        trend_ok     = float(last["Close"]) > float(prev["Close"])

        signal        = touch_ma20 and foreign_ok and vol_ok
        strong_signal = signal and (near_fib or trend_ok)

        return {
            "touch_ma20":    int(touch_ma20),
            "foreign_ok":    int(foreign_ok),
            "near_fib50":    int(near_fib),
            "vol_ok":        int(vol_ok),
            "trend_ok":      int(trend_ok),
            "entry_signal":  int(signal),
            "strong_signal": int(strong_signal),
            "price":         round(float(last["Close"]), 2),
            "ma20":          round(float(last["ma20"]),  2),
            "fib50_target":  tech.get("fib50", np.nan),
            "fib_1to1_tp":   round(tech.get("swing_hi", float(last["Close"])), 2),  # 斐波納契1:1目標
        }
    except Exception:
        return {}


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

def score_stock(f: pd.Series, tech: dict, margin: dict | None,
                fund: dict | None, risk: dict) -> float:
    s = 0.0

    # ── 外資 (30) ─────────────────────────────────────────────────
    s += min(f.get("consec_buy", 0) * 2, 16)   # 連買天數，每天2分上限16
    rp = f.get("rank_pct", 1.0)
    if 0.35 <= rp <= 0.70:
        s += 14                                  # 甜蜜區間最高分
    elif 0.25 <= rp < 0.35 or 0.70 < rp <= 0.80:
        s += 8

    # ── 技術面 (25) ───────────────────────────────────────────────
    s += min(tech.get("tech_score", 0), 25)

    # ── 融資融券 (15) ─────────────────────────────────────────────
    if margin:
        sr = margin.get("squeeze_ratio", 0)
        if sr >= 0.20:   s += 8     # 高嘎空潛力
        elif sr >= 0.10: s += 5
        elif sr >= 0.05: s += 2
        if 0.05 < sr < 0.30:
            s += 7                  # 融資緩增、融券有量（健康籌碼結構）

    # ── 基本面 (15) ───────────────────────────────────────────────
    if fund:
        roe = fund.get("ROE", np.nan)
        gpm = fund.get("GPM", np.nan)
        if not np.isnan(roe):
            s += 8 if roe >= 15 else (5 if roe >= 10 else (2 if roe >= 5 else 0))
        if not np.isnan(gpm):
            s += 7 if gpm >= 30 else (4 if gpm >= 15 else (1 if gpm >= 5 else 0))

    # ── 風險指標 (15) ─────────────────────────────────────────────
    sharpe  = risk.get("sharpe",  np.nan)
    sortino = risk.get("sortino", np.nan)
    beta    = risk.get("beta",    np.nan)

    if not np.isnan(sharpe):
        s += 5 if sharpe >= 1.5 else (3 if sharpe >= 0.8 else (1 if sharpe >= 0.3 else 0))
    if not np.isnan(sortino):
        s += 5 if sortino >= 2.0 else (3 if sortino >= 1.0 else (1 if sortino >= 0.5 else 0))
    if not np.isnan(beta):
        s += 5 if 0.8 <= beta <= 1.5 else (2 if 0.5 <= beta < 0.8 else 0)

    return round(min(s, 100.0), 1)


# ════════════════════════════════════════════════════════════════
#  ▌ OUTPUT ▸ 圖表 + CSV
# ════════════════════════════════════════════════════════════════

def save_output(result: pd.DataFrame, entry: pd.DataFrame):
    # ── CSV ───────────────────────────────────────────────────────
    result.to_csv(OUTPUT_DIR / "candidates.csv",     index=False, encoding="utf-8-sig")
    entry.to_csv (OUTPUT_DIR / "entry_signals.csv",  index=False, encoding="utf-8-sig")

    # ── 評分散點圖 ────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(f"台股波段選股系統 — 候選股分析  ({END_DATE.strftime('%Y-%m-%d')})", fontsize=13)

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
    plt.savefig(OUTPUT_DIR / "score_chart.png", dpi=140, bbox_inches="tight")
    plt.close()

    print(f"\n📁 輸出目錄：./{OUTPUT_DIR}/")
    print(f"   ├── candidates.csv      ← 前{TARGET}名候選股（含所有指標）")
    print(f"   ├── entry_signals.csv   ← 目前有進場訊號者")
    print(f"   └── score_chart.png     ← 評分分佈圖")


# ════════════════════════════════════════════════════════════════
#  ▌ MAIN
# ════════════════════════════════════════════════════════════════

def main():
    t_start = time.time()
    print("═" * 60)
    print("  台股波段選股系統 v1.0")
    print(f"  執行時間：{END_DATE.strftime('%Y-%m-%d %H:%M')}")
    print("═" * 60)

    # ── 1. 股票清單 ───────────────────────────────────────────────
    stock_df  = get_stock_list()
    valid_ids = set(stock_df["id"].tolist())

    # ── 2. 外資排行 ───────────────────────────────────────────────
    foreign_df = get_foreign_ranking(valid_ids)
    if len(foreign_df) < 10:
        print("❌ 外資候選股不足，程序中止"); return

    cand_ids = foreign_df["id"].tolist()

    # ── 3. 價量資料 ───────────────────────────────────────────────
    price_data = download_prices(cand_ids)

    # 大盤基準（0050）— 帶重試，避免一次失敗就讓所有風險指標為 NaN
    bench_raw = _yf_download(BENCHMARK_TICKER, min_rows=60, retries=3)
    if bench_raw is None or "Close" not in bench_raw.columns:
        log.warning("⚠ 無法取得 %s 基準資料 → 風險指標將全部跳過", BENCHMARK_TICKER)
        benchmark = pd.DataFrame()
    else:
        benchmark = bench_raw[["Close"]].copy()

    # ── 4. 技術分析 ───────────────────────────────────────────────
    print("\n【Step 4】技術面分析")
    tech_map = {}
    for t in tqdm(cand_ids, desc="  技術指標", ncols=72):
        if t in price_data:
            r = analyze_tech(t, price_data[t])
            if r:
                tech_map[t] = r
    print(f"  完成 {len(tech_map)} 檔")

    # ── 5. 產業排名 ───────────────────────────────────────────────
    top_ind = get_top_industries()

    # ── 6. 融資融券 ───────────────────────────────────────────────
    margin_df  = get_margin(cand_ids)
    margin_map = {} if margin_df.empty else margin_df.set_index("id").to_dict("index")

    # ── 7. 基本面 ─────────────────────────────────────────────────
    fund_df  = get_fundamentals(cand_ids)
    fund_map = {} if fund_df.empty else fund_df.set_index("id").to_dict("index")

    # ── 8. 風險指標 ───────────────────────────────────────────────
    print("\n【Step 8】計算 Beta / Sharpe / Sortino")
    risk_map = {}
    for t in tqdm(cand_ids, desc="  風險指標", ncols=72):
        if t in price_data and not benchmark.empty:
            risk_map[t] = calc_risk(t, price_data[t], benchmark)

    # ── 9 & 10. 訊號 + 評分 ─────────────────────────────────────
    print("\n【Step 9/10】進場訊號 + 綜合評分")

    # 預先建立 dict 索引：O(1) 查詢取代 O(N) DataFrame 搜尋
    f_idx     = foreign_df.set_index("id").to_dict("index")
    meta_idx  = stock_df.set_index("id")[["name", "sector"]].to_dict("index")

    all_rows = []
    sig_rows = []

    for t in cand_ids:
        if t not in tech_map:
            continue

        f_dict = f_idx.get(t, {})
        f      = pd.Series(f_dict) if f_dict else pd.Series(dtype=float)
        tech   = tech_map[t]
        mg     = margin_map.get(t)
        fu     = fund_map.get(t)
        risk   = risk_map.get(t, {})
        consec = int(f_dict.get("consec_buy", 0))

        score = score_stock(f, tech, mg, fu, risk)

        meta   = meta_idx.get(t, {})
        name   = meta.get("name", "")
        sector = meta.get("sector", "")
        ind_ok = int(not top_ind or any(ind in sector for ind in top_ind))

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
            # 分數
            "技術面分數": tech["tech_score"],
            "total_score": score,
        }
        all_rows.append(row)

        # 進場訊號
        if t in price_data:
            sig = detect_entry(t, price_data[t], tech, consec)
            if sig.get("entry_signal"):
                sig_rows.append({
                    "股票代號": t, "股票名稱": name,
                    "收盤價": sig.get("price"),
                    "MA20":   sig.get("ma20"),
                    "Fib50目標": sig.get("fib50_target"),
                    "1:1停利目標": sig.get("fib_1to1_tp"),
                    "強訊號": sig.get("strong_signal"),
                    "外資連買天數": consec,
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

    # ── 輸出 ─────────────────────────────────────────────────────
    save_output(result_df, entry_df)

    # ── 終端機摘要 ────────────────────────────────────────────────
    elapsed = (time.time() - t_start) / 60
    print(f"\n{'═'*60}")
    print(f"✅ 完成！耗時 {elapsed:.1f} 分鐘")

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
    main()