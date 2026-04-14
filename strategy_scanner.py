"""
台股波段選股系統 v1.0
策略：外資買超中後段排名 + 技術面 + 融資融券 + 基本面 + 風險指標
Python 3.12+ | 執行前確認已安裝：pip install lxml openpyxl finmind
"""

import warnings
warnings.filterwarnings("ignore")

import os, time, re
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import pandas as pd
import numpy as np
import yfinance as yf
import pandas_ta as ta
from scipy import stats
from tqdm import tqdm
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
# ════════════════════════════════════════════════════════════════
#  ▌ 智慧隨機頻率調節器 (Monkey Patch)
# ════════════════════════════════════════════════════════════════
import random
import threading

# 備份原始的 requests.get
_original_get = requests.get
# 建立執行緒鎖，防止多執行緒同時發送請求
_request_lock = threading.Lock()

def _smart_delayed_get(*args, **kwargs):
    with _request_lock:
        time.sleep(random.uniform(0.7,1.5 ))
        return _original_get(*args, **kwargs)

# 覆寫底層的 requests.get，讓全域所有的爬蟲動作都套用此延遲規則
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

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

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


def safe_get(url: str, params: dict = None, retries: int = 3) -> dict | None:
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=14)
            if r.status_code == 200:
                return r.json()
        except Exception:
            time.sleep(1.5 * (i + 1))
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
    before = len(df)
    df["sector"] = df["sector"].fillna("").astype(str)
    df = df[~df["sector"].apply(lambda x: any(k in x for k in FIN_KW))]
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

    # 連續買超天數（從最近日往前數，碰到非正即停）
    bool_mat = master[day_cols].values > 0
    consec   = np.zeros(len(master), dtype=int)
    for j in range(bool_mat.shape[1]):
        mask = bool_mat[:, j]
        still_running = (consec == j)
        consec[still_running & mask] += 1

    master["consec_buy"] = consec

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

def _dl_one(t: str) -> tuple:
    try:
        raw = yf.download(f"{t}.TW", start=START_DATE, end=END_DATE,
                          progress=False, auto_adjust=True, timeout=15)
        if raw.empty or len(raw) < 60:
            return t, None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        return t, raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    except Exception:
        return t, None


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
        stoch = ta.stoch(d["High"], d["Low"], d["Close"], k=9, d=3, smooth_k=3)
        K = float(stoch.iloc[-1, 0]) if stoch is not None and not stoch.empty else 50.0
        D = float(stoch.iloc[-1, 1]) if stoch is not None and not stoch.empty else 50.0
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
    for date_str in recent_weekdays(5):
        data = safe_get(
            "https://www.twse.com.tw/exchangeReport/MI_MARGN",
            params={"response": "json", "date": date_str, "selectType": "ALL"}
        )
        if data and data.get("stat") == "OK" and data.get("data"):
            cols = data["fields"]
            df   = pd.DataFrame(data["data"], columns=cols)
            id_c = next((c for c in cols if "代號" in c or "代碼" in c), cols[0])
            df["id"] = df[id_c].astype(str).str.strip()
            df = df[df["id"].isin(set(tickers))].copy()

            # 標準化數字欄位
            for c in [cc for cc in cols if cc not in [id_c, cols[1]]]:
                df[c] = to_numeric_series(df[c])

            # 融資餘額 / 融券餘額
            mb_c = next((c for c in cols if "融資" in c and "餘額" in c and "股數" not in c), None)
            ms_c = next((c for c in cols if "融券" in c and "餘額" in c and "股數" not in c), None)

            rows = []
            for _, row in df.iterrows():
                mb = float(row[mb_c]) if mb_c and pd.notna(row.get(mb_c)) else 0.0
                ms = float(row[ms_c]) if ms_c and pd.notna(row.get(ms_c)) else 0.0
                # 嘎空比（融券/融資，越高嘎空潛力越大）
                squeeze = ms / mb if mb > 0 else 0.0
                rows.append({"id": row["id"], "margin_buy": mb,
                              "margin_short": ms, "squeeze_ratio": round(squeeze, 4)})

            result = pd.DataFrame(rows)
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
    try:
        r = df["Close"].pct_change().dropna()
        b = bench["Close"].pct_change().dropna()
        aligned = pd.concat([r, b], axis=1, join="inner").dropna()
        if len(aligned) < 60:
            return {}

        sr, br = aligned.iloc[:, 0], aligned.iloc[:, 1]

        # Beta
        cov  = np.cov(sr.values, br.values)[0, 1]
        vb   = np.var(br.values)
        beta = cov / vb if vb > 0 else np.nan

        # Sharpe（年化，rf=1.5%）
        rf_d   = 0.015 / 252
        excess = sr - rf_d
        sharpe = (excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else np.nan

        # Sortino（下行波動）
        down   = excess[excess < 0]
        dstd   = down.std() if len(down) > 5 else np.nan
        sortino = (excess.mean() / dstd * np.sqrt(252)) if dstd and dstd > 0 else np.nan

        return {
            "beta":    round(beta,    3) if not np.isnan(beta)    else np.nan,
            "sharpe":  round(sharpe,  3) if not np.isnan(sharpe)  else np.nan,
            "sortino": round(sortino, 3) if not np.isnan(sortino) else np.nan,
        }
    except Exception:
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

    # 右：技術面 vs 外資連買天數（顏色=總分）
    if "tech_score" in result.columns and "consec_buy" in result.columns:
        sc = axes[1].scatter(
            result["tech_score"], result["consec_buy"],
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

    # 大盤基準（0050）
    bench_raw = yf.download("0050.TW", start=START_DATE, end=END_DATE,
                             progress=False, auto_adjust=True)
    if isinstance(bench_raw.columns, pd.MultiIndex):
        bench_raw.columns = bench_raw.columns.get_level_values(0)
    benchmark = bench_raw[["Close"]].copy() if not bench_raw.empty else pd.DataFrame()

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
    f_idx    = foreign_df.set_index("id")
    all_rows = []
    sig_rows = []

    for t in cand_ids:
        if t not in tech_map:
            continue

        f    = f_idx.loc[t] if t in f_idx.index else pd.Series(dtype=float)
        tech = tech_map[t]
        mg   = margin_map.get(t)
        fu   = fund_map.get(t)
        risk = risk_map.get(t, {})
        consec = int(f.get("consec_buy", 0))

        score = score_stock(f, tech, mg, fu, risk)

        # 股票基本資訊
        meta   = stock_df[stock_df["id"] == t]
        name   = meta["name"].values[0]   if len(meta) else ""
        sector = meta["sector"].values[0] if len(meta) else ""
        ind_ok = int(not top_ind or any(ind in sector for ind in top_ind))

        row = {
            "股票代號":      t,
            "股票名稱":      name,
            "產業別":        sector,
            "產業達標(前5)": ind_ok,
            # 外資
            "外資累計淨買(萬股)": round(float(f.get("cum_net", 0)), 1),
            "連續外資買超天數":    consec,
            "外資排名百分位":      round(float(f.get("rank_pct", 1.0)), 3),
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
            "consec_buy": consec,
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