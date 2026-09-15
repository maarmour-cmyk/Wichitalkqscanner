import streamlit as st
import pandas as pd
import numpy as np
import requests, re, time
from bs4 import BeautifulSoup
from datetime import datetime

st.set_page_config(
    page_title="Wichita Yard Profit Scanner V4",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
.block-container{padding-top:.65rem;padding-bottom:5rem;max-width:1100px}
h1{font-size:1.55rem!important;line-height:1.1}
h2{font-size:1.25rem!important}
div.stButton>button{width:100%;min-height:54px;font-size:1.05rem;font-weight:750;border-radius:12px}
div[data-testid="stMetric"]{border:1px solid rgba(128,128,128,.25);border-radius:12px;padding:.55rem}
input,textarea{font-size:16px!important}
.ycard{border:1px solid rgba(128,128,128,.30);border-radius:14px;padding:.8rem;margin:.55rem 0}
.newtag{font-weight:800}
@media(max-width:700px){.block-container{padding-left:.65rem;padding-right:.65rem}h1{font-size:1.35rem!important}}
</style>
""", unsafe_allow_html=True)

YARD_NAME="Pick Your Part - Wichita"
YARD_ADDRESS="700 E 21st St N, Wichita, KS 67214"
INV_URL="https://www.pyp.com/inventory/wichita-1246/"
PRICE_URL="https://www.pyp.com/locations/LKQ_Pick_Your_Part_-_Wichita-246/prices/"

PARTS=pd.DataFrame([
["LED Headlight Assembly",55,20,35,.10,.93,450],
["LED Tail Light Assembly",45,15,25,.08,.90,240],
["OEM Infotainment / Radio",45,20,25,.12,.92,350],
["Instrument Cluster",35,15,20,.12,.88,240],
["ECU / ECM / PCM",45,15,15,.20,.86,275],
["Body Control Module",30,15,12,.20,.80,175],
["ABS Module / Pump",70,30,25,.18,.80,280],
["Turbocharger",95,60,40,.25,.82,650],
["Diesel High Pressure Fuel Pump",70,75,35,.28,.78,600],
["Diesel Injector Set",90,90,30,.30,.75,750],
["Power Folding Mirror",40,20,20,.08,.92,240],
["Camera / ADAS Module",30,15,12,.18,.84,300],
["Radar Sensor",35,15,12,.20,.82,380],
["Amplifier",28.50,20,15,.12,.86,260],
["OEM Navigation Screen",45,20,20,.12,.88,400],
["Climate Control Panel",25,10,12,.08,.89,150],
["Steering Wheel Controls",20,15,12,.08,.86,120],
["Transfer Case",180,120,90,.25,.68,650],
["Rear Differential",180,150,110,.25,.66,550],
["Transmission",300,210,180,.35,.62,950],
["Engine Assembly",500,300,250,.40,.58,1600],
],columns=["part","yard_cost","pull_minutes","shipping","return_risk","demand_score","starter_resale"])

MAKE_MULT={
"bmw":1.25,"mercedes-benz":1.28,"mercedes":1.28,"audi":1.23,"lexus":1.18,
"cadillac":1.14,"lincoln":1.10,"ram":1.08,"gmc":1.07,"toyota":1.08,
"ford":1.03,"chevrolet":1.02,"honda":1.04,"jeep":1.04
}

def norm(x): return re.sub(r"\s+"," ",str(x).strip().lower())
def money(x):
    try: return f"${float(x):,.0f}"
    except: return "—"

def http_session():
    s=requests.Session()
    s.headers.update({
        "User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
        "Accept-Language":"en-US,en;q=0.9"
    })
    return s

def parse_inventory_html(html, source_url):
    soup=BeautifulSoup(html,"html.parser")
    text=soup.get_text(" ",strip=True).replace("\xa0"," ")
    # Vehicle starts are highly stable on PYP: YEAR MAKE MODEL, followed by stock/yard data.
    starts=list(re.finditer(r"\b((?:19[8-9]\d|20[0-2]\d))\s+([A-Z][A-Z0-9\-]+)\s+([A-Z0-9][A-Z0-9&\-/ ]{0,45}?)(?=\s+(?:[A-Z][a-z]+)\s*[·•]?\s*1246-\d+|\s+Color:)", text, re.I))
    rows=[]
    for i,m in enumerate(starts):
        chunk=text[m.start():(starts[i+1].start() if i+1<len(starts) else min(len(text),m.start()+1500))]
        year=int(m.group(1)); make=m.group(2).strip(); model=re.sub(r"\s+"," ",m.group(3)).strip(" -")
        stock=re.search(r"(1246-\d+)",chunk)
        vin=re.search(r"\bVIN:?\s*([A-HJ-NPR-Z0-9]{17})\b",chunk,re.I)
        available=re.search(r"\bAvailable:?\s*(\d{1,2}/\d{1,2}/\d{4})",chunk,re.I)

        # Supports both "Section CAR | Row 46 | Space 1" and "Section: CAR Row: 46 Space: 1"
        sec=re.search(r"\bSection:?\s*([A-Za-z]+)",chunk,re.I)
        row=re.search(r"\bRow:?\s*([A-Za-z0-9\-]+)",chunk,re.I)
        space=re.search(r"\bSpace:?\s*([A-Za-z0-9\-]+)",chunk,re.I)
        color=""
        c=re.search(r"(?:Color:\s*|"+re.escape(model)+r"\s+)([A-Za-z]+)(?:\s*[·•]|\s+1246-)",chunk,re.I)
        if c: color=c.group(1)

        if stock or vin:
            rows.append({
                "year":year,"make":make.upper(),"model":model.upper(),
                "color":color,"stock_number":stock.group(1) if stock else "",
                "vin":vin.group(1).upper() if vin else "",
                "section":sec.group(1).upper() if sec else "",
                "yard_row":row.group(1) if row else "",
                "space":space.group(1) if space else "",
                "available_date":available.group(1) if available else "",
                "source_url":source_url
            })
    return pd.DataFrame(rows)

def sync_inventory(max_pages=60):
    s=http_session()
    all_rows=[]
    seen=set()
    pages=0
    consecutive_empty=0
    for page in range(1,max_pages+1):
        url=INV_URL if page==1 else f"{INV_URL}?page={page}"
        r=s.get(url,timeout=20)
        r.raise_for_status()
        df=parse_inventory_html(r.text,url)
        if len(df)==0:
            consecutive_empty+=1
            if consecutive_empty>=2: break
            continue
        consecutive_empty=0
        new_count=0
        for _,x in df.iterrows():
            key=x["stock_number"] or x["vin"]
            if key and key not in seen:
                seen.add(key); all_rows.append(x.to_dict()); new_count+=1
        pages=page
        # Repeated page/end-of-pagination protection.
        if new_count==0: break
        time.sleep(.12)
    return pd.DataFrame(all_rows),pages

def parse_price_list():
    s=http_session()
    r=s.get(PRICE_URL,timeout=20); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    prices={}
    # Table parsing without pandas.read_html/lxml dependency.
    for tr in soup.find_all("tr"):
        cells=[c.get_text(" ",strip=True) for c in tr.find_all(["th","td"])]
        if len(cells)>=3:
            name=cells[0].strip().upper()
            vals=[]
            for c in cells[1:]:
                m=re.search(r"\$([\d,]+(?:\.\d+)?)",c)
                vals.append(float(m.group(1).replace(",","")) if m else np.nan)
            if name and any(pd.notna(v) for v in vals):
                prices[name]={"total":vals[0] if len(vals)>0 else np.nan,
                              "price":vals[1] if len(vals)>1 else np.nan,
                              "core":vals[2] if len(vals)>2 else np.nan}
    return prices

def score_part(vehicle,p):
    resale=float(p.starter_resale)
    y=int(vehicle.get("year") or 2015)
    if y>=2021: resale*=1.28
    elif y>=2018: resale*=1.15
    elif y>=2014: resale*=1.05
    elif y<2008: resale*=.76
    resale*=MAKE_MULT.get(norm(vehicle.get("make","")),1.0)

    text=f"{vehicle.get('make','')} {vehicle.get('model','')}".lower()
    if any(k in text for k in ["2500","3500","super duty","f-250","f-350","duramax","cummins","diesel"]):
        if p.part in ["Turbocharger","Diesel High Pressure Fuel Pump","Diesel Injector Set","Transfer Case"]:
            resale*=1.28

    yard=float(p.yard_cost)
    # Replace selected starter costs from live Wichita price list when a reliable mapping exists.
    live=st.session_state.get("live_prices",{})
    map_names={
        "Amplifier":"AMPLIFIER",
        "ECU / ECM / PCM":"COMPUTER / ECM / ECU",
        "Climate Control Panel":"CLIMATE CONTROL",
        "Transfer Case":"TRANSFER CASE",
        "Transmission":"TRANSMISSION",
        "Engine Assembly":"ENGINE",
        "Turbocharger":"TURBO / SUPERCHARGER",
        "Power Folding Mirror":"MIRROR",
        "ABS Module / Pump":"ABS PUMP"
    }
    lname=map_names.get(p.part)
    if lname and lname in live and pd.notna(live[lname].get("price")):
        yard=float(live[lname]["price"])

    fee=resale*st.session_state.fee_pct
    net=resale-yard-float(p.shipping)-fee-st.session_state.travel-st.session_state.packing
    hrs=max(float(p.pull_minutes)/60,.1)
    pph=net/hrs
    roi=net/max(yard+st.session_state.travel+st.session_state.packing,1)
    sc=(np.clip(net/400*35,0,35)+np.clip(pph/150*25,0,25)+
        np.clip(roi/3*15,0,15)+float(p.demand_score)*15+(1-float(p.return_risk))*10)
    return round(yard,2),round(resale,2),round(net,2),round(pph,2),round(roi,2),round(float(np.clip(sc,0,100)),1)

def calculate_opportunities(inv):
    out=[]
    for idx,v in inv.iterrows():
        vd=v.to_dict()
        for _,p in PARTS.iterrows():
            yard,resale,net,pph,roi,score=score_part(vd,p)
            out.append({
                "vehicle_id":idx,
                "NEW":"🆕" if bool(vd.get("is_new",False)) else "",
                "CAR":f"{vd.get('year','')} {vd.get('make','')} {vd.get('model','')}".strip(),
                "STOCK":vd.get("stock_number",""),
                "SECTION":vd.get("section",""),"ROW":vd.get("yard_row",""),"SPACE":vd.get("space",""),
                "AVAILABLE":vd.get("available_date",""),"VIN":vd.get("vin",""),
                "PART":p.part,"BUY":yard,"SELL":resale,"PROFIT":net,"$/HR":pph,
                "ROI":roi,"MIN":int(p.pull_minutes),"SCORE":score
            })
    return pd.DataFrame(out)

# state
if "inventory" not in st.session_state: st.session_state.inventory=pd.DataFrame()
if "previous_stocks" not in st.session_state: st.session_state.previous_stocks=set()
if "live_prices" not in st.session_state: st.session_state.live_prices={}
if "fee_pct" not in st.session_state: st.session_state.fee_pct=.13
if "travel" not in st.session_state: st.session_state.travel=10
if "packing" not in st.session_state: st.session_state.packing=5
if "last_sync" not in st.session_state: st.session_state.last_sync="Never"

st.title("🔧 Wichita Yard Profit Scanner V4")
st.caption(f"{YARD_NAME} · {YARD_ADDRESS}")

tabs=st.tabs(["🔄 SYNC","🔥 BEST PULLS","🆕 NEW CARS","🚙 ALL CARS","💲 PRICES","⚙️ SETTINGS"])

with tabs[0]:
    st.subheader("Sync the entire Wichita yard")
    st.write("This walks the public Wichita inventory pages, combines them, and removes duplicates by stock number/VIN.")

    if st.button("🔄 SYNC WICHITA INVENTORY",type="primary"):
        with st.spinner("Reading Wichita inventory pages…"):
            try:
                old=set(st.session_state.inventory.get("stock_number",pd.Series(dtype=str)).astype(str)) if len(st.session_state.inventory) else st.session_state.previous_stocks
                df,pages=sync_inventory()
                if len(df):
                    current=set(df["stock_number"].astype(str))
                    new=current-old if old else set()
                    df["is_new"]=df["stock_number"].astype(str).isin(new)
                    st.session_state.previous_stocks=current
                    st.session_state.inventory=df
                    st.session_state.last_sync=datetime.now().strftime("%m/%d/%Y %I:%M %p")
                    st.success(f"Synced {len(df)} Wichita vehicles across {pages} page(s). {len(new)} are new since your previous in-app sync.")
                else:
                    st.error("The public inventory page returned no readable vehicles. LKQ may have changed the page; use snapshot import below.")
            except Exception as e:
                st.error(f"Sync failed: {e}")

    c1,c2,c3=st.columns(3)
    c1.metric("Vehicles",len(st.session_state.inventory))
    newn=int(st.session_state.inventory["is_new"].sum()) if len(st.session_state.inventory) and "is_new" in st.session_state.inventory else 0
    c2.metric("New",newn)
    c3.metric("Last sync",st.session_state.last_sync)

    st.markdown("#### Snapshot backup / restore")
    st.caption("Streamlit Community Cloud storage can reset when the app restarts. Download a snapshot after syncing; upload it later if you want new-car comparison to survive a restart.")
    if len(st.session_state.inventory):
        st.download_button("⬇️ DOWNLOAD INVENTORY SNAPSHOT",
            st.session_state.inventory.to_csv(index=False).encode(),
            "wichita_inventory_snapshot.csv","text/csv")
    snap=st.file_uploader("Restore previous inventory snapshot",type=["csv"],key="snapshot")
    if snap:
        prev=pd.read_csv(snap,dtype=str).fillna("")
        if "stock_number" in prev.columns:
            st.session_state.previous_stocks=set(prev["stock_number"].astype(str))
            st.success(f"Loaded {len(prev)} previous stock numbers for new-car comparison.")

with tabs[1]:
    st.subheader("Best pulls in the whole yard")
    if not len(st.session_state.inventory):
        st.info("Go to SYNC and tap SYNC WICHITA INVENTORY first.")
    else:
        a,b=st.columns(2)
        min_profit=a.number_input("Minimum net profit",0,3000,100,25)
        min_score=b.slider("Minimum score",0,100,60)
        opp=calculate_opportunities(st.session_state.inventory)
        good=opp[(opp["PROFIT"]>=min_profit)&(opp["SCORE"]>=min_score)].copy()
        good=good.sort_values(["SCORE","PROFIT","$/HR"],ascending=False)

        c1,c2,c3,c4=st.columns(4)
        c1.metric("Pulls",len(good))
        c2.metric("Best profit",money(good["PROFIT"].max()) if len(good) else "—")
        c3.metric("Best $/hr",money(good["$/HR"].max()) if len(good) else "—")
        c4.metric("Cars scored",good["STOCK"].nunique() if len(good) else 0)

        if len(good):
            # Donor ranking: don't sum every hypothetical part, only top 5.
            donors=(good.groupby(["NEW","CAR","STOCK","SECTION","ROW","SPACE","AVAILABLE"],dropna=False)
                    .agg(TOP5_PROFIT=("PROFIT",lambda s:round(s.nlargest(5).sum(),0)),
                         BEST_PART_PROFIT=("PROFIT","max"),
                         BEST_SCORE=("SCORE","max"),
                         GOOD_PARTS=("PART","count"))
                    .reset_index()
                    .sort_values(["TOP5_PROFIT","BEST_SCORE"],ascending=False))
            st.markdown("### 🚗 Best donor cars first")
            for _,r in donors.head(12).iterrows():
                st.markdown(f"""<div class="ycard">
                <b>{r['NEW']} {r['CAR']}</b><br>
                Row <b>{r['ROW']}</b> · Space <b>{r['SPACE']}</b> · {r['SECTION']}<br>
                Top-5 estimated profit <b>{money(r['TOP5_PROFIT'])}</b> · Best part {money(r['BEST_PART_PROFIT'])}<br>
                Stock {r['STOCK']} · Available {r['AVAILABLE']}
                </div>""",unsafe_allow_html=True)

            st.markdown("### 🔧 Best individual parts")
            showcols=["NEW","CAR","ROW","SPACE","PART","BUY","SELL","PROFIT","$/HR","MIN","SCORE","STOCK"]
            st.dataframe(good[showcols].head(250),use_container_width=True,hide_index=True)
            st.download_button("⬇️ DOWNLOAD FULL PULL SHEET",good.to_csv(index=False).encode(),
                               "wichita_v4_pull_sheet.csv","text/csv")

with tabs[2]:
    st.subheader("New since previous sync")
    if not len(st.session_state.inventory):
        st.info("Sync inventory first.")
    elif "is_new" not in st.session_state.inventory or not st.session_state.inventory["is_new"].any():
        st.info("No vehicles are flagged new yet. Sync once, then sync again later—or restore an older snapshot before syncing.")
    else:
        newcars=st.session_state.inventory[st.session_state.inventory["is_new"]].copy()
        st.metric("New donor cars",len(newcars))
        st.dataframe(newcars[["year","make","model","stock_number","section","yard_row","space","vin","available_date"]],
                     use_container_width=True,hide_index=True)
        newopp=calculate_opportunities(newcars)
        top=(newopp.sort_values(["SCORE","PROFIT"],ascending=False)
             [["CAR","ROW","SPACE","PART","BUY","SELL","PROFIT","$/HR","SCORE","STOCK"]]
             .head(100))
        st.markdown("### Best opportunities on new arrivals")
        st.dataframe(top,use_container_width=True,hide_index=True)

with tabs[3]:
    st.subheader("Complete Wichita inventory")
    if len(st.session_state.inventory):
        q=st.text_input("Search year / make / model / VIN / stock")
        x=st.session_state.inventory.copy()
        if q:
            mask=x.astype(str).apply(lambda c:c.str.contains(q,case=False,na=False)).any(axis=1)
            x=x[mask]
        st.write(f"{len(x)} vehicle(s)")
        st.dataframe(x,use_container_width=True,hide_index=True)
    else:
        st.info("Sync inventory first.")

with tabs[4]:
    st.subheader("Wichita parts price list")
    st.write("Optional: pull the current public Wichita price table and use matching base prices in scoring.")
    if st.button("💲 SYNC WICHITA PART PRICES"):
        try:
            with st.spinner("Reading Wichita price list…"):
                prices=parse_price_list()
            st.session_state.live_prices=prices
            st.success(f"Loaded {len(prices)} price entries.")
        except Exception as e:
            st.error(f"Price sync failed: {e}")
    if st.session_state.live_prices:
        pdf=pd.DataFrame([{"PART":k,**v} for k,v in st.session_state.live_prices.items()])
        st.dataframe(pdf,use_container_width=True,hide_index=True)
    st.caption("Scanner uses the base part price when it can confidently map a category. Verify core deposits, guarantee charges, taxes and exact part category at checkout.")

with tabs[5]:
    st.subheader("Profit assumptions")
    st.session_state.fee_pct=st.slider("Selling platform fee",0.0,.25,st.session_state.fee_pct,.01)
    st.session_state.travel=st.number_input("Travel allocation per part",0,100,st.session_state.travel,5)
    st.session_state.packing=st.number_input("Packing/materials per part",0,100,st.session_state.packing,1)
    st.markdown("### Wichita only")
    st.write(YARD_NAME)
    st.write(YARD_ADDRESS)
    st.link_button("OPEN PUBLIC WICHITA INVENTORY",INV_URL)
    st.link_button("OPEN WICHITA PART PRICES",PRICE_URL)

st.markdown("---")
st.caption("V4 reads ordinary public Pick Your Part pages only. It does not log in, evade CAPTCHAs, defeat rate limits, or bypass access controls. Inventory changes continuously; verify the vehicle and part are physically present before buying.")
