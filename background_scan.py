#!/usr/bin/env python3
"""V10 hourly Wichita LKQ background scanner.

Designed for GitHub Actions. It detects NEW arrivals, checks a small quick-pull
catalog against active USED eBay listings, writes results to Supabase, and can
send Telegram / Resend email alerts.
"""
import os, re, json, time, uuid, base64, statistics
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

INV_URL="https://www.pyp.com/inventory/wichita-1246/"
YARD="Pick Your Part - Wichita"

PARTS=[
    # part, LKQ cost estimate, pull min, ship estimate, min year, query term
    ("LED Headlight Assembly",55.0,20,24.0,2014,"OEM headlight assembly"),
    ("LED Tail Light Assembly",45.0,15,20.0,2012,"OEM tail light"),
    ("OEM Infotainment / Radio",44.5,20,13.0,2007,"OEM radio infotainment"),
    ("Instrument Cluster",39.0,15,13.0,2000,"OEM instrument cluster"),
    ("ECU / ECM / PCM",65.0,15,13.0,1996,"OEM ECM ECU PCM"),
    ("Body Control Module",44.5,15,13.0,2000,"OEM body control module BCM"),
    ("ABS Module / Pump",60.0,30,18.0,2000,"OEM ABS pump module"),
    ("Power Folding Mirror",32.5,20,24.0,2005,"OEM power mirror"),
    ("Camera / ADAS Module",30.0,15,13.0,2014,"OEM camera module"),
    ("Radar Sensor",35.0,15,13.0,2015,"OEM radar sensor"),
    ("Amplifier",28.5,20,13.0,2003,"OEM amplifier"),
    ("OEM Navigation Screen",45.0,20,13.0,2007,"OEM navigation display screen"),
    ("Climate Control Panel",31.0,10,13.0,2000,"OEM climate control panel"),
    ("Steering Wheel Controls",20.0,15,10.0,2004,"OEM steering wheel controls"),
]

def now(): return datetime.now(timezone.utc).isoformat()
def env(name,default=""): return os.getenv(name,default).strip()
def fnum(name,default):
    try: return float(env(name,str(default)))
    except: return float(default)

def supa_headers(prefer=None):
    key=env("SUPABASE_SERVICE_ROLE_KEY")
    h={"apikey":key,"Content-Type":"application/json"}
    # New Supabase sb_secret_* keys are API keys, not JWTs.
    # Only legacy JWT-form service_role keys belong in Authorization: Bearer.
    if key.startswith("eyJ"):
        h["Authorization"]=f"Bearer {key}"
    if prefer:
        h["Prefer"]=prefer
    return h

def supa_url(table): return env("SUPABASE_URL").rstrip("/")+f"/rest/v1/{table}"

def supa_get(table,params):
    r=requests.get(supa_url(table),headers=supa_headers(),params=params,timeout=20)
    if not r.ok:
        raise RuntimeError(f"Supabase GET {table} failed {r.status_code}: {r.text[:500]}")
    return r.json()

def supa_upsert(table,row,on_conflict=None):
    params={"on_conflict":on_conflict} if on_conflict else None
    r=requests.post(supa_url(table),headers=supa_headers("resolution=merge-duplicates,return=minimal"),params=params,json=row,timeout=20)
    if not r.ok:
        raise RuntimeError(f"Supabase UPSERT {table} failed {r.status_code}: {r.text[:500]}")

def parse_card(title,blob,url):
    m=re.match(r"^(19\d{2}|20\d{2})\s+(.+?)\s+([^·•]+?)(?:\s+[A-Za-z][A-Za-z ]{0,20}\s*[·•]|$)",title)
    if not m:
        parts=title.split();
        if len(parts)<3:return None
        year=int(parts[0]); make=parts[1]; model=" ".join(parts[2:])
    else:
        year=int(m.group(1)); make=m.group(2).strip(); model=m.group(3).strip()
    stock=re.search(r"\b1246-\d+\b",blob,re.I)
    vin=re.search(r"\bVIN\s*[:#]?\s*([A-HJ-NPR-Z0-9]{17})\b",blob,re.I)
    sec=re.search(r"\bSection\s*[:#]?\s*([^|·•]+?)(?=\s+Row\b|\s+Space\b|\s+Available\b|$)",blob,re.I)
    row=re.search(r"\bRow\s*[:#]?\s*([A-Za-z0-9-]+)",blob,re.I)
    space=re.search(r"\bSpace\s*[:#]?\s*([A-Za-z0-9-]+)",blob,re.I)
    avail=re.search(r"\bAvailable\s*[:#]?\s*(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})",blob,re.I)
    loc=" · ".join(x for x in [f"Section {sec.group(1).strip()}" if sec else "",f"Row {row.group(1)}" if row else "",f"Space {space.group(1)}" if space else ""] if x) or "Location pending"
    return {"year":year,"make":make,"model":model,"stock_number":stock.group(0) if stock else "","vin":vin.group(1).upper() if vin else "","yard_location":loc,"available_date":avail.group(1) if avail else "","source_url":url}

def parse_inventory(html,url):
    soup=BeautifulSoup(html,"html.parser"); rows=[]; seen=set()
    for h in soup.find_all(["h2","h3","h4"]):
        title=re.sub(r"\s+"," ",h.get_text(" ",strip=True).replace("\xa0"," ")).strip()
        if not re.match(r"^(?:19\d{2}|20\d{2})\s+",title): continue
        node=h; best=""
        for _ in range(7):
            if node is None: break
            c=re.sub(r"\s+"," ",node.get_text(" ",strip=True).replace("\xa0"," ")).strip()
            if "1246-" in c and "VIN" in c and "Available" in c:
                best=c
                if len(c)<1100: break
            node=node.parent
        if not best: continue
        r=parse_card(title,best,url)
        if r and r["stock_number"] and r["stock_number"] not in seen:
            seen.add(r["stock_number"]); rows.append(r)
    return rows

def fetch_inventory(max_pages=75):
    s=requests.Session(); s.headers["User-Agent"]="Mozilla/5.0 WichitaPartsScanner/10"
    all_rows=[]; seen=set(); empty=0
    for page in range(1,max_pages+1):
        url=INV_URL if page==1 else f"{INV_URL}?page={page}"
        r=s.get(url,timeout=25); r.raise_for_status()
        rows=parse_inventory(r.text,url)
        if not rows:
            empty+=1
            if empty>=2: break
            continue
        empty=0; new=0
        for x in rows:
            if x["stock_number"] not in seen:
                seen.add(x["stock_number"]); all_rows.append(x); new+=1
        if new==0: break
        time.sleep(.05)
    return all_rows

def ebay_token():
    cid=env("EBAY_CLIENT_ID"); sec=env("EBAY_CLIENT_SECRET")
    auth=base64.b64encode(f"{cid}:{sec}".encode()).decode()
    r=requests.post("https://api.ebay.com/identity/v1/oauth2/token",headers={"Authorization":f"Basic {auth}","Content-Type":"application/x-www-form-urlencoded"},data={"grant_type":"client_credentials","scope":"https://api.ebay.com/oauth/api_scope"},timeout=20)
    r.raise_for_status(); return r.json()["access_token"]

def ebay_median(q,token):
    r=requests.get("https://api.ebay.com/buy/browse/v1/item_summary/search",headers={"Authorization":f"Bearer {token}","X-EBAY-C-MARKETPLACE-ID":"EBAY_US"},params={"q":q,"limit":30,"filter":"conditions:{USED}"},timeout=20)
    r.raise_for_status(); vals=[]
    for x in r.json().get("itemSummaries",[]):
        try:
            p=float((x.get("price") or {}).get("value")); ship=0.0
            if x.get("shippingOptions"):
                ship=float(((x["shippingOptions"][0].get("shippingCost") or {}).get("value")) or 0)
            vals.append(p+ship)
        except: pass
    if len(vals)<3: return None,0,0
    vals=sorted(vals); med=statistics.median(vals)
    # Robustly trim extreme asks around median.
    kept=[v for v in vals if med*.35 <= v <= med*2.5]
    if len(kept)>=3: vals=kept; med=statistics.median(vals)
    p25=vals[max(0,int(len(vals)*.25)-1)]; p75=vals[min(len(vals)-1,int(len(vals)*.75))]
    spread=(p75-p25)/max(med,1)
    return float(med),len(vals),float(spread)

def dwmt(expected_net,pull,ship,n,spread):
    conf=min(1,n/18); stability=max(0,1-min(spread,1.5)/1.5)
    pph=max(expected_net,0)/max(pull/60,.1)
    score=min(max(expected_net,0)/250,1)*30+min(pph/300,1)*25+conf*15+stability*10+(1-min(pull/75,1))*12+(1-min(ship/75,1))*8
    return round(max(0,min(score,100)),1)

def send_telegram(text):
    token=env("TELEGRAM_BOT_TOKEN"); chat=env("TELEGRAM_CHAT_ID")
    if not token or not chat: return False
    r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",json={"chat_id":chat,"text":text,"disable_web_page_preview":True},timeout=20)
    r.raise_for_status(); return True

def send_email(subject,text):
    key=env("RESEND_API_KEY"); to=env("ALERT_EMAIL_TO"); sender=env("ALERT_EMAIL_FROM")
    if not (key and to and sender): return False
    r=requests.post("https://api.resend.com/emails",headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json={"from":sender,"to":[to],"subject":subject,"text":text},timeout=20)
    r.raise_for_status(); return True

def main():
    run_id=str(uuid.uuid4()); started=now(); error=""; alerts_sent=0; ebay_calls=0
    cars=[]; new_cars=[]
    try:
        cars=fetch_inventory()
        prev=supa_get("scanner_background_state",{"id":"eq.wichita-1246","select":"inventory"})
        old=(prev[0].get("inventory") or []) if prev else []
        old_stocks={str(x.get("stock_number","")) for x in old}
        # First run seeds state without blasting alerts for the whole yard.
        new_cars=[] if not old else [c for c in cars if c.get("stock_number") not in old_stocks]
        new_cars=new_cars[:int(fnum("MAX_NEW_CARS_PER_RUN",12))]

        token=ebay_token() if new_cars and env("EBAY_CLIENT_ID") and env("EBAY_CLIENT_SECRET") else None
        min_profit=fnum("ALERT_MIN_PART_PROFIT",150); min_dwmt=fnum("ALERT_MIN_DWMT",65); min_donor=fnum("ALERT_MIN_DONOR_PROFIT",400)
        max_parts=int(fnum("MAX_PARTS_PER_NEW_CAR",6)); fee=fnum("EBAY_FEE",.13); quick=fnum("EBAY_QUICK_SALE_PCT",.88)

        for car in new_cars:
            recs=[]
            for part,cost,pull,ship,min_year,term in PARTS:
                if int(car.get("year") or 0)<min_year: continue
                q=f"{car['year']} {car['make']} {car['model']} {term} used"
                med,n,spread=ebay_median(q,token); ebay_calls+=1
                if not med: continue
                target=med*quick
                expected=target-target*fee-ship-cost
                score=dwmt(expected,pull,ship,n,spread)
                confidence=round(min(96,35+min(n,25)*2.2),1)
                rec={"id":f"{car['stock_number']}|{part}","stock_number":car["stock_number"],"car":f"{car['year']} {car['make']} {car['model']}","yard_location":car.get("yard_location",""),"part":part,"lkq_cost":cost,"market_median":round(med,2),"expected_net":round(expected,2),"pull_minutes":pull,"dwmt_score":score,"confidence":confidence,"created_at":now()}
                supa_upsert("scanner_recommendations",rec,"id")
                if expected>=min_profit and score>=min_dwmt: recs.append(rec)
                if len(recs)>=max_parts: break
            donor_profit=sum(max(0,r["expected_net"]) for r in recs)
            if recs and donor_profit>=min_donor:
                lines=[f"🚨 V10 LKQ ALERT",f"{recs[0]['car']}",f"📍 {recs[0]['yard_location']}",f"Stock {car['stock_number']}",""]
                for r in sorted(recs,key=lambda x:x["dwmt_score"],reverse=True)[:4]:
                    lines.append(f"{r['part']}: ~${r['expected_net']:.0f} net · DWMT {r['dwmt_score']:.0f}/100 · {r['pull_minutes']} min")
                lines.append(f"Donor opportunity: ~${donor_profit:.0f}")
                msg="\n".join(lines)
                sent=send_telegram(msg)
                sent=send_email(f"V10 LKQ alert — {recs[0]['car']}",msg) or sent
                if sent: alerts_sent+=1
                supa_upsert("scanner_alerts",{"id":str(uuid.uuid4()),"created_at":now(),"stock_number":car["stock_number"],"car":recs[0]["car"],"message":msg,"sent":bool(sent)},"id")

        supa_upsert("scanner_background_state",{"id":"wichita-1246","inventory":cars,"updated_at":now()},"id")
        status="success"
    except Exception as e:
        status="error"; error=str(e)
        raise
    finally:
        try:
            supa_upsert("scanner_runs",{"id":run_id,"started_at":started,"finished_at":now(),"status":status if 'status' in locals() else 'error',"cars_found":len(cars),"new_cars":len(new_cars),"ebay_calls":ebay_calls,"alerts_sent":alerts_sent,"error":error},"id")
        except Exception as loge:
            print("Could not log run:",loge)

if __name__=='__main__':
    main()
