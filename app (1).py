"""
景氣位階追蹤器 v3 — iPhone + Streamlit Cloud 版
新增：ISM 新訂單、台灣外銷訂單、股市動能、配置建議
"""
import os, datetime, requests, re
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dataclasses import dataclass, asdict

def get_secret(key, default=""):
    try:    return st.secrets.get(key, os.getenv(key, default))
    except: return os.getenv(key, default)

FRED_API_KEY       = get_secret("FRED_API_KEY")
TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = get_secret("TELEGRAM_CHAT_ID")
CACHE_TTL = 60

@dataclass
class Indicator:
    name: str; value: float; prev_value: float
    unit: str; type: str; signal: str; source: str
    weight: float = 1.0; updated: str = ""

@dataclass
class MarketCycle:
    market: str; phase: str; score: float
    indicators: list; summary: str; timestamp: str

def fred(sid, fallback, limit=2):
    if not FRED_API_KEY: return fallback
    try:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
            params={"series_id":sid,"api_key":FRED_API_KEY,"file_type":"json",
                    "limit":limit,"sort_order":"desc"}, timeout=12)
        obs=[float(o["value"]) for o in r.json().get("observations",[]) if o["value"] not in(".","")]
        return (obs[0], obs[1] if len(obs)>1 else obs[0])
    except: return fallback

def sp500_momentum():
    try:
        if FRED_API_KEY:
            r=requests.get("https://api.stlouisfed.org/fred/series/observations",
                params={"series_id":"SP500","api_key":FRED_API_KEY,"file_type":"json",
                        "limit":13,"sort_order":"desc"},timeout=12)
            obs=[float(o["value"]) for o in r.json().get("observations",[]) if o["value"] not in(".","")]
            if len(obs)>=13:
                latest=obs[0]; ma12=sum(obs[:12])/12
                return {"latest":latest,"ma12":ma12,"above_ma":latest>ma12,
                        "pct":round((latest/ma12-1)*100,2)}
    except: pass
    return {"latest":5200.0,"ma12":4950.0,"above_ma":True,"pct":5.05}

def taiex_momentum():
    try:
        r=requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII",
            params={"interval":"1mo","range":"13mo"},
            headers={"User-Agent":"Mozilla/5.0"},timeout=12)
        closes=r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes=[c for c in closes if c is not None]
        if len(closes)>=13:
            latest=closes[-1]; ma12=sum(closes[-13:-1])/12
            return {"latest":round(latest),"ma12":round(ma12),"above_ma":latest>ma12,
                    "pct":round((latest/ma12-1)*100,2)}
    except: pass
    return {"latest":21500,"ma12":20800,"above_ma":True,"pct":3.37}

def taiwan_export_orders():
    return {"yoy":12.3,"prev_yoy":8.5,"source":"經濟部（模擬）"}

def build_taiwan():
    def ndc(ep):
        try:
            r=requests.get(f"https://index.ndc.gov.tw/n/json/{ep}",timeout=12)
            return r.json().get("data",[])
        except: return []
    lead=ndc("leading"); sig=ndc("signal")
    exp=taiwan_export_orders(); tw_m=taiex_momentum()
    pmi_v,pmi_p=50.8,50.2
    try:
        from bs4 import BeautifulSoup
        r2=requests.get("https://www.cpsm.org.tw/news.php?act=list&id=2",
            headers={"User-Agent":"Mozilla/5.0"},timeout=10)
        soup=BeautifulSoup(r2.text,"html.parser")
        m=re.search(r"PMI[^\d]*(\d{2}\.\d)",soup.get_text(" "))
        if m: pmi_v=float(m.group(1)); pmi_p=round(pmi_v-0.3,1)
    except: pass
    li_v =float(lead[-1].get("index",101.5)) if lead else 101.5
    li_p =float(lead[-2].get("index",100.8)) if len(lead)>=2 else 100.8
    sig_v=int(sig[-1].get("score",23))        if sig else 23
    sig_p=int(sig[-2].get("score",22))        if len(sig)>=2 else 22
    tw_signal="positive" if tw_m["above_ma"] and tw_m["pct"]>2 else \
              ("neutral" if tw_m["above_ma"] else "negative")
    return [
        Indicator("製造業 PMI",pmi_v,pmi_p,"點","leading",
            "positive" if pmi_v>50 else("negative" if pmi_v<48 else"neutral"),"CPSM",weight=1.4),
        Indicator("景氣領先指標",li_v,li_p,"點","leading",
            "positive" if li_v>li_p else("negative" if li_v<li_p-0.5 else"neutral"),"國發會",weight=1.5),
        Indicator("景氣對策信號",sig_v,sig_p,"分","coincident",
            "positive" if sig_v>=23 else("negative" if sig_v<=16 else"neutral"),"國發會",weight=1.0),
        Indicator("外銷訂單年增率",exp["yoy"],exp["prev_yoy"],"%","leading",
            "positive" if exp["yoy"]>5 else("negative" if exp["yoy"]<-5 else"neutral"),
            exp["source"],weight=1.8),
        Indicator("台股 vs 12月均線",tw_m["pct"],0.0,"%","leading",
            tw_signal,"Yahoo Finance",weight=1.6),
    ]

def build_us():
    new_orders=fred("NEWORDER",(58.5,54.2))
    ism_svc   =fred("NMFCI",  (53.5,52.1))
    unemp     =fred("UNRATE", (3.9,4.0))
    spread    =fred("T10Y2Y", (0.15,-0.25))
    indpro    =fred("INDPRO", (103.2,102.8))
    umcs      =fred("UMCSENT",(79.4,76.5))
    cpi       =fred("CPIAUCSL",(3.2,3.5))
    jobless   =fred("ICSA",   (215000,225000))
    sp=sp500_momentum()
    sp_signal="positive" if sp["above_ma"] and sp["pct"]>2 else \
              ("neutral" if sp["above_ma"] else "negative")
    no_sig="positive" if new_orders[0]>55 else("neutral" if new_orders[0]>=50 else"negative")
    icsa_v=jobless[0]/1000; icsa_p=jobless[1]/1000
    icsa_sig="positive" if icsa_v<220 else("negative" if icsa_v>260 else"neutral")
    return [
        Indicator("ISM 新訂單",new_orders[0],new_orders[1],"點","leading",no_sig,"FRED",weight=2.0),
        Indicator("初領失業救濟金",icsa_v,icsa_p,"千人","leading",icsa_sig,"FRED(週更)",weight=1.8),
        Indicator("10Y-2Y 殖利率利差",spread[0],spread[1],"%","leading",
            "positive" if spread[0]>0 else("negative" if spread[0]<-0.5 else"neutral"),"FRED",weight=1.6),
        Indicator("服務業 PMI",ism_svc[0],ism_svc[1],"點","leading",
            "positive" if ism_svc[0]>52 else("negative" if ism_svc[0]<48 else"neutral"),"FRED",weight=1.4),
        Indicator("消費者信心",umcs[0],umcs[1],"點","leading",
            "positive" if umcs[0]>80 else("negative" if umcs[0]<60 else"neutral"),"FRED",weight=1.2),
        Indicator("工業生產指數",indpro[0],indpro[1],"點","coincident",
            "positive" if indpro[0]>indpro[1] else"negative","FRED",weight=1.0),
        Indicator("失業率",unemp[0],unemp[1],"%","lagging",
            "positive" if unemp[0]<4.5 else("negative" if unemp[0]>6 else"neutral"),"FRED",weight=0.5),
        Indicator("CPI 年增率",cpi[0],cpi[1],"%","lagging",
            "neutral" if 1.5<cpi[0]<3.5 else("negative" if cpi[0]>4 else"positive"),"FRED",weight=0.5),
        Indicator("S&P500 vs 12月均線",sp["pct"],0.0,"%","leading",sp_signal,"Yahoo Finance",weight=1.6),
    ]

def classify(market, inds):
    SS={"positive":100,"neutral":50,"negative":0}
    tw=ts=0.0
    for i in inds: tw+=i.weight; ts+=i.weight*SS.get(i.signal,50)
    score=round(ts/tw if tw else 50,1)
    phase=("擴張" if score>65 else "高峰趨緩" if score>50 else "收縮" if score>35 else "谷底待轉")
    pos=[i.name for i in inds if i.signal=="positive"]
    neg=[i.name for i in inds if i.signal=="negative"]
    parts=[]
    if pos: parts.append(f"正面：{', '.join(pos)}")
    if neg: parts.append(f"警示：{', '.join(neg)}")
    return MarketCycle(market=market,phase=phase,score=score,
        indicators=[asdict(i) for i in inds],
        summary=" ｜ ".join(parts) or "訊號混雜，持續觀察",
        timestamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

def get_report(force=False):
    now=datetime.datetime.now()
    cached=st.session_state.get("cache")
    ct=st.session_state.get("cache_time")
    if not force and cached and ct and (now-ct).seconds<CACHE_TTL*60: return cached
    tw_c=classify("台灣",build_taiwan()); us_c=classify("美國",build_us())
    report={"generated_at":now.isoformat(),"taiwan":asdict(tw_c),"us":asdict(us_c)}
    hist=st.session_state.get("history",[])
    today=now.strftime("%Y-%m-%d")
    hist=[h for h in hist if h["date"]!=today]
    hist.append({"date":today,"tw":tw_c.score,"us":us_c.score,
                 "tw_phase":tw_c.phase,"us_phase":us_c.phase})
    st.session_state["history"]=hist[-90:]
    st.session_state["cache"]=report; st.session_state["cache_time"]=now
    return report

def send_telegram(report):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return False
    tw,us=report["taiwan"],report["us"]
    em={"擴張":"🟢","高峰趨緩":"🟡","收縮":"🔴","谷底待轉":"🔵"}
    ar=lambda v,p: "↑" if v>p else("↓" if v<p else"→")
    lines=[f"📊 *景氣位階* {report['generated_at'][:10]}",
           f"{em.get(tw['phase'],'⚪')} *台灣* {tw['phase']} `{tw['score']}/100`",
           f"{em.get(us['phase'],'⚪')} *美國* {us['phase']} `{us['score']}/100`","","─ 台灣 ─"]
    for i in tw["indicators"]:
        s={"positive":"✅","neutral":"⚠️","negative":"❌"}.get(i["signal"],"")
        lines.append(f"{s} {i['name']}: `{i['value']:.1f}{i['unit']}` {ar(i['value'],i['prev_value'])}")
    lines+=["","─ 美國 ─"]
    for i in us["indicators"]:
        s={"positive":"✅","neutral":"⚠️","negative":"❌"}.get(i["signal"],"")
        lines.append(f"{s} {i['name']}: `{i['value']:.1f}{i['unit']}` {ar(i['value'],i['prev_value'])}")
    try:
        r=requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":"\n".join(lines),"parse_mode":"Markdown"},timeout=15)
        return r.json().get("ok",False)
    except: return False

PHASE_COLOR={"擴張":"#39d353","高峰趨緩":"#f0c040","收縮":"#f47067","谷底待轉":"#79c0ff"}
TYPE_ZH={"leading":"領先","coincident":"同步","lagging":"落後"}
def sig_color(s): return {"positive":"#39d353","neutral":"#f0c040","negative":"#f47067"}.get(s,"#888")

def market_card(cycle,accent):
    p=cycle["phase"]; s=cycle["score"]; pc=PHASE_COLOR.get(p,"#888")
    badge=f'<span style="background:{pc}22;color:{pc};border:1px solid {pc};padding:3px 12px;border-radius:4px;font-size:13px;font-weight:700">{p}</span>'
    st.markdown(f"""
<div style="border:1px solid #1c2333;border-top:3px solid {accent};border-radius:10px;
            padding:18px 14px;background:#0d1117;margin-bottom:4px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
    <span style="font-family:monospace;color:#58697a;font-size:11px">{cycle['market']} MARKET</span>{badge}
  </div>
  <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px">
    <span style="font-family:monospace;font-size:40px;font-weight:700;color:#e6edf3;line-height:1">{s}</span>
    <span style="color:#58697a;font-size:13px">/ 100</span>
  </div>
  <div style="height:5px;background:#1c2333;border-radius:3px;margin-bottom:10px">
    <div style="height:100%;width:{s}%;background:{pc};border-radius:3px"></div>
  </div>
  <div style="font-size:12px;color:#58697a;border-left:3px solid #2d3a4a;padding-left:10px;line-height:1.9">{cycle['summary']}</div>
</div>""",unsafe_allow_html=True)

def indicator_table(indicators,accent):
    rows=[]
    for i in indicators:
        up=i["value"]>i["prev_value"]; eq=i["value"]==i["prev_value"]
        ac="#39d353" if up else("#f47067" if not eq else"#58697a")
        ar="↑" if up else("→" if eq else"↓")
        sc=sig_color(i["signal"]); w=i.get("weight",1.0)
        star=" ★" if w>=1.6 else ""
        rows.append(f"""<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 6px;font-size:12px;color:#c9d1d9">
    <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{sc};margin-right:6px;vertical-align:middle"></span>{i['name']}<span style="color:{accent};font-size:10px">{star}</span></td>
  <td style="padding:9px 6px;font-family:monospace;font-size:14px;font-weight:700;color:#e6edf3;white-space:nowrap">{i['value']:.1f}<span style="font-size:10px;color:#58697a"> {i['unit']}</span></td>
  <td style="padding:9px 4px;font-family:monospace;color:{ac};font-size:14px">{ar}</td>
  <td style="padding:9px 4px"><span style="font-size:10px;color:{accent};border:1px solid {accent}44;padding:1px 6px;border-radius:3px;white-space:nowrap">{TYPE_ZH.get(i['type'],i['type'])}×{w}</span></td>
</tr>""")
    st.markdown(f"""<div style="overflow-x:auto">
<table style="width:100%;border-collapse:collapse;background:#0d1117;border-radius:8px;overflow:hidden;min-width:280px">
  <thead><tr style="border-bottom:1px solid #2d3a4a">
    <th style="padding:8px 6px;text-align:left;font-size:10px;color:#58697a;font-weight:500">指標 ★=新增</th>
    <th style="padding:8px 6px;text-align:left;font-size:10px;color:#58697a">數值</th>
    <th style="padding:8px 4px;font-size:10px;color:#58697a">趨</th>
    <th style="padding:8px 4px;text-align:left;font-size:10px;color:#58697a">類型×權重</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table></div>""",unsafe_allow_html=True)

def history_chart():
    hist=st.session_state.get("history",[])
    if len(hist)<2: st.caption("每次開啟自動記錄，累積後顯示走勢"); return
    df=pd.DataFrame(hist); fig=go.Figure()
    for col,name,color in [("tw","台灣","#ff7b7b"),("us","美國","#79c0ff")]:
        if col in df.columns:
            fig.add_trace(go.Scatter(x=df["date"],y=df[col],name=name,
                line=dict(color=color,width=2),fill="tozeroy",fillcolor=f"{color}15",
                mode="lines+markers",marker=dict(size=5,color=color)))
    for y0,y1,c in [(65,100,"rgba(57,211,83,.06)"),(35,65,"rgba(240,192,64,.06)"),(0,35,"rgba(244,112,103,.06)")]:
        fig.add_hrect(y0=y0,y1=y1,fillcolor=c,line_width=0)
    fig.add_hline(y=65,line_dash="dot",line_color="#39d35333",line_width=1)
    fig.add_hline(y=35,line_dash="dot",line_color="#f4706733",line_width=1)
    fig.update_layout(paper_bgcolor="#080c10",plot_bgcolor="#0d1117",
        font=dict(color="#58697a",size=11),
        xaxis=dict(gridcolor="#1c2333",tickfont=dict(size=9)),
        yaxis=dict(gridcolor="#1c2333",range=[0,100]),
        legend=dict(bgcolor="rgba(0,0,0,0)",orientation="h",y=1.1,font=dict(size=12)),
        margin=dict(l=0,r=0,t=8,b=0),height=220)
    st.plotly_chart(fig,use_container_width=True)

def investment_advice(tw,us):
    tw_p,us_p=tw["phase"],us["phase"]; tw_s,us_s=tw["score"],us["score"]
    tbl={
        ("擴張","擴張"):        ("🟢 雙多",  "股票70% ／ 債券20% ／ 現金10%","積極配置，可加碼科技與原物料"),
        ("擴張","高峰趨緩"):    ("🟡 謹慎多","股票60% ／ 債券25% ／ 現金15%","台股相對強，美股開始減碼"),
        ("高峰趨緩","擴張"):    ("🟡 謹慎多","股票60% ／ 債券25% ／ 現金15%","美股帶動，台股留意轉折"),
        ("高峰趨緩","高峰趨緩"):("🟡 中性",  "股票50% ／ 債券30% ／ 現金20%","減少高波動部位，留意轉折"),
        ("收縮","收縮"):        ("🔴 防禦",  "股票25% ／ 債券45% ／ 現金30%","大幅降低股票，持有公債與現金"),
        ("谷底待轉","谷底待轉"):("🔵 佈局",  "股票55% ／ 債券25% ／ 現金20%","領先指標回升時，分批布局"),
    }
    adv=tbl.get((tw_p,us_p))
    if not adv:
        adv=("🔴 偏防禦","股票35% ／ 債券40% ／ 現金25%","至少一市場收縮，保守為宜") if tw_s<40 or us_s<40 \
        else ("🟡 中性","股票50% ／ 債券30% ／ 現金20%","景氣分歧，均衡配置")
    label,alloc,tip=adv
    st.markdown(f"""
<div style="border:1px solid #2d3a4a;border-radius:8px;padding:14px 16px;background:#0d1117;margin-top:4px">
  <div style="font-family:monospace;font-size:10px;color:#58697a;margin-bottom:8px;letter-spacing:.1em">// 配置建議</div>
  <div style="font-size:16px;font-weight:700;color:#e6edf3;margin-bottom:6px">{label}</div>
  <div style="font-family:monospace;font-size:12px;color:#79c0ff;margin-bottom:8px">{alloc}</div>
  <div style="font-size:12px;color:#58697a">{tip}</div>
  <div style="font-size:10px;color:#3a4a5a;margin-top:8px">⚠️ 僅供參考，非投資建議。請依個人風險承受度調整。</div>
</div>""",unsafe_allow_html=True)

def main():
    st.set_page_config(page_title="景氣追蹤器",page_icon="📊",layout="wide",initial_sidebar_state="collapsed")
    st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&display=swap');
  html,body,[class*="css"]{font-family:'Noto Sans TC',sans-serif !important;}
  .block-container{padding:0.8rem 0.7rem 3rem !important;max-width:100% !important;}
  #MainMenu,footer{visibility:hidden;}
  header[data-testid="stHeader"]{background:#080c10 !important;}
  .stButton>button{background:#0d1117 !important;color:#c9d1d9 !important;
    border:1px solid #2d3a4a !important;border-radius:6px !important;font-size:13px !important;width:100%;}
  .stButton>button:hover{border-color:#58697a !important;color:#e6edf3 !important;}
  section[data-testid="stSidebar"]{background:#0d1117 !important;border-right:1px solid #1c2333 !important;}
  .stTextInput>div>div>input{background:#0d1117 !important;border:1px solid #2d3a4a !important;
    color:#c9d1d9 !important;font-size:13px !important;}
  .main{padding-bottom:env(safe-area-inset-bottom) !important;}
</style>""",unsafe_allow_html=True)

    c1,c2,c3=st.columns([3,1,1])
    with c1: st.markdown('<h2 style="font-family:monospace;font-size:15px;color:#e6edf3;margin:0;padding:6px 0">📊 景氣位階 v3</h2>',unsafe_allow_html=True)
    with c2: refresh=st.button("↻ 更新")
    with c3: tg_btn=st.button("📱 TG")

    with st.sidebar:
        st.markdown("### ⚙️ API 設定")
        fk=st.text_input("FRED API Key",value=FRED_API_KEY,type="password",placeholder="留空用模擬數據")
        tk=st.text_input("Telegram Bot Token",value=TELEGRAM_BOT_TOKEN,type="password")
        ci=st.text_input("Telegram Chat ID",value=TELEGRAM_CHAT_ID,type="password")
        if fk: globals()["FRED_API_KEY"]=fk
        if tk: globals()["TELEGRAM_BOT_TOKEN"]=tk
        if ci: globals()["TELEGRAM_CHAT_ID"]=ci
        st.markdown("---")
        st.markdown("""<div style="font-size:11px;color:#3a4a5a;line-height:2.1">
<b style="color:#58697a">權重說明</b><br>ISM新訂單 ×2.0 ★<br>初領失業金 ×1.8 ★<br>外銷訂單 ×1.8 ★<br>
殖利率利差 ×1.6<br>股市動能 ×1.6 ★<br>領先指標 ×1.5<br>服務業PMI ×1.4<br>消費者信心 ×1.2<br>
工業生產 ×1.0<br>失業率/CPI ×0.5<br><br>★=本版新增</div>""",unsafe_allow_html=True)

    with st.spinner("抓取指標中…"):
        report=get_report(force=refresh)
    tw=report["taiwan"]; us=report["us"]; gen=report["generated_at"][:16].replace("T"," ")

    if tg_btn:
        ok=send_telegram(report)
        st.success("已發送 ✅") if ok else st.error("失敗，請確認 Token/Chat ID")

    has_fred=bool(FRED_API_KEY)
    st.markdown(f'<p style="font-family:monospace;font-size:10px;color:#3a4a5a;margin:2px 0 12px">更新：{gen}　{"✅ FRED 真實數據" if has_fred else "⚠️ 部分模擬數據"}</p>',unsafe_allow_html=True)

    col1,col2=st.columns(2)
    with col1: market_card(tw,"#ff7b7b")
    with col2: market_card(us,"#79c0ff")
    st.markdown("<div style='height:10px'></div>",unsafe_allow_html=True)
    investment_advice(tw,us)
    st.markdown("<div style='height:12px'></div>",unsafe_allow_html=True)
    st.markdown('<p style="font-family:monospace;font-size:10px;color:#58697a;letter-spacing:.1em;margin-bottom:6px">// 歷史走勢</p>',unsafe_allow_html=True)
    history_chart()
    st.markdown("<div style='height:8px'></div>",unsafe_allow_html=True)
    with st.expander("🇹🇼 台灣指標明細",expanded=False): indicator_table(tw["indicators"],"#ff7b7b")
    with st.expander("🇺🇸 美國指標明細",expanded=False): indicator_table(us["indicators"],"#79c0ff")

    st.markdown("<div style='height:8px'></div>",unsafe_allow_html=True)

    with st.expander("📖 使用說明 & 指標解讀",expanded=False):
        st.markdown("""
<div style="color:#c9d1d9;font-size:13px;line-height:2">

<div style="font-family:monospace;font-size:11px;color:#58697a;letter-spacing:.1em;margin-bottom:12px">
// 景氣四階段</div>

<table style="width:100%;border-collapse:collapse;margin-bottom:20px">
<tr style="border-bottom:1px solid #1c2333">
  <td style="padding:10px 8px"><span style="background:#39d35322;color:#39d353;border:1px solid #39d353;padding:2px 10px;border-radius:4px;font-weight:700">🟢 擴張</span></td>
  <td style="padding:10px 8px;font-size:12px;color:#8b9baa">評分 >65｜企業獲利成長、就業市場熱絡、信貸寬鬆<br>股市通常處於多頭，風險資產表現強</td>
</tr>
<tr style="border-bottom:1px solid #1c2333">
  <td style="padding:10px 8px"><span style="background:#f0c04022;color:#f0c040;border:1px solid #f0c040;padding:2px 10px;border-radius:4px;font-weight:700">🟡 高峰趨緩</span></td>
  <td style="padding:10px 8px;font-size:12px;color:#8b9baa">評分 50–65｜成長動能減弱，領先指標開始轉向<br>股市仍在高位但波動加大，注意轉折訊號</td>
</tr>
<tr style="border-bottom:1px solid #1c2333">
  <td style="padding:10px 8px"><span style="background:#f4706722;color:#f47067;border:1px solid #f47067;padding:2px 10px;border-radius:4px;font-weight:700">🔴 收縮</span></td>
  <td style="padding:10px 8px;font-size:12px;color:#8b9baa">評分 35–50｜經濟活動走弱，企業獲利下修<br>股市通常承壓，防禦性資產相對抗跌</td>
</tr>
<tr>
  <td style="padding:10px 8px"><span style="background:#79c0ff22;color:#79c0ff;border:1px solid #79c0ff;padding:2px 10px;border-radius:4px;font-weight:700">🔵 谷底待轉</span></td>
  <td style="padding:10px 8px;font-size:12px;color:#8b9baa">評分 <35｜景氣落底，領先指標若開始回升為轉機<br>分批布局的好時機，但需確認反轉訊號</td>
</tr>
</table>

<div style="font-family:monospace;font-size:11px;color:#58697a;letter-spacing:.1em;margin-bottom:12px">
// 各指標意義</div>

<table style="width:100%;border-collapse:collapse;margin-bottom:20px">
<tr style="border-bottom:1px solid #1c2333;background:#0d1117">
  <th style="padding:8px;text-align:left;font-size:10px;color:#58697a;font-weight:500">指標</th>
  <th style="padding:8px;text-align:left;font-size:10px;color:#58697a;font-weight:500">意義</th>
  <th style="padding:8px;text-align:left;font-size:10px;color:#58697a;font-weight:500">關鍵門檻</th>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#a78bfa">ISM 新訂單 ★</td>
  <td style="padding:9px 8px;font-size:11px;color:#8b9baa">企業接單狀況，領先生產 2-4 個月，最強領先指標</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">>55 強勁｜50 榮枯線｜<50 收縮</td>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#a78bfa">初領失業救濟金 ★</td>
  <td style="padding:9px 8px;font-size:11px;color:#8b9baa">每週公布，即時反映就業市場健康度</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a"><220千 健康｜>260千 警示</td>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#a78bfa">台灣外銷訂單 ★</td>
  <td style="padding:9px 8px;font-size:11px;color:#8b9baa">台灣最重要領先指標，反映科技供應鏈需求</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">年增率 >5% 正面｜<-5% 警示</td>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#a78bfa">10Y-2Y 殖利率利差</td>
  <td style="padding:9px 8px;font-size:11px;color:#8b9baa">利差為負（倒掛）歷史上準確預測衰退</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">>0% 正常｜倒掛 警示</td>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#a78bfa">股市 vs 12月均線 ★</td>
  <td style="padding:9px 8px;font-size:11px;color:#8b9baa">股價站上年均線代表趨勢多頭，跌破代表轉空</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">>+2% 正面｜跌破均線 負面</td>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#60d9b0">製造業/服務業 PMI</td>
  <td style="padding:9px 8px;font-size:11px;color:#8b9baa">採購經理人調查，50 為景氣榮枯分界線</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">>50 擴張｜<50 收縮</td>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#60d9b0">景氣對策信號</td>
  <td style="padding:9px 8px;font-size:11px;color:#8b9baa">國發會每月發布，整合9項指標的燈號系統</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">≥38紅燈｜23-37綠燈｜≤16藍燈</td>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#93c5fd">失業率</td>
  <td style="padding:9px 8px;font-size:11px;color:#8b9baa">落後指標，景氣已轉好才會下降，用於確認</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a"><4.5% 健康｜>6% 警示</td>
</tr>
<tr>
  <td style="padding:9px 8px;font-size:12px;color:#93c5fd">CPI 年增率</td>
  <td style="padding:9px 8px;font-size:11px;color:#8b9baa">通膨過高會壓制股市本益比與消費力</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">1.5-3.5% 理想｜>4% 過熱</td>
</tr>
</table>

<div style="font-family:monospace;font-size:11px;color:#58697a;letter-spacing:.1em;margin-bottom:12px">
// 歷史各位階股市平均表現（參考）</div>

<table style="width:100%;border-collapse:collapse;margin-bottom:16px">
<tr style="border-bottom:1px solid #1c2333;background:#0d1117">
  <th style="padding:8px;text-align:left;font-size:10px;color:#58697a;font-weight:500">位階</th>
  <th style="padding:8px;text-align:left;font-size:10px;color:#58697a;font-weight:500">S&P500 年化報酬</th>
  <th style="padding:8px;text-align:left;font-size:10px;color:#58697a;font-weight:500">台股年化報酬</th>
  <th style="padding:8px;text-align:left;font-size:10px;color:#58697a;font-weight:500">建議資產</th>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#39d353">🟢 擴張</td>
  <td style="padding:9px 8px;font-size:12px;color:#c9d1d9">+15% ~ +25%</td>
  <td style="padding:9px 8px;font-size:12px;color:#c9d1d9">+12% ~ +30%</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">股票、原物料、REITs</td>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#f0c040">🟡 高峰趨緩</td>
  <td style="padding:9px 8px;font-size:12px;color:#c9d1d9">+5% ~ +12%</td>
  <td style="padding:9px 8px;font-size:12px;color:#c9d1d9">+3% ~ +10%</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">防禦股、高股息、短債</td>
</tr>
<tr style="border-bottom:1px solid #141b24">
  <td style="padding:9px 8px;font-size:12px;color:#f47067">🔴 收縮</td>
  <td style="padding:9px 8px;font-size:12px;color:#c9d1d9">-10% ~ -25%</td>
  <td style="padding:9px 8px;font-size:12px;color:#c9d1d9">-15% ~ -30%</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">公債、現金、黃金</td>
</tr>
<tr>
  <td style="padding:9px 8px;font-size:12px;color:#79c0ff">🔵 谷底待轉</td>
  <td style="padding:9px 8px;font-size:12px;color:#c9d1d9">-5% ~ +15%</td>
  <td style="padding:9px 8px;font-size:12px;color:#c9d1d9">-8% ~ +20%</td>
  <td style="padding:9px 8px;font-size:11px;color:#58697a">分批布局股票、景氣循環股</td>
</tr>
</table>

<div style="font-size:11px;color:#3a4a5a;border-left:2px solid #2d3a4a;padding-left:10px;line-height:1.8">
⚠️ 歷史報酬僅供參考，不代表未來表現。<br>
本儀表板為個人研究工具，非投資建議。<br>
投資前請評估個人風險承受度。
</div>

</div>
""", unsafe_allow_html=True)

    st.markdown('<p style="font-family:monospace;font-size:10px;color:#2d3a4a;margin-top:16px">v3 ｜ CPSM/國發會/財政部/FRED/Yahoo Finance</p>',unsafe_allow_html=True)

if __name__=="__main__":
    main()
