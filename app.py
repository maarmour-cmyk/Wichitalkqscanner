
import streamlit as st
import pandas as pd
import numpy as np
import requests, re, base64
from bs4 import BeautifulSoup
from datetime import datetime

st.set_page_config(
    page_title="Wichita Yard Profit Scanner",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ---------------- MOBILE CSS ----------------
st.markdown("""
<style>
    .block-container {padding-top: .8rem; padding-bottom: 5rem; max-width: 1000px;}
    h1 {font-size: 1.65rem !important; line-height: 1.15;}
    h2 {font-size: 1.35rem !important;}
    h3 {font-size: 1.15rem !important;}
    div.stButton > button {
        width: 100%;
        min-height: 52px;
        font-size: 1.05rem;
        font-weight: 700;
        border-radius: 12px;
    }
    div[data-testid="stMetric"] {
        border: 1px solid rgba(128,128,128,.25);
        border-radius: 12px;
        padding: .65rem;
    }
    input, textarea {font-size: 16px !important;}
    [data-testid="stDataFrame"] {font-size: .88rem;}
    .yard-card {
        border: 1px solid rgba(128,128,128,.30);
        border-radius: 14px;
        padding: .85rem;
        margin-bottom: .8rem;
    }
    @media (max-width: 700px) {
        .block-container {padding-left: .75rem; padding-right: .75rem;}
        h1 {font-size: 1.45rem !important;}
    }
</style>
""", unsafe_allow_html=True)

YARD_NAME = "Pick Your Part - Wichita"
YARD_ADDRESS = "700 E 21st St N, Wichita, KS 67214"
YARD_PHONE = "800-962-2277"
YARD_LOCATION_PAGE = "https://www.pyp.com/locations/ks/wichita/"
KNOWN_OLD_INVENTORY_PREFIX = "https://www.lkqpickyourpart.com/inventory/wichita-1246/"

PARTS = pd.DataFrame([
    ["LED Headlight Assembly",55,20,35,0.10,0.93,450],
    ["LED Tail Light Assembly",45,15,25,0.08,0.90,240],
    ["OEM Infotainment / Radio",45,20,25,0.12,0.92,350],
    ["Instrument Cluster",35,15,20,0.12,0.88,240],
    ["ECU / ECM / PCM",45,15,15,0.20,0.86,275],
    ["Body Control Module",30,15,12,0.20,0.80,175],
    ["ABS Module / Pump",70,30,25,0.18,0.80,280],
    ["Turbocharger",95,60,40,0.25,0.82,650],
    ["Diesel High Pressure Fuel Pump",70,75,35,0.28,0.78,600],
    ["Diesel Injector Set",90,90,30,0.30,0.75,750],
    ["Power Folding Mirror",40,20,20,0.08,0.92,240],
    ["Camera / ADAS Module",30,15,12,0.18,0.84,300],
    ["Radar Sensor",35,15,12,0.20,0.82,380],
    ["Amplifier",35,20,15,0.12,0.86,260],
    ["OEM Navigation Screen",45,20,20,0.12,0.88,400],
    ["Climate Control Panel",25,10,12,0.08,0.89,150],
    ["Steering Wheel Controls",20,15,12,0.08,0.86,120],
    ["Transfer Case",180,120,90,0.25,0.68,650],
    ["Rear Differential",180,150,110,0.25,0.66,550],
    ["Transmission",300,210,180,0.35,0.62,950],
    ["Engine Assembly",500,300,250,0.40,0.58,1600],
], columns=["part","yard_cost","pull_minutes","shipping","return_risk","demand_score","starter_resale"])

def money(x):
    try:
        if pd.isna(x): return "—"
        return f"${float(x):,.0f}"
    except:
        return "—"

def norm(s):
    return re.sub(r"\s+"," ",str(s).strip().lower())

def decode_vin(vin):
    vin=re.sub(r"[^A-HJ-NPR-Z0-9]","",str(vin).upper())
    if len(vin)!=17: return {"error":"VIN must be exactly 17 characters."}
    u=f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin}?format=json"
    r=requests.get(u,timeout=12)
    r.raise_for_status()
    d=r.json()["Results"][0]
    fields=["VIN","Make","Model","ModelYear","Trim","Series","BodyClass",
            "EngineModel","EngineCylinders","DisplacementL","DriveType",
            "TransmissionStyle","FuelTypePrimary"]
    return {k:d.get(k) for k in fields}

def parse_lkq_vehicle_page(url):
    """Read a public vehicle page; no login/bypass."""
    h={"User-Agent":"Mozilla/5.0 (compatible; WichitaPartsScanner/3.0)"}
    r=requests.get(url,headers=h,timeout=15)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    text=soup.get_text(" ",strip=True)

    result={"source_url":url}
    ymake=re.search(r"\b(19[8-9]\d|20[0-2]\d)\s+([A-Z][A-Za-z0-9\-]+)\s+([A-Za-z0-9\- ]+?)(?:\s+Color:|\s+VIN:)",text,re.I)
    if ymake:
        result["year"]=int(ymake.group(1))
        result["make"]=ymake.group(2).strip()
        result["model"]=ymake.group(3).strip()

    patterns={
        "vin":r"VIN:\s*([A-HJ-NPR-Z0-9]{17})",
        "section":r"Section:\s*([A-Za-z0-9\- ]+?)\s+Row:",
        "yard_row":r"Row:\s*([A-Za-z0-9\-]+)",
        "space":r"Space:\s*([A-Za-z0-9\-]+)",
        "stock_number":r"Stock\s*#:\s*([A-Za-z0-9\-]+)",
        "available_date":r"Available:\s*([0-9/]+)",
        "color":r"Color:\s*([A-Za-z]+)"
    }
    for k,p in patterns.items():
        m=re.search(p,text,re.I)
        if m: result[k]=m.group(1).strip()
    return result

def robust_value(df, col):
    if df is None or len(df)==0 or col not in df.columns: return np.nan,0
    s=pd.to_numeric(df[col],errors="coerce").dropna()
    if not len(s): return np.nan,0
    if len(s)>=4:
        q1,q3=s.quantile([.25,.75]); iqr=q3-q1
        s=s[(s>=q1-1.5*iqr)&(s<=q3+1.5*iqr)]
    return float(s.median()), min(100,25+10*len(s))

def sold_value(sold,v,part,pn=""):
    if sold is None or len(sold)==0: return np.nan,0
    req={"make","model","part","sold_price"}
    if not req.issubset(set(sold.columns)): return np.nan,0
    mask=(
        sold["make"].astype(str).map(norm).eq(norm(v.get("make",""))) &
        sold["model"].astype(str).map(norm).eq(norm(v.get("model",""))) &
        sold["part"].astype(str).map(norm).eq(norm(part))
    )
    if pn and "oem_part_number" in sold.columns:
        exact=sold[mask & sold["oem_part_number"].astype(str).map(norm).eq(norm(pn))]
        if len(exact):
            val,conf=robust_value(exact,"sold_price")
            return val,min(100,conf+15)
    return robust_value(sold[mask],"sold_price")

def score_part(resale,p):
    fee_pct=st.session_state.fee_pct
    travel=st.session_state.travel
    packing=st.session_state.packing
    cost=float(p.yard_cost)
    fee=resale*fee_pct
    net=resale-cost-float(p.shipping)-fee-travel-packing
    hrs=max(float(p.pull_minutes)/60,.10)
    pph=net/hrs
    roi=net/max(cost+travel+packing,1)
    s=(np.clip(net/400*35,0,35)+np.clip(pph/150*25,0,25)+
       np.clip(roi/3*15,0,15)+float(p.demand_score)*15+(1-float(p.return_risk))*10)
    return round(net,2),round(pph,2),round(roi,2),round(float(np.clip(s,0,100)),1)

if "vehicles" not in st.session_state:
    st.session_state.vehicles=pd.DataFrame(columns=[
        "year","make","model","trim","vin","section","yard_row","space",
        "stock_number","available_date","source_url"
    ])
if "sold" not in st.session_state: st.session_state.sold=pd.DataFrame()
if "interchange" not in st.session_state: st.session_state.interchange=pd.DataFrame()
if "part_numbers" not in st.session_state: st.session_state.part_numbers={}
if "fee_pct" not in st.session_state: st.session_state.fee_pct=.13
if "travel" not in st.session_state: st.session_state.travel=10
if "packing" not in st.session_state: st.session_state.packing=5

# ---------------- HEADER ----------------
st.title("🔧 Wichita Yard Profit Scanner")
st.markdown(
    f"""<div class="yard-card">
    <b>{YARD_NAME}</b><br>
    {YARD_ADDRESS}<br>
    ☎ {YARD_PHONE}
    </div>""",
    unsafe_allow_html=True
)

tabs=st.tabs(["🔥 TODAY","➕ ADD CAR","📷 VIN / OEM","🔁 INTERCHANGE","💵 COMPS","⚙️ SETTINGS"])

# ---------------- TODAY ----------------
with tabs[0]:
    st.subheader("Best parts to pull")
    min_profit=st.number_input("Minimum profit",0,3000,100,25,key="today_min_profit")

    if len(st.session_state.vehicles)==0:
        st.info("Add a Wichita yard vehicle first.")
    else:
        rows=[]
        for vi,v in st.session_state.vehicles.iterrows():
            vd=v.to_dict()
            for _,p in PARTS.iterrows():
                key=f"{vi}|{p['part']}"
                pn=st.session_state.part_numbers.get(key,"")
                resale,conf=sold_value(st.session_state.sold,vd,p["part"],pn)
                source="sold history"
                if pd.isna(resale):
                    resale=float(p["starter_resale"])
                    try:
                        y=int(vd.get("year",2015))
                        if y>=2021: resale*=1.25
                        elif y>=2018: resale*=1.12
                        elif y<2012: resale*=.85
                    except: pass
                    conf=25
                    source="starter estimate"

                net,pph,roi,sc=score_part(resale,p)
                if net>=min_profit:
                    rows.append({
                        "vehicle_id":vi,
                        "CAR":f"{vd.get('year','')} {vd.get('make','')} {vd.get('model','')}".strip(),
                        "ROW":vd.get("yard_row",""),
                        "SPACE":vd.get("space",""),
                        "PART":p["part"],
                        "OEM #":pn,
                        "BUY":float(p["yard_cost"]),
                        "SELL":round(resale,0),
                        "PROFIT":round(net,0),
                        "$/HR":round(pph,0),
                        "MIN":int(p["pull_minutes"]),
                        "SCORE":sc,
                        "VALUE":source,
                        "CONF":conf
                    })
        out=pd.DataFrame(rows)
        if len(out):
            out=out.sort_values(["SCORE","PROFIT","$/HR"],ascending=False)
            c1,c2,c3=st.columns(3)
            c1.metric("Best profit",money(out["PROFIT"].max()))
            c2.metric("Best $/hr",money(out["$/HR"].max()))
            c3.metric("Pulls",len(out))

            # Mobile top cards
            for _,r in out.head(8).iterrows():
                st.markdown(
                    f"""<div class="yard-card">
                    <b>{r['CAR']}</b> — Row {r['ROW']} / Space {r['SPACE']}<br>
                    <b>{r['PART']}</b><br>
                    Profit <b>{money(r['PROFIT'])}</b> · {money(r['$/HR'])}/hr · Score {r['SCORE']}<br>
                    Buy {money(r['BUY'])} → Sell {money(r['SELL'])} · {r['MIN']} min
                    </div>""",
                    unsafe_allow_html=True
                )
            with st.expander("Full pull sheet"):
                st.dataframe(out,use_container_width=True,hide_index=True)
            st.download_button("⬇️ SAVE PULL SHEET",out.to_csv(index=False).encode(),
                               "wichita_pull_sheet.csv","text/csv")
        else:
            st.warning("No parts clear your minimum profit.")

# ---------------- ADD CAR ----------------
with tabs[1]:
    st.subheader("Add a Wichita yard car")
    st.caption("Fastest method: paste the public LKQ vehicle page URL.")
    url=st.text_input("LKQ vehicle page URL",placeholder="https://www.lkqpickyourpart.com/inventory/wichita-1246/...")
    if st.button("IMPORT FROM LINK"):
        try:
            d=parse_lkq_vehicle_page(url)
            if "year" not in d:
                st.warning("Could not parse the vehicle page. Use manual entry below.")
            else:
                for c in st.session_state.vehicles.columns:
                    d.setdefault(c,"")
                st.session_state.vehicles=pd.concat(
                    [st.session_state.vehicles,pd.DataFrame([d])[st.session_state.vehicles.columns]],
                    ignore_index=True
                )
                st.success(f"Added {d.get('year')} {d.get('make')} {d.get('model')} — Row {d.get('yard_row','?')}, Space {d.get('space','?')}")
        except Exception as e:
            st.error(f"Could not read public page: {e}")

    st.markdown("### Manual quick add")
    a,b=st.columns(2)
    year=a.number_input("Year",1980,2030,2015)
    make=b.text_input("Make")
    model=a.text_input("Model")
    trim=b.text_input("Trim")
    vin=a.text_input("VIN",max_chars=17)
    row=b.text_input("Row")
    space=a.text_input("Space")
    section=b.text_input("Section",value="CAR")
    if st.button("ADD CAR"):
        d={"year":year,"make":make,"model":model,"trim":trim,"vin":vin,
           "section":section,"yard_row":row,"space":space,"stock_number":"",
           "available_date":"","source_url":url}
        st.session_state.vehicles=pd.concat([st.session_state.vehicles,pd.DataFrame([d])],ignore_index=True)
        st.success("Added.")

    if len(st.session_state.vehicles):
        st.dataframe(st.session_state.vehicles,use_container_width=True,hide_index=True)

# ---------------- VIN / OEM ----------------
with tabs[2]:
    st.subheader("VIN + OEM number")
    vin=st.text_input("Scan/type VIN",key="decodevin",max_chars=17)
    if st.button("DECODE VIN"):
        try:
            d=decode_vin(vin)
            st.error(d["error"]) if "error" in d else st.json(d)
        except Exception as e:
            st.error(str(e))

    st.markdown("### Save OEM number to a pull")
    if len(st.session_state.vehicles):
        vi=st.selectbox(
            "Car",
            list(st.session_state.vehicles.index),
            format_func=lambda i:f"{st.session_state.vehicles.loc[i,'year']} {st.session_state.vehicles.loc[i,'make']} {st.session_state.vehicles.loc[i,'model']} — Row {st.session_state.vehicles.loc[i,'yard_row']}"
        )
        part=st.selectbox("Part",PARTS["part"].tolist())
        pn=st.text_input("OEM part number",placeholder="Read/scan the number printed on the part")
        if st.button("SAVE OEM NUMBER"):
            st.session_state.part_numbers[f"{vi}|{part}"]=pn.strip()
            st.success("Saved.")

# ---------------- INTERCHANGE ----------------
with tabs[3]:
    st.subheader("Interchange lookup")
    up=st.file_uploader("Interchange CSV",type=["csv"],key="interchange_up")
    if up:
        st.session_state.interchange=pd.read_csv(up)
        st.success(f"Loaded {len(st.session_state.interchange)} rows.")
    pn=st.text_input("OEM number to match",key="inter_pn")
    if pn and len(st.session_state.interchange):
        col=next((c for c in ["oem_part_number","part_number","oe_number"] if c in st.session_state.interchange.columns),None)
        if col:
            m=st.session_state.interchange[
                st.session_state.interchange[col].astype(str).map(norm).eq(norm(pn))
            ]
            if len(m): st.dataframe(m,use_container_width=True,hide_index=True)
            else: st.info("No exact interchange match in your file.")

# ---------------- COMPS ----------------
with tabs[4]:
    st.subheader("Sold comps")
    up=st.file_uploader("Upload your sold-history CSV",type=["csv"],key="sold_up")
    if up:
        st.session_state.sold=pd.read_csv(up)
        st.success(f"Loaded {len(st.session_state.sold)} sold records.")
    if len(st.session_state.sold):
        st.dataframe(st.session_state.sold,use_container_width=True,hide_index=True)
    st.caption("Sold-history prices override starter estimates when the make/model/part match; exact OEM-number matches get extra confidence.")

# ---------------- SETTINGS ----------------
with tabs[5]:
    st.subheader("Settings")
    st.session_state.fee_pct=st.slider("Selling platform fee",0.0,.25,st.session_state.fee_pct,.01)
    st.session_state.travel=st.number_input("Travel allocated per part",0,100,st.session_state.travel,5)
    st.session_state.packing=st.number_input("Packing/materials",0,100,st.session_state.packing,1)
    st.markdown("### Wichita-only")
    st.write(YARD_NAME)
    st.write(YARD_ADDRESS)
    st.write(YARD_PHONE)
    st.link_button("OPEN WICHITA YARD PAGE",YARD_LOCATION_PAGE)
    st.caption("This build intentionally contains no yard selector.")

st.markdown("---")
st.caption("Use only on lawfully purchased salvage-yard parts. Verify condition, OEM number, interchange, programming requirements, core/guarantee charges, taxes, and platform restrictions before buying or listing.")
