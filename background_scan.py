#!/usr/bin/env python3
"""Wichita LKQ V10.3 cloud market + VIN-fitment scanner.

Runs outside Streamlit in GitHub Actions.

Behavior:
- Re-syncs the entire Wichita yard twice daily through GitHub Actions.
- Batch-decodes yard VINs with NHTSA vPIC and saves trim/series/equipment data in Supabase.
- Compares the new inventory snapshot with the previous Supabase snapshot.
- Prioritizes newly arrived cars for market pricing.
- Progressively market-scans every quick-pull vehicle/part combination in the
  current yard and stores the result in Supabase so progress survives restarts.
- Reuses one market result across duplicate year/make/model donor cars.
- Once full coverage is reached, only new or stale market rows are refreshed.
- Option-sensitive recommendations are VIN/trim gated so base cars do not get premium-option parts.
- Engine-sensitive parts are gated by decoded fuel/engine/turbo data and use engine-specific market comps.
- Full engines/transmissions are not in this background recommendation catalog.
"""
import os, re, time, uuid, base64, statistics, hashlib
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

INV_URL="https://www.pyp.com/inventory/wichita-1246/"
YARD_ID="wichita-1246"

# Small / quick-pull catalog only. No complete engines or transmissions.
PARTS=[
    # part, LKQ cost estimate, pull min, ship estimate, min year, eBay query term
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
    # Engine-specific high-value parts. These are VIN/engine gated below.
    ("Turbocharger",101.0,75,25.0,2000,"OEM turbocharger"),
    ("Diesel High Pressure Fuel Pump",72.6,90,18.0,2003,"OEM diesel high pressure fuel injection pump"),
    ("Diesel Injector Set",90.0,100,13.0,2003,"OEM diesel fuel injector set"),
]
PART_MAP={p[0]:p for p in PARTS}


NHTSA_BATCH_URL="https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVINValuesBatch/"
OPTION_SENSITIVE_PARTS={
    "LED Headlight Assembly","LED Tail Light Assembly","Power Folding Mirror",
    "Camera / ADAS Module","Radar Sensor","Amplifier","OEM Navigation Screen",
}
STRICT_DECODE_PARTS={
    "Power Folding Mirror","Camera / ADAS Module","Radar Sensor","Amplifier","OEM Navigation Screen",
}
ENGINE_SENSITIVE_PARTS={
    "Turbocharger","Diesel High Pressure Fuel Pump","Diesel Injector Set",
}
DIESEL_ENGINE_PARTS={"Diesel High Pressure Fuel Pump","Diesel Injector Set"}
TURBO_ENGINE_PARTS={"Turbocharger"}
BASE_TRIM_TERMS={"BASE","WORK TRUCK","WT","TRADESMAN","EXPRESS","FLEET","COMMERCIAL","CARGO","POLICE","XL","LS","LX"}
PREMIUM_TRIM_TERMS={
    "LIMITED","PLATINUM","DENALI","HIGH COUNTRY","KING RANCH","LARIAT","TITANIUM","TOURING",
    "ELITE","RESERVE","PREMIER","PREMIUM","LUXURY","OVERLAND","SUMMIT","SIGNATURE","ULTIMATE",
    "AVENIR","INSCRIPTION","PRESTIGE","SEL PREMIUM","HIGH ALTITUDE"
}
PREMIUM_AUDIO_TERMS={"BOSE","B&O","BANG OLUFSEN","JBL","HARMAN","KARDON","MARK LEVINSON","BURMESTER","MERIDIAN","SONY"}

def clean_upper(v):
    return re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper()).strip()

def contains_term(text,term):
    t=clean_upper(text); q=clean_upper(term)
    return bool(re.search(r"(?:^| )"+re.escape(q)+r"(?: |$)",t))

def profile_trim_text(profile):
    if not profile: return ""
    return " ".join(str(profile.get(k) or "") for k in ("trim","trim2","series","series2"))

def is_base_trim(profile):
    t=profile_trim_text(profile)
    return any(contains_term(t,x) for x in BASE_TRIM_TERMS)

def is_premium_trim(profile):
    t=profile_trim_text(profile)
    return any(contains_term(t,x) for x in PREMIUM_TRIM_TERMS)

def feature_value(profile,*keys):
    d=(profile or {}).get("decoded") or {}
    if not isinstance(d,dict): return ""
    for k in keys:
        v=d.get(k)
        if v not in (None,""):
            return str(v)
    return ""

def feature_standard(v):
    t=clean_upper(v)
    return t in {"STANDARD","YES","STD"} or t.startswith("STANDARD ")

def feature_unavailable(v):
    t=clean_upper(v)
    return t in {"NOT AVAILABLE","NO","N A","NA"}


def engine_details(profile):
    """Return normalized VIN-decoded engine identity used for applicability and market keys."""
    profile=profile or {}
    decoded=profile.get("decoded") or {}
    if not isinstance(decoded,dict):
        decoded={}
    fuel=str(profile.get("fuel_type") or decoded.get("FuelTypePrimary") or "").strip()
    model=str(profile.get("engine_model") or decoded.get("EngineModel") or "").strip()
    disp=str(profile.get("displacement_l") or decoded.get("DisplacementL") or "").strip()
    cyl=str(profile.get("engine_cylinders") or decoded.get("EngineCylinders") or "").strip()
    turbo=str(profile.get("turbo") or decoded.get("Turbo") or "").strip()
    return {"fuel":fuel,"model":model,"displacement_l":disp,"cylinders":cyl,"turbo":turbo}

def _yesish(v):
    t=clean_upper(v)
    return t in {"YES","Y","TRUE","1","STANDARD","STD"} or t.startswith("YES ")

def _noish(v):
    t=clean_upper(v)
    return t in {"NO","N","FALSE","0","NOT AVAILABLE","N A","NA"}

def is_diesel_profile(profile):
    d=engine_details(profile)
    return "DIESEL" in clean_upper(d["fuel"]) or "DIESEL" in clean_upper(d["model"])

def is_turbo_profile(profile):
    d=engine_details(profile)
    if _yesish(d["turbo"]):
        return True
    if _noish(d["turbo"]):
        return False
    # Some decodes place forced induction in engine/series text rather than Turbo.
    txt=" ".join([d["model"],str((profile or {}).get("series") or ""),str((profile or {}).get("trim") or "")])
    return any(x in clean_upper(txt) for x in (
        "TURBO","TURBOCHARGED","ECOBOOST","TDI","TFSI","TSI",
        "POWER STROKE","POWERSTROKE","DURAMAX","ECODIESEL"
    ))


def is_common_rail_profile(car,profile):
    """Conservative common-rail inference for the diesel HPFP recommendation."""
    if not is_diesel_profile(profile):
        return False
    y=int(car.get("year") or 0)
    make=clean_upper(car.get("make"))
    model=clean_upper(car.get("model"))
    d=engine_details(profile)
    txt=clean_upper(" ".join([d["model"],str((profile or {}).get("series") or ""),model]))
    # Known common-rail families / naming.
    if any(t in txt for t in ("DURAMAX","ECODIESEL","BLUETEC","CDI","CRD")):
        return True
    # Cummins pickup common rail began for the 2003 model era.
    if ("RAM" in make or "DODGE" in make) and any(t in model for t in ("2500","3500")) and y>=2003:
        return True
    # Ford 6.4/6.7 are common rail; 6.0/7.3 HEUI are intentionally excluded.
    if "FORD" in make and any(t in model for t in ("F 250","F 350","F 450","F 550","SUPER DUTY")):
        if any(t in txt for t in ("6 4","6 7")) or y>=2008:
            return True
        return False
    # VW/Audi common-rail TDI generally starts in the 2009-era U.S. applications.
    if ("VOLKSWAGEN" in make or "AUDI" in make) and "TDI" in txt and y>=2009:
        return True
    # Modern BMW/Mercedes passenger diesels are common rail.
    if any(t in make for t in ("BMW","MERCEDES")) and y>=2003:
        return True
    return False

def engine_label(profile):
    d=engine_details(profile)
    bits=[]
    disp_term=f'{d["displacement_l"]}L' if d["displacement_l"] else ""
    if disp_term and disp_term.upper() not in str(d["model"]).upper():
        bits.append(disp_term)
    if d["model"]:
        bits.append(d["model"])
    if d["fuel"]:
        bits.append(d["fuel"])
    if d["turbo"]:
        bits.append(f'Turbo {d["turbo"]}')
    return " · ".join(dict.fromkeys(bits))

def engine_key(profile):
    """Stable discriminator for engine-sensitive market comps."""
    d=engine_details(profile)
    raw="|".join(clean_upper(d[k]) for k in ("fuel","model","displacement_l","cylinders","turbo"))
    return raw or "ENGINE UNKNOWN"

def donor_engine_fitment(car,part,profile):
    """Hard gate engine-specific parts. Unknown/nonmatching engines are never recommended."""
    if part not in ENGINE_SENSITIVE_PARTS:
        return True,"Engine not required","Standard/non-engine-sensitive",1.0
    if not profile:
        return False,"Engine unknown","No VIN engine decode saved for this donor",0.0
    label=engine_label(profile) or "decoded engine"
    if part=="Diesel High Pressure Fuel Pump":
        if is_common_rail_profile(car,profile):
            return True,"Engine-confirmed",f"VIN/year/model support a common-rail diesel HPFP ({label})",0.96
        return False,"Fuel-system mismatch",f"Diesel is not confirmed as a compatible common-rail HPFP application ({label})",0.96
    if part=="Diesel Injector Set":
        if is_diesel_profile(profile):
            return True,"Engine-confirmed",f"VIN confirms diesel powertrain ({label})",0.98
        return False,"Fuel mismatch",f"VIN does not confirm diesel ({label})",0.98
    if part in TURBO_ENGINE_PARTS:
        if is_turbo_profile(profile):
            return True,"Engine-confirmed",f"VIN confirms/strongly identifies turbocharged engine ({label})",0.95
        return False,"Turbo mismatch",f"VIN does not confirm turbocharging ({label})",0.95
    return False,"Engine unproven","Engine-specific part is not confirmed by VIN decode",0.0

def donor_option_fitment(car,part,profile):
    """Conservative donor-level gate for option-sensitive parts.

    NHTSA vPIC decodes what the manufacturer encoded in the VIN/Part 565 data.
    It does not expose every as-built package, so ambiguous premium options stay
    hidden instead of being presented as confirmed equipment.
    """
    if part not in OPTION_SENSITIVE_PARTS:
        return True,"VIN not required","Standard/non-option-sensitive",1.0

    vin=str(car.get("vin") or "").strip().upper()
    if len(vin)!=17 or not profile:
        if part in STRICT_DECODE_PARTS:
            return False,"VIN unknown","No saved VIN decode for this option-sensitive part",0.0
        return True,"Verify option","VIN data unavailable; physically verify LED/option content",0.35

    trim=profile_trim_text(profile).strip()
    base=is_base_trim(profile); premium=is_premium_trim(profile)
    ent=feature_value(profile,"EntertainmentSystem")

    if part=="OEM Navigation Screen":
        if "NAV" in clean_upper(ent):
            return True,"VIN-supported","NHTSA entertainment data references navigation",0.95
        if base:
            return False,"Trim mismatch",f"Decoded trim/series looks base/fleet ({trim or 'base trim'})",0.9
        if premium:
            return True,"Trim-likely",f"Upper trim ({trim}); verify screen/navigation physically",0.65
        return False,"Package unproven",f"VIN decoded {trim or 'trim unknown'} but does not prove factory navigation",0.25

    if part=="Amplifier":
        ent_u=clean_upper(ent)
        if any(clean_upper(x) in ent_u for x in PREMIUM_AUDIO_TERMS):
            return True,"VIN-supported","Decoded entertainment data indicates premium audio",0.95
        if base:
            return False,"Trim mismatch",f"Base/fleet trim ({trim or 'base trim'}) is unlikely to have premium amplifier",0.85
        if premium:
            return True,"Trim-likely",f"Upper trim ({trim}); verify Bose/B&O/JBL/etc. label",0.6
        return False,"Audio package unproven","VIN does not prove a separate premium-audio amplifier",0.25

    if part=="Power Folding Mirror":
        if base:
            return False,"Trim mismatch",f"Base/fleet trim ({trim or 'base trim'}) is unlikely to have power-fold mirrors",0.85
        if premium:
            return True,"Trim-likely",f"Upper trim ({trim}); verify fold switch/mirror connector",0.6
        return False,"Mirror option unproven","VIN does not prove the power-fold option",0.25

    if part=="Radar Sensor":
        vals=[feature_value(profile,"AdaptiveCruiseControl"),feature_value(profile,"ForwardCollisionWarning"),
              feature_value(profile,"DynamicBrakeSupport"),feature_value(profile,"CIB")]
        if any(feature_standard(v) for v in vals if v):
            return True,"VIN-supported","Decoded active-safety equipment supports a forward radar sensor",0.9
        if vals and all(feature_unavailable(v) for v in vals if v):
            return False,"Safety mismatch","Decoded active-safety fields show radar-related systems unavailable",0.9
        return False,"Radar option unproven","VIN data does not confirm a radar-equipped safety package",0.3

    if part=="Camera / ADAS Module":
        vals=[feature_value(profile,"LaneDepartureWarning"),feature_value(profile,"LaneKeepSystem"),
              feature_value(profile,"BackupCamera"),feature_value(profile,"ParkingAssist"),
              feature_value(profile,"ForwardCollisionWarning")]
        if any(feature_standard(v) for v in vals if v):
            return True,"VIN-supported","Decoded camera/safety equipment supports an ADAS/camera module",0.9
        if vals and all(feature_unavailable(v) for v in vals if v):
            return False,"Safety mismatch","Decoded camera/ADAS fields show these systems unavailable",0.9
        return False,"ADAS option unproven","VIN data does not confirm the relevant camera/ADAS package",0.3

    # LED head/tail lamps: vPIC normally does not encode the lamp technology.
    # Block the strongest base/fleet cases; otherwise retain the opportunity but
    # label it as a physical-verification item.
    if part in {"LED Headlight Assembly","LED Tail Light Assembly"}:
        if base:
            return False,"Trim mismatch",f"Base/fleet trim ({trim or 'base trim'}); LED package not assumed",0.75
        return True,"Verify LED",f"VIN trim {trim or 'decoded'}; verify LED vs halogen/incandescent physically",0.45

    return True,"Verify option","Option-sensitive part; physical verification required",0.4

VIN_SELECTED_FIELDS=[
    "VIN","ModelYear","Make","Model","Trim","Trim2","Series","Series2","BodyClass","DriveType",
    "EngineModel","EngineCylinders","DisplacementL","FuelTypePrimary","Turbo","TransmissionStyle",
    "TransmissionSpeeds","EntertainmentSystem","AdaptiveCruiseControl","ForwardCollisionWarning",
    "LaneDepartureWarning","LaneKeepSystem","BackupCamera","ParkingAssist","BlindSpotMon",
    "DynamicBrakeSupport","CIB","SemiautomaticHeadlampBeamSwitching","ErrorCode","ErrorText"
]

def decode_inventory_vins(cars,existing):
    """Decode only VINs not already cached. NHTSA batch endpoint accepts max 50."""
    todo=[]
    for car in cars:
        vin=str(car.get("vin") or "").strip().upper()
        if len(vin)==17 and vin not in existing:
            todo.append((vin,int(car.get("year") or 0)))
    # de-duplicate while keeping order
    seen=set(); todo=[x for x in todo if not (x[0] in seen or seen.add(x[0]))]
    cap=max(0,int(fnum("MAX_VIN_DECODES_PER_RUN",2500)))
    todo=todo[:cap] if cap else []
    saved=[]
    for i in range(0,len(todo),50):
        chunk=todo[i:i+50]
        data=";".join(f"{vin},{yr}" for vin,yr in chunk)
        r=requests.post(NHTSA_BATCH_URL,data={"format":"json","data":data},timeout=45)
        r.raise_for_status()
        results=(r.json() or {}).get("Results",[]) or []
        by_vin={str(x.get("VIN") or "").strip().upper():x for x in results if x.get("VIN")}
        for vin,yr in chunk:
            raw=by_vin.get(vin,{})
            selected={k:raw.get(k,"") for k in VIN_SELECTED_FIELDS if raw.get(k) not in (None,"")}
            row={
                "vin":vin,"model_year":int(raw.get("ModelYear") or yr or 0),
                "make":str(raw.get("Make") or ""),"model":str(raw.get("Model") or ""),
                "trim":str(raw.get("Trim") or ""),"trim2":str(raw.get("Trim2") or ""),
                "series":str(raw.get("Series") or ""),"series2":str(raw.get("Series2") or ""),
                "body_class":str(raw.get("BodyClass") or ""),"drive_type":str(raw.get("DriveType") or ""),
                "engine_model":str(raw.get("EngineModel") or ""),"fuel_type":str(raw.get("FuelTypePrimary") or ""),
                "engine_cylinders":str(raw.get("EngineCylinders") or ""),
                "displacement_l":str(raw.get("DisplacementL") or ""),
                "turbo":str(raw.get("Turbo") or ""),
                "transmission_style":str(raw.get("TransmissionStyle") or ""),
                "decoded":selected,"decode_status":"ok" if raw else "no_result",
                "decoded_at":now(),"error":str(raw.get("ErrorText") or "")[:500]
            }
            saved.append(row); existing[vin]=row
        time.sleep(.2)
    if saved:
        supa_upsert_many("scanner_vin_decode",saved,"vin",batch_size=250)
    return len(todo),existing

def now():
    return datetime.now(timezone.utc).isoformat()

def env(name,default=""):
    return os.getenv(name,default).strip()

def fnum(name,default):
    try: return float(env(name,str(default)))
    except Exception: return float(default)

def supa_headers(prefer=None,extra=None):
    key=env("SUPABASE_SERVICE_ROLE_KEY")
    h={"apikey":key,"Content-Type":"application/json","Accept":"application/json"}
    # New sb_secret_* keys are API keys, not JWTs. Legacy service-role JWTs
    # still need Authorization: Bearer.
    if key.startswith("eyJ"):
        h["Authorization"]=f"Bearer {key}"
    if prefer: h["Prefer"]=prefer
    if extra: h.update(extra)
    return h

def supa_url(table):
    return env("SUPABASE_URL").rstrip("/")+f"/rest/v1/{table}"

def supa_get(table,params=None):
    r=requests.get(supa_url(table),headers=supa_headers(),params=params or {},timeout=30)
    if not r.ok:
        raise RuntimeError(f"Supabase GET {table} failed {r.status_code}: {r.text[:500]}")
    return r.json()

def supa_get_all(table,params=None,page_size=1000):
    out=[]; start=0
    while True:
        end=start+page_size-1
        h=supa_headers(extra={"Range":f"{start}-{end}"})
        r=requests.get(supa_url(table),headers=h,params=params or {},timeout=30)
        if not r.ok:
            raise RuntimeError(f"Supabase GET ALL {table} failed {r.status_code}: {r.text[:500]}")
        rows=r.json()
        out.extend(rows)
        if len(rows)<page_size: break
        start+=page_size
    return out

def supa_upsert(table,row,on_conflict=None):
    params={"on_conflict":on_conflict} if on_conflict else None
    r=requests.post(
        supa_url(table),
        headers=supa_headers("resolution=merge-duplicates,return=minimal"),
        params=params,json=row,timeout=45
    )
    if not r.ok:
        raise RuntimeError(f"Supabase UPSERT {table} failed {r.status_code}: {r.text[:500]}")

def supa_upsert_many(table,rows,on_conflict=None,batch_size=400):
    if not rows: return
    for i in range(0,len(rows),batch_size):
        supa_upsert(table,rows[i:i+batch_size],on_conflict)

def supa_delete(table,params):
    r=requests.delete(supa_url(table),headers=supa_headers("return=minimal"),params=params,timeout=30)
    if not r.ok:
        raise RuntimeError(f"Supabase DELETE {table} failed {r.status_code}: {r.text[:500]}")

def parse_dt(value):
    try: return datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except Exception: return None

def parse_card(title,blob,url):
    m=re.match(r"^(19\d{2}|20\d{2})\s+(.+?)\s+([^·•]+?)(?:\s+[A-Za-z][A-Za-z ]{0,20}\s*[·•]|$)",title)
    if not m:
        parts=title.split()
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
    loc=" · ".join(x for x in [
        f"Section {sec.group(1).strip()}" if sec else "",
        f"Row {row.group(1)}" if row else "",
        f"Space {space.group(1)}" if space else ""
    ] if x) or "Location pending"
    return {
        "year":year,"make":make,"model":model,
        "stock_number":stock.group(0) if stock else "",
        "vin":vin.group(1).upper() if vin else "",
        "yard_location":loc,"available_date":avail.group(1) if avail else "",
        "source_url":url
    }

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
    s=requests.Session(); s.headers["User-Agent"]="Mozilla/5.0 WichitaPartsScanner/10.1"
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
    if not (cid and sec):
        raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET are missing")
    auth=base64.b64encode(f"{cid}:{sec}".encode()).decode()
    r=requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Authorization":f"Basic {auth}","Content-Type":"application/x-www-form-urlencoded"},
        data={"grant_type":"client_credentials","scope":"https://api.ebay.com/oauth/api_scope"},timeout=25
    )
    if not r.ok:
        raise RuntimeError(f"eBay OAuth failed {r.status_code}: {r.text[:300]}")
    return r.json()["access_token"]

def percentile(sorted_vals,p):
    if not sorted_vals: return None
    pos=(len(sorted_vals)-1)*p
    lo=int(pos); hi=min(lo+1,len(sorted_vals)-1); frac=pos-lo
    return sorted_vals[lo]*(1-frac)+sorted_vals[hi]*frac

def ebay_market(q,token):
    r=requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={"Authorization":f"Bearer {token}","X-EBAY-C-MARKETPLACE-ID":"EBAY_US"},
        params={"q":q,"limit":30,"filter":"conditions:{USED}"},timeout=25
    )
    if not r.ok:
        raise RuntimeError(f"eBay search failed {r.status_code}: {r.text[:260]}")
    vals=[]
    for x in r.json().get("itemSummaries",[]):
        try:
            p=float((x.get("price") or {}).get("value")); ship=0.0
            if x.get("shippingOptions"):
                ship=float(((x["shippingOptions"][0].get("shippingCost") or {}).get("value")) or 0)
            if p>0: vals.append(p+ship)
        except Exception:
            pass
    if len(vals)<3:
        return {"median":None,"n":len(vals),"p25":None,"p75":None,"spread_pct":None}
    vals=sorted(vals); med=statistics.median(vals)
    kept=[v for v in vals if med*.35 <= v <= med*2.5]
    if len(kept)>=3: vals=sorted(kept); med=statistics.median(vals)
    p25=percentile(vals,.25); p75=percentile(vals,.75)
    spread=((p75-p25)/max(med,1)*100) if p25 is not None and p75 is not None else None
    return {"median":float(med),"n":len(vals),"p25":float(p25),"p75":float(p75),"spread_pct":round(float(spread),1)}

def family_key(car):
    return f"{int(car.get('year') or 0)}|{str(car.get('make','')).strip()}|{str(car.get('model','')).strip()}".lower()

def market_id(year,make,model,part,engine_disc=""):
    raw=f"{int(year)}|{make}|{model}|{part}|{engine_disc if part in ENGINE_SENSITIVE_PARTS else ''}".lower().strip()
    return hashlib.sha1(raw.encode()).hexdigest()

def candidate_for(car,spec,profile=None):
    part,cost,pull,ship,min_year,term=spec
    year=int(car.get("year") or 0)
    if year<min_year:
        return None

    eng_ok,eng_status,eng_reason,eng_conf=donor_engine_fitment(car,part,profile)
    if not eng_ok:
        return None

    eng_disc=engine_key(profile) if part in ENGINE_SENSITIVE_PARTS else ""
    eng_label=engine_label(profile) if part in ENGINE_SENSITIVE_PARTS else ""
    eng_query=""
    if part in ENGINE_SENSITIVE_PARTS:
        d=engine_details(profile)
        qbits=[]
        disp_term=f'{d["displacement_l"]}L' if d["displacement_l"] else ""
        if disp_term and disp_term.upper() not in str(d["model"]).upper():
            qbits.append(disp_term)
        if d["model"]:
            qbits.append(d["model"])
        if d["fuel"]:
            qbits.append(d["fuel"])
        eng_query=" "+" ".join(dict.fromkeys(qbits)) if qbits else ""

    q=f"{year} {car['make']} {car['model']}{eng_query} {term} used"
    return {
        "id":market_id(year,car["make"],car["model"],part,eng_disc),
        "year":year,"make":car["make"],"model":car["model"],"part":part,
        "query":q,"cost":cost,"pull":pull,"ship":ship,
        "engine_key":eng_disc,"engine_label":eng_label,
        "engine_fit":eng_status,"engine_fit_reason":eng_reason,
        "engine_fit_confidence":eng_conf,
    }

def build_candidates(cars,vin_profiles):
    # Non-engine parts share one market price per year/make/model.
    # Engine-sensitive parts split by decoded engine so a 6.7 diesel never
    # shares injector/turbo comps with a gasoline or different-engine donor.
    fam={}
    for car in cars:
        fam.setdefault(family_key(car),[]).append(car)
    out=[]; seen=set()
    for donors in fam.values():
        representative=donors[0]
        for spec in PARTS:
            part=spec[0]
            if part in ENGINE_SENSITIVE_PARTS:
                for car in donors:
                    profile=vin_profiles.get(str(car.get("vin") or "").upper())
                    c=candidate_for(car,spec,profile)
                    if c and c["id"] not in seen:
                        out.append(c); seen.add(c["id"])
                continue

            eligible=False
            for car in donors:
                profile=vin_profiles.get(str(car.get("vin") or "").upper())
                allowed,_,_,_=donor_option_fitment(car,part,profile)
                if allowed:
                    eligible=True; break
            if not eligible:
                continue
            c=candidate_for(representative,spec,None)
            if c and c["id"] not in seen:
                out.append(c); seen.add(c["id"])
    return out

def cache_is_fresh(row,ttl_hours):
    dt=parse_dt(row.get("updated_at")) if row else None
    if dt is None: return False
    age=(datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()/3600
    return age<float(ttl_hours)

def query_market_candidate(c,token):
    try:
        m=ebay_market(c["query"],token)
        return {
            "id":c["id"],"year":c["year"],"make":c["make"],"model":c["model"],"part":c["part"],
            "query":c["query"],"engine_key":c.get("engine_key",""),"engine_label":c.get("engine_label",""),
            "market_median":m["median"],"market_n":int(m["n"]),
            "market_p25":m["p25"],"market_p75":m["p75"],"price_spread_pct":m["spread_pct"],
            "updated_at":now(),"last_error":""
        },None
    except Exception as e:
        return {
            "id":c["id"],"year":c["year"],"make":c["make"],"model":c["model"],"part":c["part"],
            "query":c["query"],"engine_key":c.get("engine_key",""),"engine_label":c.get("engine_label",""),
            "market_median":None,"market_n":0,
            "market_p25":None,"market_p75":None,"price_spread_pct":None,
            "updated_at":now(),"last_error":str(e)[:500]
        },str(e)

def dwmt(expected_net,pull,ship,n,spread_pct):
    spread=(float(spread_pct or 0)/100)
    conf=min(1,float(n or 0)/18); stability=max(0,1-min(spread,1.5)/1.5)
    pph=max(expected_net,0)/max(pull/60,.1)
    score=(min(max(expected_net,0)/250,1)*30 + min(pph/300,1)*25 + conf*15 + stability*10 +
           (1-min(pull/75,1))*12 + (1-min(ship/75,1))*8)
    return round(max(0,min(score,100)),1)

def recommendation_from(car,cache_row,vin_profiles):
    spec=PART_MAP.get(cache_row.get("part"))
    med=cache_row.get("market_median")
    if not spec or med is None: return None
    part,cost,pull,ship,_,_=spec
    profile=vin_profiles.get(str(car.get("vin") or "").strip().upper())
    eng_ok,eng_status,eng_reason,eng_conf=donor_engine_fitment(car,part,profile)
    if not eng_ok:
        return None
    fit_ok,fit_status,fit_reason,fit_conf=donor_option_fitment(car,part,profile)
    if not fit_ok:
        return None
    med=float(med); fee=fnum("EBAY_FEE",.13); quick=fnum("EBAY_QUICK_SALE_PCT",.88)
    target=med*quick
    expected=target-target*fee-ship-cost
    score=dwmt(expected,pull,ship,int(cache_row.get("market_n") or 0),cache_row.get("price_spread_pct"))
    confidence=round(min(96,35+min(int(cache_row.get("market_n") or 0),25)*2.2),1)
    return {
        "id":f"{car['stock_number']}|{part}","stock_number":car["stock_number"],
        "car":f"{car['year']} {car['make']} {car['model']}","yard_location":car.get("yard_location","") or "Location pending",
        "part":part,"lkq_cost":cost,"market_median":round(med,2),"expected_net":round(expected,2),
        "pull_minutes":pull,"dwmt_score":score,
        "confidence":round(confidence*fit_conf*eng_conf if part in ENGINE_SENSITIVE_PARTS else (confidence*fit_conf if part in OPTION_SENSITIVE_PARTS else confidence),1),
        "vin":str(car.get("vin") or ""),"trim":str((profile or {}).get("trim") or ""),
        "series":str((profile or {}).get("series") or ""),"vin_fit":fit_status,"vin_fit_reason":fit_reason,
        "engine_model":engine_details(profile).get("model",""),
        "fuel_type":engine_details(profile).get("fuel",""),
        "displacement_l":engine_details(profile).get("displacement_l",""),
        "engine_cylinders":engine_details(profile).get("cylinders",""),
        "turbo":engine_details(profile).get("turbo",""),
        "engine_fit":eng_status,"engine_fit_reason":eng_reason,
        "created_at":now(),"last_seen_at":now(),"available_date":car.get("available_date","")
    }

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
    cars=[]; new_cars=[]; market_total=0; market_scanned=0; market_priced=0; market_cov=0.0
    vin_total=0; vin_decoded=0; vin_new_decodes=0; vin_profiles={}
    try:
        # 1) Entire yard inventory is re-synced every scheduled run.
        cars=fetch_inventory()
        if not cars:
            raise RuntimeError("Inventory parser returned zero cars; refusing to overwrite the previous yard snapshot.")

        prev=supa_get("scanner_background_state",{"id":f"eq.{YARD_ID}","select":"inventory"})
        old=(prev[0].get("inventory") or []) if prev else []
        old_stocks={str(x.get("stock_number","")) for x in old}
        # First ever run seeds a baseline; after that, every unseen stock is new.
        new_cars=[] if not old else [c for c in cars if c.get("stock_number") not in old_stocks]

        # 2) Decode VIN/trim/equipment once and persist it. The first V10.3 run
        # fills the current yard in NHTSA batches; later runs decode only new VINs.
        existing_vin_rows=supa_get_all("scanner_vin_decode",{
            "select":"vin,model_year,make,model,trim,trim2,series,series2,body_class,drive_type,engine_model,fuel_type,engine_cylinders,displacement_l,turbo,transmission_style,decoded,decode_status,decoded_at,error"
        })
        vin_profiles={str(r.get("vin") or "").upper():r for r in existing_vin_rows if r.get("vin")}
        vin_total=sum(1 for c in cars if len(str(c.get("vin") or "").strip())==17)
        vin_new_decodes,vin_profiles=decode_inventory_vins(cars,vin_profiles)
        vin_decoded=sum(1 for c in cars if str(c.get("vin") or "").upper() in vin_profiles)

        # 3) Build quick-pull market combinations only when at least one donor
        # actually passes VIN/trim fitment for that option-sensitive part.
        candidates=build_candidates(cars,vin_profiles)
        market_total=len(candidates)
        candidate_by_id={c["id"]:c for c in candidates}

        # 3) Read persistent cloud market cache so a restart never loses sweep progress.
        cached_rows=supa_get_all("scanner_market_cache",{
            "select":"id,year,make,model,part,query,engine_key,engine_label,market_median,market_n,market_p25,market_p75,price_spread_pct,updated_at,last_error"
        })
        cache={r["id"]:r for r in cached_rows if r.get("id")}
        ttl=fnum("MARKET_CACHE_HOURS",168)

        # 4) Newly arrived donor families always get first priority. If a matching
        # family/part price is already fresh, we reuse it instead of wasting an API call.
        priority=[]; priority_seen=set()
        for car in new_cars:
            profile=vin_profiles.get(str(car.get("vin") or "").upper())
            for spec in PARTS:
                allowed,_,_,_=donor_option_fitment(car,spec[0],profile)
                if not allowed: continue
                c=candidate_for(car,spec,profile)
                if c and c["id"] not in priority_seen and not cache_is_fresh(cache.get(c["id"]),ttl):
                    priority.append(c); priority_seen.add(c["id"])

        # 5) Continue the whole-yard backfill: missing first, then stale oldest first.
        missing=[]; stale=[]
        for c in candidates:
            if c["id"] in priority_seen: continue
            row=cache.get(c["id"])
            if not row:
                missing.append(c)
            elif not cache_is_fresh(row,ttl):
                stale.append((parse_dt(row.get("updated_at")) or datetime(1970,1,1,tzinfo=timezone.utc),c))
        stale=[c for _,c in sorted(stale,key=lambda x:x[0])]

        budget=max(1,int(fnum("MAX_MARKET_QUERIES_PER_RUN",1500)))
        selected=(priority+missing+stale)[:budget]

        # 6) Market-price the selected batch. Progress is saved after every run.
        if selected:
            token=ebay_token()
            workers=max(1,min(int(fnum("MARKET_WORKERS",8)),12,len(selected)))
            rows=[]; errors=[]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs={pool.submit(query_market_candidate,c,token):c for c in selected}
                for fut in as_completed(futs):
                    row,err=fut.result(); rows.append(row); ebay_calls+=1
                    if err: errors.append(err)
            supa_upsert_many("scanner_market_cache",rows,"id")
            for r in rows: cache[r["id"]]=r
            if errors:
                print(f"Market lookup errors: {len(errors)} (rows retained for retry after TTL)")

        # Coverage means every current-yard candidate has been attempted and cached.
        current_ids=set(candidate_by_id)
        market_scanned=sum(1 for mid in current_ids if mid in cache)
        market_priced=sum(1 for mid in current_ids if mid in cache and cache[mid].get("market_median") is not None)
        market_cov=round((market_scanned/max(market_total,1))*100,1)

        # 7) Materialize stock-specific recommendations from the cloud cache.
        # Duplicate donor cars reuse the same year/make/model price research.
        rec_rows=[]
        for car in cars:
            for spec in PARTS:
                profile=vin_profiles.get(str(car.get("vin") or "").upper())
                c=candidate_for(car,spec,profile)
                if not c: continue
                row=cache.get(c["id"])
                if not row: continue
                rec=recommendation_from(car,row,vin_profiles)
                if rec is not None:
                    rec_rows.append(rec)
        supa_upsert_many("scanner_recommendations",rec_rows,"id")

        # Remove recommendations for donor cars that have left the yard.
        current_stocks={c["stock_number"] for c in cars}
        existing_rec_stocks=supa_get_all("scanner_recommendations",{"select":"stock_number"})
        stale_stocks={str(r.get("stock_number","")) for r in existing_rec_stocks if r.get("stock_number")} - current_stocks
        for stock in stale_stocks:
            supa_delete("scanner_recommendations",{"stock_number":f"eq.{stock}"})

        # 8) New-car alerting uses every available quick-pull market result for that donor.
        min_profit=fnum("ALERT_MIN_PART_PROFIT",150); min_dwmt=fnum("ALERT_MIN_DWMT",65); min_donor=fnum("ALERT_MIN_DONOR_PROFIT",400)
        for car in new_cars:
            recs=[]
            for spec in PARTS:
                profile=vin_profiles.get(str(car.get("vin") or "").upper())
                c=candidate_for(car,spec,profile)
                if not c: continue
                row=cache.get(c["id"])
                if not row: continue
                rec=recommendation_from(car,row,vin_profiles)
                if rec and rec["expected_net"]>=min_profit and rec["dwmt_score"]>=min_dwmt:
                    recs.append(rec)
            donor_profit=sum(max(0,r["expected_net"]) for r in recs)
            if recs and donor_profit>=min_donor:
                engine_txt=engine_label(vin_profiles.get(str(car.get("vin") or "").upper()))
                lines=["🚨 V10.3 ENGINE+VIN LKQ ALERT",recs[0]["car"],f"Engine: {engine_txt or 'VIN engine not decoded'}",f"📍 {recs[0]['yard_location']}",f"Stock {car['stock_number']}",""]
                for r in sorted(recs,key=lambda x:x["dwmt_score"],reverse=True)[:4]:
                    lines.append(f"{r['part']}: ~${r['expected_net']:.0f} net · DWMT {r['dwmt_score']:.0f}/100 · {r['pull_minutes']} min")
                lines.append(f"Donor opportunity: ~${donor_profit:.0f}")
                msg="\n".join(lines)
                sent=send_telegram(msg)
                sent=send_email(f"V10.3 LKQ alert — {recs[0]['car']}",msg) or sent
                if sent: alerts_sent+=1
                supa_upsert("scanner_alerts",{"id":str(uuid.uuid4()),"created_at":now(),"stock_number":car["stock_number"],"car":recs[0]["car"],"message":msg,"sent":bool(sent)},"id")

        # 9) Store the complete current yard snapshot after successful processing.
        supa_upsert("scanner_background_state",{"id":YARD_ID,"inventory":cars,"updated_at":now()},"id")
        status="success"
        print(f"Yard {len(cars)} cars | new {len(new_cars)} | VIN {vin_decoded}/{vin_total} (+{vin_new_decodes}) | market {market_scanned}/{market_total} ({market_cov}%) | priced {market_priced} | eBay calls {ebay_calls}")
    except Exception as e:
        status="error"; error=str(e)
        raise
    finally:
        try:
            supa_upsert("scanner_runs",{
                "id":run_id,"started_at":started,"finished_at":now(),
                "status":status if 'status' in locals() else 'error',
                "cars_found":len(cars),"new_cars":len(new_cars),"ebay_calls":ebay_calls,
                "alerts_sent":alerts_sent,"error":error,
                "market_total":market_total,"market_scanned":market_scanned,
                "market_priced":market_priced,"market_coverage_pct":market_cov,
                "vin_total":vin_total,"vin_decoded":vin_decoded,"vin_new_decodes":vin_new_decodes
            },"id")
        except Exception as loge:
            print("Could not log run:",loge)

if __name__=='__main__':
    main()
