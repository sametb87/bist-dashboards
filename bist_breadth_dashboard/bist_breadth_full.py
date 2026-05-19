#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BIST MARKET BREADTH v6.0 - 613 HİSSE + SEKTÖR ENDEKSLERİ
===========================================================
  python3 bist_breadth_full.py              # İlk kurulum: 10 yıl (~40dk)
  python3 bist_breadth_full.py --update     # Günlük: eksik günler (1-2 dk)
  python3 bist_breadth_full.py --dashboard  # Dashboard yenile (2 sn)
"""
import json,os,sys,time
from datetime import datetime,timedelta
from pathlib import Path

def chk():
    m=[]
    for p in["yfinance","pandas","numpy"]:
        try:__import__(p)
        except:m.append(p)
    if m:print(f"\n  pip3 install {' '.join(m)}");sys.exit(1)
chk()
import yfinance as yf
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor,as_completed

SD=Path(os.path.dirname(os.path.abspath(__file__)))
DF=SD/"breadth_data.json"
IF=SD/"index_data.json"
HF=SD/"bist_breadth_dashboard.html"

TK=[
    "A1CAP","ACSEL","ADEL","ADESE","ADGYO","AEFES","AFYON","AGESA","AGHOL","AGROT",
    "AGYO","AHGAZ","AHSGY","AKBNK","AKCNS","AKENR","AKFGY","AKFIS","AKFYE","AKGRT",
    "AKMGY","AKSA","AKSEN","AKSGY","AKSUE","AKYHO","ALARK","ALBRK","ALCAR","ALCTL",
    "ALFAS","ALGYO","ALKA","ALKIM","ALKLC","ALTNY","ALVES","ANELE","ANGEN",
    "ANHYT","ANSGR","ARASE","ARCLK","ARDYZ","ARENA","ARMGD","ARSAN","ARTMS","ARZUM",
    "ASELS","ASGYO","ASTOR","ASUZU","ATAGY","ATAKP","ATATP","ATEKS","ATLAS","ATSYH",
    "AVGYO","AVHOL","AVOD","AVPGY","AVTUR","AYCES","AYDEM","AYEN","AYES","AYGAZ",
    "AZTEK","BAGFS","BAHKM","BAKAB","BALAT","BALSU","BANVT","BARMA","BASCM","BASGZ",
    "BAYRK","BEGYO","BERA","BEYAZ","BFREN","BIENY","BIGCH","BIGEN","BIMAS","BINBN",
    "BINHO","BIOEN","BIZIM","BJKAS","BLCYT","BMSCH","BMSTL","BNTAS","BOBET","BORLS",
    "BORSK","BOSSA","BRISA","BRKO","BRKSN","BRKVY","BRLSM","BRMEN","BRSAN","BRYAT",
    "BSOKE","BTCIM","BUCIM","BULGS","BURCE","BURVA","BVSAN","BYDNR","CANTE","CASA",
    "CATES","CCOLA","CELHA","CEMAS","CEMTS","CEMZY","CEOEM","CGCAM","CIMSA","CLEBI",
    "CMBTN","CMENT","CONSE","COSMO","CRDFA","CRFSA","CUSAN","CVKMD","CWENE",
    "DAGI","DAPGM","DARDL","DCTTR","DENGE","DERHL","DERIM","DESA","DESPC","DEVA",
    "DGATE","DGGYO","DGNMO","DIRIT","DITAS","DMRGD","DMSAS","DNISI","DOAS",
    "DOCO","DOFER","DOGUB","DOHOL","DOKTA","DSTKF","DURDO","DURKN","DYOBY","DZGYO",
    "EBEBK","ECILC","ECZYT","EDATA","EDIP","EGEEN","EGEGY","EGEPO","EGGUB",
    "EGPRO","EGSER","EKGYO","EKIZ","EKOS","EKSUN","ELITE","EMKEL","EMNIS","ENDAE",
    "ENERY","ENJSA","ENKAI","ENSRI","ENTRA","EPLAS","ERBOS","ERCB","EREGL","ERSU",
    "ESCAR","ESCOM","ESEN","ETILR","ETYAT","EUHOL","EUKYO","EUPWR","EUREN","EUYO",
    "EYGYO","FADE","FENER","FLAP","FMIZP","FONET","FORMT","FORTE","FRIGO","FROTO",
    "FZLGY","GARAN","GARFA","GEDIK","GEDZA","GENIL","GENTS","GEREL","GESAN","GIPTA",
    "GLBMD","GLCVY","GLRMK","GLRYH","GLYHO","GMTAS","GOKNR","GOLTS","GOODY","GOZDE",
    "GRNYO","GRSEL","GRTHO","GRTRK","GSDDE","GSDHO","GSRAY","GUBRF","GUNDG","GWIND",
    "GZNMI","HALKB","HATEK","HATSN","HDFGS","HEDEF","HEKTS","HKTM","HLGYO","HOROZ",
    "HRKET","HTTBT","HUBVC","HUNER","HURGZ","ICBCT","ICUGS","IDGYO","IEYHO","IHAAS",
    "IHEVA","IHGZT","IHLAS","IHLGM","IHYAY","IMASM","INDES","INFO","INGRM","INTEK",
    "INTEM","INVEO","INVES","TRENJ","ISATR","ISBIR","ISBTR","ISCTR","ISDMR","ISFIN",
    "ISGSY","ISGYO","ISKPL","ISKUR","ISMEN","ISSEN","ISYAT","IZENR","IZFAS","IZINV",
    "IZMDC","JANTS","KAPLM","KAREL","KARSN","KARTN","KARYE","KATMR","KAYSE","KBORU",
    "KCAER","KCHOL","KENT","KERVN","KFEIN","KGYO","KIMMR","KLGYO","KLKIM",
    "KLMSN","KLNMA","KLRHO","KLSER","KLSYN","KLYPV","KMPUR","KNFRT","KOCMT","KONKA",
    "KONTR","KONYA","KOPOL","KORDS","KOTON","TRMET","TRALT","KRDMA","KRDMB","KRDMD",
    "KRGYO","KRONT","KRPLS","KRSTL","KRTEK","KRVGD","KSTUR","KTLEV","KTSKR","KUTPO",
    "KUVVA","KUYAS","KZBGY","KZGYO","LIDER","LIDFA","LILAK","LINK","LKMNH","LMKDC",
    "LOGO","LRSHO","LUKSK","LYDHO","LYDYE","MAALT","MACKO","MAGEN","MAKIM","MAKTK",
    "MANAS","MARBL","MARKA","MARTI","MAVI","MEDTR","MEGAP","MEGMT","MEKAG","MEPET",
    "MERCN","MERIT","MERKO","METRO","MGROS","MHRGY","MIATK","MMCAS","MNDRS",
    "MNDTR","MOBTL","MOGAN","MOPAS","MPARK","MRGYO","MRSHL","MSGYO","MTRKS","MTRYO",
    "MZHLD","NATEN","NETAS","NIBAS","NTGAZ","NTHOL","NUGYO","NUHCM","OBAMS","OBASE",
    "ODAS","ODINE","OFSYM","ONCSM","ONRYT","ORCAY","ORGE","ORMA","OSMEN","OSTIM",
    "OTKAR","OTTO","OYAKC","OYAYO","OYLUM","OYYAT","OZATD","OZGYO","OZKGY","OZRDN",
    "OZSUB","OZYSR","PAGYO","PAMEL","PAPIL","PARSN","PASEU","PATEK","PCILT",
    "PEKGY","PENGD","PENTA","PETKM","PETUN","PGSUS","PINSU","PKART","PKENT","PLTUR",
    "PNLSN","PNSUT","POLHO","POLTK","PRDGS","PRKAB","PRKME","PRZMA","PSDTC","PSGYO",
    "QNBFB","QNBFK","QNBFL","QNBTR","QUAGR","RALYH","RAYSG","REEDR","RGYAS","RNPOL",
    "RODRG","ROYAL","RTALB","RUBNS","RYGYO","RYSAS","SAFKR","SAHOL","SAMAT","SANEL",
    "SANFM","SANKO","SARKY","SASA","SAYAS","SDTTR","SEGMN","SEGYO","SEKFK","SEKUR",
    "SELEC","SELGD","SELVA","SERNT","SEYKM","SILVR","SISE","SKBNK","SKTAS","SKYLP",
    "SKYMD","SMART","SMRTG","SMRVA","SNGYO","SNICA","SNKRN","SNPAM","SODSN","SOKE",
    "SOKM","SONME","SRVGY","SUMAS","SUNTK","SURGY","SUWEN","TABGD","TARKM","TATEN",
    "TATGD","TAVHL","TBORG","TCELL","TCKRC","TDGYO","TEKTU","TERA","TETMT","TEZOL",
    "TGSAS","THYAO","TKFEN","TKNSA","TLMAN","TMPOL","TMSN","TNZTP","TOASO","TRCAS",
    "TRGYO","TRILC","TSGYO","TSKB","TSPOR","TTKOM","TTRAK","TUCLK","TUKAS","TUPRS",
    "TUREX","TURGG","TURSG","UFUK","ULAS","ULKER","ULUFA","ULUSE","ULUUN","UMPAS",
    "UNLU","USAK","UZERB","VAKBN","VAKFN","VAKKO","VANGD","VBTYZ","VERTU","VERUS",
    "VESBE","VESTL","VKFYO","VKGYO","VKING","VRGYO","VSNMD","YAPRK","YATAS","YAYLA",
    "YBTAS","YEOTK","YESIL","YGGYO","YGYO","YIGIT","YKBNK","YKSLN","YONGA","YUNSA",
    "YYAPI","YYLGD","ZEDUR","ZOREN","ZRGYO","DOFRB","ECOGR","VAKFA","PAHOL","ZERGY",
    "ARFYE","MEYSU","FRMPL","ZGYO","UCAYM","NETCD","AKHAN","BESTE",
    "DUNYH","A1YEN","DMLKT","MARMR","ATATR",
    "TRHOL","RUZYE","BIGTK","EFOR","BESLR","BLUME","TEHOL",
]

def fetch1(t,period="10y",start=None,end=None):
    try:
        tk=yf.Ticker(f"{t}.IS")
        df=tk.history(start=start,end=end) if start else tk.history(period=period)
        if df is None or df.empty:return None
        if df.index.tz is not None:df.index=df.index.tz_localize(None)
        df["Ticker"]=t;return df
    except:return None

def fetch_all(tickers,period="10y",start=None,end=None):
    mode=f"{start} → {end}" if start else period
    print(f"\n{'='*60}\n  📊 BIST v6.0 - {len(tickers)} hisse - {mode}\n  📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*60}\n")
    ad={};bs=20
    for i in range(0,len(tickers),bs):
        b=tickers[i:i+bs];bn=i//bs+1;tb=(len(tickers)+bs-1)//bs
        print(f"  Grup {bn}/{tb} ({i+1}-{min(i+bs,len(tickers))})...")
        with ThreadPoolExecutor(max_workers=5) as ex:
            fs={ex.submit(fetch1,t,period,start,end):t for t in b}
            for f in as_completed(fs):
                try:
                    r=f.result()
                    if r is not None and not r.empty:ad[fs[f]]=r
                except:pass
        if i+bs<len(tickers):time.sleep(2)
    print(f"\n  ✅ {len(ad)} hisse");return ad

def calc_day(ad,ds):
    s={k:0 for k in["ma5","ma20","ma50","ma200","adv","dec","unch","nh","nl","upv","dnv","tot",
       "up4","dn4","u25q","d25q","u25m","d25m","u50m","d50m","u13_34","d13_34"]}
    dr=[]
    for t,df in ad.items():
        try:
            mask=df.index.strftime("%Y-%m-%d")==ds
            if not mask.any():continue
            idx=mask.argmax()
        except:continue
        c=df.iloc[idx]["Close"];s["tot"]+=1
        for ml,k in[(5,"ma5"),(20,"ma20"),(50,"ma50"),(200,"ma200")]:
            if idx>=ml and c>df["Close"].iloc[max(0,idx-ml+1):idx+1].mean():s[k]+=1
        if idx>0:
            p=df.iloc[idx-1]["Close"];v=df.iloc[idx].get("Volume",0)
            if p>0:
                pchg=(c-p)/p*100;dr.append(pchg/100)
                if c>p:s["adv"]+=1;s["upv"]+=v
                elif c<p:s["dec"]+=1;s["dnv"]+=v
                else:s["unch"]+=1
                if pchg>=4:s["up4"]+=1
                if pchg<=-4:s["dn4"]+=1
        lb=min(idx+1,252)
        if lb>20:
            if c>=df["High"].iloc[max(0,idx-lb+1):idx+1].max()*0.99:s["nh"]+=1
            if c<=df["Low"].iloc[max(0,idx-lb+1):idx+1].min()*1.01:s["nl"]+=1
        for days,pct,uk,dk in[(63,25,"u25q","d25q"),(21,25,"u25m","d25m"),(21,50,"u50m","d50m"),(34,13,"u13_34","d13_34")]:
            if idx>=days:
                pp=df.iloc[idx-days]["Close"]
                if pp>0:
                    ch=(c-pp)/pp*100
                    if ch>=pct:s[uk]+=1
                    if ch<=-pct:s[dk]+=1
    return s,np.mean(dr) if dr else 0

def make_row(s,avg_ret,ds,adl):
    adf=s["adv"]-s["dec"];adl+=adf;t=s["tot"]
    if t<100:return None,adl  # Tatil/kapalı günleri filtrele (normal gün ~600 hisse)
    return {"date":ds,"totalStocks":t,
        "aboveMa5":s["ma5"],"aboveMa20":s["ma20"],"aboveMa50":s["ma50"],"aboveMa200":s["ma200"],
        "pctAboveMa5":round(s["ma5"]/t*100,1),"pctAboveMa20":round(s["ma20"]/t*100,1),
        "pctAboveMa50":round(s["ma50"]/t*100,1),"pctAboveMa200":round(s["ma200"]/t*100,1),
        "advancing":s["adv"],"declining":s["dec"],"unchanged":s["unch"],
        "adDiff":adf,"adLine":adl,
        "newHigh52":s["nh"],"newLow52":s["nl"],"nhNlDiff":s["nh"]-s["nl"],
        "upVolume":round(s["upv"]/1e6),"downVolume":round(s["dnv"]/1e6),
        "up4pct":s["up4"],"dn4pct":s["dn4"],
        "up25pctQ":s["u25q"],"dn25pctQ":s["d25q"],
        "up25pctM":s["u25m"],"dn25pctM":s["d25m"],
        "up50pctM":s["u50m"],"dn50pctM":s["d50m"],
        "up13pct34d":s["u13_34"],"dn13pct34d":s["d13_34"],
        "avgReturn":round(avg_ret,6)},adl

def fix_zero_days(bd):
    """Forward-fill: veri eksik günleri önceki günle doldur"""
    fill_keys=['aboveMa5','aboveMa20','aboveMa50','aboveMa200',
               'pctAboveMa5','pctAboveMa20','pctAboveMa50','pctAboveMa200',
               'advancing','declining','newHigh52','newLow52','nhNlDiff',
               'up4pct','dn4pct','up25pctQ','dn25pctQ','up25pctM','dn25pctM',
               'up50pctM','dn50pctM','up13pct34d','dn13pct34d']
    for i in range(1,len(bd)):
        # pctAboveMa50 gerçek bir işlem gününde asla 0 olmaz (600+ hisseden en az biri 50MA üstünde)
        p50=bd[i].get('pctAboveMa50',0)
        p20=bd[i].get('pctAboveMa20',0)
        if (p50==0 or p50==0.0) and (p20==0 or p20==0.0):
            for k in fill_keys:
                bd[i][k]=bd[i-1].get(k,0)
            bd[i]['totalStocks']=bd[i-1].get('totalStocks',0)
            bd[i]['adDiff']=0
            bd[i]['unchanged']=bd[i]['totalStocks']

def calc_derived(bd):
    fix_zero_days(bd)
    e19=0;e39=0;si=0;k19=2/20;k39=2/40
    for i,d in enumerate(bd):
        a=d["adDiff"]
        if i==0:e19=a;e39=a
        else:e19=a*k19+e19*(1-k19);e39=a*k39+e39*(1-k39)
        mc=round(e19-e39,2);si+=mc;d["mcOsc"]=mc;d["mcSum"]=round(si,2)
        if i>=4:
            s5a=sum(bd[j]["advancing"]for j in range(i-4,i+1));s5d=sum(bd[j]["declining"]for j in range(i-4,i+1))
            d["ratio5d"]=round(s5a/s5d,2)if s5d>0 else 0
        else:d["ratio5d"]=0
        if i>=9:
            s10a=sum(bd[j]["advancing"]for j in range(i-9,i+1));s10d=sum(bd[j]["declining"]for j in range(i-9,i+1))
            d["ratio10d"]=round(s10a/s10d,2)if s10d>0 else 0
        else:d["ratio10d"]=0
    for i,d in enumerate(bd):
        if i>=9:d["mcSum10"]=round(sum(bd[j]["mcSum"]for j in range(i-9,i+1))/10,2)
        else:d["mcSum10"]=d["mcSum"]
        # %50MA Crossover signal
        d["maCross"]=0
        if i>0:
            prev=bd[i-1]["pctAboveMa50"];cur=d["pctAboveMa50"]
            if prev<30 and cur>=30:d["maCross"]=1
            elif prev>70 and cur<=70:d["maCross"]=-1

def calc_breadth(ad):
    print("\n  🧮 Tam hesaplama...")
    dates=sorted(set(d for df in ad.values() for d in df.index.strftime("%Y-%m-%d")))
    bd=[];adl=0
    for ti,ds in enumerate(dates):
        s,ar=calc_day(ad,ds);row,adl=make_row(s,ar,ds,adl)
        if row:bd.append(row)
        if(ti+1)%50==0:print(f"    {ti+1}/{len(dates)} gün...")
    calc_derived(bd)
    print(f"  ✅ {len(bd)} gün");return bd

def update_breadth():
    if not DF.exists():
        print("  ❌ Veri yok. Önce: python3 bist_breadth_full.py");sys.exit(1)
    raw=json.loads(DF.read_text("utf-8"));bd=raw["data"]
    last=bd[-1]["date"];adl=bd[-1]["adLine"]
    today=datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*60}\n  📊 GÜNCELLEME\n  Son veri: {last}\n  Bugün:    {today}\n{'='*60}")
    if last>=today:print("  ✅ Zaten güncel!");return bd
    sf=(datetime.strptime(last,"%Y-%m-%d")-timedelta(days=350)).strftime("%Y-%m-%d")
    ef=(datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"  📥 {sf} → {ef}")
    ad=fetch_all(TK,start=sf,end=ef)
    if len(ad)<50:print("  ⚠️ Az veri");sys.exit(1)
    alld=sorted(set(d for df in ad.values() for d in df.index.strftime("%Y-%m-%d")))
    nd=[d for d in alld if d>last]
    if not nd:print("  ✅ Yeni işlem günü yok.");return bd
    print(f"\n  🧮 {len(nd)} yeni gün...")
    for ds in nd:
        s,ar=calc_day(ad,ds);row,adl=make_row(s,ar,ds,adl)
        if row:bd.append(row);print(f"    ✓ {ds} ({row['totalStocks']} hisse)")
    calc_derived(bd)
    print(f"  ✅ +{len(nd)} gün → Toplam: {len(bd)}");return bd

def fetch_index():
    """XU100 endeks verisini çek ve kaydet"""
    print("  📈 XU100 endeks verisi çekiliyor...")
    try:
        tk=yf.Ticker("XU100.IS")
        df=tk.history(period="10y")
        if df is None or df.empty:
            print("    ⚠️ XU100 verisi alınamadı")
            return {}
        if df.index.tz is not None:df.index=df.index.tz_localize(None)
        idx={d:round(float(c),2) for d,c in zip(df.index.strftime("%Y-%m-%d"),df["Close"])}
        IF.write_text(json.dumps(idx,ensure_ascii=False),"utf-8")
        print(f"    ✅ {len(idx)} gün endeks verisi")
        return idx
    except Exception as e:
        print(f"    ⚠️ XU100 hatası: {e}")
        return {}

def update_index():
    """Mevcut endeks verisini güncelle"""
    existing={}
    if IF.exists():
        existing=json.loads(IF.read_text("utf-8"))
    print("  📈 XU100 güncelleniyor...")
    try:
        tk=yf.Ticker("XU100.IS")
        # İlk sefer veya az veri varsa 10y, yoksa 1y yeter
        per="10y" if len(existing)<500 else "1y"
        df=tk.history(period=per)
        if df is not None and not df.empty:
            if df.index.tz is not None:df.index=df.index.tz_localize(None)
            for d,c in zip(df.index.strftime("%Y-%m-%d"),df["Close"]):
                existing[d]=round(float(c),2)
            IF.write_text(json.dumps(existing,ensure_ascii=False),"utf-8")
            print(f"    ✅ {len(existing)} gün")
    except Exception as e:
        print(f"    ⚠️ {e}")
    return existing

def load_index():
    if IF.exists():return json.loads(IF.read_text("utf-8"))
    return {}

def merge_index(bd,idx):
    """Breadth data'ya endeks değerlerini ekle, eksik günleri önceki değerle doldur"""
    prev=0
    for d in bd:
        v=idx.get(d["date"],0)
        if v>0:prev=v
        d["xu100"]=prev

def save(data):
    DF.write_text(json.dumps({"updated_at":datetime.now().isoformat(),"total_days":len(data),"data":data},ensure_ascii=False),"utf-8")
    print(f"  💾 {DF}")

def load():return json.loads(DF.read_text("utf-8"))["data"]

# ══════════════════════════════════════════════════════════
#  DASHBOARD HTML
# ══════════════════════════════════════════════════════════

def make_html(bd):
    print("  🎨 Dashboard...")
    dj=json.dumps(bd)
    h="""<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>BIST Market Breadth</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}body{background:#0a0a14;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
.hd{padding:18px 28px;border-bottom:1px solid rgba(255,255,255,.06);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
.hd h1{font-size:21px;font-weight:700}.hd h1 span{color:#6366f1}.meta{color:rgba(255,255,255,.4);font-size:11px;font-family:monospace}
.tabs{padding:14px 28px;display:flex;gap:7px;flex-wrap:wrap}
.tab,.btn{background:0;border:1px solid rgba(255,255,255,.08);color:rgba(255,255,255,.5);padding:7px 15px;border-radius:7px;font-size:12px;cursor:pointer;transition:all .2s}
.tab.a,.btn.a{background:rgba(99,102,241,.2);border-color:rgba(99,102,241,.4);color:#a5b4fc;font-weight:600}
.tab:hover,.btn:hover{border-color:rgba(255,255,255,.2)}
.bg.a{background:rgba(52,211,153,.15);border-color:rgba(52,211,153,.3);color:#34d399}
.bb.a{background:rgba(96,165,250,.15);border-color:rgba(96,165,250,.3);color:#60a5fa}
.bp.a{background:rgba(167,139,250,.15);border-color:rgba(167,139,250,.3);color:#a78bfa}
.by.a{background:rgba(251,191,36,.15);border-color:rgba(251,191,36,.3);color:#fbbf24}
.ct{padding:0 28px 36px}.ctr{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
.cl{color:rgba(255,255,255,.4);font-size:10px;font-family:monospace;margin-right:3px;text-transform:uppercase;letter-spacing:1px}
.kg{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:9px;margin-bottom:18px}
.kp{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:9px;padding:13px 15px;position:relative;overflow:hidden}
.kp .br{position:absolute;top:0;left:0;right:0;height:2px}.kp .lb{color:rgba(255,255,255,.5);font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:5px;font-family:monospace}
.kp .vl{font-size:22px;font-weight:700}.kp .sb{color:rgba(255,255,255,.4);font-size:10px;margin-top:2px;font-family:monospace}
.cc{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:18px;margin-bottom:18px}
.cc h3{font-size:13px;font-weight:600;margin-bottom:2px}.cc .st{color:rgba(255,255,255,.35);font-size:10px;font-family:monospace;margin-bottom:10px}
.ch{position:relative;height:290px}.gr{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:9px;margin-bottom:18px}
.ga{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:9px;padding:14px;text-align:center}
.ga .pc{font-size:26px;font-weight:700}.ga .gl{color:rgba(255,255,255,.5);font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:5px;font-family:monospace}
.ga .gz{font-size:10px;font-weight:600;margin-top:2px;font-family:monospace}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.tw{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06);border-radius:12px;overflow:hidden;margin-bottom:18px}
.tw .th{padding:13px 16px;border-bottom:1px solid rgba(255,255,255,.06);font-weight:600;font-size:13px}
table{width:100%;border-collapse:collapse;font-size:11px;font-family:monospace}
th{padding:9px 12px;text-align:left;color:rgba(255,255,255,.4);font-size:9px;text-transform:uppercase;letter-spacing:.7px;border-bottom:1px solid rgba(255,255,255,.06)}
td{padding:9px 12px;border-bottom:1px solid rgba(255,255,255,.03)}
.badge{padding:2px 7px;border-radius:4px;font-size:9px;font-weight:600}
.tc{display:none}.tc.a{display:block}
.ft{padding:12px 28px;border-top:1px solid rgba(255,255,255,.06);color:rgba(255,255,255,.2);font-size:10px;font-family:monospace;display:flex;justify-content:space-between}
.sp{color:#4ade80}.sn{color:#f87171}.sbt{overflow-x:auto}.sbt table{min-width:1000px}
@media(max-width:768px){.g2{grid-template-columns:1fr}.ct{padding:0 12px 36px}}
</style></head><body>
<div class="hd"><div><h1>BIST <span>Market Breadth</span></h1><div class="meta" id="hm"></div></div>
<div class="ctr" id="gp"><span class="cl">Periyot:</span>
<button class="btn" onclick="sP(252,this)">1Y</button><button class="btn" onclick="sP(756,this)">3Y</button>
<button class="btn" onclick="sP(1260,this)">5Y</button><button class="btn a" onclick="sP(0,this)">MAX</button></div></div>
<div class="tabs" id="mt">
<button class="tab a" onclick="sT('ov',this)">Genel Bakış</button>
<button class="tab" onclick="sT('ma',this)">MA Analizi</button>
<button class="tab" onclick="sT('ad',this)">A/D Line</button>
<button class="tab" onclick="sT('mc',this)">McClellan</button>
<button class="tab" onclick="sT('hl',this)">52W H/L</button>
<button class="tab" onclick="sT('sb',this)">Stockbee</button>
<button class="tab" onclick="sT('sg',this)">Sinyaller</button></div>
<div class="ct">
<div id="tab-ov" class="tc a"><div class="kg" id="kG"></div><div class="gr" id="gR"></div>
<div class="cc"><h3>A/D Farkı</h3><div class="st">Günlük</div><div class="ch"><canvas id="c_adD"></canvas></div></div>
<div class="cc"><h3>MA Üstü %</h3><div class="st">5/20/50/200</div><div class="ch"><canvas id="c_maO"></canvas></div></div></div>
<div id="tab-ma" class="tc"><div class="ctr"><span class="cl">Göster:</span>
<button class="btn bg a" onclick="tM('ma5',this)">5 MA</button><button class="btn bb a" onclick="tM('ma20',this)">20 MA</button>
<button class="btn bp a" onclick="tM('ma50',this)">50 MA</button><button class="btn by a" onclick="tM('ma200',this)">200 MA</button></div>
<div class="cc"><h3>MA Üstündeki Hisse</h3><div class="st">Sayı</div><div class="ch" style="height:340px"><canvas id="c_maC"></canvas></div></div>
<div class="cc"><h3>MA Üstü %</h3><div class="st">Yüzde</div><div class="ch" style="height:340px"><canvas id="c_maP"></canvas></div></div>
<div class="cc"><h3>XU100 Endeks</h3><div class="st">Çizgi grafik</div><div class="ch" style="height:200px"><canvas id="c_ix_ma"></canvas></div></div></div>
<div id="tab-ad" class="tc"><div class="ctr" id="adC"><span class="cl">Mod:</span>
<button class="btn a" onclick="sAD('c',this)">Kümülatif</button><button class="btn" onclick="sAD('d',this)">Günlük</button></div>
<div class="cc"><h3 id="adT">A/D Line (Kümülatif)</h3><div class="st" id="adS"></div><div class="ch" style="height:340px"><canvas id="c_adL"></canvas></div></div>
<div class="g2"><div class="cc"><h3>Yükselen vs Düşen</h3><div class="st">Günlük</div><div class="ch"><canvas id="c_avd"></canvas></div></div>
<div class="cc"><h3>A/D Oranı</h3><div class="st">Yükselen/Düşen</div><div class="ch"><canvas id="c_adr"></canvas></div></div></div></div>
<div id="tab-mc" class="tc"><div class="cc"><h3>McClellan Summation Index (Normalized)</h3><div class="st">MCSI (z) · 10 SMA · ±1σ ±2σ bantları</div><div class="ch" style="height:360px"><canvas id="c_mcS"></canvas></div></div>
<div class="cc"><h3>XU100 Endeks</h3><div class="st">Çizgi grafik</div><div class="ch" style="height:200px"><canvas id="c_ix_mc"></canvas></div></div>
<div class="cc"><h3>McClellan Oscillator (Normalized)</h3><div class="st">MCO (z) · ±1σ ±2σ bantları</div><div class="ch" style="height:360px"><canvas id="c_mcO"></canvas></div></div></div>
<div id="tab-hl" class="tc"><div class="cc"><h3>NH - NL Farkı</h3><div class="st">52W</div><div class="ch" style="height:340px"><canvas id="c_nhl"></canvas></div></div>
<div class="cc"><h3>XU100 Endeks</h3><div class="st">Çizgi grafik</div><div class="ch" style="height:200px"><canvas id="c_ix_hl"></canvas></div></div>
<div class="g2"><div class="cc"><h3>New Highs</h3><div class="st">52W</div><div class="ch"><canvas id="c_nh"></canvas></div></div>
<div class="cc"><h3>New Lows</h3><div class="st">52W</div><div class="ch"><canvas id="c_nl"></canvas></div></div></div></div>
<div id="tab-sb" class="tc">
<div class="cc" style="padding:14px 18px;margin-bottom:12px"><div style="font-size:12px;color:rgba(255,255,255,.6);line-height:1.8">
<b style="color:#a78bfa;font-size:14px">Stockbee Market Monitor</b><br>
<span style="display:inline-block;width:16px;height:16px;background:#b45309;border-radius:3px;vertical-align:middle;margin-right:5px;border:1px solid #d97706"></span> <b>Oversold</b> ↓4%≥40 veya 10DR≤0.7 &nbsp;&nbsp;
<span style="display:inline-block;width:16px;height:16px;background:#7e22ce;border-radius:3px;vertical-align:middle;margin-right:5px;border:1px solid #9333ea"></span> <b>Overbought</b> ↑4%≥50 veya 10DR≥1.5 &nbsp;&nbsp;
<span style="display:inline-block;width:16px;height:16px;background:#0369a1;border-radius:3px;vertical-align:middle;margin-right:5px;border:1px solid #0284c7"></span> <b>Trend Dönüşü</b> 10DR &lt;1→≥1 ve ↑4%≥25
</div></div>
<div class="cc"><div class="sbt"><table id="sbT"></table></div></div>
<div class="cc" style="padding:16px 20px"><div style="font-size:13px;font-weight:700;color:#a78bfa;margin-bottom:12px">📋 Kritik Referans Değerleri</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:11px;line-height:1.8">
<div style="background:rgba(74,222,128,0.05);border:1px solid rgba(74,222,128,0.15);border-radius:8px;padding:12px">
<div style="color:#4ade80;font-weight:700;margin-bottom:6px">🟢 Boğa / Sağlıklı Piyasa</div>
<div style="color:rgba(255,255,255,.6)">↑4% sürekli 25+ → momentum güçlü<br>
10DR > 1.0 → alıcılar hakim<br>
10DR > 1.5 → çok güçlü boğa<br>
↑25Q > 100 → orta vade trend sağlam<br>
↑13/34 > 150 → kurumsal alım aktif</div></div>
<div style="background:rgba(248,113,113,0.05);border:1px solid rgba(248,113,113,0.15);border-radius:8px;padding:12px">
<div style="color:#f87171;font-weight:700;margin-bottom:6px">🔴 Ayı / Zayıf Piyasa</div>
<div style="color:rgba(255,255,255,.6)">↓4% > 40 → panik satışı<br>
10DR < 0.7 → satıcılar hakim<br>
10DR < 0.5 → kapitülasyon<br>
↓25Q yükselirken ↑25Q düşüyor → bozulma<br>
↑13/34 < 50 → kurumsal ilgi azaldı</div></div>
<div style="background:rgba(56,189,248,0.05);border:1px solid rgba(56,189,248,0.15);border-radius:8px;padding:12px">
<div style="color:#38bdf8;font-weight:700;margin-bottom:6px">🔵 Dip / Dönüş Sinyali</div>
<div style="color:rgba(255,255,255,.6)">↓4% bir günde 40+ → sonra 1-3 gün içinde ↑4% 30+'ya zıplarsa dönüş<br>
10DR 0.7 altından 1.0 üstüne geçiş → para geri geliyor<br>
↑50M > 10 → spekülatif balon riski</div></div>
<div style="background:rgba(167,139,250,0.05);border:1px solid rgba(167,139,250,0.15);border-radius:8px;padding:12px">
<div style="color:#a78bfa;font-weight:700;margin-bottom:6px">⚠️ Tepe / Dağılım Sinyali</div>
<div style="color:rgba(255,255,255,.6)">↑4% yüksek ama düşüyor → ivme kaybı<br>
10DR 1.5'tan 1.0'a düşüş → alıcı gücü zayıflıyor<br>
↑25Q düşerken endeks hâlâ yükseliyorsa → dağılım<br>
↓25M aniden 50+ → ciddi hasar</div></div>
</div></div>
<div class="cc"><h3>XU100 Endeks</h3><div class="st">Çizgi grafik</div><div class="ch" style="height:200px"><canvas id="c_ix_sb"></canvas></div></div>
<div class="cc"><h3>Günlük %4+</h3><div class="st">Momentum</div><div class="ch"><canvas id="c_sb4"></canvas></div></div>
<div class="cc"><h3>Çeyrekte %25+</h3><div class="st">Trend</div><div class="ch"><canvas id="c_sbQ"></canvas></div></div></div>
<div id="tab-sg" class="tc">
<div class="cc" style="padding:12px 16px;margin-bottom:10px"><div style="font-size:11px;color:rgba(255,255,255,.5);line-height:1.7">
<b style="color:#a78bfa">%50 MA Crossover</b> — BIST'te 50 günlük hareketli ortalamanın üstünde olan hisse yüzdesi. %30'un altından yukarı kırılırsa <span style="color:#4ade80">▲ Bullish</span> (çoğunluk düşüşteydi, toparlanma başlıyor). %70'in üstünden aşağı kırılırsa <span style="color:#f87171">▼ Bearish</span> (çoğunluk yükselişteydi, bozulma başlıyor). Nadir sinyal verir, verdiğinde güçlüdür.
</div></div>
<div class="cc"><h3>%50 MA Crossover Sinyalleri</h3><div class="st">Mor çizgi = %50MA üstü oran · <span style="color:#4ade80">●</span> bullish · <span style="color:#f87171">●</span> bearish</div><div class="ch" style="height:340px"><canvas id="c_mac"></canvas></div></div>
<div class="cc"><h3>XU100 Endeks</h3><div class="st">Çizgi grafik</div><div class="ch" style="height:200px"><canvas id="c_ix_sg"></canvas></div></div>
<div class="tw"><div class="th">Son Sinyaller</div><div class="sbt"><table id="sgT"></table></div></div></div>
</div><div class="ft"><span id="ft"></span><span>v6.0</span></div>
"""
    h+="<script>\nconst D="+dj+";\n"
    h+=r"""let cP=0,adM='c',mV={ma5:1,ma20:1,ma50:1,ma200:1},C={};
function gD(){return cP===0?D:D.slice(-cP)}
function gL(d){return d.map(x=>{const p=x.date.split('-');return d.length>1500?p[1]+'/'+p[0].slice(2):d.length>500?p[2]+'/'+p[1]+'/'+p[0].slice(2):p[2]+'/'+p[1]})}
function sT(id,b){document.querySelectorAll('.tc').forEach(t=>t.classList.remove('a'));document.querySelectorAll('#mt .tab').forEach(t=>t.classList.remove('a'));document.getElementById('tab-'+id).classList.add('a');if(b)b.classList.add('a')}
function sP(d,b){cP=d;document.querySelectorAll('#gp .btn').forEach(x=>x.classList.remove('a'));if(b)b.classList.add('a');rA()}
function tM(k,b){mV[k]=mV[k]?0:1;b.classList.toggle('a');rMA()}
function sAD(m,b){adM=m;document.querySelectorAll('#adC .btn').forEach(x=>x.classList.remove('a'));if(b)b.classList.add('a');rAD()}
function dc(id){if(C[id]){C[id].destroy();delete C[id]}}
Chart.defaults.color='rgba(255,255,255,.4)';Chart.defaults.borderColor='rgba(255,255,255,.04)';Chart.defaults.font.family='monospace';Chart.defaults.font.size=10;
let _syncIdx=null;
const chP={id:'crosshair',afterDraw(c){const idx=c._syncIdx!=null?c._syncIdx:(c._chX!=null?Math.round(c.scales.x.getValueForPixel(c._chX)):null);if(idx==null)return;const ctx=c.ctx,a=c.chartArea;const x=c.scales.x.getPixelForValue(idx);if(x<a.left||x>a.right)return;const y=c._chY;ctx.save();ctx.setLineDash([4,3]);ctx.lineWidth=1;ctx.strokeStyle='rgba(255,255,255,0.35)';ctx.beginPath();ctx.moveTo(x,a.top);ctx.lineTo(x,a.bottom);ctx.stroke();if(y!=null&&y>=a.top&&y<=a.bottom&&c._chX!=null){ctx.beginPath();ctx.moveTo(a.left,y);ctx.lineTo(a.right,y);ctx.stroke();const ys=c.scales.y;if(ys){const v=ys.getValueForPixel(y),t=typeof v==='number'?v.toFixed(1):'';ctx.fillStyle='rgba(99,102,241,0.7)';const w=ctx.measureText(t).width+8;ctx.fillRect(a.left-w-4,y-10,w+4,20);ctx.fillStyle='#fff';ctx.font='10px monospace';ctx.textAlign='right';ctx.textBaseline='middle';ctx.fillText(t,a.left-4,y)}}const xs=c.scales.x;const lb=c.data.labels;if(idx>=0&&idx<lb.length){const dt=lb[idx];ctx.fillStyle='rgba(99,102,241,0.7)';const w2=ctx.measureText(dt).width+8;ctx.fillRect(x-w2/2,a.bottom+2,w2,18);ctx.fillStyle='#fff';ctx.font='10px monospace';ctx.textAlign='center';ctx.textBaseline='top';ctx.fillText(dt,x,a.bottom+5)}ctx.restore()},afterEvent(c,a){const e=a.event;if(e.type==='mousemove'&&c._chX!==undefined){c._chX=e.x;c._chY=e.y;const idx=Math.round(c.scales.x.getValueForPixel(e.x));_syncIdx=idx;Object.values(C).forEach(oc=>{if(oc!==c){oc._syncIdx=idx;oc.draw()}});c.draw()}else if(e.type==='mouseout'){c._chX=null;c._chY=null;_syncIdx=null;Object.values(C).forEach(oc=>{oc._syncIdx=null;oc.draw()});c.draw()}}};
Chart.register(chP);
function mmA(v){if(!v||v.length<10)return{};const n=v.filter(x=>typeof x==='number'&&isFinite(x));if(n.length<10)return{};const s=[...n].sort((a,b)=>b-a),u=[...new Set(s)],mx=u.slice(0,3),mn=u.slice(-3),a={};mx.forEach((v,i)=>{a['mx'+i]={type:'line',yMin:v,yMax:v,borderColor:'rgba(74,222,128,0.12)',borderWidth:1,borderDash:[4,4]}});mn.forEach((v,i)=>{a['mn'+i]={type:'line',yMin:v,yMax:v,borderColor:'rgba(248,113,113,0.12)',borderWidth:1,borderDash:[4,4]}});return a}
function mkC(id,tp,lb,ds,o={}){dc(id);const md=ds[0]?.data||[];const an=mmA(md.map(v=>typeof v==='string'?parseFloat(v):v));C[id]=new Chart(document.getElementById(id),{type:tp,data:{labels:lb,datasets:ds},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:ds.length>1},annotation:{annotations:an},tooltip:{enabled:true},...(o.plugins||{})},scales:{x:{ticks:{maxTicksLimit:12,maxRotation:0}},...(o.scales||{})}}})}
function mkIx(id,d,lb){mkC(id,'line',lb,[{label:'XU100',data:d.map(x=>x.xu100||0),borderColor:'rgba(251,191,36,0.7)',borderWidth:1.5,pointRadius:0,tension:.3,backgroundColor:'rgba(251,191,36,0.03)',fill:true}],{plugins:{legend:{display:false}}})}
function rKPI(){const l=D[D.length-1];document.getElementById('hm').textContent=l.date+' · '+l.totalStocks+' hisse';document.getElementById('ft').textContent='Son: '+new Date().toLocaleString('tr-TR');const ks=[{l:'5MA',v:l.aboveMa5,s:'%'+l.pctAboveMa5,c:'#34d399'},{l:'20MA',v:l.aboveMa20,s:'%'+l.pctAboveMa20,c:'#60a5fa'},{l:'50MA',v:l.aboveMa50,s:'%'+l.pctAboveMa50,c:'#a78bfa'},{l:'200MA',v:l.aboveMa200,s:'%'+l.pctAboveMa200,c:'#fbbf24'},{l:'Yükselen',v:l.advancing,s:'vs '+l.declining,c:'#4ade80'},{l:'MCO',v:l.mcOsc,s:'Osc',c:l.mcOsc>=0?'#4ade80':'#f87171'}];document.getElementById('kG').innerHTML=ks.map(k=>'<div class="kp"><div class="br" style="background:'+k.c+'"></div><div class="lb">'+k.l+'</div><div class="vl" style="color:'+k.c+'">'+k.v+'</div><div class="sb">'+k.s+'</div></div>').join('');function z(p){if(p>70)return['Aşırı Alım','#4ade80'];if(p>50)return['Nötr+','#60a5fa'];if(p>30)return['Nötr-','#fbbf24'];return['Aş.Satım','#f87171']}document.getElementById('gR').innerHTML=[['5',l.pctAboveMa5],['20',l.pctAboveMa20],['50',l.pctAboveMa50],['200',l.pctAboveMa200]].map(([n,p])=>{const[zn,c]=z(p);return'<div class="ga"><div class="gl">'+n+'MA</div><div class="pc" style="color:'+c+'">%'+p+'</div><div class="gz" style="color:'+c+'">'+zn+'</div></div>'}).join('')}
function rOV(){const d=gD(),lb=gL(d);mkC('c_adD','bar',lb,[{label:'A/D',data:d.map(x=>x.adDiff),backgroundColor:d.map(x=>x.adDiff>=0?'rgba(74,222,128,.6)':'rgba(248,113,113,.6)'),borderRadius:1}],{plugins:{legend:{display:false}}});mkC('c_maO','line',lb,[{label:'%5',data:d.map(x=>x.pctAboveMa5),borderColor:'#34d399',borderWidth:1.5,pointRadius:0,tension:.3},{label:'%20',data:d.map(x=>x.pctAboveMa20),borderColor:'#60a5fa',borderWidth:1.5,pointRadius:0,tension:.3},{label:'%50',data:d.map(x=>x.pctAboveMa50),borderColor:'#a78bfa',borderWidth:2,pointRadius:0,tension:.3},{label:'%200',data:d.map(x=>x.pctAboveMa200),borderColor:'#fbbf24',borderWidth:2,pointRadius:0,tension:.3}],{scales:{y:{min:0,max:100}}})}
function rMA(){const d=gD(),lb=gL(d);const cf=[{k:'ma5',l:'5MA',pk:'pctAboveMa5',ck:'aboveMa5',c:'#34d399',bg:'rgba(52,211,153,.1)'},{k:'ma20',l:'20MA',pk:'pctAboveMa20',ck:'aboveMa20',c:'#60a5fa',bg:'rgba(96,165,250,.08)'},{k:'ma50',l:'50MA',pk:'pctAboveMa50',ck:'aboveMa50',c:'#a78bfa',bg:'rgba(167,139,250,.06)'},{k:'ma200',l:'200MA',pk:'pctAboveMa200',ck:'aboveMa200',c:'#fbbf24',bg:'rgba(251,191,36,.05)'}];const vis=cf.filter(c=>mV[c.k]);mkC('c_maC','line',lb,vis.map(c=>({label:c.l,data:d.map(x=>x[c.ck]),borderColor:c.c,backgroundColor:c.bg,fill:true,borderWidth:1.5,pointRadius:0,tension:.3})));mkC('c_maP','line',lb,vis.map(c=>({label:'%'+c.l,data:d.map(x=>x[c.pk]),borderColor:c.c,borderWidth:2,pointRadius:0,tension:.3})),{scales:{y:{min:0,max:100}}});mkIx('c_ix_ma',d,lb)}
function rAD(){const d=gD(),lb=gL(d);const ic=adM==='c';document.getElementById('adT').textContent=ic?'A/D Line (Kümülatif)':'A/D Farkı (Günlük)';if(ic)mkC('c_adL','line',lb,[{label:'A/D',data:d.map(x=>x.adLine),borderColor:'#60a5fa',backgroundColor:'rgba(96,165,250,.1)',fill:true,borderWidth:2,pointRadius:0,tension:.3}]);else mkC('c_adL','bar',lb,[{label:'A/D',data:d.map(x=>x.adDiff),backgroundColor:d.map(x=>x.adDiff>=0?'rgba(74,222,128,.6)':'rgba(248,113,113,.6)'),borderRadius:1}],{plugins:{legend:{display:false}}});mkC('c_avd','line',lb,[{label:'Yüks.',data:d.map(x=>x.advancing),borderColor:'#4ade80',backgroundColor:'rgba(74,222,128,.1)',fill:true,borderWidth:1.5,pointRadius:0,tension:.3},{label:'Düş.',data:d.map(x=>x.declining),borderColor:'#f87171',backgroundColor:'rgba(248,113,113,.1)',fill:true,borderWidth:1.5,pointRadius:0,tension:.3}]);mkC('c_adr','line',lb,[{label:'Oran',data:d.map(x=>x.declining>0?(x.advancing/x.declining).toFixed(2):0),borderColor:'#a78bfa',borderWidth:2,pointRadius:0,tension:.3}])}
function zNorm(arr){const n=arr.filter(x=>typeof x==='number'&&isFinite(x));if(n.length<20)return arr.map(()=>0);const mn=n.reduce((a,b)=>a+b,0)/n.length;const sd=Math.sqrt(n.reduce((a,b)=>a+(b-mn)**2,0)/n.length)||1;return arr.map(v=>(typeof v==='number'&&isFinite(v))?Math.round((v-mn)/sd*100)/100:0)}
function rMC(){const d=gD(),lb=gL(d);
const mcsRaw=d.map(x=>x.mcSum),mcs10Raw=d.map(x=>x.mcSum10),mcoRaw=d.map(x=>x.mcOsc);
const mcsZ=zNorm(mcsRaw),mcs10Z=zNorm(mcs10Raw),mcoZ=zNorm(mcoRaw);
const sAn={p2s:{type:'line',yMin:2,yMax:2,borderColor:'rgba(239,68,68,0.7)',borderWidth:1.5,label:{display:true,content:'+2σ',position:'end',color:'rgba(239,68,68,0.7)',font:{size:9}}},p1s:{type:'line',yMin:1,yMax:1,borderColor:'rgba(239,68,68,0.35)',borderWidth:1,borderDash:[4,4],label:{display:true,content:'+1σ',position:'end',color:'rgba(239,68,68,0.5)',font:{size:9}}},m1s:{type:'line',yMin:-1,yMax:-1,borderColor:'rgba(20,184,166,0.35)',borderWidth:1,borderDash:[4,4],label:{display:true,content:'-1σ',position:'end',color:'rgba(20,184,166,0.5)',font:{size:9}}},m2s:{type:'line',yMin:-2,yMax:-2,borderColor:'rgba(20,184,166,0.7)',borderWidth:1.5,label:{display:true,content:'-2σ',position:'end',color:'rgba(20,184,166,0.7)',font:{size:9}}},z0:{type:'line',yMin:0,yMax:0,borderColor:'rgba(255,255,255,0.08)',borderWidth:1}};
mkC('c_mcS','line',lb,[{label:'MCSI (z)',data:mcsZ,borderColor:'#60a5fa',borderWidth:2,pointRadius:0,tension:.3},{label:'10 SMA',data:mcs10Z,borderColor:'rgba(251,191,36,0.5)',borderWidth:1.5,pointRadius:0,tension:.3,borderDash:[4,3]}],{plugins:{annotation:{annotations:sAn}},scales:{y:{min:-3,max:3,ticks:{stepSize:0.5}}}});
mkIx('c_ix_mc',d,lb);
mkC('c_mcO','line',lb,[{label:'MCO (z)',data:mcoZ,borderColor:'rgba(148,163,184,0.8)',borderWidth:1.5,pointRadius:0,tension:.2}],{plugins:{annotation:{annotations:sAn}},scales:{y:{min:-3,max:3,ticks:{stepSize:0.5}}}})}
function rHL(){const d=gD(),lb=gL(d);mkC('c_nhl','bar',lb,[{label:'NH-NL',data:d.map(x=>x.nhNlDiff),backgroundColor:d.map(x=>x.nhNlDiff>=0?'rgba(74,222,128,.6)':'rgba(248,113,113,.6)'),borderRadius:1}],{plugins:{legend:{display:false}}});mkC('c_nh','line',lb,[{label:'NH',data:d.map(x=>x.newHigh52),borderColor:'#4ade80',backgroundColor:'rgba(74,222,128,.1)',fill:true,borderWidth:1.5,pointRadius:0,tension:.3}]);mkC('c_nl','line',lb,[{label:'NL',data:d.map(x=>x.newLow52),borderColor:'#f87171',backgroundColor:'rgba(248,113,113,.1)',fill:true,borderWidth:1.5,pointRadius:0,tension:.3}]);mkIx('c_ix_hl',d,lb)}function rSB(){const d=gD(),last=d.slice(-15);
const OVS='#b45309',OVB='#7e22ce',TRN='#0369a1';
const ttips={'↑4%':'Günde %4+ yükselen','↓4%':'Günde %4+ düşen','5DR':'5g yükselen÷düşen','10DR':'10g yükselen÷düşen','↑25Q':'3 ayda %25+ yükselen','↓25Q':'3 ayda %25+ düşen','↑25M':'1 ayda %25+ yükselen','↓25M':'1 ayda %25+ düşen','↑50M':'1 ayda %50+ yükselen','↓50M':'1 ayda %50+ düşen','↑13/34':'34g %13+ yükselen','↓13/34':'34g %13+ düşen'};
function thTip(label,key){return '<th style="cursor:help;font-size:11px;padding:10px 8px;white-space:nowrap;text-align:center" title="'+ttips[key]+'">'+label+'<div style="font-size:8px;font-weight:400;color:rgba(255,255,255,.25);margin-top:2px;letter-spacing:0">'+ttips[key]+'</div></th>'}
let t='<tr><th style="font-size:11px;padding:10px 8px">Tarih</th><th style="font-size:11px;padding:10px 8px">Sinyal</th>'+thTip('↑4%','↑4%')+thTip('↓4%','↓4%')+thTip('5DR','5DR')+thTip('10DR','10DR')+thTip('↑25Q','↑25Q')+thTip('↓25Q','↓25Q')+thTip('↑25M','↑25M')+thTip('↓25M','↓25M')+thTip('↑50M','↑50M')+thTip('↓50M','↓50M')+thTip('↑13/34','↑13/34')+thTip('↓13/34','↓13/34')+'<th style="font-size:11px;padding:10px 8px">Tot</th></tr>';
const rev=[...last].reverse();
rev.forEach((r,ri)=>{const n=r.totalStocks;
const isOvs=r.dn4pct>=40||r.ratio10d<=0.7;
const isOvb=r.up4pct>=50||r.ratio10d>=1.5;
const prev=ri<rev.length-1?rev[ri+1]:null;
const isTrn=prev&&prev.ratio10d<1&&r.ratio10d>=1&&r.up4pct>=25;
let sig='',rowBg='';
if(isTrn){sig='<span style="background:#0369a1;color:#fff;padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700">DÖNÜŞ</span>';rowBg='background:rgba(3,105,161,0.1);border-left:3px solid #0369a1;';}
else if(isOvs){sig='<span style="background:#b45309;color:#fff;padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700">OVERSOLD</span>';rowBg='background:rgba(180,83,9,0.08);border-left:3px solid #b45309;';}
else if(isOvb){sig='<span style="background:#7e22ce;color:#fff;padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700">OVERBOUGHT</span>';rowBg='background:rgba(126,34,206,0.08);border-left:3px solid #7e22ce;';}
function numCell(v,hi,lo,hiC,loC){let bg='transparent',fw='400';if(hi!==null&&v>=hi){bg=hiC;fw='700'}else if(lo!==null&&v<=lo){bg=loC;fw='700'}return '<td style="background:'+bg+';font-weight:'+fw+';padding:10px 8px;text-align:center;font-size:12px">'+v+'</td>'}
t+='<tr style="'+rowBg+'"><td style="padding:10px 8px;font-size:11px">'+r.date+'</td><td style="padding:10px 8px;text-align:center">'+sig+'</td>';
t+=numCell(r.up4pct,50,null,'rgba(126,34,206,0.25)','transparent');
t+=numCell(r.dn4pct,40,null,'rgba(180,83,9,0.25)','transparent');
t+=numCell(r.ratio5d,1.5,0.7,'rgba(74,222,128,0.15)','rgba(248,113,113,0.15)');
t+=numCell(r.ratio10d,1.5,0.7,'rgba(74,222,128,0.2)','rgba(248,113,113,0.2)');
t+=numCell(r.up25pctQ,100,null,'rgba(74,222,128,0.15)','transparent');
t+=numCell(r.dn25pctQ,null,null,'transparent','transparent');
t+=numCell(r.up25pctM,null,null,'transparent','transparent');
t+=numCell(r.dn25pctM,50,null,'rgba(248,113,113,0.2)','transparent');
t+=numCell(r.up50pctM,10,null,'rgba(248,113,113,0.2)','transparent');
t+=numCell(r.dn50pctM,null,null,'transparent','transparent');
t+=numCell(r.up13pct34d,150,50,'rgba(74,222,128,0.15)','rgba(248,113,113,0.1)');
t+=numCell(r.dn13pct34d,null,null,'transparent','transparent');
t+='<td style="padding:10px 8px;text-align:center;font-size:11px;color:rgba(255,255,255,.4)">'+n+'</td></tr>'});
document.getElementById('sbT').innerHTML=t;const lb=gL(d);mkIx('c_ix_sb',d,lb);mkC('c_sb4','bar',lb,[{label:'↑4%',data:d.map(x=>x.up4pct),backgroundColor:'rgba(74,222,128,.6)',borderRadius:1},{label:'↓4%',data:d.map(x=>-x.dn4pct),backgroundColor:'rgba(248,113,113,.6)',borderRadius:1}]);mkC('c_sbQ','line',lb,[{label:'↑25Q',data:d.map(x=>x.up25pctQ),borderColor:'#4ade80',borderWidth:1.5,pointRadius:0,tension:.3},{label:'↓25Q',data:d.map(x=>x.dn25pctQ),borderColor:'#f87171',borderWidth:1.5,pointRadius:0,tension:.3}])}
function rSG(){const d=gD(),lb=gL(d);
const mcAn={};d.forEach((x,i)=>{if(x.maCross===1)mcAn['mb'+i]={type:'point',xValue:i,yValue:x.pctAboveMa50,backgroundColor:'rgba(74,222,128,0.9)',borderColor:'#4ade80',radius:5,borderWidth:2};if(x.maCross===-1)mcAn['ms'+i]={type:'point',xValue:i,yValue:x.pctAboveMa50,backgroundColor:'rgba(248,113,113,0.9)',borderColor:'#f87171',radius:5,borderWidth:2}});
const mc30={type:'line',yMin:30,yMax:30,borderColor:'rgba(74,222,128,0.15)',borderWidth:1,borderDash:[4,4],label:{display:true,content:'30%',position:'start',color:'rgba(74,222,128,0.4)',font:{size:9}}};
const mc70={type:'line',yMin:70,yMax:70,borderColor:'rgba(248,113,113,0.15)',borderWidth:1,borderDash:[4,4],label:{display:true,content:'70%',position:'start',color:'rgba(248,113,113,0.4)',font:{size:9}}};
mkC('c_mac','line',lb,[{label:'%50MA',data:d.map(x=>x.pctAboveMa50),borderColor:'#a78bfa',borderWidth:2,pointRadius:0,tension:.3}],{plugins:{annotation:{annotations:{...mcAn,mc30,mc70}}},scales:{y:{min:0,max:100}}});
let sg=[];d.forEach(x=>{
if(x.maCross===1)sg.push({d:x.date,t:'%50MA ↑30',c:'#4ade80',desc:'Bullish kırılım'});
if(x.maCross===-1)sg.push({d:x.date,t:'%50MA ↓70',c:'#f87171',desc:'Bearish kırılım'});
});sg=sg.slice(-20).reverse();
let st='<tr><th>Tarih</th><th>Sinyal</th><th>Açıklama</th></tr>';
sg.forEach(s=>{st+='<tr><td>'+s.d+'</td><td><span class="badge" style="background:'+s.c+'22;color:'+s.c+'">'+s.t+'</span></td><td>'+s.desc+'</td></tr>'});
if(!sg.length)st+='<tr><td colspan="3" style="text-align:center;color:rgba(255,255,255,.3)">Sinyal yok</td></tr>';
document.getElementById('sgT').innerHTML=st;mkIx('c_ix_sg',d,lb)}
function rA(){rKPI();rOV();rMA();rAD();rMC();rHL();rSB();rSG()}
rA();
"""
    h+="</script></body></html>"
    HF.write_text(h,"utf-8")
    print(f"  ✅ {HF}")

# ══════════════════════════════════════════════════════════
if __name__=="__main__":
    print(f"\n{'='*60}\n  🇹🇷 BIST BREADTH v6.0\n{'='*60}")
    if "--dashboard" in sys.argv:
        print("  📊 Dashboard...")
        if not DF.exists():print("  ❌ Veri yok.");sys.exit(1)
        bd=load();fix_zero_days(bd);idx=load_index();merge_index(bd,idx)
        make_html(bd)
    elif "--update" in sys.argv:
        bd=update_breadth();save(bd)
        idx=update_index();merge_index(bd,idx)
        make_html(bd)
    else:
        ad=fetch_all(TK,period="10y")
        if len(ad)<50:print("  ⚠️ Az veri");sys.exit(1)
        bd=calc_breadth(ad);save(bd)
        idx=fetch_index();merge_index(bd,idx)
        make_html(bd)
    print(f"\n  🎉 TAMAM! → open {HF}\n")
