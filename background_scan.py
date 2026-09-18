#!/usr/bin/env python3
"""Wichita LKQ V10.5.1 cloud market + VIN-fitment scanner.

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
import os, re, time, uuid, base64, statistics, hashlib, json, math
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict, Counter
import requests
from bs4 import BeautifulSoup

INV_URL="https://www.pyp.com/inventory/wichita-1246/"
YARD_ID="wichita-1246"
PRICING_VERSION="10.5.1"

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

# V10.4.1 CAR-FIRST DISCOVERY -------------------------------------------------
# The curated PARTS list remains the known-good baseline, but V10.4 also asks
# eBay what parts are actually being offered for each exact donor configuration.
# Broad listing titles are clustered into part families, costed conservatively,
# and written to Supabase. This lets the scanner discover profitable niche parts
# that were never manually entered into PARTS.
#
# Demand note: eBay Browse exposes active inventory, not completed sales. When
# imported sold-comps exist in app_memory, V10.4 uses them for sell-through.
# Otherwise demand is explicitly marked as an ACTIVE-MARKET proxy with reduced
# confidence; active asking prices are never presented as confirmed sold prices.
DISCOVERY_BLOCK_TERMS={
    "complete engine","engine assembly","engine motor","motor engine","long block","short block",
    "jdm engine","crate engine","complete transmission","transmission assembly","transmission transaxle",
    "wheel tire","wheel and tire","rim tire","set of wheels","set of rims",
    "door shell","complete door","hood assembly","bumper assembly","truck bed","pickup bed",
    "complete seat","seat assembly","airbag","air bag","seat belt","seatbelt","catalytic converter",
    "windshield","quarter panel","fender assembly","roof assembly","rear axle assembly"
}

# pattern, normalized part, LKQ/fallback cost, pull min, ship, fitment rule
# This is intentionally much broader than the hand-curated recommendation list.
DISCOVERY_RULES=[
    (r"\b(hpfp|high pressure fuel pump|injection pump)\b","High Pressure Fuel Pump",72.6,90,18,"common_rail"),
    (r"\b(injector|fuel injector)s?\b","Fuel Injector / Injector Set",90.0,90,13,"diesel_or_di"),
    (r"\b(turbocharger|turbo charger|turbo)\b","Turbocharger",101.0,75,25,"turbo"),
    (r"\b(transfer case control|4wd control|4x4 control) module\b","Transfer Case / 4WD Control Module",44.5,15,12,"4wd"),
    (r"\b(tcm|transmission control module)\b","Transmission Control Module",44.5,15,12,"engine_exact"),
    (r"\b(ecm|ecu|pcm|engine control module|powertrain control module)\b","ECU / ECM / PCM",65.0,15,13,"engine_exact"),
    (r"\b(body control module|\bbcm\b)\b","Body Control Module",44.5,15,13,"general"),
    (r"\b(fuse box|junction box|smart junction|power distribution box)\b","Fuse / Junction Box",45.0,20,18,"general"),
    (r"\b(abs pump|abs module|anti lock brake)\b","ABS Module / Pump",60.0,30,18,"general"),
    (r"\b(instrument cluster|gauge cluster|speedometer cluster)\b","Instrument Cluster",39.0,15,13,"trim"),
    (r"\b(head.?up display|\bhud\b)\b","Head-Up Display Module",45.0,20,13,"premium"),
    (r"\b(navigation screen|nav screen|display screen|infotainment screen|touchscreen|touch screen)\b","Infotainment / Navigation Display",45.0,20,13,"nav"),
    (r"\b(radio receiver|radio module|infotainment radio|head unit)\b","OEM Radio / Head Unit",44.5,20,13,"trim"),
    (r"\b(amplifier|audio amp|sound amp)\b","Premium Audio Amplifier",28.5,20,13,"audio"),
    (r"\b(idrive controller|mmi controller|command controller|comand controller|radio control knob)\b","Infotainment Controller",25.0,10,10,"trim"),
    (r"\b(climate control|hvac control|temperature control|heater control)\b","Climate Control Panel",31.0,10,13,"trim"),
    (r"\b(heated seat module|seat control module|memory seat module)\b","Seat Control Module",35.0,15,12,"premium"),
    (r"\b(radar sensor|distance sensor|adaptive cruise radar)\b","Radar / Adaptive Cruise Sensor",35.0,15,13,"radar"),
    (r"\b(camera module|front camera|lane camera|adas camera|backup camera|rear view camera)\b","Camera / ADAS Module",30.0,15,13,"camera"),
    (r"\b(blind spot|blindspot).*(module|sensor)|\b(module|sensor).*(blind spot|blindspot)\b","Blind Spot Module / Sensor",30.0,15,13,"adas"),
    (r"\b(led headlight|headlamp assembly|headlight assembly)\b","Headlight Assembly",55.0,20,24,"lamp"),
    (r"\b(led tail light|taillight assembly|tail light assembly)\b","Tail Light Assembly",45.0,15,20,"lamp"),
    (r"\b(power fold|power folding).*(mirror)|\bmirror.*(power fold|power folding)\b","Power Folding Mirror",32.5,20,24,"power_mirror"),
    (r"\b(window master switch|master window switch|power window switch)\b","Window Master Switch",20.0,10,10,"trim"),
    (r"\b(headlight switch|lamp switch)\b","Headlight Switch",20.0,10,10,"general"),
    (r"\b(multifunction switch|turn signal switch|wiper switch)\b","Multifunction / Column Switch",22.0,15,10,"trim"),
    (r"\b(ignition switch|push start button|start stop button)\b","Ignition / Start Switch",20.0,10,10,"trim"),
    (r"\b(steering wheel control|steering wheel switch)\b","Steering Wheel Controls",20.0,15,10,"trim"),
    (r"\b(gear shifter|shifter assembly|shift selector)\b","Electronic Shifter / Selector",35.0,20,14,"transmission"),
    (r"\b(throttle body)\b","Throttle Body",45.0,25,14,"engine_exact"),
    (r"\b(intake manifold)\b","Intake Manifold",55.0,45,20,"engine_exact"),
    (r"\b(vacuum pump)\b","Vacuum Pump",45.0,35,14,"engine_exact"),
    (r"\b(oil cooler|oil filter housing)\b","Oil Cooler / Filter Housing",45.0,45,18,"engine_exact"),
    (r"\b(fuel rail)\b","Fuel Rail",40.0,35,14,"engine_exact"),
    (r"\b(maf|mass air flow) sensor\b","Mass Air Flow Sensor",25.0,10,10,"engine_exact"),
    (r"\b(map sensor|manifold pressure sensor|boost pressure sensor)\b","MAP / Boost Sensor",25.0,10,10,"engine_exact"),
    (r"\b(oxygen sensor|o2 sensor)\b","Oxygen Sensor",25.0,20,10,"engine_exact"),
    (r"\b(nox sensor)\b","NOx Sensor",30.0,20,10,"diesel"),
    (r"\b(def module|def pump|urea pump)\b","DEF Module / Pump",50.0,35,16,"diesel"),
    (r"\b(glow plug module|glow plug controller)\b","Glow Plug Control Module",35.0,15,10,"diesel"),
    (r"\b(alternator)\b","Alternator",40.5,25,18,"engine_exact"),
    (r"\b(starter motor|starter)\b","Starter",38.0,35,15,"engine_exact"),
    (r"\b(a/c compressor|ac compressor|air conditioning compressor)\b","A/C Compressor",64.0,50,20,"engine_exact"),
    (r"\b(electric power steering|eps module|steering control module)\b","Electric Power Steering Module",50.0,35,18,"trim"),
    (r"\b(steering angle sensor|clock spring|clockspring)\b","Clock Spring / Steering Angle Sensor",30.0,20,12,"trim"),
    (r"\b(door lock actuator|latch actuator|door latch)\b","Door Latch / Lock Actuator",25.0,20,12,"body"),
    (r"\b(trunk latch|liftgate latch|tailgate latch)\b","Liftgate / Tailgate Latch",25.0,15,12,"body"),
    (r"\b(power liftgate module|liftgate control module)\b","Power Liftgate Module",35.0,15,12,"premium"),
    (r"\b(sunroof motor|moonroof motor)\b","Sunroof Motor",35.0,25,12,"premium"),
    (r"\b(sunroof control|sunroof module|moonroof module)\b","Sunroof Control Module",35.0,15,10,"premium"),
    (r"\b(blower motor resistor|blower control module)\b","Blower Motor Control Module",25.0,15,10,"general"),
    (r"\b(hvac actuator|blend door actuator)\b","HVAC Door Actuator",20.0,15,10,"general"),
    (r"\b(park assist module|parking assist module)\b","Parking Assist Module",35.0,15,10,"adas"),
    (r"\b(trailer brake controller|trailer brake module)\b","Factory Trailer Brake Controller",30.0,15,10,"tow"),
    (r"\b(transfer case motor|4wd actuator|4x4 actuator)\b","Transfer Case Shift Motor",45.0,35,16,"4wd"),
    (r"\b(front differential actuator|axle disconnect actuator)\b","Front Axle Disconnect Actuator",35.0,30,14,"4wd"),
    (r"\b(fuel pump control module|fuel pump driver module)\b","Fuel Pump Control Module",30.0,15,10,"engine_exact"),
    (r"\b(immobilizer module|keyless entry module|smart key module)\b","Keyless / Immobilizer Module",35.0,15,10,"trim"),
    (r"\b(garage door module|homelink module)\b","HomeLink / Garage Module",20.0,10,8,"premium"),
    (r"\b(digital mirror|rear view mirror camera|auto dim mirror)\b","Auto-Dim / Camera Mirror",30.0,10,10,"premium"),
]

DISCOVERY_COMPILED=[(re.compile(p,re.I),name,cost,pull,ship,rule) for p,name,cost,pull,ship,rule in DISCOVERY_RULES]
DISCOVERY_STOPWORDS={
    "oem","genuine","used","factory","original","complete","assembly","assy","part","parts","for","with","without",
    "left","right","front","rear","upper","lower","driver","drivers","passenger","passengers","side","black","gray","grey",
    "tan","beige","white","silver","chrome","tested","working","good","condition","stock","lot","piece","pieces"
}

NHTSA_BATCH_URL="https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVINValuesBatch/"
OPTION_SENSITIVE_PARTS={
    "LED Headlight Assembly","LED Tail Light Assembly","Power Folding Mirror",
    "Camera / ADAS Module","Radar Sensor","Amplifier","OEM Navigation Screen",
}
STRICT_DECODE_PARTS={
    "Power Folding Mirror","Camera / ADAS Module","Radar Sensor","Amplifier","OEM Navigation Screen",
}
ENGINE_SENSITIVE_PARTS={
    "ECU / ECM / PCM","Turbocharger","Diesel High Pressure Fuel Pump","Diesel Injector Set",
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


def _disp_float(v):
    try:
        x=float(str(v).strip())
        return x if 0.5 <= x <= 10.0 else None
    except Exception:
        m=re.search(r"(\d(?:\.\d+)?)",str(v or ""))
        if not m: return None
        try:
            x=float(m.group(1))
            return x if 0.5 <= x <= 10.0 else None
        except Exception:
            return None

def _title_displacements(title):
    t=str(title or "")
    vals=[]
    for m in re.finditer(r"(?<!\d)([1-9](?:\.\d)?)\s*[lL]\b",t):
        try:
            x=float(m.group(1))
            if 0.5 <= x <= 10.0: vals.append(x)
        except Exception:
            pass
    # also recognize forms like 2000cc / 3600 cc
    for m in re.finditer(r"\b([1-9]\d{2,3})\s*cc\b",t,re.I):
        try:
            x=float(m.group(1))/1000.0
            if 0.5 <= x <= 10.0: vals.append(round(x,2))
        except Exception:
            pass
    return vals

def _title_cylinders(title):
    vals=[]
    for m in re.finditer(r"\b([3-8])\s*(?:cyl|cylinder|cylinders)\b",str(title or ""),re.I):
        try: vals.append(int(m.group(1)))
        except Exception: pass
    # V6/V8/V10 style
    for m in re.finditer(r"\bV([3468])\b",str(title or ""),re.I):
        try: vals.append(int(m.group(1)))
        except Exception: pass
    return vals

def _engine_model_tokens(profile):
    d=engine_details(profile)
    txt=clean_upper(d.get("model",""))
    stop={"ENGINE","MOTOR","DOHC","SOHC","VVT","VVT I","I4","L4","V6","V8","GASOLINE","DIESEL","TURBO"}
    toks=[]
    for tok in re.findall(r"[A-Z0-9][A-Z0-9.-]{2,}",txt):
        if tok in stop: continue
        if tok.replace(".","").isdigit(): continue
        # Prefer engine-code-like tokens: K24Z3, 2AZ-FE, N55B30, etc.
        if any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok):
            toks.append(tok)
    return list(dict.fromkeys(toks))[:5]

def listing_engine_compatible(title,profile,strict=False):
    """Reject eBay comps that explicitly describe a different engine.

    strict=True is used for engine-specific parts: a comp must positively match
    donor displacement or engine code, not merely share year/make/model.
    """
    if not profile:
        return (False,"No exact engine profile") if strict else (True,"No engine profile")
    d=engine_details(profile)
    t=clean_upper(title)
    expected_disp=_disp_float(d.get("displacement_l"))
    found_disp=_title_displacements(title)
    strong=False

    if expected_disp and found_disp:
        if not any(abs(x-expected_disp) <= 0.16 for x in found_disp):
            return False,f"Listing engine {','.join(f'{x:g}L' for x in found_disp)} != donor {expected_disp:g}L"
        strong=True

    try:
        expected_cyl=int(float(str(d.get("cylinders") or "0")))
    except Exception:
        expected_cyl=0
    found_cyl=_title_cylinders(title)
    if expected_cyl and found_cyl:
        if expected_cyl not in found_cyl:
            return False,f"Listing cylinder count {found_cyl} != donor {expected_cyl}"
        strong=True

    donor_diesel=is_diesel_profile(profile)
    says_diesel=any(x in t for x in (" DIESEL "," TDI "," CDI "," DURAMAX "," POWER STROKE "," POWERSTROKE "," ECODIESEL "))
    says_gas=any(x in t for x in (" GASOLINE "," GAS ENGINE "," PETROL "))
    if donor_diesel and says_gas:
        return False,"Listing says gasoline but donor is diesel"
    if (not donor_diesel) and says_diesel:
        return False,"Listing says diesel but donor is gasoline"
    if donor_diesel and says_diesel:
        strong=True

    model_tokens=_engine_model_tokens(profile)
    if model_tokens and any(tok in t for tok in model_tokens):
        strong=True

    if strict and not strong:
        return False,"Engine-specific comp does not positively identify donor engine"
    return True,"Exact/compatible engine"

def robust_listing_price(items,min_count=3):
    """Conservative active-market pricing with outlier resistance.

    Returns a lower-market recommendation rather than trusting one high ask.
    """
    clean=[]
    for x in items or []:
        try:
            v=float(x.get("price_total") if isinstance(x,dict) else x)
        except Exception:
            continue
        if 8 <= v <= 8000:
            clean.append((v,x if isinstance(x,dict) else {}))
    if len(clean)<min_count:
        return {"raw_median":None,"recommended":None,"p25":None,"p75":None,"n":len(clean),
                "spread_pct":None,"source":{},"sources":[]}

    vals=sorted(v for v,_ in clean)
    q1=percentile(vals,.25); q3=percentile(vals,.75)
    iqr=(q3-q1) if q1 is not None and q3 is not None else 0
    if len(vals)>=5 and iqr>0:
        lo=max(8,q1-1.5*iqr); hi=q3+1.5*iqr
        clean=[(v,x) for v,x in clean if lo <= v <= hi]
    if len(clean)<min_count:
        return {"raw_median":None,"recommended":None,"p25":None,"p75":None,"n":len(clean),
                "spread_pct":None,"source":{},"sources":[]}

    vals=sorted(v for v,_ in clean)
    raw=float(statistics.median(vals))
    p25=float(percentile(vals,.25))
    p35=float(percentile(vals,.35))
    p75=float(percentile(vals,.75))
    spread=((p75-p25)/max(raw,1))*100

    # If the market is noisy, lean harder toward the lower quartile.
    recommended=p25 if spread>85 else min(raw,p35)
    # Never recommend a price above 92% of the trimmed median from active asks.
    recommended=min(recommended,raw*.92)

    ranked=sorted(clean,key=lambda vx:abs(vx[0]-recommended))
    sources=[]
    for v,x in ranked[:5]:
        sources.append({
            "title":str(x.get("title") or "")[:300],
            "url":str(x.get("url") or ""),
            "landed":round(float(v),2)
        })
    source=sources[0] if sources else {}
    return {
        "raw_median":round(raw,2),"recommended":round(float(recommended),2),
        "p25":round(p25,2),"p75":round(p75,2),"n":len(vals),
        "spread_pct":round(float(spread),1),"source":source,"sources":sources
    }

OEM_STOP_TOKENS={
    "ENGINE","MOTOR","MODULE","CONTROL","UNIT","OEM","USED","GENUINE","ASSEMBLY","ASSY",
    "FORD","CHEVROLET","CHEVY","DODGE","RAM","TOYOTA","HONDA","NISSAN","BMW","AUDI","VOLKSWAGEN",
    "MERCEDES","HYUNDAI","KIA","LEXUS","SUBARU","MAZDA","JEEP","CHRYSLER","GMC"
}

def normalize_oem(v):
    return re.sub(r"[^A-Z0-9]","",clean_upper(v))

def extract_oem_candidates(title):
    text=clean_upper(title); out=[]
    for m in re.finditer(r"(?:PART\s*(?:NO|NUMBER|#)|P/?N|OEM\s*(?:NO|#)?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9.-]{4,24})",text,re.I):
        raw=m.group(1); n=normalize_oem(raw)
        if 6<=len(n)<=24 and any(c.isalpha() for c in n) and any(c.isdigit() for c in n): out.append(raw)
    seen={normalize_oem(x) for x in out}
    for raw in re.findall(r"\b[A-Z0-9][A-Z0-9.-]{5,22}\b",text):
        n=normalize_oem(raw)
        if not (6<=len(n)<=24) or raw in OEM_STOP_TOKENS or raw.isdigit(): continue
        if re.fullmatch(r"19\d{2}|20\d{2}",raw) or re.fullmatch(r"\d+(?:\.\d)?L",raw) or re.fullmatch(r"\d{2,3}K",raw): continue
        if not (any(c.isalpha() for c in n) and any(c.isdigit() for c in n)): continue
        if n not in seen: out.append(raw); seen.add(n)
    return out[:8]

def dominant_oem_number(items):
    counts=defaultdict(int); forms={}
    for x in items or []:
        local=set()
        for raw in extract_oem_candidates(x.get("title","")):
            n=normalize_oem(raw)
            if not n or n in local: continue
            local.add(n); counts[n]+=1; forms.setdefault(n,raw)
    if not counts: return "",0,0.0
    n,c=max(counts.items(),key=lambda kv:kv[1]); share=c/max(len(items),1)
    if c<2 or share<.34: return "",c,share
    return forms.get(n,n),c,share

def price_confidence_grade(n,spread_pct,source_url="",exact_engine=False,oem_exact=False,sold_count=0):
    n=int(n or 0); spread=float(spread_pct if spread_pct is not None else 999); points=0
    points += 3 if n>=12 else 2 if n>=7 else 1 if n>=3 else 0
    points += 3 if spread<=35 else 2 if spread<=60 else 1 if spread<=90 else 0
    if source_url: points+=1
    if exact_engine: points+=2
    if oem_exact: points+=3
    points += 3 if int(sold_count or 0)>=4 else 2 if int(sold_count or 0)>0 else 0
    return "HIGH" if points>=10 else "MEDIUM" if points>=6 else "LOW" if points>=3 else "REJECT"

def saturation_status(active_count,sold90):
    active=int(active_count or 0); sold=int(sold90 or 0)
    if sold>0:
        ratio=(sold/3)/max(active,1)
        if ratio>=.40: return "LOW SATURATION"
        if ratio>=.15: return "NORMAL"
        if ratio>=.05: return "HIGH SATURATION"
        return "VERY HIGH SATURATION"
    if active>=80: return "VERY HIGH SATURATION / NO SOLD PROOF"
    if active>=40: return "HIGH SATURATION / NO SOLD PROOF"
    return "SOLD DEMAND UNKNOWN"

def walkaway_reason_for(n,spread_pct,grade,active_count=0,sold90=0,expected_net=None,source_url="",fitment_ok=True,oem_required=False,oem_exact=False):
    if not fitment_ok: return "Fitment not confirmed"
    if int(n or 0)<3: return "Fewer than 3 usable comparable listings"
    if float(spread_pct if spread_pct is not None else 999)>130: return "Comparable prices are too spread out"
    if grade=="REJECT": return "Pricing confidence is too low"
    if not source_url: return "No auditable price-source listing"
    if oem_required and not oem_exact: return "Exact OEM part number not confirmed"
    if expected_net is not None and float(expected_net)<fnum("DISCOVERY_MIN_EXPECTED_NET",75): return "Expected net profit below minimum"
    if saturation_status(active_count,sold90).startswith("VERY HIGH SATURATION") and int(sold90 or 0)>0: return "Very poor sell-through / high saturation"
    return ""

def load_donor_conditions():
    try:
        rows=supa_get_all("scanner_donor_condition",{"select":"stock_number,impact_location,severity,flood,fire,interior_stripped,notes,updated_at"})
        return {str(r.get("stock_number") or ""):r for r in rows if r.get("stock_number")}
    except Exception as e:
        print("Donor condition read skipped:",e); return {}

def donor_condition_effect(stock,part_name,conditions):
    row=(conditions or {}).get(str(stock),{})
    if not row: return True,1.0,"No donor damage entered"
    loc=clean_upper(row.get("impact_location") or "NONE"); sev=clean_upper(row.get("severity") or "NONE"); part=clean_upper(part_name)
    if bool(row.get("flood")) and any(k in part for k in ("ECU","ECM","PCM","MODULE","RADIO","DISPLAY","SCREEN","AMPLIFIER","CAMERA","RADAR","CLUSTER","ELECTRONIC")):
        return False,0.0,"Flood-marked donor: electronics blocked"
    if bool(row.get("fire")): return False,0.0,"Fire-marked donor: recommendation blocked"
    if bool(row.get("interior_stripped")) and any(k in part for k in ("RADIO","DISPLAY","SCREEN","AMPLIFIER","CLUSTER","SWITCH","CONTROL","SEAT")):
        return False,0.0,"Interior marked stripped"
    loc_terms={"FRONT":("HEADLIGHT","RADAR","CAMERA","A/C COMPRESSOR","ALTERNATOR","TURBO","RADIATOR"),"REAR":("TAIL","LIFTGATE","TAILGATE","REAR CAMERA"),"LEFT":("LEFT","MIRROR","HEADLIGHT","TAIL"),"RIGHT":("RIGHT","MIRROR","HEADLIGHT","TAIL")}
    if any(k in part for k in loc_terms.get(loc,())):
        mult={"LIGHT":.88,"MODERATE":.68,"HEAVY":.35}.get(sev,.78)
        return (mult>=.5),mult,f"{loc.title()} {sev.title()} impact near this part"
    return True,1.0,f"Condition entered: {loc.title()} {sev.title()}"

def load_user_sales_learning():
    try:
        rows=supa_get("app_memory",{"id":"eq.wichita-parts","select":"payload","limit":"1"})
        if not rows: return {},[]
        payload=rows[0].get("payload") if isinstance(rows[0].get("payload"),dict) else {}
        purchases=payload.get("purchases") or []; sales=payload.get("sales") or []
        sale_by=defaultdict(list)
        for s in sales: sale_by[str(s.get("purchase_id") or "")].append(s)
        by_part=defaultdict(list)
        for b in purchases:
            pid=str(b.get("purchase_id") or ""); ss=sale_by.get(pid,[])
            if not ss: continue
            sale=sum(float(x.get("sale_price") or 0) for x in ss); fees=sum(float(x.get("fees") or 0) for x in ss); ship=sum(float(x.get("shipping") or 0) for x in ss); refund=sum(float(x.get("refund") or 0) for x in ss); cost=float(b.get("cost") or 0); predicted=float(b.get("market_at_buy") or 0)
            actual_profit=sale-fees-ship-refund-cost
            try:
                bd=parse_dt(b.get("date")); sds=[parse_dt(x.get("sold_date")) for x in ss]; sds=[x for x in sds if x]; sd=max(sds) if sds else None; days=max(0,(sd-bd).days) if bd and sd else None
            except Exception: days=None
            by_part[clean_upper(b.get("part") or "")].append({"actual_profit":actual_profit,"sale_price":sale,"predicted":predicted,"days":days})
        stats={}; out=[]
        for k,g in by_part.items():
            if not k: continue
            n=len(g); avg_profit=sum(x["actual_profit"] for x in g)/n; ds=[x["days"] for x in g if x["days"] is not None]; avg_days=sum(ds)/len(ds) if ds else None; ratios=[x["sale_price"]/x["predicted"] for x in g if x["predicted"]>0 and x["sale_price"]>0]; ratio=sum(ratios)/len(ratios) if ratios else 1.0; w=min(n/6,1); adj=(max(.75,min(1.10,ratio))*w)+(1.0*(1-w)); adj*=.90 if avg_profit<30 else 1; adj*=.92 if avg_days and avg_days>90 else 1; adj=max(.70,min(1.10,adj))
            stats[k]={"sales":n,"avg_profit":avg_profit,"avg_days":avg_days,"price_factor":adj}
            out.append({"part_key":k,"sales":n,"avg_actual_profit":round(avg_profit,2),"avg_days_to_sell":round(avg_days,1) if avg_days is not None else None,"actual_to_predicted_ratio":round(ratio,3),"learning_factor":round(adj,3),"updated_at":now()})
        return stats,out
    except Exception as e:
        print("Sales learning skipped:",e); return {},[]

def learned_factor(part_name,learned_stats):
    row=(learned_stats or {}).get(clean_upper(part_name),{}); return float(row.get("price_factor") or 1.0),row

def exact_oem_reprice(oem,part_name,profile,token):
    oem=str(oem or "").strip()
    if not oem: return None
    q=f'"{oem}" OEM {part_name} used'; m=ebay_market(q,token,profile,False)
    if not m.get("recommended") or int(m.get("n") or 0)<3: return None
    src=m.get("source") or {}; grade=price_confidence_grade(m.get("n"),m.get("spread_pct"),src.get("url"),True,True,0)
    return {"recommended_sell_price":m.get("recommended"),"market_median":m.get("median"),"active_count":int(m.get("n") or 0),"price_spread_pct":m.get("spread_pct"),"price_source_url":str(src.get("url") or ""),"price_source_title":str(src.get("title") or ""),"price_source_urls":m.get("sources") or [],"pricing_method":"exact verified OEM part-number interchange search","price_confidence_grade":grade,"oem_match_status":"EXACT VERIFIED OEM","interchange_query":q}

def recommendation_audit_row(rec,source_kind):
    rid=str(rec.get("id") or ""); evidence={"car":rec.get("car"),"stock_number":rec.get("stock_number"),"vin":rec.get("vin"),"engine_label":rec.get("engine_label") or " ".join(str(rec.get(k) or "") for k in ("displacement_l","engine_model","fuel_type")),"part":rec.get("part") or rec.get("part_name"),"oem_part_number":rec.get("oem_part_number"),"recommended_sell_price":rec.get("recommended_sell_price"),"market_median":rec.get("market_median") or rec.get("market_value"),"lkq_cost":rec.get("lkq_cost"),"expected_net":rec.get("expected_net"),"confidence_grade":rec.get("price_confidence_grade"),"source_url":rec.get("price_source_url"),"source_title":rec.get("price_source_title"),"pricing_method":rec.get("pricing_method"),"fitment_reason":rec.get("fitment_reason") or rec.get("engine_fit_reason"),"donor_condition_reason":rec.get("donor_condition_reason"),"learning_adjustment":rec.get("learning_adjustment"),"walkaway_reason":rec.get("walkaway_reason")}; fingerprint=json.dumps(evidence,sort_keys=True,default=str)
    return {"id":hashlib.sha1(f"{source_kind}|{rid}|{PRICING_VERSION}|{fingerprint}".encode()).hexdigest(),"recommendation_id":rid,"source_kind":source_kind,"stock_number":str(rec.get("stock_number") or ""),"part_name":str(rec.get("part") or rec.get("part_name") or ""),"pricing_version":PRICING_VERSION,"evidence":evidence,"created_at":now()}

def donor_engine_fitment(car,part,profile):
    """Hard gate engine-specific parts. Unknown/nonmatching engines are never recommended."""
    if part not in ENGINE_SENSITIVE_PARTS:
        return True,"Engine not required","Standard/non-engine-sensitive",1.0
    if not profile:
        return False,"Engine unknown","No VIN engine decode saved for this donor",0.0
    label=engine_label(profile) or "decoded engine"
    if part=="ECU / ECM / PCM":
        if engine_label(profile):
            return True,"Engine-confirmed",f"ECU pricing split by decoded donor engine ({label})",0.98
        return False,"Engine unknown","ECU is engine/calibration-sensitive and donor engine is not decoded",0.0
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
        return False,"Package unproven",f"VIN decoded {trim or 'trim unknown'} but does not prove factory navigation; physical verification required",0.25

    if part=="Amplifier":
        ent_u=clean_upper(ent)
        if any(clean_upper(x) in ent_u for x in PREMIUM_AUDIO_TERMS):
            return True,"VIN-supported","Decoded entertainment data indicates premium audio",0.95
        return False,"Audio package unproven","VIN does not prove a separate premium-audio amplifier; physical verification required",0.25

    if part=="Power Folding Mirror":
        return False,"Mirror option unproven","VIN does not reliably prove power-fold mirrors; physical verification required",0.25

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
        return False,"Lamp technology unproven",f"VIN trim {trim or 'decoded'} does not prove LED vs halogen/incandescent; physical verification required",0.3

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
    s=requests.Session(); s.headers["User-Agent"]="Mozilla/5.0 WichitaPartsScanner/10.5.1"
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

def ebay_market(q,token,profile=None,strict_engine=False):
    r=requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={"Authorization":f"Bearer {token}","X-EBAY-C-MARKETPLACE-ID":"EBAY_US"},
        params={"q":q,"limit":50,"filter":"conditions:{USED}"},timeout=25
    )
    if not r.ok:
        raise RuntimeError(f"eBay search failed {r.status_code}: {r.text[:260]}")
    items=[]
    rejected_engine=0
    for x in r.json().get("itemSummaries",[]):
        title=str(x.get("title") or "")
        ok,reason=listing_engine_compatible(title,profile,strict_engine)
        if not ok:
            rejected_engine+=1
            continue
        try:
            p=float((x.get("price") or {}).get("value")); ship=0.0
            if x.get("shippingOptions"):
                ship=float(((x["shippingOptions"][0].get("shippingCost") or {}).get("value")) or 0)
            total=p+ship
        except Exception:
            continue
        if total>0:
            items.append({
                "title":title,"price_total":total,
                "url":str(x.get("itemWebUrl") or ""),
                "item_id":str(x.get("itemId") or "")
            })
    stats=robust_listing_price(items,3)
    return {
        "median":stats["raw_median"],"recommended":stats["recommended"],
        "n":stats["n"],"p25":stats["p25"],"p75":stats["p75"],
        "spread_pct":stats["spread_pct"],"source":stats["source"],
        "sources":stats["sources"],"engine_rejected":rejected_engine
    }

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
        "engine_profile":{
            "fuel_type":engine_details(profile).get("fuel",""),
            "engine_model":engine_details(profile).get("model",""),
            "displacement_l":engine_details(profile).get("displacement_l",""),
            "engine_cylinders":engine_details(profile).get("cylinders",""),
            "turbo":engine_details(profile).get("turbo",""),
            "decoded":(profile or {}).get("decoded",{}) if profile else {}
        } if profile else None,
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
    if not row or str(row.get("pricing_version") or "") != PRICING_VERSION:
        return False
    dt=parse_dt(row.get("updated_at")) if row else None
    if dt is None: return False
    age=(datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()/3600
    return age<float(ttl_hours)

def query_market_candidate(c,token):
    try:
        strict=c.get("part") in ENGINE_SENSITIVE_PARTS
        m=ebay_market(c["query"],token,c.get("engine_profile"),strict)
        src=m.get("source") or {}
        return {
            "id":c["id"],"year":c["year"],"make":c["make"],"model":c["model"],"part":c["part"],
            "query":c["query"],"engine_key":c.get("engine_key",""),"engine_label":c.get("engine_label",""),
            "market_median":m["median"],"recommended_sell_price":m["recommended"],
            "market_n":int(m["n"]),"market_p25":m["p25"],"market_p75":m["p75"],
            "price_spread_pct":m["spread_pct"],
            "price_source_url":str(src.get("url") or ""),
            "price_source_title":str(src.get("title") or ""),
            "price_source_urls":m.get("sources") or [],
            "pricing_method":"trimmed eBay USED comps; conservative lower-market price",
            "engine_match_count":int(m["n"]),
            "price_confidence_grade":price_confidence_grade(m["n"],m["spread_pct"],src.get("url"),strict,False,0),
            "walkaway_reason":walkaway_reason_for(m["n"],m["spread_pct"],price_confidence_grade(m["n"],m["spread_pct"],src.get("url"),strict,False,0),source_url=src.get("url",""),fitment_ok=True),
            "evidence_json":{"query":c["query"],"engine_label":c.get("engine_label",""),"comps":m.get("sources") or []},
            "pricing_version":PRICING_VERSION,
            "updated_at":now(),"last_error":""
        },None
    except Exception as e:
        return {
            "id":c["id"],"year":c["year"],"make":c["make"],"model":c["model"],"part":c["part"],
            "query":c["query"],"engine_key":c.get("engine_key",""),"engine_label":c.get("engine_label",""),
            "market_median":None,"recommended_sell_price":None,"market_n":0,
            "market_p25":None,"market_p75":None,"price_spread_pct":None,
            "price_source_url":"","price_source_title":"","price_source_urls":[],
            "pricing_method":"V10.5 validation failed","engine_match_count":0,
            "price_confidence_grade":"REJECT","walkaway_reason":"Pricing validation failed",
            "evidence_json":{"query":c.get("query",""),"error":str(e)[:500]},
            "pricing_version":PRICING_VERSION,
            "updated_at":now(),"last_error":str(e)[:500]
        },str(e)

def dwmt(expected_net,pull,ship,n,spread_pct):
    spread=(float(spread_pct or 0)/100)
    conf=min(1,float(n or 0)/18); stability=max(0,1-min(spread,1.5)/1.5)
    pph=max(expected_net,0)/max(pull/60,.1)
    score=(min(max(expected_net,0)/250,1)*30 + min(pph/300,1)*25 + conf*15 + stability*10 +
           (1-min(pull/75,1))*12 + (1-min(ship/75,1))*8)
    return round(max(0,min(score,100)),1)

def recommendation_from(car,cache_row,vin_profiles,donor_conditions=None,learned_stats=None):
    spec=PART_MAP.get(cache_row.get("part"))
    med=cache_row.get("recommended_sell_price")
    if med is None:
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
    cond_ok,cond_mult,cond_reason=donor_condition_effect(car.get("stock_number"),part,donor_conditions)
    if not cond_ok: return None
    grade=str(cache_row.get("price_confidence_grade") or "LOW"); walk=str(cache_row.get("walkaway_reason") or "")
    if walk or grade=="REJECT": return None
    med=float(med); learn_mult,learn_row=learned_factor(part,learned_stats); med*=learn_mult
    fee=fnum("EBAY_FEE",.13); quick=fnum("EBAY_QUICK_SALE_PCT",.88)
    target=med*quick
    expected=target-target*fee-ship-cost
    score=dwmt(expected,pull,ship,int(cache_row.get("market_n") or 0),cache_row.get("price_spread_pct"))
    confidence=round(min(96,35+min(int(cache_row.get("market_n") or 0),25)*2.2),1)
    return {
        "id":f"{car['stock_number']}|{part}","stock_number":car["stock_number"],
        "car":f"{car['year']} {car['make']} {car['model']}","yard_location":car.get("yard_location","") or "Location pending",
        "part":part,"lkq_cost":cost,"market_median":round(float(cache_row.get("market_median") or med),2),
        "recommended_sell_price":round(med,2),"expected_net":round(expected,2),
        "price_source_url":str(cache_row.get("price_source_url") or ""),
        "price_source_title":str(cache_row.get("price_source_title") or ""),
        "pricing_method":str(cache_row.get("pricing_method") or ""),
        "price_confidence_grade":grade,"walkaway_reason":"",
        "donor_condition_reason":cond_reason,"learning_adjustment":round(learn_mult,3),
        "pull_minutes":pull,"dwmt_score":round(score*cond_mult,1),
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

def _discovery_vehicle_words(car,profile=None):
    words=[]
    for v in (car.get("make"),car.get("model"),(profile or {}).get("trim"),(profile or {}).get("series")):
        words += re.findall(r"[A-Z0-9]+",clean_upper(v))
    words += [str(car.get("year") or "")]
    return {w for w in words if len(w)>1}

def discovery_family_id(car,profile=None):
    # Exact enough to separate engines and meaningful trims, but duplicates share one scan.
    trim=clean_upper((profile or {}).get("trim") or (profile or {}).get("series") or "")
    raw="|".join([
        str(int(car.get("year") or 0)),clean_upper(car.get("make")),clean_upper(car.get("model")),
        engine_key(profile),trim
    ])
    return hashlib.sha1(raw.lower().encode()).hexdigest()

def discovery_trim_key(profile):
    return clean_upper((profile or {}).get("trim") or (profile or {}).get("series") or "")[:80]

def discovery_query(car,profile=None):
    bits=[str(int(car.get("year") or 0)),str(car.get("make") or ""),str(car.get("model") or "")]
    trim=str((profile or {}).get("trim") or "").strip()
    if trim and not is_base_trim(profile): bits.append(trim)
    d=engine_details(profile)
    if d.get("displacement_l"):
        bits.append(f'{d["displacement_l"]}L')
    if d.get("model"):
        bits.append(d["model"])
    bits += ["OEM","used"]
    return " ".join(x for x in bits if x).strip()

def ebay_discovery_search(q,token,limit=100):
    limit=max(10,min(int(limit),200))
    r=requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={"Authorization":f"Bearer {token}","X-EBAY-C-MARKETPLACE-ID":"EBAY_US"},
        params={"q":q,"limit":limit,"filter":"conditions:{USED}"},timeout=30
    )
    if not r.ok:
        raise RuntimeError(f"eBay discovery failed {r.status_code}: {r.text[:260]}")
    out=[]
    for rank,x in enumerate(r.json().get("itemSummaries",[]) or [],start=1):
        title=str(x.get("title") or "").strip()
        if not title: continue
        try:
            p=float((x.get("price") or {}).get("value") or 0)
            ship=0.0
            if x.get("shippingOptions"):
                ship=float(((x["shippingOptions"][0].get("shippingCost") or {}).get("value")) or 0)
            total=p+ship
        except Exception:
            continue
        if total<=0: continue
        seller=((x.get("seller") or {}).get("username") or "")
        out.append({
            "title":title,"price_total":total,"rank":rank,"seller":seller,
            "item_id":str(x.get("itemId") or ""),"url":str(x.get("itemWebUrl") or "")
        })
    return out

WHOLE_POWERTRAIN_PATTERNS=[
    # Common eBay whole-engine title formats:
    r"\bengine\s+motor\b",
    r"\bmotor\s+engine\b",
    r"\bjdm\b.{0,45}\bengine\b",
    r"\bengine\b.{0,25}\b(jdm|long\s*block|short\s*block|assembly|assy)\b",
    r"\b\d(?:\.\d)?l\b.{0,25}\bengine\b",
    r"\bengine\b.{0,25}\b\d(?:\.\d)?l\b",
    r"\b[34568]\s*cyl(?:inder)?\b.{0,25}\bengine\b",
    r"\bengine\b.{0,25}\b[34568]\s*cyl(?:inder)?\b",
    r"\bengine\b.{0,30}\b\d{2,3}k\s*miles?\b",
    # Complete gearbox/transaxle listings:
    r"\bcomplete\s+(automatic|manual)?\s*transmission\b",
    r"\btransmission\s+(assembly|assy|transaxle)\b",
    r"\bautomatic\s+transmission\b.{0,25}\b(miles?|assembly|assy)\b",
]

def _blocked_discovery_title(title):
    t=str(title or "").lower()
    if any(x in t for x in DISCOVERY_BLOCK_TERMS):
        return True
    return any(re.search(p,t,re.I) for p in WHOLE_POWERTRAIN_PATTERNS)

def classify_discovery_title(title,car,profile=None):
    if _blocked_discovery_title(title): return None
    text=str(title or "")
    # Ignore obvious non-parts/noise.
    if re.search(r"\b(manual|brochure|catalog|poster|emblem sticker|keychain|floor mat|carpet mat)\b",text,re.I):
        return None
    for rx,name,cost,pull,ship,rule in DISCOVERY_COMPILED:
        if rx.search(text):
            return {"part_name":name,"part_key":clean_upper(name),"lkq_cost":cost,"pull_minutes":pull,"ship_estimate":ship,"fitment_rule":rule}
    # Generic fallback: only keep electronic/module/switch/sensor-like items; these
    # are small enough to be useful, but receive lower confidence until learned.
    if re.search(r"\b(module|controller|control unit|switch|sensor|actuator|solenoid|pump|valve)\b",text,re.I):
        words=[w for w in re.findall(r"[A-Za-z0-9+.-]+",text) if clean_upper(w).lower() not in DISCOVERY_STOPWORDS]
        vehicle_words=_discovery_vehicle_words(car,profile)
        words=[w for w in words if clean_upper(w) not in vehicle_words and not re.fullmatch(r"19\d{2}|20\d{2}",w)]
        # Keep the first informative window. It is deliberately labeled inferred.
        phrase=" ".join(words[:7]).strip(" -/")
        if len(phrase)>=5:
            if re.search(r"\b(module|controller|control unit)\b",text,re.I): cost,pull,ship=44.5,15,12
            elif re.search(r"\bswitch\b",text,re.I): cost,pull,ship=20.0,10,10
            elif re.search(r"\bsensor\b",text,re.I): cost,pull,ship=25.0,15,10
            elif re.search(r"\bactuator\b",text,re.I): cost,pull,ship=35.0,20,14
            else: cost,pull,ship=45.0,30,15
            return {"part_name":phrase[:90],"part_key":clean_upper(phrase)[:120],"lkq_cost":cost,"pull_minutes":pull,"ship_estimate":ship,"fitment_rule":"inferred"}
    return None

def discovery_fitment(car,profile,rule,part_name):
    """Conservative exact-donor gate for discovered parts."""
    rule=str(rule or "general")
    if rule in {"general","body","inferred"}:
        return True,"General donor fit",0.65 if rule=="inferred" else 0.85
    if rule in {"engine_exact","transmission"}:
        if profile and engine_label(profile): return True,"Exact VIN engine profile",0.92
        return False,"Engine profile unknown",0.25
    if rule=="diesel":
        return (True,"VIN confirms diesel",0.97) if is_diesel_profile(profile) else (False,"Diesel not confirmed",0.97)
    if rule=="diesel_or_di":
        if is_diesel_profile(profile): return True,"VIN confirms diesel injector application",0.97
        # Gas direct injection is not reliably represented by vPIC; keep hidden unless engine naming clearly says DI/GDI.
        txt=clean_upper(" ".join([engine_label(profile),str((profile or {}).get("series") or "")]))
        if any(x in txt for x in ("GDI","DIRECT INJECTION","TFSI","TSI","ECOBOOST")):
            return True,"Decoded engine family indicates direct injection",0.78
        return False,"Injector type not confirmed",0.35
    if rule=="common_rail":
        return (True,"Common-rail diesel inferred from exact donor",0.95) if is_common_rail_profile(car,profile) else (False,"Common-rail HPFP not confirmed",0.95)
    if rule=="turbo":
        return (True,"VIN/engine confirms turbo",0.95) if is_turbo_profile(profile) else (False,"Turbo not confirmed",0.95)
    if rule=="4wd":
        drive=clean_upper((profile or {}).get("drive_type") or feature_value(profile,"DriveType"))
        ok=any(x in drive for x in ("4WD","4X4","AWD","ALL WHEEL","FOUR WHEEL"))
        return (True,f"Decoded drivetrain {drive}",0.9) if ok else (False,"4WD/AWD not confirmed",0.85)
    if rule=="premium":
        if is_premium_trim(profile): return True,"Upper trim supports premium option; verify physically",0.62
        return False,"Premium option not confirmed",0.3
    if rule in {"nav","audio","radar","camera","adas","lamp","power_mirror"}:
        map_part={
            "nav":"OEM Navigation Screen","audio":"Amplifier","radar":"Radar Sensor",
            "camera":"Camera / ADAS Module","adas":"Camera / ADAS Module",
            "lamp":"LED Headlight Assembly","power_mirror":"Power Folding Mirror"
        }[rule]
        ok,status,reason,conf=donor_option_fitment(car,map_part,profile)
        return ok,reason,conf
    if rule=="tow":
        # VIN does not reliably prove factory brake controller. Upper truck trim gets verify-only.
        if is_premium_trim(profile): return True,"Tow equipment possible; verify controller in cab",0.5
        return False,"Factory tow controller not confirmed",0.25
    if rule=="trim":
        return (True,"VIN trim/series decoded; verify exact option/connector",0.65) if profile else (False,"Trim unknown",0.25)
    return True,"Verify exact donor fit",0.5

def _sold_rows_from_memory():
    """Read optional imported sold comps from the user's existing app_memory row."""
    try:
        rows=supa_get("app_memory",{"id":"eq.wichita-parts","select":"payload","limit":"1"})
        if not rows: return []
        payload=rows[0].get("payload") or {}
        sold=payload.get("sold_comps") or [] if isinstance(payload,dict) else []
        return sold if isinstance(sold,list) else []
    except Exception as e:
        print("Sold-comp cloud read skipped:",e)
        return []

def _parse_sold_date(v):
    if not v: return None
    try:
        dt=datetime.fromisoformat(str(v).replace("Z","+00:00"))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        try:
            return datetime.strptime(str(v)[:10],"%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None

def sold_evidence_for(car,part_name,sold_rows,profile=None,fitment_rule="general"):
    if not sold_rows: return {"sold_median":None,"sold_count_90":0,"sold_count_all":0}
    y=int(car.get("year") or 0); ma=clean_upper(car.get("make")); mo=clean_upper(car.get("model")); pk=clean_upper(part_name)
    vals=[]; n90=0; cutoff=datetime.now(timezone.utc).timestamp()-90*86400
    part_tokens={x for x in pk.split() if len(x)>=4 and x not in {"CONTROL","MODULE","ASSEMBLY"}}
    strict=str(fitment_rule or "") in {"engine_exact","transmission","diesel","diesel_or_di","common_rail","turbo"}
    for r in sold_rows:
        try:
            ry=int(float(r.get("year") or 0))
        except Exception: ry=0
        if ry and abs(ry-y)>1: continue
        if ma and ma not in clean_upper(r.get("make")): continue
        if mo and mo not in clean_upper(r.get("model")): continue
        rp=clean_upper(r.get("part") or "")
        if part_tokens and not any(t in rp for t in part_tokens): continue
        engine_text=" ".join(str(r.get(k) or "") for k in ("part","title","engine","engine_label","notes"))
        ok,_=listing_engine_compatible(engine_text,profile,strict)
        if not ok: continue
        try: v=float(r.get("sold_price") or 0)
        except Exception: v=0
        if v<=0: continue
        vals.append(v)
        dt=_parse_sold_date(r.get("sold_date"))
        if dt and dt.timestamp()>=cutoff: n90+=1
    if len(vals)>=4:
        vals=sorted(vals); q1=percentile(vals,.25); q3=percentile(vals,.75); iqr=q3-q1
        if iqr>0:
            vals=[v for v in vals if max(5,q1-1.5*iqr) <= v <= q3+1.5*iqr]
    return {"sold_median":float(statistics.median(vals)) if len(vals)>=2 else None,
            "sold_count_90":n90 if len(vals)>=2 else 0,"sold_count_all":len(vals)}

def discovery_score(expected_net,pull,ship,active_count,sold90,median,rank_score,cost_conf=1.0):
    pph=max(expected_net,0)/max(pull/60,.12)
    profit_score=min(max(expected_net,0)/300,1)*28
    pph_score=min(pph/350,1)*25
    compact_score=(1-min(pull/90,1))*10 + (1-min(ship/80,1))*7
    if sold90>0:
        monthly=sold90/3
        sellthrough=min(monthly/max(active_count,1),1.5)/1.5
        demand_score=18*sellthrough + 7*min(sold90/15,1)
        demand_conf=1.0
    else:
        # Best-Match rank + repeated active listings is a proxy, not sales evidence.
        demand_score=10*rank_score + 4*min(active_count/8,1)
        demand_conf=.45
    score=(profit_score+pph_score+compact_score+demand_score)*cost_conf
    return round(max(0,min(score,100)),1),demand_conf

def discover_family(car,profile,token,sold_rows):
    q=discovery_query(car,profile)
    listings=ebay_discovery_search(q,token,int(fnum("DISCOVERY_RESULTS_PER_QUERY",100)))
    groups=defaultdict(list); meta={}
    strict_rules={"engine_exact","transmission","diesel","diesel_or_di","common_rail","turbo"}
    for x in listings:
        # Every listing is rejected if it explicitly names a different donor engine.
        ok,_reason=listing_engine_compatible(x["title"],profile,False)
        if not ok:
            continue
        c=classify_discovery_title(x["title"],car,profile)
        if not c: continue
        # Engine-specific part families must positively identify the exact donor engine.
        if c.get("fitment_rule") in strict_rules:
            ok,_reason=listing_engine_compatible(x["title"],profile,True)
            if not ok:
                continue
        key=c["part_key"]
        groups[key].append(x); meta[key]=c
    family_id=discovery_family_id(car,profile)
    rows=[]
    fee=fnum("EBAY_FEE",.13); quick=fnum("EBAY_QUICK_SALE_PCT",.88)
    for key,items in groups.items():
        if len(items)<1: continue
        c=meta[key]
        stats=robust_listing_price(items,3)
        if stats["recommended"] is None:
            continue
        med=float(stats["raw_median"]); active=int(stats["n"])
        # Median rank normalized: 1.0 is near top of Best Match results.
        avg_rank=sum(i["rank"] for i in items)/len(items)
        rank_score=max(0.0,1.0-min(avg_rank/max(len(listings),1),1.0))
        sold=sold_evidence_for(car,c["part_name"],sold_rows,profile,c["fitment_rule"])
        active_rec=float(stats["recommended"])
        if sold["sold_median"] is not None:
            # Real sold data wins, but don't let a stale/outlier sold median exceed
            # today's active comparable market by more than 15%.
            market=min(float(sold["sold_median"]),med*1.15)
        else:
            market=active_rec
        target=market*quick
        expected=target-target*fee-float(c["ship_estimate"])-float(c["lkq_cost"])
        fit_ok,fit_reason,fit_conf=discovery_fitment(car,profile,c["fitment_rule"],c["part_name"])
        # Generic inferred names get a conservative yard-cost confidence haircut.
        cost_conf=.72 if c["fitment_rule"]=="inferred" else .92
        score,demand_conf=discovery_score(expected,c["pull_minutes"],c["ship_estimate"],active,sold["sold_count_90"],market,rank_score,cost_conf)
        confidence=min(96,25+min(active,12)*2.5 + (30 if sold["sold_count_90"] else 0)) * fit_conf * cost_conf
        sellthrough=None; days=None
        if sold["sold_count_90"]>0:
            monthly=sold["sold_count_90"]/3
            sellthrough=round(min(999,(monthly/max(active,1))*100),1)
            days=round((active/max(monthly,.1))*30,1)
        examples=sorted(items,key=lambda x:x["rank"])[:3]
        src=stats.get("source") or {}
        detected_oem,oem_hits,oem_share=dominant_oem_number(items)
        exact_engine=c["fitment_rule"] in strict_rules
        grade=price_confidence_grade(active,stats.get("spread_pct"),src.get("url"),exact_engine,False,sold["sold_count_90"])
        sat=saturation_status(active,sold["sold_count_90"])
        walk=walkaway_reason_for(active,stats.get("spread_pct"),grade,active,sold["sold_count_90"],expected,src.get("url",""),fit_ok)
        rows.append({
            "id":hashlib.sha1(f"{family_id}|{key}".lower().encode()).hexdigest(),
            "family_id":family_id,"year":int(car.get("year") or 0),"make":str(car.get("make") or ""),"model":str(car.get("model") or ""),
            "trim_key":discovery_trim_key(profile),"engine_key":engine_key(profile),"engine_label":engine_label(profile),
            "part_name":c["part_name"],"part_key":key,"fitment_rule":c["fitment_rule"],
            "search_query":q,"example_title":examples[0]["title"][:300],
            "example_titles":[e["title"][:240] for e in examples],
            "active_median":round(med,2),"active_count":active,"active_rank_score":round(rank_score,3),
            "recommended_sell_price":round(market,2),
            "price_source_url":str(src.get("url") or ""),"price_source_title":str(src.get("title") or ""),
            "price_source_urls":stats.get("sources") or [],
            "pricing_method":"sold comps capped to current market" if sold["sold_median"] is not None else "trimmed active eBay lower-market comp",
            "engine_match_count":active,"pricing_version":PRICING_VERSION,
            "detected_oem_part_number":detected_oem,"oem_match_status":("REPEATED TITLE OEM" if detected_oem else "UNCONFIRMED OEM"),
            "oem_evidence_count":int(oem_hits),"oem_evidence_share":round(float(oem_share),3),
            "price_confidence_grade":grade,"saturation_status":sat,"walkaway_reason":walk,
            "evidence_json":{"search_query":q,"engine_label":engine_label(profile),"examples":[{"title":e.get("title"),"url":e.get("url"),"price":e.get("price_total")} for e in examples],"detected_oem":detected_oem},
            "sold_median":round(float(sold["sold_median"]),2) if sold["sold_median"] is not None else None,
            "sold_count_90":int(sold["sold_count_90"]),"sold_count_all":int(sold["sold_count_all"]),
            "sell_through_pct":sellthrough,"days_to_sell":days,
            "market_value":round(market,2),"market_basis":"sold comps" if sold["sold_median"] is not None else "active eBay proxy",
            "lkq_cost":round(float(c["lkq_cost"]),2),"cost_basis":"estimated LKQ category" if c["fitment_rule"]=="inferred" else "mapped LKQ category",
            "pull_minutes":int(c["pull_minutes"]),"ship_estimate":round(float(c["ship_estimate"]),2),
            "expected_net":round(expected,2),"profit_per_hour":round(expected/max(c["pull_minutes"]/60,.12),2),
            "dwmt_score":score,"confidence":round(confidence,1),"fitment_status":"ELIGIBLE" if fit_ok else "HIDDEN",
            "fitment_reason":fit_reason,"demand_confidence":round(demand_conf,2),
            "first_seen":now(),"last_seen":now()
        })
    return q,listings,rows

def load_verifications():
    try:
        rows=supa_get_all("scanner_donor_verifications",{"select":"stock_number,part_key,status,oem_part_number,notes,updated_at"})
        return {(str(r.get("stock_number") or ""),clean_upper(r.get("part_key") or "")):r for r in rows}
    except Exception as e:
        print("Donor verification read skipped:",e); return {}

def discovered_rec_for(car,profile,row,verification=None,donor_conditions=None,learned_stats=None,oem_override=None):
    fit_ok,fit_reason,fit_conf=discovery_fitment(car,profile,row.get("fitment_rule"),row.get("part_name"))
    status=str((verification or {}).get("status") or "").lower()
    if status in {"absent","damaged"}: return None
    if status=="present":
        fit_ok=True; fit_reason="Physically verified present in donor"; fit_conf=1.0
    if not fit_ok: return None
    row=dict(row)
    if oem_override:
        row.update(oem_override); row["market_value"]=oem_override.get("recommended_sell_price")
        market=float(oem_override.get("recommended_sell_price") or 0); fee=fnum("EBAY_FEE",.13); quick=fnum("EBAY_QUICK_SALE_PCT",.88); target=market*quick; expected=target-target*fee-float(row.get("ship_estimate") or 0)-float(row.get("lkq_cost") or 0); row["expected_net"]=round(expected,2); row["profit_per_hour"]=round(expected/max(float(row.get("pull_minutes") or 30)/60,.12),2)
    expected=float(row.get("expected_net") or 0)
    cond_ok,cond_mult,cond_reason=donor_condition_effect(car.get("stock_number"),row.get("part_name"),donor_conditions)
    if not cond_ok: return None
    learn_mult,learn_row=learned_factor(row.get("part_name"),learned_stats); expected*=learn_mult; row["expected_net"]=round(expected,2)
    score=float(row.get("dwmt_score") or 0)
    conf=float(row.get("confidence") or 0)*fit_conf
    grade=str(row.get("price_confidence_grade") or "LOW"); walk=str(row.get("walkaway_reason") or "")
    if oem_override: grade=str(oem_override.get("price_confidence_grade") or grade); walk=""
    # Do not promote weak/noisy discoveries into the pull list.
    if expected < fnum("DISCOVERY_MIN_EXPECTED_NET",75) or score < fnum("DISCOVERY_MIN_DWMT",45): return None
    if grade=="REJECT" or walk: return None
    stock=str(car.get("stock_number") or "")
    part_key=clean_upper(row.get("part_key") or row.get("part_name"))
    return {
        "id":hashlib.sha1(f"{stock}|{part_key}".lower().encode()).hexdigest(),"family_id":row.get("family_id"),
        "stock_number":stock,"car":f"{car.get('year','')} {car.get('make','')} {car.get('model','')}".strip(),
        "yard_location":car.get("yard_location","") or "Location pending","available_date":car.get("available_date","") or "",
        "vin":str(car.get("vin") or ""),"trim":str((profile or {}).get("trim") or ""),"series":str((profile or {}).get("series") or ""),
        "engine_label":engine_label(profile),"part_name":row.get("part_name"),"part_key":part_key,
        "lkq_cost":row.get("lkq_cost"),"market_value":row.get("market_value"),"market_basis":row.get("market_basis"),
        "recommended_sell_price":row.get("recommended_sell_price") or row.get("market_value"),
        "price_source_url":str(row.get("price_source_url") or ""),"price_source_title":str(row.get("price_source_title") or ""),
        "pricing_method":str(row.get("pricing_method") or ""),
        "price_confidence_grade":grade,"walkaway_reason":"",
        "detected_oem_part_number":str(row.get("detected_oem_part_number") or ""),"oem_match_status":str(row.get("oem_match_status") or ""),
        "saturation_status":str(row.get("saturation_status") or ""),"donor_condition_reason":cond_reason,"learning_adjustment":round(learn_mult,3),
        "active_count":row.get("active_count"),"sold_count_90":row.get("sold_count_90"),"sell_through_pct":row.get("sell_through_pct"),
        "expected_net":row.get("expected_net"),"profit_per_hour":row.get("profit_per_hour"),"pull_minutes":row.get("pull_minutes"),
        "dwmt_score":round(score*cond_mult,1),"confidence":round(conf*cond_mult,1),"fitment_status":"VERIFIED" if status=="present" else "VIN/CONFIG ELIGIBLE",
        "fitment_reason":fit_reason,"oem_part_number":str((verification or {}).get("oem_part_number") or ""),
        "discovery_source":"eBay car-first discovery","updated_at":now()
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
    discovery_families_scanned=0; discovery_parts_found=0; discovery_recommendations=0; discovery_calls=0
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

        # 2) Decode VIN/trim/equipment once and persist it. The first V10.4 run
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
            "select":"id,year,make,model,part,query,engine_key,engine_label,market_median,recommended_sell_price,market_n,market_p25,market_p75,price_spread_pct,price_source_url,price_source_title,price_source_urls,pricing_method,engine_match_count,price_confidence_grade,walkaway_reason,evidence_json,pricing_version,updated_at,last_error"
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
        market_scanned=sum(1 for mid in current_ids if mid in cache and str(cache[mid].get("pricing_version") or "")==PRICING_VERSION)
        market_priced=sum(
            1 for mid in current_ids
            if mid in cache
            and str(cache[mid].get("pricing_version") or "")==PRICING_VERSION
            and cache[mid].get("recommended_sell_price") is not None
        )
        market_cov=round((market_scanned/max(market_total,1))*100,1)

        # 7) Load donor-condition and real-sales learning BEFORE recommendation scoring.
        # V10.5 originally loaded these later in the discovery section, which caused
        # a NameError after the market scan completed.
        donor_conditions=load_donor_conditions()
        learned_stats,learning_rows=load_user_sales_learning()
        if learning_rows:
            supa_upsert_many("scanner_sales_learning",learning_rows,"part_key",batch_size=250)

        # Materialize stock-specific recommendations from the cloud cache.
        # Duplicate donor cars reuse the same year/make/model price research.
        rec_rows=[]
        for car in cars:
            for spec in PARTS:
                profile=vin_profiles.get(str(car.get("vin") or "").upper())
                c=candidate_for(car,spec,profile)
                if not c: continue
                row=cache.get(c["id"])
                if not row: continue
                rec=recommendation_from(car,row,vin_profiles,donor_conditions,learned_stats)
                if rec is not None:
                    rec_rows.append(rec)
        supa_upsert_many("scanner_recommendations",rec_rows,"id")

        # Remove recommendations for donor cars that have left the yard.
        current_stocks={c["stock_number"] for c in cars}
        existing_rec_stocks=supa_get_all("scanner_recommendations",{"select":"stock_number"})
        stale_stocks={str(r.get("stock_number","")) for r in existing_rec_stocks if r.get("stock_number")} - current_stocks
        for stock in stale_stocks:
            supa_delete("scanner_recommendations",{"stock_number":f"eq.{stock}"})

        # 8) V10.4 car-first eBay discovery. This is separate from the curated PARTS sweep.
        # We scan exact donor configurations progressively, prioritize new arrivals, and cache the
        # discovered part families so the same donor family is not re-searched every run.
        sold_rows=_sold_rows_from_memory()
        verifications=load_verifications()
        family_state_rows=supa_get_all("scanner_discovery_families",{
            "select":"family_id,last_scanned,status,listing_count,discovered_count,error"
        })
        family_state={r.get("family_id"):r for r in family_state_rows if r.get("family_id")}

        representatives={}
        for car in cars:
            profile=vin_profiles.get(str(car.get("vin") or "").upper())
            fid=discovery_family_id(car,profile)
            # Prefer a newly arrived representative for immediate discovery.
            if fid not in representatives or car in new_cars:
                representatives[fid]=(car,profile)

        new_fids=[]
        for car in new_cars:
            profile=vin_profiles.get(str(car.get("vin") or "").upper())
            fid=discovery_family_id(car,profile)
            if fid not in new_fids: new_fids.append(fid)

        ttl_disc=fnum("DISCOVERY_CACHE_HOURS",336)
        unscanned=[]; stale_disc=[]
        for fid,(car,profile) in representatives.items():
            state=family_state.get(fid)
            if not state:
                unscanned.append(fid)
            else:
                dt=parse_dt(state.get("last_scanned"))
                age=(datetime.now(timezone.utc)-dt).total_seconds()/3600 if dt else 999999
                if age>=ttl_disc:
                    stale_disc.append((dt or datetime(1970,1,1,tzinfo=timezone.utc),fid))
        stale_disc=[fid for _,fid in sorted(stale_disc,key=lambda x:x[0])]
        order=[]
        for fid in new_fids+unscanned+stale_disc:
            if fid not in order: order.append(fid)
        fam_budget=max(0,int(fnum("MAX_DISCOVERY_FAMILIES_PER_RUN",75)))
        chosen_fids=order[:fam_budget]
        discovery_token=None
        if chosen_fids:
            discovery_token=ebay_token()
        discovered_updates=[]; family_updates=[]
        for fid in chosen_fids:
            car,profile=representatives[fid]
            try:
                q,listings,drows=discover_family(car,profile,discovery_token,sold_rows)
                discovery_calls+=1; ebay_calls+=1; discovery_families_scanned+=1
                discovery_parts_found+=len(drows)
                if drows:
                    # Preserve original first_seen on conflict is not supported by simple upsert;
                    # last_seen is the freshness field used by the app.
                    supa_upsert_many("scanner_discovered_parts",drows,"id",batch_size=250)
                family_updates.append({
                    "family_id":fid,"year":int(car.get("year") or 0),"make":str(car.get("make") or ""),"model":str(car.get("model") or ""),
                    "trim_key":discovery_trim_key(profile),"engine_key":engine_key(profile),"engine_label":engine_label(profile),
                    "search_query":q,"last_scanned":now(),"status":"success","listing_count":len(listings),
                    "discovered_count":len(drows),"error":""
                })
            except Exception as e:
                discovery_calls+=1; ebay_calls+=1; discovery_families_scanned+=1
                family_updates.append({
                    "family_id":fid,"year":int(car.get("year") or 0),"make":str(car.get("make") or ""),"model":str(car.get("model") or ""),
                    "trim_key":discovery_trim_key(profile),"engine_key":engine_key(profile),"engine_label":engine_label(profile),
                    "search_query":discovery_query(car,profile),"last_scanned":now(),"status":"error","listing_count":0,
                    "discovered_count":0,"error":str(e)[:500]
                })
        if family_updates:
            supa_upsert_many("scanner_discovery_families",family_updates,"family_id",batch_size=250)

        # Materialize donor-specific discovery recommendations from cached family discoveries.
        disc_rows=supa_get_all("scanner_discovered_parts",{
            "select":"id,family_id,year,make,model,trim_key,engine_key,engine_label,part_name,part_key,fitment_rule,active_median,active_count,recommended_sell_price,price_source_url,price_source_title,pricing_method,pricing_version,sold_median,sold_count_90,sell_through_pct,days_to_sell,market_value,market_basis,lkq_cost,cost_basis,pull_minutes,ship_estimate,expected_net,profit_per_hour,dwmt_score,confidence,fitment_status,fitment_reason,detected_oem_part_number,oem_match_status,oem_evidence_count,oem_evidence_share,price_confidence_grade,saturation_status,walkaway_reason,evidence_json,last_seen"
        })
        by_family=defaultdict(list)
        for r in disc_rows:
            by_family[str(r.get("family_id") or "")].append(r)
        disc_recs=[]
        for car in cars:
            profile=vin_profiles.get(str(car.get("vin") or "").upper())
            fid=discovery_family_id(car,profile)
            for r in by_family.get(fid,[]):
                v=verifications.get((str(car.get("stock_number") or ""),clean_upper(r.get("part_key") or r.get("part_name"))))
                oem_override=None; verified_oem=str((v or {}).get("oem_part_number") or "").strip()
                if verified_oem:
                    if "_oem_reprice_cache" not in locals(): _oem_reprice_cache={}
                    ck=(normalize_oem(verified_oem),clean_upper(r.get("part_name") or ""))
                    if ck not in _oem_reprice_cache:
                        try:
                            if discovery_token is None: discovery_token=ebay_token()
                            _oem_reprice_cache[ck]=exact_oem_reprice(verified_oem,r.get("part_name"),profile,discovery_token); discovery_calls+=1; ebay_calls+=1
                        except Exception as e:
                            print("Exact OEM repricing skipped:",e); _oem_reprice_cache[ck]=None
                    oem_override=_oem_reprice_cache.get(ck)
                rec=discovered_rec_for(car,profile,r,v,donor_conditions,learned_stats,oem_override)
                if rec: disc_recs.append(rec)
        discovery_recommendations=len(disc_recs)
        if disc_recs:
            supa_upsert_many("scanner_discovered_recommendations",disc_recs,"id",batch_size=300)
        audit_rows=[recommendation_audit_row(r,"curated") for r in rec_rows]+[recommendation_audit_row(r,"discovery") for r in disc_recs]
        if audit_rows: supa_upsert_many("scanner_recommendation_audit",audit_rows,"id",batch_size=300)
        # Purge discovery recommendations for cars no longer in the yard.
        old_disc=supa_get_all("scanner_discovered_recommendations",{"select":"stock_number"})
        stale_disc_stocks={str(r.get("stock_number") or "") for r in old_disc if r.get("stock_number")} - current_stocks
        for stock in stale_disc_stocks:
            supa_delete("scanner_discovered_recommendations",{"stock_number":f"eq.{stock}"})

        # 9) New-car alerting uses every available quick-pull market result for that donor.
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
                lines=["🚨 V10.4 ENGINE+VIN LKQ ALERT",recs[0]["car"],f"Engine: {engine_txt or 'VIN engine not decoded'}",f"📍 {recs[0]['yard_location']}",f"Stock {car['stock_number']}",""]
                for r in sorted(recs,key=lambda x:x["dwmt_score"],reverse=True)[:4]:
                    lines.append(f"{r['part']}: ~${r['expected_net']:.0f} net · DWMT {r['dwmt_score']:.0f}/100 · {r['pull_minutes']} min")
                lines.append(f"Donor opportunity: ~${donor_profit:.0f}")
                msg="\n".join(lines)
                sent=send_telegram(msg)
                sent=send_email(f"V10.4 LKQ alert — {recs[0]['car']}",msg) or sent
                if sent: alerts_sent+=1
                supa_upsert("scanner_alerts",{"id":str(uuid.uuid4()),"created_at":now(),"stock_number":car["stock_number"],"car":recs[0]["car"],"message":msg,"sent":bool(sent)},"id")

        # 10) Store the complete current yard snapshot after successful processing.
        supa_upsert("scanner_background_state",{"id":YARD_ID,"inventory":cars,"updated_at":now()},"id")
        status="success"
        print(f"Yard {len(cars)} cars | new {len(new_cars)} | VIN {vin_decoded}/{vin_total} (+{vin_new_decodes}) | market {market_scanned}/{market_total} ({market_cov}%) | priced {market_priced} | discovery families {discovery_families_scanned} | discovered parts {discovery_parts_found} | discovery recs {discovery_recommendations} | eBay calls {ebay_calls}")
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
                "vin_total":vin_total,"vin_decoded":vin_decoded,"vin_new_decodes":vin_new_decodes,
                "discovery_families_scanned":discovery_families_scanned,
                "discovery_parts_found":discovery_parts_found,
                "discovery_recommendations":discovery_recommendations,
                "discovery_calls":discovery_calls
            },"id")
        except Exception as loge:
            print("Could not log run:",loge)

if __name__=='__main__':
    main()
