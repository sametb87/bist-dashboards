"""
SWING PORTFOLIO DASHBOARD v14
==============================
DEĞİŞİKLİKLER v11'e GÖRE:
- entry_date / trim.date / add.date formatı: "MM/DD/YYYY" (yıl açık)
  → TRADE_YEAR sabiti kaldırıldı, artık yıl karışıklığı yok
- CACHE KURALI GÜÇLENDIRILDI:
    * exit=True + cache kaydı mevcut → HİÇBİR KOŞULDA yeniden fetch edilmez
    * Sadece şunlar fetch edilir:
        1. exit=False (açık pozisyon) — her çalıştırmada güncellenir
        2. Cache'de hiç kaydı olmayan trade
        3. Kapanmamış + fingerprint değişmiş (yeni trim/add eklendi)
- EXIT (CLOSED) GÖSTERİMİ:
    * trimmed_pct=100 olan trade'lerde son trim = exit → KIRMIZI çizgi + marker
    * Diğer trim'ler PEMBE çizgi + marker
- WEIGHT: her kartın başlığında gösterilir (örn. "9%")

KULLANIM:
    pip3 install yfinance pandas --break-system-packages
    python3 swing_dashboard.py
    open swing_dashboard.html
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd

SCRIPT_DIR = Path(__file__).parent
TRADES_FILE = SCRIPT_DIR / "trades.json"
OUTPUT_FILE = SCRIPT_DIR / "swing_dashboard.html"
CACHE_FILE  = SCRIPT_DIR / "ohlc_cache.json"

LOOKAHEAD_DAYS = 1

BARS_BEFORE_ENTRY = 45
BARS_AFTER_ENTRY  = 45

ENTRY_LINE_BARS = 3
STOP_LINE_BARS  = 8
TRIM_LINE_BARS  = 3
ADD_LINE_BARS   = 3

ATR_LENGTH = 14
EMA_LENGTH = 21
MA_LENGTH  = 50

RIGHT_OFFSET = 2


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def parse_date(mm_dd_yyyy: str) -> datetime:
    """'MM/DD/YYYY' → datetime"""
    return datetime.strptime(mm_dd_yyyy, "%m/%d/%Y")

def to_iso(mm_dd_yyyy: str) -> str:
    """'MM/DD/YYYY' → 'YYYY-MM-DD'"""
    return parse_date(mm_dd_yyyy).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_trades():
    with open(TRADES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def trade_key(t: dict) -> str:
    return f"{t['ticker']}|{t['entry_date']}|{t['entry_price']}"


def trade_is_closed(t: dict) -> bool:
    return bool(t.get("exit", False))


def trade_fingerprint(t: dict) -> str:
    """Kapanmamış trade'ler için durum özeti. Kapanmışlarda sabit 'closed'."""
    if trade_is_closed(t):
        return "closed"
    trims_sig = json.dumps(t.get("trims", []), sort_keys=True)
    adds_sig  = json.dumps(t.get("adds",  []), sort_keys=True)
    return (f"open"
            f"_trimmed={t.get('trimmed_pct', 0)}"
            f"_trims={trims_sig}"
            f"_adds={adds_sig}")


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def load_cache() -> dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache: dict):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Fetch & Indicators
# ---------------------------------------------------------------------------

def fetch_ohlc(ticker, start, end):
    df = yf.download(ticker, start=start, end=end, interval="1d",
                     progress=False, auto_adjust=False)
    if df.empty:
        return df
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] for c in df.columns]
    return df


def compute_indicators(df):
    df = df.copy()
    df["ema_high"]  = df["High"].ewm(span=EMA_LENGTH, adjust=False).mean()
    df["ema_low"]   = df["Low"].ewm(span=EMA_LENGTH, adjust=False).mean()
    df["ema_close"] = df["Close"].ewm(span=EMA_LENGTH, adjust=False).mean()
    df["ma_50"] = df["Close"].rolling(MA_LENGTH).mean()
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1/ATR_LENGTH, adjust=False).mean()
    return df


def df_to_records(df):
    out = []
    for idx, row in df.iterrows():
        out.append({
            "date": idx.strftime("%Y-%m-%d"),
            "o": round(float(row["Open"]), 2),
            "h": round(float(row["High"]), 2),
            "l": round(float(row["Low"]), 2),
            "c": round(float(row["Close"]), 2),
            "eh": round(float(row["ema_high"]), 4),
            "el": round(float(row["ema_low"]), 4),
            "ec": round(float(row["ema_close"]), 4),
        })
    return out


def slice_bars_for_trade(df_full: pd.DataFrame, entry_date_iso: str) -> list:
    entry_ts = pd.Timestamp(entry_date_iso)
    if entry_ts in df_full.index:
        entry_idx = df_full.index.get_loc(entry_ts)
    else:
        after = df_full.index[df_full.index >= entry_ts]
        entry_idx = df_full.index.get_loc(after[0]) if len(after) else len(df_full) - 1
    from_idx = max(0, entry_idx - BARS_BEFORE_ENTRY)
    to_idx   = min(len(df_full) - 1, entry_idx + BARS_AFTER_ENTRY)
    return df_to_records(df_full.iloc[from_idx:to_idx + 1])


# ---------------------------------------------------------------------------
# Incremental build_dataset
# ---------------------------------------------------------------------------

def build_dataset(trades: list):
    today = datetime.now()
    cache = load_cache()

    # ------------------------------------------------------------------
    # Fetch kararı:
    #   KESİNLİKLE ATLA → exit=True VE cache'de kaydı var (fingerprint=closed)
    #   FETCH ET        → cache yok, VEYA exit=False (açık pozisyon)
    # ------------------------------------------------------------------
    need_fetch: list[dict] = []

    for t in trades:
        key = trade_key(t)
        cached = cache.get(key, {})

        if trade_is_closed(t) and cached.get("fingerprint") == "closed" and cached.get("bars"):
            # Kapanmış + cache'de var → atla
            continue
        else:
            need_fetch.append(t)

    if need_fetch:
        closed_fetch  = sum(1 for t in need_fetch if trade_is_closed(t))
        open_fetch    = len(need_fetch) - closed_fetch
        print(f"\n[FETCH] {len(need_fetch)} trade fetch edilecek "
              f"({open_fetch} açık, {closed_fetch} kapanmış-yeni)...")

        # Her ticker için en geniş tarih aralığı
        ticker_ranges: dict[str, dict] = {}
        for t in need_fetch:
            tk = t["ticker"]
            d  = parse_date(t["entry_date"])
            if tk not in ticker_ranges:
                ticker_ranges[tk] = {"earliest": d, "latest": d}
            else:
                if d < ticker_ranges[tk]["earliest"]:
                    ticker_ranges[tk]["earliest"] = d
                if d > ticker_ranges[tk]["latest"]:
                    ticker_ranges[tk]["latest"] = d

        fresh_dfs: dict[str, pd.DataFrame] = {}
        for tk, rng in ticker_ranges.items():
            fetch_start = (rng["earliest"] - timedelta(days=int(BARS_BEFORE_ENTRY * 1.6) + 120)).strftime("%Y-%m-%d")
            fetch_end_target = rng["latest"] + timedelta(days=int(BARS_AFTER_ENTRY * 1.6) + 5)
            fetch_end = min(today + timedelta(days=LOOKAHEAD_DAYS), fetch_end_target).strftime("%Y-%m-%d")

            print(f"  {tk:6s}", end="  ")
            try:
                df = fetch_ohlc(tk, fetch_start, fetch_end)
                if df.empty:
                    print("BOŞ")
                    continue
                df = compute_indicators(df)
                fresh_dfs[tk] = df
                print(f"{len(df)} bar")
            except Exception as e:
                print(f"HATA: {e}")

        for t in need_fetch:
            key           = trade_key(t)
            fp            = trade_fingerprint(t)
            entry_iso     = to_iso(t["entry_date"])

            df = fresh_dfs.get(t["ticker"])
            if df is None or df.empty:
                bars = cache.get(key, {}).get("bars", [])  # eskiyi koru
            else:
                bars = slice_bars_for_trade(df, entry_iso)

            cache[key] = {"fingerprint": fp, "bars": bars}

        save_cache(cache)
        print(f"  → Cache güncellendi: {CACHE_FILE.name}")
    else:
        print(f"\n[CACHE] Tüm trade'ler güncel, fetch atlandı.")

    # indicators dict (trim tarih otomatik tespiti için)
    indicators: dict[str, pd.DataFrame] = {}
    if need_fetch:
        for tk, df in fresh_dfs.items():
            indicators[tk] = df

    for t in trades:
        ticker = t["ticker"]
        if ticker in indicators:
            continue
        bars = cache.get(trade_key(t), {}).get("bars", [])
        if bars:
            indicators[ticker] = _bars_to_df(bars)

    ohlc: dict[str, list] = {}
    for i, t in enumerate(trades):
        ohlc[str(i)] = cache.get(trade_key(t), {}).get("bars", [])

    return ohlc, indicators


def _bars_to_df(bars: list) -> pd.DataFrame:
    rows = []
    for b in bars:
        rows.append({
            "Date":      pd.Timestamp(b["date"]),
            "Open":      b["o"],
            "High":      b["h"],
            "Low":       b["l"],
            "Close":     b["c"],
            "ema_high":  b["eh"],
            "ema_low":   b["el"],
            "ema_close": b["ec"],
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("Date")
    df["ma_50"] = df["Close"].rolling(MA_LENGTH).mean()
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1/ATR_LENGTH, adjust=False).mean()
    return df


# ---------------------------------------------------------------------------
# Enrich trades
# ---------------------------------------------------------------------------

def lookup_indicators_at(df, target_date_str):
    target = pd.Timestamp(target_date_str)
    if target in df.index:
        row = df.loc[target]
    else:
        after = df.index[df.index >= target]
        if len(after) == 0:
            return None
        row = df.loc[after[0]]
    return {
        "close":  float(row["Close"]),
        "ema21":  float(row["ema_close"]),
        "ma50":   float(row["ma_50"]) if pd.notna(row["ma_50"]) else None,
        "atr":    float(row["atr"]) if pd.notna(row["atr"]) else None,
    }


def find_first_touch(df, after_date_str, target_price, side="long"):
    after = pd.Timestamp(after_date_str)
    sub = df[df.index > after]
    if sub.empty:
        return None
    if side.lower() == "long":
        hit = sub[sub["High"] >= target_price]
    else:
        hit = sub[sub["Low"] <= target_price]
    if hit.empty:
        return None
    return hit.index[0].strftime("%Y-%m-%d")


def enrich_trades(trades, indicators):
    out = []
    for i, t in enumerate(trades):
        t = dict(t)
        t["trade_id"]      = str(i)
        t["entry_date_iso"] = to_iso(t["entry_date"])

        df = indicators.get(t["ticker"])

        # ---- Indikatör mesafeleri ----
        if df is not None and not df.empty and t.get("entry_price") is not None:
            ind = lookup_indicators_at(df, t["entry_date_iso"])
            if ind and ind["atr"] and ind["atr"] > 0:
                ref = t["entry_price"]
                t["atr_dist_ema"] = round(abs(ref - ind["ema21"]) / ind["atr"], 2)
                t["atr_dist_ma"]  = round(abs(ref - ind["ma50"]) / ind["atr"], 2) if ind["ma50"] else None
            else:
                t["atr_dist_ema"] = None
                t["atr_dist_ma"]  = None
        else:
            t["atr_dist_ema"] = None
            t["atr_dist_ma"]  = None

        # ---- Adds: ISO tarihlere çevir ----
        if t.get("adds"):
            enriched_adds = []
            for a in t["adds"]:
                a2 = dict(a)
                a2["date_iso"] = to_iso(a["date"]) if a.get("date") else None
                if a.get("price") is not None and t.get("entry_price") is not None:
                    a2["diff_pct"] = round((a["price"] - t["entry_price"]) / t["entry_price"] * 100, 1)
                else:
                    a2["diff_pct"] = None
                enriched_adds.append(a2)
            t["adds"] = enriched_adds

        # ---- Trim'ler: otomatik tarih tespiti ----
        if t.get("trims") and df is not None and not df.empty:
            new_trims = []
            search_after = t["entry_date_iso"]
            for tr in t["trims"]:
                tr2 = dict(tr)
                if tr.get("date"):
                    tr2["date_iso"] = to_iso(tr["date"])
                else:
                    hit_date = find_first_touch(df, search_after, tr["price"], t["side"])
                    tr2["date_iso"] = hit_date

                if t.get("entry_price") is not None and tr.get("price") is not None:
                    if t["side"].lower() == "long":
                        tr2["profit_pct"] = round((tr["price"] - t["entry_price"]) / t["entry_price"] * 100, 1)
                    else:
                        tr2["profit_pct"] = round((t["entry_price"] - tr["price"]) / t["entry_price"] * 100, 1)
                else:
                    tr2["profit_pct"] = None

                if tr2["date_iso"]:
                    search_after = tr2["date_iso"]

                new_trims.append(tr2)
            t["trims"] = new_trims

        out.append(t)
    return out


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<title>Swing Portfolio - Alex Desjardins</title>
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {
    --bg: #ffffff;
    --panel: #ffffff;
    --border: #e1e4e8;
    --text: #1a1a1a;
    --muted: #6b7280;
    --blue: #2962ff;
    --pink: #f97316;
    --orange: #ff9800;
    --green: #16a34a;
    --brown: #8b4513;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 16px;
  }
  h1 { margin: 0 0 4px 0; font-size: 20px; letter-spacing: 0.5px; color: var(--text); }
  .subtitle { color: var(--muted); font-size: 12px; margin-bottom: 16px; }
  .grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 10px 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }
  .card-head {
    display: flex; justify-content: space-between; align-items: flex-start;
    margin-bottom: 4px;
    gap: 8px;
  }
  .ticker { font-size: 16px; font-weight: 700; letter-spacing: 0.3px; color: var(--text); }
  .meta {
    font-size: 10px; color: var(--muted);
    text-align: right; line-height: 1.4;
  }
  .meta .row { display: block; }
  .meta .green { color: var(--green); font-weight: 600; }
  .meta .pink  { color: var(--pink);  font-weight: 600; }
  .meta .label { color: var(--muted); font-weight: 400; }
  .chart { height: 440px; width: 100%; }
</style>
</head>
<body>

<h1>SWING PORTFOLIO &mdash; Alex Desjardins (@PrimeTrading_)</h1>
<div class="subtitle">21 EMA Cloud &middot; ATR(14) distance at entry &middot; Mavi = Giriş, Yeşil = Add, Pembe = Trim, Kırmızı = Exit, Kahverengi = Stop</div>

<div class="grid" id="grid"></div>

<script>
const TRADES = __TRADES_JSON__;
const OHLC   = __OHLC_JSON__;
const ENTRY_BARS = __ENTRY_BARS__;
const STOP_BARS  = __STOP_BARS__;
const TRIM_BARS  = __TRIM_BARS__;
const ADD_BARS   = __ADD_BARS__;
const RIGHT_OFFSET = __RIGHT_OFFSET__;

const grid = document.getElementById('grid');

function buildHLineData(allBars, startDate, numBars, price, direction='forward') {
  const idx = allBars.findIndex(b => b.date === startDate);
  if (idx < 0) return [];
  let from, to;
  if (direction === 'centered') {
    const half = Math.floor(numBars / 2);
    from = Math.max(0, idx - half);
    to   = Math.min(allBars.length - 1, idx + (numBars - half - 1));
  } else {
    from = idx;
    to   = Math.min(allBars.length - 1, idx + numBars - 1);
  }
  const out = [];
  for (let i = from; i <= to; i++) {
    out.push({ time: allBars[i].date, value: price });
  }
  return out;
}

function buildMetaHTML(trade) {
  const rows = [];

  // Weight + entry
  const weightStr = trade.weight ? `${trade.weight}%` : '';
  rows.push(`<span class="row"><span class="label">W:</span> <span style="font-weight:600">${weightStr}</span></span>`);

  if (trade.adds && trade.adds.length) {
    const addsTxt = trade.adds.map((a, i) => {
      const pos = a.diff_pct >= 0;
      const sign = pos ? '+' : '';
      const col = pos ? '#16a34a' : '#dc2626';
      return `A${i+1}: $${a.price} <span style="color:${col};font-weight:600">(${sign}${a.diff_pct}%)</span>`;
    }).join(' &nbsp; ');
    rows.push(`<span class="row"><span style="color:#16a34a;font-weight:600">Add</span> &nbsp; ${addsTxt}</span>`);
  }

  if (trade.trims && trade.trims.length) {
    const isFullExit = trade.trimmed_pct === 100;
    const lastIdx = trade.trims.length - 1;
    const trimsTxt = trade.trims.map((tr, i) => {
      const isExitTrim = isFullExit && i === lastIdx;
      const pos = tr.profit_pct >= 0;
      const sign = pos ? '+' : '';
      const valCol = pos ? '#16a34a' : '#dc2626';
      const label = isExitTrim ? `<span style="color:#dc2626;font-weight:600">Exit</span>` : `T${i+1}`;
      return `${label}: <span style="color:${valCol};font-weight:600">${sign}${tr.profit_pct}%</span>`;
    }).join(' &nbsp; ');
    const trimLabel = trade.trimmed_pct < 100
      ? `<span class="pink">Trim ${trade.trimmed_pct}%</span>`
      : `<span style="color:#dc2626;font-weight:600">Closed</span>`;
    rows.push(`<span class="row">${trimLabel} &nbsp; ${trimsTxt}</span>`);
  }

  const atrParts = [];
  if (trade.atr_dist_ema !== null && trade.atr_dist_ema !== undefined) {
    const emaRed = trade.atr_dist_ema > 1;
    atrParts.push(`<span class="label">21EMA:</span> <span style="color:${emaRed ? '#dc2626' : 'inherit'};font-weight:${emaRed ? '700' : '400'}">${trade.atr_dist_ema} ATR</span>`);
  }
  if (trade.atr_dist_ma !== null && trade.atr_dist_ma !== undefined) {
    const maRed = trade.atr_dist_ma > 4;
    atrParts.push(`<span class="label">50MA:</span> <span style="color:${maRed ? '#dc2626' : 'inherit'};font-weight:${maRed ? '700' : '400'}">${trade.atr_dist_ma} ATR</span>`);
  }
  if (atrParts.length) {
    rows.push(`<span class="row">${atrParts.join(' &nbsp; ')}</span>`);
  }

  return rows.join('');
}

TRADES.forEach((trade, idx) => {
  const card = document.createElement('div');
  card.className = 'card';

  const head = document.createElement('div');
  head.className = 'card-head';

  const left = document.createElement('div');
  left.innerHTML = `<span class="ticker">${trade.ticker}</span>`;

  const right = document.createElement('div');
  right.className = 'meta';
  right.innerHTML = buildMetaHTML(trade);

  head.appendChild(left);
  head.appendChild(right);
  card.appendChild(head);

  const chartDiv = document.createElement('div');
  chartDiv.className = 'chart';
  chartDiv.id = 'chart_' + idx;
  card.appendChild(chartDiv);

  grid.appendChild(card);

  const data = OHLC[trade.trade_id];
  if (!data || data.length === 0) {
    chartDiv.innerHTML = '<div style="padding:20px;color:#6b7280;font-size:12px;">Veri bulunamadı</div>';
    return;
  }

  const chart = LightweightCharts.createChart(chartDiv, {
    layout: { background: { color: '#ffffff' }, textColor: '#6b7280', fontSize: 10 },
    grid: {
      vertLines: { color: 'rgba(0,0,0,0.04)' },
      horzLines: { color: 'rgba(0,0,0,0.04)' },
    },
    rightPriceScale: {
      borderColor: '#e1e4e8',
      scaleMargins: { top: 0.01, bottom: 0.01 },
      autoScale: true,
    },
    timeScale: {
      borderColor: '#e1e4e8',
      timeVisible: false,
      fixLeftEdge: true,
      fixRightEdge: false,
    },
    crosshair: { mode: 1 },
    width: chartDiv.clientWidth,
    height: 440,
    handleScroll: false,
    handleScale: false,
  });

  // 21 EMA CLOUD
  const emaHighArea = chart.addAreaSeries({
    topColor: 'rgba(150,150,150,0.20)', bottomColor: 'rgba(150,150,150,0.20)',
    lineColor: 'rgba(120,120,120,0.5)', lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  emaHighArea.setData(data.map(d => ({ time: d.date, value: d.eh })));

  const emaLowArea = chart.addAreaSeries({
    topColor: 'rgba(255,255,255,1)', bottomColor: 'rgba(255,255,255,1)',
    lineColor: 'rgba(120,120,120,0.5)', lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  emaLowArea.setData(data.map(d => ({ time: d.date, value: d.el })));

  const emaCloseLine = chart.addLineSeries({
    color: '#ff9800', lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  emaCloseLine.setData(data.map(d => ({ time: d.date, value: d.ec })));

  // MUMLAR
  const candleSeries = chart.addCandlestickSeries({
    upColor: '#ffffff', downColor: '#1a1a1a',
    borderUpColor: '#1a1a1a', borderDownColor: '#1a1a1a',
    wickUpColor: '#1a1a1a', wickDownColor: '#1a1a1a',
    priceLineVisible: false, lastValueVisible: false,
  });
  candleSeries.setData(data.map(d => ({
    time: d.date, open: d.o, high: d.h, low: d.l, close: d.c
  })));

  // ENTRY (mavi)
  const entryLine = chart.addLineSeries({
    color: '#2962ff', lineWidth: 3,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  entryLine.setData(buildHLineData(data, trade.entry_date_iso, ENTRY_BARS, trade.entry_price, 'centered'));

  // ADD'LER (yeşil)
  if (trade.adds && trade.adds.length) {
    trade.adds.forEach(a => {
      if (a.date_iso) {
        const addLine = chart.addLineSeries({
          color: '#16a34a', lineWidth: 3,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        addLine.setData(buildHLineData(data, a.date_iso, ADD_BARS, a.price, 'centered'));
      }
    });
  }

  // STOP (kahverengi)
  if (trade.stop !== null && trade.stop !== undefined) {
    const stopLine = chart.addLineSeries({
      color: '#8b4513', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    stopLine.setData(buildHLineData(data, trade.entry_date_iso, STOP_BARS, trade.stop, 'forward'));
  }

  // TRIMS (pembe) + EXIT (kırmızı)
  if (trade.trims && trade.trims.length) {
    const isFullExit = trade.trimmed_pct === 100;
    const lastTrimIdx = trade.trims.length - 1;
    trade.trims.forEach((tr, i) => {
      if (tr.date_iso) {
        const isExitTrim = isFullExit && i === lastTrimIdx;
        const trimLine = chart.addLineSeries({
          color: isExitTrim ? '#dc2626' : '#f97316', lineWidth: 3,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        trimLine.setData(buildHLineData(data, tr.date_iso, TRIM_BARS, tr.price, 'centered'));
      }
    });
  }

  // MARKERS
  const markers = [];
  markers.push({ time: trade.entry_date_iso, position: 'belowBar', color: '#2962ff', shape: 'arrowUp', size: 0 });
  if (trade.adds && trade.adds.length) {
    trade.adds.forEach(a => {
      if (a.date_iso) markers.push({ time: a.date_iso, position: 'belowBar', color: '#16a34a', shape: 'arrowUp', size: 0 });
    });
  }
  if (trade.trims && trade.trims.length) {
    const isFullExit = trade.trimmed_pct === 100;
    const lastTrimIdx = trade.trims.length - 1;
    trade.trims.forEach((tr, i) => {
      if (tr.date_iso) {
        const isExitTrim = isFullExit && i === lastTrimIdx;
        markers.push({ time: tr.date_iso, position: 'belowBar', color: isExitTrim ? '#dc2626' : '#f97316', shape: 'arrowUp', size: 0 });
      }
    });
  }
  markers.sort((a, b) => a.time < b.time ? -1 : a.time > b.time ? 1 : 0);
  candleSeries.setMarkers(markers);

  chart.timeScale().fitContent();

  const ro = new ResizeObserver(() => {
    chart.applyOptions({ width: chartDiv.clientWidth });
    chart.timeScale().fitContent();
  });
  ro.observe(chartDiv);
});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("SWING PORTFOLIO DASHBOARD v14")
    print("=" * 60)

    print("\n[1/3] Trade'ler yükleniyor...")
    trades = load_trades()
    print(f"      {len(trades)} trade")

    cache = load_cache()
    n_skip   = sum(1 for t in trades if trade_is_closed(t)
                   and cache.get(trade_key(t), {}).get("fingerprint") == "closed"
                   and cache.get(trade_key(t), {}).get("bars"))
    n_fetch  = len(trades) - n_skip
    n_open   = sum(1 for t in trades if not trade_is_closed(t))
    print(f"      Cache'den okunacak (kapanmış): {n_skip}")
    print(f"      Fetch edilecek: {n_fetch}  (açık: {n_open}, yeni kapanmış: {n_fetch - n_open})")

    print("\n[2/3] OHLC verisi hazırlanıyor...")
    ohlc, indicators = build_dataset(trades)

    print("\n[3/3] HTML üretiliyor...")
    trades_enriched = enrich_trades(trades, indicators)

    print("\n--- Otomatik trim tarihleri ---")
    for t in trades_enriched:
        if t.get("trims"):
            for i, tr in enumerate(t["trims"], 1):
                date_str = tr.get("date_iso", "BULUNAMADI")
                print(f"  {t['ticker']:6s} {t['entry_date_iso']} T{i} ${tr['price']} @ {date_str}")

    html = (HTML_TEMPLATE
            .replace("__TRADES_JSON__",  json.dumps(trades_enriched, ensure_ascii=False))
            .replace("__OHLC_JSON__",    json.dumps(ohlc, ensure_ascii=False))
            .replace("__ENTRY_BARS__",   str(ENTRY_LINE_BARS))
            .replace("__STOP_BARS__",    str(STOP_LINE_BARS))
            .replace("__TRIM_BARS__",    str(TRIM_LINE_BARS))
            .replace("__ADD_BARS__",     str(ADD_LINE_BARS))
            .replace("__RIGHT_OFFSET__", str(RIGHT_OFFSET)))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n      ✓ {OUTPUT_FILE}")
    print(f"\nAç: open {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
