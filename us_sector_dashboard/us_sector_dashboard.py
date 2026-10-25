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

Ek (Theme/Lab Report):
  stocks_export*.csv aynı klasörde olmalı (Ticker, Industry, Theme sütunları) → Industry/Theme buradan gelir.
  Lab Report sekmesi: Leaders map, 7 durum, theme kümeleri, sektör sıralaması, concentration test.

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
FIN_CACHE   = os.path.join(BASE_DIR, "us_sector_fin.json")      # çeyreklik financials (yılda 4 kez yenilenir)
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
            _today = date.today().strftime("%Y-%m-%d")
            df = df[df.index.strftime("%Y-%m-%d") < _today]
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
            _UNI_PARTS["SPX"] = ({tk: _GICS_MAP.get(str(s).strip(), str(s).strip()) for tk, s in zip(sp_tickers, sp["GICS Sector"].tolist())}
                                 if "GICS Sector" in sp.columns else {tk: "" for tk in sp_tickers})
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
            _UNI_PARTS["NDX"] = {tk: "" for tk in ndx_tickers}
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
                    scol = next((c for c in cols if "gics sector" in c.lower()), None)
                    _UNI_PARTS["MID"] = ({tk: _GICS_MAP.get(str(s).strip(), str(s).strip()) for tk, s in zip(sp400_tickers, t[scol].tolist())}
                                         if scol else {tk: "" for tk in sp400_tickers})
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

def _last_quarter_end(today):
    """Bugün ya da öncesindeki son çeyrek sonu (31 Mart / 30 Haziran / 30 Eylül / 31 Aralık)."""
    qe = [date(y, m, d) for y in (today.year - 1, today.year) for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    return max(d for d in qe if d <= today)

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
        # Son kayıtlı günü de çek: auto_adjust ile gelen fiyat cache'tekiyle uyuşmuyorsa
        # (split/temettü sonrası geçmiş yeniden düzeltildi) → ticker baştan çekilir
        last = cache[t][-1]["d"]
        df = _fetch_history(t, start=last)
        recs = df_to_ohlc_recs(df) if df is not None else []
        redo = False
        if recs and recs[0]["d"] == last:
            old_c = cache[t][-1]["c"]
            if old_c and abs(recs[0]["c"] / old_c - 1) > 0.0005:
                redo = True
        return t, recs, redo

    if need_incr:
        redo_list = []
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
            futures = {ex.submit(fetch_incr, t): t for t in need_incr}
            done = 0
            for f in as_completed(futures):
                t, new_recs, redo = f.result()
                done += 1
                if redo:
                    redo_list.append(t)
                elif new_recs:
                    exist_d = {r["d"] for r in cache[t]}
                    added = [r for r in new_recs if r["d"] not in exist_d]
                    if added:
                        cache[t].extend(added)
                if done % 30 == 0:
                    log(f"    Incremental: {done}/{len(need_incr)}")
                    save_json(OHLC_CACHE, cache)
        if redo_list:
            log(f"    Fiyat düzeltmesi (split/temettü) → {len(redo_list)} ticker baştan çekiliyor")
            with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
                for t, recs in ex.map(fetch_full, redo_list):
                    if recs:
                        cache[t] = recs
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

_RSMS_STALE = []   # son hesaplamada SPY'dan 4+ gün geri kalan (cache'i eski) ticker'lar

def _close_at(ds, closes, ref_date):
    """ref_date'te ya da öncesindeki son kapanış (yoksa None)."""
    i = bisect.bisect_right(ds, ref_date) - 1
    return closes[i] if i >= 0 else None

def calc_rsms(ohlc_map, bench_closes, bench_dates=None):
    """
    RS-MS (MarketSmith / IBD tarzı): hisse-vs-SPY excess return ağırlıklı:
      3A×40% + 6A×20% + 9A×20% + 12A×20%
    Bu uzun vadeli "true leader" sinyalidir.

    Minimum veri şartı: 6 ay (127+ gün).
    3-6 ay arası veri olan ticker'lar (yeni arzlar) RS-MS'e dahil edilmez
    → UI'da dict'te olmadığı için `-` / `null` olarak görünür.

    6+ ay verisi olan ama 12 aylık verisi olmayan hisseler için ağırlıklar
    mevcut bileşenler üzerinde renormalize edilir.

    TARİH HİZALAMA (bench_dates verilirse): hissenin getiri pencereleri "sondan n gün" yerine
    SPY'ın takvimindeki aynı tarihlere göre hesaplanır. Cache'i güncellenemeyen (SPY'dan 4+ gün
    geri kalan) hisseler RS-MS'ten çıkarılır ve _RSMS_STALE'e yazılır. bench_dates verilmezse
    eski davranış (sıra sayısı ile hizalama) kullanılır.
    """
    _RSMS_STALE.clear()
    b3  = _bench_return(bench_closes, 63)
    b6  = _bench_return(bench_closes, 126)
    b9  = _bench_return(bench_closes, 189)
    b12 = _bench_return(bench_closes, 252)
    use_dates = bool(bench_dates) and bench_closes is not None and len(bench_dates) == len(bench_closes)
    if use_dates:
        anchor_dt = datetime.strptime(bench_dates[-1], "%Y-%m-%d")
        refs = {n: (bench_dates[-1 - n] if len(bench_dates) > n else None) for n in (63, 126, 189, 252)}
    raw = {}
    for t, ohlc in ohlc_map.items():
        # Yeni arz koruması: en az 6 aylık veri şart
        if not ohlc or len(ohlc) < 127: continue
        c = [r["c"] for r in ohlc]
        try:
            comps = []        # (excess_return, ağırlık)
            if use_dates:
                ds = [r["d"] for r in ohlc]
                if (anchor_dt - datetime.strptime(ds[-1], "%Y-%m-%d")).days > 4:
                    _RSMS_STALE.append(t)
                    continue
                p63  = _close_at(ds, c, refs[63])  if refs[63]  else None
                p126 = _close_at(ds, c, refs[126]) if refs[126] else None
                if not p63 or p63 <= 0 or not p126 or p126 <= 0:
                    continue   # 3A + 6A bileşeni şart
                comps.append((c[-1]/p63  - 1 - b3, 0.4))
                comps.append((c[-1]/p126 - 1 - b6, 0.2))
                p189 = _close_at(ds, c, refs[189]) if refs[189] else None
                if p189 and p189 > 0:
                    comps.append((c[-1]/p189 - 1 - b9, 0.2))
                p252 = _close_at(ds, c, refs[252]) if refs[252] else None
                if p252 and p252 > 0:
                    comps.append((c[-1]/p252 - 1 - b12, 0.2))
            else:
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
# CSV (INDUSTRY / THEME) + THEME GRUPLARI
# ─────────────────────────────────────────────
import glob, bisect

# Lab Report için ek ETF'ler (sektör sıralaması + concentration test). Hisse evrenine KARIŞMAZ,
# sadece OHLC cache'ine eklenir.
LAB_SECTOR_ETFS = [("RSPT", "Technology"), ("RSPH", "Healthcare"), ("RSPG", "Energy"),
                   ("RSPS", "Consumer Staples"), ("RSPM", "Basic Materials"),
                   ("RSPD", "Consumer Discretionary"), ("RSPN", "Industrial"),
                   ("RSPF", "Financial Services"), ("RSPC", "Communication Services"),
                   ("RSPU", "Utilities"), ("RSPR", "Real Estate")]
LAB_EXTRA_ETFS = ["SMH", "RSP", "IWF", "IWD"]
LAB_ETFS = [e for e, _ in LAB_SECTOR_ETFS] + LAB_EXTRA_ETFS

LAB_STATE_DEFS = [
    ["buy",       "In the buy area",             "Daily inside the 21-day lane, weekly in its normal range. The cleanest entry."],
    ["buy_ext",   "Buy area, weekly stretched",  "Daily inside the lane, but the weekly is more than 4 ATR above its 10-week SMA. More room to give back."],
    ["above",     "Above the buy area",          "Daily more than 1 ATR above the 21-day EMA, weekly normal. Wait for a pullback."],
    ["ext_both",  "Extended on both",            "Stretched on the daily and the weekly. Hold or trim, don't chase."],
    ["under",     "Under the 21-day",            "Daily has dropped below the lane while the weekly holds. Watch, don't add."],
    ["unwinding", "Unwinding",                   "Weekly stretched, daily already below the lane. Often resets into the next entry."],
    ["lost",      "Lost the weekly",             "Below the 10-week SMA. Not a leader setup anymore."],
]

def load_theme_csv():
    """
    stocks_export*.csv (en yeni dosya) → {ticker: {"industry": ..., "theme": ...}}
    Theme '-' veya boşsa "" olarak tutulur. Dosya yoksa {} döner (dashboard Yahoo industry ile çalışmaya devam eder).
    """
    files = sorted(glob.glob(os.path.join(BASE_DIR, "stocks_export*.csv")),
                   key=os.path.getmtime, reverse=True)
    if not files:
        log("  ⚠️  stocks_export*.csv bulunamadı — Industry Yahoo'dan, Theme boş kalacak")
        return {}
    try:
        df = pd.read_csv(files[0], dtype=str).fillna("")
        out = {}
        for tk, ind, th in zip(df["Ticker"], df["Industry"], df["Theme"]):
            tk = tk.strip().upper()
            if not tk:
                continue
            th = th.strip()
            out[tk] = {"industry": ind.strip(), "theme": "" if th in ("-", "") else th}
        log(f"  Theme CSV      : {os.path.basename(files[0])} → {len(out)} ticker")
        return out
    except Exception as e:
        log(f"  ⚠️  Theme CSV okunamadı ({e}) — Yahoo industry ile devam")
        return {}

def build_theme_groups(csv_meta, sector_map, stock_perf, stock_rsms):
    """Theme → [ticker] (>=2 hisse), theme perf (ortalama) ve theme RS-MS (medyan)."""
    groups = {}
    for t in sector_map:
        th = (csv_meta.get(t) or {}).get("theme", "")
        if th:
            groups.setdefault(th, []).append(t)
    groups = {k: v for k, v in groups.items() if len(v) >= 2}
    perf = {}
    for th, tks in groups.items():
        valid = [stock_perf[t] for t in tks if t in stock_perf and stock_perf[t]]
        if not valid:
            continue
        agg = {"count": len(valid)}
        for pk in ["1d", "1w", "1m", "3m", "6m", "9m", "12m"]:
            vals = [p[pk] for p in valid if p.get(pk) is not None]
            agg[pk] = round(float(np.mean(vals)), 2) if vals else None
        perf[th] = agg
    groups = {k: v for k, v in groups.items() if k in perf}
    rs = calc_sector_rs(perf, groups, stock_rsms, "rsms")
    return groups, perf, rs

# ─────────────────────────────────────────────
# LAB REPORT (Leaders board): iki saat — günlük (21 EMA) ve haftalık (10 haftalık SMA), ATR cinsinden
# ─────────────────────────────────────────────
def _lab_series(recs):
    """OHLC → EMA21, ATR14 (Wilder), 10 haftalık SMA (son hafta = güncel kapanış) serileri."""
    n = len(recs)
    if n < 90:
        return None
    h = np.array([r["h"] for r in recs], dtype=float)
    l = np.array([r["l"] for r in recs], dtype=float)
    c = np.array([r["c"] for r in recs], dtype=float)
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    ema = pd.Series(c).ewm(span=21, adjust=False).mean().to_numpy()
    iso = pd.to_datetime([r["d"] for r in recs]).isocalendar()
    wk = (iso["year"].to_numpy().astype(int) * 100 + iso["week"].to_numpy().astype(int))
    wid = np.zeros(n, dtype=int)
    for i in range(1, n):
        wid[i] = wid[i - 1] + (1 if wk[i] != wk[i - 1] else 0)
    wclose = np.zeros(wid[-1] + 1)
    for i in range(n):
        wclose[wid[i]] = c[i]           # haftanın son kapanışı
    sma = np.full(n, np.nan)
    for i in range(n):
        k = wid[i]
        if k >= 9:
            sma[i] = (wclose[k - 9:k].sum() + c[i]) / 10.0
    return {"c": c, "ema": ema, "atr": atr, "sma": sma}

def _lab_state(S, back=0):
    """back gün öncesi için mesafeler + 7 durumdan biri. Veri yetersizse None."""
    i = len(S["c"]) - 1 - back
    if i < 30 or np.isnan(S["sma"][i]) or S["atr"][i] <= 0:
        return None
    c, atr = S["c"][i], S["atr"][i]
    dD = (c - S["ema"][i]) / atr
    dW = (c - S["sma"][i]) / atr
    rising = bool(S["ema"][i] > S["ema"][i - 5] and S["sma"][i] > S["sma"][i - 5])
    if dW < 0:
        st = "lost"
    else:
        ext = dW > 4
        if dD < -0.5:
            st = "unwinding" if ext else "under"
        elif dD <= 1:
            st = "buy_ext" if ext else "buy"
        else:
            st = "ext_both" if ext else "above"
    c3 = (c / S["c"][i - 3] - 1) * 100
    return {"dD": round(float(dD), 2), "dW": round(float(dW), 2), "st": st,
            "rising": rising, "c3": round(float(c3), 2)}

def _ratio_z(a_recs, b_recs, win=63):
    """a/b oranının son `win` günlük ortalamasına göre z-skoru (σ)."""
    if not a_recs or not b_recs:
        return None
    da = {r["d"]: r["c"] for r in a_recs}
    db = {r["d"]: r["c"] for r in b_recs}
    ds = sorted(set(da) & set(db))
    ratio = np.array([da[d] / db[d] for d in ds if db[d]], dtype=float)
    if len(ratio) < win + 5:
        return None
    w = ratio[-win:]
    sd = w.std(ddof=1)
    if not sd or np.isnan(sd):
        return None
    return round(float((ratio[-1] - w.mean()) / sd), 2)

def build_lab(stock_ohlc, stock_rsms, stock_perf, csv_meta, meta_cache, ohlc_cache,
              spy_closes, spy_dates, topn=50):
    """Lab Report payload'u. Lider = RS-MS'e göre ilk `topn` hisse."""
    if not spy_dates or not spy_closes:
        return None
    series = {}
    for t, recs in stock_ohlc.items():
        S = _lab_series(recs)
        if S:
            series[t] = S
    cur = {t: _lab_state(S, 0) for t, S in series.items()}
    cur = {t: s for t, s in cur.items() if s}
    if not cur:
        return None

    def theme_of(t):
        return (csv_meta.get(t) or {}).get("theme", "")

    def ind_of(t):
        return (csv_meta.get(t) or {}).get("industry") or (meta_cache.get(t) or {}).get("industry", "")

    ranked = sorted([t for t in cur if t in stock_rsms],
                    key=lambda t: (-stock_rsms[t], -((stock_perf.get(t) or {}).get("3m") or 0), t))
    lead = ranked[:topn]
    leaders = []
    for t in lead:
        s = cur[t]
        leaders.append({"t": t, "rs": stock_rsms[t], "st": s["st"], "dD": s["dD"], "dW": s["dW"],
                        "rising": s["rising"], "c": (stock_perf.get(t) or {}).get("last"),
                        "theme": theme_of(t), "ind": ind_of(t)})

    prv = {t: _lab_state(series[t], 1) for t in lead}

    def is_setup(s):
        return bool(s) and s["st"] == "buy" and s["rising"]

    def is_weak(s):
        return bool(s) and s["st"] == "above" and s["rising"] and s["dD"] <= 2.5 and s["c3"] < 0

    buy_now  = [t for t in lead if is_setup(cur[t])]
    buy_prev = [t for t in lead if is_setup(prv.get(t))]
    weak_now  = [t for t in lead if is_weak(cur[t])]
    weak_prev = [t for t in lead if is_weak(prv.get(t))]
    setups = {
        "buy": buy_now, "weak": weak_now,
        "dagger": [t for t in lead if cur[t]["st"] == "buy" and not cur[t]["rising"]],
        "buy_added": [t for t in buy_now if t not in buy_prev],
        "buy_removed": [t for t in buy_prev if t not in buy_now],
        "weak_added": [t for t in weak_now if t not in weak_prev],
        "weak_removed": [t for t in weak_prev if t not in weak_now],
    }
    states = {k: [t for t in lead if cur[t]["st"] == k] for k, _, _ in LAB_STATE_DEFS}
    counts = {
        "buy": len(states["buy"]),
        "above_sma": len([t for t in lead if cur[t]["st"] != "lost"]),
        "stretched": len([t for t in lead if cur[t]["st"] in ("buy_ext", "ext_both", "unwinding")]),
    }
    above21_pct = round(100.0 * sum(1 for s in cur.values() if s["dD"] > 0) / len(cur))

    # Theme kümeleri (liderler içinde)
    by_theme = {}
    for x in leaders:
        by_theme.setdefault(x["theme"] or "No theme", []).append(x["t"])
    cl = sorted(by_theme.items(),
                key=lambda kv: (-len(kv[1]), -sum(1 for t in kv[1] if cur[t]["st"] == "buy"), kv[0]))
    multi = [(k, v) for k, v in cl if len(v) >= 2 and k != "No theme"][:10]
    clusters = [{"theme": k, "n": len(v), "buy": sum(1 for t in v if cur[t]["st"] == "buy"), "tickers": v}
                for k, v in multi]
    clusters_other = len(lead) - sum(len(v) for _, v in multi)

    # Önde giden temalar: theme RS-MS medyanı, 8 hafta öncesiyle
    cut = 40
    rs_then = {}
    if len(spy_closes) > cut + 127:
        then_map = {t: recs[:-cut] for t, recs in stock_ohlc.items() if len(recs) > cut + 127}
        rs_then = calc_rsms(then_map, spy_closes[:-cut], spy_dates[:-cut])
    members = {}
    for t in stock_ohlc:
        th = theme_of(t)
        if th:
            members.setdefault(th, []).append(t)
    leading = []
    for th, tks in members.items():
        now = [stock_rsms[t] for t in tks if t in stock_rsms]
        if len(now) < 3:
            continue
        then = [rs_then[t] for t in tks if t in rs_then]
        leading.append({"theme": th, "n": len(now), "now": round(float(np.median(now))),
                        "then": round(float(np.median(then))) if len(then) >= 3 else None})
    leading.sort(key=lambda x: -x["now"])
    leading = leading[:12]

    # Sektör sıralaması (eşit ağırlıklı RSP* ETF'leri: 1A ve 3A ortalaması)
    sectors = []
    for etf, name in LAB_SECTOR_ETFS:
        recs = ohlc_cache.get(etf)
        if not recs or len(recs) < 65:
            continue
        c = [r["c"] for r in recs]
        r1 = (c[-1] / c[-22] - 1) * 100
        r3 = (c[-1] / c[-64] - 1) * 100
        sectors.append({"etf": etf, "name": name, "r1": round(r1, 1), "r3": round(r3, 1),
                        "avg": round((r1 + r3) / 2, 2)})
    sectors.sort(key=lambda x: -x["avg"])

    # Concentration test
    pairs = [("QQQ/SPY", "QQQ", "SPY", "Big tech ahead", "Big tech behind", +1),
             ("SMH/SPY", "SMH", "SPY", "Chips ahead", "Chips behind", +1),
             ("RSP/SPY", "RSP", "SPY", "Average stock ahead", "Average stock behind", -1),
             ("IWM/SPY", "IWM", "SPY", "Small caps ahead", "Small caps behind", -1),
             ("IWF/IWD", "IWF", "IWD", "Growth ahead", "Value ahead", +1)]
    conc, hits = [], 0
    for name, a, b, up, dn, conc_sign in pairs:
        z = _ratio_z(ohlc_cache.get(a), ohlc_cache.get(b))
        if z is None:
            continue
        label = up if z > 0.5 else (dn if z < -0.5 else "In line")
        if z * conc_sign > 0.5:
            hits += 1
        conc.append({"pair": name, "z": z, "label": label})
    if not conc:
        conc_result = None
    elif hits >= 4:
        conc_result = "Highly concentrated"
    elif hits == 3:
        conc_result = "Moderately concentrated"
    else:
        conc_result = "Broad participation"

    return {"asof": spy_dates[-1], "n_universe": len(cur), "n_leaders": len(lead),
            "leaders": leaders, "states": states, "state_defs": LAB_STATE_DEFS,
            "counts": counts, "above21_pct": above21_pct, "setups": setups,
            "clusters": clusters, "clusters_other": clusters_other, "leading": leading,
            "sectors": sectors, "conc": conc, "conc_result": conc_result,
            "conc_hits": hits}

# ─────────────────────────────────────────────
# MARKET OVERVIEW + MARKET INTERNALS (breadth, McClellan, makro)
# ─────────────────────────────────────────────
SP600_URL     = "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"
MEMBERS_CACHE = os.path.join(BASE_DIR, "us_sector_members.json")   # endeks üyeleri + GICS sektör (haftalık yenilenir)
MACRO_CACHE   = os.path.join(BASE_DIR, "us_sector_macro.json")     # makro semboller, 5 yıllık günlük OHLC

# Market tanımları: (anahtar, ad, ETF/ticker, alt başlık, renk)
_UNI_PARTS = {}      # get_universe() sırasında alınan endeks listeleri (tekrar Wikipedia isteği atmamak için)
BREADTH_DEBUG = os.path.join(BASE_DIR, "us_sector_breadth_debug.csv")   # son 45 gün: adv/dec/net/MCO/MCSI (siteyle karşılaştırma için)
MO_MARKETS = [("NDX",  "Nasdaq 100",   "QQQE", "NASDAQ 100", "#CB4B16"),
              ("SPX",  "S&P 500",      "RSP",  "S&P 500",    "#268BD2"),
              ("NYSE", "NYSE",         "NYSE", "NYSE",       "#D33682"),
              ("RUT",  "Russell 2000", "IWM",  "Russell 2K", "#6C71C4")]

# Makro semboller (Yahoo)
MO_MACRO = ["CL=F", "^VIX", "SHY", "HYG", "XLK", "XLP", "GC=F", "BTC-USD", "QQQE", "RSP", "^NYA", "IWM",
            "IWF", "IWD", "^MOVE", "^TNX", "DX-Y.NYB", "HG=F", "SI=F", "NG=F"]

_GICS_MAP = {"Information Technology": "Technology", "Health Care": "Healthcare",
             "Consumer Discretionary": "Consumer Cyclical", "Consumer Staples": "Consumer Defensive",
             "Materials": "Basic Materials", "Financials": "Financial Services"}

def _parse_members(html_text, lo, hi):
    """Wikipedia tablosu → {ticker: sektör}."""
    for t in pd.read_html(_io.StringIO(html_text)):
        cols = [str(c) for c in t.columns]
        tcol = next((c for c in cols if c.lower() in ("ticker", "symbol")), None)
        scol = next((c for c in cols if "gics sector" in c.lower()), None)
        if tcol and lo < len(t) < hi:
            out = {}
            for i in range(len(t)):
                tk = str(t[tcol].iloc[i]).replace(".", "-").strip().upper()
                sc = str(t[scol].iloc[i]).strip() if scol else ""
                if tk and tk != "NAN":
                    out[tk] = _GICS_MAP.get(sc, sc)
            return out
    return {}

def get_index_members():
    """S&P 500 / Nasdaq 100 / S&P 400 / S&P 600 üyeleri + GICS sektörü. 7 gün cache'lenir."""
    cache = load_json(MEMBERS_CACHE)
    idx = cache.get("idx", {}) if isinstance(cache, dict) else {}
    today = date.today()
    try:
        age = (today - datetime.strptime(cache.get("d", "2000-01-01"), "%Y-%m-%d").date()).days
    except Exception:
        age = 999
    if age < 7 and all(idx.get(k) for k in ("SPX", "NDX", "MID", "SML")):
        return idx
    specs = [("SPX", SP500_URL, 400, 600), ("NDX", NDX_URL, 50, 150),
             ("MID", SP400_URL, 300, 450), ("SML", SP600_URL, 500, 700)]
    for k, url, lo, hi in specs:
        got = {}
        if _UNI_PARTS.get(k):                    # evren çekilirken zaten alınmış → ikinci istek yok
            got = dict(_UNI_PARTS[k])
        else:
            for attempt in range(3):             # tek seferlik hata (HTTP/limit) yüzünden market düşmesin
                try:
                    h = _http_get(url)
                    got = _parse_members(h, lo, hi) if h else {}
                except Exception as e:
                    log(f"  Üye listesi hata ({k}): {e}")
                if got:
                    break
                time.sleep(2.0 * (attempt + 1))
        if got:
            idx[k] = got
            log(f"  Üyeler {k:4}     : {len(got)} ticker")
        else:
            log(f"  ⚠️  {k} üye listesi alınamadı" + (" — cache'teki liste kullanılıyor" if idx.get(k) else ""))
    if any(idx.values()):
        save_json(MEMBERS_CACHE, {"d": today.strftime("%Y-%m-%d"), "idx": idx})
    return idx

def update_macro_cache(symbols):
    """Makro semboller: her çalıştırmada 5 yıllık geçmiş baştan çekilir (az sayıda sembol)."""
    old = load_json(MACRO_CACHE)
    out = {}
    def f(s):
        df = _fetch_history(s, period="5y")
        return s, (df_to_ohlc_recs(df) if df is not None else [])
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
        for s, recs in ex.map(f, symbols):
            out[s] = recs if recs else old.get(s, [])
            if not recs:
                log(f"  ⚠️  Makro veri alınamadı: {s}" + (" (eski cache)" if out[s] else ""))
    save_json(MACRO_CACHE, out)
    return out

# ---------- yardımcılar ----------
def _lst(s, n=None, nd=2):
    """pandas Series → liste (NaN → None)."""
    if n:
        s = s.iloc[-n:]
    return [None if (v is None or pd.isna(v)) else round(float(v), nd) for v in s.tolist()]

def _zs(x, win=252, minp=60):
    m = x.rolling(win, min_periods=minp).mean()
    sd = x.rolling(win, min_periods=minp).std()
    return (x - m) / sd.replace(0, np.nan)

def _closes(recs):
    return pd.Series([r["c"] for r in recs], index=[r["d"] for r in recs])

def _regime(c):
    """Kural tabanlı piyasa durumu: RALLY / UPTREND ATTEMPT / CORRECTION (21 EMA ve 50 SMA'ya göre)."""
    s = pd.Series(c)
    if len(s) < 60:
        return "UPTREND ATTEMPT"
    e21 = s.ewm(span=21, adjust=False).mean().iloc[-1]
    s50 = s.rolling(50).mean().iloc[-1]
    px = s.iloc[-1]
    if px > e21 and px > s50 and e21 > s50:
        return "RALLY"
    if px < e21 and px < s50:
        return "CORRECTION"
    return "UPTREND ATTEMPT"

def _ma_signal(c, kind):
    """21 günlük SMA'ya göre konum + eğim → (Above/Below, up/down, Bullish/Bearish/Neutral)."""
    s = pd.Series(c)
    ma = s.rolling(21).mean()
    above = bool(s.iloc[-1] > ma.iloc[-1])
    up = bool(ma.iloc[-1] > ma.iloc[-6])
    if kind == "credit":      # SHY/HYG yükseliyor = spread genişliyor = risk-off
        sig = "Bearish" if (above and up) else ("Bullish" if (not above and not up) else "Neutral")
    elif kind == "vix":
        sig = "Bearish" if (above and up) else ("Bullish" if (not above and not up) else "Neutral")
    else:                     # bitcoin
        sig = "Bullish" if (above and up) else ("Bearish" if (not above and not up) else "Neutral")
    return above, up, sig

def _pct(c, n):
    return (c[-1] / c[-1 - n] - 1) * 100 if len(c) > n and c[-1 - n] else 0.0

def _ratio_recs(a, b):
    """İki OHLC serisinin oranı (tarih kesişimi). TradingView gibi alan alan bölünür."""
    db = {r["d"]: r for r in b}
    out = []
    for r in a:
        q = db.get(r["d"])
        if not q or not q["o"] or not q["h"] or not q["l"] or not q["c"]:
            continue
        o, h, l, c = r["o"] / q["o"], r["h"] / q["h"], r["l"] / q["l"], r["c"] / q["c"]
        out.append({"d": r["d"], "o": o, "h": max(o, h, l, c), "l": min(o, h, l, c), "c": c})
    return out

# ---------- MARKET OVERVIEW ----------
def build_market_overview(ohlc_cache, members, exchange_map, macro, meta_cache=None):
    spy = ohlc_cache.get("SPY")
    if not spy or not members or len(spy) < 120:
        return None
    cal = [r["d"] for r in spy]
    last_dt = datetime.strptime(cal[-1], "%Y-%m-%d")

    sect = {}
    for k in ("SPX", "NDX", "MID", "SML"):
        for t, s in (members.get(k) or {}).items():
            sect.setdefault(t, s)
    mem = {"NDX": set(members.get("NDX") or {}), "SPX": set(members.get("SPX") or {}),
           "RUT": set(members.get("SML") or {})}
    big = set(members.get("SPX") or {}) | set(members.get("MID") or {}) | set(members.get("NDX") or {})
    mem["NYSE"] = {t for t in big if exchange_map.get(t) == "NYSE"}
    if meta_cache:                                   # GICS sektörü olmayanlara Yahoo sektörü
        for t in set().union(*mem.values()):
            if not sect.get(t):
                s = (meta_cache.get(t) or {}).get("sector")
                if s:
                    sect[t] = s

    allt = set().union(*mem.values())
    cols, new_hi, new_lo = {}, {}, {}
    for t in allt:
        recs = ohlc_cache.get(t)
        if not recs or len(recs) < 60:
            continue
        if (last_dt - datetime.strptime(recs[-1]["d"], "%Y-%m-%d")).days > 4:
            continue
        cols[t] = _closes(recs)
        if len(recs) >= 200:
            w = recs[-252:]
            new_hi[t] = recs[-1]["h"] >= max(r["h"] for r in w)
            new_lo[t] = recs[-1]["l"] <= min(r["l"] for r in w)
    if len(cols) < 100:
        return None
    C = pd.DataFrame(cols).reindex(cal).ffill(limit=3)
    R = C.pct_change(fill_method=None)
    ADV, DEC = (R > 1e-9), (R < -1e-9)
    MAS = {"5 SMA": C.rolling(5).mean(), "10 EMA": C.ewm(span=10, adjust=False, min_periods=10).mean(),
           "21 EMA": C.ewm(span=21, adjust=False, min_periods=21).mean(), "50 SMA": C.rolling(50).mean()}
    MA200 = C.rolling(200).mean()      # sadece LT Health kutusu için (son değer yeterli)
    V200 = MA200.notna() & C.notna()
    A200 = (C > MA200) & V200
    VALID = {k: v.notna() & C.notna() for k, v in MAS.items()}
    ABOVE = {k: (C > v) & VALID[k] for k, v in MAS.items()}

    W = 40                       # EMA ısınma süresi (gösterilmez)
    mk_out, gdb_s, mcs, ext = [], {}, {}, {}
    diag, dbg = [], []
    last_adv, last_hl, med = {}, {}, {}
    sector_stats = {}
    for key, name, etf, sub, colr in MO_MARKETS:
        tk = [t for t in mem[key] if t in C.columns]
        diag.append((name, len(tk), len(mem[key])))
        if len(tk) < 20:
            log(f"  ⚠️  Breadth: {name} atlandı — {len(mem[key])} üyeden yalnızca {len(tk)} hissede fiyat verisi var")
            continue
        a, d = ADV[tk].sum(axis=1), DEC[tk].sum(axis=1)
        net = (a - d).astype(float)
        netpct = ((a - d) / (a + d).replace(0, np.nan) * 100)
        gdb_s[key] = (netpct * 1.5).clip(-100, 100)
        mco = net.fillna(0).ewm(span=19, adjust=False).mean() - net.fillna(0).ewm(span=39, adjust=False).mean()
        mco = mco.iloc[W:]
        mcsi = mco.cumsum()
        mcs[key] = {"mco": mco, "mcsi": mcsi, "sma": mcsi.rolling(10).mean(),
                    "z_mco": _zs(mco), "z_mcsi": _zs(mcsi)}
        dbg.append(pd.DataFrame({"market": key, "adv": a, "dec": d, "net": a - d})
                   .join(pd.DataFrame({"mco": mco, "mcsi": mcsi, "z_mco": mcs[key]["z_mco"], "z_mcsi": mcs[key]["z_mcsi"]}))
                   .tail(45).round(2).reset_index().rename(columns={"index": "date"}))
        ext[key] = {}
        for mk in MAS:
            ext[key][mk] = (ABOVE[mk][tk].sum(axis=1) / VALID[mk][tk].sum(axis=1).replace(0, np.nan) * 100)
        last_adv[key] = {"adv": int(a.iloc[-1]), "dec": int(d.iloc[-1]),
                         "net": round(float(netpct.iloc[-1]), 1) if pd.notna(netpct.iloc[-1]) else 0.0}
        nh = sum(1 for t in tk if new_hi.get(t)); nl = sum(1 for t in tk if new_lo.get(t))
        last_hl[key] = {"hi": nh, "lo": nl, "net": round((nh - nl) / (nh + nl) * 100, 1) if (nh + nl) else 0.0}
        med[key] = round(float(R[tk].iloc[-1].median() * 100), 2)
        # sektör dağılımı (son gün getirileri)
        by = {}
        for t in tk:
            r = R[t].iloc[-1]
            if pd.notna(r) and sect.get(t):
                by.setdefault(sect[t], []).append(float(r * 100))
        st = {}
        for s, v in by.items():
            if len(v) >= 3:
                p = np.percentile(v, [5, 25, 50, 75, 95])
                st[s] = {"n": len(v), "mean": round(float(np.mean(v)), 2), "lo": round(float(p[0]), 2),
                         "q1": round(float(p[1]), 2), "med": round(float(p[2]), 2),
                         "q3": round(float(p[3]), 2), "hi": round(float(p[4]), 2)}
        sector_stats[key] = st
        mk_out.append({"k": key, "name": name, "etf": etf, "sub": sub, "color": colr, "n": len(tk)})
    if len(mk_out) < 2:
        return None
    keys = [m["k"] for m in mk_out]
    log("  Breadth evreni : " + " · ".join(f"{n} {h}/{t}" for n, h, t in diag))

    # Bütünsel kutular (tüm evren)
    tk_all = [t for t in allt if t in C.columns]
    a_all, d_all = int(ADV[tk_all].iloc[-1].sum()), int(DEC[tk_all].iloc[-1].sum())
    nh_all = sum(1 for t in tk_all if new_hi.get(t)); nl_all = sum(1 for t in tk_all if new_lo.get(t))
    lt = A200[tk_all].sum(axis=1) / V200[tk_all].sum(axis=1).replace(0, np.nan) * 100
    lt_now = float(lt.iloc[-1]) if pd.notna(lt.iloc[-1]) else None
    lt_prev = float(lt.iloc[-6]) if pd.notna(lt.iloc[-6]) else lt_now
    lt_tr = "Improving" if (lt_now is not None and lt_now - lt_prev >= 1) else ("Weakening" if (lt_now is not None and lt_now - lt_prev <= -1) else "Stable")
    sec_pos, sec_n = 0, 0
    by_sec = {}
    for t in tk_all:
        r = R[t].iloc[-1]
        if pd.notna(r) and sect.get(t):
            by_sec.setdefault(sect[t], []).append(float(r))
    for s, v in by_sec.items():
        if len(v) >= 3:
            sec_n += 1; sec_pos += 1 if np.mean(v) > 0 else 0
    avg_gdb = float(np.mean([gdb_s[k].iloc[-1] for k in keys if pd.notna(gdb_s[k].iloc[-1])]))
    net_all = (a_all - d_all) / (a_all + d_all) * 100 if (a_all + d_all) else 0.0
    tiles = {"sent": round(net_all, 1), "net_adv": a_all - d_all, "adv": a_all, "dec": d_all,
             "ext_net": nh_all - nl_all, "hi": nh_all, "lo": nl_all,
             "lt": round(lt_now) if lt_now is not None else None, "lt_tr": lt_tr,
             "mom": round(avg_gdb), "sec_pos": sec_pos, "sec_n": sec_n}

    # Üst kartlar
    def md(sym):
        return macro.get(sym) or []
    cards = []
    for key, name, etf, sub, colr in MO_MARKETS:
        if key not in keys:
            continue
        sym = {"NDX": "QQQE", "SPX": "RSP", "NYSE": "^NYA", "RUT": "IWM"}[key]
        title = {"NDX": "Equal Weight Nasdaq 100", "SPX": "Equal Weight S&P 500",
                 "NYSE": "NYSE Composite Index", "RUT": "Small Cap Index"}[key]
        recs = md(sym)
        if len(recs) < 60:
            continue
        c = [r["c"] for r in recs]
        cards.append({"k": key, "title": title, "sym": sym, "price": round(c[-1], 2),
                      "chg": round(_pct(c, 1), 2), "regime": _regime(c),
                      "gdb": int(round(float(gdb_s[key].iloc[-1]))), "med": med[key]})
    macro_cards = []
    shy, hyg = md("SHY"), md("HYG")
    ratio = _ratio_recs(shy, hyg)
    for kind, title, sym, recs, nd in [("credit", "Credit Spread", "SHY/HYG", ratio, 4),
                                       ("vix", "VIX", "^VIX", md("^VIX"), 4),
                                       ("btc", "Bitcoin", "BTCUSD", md("BTC-USD"), 2)]:
        if len(recs) < 40:
            continue
        c = [r["c"] for r in recs]
        above, up, sig = _ma_signal(c, kind)
        macro_cards.append({"kind": kind, "title": title, "sym": sym, "val": round(c[-1], nd),
                            "chg": round(_pct(c, 1), 2), "sig": sig, "above": above, "up": up})

    # GDB zaman serisi + göstergeler
    avail = len(cal) - W - 1
    N_G = min(90, avail)
    gdb_last = {k: int(round(float(gdb_s[k].iloc[-1]))) for k in keys}
    gdb = {"dates": cal[-N_G:], "series": {k: _lst(gdb_s[k], N_G, 1) for k in keys}, "last": gdb_last,
           "avg": round(avg_gdb, 1)}

    # McClellan Standard
    N_S, N_N = min(190, avail), min(130, avail)
    mc = {}
    for k in keys:
        s = mcs[k]
        cur, w1, m1 = float(s["mco"].iloc[-1]), float(s["mco"].iloc[-1] - s["mco"].iloc[-6]), float(s["mco"].iloc[-1] - s["mco"].iloc[-22])
        mc[k] = {"dates": cal[-N_S:], "mcsi": _lst(s["mcsi"], N_S), "sma": _lst(s["sma"], N_S), "mco": _lst(s["mco"], N_S),
                 "cur": int(round(cur)), "w1": int(round(w1)), "m1": int(round(m1)),
                 "trend": "up" if w1 > 0 else "down", "signal": "Bullish" if cur > 0 else "Bearish"}
    # McClellan Normalized (252 günlük z-skor)
    mcn = {"dates": cal[-N_N:], "mcsi": {}, "mco": {}, "mcsi_sma": {}, "mco_sma": {}, "summary": []}
    n_bull = n_imp = 0
    for k in keys:
        s = mcs[k]
        mcn["mcsi"][k] = _lst(s["z_mcsi"], N_N); mcn["mco"][k] = _lst(s["z_mco"], N_N)
        mcn["mcsi_sma"][k] = _lst(s["z_mcsi"].rolling(10).mean(), N_N); mcn["mco_sma"][k] = _lst(s["z_mco"].rolling(10).mean(), N_N)
        zc, zm = float(s["z_mcsi"].iloc[-1]), float(s["z_mco"].iloc[-1])
        zprev = float(s["z_mco"].iloc[-6])
        up = zm > zprev
        sig = "Improving" if up else ("Bearish" if zm < 0 else "Weakening")
        n_bull += 1 if mc[k]["signal"] == "Bullish" else 0
        n_imp += 1 if up else 0
        mcn["summary"].append({"k": k, "mcsi": round(zc, 2), "mco": round(zm, 2), "trend": "up" if up else "down", "signal": sig})
    nk = len(keys)
    mcn["n_bull"], mcn["n_imp"], mcn["n"] = n_bull, n_imp, nk
    frac = n_bull / nk
    mcn["label"] = ("Bullish" if frac == 1 else "Mostly Bullish" if frac >= 0.75 else "Mixed" if frac >= 0.5
                    else "Mostly Bearish" if frac > 0 else "Bearish")

    # Breadth Extensions (Standard + Normalized; 5 SMA, 10 EMA, 21 EMA, 50 SMA)
    N_E = min(130, avail)
    ex = {"dates": cal[-N_E:], "std": {}, "norm": {}, "last": {}}
    for k in keys:
        ex["std"][k] = {mk: _lst(v, N_E, 1) for mk, v in ext[k].items()}
        ex["norm"][k] = {mk: _lst(_zs(v.iloc[W:]), N_E) for mk, v in ext[k].items()}
        ex["last"][k] = {mk: (None if pd.isna(v.iloc[-1]) else round(float(v.iloc[-1]), 1)) for mk, v in ext[k].items()}

    return {"asof": cal[-1], "markets": mk_out, "cards": cards, "macro_cards": macro_cards, "tiles": tiles,
            "gdb": gdb, "adv": last_adv, "hl": last_hl,
            "adv_total": round(net_all, 1), "hl_total": round((nh_all - nl_all) / (nh_all + nl_all) * 100, 1) if (nh_all + nl_all) else 0.0,
            "mc": mc, "mcn": mcn, "ext": ex, "dbg": (pd.concat(dbg) if dbg else None), "sect": sector_stats, "universe": len(tk_all)}

# ---------- MARKET INTERNALS ----------
MI_CARDS = [  # (id, başlık, alt başlık, sembol etiketi, renk, tip, kaynak)
    ("crude",  "Crude Oil",    "WTI Crude Oil futures",                          "CLUSD",   "#CB4B16", "",      ("CL=F",)),
    ("vix",    "VIX",          "CBOE Volatility Index",                          "^VIX",    "#DC322F", "",      ("^VIX",)),
    ("credit", "Credit Spreads", "SHY/HYG — rising = widening spreads (risk-off)", "SHY/HYG", "#6C71C4", "RATIO", ("SHY", "HYG")),
    ("tech",   "Tech vs Staples", "XLK/XLP — rising = tech outperforming",        "XLK/XLP", "#268BD2", "RATIO", ("XLK", "XLP")),
    ("gold",   "Gold",         "Gold futures",                                   "GCUSD",   "#B58900", "",      ("GC=F",)),
    ("btc",    "Bitcoin",      "BTC/USD",                                        "BTCUSD",  "#2AA198", "",      ("BTC-USD",)),
]
MI_EXTRAS = [
    ("move",   "MOVE Index",    "ICE BofA bond volatility index",   "^MOVE",   "#CB4B16", "",      ("^MOVE",)),
    ("tnx",    "US 10Y Yield",  "CBOE 10-year Treasury yield",      "^TNX",    "#6C71C4", "",      ("^TNX",)),
    ("dxy",    "US Dollar",     "US Dollar Index",                  "DXY",     "#268BD2", "",      ("DX-Y.NYB",)),
    ("copper", "Copper",        "Copper futures",                   "HGUSD",   "#B58900", "",      ("HG=F",)),
    ("silver", "Silver",        "Silver futures",                   "SIUSD",   "#2AA198", "",      ("SI=F",)),
    ("natgas", "Natural Gas",   "Natural gas futures",              "NGUSD",   "#DC322F", "",      ("NG=F",)),
    ("growth", "Growth vs Value", "IWF/IWD — rising = growth outperforming", "IWF/IWD", "#D33682", "RATIO", ("IWF", "IWD")),
]

def _mi_series(spec, macro):
    src = spec[6]
    if len(src) == 2:
        recs = _ratio_recs(macro.get(src[0]) or [], macro.get(src[1]) or [])
    else:
        recs = macro.get(src[0]) or []
    if len(recs) < 30:
        return None
    nd = 4 if spec[5] == "RATIO" else (2 if recs[-1]["c"] > 50 else 3)
    return {"id": spec[0], "title": spec[1], "sub": spec[2], "sym": spec[3], "color": spec[4], "tag": spec[5],
            "d": [r["d"] for r in recs], "o": [round(r["o"], nd) for r in recs], "h": [round(r["h"], nd) for r in recs],
            "l": [round(r["l"], nd) for r in recs], "c": [round(r["c"], nd) for r in recs], "nd": nd}

def _mi_ai(macro):
    """Kural tabanlı AI Insights benzeri yorum (LLM yok): etiket + paragraf + 3 başlık."""
    def cl(sym):
        return [r["c"] for r in (macro.get(sym) or [])]
    vix, crude, gold, btc = cl("^VIX"), cl("CL=F"), cl("GC=F"), cl("BTC-USD")
    move, xlk, xlp, shy, hyg = cl("^MOVE"), cl("XLK"), cl("XLP"), cl("SHY"), cl("HYG")
    iwf, iwd = cl("IWF"), cl("IWD")
    if len(vix) < 30 or len(crude) < 30 or len(gold) < 30:
        return None
    tech = [a / b for a, b in zip(xlk[-len(xlp):], xlp[-len(xlk):])] if xlk and xlp else []
    cred = [a / b for a, b in zip(shy[-len(hyg):], hyg[-len(shy):])] if shy and hyg else []
    sents, tags, on = [], [], 0
    v1d, v1m = _pct(vix, 1), _pct(vix, 21)
    vix_state = ("vol sellers are firmly in control" if (vix[-1] < 17 and v1m < 0) else
                 "hedging demand is building" if (vix[-1] > 22 or v1m > 15) else "volatility is range-bound")
    sents.append(f"VIX at {vix[-1]:.2f} ({v1d:+.2f}% today, {v1m:+.2f}% on the month) says {vix_state}.")
    vabove, vup, _ = _ma_signal(vix, "vix")
    if not vabove:
        on += 1; tags.append("VIX BELOW 21-DAY MA")
    else:
        tags.append("VIX ABOVE 21-DAY MA")
    n_sig = 1
    if len(cred) > 30:
        c1w = _pct(cred, 5)
        sents.append(f"Credit is {'widening' if c1w > 0.15 else 'tightening' if c1w < -0.15 else 'quiet'} "
                     f"(SHY/HYG {c1w:+.2f}% on the week)" +
                     (f", while MOVE is {_pct(move, 21):+.2f}% on the month." if len(move) > 30 else "."))
        cab, cup, csig = _ma_signal(cred, "credit")
        n_sig += 1
        if csig != "Bearish":
            on += 1
        if csig == "Bearish":
            tags.append("CREDIT SPREADS WIDENING")
        elif len(move) > 30 and _pct(move, 21) > 10:
            tags.append("MOVE SPIKE VS CALM CREDIT")
    if len(tech) > 30:
        t1m, t1d = _pct(tech, 21), _pct(tech, 1)
        sents.append(f"XLK/XLP is {t1m:+.2f}% on the month ({t1d:+.2f}% today): "
                     f"{'tech leadership is intact' if t1m > 0 else 'defensives are gaining ground'}.")
        n_sig += 1
        if t1m > 0:
            on += 1
        tags.append("TECH LEADING STAPLES" if t1m > 0 else "DEFENSIVES GAINING")
    c1m, g1m = _pct(crude, 21), _pct(gold, 21)
    sents.append(f"Crude {c1m:+.2f}% and gold {g1m:+.2f}% on the month"
                 + (f"; IWF/IWD {_pct([a / b for a, b in zip(iwf[-len(iwd):], iwd[-len(iwf):])], 1):+.2f}% today." if iwf and iwd else "."))
    if c1m < -5:
        tags.append("CRUDE BREAKDOWN")
    elif c1m > 5:
        tags.append("CRUDE BREAKOUT")
    if g1m < -4:
        tags.append("GOLD SLIDING")
    elif g1m > 4:
        tags.append("GOLD BID")
    if len(btc) > 30:
        babove, bup, _s = _ma_signal(btc, "btc")
        n_sig += 1
        if babove:
            on += 1
        tags.append("BITCOIN ABOVE 21-DAY MA" if babove else "BITCOIN BELOW 21-DAY MA")
    sents.append("Watch whether VIX stays " + ("below" if not vabove else "above") + " its 21-day average and whether credit keeps confirming the equity tape.")
    ratio_on = on / max(n_sig, 1)
    label = "RISK-ON" if (ratio_on >= 0.8 and n_sig >= 3) else ("RISK-OFF" if ratio_on <= 0.34 else "MIXED")
    return {"label": label, "text": " ".join(sents), "tags": tags[:3]}

def build_market_internals(macro):
    cards = [c for c in (_mi_series(s, macro) for s in MI_CARDS) if c]
    extras = [c for c in (_mi_series(s, macro) for s in MI_EXTRAS) if c]
    if not cards:
        return None
    return {"cards": cards, "extras": extras, "ai": _mi_ai(macro)}


# ─────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"><meta http-equiv="Pragma" content="no-cache"><meta http-equiv="Expires" content="0">
<title>US Sector Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}body{background:#ffffff;color:#0f172a;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
.hd{padding:18px 28px;border-bottom:1px solid rgba(15,23,42,.06);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
.hd h1{font-size:21px;font-weight:700}.hd h1 span{color:#6366f1}.meta{color:rgba(15,23,42,.4);font-size:11px;font-family:monospace}
.main-tabs{padding:14px 28px 0;display:flex;gap:0;border-bottom:1px solid rgba(15,23,42,.08)}
.main-tab{background:0;border:none;border-bottom:2px solid transparent;color:rgba(15,23,42,.4);padding:10px 22px;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s;letter-spacing:.3px}
.main-tab.a{color:#4f46e5;border-bottom-color:#6366f1}.main-tab:hover{color:rgba(15,23,42,.7)}
.main-panel{display:none}.main-panel.a{display:block}
.tabs{padding:14px 28px;display:flex;gap:7px;flex-wrap:wrap}
.tab{background:0;border:1px solid rgba(15,23,42,.08);color:rgba(15,23,42,.5);padding:7px 15px;border-radius:7px;font-size:12px;cursor:pointer;transition:all .2s}
.tab.a{background:rgba(99,102,241,.2);border-color:rgba(99,102,241,.4);color:#4f46e5;font-weight:600}.tab:hover{border-color:rgba(15,23,42,.2)}
.ct{padding:0 28px 36px}.tc{display:none}.tc.a{display:block}
.ctr{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
.btn{background:0;border:1px solid rgba(15,23,42,.08);color:rgba(15,23,42,.5);padding:7px 15px;border-radius:7px;font-size:12px;cursor:pointer;transition:all .2s}
.btn.a{background:rgba(99,102,241,.2);border-color:rgba(99,102,241,.4);color:#4f46e5;font-weight:600}.btn:hover{border-color:rgba(15,23,42,.2)}
.cl{color:rgba(15,23,42,.4);font-size:10px;font-family:monospace;margin-right:3px;text-transform:uppercase;letter-spacing:1px}
.fv{margin-bottom:20px}.fv-row{display:flex;align-items:center;margin-bottom:3px;cursor:pointer;border-radius:4px;padding:3px 6px;transition:background .15s}
.fv-row:hover{background:rgba(15,23,42,.04)}
.fv-name{width:240px;min-width:240px;font-size:11px;font-weight:600;padding-right:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fv-bar-wrap{flex:1;height:22px;position:relative;background:rgba(15,23,42,.03);border-radius:3px;overflow:hidden}
.fv-bar{height:100%;position:absolute;top:0;border-radius:3px;transition:width .3s}
.fv-val{width:70px;min-width:70px;text-align:right;font-size:11px;font-weight:700;font-family:monospace;padding-left:8px}
.fv-rs{width:120px;min-width:120px;display:flex;gap:3px;justify-content:center;font-size:9px;font-weight:700;font-family:monospace;padding-left:6px}
.fv-rs .rb{font-size:9px;padding:1px 4px;min-width:24px}
.fv-cnt{width:35px;min-width:35px;text-align:center;font-size:9px;color:rgba(15,23,42,.3);font-family:monospace}
.tw{background:rgba(15,23,42,.02);border:1px solid rgba(15,23,42,.06);border-radius:12px;overflow:hidden;margin-bottom:18px}
.tw .th{padding:13px 16px;border-bottom:1px solid rgba(15,23,42,.06);font-weight:600;font-size:13px;display:flex;justify-content:space-between;align-items:center}
.tw .th .back{cursor:pointer;color:#6366f1;font-size:11px;display:none}
table{width:100%;border-collapse:collapse;font-size:11px;font-family:monospace}
th{padding:9px 10px;text-align:right;color:rgba(15,23,42,.4);font-size:9px;text-transform:uppercase;letter-spacing:.7px;border-bottom:1px solid rgba(15,23,42,.06);cursor:pointer;white-space:nowrap;user-select:none}
th:first-child{text-align:left}th:hover{color:rgba(15,23,42,.7)}
th .sa{font-size:8px;margin-left:2px;color:rgba(15,23,42,.25)}th.st .sa{color:#4f46e5}
td{padding:9px 10px;border-bottom:1px solid rgba(15,23,42,.03);text-align:right}
td:first-child{text-align:left;font-weight:600}
tr.sr{cursor:pointer}tr.sr:hover{background:rgba(99,102,241,.06)}
.sp{color:#16a34a}.sn{color:#dc2626}
.rb{display:inline-block;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700;min-width:28px;text-align:center}
.idx-bar{display:flex;gap:12px;margin-bottom:18px;flex-wrap:wrap}
.idx-card{background:rgba(15,23,42,.03);border:1px solid rgba(15,23,42,.06);border-radius:9px;padding:12px 16px;flex:1;min-width:140px}
.idx-name{font-size:10px;color:rgba(15,23,42,.4);font-family:monospace;text-transform:uppercase;margin-bottom:4px}
.idx-val{font-size:20px;font-weight:700}.idx-chg{font-size:11px;font-family:monospace;margin-top:2px}
.ft{padding:12px 28px;border-top:1px solid rgba(15,23,42,.06);color:rgba(15,23,42,.2);font-size:10px;font-family:monospace;display:flex;justify-content:space-between}
.modal-bg{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(15,23,42,.45);z-index:100;justify-content:center;align-items:center}
.modal-bg.show{display:flex}
.modal{background:#ffffff;border:1px solid rgba(15,23,42,.1);border-radius:14px;padding:24px;width:90%;max-width:700px;max-height:90vh;overflow-y:auto;position:relative}
.modal h2{font-size:16px;font-weight:700;margin-bottom:4px}.modal .msub{font-size:11px;color:rgba(15,23,42,.4);margin-bottom:16px;font-family:monospace}
.modal .close{position:absolute;top:12px;right:16px;font-size:20px;cursor:pointer;color:rgba(15,23,42,.4);background:none;border:none}
.modal .close:hover{color:#000}
.modal .ch{height:220px;position:relative;margin-bottom:16px}
.chart-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;margin-top:12px}
.mini-chart{background:rgba(15,23,42,.02);border:1px solid rgba(15,23,42,.06);border-radius:8px;padding:10px;cursor:pointer}
.mini-chart:hover{border-color:rgba(99,102,241,.3)}
.mini-chart .mc-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.mini-chart .mc-name{font-size:11px;font-weight:700}.mini-chart .mc-chg{font-size:10px;font-family:monospace;font-weight:600}
.mini-chart canvas{width:100%!important;height:140px!important}
.view-toggle{display:flex;gap:5px;margin-bottom:12px}
.hisse-search{background:rgba(15,23,42,.04);border:1px solid rgba(15,23,42,.1);border-radius:8px;padding:8px 14px;color:#0f172a;font-size:12px;font-family:monospace;width:260px;outline:none;transition:border-color .2s}
.hisse-search:focus{border-color:rgba(99,102,241,.5)}
.hisse-search::placeholder{color:rgba(15,23,42,.25)}
@media(max-width:768px){.ct{padding:0 12px 36px}.fv-name{width:140px;min-width:140px}.hisse-search{width:160px}}
.mo-wrap{background:#f4f4f5;padding:20px 28px 40px;min-height:80vh;font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',system-ui,sans-serif;color:#0f172a}
.mo-h{font-size:24px;font-weight:700;letter-spacing:-.3px;margin-bottom:4px}
.mo-note{font-size:10.5px;color:#9ca3af;margin-bottom:16px;font-family:'SF Mono',Menlo,monospace}
.mo-empty{padding:40px;color:#9ca3af;font-size:13px}
.mo-r7{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:10px;margin-bottom:12px}
.mo-r5{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:12px}
.mo-g21{display:grid;grid-template-columns:2fr 1fr;gap:12px;margin-bottom:12px}
.mo-g11{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
@media(max-width:1200px){.mo-r7{grid-template-columns:repeat(4,minmax(0,1fr))}.mo-r5{grid-template-columns:repeat(3,minmax(0,1fr))}.mo-g21,.mo-g11{grid-template-columns:1fr}}
.mo-c{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;border-left-width:3px}
.mo-ct{font-size:11px;color:#6b7280;margin-bottom:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mo-row{display:flex;justify-content:space-between;align-items:center;gap:6px}
.mo-sym{font-size:15px;font-weight:700}
.mo-big{font-size:21px;font-weight:600;letter-spacing:-.4px}
.mo-px{font-size:12.5px;color:#6b7280;margin:3px 0 9px}
.mo-tag{font-size:9.5px;font-weight:700;letter-spacing:.4px;padding:3px 7px;border-radius:4px;white-space:nowrap}
.mo-gdb{font-size:10.5px;color:#6b7280}.mo-gdb b{color:#0f172a;font-weight:700}
.mo-med{margin-top:10px;font-size:11px;color:#6b7280}
.mo-val{font-size:19px;font-weight:700;letter-spacing:-.3px}
.mo-ic{width:34px;height:34px;border-radius:7px;display:flex;align-items:center;justify-content:center;margin-bottom:12px}
.mo-tl{font-size:10px;letter-spacing:.8px;color:#6b7280;font-weight:600;margin-bottom:2px}
.mo-tv{font-size:24px;font-weight:700;letter-spacing:-.5px}
.mo-ts{font-size:11px;color:#6b7280;margin:3px 0 7px}
.mo-tst{font-size:11px;font-weight:500;display:flex;align-items:center;gap:4px}
.mo-p{background:#fff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden}
.mo-ph{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:13px 18px;border-bottom:1px solid #eef0f2;font-size:14px;font-weight:600;flex-wrap:wrap}
.mo-pb{padding:14px 18px}
.mo-tabs{display:flex;align-items:center;gap:2px;font-weight:400}
.mo-tabs span{padding:5px 10px;font-size:12px;color:#6b7280;border-bottom:2px solid transparent;cursor:pointer;white-space:nowrap}
.mo-tabs span.a{color:#0f172a;border-bottom-color:#0f172a;font-weight:500}
.mo-tabs i{width:1px;height:14px;background:#d1d5db;margin:0 8px}
.mo-cv{position:relative;width:100%}
.mo-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px}
.mo-tbl{width:100%;border-collapse:collapse;font-size:12px}
.mo-tbl th{font-weight:500;color:#6b7280;text-align:right;padding:8px 10px;font-size:11.5px}
.mo-tbl th:first-child,.mo-tbl td:first-child{text-align:left}
.mo-tbl td{text-align:right;padding:9px 10px;border-top:1px solid #f1f2f4}
.mo-tbl td:first-child{font-weight:500}
.mo-bdg{display:inline-block;font-size:10.5px;font-weight:500;padding:2px 8px;border-radius:4px;border:1px solid}
.mo-bdg.f{color:#fff;border-color:transparent;font-weight:600}
.mo-sec{font-size:11px;letter-spacing:.8px;font-weight:600;color:#6b7280;margin:14px 0 4px}
.mo-cap{font-size:10.5px;color:#9ca3af;text-align:center;margin:4px 0 6px}
.mo-mg{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}@media(max-width:1200px){.mo-mg{grid-template-columns:repeat(2,minmax(0,1fr))}}
.mo-lg{display:flex;gap:12px;font-size:10.5px;color:#6b7280;margin:2px 0 4px 2px;align-items:center;flex-wrap:wrap}
.lab-lead:hover{background:rgba(99,102,241,.08)}
.mo-mini{border:1px solid #eef0f2;border-radius:6px;padding:8px 8px 4px;background:#fff}
.mo-mh{display:flex;align-items:center;justify-content:space-between;font-size:13px;font-weight:600;margin-bottom:4px}
.mo-ad{margin-bottom:16px}
.mo-adh{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:6px}
.mo-adh b{font-size:13px}.mo-adh small{display:block;font-size:11px;color:#6b7280;font-weight:400}
.mo-bar{display:flex;height:20px;border-radius:3px;overflow:hidden;position:relative;background:#e5e7eb}
.mo-bar i{display:flex;align-items:center;font-style:normal;font-size:10px;font-weight:600;color:#fff;padding:0 6px;min-width:0;overflow:hidden}
.mo-bar u{position:absolute;left:50%;top:0;bottom:0;width:1px;background:rgba(255,255,255,.7)}
.mo-adf{display:flex;justify-content:space-between;font-size:10.5px;margin-top:3px}
.mo-gg{display:grid;grid-template-columns:1fr 1fr;gap:6px 10px;text-align:center;margin-top:8px}
.mo-gg .gv{font-size:17px;font-weight:700;margin-top:-30px;position:relative}
.mo-gg .gt{font-size:12.5px;font-weight:600;margin-top:2px}
.mo-gg .gs{font-size:11px;color:#6b7280}
.mo-gg .gl{font-size:11.5px;font-weight:500;margin-top:2px}
.mo-leg{display:flex;justify-content:center;gap:16px;font-size:11.5px;color:#6b7280;margin:8px 0 10px;flex-wrap:wrap}
.mo-hv{padding:9px 10px;border-top:1px solid #fff;font-family:'SF Mono',Menlo,monospace;font-size:12px}
.mo-pill{display:inline-flex;align-items:center;gap:5px;font-size:10px;font-weight:700;letter-spacing:.7px;padding:3px 9px;border-radius:12px}
.mi-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px}
.mi-ctl{display:flex;align-items:center;gap:8px;position:relative}
.mi-b{background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:8px 18px;font-size:12px;font-weight:600;cursor:pointer;color:#0f172a;font-family:inherit}
.mi-b.a{background:#111827;color:#fff;border-color:#111827}
.mi-ib{background:#fff;border:1px solid #e5e7eb;border-radius:6px;width:34px;height:34px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:#374151}
.mi-add{background:#111827;color:#fff;border:0;border-radius:6px;padding:9px 16px;font-size:11.5px;font-weight:700;letter-spacing:.6px;cursor:pointer;font-family:inherit}
.mi-menu{position:absolute;right:0;top:44px;z-index:30;background:#fff;border:1px solid #e5e7eb;border-radius:8px;box-shadow:0 10px 30px rgba(15,23,42,.14);min-width:230px;padding:6px;max-height:320px;overflow:auto}
.mi-menu div{padding:8px 10px;font-size:12px;border-radius:5px;cursor:pointer}
.mi-menu div:hover{background:#f3f4f6}.mi-menu small{color:#9ca3af;margin-left:6px;font-size:10.5px}
.mi-ai{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:16px 20px;margin-bottom:14px}
.mi-aih{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.mi-aiic{width:26px;height:26px;border-radius:6px;background:#fef3c7;display:flex;align-items:center;justify-content:center}
.mi-ait{font-size:10.5px;letter-spacing:1.4px;color:#6b7280;font-weight:600}
.mi-aip{font-size:14px;line-height:1.7;border-left:3px solid #f4a825;padding-left:14px;color:#1f2937}
.mi-aig{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 0 17px}
.mi-aig span{font-size:9.5px;letter-spacing:1.1px;font-weight:600;color:#6b7280;background:#eceef1;padding:4px 9px;border-radius:3px}
.mi-grid{display:grid;gap:14px}
.mi-card{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px 12px}
.mi-ch{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;gap:8px}
.mi-ct{font-size:14.5px;font-weight:600;display:flex;align-items:center;gap:8px}
.mi-ct small{font-size:9.5px;letter-spacing:.8px;color:#9ca3af;font-weight:500}
.mi-cs{font-size:12px;color:#6b7280;margin:3px 0 0 17px}
.mi-cr{display:flex;align-items:center;gap:8px;font-size:11px;color:#9ca3af}
.mi-v{font-family:'SF Mono',Menlo,monospace;font-weight:700;font-size:12px;padding:3px 8px;border-radius:4px}
.mi-x{cursor:pointer;color:#9ca3af;display:flex}.mi-x:hover{color:#111827}
.mi-cv{position:relative;border:1px solid #eef0f2;border-radius:4px;background:#fff}
.mi-cv canvas{display:block;width:100%}
.mi-tip{position:absolute;left:8px;top:6px;font-size:10.5px;font-family:'SF Mono',Menlo,monospace;color:#374151;background:rgba(255,255,255,.92);padding:2px 6px;border-radius:3px;pointer-events:none;display:none}
</style></head>
<body>
<div class="hd"><div><h1>US <span>Sector Dashboard</span></h1><div class="meta" id="hm"></div></div></div>
<!-- ===== TOP LEVEL TABS ===== -->
<div class="main-tabs">
<button class="main-tab a" onclick="switchMain('sektor',this)">Sectors</button>
<button class="main-tab" onclick="switchMain('hisse',this)">Stocks</button>
<button class="main-tab" onclick="switchMain('lab',this)">Lab Report</button>
<button class="main-tab" onclick="switchMain('mo',this)">Market Overview</button>
<button class="main-tab" onclick="switchMain('mi',this)">Market Internals</button>
</div>
<!-- ===== SEKTÖR PANEL ===== -->
<div id="panel-sektor" class="main-panel a">
<div class="tabs" id="mt">
<button class="tab a" onclick="sT('bars',this)">Sector Bars</button>
<button class="tab" onclick="sT('tbl',this)">Table</button>
<button class="tab" onclick="sT('themes',this);renderThemes()">Themes</button></div>
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
<div id="tab-themes" class="tc">
<div class="view-toggle" id="themeDetailBar" style="display:none">
<button class="btn a" onclick="setThemeView('table')">Table</button>
<button class="btn" onclick="setThemeView('charts')">Charts</button>
<span style="margin-left:12px;cursor:pointer;color:#6366f1;font-size:11px" onclick="showThemes()">← Back to themes</span></div>
<div id="themeListView"><div class="tw"><div class="th"><span>Themes</span><span id="thCount" style="font-size:11px;color:rgba(15,23,42,.3);font-family:monospace"></span></div>
<div style="overflow-x:auto"><table id="themeTbl"></table></div></div></div>
<div id="themeStocksView" style="display:none"><div class="tw"><div class="th"><span id="themeTitle"></span></div>
<div style="overflow-x:auto"><table id="themeStockTbl"></table></div></div></div>
<div id="themeChartView" style="display:none"><div class="chart-grid" id="themeChartGrid"></div></div>
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
<div class="tw"><div class="th"><span>All Stocks</span><span id="hisseCount" style="font-size:11px;color:rgba(15,23,42,.3);font-family:monospace"></span></div>
<div style="overflow-x:auto"><table id="hisseTbl"></table></div></div>
</div>
<div id="hisseChartWrap" style="display:none">
<div style="margin-bottom:10px"><button class="btn" id="cloudFilterBtn" onclick="toggleCloudFilter()">☁️ In Cloud</button><button class="btn" id="atrFilterBtn" onclick="toggleAtrFilter()" style="margin-left:5px">📏 Within 1 ATR of EMA</button><button class="btn" id="rs85FilterBtn" onclick="toggleRs85Filter()" style="margin-left:5px" title="RS21 ≥ 85: short/medium-term momentum (21-day return percentile, not benchmark-relative)">⚡ RS 85+</button><button class="btn" id="ms85FilterBtn" onclick="toggleMs85Filter()" style="margin-left:5px" title="RS-MS ≥ 85: long-term leaders (MarketSmith-style, excess vs SPY, weighted 3M/6M/9M/12M). Newly IPO'd stocks (< 6mo) are excluded.">🏆 MS 85+</button><span id="cloudFilterInfo" style="font-size:10px;color:rgba(15,23,42,.3);font-family:monospace;margin-left:8px"></span><button class="btn" onclick="exportTvList()" style="margin-left:12px;border-color:rgba(99,102,241,.3);color:rgba(79,70,229,.7)">📋 TV List</button></div>
<div class="chart-grid" id="hisseChartGrid"></div>
</div>
</div>
</div>
<!-- ===== MARKET OVERVIEW / INTERNALS PANELS ===== -->
<div id="panel-mo" class="main-panel"><div class="mo-wrap" id="moWrap"></div></div>
<div id="panel-mi" class="main-panel"><div class="mo-wrap" id="miWrap"></div></div>
<!-- ===== LAB REPORT PANEL ===== -->
<div id="panel-lab" class="main-panel"><div class="ct" style="padding-top:16px" id="labWrap"></div></div>
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
<div class="ft"><span id="ft"></span><span>US Sector Dashboard v3.4</span></div>
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
if(panel==='lab')renderLab();
if(panel==='mo')renderMO();
if(panel==='mi')renderMI();
if(panel==='sektor'){renderIdx();renderBars();renderTable();renderThemes()}}
// ===== SEKTÖR =====
function setView(v,btn){curView=v;document.querySelectorAll('#viewToggle .btn').forEach(b=>b.classList.remove('a'));if(btn)btn.classList.add('a');
document.getElementById('tableView').style.display=v==='table'?'block':'none';
document.getElementById('chartView').style.display=v==='charts'?'block':'none';
if(v==='charts'&&curSec)renderChartGrid()}
function destroyMiniCharts(){miniCharts.forEach(c=>c.destroy());miniCharts.length=0}
const rT={rs5:'RS5: 5-day raw return percentile rank (1-99). Short-term momentum, NOT benchmark-relative.',rs21:'RS21: 21-day raw return percentile rank (1-99). Medium-term momentum, NOT benchmark-relative. Picks up short-term winners regardless of long-term trend.',rsms:'RS-MS (MarketSmith / IBD-style): Excess return vs SPY weighted (3M x 40% + 6M x 20% + 9M x 20% + 12M x 20%), percentile rank (1-99). Long-term relative strength. Newly IPOd stocks (< 6 months data) are excluded.'};
function sT(id,b){document.querySelectorAll('.tc').forEach(t=>t.classList.remove('a'));document.querySelectorAll('#mt .tab').forEach(t=>t.classList.remove('a'));document.getElementById('tab-'+id).classList.add('a');if(b)b.classList.add('a')}
function sP(p,b){curP=p;document.querySelectorAll('#gp .btn').forEach(x=>x.classList.remove('a'));if(b)b.classList.add('a');sC=-1;barSort='perf';render()}
function pC(v){if(v==null)return'rgba(15,23,42,.08)';if(v>0)return v>8?'rgba(22,163,74,0.9)':v>4?'rgba(34,197,94,0.7)':v>2?'rgba(22,163,74,0.55)':'rgba(22,163,74,0.35)';return v<-8?'rgba(185,28,28,0.9)':v<-4?'rgba(220,38,38,0.7)':v<-2?'rgba(220,38,38,0.55)':'rgba(220,38,38,0.35)'}
function vF(v){if(v==null)return'-';return(v>0?'+':'')+v.toFixed(2)+'%'}
function vK(v){return v>0?'sp':v<0?'sn':''}
function rC(rs){if(rs==null)return['rgba(15,23,42,.06)','rgba(15,23,42,.5)'];if(rs>=80)return['rgba(22,163,74,0.22)','#166534'];if(rs>=60)return['rgba(22,163,74,0.12)','#15803d'];if(rs>=40)return['rgba(15,23,42,.05)','rgba(15,23,42,.6)'];if(rs>=20)return['rgba(220,38,38,0.12)','#b91c1c'];return['rgba(220,38,38,0.22)','#991b1b']}
function rB(v){const[bg,c]=rC(v);return'<span class="rb" style="background:'+bg+';color:'+c+'">'+(v||'-')+'</span>'}
// RS/MS rakam rengi: 90-99 açık yeşil, 85-89 gök mavisi, 85 altı turuncu
function rsColor(v){if(typeof v!=='number')return'rgba(15,23,42,.4)';if(v>=90)return'#16a34a';if(v>=85)return'#0284c7';return'#ea580c'}
// Sektör sırası rengi: 1-5 açık yeşil, 6-10 gök mavisi, 11+ turuncu
function rankColor(r){if(typeof r!=='number')return'#ea580c';if(r<=5)return'#16a34a';if(r<=10)return'#0284c7';return'#ea580c'}
function sortBar(k){barSort=k;renderBars()}
function renderIdx(){const bar=document.getElementById('idxBar');let h='';
['SPY','QQQ','IWM'].forEach(n=>{const p=R.idx_perf[n];if(!p)return;const v=p[curP];const c=v!=null?(v>0?'#16a34a':'#dc2626'):'rgba(15,23,42,.4)';
h+='<div class="idx-card"><div class="idx-name">'+n+'</div><div class="idx-val" style="color:'+c+'">'+vF(v)+'</div><div class="idx-chg" style="color:rgba(15,23,42,.4)">Last: '+(p.last||0).toLocaleString('en-US')+'</div></div>'});
if(curSec){const r5=R.sec_rs5[curSec]||'-';const r21=R.sec_rs21[curSec]||'-';const rms=R.sec_rsms[curSec]||'-';const sp=R.sector_perf[curSec];const sv=sp?sp[curP]:null;const sc=sv!=null?(sv>0?'#16a34a':'#dc2626'):'rgba(15,23,42,.4)';
h+='<div class="idx-card" style="border-color:rgba(99,102,241,.3)"><div class="idx-name" style="color:#4f46e5">'+curSec+'</div><div class="idx-val" style="color:'+sc+'">'+vF(sv)+'</div><div style="display:flex;gap:6px;margin-top:4px;align-items:center"><span style="font-size:8px;color:rgba(15,23,42,.35)">RS5</span>'+rB(r5)+'<span style="font-size:8px;color:rgba(15,23,42,.35)">RS21</span>'+rB(r21)+'<span style="font-size:8px;color:rgba(15,23,42,.35)">MS</span>'+rB(rms)+'</div></div>'}
bar.innerHTML=h}
function renderBars(){const g=document.getElementById('fvGrid');const sp=R.sector_perf;
let sorted;
if(barSort==='rs5')sorted=Object.keys(sp).sort((a,b)=>(R.sec_rs5[b]||0)-(R.sec_rs5[a]||0));
else if(barSort==='rs21')sorted=Object.keys(sp).sort((a,b)=>(R.sec_rs21[b]||0)-(R.sec_rs21[a]||0));
else if(barSort==='rsms')sorted=Object.keys(sp).sort((a,b)=>(R.sec_rsms[b]||0)-(R.sec_rsms[a]||0));
else sorted=Object.keys(sp).sort((a,b)=>{const va=sp[a][curP],vb=sp[b][curP];if(va==null)return 1;if(vb==null)return -1;return vb-va});
let mx=0;sorted.forEach(s=>{const v=Math.abs(sp[s][curP]||0);if(v>mx)mx=v});if(mx<1)mx=1;
const bsAct=k=>barSort===k?'color:#4f46e5;text-decoration:underline':'color:rgba(15,23,42,.35)';
let h='<div style="display:flex;align-items:center;margin-bottom:8px;padding:0 6px"><div style="width:240px;min-width:240px"></div><div style="flex:1"></div><div style="width:70px;min-width:70px"></div><div style="width:120px;min-width:120px;display:flex;gap:3px;justify-content:center;font-size:8px;font-family:monospace"><span style="min-width:24px;text-align:center;cursor:pointer;'+bsAct('rs5')+'" title="'+rT.rs5+'" onclick="sortBar(\'rs5\')">RS5</span><span style="min-width:24px;text-align:center;cursor:pointer;'+bsAct('rs21')+'" title="'+rT.rs21+'" onclick="sortBar(\'rs21\')">RS21</span><span style="min-width:24px;text-align:center;cursor:pointer;'+bsAct('rsms')+'" title="'+rT.rsms+'" onclick="sortBar(\'rsms\')">MS</span></div><div style="width:35px;min-width:35px"></div></div>';
sorted.forEach(s=>{const v=sp[s][curP];const r5=R.sec_rs5[s]||'-';const r21=R.sec_rs21[s]||'-';const rms=R.sec_rsms[s]||'-';const cnt=sp[s].count;
const pct=v!=null?Math.min(Math.abs(v)/mx*100,100):0;const clr=pC(v);const vCol=v>0?'#16a34a':v<0?'#dc2626':'rgba(15,23,42,.4)';
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
h+='<td>'+r[0]+'</td><td>'+rB(r[1])+'</td><td>'+rB(r[2])+'</td><td>'+rB(r[3])+'</td><td style="color:rgba(15,23,42,.3)">'+r[4]+'</td>';
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
ctx.fillStyle=v>0?'rgba(22,163,74,0.85)':v<0?'rgba(220,38,38,0.85)':'rgba(15,23,42,0.4)';
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
const defs={responsive:true,maintainAspectRatio:false,layout:{padding:{bottom:18}},plugins:{legend:{display:false}},scales:{x:{ticks:{color:'rgba(15,23,42,.4)',font:{family:'monospace',size:10}}},y:{ticks:{color:'rgba(15,23,42,.3)',font:{family:'monospace',size:9},callback:v=>fmtB(v)},grid:{color:'rgba(15,23,42,.04)'}}}};
_cRev=new Chart(document.getElementById('cRev'),{type:'bar',data:{labels,datasets:[{label:'Revenue',data:revs,backgroundColor:revs.map(v=>v!=null&&v>0?'rgba(37,99,235,0.6)':'rgba(220,38,38,0.6)'),borderRadius:4,barPercentage:0.5,categoryPercentage:0.7}]},options:{...defs,plugins:{...defs.plugins,title:{display:true,text:'Quarterly Revenue',color:'rgba(15,23,42,.6)',font:{size:12}}}},plugins:[mkYoyPlug(revChg)]});
_cNI=new Chart(document.getElementById('cNI'),{type:'bar',data:{labels,datasets:[{label:'Net Income',data:nis,backgroundColor:nis.map(v=>v!=null&&v>=0?'rgba(22,163,74,0.6)':'rgba(220,38,38,0.6)'),borderRadius:4,barPercentage:0.5,categoryPercentage:0.7}]},options:{...defs,plugins:{...defs.plugins,title:{display:true,text:'Quarterly Net Income',color:'rgba(15,23,42,.6)',font:{size:12}}}},plugins:[mkYoyPlug(niChg)]});
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
ctx.closePath();ctx.fillStyle='rgba(15,23,42,0.15)';ctx.fill();
ctx.beginPath();ctx.strokeStyle='rgba(15,23,42,0.12)';ctx.lineWidth=0.5;
data.forEach((d,i)=>{const x=pad.l+i*gap+gap/2;if(i===0)ctx.moveTo(x,yP(emaH[i]));else ctx.lineTo(x,yP(emaH[i]))});ctx.stroke();
ctx.beginPath();ctx.strokeStyle='rgba(15,23,42,0.12)';ctx.lineWidth=0.5;
data.forEach((d,i)=>{const x=pad.l+i*gap+gap/2;if(i===0)ctx.moveTo(x,yP(emaL[i]));else ctx.lineTo(x,yP(emaL[i]))});ctx.stroke();
ctx.beginPath();ctx.strokeStyle='rgba(251,146,60,0.8)';ctx.lineWidth=1;
data.forEach((d,i)=>{const x=pad.l+i*gap+gap/2;const y=yP(ema[i]);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});
ctx.stroke();
data.forEach((d,i)=>{
const x=pad.l+i*gap+gap/2;
const isUp=d.c>=d.o;
const color=isUp?'rgba(22,163,74,0.8)':'rgba(220,38,38,0.8)';
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
const chgC=chg!=null?(chg>0?'#16a34a':'#dc2626'):'rgba(15,23,42,.4)';
const rs=R.stock_rs21[t];
const ms=R.stock_rsms[t];
const rsTxt=(typeof rs==='number')?rs:'-';
const msTxt=(typeof ms==='number')?ms:'-';
const rsC=rsColor(rs);
const msC=rsColor(ms);
const sec=R.sector_map[t]||'';
const rank=secRank[sec];
const rankTxt=rank?'<span style="font-size:10px;font-weight:700;color:'+rankColor(rank)+'" title="Sector rank by '+curP+' performance">#'+rank+'</span> ':'';
h+='<div class="mini-chart" onclick="showFin(\''+t+'\')"><div class="mc-hd"><span class="mc-name">'+t+' '+rankTxt+'<span style="font-size:8px;font-weight:400;color:rgba(15,23,42,.3)">'+sec+'</span> <span style="font-size:10px;font-weight:700;color:'+rsC+'" title="RS21">RS:'+rsTxt+'</span> <span style="font-size:10px;font-weight:700;color:'+msC+'" title="RS-MS">MS:'+msTxt+'</span></span><span class="mc-chg" style="color:'+chgC+'">'+vF(chg)+'</span></div><canvas id="mc_'+idx+'"></canvas></div>'});
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
['SPY','QQQ','IWM'].forEach(n=>{const p=R.idx_perf[n];if(!p)return;const v=p[hisseP];const c=v!=null?(v>0?'#16a34a':'#dc2626'):'rgba(15,23,42,.4)';
ih+='<div class="idx-card"><div class="idx-name">'+n+'</div><div class="idx-val" style="color:'+c+'">'+vF(v)+'</div><div class="idx-chg" style="color:rgba(15,23,42,.4)">Last: '+(p.last||0).toLocaleString('en-US')+'</div></div>'});
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
h+='<td>'+r[0]+'</td><td style="color:rgba(15,23,42,.35);font-weight:400;font-size:10px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+r[1]+'</td>';
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
const chgC=chg!=null?(chg>0?'#16a34a':'#dc2626'):'rgba(15,23,42,.4)';
const rs=R.stock_rs21[t];
const ms=R.stock_rsms[t];
const rsTxt=(typeof rs==='number')?rs:'-';
const msTxt=(typeof ms==='number')?ms:'-';
const rsC=rsColor(rs);
const msC=rsColor(ms);
const sec=R.sector_map[t]||'';
const rank=secRank[sec];
const rankTxt=rank?'<span style="font-size:10px;font-weight:700;color:'+rankColor(rank)+'" title="Sector rank by '+hisseP+' performance">#'+rank+'</span> ':'';
h+='<div class="mini-chart" onclick="showFin(\''+t+'\')"><div class="mc-hd"><span class="mc-name">'+t+' '+rankTxt+'<span style="font-size:8px;font-weight:400;color:rgba(15,23,42,.3)">'+sec+'</span> <span style="font-size:10px;font-weight:700;color:'+rsC+'" title="RS21">RS:'+rsTxt+'</span> <span style="font-size:10px;font-weight:700;color:'+msC+'" title="RS-MS">MS:'+msTxt+'</span></span><span class="mc-chg" style="color:'+chgC+'">'+vF(chg)+'</span></div><canvas id="hmc_'+idx+'"></canvas></div>'});
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
div.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(15,23,42,.45);z-index:200;display:flex;justify-content:center;align-items:center';
div.onclick=function(e){if(e.target===div)div.remove()};
div.innerHTML='<div style="background:#ffffff;border:1px solid rgba(15,23,42,.12);border-radius:14px;padding:24px;width:90%;max-width:680px;max-height:80vh;overflow-y:auto;position:relative">'
+'<button onclick="document.getElementById(\'tvExportModal\').remove()" style="position:absolute;top:12px;right:16px;font-size:20px;cursor:pointer;color:rgba(15,23,42,.4);background:none;border:none">✕</button>'
+'<h2 style="font-size:15px;font-weight:700;margin-bottom:4px">TradingView List</h2>'
+'<div style="font-size:11px;color:rgba(15,23,42,.35);font-family:monospace;margin-bottom:14px">'+rows.length+' tickers · paste into TradingView Watchlist (comma-separated)</div>'
+'<textarea id="tvListTA" readonly style="width:100%;height:160px;background:rgba(15,23,42,.04);border:1px solid rgba(15,23,42,.1);border-radius:8px;padding:12px;color:#0f172a;font-family:monospace;font-size:11px;resize:vertical;outline:none;line-height:1.5">'+listStr+'</textarea>'
+'<div style="display:flex;gap:8px;margin-top:12px">'
+'<button onclick="const ta=document.getElementById(\'tvListTA\');ta.select();navigator.clipboard.writeText(ta.value).then(()=>{this.textContent=\'✓ Copied!\';setTimeout(()=>this.textContent=\'📋 Copy\',2000)}).catch(()=>{ta.select();document.execCommand(\'copy\');this.textContent=\'✓ Copied!\';setTimeout(()=>this.textContent=\'📋 Copy\',2000)})" class="btn a" style="font-size:12px;padding:8px 18px">📋 Copy</button>'
+'<button onclick="const ta=document.getElementById(\'tvListTA\');const spaced=ta.value.replace(/,/g,\' \');navigator.clipboard.writeText(spaced).catch(()=>{});this.textContent=\'✓ Copied!\';setTimeout(()=>this.textContent=\'Copy w/ spaces\',2000)" class="btn" style="font-size:12px;padding:8px 18px">Copy w/ spaces</button>'
+'</div>'
+'<div style="font-size:10px;color:rgba(15,23,42,.2);font-family:monospace;margin-top:10px">💡 TradingView → Watchlist → Import symbols → paste</div>'
+'</div>';
document.body.appendChild(div);
setTimeout(()=>{const ta=document.getElementById('tvListTA');if(ta)ta.select()},50)}
// ===== THEMES (CSV) =====
let thSC=1,thSA=false,curTheme=null,thView='table',tsSC=-1,tsSA=false,thRows=[];
function thSort(i){if(thSC===i)thSA=!thSA;else{thSC=i;thSA=false}renderThemes()}
function openThemeIdx(i){curTheme=thRows[i][0];tsSC=-1;thView='table';renderThemes()}
function showThemes(){curTheme=null;destroyMiniCharts();renderThemes()}
function setThemeView(v){thView=v;renderThemes()}
function tsSort(i){if(tsSC===i)tsSA=!tsSA;else{tsSC=i;tsSA=false}renderThemes()}
function renderThemes(){
const lv=document.getElementById('themeListView');if(!lv)return;
const bar=document.getElementById('themeDetailBar'),sv=document.getElementById('themeStocksView'),cv=document.getElementById('themeChartView');
if(curTheme&&!(R.themes||{})[curTheme])curTheme=null;
lv.style.display=curTheme?'none':'block';bar.style.display=curTheme?'flex':'none';
sv.style.display=(curTheme&&thView==='table')?'block':'none';cv.style.display=(curTheme&&thView==='charts')?'block':'none';
if(!curTheme){
const tbl=document.getElementById('themeTbl');
const tp=R.theme_perf||{},rm=R.theme_rsms||{};
const rows=Object.keys(tp).map(s=>[s,rm[s]||0,tp[s].count,tp[s]['1d'],tp[s]['1w'],tp[s]['1m'],tp[s]['3m'],tp[s]['6m'],tp[s]['12m']]);
rows.sort((a,b)=>{const va=a[thSC],vb=b[thSC];if(typeof va==='string')return thSA?va.localeCompare(vb):vb.localeCompare(va);return thSA?(va||0)-(vb||0):(vb||0)-(va||0)});
thRows=rows;
const hd=['Theme','RS-MS','#','1D','1W','1M','3M','6M','12M'];
let h='<tr>'+hd.map((x,i)=>'<th class="'+(thSC===i?'st':'')+'" onclick="thSort('+i+')" title="'+(i===1?rT.rsms:'')+'">'+x+'<span class="sa">'+(thSC===i?(thSA?'▲':'▼'):'')+'</span></th>').join('')+'</tr>';
rows.forEach((r,ri)=>{h+='<tr class="sr" onclick="openThemeIdx('+ri+')"><td>'+r[0]+'</td><td>'+rB(r[1]||null)+'</td><td style="color:rgba(15,23,42,.3)">'+r[2]+'</td>'+[3,4,5,6,7,8].map(i=>'<td class="'+vK(r[i])+'">'+vF(r[i])+'</td>').join('')+'</tr>'});
tbl.innerHTML=h;const c=document.getElementById('thCount');if(c)c.textContent=rows.length+' themes · median RS-MS, average performance · click a theme to see its stocks';
return}
// ----- seçili theme: hisseler -----
bar.querySelectorAll('.btn').forEach((b,i)=>b.classList.toggle('a',(i===0)===(thView==='table')));
const tks=(R.themes[curTheme]||[]).filter(t=>R.stock_perf[t]);
const trs=(R.theme_rsms||{})[curTheme];
document.getElementById('themeTitle').textContent=curTheme+' · '+tks.length+' stocks'+(trs?' · RS-MS '+trs:'');
const pMap={'1d':5,'1w':6,'1m':7,'3m':8,'6m':9,'12m':10};
const rows=tks.map(t=>{const p=R.stock_perf[t];return[t,R.sector_map[t]||'',R.stock_rs5[t]||0,R.stock_rs21[t]||0,R.stock_rsms[t]||0,p['1d'],p['1w'],p['1m'],p['3m'],p['6m'],p['12m']]});
const di=pMap[curP]||7;
if(tsSC>=0)rows.sort((a,b)=>{const va=a[tsSC],vb=b[tsSC];if(va==null)return 1;if(vb==null)return -1;if(typeof va==='string')return tsSA?va.localeCompare(vb):vb.localeCompare(va);return tsSA?va-vb:vb-va});
else rows.sort((a,b)=>{const va=a[di],vb=b[di];if(va==null)return 1;if(vb==null)return -1;return vb-va});
if(thView==='table'){
const ar=tsSA?'▲':'▼';
const tH=(i,l,tip)=>'<th'+(tsSC===i?' class="st"':'')+(tip?' title="'+tip+'"':'')+' onclick="tsSort('+i+')">'+l+'<span class="sa">'+(tsSC===i?ar:'⇅')+'</span></th>';
let h='<tr>'+tH(0,'Ticker')+tH(1,'Industry')+tH(2,'RS5',rT.rs5)+tH(3,'RS21',rT.rs21)+tH(4,'RS-MS',rT.rsms)+tH(5,'1D')+tH(6,'1W')+tH(7,'1M')+tH(8,'3M')+tH(9,'6M')+tH(10,'12M')+'</tr>';
rows.forEach(r=>{h+='<tr style="cursor:pointer" onclick="showFin(\''+r[0]+'\')"><td>'+r[0]+'</td><td style="font-weight:400;color:rgba(15,23,42,.5)">'+r[1]+'</td><td>'+rB(r[2])+'</td><td>'+rB(r[3])+'</td><td>'+rB(r[4])+'</td>';
for(let i=5;i<=10;i++)h+='<td class="'+vK(r[i])+'">'+vF(r[i])+'</td>';h+='</tr>'});
document.getElementById('themeStockTbl').innerHTML=h}
else{
destroyMiniCharts();
const g=document.getElementById('themeChartGrid');let h='';
rows.forEach((r,idx)=>{const t=r[0],chg=r[di];
const chgC=chg!=null?(chg>0?'#16a34a':'#dc2626'):'rgba(15,23,42,.4)';
h+='<div class="mini-chart" onclick="showFin(\''+t+'\')"><div class="mc-hd"><span class="mc-name">'+t+' <span style="font-size:8px;font-weight:400;color:rgba(15,23,42,.3)">'+r[1]+'</span> <span style="font-size:10px;font-weight:700;color:'+rsColor(r[3])+'" title="RS21">RS:'+(r[3]||'-')+'</span> <span style="font-size:10px;font-weight:700;color:'+rsColor(r[4])+'" title="RS-MS">MS:'+(r[4]||'-')+'</span></span><span class="mc-chg" style="color:'+chgC+'">'+vF(chg)+'</span></div><canvas id="tc_'+idx+'"></canvas></div>'});
g.innerHTML=h;
if(g.offsetWidth>0)rows.forEach((r,idx)=>{const c=document.getElementById('tc_'+idx);if(c)drawCandles(c,(R.ohlc||{})[r[0]],'tc_')})}}
// ===== LAB REPORT =====
const LAB_COL={buy:'#16a34a',buy_ext:'#65a30d',above:'#0284c7',ext_both:'#9333ea',under:'#ea580c',unwinding:'#ca8a04',lost:'#dc2626'};
let labChart=null;
function f2(v){return v==null?'-':(v>0?'+':'')+v.toFixed(2)}
// Hover pop-up: 21 EMA cloud'lu mum grafiği (Stocks sekmesindeki ile aynı)
function labPopMove(e){const p=document.getElementById('labPop');if(!p||p.style.display==='none')return;
let x=e.clientX+16,y=e.clientY+16;const w=p.offsetWidth||330,h=p.offsetHeight||240;
if(x+w>window.innerWidth-8)x=e.clientX-w-16;if(y+h>window.innerHeight-8)y=Math.max(8,e.clientY-h-16);
p.style.left=Math.max(8,x)+'px';p.style.top=y+'px'}
function labPopHide(){const p=document.getElementById('labPop');if(p)p.style.display='none'}
function labPop(e,t){
const x=(R.lab&&R.lab.leaders||[]).find(l=>l.t===t);if(!x)return;
const ohlc=(R.ohlc||{})[t];
let p=document.getElementById('labPop');
if(!p){p=document.createElement('div');p.id='labPop';p.className='mini-chart';p.style.cssText='position:fixed;z-index:300;width:330px;background:#fff;border:1px solid rgba(15,23,42,.15);border-radius:10px;padding:10px 12px;box-shadow:0 8px 30px rgba(15,23,42,.18);pointer-events:none';document.body.appendChild(p)}
const sd=R.lab.state_defs.find(d=>d[0]===x.st);const col=LAB_COL[x.st]||'#64748b';
p.innerHTML='<div class="mc-hd"><span class="mc-name">'+t+' <span style="font-size:9px;font-weight:400;color:rgba(15,23,42,.45)">'+(x.theme||x.ind||'')+'</span> <span style="font-size:10px;font-weight:700;color:'+rsColor(x.rs)+'">MS:'+x.rs+'</span></span><span class="mc-chg" style="color:'+col+'">'+(sd?sd[1]:'')+'</span></div>'
+(ohlc&&ohlc.length>=10?'<canvas id="labPopCv" style="display:block;width:100%;height:140px"></canvas>':'<div style="font-size:11px;color:rgba(15,23,42,.4);padding:30px 0;text-align:center">No chart data</div>')
+'<div style="font-size:10px;font-family:monospace;color:rgba(15,23,42,.45);margin-top:6px">daily '+f2(x.dD)+' ATR · weekly '+f2(x.dW)+' ATR · 21 EMA cloud, 63 days</div>';
p.style.display='block';labPopMove(e);
const cv=document.getElementById('labPopCv');if(cv)drawCandles(cv,ohlc,'lp_')}
function labChip(x){const c=LAB_COL[x.st]||'#94a3b8';return '<span class="rb" style="background:'+c+'22;color:'+c+';margin:2px 3px 2px 0;min-width:0;cursor:default" onmouseenter="labPop(event,\''+x.t+'\')" onmousemove="labPopMove(event)" onmouseleave="labPopHide()">'+x.t+'</span>'}
function labCard(title,sub,body){return '<div class="tw" style="margin-bottom:0"><div class="th"><span>'+title+'</span><span style="font-size:10px;color:rgba(15,23,42,.3);font-family:monospace">'+(sub||'')+'</span></div><div style="padding:12px 16px">'+body+'</div></div>'}
function drawLabMap(){
const cv=document.getElementById('labMap');if(!cv)return;
if(labChart){labChart.destroy();labChart=null}
const L=R.lab.leaders;
// Eksenler: min her zaman -1 ATR, max en büyük değerin hemen üstü (örn. 3.89 → 4, 6.31 → 7)
const mxD=Math.max.apply(null,L.map(x=>x.dD)),mxW=Math.max.apply(null,L.map(x=>x.dW));
const XM=Math.max(2,Math.ceil(mxD+0.05)),YM=Math.max(5,Math.ceil(mxW+0.05));
const cx=v=>Math.max(-1,Math.min(XM,v)),cy=v=>Math.max(-1,Math.min(YM,v));
const ds=L.map(x=>({x:cx(x.dD),y:cy(x.dW),raw:x}));
const bandPlugin={id:'bands',beforeDatasetsDraw(ch){const ctx=ch.ctx,sx=ch.scales.x,sy=ch.scales.y;ctx.save();
const px=v=>sx.getPixelForValue(v),top=sy.top,h=sy.bottom-sy.top;
ctx.fillStyle='rgba(244,114,182,.10)';ctx.fillRect(px(-1),top,px(-0.5)-px(-1),h);
ctx.fillStyle='rgba(34,197,94,.13)';ctx.fillRect(px(-0.5),top,px(1)-px(-0.5),h);
ctx.fillStyle='rgba(250,204,21,.14)';ctx.fillRect(px(1),top,sx.right-px(1),h);
const y0=sy.getPixelForValue(0),y4=sy.getPixelForValue(4);
ctx.setLineDash([4,4]);ctx.strokeStyle='rgba(15,23,42,.22)';ctx.lineWidth=1;
ctx.beginPath();ctx.moveTo(sx.left,y4);ctx.lineTo(sx.right,y4);ctx.stroke();
ctx.beginPath();ctx.moveTo(sx.left,y0);ctx.lineTo(sx.right,y0);ctx.stroke();
ctx.setLineDash([]);ctx.strokeStyle='rgba(22,163,74,.65)';ctx.lineWidth=1.5;ctx.strokeRect(px(-0.5),y4,px(1)-px(-0.5),y0-y4);ctx.restore()}};
const lblPlugin={id:'lblT',afterDatasetsDraw(ch){const ctx=ch.ctx,sx=ch.scales.x;ctx.save();ctx.font='9px monospace';ctx.fillStyle='rgba(15,23,42,.8)';
ch.getDatasetMeta(0).data.forEach((pt,i)=>{const t=L[i].t;if(pt.x>sx.right-42){ctx.textAlign='right';ctx.fillText(t,pt.x-6,pt.y+3)}else{ctx.textAlign='left';ctx.fillText(t,pt.x+6,pt.y+3)}});ctx.restore()}};
const ax=(t)=>({title:{display:true,text:t,color:'rgba(15,23,42,.5)',font:{size:10}},grid:{color:'rgba(15,23,42,.07)'},ticks:{stepSize:1,color:'rgba(15,23,42,.5)',font:{size:10}}});
labChart=new Chart(cv,{type:'scatter',data:{datasets:[{data:ds,pointRadius:4.5,pointBorderWidth:0,pointBackgroundColor:L.map(x=>LAB_COL[x.st])}]},
options:{responsive:true,maintainAspectRatio:false,animation:false,layout:{padding:{right:6,top:6}},plugins:{legend:{display:false},
tooltip:{callbacks:{label:function(c){const r=c.raw.raw;return r.t+' · RS-MS '+r.rs+' · daily '+f2(r.dD)+' ATR · weekly '+f2(r.dW)+' ATR'}}}},
scales:{x:Object.assign({min:-1,max:XM},ax('Daily: distance from 21-day EMA (ATR)')),y:Object.assign({min:-1,max:YM},ax('Weekly: distance from 10-week SMA (ATR)'))}},
plugins:[bandPlugin,lblPlugin]})}
// Lab Report TV listesi: buy area önce, sonra sol alttan sol üste (lost → under → unwinding), sonra sağ taraf (above → extended)
const LAB_TV_ORDER=['buy','buy_ext','lost','under','unwinding','above','ext_both'];
function labTvList(){
const L=R.lab;if(!L)return;
const ord={};LAB_TV_ORDER.forEach((k,i)=>{ord[k]=i});
const rows=L.leaders.map((x,i)=>[x,i]).sort((a,b)=>(ord[a[0].st]-ord[b[0].st])||(a[1]-b[1])).map(a=>a[0]);
const ex=R.exchange_map||{};
const sym=x=>(ex[x.t]||'NASDAQ')+':'+x.t;
const plain=rows.map(sym).join(',');
const names={};L.state_defs.forEach(d=>{names[d[0]]=d[1]});
let sect=[],last=null;
rows.forEach(x=>{if(x.st!==last){sect.push('###'+names[x.st]);last=x.st}sect.push(sym(x))});
const withSections=sect.join(',');
const byTh={};rows.forEach(x=>{const k=x.theme||'No theme';(byTh[k]=byTh[k]||[]).push(x)});
const thKeys=Object.keys(byTh).sort((a,b)=>{if(a==='No theme')return 1;if(b==='No theme')return -1;return (byTh[b].length-byTh[a].length)||(Math.max.apply(null,byTh[b].map(x=>x.rs))-Math.max.apply(null,byTh[a].map(x=>x.rs)))||a.localeCompare(b)});
const withThemes=thKeys.map(k=>'###'+k+','+byTh[k].map(sym).join(',')).join(',');
const summary=LAB_TV_ORDER.map(k=>{const n=rows.filter(x=>x.st===k).length;return n?names[k]+' '+n:''}).filter(Boolean).join(' · ');
const old=document.getElementById('tvExportModal');if(old)old.remove();
const div=document.createElement('div');div.id='tvExportModal';
div.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(15,23,42,.45);z-index:200;display:flex;justify-content:center;align-items:center';
div.onclick=function(e){if(e.target===div)div.remove()};
div.innerHTML='<div style="background:#ffffff;border:1px solid rgba(15,23,42,.12);border-radius:14px;padding:24px;width:90%;max-width:680px;max-height:80vh;overflow-y:auto;position:relative">'
+'<button id="tvLabClose" style="position:absolute;top:12px;right:16px;font-size:20px;cursor:pointer;color:rgba(15,23,42,.4);background:none;border:none">✕</button>'
+'<h2 style="font-size:15px;font-weight:700;margin-bottom:4px">Leaders · TradingView List</h2>'
+'<div style="font-size:11px;color:rgba(15,23,42,.35);font-family:monospace;margin-bottom:6px">'+rows.length+' tickers · buy area first, then lost, under, unwinding, above, extended</div>'
+'<div style="font-size:10px;color:rgba(15,23,42,.3);font-family:monospace;margin-bottom:12px">'+summary+'</div>'
+'<textarea id="tvLabTA" readonly style="width:100%;height:160px;background:rgba(15,23,42,.04);border:1px solid rgba(15,23,42,.1);border-radius:8px;padding:12px;color:#0f172a;font-family:monospace;font-size:11px;resize:vertical;outline:none;line-height:1.5"></textarea>'
+'<div style="display:flex;gap:8px;margin-top:12px"><button id="tvLabCopy" class="btn a" style="font-size:12px;padding:8px 18px">📋 Copy</button>'
+'<button id="tvLabCopySec" class="btn" style="font-size:12px;padding:8px 18px">Copy with sections</button><button id="tvLabCopyTh" class="btn" style="font-size:12px;padding:8px 18px">Copy with themes</button></div>'
+'<div style="font-size:10px;color:rgba(15,23,42,.2);font-family:monospace;margin-top:10px">TradingView → Watchlist → Import symbols → paste. "With sections" adds a ### header per state; "with themes" groups the tickers under a ### header for each theme (largest theme first).</div></div>';
document.body.appendChild(div);
const ta=document.getElementById('tvLabTA');ta.value=plain;
const cp=(btn,txt,lbl)=>{ta.value=txt;ta.select();const done=()=>{btn.textContent='✓ Copied!';setTimeout(()=>{btn.textContent=lbl},2000)};
if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(txt).then(done).catch(()=>{document.execCommand('copy');done()});else{document.execCommand('copy');done()}};
document.getElementById('tvLabCopy').onclick=function(){cp(this,plain,'📋 Copy')};
document.getElementById('tvLabCopySec').onclick=function(){cp(this,withSections,'Copy with sections')};
document.getElementById('tvLabCopyTh').onclick=function(){cp(this,withThemes,'Copy with themes')};
document.getElementById('tvLabClose').onclick=function(){div.remove()};
setTimeout(()=>ta.select(),50)}
// "What's leading" satırı → Sectors > Themes > o theme'nin hisseleri (chart görünümü)
function labGoTheme(i){const x=R.lab&&R.lab.leading[i];if(!x||!(R.themes||{})[x.theme])return;
const mb=Array.from(document.querySelectorAll('.main-tab')).find(b=>(b.getAttribute('onclick')||'').indexOf('sektor')>=0);
switchMain('sektor',mb||null);
const tb=Array.from(document.querySelectorAll('.tab')).find(b=>(b.getAttribute('onclick')||'').indexOf('themes')>=0);
sT('themes',tb||null);curTheme=x.theme;thView='charts';tsSC=-1;renderThemes();window.scrollTo(0,0)}
function renderLab(){
const w=document.getElementById('labWrap');if(!w)return;const L=R.lab;
if(!L){w.innerHTML='<div style="padding:40px;color:rgba(15,23,42,.4);font-size:13px">Not enough price history to build the Lab Report.</div>';return}
const LB={};L.leaders.forEach(x=>{LB[x.t]=x});
const SD={};L.state_defs.forEach(d=>{SD[d[0]]=d});
const chips=a=>(a&&a.length)?a.map(t=>LB[t]?labChip(LB[t]):'').join(''):'<span style="color:rgba(15,23,42,.25);font-size:11px">none</span>';
const grid='display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:14px;margin-bottom:14px';
const sub=t=>'<div style="font-size:10px;color:rgba(15,23,42,.4);font-family:monospace;margin:10px 0 4px">'+t+'</div>';
const S=L.setups,C=L.counts,N=L.n_leaders;
let h='<div class="meta" style="margin:0 0 12px">'+L.asof+' close · top '+N+' of '+L.n_universe+' stocks by RS-MS · distances measured in ATR</div>';
// Setups
const chg=(a,r)=>(a.length||r.length)?'<div style="font-size:10px;color:rgba(15,23,42,.4);margin-top:6px;font-family:monospace">vs previous day: '+(a.length?'<span style="color:#16a34a">in '+a.join(', ')+'</span> ':'')+(r.length?'<span style="color:#dc2626">out '+r.join(', ')+'</span>':'')+'</div>':'';
let sb1=sub('In the buy area · both averages rising ('+S.buy.length+')')+chips(S.buy)+chg(S.buy_added,S.buy_removed);
if(S.dagger.length)sb1+='<div style="font-size:10px;color:rgba(15,23,42,.35);margin-top:6px">In the box but an average is not rising (not a setup): '+S.dagger.join(', ')+'</div>';
let sb2=sub('On weakness · wait for the level ('+S.weak.length+')')+chips(S.weak)+chg(S.weak_added,S.weak_removed);
const sb='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:24px"><div>'+sb1+'</div><div>'+sb2+'</div></div>';
// Leaders map
const mapCard=labCard('Leaders map','outlined box = buy area <button class="btn" style="padding:3px 10px;font-size:10px;margin-left:10px" onclick="labTvList()">📋 TV List</button>','<div style="height:580px;position:relative"><canvas id="labMap"></canvas></div><div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;font-size:10px;font-family:monospace;color:rgba(15,23,42,.5)">'+L.state_defs.map(d=>'<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+LAB_COL[d[0]]+';margin-right:4px"></span>'+d[1]+'</span>').join('')+'</div>');
h+='<div style="margin-bottom:14px">'+mapCard+'</div><div style="margin-bottom:14px">'+labCard('Setups',L.asof,sb)+'</div>';
h+='<div class="idx-bar">'+[['Leaders in the buy area',C.buy+' / '+N,'#16a34a'],['Leaders above 10-week SMA',C.above_sma+' / '+N,'#0284c7'],['Weekly stretched (>+4 ATR)',C.stretched+' / '+N,'#9333ea'],['Stocks above 21-day EMA',L.above21_pct+'%','#ea580c']].map(a=>'<div class="idx-card"><div class="idx-name">'+a[0]+'</div><div class="idx-val" style="color:'+a[2]+'">'+a[1]+'</div></div>').join('')+'</div>';
// 7 states
let st='';L.state_defs.forEach(d=>{const a=L.states[d[0]]||[];st+='<div style="display:flex;gap:12px;padding:9px 0;border-bottom:1px solid rgba(15,23,42,.04)"><div style="width:230px;min-width:230px"><div style="font-size:12px;font-weight:600;color:'+LAB_COL[d[0]]+'">'+d[1]+' <span style="color:rgba(15,23,42,.4);font-family:monospace;font-size:11px">'+a.length+'</span></div><div style="font-size:10px;color:rgba(15,23,42,.35);margin-top:2px;line-height:1.4">'+d[2]+'</div></div><div style="flex:1">'+chips(a)+'</div></div>'});
h+='<div style="margin-bottom:14px">'+labCard('Seven states, best first','where each leader sits on the two clocks',st)+'</div>';
// Clusters + leading
let cb='';L.clusters.forEach(c=>{cb+='<div style="padding:8px 0;border-bottom:1px solid rgba(15,23,42,.04)"><div style="font-size:12px;font-weight:600">'+c.theme+' <span style="font-family:monospace;font-size:11px;color:'+(c.buy?'#16a34a':'rgba(15,23,42,.4)')+'">'+c.buy+'/'+c.n+'</span></div><div>'+chips(c.tickers)+'</div></div>'});
if(!L.clusters.length)cb='<span style="color:rgba(15,23,42,.3);font-size:11px">No theme has 2+ leaders.</span>';
else cb+='<div style="font-size:10px;color:rgba(15,23,42,.3);margin-top:8px;font-family:monospace">'+L.clusters_other+' other leaders sit alone or in themes with one leader. buy area / leaders in theme.</div>';
let lb='<div style="display:flex;font-size:9px;color:rgba(15,23,42,.3);font-family:monospace;margin-bottom:4px"><div style="width:190px;min-width:190px"></div><div style="flex:1;display:flex;justify-content:space-between"><span>RS 40</span><span>70</span><span>100</span></div><div style="width:70px;min-width:70px"></div></div>';
const pos=v=>Math.max(0,Math.min(100,(v-40)/60*100));
L.leading.forEach((x,i)=>{const d=x.then==null?null:x.now-x.then;const dc=d==null?'rgba(15,23,42,.4)':(d>0?'#16a34a':(d<0?'#dc2626':'rgba(15,23,42,.4)'));
let bar='<div style="flex:1;position:relative;height:14px;background:rgba(15,23,42,.03);border-radius:3px">';
if(x.then!=null)bar+='<div style="position:absolute;top:6px;height:2px;left:'+Math.min(pos(x.then),pos(x.now))+'%;width:'+Math.abs(pos(x.now)-pos(x.then))+'%;background:rgba(15,23,42,.25)"></div><div style="position:absolute;top:3px;left:calc('+pos(x.then)+'% - 4px);width:8px;height:8px;border:1.5px solid rgba(15,23,42,.5);border-radius:50%"></div>';
bar+='<div style="position:absolute;top:3px;left:calc('+pos(x.now)+'% - 4px);width:8px;height:8px;background:#6366f1;border-radius:50%"></div></div>';
lb+='<div class="lab-lead" onclick="labGoTheme('+i+')" title="Open '+x.theme+' stocks" style="display:flex;align-items:center;padding:4px 6px;cursor:pointer;border-radius:5px"><div style="width:190px;min-width:190px;font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="'+x.n+' stocks">'+x.theme+'</div>'+bar+'<div style="width:70px;min-width:70px;text-align:right;font-family:monospace;font-size:11px">'+x.now+' <span style="color:'+dc+';font-size:10px">'+(d==null?'':(d>0?'▲'+d:(d<0?'▼'+Math.abs(d):'=')))+'</span></div></div>'});
lb+='<div style="font-size:10px;color:rgba(15,23,42,.3);margin-top:8px;font-family:monospace">Themes with 3+ ranked stocks, median RS-MS. Hollow dot = 8 weeks earlier.</div>';
h+='<div style="'+grid+'">'+labCard('Where they cluster','leaders by CSV theme',cb)+labCard("What's leading",'theme strength now vs 8 weeks ago',lb)+'</div>';
// Sector ranking + growth leaders
let sr='<table><tr><th>#</th><th>Sector</th><th>ETF</th><th>1M</th><th>3M</th></tr>';
L.sectors.forEach((x,i)=>{sr+='<tr><td>'+(i+1)+'</td><td style="font-weight:400">'+x.name+'</td><td style="color:rgba(15,23,42,.4)">'+x.etf+'</td><td class="'+vK(x.r1)+'">'+vF(x.r1)+'</td><td class="'+vK(x.r3)+'">'+vF(x.r3)+'</td></tr>'});
sr+='</table>';if(!L.sectors.length)sr='<span style="color:rgba(15,23,42,.3);font-size:11px">RSP sector ETF data not available.</span>';
let gl='<table><tr><th>#</th><th>Ticker</th><th>Theme</th><th>State</th><th>RS-MS</th></tr>';
L.leaders.slice(0,11).forEach((x,i)=>{gl+='<tr><td>'+(i+1)+'</td><td>'+x.t+'</td><td style="font-weight:400;color:rgba(15,23,42,.55)">'+(x.theme||'-')+'</td><td style="font-weight:400;color:'+LAB_COL[x.st]+'">'+SD[x.st][1]+'</td><td>'+rB(x.rs)+'</td></tr>'});
gl+='</table>';
h+='<div style="'+grid+'">'+labCard('Sector ranking','equal-weight RSP ETFs · avg of 1M and 3M',sr)+labCard('Growth leaders','strongest 11 by RS-MS',gl)+'</div>';
// Concentration
let cc='';
L.conc.forEach(x=>{const c=x.z>0.5?'#16a34a':(x.z<-0.5?'#dc2626':'rgba(15,23,42,.5)');cc+='<div class="idx-card" style="min-width:150px"><div class="idx-name">'+x.pair+'</div><div class="idx-val" style="color:'+c+'">'+f2(x.z)+'σ</div><div class="idx-chg" style="color:rgba(15,23,42,.5)">'+x.label+'</div></div>'});
if(!L.conc.length)cc='<span style="color:rgba(15,23,42,.3);font-size:11px">Benchmark ETF data not available.</span>';
else cc='<div class="idx-bar" style="margin-bottom:10px">'+cc+'</div><div style="font-size:12px;font-weight:600">'+(L.conc_result||'')+' <span style="font-family:monospace;font-size:10px;color:rgba(15,23,42,.35)">('+L.conc_hits+' of 5 ratios point to concentration)</span></div>';
h+='<div style="margin-bottom:14px">'+labCard('Concentration test','each ratio vs its own 63-day average, in standard deviations',cc)+'</div>';
// Notes
h+='<div style="font-size:10px;color:rgba(15,23,42,.3);font-family:monospace;line-height:1.7;padding:4px 2px 24px">Daily distance = (close - 21-day EMA) / ATR(14). Weekly distance = (close - 10-week SMA) / ATR(14). Buy area: daily -0.5 to +1 ATR and weekly 0 to +4 ATR; a setup also needs both averages rising (vs 5 days ago). On weakness: above the lane (up to +2.5 ATR), both averages rising, and lower than 3 days ago. Leaders = top '+N+' by RS-MS. Themes and industries come from the CSV. Map clamps extreme values to the plot edge. Not investment advice.</div>';
w.innerHTML=h;drawLabMap()}
// ===== MARKET OVERVIEW =====
const MO={teal:'#20897D',red:'#E04141',gray:'#9CA3AF',mut:'#6b7280'};
let moCh={};
const moSt={ext:'NDX',extMode:'std',sec:'NDX',secView:'box',nf:'ALL'};
function moKill(p){Object.keys(moCh).forEach(k=>{if(!p||k.indexOf(p)===0){try{moCh[k].destroy()}catch(e){}delete moCh[k]}})}
function moC(v){return v>0?MO.teal:(v<0?MO.red:MO.gray)}
function moN(v,d){return Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d})}
function moS(v,d,plus){if(v==null||isNaN(v))return '-';return (v<0?'-':(plus&&v>0?'+':''))+moN(v,d)}
function moP(v,d){return moS(v,d==null?2:d,true)+'%'}
function moTint(hex,a){const n=parseInt(hex.slice(1),16);return 'rgba('+(n>>16)+','+((n>>8)&255)+','+(n&255)+','+a+')'}
function moSvg(p,c,s){return '<svg viewBox="0 0 24 24" width="'+(s||12)+'" height="'+(s||12)+'" fill="none" stroke="'+(c||'currentColor')+'" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'+p+'</svg>'}
const MO_I={up:'<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',dn:'<polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/>',
act:'<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',tgt:'<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
bar:'<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',zap:'<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
spark:'<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>',ref:'<polyline points="23 4 23 10 17 10"/><path d="M20.5 15a9 9 0 1 1-2.1-9.4L23 10"/>',
zin:'<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.7" y2="16.7"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>',
zout:'<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.7" y2="16.7"/><line x1="8" y1="11" x2="14" y2="11"/>',
grid:'<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
x:'<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',plus:'<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>'};
function moTr(up,c,s){return moSvg(up?MO_I.up:MO_I.dn,c,s||12)}
// ---- Chart.js eklentisi: sağda değer etiketleri + yatay referans çizgileri ----
const moTagPlugin={id:'moTags',
beforeDatasetsDraw(ch,a,o){const ctx=ch.ctx,ca=ch.chartArea,ys=ch.scales.y;ctx.save();
(o.hl||[]).forEach(h=>{const y=ys.getPixelForValue(h.y);if(y<ca.top||y>ca.bottom)return;ctx.beginPath();ctx.setLineDash(h.dash?[5,4]:[]);ctx.strokeStyle=h.color;ctx.lineWidth=h.dash?1:1.6;ctx.moveTo(ca.left,y);ctx.lineTo(ca.right,y);ctx.stroke()});ctx.restore()},
afterDatasetsDraw(ch,a,o){if(!o.tags)return;const ctx=ch.ctx,ca=ch.chartArea,ys=ch.scales.y,tags=[];
ch.data.datasets.forEach((ds,i)=>{if(!ds.tag||!ch.isDatasetVisible(i))return;let li=-1;for(let j=ds.data.length-1;j>=0;j--){if(ds.data[j]!=null){li=j;break}}if(li<0)return;tags.push({y:ys.getPixelForValue(ds.data[li]),lab:ds.tag,val:ds.data[li],col:ds.borderColor})});
(o.hl||[]).forEach(h=>{if(h.tag)tags.push({y:ys.getPixelForValue(h.y),lab:h.tag,val:h.y,col:h.color})});
tags.forEach(t=>{t.y=Math.max(ca.top+8,Math.min(ca.bottom-8,t.y))});tags.sort((x,y)=>x.y-y.y);
for(let i=1;i<tags.length;i++){if(tags[i].y-tags[i-1].y<15)tags[i].y=tags[i-1].y+15}
ctx.save();ctx.font='600 10px -apple-system,system-ui,sans-serif';ctx.textBaseline='middle';
tags.forEach(t=>{const vs=t.val.toFixed(2),lw=ctx.measureText(t.lab).width+12,vw=ctx.measureText(vs).width+12,h=15,x1=ca.right-lw-vw,y0=t.y-h/2;
ctx.fillStyle=t.col;ctx.fillRect(x1,y0,lw,h);ctx.globalAlpha=.82;ctx.fillRect(x1+lw,y0,vw,h);ctx.globalAlpha=1;ctx.fillStyle='#fff';ctx.textAlign='left';ctx.fillText(t.lab,x1+6,t.y+.5);ctx.fillText(vs,x1+lw+6,t.y+.5)});ctx.restore()}};
function moXL(d,gap){const m=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];let pm='';const o=d.map(x=>{const mo=x.slice(5,7);if(mo!==pm){pm=mo;return mo==='01'?x.slice(0,4):m[+mo-1]}return ''});
if(gap&&o[0]){let j=1;while(j<o.length&&!o[j])j++;if(j<gap)o[0]=''}return o}
function moLine(id,dates,sets,o){o=o||{};const cv=document.getElementById(id);if(!cv)return;if(moCh[id])moCh[id].destroy();
const pad=o.pad==null?1:o.pad,lab=moXL(dates,o.gap).concat(Array(pad).fill('')),dts=dates.concat(Array(pad).fill(''));
const ds=sets.map(s=>({label:s.label,tag:s.tag,borderColor:s.color,backgroundColor:s.color,borderWidth:s.w||1.4,borderDash:s.dash||[],pointRadius:0,pointHoverRadius:3,tension:0,spanGaps:true,hidden:!!s.hidden,data:s.data.concat(Array(pad).fill(null))}));
moCh[id]=new Chart(cv,{type:'line',data:{labels:lab,datasets:ds},options:{responsive:true,maintainAspectRatio:false,animation:false,interaction:{mode:'index',intersect:false},layout:{padding:{top:6,right:0}},
plugins:{legend:{display:false},tooltip:{callbacks:{title:i=>dts[i[0].dataIndex]||'',label:c=>c.parsed.y==null?'':(c.dataset.label+': '+c.parsed.y.toFixed(2))}},moTags:{hl:o.hl||[]}},
scales:{x:{grid:{display:false},ticks:{autoSkip:false,maxRotation:0,align:'inner',color:'rgba(15,23,42,.6)',font:{size:10,weight:'600'}}},y:{position:'right',min:o.min,max:o.max,suggestedMin:o.smin,suggestedMax:o.smax,grid:{color:'rgba(15,23,42,.06)'},ticks:{color:'rgba(15,23,42,.55)',font:{size:10},maxTicksLimit:o.ticks||6}}}},plugins:[moTagPlugin]})}
// ---- parçalar ----
function moRegCol(r){return r==='UPTREND ATTEMPT'?MO.teal:(r==='CORRECTION'?MO.red:MO.mut)}
function moIdxCard(c){const col=moC(c.chg),rc=moRegCol(c.regime);
return '<div class="mo-c" style="border-left-color:'+col+'"><div class="mo-ct">'+c.title+'</div><div class="mo-row"><span class="mo-sym">'+c.sym+'</span><span class="mo-big" style="color:'+col+'">'+moP(c.chg)+'</span></div>'
+'<div class="mo-px">$'+moN(c.price,2)+'</div><div class="mo-row"><span class="mo-tag" style="color:'+rc+';background:'+moTint(rc==='#6b7280'?'#6b7280':rc,.1)+'">'+c.regime+'</span><span class="mo-gdb">GDB <b>'+c.gdb+'</b></span></div>'
+'<div class="mo-row mo-med"><span>Median</span><span style="color:'+moC(c.med)+';display:flex;align-items:center;gap:4px">'+moTr(c.med>=0,moC(c.med),11)+moP(c.med)+'</span></div></div>'}
function moMacroCard(c){const sc=c.sig==='Bullish'?MO.teal:(c.sig==='Bearish'?MO.red:MO.mut),col=moC(c.chg),dec=c.kind==='btc'?2:4;
return '<div class="mo-c" style="border-left-color:'+(c.sig==='Neutral'?'#9CA3AF':sc)+'"><div class="mo-ct">'+c.title+'</div><div class="mo-row"><span class="mo-sym">'+c.sym+'</span><span class="mo-tag" style="color:'+sc+';background:'+moTint(sc,.1)+'">'+c.sig+'</span></div>'
+'<div class="mo-row" style="margin-top:12px"><span class="mo-val">'+moN(c.val,dec)+'</span><span style="color:'+col+';font-size:12.5px;font-weight:600;display:flex;align-items:center;gap:4px">'+moTr(c.chg>=0,col,11)+moP(c.chg)+'</span></div>'
+'<div class="mo-row mo-med" style="margin-top:12px"><span>21-Day MA</span><span style="color:'+(c.above?MO.teal:MO.red)+';font-weight:600">'+(c.above?'Above':'Below')+' '+(c.up?'↑':'↓')+'</span></div></div>'}
function moTile(ic,col,lab,val,sub,st,stc){return '<div class="mo-c" style="border-left-width:1px;padding:14px 16px"><div class="mo-ic" style="background:'+moTint(col,.1)+'">'+moSvg(MO_I[ic],col,16)+'</div><div class="mo-tl">'+lab+'</div><div class="mo-tv">'+val+'</div><div class="mo-ts">'+sub+'</div><div class="mo-tst" style="color:'+stc+'">'+st+'</div></div>'}
function moTier(v){const a=Math.abs(v);return a<20?['Neutral',0]:a<40?['Weak',.45]:a<60?['Moderate',.65]:a<80?['Strong',.85]:['Extreme',1]}
function moTierCol(v){const t=moTier(v);return t[1]===0?MO.gray:moTint(v>0?MO.teal:MO.red,t[1])}
function moGauge(v){const R0=36,cx=50,cy=46,pt=a=>[cx+R0*Math.cos(Math.PI*(1-a)),cy-R0*Math.sin(Math.PI*(1-a))],f=Math.max(.004,Math.min(.996,(v+100)/200)),p0=pt(0),p1=pt(1),pv=pt(f),col=moTierCol(v);
return '<svg viewBox="0 0 100 52" width="100%" style="max-width:112px;display:block;margin:0 auto"><path d="M'+p0[0]+' '+p0[1]+' A'+R0+' '+R0+' 0 0 1 '+p1[0]+' '+p1[1]+'" fill="none" stroke="#d1d5db" stroke-width="6" stroke-linecap="round"/><path d="M'+p0[0]+' '+p0[1]+' A'+R0+' '+R0+' 0 0 1 '+pv[0]+' '+pv[1]+'" fill="none" stroke="'+col+'" stroke-width="6" stroke-linecap="round"/></svg>'}
function moAD(rows,hiK,loK,hiL,loL){return rows.map(r=>{const a=r[hiK],d=r[loK],t=a+d,pa=t?a/t*100:0;
return '<div class="mo-ad"><div class="mo-adh"><div><b>'+r.etf+'</b><small>'+r.sub+'</small></div><span style="font-size:15px;font-weight:700;color:'+moC(r.net)+'">'+moP(r.net,1)+'</span></div>'
+'<div class="mo-bar"><i style="width:'+pa+'%;background:#20897D"><em style="font-style:normal">'+(pa>6?a:'')+'</em></i><i style="width:'+(100-pa)+'%;background:#D14B47;justify-content:flex-end">'+(pa<94?d:'')+'</i><u></u></div>'
+'<div class="mo-adf"><span style="color:#20897D">'+hiL+' '+a+'</span><span style="color:#D14B47">'+loL+' '+d+'</span></div></div>'}).join('')}
function moMcMini(M,k){const m=M.markets.find(x=>x.k===k),s=M.mc[k],col=s.signal==='Bullish'?MO.teal:MO.red;
return '<div class="mo-mini"><div class="mo-mh"><span><span class="mo-dot" style="background:'+m.color+'"></span>'+m.name+'</span><span style="display:flex;align-items:center;gap:6px;font-size:12px;color:'+moC(s.cur)+'">'+moS(s.cur,0)+' '+moTr(s.trend==='up',moC(s.cur),12)+'<span class="mo-bdg f" style="background:'+col+'">'+s.signal+'</span></span></div>'
+moLg([['#268BD2','MCSI'],['#B58900','SMA (10)']])+'<div class="mo-cv" style="height:150px"><canvas id="mo_mc1_'+k+'"></canvas></div>'+moLg([['#6B7280','MCO']])+'<div class="mo-cv" style="height:150px"><canvas id="mo_mc2_'+k+'"></canvas></div></div>'}
function moLg(items,extra){return '<div class="mo-lg">'+items.map(a=>'<span><span class="mo-dot" style="background:'+a[0]+';width:7px;height:7px;margin-right:5px"></span>'+a[1]+'</span>').join('')+(extra||'')+'</div>'}
function moBadge(t,c){return '<span class="mo-bdg" style="color:'+c+';border-color:'+c+';background:'+moTint(c,.05)+'">'+t+'</span>'}
function moHeat(v){if(v==null)return '';return v>=50?'rgba(32,137,125,'+((v-50)/50*.45+.04).toFixed(3)+')':'rgba(224,65,65,'+((50-v)/50*.6+.04).toFixed(3)+')'}
function moBox(st){const rows=Object.keys(st).map(s=>Object.assign({s:s},st[s])).sort((a,b)=>b.med-a.med);if(!rows.length)return '<div class="mo-empty">No sector data</div>';
let mn=Math.min.apply(null,rows.map(r=>r.lo)),mx=Math.max.apply(null,rows.map(r=>r.hi));mn=Math.floor(Math.min(mn,-1)-.4);mx=Math.ceil(Math.max(mx,1)+.4);
const W=900,L=170,RR=22,rh=50,T=26,H=T+rows.length*rh+34,X=v=>L+(v-mn)/(mx-mn)*(W-L-RR),step=Math.max(1,Math.ceil((mx-mn)/10));
let s='<svg viewBox="0 0 '+W+' '+H+'" width="100%" style="display:block">';
for(let v=Math.ceil(mn/step)*step;v<=mx;v+=step){s+='<line x1="'+X(v)+'" y1="'+(T-6)+'" x2="'+X(v)+'" y2="'+(H-30)+'" stroke="'+(v===0?'#9ca3af':'#eef0f2')+'" '+(v===0?'stroke-dasharray="4 4"':'')+'/><text x="'+X(v)+'" y="'+(H-12)+'" text-anchor="middle" font-size="11" fill="#374151">'+v.toFixed(1)+'%</text>'}
rows.forEach((r,i)=>{const y=T+i*rh+rh/2,c=r.med>=0?MO.teal:MO.red,bh=24;
s+='<text x="'+(L-12)+'" y="'+(y+4)+'" text-anchor="end" font-size="12.5" fill="#111827">'+r.s+'</text>'
+'<line x1="'+X(r.lo)+'" y1="'+y+'" x2="'+X(r.hi)+'" y2="'+y+'" stroke="'+c+'" stroke-width="1.6"/><line x1="'+X(r.lo)+'" y1="'+(y-6)+'" x2="'+X(r.lo)+'" y2="'+(y+6)+'" stroke="'+c+'" stroke-width="1.6"/><line x1="'+X(r.hi)+'" y1="'+(y-6)+'" x2="'+X(r.hi)+'" y2="'+(y+6)+'" stroke="'+c+'" stroke-width="1.6"/>'
+'<rect x="'+X(r.q1)+'" y="'+(y-bh/2)+'" width="'+Math.max(2,X(r.q3)-X(r.q1))+'" height="'+bh+'" fill="'+moTint(c,.13)+'" stroke="'+c+'" stroke-width="1.8"/><line x1="'+X(r.med)+'" y1="'+(y-bh/2)+'" x2="'+X(r.med)+'" y2="'+(y+bh/2)+'" stroke="'+c+'" stroke-width="2.4"/>'
+'<text x="'+X(r.med)+'" y="'+(r.med>=0?y-bh/2-6:y+bh/2+14)+'" text-anchor="middle" font-size="10.5" fill="'+c+'">'+moP(r.med,1)+'</text>'});
return s+'</svg>'}
function moRet(st){const rows=Object.keys(st).map(s=>Object.assign({s:s},st[s])).sort((a,b)=>b.mean-a.mean);if(!rows.length)return '<div class="mo-empty">No sector data</div>';
const mx=Math.max(1,Math.max.apply(null,rows.map(r=>Math.abs(r.mean))));
return rows.map(r=>{const w=Math.abs(r.mean)/mx*46,c=r.mean>=0?MO.teal:MO.red;return '<div style="display:flex;align-items:center;gap:10px;padding:7px 0;font-size:12.5px"><div style="width:170px;text-align:right;color:#111827">'+r.s+'</div><div style="flex:1;position:relative;height:20px"><div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:#d1d5db"></div><div style="position:absolute;top:2px;height:16px;background:'+c+';border-radius:2px;'+(r.mean>=0?'left:50%;':'right:50%;')+'width:'+w+'%"></div></div><div style="width:64px;color:'+c+';font-weight:600">'+moP(r.mean,2)+'</div></div>'}).join('')}
function moTabs(items,cur,fn){return items.map(i=>'<span class="'+(i[0]===cur?'a':'')+'" onclick="'+fn+'(\''+i[0]+'\')">'+i[1]+'</span>').join('')}
function moSetExt(k){if(k==='std'||k==='norm')moSt.extMode=k;else moSt.ext=k;moExtDraw(true)}
function moSetNF(k){moSt.nf=k;moNormDraw(true)}
function moSetSec(k){if(k==='box'||k==='ret')moSt.secView=k;else moSt.sec=k;moSecDraw()}
const MO_GC={NDX:'#CB4B16',SPX:'#B58900',NYSE:'#268BD2',RUT:'#6C71C4'};
const MO_MA=[['5 SMA','#6B7280'],['10 EMA','#6C71C4'],['21 EMA','#268BD2'],['50 SMA','#B58900']];
function moExtDraw(full){const M=R.mo,k=moSt.ext,E=M.ext,std=moSt.extMode==='std';
const mk=M.markets.map(m=>[m.k,m.name]);
document.getElementById('moExtTabs').innerHTML=moTabs(mk,k,'moSetExt')+'<i></i>'+moTabs([['std','Standard'],['norm','Normalized']],moSt.extMode,'moSetExt');
const src=(std?E.std:E.norm)[k]||{};
const sets=MO_MA.map(a=>({label:a[0],tag:a[0],color:a[1],data:src[a[0]]||[]}));
const hl=std?[{y:75,color:'#E04141',tag:'OB'},{y:25,color:'#20897D',tag:'OS'}]:[{y:2,color:'#E04141',tag:'2σ'},{y:1,color:'#E04141',tag:'1σ',dash:1},{y:-1,color:'#20897D',tag:'-1σ',dash:1},{y:-2,color:'#20897D',tag:'-2σ'}];
moLine('mo_ext',E.dates,sets,{hl:hl,smin:std?20:-2.5,smax:std?80:2.5,ticks:7});
document.getElementById('moExtTbl').innerHTML='<tr><th>MARKET</th>'+MO_MA.map(a=>'<th>'+a[0]+'</th>').join('')+'</tr>'+M.markets.map(m=>'<tr><td><span class="mo-dot" style="background:'+m.color+'"></span>'+m.name+'</td>'+MO_MA.map(a=>{const v=E.last[m.k][a[0]];return '<td class="mo-hv" style="background:'+moHeat(v)+'">'+(v==null?'-':v.toFixed(1)+'%')+'</td>'}).join('')+'</tr>').join('')}
function moSecDraw(){const M=R.mo;document.getElementById('moSecTabs').innerHTML=moTabs(M.markets.map(m=>[m.k,m.name]),moSt.sec,'moSetSec')+'<i></i>'+moTabs([['box','Box Plot'],['ret','Returns']],moSt.secView,'moSetSec');
const st=M.sect[moSt.sec]||{};document.getElementById('moSecBody').innerHTML=moSt.secView==='box'?moBox(st):moRet(st)}
function moNormDraw(full){const M=R.mo,N=M.mcn,flt=moSt.nf;
document.getElementById('moNF').innerHTML=moTabs([['ALL','All']].concat(M.markets.map(m=>[m.k,{NDX:'NDX',SPX:'SPX',NYSE:'NYSE',RUT:'RUT'}[m.k]])),flt,'moSetNF');
const mk=(src,sm)=>{const out=[];M.markets.forEach(m=>{const hid=flt!=='ALL'&&flt!==m.k;out.push({label:m.name,tag:m.name,color:m.color,data:src[m.k]||[],hidden:hid});out.push({label:m.name+' SMA (10)',color:m.color,data:(sm&&sm[m.k])||[],hidden:hid,dash:[2,3],w:1.1})});return out};
const hl=[{y:2,color:'#E04141',tag:'2σ'},{y:1,color:'#E04141',tag:'1σ',dash:1},{y:-1,color:'#20897D',tag:'-1σ',dash:1},{y:-2,color:'#20897D',tag:'-2σ'}];
moLine('mo_nm1',N.dates,mk(N.mcsi,N.mcsi_sma),{hl:hl,smin:-2.5,smax:2.5,ticks:7});moLine('mo_nm2',N.dates,mk(N.mco,N.mco_sma),{hl:hl,smin:-2.5,smax:2.5,ticks:7})}
function renderMO(){const w=document.getElementById('moWrap');if(!w)return;const M=R.mo;moKill('mo');
if(!M){w.innerHTML='<div class="mo-h">Market Overview</div><div class="mo-empty">Market Overview data is not available (index member lists or price history could not be loaded).</div>';return}
const T=M.tiles,G=M.gdb,mkAll=M.markets;
let h='<div class="mo-h">Market Overview</div><div class="mo-note">'+M.asof+' close · end-of-day breadth over '+M.universe+' stocks · NYSE = NYSE-listed S&amp;P 1500 stocks · Russell 2000 = S&amp;P 600 proxy · GDB = 1.5 × net advance % (approximation)</div>';
h+='<div class="mo-r7">'+M.cards.map(moIdxCard).join('')+M.macro_cards.map(moMacroCard).join('')+'</div>';
const sc=moC(T.sent),ec=T.ext_net<0?MO.red:MO.teal,mc=Math.abs(T.mom)>=60?(T.mom<0?MO.red:MO.teal):MO.mut,spct=T.sec_n?Math.round(T.sec_pos/T.sec_n*100):0,pc=spct>50?MO.teal:(spct<50?MO.red:MO.mut);
h+='<div class="mo-r5">'
+moTile('act',sc,'SENTIMENT',moP(T.sent,1),moS(T.net_adv,0)+' net adv',(T.sent<0?'↓ ':'↑ ')+moN(T.adv,0)+' vs '+moN(T.dec,0),sc)
+moTile('tgt',ec,'NEW EXTREMES',moN(Math.abs(T.ext_net),0),T.ext_net<0?'Net new lows':'Net new highs',(T.ext_net<0?'↓ ':'↑ ')+T.hi+' H, '+T.lo+' L',ec)
+moTile('bar',MO.mut,'LT HEALTH',T.lt==null?'-':T.lt+'%','Above 200-day MA',T.lt_tr==='Improving'?'↑ Improving':(T.lt_tr==='Weakening'?'— Weakening':'— Stable'),T.lt_tr==='Improving'?MO.teal:(T.lt_tr==='Weakening'?MO.red:MO.mut))
+moTile('zap',moC(T.mom),'MOMENTUM',moS(T.mom,0),'GDB Percentile',Math.abs(T.mom)>=60?((T.mom<0?'↓ ':'↑ ')+'Extreme reading'):'Normal conditions',mc)
+moTile('up',MO.mut,'SECTORS',T.sec_pos+'/'+T.sec_n,'Positive sectors',(spct<50?'↓ ':(spct>50?'↑ ':'— '))+spct+'% showing strength',pc)+'</div>';
// GDB
const avg=G.avg,at=moTier(avg),gk=mkAll.map(m=>m.k);
const gl={NDX:['QQQE','NASDAQ 100'],SPX:['RSP','S&P 500'],NYSE:['NYSE','NYSE'],RUT:['IWM','Russell 2K']};
h+='<div class="mo-g21"><div class="mo-p"><div class="mo-ph">Global Daily Breadth<span style="font-size:11px;font-weight:400;color:#9ca3af">daily close · -100 to +100</span></div><div class="mo-pb">'+moLg(gk.map(k=>[MO_GC[k],gl[k][0]]))+'<div class="mo-cv" style="height:340px"><canvas id="mo_gdb"></canvas></div></div></div>'
+'<div class="mo-p"><div class="mo-ph">Market Indices GDB</div><div class="mo-pb" style="text-align:center"><div style="font-size:30px;font-weight:700;letter-spacing:-.6px;margin-top:6px">'+moS(avg,1)+'</div><div style="font-size:12px;color:#374151">Market Average</div><div style="font-size:12px;font-weight:600;color:'+moTierCol(avg)+';margin:3px 0 14px">'+(avg<0?'↘ ':'↗ ')+at[0]+'</div>'
+'<div class="mo-gg">'+gk.map(k=>{const v=G.last[k],t=moTier(v);return '<div>'+moGauge(v)+'<div class="gv">'+v+'</div><div class="gt">'+gl[k][0]+'</div><div class="gs">'+gl[k][1]+'</div><div class="gl" style="color:'+moTierCol(v)+'">'+t[0]+'</div></div>'}).join('')+'</div></div></div></div>';
// A/D + H/L
const rowsA=gk.map(k=>Object.assign({etf:gl[k][0],sub:gl[k][1]},M.adv[k])),rowsH=gk.map(k=>Object.assign({etf:gl[k][0],sub:gl[k][1],adv:M.hl[k].hi,dec:M.hl[k].lo,net:M.hl[k].net},{}));
h+='<div class="mo-g11"><div class="mo-p"><div class="mo-ph">Advances vs Declines<span style="color:'+moC(M.adv_total)+';font-size:13px;display:flex;align-items:center;gap:5px">'+moTr(M.adv_total>=0,moC(M.adv_total),13)+moP(M.adv_total,1)+'</span></div><div class="mo-pb" style="display:grid;grid-template-columns:1fr 1fr;gap:6px 22px">'+moAD(rowsA,'adv','dec','Adv','Dec')+'</div></div>'
+'<div class="mo-p"><div class="mo-ph">New Highs vs Lows<span style="color:'+moC(M.hl_total)+';font-size:13px;display:flex;align-items:center;gap:5px">'+moTr(M.hl_total>=0,moC(M.hl_total),13)+moP(M.hl_total,1)+'</span></div><div class="mo-pb" style="display:grid;grid-template-columns:1fr 1fr;gap:6px 22px">'+moAD(rowsH,'adv','dec','Highs','Lows')+'</div></div></div>';
// McClellan
const N=M.mcn,sig=(s)=>s==='Improving'?MO.teal:(s==='Weakening'?'#B58900':MO.red),lc=N.label.indexOf('Bull')>=0?MO.teal:(N.label==='Mixed'?'#B58900':MO.red);
h+='<div class="mo-g11"><div class="mo-p"><div class="mo-ph">McClellan Standard</div><div class="mo-pb"><div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'+gk.map(k=>moMcMini(M,k)).join('')+'</div>'
+'<table class="mo-tbl" style="margin-top:14px"><tr><th>Market</th><th>Current</th><th>1W</th><th>1M</th><th>Trend</th><th>Signal</th></tr>'+mkAll.map(m=>{const s=M.mc[m.k];return '<tr><td><span class="mo-dot" style="background:'+m.color+'"></span>'+m.name+'</td><td style="color:'+moC(s.cur)+'">'+moS(s.cur,0)+'</td><td style="color:'+moC(s.w1)+'">'+moS(s.w1,0,true)+'</td><td style="color:'+moC(s.m1)+'">'+moS(s.m1,0,true)+'</td><td>'+moTr(s.trend==='up',s.trend==='up'?MO.teal:MO.red,15)+'</td><td>'+moBadge(s.signal,s.signal==='Bullish'?MO.teal:MO.red)+'</td></tr>'}).join('')+'</table></div></div>'
+'<div class="mo-p"><div class="mo-ph"><span style="display:flex;align-items:center;gap:10px">McClellan Normalized<span class="mo-pill" style="color:'+lc+';background:'+moTint(lc,.1)+'">'+N.label.toUpperCase()+'</span><span style="font-size:11px;font-weight:400;color:#6b7280">'+N.n_bull+'/'+N.n+' bullish · '+N.n_imp+'/'+N.n+' improving</span></span><span class="mo-tabs" id="moNF"></span></div><div class="mo-pb">'
+''+moLg(mkAll.map(m=>[m.color,m.name]),'<span style="color:#9ca3af">dashed ±1σ · solid ±2σ · dotted SMA (10)</span>')+'<div class="mo-sec" style="margin-top:6px;color:#111827;font-size:12.5px;letter-spacing:0">Normalized MCSI</div><div class="mo-cv" style="height:290px"><canvas id="mo_nm1"></canvas></div><div class="mo-cap">Z-score normalized cumulative breadth trend</div>'
+'<div class="mo-sec" style="color:#111827;font-size:12.5px;letter-spacing:0">Normalized MCO</div><div class="mo-cv" style="height:290px"><canvas id="mo_nm2"></canvas></div><div class="mo-cap">Z-score normalized breadth momentum</div>'
+'<div class="mo-sec">Z-SCORE SUMMARY</div><table class="mo-tbl"><tr><th>Market</th><th>MCSI</th><th>MCO</th><th>Trend</th><th>Signal</th></tr>'+N.summary.map(s=>{const m=mkAll.find(x=>x.k===s.k);return '<tr><td><span class="mo-dot" style="background:'+m.color+'"></span>'+m.name+'</td><td style="color:'+moC(s.mcsi)+'">'+moS(s.mcsi,2)+'</td><td style="color:'+moC(s.mco)+'">'+moS(s.mco,2)+'</td><td>'+moTr(s.trend==='up',s.trend==='up'?MO.teal:MO.red,15)+'</td><td>'+moBadge(s.signal,sig(s.signal))+'</td></tr>'}).join('')+'</table></div></div></div>';
// Breadth Extensions + Sector Performance
h+='<div class="mo-g11"><div class="mo-p"><div class="mo-ph">Breadth Extensions<span class="mo-tabs" id="moExtTabs"></span></div><div class="mo-pb"><div class="mo-cv" style="height:360px"><canvas id="mo_ext"></canvas></div>'
+'<div class="mo-leg">'+MO_MA.map(a=>'<span><span class="mo-dot" style="background:'+a[1]+'"></span>'+a[0]+'</span>').join('')+'</div><table class="mo-tbl" id="moExtTbl"></table></div></div>'
+'<div class="mo-p"><div class="mo-ph">Sector Performance<span class="mo-tabs" id="moSecTabs"></span></div><div class="mo-pb" id="moSecBody" style="min-height:420px"></div></div></div>';
w.innerHTML=h;
gk.forEach(k=>{const s=M.mc[k];moLine('mo_mc1_'+k,s.dates,[{label:'SMA (10)',tag:'SMA (10)',color:'#B58900',data:s.sma},{label:'MCSI',tag:'MCSI',color:'#268BD2',data:s.mcsi}],{gap:24});
moLine('mo_mc2_'+k,s.dates,[{label:'MCO',tag:'MCO',color:'#6B7280',data:s.mco,w:1.1}],{gap:24,hl:[{y:0,color:'#9ca3af',dash:1}]})});
const gc=MO_GC;
moLine('mo_gdb',G.dates,gk.map(k=>({label:gl[k][0],tag:gl[k][0],color:gc[k],data:G.series[k],w:1.6})),{ticks:8,hl:[{y:0,color:'#9ca3af',dash:1}]});
moNormDraw();moExtDraw();moSecDraw()}
// ===== MARKET INTERNALS =====
const MI_TF={'3M':63,'6M':126,'1Y':252,'2Y':504,'5Y':1260},MI_TFK=['3M','6M','1Y','2Y','5Y'];
let miSt={tf:'6M',cols:2,removed:[],added:[],span:[]};
try{const s=JSON.parse(localStorage.getItem('usd_mi_state')||'null');if(s&&MI_TF[s.tf]){miSt=Object.assign(miSt,s)}}catch(e){}
function miSave(){try{localStorage.setItem('usd_mi_state',JSON.stringify(miSt))}catch(e){}}
function miFmt(v,nd){return v.toLocaleString('en-US',{minimumFractionDigits:nd>2?nd:(v>=1000?(nd>0?nd:0):nd),maximumFractionDigits:nd>2?nd:(v>=1000?(nd>0?nd:0):nd)})}
function miSetTF(t){miSt.tf=t;miSave();renderMI()}
function miZoom(dir){let i=MI_TFK.indexOf(miSt.tf)+dir;i=Math.max(0,Math.min(MI_TFK.length-1,i));miSt.tf=MI_TFK[i];miSave();renderMI()}
function miGrid(){miSt.cols=miSt.cols===2?1:2;miSave();renderMI()}
function miRemove(id){if(miSt.added.indexOf(id)>=0)miSt.added=miSt.added.filter(x=>x!==id);else if(miSt.removed.indexOf(id)<0)miSt.removed.push(id);miSave();renderMI()}
function miAddCard(id){miSt.removed=miSt.removed.filter(x=>x!==id);if(R.mi.cards.every(c=>c.id!==id)&&miSt.added.indexOf(id)<0)miSt.added.push(id);miSave();renderMI()}
function miSpan(id){const i=miSt.span.indexOf(id);if(i>=0)miSt.span.splice(i,1);else miSt.span.push(id);miSave();renderMI()}
function miMenu(){const m=document.getElementById('miMenu');m.style.display=m.style.display==='block'?'none':'block'}
function miNice(r){const e=Math.pow(10,Math.floor(Math.log10(r))),f=r/e;return (f<1.5?1:f<3?2:f<7?5:10)*e}
function miBars(card){const tf=miSt.tf,n=card.c.length,st=Math.max(0,n-MI_TF[tf]),k=2/22;let e=card.c[0],eh=card.h[0],el=card.l[0];const E=[],EH=[],EL=[];
for(let i=0;i<n;i++){if(i>0){e=card.c[i]*k+e*(1-k);eh=card.h[i]*k+eh*(1-k);el=card.l[i]*k+el*(1-k)}E.push(e);EH.push(eh);EL.push(el)}
let bars=[];for(let i=st;i<n;i++)bars.push({d:card.d[i],o:card.o[i],h:card.h[i],l:card.l[i],c:card.c[i],e:E[i],eh:EH[i],el:EL[i]});
if(tf==='2Y'||tf==='5Y'){const g={},ord=[];bars.forEach(b=>{const wk=Math.floor((Date.parse(b.d)/86400000+3)/7);if(!g[wk]){g[wk]=Object.assign({},b);ord.push(wk)}else{const x=g[wk];x.h=Math.max(x.h,b.h);x.l=Math.min(x.l,b.l);x.c=b.c;x.d=b.d;x.e=b.e;x.eh=b.eh;x.el=b.el}});bars=ord.map(w=>g[w])}
return bars}
function miPaint(cv,card,hi){const bars=cv._bars,dpr=window.devicePixelRatio||1,W=cv.parentElement.clientWidth,H=300;cv.width=W*dpr;cv.height=H*dpr;cv.style.height=H+'px';
const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,W,H);
const pl=6,pr=64,pt=12,pb=24,cw=(W-pl-pr)/bars.length;let mn=Infinity,mx=-Infinity;bars.forEach(b=>{mn=Math.min(mn,b.l,b.el);mx=Math.max(mx,b.h,b.eh)});const mg=(mx-mn)*.05||1;mn-=mg;mx+=mg;
const Y=v=>pt+(H-pt-pb)*(1-(v-mn)/(mx-mn)),X=i=>pl+(i+.5)*cw,step=miNice((mx-mn)/6),nd=card.nd;
ctx.font='11px -apple-system,system-ui,sans-serif';ctx.textBaseline='middle';ctx.textAlign='left';
for(let v=Math.ceil(mn/step)*step;v<=mx;v+=step){const y=Y(v);ctx.strokeStyle='rgba(15,23,42,.06)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pl,y);ctx.lineTo(W-pr,y);ctx.stroke();ctx.fillStyle='rgba(15,23,42,.65)';ctx.fillText(v.toFixed(step<.1?Math.min(4,Math.ceil(-Math.log10(step))+1):2),W-pr+8,y)}
// 21 EMA cloud
ctx.beginPath();bars.forEach((b,i)=>{i?ctx.lineTo(X(i),Y(b.eh)):ctx.moveTo(X(i),Y(b.eh))});for(let i=bars.length-1;i>=0;i--)ctx.lineTo(X(i),Y(bars[i].el));ctx.closePath();ctx.fillStyle='rgba(15,23,42,.10)';ctx.fill();
ctx.beginPath();bars.forEach((b,i)=>{i?ctx.lineTo(X(i),Y(b.e)):ctx.moveTo(X(i),Y(b.e))});ctx.strokeStyle='rgba(234,88,12,.85)';ctx.lineWidth=1.3;ctx.stroke();
// mumlar
const bw=Math.max(1,Math.min(14,cw*.66));bars.forEach((b,i)=>{const up=b.c>=b.o,col=up?'#20897D':'#E04141',x=X(i);ctx.strokeStyle=col;ctx.fillStyle=col;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,Y(b.h));ctx.lineTo(x,Y(b.l));ctx.stroke();const y1=Y(Math.max(b.o,b.c)),y2=Y(Math.min(b.o,b.c));ctx.fillRect(x-bw/2,y1,bw,Math.max(1,y2-y1))});
// x etiketleri
ctx.fillStyle='rgba(15,23,42,.7)';ctx.textAlign='center';ctx.font='600 11px -apple-system,system-ui,sans-serif';const mn3=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];let pm='',lastx=-99;
bars.forEach((b,i)=>{const mo=b.d.slice(5,7);if(mo!==pm){pm=mo;const x=X(i);if(x-lastx>34){ctx.fillText(mo==='01'?b.d.slice(0,4):mn3[+mo-1],x,H-10);lastx=x}}});
if(hi>=0&&hi<bars.length){const x=X(hi),b=bars[hi];ctx.strokeStyle='rgba(15,23,42,.35)';ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x,pt);ctx.lineTo(x,H-pb);ctx.stroke();ctx.setLineDash([]);
const tip=document.getElementById('mi_tip_'+card.id);if(tip){tip.style.display='block';tip.textContent=b.d+'  O '+miFmt(b.o,nd)+'  H '+miFmt(b.h,nd)+'  L '+miFmt(b.l,nd)+'  C '+miFmt(b.c,nd)}}}
function miDraw(card){const cv=document.getElementById('mi_cv_'+card.id);if(!cv)return;cv._bars=miBars(card);miPaint(cv,card,-1);
cv.onmousemove=function(e){const r=cv.getBoundingClientRect(),cw=(r.width-70)/cv._bars.length;miPaint(cv,card,Math.floor((e.clientX-r.left-6)/cw))};
cv.onmouseleave=function(){const tip=document.getElementById('mi_tip_'+card.id);if(tip)tip.style.display='none';miPaint(cv,card,-1)}}
function miCardHtml(c){const col=c.color,last=c.c[c.c.length-1];
return '<div class="mi-card" style="'+(miSt.span.indexOf(c.id)>=0?'grid-column:1/-1':'')+'"><div class="mi-ch"><div><div class="mi-ct"><span class="mo-dot" style="background:'+col+';margin:0"></span>'+c.title+(c.tag?'<small>'+c.tag+'</small>':'')+'</div><div class="mi-cs">'+c.sub+'</div></div>'
+'<div class="mi-cr"><span class="mi-v" style="color:'+col+';background:'+moTint(col,.1)+'">'+miFmt(last,c.nd)+'</span><span>'+c.sym+'</span><span class="mi-x" title="Expand" onclick="miSpan(\''+c.id+'\')">'+moSvg(MO_I.zin,'currentColor',14)+'</span><span class="mi-x" title="Remove" onclick="miRemove(\''+c.id+'\')">'+moSvg(MO_I.x,'currentColor',15)+'</span></div></div>'
+'<div class="mi-cv"><canvas id="mi_cv_'+c.id+'"></canvas><div class="mi-tip" id="mi_tip_'+c.id+'"></div></div></div>'}
function renderMI(){const w=document.getElementById('miWrap');if(!w)return;const I=R.mi;
if(!I){w.innerHTML='<div class="mo-h">Market Internals</div><div class="mo-empty">Market Internals data is not available (macro price history could not be loaded).</div>';return}
const all=I.cards.concat(I.extras),byId={};all.forEach(c=>{byId[c.id]=c});
const show=I.cards.filter(c=>miSt.removed.indexOf(c.id)<0).concat(miSt.added.map(i=>byId[i]).filter(Boolean));
const avail=all.filter(c=>show.indexOf(c)<0);
let h='<div class="mi-top"><div class="mo-h" style="margin:0">Market Internals</div><div class="mi-ctl">'+MI_TFK.map(t=>'<button class="mi-b'+(t===miSt.tf?' a':'')+'" onclick="miSetTF(\''+t+'\')">'+t+'</button>').join('')
+'<span class="mi-ib" onclick="miZoom(1)" title="Zoom out">'+moSvg(MO_I.zout,'currentColor',16)+'</span><span class="mi-ib" onclick="miZoom(-1)" title="Zoom in">'+moSvg(MO_I.zin,'currentColor',16)+'</span><span class="mi-ib" onclick="miGrid()" title="Layout">'+moSvg(MO_I.grid,'currentColor',15)+'</span>'
+'<button class="mi-add" onclick="miMenu()">+ ADD</button><div class="mi-menu" id="miMenu" style="display:none">'+(avail.length?avail.map(c=>'<div onclick="miAddCard(\''+c.id+'\')">'+c.title+'<small>'+c.sym+'</small></div>').join(''):'<div style="color:#9ca3af;cursor:default">All cards are shown</div>')+'</div></div></div>';
if(I.ai){const a=I.ai,ac=a.label==='RISK-ON'?MO.teal:(a.label==='RISK-OFF'?MO.red:'#D98E04');
h+='<div class="mi-ai"><div class="mi-aih"><span class="mi-aiic">'+moSvg(MO_I.spark,'#D98E04',14)+'</span><span class="mi-ait">AI INSIGHTS</span><span style="color:#d1d5db">|</span><span class="mo-pill" style="color:'+ac+';background:'+moTint(ac.length===7?ac:'#D98E04',.12)+'">● '+a.label+'</span><span style="flex:1"></span><span class="mi-ib" onclick="renderMI()" title="Refresh">'+moSvg(MO_I.ref,'currentColor',14)+'</span></div><div class="mi-aip">'+a.text+'</div><div class="mi-aig">'+a.tags.map(t=>'<span>'+t+'</span>').join('')+'</div></div>'}
h+='<div class="mi-grid" style="grid-template-columns:repeat('+miSt.cols+',minmax(0,1fr))">'+show.map(miCardHtml).join('')+'</div>';
w.innerHTML=h;show.forEach(miDraw)}
// ===== RENDER =====
function render(){renderIdx();renderBars();renderTable();renderThemes();
document.getElementById('hm').textContent=new Date().toLocaleString('en-US')+' · '+Object.keys(R.sector_map).length+' stocks · '+Object.keys(R.sector_perf).length+' sectors';
document.getElementById('ft').textContent='Last update: '+new Date().toLocaleString('en-US')}
render();
</script></body></html>"""

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    log("=" * 55)
    log("US SECTOR DASHBOARD  v3.4  (Market Overview · Market Internals · Lab Report · çeyreklik financials)")
    log(f"Klasör: {BASE_DIR}")
    log("=" * 55)

    # 1) Universe
    universe = get_universe()
    csv_meta = load_theme_csv()

    # 2) OHLC cache
    ohlc_cache = load_json(OHLC_CACHE)
    log(f"OHLC cache: {len(ohlc_cache)} ticker zaten kayıtlı")
    members = get_index_members()
    extra_t = [t for t in (members.get("SML") or {}) if t not in universe]      # S&P 600: sadece breadth için, evrene girmez
    ohlc_cache = update_ohlc_cache(universe + [e for e in LAB_ETFS if e not in universe] + extra_t, ohlc_cache)
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
    spy_dates  = [r["d"] for r in spy_recs] if spy_recs else None
    stock_rsms = calc_rsms(stock_ohlc, spy_closes, spy_dates)
    if _RSMS_STALE:
        log(f"  ⚠️  RS-MS dışı (cache SPY'dan 4+ gün geride): {len(_RSMS_STALE)} ticker — {', '.join(sorted(_RSMS_STALE)[:12])}")
    log(f"  RS5  : {len(stock_rs5)}")
    log(f"  RS21 : {len(stock_rs21)}")
    log(f"  RS-MS: {len(stock_rsms)}  (newly IPO'd <6mo excluded by design)")

    # 7) Sektörler
    log("Sektör aggregate...")
    # Industry: CSV varsa CSV, yoksa Yahoo (cache'e yazılmaz, sadece gruplama için)
    meta_view = {t: ({**m, "industry": csv_meta[t]["industry"]} if csv_meta.get(t, {}).get("industry") else m)
                 for t, m in meta_cache.items()}
    sectors, sector_map, sector_perf = build_sectors(meta_view, ohlc_cache, stock_perf)
    log(f"  → {len(sectors)} sektör (>=2 ticker), {len(sector_map)} ticker maplendi")

    # Sektör RS (medyan)
    sec_rs5  = calc_sector_rs(sector_perf, sectors, stock_rs5,  "rs5")
    sec_rs21 = calc_sector_rs(sector_perf, sectors, stock_rs21, "rs21")
    sec_rsms = calc_sector_rs(sector_perf, sectors, stock_rsms, "rsms")

    # 7b) Theme grupları (CSV) + Lab Report
    themes, theme_perf, theme_rsms = build_theme_groups(csv_meta, sector_map, stock_perf, stock_rsms)
    theme_map = {t: csv_meta[t]["theme"] for t in sector_map if csv_meta.get(t, {}).get("theme")}
    if csv_meta:
        miss = sorted(t for t in sector_map if t not in csv_meta)
        log(f"  CSV eşleşme    : {len(sector_map) - len(miss)}/{len(sector_map)} hisse (theme'li: {len(theme_map)}, {len(themes)} theme)")
        if miss:
            log(f"    CSV'de olmayan : {', '.join(miss[:20])}{' ...' if len(miss) > 20 else ''}")
    log("Lab Report hesaplanıyor...")
    lab = build_lab(stock_ohlc, stock_rsms, stock_perf, csv_meta, meta_cache, ohlc_cache, spy_closes, spy_dates)
    if lab:
        log(f"  → {lab['n_leaders']} lider, buy area: {lab['counts']['buy']}, setup: {len(lab['setups']['buy'])}")

    # 8) Quarterly financials — YILDA 4 KEZ: çeyrek sonundan (Mar/Haz/Eyl/Ara) sonraki ilk çalıştırmada çekilir,
    #    diğer tüm çalıştırmalarda cache'teki önceki veri kullanılır. Zorla yenilemek için: --refresh-fin
    fin_cache = load_json(FIN_CACHE)     # {ticker: {"r": [...], "d": son başarılı çekim, "a": son deneme}}
    today_d   = date.today()
    qe_str    = _last_quarter_end(today_d).strftime("%Y-%m-%d")
    force_fin = "--refresh-fin" in sys.argv
    fin_tickers = list(sector_map.keys())
    def _days_ago(s):
        return (today_d - datetime.strptime(s, "%Y-%m-%d").date()).days
    def _fin_stale(t):
        e = fin_cache.get(t)
        if force_fin or not e:
            return True
        if e.get("d") and e["d"] >= qe_str:
            return False                                  # bu çeyrekte zaten çekilmiş
        return (not e.get("a")) or _days_ago(e["a"]) >= 7  # başarısız denemeyi haftada bir tekrar dene
    todo = [t for t in fin_tickers if _fin_stale(t)]
    log(f"Quarterly financials: {len(fin_tickers) - len(todo)} ticker cache'ten (son çeyrek sonu {qe_str}), {len(todo)} çekilecek")
    if todo:
        def fetch_fin(t):
            return t, _fetch_quarterly_financials(t)
        today_s = today_d.strftime("%Y-%m-%d")
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
            futures = {ex.submit(fetch_fin, t): t for t in todo}
            done = 0
            for f in as_completed(futures):
                t, recs = f.result()
                done += 1
                if recs:
                    fin_cache[t] = {"r": recs, "d": today_s, "a": today_s}
                else:   # başarısız/boş: eski veri varsa koru, sadece deneme tarihini işle
                    old = fin_cache.get(t) or {"r": [], "d": None}
                    fin_cache[t] = {"r": old.get("r", []), "d": old.get("d"), "a": today_s}
                if done % 40 == 0:
                    log(f"  Fins: {done}/{len(todo)}")
                    save_json(FIN_CACHE, fin_cache)
        save_json(FIN_CACHE, fin_cache)
    fins = {t: fin_cache[t]["r"] for t in fin_tickers if fin_cache.get(t, {}).get("r")}
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

    # 9b) Market Overview + Market Internals (breadth, McClellan, makro)
    log("Makro semboller + breadth hesaplanıyor...")
    try:
        macro = update_macro_cache(MO_MACRO)
        mo = build_market_overview(ohlc_cache, members, exchange_map, macro, meta_cache)
        dbg = mo.pop("dbg", None) if mo else None
        if dbg is not None:
            dbg.to_csv(BREADTH_DEBUG, index=False)
            log(f"  Breadth detay  : {os.path.basename(BREADTH_DEBUG)} (son 45 gün)")
        mi = build_market_internals(macro)
        log(f"  → Market Overview: {'hazır' if mo else 'veri yok'} | Market Internals: {'hazır' if mi else 'veri yok'}")
    except Exception as e:
        log(f"  ⚠️  Market Overview/Internals hesaplanamadı: {e}")
        mo, mi = None, None

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
        "ohlc":         {t: ohlc_cache[t][-294:] for t in sector_map if t in ohlc_cache},   # son ~14 ay (eski davranış)
        "exchange_map": exchange_map,
        "theme_map":    theme_map,
        "theme_perf":   theme_perf,
        "theme_rsms":   theme_rsms,
        "themes":       themes,
        "lab":          lab,
        "mo":           mo,
        "mi":           mi,
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
