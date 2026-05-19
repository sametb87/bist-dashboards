#!/usr/bin/env python3
"""
build_trade_dashboard.py
Samet'in BIST trade log'undan interaktif HTML dashboard üretir.
Kullanım: python3 build_trade_dashboard.py

Gereksinimler: pip install yfinance openpyxl pandas numpy
Çıktı: trade_dashboard.html (mevcut varsa kapalı trade'lerin OHLC'si korunur)
"""

import json, re, sys, os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import tempfile, sqlite3

# yfinance SQLite cache hatasını önle
import tempfile as _tmp
_cache_dir = _tmp.mkdtemp()
os.environ['YFINANCE_CACHE_DIR'] = _cache_dir
# Eski yfinance versiyonları için
try:
    import appdirs as _ad
    _ad.user_cache_dir = lambda *a, **k: _cache_dir
except Exception:
    pass
import openpyxl

# ──────────────────────────────────────────────────────────────────────────────
# AYARLAR
# ──────────────────────────────────────────────────────────────────────────────
EXCEL_PATH     = "PT.xlsx"          # Trade log Excel dosyası
OUTPUT_HTML    = "trade_dashboard.html"
BARS_BEFORE    = 50                 # Giriş öncesi gösterilecek bar sayısı
BARS_AFTER     = 50                 # Çıkış sonrası gösterilecek bar sayısı
XU100_TICKER   = "XU100.IS"        # Yahoo Finance XU100 sembolü
EMA_PERIOD     = 21
MA_PERIOD      = 50
ATR_PERIOD     = 14
ENTRY_BARS     = 3
STOP_BARS      = 8
TRIM_BARS      = 3
ADD_BARS       = 3

# ──────────────────────────────────────────────────────────────────────────────
# EXCEL PARSE
# ──────────────────────────────────────────────────────────────────────────────
def parse_trades(path):
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    trades, current = [], None
    for row in rows:
        num, ticker, action = row[1], row[2], row[3]
        if isinstance(num, int) and action is None and ticker:
            if current:
                trades.append(current)
            is_viop  = row[0] == 'V'
            entry_dt = row[4]
            exit_dt  = row[5]
            current = {
                'id':          num,
                'ticker':      ticker,
                'is_viop':     is_viop,
                'entry_date':  entry_dt.strftime('%Y-%m-%d') if isinstance(entry_dt, datetime) else None,
                'exit_date':   exit_dt.strftime('%Y-%m-%d')  if isinstance(exit_dt,  datetime) else None,
                'entry_price': row[6],
                'pnl':         row[15] or 0,
                'pnl_pct':     (row[16] or 0) * 100,
                'moves':       []
            }
        elif action in ('BUY', 'SELL') and current is not None:
            entry_d = row[4]; exit_d = row[5]
            current['moves'].append({
                'action':     action,
                'entry_date': entry_d.strftime('%Y-%m-%d') if isinstance(entry_d, datetime) else None,
                'exit_date':  exit_d.strftime('%Y-%m-%d')  if isinstance(exit_d,  datetime) else None,
                'price':      row[6],
                'shares':     row[7],
                'exit_shares': row[9],
                'exit_price': row[10],
            })
    if current:
        trades.append(current)

    # Yapılandır
    result = []
    for t in trades:
        buys  = [m for m in t['moves'] if m['action'] == 'BUY']
        sells = [m for m in t['moves'] if m['action'] == 'SELL']

        adds = [{'date': b['entry_date'], 'price': b['price']}
                for b in buys[1:] if b['entry_date']]
        trims = [{'date': s['exit_date'], 'price': s['exit_price']}
                 for s in sells[:-1] if s['exit_date'] and s['exit_price']]
        final = next((
            {'date': s['exit_date'], 'price': s['exit_price']}
            for s in reversed(sells)
            if s['exit_date'] and s['exit_price']), None)

        days = 0
        if t['entry_date'] and t['exit_date']:
            try:
                days = (datetime.strptime(t['exit_date'], '%Y-%m-%d') -
                        datetime.strptime(t['entry_date'], '%Y-%m-%d')).days
            except: pass

        result.append({
            'id':          t['id'],
            'ticker':      t['ticker'],
            'is_viop':     t['is_viop'],
            'entry_date':  t['entry_date'],
            'exit_date':   t['exit_date'],
            'entry_price': t['entry_price'],
            'pnl':         t['pnl'],
            'pnl_pct':     t['pnl_pct'],
            'days':        days,
            'adds':        adds,
            'trims':       trims,
            'final_exit':  final,
        })
    return result

# ──────────────────────────────────────────────────────────────────────────────
# OHLC + İNDİKATÖRLER
# ──────────────────────────────────────────────────────────────────────────────
def calc_ema(series, period):
    return pd.Series(series).ewm(span=period, adjust=False).mean().values

def calc_ma(series, period):
    return pd.Series(series).rolling(period).mean().values

def calc_atr(df, period=14):
    high, low, close = df['High'].values, df['Low'].values, df['Close'].values
    prev_close = np.roll(close, 1); prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(
        np.abs(high - prev_close), np.abs(low - prev_close)))
    # Wilder's smoothing (RMA)
    atr = np.full(len(tr), np.nan)
    if len(tr) >= period:
        atr[period-1] = tr[:period].mean()
        for i in range(period, len(tr)):
            atr[i] = (atr[i-1] * (period-1) + tr[i]) / period
    return atr

def calc_ema21_cloud(df):
    """21 EMA of high, low, close — for the cloud overlay"""
    eh = calc_ema(df['High'].values,  EMA_PERIOD)
    el = calc_ema(df['Low'].values,   EMA_PERIOD)
    ec = calc_ema(df['Close'].values, EMA_PERIOD)
    return eh, el, ec

def compute_action_factor(actions_df, entry_date_str, today_str):
    """Bir trade'in entry tarihinden bugüne kadar olan tüm splits ve dividends'tan
    kümülatif adjustment factor hesapla.

    actions_df: yfinance Ticker.actions DataFrame'i (sütunlar: Dividends, Stock Splits)
                veya Ticker.history(actions=True)'dan filtrelenmiş bir df
    entry_date_str: 'YYYY-MM-DD'
    today_str:      'YYYY-MM-DD'

    Mantık:
      - Split N:1 (örn 2:1 → values=2.0) → kümülatif factor *= 1/N
      - Temettü D, ex-date close C → factor *= (C-D)/C (yaklaşık)
        Pratik: temettü değeri, o günden bir önceki günün adjusted close'una göre düşürülür.
        Burada bizde close yok, sadece amount var. Tahmini factor olarak (1 - D/yaklaşık_close)
        kullanırız. Bunun için actions_df içindeki tüm temettüleri toplayıp basit bir
        yaklaşıma gideriz: her temettü için "o gün civarındaki fiyatı" tahmin etmek lazım,
        bizde olmadığı için sadece SPLIT'leri kesin uygularız. Temettüler için
        derived_af farkı uygulanır (aşağıda).

    Returns: (split_factor, dividend_list) — split_factor kesin, dividend_list tahmini.
    """
    if actions_df is None or actions_df.empty:
        return 1.0, []
    try:
        idx = actions_df.index
        try:
            idx = idx.tz_localize(None)
        except (TypeError, AttributeError):
            pass
        # Sadece entry_date'ten SONRA olan aksiyonları say (ex-date entry'den büyük)
        entry_ts = pd.Timestamp(entry_date_str)
        today_ts = pd.Timestamp(today_str)
        mask = (idx > entry_ts) & (idx <= today_ts)
        sub = actions_df.loc[mask] if mask.any() else None
        if sub is None or sub.empty:
            return 1.0, []
    except Exception:
        return 1.0, []

    split_factor = 1.0
    dividends = []  # her biri (date_str, amount)
    for d, row in sub.iterrows():
        sp = float(row.get('Stock Splits', 0) or 0)
        div = float(row.get('Dividends', 0) or 0)
        if sp > 0:
            split_factor *= 1.0 / sp
        if div > 0:
            try:
                dividends.append((d.strftime('%Y-%m-%d'), div))
            except Exception:
                pass
    return split_factor, dividends


def fetch_ohlc(ticker_sym, start_date, end_date):
    """Fetch OHLC from Yahoo Finance with split/dividend adjustment.

    yfinance'in davranışı BIST hisselerinde tutarsızdır: bazen Close'a splitleri zaten
    uygulamıştır, bazen uygulamamıştır. Bu yüzden bu fonksiyondan dönen df.Close değerlerine
    tam güvenmiyoruz. Marker fiyatlarını ayarlarken kullandığımız asıl adj_factor,
    build_bars içinde "derived_af = adjusted_close_on_entry / original_entry_price"
    formülü ile veriden doğrudan türetilir.

    Returns (df_adjusted, adj_factors_series) or (None, None).
    adj_factors maps date → adjustment factor vs original price (yfinance raporu, yedek).
    """
    try:
        pad_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=BARS_BEFORE*2 + MA_PERIOD*2))
        pad_end   = (datetime.strptime(end_date,   '%Y-%m-%d') + timedelta(days=BARS_AFTER  + 10))
        ticker = yf.Ticker(ticker_sym)

        df = ticker.history(
            start=pad_start.strftime('%Y-%m-%d'),
            end=pad_end.strftime('%Y-%m-%d'),
            auto_adjust=False,
            actions=False,
        )
        if df is None or df.empty:
            return None, None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.index = pd.to_datetime(df.index)
        try:
            df.index = df.index.tz_localize(None)
        except TypeError:
            df.index = df.index.tz_convert(None)

        # yfinance Adj Close / Close oranı — temettüyü ve bazı durumlarda split'i içerir
        if 'Adj Close' in df.columns and 'Close' in df.columns:
            raw_adj = (df['Adj Close'] / df['Close']).fillna(1.0)
        else:
            raw_adj = pd.Series(1.0, index=df.index)

        for col in ['Open','High','Low','Close']:
            if col in df.columns:
                df[col] = df[col] * raw_adj

        df.index = df.index.strftime('%Y-%m-%d')
        raw_adj.index = df.index

        df = df[['Open','High','Low','Close']].dropna()
        adj_factor = raw_adj.reindex(df.index).fillna(1.0)
        return df, adj_factor
    except Exception as e:
        print(f"  WARN: {ticker_sym} fetch error: {e}")
        return None, None

def adjust_price(price, date, adj_factor):
    """Adjust an original price using the adj_factor series for a given date.""",
    if price is None or adj_factor is None:
        return price
    # Find closest available date (on or after)
    if date in adj_factor.index:
        return price * adj_factor[date]
    # Try nearby dates (±3 days)
    from datetime import datetime as _dt, timedelta as _td
    try:
        d = _dt.strptime(date, '%Y-%m-%d')
        for delta in range(0, 4):
            for sign in [1, -1]:
                alt = (d + _td(days=delta*sign)).strftime('%Y-%m-%d')
                if alt in adj_factor.index:
                    return price * adj_factor[alt]
    except:
        pass
    return price

def build_bars(trade, df, adj_factor=None, actions_df=None):
    """
    Trim df to window around trade, compute indicators,
    return list of bar dicts for embedding.
    Returns None if entry/exit dates not found in data.
    """
    if df is None or df.empty:
        return None

    entry = trade['entry_date']
    exit_ = trade['exit_date'] or entry  # açık trade: bugüne kadar göster

    # Find entry index
    dates = list(df.index)
    if entry not in dates:
        # Try nearby dates (max 3 days forward)
        for d in range(1, 4):
            alt = (datetime.strptime(entry, '%Y-%m-%d') + timedelta(days=d)).strftime('%Y-%m-%d')
            if alt in dates:
                entry = alt
                break
        else:
            return None  # entry bulunamadı — atla

    ei = dates.index(entry)
    xi = dates.index(exit_) if exit_ in dates else min(ei + BARS_AFTER, len(dates) - 1)

    start_i = max(0, ei - BARS_BEFORE)
    # end_i: çıkış sonrası BARS_AFTER mum VEYA giriş sonrası BARS_AFTER mum (hangisi büyükse).
    # Bu, açık trade'lerde ve yakın kapanmış trade'lerde giriş/çıkışın en sağda kalmasını engeller.
    end_i   = min(len(dates) - 1, max(xi + BARS_AFTER, ei + BARS_AFTER))

    # Hesaplamalar için tam df üzerinden yap (window'dan önce de)
    # Sonra sadece window'u döndür
    close  = df['Close'].values
    high   = df['High'].values
    low    = df['Low'].values
    eh_all, el_all, ec_all = calc_ema21_cloud(df)
    atr_all = calc_atr(df)
    ema21_all = calc_ema(close, EMA_PERIOD)
    ma10_all  = calc_ma(close, 10)
    ma50_all  = calc_ma(close,  MA_PERIOD)

    # ATR uzaklıkları giriş tarihinde
    # ÖNEMLİ: Referans fiyat olarak entry günü kapanışı DEĞİL, kullanıcının GERÇEK GİRİŞ FİYATI kullanılır.
    # df adjusted olduğu için (split/dividend uygulandı), orijinal entry_price'ı da adjusted hale çevirmek lazım
    # ki indikatörlerle aynı baz üzerinde karşılaştırılabilsin.
    #
    # ÇOK ÖNEMLİ: yfinance bazı BIST hisseleri için unadjusted Close'a bile split uyguluyor
    # (auto_adjust=False olmasına rağmen). Bu yüzden Adj Close/Close ile hesapladığımız adj_factor
    # sadece TEMETTÜYÜ yansıtır, splitleri yansıtmaz. Bunu telafi etmek için, factor'ü doğrudan
    # veriden geri çıkarıyoruz: derived_af = adjusted_close_entry_day / original_entry_price
    # Bu yöntem split+temettü ikisini de eksiksiz yakalar.
    entry_i_full = ei  # same index since df not sliced yet
    atr_at_entry = atr_all[entry_i_full] if not np.isnan(atr_all[entry_i_full]) else None
    ema21_at_entry = ema21_all[entry_i_full]
    ma50_at_entry  = ma50_all[entry_i_full]

    raw_entry = trade.get('entry_price')
    adj_close_entry = float(close[entry_i_full])

    # Adjustment factor stratejisi (öncelik sırasına göre):
    # A) actions_df verildiyse: yfinance actions (splits + dividends) üzerinden kümülatif factor hesapla.
    #    Bu en doğru yöntemdir çünkü gerçek aksiyonları kullanır.
    # B) actions yoksa: derived_af = adjusted_close / orig_entry_price (veriden geri çıkarma).
    #    Bu split'i kesin yakalar, küçük temettüleri (≤%8) ihmal eder.
    derived_af = 1.0
    if actions_df is not None and raw_entry and raw_entry > 0:
        today_str = datetime.today().strftime('%Y-%m-%d')
        split_factor, dividends = compute_action_factor(actions_df, entry, today_str)
        # Temettü factor'ünü tahminen hesapla: her temettü için (1 - D/yaklaşık_o_günkü_fiyat)
        # Yaklaşık fiyat olarak: entry günü orijinal fiyatı baz al, sonra her splitin ardından
        # gelen temettülere doğru ilerle. Aslında basitlik açısından, tüm temettülerin toplamını
        # entry'deki orijinal fiyata göre orana çevir ve onu da factor'e ekle.
        # (Bu yaklaşım küçük bir hata payı bırakır ama küçük temettüler için kabul edilebilir.)
        div_factor = 1.0
        running_price = raw_entry
        # Splitleri ve temettüleri tarih sırasına göre tekrar çek
        try:
            ai = actions_df.index
            try: ai = ai.tz_localize(None)
            except (TypeError, AttributeError): pass
            mask = (ai > pd.Timestamp(entry)) & (ai <= pd.Timestamp(today_str))
            sub = actions_df.loc[mask].sort_index() if mask.any() else None
            if sub is not None and not sub.empty:
                for d, row in sub.iterrows():
                    sp = float(row.get('Stock Splits', 0) or 0)
                    div = float(row.get('Dividends', 0) or 0)
                    if sp > 0 and sp != 1.0:
                        running_price /= sp
                    if div > 0 and running_price > 0:
                        div_factor *= max(0.0, (running_price - div) / running_price)
                        running_price -= div
        except Exception:
            pass
        action_based_af = split_factor * div_factor
        if action_based_af > 0 and abs(action_based_af - 1.0) > 0.005:
            derived_af = action_based_af
    elif raw_entry and raw_entry > 0:
        # Fallback: veriden geri çıkar
        ratio = adj_close_entry / raw_entry
        # Eğer kullanıcının giriş fiyatı close'a çok yakınsa (≈ 0.92-1.08 arası), zaten ayar yok
        # demektir (kullanıcı gün içinde close civarında girmiş). Bu bandın dışında ise gerçek bir
        # adjustment vardır.
        if not (0.92 <= ratio <= 1.08):
            derived_af = ratio

    if raw_entry and raw_entry > 0:
        adjusted_entry_for_atr = raw_entry * derived_af
    else:
        adjusted_entry_for_atr = adj_close_entry

    atr_dist_ema21 = round((adjusted_entry_for_atr - ema21_at_entry) / atr_at_entry, 2) if atr_at_entry and not np.isnan(ema21_at_entry) else None
    atr_dist_ma50  = round((adjusted_entry_for_atr - ma50_at_entry)  / atr_at_entry, 2) if atr_at_entry and not np.isnan(ma50_at_entry)  else None

    # Window bars
    bars = []
    for i in range(start_i, end_i + 1):
        d = dates[i]
        bar = {
            'date': d,
            'o': round(float(df['Open'].iloc[i]),  4),
            'h': round(float(df['High'].iloc[i]),  4),
            'l': round(float(df['Low'].iloc[i]),   4),
            'c': round(float(df['Close'].iloc[i]), 4),
            'eh': round(float(eh_all[i]), 4),
            'el': round(float(el_all[i]), 4),
            'ec': round(float(ec_all[i]), 4),
        }
        if not np.isnan(ma10_all[i]):
            bar['m10'] = round(float(ma10_all[i]), 4)
        bars.append(bar)

    # Trade-level derived adjustment factor: marker fiyatlarını grafik ile aynı bazda tutar.
    # Bu, yfinance'in adj_factor'üne güvenmediği için çok daha sağlamdır.
    # Tüm marker fiyatları (entry, adds, trims, exit) bu tek factor ile çarpılır.
    # Kısa süreli trade'lerde (genelde günler/haftalar) bu factor sabit kabul edilir.

    def _adjust(p):
        return round(p * derived_af, 4) if (p is not None and derived_af != 1.0) else p

    entry_price_adj = _adjust(trade['entry_price'])
    adds_adj  = [{'date': a['date'], 'price': _adjust(a['price'])}
                 for a in trade.get('adds', []) if a['date'] and a['price']]
    trims_adj = [{'date': tr['date'], 'price': _adjust(tr['price'])}
                 for tr in trade.get('trims', []) if tr['date'] and tr['price']]
    fe = trade.get('final_exit')
    fe_adj = ({'date': fe['date'], 'price': _adjust(fe['price'])}
              if fe and fe.get('date') and fe.get('price') else None)

    return bars, atr_dist_ema21, atr_dist_ma50, entry_price_adj, adds_adj, trims_adj, fe_adj, derived_af

# ──────────────────────────────────────────────────────────────────────────────
# XU100 ATR UZAKLIKLARI
# ──────────────────────────────────────────────────────────────────────────────
def build_xu100_cache(trades):
    """Fetch XU100 full range and compute ATR distances for all trade entry dates."""
    print("XU100 verisi çekiliyor...")
    all_dates = [t['entry_date'] for t in trades if t['entry_date']]
    min_d = min(all_dates); max_d = max(all_dates)
    df, _ = fetch_ohlc(XU100_TICKER,
                    (datetime.strptime(min_d, '%Y-%m-%d') - timedelta(days=150)).strftime('%Y-%m-%d'),
                    (datetime.strptime(max_d, '%Y-%m-%d') + timedelta(days=10)).strftime('%Y-%m-%d'))
    if df is None:
        print("  WARN: XU100 verisi alınamadı")
        return {}

    close   = df['Close'].values
    atr_all = calc_atr(df)
    ema21   = calc_ema(close, EMA_PERIOD)
    ma50    = calc_ma(close,  MA_PERIOD)
    dates   = list(df.index)
    date_idx = {d: i for i, d in enumerate(dates)}

    cache = {}
    for t in trades:
        ed = t['entry_date']
        if not ed:
            continue
        i = date_idx.get(ed)
        if i is None:
            # Try next trading day
            for offset in range(1, 5):
                alt = (datetime.strptime(ed, '%Y-%m-%d') + timedelta(days=offset)).strftime('%Y-%m-%d')
                if alt in date_idx:
                    i = date_idx[alt]; break
        if i is None or np.isnan(atr_all[i]):
            continue
        atr_v = atr_all[i]
        price = close[i]
        cache[t['id']] = {
            'xu_atr_dist_ema21': round((price - ema21[i]) / atr_v, 2) if not np.isnan(ema21[i]) else None,
            'xu_atr_dist_ma50':  round((price - ma50[i])  / atr_v, 2) if not np.isnan(ma50[i])  else None,
        }
    print(f"  XU100 ATR hesaplandı: {len(cache)} trade")
    return cache

# ──────────────────────────────────────────────────────────────────────────────
# MEVCUT HTML'DEN OHLC YÜKLE (artımlı güncelleme)
# ──────────────────────────────────────────────────────────────────────────────
def load_existing_ohlc(html_path):
    if not os.path.exists(html_path):
        return {}
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    m = re.search(r'const OHLC\s*=\s*(\{.*?\});', content, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except:
        return {}

def load_existing_atr(html_path):
    if not os.path.exists(html_path):
        return {}
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    m = re.search(r'const ATR_DISTS\s*=\s*(\{.*?\});', content, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except:
        return {}

def load_existing_adj(html_path):
    """Load existing adjusted marker prices from HTML cache."""
    if not os.path.exists(html_path):
        return {}
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    m = re.search(r'const ADJ_MARKERS\s*=\s*(\{.*?\});', content, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except:
        return {}

def find_last_cache_date(existing_ohlc):
    """Mevcut cache'de en son hangi tarihe kadar veri var? Bu tarihten sonra split/temettü olan
    ticker'ları yeniden çekmemiz lazım ki adj_factor'lar güncel olsun."""
    last_date = None
    for bars in existing_ohlc.values():
        if not bars:
            continue
        d = bars[-1].get('date')
        if d and (last_date is None or d > last_date):
            last_date = d
    return last_date  # YYYY-MM-DD string or None

def find_tickers_with_recent_actions(chart_trades, existing_ohlc, last_cache_date):
    """Mevcut cache'den sonra split/temettü olan ticker'ları batch fiyat karşılaştırması ile tespit et.
    
    Yaklaşım: yfinance batch download ile son ~15 günün adjusted close'larını çek.
    Her ticker için, cache'deki son barın adjusted close'u ile, yfinance'tan AYNI TARİH için
    bugün çekilen adjusted close'u karşılaştır. Eğer fark anlamlıysa → yfinance fiyat
    geçmişini geriye dönük ayarlamış demektir (split veya temettü olmuştur).
    
    Bu test split + temettü ikisini de yakalar. Çünkü her ikisi de yfinance'ın geçmiş 
    adjusted fiyatlarını değiştirir.
    
    Dönüş: yeniden çekilmesi gereken trade ID'lerinin seti.
    """
    if not last_cache_date:
        return set()

    # Cache'deki son tarihteki adjusted close: ticker → (date, close)
    ticker_to_ids = {}
    ticker_last_close = {}
    for t in chart_trades:
        tid = str(t['id'])
        if tid in existing_ohlc:
            ticker_to_ids.setdefault(t['ticker'], []).append(tid)
            bars = existing_ohlc[tid]
            if bars:
                last_bar = bars[-1]
                prev = ticker_last_close.get(t['ticker'])
                if prev is None or last_bar['date'] > prev[0]:
                    ticker_last_close[t['ticker']] = (last_bar['date'], float(last_bar['c']))

    if not ticker_to_ids:
        return set()

    print(f"  Son cache tarihi: {last_cache_date} — split/temettü kontrolü (batch download, {len(ticker_to_ids)} ticker)...")

    # Batch download: cache'in son tarihinden bugüne kadar
    tickers_list = [tk + '.IS' for tk in ticker_to_ids.keys()]
    # Karşılaştırma için son cache tarihinden 5 gün öncesini başlangıç al
    fetch_start = (datetime.strptime(last_cache_date, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d')
    fetch_end   = (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d')

    try:
        batch = yf.download(
            ' '.join(tickers_list),
            start=fetch_start, end=fetch_end,
            auto_adjust=True, actions=False, progress=False, threads=True,
            group_by='ticker',
        )
    except Exception as e:
        print(f"  WARN: Batch split kontrolü başarısız: {e}")
        return set()

    if batch is None or batch.empty:
        print("  WARN: Batch fiyat verisi boş — split kontrolü atlanıyor")
        return set()

    affected_ids = set()
    affected_tickers = []
    # %0.5'den büyük fark → kesinlikle anlamlı (rounding errorları geç)
    SIGNIFICANT_DIFF = 0.005

    for ticker, ids in ticker_to_ids.items():
        sym = ticker + '.IS'
        if sym not in batch.columns.get_level_values(0):
            continue
        try:
            sub = batch[sym].dropna(subset=['Close'])
            if sub.empty:
                continue
        except Exception:
            continue

        last = ticker_last_close.get(ticker)
        if not last:
            continue
        cache_date, cache_close = last
        if cache_close <= 0:
            continue

        # yfinance'in bu tarih için yeni verdiği adjusted close
        try:
            # Index zaman zon ayarı
            sub_idx = sub.index
            try:
                sub_idx = sub_idx.tz_localize(None)
            except (TypeError, AttributeError):
                pass
            sub.index = sub_idx
            target = pd.Timestamp(cache_date)
            if target in sub.index:
                new_close = float(sub.loc[target, 'Close'])
            else:
                # En yakın geçmiş tarihte ara
                earlier = sub.index[sub.index <= target]
                if len(earlier) == 0:
                    continue
                new_close = float(sub.loc[earlier[-1], 'Close'])
        except Exception:
            continue

        if new_close <= 0:
            continue
        ratio = new_close / cache_close
        # Anlamlı fark var mı? (split, büyük temettü, veya hisse adı değişikliği vs)
        if abs(ratio - 1.0) > SIGNIFICANT_DIFF:
            affected_ids.update(ids)
            affected_tickers.append(f"{ticker}({ratio:.3f})")

    if affected_tickers:
        print(f"  Split/temettü olan ticker'lar ({len(affected_tickers)}): {', '.join(affected_tickers[:20])}{'...' if len(affected_tickers)>20 else ''}")
        print(f"  Yeniden çekilecek trade sayısı (split/temettü kaynaklı): {len(affected_ids)}")
    else:
        print("  Split/temettü değişikliği bulunamadı.")
    return affected_ids

# ──────────────────────────────────────────────────────────────────────────────
# ANA DERLEME
# ──────────────────────────────────────────────────────────────────────────────
def main():
    # macOS dosya handle limitini artır
    import resource
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 10000), hard))
    except Exception:
        pass

    print(f"Trade log okunuyor: {EXCEL_PATH}")
    trades = parse_trades(EXCEL_PATH)
    print(f"  {len(trades)} trade parse edildi")

    # Kapalı/açık ayrımı
    def is_open(t):
        return not t['exit_date'] or t['exit_date'] in ('', '00:00:00')

    # Mevcut HTML'den cached OHLC ve ATR yükle
    existing_ohlc = load_existing_ohlc(OUTPUT_HTML)
    existing_atr  = load_existing_atr(OUTPUT_HTML)
    print(f"  Mevcut OHLC cache: {len(existing_ohlc)} trade")

    # Hangi trade'ler için veri çekilecek?
    # - Yeni trade (ID mevcut HTML'de yok): çek
    # - Açık trade (exit_date yok): her zaman yeniden çek
    # - Kapalı trade ve ID mevcut: mevcut veriyi koru
    chart_trades = [t for t in trades if not t['is_viop']]

    # Check if existing OHLC has af fields (new format) or is old format
    has_af = any(
        any('af' in bar for bar in bars)
        for bars in existing_ohlc.values()
        if bars
    )
    if existing_ohlc and not has_af:
        print("  Eski OHLC formatı tespit edildi (af yok) — tüm veriler yeniden çekilecek")
        existing_ohlc = {}

    to_fetch = []
    # Bugünden BARS_AFTER takvim günü öncesine kadar olan trade'lerin entry'si "yakın geçmişte" sayılır.
    # Bu trade'leri her seferinde yeniden çek ki entry günü grafiğin ortasında kalsın
    # (sol tarafta 50 mum, sağ tarafta 50 mum). Aksi takdirde yeni trade'in entry'si en sağda kalır.
    # Takvim günü olarak BARS_AFTER kullanıyoruz (≈50 takvim günü ≈ 35 işlem günü), bu yeterli güvenlik payı sağlar.
    refresh_cutoff = (datetime.today() - timedelta(days=BARS_AFTER)).strftime('%Y-%m-%d')

    # Mevcut cache'deki son tarihten sonra split/temettü olan ticker'ları tespit et
    last_cache_date = find_last_cache_date(existing_ohlc)
    split_affected_ids = find_tickers_with_recent_actions(chart_trades, existing_ohlc, last_cache_date)

    refresh_recent_n = 0
    refresh_split_n = 0
    for t in chart_trades:
        tid = str(t['id'])
        needs_fetch = False
        reason = None
        if is_open(t):
            needs_fetch = True; reason = 'open'
        elif tid not in existing_ohlc:
            needs_fetch = True; reason = 'new'
        elif t['entry_date'] and t['entry_date'] >= refresh_cutoff:
            # Kapanmış olsa bile, entry tarihi son ~50 takvim gününde olan trade'ler:
            # sağ tarafta yeterli mum oluşması için yeniden çekilir
            needs_fetch = True; reason = 'recent'
            refresh_recent_n += 1
        elif tid in split_affected_ids:
            # Bu trade'in ticker'ında son cache tarihinden sonra split/temettü oldu — yeniden çek
            needs_fetch = True; reason = 'split'
            refresh_split_n += 1
        if needs_fetch:
            to_fetch.append(t)

    print(f"  Veri çekilecek trade sayısı: {len(to_fetch)} "
          f"({sum(1 for t in to_fetch if is_open(t))} açık, "
          f"{refresh_recent_n} son ~{BARS_AFTER} gün içinde entry'li kapalı, "
          f"{refresh_split_n} split/temettü etkilenen, "
          f"{len(to_fetch) - sum(1 for t in to_fetch if is_open(t)) - refresh_recent_n - refresh_split_n} yeni)")

    # XU100 ATR
    # Sadece yeni trade'ler için hesapla
    new_trade_ids = {str(t['id']) for t in to_fetch}
    xu100_cache = {}
    if new_trade_ids:
        new_trades_list = [t for t in chart_trades if str(t['id']) in new_trade_ids]
        xu100_cache = build_xu100_cache(new_trades_list)

    # Merge existing ATR
    merged_atr = {**existing_atr}
    for tid, v in xu100_cache.items():
        merged_atr[str(tid)] = v

    # OHLC fetch
    ohlc = {k: v for k, v in existing_ohlc.items()}  # copy existing
    stock_atr_dists = {}  # id → {atr_dist_ema21, atr_dist_ma50}
    adj_markers = {}      # id → {entry_price, adds, trims, final_exit} (adjusted)
    # Load existing adj_markers from HTML if available
    existing_adj = load_existing_adj(OUTPUT_HTML)
    adj_markers = {k: v for k, v in existing_adj.items()}

    # Ticker actions cache: ticker → DataFrame (Dividends + Stock Splits)
    # Her ticker için bir kez çekilir, aynı tickerın tüm trade'lerinde kullanılır.
    actions_cache = {}

    def get_actions(ticker_sym):
        """Ticker actions'ı cache'le."""
        if ticker_sym in actions_cache:
            return actions_cache[ticker_sym]
        try:
            tk = yf.Ticker(ticker_sym)
            acts = tk.actions
            if acts is None or acts.empty:
                actions_cache[ticker_sym] = None
            else:
                actions_cache[ticker_sym] = acts
        except Exception:
            actions_cache[ticker_sym] = None
        return actions_cache[ticker_sym]

    for i, t in enumerate(to_fetch):
        ticker_sym = t['ticker'] + '.IS'
        entry = t['entry_date']
        exit_ = t['exit_date'] if not is_open(t) else datetime.today().strftime('%Y-%m-%d')
        print(f"  [{i+1}/{len(to_fetch)}] {t['ticker']} #{t['id']}  {entry} → {exit_}  ...", end=' ')
        sys.stdout.flush()

        df, adj_factor = fetch_ohlc(ticker_sym, entry, exit_)
        if df is None:
            print("SKIP (fetch failed)")
            continue

        # Ticker actions cache'i (splits + dividends) — entry'den bugüne kadar kümülatif factor için
        actions = get_actions(ticker_sym)

        result = build_bars(t, df, adj_factor, actions_df=actions)
        if result is None:
            print("SKIP (date not found)")
            continue

        bars, atr_dist_ema, atr_dist_ma, entry_price_adj, adds_adj, trims_adj, fe_adj, derived_af = result
        ohlc[str(t['id'])] = bars

        # Store adjusted marker prices for this trade
        adj_markers[str(t['id'])] = {
            'entry_price': round(entry_price_adj, 4) if entry_price_adj else t['entry_price'],
            'adds':  [{'date': a['date'], 'price': round(a['price'], 4)} for a in adds_adj],
            'trims': [{'date': tr['date'], 'price': round(tr['price'], 4)} for tr in trims_adj],
            'final_exit': {'date': fe_adj['date'], 'price': round(fe_adj['price'], 4)} if fe_adj else None,
            'daf': round(derived_af, 6),
        }

        if atr_dist_ema is not None or atr_dist_ma is not None:
            stock_atr_dists[str(t['id'])] = {
                'st_atr_dist_ema21': atr_dist_ema,
                'st_atr_dist_ma50':  atr_dist_ma,
            }
        print(f"OK ({len(bars)} bar, atr_ema={atr_dist_ema}, atr_ma={atr_dist_ma}, daf={derived_af:.4f})")

    # Merge stock ATR dists into main ATR dict
    for tid, v in stock_atr_dists.items():
        if tid in merged_atr:
            merged_atr[tid].update(v)
        else:
            merged_atr[tid] = v

    # ── Analiz verilerini hesapla ──────────────────────────────────────────────
    # Tüm trade'ler (VIOP dahil) analiz için
    closed = [t for t in trades if t['exit_date'] and len(t['exit_date']) == 10]
    winners = [t for t in closed if t['pnl'] > 0]
    losers  = [t for t in closed if t['pnl'] <= 0]
    total   = len(closed)
    viop_n  = sum(1 for t in trades if t['is_viop'])

    total_profit = sum(t['pnl'] for t in winners)
    total_loss   = abs(sum(t['pnl'] for t in losers))
    pf = total_profit / total_loss if total_loss else 0

    from collections import defaultdict
    monthly = defaultdict(lambda: {'wins':0,'losses':0,'pnl':0,'n':0})
    yearly  = defaultdict(lambda: {'wins':0,'losses':0,'pnl':0,'n':0})
    for t in closed:
        ym = t['entry_date'][:7]; yr = t['entry_date'][:4]
        pnl = t['pnl']
        monthly[ym]['n']   += 1; monthly[ym]['pnl'] += pnl
        yearly[yr]['n']    += 1; yearly[yr]['pnl']  += pnl
        if pnl > 0:
            monthly[ym]['wins'] += 1; yearly[yr]['wins'] += 1
        else:
            monthly[ym]['losses'] += 1; yearly[yr]['losses'] += 1

    monthly_list = [{'month': k, **v} for k, v in sorted(monthly.items())]
    yearly_list  = [{'year':  k, **v} for k, v in sorted(yearly.items())]

    analytics = {
        'total': total,
        'viop_n': viop_n,
        'win_n': len(winners),
        'lose_n': len(losers),
        'win_rate': round(len(winners)/total*100, 1) if total else 0,
        'profit_factor': round(pf, 2),
        'total_pnl': round(total_profit - total_loss),
        'total_profit': round(total_profit),
        'total_loss': round(total_loss),
        'avg_win_days': round(sum(t['days'] for t in winners) / len(winners), 1) if winners else 0,
        'avg_lose_days': round(sum(t['days'] for t in losers) / len(losers), 1) if losers else 0,
        'avg_win_pct': round(sum(t['pnl_pct'] for t in winners) / len(winners), 2) if winners else 0,
        'avg_lose_pct': round(sum(t['pnl_pct'] for t in losers) / len(losers), 2) if losers else 0,
        'best_trade': max(closed, key=lambda t: t['pnl_pct'], default=None),
        'worst_trade': min(closed, key=lambda t: t['pnl_pct'], default=None),
        'monthly': monthly_list,
        'yearly':  yearly_list,
        'trade_records': [
            {
                'id': t['id'], 'ticker': t['ticker'], 'is_viop': t['is_viop'],
                'entry_date': t['entry_date'], 'exit_date': t['exit_date'],
                'entry_price': t['entry_price'], 'pnl': round(t['pnl']),
                'pnl_pct': round(t['pnl_pct'], 2), 'days': t['days']
            }
            for t in trades
        ]
    }

    # ATR analysis buckets — tüm trade'ler için (merged_atr kullan)
    # EMA21 için: 0–2+ aralığı (5 bucket); MA50 için: 0–4+ aralığı (9 bucket)
    def bucket_ema(v):
        if v is None: return None
        av = abs(v)
        if av < 0.5: return '0–0.5'
        if av < 1.0: return '0.5–1'
        if av < 1.5: return '1–1.5'
        if av < 2.0: return '1.5–2'
        return '2+'

    def bucket_ma(v):
        if v is None: return None
        av = abs(v)
        if av < 0.5: return '0–0.5'
        if av < 1.0: return '0.5–1'
        if av < 1.5: return '1–1.5'
        if av < 2.0: return '1.5–2'
        if av < 2.5: return '2–2.5'
        if av < 3.0: return '2.5–3'
        if av < 3.5: return '3–3.5'
        if av < 4.0: return '3.5–4'
        return '4+'

    BUCKET_ORDER_EMA = ['0–0.5','0.5–1','1–1.5','1.5–2','2+']
    BUCKET_ORDER_MA  = ['0–0.5','0.5–1','1–1.5','1.5–2','2–2.5','2.5–3','3–3.5','3.5–4','4+']
    atr_analysis = {}
    for key_label, atr_key, is_ma50 in [
        ('xu100_ema21', 'xu_atr_dist_ema21', False),
        ('xu100_ma50',  'xu_atr_dist_ma50',  True),
        ('stock_ema21', 'st_atr_dist_ema21', False),
        ('stock_ma50',  'st_atr_dist_ma50',  True),
    ]:
        bucket_fn = bucket_ma if is_ma50 else bucket_ema
        order     = BUCKET_ORDER_MA if is_ma50 else BUCKET_ORDER_EMA
        buckets = {b: {'n':0,'w':0,'pnl':0,'win_pnl':0,'lose_pnl':0} for b in order}
        for t in closed:
            tid = str(t['id'])
            atr_info = merged_atr.get(tid, {})
            v = atr_info.get(atr_key)
            bk = bucket_fn(v)
            if bk is None: continue
            b = buckets[bk]
            b['n'] += 1; b['pnl'] += t['pnl_pct']
            if t['pnl'] > 0: b['w'] += 1; b['win_pnl'] += t['pnl_pct']
            else: b['lose_pnl'] += t['pnl_pct']
        result_list = []
        for bname in order:
            b = buckets[bname]
            n = b['n']
            if n == 0: result_list.append({'name':bname,'n':0}); continue
            w = b['w']; l = n-w
            result_list.append({
                'name':     bname,
                'n':        n,
                'wr':       round(w/n*100, 1),
                'avg_ret':  round(b['pnl']/n, 2),
                'avg_win':  round(b['win_pnl']/w, 2) if w else 0,
                'avg_lose': round(b['lose_pnl']/l, 2) if l else 0,
                'pf':       round(abs(b['win_pnl']/b['lose_pnl']), 2) if b['lose_pnl'] else None,
            })
        atr_analysis[key_label] = result_list

    analytics['atr_analysis'] = atr_analysis

    # Chart trade list (non-VIOP)
    def derive_af_from_cache(bars_list, entry_date, orig_entry_price):
        """Veriden trade-level adjustment factor türet:
        derived_af = adjusted_close_on_entry_day / original_entry_price
        Bu yaklaşım yfinance'in af'sine güvenmez, hem split hem temettüyü yakalar.
        """
        if not bars_list or not entry_date or not orig_entry_price or orig_entry_price <= 0:
            return 1.0
        adj_close = None
        for b in bars_list:
            if b['date'] == entry_date:
                adj_close = float(b['c']); break
        if adj_close is None:
            # En yakın geçmiş tarihte ara (±3 gün)
            try:
                d = datetime.strptime(entry_date, '%Y-%m-%d')
                bar_map = {b['date']: float(b['c']) for b in bars_list}
                for delta in range(1, 4):
                    for sign in [1, -1]:
                        alt = (d + timedelta(days=delta*sign)).strftime('%Y-%m-%d')
                        if alt in bar_map:
                            adj_close = bar_map[alt]; break
                    if adj_close is not None: break
            except Exception:
                pass
        if adj_close is None or adj_close <= 0:
            return 1.0
        af = adj_close / orig_entry_price
        # Normal aralıkta (0.92-1.08) ise adjustment yoktur, factor=1
        if 0.92 <= af <= 1.08:
            return 1.0
        return af

    chart_trade_list = []
    for t in chart_trades:
        tid = str(t['id'])
        bars_list = ohlc.get(tid, [])

        # 1) Eğer bu trade adj_markers'ta önceden hesaplanmış adjusted marker'lara sahipse
        #    (bu çalıştırmada veya önceki HTML'de) doğrudan oradan al
        am = adj_markers.get(tid)
        if am and am.get('entry_price') is not None and 'daf' in am:
            # Yeni format (build_bars'tan gelen derived_af'li)
            entry_price_adj = am['entry_price']
            adds_adj  = am.get('adds')  or t['adds']
            trims_adj = am.get('trims') or t['trims']
            fe_adj    = am.get('final_exit') or t['final_exit']
        else:
            # 2) Cache'den gelen eski format trade: derived_af'yi cache verisinden türet
            af = derive_af_from_cache(bars_list, t['entry_date'], t['entry_price'])

            def _adj(p):
                return round(p * af, 4) if (p is not None and af != 1.0) else p

            entry_price_adj = _adj(t['entry_price'])
            adds_adj = [{'date': a['date'], 'price': _adj(a['price'])}
                        for a in t['adds'] if a['date'] and a['price']]
            trims_adj = [{'date': tr['date'], 'price': _adj(tr['price'])}
                         for tr in t['trims'] if tr['date'] and tr['price']]
            fe = t['final_exit']
            fe_adj = ({'date': fe['date'], 'price': _adj(fe['price'])}
                      if fe and fe.get('date') and fe.get('price') else None)

            # Cache'den okunan trade için adj_markers'a yaz (bir sonraki çalıştırmada yeniden hesaplamayalım)
            adj_markers[tid] = {
                'entry_price': entry_price_adj,
                'adds':  adds_adj,
                'trims': trims_adj,
                'final_exit': fe_adj,
                'daf': round(af, 6),
            }

        chart_trade_list.append({
            'id': tid,
            'ticker': t['ticker'],
            'entry_date': t['entry_date'],
            'exit_date': t['exit_date'],
            'entry_price': entry_price_adj,
            'pnl': round(t['pnl']),
            'pnl_pct': round(t['pnl_pct'], 2),
            'days': t['days'],
            'adds':       adds_adj,
            'trims':      trims_adj,
            'final_exit': fe_adj,
            'atr_dist_ema': merged_atr.get(tid,{}).get('st_atr_dist_ema21'),
            'atr_dist_ma':  merged_atr.get(tid,{}).get('st_atr_dist_ma50'),
            'xu_atr_ema':   merged_atr.get(tid,{}).get('xu_atr_dist_ema21'),
            'xu_atr_ma':    merged_atr.get(tid,{}).get('xu_atr_dist_ma50'),
        })

    # ── HTML üret ─────────────────────────────────────────────────────────────
    print("HTML üretiliyor...")
    build_time = datetime.now().strftime('%Y-%m-%d %H:%M')

    ohlc_json      = json.dumps(ohlc,            ensure_ascii=False)
    atr_json       = json.dumps(merged_atr,      ensure_ascii=False)
    analytics_json = json.dumps(analytics,       ensure_ascii=False)
    chart_trades_j = json.dumps(chart_trade_list, ensure_ascii=False)
    all_trades_j   = json.dumps(analytics['trade_records'], ensure_ascii=False)

    adj_markers_j = json.dumps(adj_markers, ensure_ascii=False)
    html = generate_html(ohlc_json, atr_json, adj_markers_j, analytics_json, chart_trades_j, all_trades_j, build_time)

    # Temp dosyaya yaz, sonra rename (Too many open files hatasını önler)
    tmp_path = OUTPUT_HTML + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(html)
    if os.path.exists(OUTPUT_HTML):
        os.remove(OUTPUT_HTML)
    os.rename(tmp_path, OUTPUT_HTML)

    size_kb = os.path.getsize(OUTPUT_HTML) / 1024
    print(f"\n✅ Tamamlandı! → {OUTPUT_HTML}  ({size_kb:.0f} KB)")
    print(f"   {len(ohlc)} trade OHLC verisi, {len(merged_atr)} trade ATR verisi gömüldü")


# ──────────────────────────────────────────────────────────────────────────────
# HTML TEMPLATE
# ──────────────────────────────────────────────────────────────────────────────
def generate_html(ohlc_json, atr_json, adj_markers_j, analytics_json, chart_trades_j, all_trades_j, build_time):
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Personal Trade Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {{
    --bg:#fff;--panel:#fff;--border:#e1e4e8;--text:#1a1a1a;--muted:#6b7280;
    --blue:#2962ff;--green:#16a34a;--orange:#f97316;--red:#dc2626;
    --ab:#f8f9fa;--ab2:#f0f4ff;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:16px;font-size:13px}}
  h1{{font-size:18px;font-weight:700}} .subtitle{{color:var(--muted);font-size:11px;margin-top:2px}}

  .tabs{{display:flex;gap:0;margin:16px 0 0;border-bottom:2px solid var(--border);overflow-x:auto}}
  .tab-btn{{padding:8px 18px;font-size:12px;font-weight:600;border:none;background:none;cursor:pointer;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s;white-space:nowrap}}
  .tab-btn.active{{color:var(--text);border-bottom-color:var(--text)}}
  .tab-content{{display:none;padding-top:20px}}
  .tab-content.active{{display:block}}

  /* STATS */
  .stats-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px}}
  @media(max-width:900px){{.stats-grid{{grid-template-columns:repeat(2,1fr)}}}}
  .stat-card{{background:var(--ab);border:1px solid var(--border);border-radius:8px;padding:14px 16px}}
  .stat-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
  .stat-value{{font-size:22px;font-weight:700;line-height:1}}
  .stat-sub{{font-size:10px;color:var(--muted);margin-top:4px}}
  .clr-green{{color:var(--green)}}.clr-red{{color:var(--red)}}.clr-blue{{color:var(--blue)}}

  .two-col{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px}}
  @media(max-width:800px){{.two-col{{grid-template-columns:1fr}}}}
  .section-card{{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:16px}}
  .section-title{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin-bottom:12px}}

  .wl-row{{display:flex;gap:16px}}
  .wl-col{{flex:1}}
  .wl-head{{font-size:11px;font-weight:700;margin-bottom:8px}}
  .wl-head.w{{color:var(--green)}}.wl-head.l{{color:var(--red)}}
  .wl-item{{display:flex;justify-content:space-between;font-size:12px;padding:4px 0;border-bottom:1px solid var(--border)}}
  .wl-item:last-child{{border-bottom:none}}
  .wl-key{{color:var(--muted)}}
  #monthly-chart{{height:160px}}

  /* ATR TAB */
  .atr-info{{background:var(--ab2);border:1px solid #c7d7fe;border-radius:8px;padding:12px 14px;margin-bottom:16px;font-size:12px;color:#3730a3;line-height:1.6}}
  .atr-tabs{{display:flex;gap:6px;margin-bottom:16px;flex-wrap:wrap}}
  .atr-tab{{padding:6px 14px;border:1px solid var(--border);border-radius:20px;font-size:11px;font-weight:600;cursor:pointer;background:var(--ab);color:var(--muted)}}
  .atr-tab.active{{background:var(--text);color:#fff;border-color:var(--text)}}
  .atr-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}}
  @media(max-width:800px){{.atr-grid{{grid-template-columns:1fr}}}}
  .atr-chart-wrap{{border:1px solid var(--border);border-radius:8px;padding:12px}}
  .atr-chart-title{{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:10px}}
  .atr-bar-chart{{display:flex;flex-direction:column;gap:8px}}
  .atr-bar-row{{display:flex;align-items:center;gap:8px;font-size:11px}}
  .atr-bar-label{{width:70px;color:var(--muted);text-align:right;flex-shrink:0;font-size:10px}}
  .atr-bar-track{{flex:1;height:22px;background:var(--ab);border-radius:3px;overflow:hidden;position:relative}}
  .atr-bar-fill{{height:100%;border-radius:3px;transition:width .4s ease;min-width:2px}}
  .atr-bar-val{{position:absolute;right:6px;top:50%;transform:translateY(-50%);font-size:10px;font-weight:700;white-space:nowrap}}
  .atr-bar-n{{width:55px;color:var(--muted);font-size:10px;flex-shrink:0}}

  /* TABLE */
  .tbl-controls{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:14px;padding:10px 12px;background:var(--ab);border:1px solid var(--border);border-radius:8px}}
  .tbl-ctrl{{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--muted)}}
  input[type=text],select{{border:1px solid var(--border);border-radius:4px;padding:4px 7px;font-size:12px;background:#fff;color:var(--text)}}
  input[type=text]{{width:110px}}
  .tbl-btn{{padding:5px 10px;border:1px solid var(--border);border-radius:4px;background:#fff;font-size:11px;cursor:pointer;font-weight:600}}
  .tbl-btn:hover{{background:#f0f0f0}}
  .tbl-wrap{{overflow-x:auto}}
  .trade-table{{width:100%;border-collapse:collapse;font-size:11px;min-width:700px}}
  .trade-table th{{padding:7px 10px;font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted);border-bottom:2px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;background:var(--ab);position:sticky;top:0}}
  .trade-table th:hover{{color:var(--text)}}
  .trade-table th.sort-asc::after{{content:" ▲";font-size:8px}}
  .trade-table th.sort-desc::after{{content:" ▼";font-size:8px}}
  .trade-table td{{padding:5px 10px;border-bottom:1px solid var(--border);white-space:nowrap}}
  .trade-table tr:hover td{{background:var(--ab)}}
  .badge{{display:inline-block;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700}}
  .bw{{background:#dcfce7;color:#16a34a}}.bl{{background:#fee2e2;color:#dc2626}}
  .bv{{background:#e0e7ff;color:#4f46e5}}.bo{{background:#fef3c7;color:#d97706}}
  .pnl-pos{{color:var(--green);font-weight:600}}.pnl-neg{{color:var(--red);font-weight:600}}
  .tbl-summary{{font-size:11px;color:var(--muted);margin-bottom:8px}}

  /* TABLE DETAIL TABLE (yearly/general) */
  .gen-table{{width:100%;border-collapse:collapse;font-size:12px}}
  .gen-table th{{text-align:left;padding:6px 8px;font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted);border-bottom:1px solid var(--border)}}
  .gen-table td{{padding:6px 8px;border-bottom:1px solid var(--border)}}
  .gen-table tr:last-child td{{border-bottom:none}}

  /* CHARTS */
  .chart-controls{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:16px;padding:12px;background:var(--ab);border:1px solid var(--border);border-radius:8px}}
  .ctrl-group{{display:flex;align-items:center;gap:6px}}
  .ctrl-label{{font-size:11px;color:var(--muted)}}
  .ctrl-btn{{padding:5px 12px;border:1px solid var(--border);border-radius:4px;background:#fff;font-size:12px;cursor:pointer;font-weight:600}}
  .ctrl-btn:hover{{background:#f0f0f0}}
  .charts-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}
  @media(max-width:800px){{.charts-grid{{grid-template-columns:1fr}}}}
  .chart-card{{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:8px 10px 10px;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
  .card-head{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;gap:8px}}
  .ticker{{font-size:15px;font-weight:700;letter-spacing:.3px}}
  .card-meta{{font-size:10px;color:var(--muted);text-align:right;line-height:1.6}}
  .card-meta .pos{{color:var(--green);font-weight:600}}.card-meta .neg{{color:var(--red);font-weight:600}}
  .chart-area{{height:440px;width:100%}}
  .chart-nodata{{height:100px;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:12px;background:var(--ab);border-radius:4px}}
  .legend{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px}}
  .leg-item{{display:flex;align-items:center;gap:4px;font-size:11px;color:var(--muted)}}
  .leg-dot{{width:16px;height:3px;border-radius:2px}}
  .pagination{{display:flex;align-items:center;gap:8px;margin-top:16px;justify-content:center}}
  .page-info{{font-size:12px;color:var(--muted)}}
</style>
</head>
<body>

<h1>Personal Trade Dashboard</h1>
<div class="subtitle">2022–2026 · BIST &nbsp;·&nbsp; Son güncelleme: {build_time}</div>

<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('analysis')">📊 Trade Analizi</button>
  <button class="tab-btn"        onclick="switchTab('atr')">📐 ATR Uzaklık</button>
  <button class="tab-btn"        onclick="switchTab('table')">📋 Trade Tablosu</button>
  <button class="tab-btn"        onclick="switchTab('charts')">📈 Grafikler</button>
</div>

<!-- ═══ TAB 1: ANALİZ ═══ -->
<div id="tab-analysis" class="tab-content active">
  <div class="stats-grid" id="stats-cards"></div>
  <div class="two-col">
    <div class="section-card">
      <div class="section-title">Kazanan vs Kaybeden</div>
      <div class="wl-row" id="wl-compare"></div>
    </div>
    <div class="section-card">
      <div class="section-title">Yıllık Performans</div>
      <table class="gen-table" id="yearly-table">
        <thead><tr><th>Yıl</th><th>İşlem</th><th>Win%</th><th>PnL</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
  <div class="section-card">
    <div class="section-title">Aylık PnL</div>
    <div id="monthly-chart"></div>
  </div>
</div>

<!-- ═══ TAB 2: ATR ═══ -->
<div id="tab-atr" class="tab-content">
  <div class="atr-info">
    ATR uzaklıkları Python script çalıştırıldığında hesaplanarak HTML'e gömülür.
    Veri kaynağı: Yahoo Finance (yfinance). ATR(14) Wilder yöntemi, EMA(21), MA(50).
    <b>Pozitif değer</b> = fiyat MA/EMA üzerinde &nbsp;·&nbsp; <b>Negatif</b> = altında.
  </div>
  <div class="atr-tabs" id="atr-subtabs">
    <button class="atr-tab active" onclick="showATRSub('xu100_ema21')">XU100 / EMA21</button>
    <button class="atr-tab"        onclick="showATRSub('xu100_ma50')">XU100 / MA50</button>
    <button class="atr-tab"        onclick="showATRSub('stock_ema21')">Hisse / EMA21</button>
    <button class="atr-tab"        onclick="showATRSub('stock_ma50')">Hisse / MA50</button>
  </div>
  <div id="atr-content">
    <div class="atr-grid">
      <div class="atr-chart-wrap">
        <div class="atr-chart-title" id="atr-main-title">Win Rate</div>
        <div class="atr-bar-chart" id="atr-winrate-bars"></div>
      </div>
      <div class="atr-chart-wrap">
        <div class="atr-chart-title">Ortalama Getiri (%)</div>
        <div class="atr-bar-chart" id="atr-return-bars"></div>
      </div>
      <div class="atr-chart-wrap">
        <div class="atr-chart-title">Ort. Kazanan Getiri (%)</div>
        <div class="atr-bar-chart" id="atr-winret-bars"></div>
      </div>
      <div class="atr-chart-wrap">
        <div class="atr-chart-title">İşlem Sayısı</div>
        <div class="atr-bar-chart" id="atr-count-bars"></div>
      </div>
    </div>
    <div class="section-card" style="margin-top:16px">
      <div class="section-title">Detay Tablosu</div>
      <div class="tbl-wrap">
        <table class="trade-table" id="atr-detail-table">
          <thead><tr>
            <th>ATR Grubu</th><th>İşlem</th><th>Win Rate</th>
            <th>Ort. Getiri</th><th>Ort. Kazanan</th><th>Ort. Kaybeden</th><th>Profit Factor</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- ═══ TAB 3: TABLO ═══ -->
<div id="tab-table" class="tab-content">
  <div class="tbl-controls">
    <div class="tbl-ctrl"><span>Ticker:</span><input type="text" id="tt-ticker" placeholder="FROTO..." oninput="renderTable()"></div>
    <div class="tbl-ctrl"><span>Yıl:</span>
      <select id="tt-year" onchange="renderTable()">
        <option value="">Tümü</option>
        <option>2022</option><option>2023</option><option>2024</option><option>2025</option><option>2026</option>
      </select>
    </div>
    <div class="tbl-ctrl"><span>Sonuç:</span>
      <select id="tt-result" onchange="renderTable()">
        <option value="">Tümü</option>
        <option value="win">Kazanan</option><option value="loss">Kaybeden</option><option value="open">Açık</option>
      </select>
    </div>
    <div class="tbl-ctrl"><span>Tür:</span>
      <select id="tt-viop" onchange="renderTable()">
        <option value="">Tümü</option><option value="no">VIOP Hariç</option><option value="yes">Sadece VIOP</option>
      </select>
    </div>
    <button class="tbl-btn" onclick="clearTblFilters()">Temizle</button>
    <div style="margin-left:auto;font-size:11px;color:var(--muted)" id="tt-count"></div>
  </div>
  <div class="tbl-summary" id="tt-summary"></div>
  <div class="tbl-wrap">
    <table class="trade-table">
      <thead><tr>
        <th onclick="sortTbl('id')">#</th>
        <th onclick="sortTbl('ticker')">Ticker</th>
        <th onclick="sortTbl('entry_date')">Giriş</th>
        <th onclick="sortTbl('exit_date')">Çıkış</th>
        <th onclick="sortTbl('days')">Gün</th>
        <th onclick="sortTbl('entry_price')">Fiyat</th>
        <th onclick="sortTbl('pnl_pct')">Getiri%</th>
        <th onclick="sortTbl('pnl')">PnL(₺)</th>
        <th>Durum</th><th>ATR/EMA21</th><th>ATR/MA50</th><th>XU/EMA21</th><th>XU/MA50</th>
      </tr></thead>
      <tbody id="tt-body"></tbody>
    </table>
  </div>
  <div class="pagination" id="tt-pag"></div>
</div>

<!-- ═══ TAB 4: GRAFİKLER ═══ -->
<div id="tab-charts" class="tab-content">
  <div class="chart-controls">
    <div class="ctrl-group"><span class="ctrl-label">Ticker:</span><input type="text" id="f-ticker" placeholder="Örn: FROTO" onkeyup="applyFilters()"></div>
    <div class="ctrl-group"><span class="ctrl-label">Yıl:</span>
      <select id="f-year" onchange="applyFilters()">
        <option value="">Tümü</option>
        <option>2022</option><option>2023</option><option>2024</option><option>2025</option><option>2026</option>
      </select>
    </div>
    <div class="ctrl-group"><span class="ctrl-label">Sonuç:</span>
      <select id="f-result" onchange="applyFilters()">
        <option value="">Tümü</option><option value="win">Kazanan</option><option value="loss">Kaybeden</option>
      </select>
    </div>
    <button class="ctrl-btn" onclick="clearFilters()">Temizle</button>
    <div style="margin-left:auto;font-size:11px;color:var(--muted)" id="f-count"></div>
  </div>
  <div class="legend">
    <div class="leg-item"><div class="leg-dot" style="background:#2962ff"></div>Giriş (Mavi)</div>
    <div class="leg-item"><div class="leg-dot" style="background:#16a34a"></div>Ekleme (Yeşil)</div>
    <div class="leg-item"><div class="leg-dot" style="background:#f97316"></div>Ara Satış (Turuncu)</div>
    <div class="leg-item"><div class="leg-dot" style="background:#dc2626"></div>Son Satış (Kırmızı)</div>
    <div class="leg-item"><div class="leg-dot" style="background:#8b4513"></div>Stop (Kahve)</div>
  </div>
  <div class="charts-grid" id="charts-grid"></div>
  <div class="pagination" id="charts-pag"></div>
</div>

<script>
const OHLC       = {ohlc_json};
const ATR_DISTS  = {atr_json};
const ADJ_MARKERS = {adj_markers_j};
const ANALYTICS  = {analytics_json};
const CHART_TRADES = {chart_trades_j};
const ALL_TRADES = {all_trades_j};
const ENTRY_BARS = 3;
const STOP_BARS  = 8;
const TRIM_BARS  = 3;
const ADD_BARS   = 3;

// ── ATR RENK KODLAMASI ────────────────────────────────────────────
// EMA21: |v|>2 kırmızı, |v|>1 turuncu, diğer normal
// MA50:  |v|>4 kırmızı, diğer normal
function atrEmaColor(v) {{
  if (v == null) return '';
  const av = Math.abs(v);
  if (av > 2) return 'color:#dc2626;font-weight:700';
  if (av > 1) return 'color:#f97316;font-weight:700';
  return '';
}}
function atrMaColor(v) {{
  if (v == null) return '';
  const av = Math.abs(v);
  if (av > 4) return 'color:#dc2626;font-weight:700';
  return '';
}}

// ── TAB SWITCH ───────────────────────────────────────────────────
let tabInits = {{}};
function switchTab(tab) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  const names = ['analysis','atr','table','charts'];
  document.querySelectorAll('.tab-btn')[names.indexOf(tab)].classList.add('active');
  document.getElementById('tab-'+tab).classList.add('active');
  if (!tabInits[tab]) {{ tabInits[tab]=true; if(tab==='table') renderTable(); if(tab==='charts') initCharts(); if(tab==='atr') showATRSub('xu100_ema21'); }}
}}

// ── TAB 1: ANALİZ ────────────────────────────────────────────────
function buildAnalysis() {{
  const A = ANALYTICS;
  const cards = [
    {{l:'Toplam İşlem', v:A.total.toLocaleString('tr'), s:A.viop_n+' VIOP dahil', c:''}},
    {{l:'Win Rate', v:A.win_rate+'%', s:`${{A.win_n}}W / ${{A.lose_n}}L`, c:A.win_rate>=50?'clr-green':''}},
    {{l:'Profit Factor', v:A.profit_factor, s:'Kazanç / Kayıp', c:A.profit_factor>=1.5?'clr-green':A.profit_factor<1?'clr-red':'clr-blue'}},
    {{l:'Net PnL', v:'₺'+A.total_pnl.toLocaleString('tr'), s:`Kaz: ₺${{A.total_profit.toLocaleString('tr')}}`, c:A.total_pnl>=0?'clr-green':'clr-red'}},
  ];
  document.getElementById('stats-cards').innerHTML = cards.map(c=>
    `<div class="stat-card"><div class="stat-label">${{c.l}}</div><div class="stat-value ${{c.c}}">${{c.v}}</div><div class="stat-sub">${{c.s}}</div></div>`
  ).join('');

  const bt = A.best_trade, wt = A.worst_trade;
  document.getElementById('wl-compare').innerHTML = `
    <div class="wl-col">
      <div class="wl-head w">✅ Kazanan (${{A.win_n}})</div>
      <div class="wl-item"><span class="wl-key">Ort. Tutuş</span><span>${{A.avg_win_days}} gün</span></div>
      <div class="wl-item"><span class="wl-key">Ort. Getiri</span><span style="color:var(--green);font-weight:600">+${{A.avg_win_pct}}%</span></div>
      <div class="wl-item"><span class="wl-key">Toplam Kaz.</span><span style="color:var(--green);font-weight:600">₺${{A.total_profit.toLocaleString('tr')}}</span></div>
      ${{bt?`<div class="wl-item"><span class="wl-key">En İyi</span><span>${{bt.ticker}} +${{bt.pnl_pct.toFixed(1)}}%</span></div>`:''}}
    </div>
    <div class="wl-col">
      <div class="wl-head l">❌ Kaybeden (${{A.lose_n}})</div>
      <div class="wl-item"><span class="wl-key">Ort. Tutuş</span><span>${{A.avg_lose_days}} gün</span></div>
      <div class="wl-item"><span class="wl-key">Ort. Getiri</span><span style="color:var(--red);font-weight:600">${{A.avg_lose_pct}}%</span></div>
      <div class="wl-item"><span class="wl-key">Toplam Kay.</span><span style="color:var(--red);font-weight:600">-₺${{A.total_loss.toLocaleString('tr')}}</span></div>
      ${{wt?`<div class="wl-item"><span class="wl-key">En Kötü</span><span>${{wt.ticker}} ${{wt.pnl_pct.toFixed(1)}}%</span></div>`:''}}
    </div>`;

  document.querySelector('#yearly-table tbody').innerHTML = A.yearly.map(y=>
    `<tr><td><strong>${{y.year}}</strong></td><td>${{y.n}}</td>
     <td>${{(y.wins/y.n*100).toFixed(1)}}%</td>
     <td class="${{y.pnl>=0?'pnl-pos':'pnl-neg'}}">${{y.pnl>=0?'+':''}}₺${{Math.round(y.pnl).toLocaleString('tr')}}</td></tr>`
  ).join('');

  const el = document.getElementById('monthly-chart');
  const ch = LightweightCharts.createChart(el,{{
    layout:{{background:{{color:'#fff'}},textColor:'#6b7280',fontSize:10}},
    grid:{{vertLines:{{color:'rgba(0,0,0,.03)'}},horzLines:{{color:'rgba(0,0,0,.03)'}}}},
    rightPriceScale:{{borderColor:'#e1e4e8',scaleMargins:{{top:.1,bottom:.1}}}},
    timeScale:{{borderColor:'#e1e4e8'}},
    crosshair:{{mode:1}},width:el.clientWidth,height:160,handleScroll:false,handleScale:false
  }});
  const hist = ch.addHistogramSeries({{priceLineVisible:false,lastValueVisible:false}});
  hist.setData(A.monthly.map(m=>({{time:m.month+'-01',value:m.pnl,color:m.pnl>=0?'#16a34a':'#dc2626'}})));
  ch.timeScale().fitContent();
}}

// ── TAB 2: ATR ───────────────────────────────────────────────────
function showATRSub(sub) {{
  document.querySelectorAll('.atr-tab').forEach((b,i) => {{
    const subs = ['xu100_ema21','xu100_ma50','stock_ema21','stock_ma50'];
    b.classList.toggle('active', subs[i]===sub);
  }});
  const data = ANALYTICS.atr_analysis[sub] || [];
  const maxWR  = Math.max(...data.map(d=>d.wr||0),1);
  const maxRet = Math.max(...data.map(d=>Math.abs(d.avg_ret||0)),.01);
  const maxWin = Math.max(...data.map(d=>Math.abs(d.avg_win||0)),.01);
  const maxN   = Math.max(...data.map(d=>d.n||0),1);

  const labels = {{xu100_ema21:'XU100 / 21 EMA',xu100_ma50:'XU100 / 50 MA',stock_ema21:'Hisse / 21 EMA',stock_ma50:'Hisse / 50 MA'}};
  document.getElementById('atr-main-title').textContent = 'Win Rate — ' + labels[sub];

  function bars(cid, valFn, colorFn, fmtFn, maxV) {{
    document.getElementById(cid).innerHTML = data.map(d => {{
      if (!d.n) return `<div class="atr-bar-row"><div class="atr-bar-label">${{d.name}}</div><div class="atr-bar-n">0</div></div>`;
      const v   = valFn(d), pct = Math.min(100, Math.abs(v)/maxV*100);
      return `<div class="atr-bar-row">
        <div class="atr-bar-label">${{d.name}}</div>
        <div class="atr-bar-track">
          <div class="atr-bar-fill" style="width:${{pct}}%;background:${{colorFn(v)}}"></div>
          <span class="atr-bar-val" style="color:${{colorFn(v)}}">${{fmtFn(v)}}</span>
        </div>
        <div class="atr-bar-n">${{d.n}} işlem</div>
      </div>`;
    }}).join('');
  }}

  bars('atr-winrate-bars', d=>d.wr,    v=>v>=50?'#16a34a':'#dc2626', v=>v.toFixed(1)+'%', maxWR);
  bars('atr-return-bars',  d=>d.avg_ret||0, v=>v>=0?'#16a34a':'#dc2626', v=>(v>=0?'+':'')+v.toFixed(2)+'%', maxRet);
  bars('atr-winret-bars',  d=>d.avg_win||0, v=>'#16a34a', v=>'+'+v.toFixed(2)+'%', maxWin);
  bars('atr-count-bars',   d=>d.n,     v=>'#6b7280', v=>v.toString(), maxN);

  document.querySelector('#atr-detail-table tbody').innerHTML = data.filter(d=>d.n).map(d=>
    `<tr><td><strong>${{d.name}}</strong></td><td>${{d.n}}</td>
     <td class="${{d.wr>=50?'pnl-pos':'pnl-neg'}}">${{d.wr}}%</td>
     <td class="${{(d.avg_ret||0)>=0?'pnl-pos':'pnl-neg'}}">${{(d.avg_ret||0)>=0?'+':''}}${{(d.avg_ret||0).toFixed(2)}}%</td>
     <td class="pnl-pos">+${{(d.avg_win||0).toFixed(2)}}%</td>
     <td class="pnl-neg">${{(d.avg_lose||0).toFixed(2)}}%</td>
     <td>${{d.pf??'—'}}</td></tr>`
  ).join('');
}}

// ── TAB 3: TABLO ─────────────────────────────────────────────────
let ttSort = 'id', ttDir = 1, ttPage = 0;
const TT_PAGE = 50;

function clearTblFilters() {{
  ['tt-ticker','tt-year','tt-result','tt-viop'].forEach(id => {{
    const el = document.getElementById(id);
    el.tagName==='INPUT' ? el.value='' : el.value='';
  }});
  renderTable();
}}

function sortTbl(col) {{
  if (ttSort===col) ttDir*=-1; else {{ttSort=col;ttDir=1;}}
  document.querySelectorAll('.trade-table th').forEach(th => {{
    th.classList.remove('sort-asc','sort-desc');
    if (th.getAttribute('onclick')==`sortTbl('${{col}}')`) th.classList.add(ttDir===1?'sort-asc':'sort-desc');
  }});
  ttPage=0; renderTable();
}}

function renderTable() {{
  const ticker = document.getElementById('tt-ticker').value.toUpperCase().trim();
  const year   = document.getElementById('tt-year').value;
  const result = document.getElementById('tt-result').value;
  const viop   = document.getElementById('tt-viop').value;

  let data = ALL_TRADES.filter(t => {{
    if (ticker && !t.ticker.includes(ticker)) return false;
    if (year   && (!t.entry_date||!t.entry_date.startsWith(year))) return false;
    if (result==='win'  && t.pnl<=0)   return false;
    if (result==='loss' && t.pnl>0)    return false;
    if (result==='open' && t.exit_date) return false;
    if (viop==='no'  && t.is_viop)  return false;
    if (viop==='yes' && !t.is_viop) return false;
    return true;
  }});

  data.sort((a,b) => {{
    let va=a[ttSort], vb=b[ttSort];
    if(va==null) va=ttDir===1?Infinity:-Infinity;
    if(vb==null) vb=ttDir===1?Infinity:-Infinity;
    if(typeof va==='string') return va.localeCompare(vb)*ttDir;
    return (va-vb)*ttDir;
  }});

  const tot = data.length, wins = data.filter(t=>t.pnl>0).length;
  const totPnl = data.reduce((s,t)=>s+t.pnl,0);
  document.getElementById('tt-count').textContent = tot+' işlem';
  document.getElementById('tt-summary').innerHTML = tot>0
    ? `<b>${{wins}}</b> kazanan <b>${{tot-wins}}</b> kaybeden &nbsp;·&nbsp; WR: <b>${{(wins/tot*100).toFixed(1)}}%</b> &nbsp;·&nbsp; PnL: <b class="${{totPnl>=0?'clr-green':'clr-red'}}">₺${{Math.round(totPnl).toLocaleString('tr')}}</b>` : '';

  const page = data.slice(ttPage*TT_PAGE,(ttPage+1)*TT_PAGE);
  document.getElementById('tt-body').innerHTML = page.map(t => {{
    const isOpen=!t.exit_date, isViop=t.is_viop, isWin=t.pnl>0;
    const atr = ATR_DISTS[t.id] || {{}};
    const fmt = v => v!=null ? v.toFixed(2) : '—';
    return `<tr>
      <td><strong>${{t.id}}</strong></td>
      <td><strong>${{t.ticker}}</strong></td>
      <td>${{t.entry_date||'—'}}</td>
      <td>${{t.exit_date||'—'}}</td>
      <td>${{t.days}}</td>
      <td>${{t.entry_price?t.entry_price.toFixed(2):'—'}}</td>
      <td class="${{t.pnl>=0?'pnl-pos':'pnl-neg'}}">${{(t.pnl_pct>=0?'+':'')+t.pnl_pct.toFixed(2)}}%</td>
      <td class="${{t.pnl>=0?'pnl-pos':'pnl-neg'}}">${{(t.pnl>=0?'+':'')+Math.round(t.pnl).toLocaleString('tr')}}</td>
      <td>${{isOpen?'<span class="badge bo">Açık</span>':isWin?'<span class="badge bw">Kaz.</span>':'<span class="badge bl">Kayb.</span>'}}</td>
      <td style="${{atrEmaColor(atr.st_atr_dist_ema21)}}">${{fmt(atr.st_atr_dist_ema21)}}</td>
      <td style="${{atrMaColor(atr.st_atr_dist_ma50)}}">${{fmt(atr.st_atr_dist_ma50)}}</td>
      <td style="${{atrEmaColor(atr.xu_atr_dist_ema21)}}">${{fmt(atr.xu_atr_dist_ema21)}}</td>
      <td style="${{atrMaColor(atr.xu_atr_dist_ma50)}}">${{fmt(atr.xu_atr_dist_ma50)}}</td>
    </tr>`;
  }}).join('');

  const totalPages = Math.ceil(tot/TT_PAGE);
  const pag = document.getElementById('tt-pag');
  pag.innerHTML = totalPages>1 ? `
    <button class="tbl-btn" ${{ttPage===0?'disabled':''}} onclick="ttGo(${{ttPage-1}})">← Önceki</button>
    <span class="page-info">Sayfa ${{ttPage+1}}/${{totalPages}}</span>
    <button class="tbl-btn" ${{ttPage>=totalPages-1?'disabled':''}} onclick="ttGo(${{ttPage+1}})">Sonraki →</button>` : '';
}}
function ttGo(p) {{ ttPage=p; renderTable(); window.scrollTo(0,0); }}

// ── TAB 4: GRAFİKLER ────────────────────────────────────────────
let filteredTrades=[...CHART_TRADES], chartPage=0;
const CHART_PAGE=100;

function applyFilters() {{
  const t=document.getElementById('f-ticker').value.toUpperCase();
  const y=document.getElementById('f-year').value;
  const r=document.getElementById('f-result').value;
  filteredTrades=CHART_TRADES.filter(tr=>{{
    if(t&&!tr.ticker.includes(t)) return false;
    if(y&&(!tr.entry_date||!tr.entry_date.startsWith(y))) return false;
    if(r==='win'&&(tr.pnl_pct||0)<=0) return false;
    if(r==='loss'&&(tr.pnl_pct||0)>0) return false;
    return true;
  }});
  chartPage=0; renderChartPage();
}}
function clearFilters(){{
  document.getElementById('f-ticker').value='';
  document.getElementById('f-year').value='';
  document.getElementById('f-result').value='';
  applyFilters();
}}
function initCharts(){{filteredTrades=[...CHART_TRADES];renderChartPage();}}

function renderChartPage(){{
  const grid=document.getElementById('charts-grid');
  const pag=document.getElementById('charts-pag');
  grid.innerHTML='';
  const tot=filteredTrades.length;
  const totalPages=Math.ceil(tot/CHART_PAGE);
  const page=filteredTrades.slice(chartPage*CHART_PAGE,(chartPage+1)*CHART_PAGE);
  document.getElementById('f-count').textContent=tot+' işlem';

  page.forEach(trade => {{
    const ohlcData=OHLC[trade.id];
    const isPos=(trade.pnl_pct||0)>0;
    const atr=ATR_DISTS[trade.id]||{{}};
    let atrStr='';
    if (atr.st_atr_dist_ema21!=null) {{
      const emaStyle = atrEmaColor(atr.st_atr_dist_ema21);
      const maStyle  = atrMaColor(atr.st_atr_dist_ma50);
      atrStr = `EMA21: <span style="${{emaStyle||'color:#94a3b8'}}">${{atr.st_atr_dist_ema21}}</span> | MA50: <span style="${{maStyle||'color:#94a3b8'}}">${{atr.st_atr_dist_ma50!=null?atr.st_atr_dist_ma50:'—'}}</span>`;
    }}
    const card=document.createElement('div');
    card.className='chart-card';
    card.innerHTML=`
      <div class="card-head">
        <div><span class="ticker">${{trade.ticker}}</span><span style="font-size:11px;color:var(--muted);margin-left:6px">#${{trade.id}}</span></div>
        <div class="card-meta">
          <span class="${{isPos?'pos':'neg'}}">${{isPos?'+':''}}${{(trade.pnl_pct||0).toFixed(2)}}%</span>
          <span style="margin-left:4px">₺${{Math.round(trade.pnl||0).toLocaleString('tr')}}</span><br>
          <span>${{trade.entry_date||''}} → ${{trade.exit_date||'açık'}}</span><br>
          ${{atrStr?`<span style="color:#94a3b8">${{atrStr}}</span>`:''}}
        </div>
      </div>
      ${{ohlcData ? `<div class="chart-area" id="ca-${{trade.id}}"></div>` : `<div class="chart-nodata">Grafik verisi yok (VIOP veya veri alınamadı)</div>`}}`;
    grid.appendChild(card);
    if(ohlcData) renderChart(trade, ohlcData);
  }});

  pag.innerHTML=totalPages>1?`
    <button class="ctrl-btn" ${{chartPage===0?'disabled':''}} onclick="cPage(${{chartPage-1}})">← Önceki</button>
    <span class="page-info">Sayfa ${{chartPage+1}}/${{totalPages}} (${{chartPage*CHART_PAGE+1}}–${{Math.min((chartPage+1)*CHART_PAGE,tot)}}/${{tot}})</span>
    <button class="ctrl-btn" ${{chartPage>=totalPages-1?'disabled':''}} onclick="cPage(${{chartPage+1}})">Sonraki →</button>`:'';
}}
function cPage(p){{chartPage=p;renderChartPage();window.scrollTo(0,0);}}

function buildHLineData(allBars,startDate,numBars,price,direction){{
  const idx=allBars.findIndex(b=>b.date===startDate);
  if(idx<0) return [];
  let from,to;
  if(direction==='centered'){{
    const half=Math.floor(numBars/2);
    from=Math.max(0,idx-half); to=Math.min(allBars.length-1,idx+(numBars-half-1));
  }}else{{from=idx; to=Math.min(allBars.length-1,idx+numBars-1);}}
  const out=[];
  for(let i=from;i<=to;i++) out.push({{time:allBars[i].date,value:price}});
  return out;
}}

function renderChart(trade, data) {{
  const el=document.getElementById('ca-'+trade.id);
  if(!el||!data||!data.length) return;
  const chart=LightweightCharts.createChart(el,{{
    layout:{{background:{{color:'#ffffff'}},textColor:'#6b7280',fontSize:10}},
    grid:{{vertLines:{{color:'rgba(0,0,0,0.04)'}},horzLines:{{color:'rgba(0,0,0,0.04)'}}}},
    rightPriceScale:{{borderColor:'#e1e4e8',scaleMargins:{{top:0.01,bottom:0.01}},autoScale:true}},
    timeScale:{{borderColor:'#e1e4e8',timeVisible:false,fixLeftEdge:true,fixRightEdge:false}},
    crosshair:{{mode:1}},width:el.clientWidth,height:440,handleScroll:false,handleScale:false
  }});

  // 10 MA (mor, silik)
  const ma10data = data.filter(d=>d.m10!=null).map(d=>({{time:d.date,value:d.m10}}));
  if (ma10data.length > 0) {{
    const ma10line = chart.addLineSeries({{color:'rgba(139,92,246,0.45)',lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false}});
    ma10line.setData(ma10data);
  }}

  // EMA Cloud
  const emaHigh=chart.addAreaSeries({{topColor:'rgba(150,150,150,0.20)',bottomColor:'rgba(150,150,150,0.20)',lineColor:'rgba(120,120,120,0.5)',lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false}});
  emaHigh.setData(data.map(d=>({{time:d.date,value:d.eh}})));
  const emaLow=chart.addAreaSeries({{topColor:'rgba(255,255,255,1)',bottomColor:'rgba(255,255,255,1)',lineColor:'rgba(120,120,120,0.5)',lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false}});
  emaLow.setData(data.map(d=>({{time:d.date,value:d.el}})));
  const emaMid=chart.addLineSeries({{color:'#ff9800',lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false}});
  emaMid.setData(data.map(d=>({{time:d.date,value:d.ec}})));

  // Candlesticks
  const cs=chart.addCandlestickSeries({{upColor:'#ffffff',downColor:'#1a1a1a',borderUpColor:'#1a1a1a',borderDownColor:'#1a1a1a',wickUpColor:'#1a1a1a',wickDownColor:'#1a1a1a',priceLineVisible:false,lastValueVisible:false}});
  cs.setData(data.map(d=>({{time:d.date,open:d.o,high:d.h,low:d.l,close:d.c}})));

  function hline(date,price,color,width,dir){{
    const d=buildHLineData(data,date,dir==='forward'?STOP_BARS:ENTRY_BARS,price,dir||'centered');
    if(!d.length) return;
    const s=chart.addLineSeries({{color,lineWidth:width,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false}});
    s.setData(d);
  }}

  if(trade.entry_date&&trade.entry_price) hline(trade.entry_date,trade.entry_price,'#2962ff',3);
  (trade.adds||[]).forEach(a=>{{if(a.date&&a.price) hline(a.date,a.price,'#16a34a',3);}});
  (trade.trims||[]).forEach(tr=>{{if(tr.date&&tr.price) hline(tr.date,tr.price,'#f97316',3);}});
  const fe=trade.final_exit;
  if(fe&&fe.date&&fe.price) hline(fe.date,fe.price,'#dc2626',3);

  chart.timeScale().fitContent();
}}

// ── INIT ─────────────────────────────────────────────────────────
try {{
  buildAnalysis();
}} catch(e) {{
  console.error('buildAnalysis error:', e);
  document.getElementById('stats-cards').innerHTML = '<div style="color:red;padding:10px">Hata: ' + e.message + '</div>';
}}
try {{
  showATRSub('xu100_ema21');
}} catch(e) {{
  console.error('showATRSub error:', e);
}}
tabInits['analysis']=true; tabInits['atr']=true;
</script>
</body>
</html>"""

if __name__ == '__main__':
    main()
