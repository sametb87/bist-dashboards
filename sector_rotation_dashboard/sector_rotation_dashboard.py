#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sector_rotation_dashboard.py
============================
US sektör rotasyon & tema takip dashboard'u.

4 sekme:
  Tab 1 - RRG Rotation Map  : makro sektörlerin RS-Ratio / RS-Momentum kadranları (+ kuyruk izi)
  Tab 2 - Theme Heatmap     : tüm temalar için 1W/1M/3M/6M getiri + RS vs SPY, renk kodlu grid
  Tab 3 - Theme Leaders     : dual-momentum skoruyla sıralı en güçlü/en zayıf temalar
  Tab 4 - Drilldown         : ETF seç -> 21 EMA cloud grafiği + ATR + yfinance top-10 holdings

Veri  : yfinance, curl_cffi Chrome-impersonate session ile.
Çıktı : self-contained tek HTML (lightweight-charts CDN'den).

Kullanım:
    python3 sector_rotation_dashboard.py
    # ardından dashboard_rotation.html tarayıcıda açılır
"""

import json
import sys
import time
import math
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ----------------------------------------------------------------------------
# 1) curl_cffi Chrome session + yfinance  (yfinance auth çözümün)
# ----------------------------------------------------------------------------
try:
    from curl_cffi import requests as cffi_requests
    _SESSION = cffi_requests.Session(impersonate="chrome")
except Exception as e:
    print(f"[uyarı] curl_cffi yüklenemedi ({e}); düz yfinance ile denenecek.")
    _SESSION = None

import yfinance as yf


# ----------------------------------------------------------------------------
# 2) EVREN  ── sektör -> {tema: ticker}
#    Saf ETF olmayan dar temalarda EN YAKIN PROXY kullanıldı (notla işaretli).
# ----------------------------------------------------------------------------
# Tab 1 (RRG) sadece makro sektör ETF'lerini kullanır:
SECTOR_ETFS = {
    "Technology":  "XLK",
    "Financials":  "XLF",
    "Health Care": "XLV",
    "Energy":      "XLE",
    "Industrials": "XLI",
    "Cons. Disc.": "XLY",
    "Cons. Stap.": "XLP",
    "Materials":   "XLB",
    "Utilities":   "XLU",
    "Real Estate": "XLRE",
    "Comm. Svcs.": "XLC",
}

# Tab 2/3/4 için genişletilmiş tema evreni.
# proxy=True  -> dar tema, saf ETF yok, en yakın ETF proxy olarak kullanılıyor.
THEME_UNIVERSE = {
    "Technology": [
        ("Semiconductors",        "SMH",  False),
        ("Semis (equal-wt)",      "XSD",  False),
        ("Software",              "IGV",  False),
        ("Cloud / SaaS",          "WCLD", False),
        ("Cloud Infra",           "SKYY", False),
        ("Cybersecurity",         "CIBR", False),
        ("AI",                    "AIQ",  False),
        ("Robotics & Automation", "BOTZ", False),
        ("Quantum (broad tech)",  "QTUM", True),   # saf kuantum yok; geniş future-tech
        ("Networking",            "IGV",  True),   # saf networking ETF yok; software proxy
        ("Data Center REITs",     "DTCR", False),
        ("Fintech",               "FINX", False),
        ("Blockchain (equity)",   "BLOK", False),
    ],
    "Crypto": [
        ("Bitcoin (spot)",        "IBIT", False),
        ("Ethereum (spot)",       "ETHA", False),
        ("Crypto Miners",         "WGMI", False),
        ("Crypto Industry",       "BITQ", False),
    ],
    "Space & Defense": [
        ("Space",                 "ARKX", False),
        ("Aerospace & Defense",   "ITA",  False),
        ("Defense (equal-wt)",    "XAR",  False),
        ("Drones / UAV",          "ARKX", True),   # saf drone ETF yok; space&def proxy
    ],
    "Clean Energy": [
        ("Solar",                 "TAN",  False),
        ("Clean Energy (broad)",  "ICLN", False),
        ("Batteries",             "BATT", False),
        ("Lithium",               "LIT",  False),
        ("Smart Grid",            "GRID", False),
        ("Uranium / Nuclear",     "URA",  False),
        ("Hydrogen",              "HYDR", True),   # likidite düşük; proxy say
    ],
    "Health Care": [
        ("Biotech",               "XBI",  False),
        ("Biotech (cap-wt)",      "IBB",  False),
        ("Medical Devices",       "IHI",  False),
        ("Genomics",              "ARKG", False),
    ],
    "Financials": [
        ("Banks",                 "KBE",  False),
        ("Regional Banks",        "KRE",  False),
        ("Capital Markets",       "IAI",  False),
        ("Insurance",             "KIE",  False),
    ],
    "Consumer": [
        ("Retail",                "XRT",  False),
        ("Homebuilders",          "XHB",  False),
        ("Travel & Leisure",      "AWAY", False),
        ("Airlines",              "JETS", False),
    ],
    "Industrials": [
        ("Transport",             "IYT",  False),
        ("Infrastructure",        "PAVE", False),
    ],
    "Materials / Metals": [
        ("Gold Miners",           "GDX",  False),
        ("Jr. Gold Miners",       "GDXJ", False),
        ("Copper",                "COPX", False),
        ("Steel",                 "SLX",  False),
        ("Rare Earth",            "REMX", False),
    ],
}

BENCHMARK = "SPY"

# Skorlama pencereleri (gün) ve ağırlıkları
WINDOWS = {"1W": 5, "1M": 21, "3M": 63, "6M": 126}
MOMENTUM_WEIGHTS = {"1M": 0.30, "3M": 0.40, "6M": 0.30}  # dual-momentum bileşimi

RRG_TAIL = 8        # RRG kuyruk uzunluğu (hafta)
HISTORY_DAYS = 400  # indirilecek geçmiş


# ----------------------------------------------------------------------------
# 3) Veri indirme
# ----------------------------------------------------------------------------
def all_tickers():
    tk = set(SECTOR_ETFS.values()) | {BENCHMARK}
    for items in THEME_UNIVERSE.values():
        for _, t, _ in items:
            tk.add(t)
    return sorted(tk)


def download_history(tickers):
    """Tüm tickerlar için günlük OHLCV indir. Dönen: {ticker: DataFrame}."""
    print(f"[indir] {len(tickers)} sembol, ~{HISTORY_DAYS} gün ...")
    kwargs = dict(period=f"{HISTORY_DAYS}d", interval="1d",
                  auto_adjust=True, progress=False, group_by="ticker")
    if _SESSION is not None:
        kwargs["session"] = _SESSION

    data = {}
    # toplu indir, hata olursa tek tek dene
    try:
        raw = yf.download(tickers, **kwargs)
    except Exception as e:
        print(f"[uyarı] toplu indirme hatası: {e}; tek tek denenecek.")
        raw = None

    for t in tickers:
        df = None
        if raw is not None:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    df = raw[t].dropna(how="all")
                else:
                    df = raw.dropna(how="all")
            except Exception:
                df = None
        if df is None or df.empty:
            try:
                tk = yf.Ticker(t, session=_SESSION) if _SESSION else yf.Ticker(t)
                df = tk.history(period=f"{HISTORY_DAYS}d", interval="1d", auto_adjust=True)
            except Exception as e:
                print(f"  [atla] {t}: {e}")
                continue
        if df is None or df.empty:
            print(f"  [atla] {t}: veri yok")
            continue
        df = df.rename(columns=str.title)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        data[t] = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        time.sleep(0.02)
    print(f"[indir] {len(data)} sembol başarıyla alındı.")
    return data


def fetch_top_holdings(ticker):
    """yfinance ile ETF top-10 holdings (varsa). Liste: [{symbol, name, weight}]."""
    try:
        tk = yf.Ticker(ticker, session=_SESSION) if _SESSION else yf.Ticker(ticker)
        fd = tk.funds_data
        th = fd.top_holdings  # DataFrame: index=symbol, cols ['Name','Holding Percent']
        if th is None or len(th) == 0:
            return []
        out = []
        for sym, row in th.iterrows():
            name = row.get("Name", "")
            w = row.get("Holding Percent", None)
            out.append({
                "symbol": str(sym),
                "name": str(name),
                "weight": round(float(w) * 100, 2) if w is not None and not pd.isna(w) else None,
            })
        return out
    except Exception:
        return []


# ----------------------------------------------------------------------------
# 4) Hesaplamalar
# ----------------------------------------------------------------------------
def pct_return(close, n):
    if len(close) <= n:
        return None
    a, b = close.iloc[-1], close.iloc[-n - 1]
    if b == 0 or pd.isna(a) or pd.isna(b):
        return None
    return (a / b - 1.0) * 100.0


def atr_pct(df, n=14):
    if len(df) < n + 1:
        return None
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean().iloc[-1]
    last = c.iloc[-1]
    if pd.isna(atr) or last == 0:
        return None
    return round(atr / last * 100.0, 2)


def rs_line(close, bench_close):
    """Relatif güç çizgisi = ticker/benchmark, ortak tarihlere hizalı."""
    j = pd.concat([close, bench_close], axis=1, keys=["t", "b"]).dropna()
    if j.empty:
        return None
    return j["t"] / j["b"]


def rs_vs_bench(close, bench_close, n):
    """Belirli pencerede RS değişimi (ETF getirisi - benchmark getirisi, %)."""
    rt, rb = pct_return(close, n), pct_return(bench_close, n)
    if rt is None or rb is None:
        return None
    return round(rt - rb, 2)


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def compute_rrg(sector_data, bench_close, tail=RRG_TAIL):
    """
    Basitleştirilmiş RRG: haftalık RS çizgisinden RS-Ratio ve RS-Momentum.
    Dönen: {sector: {ticker, points:[{x:rs_ratio, y:rs_mom}], quadrant}}
    """
    out = {}
    palette = ["#58a6ff", "#26a69a", "#ef5350", "#d29922", "#a371f7",
               "#ff7b72", "#2ea043", "#db61a2", "#56d4dd", "#e3b341", "#f0883e"]
    for idx, (sector, tk) in enumerate(sector_data.items()):
        rs = rs_line(tk["Close"], bench_close)
        if rs is None or len(rs) < 60:
            continue
        rs_w = rs.resample("W-FRI").last().dropna()
        if len(rs_w) < 30:
            continue
        # RS-Ratio: RS çizgisinin kendi ortalamasına normalize edilmiş hali
        m = rs_w.rolling(10).mean()
        s = rs_w.rolling(10).std()
        rs_ratio = 100 + (rs_w - m) / s
        # RS-Momentum: RS-Ratio'nun değişim momentumu
        rs_mom = 100 + (rs_ratio - rs_ratio.rolling(5).mean()) / rs_ratio.rolling(5).std()
        df = pd.concat([rs_ratio, rs_mom], axis=1, keys=["ratio", "mom"]).dropna()
        if df.empty:
            continue
        df = df.tail(tail)
        pts = [{"x": round(float(r.ratio), 3), "y": round(float(r.mom), 3)}
               for r in df.itertuples()]
        last = pts[-1]
        if last["x"] >= 100 and last["y"] >= 100:
            quad = "Leading"
        elif last["x"] >= 100 and last["y"] < 100:
            quad = "Weakening"
        elif last["x"] < 100 and last["y"] < 100:
            quad = "Lagging"
        else:
            quad = "Improving"
        out[sector] = {"ticker": SECTOR_ETFS[sector], "points": pts,
                       "quadrant": quad, "color": palette[idx % len(palette)]}
    return out


def rs_roc(close, bench_close, n):
    """RS çizgisinin (ETF/SPY) n-günlük yüzde değişimi. Erken sinyalin çekirdeği."""
    rs = rs_line(close, bench_close)
    if rs is None or len(rs) <= n:
        return None
    a, b = rs.iloc[-1], rs.iloc[-n - 1]
    if b == 0 or pd.isna(a) or pd.isna(b):
        return None
    return round((a / b - 1.0) * 100.0, 2)


def rs_cross_flag(close, bench_close, fast=5, slow=20):
    """Kısa RS-momentum uzun RS-momentum'u yukarı kesti mi (son ~3 gün)? Erken giriş."""
    rs = rs_line(close, bench_close)
    if rs is None or len(rs) < slow + 4:
        return False
    f = rs.pct_change(fast)
    s = rs.pct_change(slow)
    diff = (f - s).dropna()
    if len(diff) < 4:
        return False
    # son barda pozitif, 3 bar önce negatif -> taze yukarı kesişim
    return bool(diff.iloc[-1] > 0 and diff.iloc[-4] <= 0)


def rs_new_high_flag(close, bench_close, lookback=20):
    """RS çizgisi son `lookback` günün zirvesini bugün kırdı mı? RS breakout."""
    rs = rs_line(close, bench_close)
    if rs is None or len(rs) < lookback + 1:
        return False
    window = rs.iloc[-(lookback + 1):]
    return bool(window.iloc[-1] >= window.iloc[:-1].max())


def early_score(roc1, roc3, roc5, cross, newhigh):
    """Erken rotasyon bileşik skoru: kısa RS-ROC ağırlıklı + bayrak bonusları."""
    s = 0.0
    if roc5 is not None: s += 0.30 * roc5
    if roc3 is not None: s += 0.40 * roc3
    if roc1 is not None: s += 0.30 * roc1
    if cross:   s += 1.5   # taze kesişim bonusu
    if newhigh: s += 1.5   # RS yeni zirve bonusu
    return round(s, 2)


def build_theme_rows(data, bench_close):
    """Tab 2/3/5 için her tema satırını üret."""
    rows = []
    for sector, items in THEME_UNIVERSE.items():
        for theme, tk, is_proxy in items:
            if tk not in data:
                continue
            df = data[tk]
            close = df["Close"]
            rets = {k: pct_return(close, n) for k, n in WINDOWS.items()}
            rs = {k: rs_vs_bench(close, bench_close, n) for k, n in WINDOWS.items()}
            # dual-momentum skoru (RS bazlı, eksikse 0)
            score = 0.0
            for k, w in MOMENTUM_WEIGHTS.items():
                v = rs.get(k)
                if v is not None:
                    score += w * v
            # erken rotasyon metrikleri (kısa pencere RS hızlanması)
            roc1 = rs_roc(close, bench_close, 1)
            roc3 = rs_roc(close, bench_close, 3)
            roc5 = rs_roc(close, bench_close, 5)
            cross = rs_cross_flag(close, bench_close)
            newhigh = rs_new_high_flag(close, bench_close)
            rows.append({
                "sector": sector,
                "theme": theme,
                "ticker": tk,
                "proxy": is_proxy,
                "ret": rets,
                "rs": rs,
                "atr": atr_pct(df),
                "score": round(score, 2),
                "early": {
                    "roc1": roc1, "roc3": roc3, "roc5": roc5,
                    "cross": cross, "newhigh": newhigh,
                    "score": early_score(roc1, roc3, roc5, cross, newhigh),
                },
            })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def build_chart_payload(data, bench_close):
    """Tab 4 drilldown: her ticker için OHLC + 21 EMA cloud + RS vs SPY çizgisi."""
    out = {}
    for tk, df in data.items():
        d = df.tail(180).copy()
        ema_high = ema(d["High"], 21)
        ema_low = ema(d["Low"], 21)
        candles, cloud_hi, cloud_lo, vol = [], [], [], []
        for ts, row in d.iterrows():
            t = int(pd.Timestamp(ts).timestamp())
            candles.append({"time": t, "open": round(row.Open, 2),
                            "high": round(row.High, 2), "low": round(row.Low, 2),
                            "close": round(row.Close, 2)})
            vol.append({"time": t, "value": int(row.Volume) if not pd.isna(row.Volume) else 0,
                        "color": "rgba(38,166,154,0.4)" if row.Close >= row.Open
                                 else "rgba(239,83,80,0.4)"})
        for ts, v in ema_high.items():
            if not pd.isna(v):
                cloud_hi.append({"time": int(pd.Timestamp(ts).timestamp()), "value": round(v, 2)})
        for ts, v in ema_low.items():
            if not pd.isna(v):
                cloud_lo.append({"time": int(pd.Timestamp(ts).timestamp()), "value": round(v, 2)})
        # RS vs SPY çizgisi (100'e normalize, ortak tarihler)
        rs_pts = []
        rs = rs_line(df["Close"], bench_close)
        if rs is not None:
            rs = rs.tail(180)
            base = rs.iloc[0]
            if base and not pd.isna(base):
                for ts, v in rs.items():
                    if not pd.isna(v):
                        rs_pts.append({"time": int(pd.Timestamp(ts).timestamp()),
                                       "value": round(v / base * 100, 2)})
        out[tk] = {"candles": candles, "emaHigh": cloud_hi, "emaLow": cloud_lo,
                   "volume": vol, "rs": rs_pts}
    return out


# ----------------------------------------------------------------------------
# 5) HTML üretimi
# ----------------------------------------------------------------------------
def build_html(rrg, theme_rows, charts, holdings, meta):
    payload = {
        "rrg": rrg,
        "themes": theme_rows,
        "charts": charts,
        "holdings": holdings,
        "windows": list(WINDOWS.keys()),
        "meta": meta,
    }
    data_json = json.dumps(payload, ensure_ascii=False)

    html = """<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"><meta http-equiv="Pragma" content="no-cache"><meta http-equiv="Expires" content="0">
<title>US Sektör Rotasyon Dashboard</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root{--bg:#0e1117;--panel:#161b22;--border:#2d333b;--txt:#e6edf3;--muted:#8b949e;
        --green:#26a69a;--red:#ef5350;--accent:#58a6ff;}
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--txt);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
  header{padding:14px 20px;border-bottom:1px solid var(--border);display:flex;
         align-items:baseline;gap:14px;flex-wrap:wrap;}
  header h1{font-size:17px;margin:0;font-weight:600;}
  header .meta{color:var(--muted);font-size:12px;}
  .tabs{display:flex;gap:4px;padding:0 20px;border-bottom:1px solid var(--border);background:var(--bg);}
  .tab{padding:11px 16px;cursor:pointer;color:var(--muted);font-size:13px;
       border-bottom:2px solid transparent;user-select:none;}
  .tab:hover{color:var(--txt);}
  .tab.active{color:var(--txt);border-bottom-color:var(--accent);}
  .view{display:none;padding:18px 20px;}
  .view.active{display:block;}
  table{border-collapse:collapse;width:100%;font-size:12.5px;}
  th,td{padding:7px 9px;text-align:right;border-bottom:1px solid var(--border);white-space:nowrap;}
  th{color:var(--muted);font-weight:500;text-align:right;cursor:pointer;user-select:none;position:sticky;top:0;background:var(--bg);}
  th.l,td.l{text-align:left;}
  tr:hover td{background:#1c2230;}
  .sector-tag{color:var(--muted);font-size:11px;}
  .proxy{color:#d29922;font-size:10px;border:1px solid #d29922;border-radius:3px;padding:0 4px;margin-left:5px;}
  .tk{color:var(--accent);cursor:pointer;font-weight:600;}
  .tk:hover{text-decoration:underline;}
  .pos{color:var(--green);} .neg{color:var(--red);} .zero{color:var(--muted);}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
  @media(max-width:900px){.grid2{grid-template-columns:1fr;}}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px;}
  .card h3{margin:0 0 10px;font-size:13px;color:var(--muted);font-weight:500;}
  #rrg-wrap{position:relative;}
  #rrg{width:100%;height:560px;display:block;background:var(--panel);border-radius:8px;}
  .legend{display:flex;gap:16px;font-size:12px;margin-top:8px;flex-wrap:wrap;color:var(--muted);}
  .legend b{font-weight:600;}
  .lead{color:#26a69a;} .impr{color:#58a6ff;} .weak{color:#d29922;} .lag{color:#ef5350;}
  #chart{width:100%;height:380px;}
  #volChart{width:100%;height:90px;}
  select{background:var(--panel);color:var(--txt);border:1px solid var(--border);
          border-radius:6px;padding:7px 10px;font-size:13px;}
  .hold-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border);font-size:12.5px;}
  .hold-row .w{color:var(--accent);font-variant-numeric:tabular-nums;}
  .bar{height:5px;background:var(--accent);border-radius:3px;margin-top:3px;opacity:.6;}
  .leaders-cols{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
  @media(max-width:900px){.leaders-cols{grid-template-columns:1fr;}}
  .note{color:var(--muted);font-size:11.5px;margin-top:6px;}
</style></head>
<body>
<header>
  <h1>US Sektör Rotasyon & Tema Dashboard</h1>
  <span class="meta" id="meta"></span>
</header>
<div class="tabs">
  <div class="tab active" data-v="rrg">1 · Rotation Map (RRG)</div>
  <div class="tab" data-v="heat">2 · Theme Heatmap</div>
  <div class="tab" data-v="lead">3 · Theme Leaders</div>
  <div class="tab" data-v="drill">4 · Drilldown</div>
  <div class="tab" data-v="early">5 · Erken Rotasyon</div>
</div>

<div class="view active" id="v-rrg">
  <div class="card">
    <h3>Makro sektör rotasyonu — RS-Ratio (x) / RS-Momentum (y), son 8 hafta izi</h3>
    <div id="rrg-wrap"><canvas id="rrg"></canvas></div>
    <div class="legend" id="rrg-legend"></div>
    <div class="note">Her ETF kendi sabit rengiyle çizilir; kuyruk son 8 haftadır, büyük uç = güncel hafta. Tipik rotasyon saat yönünde: Improving→Leading→Weakening→Lagging. Kuyruğun yönü o sektöre paranın girdiğini (yukarı-sağa) ya da çıktığını (aşağı-sola) gösterir.</div>
  </div>
</div>

<div class="view" id="v-heat">
  <div class="card">
    <h3>Tüm temalar — getiri & RS vs SPY (başlıklara tıkla = sırala)</h3>
    <div style="max-height:72vh;overflow:auto;">
      <table id="heat-table"></table>
    </div>
  </div>
</div>

<div class="view" id="v-lead">
  <div class="leaders-cols">
    <div class="card"><h3>🔼 En güçlü 10 (dual-momentum skoru)</h3><table id="lead-top"></table></div>
    <div class="card"><h3>🔽 En zayıf 10</h3><table id="lead-bot"></table></div>
  </div>
  <div class="note">Skor = 1A RS ×0.30 + 3A RS ×0.40 + 6A RS ×0.30 (RS = tema getirisi − SPY getirisi).</div>
</div>

<div class="view" id="v-drill">
  <div style="margin-bottom:12px;">
    <select id="tk-select"></select>
  </div>
  <div class="grid2">
    <div class="card">
      <h3 id="chart-title">21 EMA Cloud</h3>
      <div id="chart"></div><div id="volChart"></div>
      <h3 style="margin-top:12px;">RS vs SPY (100'e normalize)</h3>
      <div id="rsChart"></div>
      <div class="note">RS çizgisi fiyattan önce yeni zirve yapıyorsa, tema piyasadan güç çekmeye başlamış demektir — erken sinyal.</div>
    </div>
    <div class="card">
      <h3 id="hold-title">Top 10 Holdings</h3>
      <div id="holdings"></div>
    </div>
  </div>
</div>

<div class="view" id="v-early">
  <div class="card">
    <h3>Erken rotasyon taraması — kısa pencere RS hızlanması (1G/3G/5G RS-ROC)</h3>
    <div style="max-height:72vh;overflow:auto;">
      <table id="early-table"></table>
    </div>
  </div>
  <div class="note">
    <b>RS-ROC</b> = RS çizgisinin (ETF/SPY) o penceredeki % değişimi; pozitif = SPY'dan hızlı.
    <b>✚ Cross</b> = kısa RS-momentum uzun RS-momentum'u son günlerde yukarı kesti (taze giriş).
    <b>▲ RS-NH</b> = RS çizgisi son 20 günün zirvesini bugün kırdı (RS breakout).
    <b>Erken skor</b> = 5G×0.30 + 3G×0.40 + 1G×0.30 + bayrak bonusları. Üsttekiler = paranın yeni yeni döndüğü temalar, fiyat breakout'undan önce.
  </div>
</div>

<script>
const DATA = __DATA__;
document.getElementById('meta').textContent =
  'Güncelleme: ' + DATA.meta.generated + ' · ' + DATA.meta.n_symbols + ' sembol · benchmark SPY';

// ---- tab switching ----
document.querySelectorAll('.tab').forEach(t=>{
  t.onclick=()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('v-'+t.dataset.v).classList.add('active');
    if(t.dataset.v==='rrg') drawRRG();
    if(t.dataset.v==='drill') ensureChart();
    if(t.dataset.v==='early') renderEarly();
  };
});

function cls(v){ if(v===null||v===undefined) return 'zero'; return v>0?'pos':(v<0?'neg':'zero'); }
function fmt(v){ if(v===null||v===undefined) return '–'; return (v>0?'+':'')+v.toFixed(2)+'%'; }

// ================= TAB 1: RRG =================
function drawRRG(){
  const cv=document.getElementById('rrg');
  const wrap=document.getElementById('rrg-wrap');
  const W=wrap.clientWidth, H=560, dpr=window.devicePixelRatio||1;
  cv.width=W*dpr; cv.height=H*dpr; cv.style.width=W+'px'; cv.style.height=H+'px';
  const ctx=cv.getContext('2d'); ctx.scale(dpr,dpr); ctx.clearRect(0,0,W,H);
  const sectors=DATA.rrg;
  let xs=[],ys=[];
  for(const k in sectors) sectors[k].points.forEach(p=>{xs.push(p.x);ys.push(p.y);});
  if(xs.length===0){ctx.fillStyle='#8b949e';ctx.fillText('RRG verisi yok',20,30);return;}
  const pad=46;
  const xmin=Math.min(...xs,98), xmax=Math.max(...xs,102);
  const ymin=Math.min(...ys,98), ymax=Math.max(...ys,102);
  const sx=v=>pad+(v-xmin)/(xmax-xmin)*(W-2*pad);
  const sy=v=>H-pad-(v-ymin)/(ymax-ymin)*(H-2*pad);
  // kadran arka planları (sabit konum referansı, soluk)
  const cx=sx(100), cy=sy(100);
  const fills=[['#26a69a',cx,0,W-cx,cy],['#d29922',cx,cy,W-cx,H-cy],
               ['#ef5350',0,cy,cx,H-cy],['#58a6ff',0,0,cx,cy]];
  fills.forEach(([c,x,y,w,h])=>{ctx.globalAlpha=.05;ctx.fillStyle=c;ctx.fillRect(x,y,w,h);});
  ctx.globalAlpha=1;
  // 100 eksenleri
  ctx.strokeStyle='#2d333b';ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(cx,0);ctx.lineTo(cx,H);ctx.moveTo(0,cy);ctx.lineTo(W,cy);ctx.stroke();
  ctx.fillStyle='#6e7681';ctx.font='11px sans-serif';
  ctx.fillText('Leading',W-pad-54,18);ctx.fillText('Weakening',W-pad-64,H-8);
  ctx.fillText('Lagging',6,H-8);ctx.fillText('Improving',6,18);
  // her sektör: KENDİ SABİT RENGİYLE kuyruk + güncel nokta
  for(const k in sectors){
    const pts=sectors[k].points, col=sectors[k].color;
    ctx.strokeStyle=col;ctx.fillStyle=col;ctx.lineWidth=2;ctx.globalAlpha=.85;
    ctx.beginPath();
    pts.forEach((p,i)=>{const X=sx(p.x),Y=sy(p.y); i?ctx.lineTo(X,Y):ctx.moveTo(X,Y);});
    ctx.stroke();
    // kuyruk noktaları küçükten büyüğe (eskiden güncele)
    pts.slice(0,-1).forEach((p,i)=>{
      ctx.globalAlpha=.35+.4*(i/pts.length);
      ctx.beginPath();ctx.arc(sx(p.x),sy(p.y),2.5,0,7);ctx.fill();});
    const last=pts[pts.length-1];
    ctx.globalAlpha=1;ctx.beginPath();ctx.arc(sx(last.x),sy(last.y),6.5,0,7);ctx.fill();
    ctx.strokeStyle='#0e1117';ctx.lineWidth=1.5;ctx.stroke();
    ctx.fillStyle=col;ctx.font='600 11px sans-serif';
    ctx.fillText(sectors[k].ticker, sx(last.x)+10, sy(last.y)+4);
  }
  ctx.globalAlpha=1;
  // sektör renk lejantı
  const leg=document.getElementById('rrg-legend');
  leg.innerHTML=Object.keys(sectors).map(k=>{
    const s=sectors[k];
    return `<span style="color:${s.color};"><b>■ ${s.ticker}</b></span>`+
           `<span class="sector-tag" style="margin-left:2px;">${k} · ${s.quadrant}</span>`;
  }).join('');
}
window.addEventListener('resize',()=>{
  if(document.querySelector('.tab.active').dataset.v==='rrg') drawRRG();
});

// ================= TAB 2: HEATMAP =================
let heatSort={key:'score',dir:-1};
function heatColor(v){
  if(v===null||v===undefined) return 'transparent';
  const a=Math.min(Math.abs(v)/12,1)*0.5;
  return v>0?`rgba(38,166,154,${a})`:`rgba(239,83,80,${a})`;
}
function renderHeat(){
  const rows=DATA.themes.slice();
  const W=DATA.windows;
  rows.sort((a,b)=>{
    let av,bv;
    if(heatSort.key==='score'){av=a.score;bv=b.score;}
    else if(heatSort.key==='theme'){av=a.theme;bv=b.theme;}
    else if(heatSort.key.startsWith('r_')){const w=heatSort.key.slice(2);av=a.ret[w];bv=b.ret[w];}
    else if(heatSort.key.startsWith('s_')){const w=heatSort.key.slice(2);av=a.rs[w];bv=b.rs[w];}
    else if(heatSort.key==='atr'){av=a.atr;bv=b.atr;}
    av=(av===null||av===undefined)?-1e9:av; bv=(bv===null||bv===undefined)?-1e9:bv;
    if(typeof av==='string') return heatSort.dir*av.localeCompare(bv);
    return heatSort.dir*(av-bv);
  });
  let h='<thead><tr>'+
    `<th class="l" data-k="theme">Tema</th><th class="l">ETF</th>`+
    W.map(w=>`<th data-k="r_${w}">${w}</th>`).join('')+
    W.map(w=>`<th data-k="s_${w}">RS ${w}</th>`).join('')+
    `<th data-k="atr">ATR%</th><th data-k="score">Skor</th></tr></thead><tbody>`;
  rows.forEach(r=>{
    h+=`<tr><td class="l">${r.theme}${r.proxy?'<span class="proxy">proxy</span>':''}`+
       `<div class="sector-tag">${r.sector}</div></td>`+
       `<td class="l tk" data-tk="${r.ticker}">${r.ticker}</td>`+
       W.map(w=>`<td class="${cls(r.ret[w])}" style="background:${heatColor(r.ret[w])}">${fmt(r.ret[w])}</td>`).join('')+
       W.map(w=>`<td class="${cls(r.rs[w])}">${fmt(r.rs[w])}</td>`).join('')+
       `<td class="zero">${r.atr!==null?r.atr.toFixed(1):'–'}</td>`+
       `<td class="${cls(r.score)}"><b>${r.score!==null?r.score.toFixed(2):'–'}</b></td></tr>`;
  });
  h+='</tbody>';
  const tbl=document.getElementById('heat-table'); tbl.innerHTML=h;
  tbl.querySelectorAll('th[data-k]').forEach(th=>{
    th.onclick=()=>{const k=th.dataset.k;
      if(heatSort.key===k) heatSort.dir*=-1; else {heatSort.key=k;heatSort.dir=-1;}
      renderHeat();};
  });
  tbl.querySelectorAll('.tk').forEach(el=>{
    el.onclick=()=>openDrill(el.dataset.tk);
  });
}

// ================= TAB 3: LEADERS =================
function leaderTable(rows){
  let h='<thead><tr><th class="l">Tema</th><th class="l">ETF</th>'+
        '<th>3A RS</th><th>Skor</th></tr></thead><tbody>';
  rows.forEach(r=>{
    h+=`<tr><td class="l">${r.theme}${r.proxy?'<span class="proxy">proxy</span>':''}`+
       `<div class="sector-tag">${r.sector}</div></td>`+
       `<td class="l tk" data-tk="${r.ticker}">${r.ticker}</td>`+
       `<td class="${cls(r.rs['3M'])}">${fmt(r.rs['3M'])}</td>`+
       `<td class="${cls(r.score)}"><b>${r.score.toFixed(2)}</b></td></tr>`;
  });
  return h+'</tbody>';
}
function renderLeaders(){
  const sorted=DATA.themes.slice().sort((a,b)=>b.score-a.score);
  document.getElementById('lead-top').innerHTML=leaderTable(sorted.slice(0,10));
  document.getElementById('lead-bot').innerHTML=leaderTable(sorted.slice(-10).reverse());
  document.querySelectorAll('#v-lead .tk').forEach(el=>el.onclick=()=>openDrill(el.dataset.tk));
}

// ================= TAB 4: DRILLDOWN =================
let chart,candleS,emaHiS,emaLoS,volChartObj,volS,rsChartObj,rsS,chartReady=false,pendingTk=null;
function initChart(){
  if(chartReady) return;
  const el=document.getElementById('chart');
  chart=LightweightCharts.createChart(el,{
    width:el.clientWidth,height:380,
    layout:{background:{color:'#161b22'},textColor:'#e6edf3'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},
    timeScale:{timeVisible:false,borderColor:'#2d333b'},
    rightPriceScale:{borderColor:'#2d333b'},
  });
  candleS=chart.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',
    borderUpColor:'#26a69a',borderDownColor:'#ef5350',wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
  emaHiS=chart.addLineSeries({color:'rgba(88,166,255,.9)',lineWidth:1});
  emaLoS=chart.addLineSeries({color:'rgba(210,153,34,.9)',lineWidth:1});
  const vel=document.getElementById('volChart');
  volChartObj=LightweightCharts.createChart(vel,{
    width:vel.clientWidth,height:90,
    layout:{background:{color:'#161b22'},textColor:'#8b949e'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},
    timeScale:{visible:false},rightPriceScale:{borderColor:'#2d333b'},
  });
  volS=volChartObj.addHistogramSeries({priceFormat:{type:'volume'}});
  const rel=document.getElementById('rsChart');
  rsChartObj=LightweightCharts.createChart(rel,{
    width:rel.clientWidth,height:140,
    layout:{background:{color:'#161b22'},textColor:'#8b949e'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},
    timeScale:{timeVisible:false,borderColor:'#2d333b'},rightPriceScale:{borderColor:'#2d333b'},
  });
  rsS=rsChartObj.addLineSeries({color:'#a371f7',lineWidth:2});
  chart.timeScale().subscribeVisibleLogicalRangeChange(r=>{
    if(r){ volChartObj.timeScale().setVisibleLogicalRange(r);
            rsChartObj.timeScale().setVisibleLogicalRange(r);}});
  chartReady=true;
  window.addEventListener('resize',()=>{
    chart.applyOptions({width:el.clientWidth});
    volChartObj.applyOptions({width:vel.clientWidth});
    rsChartObj.applyOptions({width:rel.clientWidth});
  });
}
function loadChart(tk){
  initChart();
  const c=DATA.charts[tk];
  if(!c){document.getElementById('chart-title').textContent=tk+' — grafik verisi yok';return;}
  candleS.setData(c.candles); emaHiS.setData(c.emaHigh); emaLoS.setData(c.emaLow);
  volS.setData(c.volume); rsS.setData(c.rs||[]);
  chart.timeScale().fitContent(); rsChartObj.timeScale().fitContent();
  document.getElementById('chart-title').textContent=tk+' · 21 EMA Cloud (mavi=EMA High, sarı=EMA Low)';
  renderHoldings(tk);
}
function renderHoldings(tk){
  const h=DATA.holdings[tk]||[];
  document.getElementById('hold-title').textContent=tk+' · Top Holdings';
  const box=document.getElementById('holdings');
  if(h.length===0){box.innerHTML='<div class="note">yfinance bu ETF için holdings döndürmedi.</div>';return;}
  const max=Math.max(...h.map(x=>x.weight||0),1);
  box.innerHTML=h.map(x=>`<div class="hold-row"><div>
      <b>${x.symbol||''}</b> <span class="sector-tag">${(x.name||'').slice(0,34)}</span>
      <div class="bar" style="width:${((x.weight||0)/max*100).toFixed(0)}%"></div></div>
      <div class="w">${x.weight!==null?x.weight.toFixed(2)+'%':'–'}</div></div>`).join('');
}
function fillSelect(){
  const sel=document.getElementById('tk-select');
  const seen=new Set(); let opts='';
  DATA.themes.forEach(r=>{ if(!seen.has(r.ticker)){seen.add(r.ticker);
    opts+=`<option value="${r.ticker}">${r.ticker} — ${r.theme}</option>`;}});
  sel.innerHTML=opts;
  sel.onchange=()=>loadChart(sel.value);
}
function ensureChart(){ initChart(); if(pendingTk){loadChart(pendingTk);pendingTk=null;}
  else { const sel=document.getElementById('tk-select'); if(sel.value) loadChart(sel.value);} }
function openDrill(tk){
  pendingTk=tk;
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
  document.querySelector('.tab[data-v="drill"]').classList.add('active');
  document.getElementById('v-drill').classList.add('active');
  const sel=document.getElementById('tk-select'); sel.value=tk;
  ensureChart();
}

// ================= TAB 5: ERKEN ROTASYON =================
let earlySort={key:'score',dir:-1};
function rocColor(v){
  if(v===null||v===undefined) return 'transparent';
  const a=Math.min(Math.abs(v)/4,1)*0.5;
  return v>0?`rgba(38,166,154,${a})`:`rgba(239,83,80,${a})`;
}
function renderEarly(){
  const rows=DATA.themes.slice();
  rows.sort((a,b)=>{
    let av,bv;
    const k=earlySort.key;
    if(k==='theme'){av=a.theme;bv=b.theme;}
    else if(k==='roc1'){av=a.early.roc1;bv=b.early.roc1;}
    else if(k==='roc3'){av=a.early.roc3;bv=b.early.roc3;}
    else if(k==='roc5'){av=a.early.roc5;bv=b.early.roc5;}
    else {av=a.early.score;bv=b.early.score;}
    av=(av===null||av===undefined)?-1e9:av; bv=(bv===null||bv===undefined)?-1e9:bv;
    if(typeof av==='string') return earlySort.dir*av.localeCompare(bv);
    return earlySort.dir*(av-bv);
  });
  let h='<thead><tr>'+
    '<th class="l" data-k="theme">Tema</th><th class="l">ETF</th>'+
    '<th data-k="roc1">RS 1G</th><th data-k="roc3">RS 3G</th><th data-k="roc5">RS 5G</th>'+
    '<th>Bayrak</th><th data-k="score">Erken Skor</th></tr></thead><tbody>';
  rows.forEach(r=>{
    const e=r.early;
    const flags=(e.cross?'<span style="color:#26a69a;" title="taze RS kesişimi">✚ Cross</span> ':'')+
                (e.newhigh?'<span style="color:#58a6ff;" title="RS 20g yeni zirve">▲ RS-NH</span>':'');
    h+=`<tr><td class="l">${r.theme}${r.proxy?'<span class="proxy">proxy</span>':''}`+
       `<div class="sector-tag">${r.sector}</div></td>`+
       `<td class="l tk" data-tk="${r.ticker}">${r.ticker}</td>`+
       `<td class="${cls(e.roc1)}" style="background:${rocColor(e.roc1)}">${fmt(e.roc1)}</td>`+
       `<td class="${cls(e.roc3)}" style="background:${rocColor(e.roc3)}">${fmt(e.roc3)}</td>`+
       `<td class="${cls(e.roc5)}" style="background:${rocColor(e.roc5)}">${fmt(e.roc5)}</td>`+
       `<td class="l">${flags||'<span class="zero">–</span>'}</td>`+
       `<td class="${cls(e.score)}"><b>${e.score.toFixed(2)}</b></td></tr>`;
  });
  h+='</tbody>';
  const tbl=document.getElementById('early-table'); tbl.innerHTML=h;
  tbl.querySelectorAll('th[data-k]').forEach(th=>{
    th.onclick=()=>{const k=th.dataset.k;
      if(earlySort.key===k) earlySort.dir*=-1; else {earlySort.key=k;earlySort.dir=-1;}
      renderEarly();};
  });
  tbl.querySelectorAll('.tk').forEach(el=>el.onclick=()=>openDrill(el.dataset.tk));
}

// init
renderHeat(); renderLeaders(); fillSelect(); drawRRG();
</script>
</body></html>"""
    return html.replace("__DATA__", data_json)


# ----------------------------------------------------------------------------
# 6) main
# ----------------------------------------------------------------------------
def main():
    tickers = all_tickers()
    data = download_history(tickers)
    if BENCHMARK not in data:
        print(f"[hata] benchmark {BENCHMARK} indirilemedi, çıkılıyor."); sys.exit(1)
    bench_close = data[BENCHMARK]["Close"]

    sector_data = {s: data[t] for s, t in SECTOR_ETFS.items() if t in data}

    print("[hesap] RRG ...")
    rrg = compute_rrg(sector_data, bench_close)
    print("[hesap] tema satırları ...")
    theme_rows = build_theme_rows(data, bench_close)
    print("[hesap] drilldown grafikleri ...")
    charts = build_chart_payload(data, bench_close)

    print("[indir] ETF holdings (top-10) ...")
    holdings = {}
    uniq = sorted({r["ticker"] for r in theme_rows})
    for i, tk in enumerate(uniq, 1):
        holdings[tk] = fetch_top_holdings(tk)
        print(f"  ({i}/{len(uniq)}) {tk}: {len(holdings[tk])} holding")
        time.sleep(0.05)

    meta = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "n_symbols": len(data)}
    html = build_html(rrg, theme_rows, charts, holdings, meta)

    out = "dashboard_rotation.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ Hazır: {out}  ({len(html)//1024} KB)")
    print("   Tarayıcıda aç:  open dashboard_rotation.html")


if __name__ == "__main__":
    main()
