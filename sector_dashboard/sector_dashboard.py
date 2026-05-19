#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BIST SEKTÖR PERFORMANSI DASHBOARD
===================================
  python3 sector_dashboard.py                # İlk kurulum (~10dk)
  python3 sector_dashboard.py --update       # Güncelle (~3dk)
  python3 sector_dashboard.py --dashboard    # Sadece HTML yenile
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
SF=SD/"sector_map.json"
PF=SD/"sector_prices.json"
HF=SD/"sector_dashboard.html"
FF=SD/"sector_financials.json"
OF=SD/"sector_ohlc.json"
SKTR=SD/"SKTR.xlsx"

def load_sector_map():
    if not SKTR.exists():print(f"  ❌ {SKTR} bulunamadı");sys.exit(1)
    df=pd.read_excel(SKTR,sheet_name='DUZENLEME',header=None)
    data=df.iloc[4:,[2,4]].dropna(subset=[2,4])
    data.columns=['TICKER','SEKTOR']
    data=data[~data['SEKTOR'].isin(['SEKTÖR','ANA SEKTÖR'])]
    sm={}
    for _,r in data.iterrows():
        t=str(r['TICKER']).strip();s=str(r['SEKTOR']).strip()
        if t and s:sm[t]=s
    SF.write_text(json.dumps(sm,ensure_ascii=False),"utf-8")
    print(f"  ✅ {len(sm)} hisse → {len(set(sm.values()))} sektör")
    return sm

def load_cached_map():
    if SF.exists():return json.loads(SF.read_text("utf-8"))
    return load_sector_map()

def fetch_prices(tickers,period="2y"):
    print(f"  📥 {len(tickers)} hisse fiyatı çekiliyor ({period})...")
    prices={};ohlc={};errors=[];done=[0]
    def fetch_one(t):
        try:
            tk=yf.Ticker(f"{t}.IS")
            h=tk.history(period=period)
            if h is not None and len(h)>20:
                if h.index.tz is not None:h.index=h.index.tz_localize(None)
                prices[t]={d:round(float(c),4) for d,c in zip(h.index.strftime('%Y-%m-%d'),h['Close'])}
                # Son 80 gün OHLC (mum grafikleri için)
                recent=h.tail(80)
                ohlc[t]=[{'d':d,'o':round(float(r['Open']),2),'h':round(float(r['High']),2),'l':round(float(r['Low']),2),'c':round(float(r['Close']),2)} for d,r in zip(recent.index.strftime('%Y-%m-%d'),recent.to_dict('records'))]
            else:errors.append(t)
        except:errors.append(t)
        done[0]+=1
        if done[0]%50==0:print(f"     {done[0]}/{len(tickers)}...")
    for i in range(0,len(tickers),20):
        batch=tickers[i:i+20]
        with ThreadPoolExecutor(max_workers=5) as ex:list(ex.map(fetch_one,batch))
    print(f"  ✅ {len(prices)} hisse OK, {len(errors)} hata")
    return prices,ohlc

def fetch_indices():
    print("  📈 Endeksler çekiliyor...")
    idx={}
    for sym,name in [('XU100.IS','XU100'),('XU030.IS','XU030'),('XUTUM.IS','XUTUM')]:
        try:
            h=yf.Ticker(sym).history(period="2y")
            if h is not None and len(h)>20:
                if h.index.tz is not None:h.index=h.index.tz_localize(None)
                idx[name]={d:round(float(c),2) for d,c in zip(h.index.strftime('%Y-%m-%d'),h['Close'])}
                print(f"    ✅ {name}: {len(idx[name])} gün")
        except Exception as e:print(f"    ⚠️ {name}: {e}")
    return idx

def fetch_financials(tickers):
    """Çeyreklik satış ve net kâr verisi çek"""
    print(f"  📊 Çeyreklik finansal veri çekiliyor ({len(tickers)} hisse)...")
    fins={};done=[0];ok=0
    def fetch_fin(t):
        nonlocal ok
        try:
            tk=yf.Ticker(f"{t}.IS")
            q=tk.quarterly_income_stmt
            if q is not None and not q.empty:
                data=[]
                cols=sorted([str(c)[:10] for c in q.columns])
                for c in q.columns:
                    d=str(c)[:10]
                    rev=None;ni=None
                    if 'Total Revenue' in q.index:
                        v=q.loc['Total Revenue',c]
                        if pd.notna(v):rev=round(float(v))
                    if 'Net Income' in q.index:
                        v=q.loc['Net Income',c]
                        if pd.notna(v):ni=round(float(v))
                    if rev is not None or ni is not None:
                        data.append({'date':d,'revenue':rev,'net_income':ni})
                if data:
                    data.sort(key=lambda x:x['date'])
                    fins[t]=data  # Tüm çeyrekler
                    ok+=1
        except:pass
        done[0]+=1
        if done[0]%50==0:print(f"     {done[0]}/{len(tickers)}...")
    for i in range(0,len(tickers),20):
        batch=tickers[i:i+20]
        with ThreadPoolExecutor(max_workers=5) as ex:list(ex.map(fetch_fin,batch))
    print(f"  ✅ {ok} hisse finansal veri OK")
    return fins

def save_financials(fins):
    FF.write_text(json.dumps(fins,ensure_ascii=False),"utf-8")
    print(f"  💾 {FF}")

def load_financials():
    if FF.exists():return json.loads(FF.read_text("utf-8"))
    return {}

def save_prices(prices,indices):
    PF.write_text(json.dumps({"updated":datetime.now().isoformat(),"prices":prices,"indices":indices},ensure_ascii=False),"utf-8")
    print(f"  💾 {PF}")

def save_ohlc(ohlc):
    OF.write_text(json.dumps(ohlc,ensure_ascii=False),"utf-8")
    print(f"  💾 {OF}")

def load_ohlc():
    if OF.exists():return json.loads(OF.read_text("utf-8"))
    return {}

def load_prices():
    if PF.exists():
        d=json.loads(PF.read_text("utf-8"))
        return d.get("prices",{}),d.get("indices",{})
    return {},{}

def get_return(price_dict,days):
    dates=sorted(price_dict.keys())
    if len(dates)<=days:return None
    last=price_dict[dates[-1]];prev=price_dict[dates[-1-days]]
    return round((last-prev)/prev*100,2) if prev>0 else None

def calc_performance(price_dict):
    dates=sorted(price_dict.keys())
    if len(dates)<2:return None
    last=price_dict[dates[-1]]
    perf={}
    for k,days in {'1d':1,'1w':5,'1m':21,'3m':63,'6m':126,'9m':189,'12m':252}.items():
        perf[k]=get_return(price_dict,days)
    perf['last']=last
    return perf

def calc_rs_score(val,all_vals):
    valid=[p for p in all_vals if p is not None]
    if not valid or val is None:return None
    rank=sum(1 for p in valid if p<val)
    return max(1,min(99,int(rank/len(valid)*99)+1))

def calc_ms_raw(perf):
    v3=perf.get('3m');v6=perf.get('6m');v9=perf.get('9m');v12=perf.get('12m')
    parts=[];weights=[]
    if v3 is not None:parts.append(v3*0.4);weights.append(0.4)
    if v6 is not None:parts.append(v6*0.2);weights.append(0.2)
    if v9 is not None:parts.append(v9*0.2);weights.append(0.2)
    if v12 is not None:parts.append(v12*0.2);weights.append(0.2)
    if not parts:return None
    return round(sum(parts)/sum(weights),2)

def compute_all(sm,prices,indices):
    print("  🧮 Hesaplanıyor...")
    stock_perf={};stock_rs5_raw={};stock_rs21_raw={};stock_ms_raw={}
    for t in sm:
        if t in prices:
            p=calc_performance(prices[t])
            if p:
                stock_perf[t]=p
                r5=get_return(prices[t],5);r21=get_return(prices[t],21);ms=calc_ms_raw(p)
                if r5 is not None:stock_rs5_raw[t]=r5
                if r21 is not None:stock_rs21_raw[t]=r21
                if ms is not None:stock_ms_raw[t]=ms
    all5=list(stock_rs5_raw.values());all21=list(stock_rs21_raw.values());allms=list(stock_ms_raw.values())
    stock_rs5={t:calc_rs_score(v,all5) for t,v in stock_rs5_raw.items()}
    stock_rs21={t:calc_rs_score(v,all21) for t,v in stock_rs21_raw.items()}
    stock_rsms={t:calc_rs_score(v,allms) for t,v in stock_ms_raw.items()}
    idx_perf={}
    for name,pd_dict in indices.items():
        p=calc_performance(pd_dict)
        if p:idx_perf[name]=p
    sectors={}
    for t,s in sm.items():
        if s not in sectors:sectors[s]=[]
        if t in stock_perf:sectors[s].append(t)
    sector_perf={}
    for s,tickers in sectors.items():
        if not tickers:continue
        sp={'count':len(tickers)}
        for period in ['1d','1w','1m','3m','6m','9m','12m']:
            vals=[stock_perf[t][period] for t in tickers if stock_perf[t].get(period) is not None]
            sp[period]=round(np.mean(vals),2) if vals else None
        sector_perf[s]=sp
    def sector_rs(raw_dict):
        sec_raw={}
        for s,tickers in sectors.items():
            vals=[raw_dict[t] for t in tickers if t in raw_dict]
            if vals:sec_raw[s]=round(np.mean(vals),2)
        all_v=list(sec_raw.values())
        return {s:calc_rs_score(v,all_v) for s,v in sec_raw.items()}
    return {'stock_perf':stock_perf,'stock_rs5':stock_rs5,'stock_rs21':stock_rs21,'stock_rsms':stock_rsms,
            'idx_perf':idx_perf,'sector_perf':sector_perf,
            'sec_rs5':sector_rs(stock_rs5_raw),'sec_rs21':sector_rs(stock_rs21_raw),'sec_rsms':sector_rs(stock_ms_raw),
            'sectors':sectors,'sector_map':sm}

def make_html(result,fins=None,ohlc=None):
    print("  🎨 Dashboard...")
    if fins:result['fins']=fins
    if ohlc:result['ohlc']=ohlc
    dj=json.dumps(result,ensure_ascii=False)
    h="""<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>BIST Sektör Performansı</title>
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
.fv-name{width:220px;min-width:220px;font-size:11px;font-weight:600;padding-right:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
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
@media(max-width:768px){.ct{padding:0 12px 36px}.fv-name{width:120px;min-width:120px}.hisse-search{width:160px}}
</style></head><body>
<div class="hd"><div><h1>BIST <span>Sektör Performansı</span></h1><div class="meta" id="hm"></div></div></div>
<!-- ===== TOP LEVEL TABS: Sektör / Hisse ===== -->
<div class="main-tabs">
<button class="main-tab a" onclick="switchMain('sektor',this)">Sektör</button>
<button class="main-tab" onclick="switchMain('hisse',this)">Hisse</button>
</div>
<!-- ===== SEKTÖR PANEL ===== -->
<div id="panel-sektor" class="main-panel a">
<div class="tabs" id="mt">
<button class="tab a" onclick="sT('bars',this)">Sektör Barları</button>
<button class="tab" onclick="sT('tbl',this)">Tablo</button></div>
<div class="ct">
<div class="ctr" id="gp"><span class="cl">Periyot:</span>
<button class="btn" onclick="sP('1d',this)">1G</button><button class="btn" onclick="sP('1w',this)">1H</button>
<button class="btn a" onclick="sP('1m',this)">1A</button><button class="btn" onclick="sP('3m',this)">3A</button>
<button class="btn" onclick="sP('6m',this)">6A</button><button class="btn" onclick="sP('12m',this)">12A</button></div>
<div class="idx-bar" id="idxBar"></div>
<div id="tab-bars" class="tc a"><div class="fv" id="fvGrid"></div></div>
<div id="tab-tbl" class="tc">
<div class="view-toggle" id="viewToggle" style="display:none">
<button class="btn a" onclick="setView('table',this)">Tablo</button>
<button class="btn" onclick="setView('charts',this)">Grafikler</button>
<span style="margin-left:12px;cursor:pointer;color:#6366f1;font-size:11px" id="backBtn2" onclick="showSectors()">← Sektörlere dön</span></div>
<div id="tableView">
<div class="tw"><div class="th"><span id="tblTitle">Sektör Performansı</span><span class="back" id="backBtn" onclick="showSectors()">← Sektörlere dön</span></div>
<div style="overflow-x:auto"><table id="mainTbl"></table></div></div></div>
<div id="chartView" style="display:none"><div class="chart-grid" id="chartGrid"></div></div>
</div>
</div>
</div>
<!-- ===== HİSSE PANEL ===== -->
<div id="panel-hisse" class="main-panel">
<div class="tabs" id="hisseTabs">
<button class="tab a" onclick="setHisseView('table',this)">Tablo</button>
<button class="tab" onclick="setHisseView('charts',this)">Grafikler</button>
</div>
<div class="ct">
<div class="ctr" id="hisseGp"><span class="cl">Periyot:</span>
<button class="btn" onclick="hisseSP('1d',this)">1G</button><button class="btn" onclick="hisseSP('1w',this)">1H</button>
<button class="btn a" onclick="hisseSP('1m',this)">1A</button><button class="btn" onclick="hisseSP('3m',this)">3A</button>
<button class="btn" onclick="hisseSP('6m',this)">6A</button><button class="btn" onclick="hisseSP('12m',this)">12A</button>
<span style="margin-left:12px"><input type="text" id="hisseSearch" class="hisse-search" placeholder="Hisse ara..." oninput="renderHisse()"></span>
</div>
<div class="idx-bar" id="hisseIdxBar"></div>
<div id="hisseTableWrap">
<div class="tw"><div class="th"><span>Tüm Hisseler</span><span id="hisseCount" style="font-size:11px;color:rgba(255,255,255,.3);font-family:monospace"></span></div>
<div style="overflow-x:auto"><table id="hisseTbl"></table></div></div>
</div>
<div id="hisseChartWrap" style="display:none">
<div style="margin-bottom:10px"><button class="btn" id="cloudFilterBtn" onclick="toggleCloudFilter()">☁️ Cloud İçindekiler</button><button class="btn" id="atrFilterBtn" onclick="toggleAtrFilter()" style="margin-left:5px">📏 EMA'ya 1ATR Yakın</button><button class="btn" id="rs85FilterBtn" onclick="toggleRs85Filter()" style="margin-left:5px">⚡ RS 85+</button><span id="cloudFilterInfo" style="font-size:10px;color:rgba(255,255,255,.3);font-family:monospace;margin-left:8px"></span><button class="btn" onclick="exportTvList()" style="margin-left:12px;border-color:rgba(99,102,241,.3);color:rgba(165,180,252,.7)">📋 TV Liste</button></div>
<div class="chart-grid" id="hisseChartGrid"></div>
</div>
</div>
</div>
<!-- ===== MODAL (shared) ===== -->
<div class="modal-bg" id="modalBg" onclick="if(event.target===this)closeModal()">
<div class="modal"><button class="close" onclick="closeModal()">✕</button>
<h2 id="mTitle"></h2><div class="msub" id="mSub"></div>
<div style="display:flex;gap:5px;margin-bottom:12px" id="yoyBtns">
<button class="btn a" onclick="setYoyMode('yoy',this)">YoY (Yıllık)</button>
<button class="btn" onclick="setYoyMode('qoq',this)">QoQ (Çeyreklik)</button></div>
<div class="ch"><canvas id="cRev"></canvas></div>
<div class="ch"><canvas id="cNI"></canvas></div>
</div></div>
<div class="ft"><span id="ft"></span><span>Sektör Dashboard v2.0</span></div>
"""
    h+="<script>\nconst R="+dj+";\n"
    h+=r"""let curP='1m',curSec=null,sC=-1,sA=false,barSort='perf',curView='table';
let hisseP='1m',hisseSC=-1,hisseSA=false,hisseViewMode='table',cloudFilter=false,atrFilter=false,rs85Filter=false;
const miniCharts=[];
// ===== MAIN TAB SWITCHING =====
function switchMain(panel,btn){
document.querySelectorAll('.main-panel').forEach(p=>p.classList.remove('a'));
document.querySelectorAll('.main-tab').forEach(b=>b.classList.remove('a'));
document.getElementById('panel-'+panel).classList.add('a');
if(btn)btn.classList.add('a');
if(panel==='hisse')renderHisse();
if(panel==='sektor'){renderIdx();renderBars();renderTable()}}
// ===== SEKTÖR TAB FUNCTIONS =====
function setView(v,btn){curView=v;document.querySelectorAll('#viewToggle .btn').forEach(b=>b.classList.remove('a'));if(btn)btn.classList.add('a');
document.getElementById('tableView').style.display=v==='table'?'block':'none';
document.getElementById('chartView').style.display=v==='charts'?'block':'none';
if(v==='charts'&&curSec)renderChartGrid()}
function destroyMiniCharts(){miniCharts.forEach(c=>c.destroy());miniCharts.length=0}
const rT={rs5:'RS5: Son 5 günlük getiri bazlı percentile rank (1-99). Kısa vadeli momentum ölçer.',rs21:'RS21: Son 21 günlük getiri bazlı percentile rank (1-99). Orta vadeli momentum ölçer.',rsms:'RS-MS (MarketSmith): (3A getiri×40% + 6A×20% + 9A×20% + 12A×20%) bazlı percentile rank (1-99). Uzun vadeli relatif güç.'};
function sT(id,b){document.querySelectorAll('.tc').forEach(t=>t.classList.remove('a'));document.querySelectorAll('#mt .tab').forEach(t=>t.classList.remove('a'));document.getElementById('tab-'+id).classList.add('a');if(b)b.classList.add('a')}
function sP(p,b){curP=p;document.querySelectorAll('#gp .btn').forEach(x=>x.classList.remove('a'));if(b)b.classList.add('a');sC=-1;barSort='perf';render()}
function pC(v){if(v==null)return'rgba(255,255,255,.08)';if(v>0)return v>8?'rgba(22,163,74,0.9)':v>4?'rgba(34,197,94,0.7)':v>2?'rgba(74,222,128,0.55)':'rgba(74,222,128,0.35)';return v<-8?'rgba(185,28,28,0.9)':v<-4?'rgba(220,38,38,0.7)':v<-2?'rgba(248,113,113,0.55)':'rgba(248,113,113,0.35)'}
function vF(v){if(v==null)return'-';return(v>0?'+':'')+v.toFixed(2)+'%'}
function vK(v){return v>0?'sp':v<0?'sn':''}
function rC(rs){if(rs==null)return['rgba(255,255,255,.08)','rgba(255,255,255,.5)'];if(rs>=80)return['rgba(22,163,74,0.3)','#4ade80'];if(rs>=60)return['rgba(74,222,128,0.15)','#4ade80'];if(rs>=40)return['rgba(255,255,255,.06)','rgba(255,255,255,.5)'];if(rs>=20)return['rgba(248,113,113,0.15)','#f87171'];return['rgba(185,28,28,0.3)','#f87171']}
function rB(v){const[bg,c]=rC(v);return'<span class="rb" style="background:'+bg+';color:'+c+'">'+(v||'-')+'</span>'}
function sortBar(k){barSort=k;renderBars()}
function renderIdx(){const bar=document.getElementById('idxBar');let h='';
['XU100','XU030','XUTUM'].forEach(n=>{const p=R.idx_perf[n];if(!p)return;const v=p[curP];const c=v!=null?(v>0?'#4ade80':'#f87171'):'rgba(255,255,255,.4)';
h+='<div class="idx-card"><div class="idx-name">'+n+'</div><div class="idx-val" style="color:'+c+'">'+vF(v)+'</div><div class="idx-chg" style="color:rgba(255,255,255,.4)">Son: '+(p.last||0).toLocaleString('tr-TR')+'</div></div>'});
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
let h='<div style="display:flex;align-items:center;margin-bottom:8px;padding:0 6px"><div style="width:220px;min-width:220px"></div><div style="flex:1"></div><div style="width:70px;min-width:70px"></div><div style="width:120px;min-width:120px;display:flex;gap:3px;justify-content:center;font-size:8px;font-family:monospace"><span style="min-width:24px;text-align:center;cursor:pointer;'+bsAct('rs5')+'" title="'+rT.rs5+'" onclick="sortBar(\'rs5\')">RS5</span><span style="min-width:24px;text-align:center;cursor:pointer;'+bsAct('rs21')+'" title="'+rT.rs21+'" onclick="sortBar(\'rs21\')">RS21</span><span style="min-width:24px;text-align:center;cursor:pointer;'+bsAct('rsms')+'" title="'+rT.rsms+'" onclick="sortBar(\'rsms\')">MS</span></div><div style="width:35px;min-width:35px"></div></div>';
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
let h='<tr>'+tH(0,'Hisse')+tH(1,'RS5',rT.rs5)+tH(2,'RS21',rT.rs21)+tH(3,'RS-MS',rT.rsms)+tH(4,'1G')+tH(5,'1H')+tH(6,'1A')+tH(7,'3A')+tH(8,'6A')+tH(9,'12A')+'</tr>';
rows.forEach(r=>{h+='<tr style="cursor:pointer" onclick="showFin(\''+r[0]+'\')"><td>'+r[0]+'</td><td>'+rB(r[1])+'</td><td>'+rB(r[2])+'</td><td>'+rB(r[3])+'</td>';
for(let i=4;i<=9;i++)h+='<td class="'+vK(r[i])+'">'+vF(r[i])+'</td>';h+='</tr>'});tbl.innerHTML=h;
}else{tt.textContent='Sektör Performansı';bb.style.display='none';
const sp=R.sector_perf;
const rows=Object.keys(sp).map(s=>[s,R.sec_rs5[s]||0,R.sec_rs21[s]||0,R.sec_rsms[s]||0,sp[s].count,sp[s]['1d'],sp[s]['1w'],sp[s]['1m'],sp[s]['3m'],sp[s]['6m'],sp[s]['12m']]);
if(sC>=0)rows.sort((a,b)=>{const va=a[sC],vb=b[sC];if(va==null)return 1;if(vb==null)return -1;if(typeof va==='string')return sA?va.localeCompare(vb):vb.localeCompare(va);return sA?va-vb:vb-va});
else rows.sort((a,b)=>(sp[b[0]][curP]||0)-(sp[a[0]][curP]||0));
let h='<tr>'+tH(0,'Sektör')+tH(1,'RS5',rT.rs5)+tH(2,'RS21',rT.rs21)+tH(3,'RS-MS',rT.rsms)+tH(4,'Hisse')+tH(5,'1G')+tH(6,'1H')+tH(7,'1A')+tH(8,'3A')+tH(9,'6A')+tH(10,'12A')+'</tr>';
rows.forEach(r=>{h+='<tr class="sr" onclick="curSec=\''+r[0].replace(/'/g,"\\'")+'\';sC=-1;renderTable();renderIdx()">';
h+='<td>'+r[0]+'</td><td>'+rB(r[1])+'</td><td>'+rB(r[2])+'</td><td>'+rB(r[3])+'</td><td style="color:rgba(255,255,255,.3)">'+r[4]+'</td>';
for(let i=5;i<=10;i++)h+='<td class="'+vK(r[i])+'">'+vF(r[i])+'</td>';h+='</tr>'});tbl.innerHTML=h}}
// ===== FINANCIALS MODAL =====
let _cRev=null,_cNI=null,_curFin=null,_yoyMode='yoy';
function closeModal(){document.getElementById('modalBg').classList.remove('show');if(_cRev){_cRev.destroy();_cRev=null}if(_cNI){_cNI.destroy();_cNI=null}_curFin=null}
function fmtB(v){if(v==null)return'-';const a=Math.abs(v);if(a>=1e9)return(v/1e9).toFixed(1)+' mr';if(a>=1e6)return(v/1e6).toFixed(1)+' mn';if(a>=1e3)return(v/1e3).toFixed(0)+' bin';return v.toString()}
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
if(!f||f.length<1){document.getElementById('mSub').textContent='Çeyreklik finansal veri bulunamadı';
document.getElementById('cRev').style.display='none';document.getElementById('cNI').style.display='none';
document.getElementById('yoyBtns').style.display='none';
document.getElementById('modalBg').classList.add('show');return}
document.getElementById('cRev').style.display='block';document.getElementById('cNI').style.display='block';
document.getElementById('yoyBtns').style.display='flex';
const sec=R.sector_map[t]||'';document.getElementById('mSub').textContent=sec+' · '+f.length+' çeyrek';
const labels=f.map(x=>{const p=x.date.split('-');return p[0].slice(2)+'/'+p[1]});
const revs=f.map(x=>x.revenue);const nis=f.map(x=>x.net_income);
const revChg=_yoyMode==='qoq'?calcQoQ(f,'revenue'):calcYoY(f,'revenue');
const niChg=_yoyMode==='qoq'?calcQoQ(f,'net_income'):calcYoY(f,'net_income');
const chgLabel=_yoyMode==='qoq'?'QoQ':'YoY';
if(_cRev)_cRev.destroy();if(_cNI)_cNI.destroy();
const defs={responsive:true,maintainAspectRatio:false,layout:{padding:{bottom:18}},plugins:{legend:{display:false}},scales:{x:{ticks:{color:'rgba(255,255,255,.4)',font:{family:'monospace',size:10}}},y:{ticks:{color:'rgba(255,255,255,.3)',font:{family:'monospace',size:9},callback:v=>fmtB(v)},grid:{color:'rgba(255,255,255,.04)'}}}};
_cRev=new Chart(document.getElementById('cRev'),{type:'bar',data:{labels,datasets:[{label:'Satışlar',data:revs,backgroundColor:revs.map(v=>v!=null&&v>0?'rgba(96,165,250,0.6)':'rgba(248,113,113,0.6)'),borderRadius:4,barPercentage:0.5,categoryPercentage:0.7}]},options:{...defs,plugins:{...defs.plugins,title:{display:true,text:'Çeyreklik Satışlar',color:'rgba(255,255,255,.6)',font:{size:12}}}},plugins:[mkYoyPlug(revChg)]});
_cNI=new Chart(document.getElementById('cNI'),{type:'bar',data:{labels,datasets:[{label:'Net Kâr',data:nis,backgroundColor:nis.map(v=>v!=null&&v>=0?'rgba(74,222,128,0.6)':'rgba(248,113,113,0.6)'),borderRadius:4,barPercentage:0.5,categoryPercentage:0.7}]},options:{...defs,plugins:{...defs.plugins,title:{display:true,text:'Çeyreklik Net Kâr',color:'rgba(255,255,255,.6)',font:{size:12}}}},plugins:[mkYoyPlug(niChg)]});
document.getElementById('modalBg').classList.add('show')}
function showFin(t){_yoyMode='yoy';document.querySelectorAll('#yoyBtns .btn').forEach((b,i)=>{b.classList.toggle('a',i===0)});renderFin(t)}
// ===== CANDLE DRAWING (shared) =====
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
const rss=R.stock_rs21;
let h='';
sorted.forEach((t,idx)=>{
const p=R.stock_perf[t];const chg=p?p[curP]:null;
const chgC=chg!=null?(chg>0?'#4ade80':'#f87171'):'rgba(255,255,255,.4)';
const rs=rss[t]||'-';
const rsC=typeof rs==='number'&&rs>=80?'#4ade80':'#f87171';
h+='<div class="mini-chart" onclick="showFin(\''+t+'\')"><div class="mc-hd"><span class="mc-name">'+t+' <span style="font-size:11px;font-weight:700;color:'+rsC+'">RS:'+rs+'</span></span><span class="mc-chg" style="color:'+chgC+'">'+vF(chg)+'</span></div><canvas id="mc_'+idx+'"></canvas></div>'});
grid.innerHTML=h;
sorted.forEach((t,idx)=>{
const ohlc=(R.ohlc||{})[t];
const cvs=document.getElementById('mc_'+idx);
if(cvs)drawCandles(cvs,ohlc,'mc_')})}
// ===== CLOUD FILTER =====
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
// 21 EMA close
let em=data[0].c;
for(let i=1;i<data.length;i++){em=data[i].c*k+em*(1-k)}
// ATR 14
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
// ===== HİSSE TAB FUNCTIONS =====
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
// Render index bar
const ibar=document.getElementById('hisseIdxBar');let ih='';
['XU100','XU030','XUTUM'].forEach(n=>{const p=R.idx_perf[n];if(!p)return;const v=p[hisseP];const c=v!=null?(v>0?'#4ade80':'#f87171'):'rgba(255,255,255,.4)';
ih+='<div class="idx-card"><div class="idx-name">'+n+'</div><div class="idx-val" style="color:'+c+'">'+vF(v)+'</div><div class="idx-chg" style="color:rgba(255,255,255,.4)">Son: '+(p.last||0).toLocaleString('tr-TR')+'</div></div>'});
ibar.innerHTML=ih;
if(hisseViewMode==='table')renderHisseTable(tks);
else renderHisseCharts(tks)}
function renderHisseTable(tks){
const tbl=document.getElementById('hisseTbl');
const ar=hisseSA?'▲':'▼';
function tH(i,l,tip){const cls=hisseSC===i?' class="st"':'';const t=tip?' title="'+tip+'"':'';return'<th'+cls+t+' onclick="hisseTS('+i+')">'+l+'<span class="sa">'+(hisseSC===i?ar:'⇅')+'</span></th>'}
// columns: 0=Hisse, 1=Sektör, 2=RS5, 3=RS21, 4=RS-MS, 5=1G, 6=1H, 7=1A, 8=3A, 9=6A, 10=12A
const rows=tks.map(t=>{const p=R.stock_perf[t];return[t,R.sector_map[t]||'',R.stock_rs5[t]||0,R.stock_rs21[t]||0,R.stock_rsms[t]||0,p['1d'],p['1w'],p['1m'],p['3m'],p['6m'],p['12m']]});
if(hisseSC>=0)rows.sort((a,b)=>{const va=a[hisseSC],vb=b[hisseSC];if(va==null)return 1;if(vb==null)return -1;if(typeof va==='string')return hisseSA?va.localeCompare(vb):vb.localeCompare(va);return hisseSA?va-vb:vb-va});
else{const pMap={'1d':5,'1w':6,'1m':7,'3m':8,'6m':9,'12m':10};const di=pMap[hisseP]||7;rows.sort((a,b)=>{const va=a[di],vb=b[di];if(va==null)return 1;if(vb==null)return -1;return vb-va})}
document.getElementById('hisseCount').textContent=rows.length+' hisse';
let h='<tr>'+tH(0,'Hisse')+tH(1,'Sektör')+tH(2,'RS5',rT.rs5)+tH(3,'RS21',rT.rs21)+tH(4,'RS-MS',rT.rsms)+tH(5,'1G')+tH(6,'1H')+tH(7,'1A')+tH(8,'3A')+tH(9,'6A')+tH(10,'12A')+'</tr>';
rows.forEach(r=>{h+='<tr style="cursor:pointer" onclick="showFin(\''+r[0]+'\')">';
h+='<td>'+r[0]+'</td><td style="color:rgba(255,255,255,.35);font-weight:400;font-size:10px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+r[1]+'</td>';
h+='<td>'+rB(r[2])+'</td><td>'+rB(r[3])+'</td><td>'+rB(r[4])+'</td>';
for(let i=5;i<=10;i++)h+='<td class="'+vK(r[i])+'">'+vF(r[i])+'</td>';h+='</tr>'});
tbl.innerHTML=h}
function renderHisseCharts(tks){
const grid=document.getElementById('hisseChartGrid');
const info=document.getElementById('cloudFilterInfo');
// Apply cloud filter
let filtered=tks;
if(cloudFilter){filtered=filtered.filter(t=>isInCloud(t))}
if(atrFilter){filtered=filtered.filter(t=>isNearEma(t))}
if(rs85Filter){filtered=filtered.filter(t=>{const rs=R.stock_rs21[t];return typeof rs==='number'&&rs>=85})}
if(cloudFilter||atrFilter||rs85Filter){info.textContent=filtered.length+'/'+tks.length+' hisse'}
else{info.textContent=''}
// Sort same as table
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
const rss=R.stock_rs21;
let h='';
rows.forEach(({t},idx)=>{
const p=R.stock_perf[t];const chg=p?p[hisseP]:null;
const chgC=chg!=null?(chg>0?'#4ade80':'#f87171'):'rgba(255,255,255,.4)';
const rs=rss[t]||'-';
const rsC=typeof rs==='number'&&rs>=80?'#4ade80':'#f87171';
const sec=R.sector_map[t]||'';
h+='<div class="mini-chart" onclick="showFin(\''+t+'\')"><div class="mc-hd"><span class="mc-name">'+t+' <span style="font-size:9px;font-weight:400;color:rgba(255,255,255,.3)">'+sec+'</span> <span style="font-size:11px;font-weight:700;color:'+rsC+'">RS:'+rs+'</span></span><span class="mc-chg" style="color:'+chgC+'">'+vF(chg)+'</span></div><canvas id="hmc_'+idx+'"></canvas></div>'});
grid.innerHTML=h;
// Draw candles (defer to avoid blocking)
requestAnimationFrame(()=>{
rows.forEach(({t},idx)=>{
const ohlc=(R.ohlc||{})[t];
const cvs=document.getElementById('hmc_'+idx);
if(cvs)drawCandles(cvs,ohlc,'hmc_')})})}
// ===== TV LIST EXPORT =====
function exportTvList(){
const tks=getFilteredStocks();
let filtered=tks;
if(cloudFilter){filtered=filtered.filter(t=>isInCloud(t))}
if(atrFilter){filtered=filtered.filter(t=>isNearEma(t))}
if(rs85Filter){filtered=filtered.filter(t=>{const rs=R.stock_rs21[t];return typeof rs==='number'&&rs>=85})}
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
const listStr=rows.map(r=>'BIST:'+r.t).join(',');
const old=document.getElementById('tvExportModal');if(old)old.remove();
const div=document.createElement('div');
div.id='tvExportModal';
div.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.75);z-index:200;display:flex;justify-content:center;align-items:center';
div.onclick=function(e){if(e.target===div)div.remove()};
div.innerHTML='<div style="background:#12121e;border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:24px;width:90%;max-width:680px;max-height:80vh;overflow-y:auto;position:relative">'
+'<button onclick="document.getElementById(\'tvExportModal\').remove()" style="position:absolute;top:12px;right:16px;font-size:20px;cursor:pointer;color:rgba(255,255,255,.4);background:none;border:none">✕</button>'
+'<h2 style="font-size:15px;font-weight:700;margin-bottom:4px">TradingView Liste</h2>'
+'<div style="font-size:11px;color:rgba(255,255,255,.35);font-family:monospace;margin-bottom:14px">'+rows.length+' ticker · TradingView Watchlist\'e yapıştır (virgülle ayrılmış)</div>'
+'<textarea id="tvListTA" readonly style="width:100%;height:160px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:12px;color:#e2e8f0;font-family:monospace;font-size:11px;resize:vertical;outline:none;line-height:1.5">'+listStr+'</textarea>'
+'<div style="display:flex;gap:8px;margin-top:12px">'
+'<button onclick="const ta=document.getElementById(\'tvListTA\');ta.select();navigator.clipboard.writeText(ta.value).then(()=>{this.textContent=\'✓ Kopyalandı!\';setTimeout(()=>this.textContent=\'📋 Kopyala\',2000)}).catch(()=>{ta.select();document.execCommand(\'copy\');this.textContent=\'✓ Kopyalandı!\';setTimeout(()=>this.textContent=\'📋 Kopyala\',2000)})" class="btn a" style="font-size:12px;padding:8px 18px">📋 Kopyala</button>'
+'<button onclick="const ta=document.getElementById(\'tvListTA\');const spaced=ta.value.replace(/,/g,\' \');navigator.clipboard.writeText(spaced).catch(()=>{});this.textContent=\'✓ Kopyalandı!\';setTimeout(()=>this.textContent=\'Boşluklu Kopyala\',2000)" class="btn" style="font-size:12px;padding:8px 18px">Boşluklu Kopyala</button>'
+'</div>'
+'<div style="font-size:10px;color:rgba(255,255,255,.2);font-family:monospace;margin-top:10px">💡 TradingView → Watchlist → Import symbols → yapıştır</div>'
+'</div>';
document.body.appendChild(div);
setTimeout(()=>{const ta=document.getElementById('tvListTA');if(ta)ta.select()},50)}
// ===== RENDER =====
function render(){renderIdx();renderBars();renderTable();
document.getElementById('hm').textContent=new Date().toLocaleString('tr-TR')+' · '+Object.keys(R.sector_map).length+' hisse · '+Object.keys(R.sector_perf).length+' sektör';
document.getElementById('ft').textContent='Son güncelleme: '+new Date().toLocaleString('tr-TR')}
render();
"""
    h+="</script></body></html>"
    HF.write_text(h,"utf-8")
    print(f"  ✅ {HF}")

if __name__=="__main__":
    print(f"\n{'='*60}\n  🏭 BIST SEKTÖR PERFORMANSI\n{'='*60}")
    if "--dashboard" in sys.argv:
        sm=load_cached_map();prices,indices=load_prices();fins=load_financials();ohlc=load_ohlc()
        make_html(compute_all(sm,prices,indices),fins,ohlc)
    elif "--update" in sys.argv:
        sm=load_sector_map();prices,indices=load_prices()
        np2,ohlc=fetch_prices(list(sm.keys()),period="1y")
        for t,p in np2.items():
            if t in prices:prices[t].update(p)
            else:prices[t]=p
        ni=fetch_indices()
        for n,d in ni.items():
            if n in indices:indices[n].update(d)
            else:indices[n]=d
        save_prices(prices,indices);save_ohlc(ohlc)
        fins=load_financials()
        if not fins or len(fins)<100:
            fins=fetch_financials(list(sm.keys()));save_financials(fins)
        make_html(compute_all(sm,prices,indices),fins,ohlc)
    else:
        sm=load_sector_map();prices,ohlc=fetch_prices(list(sm.keys()),period="2y")
        indices=fetch_indices();save_prices(prices,indices);save_ohlc(ohlc)
        fins=fetch_financials(list(sm.keys()));save_financials(fins)
        make_html(compute_all(sm,prices,indices),fins,ohlc)
    print(f"\n  🎉 TAMAM! → open {HF}\n")
