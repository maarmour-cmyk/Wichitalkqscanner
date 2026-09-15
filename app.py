
import streamlit as st
import pandas as pd
import numpy as np
import requests, re, time, base64, json, uuid, io, hashlib, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
from PIL import Image

st.set_page_config(
    page_title="Wichita Parts Profit Scanner V7.2",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.block-container{padding-top:.55rem;padding-bottom:5rem;max-width:1180px}
h1{font-size:1.48rem!important;line-height:1.08}
h2{font-size:1.2rem!important}
h3{font-size:1.04rem!important}
div.stButton>button{width:100%;min-height:50px;font-size:1rem;font-weight:750;border-radius:12px}
div[data-testid="stMetric"]{border:1px solid rgba(128,128,128,.25);border-radius:12px;padding:.52rem}
input,textarea{font-size:16px!important}
.card{border:1px solid rgba(128,128,128,.30);border-radius:14px;padding:.78rem;margin:.55rem 0}
.partrow{padding:.42rem 0;border-top:1px solid rgba(128,128,128,.15)}
.small{opacity:.76;font-size:.86rem}
.alert{border:1px solid rgba(255,180,0,.55);border-radius:12px;padding:.65rem;margin:.4rem 0}
.good{font-weight:800}
@media(max-width:700px){
.block-container{padding-left:.62rem;padding-right:.62rem}
h1{font-size:1.28rem!important}
}
</style>
""", unsafe_allow_html=True)

YARD_NAME="Pick Your Part - Wichita"
YARD_ADDRESS="700 E 21st St N, Wichita, KS 67214"
INV_URL="https://www.pyp.com/inventory/wichita-1246/"
PRICE_URL="https://www.pyp.com/locations/LKQ_Pick_Your_Part_-_Wichita-246/prices/"

# ---------------- PART CATALOG ----------------
# Part, fallback LKQ cost, pull minutes, return risk, base demand, fallback resale,
# earliest year, applicability, weight lb, L/W/H in, common compatibility warnings
PARTS = pd.DataFrame([
["LED Headlight Assembly",55,20,.10,.94,450,2014,"all",12,28,16,12,"Verify left/right, LED vs halogen/HID, adaptive/AFS, ballast/module, tabs."],
["LED Tail Light Assembly",45,15,.08,.91,240,2012,"all",7,22,14,10,"Verify left/right, LED vs incandescent, body style and mounting tabs."],
["OEM Infotainment / Radio",45,20,.12,.93,350,2007,"all",6,13,10,8,"May require VIN programming/unlock. Verify screen size, audio package and connectors."],
["Instrument Cluster",35,15,.12,.89,240,2000,"all",4,16,9,8,"Mileage/VIN programming may be required. Match engine, trim and display type."],
["ECU / ECM / PCM",45,15,.20,.86,275,1996,"all",4,10,8,5,"Often requires VIN/immobilizer programming. Match exact OEM number/calibration."],
["Body Control Module",30,15,.20,.81,175,2000,"all",3,9,7,4,"Programming/configuration often required. Match exact OEM number."],
["ABS Module / Pump",70,30,.18,.82,280,2000,"all",10,13,10,8,"Match pump/module number, traction/stability options. Bleeding/programming may be required."],
["Power Folding Mirror",40,20,.08,.93,240,2005,"all",8,18,14,12,"Verify side, heat, blind spot, camera, memory, puddle light and power fold."],
["Camera / ADAS Module",30,15,.18,.86,300,2014,"newer",2,8,6,5,"Calibration/programming may be required. Verify camera location/options."],
["Radar Sensor",35,15,.20,.84,380,2015,"newer",2,8,6,5,"Calibration usually required. Verify bracket and exact OEM number."],
["Amplifier",28.5,20,.12,.88,260,2003,"all",5,12,9,6,"Match premium audio package (B&O/Bose/JBL/etc.) and exact OEM number."],
["OEM Navigation Screen",45,20,.12,.90,400,2007,"all",5,14,10,7,"Verify screen size, touch/non-touch, trim and connector layout."],
["Climate Control Panel",25,10,.08,.90,150,2000,"all",2,11,7,5,"Match dual/single zone, heated/cooled seats and trim."],
["Steering Wheel Controls",20,15,.08,.87,120,2004,"all",2,10,8,5,"Verify cruise/audio/heat options and connector."],
["Alternator",40.5,25,.12,.84,130,1990,"all",15,12,10,10,"Match amperage, pulley and engine."],
["Starter",38,35,.14,.80,125,1990,"all",12,12,9,8,"Match engine/transmission and mounting pattern."],
["A/C Compressor",64,50,.17,.78,180,1990,"all",18,14,12,12,"Check clutch/pulley and oil contamination. Match engine and connector."],
["Turbocharger",95,75,.25,.83,650,2000,"turbo_likely",25,18,16,14,"Check shaft play/oil, actuator and housing damage. Exact engine/turbo code matters."],
["Diesel High Pressure Fuel Pump",70,90,.29,.79,600,2003,"diesel_likely",18,16,12,10,"Contamination risk. Match engine/pump generation and inspect for metal."],
["Diesel Injector Set",90,100,.31,.76,750,2003,"diesel_likely",10,14,10,8,"High return risk if untested. Match injector code/generation."],
["Transfer Case",180,120,.25,.70,650,1990,"4x4_likely",90,28,24,22,"Match transfer-case tag, ratio, electronic/manual shift and transmission."],
["Rear Differential",202,150,.25,.68,550,1990,"truck_suv",120,36,24,24,"Verify ratio, axle type, locker/LSD and spline count."],
["Transmission",300,210,.35,.63,950,1990,"all",190,40,30,28,"High freight/return risk. Match transmission code, drivetrain and engine."],
["Tailgate / Liftgate",95,55,.12,.72,500,1995,"truck_suv",70,60,30,12,"Verify camera, handle, power release, step and trim. Local pickup preferred."],
], columns=[
"part","yard_cost","pull_minutes","return_risk","demand_score","starter_resale",
"min_year","rule","weight","length","width","height","warnings"
])

LKQ_MAP={
"A/C Compressor":"A/C COMPRESSOR",
"Amplifier":"AMPLIFIER",
"Alternator":"ALTERNATOR",
"Starter":"STARTER",
"ABS Module / Pump":"ANTI LOCK BRAKE (ABS UNIT)",
"Transmission":"TRANSMISSION",
"Transfer Case":"TRANSFER CASE",
"Rear Differential":"REAR AXLE ASSEMBLY",
}

TRUCK_SUV_WORDS=[
"F-150","F150","F-250","F250","F-350","F350","SILVERADO","SIERRA","RAM",
"TAHOE","SUBURBAN","YUKON","EXPEDITION","EXPLORER","4RUNNER","TACOMA","TUNDRA",
"WRANGLER","GRAND CHEROKEE","CHEROKEE","DURANGO","ESCALADE","NAVIGATOR",
"COLORADO","CANYON","FRONTIER","TITAN","RIDGELINE","TRAVERSE","PILOT","HIGHLANDER"
]
DIESEL_HINTS=["F-250","F250","F-350","F350","SUPER DUTY","2500","3500","TDI","DIESEL","SPRINTER","DURAMAX","CUMMINS"]
TURBO_HINTS=["TDI","ECOBOOST","TURBO","GTI","GLI","WRX","335","340","535","540","A3","A4","A5","A6","Q5","Q7","SONATA","OPTIMA","VELOSTER","CRUZE","REGAL"]

# ---------------- HELPERS ----------------
def norm(x): return re.sub(r"\s+"," ",str(x).strip().lower())
def money(x):
    try:
        if pd.isna(x): return "—"
        return f"${float(x):,.0f}"
    except: return "—"

def safe_float(x,default=0.0):
    try:
        if pd.isna(x): return default
        return float(x)
    except: return default

def today_iso(): return date.today().isoformat()

def http_session():
    s=requests.Session()
    s.headers.update({
        "User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
        "Accept-Language":"en-US,en;q=0.9",
    })
    return s

def row_num(x):
    m=re.search(r"\d+",str(x))
    return int(m.group()) if m else 9999

def unique_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"

# ---------------- WICHITA INVENTORY ----------------
def parse_inventory_html(html,source_url):
    soup=BeautifulSoup(html,"html.parser")
    text=soup.get_text(" ",strip=True).replace("\xa0"," ")
    starts=list(re.finditer(
        r"\b((?:19[8-9]\d|20[0-2]\d))\s+([A-Z][A-Z0-9\-]+)\s+"
        r"([A-Z0-9][A-Z0-9&\-/ ]{0,45}?)(?=\s+(?:[A-Z][a-z]+)\s*[·•]?\s*1246-\d+|\s+Color:)",
        text,re.I
    ))
    rows=[]
    for i,m in enumerate(starts):
        chunk=text[m.start():(starts[i+1].start() if i+1<len(starts) else min(len(text),m.start()+1700))]
        stock=re.search(r"(1246-\d+)",chunk)
        vin=re.search(r"\bVIN:?\s*([A-HJ-NPR-Z0-9]{17})\b",chunk,re.I)
        if not stock and not vin: continue
        sec=re.search(r"\bSection:?\s*([A-Za-z]+)",chunk,re.I)
        rr=re.search(r"\bRow:?\s*([A-Za-z0-9\-]+)",chunk,re.I)
        sp=re.search(r"\bSpace:?\s*([A-Za-z0-9\-]+)",chunk,re.I)
        av=re.search(r"\bAvailable:?\s*(\d{1,2}/\d{1,2}/\d{4})",chunk,re.I)
        color=""
        cm=re.search(r"\b(?:Color:?\s*)([A-Za-z]+)",chunk,re.I)
        if cm: color=cm.group(1)
        rows.append({
            "year":int(m.group(1)),
            "make":m.group(2).strip().upper(),
            "model":re.sub(r"\s+"," ",m.group(3)).strip(" -").upper(),
            "color":color,
            "stock_number":stock.group(1) if stock else "",
            "vin":vin.group(1).upper() if vin else "",
            "section":sec.group(1).upper() if sec else "",
            "yard_row":rr.group(1) if rr else "",
            "space":sp.group(1) if sp else "",
            "available_date":av.group(1) if av else "",
            "source_url":source_url,
        })
    return pd.DataFrame(rows)

def sync_inventory(max_pages=75):
    s=http_session(); rows=[]; seen=set(); pages=0; empty=0
    for page in range(1,max_pages+1):
        url=INV_URL if page==1 else f"{INV_URL}?page={page}"
        r=s.get(url,timeout=20); r.raise_for_status()
        df=parse_inventory_html(r.text,url)
        if len(df)==0:
            empty+=1
            if empty>=2: break
            continue
        empty=0; new=0
        for _,x in df.iterrows():
            key=x["stock_number"] or x["vin"]
            if key and key not in seen:
                seen.add(key); rows.append(x.to_dict()); new+=1
        pages=page
        if new==0: break
        time.sleep(.08)
    return pd.DataFrame(rows),pages

def parse_price_list():
    r=http_session().get(PRICE_URL,timeout=20); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    prices={}
    for tr in soup.find_all("tr"):
        cells=[c.get_text(" ",strip=True) for c in tr.find_all(["th","td"])]
        if len(cells)<3: continue
        name=cells[0].strip().upper()
        vals=[]
        for c in cells[1:]:
            m=re.search(r"\$([\d,]+(?:\.\d+)?)",c)
            vals.append(float(m.group(1).replace(",","")) if m else np.nan)
        if name and any(pd.notna(v) for v in vals):
            prices[name]={
                "total":vals[0] if len(vals)>0 else np.nan,
                "price":vals[1] if len(vals)>1 else np.nan,
                "core":vals[2] if len(vals)>2 else np.nan,
                "guarantee":vals[3] if len(vals)>3 else np.nan,
            }
    return prices

# ---------------- VEHICLE / PART INTELLIGENCE ----------------
def applicability(vehicle,p):
    y=int(vehicle.get("year") or 0)
    if y<int(p.min_year): return 0.0
    text=f"{vehicle.get('make','')} {vehicle.get('model','')}".upper()
    rule=p.rule
    if rule=="all": return 1.0
    if rule=="newer": return 1.0 if y>=2015 else .15
    if rule=="truck_suv": return 1.0 if any(k in text for k in TRUCK_SUV_WORDS) else .12
    if rule=="4x4_likely": return 1.0 if any(k in text for k in TRUCK_SUV_WORDS) else .08
    if rule=="diesel_likely": return .95 if any(k in text for k in DIESEL_HINTS) else .02
    if rule=="turbo_likely": return .90 if any(k in text for k in TURBO_HINTS) else .10
    return .5

def impact_penalty(stock,part):
    data=st.session_state.impact_map.get(str(stock),{})
    loc=data.get("location","None")
    sev=data.get("severity","None")
    sev_mult={"None":0.0,"Light":.08,"Moderate":.20,"Heavy":.38}.get(sev,0)
    part=norm(part)
    loc_parts={
        "Front":["headlight","radar","camera","a/c compressor","engine","alternator","turbo"],
        "Rear":["tail light","tailgate","rear differential"],
        "Left":["mirror","headlight","tail light","wheel"],
        "Right":["mirror","headlight","tail light","wheel"],
        "Flood":["ecu","ecm","pcm","module","radio","infotainment","cluster","amplifier","screen","camera","radar","control"],
        "Fire":["engine","transmission","module","radio","headlight","tail light","harness","alternator","starter"],
    }
    if loc in ["Flood","Fire"]:
        return min(.65,sev_mult+.25)
    if any(k in part for k in loc_parts.get(loc,[])):
        return sev_mult
    return 0.0

def condition_penalty(stock,part):
    key=f"{stock}|{part}"
    data=st.session_state.condition_map.get(key,{})
    penalty=0.0
    if data.get("broken_tabs"): penalty+=.14
    if data.get("cracked"): penalty+=.22
    if data.get("corrosion"): penalty+=.16
    if data.get("cut_wires"): penalty+=.14
    if data.get("water_damage"): penalty+=.30
    if data.get("untested"): penalty+=.08
    return min(.65,penalty)

def get_yard_cost(p):
    live=st.session_state.live_prices
    name=LKQ_MAP.get(p.part)
    if name and name in live and pd.notna(live[name].get("price")):
        return float(live[name]["price"])
    return float(p.yard_cost)

def part_number(stock,part):
    return st.session_state.part_numbers.get(f"{stock}|{part}","")

def interchange_matches(pn):
    df=st.session_state.interchange
    if not pn or df is None or len(df)==0: return pd.DataFrame()
    col=next((c for c in ["oem_part_number","part_number","oe_number"] if c in df.columns),None)
    if not col: return pd.DataFrame()
    return df[df[col].astype(str).map(norm).eq(norm(pn))]

def fitment_bonus(stock,part):
    pn=part_number(stock,part)
    n=len(interchange_matches(pn))
    return min(.12,n*.015),n

# ---------------- SHIPPING / CHANNELS ----------------
def shipping_estimate(weight,L,W,H):
    weight=max(float(weight),1)
    dim=max(float(L)*float(W)*float(H)/139.0,weight)
    billed=max(weight,dim)
    if billed<=2: cost=8.5+1.5*billed
    elif billed<=10: cost=10+1.2*billed
    elif billed<=25: cost=15+1.0*billed
    elif billed<=50: cost=22+.95*billed
    elif billed<=70: cost=30+.95*billed
    elif billed<=150: cost=55+1.15*billed
    else: cost=225+0.55*billed
    return round(cost,2),round(billed,1)

def selling_channels(market,p):
    ship,billed=shipping_estimate(p.weight,p.length,p.width,p.height)
    ebay_quick=market*st.session_state.ebay_quick_pct
    ebay_net=ebay_quick - get_yard_cost(p) - ebay_quick*st.session_state.ebay_fee - ship - st.session_state.packing - st.session_state.travel

    local_price=market*st.session_state.local_price_pct
    local_net=local_price - get_yard_cost(p) - local_price*st.session_state.local_fee - st.session_state.travel
    return {
        "ebay_price":ebay_quick,"ebay_ship":ship,"ebay_profit":ebay_net,
        "local_price":local_price,"local_profit":local_net,
        "best_channel":"LOCAL" if local_net>ebay_net else "EBAY",
        "best_profit":max(local_net,ebay_net),
        "billed_weight":billed,
    }

# ---------------- EBAY LIVE COMPS ----------------
def ebay_credentials():
    cid=secret=""
    try:
        cid=st.secrets.get("EBAY_CLIENT_ID","")
        secret=st.secrets.get("EBAY_CLIENT_SECRET","")
    except: pass
    if not cid: cid=st.session_state.ebay_client_id
    if not secret: secret=st.session_state.ebay_client_secret
    return cid,secret

def ebay_access_token(cid,secret):
    auth=base64.b64encode(f"{cid}:{secret}".encode()).decode()
    r=requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Authorization":f"Basic {auth}","Content-Type":"application/x-www-form-urlencoded"},
        data={"grant_type":"client_credentials","scope":"https://api.ebay.com/oauth/api_scope"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["access_token"]

def comp_query(vehicle,part,pn=""):
    term={
        "LED Headlight Assembly":"OEM headlight assembly",
        "LED Tail Light Assembly":"OEM tail light",
        "OEM Infotainment / Radio":"OEM radio infotainment",
        "Instrument Cluster":"OEM instrument cluster",
        "ECU / ECM / PCM":"OEM ECM ECU PCM",
        "Body Control Module":"OEM body control module BCM",
        "ABS Module / Pump":"OEM ABS pump module",
        "Power Folding Mirror":"OEM power mirror",
        "Camera / ADAS Module":"OEM camera module",
        "Radar Sensor":"OEM radar sensor",
        "Amplifier":"OEM amplifier",
        "OEM Navigation Screen":"OEM navigation display screen",
        "Climate Control Panel":"OEM climate control panel",
        "Steering Wheel Controls":"OEM steering wheel controls",
        "A/C Compressor":"OEM AC compressor",
        "Tailgate / Liftgate":"OEM tailgate liftgate",
    }.get(part,part)
    pnterm=f" {pn}" if pn else ""
    return f"{vehicle.get('year')} {vehicle.get('make')} {vehicle.get('model')} {term}{pnterm} used"

def ebay_used_comps(query,cid,secret,limit=40):
    token=ebay_access_token(cid,secret)
    r=requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={"Authorization":f"Bearer {token}","X-EBAY-C-MARKETPLACE-ID":"EBAY_US"},
        params={"q":query,"limit":min(limit,50),"filter":"conditions:{USED}"},
        timeout=22,
    )
    r.raise_for_status()
    rows=[]
    for x in r.json().get("itemSummaries",[]):
        try: price=float((x.get("price") or {}).get("value"))
        except: continue
        ship=0.0
        opts=x.get("shippingOptions") or []
        if opts:
            try: ship=float((opts[0].get("shippingCost") or {}).get("value") or 0)
            except: pass
        rows.append({
            "title":x.get("title",""),"price":price,"shipping":ship,
            "landed":price+ship,"condition":x.get("condition",""),
            "url":x.get("itemWebUrl",""),
        })
    return pd.DataFrame(rows)

def ebay_used_comps_with_token(query,token,limit=40):
    """Same Browse API lookup, but reuses one OAuth token for the whole batch."""
    r=requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={"Authorization":f"Bearer {token}","X-EBAY-C-MARKETPLACE-ID":"EBAY_US"},
        params={"q":query,"limit":min(limit,50),"filter":"conditions:{USED}"},
        timeout=18,
    )
    r.raise_for_status()
    rows=[]
    for x in r.json().get("itemSummaries",[]):
        try:
            price=float((x.get("price") or {}).get("value"))
        except Exception:
            continue
        ship=0.0
        opts=x.get("shippingOptions") or []
        if opts:
            try:
                ship=float((opts[0].get("shippingCost") or {}).get("value") or 0)
            except Exception:
                pass
        rows.append({
            "title":x.get("title",""),"price":price,"shipping":ship,
            "landed":price+ship,"condition":x.get("condition",""),
            "url":x.get("itemWebUrl",""),
        })
    return pd.DataFrame(rows)

# ---------------- GOOGLE SHOPPING / MULTI-SOURCE COMPS ----------------
def serpapi_key():
    key=""
    try:
        key=st.secrets.get("SERPAPI_KEY","")
    except Exception:
        pass
    return key or st.session_state.get("serpapi_api_key","")

def serpapi_shopping_comps(query,key,limit=35):
    """Google Shopping via SerpAPI. Used as an asking-price reference."""
    r=requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine":"google_shopping","q":query,
            "location":"Wichita, Kansas, United States",
            "gl":"us","hl":"en","api_key":key,
        },
        timeout=25,
    )
    r.raise_for_status()
    rows=[]
    for x in r.json().get("shopping_results",[])[:limit]:
        price=x.get("extracted_price")
        if price is None:
            m=re.search(r"\$([\d,]+(?:\.\d+)?)",str(x.get("price","")))
            price=float(m.group(1).replace(",","")) if m else None
        if price is None: continue
        title=str(x.get("title",""))
        cond=str(x.get("second_hand_condition","") or "")
        blob=(title+" "+cond).lower()
        # Query includes used; reject obvious brand-new-only results.
        if any(w in blob for w in ["brand new","new sealed","new oem"]) and not any(w in blob for w in ["used","pre-owned","preowned","second hand"]):
            continue
        rows.append({
            "title":title,"price":float(price),"shipping":0.0,
            "landed":float(price),"condition":cond or "shopping result",
            "url":x.get("product_link","") or x.get("link",""),
            "source":x.get("source","Google Shopping"),
        })
    return pd.DataFrame(rows)

def provider_status():
    cid,secret=ebay_credentials()
    return {"eBay":bool(cid and secret),"Google Shopping":bool(serpapi_key())}

def combine_source_medians(source_rows):
    """Confidence-weighted blend of independent market sources."""
    vals=[]
    for row in source_rows:
        med=row.get("median",np.nan)
        if pd.isna(med): continue
        n=max(int(row.get("n",0) or 0),1)
        base=float(row.get("weight",1.0))
        # More observations help, but cap their influence.
        weight=base*min(2.0,0.75+n/12.0)
        vals.append((float(med),weight))
    if not vals: return np.nan
    arr=np.array([v for v,w in vals],dtype=float)
    w=np.array([w for v,w in vals],dtype=float)
    # Drop source medians wildly far from the cross-source median when >=3 sources.
    if len(arr)>=3:
        center=float(np.median(arr))
        keep=(arr>=center*.45)&(arr<=center*1.85)
        arr,w=arr[keep],w[keep]
    return float(np.average(arr,weights=w)) if len(arr) else np.nan

def summarize_comps(df):
    if df is None or len(df)<2: return {"p25":np.nan,"median":np.nan,"p75":np.nan,"n":0}
    s=pd.to_numeric(df["landed"],errors="coerce").dropna()
    s=s[(s>=12)&(s<=8000)]
    if len(s)>=5:
        q1,q3=s.quantile([.25,.75]); iqr=q3-q1
        s=s[(s>=q1-1.5*iqr)&(s<=q3+1.5*iqr)]
    if len(s)<2: return {"p25":np.nan,"median":np.nan,"p75":np.nan,"n":len(s)}
    return {"p25":float(s.quantile(.25)),"median":float(s.median()),"p75":float(s.quantile(.75)),"n":len(s)}

# ---------------- SOLD DATA / SELL-THROUGH ----------------
def sold_matches(vehicle,part,pn=""):
    df=st.session_state.sold_comps
    if df is None or len(df)==0 or "sold_price" not in df.columns: return pd.DataFrame()
    x=df.copy()
    req={"part","make","model"}
    if not req.issubset(x.columns): return pd.DataFrame()
    mask=(
        x["part"].astype(str).map(norm).eq(norm(part)) &
        x["make"].astype(str).map(norm).eq(norm(vehicle.get("make",""))) &
        x["model"].astype(str).map(norm).eq(norm(vehicle.get("model","")))
    )
    if pn and "oem_part_number" in x.columns:
        exact=x[mask & x["oem_part_number"].astype(str).map(norm).eq(norm(pn))]
        if len(exact): return exact
    return x[mask]

def sold_market_value(vehicle,part,pn=""):
    x=sold_matches(vehicle,part,pn)
    if len(x)<2: return np.nan,0
    s=pd.to_numeric(x["sold_price"],errors="coerce").dropna()
    if len(s)>=4:
        q1,q3=s.quantile([.25,.75]); iqr=q3-q1
        s=s[(s>=q1-1.5*iqr)&(s<=q3+1.5*iqr)]
    return (float(s.median()),len(s)) if len(s) else (np.nan,0)

def sell_through(vehicle,part,pn,active_count,base_demand):
    x=sold_matches(vehicle,part,pn)
    if len(x)>=2 and "sold_date" in x.columns and active_count>0:
        dates=pd.to_datetime(x["sold_date"],errors="coerce")
        cutoff=pd.Timestamp.today().normalize()-pd.Timedelta(days=90)
        sold90=int((dates>=cutoff).sum())
        monthly=sold90/3
        ratio=monthly/max(active_count,1)
        pct=min(300,ratio*100)
        days=int(np.clip(30/max(ratio,.08),5,365))
        return pct,days,"sold-comps + active supply"
    # clearly labeled heuristic if no completed-sales feed
    scarcity=1.0
    if active_count>0:
        scarcity=float(np.clip(30/active_count,.55,1.35))
    speed=float(base_demand)*scarcity
    days=int(np.clip(90-(speed*65),12,150))
    pct=float(np.clip(speed*55,10,95))
    return pct,days,"demand estimate"

# ---------------- MARKET VALUE ----------------
def market_key(vehicle,part,pn=""):
    return f"{vehicle.get('year')}|{vehicle.get('make')}|{vehicle.get('model')}|{part}|{pn}"

def fallback_market(vehicle,p):
    val=float(p.starter_resale)
    y=int(vehicle.get("year") or 2010)
    if y>=2021: val*=1.28
    elif y>=2018: val*=1.15
    elif y>=2014: val*=1.05
    elif y<2008: val*=.76
    mult={"bmw":1.24,"mercedes-benz":1.28,"audi":1.22,"lexus":1.17,
          "cadillac":1.13,"lincoln":1.09,"ram":1.07,"gmc":1.06,
          "toyota":1.07,"honda":1.04}
    return val*mult.get(norm(vehicle.get("make","")),1.0)

def part_market(vehicle,p):
    stock=str(vehicle.get("stock_number",""))
    pn=part_number(stock,p.part)
    sold_val,sold_n=sold_market_value(vehicle,p.part,pn)
    key=market_key(vehicle,p.part,pn)
    live=st.session_state.market_cache.get(key,{})

    sources=[]
    source_labels=[]
    active=0

    if pd.notna(sold_val):
        sources.append({"name":"sold comps","median":float(sold_val),"n":sold_n,"weight":1.35})
        source_labels.append(f"sold comps ({sold_n})")

    ebay=live.get("ebay",{}) if isinstance(live,dict) else {}
    if pd.notna(ebay.get("median",np.nan)):
        sources.append({"name":"eBay","median":float(ebay["median"]),"n":int(ebay.get("n",0)),"weight":1.0})
        source_labels.append(f"eBay USED ({ebay.get('n',0)})")
        active+=int(ebay.get("n",0) or 0)

    shop=live.get("shopping",{}) if isinstance(live,dict) else {}
    if pd.notna(shop.get("median",np.nan)):
        sources.append({"name":"Google Shopping","median":float(shop["median"]),"n":int(shop.get("n",0)),"weight":.82})
        source_labels.append(f"Google Shopping ({shop.get('n',0)})")
        active+=int(shop.get("n",0) or 0)

    market=combine_source_medians(sources)
    has_market_data=pd.notna(market)
    if not has_market_data:
        market=fallback_market(vehicle,p)
        source="NO MARKET DATA"
        confidence=8
    else:
        source=" + ".join(source_labels)
        source_count=len(sources)
        obs=sum(int(x.get("n",0) or 0) for x in sources)
        confidence=min(96,35+source_count*16+min(obs,25))

    st_pct,days,st_source=sell_through(vehicle,p.part,pn,active,float(p.demand_score))
    fit_bonus,fit_count=fitment_bonus(stock,p.part)

    impact=impact_penalty(stock,p.part)
    cond=condition_penalty(stock,p.part)
    risk_discount=min(.75,impact+cond)
    adjusted_market=float(market)*(1-risk_discount)

    return {
        "market":float(market),"adjusted_market":adjusted_market,"source":source,
        "confidence":confidence,"sell_through":st_pct,"days_to_sell":days,
        "sell_source":st_source,"active_count":active,
        "fitment_count":fit_count,"fit_bonus":fit_bonus,
        "risk_discount":risk_discount,"pn":pn,"has_market_data":bool(has_market_data),
        "market_sources":len(sources),
    }

# ---------------- SCORING ----------------
def opportunity(vehicle,p,allow_unpriced=False):
    app=applicability(vehicle,p)
    if app<.10: return None
    m=part_market(vehicle,p)
    if st.session_state.market_only and not m["has_market_data"] and not allow_unpriced:
        return None
    channels=selling_channels(m["adjusted_market"],p)
    best_profit=channels["best_profit"]

    avoid = norm(p.part) in {norm(x) for x in st.session_state.avoid_parts}
    learned = learned_part_warning(p.part)
    if avoid: best_profit-=250

    pph=best_profit/max(float(p.pull_minutes)/60,.1)
    roi=best_profit/max(get_yard_cost(p)+st.session_state.travel,1)
    liquidity=float(np.clip((100-m["days_to_sell"])/100,0,1))
    score=(
        np.clip(best_profit/350*30,0,30)+
        np.clip(pph/150*20,0,20)+
        np.clip(roi/3*12,0,12)+
        float(p.demand_score)*10+
        liquidity*10+
        (m["confidence"]/100)*8+
        app*5+
        m["fit_bonus"]*20
    )
    score-=m["risk_discount"]*30
    if learned: score-=8
    if avoid: score-=25

    channel=channels["best_channel"]
    target=channels["local_price"] if channel=="LOCAL" else channels["ebay_price"]
    ship=0 if channel=="LOCAL" else channels["ebay_ship"]

    return {
        "PART":p.part,"OEM":m["pn"],"BUY":round(get_yard_cost(p),2),
        "MARKET":round(m["market"],2),"TARGET":round(target,2),
        "SHIP":round(ship,2),"PROFIT":round(best_profit,2),
        "$/HR":round(pph,2),"ROI":round(roi,2),"MIN":int(p.pull_minutes),
        "SCORE":round(float(np.clip(score,0,100)),1),"CHANNEL":channel,
        "SELL_THROUGH":round(m["sell_through"],1),"DAYS":m["days_to_sell"],
        "SELL_SOURCE":m["sell_source"],"VALUE_SOURCE":m["source"],
        "CONF":m["confidence"],"FITMENTS":m["fitment_count"],
        "RISK_DISC":round(m["risk_discount"]*100,0),
        "WARNING":p.warnings,"LEARNED_WARNING":learned,
        "MARKET_DATA":m["has_market_data"],"MARKET_SOURCES":m["market_sources"],
    }

def all_opportunities(inv,allow_unpriced=False):
    rows=[]
    expected_cols=[
        "vehicle_id","CAR","STOCK","VIN","SECTION","ROW","SPACE","AVAILABLE","NEW",
        "PART","OEM","BUY","MARKET","TARGET","SHIP","PROFIT","$/HR","ROI","MIN",
        "SCORE","CHANNEL","SELL_THROUGH","DAYS","SELL_SOURCE","VALUE_SOURCE",
        "CONF","FITMENTS","RISK_DISC","WARNING","LEARNED_WARNING",
        "MARKET_DATA","MARKET_SOURCES"
    ]
    for idx,v in inv.iterrows():
        vd=v.to_dict()
        for _,p in PARTS.iterrows():
            o=opportunity(vd,p,allow_unpriced=allow_unpriced)
            if not o:
                continue
            rows.append({
                "vehicle_id":idx,
                "CAR":f"{vd.get('year')} {vd.get('make')} {vd.get('model')}",
                "STOCK":vd.get("stock_number",""),"VIN":vd.get("vin",""),
                "SECTION":vd.get("section",""),"ROW":vd.get("yard_row",""),
                "SPACE":vd.get("space",""),"AVAILABLE":vd.get("available_date",""),
                "NEW":"🆕" if bool(vd.get("is_new",False)) else "",
                **o
            })
    if not rows:
        return pd.DataFrame(columns=expected_cols)
    return pd.DataFrame(rows).reindex(columns=expected_cols)

# ---------------- LIVE PRICING BATCH ----------------
def _parse_iso_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except Exception:
        return None

def _provider_fresh(entry,provider,ttl_hours):
    ts=((entry or {}).get("timestamps") or {}).get(provider)
    dt=_parse_iso_dt(ts)
    if dt is None:
        return False
    now=datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    return (now-dt).total_seconds() < float(ttl_hours)*3600

def _utc_now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()+"Z"

def _price_one_candidate(task,cid,secret,skey,ebay_token,ttl_hours):
    """Worker used by ThreadPoolExecutor. Does not touch Streamlit UI."""
    vehicle,stock,part,pn,key,q,existing=task
    entry=dict(existing or {})
    entry["query"]=q
    timestamps=dict(entry.get("timestamps") or {})
    errors=[]
    fetched=[]

    if cid and secret:
        if _provider_fresh(entry,"ebay",ttl_hours) and entry.get("ebay"):
            pass
        else:
            try:
                df=ebay_used_comps_with_token(q,ebay_token,40)
                summ=summarize_comps(df)
                entry["ebay"]={**summ,"examples":df.head(8).to_dict("records") if len(df) else []}
                timestamps["ebay"]=_utc_now_iso()
                fetched.append("eBay")
            except Exception as e:
                errors.append(f"eBay | {q}: {e}")

    if skey:
        if _provider_fresh(entry,"shopping",ttl_hours) and entry.get("shopping"):
            pass
        else:
            try:
                df=serpapi_shopping_comps(q,skey,35)
                summ=summarize_comps(df)
                entry["shopping"]={**summ,"examples":df.head(8).to_dict("records") if len(df) else []}
                timestamps["shopping"]=_utc_now_iso()
                fetched.append("Google Shopping")
            except Exception as e:
                errors.append(f"Google Shopping | {q}: {e}")

    entry["timestamps"]=timestamps
    entry["updated_at"]=_utc_now_iso()
    return key,entry,errors,fetched

def price_candidates(cands,max_queries,progress=None,status=None):
    cid,secret=ebay_credentials()
    skey=serpapi_key()
    if not (cid and secret) and not skey:
        raise RuntimeError("Add either eBay credentials or a SerpAPI key in SETUP first.")

    subset=cands.drop_duplicates(["vehicle_id","PART"]).head(max_queries)
    if len(subset)==0:
        return 0,0,[]

    ttl=float(st.session_state.get("market_cache_hours",24))
    workers=max(1,min(int(st.session_state.get("market_workers",4)),8,len(subset)))

    # Reuse one eBay token for the entire batch.
    ebay_token=None
    if cid and secret:
        ebay_token=ebay_access_token(cid,secret)

    tasks=[]
    skipped=0
    for _,r in subset.iterrows():
        vehicle=st.session_state.inventory.loc[r["vehicle_id"]].to_dict()
        pn=part_number(r["STOCK"],r["PART"])
        key=market_key(vehicle,r["PART"],pn)
        q=comp_query(vehicle,r["PART"],pn)
        existing=st.session_state.market_cache.get(key,{})
        ebay_ok=(not (cid and secret)) or (_provider_fresh(existing,"ebay",ttl) and bool(existing.get("ebay")))
        shop_ok=(not skey) or (_provider_fresh(existing,"shopping",ttl) and bool(existing.get("shopping")))
        if ebay_ok and shop_ok:
            skipped+=1
            continue
        tasks.append((vehicle,r["STOCK"],r["PART"],pn,key,q,existing))

    total=len(tasks)
    if total==0:
        if progress:
            progress.progress(1.0)
        if status:
            status.info(f"All {len(subset)} selected market searches were already cached and fresh.")
        return 0,skipped,[]

    errors=[]
    completed=0
    fetched_queries=0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map={
            pool.submit(_price_one_candidate,t,cid,secret,skey,ebay_token,ttl):t
            for t in tasks
        }
        for fut in as_completed(future_map):
            try:
                key,entry,errs,fetched=fut.result()
                st.session_state.market_cache[key]=entry
                errors.extend(errs)
                if fetched:
                    fetched_queries+=1
            except Exception as e:
                errors.append(str(e))
            completed+=1
            if progress:
                progress.progress(completed/max(total,1))
            if status:
                status.info(
                    f"Market pricing: {completed} of {total} finished · "
                    f"{skipped} reused from cache · {workers} parallel workers"
                )

    return fetched_queries,skipped,errors

# ---------------- OCR / CAMERA ----------------
def run_ocr(uploaded):
    try:
        from rapidocr import RapidOCR
    except Exception as e:
        return "",f"OCR package unavailable: {e}"
    try:
        img=Image.open(uploaded).convert("RGB")
        engine=RapidOCR()
        result=engine(np.array(img))
        texts=[]
        if hasattr(result,"txts"):
            texts=[str(x) for x in (result.txts or [])]
        elif isinstance(result,tuple) and len(result):
            first=result[0]
            if isinstance(first,list):
                for item in first:
                    if isinstance(item,(list,tuple)) and len(item)>=2:
                        texts.append(str(item[1]))
        elif isinstance(result,list):
            for item in result:
                if isinstance(item,(list,tuple)) and len(item)>=2:
                    texts.append(str(item[1]))
        return " ".join(texts),None
    except Exception as e:
        return "",str(e)

def extract_vins(text):
    cleaned=re.sub(r"[^A-HJ-NPR-Z0-9]","",text.upper())
    found=set(re.findall(r"[A-HJ-NPR-Z0-9]{17}",cleaned))
    return sorted(found)

def extract_part_numbers(text):
    toks=re.findall(r"\b[A-Z0-9][A-Z0-9\-]{5,22}\b",text.upper())
    out=[]
    for t in toks:
        if len(t)==17 and re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}",t): continue
        if any(c.isalpha() for c in t) and any(c.isdigit() for c in t):
            if t not in out: out.append(t)
    return out[:20]

# ---------------- IMPACT / CONDITION ----------------
def save_condition(stock,part,vals):
    st.session_state.condition_map[f"{stock}|{part}"]=vals

# ---------------- BUSINESS TRACKING ----------------
PURCHASE_COLS=["purchase_id","date","stock","car","part","oem","cost","channel_plan","market_at_buy","notes","status"]
LISTING_COLS=["listing_id","purchase_id","platform","list_date","list_price","current_price","status","url"]
SALE_COLS=["sale_id","purchase_id","sold_date","sale_price","fees","shipping","refund","notes"]

def ensure_df(name,cols):
    if name not in st.session_state or not isinstance(st.session_state[name],pd.DataFrame):
        st.session_state[name]=pd.DataFrame(columns=cols)

def purchase_profit_table():
    p=st.session_state.purchases.copy()
    if len(p)==0: return pd.DataFrame()
    s=st.session_state.sales.copy()
    if len(s):
        agg=s.groupby("purchase_id",as_index=False).agg(
            sale_price=("sale_price","sum"),fees=("fees","sum"),
            shipping=("shipping","sum"),refund=("refund","sum"),
            last_sold=("sold_date","max")
        )
        x=p.merge(agg,on="purchase_id",how="left")
    else:
        x=p.copy()
        for c in ["sale_price","fees","shipping","refund"]: x[c]=0.0
        x["last_sold"]=""
    for c in ["sale_price","fees","shipping","refund"]:
        x[c]=pd.to_numeric(x[c],errors="coerce").fillna(0)
    x["cost"]=pd.to_numeric(x["cost"],errors="coerce").fillna(0)
    x["actual_profit"]=x["sale_price"]-x["fees"]-x["shipping"]-x["refund"]-x["cost"]
    buydate=pd.to_datetime(x["date"],errors="coerce")
    solddate=pd.to_datetime(x["last_sold"],errors="coerce")
    x["days_to_sell"]=(solddate-buydate).dt.days
    return x

def learned_part_warning(part):
    x=purchase_profit_table()
    if len(x)==0 or "part" not in x.columns: return ""
    g=x[x["part"].astype(str).map(norm).eq(norm(part)) & (x["sale_price"]>0)]
    if len(g)<2: return ""
    avg_profit=g["actual_profit"].mean()
    avg_days=g["days_to_sell"].dropna().mean() if g["days_to_sell"].notna().any() else np.nan
    if avg_profit<50: return f"Your history: low avg profit ({money(avg_profit)})"
    if pd.notna(avg_days) and avg_days>75: return f"Your history: slow seller ({avg_days:.0f} days avg)"
    return ""

def price_drop_recommendations():
    if not len(st.session_state.listings): return pd.DataFrame()
    p=st.session_state.purchases.set_index("purchase_id") if len(st.session_state.purchases) else pd.DataFrame()
    rows=[]
    for _,l in st.session_state.listings.iterrows():
        if str(l.get("status","")).lower()!="active": continue
        try: days=(pd.Timestamp.today().normalize()-pd.to_datetime(l["list_date"])).days
        except: days=0
        current=safe_float(l.get("current_price"),safe_float(l.get("list_price")))
        pid=l.get("purchase_id")
        cost=0; market=0; part=""; car=""
        if len(p) and pid in p.index:
            pr=p.loc[pid]
            cost=safe_float(pr.get("cost"))
            market=safe_float(pr.get("market_at_buy"))
            part=str(pr.get("part","")); car=str(pr.get("car",""))
        if days<14: continue
        floor=cost*1.20
        target=current
        reason=""
        if market>0:
            target=max(floor,min(current*.94,market*.90))
            reason="older listing vs market reference"
        else:
            target=max(floor,current*(.95 if days<30 else .90))
            reason="age-based reduction"
        if target<current-1:
            rows.append({
                "listing_id":l["listing_id"],"car":car,"part":part,
                "days_listed":days,"current":round(current,2),
                "suggested":round(target,2),"reason":reason
            })
    return pd.DataFrame(rows)

# ---------------- BACKUP / RESTORE / MEMORY ----------------
def df_records(df):
    if df is None or len(df)==0: return []
    return df.replace({np.nan:None}).to_dict("records")

def backup_payload():
    return {
        "version":"7.2",
        "inventory":df_records(st.session_state.inventory),
        "market_cache":st.session_state.market_cache,
        "live_prices":st.session_state.live_prices,
        "impact_map":st.session_state.impact_map,
        "condition_map":st.session_state.condition_map,
        "part_numbers":st.session_state.part_numbers,
        "pull_list":df_records(st.session_state.pull_list),
        "purchases":df_records(st.session_state.purchases),
        "listings":df_records(st.session_state.listings),
        "sales":df_records(st.session_state.sales),
        "avoid_parts":list(st.session_state.avoid_parts),
        "watch_keywords":st.session_state.watch_keywords,
        "market_only":st.session_state.market_only,
        "sold_comps":df_records(st.session_state.sold_comps),
        "interchange":df_records(st.session_state.interchange),
        "market_cache_hours":st.session_state.get("market_cache_hours",24),
        "market_workers":st.session_state.get("market_workers",4),
        "saved_at":datetime.now().isoformat(),
    }

def restore_payload(data):
    st.session_state.inventory=pd.DataFrame(data.get("inventory",[]))
    st.session_state.market_cache=data.get("market_cache",{})
    st.session_state.live_prices=data.get("live_prices",{})
    st.session_state.impact_map=data.get("impact_map",{})
    st.session_state.condition_map=data.get("condition_map",{})
    st.session_state.part_numbers=data.get("part_numbers",{})
    st.session_state.pull_list=pd.DataFrame(data.get("pull_list",[]))
    st.session_state.purchases=pd.DataFrame(data.get("purchases",[]),columns=PURCHASE_COLS) if data.get("purchases") else pd.DataFrame(columns=PURCHASE_COLS)
    st.session_state.listings=pd.DataFrame(data.get("listings",[]),columns=LISTING_COLS) if data.get("listings") else pd.DataFrame(columns=LISTING_COLS)
    st.session_state.sales=pd.DataFrame(data.get("sales",[]),columns=SALE_COLS) if data.get("sales") else pd.DataFrame(columns=SALE_COLS)
    st.session_state.avoid_parts=set(data.get("avoid_parts",[]))
    st.session_state.watch_keywords=data.get("watch_keywords",[])
    st.session_state.market_only=bool(data.get("market_only",True))
    st.session_state.sold_comps=pd.DataFrame(data.get("sold_comps",[]))
    st.session_state.interchange=pd.DataFrame(data.get("interchange",[]))
    st.session_state.market_cache_hours=int(data.get("market_cache_hours",st.session_state.get("market_cache_hours",24)))
    st.session_state.market_workers=int(data.get("market_workers",st.session_state.get("market_workers",4)))

def _memory_json_for_hash():
    data=backup_payload().copy()
    data.pop("saved_at",None)
    return json.dumps(data,sort_keys=True,default=str,separators=(",",":"))

def _memory_hash():
    return hashlib.sha256(_memory_json_for_hash().encode()).hexdigest()

def _local_memory_path():
    mem_id=re.sub(r"[^A-Za-z0-9_.-]","_",str(st.session_state.get("memory_id","wichita-v72")))
    return Path("/tmp")/f"{mem_id}_scanner_memory.json"

def save_local_memory():
    path=_local_memory_path()
    path.write_text(json.dumps(backup_payload(),default=str))
    return str(path)

def load_local_memory():
    path=_local_memory_path()
    if not path.exists():
        return False,"No local memory file yet."
    restore_payload(json.loads(path.read_text()))
    return True,f"Loaded local memory from {path.name}."

def supabase_config():
    url=key=""
    table="app_memory"
    memory_id="wichita-v72"
    try:
        url=st.secrets.get("SUPABASE_URL","")
        key=st.secrets.get("SUPABASE_KEY","")
        table=st.secrets.get("SUPABASE_TABLE","app_memory")
        memory_id=st.secrets.get("SUPABASE_MEMORY_ID","wichita-v72")
    except Exception:
        pass
    url=url or st.session_state.get("supabase_url","")
    key=key or st.session_state.get("supabase_key","")
    table=st.session_state.get("supabase_table",table) or table
    memory_id=st.session_state.get("memory_id",memory_id) or memory_id
    return str(url).rstrip("/"),str(key),str(table),str(memory_id)

def cloud_memory_enabled():
    url,key,table,memory_id=supabase_config()
    return bool(url and key and table and memory_id)

def load_cloud_memory():
    url,key,table,memory_id=supabase_config()
    if not (url and key):
        return False,"Cloud memory is not configured."
    headers={"apikey":key,"Authorization":f"Bearer {key}","Accept":"application/json"}
    r=requests.get(
        f"{url}/rest/v1/{table}",
        headers=headers,
        params={"id":f"eq.{memory_id}","select":"payload,updated_at","limit":"1"},
        timeout=15,
    )
    r.raise_for_status()
    rows=r.json()
    if not rows:
        return False,"No cloud memory record exists yet."
    payload=rows[0].get("payload") or {}
    restore_payload(payload)
    return True,f"Loaded cloud memory saved {rows[0].get('updated_at','previously')}."

def save_cloud_memory():
    url,key,table,memory_id=supabase_config()
    if not (url and key):
        return False,"Cloud memory is not configured."
    headers={
        "apikey":key,"Authorization":f"Bearer {key}",
        "Content-Type":"application/json",
        "Prefer":"resolution=merge-duplicates,return=minimal",
    }
    body={
        "id":memory_id,
        "payload":backup_payload(),
        "updated_at":_utc_now_iso(),
    }
    r=requests.post(f"{url}/rest/v1/{table}",headers=headers,json=body,timeout=20)
    r.raise_for_status()
    return True,"Cloud memory saved."

def autosave_memory_if_changed():
    if not st.session_state.get("memory_autosave",True):
        return
    try:
        h=_memory_hash()
    except Exception:
        return
    if h==st.session_state.get("_last_memory_hash",""):
        return
    # Fast local autosave on every meaningful state change.
    try:
        save_local_memory()
    except Exception:
        pass
    # Optional durable cloud autosave.
    if cloud_memory_enabled():
        try:
            save_cloud_memory()
            st.session_state.memory_status="Cloud memory saved automatically."
        except Exception as e:
            st.session_state.memory_status=f"Local memory saved; cloud save failed: {e}"
    else:
        st.session_state.memory_status="Local session memory saved. Configure cloud memory for restart-proof storage."
    st.session_state["_last_memory_hash"]=h

# ---------------- STATE ----------------
defaults={
    "inventory":pd.DataFrame(),"market_cache":{},"live_prices":{},
    "impact_map":{},"condition_map":{},"part_numbers":{},
    "pull_list":pd.DataFrame(),"sold_comps":pd.DataFrame(),"interchange":pd.DataFrame(),
    "avoid_parts":set(),"watch_keywords":["F-150","SILVERADO","SIERRA","RAM","LEXUS","BMW","2500","3500"],
    "last_sync":"Never","previous_stocks":set(),
    "ebay_client_id":"","ebay_client_secret":"","serpapi_api_key":"",
    "market_only":True,
    "market_cache_hours":24,"market_workers":4,
    "memory_autosave":True,"memory_loaded":False,"memory_status":"Not loaded yet.",
    "supabase_url":"","supabase_key":"","supabase_table":"app_memory","memory_id":"wichita-v72",
    "ebay_fee":.13,"local_fee":0.0,"ebay_quick_pct":.88,"local_price_pct":.82,
    "travel":10.0,"packing":5.0,"high_value_alert":600.0,
}
for k,v in defaults.items():
    if k not in st.session_state: st.session_state[k]=v
ensure_df("purchases",PURCHASE_COLS)
ensure_df("listings",LISTING_COLS)
ensure_df("sales",SALE_COLS)

# Load saved memory once when the Streamlit session starts.
if not st.session_state.get("memory_loaded",False):
    loaded=False
    msg=""
    if cloud_memory_enabled():
        try:
            loaded,msg=load_cloud_memory()
        except Exception as e:
            msg=f"Cloud memory load failed: {e}"
    if not loaded:
        try:
            loaded,local_msg=load_local_memory()
            if loaded:
                msg=local_msg
        except Exception:
            pass
    st.session_state.memory_loaded=True
    st.session_state.memory_status=msg or "No saved memory found yet."
    ensure_df("purchases",PURCHASE_COLS)
    ensure_df("listings",LISTING_COLS)
    ensure_df("sales",SALE_COLS)

# ---------------- UI ----------------
st.title("🔧 Wichita Parts Profit Scanner V7.2")
st.caption(f"{YARD_NAME} · {YARD_ADDRESS}")

tabs=st.tabs([
    "🏆 BEST CARS","🧭 YARD MODE","🔄 SYNC","📷 SCAN",
    "🌐 MARKET","📦 BUSINESS","📊 DASHBOARD","⚙️ SETUP"
])

# ---------- BEST CARS ----------
with tabs[0]:
    st.subheader("Best donor cars + best parts")
    if not len(st.session_state.inventory):
        st.info("Sync the Wichita yard first.")
    else:
        c1,c2=st.columns(2)
        min_profit=c1.number_input("Minimum expected profit",0,3000,75,25)
        min_score=c2.slider("Minimum score",0,100,52)

        opp=all_opportunities(st.session_state.inventory)
        good=opp[(opp["PROFIT"]>=min_profit)&(opp["SCORE"]>=min_score)].copy()
        good=good.sort_values(["SCORE","PROFIT","$/HR"],ascending=False)

        if not len(good):
            st.info("No market-priced recommendations yet. Go to MARKET and run PRICE TOP OPPORTUNITIES, or temporarily turn off market-priced-only mode in SETUP.")
        else:
            donors=[]
            for vid,g in good.groupby("vehicle_id"):
                top=g.nlargest(5,"SCORE")
                f=top.iloc[0]
                donors.append({
                    "vehicle_id":vid,"NEW":f.NEW,"CAR":f.CAR,"STOCK":f.STOCK,
                    "SECTION":f.SECTION,"ROW":f.ROW,"SPACE":f.SPACE,
                    "TOP5_PROFIT":top["PROFIT"].sum(),"BEST_SCORE":top["SCORE"].max(),
                    "AVG_DAYS":top["DAYS"].mean()
                })
            donors=pd.DataFrame(donors).sort_values(["TOP5_PROFIT","BEST_SCORE"],ascending=False)

            a,b,c,d=st.columns(4)
            a.metric("Cars worth checking",len(donors))
            b.metric("Best single pull",money(good["PROFIT"].max()))
            c.metric("Best 5-part car",money(donors["TOP5_PROFIT"].max()))
            d.metric("Market-priced pulls",int(good["MARKET_DATA"].sum()))

            # high value / watch alerts
            alerts=[]
            for _,drow in donors.iterrows():
                watch=any(norm(k) in norm(drow.CAR) for k in st.session_state.watch_keywords)
                high=drow.TOP5_PROFIT>=st.session_state.high_value_alert
                if str(drow.NEW)=="🆕" and (watch or high):
                    alerts.append(drow)
            if alerts:
                st.markdown("### 🚨 New-arrival alerts")
                for arow in alerts[:10]:
                    st.markdown(
                        f"<div class='alert'><b>{arow['CAR']}</b> — Row {arow['ROW']} / Space {arow['SPACE']}<br>"
                        f"Top-5 est. profit <b>{money(arow['TOP5_PROFIT'])}</b> · Stock {arow['STOCK']}</div>",
                        unsafe_allow_html=True
                    )

            st.caption("Recommendations are market-driven. MARKET blends available sold comps, eBay USED asks and Google Shopping used-part asks. If market-only mode is on, parts without real market data are hidden.")

            for _,drow in donors.head(18).iterrows():
                g=good[good["vehicle_id"]==drow.vehicle_id].nlargest(5,"SCORE")
                ph=""
                for _,r in g.iterrows():
                    warn=""
                    if r["LEARNED_WARNING"]: warn=f"<br><span class='small'>⚠ {r['LEARNED_WARNING']}</span>"
                    ph+=(
                        f"<div class='partrow'><b>{r['PART']}</b> · {r['CHANNEL']}<br>"
                        f"Buy {money(r['BUY'])} · Target {money(r['TARGET'])} · "
                        f"<b>Profit {money(r['PROFIT'])}</b> · {money(r['$/HR'])}/hr<br>"
                        f"<span class='small'>Market {money(r['MARKET'])} · ~{r['DAYS']} days · "
                        f"Sell-through {r['SELL_THROUGH']:.0f}% · Confidence {r['CONF']}% · "
                        f"Risk discount {r['RISK_DISC']:.0f}% · {r['VALUE_SOURCE']}</span>{warn}</div>"
                    )
                st.markdown(
                    f"<div class='card'><b>{drow.NEW} {drow.CAR}</b><br>"
                    f"Row <b>{drow.ROW}</b> · Space <b>{drow.SPACE}</b> · {drow.SECTION}<br>"
                    f"<span class='small'>Stock {drow.STOCK} · Top-5 est. profit {money(drow.TOP5_PROFIT)}</span>{ph}</div>",
                    unsafe_allow_html=True
                )

            with st.expander("Full opportunity table"):
                cols=["NEW","CAR","ROW","SPACE","PART","OEM","BUY","MARKET","TARGET","CHANNEL","PROFIT","$/HR","DAYS","SELL_THROUGH","CONF","SCORE","VALUE_SOURCE","STOCK"]
                st.dataframe(good[cols],use_container_width=True,hide_index=True)

# ---------- YARD MODE ----------
with tabs[1]:
    st.subheader("I'm at the yard")
    if not len(st.session_state.inventory):
        st.info("Sync inventory first.")
    else:
        opp=all_opportunities(st.session_state.inventory).sort_values(["SCORE","PROFIT"],ascending=False)
        a,b,c=st.columns(3)
        max_cars=a.slider("Cars to visit",3,20,8,1)
        per_car=b.slider("Parts/car",1,5,3,1)
        yard_min=c.number_input("Min profit/part",0,1000,100,25)

        if st.button("🧭 BUILD TODAY'S YARD ROUTE",type="primary"):
            filt=opp[opp["PROFIT"]>=yard_min].copy()
            rows=[]
            seen=[]
            for vid,g in filt.groupby("vehicle_id"):
                top=g.nlargest(per_car,"SCORE")
                if len(top):
                    total=top["PROFIT"].sum()
                    seen.append((vid,total,top))
            seen=sorted(seen,key=lambda x:x[1],reverse=True)[:max_cars]
            for vid,total,top in seen:
                for _,r in top.iterrows():
                    rows.append({
                        "route_id":unique_id("route"),"done":False,"bought":False,
                        "SECTION":r.SECTION,"ROW":r.ROW,"SPACE":r.SPACE,
                        "CAR":r.CAR,"STOCK":r.STOCK,"PART":r.PART,"OEM":r.OEM,
                        "BUY":r.BUY,"TARGET":r.TARGET,"PROFIT":r.PROFIT,
                        "CHANNEL":r.CHANNEL,"SCORE":r.SCORE
                    })
            route=pd.DataFrame(rows)
            if len(route):
                route["_r"]=route["ROW"].map(row_num)
                route["_s"]=route["SPACE"].map(row_num)
                route=route.sort_values(["SECTION","_r","_s","CAR"]).drop(columns=["_r","_s"])
            st.session_state.pull_list=route

        if len(st.session_state.pull_list):
            st.markdown("### Walking order")
            route=st.session_state.pull_list.copy()
            for stock,g in route.groupby("STOCK",sort=False):
                first=g.iloc[0]
                st.markdown(f"#### Row {first.ROW} / Space {first.SPACE} — {first.CAR}")
                for idx,r in g.iterrows():
                    key=f"done_{r.route_id}"
                    done=st.checkbox(
                        f"{r.PART} — est. {money(r.PROFIT)} profit ({r.CHANNEL})",
                        value=bool(r.done),key=key
                    )
                    st.session_state.pull_list.loc[idx,"done"]=done
                st.caption(f"Stock {stock}")

            st.markdown("### Inspect / buy a route item")
            choices=list(st.session_state.pull_list.index)
            ridx=st.selectbox(
                "Route item",choices,
                format_func=lambda i:f"{st.session_state.pull_list.loc[i,'CAR']} — {st.session_state.pull_list.loc[i,'PART']}"
            )
            rr=st.session_state.pull_list.loc[ridx]
            st.warning(str(PARTS.loc[PARTS.part.eq(rr.PART),"warnings"].iloc[0]))

            c1,c2,c3=st.columns(3)
            broken=c1.checkbox("Broken tabs",key=f"bt_{ridx}")
            cracked=c2.checkbox("Cracked/damaged",key=f"cr_{ridx}")
            corrosion=c3.checkbox("Corrosion",key=f"co_{ridx}")
            c4,c5,c6=st.columns(3)
            cut=c4.checkbox("Cut wires",key=f"cw_{ridx}")
            water=c5.checkbox("Water damage",key=f"wd_{ridx}")
            untested=c6.checkbox("Untested",value=True,key=f"ut_{ridx}")
            if st.button("SAVE CONDITION"):
                save_condition(rr.STOCK,rr.PART,{
                    "broken_tabs":broken,"cracked":cracked,"corrosion":corrosion,
                    "cut_wires":cut,"water_damage":water,"untested":untested
                })
                st.success("Condition saved. Scores will reflect it.")

            actual_cost=st.number_input("Actual checkout cost",0.0,5000.0,float(rr.BUY),1.0,key="actualcost")
            notes=st.text_input("Purchase notes")
            if st.button("✅ I BOUGHT THIS PART"):
                row={
                    "purchase_id":unique_id("buy"),"date":today_iso(),"stock":rr.STOCK,
                    "car":rr.CAR,"part":rr.PART,"oem":part_number(rr.STOCK,rr.PART),
                    "cost":actual_cost,"channel_plan":rr.CHANNEL,
                    "market_at_buy":rr.TARGET,"notes":notes,"status":"inventory"
                }
                st.session_state.purchases=pd.concat([st.session_state.purchases,pd.DataFrame([row])],ignore_index=True)
                st.session_state.pull_list.loc[ridx,"bought"]=True
                st.success("Added to purchased inventory.")

            st.download_button("⬇️ DOWNLOAD ROUTE",st.session_state.pull_list.to_csv(index=False).encode(),
                               "wichita_yard_route.csv","text/csv")
        else:
            st.info("Build a route to get a row-by-row walking list.")

# ---------- SYNC ----------
with tabs[2]:
    st.subheader("Sync Wichita inventory")
    if st.button("🔄 SYNC WHOLE WICHITA YARD",type="primary"):
        try:
            old=set(st.session_state.inventory.get("stock_number",pd.Series(dtype=str)).astype(str)) if len(st.session_state.inventory) else st.session_state.previous_stocks
            with st.spinner("Walking public Wichita inventory pages…"):
                df,pages=sync_inventory()
            if len(df):
                current=set(df["stock_number"].astype(str))
                new=current-old if old else set()
                df["is_new"]=df["stock_number"].astype(str).isin(new)
                st.session_state.previous_stocks=current
                st.session_state.inventory=df
                st.session_state.last_sync=datetime.now().strftime("%m/%d/%Y %I:%M %p")
                st.success(f"Loaded {len(df)} vehicles from {pages} page(s). {len(new)} flagged new.")
            else:
                st.error("No readable inventory returned.")
        except Exception as e:
            st.error(f"Sync failed: {e}")

    a,b,c=st.columns(3)
    a.metric("Vehicles",len(st.session_state.inventory))
    newn=int(st.session_state.inventory["is_new"].sum()) if len(st.session_state.inventory) and "is_new" in st.session_state.inventory else 0
    b.metric("New",newn)
    c.metric("Last sync",st.session_state.last_sync)

    if len(st.session_state.inventory):
        st.dataframe(st.session_state.inventory,use_container_width=True,hide_index=True)
        st.download_button("⬇️ INVENTORY SNAPSHOT",st.session_state.inventory.to_csv(index=False).encode(),
                           "wichita_inventory_snapshot.csv","text/csv")
    snap=st.file_uploader("Load an older inventory snapshot for new-arrival comparison",type=["csv"],key="old_snap")
    if snap:
        prev=pd.read_csv(snap,dtype=str).fillna("")
        if "stock_number" in prev.columns:
            st.session_state.previous_stocks=set(prev["stock_number"].astype(str))
            st.success(f"Loaded {len(prev)} previous stock numbers.")

# ---------- SCAN ----------
with tabs[3]:
    st.subheader("Camera VIN / OEM scanner")
    st.caption("Take a clear, close photo. OCR is done inside the Streamlit app; always verify the extracted number against the label.")

    mode=st.radio("Scan",["VIN","OEM part number"],horizontal=True)
    photo=st.camera_input("Take photo")
    upload=st.file_uploader("Or upload a photo",type=["jpg","jpeg","png","webp"],key="ocr_upload")
    target=photo if photo is not None else upload

    if target is not None and st.button("🔎 READ PHOTO"):
        with st.spinner("Reading text…"):
            text,err=run_ocr(target)
        if err:
            st.error(err)
        else:
            st.session_state["last_ocr_text"]=text
            st.write("OCR text:",text)
            if mode=="VIN":
                vins=extract_vins(text)
                if vins:
                    st.success(f"VIN candidate: {vins[0]}")
                else:
                    st.warning("No 17-character VIN confidently found.")
            else:
                pns=extract_part_numbers(text)
                if pns:
                    st.success("Possible OEM numbers:")
                    st.write(pns)
                else:
                    st.warning("No likely part number found.")

    st.markdown("### Save an OEM number to a donor part")
    if len(st.session_state.inventory):
        vi=st.selectbox("Vehicle",list(st.session_state.inventory.index),
            format_func=lambda i:f"{st.session_state.inventory.loc[i,'year']} {st.session_state.inventory.loc[i,'make']} {st.session_state.inventory.loc[i,'model']} — Row {st.session_state.inventory.loc[i,'yard_row']}")
        part=st.selectbox("Part",PARTS.part.tolist())
        suggested=""
        txt=st.session_state.get("last_ocr_text","")
        pns=extract_part_numbers(txt) if txt else []
        if pns: suggested=pns[0]
        pn=st.text_input("OEM part number",value=suggested)
        if st.button("SAVE OEM NUMBER"):
            stock=str(st.session_state.inventory.loc[vi,"stock_number"])
            st.session_state.part_numbers[f"{stock}|{part}"]=pn.strip()
            matches=interchange_matches(pn.strip())
            st.success(f"Saved. {len(matches)} interchange match(es) in your database.")

# ---------- MARKET ----------
with tabs[4]:
    st.subheader("Market, fitment & pricing")
    up=st.file_uploader("Import sold comps CSV",type=["csv"],key="soldcomp_upload")
    if up:
        st.session_state.sold_comps=pd.read_csv(up)
        st.success(f"Loaded {len(st.session_state.sold_comps)} sold comp rows.")

    iu=st.file_uploader("Import interchange CSV",type=["csv"],key="inter_upload")
    if iu:
        st.session_state.interchange=pd.read_csv(iu)
        st.success(f"Loaded {len(st.session_state.interchange)} interchange rows.")

    providers=provider_status()
    active_names=[k for k,v in providers.items() if v]
    if active_names:
        st.success("Market sources enabled: " + ", ".join(active_names))
    else:
        st.warning("No live market source is configured yet. Add eBay and/or SerpAPI in SETUP.")

    st.session_state.market_only=st.toggle("Recommend only parts with real market-price data",value=st.session_state.market_only,
                                           help="When on, BEST CARS and YARD MODE hide parts until eBay, Google Shopping or sold-comps data produces a usable market value.")

    if len(st.session_state.inventory):
        base=all_opportunities(st.session_state.inventory,allow_unpriced=True).sort_values(["SCORE","PROFIT"],ascending=False)
        c1,c2=st.columns(2)
        n=c1.slider("Top part searches to live-price",5,60,20,5)
        c2.caption(f"Cache: {st.session_state.market_cache_hours} hr · Parallel workers: {st.session_state.market_workers}")
        if st.button("🌐 PRICE TOP OPPORTUNITIES"):
            prog=st.progress(0.0)
            stat=st.empty()
            try:
                fetched,skipped,errors=price_candidates(base,n,prog,stat)
                prog.empty()
                stat.empty()
                st.success(f"Finished. {fetched} candidate(s) refreshed from the internet; {skipped} reused from fresh cache.")
                if errors:
                    with st.expander(f"{len(errors)} lookup error(s)"):
                        st.write(errors)
            except Exception as e:
                prog.empty(); stat.empty(); st.error(str(e))

        vi=st.selectbox("Inspect one donor car",list(st.session_state.inventory.index),
            format_func=lambda i:f"{st.session_state.inventory.loc[i,'year']} {st.session_state.inventory.loc[i,'make']} {st.session_state.inventory.loc[i,'model']} — Row {st.session_state.inventory.loc[i,'yard_row']}",
            key="market_car")
        v=st.session_state.inventory.loc[vi].to_dict()
        caropp=all_opportunities(st.session_state.inventory.loc[[vi]],allow_unpriced=True).sort_values(["SCORE","PROFIT"],ascending=False)
        st.dataframe(caropp[["PART","OEM","MARKET","TARGET","CHANNEL","PROFIT","DAYS","SELL_THROUGH","SELL_SOURCE","CONF","FITMENTS","WARNING"]].head(15),
                     use_container_width=True,hide_index=True)

    if st.session_state.market_cache:
        st.markdown("### Cached market references")
        rows=[]
        for k,v in st.session_state.market_cache.items():
            eb=v.get("ebay",{}) if isinstance(v,dict) else {}
            sh=v.get("shopping",{}) if isinstance(v,dict) else {}
            rows.append({
                "vehicle_part":k,"eBay_median":eb.get("median"),"eBay_n":eb.get("n"),
                "Shopping_median":sh.get("median"),"Shopping_n":sh.get("n"),
                "query":v.get("query") if isinstance(v,dict) else ""
            })
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)

# ---------- BUSINESS ----------
with tabs[5]:
    st.subheader("Purchased parts, listings & sales")

    subtabs=st.tabs(["Purchased","Listings","Record sale","Price drops","Avoid list"])

    with subtabs[0]:
        st.dataframe(st.session_state.purchases,use_container_width=True,hide_index=True)
        if len(st.session_state.purchases):
            st.download_button("Download purchases",st.session_state.purchases.to_csv(index=False).encode(),"purchases.csv","text/csv")

    with subtabs[1]:
        if len(st.session_state.purchases):
            pid=st.selectbox("Purchased part",st.session_state.purchases.purchase_id.tolist(),
                format_func=lambda x:f"{st.session_state.purchases.loc[st.session_state.purchases.purchase_id.eq(x),'car'].iloc[0]} — {st.session_state.purchases.loc[st.session_state.purchases.purchase_id.eq(x),'part'].iloc[0]}")
            platform=st.selectbox("Platform",["eBay","Facebook Marketplace","Local","Other"])
            list_price=st.number_input("List price",0.0,10000.0,200.0,5.0)
            url=st.text_input("Listing URL (optional)")
            if st.button("ADD LISTING"):
                row={"listing_id":unique_id("list"),"purchase_id":pid,"platform":platform,
                     "list_date":today_iso(),"list_price":list_price,"current_price":list_price,
                     "status":"active","url":url}
                st.session_state.listings=pd.concat([st.session_state.listings,pd.DataFrame([row])],ignore_index=True)
                st.success("Listing added.")
        st.dataframe(st.session_state.listings,use_container_width=True,hide_index=True)

    with subtabs[2]:
        if len(st.session_state.purchases):
            pid=st.selectbox("Part sold",st.session_state.purchases.purchase_id.tolist(),key="sale_pid")
            sale_price=st.number_input("Sale price",0.0,20000.0,250.0,5.0)
            fees=st.number_input("Marketplace fees",0.0,5000.0,0.0,1.0)
            ship=st.number_input("Actual shipping cost",0.0,5000.0,0.0,1.0)
            refund=st.number_input("Refund/return loss",0.0,5000.0,0.0,1.0)
            notes=st.text_input("Sale notes")
            if st.button("RECORD SALE"):
                row={"sale_id":unique_id("sale"),"purchase_id":pid,"sold_date":today_iso(),
                     "sale_price":sale_price,"fees":fees,"shipping":ship,"refund":refund,"notes":notes}
                st.session_state.sales=pd.concat([st.session_state.sales,pd.DataFrame([row])],ignore_index=True)
                st.session_state.purchases.loc[st.session_state.purchases.purchase_id.eq(pid),"status"]="sold"
                st.session_state.listings.loc[st.session_state.listings.purchase_id.eq(pid),"status"]="sold"
                st.success("Sale recorded.")
        st.dataframe(st.session_state.sales,use_container_width=True,hide_index=True)

    with subtabs[3]:
        rec=price_drop_recommendations()
        if len(rec):
            st.dataframe(rec,use_container_width=True,hide_index=True)
        else:
            st.info("No active listings currently need a price-drop recommendation.")

    with subtabs[4]:
        st.write("Explicitly block parts you don't want the scanner recommending.")
        avoid_choice=st.multiselect("Never buy again",PARTS.part.tolist(),default=sorted(st.session_state.avoid_parts))
        st.session_state.avoid_parts=set(avoid_choice)

        x=purchase_profit_table()
        if len(x):
            sold=x[x["sale_price"]>0]
            if len(sold):
                stats=(sold.groupby("part")
                       .agg(sales=("purchase_id","count"),
                            avg_profit=("actual_profit","mean"),
                            avg_days=("days_to_sell","mean"))
                       .reset_index())
                weak=stats[(stats.sales>=2)&((stats.avg_profit<50)|(stats.avg_days>75))]
                if len(weak):
                    st.markdown("#### Learned weak categories from your own history")
                    st.dataframe(weak,use_container_width=True,hide_index=True)

# ---------- DASHBOARD ----------
with tabs[6]:
    st.subheader("Parts business dashboard")
    x=purchase_profit_table()
    if len(x):
        revenue=x["sale_price"].sum()
        cost=x["cost"].sum()
        fees=x["fees"].sum()
        shipping=x["shipping"].sum()
        refunds=x["refund"].sum()
        profit=x["actual_profit"].sum()
        sold=x[x["sale_price"]>0]
        avg_days=sold["days_to_sell"].dropna().mean() if len(sold) else np.nan
        unsold=x[x["sale_price"]<=0]

        a,b,c,d=st.columns(4)
        a.metric("Revenue",money(revenue))
        b.metric("Net profit",money(profit))
        c.metric("Parts on hand",len(unsold))
        d.metric("Avg days to sell",f"{avg_days:.0f}" if pd.notna(avg_days) else "—")

        a2,b2,c2,d2=st.columns(4)
        a2.metric("Parts cost",money(cost))
        b2.metric("Fees",money(fees))
        c2.metric("Shipping",money(shipping))
        d2.metric("Refund loss",money(refunds))

        if len(sold):
            bypart=(sold.groupby("part")
                    .agg(sales=("purchase_id","count"),
                         revenue=("sale_price","sum"),
                         profit=("actual_profit","sum"),
                         avg_profit=("actual_profit","mean"),
                         avg_days=("days_to_sell","mean"))
                    .reset_index()
                    .sort_values("profit",ascending=False))
            st.markdown("### Best categories from your actual results")
            st.dataframe(bypart,use_container_width=True,hide_index=True)

        st.markdown("### Detailed realized profit")
        st.dataframe(x,use_container_width=True,hide_index=True)
    else:
        st.info("Record purchases and sales to build your business dashboard.")

# ---------- SETUP ----------
with tabs[7]:
    st.subheader("Setup & controls")

    setup_tabs=st.tabs(["Market APIs","Speed & Cache","Memory","LKQ prices","Impact","Shipping","Watchlist","Backup"])

    with setup_tabs[0]:
        st.markdown("#### eBay Browse API")
        st.session_state.ebay_client_id=st.text_input("eBay Client ID / App ID",value=st.session_state.ebay_client_id,type="password")
        st.session_state.ebay_client_secret=st.text_input("eBay Client Secret / Cert ID",value=st.session_state.ebay_client_secret,type="password")
        if st.button("TEST EBAY CONNECTION"):
            cid,secret=ebay_credentials()
            try:
                ebay_access_token(cid,secret)
                st.success("eBay API connection works.")
            except Exception as e:
                st.error(str(e))

        st.markdown("#### Google Shopping via SerpAPI")
        st.session_state.serpapi_api_key=st.text_input("SerpAPI API key",value=st.session_state.serpapi_api_key,type="password")
        if st.button("TEST SERPAPI CONNECTION"):
            key=serpapi_key()
            if not key:
                st.error("Enter a SerpAPI key.")
            else:
                try:
                    test=serpapi_shopping_comps("2019 Ford F-150 OEM headlight used",key,5)
                    st.success(f"SerpAPI works — {len(test)} usable shopping result(s) returned.")
                except Exception as e:
                    st.error(str(e))

        st.caption("For permanent Streamlit setup, use Secrets:")
        st.code('EBAY_CLIENT_ID = "your-client-id"\nEBAY_CLIENT_SECRET = "your-client-secret"\nSERPAPI_KEY = "your-serpapi-key"',language="toml")

    with setup_tabs[1]:
        st.markdown("#### Faster market pricing")
        st.session_state.market_workers=st.slider(
            "Parallel market searches",1,8,int(st.session_state.market_workers),1,
            help="4 is a good default. Higher can be faster, but can hit provider rate limits."
        )
        st.session_state.market_cache_hours=st.slider(
            "Reuse internet prices for",1,168,int(st.session_state.market_cache_hours),1,
            help="24 hours saves API quota and makes repeat searches almost instant."
        )
        fresh=0
        now=datetime.now()
        for entry in st.session_state.market_cache.values():
            ts=_parse_iso_dt((entry or {}).get("updated_at"))
            if ts:
                nnow=datetime.now(ts.tzinfo) if ts.tzinfo else now
                if (nnow-ts).total_seconds() < st.session_state.market_cache_hours*3600:
                    fresh+=1
        st.metric("Fresh cached part searches",fresh)
        if st.button("CLEAR MARKET CACHE"):
            st.session_state.market_cache={}
            st.session_state["_last_memory_hash"]=""
            st.success("Market cache cleared.")

    with setup_tabs[2]:
        st.markdown("#### App memory")
        st.session_state.memory_autosave=st.toggle(
            "Auto-save memory",value=st.session_state.memory_autosave,
            help="Saves inventory, price cache, OEM numbers, route, purchases, listings, sales and settings."
        )
        st.info(st.session_state.get("memory_status",""))

        st.markdown("##### Optional restart-proof cloud memory (Supabase)")
        st.caption("Without cloud memory, the app still remembers during the running Streamlit instance, but Streamlit can erase local files when it sleeps/redeploys.")
        st.session_state.supabase_url=st.text_input(
            "Supabase project URL",value=st.session_state.supabase_url,
            placeholder="https://xxxxx.supabase.co"
        )
        st.session_state.supabase_key=st.text_input(
            "Supabase key",value=st.session_state.supabase_key,type="password"
        )
        st.session_state.supabase_table=st.text_input(
            "Memory table",value=st.session_state.supabase_table
        )
        st.session_state.memory_id=st.text_input(
            "Memory ID",value=st.session_state.memory_id,
            help="Use one stable ID for this app, e.g. wichita-parts."
        )
        c1,c2,c3=st.columns(3)
        if c1.button("SAVE MEMORY NOW"):
            try:
                save_local_memory()
                if cloud_memory_enabled():
                    ok,msg=save_cloud_memory()
                    st.success(msg)
                else:
                    st.success("Local memory saved. Add Supabase settings for restart-proof cloud memory.")
                st.session_state["_last_memory_hash"]=_memory_hash()
            except Exception as e:
                st.error(str(e))
        if c2.button("LOAD MEMORY NOW"):
            try:
                if cloud_memory_enabled():
                    ok,msg=load_cloud_memory()
                else:
                    ok,msg=load_local_memory()
                st.success(msg) if ok else st.warning(msg)
            except Exception as e:
                st.error(str(e))
        if c3.button("TEST CLOUD MEMORY"):
            try:
                ok,msg=save_cloud_memory()
                st.success("Cloud memory connection works.")
            except Exception as e:
                st.error(f"Cloud memory test failed: {e}")

        st.caption("For Streamlit Secrets you can use:")
        st.code(
            'SUPABASE_URL = "https://xxxxx.supabase.co"\n'
            'SUPABASE_KEY = "your-key"\n'
            'SUPABASE_TABLE = "app_memory"\n'
            'SUPABASE_MEMORY_ID = "wichita-parts"',
            language="toml"
        )
        st.caption("Supabase table: id text PRIMARY KEY, payload jsonb, updated_at timestamptz.")

    with setup_tabs[3]:
        if st.button("💲 SYNC WICHITA LKQ PART PRICES"):
            try:
                st.session_state.live_prices=parse_price_list()
                st.success(f"Loaded {len(st.session_state.live_prices)} price rows.")
            except Exception as e:
                st.error(str(e))
        if st.session_state.live_prices:
            st.dataframe(pd.DataFrame([{"part":k,**v} for k,v in st.session_state.live_prices.items()]),
                         use_container_width=True,hide_index=True)

    with setup_tabs[4]:
        if len(st.session_state.inventory):
            vi=st.selectbox("Donor vehicle",list(st.session_state.inventory.index),
                format_func=lambda i:f"{st.session_state.inventory.loc[i,'year']} {st.session_state.inventory.loc[i,'make']} {st.session_state.inventory.loc[i,'model']} — Stock {st.session_state.inventory.loc[i,'stock_number']}",
                key="impact_vehicle")
            stock=str(st.session_state.inventory.loc[vi,"stock_number"])
            current=st.session_state.impact_map.get(stock,{})
            locs=["None","Front","Rear","Left","Right","Flood","Fire"]
            sevs=["None","Light","Moderate","Heavy"]
            loc=st.selectbox("Impact location",locs,index=locs.index(current.get("location","None")) if current.get("location","None") in locs else 0)
            sev=st.selectbox("Severity",sevs,index=sevs.index(current.get("severity","None")) if current.get("severity","None") in sevs else 0)
            if st.button("SAVE IMPACT"):
                st.session_state.impact_map[stock]={"location":loc,"severity":sev}
                st.success("Impact risk saved and will affect part scores.")

    with setup_tabs[5]:
        st.session_state.ebay_fee=st.slider("eBay fee assumption",0.0,.25,st.session_state.ebay_fee,.01)
        st.session_state.ebay_quick_pct=st.slider("eBay quick-sale % of market",.60,1.0,st.session_state.ebay_quick_pct,.01)
        st.session_state.local_fee=st.slider("Local selling fee",0.0,.15,st.session_state.local_fee,.01)
        st.session_state.local_price_pct=st.slider("Local price % of internet market",.50,1.0,st.session_state.local_price_pct,.01)
        st.session_state.travel=st.number_input("Travel allocated per part",0.0,200.0,float(st.session_state.travel),5.0)
        st.session_state.packing=st.number_input("Packing materials per shipped part",0.0,100.0,float(st.session_state.packing),1.0)

        st.markdown("#### Shipping estimator")
        part=st.selectbox("Part",PARTS.part.tolist(),key="ship_part")
        p=PARTS[PARTS.part.eq(part)].iloc[0]
        w=st.number_input("Weight lb",1.0,500.0,float(p.weight),1.0)
        L=st.number_input("Length in",1.0,100.0,float(p.length),1.0)
        W=st.number_input("Width in",1.0,100.0,float(p.width),1.0)
        H=st.number_input("Height in",1.0,100.0,float(p.height),1.0)
        est,billed=shipping_estimate(w,L,W,H)
        st.metric("Estimated shipping reference",money(est))
        st.caption(f"Estimated billed weight: {billed} lb. This is a planning estimate, not a carrier quote.")

    with setup_tabs[6]:
        txt=st.text_area("Watch keywords, one per line",value="\n".join(st.session_state.watch_keywords))
        st.session_state.watch_keywords=[x.strip() for x in txt.splitlines() if x.strip()]
        st.session_state.high_value_alert=st.number_input("High-value car alert threshold (top-5 profit)",0.0,10000.0,float(st.session_state.high_value_alert),50.0)

    with setup_tabs[7]:
        payload=json.dumps(backup_payload(),indent=2,default=str)
        st.download_button("⬇️ DOWNLOAD FULL V6 BUSINESS BACKUP",payload.encode(),"wichita_scanner_v6_backup.json","application/json")
        bup=st.file_uploader("Restore V6 backup",type=["json"],key="v6backup")
        if bup and st.button("RESTORE BACKUP"):
            try:
                restore_payload(json.load(bup))
                st.success("Backup restored. Refresh/reopen tabs to see restored data.")
            except Exception as e:
                st.error(str(e))

        st.markdown("#### Templates")
        sold_template=pd.DataFrame([
            [2020,"Ford","F-150","LED Headlight Assembly","HL3Z-13008-AA",425,"2026-08-15"],
            [2020,"Ford","F-150","LED Headlight Assembly","HL3Z-13008-AA",475,"2026-08-28"],
        ],columns=["year","make","model","part","oem_part_number","sold_price","sold_date"])
        inter_template=pd.DataFrame([
            ["HL3Z-13008-AA","LED Headlight Assembly",2018,2020,"Ford","F-150","LED/adaptive verify"],
            ["HL3Z-13008-AA","LED Headlight Assembly",2018,2020,"Ford","Expedition","Verify connector/options"],
        ],columns=["oem_part_number","part","year_from","year_to","make","model","notes"])
        st.download_button("Sold comps template",sold_template.to_csv(index=False).encode(),"sold_comps_template.csv","text/csv")
        st.download_button("Interchange template",inter_template.to_csv(index=False).encode(),"interchange_template.csv","text/csv")

st.markdown("---")
st.caption("V7 is a decision aid, not a guarantee. Verify physical condition, exact OEM number/interchange, programming requirements, shipping restrictions, yard price/core/fees and current marketplace rules before buying. Internet MARKET values may be active asking prices unless sold comps are imported.")

# Persist changed state after each Streamlit rerun.
try:
    autosave_memory_if_changed()
except Exception:
    pass
