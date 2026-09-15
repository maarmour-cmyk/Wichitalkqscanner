
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
    page_title="Wichita Parts Profit Scanner V8.8",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
:root{
  --accent:#ffb000;
  --accent2:#ff7a00;
  --panel:rgba(21,25,31,.88);
  --panel2:rgba(30,35,43,.82);
  --line:rgba(255,255,255,.09);
  --muted:#9ca8b6;
  --good:#45d483;
  --warn:#ffca54;
}
.stApp{
  background:
    radial-gradient(circle at 15% 0%, rgba(255,176,0,.09), transparent 28rem),
    radial-gradient(circle at 95% 18%, rgba(62,123,250,.08), transparent 25rem),
    #0d1015;
}
.block-container{padding-top:.45rem;padding-bottom:5rem;max-width:1220px}
h1,h2,h3{letter-spacing:-.02em}
h1{font-size:1.48rem!important;line-height:1.08}
h2{font-size:1.22rem!important}
h3{font-size:1.04rem!important}
p,div,span{font-synthesis-weight:none}
input,textarea{font-size:16px!important}
div.stButton>button{
  width:100%;min-height:48px;font-size:.98rem;font-weight:760;border-radius:12px;
  border:1px solid rgba(255,176,0,.22);
}
div.stButton>button[kind="primary"]{
  background:linear-gradient(135deg,#ffb000,#ff7a00);
  color:#111;border:none;
}
div[data-testid="stMetric"]{
  background:linear-gradient(145deg,rgba(31,36,44,.88),rgba(20,24,30,.88));
  border:1px solid var(--line);border-radius:14px;padding:.62rem .72rem;
  box-shadow:0 8px 30px rgba(0,0,0,.12);
}
div[data-testid="stMetricLabel"]{color:var(--muted)}
div[role="radiogroup"]{
  gap:.32rem;flex-wrap:wrap;
}
div[role="radiogroup"] label{
  background:rgba(30,35,43,.78);border:1px solid var(--line);
  border-radius:999px;padding:.26rem .54rem;
}
.card{
  background:linear-gradient(145deg,rgba(30,35,43,.92),rgba(19,23,29,.92));
  border:1px solid var(--line);border-radius:16px;padding:.86rem;margin:.58rem 0;
  box-shadow:0 10px 32px rgba(0,0,0,.16);
}
.plan-card{
  background:linear-gradient(145deg,rgba(35,40,48,.96),rgba(20,24,30,.96));
  border:1px solid rgba(255,176,0,.22);border-radius:17px;padding:.9rem;margin:.65rem 0;
}
.partrow{padding:.48rem 0;border-top:1px solid rgba(255,255,255,.07)}
.small{color:var(--muted);font-size:.85rem}
.alert{
  background:rgba(255,176,0,.08);border:1px solid rgba(255,176,0,.28);
  border-radius:13px;padding:.7rem;margin:.45rem 0
}
.hero{
  background:
    linear-gradient(135deg,rgba(255,176,0,.12),rgba(255,122,0,.035) 45%,rgba(61,106,190,.08)),
    rgba(20,24,30,.86);
  border:1px solid rgba(255,176,0,.16);border-radius:18px;padding:1rem 1.05rem;
  margin:.15rem 0 .75rem 0;box-shadow:0 12px 40px rgba(0,0,0,.17);
}
.hero-title{font-size:1.34rem;font-weight:820;letter-spacing:-.03em}
.hero-sub{color:var(--muted);font-size:.88rem;margin-top:.18rem}
.chips{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.65rem}
.chip{
  font-size:.78rem;padding:.28rem .48rem;border-radius:999px;
  border:1px solid var(--line);background:rgba(255,255,255,.035)
}
.badge-good{color:#65e59a}.badge-warn{color:#ffd66f}.badge-muted{color:#9ca8b6}
.kpi{font-size:1.14rem;font-weight:820}
hr{border-color:rgba(255,255,255,.07)!important}
[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;overflow:hidden}
@media(max-width:700px){
  .block-container{padding-left:.56rem;padding-right:.56rem}
  h1{font-size:1.22rem!important}
  .hero{padding:.82rem}
  .hero-title{font-size:1.18rem}
  div[data-testid="stMetric"]{padding:.5rem}
}
</style>
""", unsafe_allow_html=True)

YARD_NAME="Pick Your Part - Wichita"
YARD_ADDRESS="700 E 21st St N, Wichita, KS 67214"
INV_URL="https://www.pyp.com/inventory/wichita-1246/"
PRICE_URL="https://www.pyp.com/locations/LKQ_Pick_Your_Part_-_Wichita-246/prices/"
PRICE_URLS=[
    PRICE_URL,
    "https://www.pyp.com/prices/wichita-1246/",
    "https://publicsiteqc.pyp.com/prices/wichita-1246/",
]

# Last-resort Wichita snapshot for the categories this app actually recommends.
# Live page rows override these whenever LKQ/PYP returns the rendered table.
# Verified against the public Wichita price page on 2026-09-14.
WICHITA_PRICE_SNAPSHOT={
    "A/C COMPRESSOR":{"total":93.20,"price":64.00,"core":10.00,"guarantee":19.20},
    "ALTERNATOR":{"total":62.65,"price":40.50,"core":10.00,"guarantee":12.15},
    "AMPLIFIER":{"total":37.05,"price":28.50,"core":0.0,"guarantee":8.55},
    "ANTI LOCK BRAKE (ABS UNIT)":{"total":88.00,"price":60.00,"core":10.00,"guarantee":18.00},
    "AXLE ASSEMBLY, REAR CAR/LESS BRAKES":{"total":292.60,"price":202.00,"core":30.00,"guarantee":60.60},
    "CHASSIS CONTROL MODULE":{"total":62.85,"price":44.50,"core":5.00,"guarantee":13.35},
    "DECKLID/TAILGATE (BARE)":{"total":85.40,"price":58.00,"core":10.00,"guarantee":17.40},
    "ENGINE CONTROL MODULE":{"total":89.50,"price":65.00,"core":5.00,"guarantee":19.50},
    "FUEL INJECTION PUMP (DIESEL)":{"total":99.38,"price":72.60,"core":5.00,"guarantee":21.78},
    "INSTRUMENT CLUSTER":{"total":50.70,"price":39.00,"core":0.0,"guarantee":11.70},
    "MIRROR (SIDE VIEW)":{"total":42.25,"price":32.50,"core":0.0,"guarantee":9.75},
    "RADIO WITH DISPLAY":{"total":57.85,"price":44.50,"core":0.0,"guarantee":13.35},
    "STARTER":{"total":59.40,"price":38.00,"core":10.00,"guarantee":11.40},
    "TAILLIGHT (QUARTER MOUNTED)":{"total":37.70,"price":29.00,"core":0.0,"guarantee":8.70},
    "TEMPERATURE CONTROL":{"total":40.30,"price":31.00,"core":0.0,"guarantee":9.30},
    "TRANSFER CASE":{"total":235.40,"price":158.00,"core":30.00,"guarantee":47.40},
    "TRANSMISSION":{"total":204.20,"price":134.00,"core":30.00,"guarantee":40.20},
    "TURBO/ SUPERCHARGER":{"total":141.30,"price":101.00,"core":10.00,"guarantee":30.30},
}


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
"LED Tail Light Assembly":"TAILLIGHT (QUARTER MOUNTED)",
"OEM Infotainment / Radio":"RADIO WITH DISPLAY",
"Instrument Cluster":"INSTRUMENT CLUSTER",
"ECU / ECM / PCM":"ENGINE CONTROL MODULE",
"Body Control Module":"CHASSIS CONTROL MODULE",
"ABS Module / Pump":"ANTI LOCK BRAKE (ABS UNIT)",
"Power Folding Mirror":"MIRROR (SIDE VIEW)",
"Amplifier":"AMPLIFIER",
"Climate Control Panel":"TEMPERATURE CONTROL",
"Alternator":"ALTERNATOR",
"Starter":"STARTER",
"A/C Compressor":"A/C COMPRESSOR",
"Turbocharger":"TURBO/ SUPERCHARGER",
"Diesel High Pressure Fuel Pump":"FUEL INJECTION PUMP (DIESEL)",
"Transmission":"TRANSMISSION",
"Transfer Case":"TRANSFER CASE",
"Rear Differential":"AXLE ASSEMBLY, REAR CAR/LESS BRAKES",
"Tailgate / Liftgate":"DECKLID/TAILGATE (BARE)",
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


_HTTP_LOCAL=threading.local()

def fast_http_session():
    """
    One persistent requests.Session per worker thread.
    Reuses TLS/TCP connections during parallel market searches.
    """
    sess=getattr(_HTTP_LOCAL,"session",None)
    if sess is None:
        sess=requests.Session()
        sess.headers.update({
            "User-Agent":"Mozilla/5.0 (compatible; WichitaPartsScanner/8.1)",
            "Accept-Language":"en-US,en;q=0.9",
            "Connection":"keep-alive",
        })
        _HTTP_LOCAL.session=sess
    return sess

def row_num(x):
    m=re.search(r"\d+",str(x))
    return int(m.group()) if m else 9999


def inventory_age_days(value):
    """Days since the donor became available, when the yard exposes the date."""
    if value is None or str(value).strip()=="":
        return 999
    for fmt in ("%m/%d/%Y","%Y-%m-%d"):
        try:
            return max(0,(date.today()-datetime.strptime(str(value).strip(),fmt).date()).days)
        except Exception:
            pass
    return 999

def missing_learning_multiplier(part):
    events=st.session_state.get("missing_events",[])
    n=sum(1 for e in events if norm(e.get("part",""))==norm(part))
    if n<=0:
        return 1.0
    return max(.72,1.0-.055*min(n,5))

def presence_probability(vehicle,p):
    """
    Heuristic probability that a desirable part is still on the donor.
    New arrivals and bulky/slow-removal parts retain higher probability.
    User 'missing' feedback gradually reduces the category estimate.
    """
    age=inventory_age_days(vehicle.get("available_date",""))
    if age==999:
        base=.68
    else:
        demand=float(p.demand_score)
        size_factor=float(np.clip(float(p.weight)/75.0,.15,1.0))
        removal_factor=float(np.clip(float(p.pull_minutes)/90.0,.15,1.0))
        # Small, fast, high-demand pieces tend to disappear sooner.
        turnover=.010 + .018*demand + .010*(1-size_factor) + .008*(1-removal_factor)
        base=float(np.exp(-turnover*age))
        base=float(np.clip(base,.12,.98))
    base*=missing_learning_multiplier(p.part)
    return float(np.clip(base,.08,.98)),age

def recommendation_label(score,confidence,expected_profit,market_sources,present_pct):
    if confidence>=70 and market_sources>=2 and expected_profit>=175 and score>=78 and present_pct>=55:
        return "🔥 STRONG"
    if confidence>=45 and expected_profit>=100 and score>=65 and present_pct>=35:
        return "✅ GOOD"
    if expected_profit>50 and score>=50:
        return "👀 WATCH"
    return "🔎 RESEARCH"

def unique_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"

# ---------------- WICHITA INVENTORY ----------------
MULTIWORD_MAKES=[
    "LAND ROVER","ALFA ROMEO","ASTON MARTIN","MERCEDES-BENZ",
    "ROLLS-ROYCE","AM GENERAL"
]

def _split_vehicle_title(title):
    clean=re.sub(r"\s+"," ",str(title).replace("\xa0"," ")).strip()
    m=re.match(r"^(19\d{2}|20\d{2})\s+(.+)$",clean,re.I)
    if not m:
        return None
    year=int(m.group(1))
    rest=m.group(2).strip()
    upper=rest.upper()
    make=""
    model=""
    for candidate in sorted(MULTIWORD_MAKES,key=len,reverse=True):
        if upper==candidate or upper.startswith(candidate+" "):
            make=candidate
            model=rest[len(candidate):].strip()
            break
    if not make:
        parts=rest.split(None,1)
        make=parts[0].upper() if parts else ""
        model=parts[1].upper() if len(parts)>1 else ""
    return year,make.upper(),model.upper()

def _extract_inventory_card(title,blob,source_url):
    parsed=_split_vehicle_title(title)
    if not parsed:
        return None
    year,make,model=parsed
    blob=re.sub(r"\s+"," ",str(blob).replace("\xa0"," ")).strip()

    stock=re.search(r"\b(1246-\d+)\b",blob,re.I)
    vin=re.search(r"\bVIN:?\s*([A-HJ-NPR-Z0-9]{17})\b",blob,re.I)
    if not stock and not vin:
        return None

    cm=re.search(r"([A-Za-z][A-Za-z ]{0,20}?)\s*[·•]\s*1246-\d+",blob,re.I)
    sec=re.search(r"\bSection\s*:?\s*([^|]*?)(?=\s*\|\s*Row|\s+Row\b)",blob,re.I)
    rr=re.search(r"\bRow\s*:?\s*([^|]*?)(?=\s*\|\s*Space|\s+Space\b)",blob,re.I)
    sp=re.search(r"\bSpace\s*:?\s*([A-Za-z0-9-]*)",blob,re.I)
    av=re.search(r"\bAvailable\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})",blob,re.I)

    section=(sec.group(1).strip().upper() if sec else "")
    row=(rr.group(1).strip() if rr else "")
    space=(sp.group(1).strip() if sp else "")
    available=(av.group(1) if av else "")
    age=inventory_age_days(available)

    if section or row or space:
        location=" · ".join(
            [x for x in [
                f"Section {section}" if section else "",
                f"Row {row}" if row else "",
                f"Space {space}" if space else "",
            ] if x]
        )
    else:
        location="Location pending"

    return {
        "year":year,
        "make":make,
        "model":model,
        "color":cm.group(1).strip().title() if cm else "",
        "stock_number":stock.group(1) if stock else "",
        "vin":vin.group(1).upper() if vin else "",
        "section":section,
        "yard_row":row,
        "space":space,
        "yard_location":location,
        "available_date":available,
        "days_in_yard":None if age==999 else int(age),
        "source_url":source_url,
    }

def parse_inventory_html(html,source_url):
    soup=BeautifulSoup(html,"html.parser")
    rows=[]
    seen=set()

    # Preferred path: current PYP cards have a year/make/model heading.
    for h in soup.find_all(["h2","h3","h4"]):
        title=re.sub(r"\s+"," ",h.get_text(" ",strip=True).replace("\xa0"," ")).strip()
        if not re.match(r"^(?:19\d{2}|20\d{2})\s+",title):
            continue

        # Ascend to the smallest useful card-like container.
        node=h
        best=""
        for _ in range(7):
            if node is None:
                break
            candidate=re.sub(r"\s+"," ",node.get_text(" ",strip=True).replace("\xa0"," ")).strip()
            if ("1246-" in candidate and "VIN" in candidate and "Available" in candidate):
                best=candidate
                if len(candidate)<1100:
                    break
            node=node.parent
        if not best:
            continue

        row=_extract_inventory_card(title,best,source_url)
        if row:
            key=row["stock_number"] or row["vin"]
            if key and key not in seen:
                seen.add(key)
                rows.append(row)

    # Fallback for markup changes: parse the flattened text around each stock number.
    if not rows:
        flat=re.sub(r"\s+"," ",soup.get_text(" ",strip=True).replace("\xa0"," ")).strip()
        stocks=list(re.finditer(r"\b1246-\d+\b",flat,re.I))
        for sm in stocks:
            left=max(0,sm.start()-180)
            right=min(len(flat),sm.end()+520)
            blob=flat[left:right]

            # Find the last year before this stock number; everything through the
            # color marker is the vehicle title.
            prefix=flat[left:sm.start()]
            title_matches=list(re.finditer(
                r"\b(19\d{2}|20\d{2})\s+(.+?)(?=\s+[A-Za-z][A-Za-z ]{0,20}\s*[·•]\s*$)",
                prefix,re.I
            ))
            if not title_matches:
                continue
            tm=title_matches[-1]
            title=f"{tm.group(1)} {tm.group(2)}"
            row=_extract_inventory_card(title,blob,source_url)
            if row:
                key=row["stock_number"] or row["vin"]
                if key and key not in seen:
                    seen.add(key)
                    rows.append(row)

    cols=[
        "year","make","model","color","stock_number","vin","section","yard_row",
        "space","yard_location","available_date","days_in_yard","source_url"
    ]
    return pd.DataFrame(rows,columns=cols)

def sync_inventory(max_pages=75):
    s=http_session()
    rows=[]
    seen=set()
    pages=0
    empty=0
    for page in range(1,max_pages+1):
        url=INV_URL if page==1 else f"{INV_URL}?page={page}"
        r=s.get(url,timeout=20)
        r.raise_for_status()
        df=parse_inventory_html(r.text,url)
        if len(df)==0:
            empty+=1
            if empty>=2:
                break
            continue
        empty=0
        new=0
        for _,x in df.iterrows():
            key=x["stock_number"] or x["vin"]
            if key and key not in seen:
                seen.add(key)
                rows.append(x.to_dict())
                new+=1
        pages=page
        if new==0:
            break
        time.sleep(.05)

    out=pd.DataFrame(rows)
    if len(out):
        out["days_in_yard"]=out["available_date"].map(
            lambda x: None if inventory_age_days(x)==999 else int(inventory_age_days(x))
        )
        out["yard_location"]=out.apply(
            lambda r: " · ".join(
                [x for x in [
                    f"Section {str(r.get('section','')).strip()}" if str(r.get("section","")).strip() else "",
                    f"Row {str(r.get('yard_row','')).strip()}" if str(r.get("yard_row","")).strip() else "",
                    f"Space {str(r.get('space','')).strip()}" if str(r.get("space","")).strip() else "",
                ] if x]
            ) or "Location pending",
            axis=1
        )
    return out,pages

def _money_cell(v):
    m=re.search(r"\$([\d,]+(?:\.\d+)?)",str(v))
    return float(m.group(1).replace(",","")) if m else np.nan

def _parse_price_html(html):
    soup=BeautifulSoup(html,"html.parser")
    prices={}

    # Current/legacy server-rendered table rows.
    for tr in soup.find_all("tr"):
        cells=[c.get_text(" ",strip=True) for c in tr.find_all(["th","td"])]
        if len(cells)<3:
            continue
        name=cells[0].strip().upper()
        if not name or name in {"PART","TOTAL PRICE"}:
            continue
        vals=[_money_cell(c) for c in cells[1:5]]
        if any(pd.notna(v) for v in vals):
            prices[name]={
                "total":vals[0] if len(vals)>0 else np.nan,
                "price":vals[1] if len(vals)>1 else np.nan,
                "core":vals[2] if len(vals)>2 else np.nan,
                "guarantee":vals[3] if len(vals)>3 else np.nan,
            }

    # Some versions place rendered row text outside a normal <tr>.
    if len(prices)<5:
        flat=re.sub(r"\s+"," ",soup.get_text(" ",strip=True).replace("\xa0"," ")).strip()
        row_pat=re.compile(
            r"([A-Z][A-Z0-9 /(),.&'\-]{2,85}?)\s+"
            r"\$([\d,]+(?:\.\d+)?)\s+"
            r"\$([\d,]+(?:\.\d+)?)\s+"
            r"(?:\$([\d,]+(?:\.\d+)?)|-)\s+"
            r"(?:\$([\d,]+(?:\.\d+)?)|-)",
            re.I
        )
        for m in row_pat.finditer(flat):
            name=re.sub(r"\s+"," ",m.group(1)).strip().upper()
            # Trim common heading text if regex began too early.
            for marker in ["GUARANTEE ","PRICE ","PARTS PRICES "]:
                if marker in name:
                    name=name.split(marker)[-1].strip()
            if len(name)>90:
                continue
            prices[name]={
                "total":float(m.group(2).replace(",","")),
                "price":float(m.group(3).replace(",","")),
                "core":float(m.group(4).replace(",","")) if m.group(4) else 0.0,
                "guarantee":float(m.group(5).replace(",","")) if m.group(5) else 0.0,
            }
    return prices

def parse_price_list():
    """
    Try all known public Wichita PYP price routes.
    If LKQ serves an unrendered JS shell, fill the app's relevant categories
    from the current verified Wichita snapshot instead of returning zero rows.
    """
    live={}
    used_url=""
    errors=[]
    s=http_session()

    for url in PRICE_URLS:
        try:
            r=s.get(url,timeout=18)
            r.raise_for_status()
            parsed=_parse_price_html(r.text)
            if len(parsed)>len(live):
                live=parsed
                used_url=url
            if len(live)>=20:
                break
        except Exception as e:
            errors.append(f"{url}: {e}")

    # Live rows win; snapshot only fills missing app-relevant categories.
    merged={k:dict(v) for k,v in WICHITA_PRICE_SNAPSHOT.items()}
    merged.update(live)

    if len(live):
        st.session_state["lkq_price_source"]=f"Live Wichita PYP page · {len(live)} live rows"
        st.session_state["lkq_price_url"]=used_url
    else:
        st.session_state["lkq_price_source"]="Wichita verified fallback snapshot · 9/14/2026"
        st.session_state["lkq_price_url"]=PRICE_URL
    st.session_state["lkq_price_errors"]=errors[-3:]

    return merged

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
    """Persistent Streamlit Secrets first, temporary session values second."""
    try:
        cid=str(st.secrets.get("EBAY_CLIENT_ID","") or "").strip()
        secret=str(st.secrets.get("EBAY_CLIENT_SECRET","") or "").strip()
    except Exception:
        cid=secret=""
    if not cid:
        cid=str(st.session_state.get("ebay_client_id","") or "").strip()
    if not secret:
        secret=str(st.session_state.get("ebay_client_secret","") or "").strip()
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
    r=fast_http_session().get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={"Authorization":f"Bearer {token}","X-EBAY-C-MARKETPLACE-ID":"EBAY_US"},
        params={"q":query,"limit":min(limit,50),"filter":"conditions:{USED}"},
        timeout=(4,12),
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
    r=fast_http_session().get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={"Authorization":f"Bearer {token}","X-EBAY-C-MARKETPLACE-ID":"EBAY_US"},
        params={"q":query,"limit":min(limit,50),"filter":"conditions:{USED}"},
        timeout=(4,12),
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
    """
    Resolve SerpAPI credentials in this priority:
    1) Streamlit Community Cloud Secrets (persistent; recommended)
    2) current session field (temporary fallback)
    """
    try:
        secret=str(st.secrets.get("SERPAPI_KEY","") or "").strip()
        if secret:
            return secret
    except Exception:
        pass
    return str(st.session_state.get("serpapi_api_key","") or "").strip()


def serpapi_shopping_comps(query,key,limit=20):
    """
    Google Shopping Light via SerpAPI.
    This lighter engine is optimized for lower latency while retaining the
    structured price / seller / second-hand fields needed by the scanner.
    """
    s=fast_http_session()
    r=s.get(
        "https://serpapi.com/search.json",
        params={
            "engine":"google_shopping_light",
            "q":query,
            "location":"Wichita, Kansas, United States",
            "gl":"us","hl":"en",
            "device":"mobile",
            "api_key":key,
        },
        timeout=(4,12),
    )
    r.raise_for_status()
    payload=r.json()
    results=list(payload.get("shopping_results",[]) or [])

    # Light API can also return inline shopping results. Use them if the
    # regular shopping array is sparse, without issuing a second API call.
    if len(results)<6:
        results.extend(payload.get("inline_shopping_results",[]) or [])

    rows=[]
    seen=set()
    for x in results:
        if len(rows)>=int(limit):
            break
        price=x.get("extracted_price")
        if price is None:
            m=re.search(r"\\$([\\d,]+(?:\\.\\d+)?)",str(x.get("price","")))
            price=float(m.group(1).replace(",","")) if m else None
        if price is None:
            continue

        title=str(x.get("title","")).strip()
        cond=str(x.get("second_hand_condition","") or "").strip()
        source=str(x.get("source","Google Shopping")).strip()
        dedupe=(norm(title),round(float(price),2),norm(source))
        if dedupe in seen:
            continue
        seen.add(dedupe)

        blob=(title+" "+cond).lower()
        if any(w in blob for w in ["brand new","new sealed","new oem"]) and not any(
            w in blob for w in ["used","pre-owned","preowned","second hand","refurbished"]
        ):
            continue

        rows.append({
            "title":title,
            "price":float(price),
            "shipping":0.0,
            "landed":float(price),
            "condition":cond or "shopping result",
            "url":x.get("product_link","") or x.get("link",""),
            "source":source or "Google Shopping",
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
    present_prob,age_days=presence_probability(vehicle,p)
    expected_profit=best_profit*present_prob

    avoid = norm(p.part) in {norm(x) for x in st.session_state.avoid_parts}
    learned = learned_part_warning(p.part)
    if avoid: best_profit-=250

    pph=best_profit/max(float(p.pull_minutes)/60,.1)
    roi=best_profit/max(get_yard_cost(p)+st.session_state.travel,1)
    liquidity=float(np.clip((100-m["days_to_sell"])/100,0,1))
    score=(
        np.clip(expected_profit/350*30,0,30)+
        np.clip(pph/150*20,0,20)+
        np.clip(roi/3*12,0,12)+
        float(p.demand_score)*10+
        liquidity*10+
        (m["confidence"]/100)*8+
        app*5+
        m["fit_bonus"]*20+
        present_prob*7
    )
    score-=m["risk_discount"]*30
    if learned: score-=8
    if avoid: score-=25

    channel=channels["best_channel"]
    target=channels["local_price"] if channel=="LOCAL" else channels["ebay_price"]
    ship=0 if channel=="LOCAL" else channels["ebay_ship"]

    final_score=round(float(np.clip(score,0,100)),1)
    present_pct=round(present_prob*100,0)
    rec=recommendation_label(final_score,m["confidence"],expected_profit,m["market_sources"],present_pct)
    return {
        "PART":p.part,"OEM":m["pn"],"BUY":round(get_yard_cost(p),2),
        "MARKET":round(m["market"],2),"TARGET":round(target,2),
        "SHIP":round(ship,2),"PROFIT":round(best_profit,2),
        "EXPECTED_PROFIT":round(expected_profit,2),
        "$/HR":round(pph,2),"ROI":round(roi,2),"MIN":int(p.pull_minutes),
        "SCORE":final_score,"CHANNEL":channel,
        "AGE_DAYS":age_days,"PRESENT_PCT":present_pct,"REC":rec,
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
        "vehicle_id","CAR","STOCK","VIN","SECTION","ROW","SPACE","YARD_LOCATION","AVAILABLE","DAYS_YARD","NEW",
        "PART","OEM","BUY","MARKET","TARGET","SHIP","PROFIT","EXPECTED_PROFIT","$/HR","ROI","MIN",
        "SCORE","CHANNEL","AGE_DAYS","PRESENT_PCT","REC","SELL_THROUGH","DAYS","SELL_SOURCE","VALUE_SOURCE",
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
                "SPACE":vd.get("space",""),
                "YARD_LOCATION":vd.get("yard_location","") or "Location pending",
                "AVAILABLE":vd.get("available_date",""),
                "DAYS_YARD":None if inventory_age_days(vd.get("available_date",""))==999 else int(inventory_age_days(vd.get("available_date",""))),
                "NEW":"🆕" if bool(vd.get("is_new",False)) else "",
                **o
            })
    if not rows:
        return pd.DataFrame(columns=expected_cols)
    return pd.DataFrame(rows).reindex(columns=expected_cols)


def invalidate_rankings():
    """Mark expensive donor/part rankings stale."""
    st.session_state["ranking_revision"]=int(st.session_state.get("ranking_revision",0))+1
    st.session_state["opportunity_cache"]={}

def _ranking_settings_signature():
    return (
        round(float(st.session_state.get("ebay_fee",.13)),4),
        round(float(st.session_state.get("local_fee",0.0)),4),
        round(float(st.session_state.get("ebay_quick_pct",.88)),4),
        round(float(st.session_state.get("local_price_pct",.82)),4),
        round(float(st.session_state.get("travel",10.0)),2),
        round(float(st.session_state.get("packing",5.0)),2),
        bool(st.session_state.get("market_only",True)),
        tuple(sorted(str(x) for x in st.session_state.get("avoid_parts",set()))),
    )

def get_opportunities_cached(inv,allow_unpriced=False):
    """
    Full-yard scoring is expensive.

    V8.7 intentionally keeps BOTH current variants in memory:
      - market-priced-only
      - all candidates / allow_unpriced

    Older builds cleared the other variant on every miss, which meant moving
    between BEST CARS and MARKET could force a complete yard rescore.
    """
    if inv is None or len(inv)==0:
        return all_opportunities(inv,allow_unpriced=allow_unpriced)

    cache=st.session_state.setdefault("opportunity_cache",{})
    revision=int(st.session_state.get("ranking_revision",0))
    signature=_ranking_settings_signature()
    key=(
        bool(allow_unpriced),
        revision,
        str(st.session_state.get("last_sync","")),
        len(inv),
        signature,
    )

    if key not in cache:
        # Remove stale generations, but preserve the priced/unpriced pair for
        # the current revision/signature.
        stale=[]
        for k in list(cache.keys()):
            try:
                same_generation=(
                    k[1]==revision and
                    k[2]==str(st.session_state.get("last_sync","")) and
                    k[3]==len(inv) and
                    k[4]==signature
                )
            except Exception:
                same_generation=False
            if not same_generation:
                stale.append(k)
        for k in stale:
            cache.pop(k,None)

        cache[key]=all_opportunities(inv,allow_unpriced=allow_unpriced)

    return cache[key]


# ---------------- TODAY'S PLAN OPTIMIZER ----------------
def build_today_plan(opp,budget,hours,max_cars=8,parts_per_car=4,min_conf=35,min_presence=25,min_profit=75):
    """
    Two-constraint greedy optimizer.
    Objective is presence-adjusted expected profit while respecting:
    - cash budget
    - available time
    - donor count
    - max parts per donor
    A one-time donor overhead rewards pulling multiple good parts from the same car.
    """
    if opp is None or len(opp)==0:
        return pd.DataFrame()

    x=opp.copy()
    x=x[
        (x["PROFIT"]>=float(min_profit)) &
        (x["CONF"]>=float(min_conf)) &
        (x["PRESENT_PCT"]>=float(min_presence))
    ].copy()
    if not len(x):
        return pd.DataFrame()

    budget=float(budget)
    total_minutes=float(hours)*60
    if budget<=0 or total_minutes<=0:
        return pd.DataFrame()

    selected=[]
    used_budget=0.0
    used_minutes=0.0
    donor_parts={}
    donor_set=set()
    remaining=x.sort_values(["EXPECTED_PROFIT","SCORE"],ascending=False).copy()

    while len(remaining):
        best_idx=None
        best_utility=-1
        best_time=0

        for idx,r in remaining.head(180).iterrows():
            stock=str(r["STOCK"])
            new_donor=stock not in donor_set
            if new_donor and len(donor_set)>=int(max_cars):
                continue
            if donor_parts.get(stock,0)>=int(parts_per_car):
                continue

            # Cost uses actual yard buy estimate.
            cost=max(float(r["BUY"]),0)
            donor_overhead=7 if new_donor else 0
            time_need=float(r["MIN"])+donor_overhead
            if used_budget+cost>budget or used_minutes+time_need>total_minutes:
                continue

            # Favor expected profit, confidence, presence, and reusing an existing donor.
            exp_profit=max(float(r["EXPECTED_PROFIT"]),0)
            conf=float(r["CONF"])/100
            presence=float(r["PRESENT_PCT"])/100
            reuse_bonus=1.12 if not new_donor else 1.0
            cost_pressure=cost/max(budget,.01)
            time_pressure=time_need/max(total_minutes,.01)
            utility=(exp_profit*reuse_bonus*(.72+.18*conf+.10*presence)) / max(.18+cost_pressure+time_pressure, .05)

            if utility>best_utility:
                best_utility=utility
                best_idx=idx
                best_time=time_need

        if best_idx is None:
            break

        r=remaining.loc[best_idx]
        stock=str(r["STOCK"])
        selected.append(r.to_dict())
        used_budget+=float(r["BUY"])
        used_minutes+=best_time
        donor_set.add(stock)
        donor_parts[stock]=donor_parts.get(stock,0)+1
        remaining=remaining.drop(index=best_idx)

    if not selected:
        return pd.DataFrame()

    plan=pd.DataFrame(selected)
    plan["PLAN_COST"]=plan["BUY"]
    plan["PLAN_TIME_MIN"]=plan["MIN"]
    plan["ROUTE_ROW"]=plan["ROW"].map(row_num)
    plan["ROUTE_SPACE"]=plan["SPACE"].map(row_num)
    plan=plan.sort_values(["SECTION","ROUTE_ROW","ROUTE_SPACE","CAR","SCORE"],
                          ascending=[True,True,True,True,False]).reset_index(drop=True)
    return plan

def donor_plan_summary(plan):
    if plan is None or len(plan)==0:
        return pd.DataFrame()
    return (plan.groupby(["CAR","STOCK","SECTION","ROW","SPACE"],dropna=False)
            .agg(
                PARTS=("PART","count"),
                AVAILABLE=("AVAILABLE","first"),
                DAYS_YARD=("DAYS_YARD","min"),
                COST=("BUY","sum"),
                PROFIT=("PROFIT","sum"),
                EXPECTED=("EXPECTED_PROFIT","sum"),
                MINUTES=("MIN","sum"),
                AVG_CONF=("CONF","mean"),
                AVG_PRESENT=("PRESENT_PCT","mean"),
            )
            .reset_index())

def listing_draft(purchase):
    car=str(purchase.get("car","")).strip()
    part=str(purchase.get("part","")).strip()
    oem=str(purchase.get("oem","")).strip()
    title=f"{car} {part} OEM {oem} Used Genuine".replace("  "," ").strip()
    title=title[:80]
    desc=(
        f"Used genuine OEM {part} removed from a {car}. "
        + (f"OEM/part number: {oem}. " if oem else "")
        + "Verify the part number, connectors, options and interchange before purchase. "
          "Condition is as pictured. Programming/calibration may be required depending on the component."
    )
    return title,desc


# ---------------- WHOLE-YARD MARKET COVERAGE ----------------
def _candidate_market_key(row):
    vehicle=st.session_state.inventory.loc[row["vehicle_id"]].to_dict()
    pn=part_number(row["STOCK"],row["PART"])
    return market_key(vehicle,row["PART"],pn)

def _candidate_attempted_fresh(row):
    """
    True when every configured live provider has already been attempted recently
    for this vehicle/part, including zero-result searches.
    """
    vehicle=st.session_state.inventory.loc[row["vehicle_id"]].to_dict()
    pn=part_number(row["STOCK"],row["PART"])
    key=market_key(vehicle,row["PART"],pn)
    entry=st.session_state.market_cache.get(key,{})
    ttl=float(st.session_state.get("market_cache_hours",48))
    cid,secret=ebay_credentials()
    skey=serpapi_key()

    checks=[]
    if cid and secret:
        checks.append(_provider_fresh(entry,"ebay",ttl) and isinstance(entry.get("ebay"),dict))
    if skey:
        checks.append(_provider_fresh(entry,"shopping",ttl) and isinstance(entry.get("shopping"),dict))
    return bool(checks) and all(checks)

def whole_yard_coverage(base=None):
    """
    Coverage = donor cars with at least one part backed by real market data.
    Exhausted = no market-priced part yet, but all currently plausible candidate
    parts have already had fresh provider attempts.
    """
    total=len(st.session_state.inventory)
    if total==0:
        return {"total":0,"covered_ids":set(),"covered":0,"remaining":0,"exhausted_ids":set(),"exhausted":0}

    priced=get_opportunities_cached(st.session_state.inventory,allow_unpriced=False)
    covered_ids=set(priced["vehicle_id"].tolist()) if len(priced) else set()

    if base is None:
        base=get_opportunities_cached(st.session_state.inventory,allow_unpriced=True)

    exhausted_ids=set()
    for vid,g in base.groupby("vehicle_id"):
        if vid in covered_ids:
            continue
        # Don't declare exhausted until several plausible parts have been attempted.
        plausible=g.sort_values(["SCORE","EXPECTED_PROFIT"],ascending=False).head(5)
        if len(plausible) and all(_candidate_attempted_fresh(r) for _,r in plausible.iterrows()):
            exhausted_ids.add(vid)

    remaining=max(0,total-len(covered_ids))
    st.session_state.market_covered_count=len(covered_ids)
    return {
        "total":total,
        "covered_ids":covered_ids,
        "covered":len(covered_ids),
        "remaining":remaining,
        "exhausted_ids":exhausted_ids,
        "exhausted":len(exhausted_ids),
    }

def whole_yard_candidate_order(base,parts_per_car=3,only_uncovered=True):
    """
    Round-robin ordering across donor cars.

    Pass 1: best unattempted candidate from each car.
    Pass 2: second-best unattempted candidate from each car.
    Pass 3: third-best, etc.

    This prevents one high-scoring donor from consuming the entire API batch.
    """
    if base is None or len(base)==0:
        return base.copy() if isinstance(base,pd.DataFrame) else pd.DataFrame()

    cov=whole_yard_coverage(base)
    covered=cov["covered_ids"]
    exhausted=cov["exhausted_ids"]

    groups={}
    donor_rank=[]
    for vid,g in base.groupby("vehicle_id"):
        if only_uncovered and vid in covered:
            continue
        if vid in exhausted:
            continue

        g=g.sort_values(["SCORE","EXPECTED_PROFIT","PRESENT_PCT"],ascending=False)
        # Prefer candidates not recently attempted. If market data already exists,
        # the donor would normally be "covered" and excluded in coverage mode.
        unattempted=[]
        for _,r in g.iterrows():
            if not _candidate_attempted_fresh(r):
                unattempted.append(r)
            if len(unattempted)>=int(parts_per_car):
                break
        if not unattempted:
            continue

        groups[vid]=unattempted
        donor_rank.append((vid,float(g.iloc[0]["SCORE"]),float(g.iloc[0]["EXPECTED_PROFIT"])))

    # Best donors first within each coverage round, but still one candidate per car
    # before moving to a second part from any donor.
    donor_rank=sorted(donor_rank,key=lambda x:(x[1],x[2]),reverse=True)

    ordered=[]
    for round_idx in range(int(parts_per_car)):
        for vid,_,__ in donor_rank:
            arr=groups.get(vid,[])
            if round_idx<len(arr):
                ordered.append(arr[round_idx].to_dict())

    if not ordered:
        return pd.DataFrame(columns=base.columns)
    return pd.DataFrame(ordered).reindex(columns=base.columns)

def deepen_yard_candidate_order(base,parts_per_car=3):
    """Round-robin candidate order across all cars, including already-covered donors."""
    return whole_yard_candidate_order(base,parts_per_car=parts_per_car,only_uncovered=False)

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
                df=ebay_used_comps_with_token(q,ebay_token,24)
                summ=summarize_comps(df)
                entry["ebay"]={**summ,"examples":df.head(4).to_dict("records") if len(df) else []}
                timestamps["ebay"]=_utc_now_iso()
                fetched.append("eBay")
            except Exception as e:
                errors.append(f"eBay | {q}: {e}")

    if skey:
        if _provider_fresh(entry,"shopping",ttl_hours) and entry.get("shopping"):
            pass
        else:
            try:
                df=serpapi_shopping_comps(q,skey,18)
                summ=summarize_comps(df)
                entry["shopping"]={**summ,"examples":df.head(4).to_dict("records") if len(df) else []}
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
    workers=max(1,min(int(st.session_state.get("market_workers",8)),12,len(subset)))

    # Reuse one eBay token for the entire batch.
    ebay_token=None
    if cid and secret:
        ebay_token=ebay_access_token(cid,secret)

    tasks=[]
    skipped=0
    seen_queries=set()
    duplicate_query_keys={}
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

        qnorm=norm(q)
        if qnorm in seen_queries:
            duplicate_query_keys.setdefault(qnorm,[]).append((key,existing))
            continue
        seen_queries.add(qnorm)
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
                qnorm=norm(entry.get("query",""))
                for dup_key,dup_existing in duplicate_query_keys.get(qnorm,[]):
                    dup_entry=dict(entry)
                    st.session_state.market_cache[dup_key]=dup_entry
                    skipped+=1
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
                    f"Market pricing: {completed} of {total} searches finished · "
                    f"{skipped} cached/shared · {workers} workers"
                )

    if fetched_queries:
        invalidate_rankings()
        mark_memory_dirty("market prices")
    return fetched_queries,skipped,errors


def fast_market_scan(cands,target_recommendations=6,max_searches=16,progress=None,status=None):
    """
    Two-stage market scan:
    1) price a small first batch
    2) rerank immediately
    3) only spend more API searches when too few priced opportunities qualify
    """
    target_recommendations=max(1,int(target_recommendations))
    max_searches=max(4,int(max_searches))
    stage1=min(max_searches, max(4,target_recommendations))
    errors=[]
    fetched_total=0
    skipped_total=0

    f,s,e=price_candidates(cands,stage1,progress,status)
    fetched_total+=f; skipped_total+=s; errors.extend(e)

    priced=get_opportunities_cached(st.session_state.inventory)
    strong=priced[
        (priced["CONF"]>=35) &
        (priced["PROFIT"]>=75)
    ] if len(priced) else priced

    if len(strong)>=target_recommendations or stage1>=max_searches:
        return fetched_total,skipped_total,errors,stage1

    stage2=min(max_searches, max(stage1+4, target_recommendations*2))
    f,s,e=price_candidates(cands,stage2,progress,status)
    fetched_total+=f; skipped_total+=s; errors.extend(e)
    return fetched_total,skipped_total,errors,stage2


def whole_yard_market_scan(base,batch_size=20,parts_per_car=3,progress=None,status=None,deep=False):
    """
    Search a fair cross-section of the entire yard.
    Coverage mode targets uncovered cars first.
    Deep mode adds more part prices after initial donor coverage.
    """
    ordered=(deepen_yard_candidate_order(base,parts_per_car)
             if deep else whole_yard_candidate_order(base,parts_per_car,only_uncovered=True))

    if ordered is None or len(ordered)==0:
        cov=whole_yard_coverage(base)
        return 0,0,[],0,cov

    subset=ordered.head(int(batch_size))
    donor_count=subset["vehicle_id"].nunique()
    fetched,skipped,errors=price_candidates(subset,len(subset),progress,status)
    cov=whole_yard_coverage(base)
    return fetched,skipped,errors,donor_count,cov

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
    invalidate_rankings()
    mark_memory_dirty("condition")

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
    rev=int(st.session_state.get("business_revision",0))
    cached=st.session_state.get("_learned_warning_cache")
    if not isinstance(cached,dict) or cached.get("_rev")!=rev:
        warning_map={"_rev":rev}
        x=purchase_profit_table()
        if len(x) and "part" in x.columns:
            sold=x[x["sale_price"]>0]
            if len(sold):
                for pname,g in sold.groupby("part"):
                    if len(g)<2:
                        continue
                    avg_profit=g["actual_profit"].mean()
                    avg_days=g["days_to_sell"].dropna().mean() if g["days_to_sell"].notna().any() else np.nan
                    if avg_profit<50:
                        warning_map[norm(pname)]=f"Your history: low avg profit ({money(avg_profit)})"
                    elif pd.notna(avg_days) and avg_days>75:
                        warning_map[norm(pname)]=f"Your history: slow seller ({avg_days:.0f} days avg)"
        st.session_state["_learned_warning_cache"]=warning_map
        cached=warning_map
    return cached.get(norm(part),"")

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

def compact_market_cache(cache):
    """
    Persistent memory doesn't need dozens of example listing rows per part.
    Keeping only pricing summaries makes local/cloud saves dramatically smaller.
    """
    out={}
    for k,v in (cache or {}).items():
        if not isinstance(v,dict):
            continue
        item={
            "query":v.get("query",""),
            "timestamps":v.get("timestamps",{}),
            "updated_at":v.get("updated_at",""),
        }
        for provider in ("ebay","shopping"):
            p=v.get(provider)
            if isinstance(p,dict):
                item[provider]={
                    "p25":p.get("p25"),
                    "median":p.get("median"),
                    "p75":p.get("p75"),
                    "n":p.get("n",0),
                    # Keep only two examples for debugging/reference after restore.
                    "examples":(p.get("examples") or [])[:2],
                }
        out[k]=json_safe(item)
    return out

def json_safe(value):
    """
    Recursively convert app state into strict JSON-compatible values.

    Supabase/Postgres JSONB rejects NaN and +/-Infinity. Pandas/numpy can
    introduce those values into market summaries, imported CSVs and cached
    calculations, so normalize them to null before local/cloud persistence.
    """
    if value is None:
        return None

    # DataFrames / Series
    if isinstance(value,pd.DataFrame):
        return json_safe(value.replace({np.nan:None}).to_dict("records"))
    if isinstance(value,pd.Series):
        return json_safe(value.replace({np.nan:None}).tolist())

    # Containers
    if isinstance(value,dict):
        return {str(k):json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,set)):
        return [json_safe(v) for v in value]

    # numpy scalar types
    if isinstance(value,np.generic):
        value=value.item()

    # Datetime-ish values
    if isinstance(value,(datetime,date)):
        return value.isoformat()

    # Strict JSON numeric handling
    if isinstance(value,float):
        if np.isnan(value) or np.isinf(value):
            return None
        return float(value)

    # pandas NA / NaT and similar scalar missing values
    try:
        missing=pd.isna(value)
        if isinstance(missing,(bool,np.bool_)) and missing:
            return None
    except Exception:
        pass

    # Plain JSON-compatible scalar
    if isinstance(value,(str,int,bool)):
        return value

    # Last-resort representation instead of crashing persistence.
    return str(value)

def backup_payload():
    return json_safe({
        "version":"8.8",
        "inventory":df_records(st.session_state.inventory),
        "market_cache":compact_market_cache(st.session_state.market_cache),
        "live_prices":st.session_state.live_prices,
        "lkq_price_source":st.session_state.get("lkq_price_source","Not synced"),
        "impact_map":st.session_state.impact_map,
        "condition_map":st.session_state.condition_map,
        "part_numbers":st.session_state.part_numbers,
        "pull_list":df_records(st.session_state.pull_list),
        "purchases":df_records(st.session_state.purchases),
        "listings":df_records(st.session_state.listings),
        "sales":df_records(st.session_state.sales),
        "avoid_parts":list(st.session_state.avoid_parts),
        "missing_events":st.session_state.get("missing_events",[]),
        "today_plan":df_records(st.session_state.get("today_plan",pd.DataFrame())),
        "watch_keywords":st.session_state.watch_keywords,
        "market_only":st.session_state.market_only,
        "sold_comps":df_records(st.session_state.sold_comps),
        "interchange":df_records(st.session_state.interchange),
        "market_cache_hours":st.session_state.get("market_cache_hours",24),
        "market_workers":st.session_state.get("market_workers",4),
        "market_covered_count":int(st.session_state.get("market_covered_count",0)),
        "saved_at":datetime.now().isoformat(),
    })

def restore_payload(data):
    st.session_state.inventory=pd.DataFrame(data.get("inventory",[]))
    st.session_state.market_cache=data.get("market_cache",{})
    st.session_state.live_prices=data.get("live_prices",{})
    st.session_state.lkq_price_source=data.get("lkq_price_source","Not synced")
    st.session_state.impact_map=data.get("impact_map",{})
    st.session_state.condition_map=data.get("condition_map",{})
    st.session_state.part_numbers=data.get("part_numbers",{})
    st.session_state.pull_list=pd.DataFrame(data.get("pull_list",[]))
    st.session_state.purchases=pd.DataFrame(data.get("purchases",[]),columns=PURCHASE_COLS) if data.get("purchases") else pd.DataFrame(columns=PURCHASE_COLS)
    st.session_state.listings=pd.DataFrame(data.get("listings",[]),columns=LISTING_COLS) if data.get("listings") else pd.DataFrame(columns=LISTING_COLS)
    st.session_state.sales=pd.DataFrame(data.get("sales",[]),columns=SALE_COLS) if data.get("sales") else pd.DataFrame(columns=SALE_COLS)
    st.session_state.avoid_parts=set(data.get("avoid_parts",[]))
    st.session_state.missing_events=data.get("missing_events",[])
    st.session_state.today_plan=pd.DataFrame(data.get("today_plan",[]))
    st.session_state.watch_keywords=data.get("watch_keywords",[])
    st.session_state.market_only=bool(data.get("market_only",True))
    st.session_state.sold_comps=pd.DataFrame(data.get("sold_comps",[]))
    st.session_state.interchange=pd.DataFrame(data.get("interchange",[]))
    st.session_state.market_cache_hours=int(data.get("market_cache_hours",st.session_state.get("market_cache_hours",24)))
    st.session_state.market_workers=int(data.get("market_workers",st.session_state.get("market_workers",4)))
    st.session_state.market_covered_count=int(data.get("market_covered_count",st.session_state.get("market_covered_count",0)))

def _memory_json_for_hash():
    data=backup_payload().copy()
    data.pop("saved_at",None)
    return json.dumps(json_safe(data),sort_keys=True,default=str,separators=(",",":"),allow_nan=False)

def _memory_hash():
    return hashlib.sha256(_memory_json_for_hash().encode()).hexdigest()

def _local_memory_path():
    mem_id=re.sub(r"[^A-Za-z0-9_.-]","_",str(st.session_state.get("memory_id","wichita-parts")))
    return Path("/tmp")/f"{mem_id}_scanner_memory.json"

def save_local_memory():
    path=_local_memory_path()
    path.write_text(json.dumps(json_safe(backup_payload()),default=str,allow_nan=False))
    return str(path)

def load_local_memory():
    path=_local_memory_path()
    if not path.exists():
        return False,"No local memory file yet."
    restore_payload(json.loads(path.read_text()))
    return True,f"Loaded local memory from {path.name}."

def _secret_value(name,default=""):
    try:
        return str(st.secrets.get(name,default) or "").strip()
    except Exception:
        return str(default or "").strip()

def supabase_config():
    """
    Streamlit Secrets have priority over session fallbacks for EVERY field.
    This prevents stale UI/session defaults from overriding the protected
    `wichita-parts` row configured by the Supabase RLS policy.
    """
    secret_url=_secret_value("SUPABASE_URL")
    secret_key=_secret_value("SUPABASE_KEY")
    secret_table=_secret_value("SUPABASE_TABLE","app_memory")
    secret_memory_id=_secret_value("SUPABASE_MEMORY_ID","wichita-parts")

    url=secret_url or str(st.session_state.get("supabase_url","") or "").strip()
    key=secret_key or str(st.session_state.get("supabase_key","") or "").strip()

    # Only use session values when the corresponding secret is absent.
    table=(secret_table if _secret_value("SUPABASE_TABLE") else
           str(st.session_state.get("supabase_table","app_memory") or "app_memory").strip())
    memory_id=(secret_memory_id if _secret_value("SUPABASE_MEMORY_ID") else
               str(st.session_state.get("memory_id","wichita-parts") or "wichita-parts").strip())

    url=str(url).strip().rstrip("/")
    # Be forgiving if the REST suffix was pasted into SUPABASE_URL.
    if "/rest/v1" in url:
        url=url.split("/rest/v1",1)[0].rstrip("/")

    return url,str(key).strip(),str(table).strip(),str(memory_id).strip()

def supabase_config_source():
    has_url=bool(_secret_value("SUPABASE_URL"))
    has_key=bool(_secret_value("SUPABASE_KEY"))
    has_table=bool(_secret_value("SUPABASE_TABLE"))
    has_id=bool(_secret_value("SUPABASE_MEMORY_ID"))
    if has_url and has_key:
        return "Streamlit Secrets"
    if st.session_state.get("supabase_url") and st.session_state.get("supabase_key"):
        return "temporary app fields"
    return "not configured"

def cloud_memory_enabled():
    url,key,table,memory_id=supabase_config()
    return bool(url and key and table and memory_id)

def _supabase_error_message(status_code,body):
    body=str(body or "").replace("\n"," ").strip()
    if len(body)>300:
        body=body[:300]+"…"
    if status_code==401:
        hint="Check SUPABASE_KEY. Use the publishable key for this setup."
    elif status_code==403:
        hint="The key reached Supabase, but RLS/policies blocked access. Confirm the allowed row ID is 'wichita-parts'."
    elif status_code==404:
        hint="Check SUPABASE_URL and SUPABASE_TABLE. The table should be public.app_memory."
    elif status_code==400:
        hint="Supabase received the request but rejected its parameters/schema. Confirm app_memory has id, payload and updated_at columns."
    else:
        hint="Check the project URL, key, table and RLS policy."
    return f"HTTP {status_code}. {hint}" + (f" Supabase said: {body}" if body else "")

def test_cloud_memory_connection():
    """
    Fast read-only connectivity test.
    A 200 with zero rows is still a valid Supabase connection.
    """
    url,key,table,memory_id=supabase_config()
    missing=[]
    if not url: missing.append("SUPABASE_URL")
    if not key: missing.append("SUPABASE_KEY")
    if not table: missing.append("SUPABASE_TABLE")
    if not memory_id: missing.append("SUPABASE_MEMORY_ID")
    if missing:
        return False,"Missing: "+", ".join(missing)

    try:
        r=fast_http_session().get(
            f"{url}/rest/v1/{table}",
            headers={"apikey":key,"Accept":"application/json"},
            params={"id":f"eq.{memory_id}","select":"id,updated_at","limit":"1"},
            timeout=(4,8),
        )
    except Exception as e:
        return False,f"Could not reach Supabase: {e}"

    if not r.ok:
        return False,_supabase_error_message(r.status_code,r.text)

    try:
        rows=r.json()
    except Exception:
        rows=[]

    if rows:
        stamp=rows[0].get("updated_at","")
        return True,f"Connected. Cloud memory row '{memory_id}' exists" + (f" · updated {stamp}" if stamp else "") + "."
    return True,f"Connected to Supabase and table '{table}'. No '{memory_id}' row exists yet — use SAVE TO CLOUD NOW once."

def load_cloud_memory():
    url,key,table,memory_id=supabase_config()
    if not (url and key):
        return False,"Cloud memory is not configured."
    headers={"apikey":key,"Accept":"application/json"}
    r=fast_http_session().get(
        f"{url}/rest/v1/{table}",
        headers=headers,
        params={"id":f"eq.{memory_id}","select":"payload,updated_at","limit":"1"},
        timeout=(3,7),
    )
    if not r.ok:
        raise RuntimeError(_supabase_error_message(r.status_code,r.text))
    rows=r.json()
    if not rows:
        return False,f"Connected, but no cloud memory row exists for '{memory_id}' yet."
    payload=rows[0].get("payload") or {}
    restore_payload(payload)
    return True,f"Loaded cloud memory saved {rows[0].get('updated_at','previously')}."

def save_cloud_memory():
    url,key,table,memory_id=supabase_config()
    if not (url and key):
        return False,"Cloud memory is not configured."
    headers={
        "apikey":key,
        "Content-Type":"application/json",
        "Prefer":"resolution=merge-duplicates,return=minimal",
    }
    body=json_safe({
        "id":memory_id,
        "payload":backup_payload(),
        "updated_at":_utc_now_iso(),
    })
    r=fast_http_session().post(
        f"{url}/rest/v1/{table}",
        headers=headers,
        json=body,
        timeout=(4,15),
    )
    if not r.ok:
        raise RuntimeError(_supabase_error_message(r.status_code,r.text))
    return True,f"Cloud memory saved to '{memory_id}'."

def mark_memory_dirty(reason=""):
    """Flag persistent state as changed without serializing it immediately."""
    st.session_state["_memory_dirty"]=True
    st.session_state["_cloud_memory_pending"]=True
    st.session_state["_last_mutation_ts"]=time.time()
    if reason:
        st.session_state["_memory_dirty_reason"]=str(reason)

def autosave_memory_if_changed():
    """
    Event-driven persistence.

    Normal page navigation / widget reruns do almost nothing here.
    The expensive backup JSON is created only after a real persistent-state
    mutation calls mark_memory_dirty().
    """
    if not st.session_state.get("memory_autosave",True):
        return

    now=time.time()
    dirty=bool(st.session_state.get("_memory_dirty",False))
    pending=bool(st.session_state.get("_cloud_memory_pending",False))

    # Let the user-facing rerun finish before serializing a large state payload.
    # A following interaction or explicit save will persist it.
    if dirty and (now-float(st.session_state.get("_last_mutation_ts",0))) < 1.25:
        return

    # Fast path: ordinary Streamlit rerun with no state mutation.
    if not dirty and not pending:
        return

    # Save locally only when something actually changed.
    if dirty:
        try:
            save_local_memory()
            st.session_state["_memory_dirty"]=False
            st.session_state.memory_status="Local memory saved."
        except Exception as e:
            st.session_state.memory_status=f"Local memory save failed: {e}"
            return

    if not cloud_memory_enabled():
        st.session_state["_cloud_memory_pending"]=False
        st.session_state.memory_status="Local memory saved. Configure Supabase for restart-proof cloud memory."
        return

    cloud_interval=float(st.session_state.get("cloud_autosave_seconds",90))
    last_cloud=float(st.session_state.get("_last_cloud_save_ts",0))

    # Cloud writes are intentionally much less frequent.
    if now-last_cloud < cloud_interval:
        remaining=max(1,int(cloud_interval-(now-last_cloud)))
        st.session_state.memory_status=f"Saved locally · cloud save queued (≤{remaining}s)."
        return

    try:
        save_cloud_memory()
        st.session_state["_last_cloud_save_ts"]=now
        st.session_state["_cloud_memory_pending"]=False
        st.session_state.supabase_connected=True
        st.session_state.supabase_connection_status="Connected · autosave succeeded."
        st.session_state.memory_status="Cloud memory saved."
    except Exception as e:
        st.session_state.supabase_connected=False
        st.session_state.supabase_connection_status=f"Cloud save failed: {e}"
        st.session_state.memory_status=f"Local memory saved; cloud save failed: {e}"

# ---------------- STATE ----------------
defaults={
    "inventory":pd.DataFrame(),"market_cache":{},"live_prices":{},
    "lkq_price_source":"Not synced","lkq_price_url":"","lkq_price_errors":[],
    "impact_map":{},"condition_map":{},"part_numbers":{},
    "pull_list":pd.DataFrame(),"sold_comps":pd.DataFrame(),"interchange":pd.DataFrame(),
    "avoid_parts":set(),"watch_keywords":["F-150","SILVERADO","SIERRA","RAM","LEXUS","BMW","2500","3500"],
    "last_sync":"Never","previous_stocks":set(),
    "ebay_client_id":"","ebay_client_secret":"","serpapi_api_key":"",
    "market_only":True,
    "market_cache_hours":48,"market_workers":8,
    "ranking_revision":0,"opportunity_cache":{},
    "market_covered_count":0,
    "business_revision":0,"_learned_warning_cache":{},
    "missing_events":[],
    "best_min_profit":75,"best_min_score":52,"best_show_cards":10,
    "plan_budget":500.0,"plan_hours":3.0,"plan_max_cars":8,"plan_parts_per_car":4,
    "plan_min_conf":35,"plan_min_presence":25,"plan_min_profit":75,
    "today_plan":pd.DataFrame(),
    "memory_autosave":True,"memory_loaded":False,"memory_status":"Not loaded yet.",
    "memory_check_seconds":3,"cloud_autosave_seconds":150,
    "_memory_dirty":False,"_cloud_memory_pending":False,"_memory_dirty_reason":"","_last_mutation_ts":0.0,
    "supabase_url":"","supabase_key":"","supabase_table":"app_memory","memory_id":"wichita-parts",
    "supabase_connected":False,"supabase_connection_status":"Not tested yet.","_supabase_probe_done":False,
    "ebay_fee":.13,"local_fee":0.0,"ebay_quick_pct":.88,"local_price_pct":.82,
    "travel":10.0,"packing":5.0,"high_value_alert":600.0,
}
for k,v in defaults.items():
    if k not in st.session_state: st.session_state[k]=v

# Migrate the old V7/V8 memory ID that conflicts with the RLS policy we created.
if str(st.session_state.get("memory_id","")).strip()=="wichita-v72":
    st.session_state.memory_id="wichita-parts"

ensure_df("purchases",PURCHASE_COLS)
ensure_df("listings",LISTING_COLS)
ensure_df("sales",SALE_COLS)

# Fast startup memory strategy:
# 1) Load local /tmp memory first (no network).
# 2) Only contact Supabase automatically when local memory is unavailable,
#    which normally means a real cold container/redeploy.
# 3) Never run a separate cloud probe during boot.
if not st.session_state.get("memory_loaded",False):
    loaded=False
    msg=""

    try:
        loaded,local_msg=load_local_memory()
        if loaded:
            msg=local_msg
    except Exception:
        pass

    if not loaded and cloud_memory_enabled():
        try:
            loaded,msg=load_cloud_memory()
            st.session_state.supabase_connected=True
            st.session_state.supabase_connection_status=msg
        except Exception as e:
            st.session_state.supabase_connected=False
            st.session_state.supabase_connection_status=f"Cloud memory load failed: {e}"
            msg=st.session_state.supabase_connection_status
    elif loaded and cloud_memory_enabled():
        # Don't spend a network request merely to prove a configured cloud
        # connection during normal startup. The next save/test/load confirms it.
        st.session_state.supabase_connection_status=(
            st.session_state.get("supabase_connection_status")
            or "Cloud configured · local memory loaded for fast startup."
        )

    st.session_state.memory_loaded=True
    st.session_state.memory_status=msg or "No saved memory found yet."
    ensure_df("purchases",PURCHASE_COLS)
    ensure_df("listings",LISTING_COLS)
    ensure_df("sales",SALE_COLS)

# ---------------- UI ----------------
_market_sources=sum(1 for v in provider_status().values() if v)
_cloud=("Supabase ✓" if st.session_state.get("supabase_connected",False) else ("Cloud configured" if cloud_memory_enabled() else "Local memory"))
_covered_now=min(int(st.session_state.get("market_covered_count",0)),len(st.session_state.inventory))
st.markdown(
    f"""<div class="hero">
      <div class="hero-title">Wichita Parts Profit Scanner <span style="color:#ffb000">V8.8</span></div>
      <div class="hero-sub">{YARD_NAME} · {YARD_ADDRESS}</div>
      <div class="chips">
        <span class="chip">🚙 {len(st.session_state.inventory)} yard cars</span>
        <span class="chip">🌐 {_market_sources} market source{'s' if _market_sources!=1 else ''}</span>
        <span class="chip">⚡ {len(st.session_state.market_cache)} cached market searches</span>
        <span class="chip">🎯 {_covered_now}/{len(st.session_state.inventory)} cars market-checked</span>
        <span class="chip">🧠 {_cloud}</span>
      </div>
    </div>""",
    unsafe_allow_html=True
)

MAIN_PAGES=[
    "⚡ TODAY'S PLAN","🏆 BEST CARS","🧭 YARD MODE","🔄 SYNC","📷 SCAN",
    "🌐 MARKET","📦 BUSINESS","📊 DASHBOARD","⚙️ SETUP"
]
page=st.radio(
    "Page",
    MAIN_PAGES,
    horizontal=True,
    label_visibility="collapsed",
    key="main_page_v8",
)

# ---------- TODAY'S PLAN ----------
if page=="⚡ TODAY'S PLAN":
    st.subheader("Today's highest-profit yard plan")
    if not len(st.session_state.inventory):
        st.info("Sync the Wichita yard first.")
    else:
        opp=get_opportunities_cached(st.session_state.inventory)

        with st.form("today_plan_form"):
            c1,c2,c3=st.columns(3)
            budget=c1.number_input("Cash budget",100.0,5000.0,float(st.session_state.plan_budget),50.0)
            hours=c2.number_input("Time available (hours)",.5,10.0,float(st.session_state.plan_hours),.5)
            max_cars=c3.slider("Maximum donor cars",1,20,int(st.session_state.plan_max_cars),1)

            c4,c5,c6=st.columns(3)
            parts_per_car=c4.slider("Max parts per car",1,6,int(st.session_state.plan_parts_per_car),1)
            min_conf=c5.slider("Minimum market confidence",0,95,int(st.session_state.plan_min_conf),5)
            min_presence=c6.slider("Minimum likely-still-there %",0,95,int(st.session_state.plan_min_presence),5)

            min_profit=st.number_input("Minimum profit per part",0,1000,int(st.session_state.plan_min_profit),25)
            submitted=st.form_submit_button("⚡ BUILD TODAY'S PLAN",type="primary")

        if submitted:
            st.session_state.plan_budget=budget
            st.session_state.plan_hours=hours
            st.session_state.plan_max_cars=max_cars
            st.session_state.plan_parts_per_car=parts_per_car
            st.session_state.plan_min_conf=min_conf
            st.session_state.plan_min_presence=min_presence
            st.session_state.plan_min_profit=min_profit
            st.session_state.today_plan=build_today_plan(
                opp,budget,hours,max_cars,parts_per_car,min_conf,min_presence,min_profit
            )
            mark_memory_dirty("today plan")

        plan=st.session_state.today_plan
        if plan is None or not len(plan):
            st.info("Build a plan above. If no parts qualify, price more opportunities in MARKET or lower the confidence/profit filters.")
        else:
            summary=donor_plan_summary(plan)
            plan_cost=plan["BUY"].sum()
            raw_profit=plan["PROFIT"].sum()
            expected_profit=plan["EXPECTED_PROFIT"].sum()
            pull_minutes=plan["MIN"].sum()+max(0,summary["STOCK"].nunique())*7 if len(summary) else plan["MIN"].sum()

            a,b,c,d=st.columns(4)
            a.metric("Bring about",money(plan_cost))
            b.metric("Expected profit",money(expected_profit))
            c.metric("If everything is there",money(raw_profit))
            d.metric("Yard time",f"{pull_minutes/60:.1f} hr")

            st.caption("Expected profit is adjusted for the donor's age and the estimated chance the part is still present. A 7-minute per-donor movement/inspection allowance is included in the time estimate.")

            for _,car in summary.iterrows():
                parts=plan[plan["STOCK"].astype(str).eq(str(car.STOCK))]
                ph=""
                for _,r in parts.iterrows():
                    ph+=(
                        f"<div class='partrow'><b>{r['PART']}</b> · {r['REC']}<br>"
                        f"Buy {money(r['BUY'])} → Target {money(r['TARGET'])} · "
                        f"<b>{money(r['EXPECTED_PROFIT'])} expected</b><br>"
                        f"<span class='small'>{r['PRESENT_PCT']:.0f}% likely present · "
                        f"{r['CONF']:.0f}% market confidence · {r['MIN']} min · {r['CHANNEL']}</span></div>"
                    )
                st.markdown(
                    f"<div class='plan-card'><div class='kpi'>{car.SECTION or 'Location pending'} · Row {car.ROW or '—'} / Space {car.SPACE or '—'} — {car.CAR}</div>"
                    f"<div class='small'>Stock {car.STOCK} · Available {car.AVAILABLE or '—'} · "
                    f"{int(car.DAYS_YARD) if pd.notna(car.DAYS_YARD) else '—'} days in yard · {int(car.PARTS)} pulls · "
                    f"{money(car.COST)} cost · {money(car.EXPECTED)} expected profit</div>{ph}</div>",
                    unsafe_allow_html=True
                )

            c1,c2=st.columns(2)
            if c1.button("🧭 SEND PLAN TO YARD MODE",type="primary"):
                route=plan.copy()
                route["route_id"]=[unique_id("route") for _ in range(len(route))]
                route["done"]=False
                route["bought"]=False
                keep=["route_id","done","bought","SECTION","ROW","SPACE","YARD_LOCATION","AVAILABLE","DAYS_YARD","CAR","STOCK","PART","OEM","BUY","TARGET","PROFIT","CHANNEL","SCORE"]
                st.session_state.pull_list=route[keep].copy()
                mark_memory_dirty("yard route")
                st.success("Plan sent to YARD MODE.")
            c2.download_button("⬇️ DOWNLOAD TODAY'S PLAN",plan.to_csv(index=False).encode(),
                               "wichita_today_plan.csv","text/csv")

# ---------- BEST CARS ----------
if page=="🏆 BEST CARS":
    st.subheader("Best donor cars + best parts")
    if not len(st.session_state.inventory):
        st.info("Sync the Wichita yard first.")
    else:
        with st.form("best_cars_filters"):
            c1,c2,c3=st.columns(3)
            min_profit=c1.number_input("Minimum profit",0,3000,int(st.session_state.best_min_profit),25)
            min_score=c2.slider("Minimum score",0,100,int(st.session_state.best_min_score))
            show_cards=c3.slider("Donor cards to render",4,18,int(st.session_state.best_show_cards),1)
            apply_filters=st.form_submit_button("APPLY FILTERS")
        if apply_filters:
            st.session_state.best_min_profit=min_profit
            st.session_state.best_min_score=min_score
            st.session_state.best_show_cards=show_cards
        min_profit=st.session_state.best_min_profit
        min_score=st.session_state.best_min_score
        show_cards=st.session_state.best_show_cards

        opp=get_opportunities_cached(st.session_state.inventory)
        good=opp[(opp["PROFIT"]>=min_profit)&(opp["SCORE"]>=min_score)].copy()
        good=good.sort_values(["SCORE","PROFIT","$/HR"],ascending=False)

        if not len(good):
            st.info("No market-priced recommendations yet. Go to MARKET and run SCAN NEXT WHOLE-YARD BATCH. The scanner now covers unpriced donor cars first.")
        else:
            donors=[]
            for vid,g in good.groupby("vehicle_id"):
                top=g.nlargest(5,"SCORE")
                f=top.iloc[0]
                donors.append({
                    "vehicle_id":vid,"NEW":f.NEW,"CAR":f.CAR,"STOCK":f.STOCK,
                    "SECTION":f.SECTION,"ROW":f.ROW,"SPACE":f.SPACE,
                    "YARD_LOCATION":f.YARD_LOCATION,"AVAILABLE":f.AVAILABLE,"DAYS_YARD":f.DAYS_YARD,
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

            for _,drow in donors.head(show_cards).iterrows():
                g=good[good["vehicle_id"]==drow.vehicle_id].nlargest(5,"SCORE")
                ph=""
                for _,r in g.iterrows():
                    warn=""
                    if r["LEARNED_WARNING"]: warn=f"<br><span class='small'>⚠ {r['LEARNED_WARNING']}</span>"
                    ph+=(
                        f"<div class='partrow'><b>{r['PART']}</b> · {r['CHANNEL']}<br>"
                        f"Buy {money(r['BUY'])} · Target {money(r['TARGET'])} · "
                        f"<b>Profit {money(r['PROFIT'])}</b> · {money(r['$/HR'])}/hr<br>"
                        f"<span class='small'>{r['REC']} · Market {money(r['MARKET'])} · "
                        f"{r['PRESENT_PCT']:.0f}% likely present · ~{r['DAYS']} days to sell · "
                        f"Confidence {r['CONF']}% · {r['VALUE_SOURCE']}</span>{warn}</div>"
                    )
                st.markdown(
                    f"<div class='card'><b>{drow.NEW} {drow.CAR}</b><br>"
                    f"📍 <b>{drow.YARD_LOCATION or 'Location pending'}</b><br>"
                    f"<span class='small'>Available {drow.AVAILABLE or '—'} · "
                    f"{int(drow.DAYS_YARD) if pd.notna(drow.DAYS_YARD) else '—'} days in yard · "
                    f"Stock {drow.STOCK} · Top-5 est. profit {money(drow.TOP5_PROFIT)}</span>{ph}</div>",
                    unsafe_allow_html=True
                )

            if st.toggle("Show full opportunity table",value=False,key="show_full_best_table"):
                cols=["NEW","CAR","YARD_LOCATION","AVAILABLE","DAYS_YARD","PART","REC","BUY","MARKET","TARGET","CHANNEL","PROFIT","EXPECTED_PROFIT","PRESENT_PCT","$/HR","DAYS","CONF","SCORE","VALUE_SOURCE","STOCK"]
                st.dataframe(good[cols],use_container_width=True,hide_index=True)
            st.download_button("⬇️ DOWNLOAD CURRENT OPPORTUNITIES",good.to_csv(index=False).encode(),
                               "wichita_current_opportunities.csv","text/csv")

# ---------- YARD MODE ----------
if page=="🧭 YARD MODE":
    st.subheader("I'm at the yard")
    if not len(st.session_state.inventory):
        st.info("Sync inventory first.")
    else:
        opp=get_opportunities_cached(st.session_state.inventory).sort_values(["SCORE","PROFIT"],ascending=False)
        with st.form("yard_route_form"):
            a,b,c=st.columns(3)
            max_cars=a.slider("Cars to visit",3,20,8,1)
            per_car=b.slider("Parts/car",1,5,3,1)
            yard_min=c.number_input("Min profit/part",0,1000,100,25)
            build_route=st.form_submit_button("🧭 BUILD YARD ROUTE",type="primary")

        if build_route:
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
                        "YARD_LOCATION":r.YARD_LOCATION,"AVAILABLE":r.AVAILABLE,"DAYS_YARD":r.DAYS_YARD,
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
            mark_memory_dirty("yard route")

        if len(st.session_state.pull_list):
            st.markdown("### Walking order")
            route=st.session_state.pull_list.copy()
            for stock,g in route.groupby("STOCK",sort=False):
                first=g.iloc[0]
                st.markdown(f"#### 📍 {first.YARD_LOCATION or 'Location pending'} — {first.CAR}")
                st.caption(f"Available {first.AVAILABLE or '—'} · {int(first.DAYS_YARD) if pd.notna(first.DAYS_YARD) else '—'} days in yard")
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
                invalidate_rankings()
                st.session_state.pull_list.loc[ridx,"bought"]=True
                st.session_state.business_revision+=1
                mark_memory_dirty("purchase")
                st.success("Added to purchased inventory.")

            if st.button("❌ PART IS MISSING"):
                st.session_state.missing_events.append({
                    "date":today_iso(),"stock":str(rr.STOCK),"car":str(rr.CAR),"part":str(rr.PART)
                })
                st.session_state.pull_list.loc[ridx,"done"]=True
                invalidate_rankings()
                mark_memory_dirty("missing part")
                st.warning("Marked missing. Future likely-still-there estimates will learn from this category.")

            st.download_button("⬇️ DOWNLOAD ROUTE",st.session_state.pull_list.to_csv(index=False).encode(),
                               "wichita_yard_route.csv","text/csv")
        else:
            st.info("Build a route to get a row-by-row walking list.")

# ---------- SYNC ----------
if page=="🔄 SYNC":
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
                st.session_state.market_covered_count=0
                invalidate_rankings()
                mark_memory_dirty("inventory sync")
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
        inv_show=st.session_state.inventory.copy()
        show_cols=[c for c in [
            "year","make","model","color","yard_location","available_date","days_in_yard",
            "stock_number","vin","is_new"
        ] if c in inv_show.columns]
        st.dataframe(inv_show[show_cols],use_container_width=True,hide_index=True)
        st.download_button("⬇️ INVENTORY SNAPSHOT",st.session_state.inventory.to_csv(index=False).encode(),
                           "wichita_inventory_snapshot.csv","text/csv")
    snap=st.file_uploader("Load an older inventory snapshot for new-arrival comparison",type=["csv"],key="old_snap")
    if snap:
        prev=pd.read_csv(snap,dtype=str).fillna("")
        if "stock_number" in prev.columns:
            st.session_state.previous_stocks=set(prev["stock_number"].astype(str))
            st.success(f"Loaded {len(prev)} previous stock numbers.")

# ---------- SCAN ----------
if page=="📷 SCAN":
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
            invalidate_rankings()
            mark_memory_dirty("OEM number")
            matches=interchange_matches(pn.strip())
            st.success(f"Saved. {len(matches)} interchange match(es) in your database.")

# ---------- MARKET ----------
if page=="🌐 MARKET":
    st.subheader("Whole-yard market scan")

    providers=provider_status()
    active_names=[k for k,v in providers.items() if v]
    if active_names:
        st.success("Market sources enabled: " + ", ".join(active_names))
    else:
        st.warning("No live market source is configured yet. Add eBay and/or SerpAPI in SETUP.")

    st.session_state.market_only=st.toggle(
        "Recommend only parts with real market-price data",
        value=st.session_state.market_only,
        help="BEST CARS and TODAY'S PLAN only promote parts after a real market source or sold comp supplies a usable value."
    )

    with st.expander("Import sold comps / interchange"):
        up=st.file_uploader("Import sold comps CSV",type=["csv"],key="soldcomp_upload")
        if up:
            st.session_state.sold_comps=pd.read_csv(up)
            invalidate_rankings()
            mark_memory_dirty("sold comps")
            st.success(f"Loaded {len(st.session_state.sold_comps)} sold comp rows.")

        iu=st.file_uploader("Import interchange CSV",type=["csv"],key="inter_upload")
        if iu:
            st.session_state.interchange=pd.read_csv(iu)
            invalidate_rankings()
            mark_memory_dirty("interchange")
            st.success(f"Loaded {len(st.session_state.interchange)} interchange rows.")

    if len(st.session_state.inventory):
        base=get_opportunities_cached(st.session_state.inventory,allow_unpriced=True)
        cov=whole_yard_coverage(base)

        a,b,c,d=st.columns(4)
        a.metric("Yard cars",cov["total"])
        b.metric("Market-checked",cov["covered"])
        pct=(100*cov["covered"]/cov["total"]) if cov["total"] else 0
        c.metric("Coverage",f"{pct:.0f}%")
        d.metric("No-price exhausted",cov["exhausted"])

        st.progress(min(1.0,pct/100 if cov["total"] else 0))
        st.caption(
            "Whole-yard coverage gives every donor car a chance before spending extra searches on cars already priced. "
            "Matching year/make/model/part searches are shared automatically."
        )

        with st.form("whole_yard_market_form_v83"):
            c1,c2,c3=st.columns(3)
            scope=c1.selectbox(
                "Scan scope",
                ["🎯 Cover unpriced cars first","🔬 Deepen all cars"],
                help="Cover mode is recommended until market coverage reaches 100%."
            )
            batch=c2.slider("Unique candidates this run",5,60,20,5)
            parts_per_car=c3.slider(
                "Candidate parts/car",
                1,5,3,1,
                help="The scan tries the best part first, then moves to the next part only on later coverage rounds."
            )
            run_whole=st.form_submit_button("🌐 SCAN NEXT WHOLE-YARD BATCH",type="primary")

        if run_whole:
            prog=st.progress(0.0)
            stat=st.empty()
            try:
                # Refresh base after any previous market cache changes.
                base=get_opportunities_cached(st.session_state.inventory,allow_unpriced=True)
                deep=scope.startswith("🔬")
                fetched,skipped,errors,donors_touched,new_cov=whole_yard_market_scan(
                    base,batch,parts_per_car,prog,stat,deep=deep
                )
                prog.empty()
                stat.empty()

                pct2=(100*new_cov["covered"]/new_cov["total"]) if new_cov["total"] else 0
                st.success(
                    f"Batch finished: {donors_touched} donor car(s) targeted · "
                    f"{fetched} internet refresh(es) · {skipped} cached/shared · "
                    f"yard coverage now {new_cov['covered']}/{new_cov['total']} ({pct2:.0f}%)."
                )
                if errors:
                    with st.expander(f"{len(errors)} lookup error(s)"):
                        st.write(errors)
            except Exception as e:
                prog.empty()
                stat.empty()
                st.error(str(e))

        # Show uncovered donors so the user can see that the scanner is genuinely moving across the yard.
        priced=get_opportunities_cached(st.session_state.inventory,allow_unpriced=False)
        covered_ids=set(priced["vehicle_id"].tolist()) if len(priced) else set()
        remaining=st.session_state.inventory.loc[
            [i for i in st.session_state.inventory.index if i not in covered_ids]
        ].copy()

        if len(remaining):
            with st.expander(f"Cars still needing a usable market price ({len(remaining)})"):
                cols=[c for c in ["year","make","model","yard_location","available_date","days_in_yard","stock_number"] if c in remaining.columns]
                st.dataframe(remaining[cols].head(250),use_container_width=True,hide_index=True)
        else:
            st.success("Every current Wichita donor car has at least one market-priced part.")

        st.markdown("### Inspect one donor")
        vi=st.selectbox(
            "Donor car",
            list(st.session_state.inventory.index),
            format_func=lambda i:f"{st.session_state.inventory.loc[i,'year']} {st.session_state.inventory.loc[i,'make']} {st.session_state.inventory.loc[i,'model']} — {st.session_state.inventory.loc[i].get('yard_location','Location pending')}",
            key="market_car"
        )
        _all_unpriced=get_opportunities_cached(st.session_state.inventory,allow_unpriced=True)
        caropp=_all_unpriced[_all_unpriced["vehicle_id"].eq(vi)].sort_values(["MARKET_DATA","SCORE","PROFIT"],ascending=False)
        cols=["PART","MARKET_DATA","MARKET","TARGET","PROFIT","EXPECTED_PROFIT","CONF","PRESENT_PCT","SCORE","VALUE_SOURCE"]
        st.dataframe(caropp[cols].head(15),use_container_width=True,hide_index=True)

    else:
        st.info("Sync the Wichita yard first.")

    if st.session_state.market_cache:
        if st.toggle("Show cached market references",value=False,key="show_market_cache_v83"):
            rows=[]
            for k,v in st.session_state.market_cache.items():
                eb=v.get("ebay",{}) if isinstance(v,dict) else {}
                sh=v.get("shopping",{}) if isinstance(v,dict) else {}
                rows.append({
                    "vehicle_part":k,
                    "eBay_median":eb.get("median"),"eBay_n":eb.get("n"),
                    "Shopping_median":sh.get("median"),"Shopping_n":sh.get("n"),
                    "query":v.get("query") if isinstance(v,dict) else ""
                })
            st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)

# ---------- BUSINESS ----------
if page=="📦 BUSINESS":
    st.subheader("Purchased parts, listings & sales")

    BUSINESS_PAGES=["Purchased","Listings","Record sale","Price drops","Avoid list"]
    business_page=st.radio(
        "Business section",
        BUSINESS_PAGES,
        horizontal=True,
        label_visibility="collapsed",
        key="business_page_v74",
    )

    if business_page=="Purchased":
        st.dataframe(st.session_state.purchases,use_container_width=True,hide_index=True)
        if len(st.session_state.purchases):
            st.download_button("Download purchases",st.session_state.purchases.to_csv(index=False).encode(),"purchases.csv","text/csv")

    if business_page=="Listings":
        if len(st.session_state.purchases):
            pid=st.selectbox("Purchased part",st.session_state.purchases.purchase_id.tolist(),
                format_func=lambda x:f"{st.session_state.purchases.loc[st.session_state.purchases.purchase_id.eq(x),'car'].iloc[0]} — {st.session_state.purchases.loc[st.session_state.purchases.purchase_id.eq(x),'part'].iloc[0]}")
            prow=st.session_state.purchases.loc[st.session_state.purchases.purchase_id.eq(pid)].iloc[0].to_dict()
            draft_title,draft_desc=listing_draft(prow)
            st.markdown("#### Listing builder")
            title=st.text_input("Suggested title",value=draft_title)
            description=st.text_area("Suggested description",value=draft_desc,height=130)
            platform=st.selectbox("Platform",["eBay","Facebook Marketplace","Local","Other"])
            suggested=max(1.0,safe_float(prow.get("market_at_buy"),200.0))
            list_price=st.number_input("List price",0.0,10000.0,float(suggested),5.0)
            url=st.text_input("Listing URL (optional)")
            if st.button("ADD LISTING"):
                row={"listing_id":unique_id("list"),"purchase_id":pid,"platform":platform,
                     "list_date":today_iso(),"list_price":list_price,"current_price":list_price,
                     "status":"active","url":url}
                st.session_state.listings=pd.concat([st.session_state.listings,pd.DataFrame([row])],ignore_index=True)
                mark_memory_dirty("listing")
                st.success("Listing added.")
        st.dataframe(st.session_state.listings,use_container_width=True,hide_index=True)

    if business_page=="Record sale":
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
                st.session_state.business_revision+=1
                invalidate_rankings()
                mark_memory_dirty("sale")
                st.session_state.purchases.loc[st.session_state.purchases.purchase_id.eq(pid),"status"]="sold"
                st.session_state.listings.loc[st.session_state.listings.purchase_id.eq(pid),"status"]="sold"
                st.success("Sale recorded.")
        st.dataframe(st.session_state.sales,use_container_width=True,hide_index=True)

    if business_page=="Price drops":
        rec=price_drop_recommendations()
        if len(rec):
            st.dataframe(rec,use_container_width=True,hide_index=True)
        else:
            st.info("No active listings currently need a price-drop recommendation.")

    if business_page=="Avoid list":
        st.write("Explicitly block parts you don't want the scanner recommending.")
        avoid_choice=st.multiselect("Never buy again",PARTS.part.tolist(),default=sorted(st.session_state.avoid_parts))
        new_avoid=set(avoid_choice)
        if new_avoid!=st.session_state.avoid_parts:
            st.session_state.avoid_parts=new_avoid
            invalidate_rankings()
            mark_memory_dirty("avoid list")

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
if page=="📊 DASHBOARD":
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
if page=="⚙️ SETUP":
    st.subheader("Setup & controls")

    SETUP_PAGES=["Market APIs","Speed & Cache","Memory","LKQ prices","Impact","Shipping","Watchlist","Backup"]
    setup_page=st.radio(
        "Setup section",
        SETUP_PAGES,
        horizontal=True,
        label_visibility="collapsed",
        key="setup_page_v74",
    )

    if setup_page=="Market APIs":
        st.markdown("#### Market API connections")

        try:
            secret_serp=bool(str(st.secrets.get("SERPAPI_KEY","") or "").strip())
            secret_ebay=bool(
                str(st.secrets.get("EBAY_CLIENT_ID","") or "").strip()
                and str(st.secrets.get("EBAY_CLIENT_SECRET","") or "").strip()
            )
        except Exception:
            secret_serp=False
            secret_ebay=False

        if secret_serp:
            st.success("✅ SerpAPI is loaded permanently from Streamlit Secrets.")
        else:
            st.warning("SerpAPI is not in Streamlit Secrets yet. Anything typed below is temporary and can disappear after a restart.")

        if secret_ebay:
            st.success("✅ eBay credentials are loaded permanently from Streamlit Secrets.")

        st.caption("Recommended: keep API credentials in Streamlit Community Cloud → App Settings → Secrets. Do not commit API keys to GitHub.")

        with st.expander("Temporary session credentials", expanded=not secret_serp):
            if not secret_serp:
                temp_serp=st.text_input(
                    "SerpAPI key (temporary fallback)",
                    value=str(st.session_state.get("serpapi_api_key","")),
                    type="password",
                    key="serpapi_temp_v84",
                )
                if st.button("USE TEMPORARY SERPAPI KEY"):
                    st.session_state.serpapi_api_key=temp_serp.strip()
                    st.success("Temporary key loaded for this Streamlit session.")
            else:
                st.info("No SerpAPI entry needed here because the persistent secret is active.")

            if not secret_ebay:
                temp_cid=st.text_input(
                    "eBay Client ID (temporary)",
                    value=str(st.session_state.get("ebay_client_id","")),
                    key="ebay_cid_temp_v84",
                )
                temp_sec=st.text_input(
                    "eBay Client Secret (temporary)",
                    value=str(st.session_state.get("ebay_client_secret","")),
                    type="password",
                    key="ebay_sec_temp_v84",
                )
                if st.button("USE TEMPORARY EBAY CREDENTIALS"):
                    st.session_state.ebay_client_id=temp_cid.strip()
                    st.session_state.ebay_client_secret=temp_sec.strip()
                    st.success("Temporary eBay credentials loaded for this session.")

        status=provider_status()
        active=[k for k,v in status.items() if v]
        if active:
            st.info("Active market sources: " + ", ".join(active))
        else:
            st.error("No market API source is currently active.")

    if setup_page=="Speed & Cache":
        st.markdown("#### Performance")
        st.caption("V8.7 fast shell: no yard ranking in the header, both ranking variants stay cached, and normal startup avoids Supabase network calls when local memory exists.")
        with st.form("speed_settings_form"):
            workers=st.slider(
                "Parallel market searches",1,12,int(st.session_state.market_workers),1,
                help="8 is the V8.1 default. If your API plan rate-limits you, reduce this to 4–6."
            )
            cache_hours=st.slider(
                "Reuse internet prices for",1,168,int(st.session_state.market_cache_hours),1,
                help="24 hours saves API quota and makes repeat searches almost instant."
            )
            cloud_seconds=st.slider(
                "Minimum time between cloud memory saves",30,300,int(st.session_state.cloud_autosave_seconds),30
            )
            save_perf=st.form_submit_button("SAVE PERFORMANCE SETTINGS")
        if save_perf:
            st.session_state.market_workers=workers
            st.session_state.market_cache_hours=cache_hours
            st.session_state.cloud_autosave_seconds=cloud_seconds
            mark_memory_dirty("performance settings")
            st.success("Performance settings saved.")
        fresh=0
        now=datetime.now()
        for entry in st.session_state.market_cache.values():
            ts=_parse_iso_dt((entry or {}).get("updated_at"))
            if ts:
                nnow=datetime.now(ts.tzinfo) if ts.tzinfo else now
                if (nnow-ts).total_seconds() < st.session_state.market_cache_hours*3600:
                    fresh+=1
        cstat1,cstat2,cstat3=st.columns(3)
        cstat1.metric("Fresh market searches",fresh)
        cstat2.metric("Cached yard rankings",len(st.session_state.get("opportunity_cache",{})))
        cstat3.metric("Cloud boot calls","0 normally")
        if st.button("REFRESH YARD RANKINGS"):
            invalidate_rankings()
            st.success("Rankings marked for refresh.")
        if st.button("CLEAR MARKET CACHE"):
            st.session_state.market_cache={}
            invalidate_rankings()
            mark_memory_dirty("market cache cleared")
            st.success("Market cache cleared.")

    if setup_page=="Memory":
        st.markdown("#### App memory")
        st.caption("V8.8 uses strict JSON-safe cloud persistence; NaN/Infinity values are converted to null automatically.")

        connected=bool(st.session_state.get("supabase_connected",False))
        configured=cloud_memory_enabled()
        source=supabase_config_source()
        url,key,table,memory_id=supabase_config()

        if connected:
            st.success("✅ Supabase connected")
        elif configured:
            st.warning("⚠️ Supabase is configured, but the last connection test did not confirm access.")
        else:
            st.error("❌ Supabase is not configured. The app is using local temporary memory only.")

        st.caption(st.session_state.get("supabase_connection_status","Not tested yet."))
        if configured:
            project_host=url.replace("https://","").replace("http://","")
            st.markdown(
                f"<div class='card'><b>Cloud configuration</b><br>"
                f"<span class='small'>Source: {source}<br>"
                f"Project: {project_host}<br>"
                f"Table: {table}<br>"
                f"Memory ID: {memory_id}</span></div>",
                unsafe_allow_html=True
            )

        st.session_state.memory_autosave=st.toggle(
            "Auto-save memory",value=st.session_state.memory_autosave,
            help="Saves inventory, market cache, OEM numbers, route, purchases, listings, sales and settings."
        )
        st.session_state.cloud_autosave_seconds=st.slider(
            "Minimum time between cloud saves",60,600,int(st.session_state.cloud_autosave_seconds),30,
            help="120–300 seconds is recommended for a faster-feeling app."
        )
        st.info(st.session_state.get("memory_status",""))

        c1,c2,c3=st.columns(3)
        if c1.button("🔌 TEST CONNECTION",type="primary"):
            ok,msg=test_cloud_memory_connection()
            st.session_state.supabase_connected=ok
            st.session_state.supabase_connection_status=msg
            st.success(msg) if ok else st.error(msg)

        if c2.button("☁️ SAVE TO CLOUD NOW"):
            try:
                ok,msg=save_cloud_memory()
                st.session_state.supabase_connected=True
                st.session_state.supabase_connection_status=msg
                st.session_state["_memory_dirty"]=False
                st.session_state["_cloud_memory_pending"]=False
                st.session_state["_last_cloud_save_ts"]=time.time()
                st.success(msg)
            except Exception as e:
                st.session_state.supabase_connected=False
                st.session_state.supabase_connection_status=str(e)
                st.error(str(e))

        if c3.button("⬇️ LOAD FROM CLOUD"):
            try:
                ok,msg=load_cloud_memory()
                st.session_state.supabase_connected=True
                st.session_state.supabase_connection_status=msg
                st.success(msg) if ok else st.warning(msg)
            except Exception as e:
                st.session_state.supabase_connected=False
                st.session_state.supabase_connection_status=str(e)
                st.error(str(e))

        # Only show editable fallback fields if permanent secrets are absent.
        if source!="Streamlit Secrets":
            with st.expander("Temporary Supabase settings",expanded=True):
                with st.form("temporary_supabase_v85"):
                    temp_url=st.text_input("Supabase project URL",value=st.session_state.supabase_url,
                                           placeholder="https://xxxxx.supabase.co")
                    temp_key=st.text_input("Supabase publishable key",value=st.session_state.supabase_key,type="password")
                    temp_table=st.text_input("Memory table",value=st.session_state.supabase_table)
                    temp_id=st.text_input("Memory ID",value=st.session_state.memory_id)
                    save_temp=st.form_submit_button("USE TEMPORARY SETTINGS")
                if save_temp:
                    st.session_state.supabase_url=temp_url.strip()
                    st.session_state.supabase_key=temp_key.strip()
                    st.session_state.supabase_table=temp_table.strip() or "app_memory"
                    st.session_state.memory_id=temp_id.strip() or "wichita-parts"
                    st.session_state["_supabase_probe_done"]=False
                    st.warning("Temporary settings loaded. Put them in Streamlit Secrets so they survive restarts.")

        st.markdown("##### Streamlit Secrets")
        st.code(
            'SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"\n'
            'SUPABASE_KEY = "sb_publishable_YOUR_KEY"\n'
            'SUPABASE_TABLE = "app_memory"\n'
            'SUPABASE_MEMORY_ID = "wichita-parts"',
            language="toml"
        )
        st.caption("Important: the Memory ID must be `wichita-parts` because that is the row allowed by the RLS policy we created.")

    if setup_page=="LKQ prices":
        st.caption("The scanner tries the live Wichita PYP price routes first. If PYP serves a JavaScript shell instead of the table, V8.6 fills the app-relevant categories from a verified Wichita fallback snapshot rather than returning zero rows.")
        if st.button("💲 SYNC WICHITA LKQ PART PRICES",type="primary"):
            try:
                st.session_state.live_prices=parse_price_list()
                invalidate_rankings()
                mark_memory_dirty("LKQ prices")
                st.success(f"Loaded {len(st.session_state.live_prices)} usable Wichita price rows.")
            except Exception as e:
                st.error(str(e))

        source=st.session_state.get("lkq_price_source","Not synced")
        st.info(f"Price source: {source}")

        if st.session_state.get("lkq_price_errors"):
            with st.expander("Price sync diagnostics"):
                st.write(st.session_state.lkq_price_errors)

        if st.session_state.live_prices:
            pshow=pd.DataFrame([{"part":k,**v} for k,v in st.session_state.live_prices.items()])
            st.dataframe(pshow.sort_values("part"),use_container_width=True,hide_index=True)

    if setup_page=="Impact":
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
                invalidate_rankings()
                mark_memory_dirty("impact")
                st.success("Impact risk saved and will affect part scores.")

    if setup_page=="Shipping":
        with st.form("selling_assumptions_v82"):
            ebay_fee=st.slider("eBay fee assumption",0.0,.25,float(st.session_state.ebay_fee),.01)
            ebay_quick=st.slider("eBay quick-sale % of market",.60,1.0,float(st.session_state.ebay_quick_pct),.01)
            local_fee=st.slider("Local selling fee",0.0,.15,float(st.session_state.local_fee),.01)
            local_pct=st.slider("Local price % of internet market",.50,1.0,float(st.session_state.local_price_pct),.01)
            travel=st.number_input("Travel allocated per part",0.0,200.0,float(st.session_state.travel),5.0)
            packing=st.number_input("Packing materials per shipped part",0.0,100.0,float(st.session_state.packing),1.0)
            save_sell=st.form_submit_button("SAVE SELLING ASSUMPTIONS")
        if save_sell:
            st.session_state.ebay_fee=ebay_fee
            st.session_state.ebay_quick_pct=ebay_quick
            st.session_state.local_fee=local_fee
            st.session_state.local_price_pct=local_pct
            st.session_state.travel=travel
            st.session_state.packing=packing
            invalidate_rankings()
            mark_memory_dirty("selling assumptions")
            st.success("Selling assumptions saved.")

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

    if setup_page=="Watchlist":
        with st.form("watchlist_form_v82"):
            txt=st.text_area("Watch keywords, one per line",value="\n".join(st.session_state.watch_keywords))
            alert_value=st.number_input("High-value car alert threshold (top-5 profit)",0.0,10000.0,float(st.session_state.high_value_alert),50.0)
            save_watch=st.form_submit_button("SAVE WATCHLIST")
        if save_watch:
            st.session_state.watch_keywords=[x.strip() for x in txt.splitlines() if x.strip()]
            st.session_state.high_value_alert=alert_value
            mark_memory_dirty("watchlist")
            st.success("Watchlist saved.")

    if setup_page=="Backup":
        payload=json.dumps(backup_payload(),indent=2,default=str)
        st.download_button("⬇️ DOWNLOAD FULL V8 BUSINESS BACKUP",payload.encode(),"wichita_scanner_v8_backup.json","application/json")
        bup=st.file_uploader("Restore V8 backup",type=["json"],key="v6backup")
        if bup and st.button("RESTORE BACKUP"):
            try:
                restore_payload(json.load(bup))
                st.session_state["_memory_dirty"]=False
                st.session_state["_cloud_memory_pending"]=False
                st.success("Backup restored.")
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
st.caption("V8 is a decision aid, not a guarantee. Verify physical condition, exact OEM number/interchange, programming requirements, shipping restrictions, yard price/core/fees and current marketplace rules before buying. Internet MARKET values may be active asking prices unless sold comps are imported.")

# Persist changed state after each Streamlit rerun.
try:
    autosave_memory_if_changed()
except Exception:
    pass
