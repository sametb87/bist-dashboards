#!/usr/bin/env python3
"""
US Sector Dashboard — BIST Sector Dashboard'un ABD versiyonu
=============================================================
Klasör: ~/Desktop/us_sector_dashboard/

Çalıştırma:
  cd ~/Desktop/us_sector_dashboard
  python3 us_sector_dashboard.py

Çıktı:
  us_sector_dashboard.html  (tarayıcıda aç)
  us_sector_ohlc.json       (OHLC cache, incremental)

Kapsam:
  S&P 500 + NASDAQ 100 birleşik tekil set (~520 ticker)
  Sektör kırılımı: Yahoo Finance industry alanı (~100 alt-sektör)
  Endeksler: SPY · QQQ · IWM

Aynı UX (BIST dashboard'unun birebir kopyası):
  • 2 ana sekme: Sektör / Hisse
  • Periyot butonları: 1G / 1H / 1A / 3A / 6A / 12A
  • Finviz tarzı yatay barlar
  • RS5 / RS21 / RS-MS (MarketSmith) skorları
  • EMA 21 cloud + mini mum grafikler
  • Cloud içi / ±1 ATR / RS≥85 filtre butonları
  • TradingView watchlist export (NASDAQ:/NYSE: prefix'i)
  • Quarterly financials modal'ı (revenue + net income)

Kurulum (bir kere):
  pip3 install yfinance pandas numpy curl_cffi requests lxml
"""

import os, sys, json, time, random, resource
from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import numpy  as np
import pandas as pd
import yfinance as yf

# ── yfinance cache off (file descriptor sorunu önler) ──
try:
    from yfinance import cache as _yfc
    _yfc.set_cache_path(None)
except Exception:
    pass
os.environ["YFINANCE_NO_CACHE"] = "1"

# ── macOS FD limit ──
try:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 8192), hard))
except Exception:
    pass

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "us_sector_dashboard.html")
OHLC_CACHE  = os.path.join(BASE_DIR, "us_sector_ohlc.json")
META_CACHE  = os.path.join(BASE_DIR, "us_sector_meta.json")     # info: sector/industry/exchange

MAX_THREADS = 3
REQ_DELAY   = (0.3, 0.7)

SP500_URL   = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL     = "https://en.wikipedia.org/wiki/Nasdaq-100"
SP400_URL   = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
BENCHMARKS  = ["SPY", "QQQ", "IWM"]

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
_log_lock = Lock()
def log(msg):
    with _log_lock:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ─────────────────────────────────────────────
# YFINANCE — 401 fix (paylaşımlı session + jitter + backoff)
# ─────────────────────────────────────────────
_session = None
_session_lock = Lock()
_session_uses = 0
_SESSION_REFRESH_EVERY = 80

def _new_session():
    try:
        from curl_cffi import requests as curl_requests
        impersonate = random.choice(["chrome120","chrome119","chrome116","chrome110"])
        return curl_requests.Session(impersonate=impersonate)
    except Exception as e:
        log(f"  curl_cffi yok ({e}) — düz requests")
        return None

def get_session(force_new=False):
    global _session, _session_uses
    with _session_lock:
        if force_new or _session is None or _session_uses >= _SESSION_REFRESH_EVERY:
            if _session is not None:
                try: _session.close()
                except Exception: pass
            _session = _new_session()
            _session_uses = 0
        _session_uses += 1
        return _session

def _polite_sleep():
    time.sleep(random.uniform(*REQ_DELAY))

def _fetch_history(ticker, period=None, start=None, retries=4):
    """OHLC indir."""
    for attempt in range(retries):
        try:
            session = get_session(force_new=(attempt > 0))
            t_obj = yf.Ticker(ticker, session=session)
            if period:
                df = t_obj.history(period=period, interval="1d", auto_adjust=True)
            else:
                df = t_obj.history(start=start, interval="1d", auto_adjust=True)
            _polite_sleep()
            if df is None or df.empty:
                if attempt < retries - 1:
                    time.sleep(1 + attempt); continue
                return None
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_convert(None)
            return df.dropna(subset=["Close"])
        except Exception as e:
            err = str(e).lower()
            wait = (2 ** attempt) + random.uniform(0, 1)
            if "401" in err or "unauthorized" in err or "429" in err or "rate" in err:
                with _session_lock:
                    global _session_uses
                    _session_uses = _SESSION_REFRESH_EVERY
                if attempt < retries - 1:
                    time.sleep(wait * 1.5); continue
            if attempt < retries - 1:
                time.sleep(wait); continue
            return None
    return None

def _fetch_info(ticker, retries=3):
    """Sector/industry/exchange bilgisi."""
    for attempt in range(retries):
        try:
            session = get_session(force_new=(attempt > 0))
            t_obj = yf.Ticker(ticker, session=session)
            try:
                fi = t_obj.info or {}
            except Exception:
                fi = {}
            _polite_sleep()
            if fi:
                return {
                    "name":     fi.get("shortName") or fi.get("longName") or ticker,
                    "sector":   fi.get("sector", ""),
                    "industry": fi.get("industry", ""),
                    "exchange": fi.get("exchange", ""),
                    "mktcap":   fi.get("marketCap", 0) or 0,
                    "avg_vol":  fi.get("averageVolume") or fi.get("averageDailyVolume10Day") or 0,
                }
            if attempt < retries - 1:
                time.sleep(1 + attempt)
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None

def _fetch_quarterly_financials(ticker, retries=2):
    """Çeyreklik gelir + net kâr."""
    for attempt in range(retries):
        try:
            session = get_session(force_new=(attempt > 0))
            t_obj = yf.Ticker(ticker, session=session)
            qf = None
            try:
                qf = t_obj.quarterly_financials
            except Exception:
                qf = None
            _polite_sleep()
            if qf is None or qf.empty:
                if attempt < retries - 1:
                    time.sleep(1); continue
                return []
            recs = []
            # qf: rows = items, cols = dates (descending)
            # Yahoo'da satır isimleri: "Total Revenue", "Net Income"
            rev_row = None
            ni_row  = None
            for idx in qf.index:
                if "Total Revenue" in str(idx) or str(idx).strip() == "Total Revenue":
                    rev_row = idx
                if "Net Income" in str(idx) and "Continuous" not in str(idx) and "Common" not in str(idx):
                    if ni_row is None:
                        ni_row = idx
            # Sütunlar tarih (sondan eski → başta yeni)
            for col in qf.columns:
                d = pd.Timestamp(col).strftime("%Y-%m-%d")
                rev = qf.loc[rev_row, col] if rev_row is not None else None
                ni  = qf.loc[ni_row,  col] if ni_row  is not None else None
                if pd.notna(rev) or pd.notna(ni):
                    recs.append({
                        "date":       d,
                        "revenue":    None if pd.isna(rev) else float(rev),
                        "net_income": None if pd.isna(ni)  else float(ni),
                    })
            # Eskiden yeniye sırala
            recs.sort(key=lambda x: x["date"])
            return recs
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return []

# ─────────────────────────────────────────────
# UNIVERSE — S&P 500 + Nasdaq 100
# ─────────────────────────────────────────────
# Wikipedia bazen Python-urllib User-Agent'ını 403'lüyor.
# Çözüm: önce User-Agent header'lı requests.get ile HTML'i al, sonra read_html.
# Çift güvence olarak hard-coded fallback liste de mevcut (Mayıs 2026).

import io as _io

def _http_get(url, timeout=15):
    """Wikipedia / başka site → tarayıcı gibi davran."""
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    # Önce curl_cffi dene (zaten paketi var), sonra urllib
    try:
        from curl_cffi import requests as curl_requests
        s = curl_requests.Session(impersonate="chrome120")
        r = s.get(url, headers=headers, timeout=timeout)
        s.close()
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    # Fallback: urllib + User-Agent
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

# Hard-coded fallback (Mayıs 2026 itibarıyla)
# Wikipedia tamamen erişilemezse devreye girer.
# Üç set birleşik: S&P 500 + Nasdaq 100 + S&P 400 (mid cap)
_FALLBACK_SP500_NDX100 = """
A AAPL ABBV ABNB ABT ACGL ACN ADBE ADI ADM ADP ADSK AEE AEP AES AFL AIG AIZ AJG
AKAM ALB ALGN ALL ALLE AMAT AMCR AMD AME AMGN AMP AMT AMZN ANET ANSS AON AOS APA
APD APH APO APTV ARE ATO AVB AVGO AVY AWK AXON AXP AZO BA BAC BALL BAX BBWI BBY
BDX BEN BF-B BG BIIB BIO BK BKNG BKR BLDR BLK BMY BR BRK-B BRO BSX BWA BX BXP C
CAG CAH CARR CAT CB CBOE CBRE CCI CCL CDNS CDW CE CEG CF CFG CHD CHRW CHTR CI
CINF CL CLX CMCSA CME CMG CMI CMS CNC CNP COF COIN COO COP COR COST CPAY CPB CPRT
CPT CRL CRM CRWD CSCO CSGP CSX CTAS CTLT CTRA CTSH CTVA CVS CVX CZR D DAL DAY DD
DE DECK DELL DFS DG DGX DHI DHR DIS DLR DLTR DOC DOV DOW DPZ DRI DTE DUK DVA DVN
DXCM EA EBAY ECL ED EFX EG EIX EL ELV EMN EMR ENPH EOG EPAM EQIX EQR EQT ERIE ES
ESS ETN ETR ETSY EVRG EW EXC EXPD EXPE EXR F FANG FAST FCX FDS FDX FE FFIV FI FICO
FIS FITB FMC FOX FOXA FRT FSLR FTNT FTV GD GDDY GE GEHC GEN GEV GILD GIS GL GLW
GM GNRC GOOG GOOGL GPC GPN GRMN GS GWW HAL HAS HBAN HCA HD HES HIG HII HLT HOLX
HON HPE HPQ HRL HSIC HST HSY HUBB HUM HWM IBM ICE IDXX IEX IFF INCY INTC INTU INVH
IP IPG IQV IR IRM ISRG IT ITW IVZ J JBHT JBL JCI JKHY JNJ JNPR JPM K KDP KEY KEYS
KHC KIM KKR KLAC KMB KMI KMX KO KR KVUE L LDOS LEN LH LHX LIN LKQ LLY LMT LNT LOW
LRCX LULU LUV LVS LW LYB LYV MA MAA MAR MAS MCD MCHP MCK MCO MDLZ MDT MET META
MGM MHK MKC MKTX MLM MMC MMM MNST MO MOH MOS MPC MPWR MRK MRNA MRO MS MSCI MSFT
MSI MTB MTCH MTD MU NCLH NDAQ NDSN NEE NEM NFLX NI NKE NOC NOW NRG NSC NTAP NTRS
NUE NVDA NVR NWS NWSA NXPI O ODFL OKE OMC ON ORCL ORLY OTIS OXY PANW PARA PAYC
PAYX PCAR PCG PEG PEP PFE PFG PG PGR PH PHM PKG PLD PLTR PM PNC PNR PNW PODD POOL
PPG PPL PRU PSA PSX PTC PWR PYPL QCOM QRVO RCL REG REGN RF RJF RL RMD ROK ROL ROP
ROST RSG RTX RVTY SBAC SBUX SCHW SHW SJM SLB SMCI SNA SNPS SO SOLV SPG SPGI SRE
STE STLD STT STX STZ SWK SWKS SYF SYK SYY T TAP TDG TDY TECH TEL TER TFC TFX TGT
TJX TMO TMUS TPR TRGP TRMB TROW TRV TSCO TSLA TSN TT TTWO TXN TXT TYL UAL UBER UDR
UHS ULTA UNH UNP UPS URI USB V VICI VLO VMC VRSK VRSN VRTX VST VTR VTRS VZ WAB
WAT WBA WBD WDAY WDC WEC WELL WFC WM WMB WMT WRB WST WTW WY WYNN XEL XOM XYL YUM
ZBH ZBRA ZTS
ADP ADBE AAPL AMAT AMD AMZN AMGN ANSS APP ARM ASML AVGO AZN AXON BIIB BKNG BKR CCEP
CDNS CDW CEG CHTR CMCSA COST CPRT CRWD CSCO CSGP CSX CTAS CTSH DASH DDOG DXCM EA
EXC FANG FAST FTNT GEHC GFS GILD GOOG GOOGL HON IDXX INTC INTU ISRG KDP KHC KLAC
LIN LRCX LULU MAR MCHP MDB MDLZ MELI META MNST MRVL MRNA MSFT MU NFLX NVDA NXPI
ODFL ON ORLY PANW PAYX PCAR PDD PEP PYPL QCOM REGN ROP ROST SBUX SHOP SMCI SNPS
TEAM TMUS TSLA TTD TTWO TXN VRSK VRTX WBD WDAY XEL ZS
"""

_FALLBACK_SP400 = """
ACIW ACM ADC AGCO ALK ALKS ALV AM AMG AMH AN APLE APOG ARMK ARW ASB ASGN ATGE ATR
AVNT AVT AWI AYI AZTA BC BCO BCPC BDC BERY BFAM BFH BG BHF BIO BKH BLD BLKB BMI BRBR
BRX BWA BWXT BYD BYDD CABO CACI CADE CAR CASY CBSH CC CCL CDP CFR CGNX CHE CHRD CHX
CIEN CIVI CLF CLH CMA CMC CNH CNM CNO CNX COHR COKE COLB COLM CONA CR CRI CRS CRUS
CSL CTLP CUBE CUZ CVCO CW CWAN CWEN CWST CXT DAN DAR DBX DCI DECK DINO DKS DLB DNB
DOCS DRVN DT DTM DV DVN DXC EAT EEFT EHC ELAN ELS ELY EME EME ENS ENSG ENV EPAM EPC
EPR EQH ETD ETSY EVR EWBC EXAS EXEL FAF FBP FCN FFIN FHB FHN FIBK FIVN FIX FL FLEX
FLG FLR FLS FN FNB FNF FNR FOUR FR FRPT FSS G GBCI GEF GGG GHC GME GMED GNL GNTX
GO GPI GPK GTES GTLS GTY GVA GXO H HALO HAS HCM HE HELE HHC HII HII HIW HLI HOG HOMB
HPP HRB HSII HUBG HUN HXL IART ICUI IDA IDCC IDT IGT IIPR INFA INGR INSM IOSP IRT
ITGR ITT ITY JACK JAZZ JBL JBT JEF JLL JWN KAR KBH KBR KMT KMX KN KNF KNX KRC KRG KRYS
LAUR LAZ LBRDA LBRDK LBTYA LBTYK LECO LEG LFUS LIVN LNW LNTH LOPE LSI LSTR LSXMA LSXMK
LSXMB MAN MAS MASI MATX MBC MC MDP MDU MGEE MGY MIDD MKSI MLI MMS MOG-A MORN MOS MP
MSA MSM MTH MTSI MTX MTZ MUR MUSA NARI NATL NEOG NEU NJR NNN NOG NOV NPO NSA NSP NTNX
NVST NWE NXST NYT OC OGE OGN OHI OLED OLN OLLI ONB ONTO OSK OZK PAG PARA PB PBF PBH PCH
PCTY PEN PENN PFGC PGR PII PINC PLNT PLXS PNFP PNM POR POST POWI PPC PR PRG PRGO PRI
PRIM PRMW PSN PSTG PTC PVH R RBA RBC RCM RDN REXR REZI RGA RH RHI RIG RIVN RL RNG RNST
RPM RRC RRX RUSHA RYAN RYN S SAIA SAIC SAM SANM SBCF SBNY SBRA SCI SEE SEIC SF SFM
SHC SHO SIG SITC SITM SJW SLAB SLG SLGN SLM SLVM SM SMG SMR SNDR SNDX SNX SNV SON SPB
SPH SPSC SR SRCL SRPT SSB SSD SSNC ST STAG STR STWD SUM SWI SXT SYNA TBLA TCBI TDOC TDS
TDW TENB TEX TFII TFIN TGNA THC THG THO THRY TKR TKO TMHC TNDM TNET TPH TPR TPX TRMB
TRN TROX TRU TRYG TTC TWLO TWST TYL UAA UAL UCBI UDR UFPI UGI UHS UI UMBF UNF UNFI USFD
UTL UTHR UTL VAC VC VCEL VCR VFC VGR VIRT VLY VMI VNDA VNO VNT VRSN VSAT VST VVV WAFD
WBS WCC WDC WDFC WEN WERN WEX WGO WH WHD WK WLK WMS WNS WOLF WOR WSO WTS WTTR WTW WU
WYNN X XPEL XPO YELP ZD ZWS
"""

# YENİ TREND HİSSELERİ — endekslerde olmayan ama yüksek hacimli/likit hisseler
# Bu liste yfinance üzerinden tarandığında zaten doğrulanır (info varsa, OHLC varsa kalır)
_TRENDS_LIST = """
AI APLD ARM CRWV NBIS WULF CORZ IREN HUT RIOT MARA CIFR HIVE CLSK BTBT BITF
SOUN BBAI RGTI IONQ QBTS
NNE SMR OKLO LEU NXE UEC DNN UUUU UROY
RKLB ASTS LUNR BKSY PL SATL SPIR RDW ACHR JOBY KTOS AVAV
AFRM HOOD SOFI UPST DAVE TOST FOUR FLYW NU XYZ HIMS RDDT DUOL CART ELF BIRK CAVA SG
BE FCEL PLUG BLDP BLNK ENVX WBX STEM FLNC
LITE COHR CIEN VIAV ALAB POET INFN OUST ADTN
VICR MPWR ONTO ACLS MKSI CEVA AMBA NVMI KLIC FORM COHU SITM CRDO
PLTR SNOW DOCN ESTC CFLT S CYBR BRZE GTLB DDOG MDB SMCI
VKTX ALNY CRSP NTLA BEAM RXRX RCKT
GME AMC KOSS BB
KSCP SYM SERV PATH
TTD APP DKNG FLUT
HUBS MNDY BILL PAYC PCOR ZS NET
NEM AEM WPM PAAS AG FSLR ENPH RUN ARRY SHLS
NOC HEI TDG CACI LDOS PSN
LULU ONON SKX ANF AEO
CHWY CVNA DASH ABNB UBER
TSLA RIVN LEA BWA APTV
META PINS RDDT
NVDA AMD AVGO MRVL ARM
SHOP MELI ETSY PINS
CCJ DNN
INSW STNG FRO DHT NAT
GLNG LNG
ALB SQM SGML LAR
FCX SCCO TECK HBM
ISRG ROK FANUY
WNS INFY
"""

def _parse_fallback_list():
    """Tüm fallback kaynaklarını birleştirip tekil sete dönüştür."""
    all_text = _FALLBACK_SP500_NDX100 + " " + _FALLBACK_SP400 + " " + _TRENDS_LIST
    return sorted(set(t.strip() for t in all_text.split() if t.strip()))

def _parse_trends_list():
    return sorted(set(t.strip() for t in _TRENDS_LIST.split() if t.strip()))

def get_universe():
    log("Universe çekiliyor: S&P 500 + Nasdaq 100 + S&P 400 + Trends...")
    universe = set()

    # 1) S&P 500
    try:
        html_text = _http_get(SP500_URL)
        if html_text:
            sp = pd.read_html(_io.StringIO(html_text))[0]
            sp_tickers = sp["Symbol"].str.replace(".", "-", regex=False).tolist()
            universe.update(sp_tickers)
            log(f"  S&P 500   : {len(sp_tickers)} ticker")
        else:
            raise RuntimeError("HTTP 200 alınamadı")
    except Exception as e:
        log(f"  S&P 500 hata: {e}")

    # 2) Nasdaq 100
    try:
        html_text = _http_get(NDX_URL)
        if html_text:
            tables = pd.read_html(_io.StringIO(html_text))
            ndx_tickers = []
            for t in tables:
                cols = [str(c) for c in t.columns]
                tcol = next((c for c in cols if c.lower() in ("ticker", "symbol")), None)
                if tcol and 50 < len(t) < 150:
                    ndx_tickers = t[tcol].astype(str).str.replace(".", "-", regex=False).tolist()
                    break
            universe.update(ndx_tickers)
            log(f"  Nasdaq 100: {len(ndx_tickers)} ticker")
        else:
            raise RuntimeError("HTTP 200 alınamadı")
    except Exception as e:
        log(f"  Nasdaq 100 hata: {e}")

    # 3) S&P 400 (Mid Cap)
    try:
        html_text = _http_get(SP400_URL)
        if html_text:
            tables = pd.read_html(_io.StringIO(html_text))
            sp400_tickers = []
            for t in tables:
                cols = [str(c) for c in t.columns]
                tcol = next((c for c in cols if c.lower() in ("ticker", "symbol")), None)
                if tcol and 300 < len(t) < 450:
                    sp400_tickers = t[tcol].astype(str).str.replace(".", "-", regex=False).tolist()
                    break
            universe.update(sp400_tickers)
            log(f"  S&P 400   : {len(sp400_tickers)} ticker (mid cap)")
        else:
            raise RuntimeError("HTTP 200 alınamadı")
    except Exception as e:
        log(f"  S&P 400 hata: {e}")

    # 4) Wikipedia başarısız olduysa fallback liste
    if len(universe) < 300:
        log(f"  Wikipedia'dan {len(universe)} ticker geldi — fallback listeye geçiliyor")
        fallback = _parse_fallback_list()
        universe.update(fallback)
        log(f"  Fallback liste: {len(fallback)} ticker eklendi")

    # 5) Her zaman trend hisselerini ekle (endekslerde olmayan ama önemli)
    trends = _parse_trends_list()
    new_from_trends = [t for t in trends if t not in universe]
    universe.update(trends)
    if new_from_trends:
        log(f"  Trends ekstra: {len(new_from_trends)} ticker (AFRM, BE, HIMS, RKLB, WULF, APLD, COHR vb.)")

    # 6) Benchmark'ları ekle
    universe.update(BENCHMARKS)

    universe = sorted(universe)
    log(f"  ───────────────────────────────")
    log(f"  TOPLAM    : {len(universe)} ticker")
    return universe

# ─────────────────────────────────────────────
# OHLC CACHE
# ─────────────────────────────────────────────
def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = f.read().strip()
                return json.loads(txt) if txt else {}
        except Exception as e:
            log(f"  Cache okuma hatası ({path}): {e}")
    return {}

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(',', ':'))
    os.replace(tmp, path)

def df_to_ohlc_recs(df):
    recs = []
    for ts, row in df.iterrows():
        try:
            o = float(row.get("Open",  np.nan))
            h = float(row.get("High",  np.nan))
            l = float(row.get("Low",   np.nan))
            c = float(row.get("Close", np.nan))
            if any(np.isnan(x) for x in [o, h, l, c]):
                continue
            recs.append({
                "d": ts.strftime("%Y-%m-%d"),
                "o": round(o, 4), "h": round(h, 4),
                "l": round(l, 4), "c": round(c, 4),
            })
        except Exception:
            pass
    return recs

def update_ohlc_cache(tickers, cache):
    today = date.today().strftime("%Y-%m-%d")
    need_full = [t for t in tickers if t not in cache]
    need_incr = [t for t in tickers if t in cache and cache[t] and cache[t][-1]["d"] < today]

    log(f"  Full OHLC      : {len(need_full)} ticker")
    log(f"  Incremental    : {len(need_incr)} ticker")
    log(f"  Zaten güncel   : {len(tickers) - len(need_full) - len(need_incr)} ticker")

    def fetch_full(t):
        df = _fetch_history(t, period="14mo")
        return t, (df_to_ohlc_recs(df) if df is not None else [])

    if need_full:
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
            futures = {ex.submit(fetch_full, t): t for t in need_full}
            done = 0
            for f in as_completed(futures):
                t, recs = f.result()
                done += 1
                if recs:
                    cache[t] = recs
                if done % 25 == 0:
                    log(f"    Full: {done}/{len(need_full)} (cached: {len([x for x in cache if cache[x]])})")
                    save_json(OHLC_CACHE, cache)
        save_json(OHLC_CACHE, cache)

    def fetch_incr(t):
        last = cache[t][-1]["d"]
        start_dt = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        df = _fetch_history(t, start=start_dt)
        return t, (df_to_ohlc_recs(df) if df is not None else [])

    if need_incr:
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
            futures = {ex.submit(fetch_incr, t): t for t in need_incr}
            done = 0
            for f in as_completed(futures):
                t, new_recs = f.result()
                done += 1
                if new_recs:
                    exist_d = {r["d"] for r in cache[t]}
                    added = [r for r in new_recs if r["d"] not in exist_d]
                    if added:
                        cache[t].extend(added)
                if done % 30 == 0:
                    log(f"    Incremental: {done}/{len(need_incr)}")
                    save_json(OHLC_CACHE, cache)
        save_json(OHLC_CACHE, cache)

    return cache

def update_meta_cache(tickers, meta_cache, refresh_days=7):
    """
    Sector/industry/exchange bilgisini cache'le.
    Eski cache > refresh_days günse yeniden çek.
    """
    today = datetime.now()
    need = []
    for t in tickers:
        entry = meta_cache.get(t)
        if not entry:
            need.append(t)
        else:
            ts = entry.get("_ts", "1970-01-01")
            try:
                age = (today - datetime.strptime(ts[:10], "%Y-%m-%d")).days
                if age > refresh_days or not entry.get("industry"):
                    need.append(t)
            except Exception:
                need.append(t)

    log(f"  Meta info      : {len(need)} ticker (refresh > {refresh_days} gün)")
    if not need:
        return meta_cache

    def fetch_meta(t):
        info = _fetch_info(t)
        return t, info

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
        futures = {ex.submit(fetch_meta, t): t for t in need}
        done = 0
        for f in as_completed(futures):
            t, info = f.result()
            done += 1
            if info:
                info["_ts"] = today.strftime("%Y-%m-%d")
                meta_cache[t] = info
            if done % 25 == 0:
                log(f"    Meta: {done}/{len(need)}")
                save_json(META_CACHE, meta_cache)
    save_json(META_CACHE, meta_cache)
    return meta_cache

# ─────────────────────────────────────────────
# PERFORMANCE & RS HESAPLAMA
# ─────────────────────────────────────────────
PERIOD_DAYS = {"1d":1, "1w":5, "1m":21, "3m":63, "6m":126, "9m":189, "12m":252}

def calc_perf(ohlc):
    """Bir ticker için tüm periyot getirilerini hesapla."""
    if not ohlc or len(ohlc) < 2:
        return None
    closes = [r["c"] for r in ohlc]
    last = closes[-1]
    out = {"last": round(last, 2)}
    for pk, n in PERIOD_DAYS.items():
        if len(closes) > n:
            past = closes[-1 - n]
            out[pk] = round((last / past - 1) * 100, 2) if past else None
        else:
            out[pk] = None
    return out

def percentile_rank(scores_dict):
    """{ticker: score} → {ticker: 1-99 percentile}"""
    items = [(t, s) for t, s in scores_dict.items() if s is not None and not (isinstance(s, float) and np.isnan(s))]
    if not items:
        return {}
    items.sort(key=lambda x: x[1])
    n = len(items)
    return {t: round(1 + i / max(n-1, 1) * 98) for i, (t, _) in enumerate(items)}

def _bench_return(bench_closes, n):
    """Benchmark'ın n-gün önceki kapanışına göre dönüşü."""
    if bench_closes is None or len(bench_closes) <= n: return 0.0
    if bench_closes[-1-n] <= 0: return 0.0
    return bench_closes[-1] / bench_closes[-1-n] - 1

def calc_rs5(ohlc_map, bench_closes=None):
    """
    RS5: 5-gün ham getiri bazlı percentile (1-99). Kısa vadeli momentum.
    Benchmark-relative DEĞİL — bütün hisseleri ham 5-gün getirisine göre sıralar.
    Yeni arz hisseler de hesaba katılır (sadece 6+ günlük veri gerekir).
    """
    raw = {}
    for t, ohlc in ohlc_map.items():
        if not ohlc or len(ohlc) < 6: continue
        c = [r["c"] for r in ohlc]
        if c[-6] > 0:
            raw[t] = (c[-1] / c[-6] - 1)
    return percentile_rank(raw)

def calc_rs21(ohlc_map, bench_closes=None):
    """
    RS21: 21-gün ham getiri bazlı percentile (1-99). Orta vadeli momentum.
    Benchmark-relative DEĞİL — saf 21-gün getirisi sıralaması.
    Yeni arz hisseler de katılır (22+ günlük veri yeterli).
    """
    raw = {}
    for t, ohlc in ohlc_map.items():
        if not ohlc or len(ohlc) < 22: continue
        c = [r["c"] for r in ohlc]
        if c[-22] > 0:
            raw[t] = (c[-1] / c[-22] - 1)
    return percentile_rank(raw)

def calc_rsms(ohlc_map, bench_closes):
    """
    RS-MS (MarketSmith / IBD tarzı): hisse-vs-SPY excess return ağırlıklı:
      3A×40% + 6A×20% + 9A×20% + 12A×20%
    Bu uzun vadeli "true leader" sinyalidir.

    Minimum veri şartı: 6 ay (127+ gün).
    3-6 ay arası veri olan ticker'lar (yeni arzlar) RS-MS'e dahil edilmez
    → UI'da dict'te olmadığı için `-` / `null` olarak görünür.
    Bu doğru davranış: 3 aylık IPO bir leader olarak değerlendirilemez.

    6+ ay verisi olan ama 12 aylık verisi olmayan hisseler için ağırlıklar
    mevcut bileşenler üzerinde renormalize edilir.
    """
    b3  = _bench_return(bench_closes, 63)
    b6  = _bench_return(bench_closes, 126)
    b9  = _bench_return(bench_closes, 189)
    b12 = _bench_return(bench_closes, 252)
    raw = {}
    for t, ohlc in ohlc_map.items():
        # Yeni arz koruması: en az 6 aylık veri şart
        if not ohlc or len(ohlc) < 127: continue
        c = [r["c"] for r in ohlc]
        try:
            comps = []        # (excess_return, ağırlık)
            # 3m (her zaman var, çünkü 6+ ay var)
            if c[-64] > 0:
                comps.append((c[-1]/c[-64] - 1 - b3, 0.4))
            # 6m (her zaman var)
            if c[-127] > 0:
                comps.append((c[-1]/c[-127] - 1 - b6, 0.2))
            # 9m (varsa)
            if len(c) > 189 and c[-190] > 0:
                comps.append((c[-1]/c[-190] - 1 - b9, 0.2))
            # 12m (varsa)
            if len(c) > 252 and c[-253] > 0:
                comps.append((c[-1]/c[-253] - 1 - b12, 0.2))
            if len(comps) >= 2:  # en az 3m + 6m
                # Mevcut bileşenlerin ağırlıklarını renormalize et
                total_w = sum(w for _, w in comps)
                raw[t] = sum(r*w for r, w in comps) / total_w
        except Exception:
            pass
    return percentile_rank(raw)

# ─────────────────────────────────────────────
# SEKTÖR AGGREGATE
# ─────────────────────────────────────────────
def build_sectors(meta_cache, ohlc_cache, perf_map):
    """
    industry alanını sektör olarak kullan (Yahoo'nun ~150 alt-sektörü).
    Boş industry olanlar "Other" altına gider.
    """
    sectors  = {}        # sector → [tickers]
    sec_map  = {}        # ticker → sector
    for t, meta in meta_cache.items():
        if t not in perf_map: continue
        industry = meta.get("industry", "").strip()
        if not industry:
            industry = meta.get("sector", "").strip() or "Other"
        sectors.setdefault(industry, []).append(t)
        sec_map[t] = industry

    # En az 2 ticker'lı sektörleri tut (1'lik gürültü)
    sectors = {k: v for k, v in sectors.items() if len(v) >= 2}

    # Sektör perf'i: hisselerin ortalaması (medyan da seçenek ama BIST'te mean kullanılmış)
    sec_perf = {}
    for sec, tks in sectors.items():
        valid = [perf_map[t] for t in tks if t in perf_map and perf_map[t]]
        if not valid:
            continue
        agg = {"count": len(valid)}
        for pk in ["1d","1w","1m","3m","6m","9m","12m"]:
            vals = [p[pk] for p in valid if p.get(pk) is not None]
            agg[pk] = round(float(np.mean(vals)), 2) if vals else None
        sec_perf[sec] = agg

    return sectors, sec_map, sec_perf

def calc_sector_rs(sec_perf, sec_tickers, stock_rs, mode):
    """Sektör için RS = ticker'ların RS'lerinin medyanı."""
    out = {}
    for sec, tks in sec_tickers.items():
        rs_vals = [stock_rs.get(t) for t in tks if t in stock_rs and stock_rs.get(t) is not None]
        if rs_vals:
            out[sec] = round(float(np.median(rs_vals)))
        else:
            out[sec] = None
    return out

# ─────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"><meta http-equiv="Pragma" content="no-cache"><meta http-equiv="Expires" content="0">
<title>US Sector Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}body{background:#0a0a14;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
.hd{padding:18px 28px;border-bottom:1px solid rgba(255,255,255,.06);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
.hd h1{font-size:21px;font-weight:700}.hd h1 span{color:#6366f1}.meta{color:rgba(255,255,255,.4);font-size:11px;font-family:monospace}
.main-tabs{padding:14px 28px 0;display:flex;gap:0;border-bottom:1px solid rgba(255,255,255,.08)}
.main-tab{background:0;border:none;border-bottom:2px solid transparent;color:rgba(255,255,255,.4);padding:10px 22px;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s;letter-spacing:.3px}
.main-tab.a{color:#a5b4fc;border-bottom-color:#6366f1}.main-tab:hover{color:rgba(255,255,255,.7)}
.main-panel{display:none}.main-panel.a{display:block}
.tabs{padding:14px 28px;display:flex;gap:7px;flex-wrap:wrap}
.tab{background:0;border:1px solid rgba(255,255,255,.08);color:rgba(255,255,255,.5);padding:7px 15px;border-radius:7px;font-size:12px;cursor:pointer;transition:all .2s}
.tab.a{background:rgba(99,102,241,.2);border-color:rgba(99,102,241,.4);color:#a5b4fc;font-weight:600}.tab:hover{border-color:rgba(255,255,255,.2)}
.ct{padding:0 28px 36px}.tc{display:none}.tc.a{display:block}
.ctr{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
.btn{background:0;border:1px solid rgba(255,255,255,.08);color:rgba(255,255,255,.5);padding:7px 15px;border-radius:7px;font-size:12px;cursor:pointer;transition:all .2s}
.btn.a{background:rgba(99,102,241,.2);border-color:rgba(99,102,241,.4);color:#a5b4fc;font-weight:600}.btn:hover{border-color:rgba(255,255,255,.2)}
.cl{color:rgba(255,255,255,.4);font-size:10px;font-family:monospace;margin-right:3px;text-transform:uppercase;letter-spacing:1px}
.fv{margin-bottom:20px}.fv-row{display:flex;align-items:center;margin-bottom:3px;cursor:pointer;border-radius:4px;padding:3px 6px;transition:background .15s}
.fv-row:hover{background:rgba(255,255,255,.04)}
.fv-name{width:240px;min-width:240px;font-size:11px;font-weight:600;padding-right:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fv-bar-wrap{flex:1;height:22px;position:relative;background:rgba(255,255,255,.03);border-radius:3px;overflow:hidden}
.fv-bar{height:100%;position:absolute;top:0;border-radius:3px;transition:width .3s}
.fv-val{width:70px;min-width:70px;text-align:right;font-size:11px;font-weight:700;font-family:monospace;padding-left:8px}
.fv-rs{width:120px;min-width:120px;display:flex;gap:3px;justify-content:center;font-size:9px;font-weight:700;font-family:monospace;padding-left:6px}
.fv-rs .rb{font-size:9px;padding:1px 4px;min-width:24px}
.fv-cnt{width:35px;min-width:35px;text-align:center;font-size:9px;color:rgba(255,255,255,.3);font-family:monospace}
.tw{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06);border-radius:12px;overflow:hidden;margin-bottom:18px}
.tw .th{padding:13px 16px;border-bottom:1px solid rgba(255,255,255,.06);font-weight:600;font-size:13px;display:flex;justify-content:space-between;align-items:center}
.tw .th .back{cursor:pointer;color:#6366f1;font-size:11px;display:none}
table{width:100%;border-collapse:collapse;font-size:11px;font-family:monospace}
th{padding:9px 10px;text-align:right;color:rgba(255,255,255,.4);font-size:9px;text-transform:uppercase;letter-spacing:.7px;border-bottom:1px solid rgba(255,255,255,.06);cursor:pointer;white-space:nowrap;user-select:none}
th:first-child{text-align:left}th:hover{color:rgba(255,255,255,.7)}
th .sa{font-size:8px;margin-left:2px;color:rgba(255,255,255,.25)}th.st .sa{color:#a5b4fc}
td{padding:9px 10px;border-bottom:1px solid rgba(255,255,255,.03);text-align:right}
td:first-child{text-align:left;font-weight:600}
tr.sr{cursor:pointer}tr.sr:hover{background:rgba(99,102,241,.06)}
.sp{color:#4ade80}.sn{color:#f87171}
.rb{display:inline-block;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700;min-width:28px;text-align:center}
.idx-bar{display:flex;gap:12px;margin-bottom:18px;flex-wrap:wrap}
.idx-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:9px;padding:12px 16px;flex:1;min-width:140px}
.idx-name{font-size:10px;color:rgba(255,255,255,.4);font-family:monospace;text-transform:uppercase;margin-bottom:4px}
.idx-val{font-size:20px;font-weight:700}.idx-chg{font-size:11px;font-family:monospace;margin-top:2px}
.ft{padding:12px 28px;border-top:1px solid rgba(255,255,255,.06);color:rgba(255,255,255,.2);font-size:10px;font-family:monospace;display:flex;justify-content:space-between}
.modal-bg{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);z-index:100;justify-content:center;align-items:center}
.modal-bg.show{display:flex}
.modal{background:#12121e;border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:24px;width:90%;max-width:700px;max-height:90vh;overflow-y:auto;position:relative}
.modal h2{font-size:16px;font-weight:700;margin-bottom:4px}.modal .msub{font-size:11px;color:rgba(255,255,255,.4);margin-bottom:16px;font-family:monospace}
.modal .close{position:absolute;top:12px;right:16px;font-size:20px;cursor:pointer;color:rgba(255,255,255,.4);background:none;border:none}
.modal .close:hover{color:#fff}
.modal .ch{height:220px;position:relative;margin-bottom:16px}
.chart-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;margin-top:12px}
.mini-chart{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:10px;cursor:pointer}
.mini-chart:hover{border-color:rgba(99,102,241,.3)}
.mini-chart .mc-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.mini-chart .mc-name{font-size:11px;font-weight:700}.mini-chart .mc-chg{font-size:10px;font-family:monospace;font-weight:600}
.mini-chart canvas{width:100%!important;height:140px!important}
.view-toggle{display:flex;gap:5px;margin-bottom:12px}
.hisse-search{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:8px 14px;color:#e2e8f0;font-size:12px;font-family:monospace;width:260px;outline:none;transition:border-color .2s}
.hisse-search:focus{border-color:rgba(99,102,241,.5)}
.hisse-search::placeholder{color:rgba(255,255,255,.25)}
@media(max-width:768px){.ct{padding:0 12px 36px}.fv-name{width:140px;min-width:140px}.hisse-search{width:160px}}
</style></head>
<body>
<div class="hd"><div><h1>US <span>Sector Dashboard</span></h1><div class="meta" id="hm"></div></div></div>
<!-- ===== TOP LEVEL TABS ===== -->
<div class="main-tabs">
<button class="main-tab a" onclick="switchMain('sektor',this)">Sectors</button>
<button class="main-tab" onclick="switchMain('hisse',this)">Stocks</button>
</div>
<!-- ===== SEKTÖR PANEL ===== -->
<div id="panel-sektor" class="main-panel a">
<div class="tabs" id="mt">
<button class="tab a" onclick="sT('bars',this)">Sector Bars</button>
<button class="tab" onclick="sT('tbl',this)">Table</button></div>
<div class="ct">
<div class="ctr" id="gp"><span class="cl">Period:</span>
<button class="btn" onclick="sP('1d',this)">1D</button><button class="btn" onclick="sP('1w',this)">1W</button>
<button class="btn a" onclick="sP('1m',this)">1M</button><button class="btn" onclick="sP('3m',this)">3M</button>
<button class="btn" onclick="sP('6m',this)">6M</button><button class="btn" onclick="sP('12m',this)">12M</button></div>
<div class="idx-bar" id="idxBar"></div>
<div id="tab-bars" class="tc a"><div class="fv" id="fvGrid"></div></div>
<div id="tab-tbl" class="tc">
<div class="view-toggle" id="viewToggle" style="display:none">
<button class="btn a" onclick="setView('table',this)">Table</button>
<button class="btn" onclick="setView('charts',this)">Charts</button>
<span style="margin-left:12px;cursor:pointer;color:#6366f1;font-size:11px" id="backBtn2" onclick="showSectors()">← Back to sectors</span></div>
<div id="tableView">
<div class="tw"><div class="th"><span id="tblTitle">Sector Performance</span><span class="back" id="backBtn" onclick="showSectors()">← Back to sectors</span></div>
<div style="overflow-x:auto"><table id="mainTbl"></table></div></div></div>
<div id="chartView" style="display:none"><div class="chart-grid" id="chartGrid"></div></div>
</div>
</div>
</div>
<!-- ===== HİSSE PANEL ===== -->
<div id="panel-hisse" class="main-panel">
<div class="tabs" id="hisseTabs">
<button class="tab a" onclick="setHisseView('table',this)">Table</button>
<button class="tab" onclick="setHisseView('charts',this)">Charts</button>
</div>
<div class="ct">
<div class="ctr" id="hisseGp"><span class="cl">Period:</span>
<button class="btn" onclick="hisseSP('1d',this)">1D</button><button class="btn" onclick="hisseSP('1w',this)">1W</button>
<button class="btn a" onclick="hisseSP('1m',this)">1M</button><button class="btn" onclick="hisseSP('3m',this)">3M</button>
<button class="btn" onclick="hisseSP('6m',this)">6M</button><button class="btn" onclick="hisseSP('12m',this)">12M</button>
<span style="margin-left:12px"><input type="text" id="hisseSearch" class="hisse-search" placeholder="Search ticker or sector..." oninput="renderHisse()"></span>
</div>
<div class="idx-bar" id="hisseIdxBar"></div>
<div id="hisseTableWrap">
<div class="tw"><div class="th"><span>All Stocks</span><span id="hisseCount" style="font-size:11px;color:rgba(255,255,255,.3);font-family:monospace"></span></div>
<div style="overflow-x:auto"><table id="hisseTbl"></table></div></div>
</div>
<div id="hisseChartWrap" style="display:none">
<div style="margin-bottom:10px"><button class="btn" id="cloudFilterBtn" onclick="toggleCloudFilter()">☁️ In Cloud</button><button class="btn" id="atrFilterBtn" onclick="toggleAtrFilter()" style="margin-left:5px">📏 Within 1 ATR of EMA</button><button class="btn" id="rs85FilterBtn" onclick="toggleRs85Filter()" style="margin-left:5px" title="RS21 ≥ 85: short/medium-term momentum (21-day return percentile, not benchmark-relative)">⚡ RS 85+</button><button class="btn" id="ms85FilterBtn" onclick="toggleMs85Filter()" style="margin-left:5px" title="RS-MS ≥ 85: long-term leaders (MarketSmith-style, excess vs SPY, weighted 3M/6M/9M/12M). Newly IPO'd stocks (< 6mo) are excluded.">🏆 MS 85+</button><span id="cloudFilterInfo" style="font-size:10px;color:rgba(255,255,255,.3);font-family:monospace;margin-left:8px"></span><button class="btn" onclick="exportTvList()" style="margin-left:12px;border-color:rgba(99,102,241,.3);color:rgba(165,180,252,.7)">📋 TV List</button></div>
<div class="chart-grid" id="hisseChartGrid"></div>
</div>
</div>
</div>
<!-- ===== MODAL ===== -->
<div class="modal-bg" id="modalBg" onclick="if(event.target===this)closeModal()">
<div class="modal"><button class="close" onclick="closeModal()">✕</button>
<h2 id="mTitle"></h2><div class="msub" id="mSub"></div>
<div style="display:flex;gap:5px;margin-bottom:12px" id="yoyBtns">
<button class="btn a" onclick="setYoyMode('yoy',this)">YoY (Annual)</button>
<button class="btn" onclick="setYoyMode('qoq',this)">QoQ (Quarterly)</button></div>
<div class="ch"><canvas id="cRev"></canvas></div>
<div class="ch"><canvas id="cNI"></canvas></div>
</div></div>
<div class="ft"><span id="ft"></span><span>US Sector Dashboard v1.0</span></div>
<script>
const R=__DATA__;
let curP='1m',curSec=null,sC=-1,sA=false,barSort='perf',curView='table';
let hisseP='1m',hisseSC=-1,hisseSA=false,hisseViewMode='table',cloudFilter=false,atrFilter=false,rs85Filter=false,ms85Filter=false;
const miniCharts=[];
// ===== MAIN TAB SWITCHING =====
function switchMain(panel,btn){
document.querySelectorAll('.main-panel').forEach(p=>p.classList.remove('a'));
document.querySelectorAll('.main-tab').forEach(b=>b.classList.remove('a'));
document.getElementById('panel-'+panel).classList.add('a');
if(btn)btn.classList.add('a');
if(panel==='hisse')renderHisse();
if(panel==='sektor'){renderIdx();renderBars();renderTable()}}
// ===== SEKTÖR =====
function setView(v,btn){curView=v;document.querySelectorAll('#viewToggle .btn').forEach(b=>b.classList.remove('a'));if(btn)btn.classList.add('a');
document.getElementById('tableView').style.display=v==='table'?'block':'none';
document.getElementById('chartView').style.display=v==='charts'?'block':'none';
if(v==='charts'&&curSec)renderChartGrid()}
function destroyMiniCharts(){miniCharts.forEach(c=>c.destroy());miniCharts.length=0}
const rT={rs5:'RS5: 5-day raw return percentile rank (1-99). Short-term momentum, NOT benchmark-relative.',rs21:'RS21: 21-day raw return percentile rank (1-99). Medium-term momentum, NOT benchmark-relative. Picks up short-term winners regardless of long-term trend.',rsms:'RS-MS (MarketSmith / IBD-style): Excess return vs SPY weighted (3M x 40% + 6M x 20% + 9M x 20% + 12M x 20%), percentile rank (1-99). Long-term relative strength. Newly IPOd stocks (< 6 months data) are excluded.'};
function sT(id,b){document.querySelectorAll('.tc').forEach(t=>t.classList.remove('a'));document.querySelectorAll('#mt .tab').forEach(t=>t.classList.remove('a'));document.getElementById('tab-'+id).classList.add('a');if(b)b.classList.add('a')}
function sP(p,b){curP=p;document.querySelectorAll('#gp .btn').forEach(x=>x.classList.remove('a'));if(b)b.classList.add('a');sC=-1;barSort='perf';render()}
function pC(v){if(v==null)return'rgba(255,255,255,.08)';if(v>0)return v>8?'rgba(22,163,74,0.9)':v>4?'rgba(34,197,94,0.7)':v>2?'rgba(74,222,128,0.55)':'rgba(74,222,128,0.35)';return v<-8?'rgba(185,28,28,0.9)':v<-4?'rgba(220,38,38,0.7)':v<-2?'rgba(248,113,113,0.55)':'rgba(248,113,113,0.35)'}
function vF(v){if(v==null)return'-';return(v>0?'+':'')+v.toFixed(2)+'%'}
function vK(v){return v>0?'sp':v<0?'sn':''}
function rC(rs){if(rs==null)return['rgba(255,255,255,.08)','rgba(255,255,255,.5)'];if(rs>=80)return['rgba(22,163,74,0.3)','#4ade80'];if(rs>=60)return['rgba(74,222,128,0.15)','#4ade80'];if(rs>=40)return['rgba(255,255,255,.06)','rgba(255,255,255,.5)'];if(rs>=20)return['rgba(248,113,113,0.15)','#f87171'];return['rgba(185,28,28,0.3)','#f87171']}
function rB(v){const[bg,c]=rC(v);return'<span class="rb" style="background:'+bg+';color:'+c+'">'+(v||'-')+'</span>'}
// RS/MS rakam rengi: 90-99 açık yeşil, 85-89 gök mavisi, 85 altı turuncu
function rsColor(v){if(typeof v!=='number')return'rgba(255,255,255,.4)';if(v>=90)return'#4ade80';if(v>=85)return'#38bdf8';return'#fb923c'}
// Sektör sırası rengi: 1-5 açık yeşil, 6-10 gök mavisi, 11+ turuncu
function rankColor(r){if(typeof r!=='number')return'#fb923c';if(r<=5)return'#4ade80';if(r<=10)return'#38bdf8';return'#fb923c'}
function sortBar(k){barSort=k;renderBars()}
function renderIdx(){const bar=document.getElementById('idxBar');let h='';
['SPY','QQQ','IWM'].forEach(n=>{const p=R.idx_perf[n];if(!p)return;const v=p[curP];const c=v!=null?(v>0?'#4ade80':'#f87171'):'rgba(255,255,255,.4)';
h+='<div class="idx-card"><div class="idx-name">'+n+'</div><div class="idx-val" style="color:'+c+'">'+vF(v)+'</div><div class="idx-chg" style="color:rgba(255,255,255,.4)">Last: '+(p.last||0).toLocaleString('en-US')+'</div></div>'});
if(curSec){const r5=R.sec_rs5[curSec]||'-';const r21=R.sec_rs21[curSec]||'-';const rms=R.sec_rsms[curSec]||'-';const sp=R.sector_perf[curSec];const sv=sp?sp[curP]:null;const sc=sv!=null?(sv>0?'#4ade80':'#f87171'):'rgba(255,255,255,.4)';
h+='<div class="idx-card" style="border-color:rgba(99,102,241,.3)"><div class="idx-name" style="color:#a5b4fc">'+curSec+'</div><div class="idx-val" style="color:'+sc+'">'+vF(sv)+'</div><div style="display:flex;gap:6px;margin-top:4px;align-items:center"><span style="font-size:8px;color:rgba(255,255,255,.35)">RS5</span>'+rB(r5)+'<span style="font-size:8px;color:rgba(255,255,255,.35)">RS21</span>'+rB(r21)+'<span style="font-size:8px;color:rgba(255,255,255,.35)">MS</span>'+rB(rms)+'</div></div>'}
bar.innerHTML=h}
function renderBars(){const g=document.getElementById('fvGrid');const sp=R.sector_perf;
let sorted;
if(barSort==='rs5')sorted=Object.keys(sp).sort((a,b)=>(R.sec_rs5[b]||0)-(R.sec_rs5[a]||0));
else if(barSort==='rs21')sorted=Object.keys(sp).sort((a,b)=>(R.sec_rs21[b]||0)-(R.sec_rs21[a]||0));
else if(barSort==='rsms')sorted=Object.keys(sp).sort((a,b)=>(R.sec_rsms[b]||0)-(R.sec_rsms[a]||0));
else sorted=Object.keys(sp).sort((a,b)=>{const va=sp[a][curP],vb=sp[b][curP];if(va==null)return 1;if(vb==null)return -1;return vb-va});
let mx=0;sorted.forEach(s=>{const v=Math.abs(sp[s][curP]||0);if(v>mx)mx=v});if(mx<1)mx=1;
const bsAct=k=>barSort===k?'color:#a5b4fc;text-decoration:underline':'color:rgba(255,255,255,.35)';
let h='<div style="display:flex;align-items:center;margin-bottom:8px;padding:0 6px"><div style="width:240px;min-width:240px"></div><div style="flex:1"></div><div style="width:70px;min-width:70px"></div><div style="width:120px;min-width:120px;display:flex;gap:3px;justify-content:center;font-size:8px;font-family:monospace"><span style="min-width:24px;text-align:center;cursor:pointer;'+bsAct('rs5')+'" title="'+rT.rs5+'" onclick="sortBar(\'rs5\')">RS5</span><span style="min-width:24px;text-align:center;cursor:pointer;'+bsAct('rs21')+'" title="'+rT.rs21+'" onclick="sortBar(\'rs21\')">RS21</span><span style="min-width:24px;text-align:center;cursor:pointer;'+bsAct('rsms')+'" title="'+rT.rsms+'" onclick="sortBar(\'rsms\')">MS</span></div><div style="width:35px;min-width:35px"></div></div>';
sorted.forEach(s=>{const v=sp[s][curP];const r5=R.sec_rs5[s]||'-';const r21=R.sec_rs21[s]||'-';const rms=R.sec_rsms[s]||'-';const cnt=sp[s].count;
const pct=v!=null?Math.min(Math.abs(v)/mx*100,100):0;const clr=pC(v);const vCol=v>0?'#4ade80':v<0?'#f87171':'rgba(255,255,255,.4)';
h+='<div class="fv-row" onclick="curSec=\''+s.replace(/'/g,"\\'")+'\';sT(\'tbl\',document.querySelectorAll(\'#mt .tab\')[1]);renderTable();renderIdx()">';
h+='<div class="fv-name">'+s+'</div><div class="fv-bar-wrap">';
if(v>=0)h+='<div class="fv-bar" style="left:0;width:'+pct+'%;background:'+clr+'"></div>';
else h+='<div class="fv-bar" style="right:0;width:'+pct+'%;background:'+clr+'"></div>';
h+='</div><div class="fv-val" style="color:'+vCol+'">'+vF(v)+'</div>';
h+='<div class="fv-rs">'+rB(r5)+rB(r21)+rB(rms)+'</div><div class="fv-cnt">'+cnt+'</div></div>'});g.innerHTML=h}
function showSectors(){curSec=null;sC=-1;curView='table';destroyMiniCharts();
document.getElementById('viewToggle').style.display='none';
document.getElementById('tableView').style.display='block';
document.getElementById('chartView').style.display='none';
renderTable();renderIdx()}
function tS(i){if(sC===i)sA=!sA;else{sC=i;sA=false}renderTable()}
function renderTable(){const tbl=document.getElementById('mainTbl'),bb=document.getElementById('backBtn'),tt=document.getElementById('tblTitle');
const ar=sA?'▲':'▼';
function tH(i,l,tip){const cls=sC===i?' class="st"':'';const t=tip?' title="'+tip+'"':'';return'<th'+cls+t+' onclick="tS('+i+')">'+l+'<span class="sa">'+(sC===i?ar:'⇅')+'</span></th>'}
if(curSec){tt.textContent=curSec;bb.style.display='inline';
document.getElementById('viewToggle').style.display='flex';
const tks=(R.sectors[curSec]||[]).filter(t=>R.stock_perf[t]);
const pMap={rs5:1,rs21:2,rsms:3,'1d':4,'1w':5,'1m':6,'3m':7,'6m':8,'12m':9};
const rows=tks.map(t=>{const p=R.stock_perf[t];return[t,R.stock_rs5[t]||0,R.stock_rs21[t]||0,R.stock_rsms[t]||0,p['1d'],p['1w'],p['1m'],p['3m'],p['6m'],p['12m']]});
if(sC>=0)rows.sort((a,b)=>{const va=a[sC],vb=b[sC];if(va==null)return 1;if(vb==null)return -1;if(typeof va==='string')return sA?va.localeCompare(vb):vb.localeCompare(va);return sA?va-vb:vb-va});
else{const di=pMap[curP]||6;rows.sort((a,b)=>{const va=a[di],vb=b[di];if(va==null)return 1;if(vb==null)return -1;return vb-va})}
let h='<tr>'+tH(0,'Ticker')+tH(1,'RS5',rT.rs5)+tH(2,'RS21',rT.rs21)+tH(3,'RS-MS',rT.rsms)+tH(4,'1D')+tH(5,'1W')+tH(6,'1M')+tH(7,'3M')+tH(8,'6M')+tH(9,'12M')+'</tr>';
rows.forEach(r=>{h+='<tr style="cursor:pointer" onclick="showFin(\''+r[0]+'\')"><td>'+r[0]+'</td><td>'+rB(r[1])+'</td><td>'+rB(r[2])+'</td><td>'+rB(r[3])+'</td>';
for(let i=4;i<=9;i++)h+='<td class="'+vK(r[i])+'">'+vF(r[i])+'</td>';h+='</tr>'});tbl.innerHTML=h;
}else{tt.textContent='Sector Performance';bb.style.display='none';
const sp=R.sector_perf;
const rows=Object.keys(sp).map(s=>[s,R.sec_rs5[s]||0,R.sec_rs21[s]||0,R.sec_rsms[s]||0,sp[s].count,sp[s]['1d'],sp[s]['1w'],sp[s]['1m'],sp[s]['3m'],sp[s]['6m'],sp[s]['12m']]);
if(sC>=0)rows.sort((a,b)=>{const va=a[sC],vb=b[sC];if(va==null)return 1;if(vb==null)return -1;if(typeof va==='string')return sA?va.localeCompare(vb):vb.localeCompare(va);return sA?va-vb:vb-va});
else rows.sort((a,b)=>(sp[b[0]][curP]||0)-(sp[a[0]][curP]||0));
let h='<tr>'+tH(0,'Sector')+tH(1,'RS5',rT.rs5)+tH(2,'RS21',rT.rs21)+tH(3,'RS-MS',rT.rsms)+tH(4,'#')+tH(5,'1D')+tH(6,'1W')+tH(7,'1M')+tH(8,'3M')+tH(9,'6M')+tH(10,'12M')+'</tr>';
rows.forEach(r=>{h+='<tr class="sr" onclick="curSec=\''+r[0].replace(/'/g,"\\'")+'\';sC=-1;renderTable();renderIdx()">';
h+='<td>'+r[0]+'</td><td>'+rB(r[1])+'</td><td>'+rB(r[2])+'</td><td>'+rB(r[3])+'</td><td style="color:rgba(255,255,255,.3)">'+r[4]+'</td>';
for(let i=5;i<=10;i++)h+='<td class="'+vK(r[i])+'">'+vF(r[i])+'</td>';h+='</tr>'});tbl.innerHTML=h}}
// ===== FINANCIALS MODAL =====
let _cRev=null,_cNI=null,_curFin=null,_yoyMode='yoy';
function closeModal(){document.getElementById('modalBg').classList.remove('show');if(_cRev){_cRev.destroy();_cRev=null}if(_cNI){_cNI.destroy();_cNI=null}_curFin=null}
function fmtB(v){if(v==null)return'-';const a=Math.abs(v);if(a>=1e9)return(v/1e9).toFixed(1)+'B';if(a>=1e6)return(v/1e6).toFixed(1)+'M';if(a>=1e3)return(v/1e3).toFixed(0)+'K';return v.toString()}
function calcYoY(f,key){
function qN(d){const m=parseInt(d.slice(5,7));if(m<=3)return 1;if(m<=6)return 2;if(m<=9)return 3;return 4}
const byYQ={};f.forEach(x=>{byYQ[x.date.slice(0,4)+'Q'+qN(x.date)]=x});
return f.map(x=>{const y=parseInt(x.date.slice(0,4));const q=qN(x.date);const pk=(y-1)+'Q'+q;
const prev=byYQ[pk];if(!prev||prev[key]==null||x[key]==null||prev[key]===0)return null;
return Math.round((x[key]-prev[key])/Math.abs(prev[key])*100)})}
function calcQoQ(f,key){
return f.map((x,i)=>{if(i===0)return null;const prev=f[i-1];
if(!prev||prev[key]==null||x[key]==null||prev[key]===0)return null;
return Math.round((x[key]-prev[key])/Math.abs(prev[key])*100)})}
function setYoyMode(m,btn){_yoyMode=m;document.querySelectorAll('#yoyBtns .btn').forEach(b=>b.classList.remove('a'));if(btn)btn.classList.add('a');if(_curFin)renderFin(_curFin)}
function mkYoyPlug(yoyArr){return{id:'yoy'+Math.random(),afterDraw(chart){const ctx=chart.ctx;const xA=chart.scales.x;const bA=chart.chartArea.bottom;
yoyArr.forEach((v,i)=>{if(v==null)return;const x=xA.getPixelForValue(i);const txt=(v>0?'+':'')+v+'%';
ctx.save();ctx.font='bold 9px monospace';ctx.textAlign='center';ctx.textBaseline='top';
ctx.fillStyle=v>0?'rgba(74,222,128,0.85)':v<0?'rgba(248,113,113,0.85)':'rgba(255,255,255,0.4)';
ctx.fillText(txt,x,bA+22);ctx.restore()})}}}
function renderFin(t){
_curFin=t;const f=(R.fins||{})[t];
document.getElementById('mTitle').textContent=t;
if(!f||f.length<1){document.getElementById('mSub').textContent='No quarterly financial data available';
document.getElementById('cRev').style.display='none';document.getElementById('cNI').style.display='none';
document.getElementById('yoyBtns').style.display='none';
document.getElementById('modalBg').classList.add('show');return}
document.getElementById('cRev').style.display='block';document.getElementById('cNI').style.display='block';
document.getElementById('yoyBtns').style.display='flex';
const sec=R.sector_map[t]||'';document.getElementById('mSub').textContent=sec+' · '+f.length+' quarters';
const labels=f.map(x=>{const p=x.date.split('-');return p[0].slice(2)+'/'+p[1]});
const revs=f.map(x=>x.revenue);const nis=f.map(x=>x.net_income);
const revChg=_yoyMode==='qoq'?calcQoQ(f,'revenue'):calcYoY(f,'revenue');
const niChg=_yoyMode==='qoq'?calcQoQ(f,'net_income'):calcYoY(f,'net_income');
const chgLabel=_yoyMode==='qoq'?'QoQ':'YoY';
if(_cRev)_cRev.destroy();if(_cNI)_cNI.destroy();
const defs={responsive:true,maintainAspectRatio:false,layout:{padding:{bottom:18}},plugins:{legend:{display:false}},scales:{x:{ticks:{color:'rgba(255,255,255,.4)',font:{family:'monospace',size:10}}},y:{ticks:{color:'rgba(255,255,255,.3)',font:{family:'monospace',size:9},callback:v=>fmtB(v)},grid:{color:'rgba(255,255,255,.04)'}}}};
_cRev=new Chart(document.getElementById('cRev'),{type:'bar',data:{labels,datasets:[{label:'Revenue',data:revs,backgroundColor:revs.map(v=>v!=null&&v>0?'rgba(96,165,250,0.6)':'rgba(248,113,113,0.6)'),borderRadius:4,barPercentage:0.5,categoryPercentage:0.7}]},options:{...defs,plugins:{...defs.plugins,title:{display:true,text:'Quarterly Revenue',color:'rgba(255,255,255,.6)',font:{size:12}}}},plugins:[mkYoyPlug(revChg)]});
_cNI=new Chart(document.getElementById('cNI'),{type:'bar',data:{labels,datasets:[{label:'Net Income',data:nis,backgroundColor:nis.map(v=>v!=null&&v>=0?'rgba(74,222,128,0.6)':'rgba(248,113,113,0.6)'),borderRadius:4,barPercentage:0.5,categoryPercentage:0.7}]},options:{...defs,plugins:{...defs.plugins,title:{display:true,text:'Quarterly Net Income',color:'rgba(255,255,255,.6)',font:{size:12}}}},plugins:[mkYoyPlug(niChg)]});
document.getElementById('modalBg').classList.add('show')}
function showFin(t){_yoyMode='yoy';document.querySelectorAll('#yoyBtns .btn').forEach((b,i)=>{b.classList.toggle('a',i===0)});renderFin(t)}
// ===== CANDLES =====
function drawCandles(cvs,ohlcData,prefix){
if(!ohlcData||ohlcData.length<10)return;
const data=ohlcData.slice(-63);
const ctx=cvs.getContext('2d');
const W=cvs.offsetWidth;const H=140;
cvs.width=W*2;cvs.height=H*2;cvs.style.width=W+'px';cvs.style.height=H+'px';
ctx.scale(2,2);
const pad={t:8,b:8,l:2,r:2};
const cW=W-pad.l-pad.r;const cH=H-pad.t-pad.b;
const n=data.length;const bW=Math.max(1,cW/n*0.6);const gap=cW/n;
let mn=Infinity,mx=-Infinity;
data.forEach(d=>{if(d.l<mn)mn=d.l;if(d.h>mx)mx=d.h});
const rng=mx-mn||1;
function yP(v){return pad.t+cH*(1-(v-mn)/rng)}
const ema=[];let em=data[0].c;const k=2/22;
data.forEach((d,i)=>{if(i===0)em=d.c;else em=d.c*k+em*(1-k);ema.push(em)});
const emaH=[];let emH=data[0].h;
data.forEach((d,i)=>{if(i===0)emH=d.h;else emH=d.h*k+emH*(1-k);emaH.push(emH)});
const emaL=[];let emL=data[0].l;
data.forEach((d,i)=>{if(i===0)emL=d.l;else emL=d.l*k+emL*(1-k);emaL.push(emL)});
ctx.beginPath();
data.forEach((d,i)=>{const x=pad.l+i*gap+gap/2;const y=yP(emaH[i]);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});
for(let i=n-1;i>=0;i--){const x=pad.l+i*gap+gap/2;ctx.lineTo(x,yP(emaL[i]))}
ctx.closePath();ctx.fillStyle='rgba(255,255,255,0.15)';ctx.fill();
ctx.beginPath();ctx.strokeStyle='rgba(255,255,255,0.12)';ctx.lineWidth=0.5;
data.forEach((d,i)=>{const x=pad.l+i*gap+gap/2;if(i===0)ctx.moveTo(x,yP(emaH[i]));else ctx.lineTo(x,yP(emaH[i]))});ctx.stroke();
ctx.beginPath();ctx.strokeStyle='rgba(255,255,255,0.12)';ctx.lineWidth=0.5;
data.forEach((d,i)=>{const x=pad.l+i*gap+gap/2;if(i===0)ctx.moveTo(x,yP(emaL[i]));else ctx.lineTo(x,yP(emaL[i]))});ctx.stroke();
ctx.beginPath();ctx.strokeStyle='rgba(251,146,60,0.8)';ctx.lineWidth=1;
data.forEach((d,i)=>{const x=pad.l+i*gap+gap/2;const y=yP(ema[i]);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});
ctx.stroke();
data.forEach((d,i)=>{
const x=pad.l+i*gap+gap/2;
const isUp=d.c>=d.o;
const color=isUp?'rgba(74,222,128,0.8)':'rgba(248,113,113,0.8)';
ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=1;
ctx.moveTo(x,yP(d.h));ctx.lineTo(x,yP(d.l));ctx.stroke();
const oY=yP(d.o);const cY=yP(d.c);
const bodyH=Math.max(1,Math.abs(oY-cY));
ctx.fillStyle=color;
ctx.fillRect(x-bW/2,Math.min(oY,cY),bW,bodyH)})}
// ===== SEKTÖR CHART GRID =====
function renderChartGrid(){
destroyMiniCharts();
const grid=document.getElementById('chartGrid');
if(!curSec){grid.innerHTML='';return}
const tks=(R.sectors[curSec]||[]).filter(t=>R.stock_perf[t]);
function getSortVal(t){
const colMap={0:t,1:R.stock_rs5[t]||0,2:R.stock_rs21[t]||0,3:R.stock_rsms[t]||0};
if(sC>=0){
if(sC<=3)return colMap[sC];
const pk=['1d','1w','1m','3m','6m','12m'][sC-4];
return R.stock_perf[t]?R.stock_perf[t][pk]||0:0}
const pk=curP;return R.stock_perf[t]?R.stock_perf[t][pk]||0:0}
const sorted=[...tks].sort((a,b)=>{const va=getSortVal(a),vb=getSortVal(b);
if(typeof va==='string'&&sC>=0&&sA)return va.localeCompare(vb);
if(typeof va==='string')return vb.localeCompare?vb.localeCompare(va):0;
return sC>=0&&sA?va-vb:vb-va});
// Dinamik sektör sıralaması (curP periyoda göre)
const sortedSecs=Object.keys(R.sector_perf).sort((a,b)=>{
const va=R.sector_perf[a][curP],vb=R.sector_perf[b][curP];
if(va==null)return 1;if(vb==null)return -1;return vb-va});
const secRank={};sortedSecs.forEach((s,i)=>{secRank[s]=i+1});
let h='';
sorted.forEach((t,idx)=>{
const p=R.stock_perf[t];const chg=p?p[curP]:null;
const chgC=chg!=null?(chg>0?'#4ade80':'#f87171'):'rgba(255,255,255,.4)';
const rs=R.stock_rs21[t];
const ms=R.stock_rsms[t];
const rsTxt=(typeof rs==='number')?rs:'-';
const msTxt=(typeof ms==='number')?ms:'-';
const rsC=rsColor(rs);
const msC=rsColor(ms);
const sec=R.sector_map[t]||'';
const rank=secRank[sec];
const rankTxt=rank?'<span style="font-size:10px;font-weight:700;color:'+rankColor(rank)+'" title="Sector rank by '+curP+' performance">#'+rank+'</span> ':'';
h+='<div class="mini-chart" onclick="showFin(\''+t+'\')"><div class="mc-hd"><span class="mc-name">'+t+' '+rankTxt+'<span style="font-size:8px;font-weight:400;color:rgba(255,255,255,.3)">'+sec+'</span> <span style="font-size:10px;font-weight:700;color:'+rsC+'" title="RS21">RS:'+rsTxt+'</span> <span style="font-size:10px;font-weight:700;color:'+msC+'" title="RS-MS">MS:'+msTxt+'</span></span><span class="mc-chg" style="color:'+chgC+'">'+vF(chg)+'</span></div><canvas id="mc_'+idx+'"></canvas></div>'});
grid.innerHTML=h;
sorted.forEach((t,idx)=>{
const ohlc=(R.ohlc||{})[t];
const cvs=document.getElementById('mc_'+idx);
if(cvs)drawCandles(cvs,ohlc,'mc_')})}
// ===== FILTERS =====
function isInCloud(t){
const ohlc=(R.ohlc||{})[t];
if(!ohlc||ohlc.length<22)return false;
const data=ohlc.slice(-63);
const k=2/22;
let emH=data[0].h,emL=data[0].l;
for(let i=1;i<data.length;i++){emH=data[i].h*k+emH*(1-k);emL=data[i].l*k+emL*(1-k)}
const lastClose=data[data.length-1].c;
return lastClose>=emL&&lastClose<=emH}
function toggleCloudFilter(){cloudFilter=!cloudFilter;
const btn=document.getElementById('cloudFilterBtn');
btn.classList.toggle('a',cloudFilter);
renderHisse()}
function isNearEma(t){
const ohlc=(R.ohlc||{})[t];
if(!ohlc||ohlc.length<22)return false;
const data=ohlc.slice(-63);
const k=2/22;
let em=data[0].c;
for(let i=1;i<data.length;i++){em=data[i].c*k+em*(1-k)}
let atr=0;
for(let i=data.length-14;i<data.length;i++){
const tr=Math.max(data[i].h-data[i].l,Math.abs(data[i].h-data[i-1].c),Math.abs(data[i].l-data[i-1].c));
atr+=tr}
atr/=14;
const lastClose=data[data.length-1].c;
return Math.abs(lastClose-em)<=atr}
function toggleAtrFilter(){atrFilter=!atrFilter;
const btn=document.getElementById('atrFilterBtn');
btn.classList.toggle('a',atrFilter);
renderHisse()}
function toggleRs85Filter(){rs85Filter=!rs85Filter;
const btn=document.getElementById('rs85FilterBtn');
btn.classList.toggle('a',rs85Filter);
renderHisse()}
function toggleMs85Filter(){ms85Filter=!ms85Filter;
const btn=document.getElementById('ms85FilterBtn');
btn.classList.toggle('a',ms85Filter);
renderHisse()}
// ===== HİSSE =====
function setHisseView(v,btn){hisseViewMode=v;document.querySelectorAll('#hisseTabs .tab').forEach(b=>b.classList.remove('a'));if(btn)btn.classList.add('a');
document.getElementById('hisseTableWrap').style.display=v==='table'?'block':'none';
document.getElementById('hisseChartWrap').style.display=v==='charts'?'block':'none';
renderHisse()}
function hisseSP(p,btn){hisseP=p;document.querySelectorAll('#hisseGp .btn').forEach(x=>x.classList.remove('a'));if(btn)btn.classList.add('a');hisseSC=-1;renderHisse()}
function hisseTS(i){if(hisseSC===i)hisseSA=!hisseSA;else{hisseSC=i;hisseSA=false}renderHisse()}
function getFilteredStocks(){
const q=(document.getElementById('hisseSearch')||{}).value||'';
const qUp=q.toUpperCase().trim();
const allTks=Object.keys(R.stock_perf);
if(!qUp)return allTks;
return allTks.filter(t=>{
const sec=(R.sector_map[t]||'').toUpperCase();
return t.toUpperCase().includes(qUp)||sec.includes(qUp)})}
function renderHisse(){
const tks=getFilteredStocks();
const ibar=document.getElementById('hisseIdxBar');let ih='';
['SPY','QQQ','IWM'].forEach(n=>{const p=R.idx_perf[n];if(!p)return;const v=p[hisseP];const c=v!=null?(v>0?'#4ade80':'#f87171'):'rgba(255,255,255,.4)';
ih+='<div class="idx-card"><div class="idx-name">'+n+'</div><div class="idx-val" style="color:'+c+'">'+vF(v)+'</div><div class="idx-chg" style="color:rgba(255,255,255,.4)">Last: '+(p.last||0).toLocaleString('en-US')+'</div></div>'});
ibar.innerHTML=ih;
if(hisseViewMode==='table')renderHisseTable(tks);
else renderHisseCharts(tks)}
function renderHisseTable(tks){
const tbl=document.getElementById('hisseTbl');
const ar=hisseSA?'▲':'▼';
function tH(i,l,tip){const cls=hisseSC===i?' class="st"':'';const t=tip?' title="'+tip+'"':'';return'<th'+cls+t+' onclick="hisseTS('+i+')">'+l+'<span class="sa">'+(hisseSC===i?ar:'⇅')+'</span></th>'}
const rows=tks.map(t=>{const p=R.stock_perf[t];return[t,R.sector_map[t]||'',R.stock_rs5[t]||0,R.stock_rs21[t]||0,R.stock_rsms[t]||0,p['1d'],p['1w'],p['1m'],p['3m'],p['6m'],p['12m']]});
if(hisseSC>=0)rows.sort((a,b)=>{const va=a[hisseSC],vb=b[hisseSC];if(va==null)return 1;if(vb==null)return -1;if(typeof va==='string')return hisseSA?va.localeCompare(vb):vb.localeCompare(va);return hisseSA?va-vb:vb-va});
else{const pMap={'1d':5,'1w':6,'1m':7,'3m':8,'6m':9,'12m':10};const di=pMap[hisseP]||7;rows.sort((a,b)=>{const va=a[di],vb=b[di];if(va==null)return 1;if(vb==null)return -1;return vb-va})}
document.getElementById('hisseCount').textContent=rows.length+' stocks';
let h='<tr>'+tH(0,'Ticker')+tH(1,'Sector')+tH(2,'RS5',rT.rs5)+tH(3,'RS21',rT.rs21)+tH(4,'RS-MS',rT.rsms)+tH(5,'1D')+tH(6,'1W')+tH(7,'1M')+tH(8,'3M')+tH(9,'6M')+tH(10,'12M')+'</tr>';
rows.forEach(r=>{h+='<tr style="cursor:pointer" onclick="showFin(\''+r[0]+'\')">';
h+='<td>'+r[0]+'</td><td style="color:rgba(255,255,255,.35);font-weight:400;font-size:10px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+r[1]+'</td>';
h+='<td>'+rB(r[2])+'</td><td>'+rB(r[3])+'</td><td>'+rB(r[4])+'</td>';
for(let i=5;i<=10;i++)h+='<td class="'+vK(r[i])+'">'+vF(r[i])+'</td>';h+='</tr>'});
tbl.innerHTML=h}
function renderHisseCharts(tks){
const grid=document.getElementById('hisseChartGrid');
const info=document.getElementById('cloudFilterInfo');
let filtered=tks;
if(cloudFilter){filtered=filtered.filter(t=>isInCloud(t))}
if(atrFilter){filtered=filtered.filter(t=>isNearEma(t))}
if(rs85Filter){filtered=filtered.filter(t=>{const rs=R.stock_rs21[t];return typeof rs==='number'&&rs>=85})}
if(ms85Filter){filtered=filtered.filter(t=>{const rs=R.stock_rsms[t];return typeof rs==='number'&&rs>=85})}
if(cloudFilter||atrFilter||rs85Filter||ms85Filter){info.textContent=filtered.length+'/'+tks.length+' stocks'}
else{info.textContent=''}
const rows=filtered.map(t=>({t,p:R.stock_perf[t]}));
if(hisseSC>=0){
const getVal=(t)=>{
const colMap=[t,R.sector_map[t]||'',R.stock_rs5[t]||0,R.stock_rs21[t]||0,R.stock_rsms[t]||0];
if(hisseSC<=4)return colMap[hisseSC];
const pk=['1d','1w','1m','3m','6m','12m'][hisseSC-5];
return R.stock_perf[t]?R.stock_perf[t][pk]||0:0};
rows.sort((a,b)=>{const va=getVal(a.t),vb=getVal(b.t);
if(typeof va==='string')return hisseSA?va.localeCompare(vb):vb.localeCompare(va);
return hisseSA?va-vb:vb-va})}
else{rows.sort((a,b)=>{const va=a.p?a.p[hisseP]||0:0;const vb=b.p?b.p[hisseP]||0:0;return vb-va})}
// Dinamik sektör sıralaması — kullanıcının seçtiği periyoda göre
const sortedSecs=Object.keys(R.sector_perf).sort((a,b)=>{
const va=R.sector_perf[a][hisseP],vb=R.sector_perf[b][hisseP];
if(va==null)return 1;if(vb==null)return -1;return vb-va});
const secRank={};sortedSecs.forEach((s,i)=>{secRank[s]=i+1});
let h='';
rows.forEach(({t},idx)=>{
const p=R.stock_perf[t];const chg=p?p[hisseP]:null;
const chgC=chg!=null?(chg>0?'#4ade80':'#f87171'):'rgba(255,255,255,.4)';
const rs=R.stock_rs21[t];
const ms=R.stock_rsms[t];
const rsTxt=(typeof rs==='number')?rs:'-';
const msTxt=(typeof ms==='number')?ms:'-';
const rsC=rsColor(rs);
const msC=rsColor(ms);
const sec=R.sector_map[t]||'';
const rank=secRank[sec];
const rankTxt=rank?'<span style="font-size:10px;font-weight:700;color:'+rankColor(rank)+'" title="Sector rank by '+hisseP+' performance">#'+rank+'</span> ':'';
h+='<div class="mini-chart" onclick="showFin(\''+t+'\')"><div class="mc-hd"><span class="mc-name">'+t+' '+rankTxt+'<span style="font-size:8px;font-weight:400;color:rgba(255,255,255,.3)">'+sec+'</span> <span style="font-size:10px;font-weight:700;color:'+rsC+'" title="RS21">RS:'+rsTxt+'</span> <span style="font-size:10px;font-weight:700;color:'+msC+'" title="RS-MS">MS:'+msTxt+'</span></span><span class="mc-chg" style="color:'+chgC+'">'+vF(chg)+'</span></div><canvas id="hmc_'+idx+'"></canvas></div>'});
grid.innerHTML=h;
requestAnimationFrame(()=>{
rows.forEach(({t},idx)=>{
const ohlc=(R.ohlc||{})[t];
const cvs=document.getElementById('hmc_'+idx);
if(cvs)drawCandles(cvs,ohlc,'hmc_')})})}
// ===== TV LIST EXPORT (NASDAQ:/NYSE: prefix) =====
function exportTvList(){
const tks=getFilteredStocks();
let filtered=tks;
if(cloudFilter){filtered=filtered.filter(t=>isInCloud(t))}
if(atrFilter){filtered=filtered.filter(t=>isNearEma(t))}
if(rs85Filter){filtered=filtered.filter(t=>{const rs=R.stock_rs21[t];return typeof rs==='number'&&rs>=85})}
if(ms85Filter){filtered=filtered.filter(t=>{const rs=R.stock_rsms[t];return typeof rs==='number'&&rs>=85})}
const rows=filtered.map(t=>({t,p:R.stock_perf[t]}));
if(hisseSC>=0){
const getVal=(t)=>{
const colMap=[t,R.sector_map[t]||'',R.stock_rs5[t]||0,R.stock_rs21[t]||0,R.stock_rsms[t]||0];
if(hisseSC<=4)return colMap[hisseSC];
const pk=['1d','1w','1m','3m','6m','12m'][hisseSC-5];
return R.stock_perf[t]?R.stock_perf[t][pk]||0:0};
rows.sort((a,b)=>{const va=getVal(a.t),vb=getVal(b.t);
if(typeof va==='string')return hisseSA?va.localeCompare(vb):vb.localeCompare(va);
return hisseSA?va-vb:vb-va})}
else{rows.sort((a,b)=>{const va=a.p?a.p[hisseP]||0:0;const vb=b.p?b.p[hisseP]||0:0;return vb-va})}
// Per-ticker exchange (NASDAQ/NYSE) from R.exchange_map
const exMap=R.exchange_map||{};
const listStr=rows.map(r=>{
const ex=exMap[r.t]||'NASDAQ';
return ex+':'+r.t}).join(',');
const old=document.getElementById('tvExportModal');if(old)old.remove();
const div=document.createElement('div');
div.id='tvExportModal';
div.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.75);z-index:200;display:flex;justify-content:center;align-items:center';
div.onclick=function(e){if(e.target===div)div.remove()};
div.innerHTML='<div style="background:#12121e;border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:24px;width:90%;max-width:680px;max-height:80vh;overflow-y:auto;position:relative">'
+'<button onclick="document.getElementById(\'tvExportModal\').remove()" style="position:absolute;top:12px;right:16px;font-size:20px;cursor:pointer;color:rgba(255,255,255,.4);background:none;border:none">✕</button>'
+'<h2 style="font-size:15px;font-weight:700;margin-bottom:4px">TradingView List</h2>'
+'<div style="font-size:11px;color:rgba(255,255,255,.35);font-family:monospace;margin-bottom:14px">'+rows.length+' tickers · paste into TradingView Watchlist (comma-separated)</div>'
+'<textarea id="tvListTA" readonly style="width:100%;height:160px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:12px;color:#e2e8f0;font-family:monospace;font-size:11px;resize:vertical;outline:none;line-height:1.5">'+listStr+'</textarea>'
+'<div style="display:flex;gap:8px;margin-top:12px">'
+'<button onclick="const ta=document.getElementById(\'tvListTA\');ta.select();navigator.clipboard.writeText(ta.value).then(()=>{this.textContent=\'✓ Copied!\';setTimeout(()=>this.textContent=\'📋 Copy\',2000)}).catch(()=>{ta.select();document.execCommand(\'copy\');this.textContent=\'✓ Copied!\';setTimeout(()=>this.textContent=\'📋 Copy\',2000)})" class="btn a" style="font-size:12px;padding:8px 18px">📋 Copy</button>'
+'<button onclick="const ta=document.getElementById(\'tvListTA\');const spaced=ta.value.replace(/,/g,\' \');navigator.clipboard.writeText(spaced).catch(()=>{});this.textContent=\'✓ Copied!\';setTimeout(()=>this.textContent=\'Copy w/ spaces\',2000)" class="btn" style="font-size:12px;padding:8px 18px">Copy w/ spaces</button>'
+'</div>'
+'<div style="font-size:10px;color:rgba(255,255,255,.2);font-family:monospace;margin-top:10px">💡 TradingView → Watchlist → Import symbols → paste</div>'
+'</div>';
document.body.appendChild(div);
setTimeout(()=>{const ta=document.getElementById('tvListTA');if(ta)ta.select()},50)}
// ===== RENDER =====
function render(){renderIdx();renderBars();renderTable();
document.getElementById('hm').textContent=new Date().toLocaleString('en-US')+' · '+Object.keys(R.sector_map).length+' stocks · '+Object.keys(R.sector_perf).length+' sectors';
document.getElementById('ft').textContent='Last update: '+new Date().toLocaleString('en-US')}
render();
</script></body></html>"""

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    log("=" * 55)
    log("US SECTOR DASHBOARD")
    log(f"Klasör: {BASE_DIR}")
    log("=" * 55)

    # 1) Universe
    universe = get_universe()

    # 2) OHLC cache
    ohlc_cache = load_json(OHLC_CACHE)
    log(f"OHLC cache: {len(ohlc_cache)} ticker zaten kayıtlı")
    ohlc_cache = update_ohlc_cache(universe, ohlc_cache)
    log(f"OHLC cache → {len(ohlc_cache)} ticker (kaydedildi)")

    # 3) Meta cache (sector/industry/exchange)
    meta_cache = load_json(META_CACHE)
    log(f"Meta cache: {len(meta_cache)} ticker zaten kayıtlı")
    # Sadece OHLC'si olan ticker'ların meta'sını çekmeye gerek var
    have_ohlc = [t for t in universe if t in ohlc_cache and ohlc_cache[t]]
    meta_cache = update_meta_cache(have_ohlc, meta_cache)
    log(f"Meta cache → {len(meta_cache)} ticker")

    # 4) Performance
    log("Performance hesaplanıyor...")
    perf_map = {}
    for t in have_ohlc:
        p = calc_perf(ohlc_cache[t])
        if p: perf_map[t] = p
    log(f"  → {len(perf_map)} ticker için perf hesaplandı")

    # 5) Endeks perf'leri (SPY/QQQ/IWM ayrı tutulur)
    idx_perf = {}
    for bm in BENCHMARKS:
        p = calc_perf(ohlc_cache.get(bm))
        if p:
            idx_perf[bm] = p
    # Hisse perf'inden benchmark'ları çıkar (stock_perf'e karışmasınlar)
    stock_perf = {t: p for t, p in perf_map.items() if t not in BENCHMARKS}

    # 6) RS — RS5/RS21 ham getiri (kısa-orta momentum), RS-MS excess vs SPY (uzun vadeli leader)
    log("RS skorları (RS5/RS21 ham, RS-MS excess vs SPY)...")
    stock_ohlc = {t: ohlc_cache[t] for t in stock_perf}
    spy_recs = ohlc_cache.get("SPY", [])
    spy_closes = [r["c"] for r in spy_recs] if spy_recs else None
    if not spy_closes:
        log("  ⚠️  SPY verisi yok — RS-MS hesabı düz getiri ile yapılacak (benchmark=0)")
    stock_rs5  = calc_rs5 (stock_ohlc, spy_closes)
    stock_rs21 = calc_rs21(stock_ohlc, spy_closes)
    stock_rsms = calc_rsms(stock_ohlc, spy_closes)
    log(f"  RS5  : {len(stock_rs5)}")
    log(f"  RS21 : {len(stock_rs21)}")
    log(f"  RS-MS: {len(stock_rsms)}  (newly IPO'd <6mo excluded by design)")

    # 7) Sektörler
    log("Sektör aggregate...")
    sectors, sector_map, sector_perf = build_sectors(meta_cache, ohlc_cache, stock_perf)
    log(f"  → {len(sectors)} sektör (>=2 ticker), {len(sector_map)} ticker maplendi")

    # Sektör RS (medyan)
    sec_rs5  = calc_sector_rs(sector_perf, sectors, stock_rs5,  "rs5")
    sec_rs21 = calc_sector_rs(sector_perf, sectors, stock_rs21, "rs21")
    sec_rsms = calc_sector_rs(sector_perf, sectors, stock_rsms, "rsms")

    # 8) Quarterly financials (sadece sectorlanmış ticker'lar için)
    log("Quarterly financials çekiliyor...")
    fins = {}
    fin_tickers = list(sector_map.keys())
    def fetch_fin(t):
        return t, _fetch_quarterly_financials(t)
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
        futures = {ex.submit(fetch_fin, t): t for t in fin_tickers}
        done = 0
        for f in as_completed(futures):
            t, recs = f.result()
            done += 1
            if recs:
                fins[t] = recs
            if done % 40 == 0:
                log(f"  Fins: {done}/{len(fin_tickers)}")
    log(f"  → {len(fins)} ticker için financials")

    # 9) Exchange map (TV export için)
    exchange_map = {}
    for t, m in meta_cache.items():
        ex = (m.get("exchange") or "").upper()
        if "NMS" in ex or "NCM" in ex or "NGM" in ex or "NAS" in ex:
            exchange_map[t] = "NASDAQ"
        elif "NYQ" in ex or "NYS" in ex or "ARCA" in ex or "AMEX" in ex:
            exchange_map[t] = "NYSE"
        else:
            exchange_map[t] = "NASDAQ"  # default

    # 10) Payload
    payload = {
        "stock_perf":   stock_perf,
        "stock_rs5":    stock_rs5,
        "stock_rs21":   stock_rs21,
        "stock_rsms":   stock_rsms,
        "idx_perf":     idx_perf,
        "sector_perf":  sector_perf,
        "sec_rs5":      sec_rs5,
        "sec_rs21":     sec_rs21,
        "sec_rsms":     sec_rsms,
        "sectors":      sectors,
        "sector_map":   sector_map,
        "fins":         fins,
        "ohlc":         {t: ohlc_cache[t] for t in sector_map if t in ohlc_cache},
        "exchange_map": exchange_map,
    }
    payload_json = json.dumps(payload, separators=(',', ':'))
    log(f"Payload: {len(payload_json)/1024:.0f} KB")

    # 11) HTML
    html = HTML_TEMPLATE.replace("__DATA__", payload_json)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    log(f"\n✅  Tamamlandı!")
    log(f"   Dosya       : {OUTPUT_FILE}")
    log(f"   Sektör      : {len(sectors)}")
    log(f"   Hisse       : {len(sector_map)}")
    log(f"   Financials  : {len(fins)}")
    log(f"   OHLC cache  : {len(ohlc_cache)} ticker → {OHLC_CACHE}")
    log(f"   Meta cache  : {len(meta_cache)} ticker → {META_CACHE}")
    log(f"\n   Aç          : open {os.path.basename(OUTPUT_FILE)}\n")

if __name__ == "__main__":
    main()
