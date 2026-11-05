"""RTUDES RECON037 — generic geographic reference and contextual address engine."""
import csv, re
US_STATES={
"ALABAMA":"AL","ALASKA":"AK","ARIZONA":"AZ","ARKANSAS":"AR","CALIFORNIA":"CA","COLORADO":"CO","CONNECTICUT":"CT",
"DELAWARE":"DE","FLORIDA":"FL","GEORGIA":"GA","HAWAII":"HI","IDAHO":"ID","ILLINOIS":"IL","INDIANA":"IN","IOWA":"IA",
"KANSAS":"KS","KENTUCKY":"KY","LOUISIANA":"LA","MAINE":"ME","MARYLAND":"MD","MASSACHUSETTS":"MA","MICHIGAN":"MI",
"MINNESOTA":"MN","MISSISSIPPI":"MS","MISSOURI":"MO","MONTANA":"MT","NEBRASKA":"NE","NEVADA":"NV","NEW HAMPSHIRE":"NH",
"NEW JERSEY":"NJ","NEW MEXICO":"NM","NEW YORK":"NY","NORTH CAROLINA":"NC","NORTH DAKOTA":"ND","OHIO":"OH",
"OKLAHOMA":"OK","OREGON":"OR","PENNSYLVANIA":"PA","RHODE ISLAND":"RI","SOUTH CAROLINA":"SC","SOUTH DAKOTA":"SD",
"TENNESSEE":"TN","TEXAS":"TX","UTAH":"UT","VERMONT":"VT","VIRGINIA":"VA","WASHINGTON":"WA","WEST VIRGINIA":"WV",
"WISCONSIN":"WI","WYOMING":"WY","DISTRICT OF COLUMBIA":"DC"}
STATE_ABBR={v:k for k,v in US_STATES.items()}
STREET_SUFFIXES={"ST","STREET","AVE","AVENUE","RD","ROAD","DR","DRIVE","LN","LANE","BLVD","BOULEVARD","CT","COURT",
"CIR","CIRCLE","PKWY","PARKWAY","PL","PLACE","TER","TERRACE","WAY","HWY","HIGHWAY","TRL","TRAIL","PIKE","PLZ","PLAZA"}
DIRECTIONS={"N","S","E","W","NE","NW","SE","SW","NORTH","SOUTH","EAST","WEST"}
_REFERENCE=[]
def norm(v): return " ".join(str(v or "").split())
def load_reference_csv(path):
    global _REFERENCE
    rows=[]
    with open(path,"r",encoding="utf-8-sig",newline="") as f:
        r=csv.DictReader(f); names={str(x).lower():x for x in (r.fieldnames or [])}
        def pick(*opts):
            for x in opts:
                if x in names:return names[x]
        cc=pick("city","locality","municipality"); sc=pick("state","state_code","province","region")
        zc=pick("zip","zipcode","zip_code","postal","postal_code","postcode"); kc=pick("country","country_code")
        if not (cc and sc and zc): raise ValueError("Reference CSV needs City, State/Province, and ZIP/Postal columns.")
        for row in r:
            city=norm(row.get(cc)); state=norm(row.get(sc)).upper(); zp=norm(row.get(zc)); country=norm(row.get(kc)) if kc else "US"
            if city and state and zp: rows.append({"city":city,"state":state,"zip":zp,"country":country or "US"})
    _REFERENCE=rows; return len(rows)
def reference_count(): return len(_REFERENCE)
def normalize_state(v):
    x=norm(v).upper().strip(" ,.")
    if x in STATE_ABBR:return x
    return US_STATES.get(x,"")
def validate_geo(city="",state="",zipcode="",country="US"):
    city=norm(city); state=normalize_state(state) or norm(state).upper(); zipcode=norm(zipcode)
    if not _REFERENCE:
        score=(30 if state in STATE_ABBR else 0)+(30 if re.fullmatch(r"\d{5}(?:-\d{4})?",zipcode) else 0)+(20 if city and re.fullmatch(r"[A-Za-z .'-]{2,80}",city) else 0)
        return {"valid":score>=50,"confidence":score,"matches":[],"mode":"context"}
    m=[r for r in _REFERENCE if (not zipcode or r["zip"]==zipcode) and (not state or r["state"]==state) and (not city or r["city"].lower()==city.lower())]
    return {"valid":bool(m),"confidence":100 if m else 0,"matches":m[:10],"mode":"reference"}
def _state_match(text):
    up=text.upper()
    m=re.search(r"\b([A-Z]{2})\b(?=\s+\d{5}(?:-\d{4})?\b)",up)
    if m and m.group(1) in STATE_ABBR:return m.group(1),m.start(),m.end()
    for name,abbr in sorted(US_STATES.items(),key=lambda x:-len(x[0])):
        m=re.search(r"\b"+re.escape(name)+r"\b",up)
        if m:return abbr,m.start(),m.end()
    return "",-1,-1
def parse_us_address(text):
    raw=norm(text); out={"street":"","city":"","state":"","zip":"","country":"US","confidence":0}
    if not raw:return out
    state,ss,se=_state_match(raw); out["state"]=state
    if state:
        m=re.search(r"\b(\d{5}(?:-\d{4})?)\b",raw[se:])
        if m: out["zip"]=m.group(1)
    parts=[p.strip() for p in raw.split(",") if p.strip()]
    if len(parts)>=3:
        out["street"]=parts[0]; out["city"]=parts[-2]
    elif state and ss>0:
        before=raw[:ss].strip(" ,"); bp=[p.strip() for p in before.split(",") if p.strip()]
        if len(bp)>=2:
            out["street"]=bp[0]; out["city"]=bp[-1]
        else:
            toks=before.split(); boundary=-1
            for i,t in enumerate(toks):
                x=re.sub(r"[^A-Za-z]","",t).upper()
                if x in STREET_SUFFIXES:
                    boundary=i
                    if i+1<len(toks) and re.sub(r"[^A-Za-z]","",toks[i+1]).upper() in DIRECTIONS: boundary=i+1
            if boundary>=0 and boundary+1<len(toks):
                out["street"]=" ".join(toks[:boundary+1]); out["city"]=" ".join(toks[boundary+1:])
            elif re.search(r"\d",before): out["street"]=before
    if out["city"]:
        out["city"]=re.sub(r"\b\d{5}(?:-\d{4})?\b","",out["city"]).strip(" ,")
        if out["state"]: out["city"]=re.sub(r"\b"+re.escape(out["state"])+r"\b","",out["city"],flags=re.I).strip(" ,")
        for name in US_STATES:
            out["city"]=re.sub(r"\b"+re.escape(name)+r"\b","",out["city"],flags=re.I).strip(" ,")
    if _REFERENCE and out["zip"] and out["state"]:
        exact=[r for r in _REFERENCE if r["zip"]==out["zip"] and r["state"]==out["state"]]
        cities=sorted({r["city"] for r in exact})
        if len(cities)==1 and (not out["city"] or cities[0].lower()!=out["city"].lower()): out["city"]=cities[0]
    out["confidence"]=validate_geo(out["city"],out["state"],out["zip"])["confidence"]
    return out
