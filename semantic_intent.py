"""RTUDES RECON032 — generic record-bound semantic research and value extraction.

No website/domain rules.  Desired examples identify evidence; related desired
values are preferentially bound to the same logical record.  Composite/free
text values are reduced to the requested semantic value when possible.
"""
import re, html, sys, json
from pathlib import Path
_THIS_DIR=Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path: sys.path.insert(0,str(_THIS_DIR))
from difflib import SequenceMatcher
from urllib.parse import urlparse
import urllib.request, urllib.error
from geo_reference import parse_us_address, validate_geo

def norm(v):
    if v is None: return ""
    if isinstance(v,(dict,list,tuple)): return str(v)
    return " ".join(html.unescape(str(v)).split())

def tokens(v):
    return [x for x in re.split(r"[^a-z0-9]+",norm(v).lower()) if x]

def flatten(value,path=""):
    out=[]
    if isinstance(value,dict):
        for k,v in value.items():
            p=f"{path}.{k}" if path else str(k)
            out.extend(flatten(v,p))
    elif isinstance(value,(list,tuple)):
        for i,v in enumerate(value):
            out.extend(flatten(v,f"{path}[{i}]"))
    else:
        out.append((path,norm(value)))
    return out

def name_score(desired,path):
    a=" ".join(tokens(desired)); b=" ".join(tokens(path))
    if not a or not b: return 0
    if a==b: return 100
    aset=set(a.split()); bset=set(b.split())
    overlap=len(aset & bset)/max(1,len(aset))
    seq=SequenceMatcher(None,a,b).ratio()
    return int(max(overlap*92,seq*72))

def example_score(example,value):
    e=norm(example).lower(); v=norm(value).lower()
    if not e or not v: return 0
    if e==v: return 100
    if e in v: return 98
    et=set(tokens(e)); vt=set(tokens(v))
    if et and et.issubset(vt): return 94
    return int(SequenceMatcher(None,e,v).ratio()*70)

def _money_near_label(desired,text):
    words=[re.escape(x) for x in tokens(desired) if len(x)>2]
    if not words: return ""
    label=r"\s*[\s:/#_-]*\s*".join(words)
    m=re.search(label+r".{0,45}?(\$\s*\d[\d,]*(?:\.\d{1,2})?)",text,re.I)
    return re.sub(r"\s+","",m.group(1)) if m else ""

def _date_near_label(desired,text):
    words=[re.escape(x) for x in tokens(desired) if len(x)>2]
    if not words: return ""
    label=r"\s*[\s:/#_-]*\s*".join(words)
    m=re.search(label+r".{0,45}?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",text,re.I)
    return m.group(1) if m else ""

def extract_semantic_value(desired,example,value):
    """Reduce a composite source to the requested value without site rules."""
    text=norm(value); ex=norm(example)
    if not text: return ""
    if ex and ex.lower() in text.lower():
        # Exact user evidence is the safest extraction.
        return text[text.lower().find(ex.lower()):text.lower().find(ex.lower())+len(ex)]
    m=_money_near_label(desired,text)
    if m: return m
    d=_date_near_label(desired,text)
    if d: return d
    # For short source values, the whole value is already atomic.
    if len(text)<=120: return text
    return ""

def _record_anchor_scores(records,desired_fields):
    """Use all supplied examples to identify the logical example record."""
    scores=[0]*len(records)
    evidence=[0]*len(records)
    for ri,record in enumerate(records):
        vals=[v for _,v in flatten(record)]
        for spec in desired_fields:
            ex=norm(spec.get("example"))
            if not ex: continue
            best=max([example_score(ex,v) for v in vals] or [0])
            if best>=90:
                scores[ri]+=best
                evidence[ri]+=1
    return scores,evidence

def _qualified_urls(record):
    """Discover potential deeper sources; classification is generic."""
    found=[]
    for path,value in flatten(record):
        v=norm(value)
        if not re.match(r"^https?://",v,re.I): continue
        low=(path+" "+v).lower()
        role="RELATED"
        if any(x in low for x in ("detail","profile","property","record","item")): role="DETAIL"
        elif any(x in low for x in (".pdf","document","download","attachment")): role="DOCUMENT"
        elif any(x in low for x in ("image","photo",".jpg",".jpeg",".png",".webp")): role="MEDIA"
        elif any(x in low for x in ("next","page=","offset=")): role="PAGINATION"
        found.append({"path":path,"url":v,"role":role})
    return found


def _value_validity(desired,value,example=""):
    """Generic semantic plausibility; field-name similarity alone is insufficient."""
    d=" ".join(tokens(desired)); v=norm(value); ex=norm(example)
    if not v: return 0
    low=v.lower()
    if ex and example_score(ex,v)>=90: return 100
    if any(x in d for x in ("email",)):
        return 95 if re.search(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b",v) else 15
    if any(x in d for x in ("zip","postal")):
        return 90 if re.search(r"\b\d{5}(?:-\d{4})?\b",v) else 20
    if any(x in d for x in ("date","time")):
        return 90 if re.search(r"\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b",v) or re.fullmatch(r"\d{9,13}",v.strip()) else 35
    if any(x in d for x in ("price","bid","value","amount","cost","revenue","salary")):
        return 90 if re.search(r"(?:[$€£]\s*)?\d[\d,]*(?:\.\d+)?",v) else 25
    if d=="state" or " state " in (" "+d+" "):
        # Accept abbreviations or human-readable location fragments, reject opaque/blank codes.
        if re.fullmatch(r"[A-Za-z]{2}",v.strip()): return 90
        if re.search(r"[A-Za-z]{3,}",v) and len(v)<100: return 75
        return 25
    if any(x in d for x in ("city","country","name","address","location","description")):
        return 80 if re.search(r"[A-Za-z]",v) else 25
    return 65

def _profile_bonus(profile,desired,path):
    """Profiles guide ranking only; they never create or suppress evidence."""
    p=" ".join(tokens(profile)); d=set(tokens(desired)); sp=set(tokens(path))
    if not p or p=="auto general": return 0
    pt=set(tokens(p))
    # Small generic context bonus where source vocabulary overlaps profile or desired concept.
    return min(6, 2*len((d|pt)&sp))

def research_desired_fields(records,desired_fields,max_records=50,profile="Auto / General"):
    records=list(records or [])[:max_records]
    desired_fields=list(desired_fields or [])
    anchor_scores,anchor_evidence=_record_anchor_scores(records,desired_fields)
    anchor_index=max(range(len(records)),key=lambda i:(anchor_evidence[i],anchor_scores[i])) if records else -1
    # Only call it an anchor when at least two independent examples agree, or
    # when there is only one supplied example.
    supplied=sum(1 for s in desired_fields if norm(s.get("example")))
    if anchor_index>=0 and anchor_evidence[anchor_index] < (1 if supplied<=1 else 2):
        anchor_index=-1

    results=[]
    for spec in desired_fields:
        desired=norm(spec.get("name")); example=norm(spec.get("example"))
        hits=[]
        for ri,record in enumerate(records):
            for path,value in flatten(record):
                ns=name_score(desired,path); es=example_score(example,value)
                validity=_value_validity(desired,value,example)
                # A semantic-looking source name cannot win with an empty or implausible value.
                base=max(es, int(ns*.72 + validity*.28), int(ns*.45+es*.55) if es else 0)
                score=base + _profile_bonus(profile,desired,path)
                if validity < 30 and es < 90:
                    score=min(score,45)
                # Once the example record is established, same-record evidence
                # outranks coincidental matches in other records.
                if anchor_index>=0:
                    score += 12 if ri==anchor_index else -18
                score=max(0,min(100,score))
                mode=("Example inside value" if es>=94 and ns<75
                      else "Direct / semantic field" if ns>=80
                      else "Candidate value")
                if score>=55:
                    extracted=extract_semantic_value(desired,example,value)
                    hits.append((score,ri,path,value,mode,ns,es,extracted))
        hits.sort(key=lambda x:(-x[0], x[1]!=anchor_index if anchor_index>=0 else False, x[2]))
        if hits:
            score,ri,path,value,mode,ns,es,extracted=hits[0]
            confidence="High" if score>=90 else "Medium" if score>=70 else "Low"
            results.append({
                "desired":desired,"example":example,"source_path":path,
                "sample_value":value,"extracted_value":extracted,
                "mode":mode,"confidence":confidence,"score":score,
                "record_index":ri,"anchor_record_index":anchor_index,
                "record_bound":anchor_index>=0 and ri==anchor_index,
                "matches":len(hits),"qualified_urls":_qualified_urls(records[ri])
            })
        else:
            urls=_qualified_urls(records[anchor_index]) if anchor_index>=0 else []
            results.append({
                "desired":desired,"example":example,"source_path":"",
                "sample_value":"","extracted_value":"",
                "mode":"Escalation required" if urls else "Not found in current records",
                "confidence":"Unresolved","score":0,"record_index":anchor_index,
                "anchor_record_index":anchor_index,"record_bound":anchor_index>=0,
                "matches":0,"qualified_urls":urls
            })
    return results


def learn_semantic_mapping(research_results):
    mapping=[]
    for r in research_results or []:
        if r.get("score",0)>=70 and r.get("source_path"):
            mapping.append({"desired":norm(r.get("desired")),"source_path":r["source_path"],
                            "example":norm(r.get("example")),"confidence":r.get("confidence","")})
    return mapping

def _path_values(record,target_path):
    target_norm=re.sub(r"\[\d+\]","[]",target_path or "")
    return [(p,v) for p,v in flatten(record)
            if p==target_path or re.sub(r"\[\d+\]","[]",p)==target_norm]

def extract_learned_value(desired,source_value,example=""):
    text=norm(source_value)
    if not text: return ""
    ex=norm(example)
    if ex and ex.lower() in text.lower():
        return extract_semantic_value(desired,ex,text)
    dn=" ".join(tokens(desired))
    if "date" in dn or "time" in dn:
        d=_date_near_label(desired,text)
        if d: return d
    if any(x in dn for x in ("price","bid","value","amount","cost","revenue","salary","estimate")):
        m=_money_near_label(desired,text)
        if m: return m
    if any(x in dn for x in ("zip","postal")):
        m=re.search(r"\b\d{5}(?:-\d{4})?\b",text); return m.group(0) if m else ""
    if dn=="state" or " state " in (" "+dn+" "):
        m=re.search(r"\b([A-Z]{2})\b(?=\s+\d{5}(?:-\d{4})?\b)",text)
        if m: return m.group(1)
        parts=[p.strip() for p in text.split(",")]
        if len(parts)>=2:
            tail=re.sub(r"\b\d{5}(?:-\d{4})?\b","",parts[-1]).strip()
            words=re.findall(r"[A-Za-z]+",tail)
            if words: return " ".join(words)
        return ""
    if "city" in dn:
        parts=[p.strip() for p in text.split(",")]
        if len(parts)>=3: return parts[-2]
        return ""
    if "address" in dn:
        parts=[p.strip() for p in text.split(",")]
        return parts[0] if len(parts)>=2 else (text if len(text)<=160 else "")
    if len(text)<=120 and _value_validity(desired,text)>=60: return text
    return ""

def apply_semantic_mapping(records,mapping):
    rows=[]; diagnostics=[]
    for record in records or []:
        out={}; diag={}
        for rule in mapping or []:
            candidates=_path_values(record,rule["source_path"]); value=""
            for _,source in candidates:
                value=extract_learned_value(rule["desired"],source,rule.get("example",""))
                if value: break
            out[rule["desired"]]=value
            diag[rule["desired"]]={"source_path":rule["source_path"],"found":bool(value),"source_present":bool(candidates)}
        rows.append(out); diagnostics.append(diag)
    return rows,diagnostics


# ---------- RECON035 adaptive per-record semantic resolver ----------

def _strict_accept(desired,value):
    """Final gate: only semantically defensible extracted values are emitted."""
    d=" ".join(tokens(desired)); v=norm(value)
    if not v: return False
    if any(x in d for x in ("price","bid","value","amount","cost","revenue","salary","estimate")):
        return bool(re.fullmatch(r"[$€£]?\s*\d[\d,]*(?:\.\d{1,2})?",v))
    if "date" in d or "time" in d:
        return bool(re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",v))
    if any(x in d for x in ("zip","postal")):
        return bool(re.fullmatch(r"\d{5}(?:-\d{4})?",v))
    if d=="state" or " state " in (" "+d+" "):
        return bool(re.fullmatch(r"[A-Za-z]{2}",v) or (re.fullmatch(r"[A-Za-z ]{3,30}",v) and len(v.split())<=3))
    if "city" in d:
        return bool(re.fullmatch(r"[A-Za-z .'-]{2,60}",v))
    if "address" in d:
        return bool(re.search(r"\d",v) and re.search(r"[A-Za-z]",v) and len(v)<=180)
    if "description" in d:
        return bool(re.search(r"[A-Za-z]",v) and len(v)>=8)
    if "email" in d:
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",v))
    return _value_validity(desired,v)>=60

def _extract_labeled_text(desired,text):
    """Generic label/value extraction for non-money/non-date text."""
    words=[re.escape(x) for x in tokens(desired) if len(x)>2]
    if not words: return ""
    label=r"\s*[\s:/#_-]*\s*".join(words)
    # Capture until common field separators; deliberately conservative.
    m=re.search(label+r"\s*[:=-]?\s*([^|;\n\r]{2,220})",norm(text),re.I)
    return m.group(1).strip(" .:-") if m else ""

def _adaptive_extract(desired,path,value,learned_path=""):
    text=norm(value); d=" ".join(tokens(desired))
    if not text: return ("",0,"empty")
    ns=name_score(desired,path)

    # Strong labeled patterns.
    if any(x in d for x in ("price","bid","value","amount","cost","revenue","salary","estimate")):
        v=_money_near_label(desired,text)
        if v and _strict_accept(desired,v): return (v,98,"labeled monetary value")
        # Atomic numeric/money field is acceptable only when source name is semantically strong.
        if ns>=78 and re.fullmatch(r"[$€£]?\s*\d[\d,]*(?:\.\d{1,2})?",text):
            return (re.sub(r"\s+","",text),90,"semantic atomic money field")
        return ("",0,"no monetary evidence")

    if "date" in d or "time" in d:
        v=_date_near_label(desired,text)
        if v and _strict_accept(desired,v): return (v,98,"labeled date value")
        if ns>=78:
            m=re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",text)
            if m: return (m.group(0),90,"semantic date field")
        return ("",0,"no date evidence")

    if any(x in d for x in ("zip","postal")):
        m=re.search(r"\b\d{5}(?:-\d{4})?\b",text)
        if m: return (m.group(0),92 if ns>=55 else 82,"postal pattern")
        return ("",0,"no postal evidence")

    if d=="state" or " state " in (" "+d+" "):
        # Prefer state token immediately before postal code.
        m=re.search(r"\b([A-Z]{2})\b(?=\s+\d{5}(?:-\d{4})?\b)",text)
        if m: return (m.group(1),96,"region before postal")
        if ns>=80 and _strict_accept(desired,text): return (text.upper() if len(text)==2 else text,88,"semantic state field")
        return ("",0,"no state evidence")

    if "city" in d:
        # Comma-structured address: street, city, region/postal.
        parts=[p.strip() for p in text.split(",")]
        if len(parts)>=3 and re.search(r"\d",parts[0]):
            cand=parts[-2]
            if _strict_accept(desired,cand): return (cand,94,"address locality component")
        # City immediately before region + postal, with or without comma.
        m=re.search(r"(?:,\s*|\s)([A-Za-z][A-Za-z .'-]{1,50})\s*,?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?\b",text)
        if m:
            cand=m.group(1).strip()
            # Avoid swallowing the street by preferring trailing alphabetic words after last comma.
            if "," not in text and re.search(r"\d",cand):
                cand=""
            if cand and _strict_accept(desired,cand): return (cand,90,"locality before region/postal")
        if ns>=82 and len(text)<=60 and _strict_accept(desired,text): return (text,88,"semantic city field")
        return ("",0,"no city evidence")

    if "address" in d:
        # If source itself is an address-like composite, remove trailing locality/region/postal.
        if re.search(r"\d",text):
            if "," in text:
                cand=text.split(",")[0].strip()
                if _strict_accept(desired,cand): return (cand,92,"address component")
            # Strip a terminal city + state + zip only when detectable.
            cand=re.sub(r"\s+[A-Za-z][A-Za-z .'-]{1,45}\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?\s*$","",text).strip(" ,")
            if cand and cand!=text and _strict_accept(desired,cand): return (cand,88,"address before locality")
            if ns>=65 and len(text)<=180 and _strict_accept(desired,text): return (text,80,"address-like source")
        return ("",0,"no address evidence")

    if "description" in d:
        # Strong semantic field: keep meaningful prose; otherwise use labeled text.
        if ns>=70 and _strict_accept(desired,text): return (text,88,"semantic description field")
        v=_extract_labeled_text(desired,text)
        if v and _strict_accept(desired,v): return (v,90,"labeled description")
        return ("",0,"no description evidence")

    if ns>=80 and _strict_accept(desired,text): return (text,86,"semantic atomic field")
    return ("",0,"no validated evidence")

def resolve_record_field(record,rule):
    """Resolve one desired output within one record, adapting beyond the learned source."""
    desired=rule["desired"]; learned=rule.get("source_path","")
    flat=flatten(record)
    candidates=[]
    dlow=" ".join(tokens(desired))
    geo_key=("street" if "address" in dlow else "city" if "city" in dlow else "state" if (dlow=="state" or " state " in (" "+dlow+" ")) else "zip" if any(x in dlow for x in ("zip","postal")) else "")
    if geo_key:
        geos=[]
        for path,value in flat:
            text=norm(value)
            if not text or not re.search(r"\d",text): continue
            p=parse_us_address(text); val=p.get(geo_key,"")
            if val:
                score=70+(15 if path==learned else 0)+min(15,int(p.get("confidence",0)/7))
                geos.append((score,path,val,"contextual address parse"))
        geos.sort(key=lambda x:(-x[0],x[1]!=learned,x[1]))
        if geos:
            score,path,val,why=geos[0]
            return val,{"found":True,"source_path":path,"score":min(100,score),"method":why,"recovered_alternate":bool(learned and path!=learned)}

    for path,value in flat:
        v,score,why=_adaptive_extract(desired,path,value,learned)
        if not v or not _strict_accept(desired,v): continue
        # Learned path is a useful prior, not a requirement.
        bonus=10 if path==learned else 0
        candidates.append((min(100,score+bonus),path,v,why))
    candidates.sort(key=lambda x:(-x[0], x[1]!=learned, x[1]))
    if candidates:
        score,path,v,why=candidates[0]
        return v,{"found":True,"source_path":path,"score":score,"method":why,
                  "recovered_alternate":bool(learned and path!=learned)}
    return "",{"found":False,"source_path":"","score":0,"method":"unresolved",
               "recovered_alternate":False,"qualified_urls":_qualified_urls(record)}

def apply_adaptive_semantic_mapping(records,mapping):
    rows=[]; diagnostics=[]
    for record in records or []:
        row={}; diag={}
        for rule in mapping or []:
            value,info=resolve_record_field(record,rule)
            row[rule["desired"]]=value; diag[rule["desired"]]=info
        rows.append(row); diagnostics.append(diag)
    return rows,diagnostics


# ---------- RECON036 Power-Query-style cleaning / normalization ----------

def _title_case(v):
    return " ".join(w[:1].upper()+w[1:].lower() if w else w for w in norm(v).split())

def _strip_html(v):
    return norm(re.sub(r"<[^>]+>"," ",norm(v)))

def _normalize_phone(v):
    digits=re.sub(r"\D","",norm(v))
    return digits if 7 <= len(digits) <= 15 else ""

def _normalize_email(v):
    raw=norm(v).strip().lower()
    return raw if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",raw) else ""

def _normalize_number(v):
    m=re.search(r"-?\d[\d,]*(?:\.\d+)?",norm(v))
    return m.group(0).replace(",","") if m else ""

def _atomic_currency(v):
    raw=norm(v)
    m=re.fullmatch(r"\s*([$€£]?)\s*(-?\d[\d,]*(?:\.\d{1,2})?)\s*",raw)
    if not m: return ""
    return f"{m.group(1)}{m.group(2)}" if m.group(1) else m.group(2)

def clean_semantic_value(field,value,rule):
    v=norm(value)
    if not v:
        return "",{"changed":False,"valid":False,"reason":"blank"}

    transform=norm((rule or {}).get("transform","Text Clean"))
    validation=norm((rule or {}).get("validation","Non-empty"))
    fmt=norm((rule or {}).get("format","Original"))
    semantic=norm((rule or {}).get("semantic","Text"))
    out=v

    if "Remove HTML" in transform:
        out=_strip_html(out)
    if "Text Clean" in transform or "Trim" in transform or "Collapse Spaces" in transform:
        out=norm(out)
    if "Email Clean" in transform:
        out=_normalize_email(out)
    if "Phone Normalize" in transform:
        out=_normalize_phone(out)
    if "Number Extract" in transform:
        out=_normalize_number(out)

    if "Labeled Money Extract" in transform or semantic=="Currency / Amount":
        # Critical safety rule: use semantic label context for composite text.
        # If the value is already atomic money, keep it. Do not turn arbitrary
        # prose containing a number into a monetary output.
        atomic=_atomic_currency(out)
        if atomic:
            out=atomic
        else:
            labeled=_money_near_label(field,out)
            out=labeled if labeled else ""

    if "Date Detect" in transform or semantic=="Date / Time":
        if not re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",out):
            labeled=_date_near_label(field,out)
            out=labeled if labeled else ""

    if "Upper" in transform or fmt in ("Uppercase","Abbrev"):
        out=out.upper()
    elif "Lower" in transform or fmt=="Lowercase":
        out=out.lower()
    elif "Proper Case" in transform or fmt=="Title Case":
        out=_title_case(out)

    valid=True
    if validation=="Email pattern":
        valid=bool(_normalize_email(out))
    elif validation=="Phone pattern":
        valid=bool(_normalize_phone(out))
    elif validation=="Money + semantic label":
        valid=bool(_atomic_currency(out))
    elif validation=="Date context":
        valid=bool(re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",out))
    elif validation=="Postal context":
        valid=bool(re.fullmatch(r"\d{5}(?:-\d{4})?",out))
    elif validation=="Region context":
        valid=bool(re.fullmatch(r"[A-Za-z]{2}",out) or re.fullmatch(r"[A-Za-z ]{3,30}",out))
    elif validation=="Locality context":
        valid=bool(re.fullmatch(r"[A-Za-z .'-]{2,60}",out))
    elif validation=="Address structure":
        valid=bool(re.search(r"\d",out) and re.search(r"[A-Za-z]",out))
    elif validation=="Non-empty":
        valid=bool(out)

    if not valid:
        return "",{"changed":out!=v,"valid":False,"reason":"validation failed"}
    return out,{"changed":out!=v,"valid":True,"reason":"validated"}

def apply_cleaning_rules(rows,rules):
    cleaned=[]; diagnostics=[]
    for row in rows or []:
        out={}; diag={}
        for field,value in row.items():
            rule=(rules or {}).get(field,{})
            if rule:
                nv,info=clean_semantic_value(field,value,rule)
                out[field]=nv; diag[field]=info
            else:
                out[field]=value
                diag[field]={"changed":False,"valid":bool(value),"reason":"no rule"}
        cleaned.append(out); diagnostics.append(diag)
    return cleaned,diagnostics


# ---------- RECON038 relationship/deep-evidence enrichment ----------

def merge_record_evidence(record, evidence_items):
    """Non-destructively attach retrieved relationship evidence to a record."""
    out=dict(record or {})
    bucket=[]
    for item in evidence_items or []:
        if not isinstance(item,dict): continue
        bucket.append({
            "url": norm(item.get("url")),
            "status": norm(item.get("status")),
            "title": norm(item.get("title")),
            "text": norm(item.get("text")),
            "data": item.get("data") if isinstance(item.get("data"),(dict,list)) else {}
        })
    if bucket: out["_relationship_evidence"]=bucket
    return out

def unresolved_desired_fields(row):
    return [k for k,v in (row or {}).items() if not norm(v)]

def relationship_enrichment_plan(record,row,max_urls=5):
    """Build a generic follow-up plan only when desired outputs remain unresolved."""
    missing=unresolved_desired_fields(row)
    urls=_qualified_urls(record) if missing else []
    return {"missing":missing,"urls":urls[:max_urls],"needs_enrichment":bool(missing and urls)}

def re_resolve_with_evidence(record,row,mapping,evidence_items):
    """Merge retrieved evidence, then re-run the same adaptive semantic resolver."""
    enriched=merge_record_evidence(record,evidence_items)
    new_rows,new_diag=apply_adaptive_semantic_mapping([enriched],mapping)
    resolved=new_rows[0] if new_rows else {}
    merged=dict(row or {})
    for k,v in resolved.items():
        if not norm(merged.get(k)) and norm(v):
            merged[k]=v
    return merged,(new_diag[0] if new_diag else {})


# ---------- RECON039 generic final-output quality gate ----------
_CITY_BAD=re.compile(r"[\[\]{}<>]|^\s*[#\d$]|https?://|@")
def validate_resolved_output(desired,value):
    v=norm(value); d=" ".join(tokens(desired))
    if not v:return "","blank"
    if "city" in d and (_CITY_BAD.search(v) or not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,79}",v)):
        return "","rejected invalid city"
    if d=="state" or " state " in (" "+d+" "):
        from geo_reference import normalize_state
        v=normalize_state(v)
        if not v:return "","rejected invalid state"
    if any(x in d for x in ("zip","postal")) and not re.fullmatch(r"\d{5}(?:-\d{4})?",v):
        return "","rejected invalid postal"
    return v,"accepted"
def quality_gate_rows(rows):
    out=[]; rejected=0
    for row in rows or []:
        nr={}
        for k,v in row.items():
            nv,_=validate_resolved_output(k,v)
            if norm(v) and not nv: rejected+=1
            nr[k]=nv
        out.append(nr)
    return out,{"rejected":rejected}


# ---------- RECON040 Evidence Envelope / Acquire -> Resolve -> Repair ----------
def build_evidence_envelope(record):
    """Collect all already-acquired evidence associated with one record, without site rules."""
    env={"direct":{}, "nested":[], "text":[], "urls":[], "relationship":[]}
    if not isinstance(record,dict):
        env["text"].append(norm(record)); return env
    for k,v in record.items():
        key=str(k); lk=key.lower()
        if isinstance(v,(dict,list)):
            env["nested"].append({key:v})
            if any(x in lk for x in ("detail","relationship","linked","expanded","resource")):
                env["relationship"].append({key:v})
        else:
            nv=norm(v)
            env["direct"][key]=nv
            if nv and len(nv)>2: env["text"].append(nv)
            if nv.lower().startswith(("http://","https://")): env["urls"].append(nv)
    for u in _qualified_urls(record):
        if u not in env["urls"]: env["urls"].append(u)
    existing=record.get("_relationship_evidence")
    if isinstance(existing,list): env["relationship"].extend(existing)
    return env

def envelope_as_record(record,envelope=None):
    """Attach the evidence envelope non-destructively so flatten() can search all evidence."""
    out=dict(record or {}) if isinstance(record,dict) else {"value":record}
    out["_evidence_envelope"]=envelope or build_evidence_envelope(record)
    return out

def recover_geography_from_row(row):
    """Repair missing geographic components using coherent address/state/postal context."""
    from geo_reference import parse_us_address, validate_geo
    out=dict(row or {})
    address=norm(out.get("Property Address") or out.get("Address") or out.get("Street Address"))
    city=norm(out.get("City")); state=norm(out.get("State")); zipcode=norm(out.get("Zip") or out.get("ZIP") or out.get("Postal Code"))
    composite=" ".join(x for x in (address,city,state,zipcode) if x)
    parsed=parse_us_address(composite)
    if not city and parsed.get("city"): out["City"]=parsed["city"]
    if not state and parsed.get("state"): out["State"]=parsed["state"]
    zkey="Zip" if "Zip" in out else "ZIP" if "ZIP" in out else "Postal Code" if "Postal Code" in out else ""
    if zkey and not zipcode and parsed.get("zip"): out[zkey]=parsed["zip"]
    return out

def acquire_resolve_repair(records,mapping,cleaning_rules=None):
    """Run the generic three-stage desired-data pipeline over records already acquired by RTUDES."""
    envelopes=[]; enriched=[]
    for r in records or []:
        env=build_evidence_envelope(r); envelopes.append(env); enriched.append(envelope_as_record(r,env))
    rows,diag=apply_adaptive_semantic_mapping(enriched,mapping)
    if cleaning_rules:
        rows,clean_diag=apply_cleaning_rules(rows,cleaning_rules)
    else:
        clean_diag={"changed":0}
    repaired=[recover_geography_from_row(r) for r in rows]
    gated,quality=quality_gate_rows(repaired)
    return gated,{"semantic":diag,"cleaning":clean_diag,"quality":quality,"envelopes":envelopes}


# ---------- RECON041 Canonical Record Integrity + Active Acquisition ----------
def canonical_record_identity(record,index=0):
    """Stable generic identity from explicit IDs/URLs first; never drops a record."""
    if isinstance(record,dict):
        for k,v in record.items():
            lk=str(k).lower()
            nv=norm(v)
            if nv and any(tag in lk for tag in ("record_id","auction_id","item_id","listing_id","identifier","uuid","guid")):
                return "id:"+nv.lower()
        urls=_qualified_urls(record)
        if urls:
            first=urls[0]
            if isinstance(first,dict):
                first=first.get("url") or first.get("value") or first.get("href") or ""
            first=norm(first)
            if first: return "url:"+first.lower()
        for k in ("name","title","address","location"):
            if norm(record.get(k)): return "text:"+norm(record.get(k)).lower()
    return "ordinal:"+str(index)

def preserve_record_cardinality(records,rows):
    """Hard integrity rule: one output row per input record, in source order."""
    records=list(records or []); rows=list(rows or [])
    if len(rows)<len(records):
        rows.extend({} for _ in range(len(records)-len(rows)))
    elif len(rows)>len(records):
        rows=rows[:len(records)]
    return rows,{"source_count":len(records),"output_count":len(rows),"preserved":len(records)==len(rows)}

def active_acquisition_plan(record,row,index=0,max_urls=8):
    """Generic plan for unresolved desired outputs; acquisition execution stays transport-agnostic."""
    missing=unresolved_desired_fields(row)
    env=build_evidence_envelope(record)
    urls=[]
    for u in env.get("urls",[]):
        if isinstance(u,dict): u=u.get("url") or u.get("value") or u.get("href") or ""
        u=norm(u)
        if u and u not in urls: urls.append(u)
    return {
        "identity":canonical_record_identity(record,index),
        "missing":missing,
        "urls":urls[:max_urls],
        "needs_acquisition":bool(missing),
        "has_fetch_target":bool(missing and urls),
    }

def build_active_acquisition_queue(records,rows):
    rows,_=preserve_record_cardinality(records,rows)
    queue=[]
    for i,(r,row) in enumerate(zip(records or [],rows)):
        p=active_acquisition_plan(r,row,i)
        if p["needs_acquisition"]: queue.append({"record_index":i,**p})
    return queue


# ---------- RECON043 Keyed Record Processor / Final Sweep ----------

FIELD_FOUND="FOUND"
FIELD_ABSENT="ABSENT"
FIELD_UNRESOLVED="UNRESOLVED"
FIELD_ERROR="ERROR"
FIELD_NOT_CHECKED="NOT CHECKED"

def recommend_key_field(records):
    """Recommend a stable key by completeness + uniqueness, without site rules."""
    records=list(records or [])
    if not records:
        return {"field":"","score":0,"reason":"no records"}
    preferred=("record_id","listing_id","auction_id","item_id","case_number","case","parcel","apn","identifier","uuid","guid","id","detail_url","url","name","address")
    candidates=[]
    seen=set()
    for r in records[:100]:
        if not isinstance(r,dict):
            continue
        for k in r:
            lk=str(k).lower()
            if k not in seen and any(p==lk or p in lk for p in preferred):
                seen.add(k);candidates.append(k)

    best={"field":"","score":0,"reason":"no suitable key candidate"}
    for field in candidates:
        vals=[norm(r.get(field)) if isinstance(r,dict) else "" for r in records]
        nonempty=[v for v in vals if v]
        if not nonempty:
            continue
        completeness=len(nonempty)/len(records)
        uniqueness=len(set(nonempty))/len(nonempty)
        score=round((completeness*.45+uniqueness*.55)*100,1)
        if score>best["score"]:
            best={"field":field,"score":score,"reason":f"completeness {completeness:.1%}, uniqueness {uniqueness:.1%}"}
    return best

def record_key(record,index=0,key_field=""):
    """Use configured key first, then canonical generic identity."""
    if isinstance(record,dict) and key_field:
        v=norm(record.get(key_field))
        if v:
            return f"{key_field}:{v}"
    return canonical_record_identity(record,index)

def _all_evidence_text(record):
    env=build_evidence_envelope(record)
    parts=[]
    for v in env.get("direct",{}).values():
        if norm(v):parts.append(norm(v))
    for v in env.get("text",[]):
        if norm(v):parts.append(norm(v))
    for obj in env.get("nested",[])+env.get("relationship",[]):
        parts.append(norm(obj))
    return "\n".join(parts)

def concept_evidence_exists(record,desired):
    """Cheap existence test.
    True = evidence of the concept exists.
    False = acquired record contains no evidence and no deeper target.
    None = absence cannot yet be proven.
    """
    if not isinstance(record,dict):
        return None
    desired=norm(desired)
    for path,value in flatten(record):
        if name_score(desired,path)>=78 and norm(value):
            return True

    text=_all_evidence_text(record)
    if not text:
        return False
    low=text.lower()
    phrase=" ".join(tokens(desired))
    if phrase and phrase in low:
        return True

    d=" ".join(tokens(desired))
    if any(x in d for x in ("bid","value","estimate","price","amount","cost")):
        labels=[x for x in tokens(desired) if len(x)>2]
        if labels and all(x in low for x in labels) and re.search(r"[$€£]\s*\d",text):
            return True
    if "date" in d or "time" in d:
        labels=[x for x in tokens(desired) if len(x)>2]
        if labels and all(x in low for x in labels) and re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",text):
            return True

    # Deeper qualified sources mean "not proven absent yet".
    if _qualified_urls(record):
        return None
    return False

def process_record_fields(record,mapping,index=0,key_field="",cleaning_rules=None):
    """Process requested fields sequentially for one record."""
    result={}
    states={}
    diagnostics={}
    for rule in mapping or []:
        desired=rule.get("desired","")
        states[desired]=FIELD_NOT_CHECKED
        try:
            value,info=resolve_record_field(record,rule)
            if value:
                if cleaning_rules and desired in cleaning_rules:
                    value,_=clean_semantic_value(desired,value,cleaning_rules[desired])
                gated,_=validate_resolved_output(desired,value)
                if gated:
                    result[desired]=gated
                    states[desired]=FIELD_FOUND
                    diagnostics[desired]=info
                    continue

            exists=concept_evidence_exists(record,desired)
            result[desired]=""
            if exists is False:
                states[desired]=FIELD_ABSENT
                diagnostics[desired]={"method":"existence check","reason":"no evidence in acquired record"}
            else:
                states[desired]=FIELD_UNRESOLVED
                diagnostics[desired]={
                    "method":"existence check",
                    "reason":"evidence exists or absence cannot yet be proven",
                    "urls":_qualified_urls(record)
                }
        except Exception as exc:
            result[desired]=""
            states[desired]=FIELD_ERROR
            diagnostics[desired]={"method":"processor","reason":str(exc)}

    return {
        "key":record_key(record,index,key_field),
        "row":result,
        "states":states,
        "diagnostics":diagnostics,
        "record_index":index
    }

def final_sweep_record(record,processed,mapping,index=0,key_field="",cleaning_rules=None,evidence_items=None):
    """Revisit only unresolved/error fields after normal processing."""
    out=dict(processed or {})
    row=dict(out.get("row",{}))
    states=dict(out.get("states",{}))
    diagnostics=dict(out.get("diagnostics",{}))
    target=merge_record_evidence(record,evidence_items) if evidence_items else record
    by_name={r.get("desired",""):r for r in mapping or []}

    for desired,state in list(states.items()):
        if state not in (FIELD_UNRESOLVED,FIELD_ERROR):
            continue
        rule=by_name.get(desired)
        if not rule:
            continue
        try:
            value,info=resolve_record_field(target,rule)
            if value and cleaning_rules and desired in cleaning_rules:
                value,_=clean_semantic_value(desired,value,cleaning_rules[desired])
            gated,_=validate_resolved_output(desired,value)
            if gated:
                row[desired]=gated
                states[desired]=FIELD_FOUND
                diagnostics[desired]=info
                continue

            exists=concept_evidence_exists(target,desired)
            if exists is False:
                states[desired]=FIELD_ABSENT
                diagnostics[desired]={"method":"final sweep","reason":"absence established"}
            else:
                states[desired]=FIELD_UNRESOLVED
                diagnostics[desired]={"method":"final sweep","reason":"still unresolved","urls":_qualified_urls(target)}
        except Exception as exc:
            states[desired]=FIELD_ERROR
            diagnostics[desired]={"method":"final sweep","reason":str(exc)}

    out.update({
        "key":record_key(record,index,key_field),
        "row":row,
        "states":states,
        "diagnostics":diagnostics,
        "record_index":index
    })
    return out

def process_dataset_keyed(records,mapping,key_field="",cleaning_rules=None,progress=None):
    """EOF processor: one record at a time; never drops a record."""
    records=list(records or [])
    processed=[]
    counts={FIELD_FOUND:0,FIELD_ABSENT:0,FIELD_UNRESOLVED:0,FIELD_ERROR:0,FIELD_NOT_CHECKED:0}
    for i,record in enumerate(records):
        item=process_record_fields(record,mapping,i,key_field,cleaning_rules)
        processed.append(item)
        for state in item["states"].values():
            counts[state]=counts.get(state,0)+1
        if callable(progress):
            progress(i+1,len(records),item)
    return processed,{"records":len(records),"states":counts,"key_field":key_field}

def rows_from_processed(processed,mapping):
    cols=[m.get("desired","") for m in mapping or []]
    return [{c:item.get("row",{}).get(c,"") for c in cols} for item in processed or []]

def acquisition_queue_from_processed(records,processed):
    """Queue only fields that remain UNRESOLVED/ERROR after the keyed first pass."""
    queue=[]
    for i,(record,item) in enumerate(zip(records or [],processed or [])):
        missing=[k for k,v in item.get("states",{}).items() if v in (FIELD_UNRESOLVED,FIELD_ERROR)]
        if not missing:
            continue
        env=build_evidence_envelope(record)
        urls=[]
        for u in env.get("urls",[]):
            if isinstance(u,dict):
                u=u.get("url") or u.get("href") or ""
            u=norm(u)
            if u and u not in urls:
                urls.append(u)
        queue.append({
            "record_index":i,
            "key":item.get("key") or record_key(record,i),
            "missing":missing,
            "urls":urls[:8],
            "has_fetch_target":bool(urls)
        })
    return queue

def serialize_processor_state(processed,summary,path):
    payload={"processed":processed,"summary":summary}
    Path(path).write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding="utf-8")
    return str(path)

def load_processor_state(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------- RECON044 Active Evidence Acquisition Executor ----------

def fetch_evidence_url(url,timeout=15,max_bytes=2500000,user_agent="Mozilla/5.0"):
    u=norm(url)
    if not u.lower().startswith(("http://","https://")):
        return {"url":u,"status":"error","title":"","text":"","data":{},"error":"unsupported URL"}
    req=urllib.request.Request(u,headers={"User-Agent":user_agent,"Accept":"text/html,application/json,text/plain,*/*"})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as resp:
            raw=resp.read(max_bytes+1)
            truncated=len(raw)>max_bytes
            raw=raw[:max_bytes]
            ctype=(resp.headers.get("Content-Type") or "").lower()
            charset="utf-8"
            m=re.search(r"charset=([A-Za-z0-9._-]+)",ctype)
            if m: charset=m.group(1)
            text=raw.decode(charset,errors="replace")
            data={}
            if "json" in ctype or text.lstrip().startswith(("{","[")):
                try:data=json.loads(text)
                except Exception:data={}
            if data:
                return {"url":u,"status":"ok","title":"","text":norm(data),"data":data,"truncated":truncated}
            tm=re.search(r"<title[^>]*>(.*?)</title>",text,re.I|re.S)
            title=norm(re.sub(r"<[^>]+>"," ",tm.group(1))) if tm else ""
            visible=norm(re.sub(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>|<[^>]+>"," ",text,flags=re.I|re.S))
            return {"url":u,"status":"ok","title":title,"text":visible,"data":{"html":text},"truncated":truncated}
    except Exception as exc:
        return {"url":u,"status":"error","title":"","text":"","data":{},"error":str(exc)}

def execute_active_acquisition(records,processed,mapping,key_field="",cleaning_rules=None,timeout=15,max_urls_per_record=4,progress=None):
    records=list(records or []); processed=list(processed or [])
    queue=acquisition_queue_from_processed(records,processed)
    by_index={q["record_index"]:q for q in queue}
    updated=[]
    stats={"queued_records":len(queue),"fetch_target_records":0,"urls_attempted":0,"urls_ok":0,
           "records_improved":0,"fields_recovered":0,"fetch_errors":0}
    for i,record in enumerate(records):
        item=processed[i] if i<len(processed) else process_record_fields(record,mapping,i,key_field,cleaning_rules)
        q=by_index.get(i)
        if not q:
            updated.append(item)
            continue
        urls=[u for u in q.get("urls",[]) if norm(u)][:max_urls_per_record]
        evidence=[]
        if urls: stats["fetch_target_records"]+=1
        for url in urls:
            stats["urls_attempted"]+=1
            ev=fetch_evidence_url(url,timeout=timeout)
            evidence.append(ev)
            if ev.get("status")=="ok": stats["urls_ok"]+=1
            else: stats["fetch_errors"]+=1
        before=sum(1 for s in item.get("states",{}).values() if s==FIELD_FOUND)
        swept=final_sweep_record(record,item,mapping,i,key_field,cleaning_rules,evidence_items=evidence if evidence else None)
        after=sum(1 for s in swept.get("states",{}).values() if s==FIELD_FOUND)
        if after>before:
            stats["records_improved"]+=1
            stats["fields_recovered"]+=(after-before)
        updated.append(swept)
    counts={FIELD_FOUND:0,FIELD_ABSENT:0,FIELD_UNRESOLVED:0,FIELD_ERROR:0,FIELD_NOT_CHECKED:0}
    for item in updated:
        for state in item.get("states",{}).values():
            counts[state]=counts.get(state,0)+1
    return updated,{"records":len(updated),"states":counts,"key_field":key_field,"acquisition":stats}


# ---------- RECON045 Existing DOM Intelligence Reintegration ----------

_MONTHS={
    "jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,"apr":4,"april":4,
    "may":5,"jun":6,"june":6,"jul":7,"july":7,"aug":8,"august":8,
    "sep":9,"sept":9,"september":9,"oct":10,"october":10,"nov":11,"november":11,
    "dec":12,"december":12
}

def flexible_date_candidate(text):
    """Extract common numeric or month-name dates and normalize to M/D/YY."""
    raw=norm(text)
    if not raw:
        return ""
    m=re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b",raw)
    if m:
        mo,day,yr=int(m.group(1)),int(m.group(2)),int(m.group(3))
        if yr>=2000: yr-=2000
        return f"{mo}/{day}/{yr:02d}"
    month_pat="|".join(sorted(_MONTHS,key=len,reverse=True))
    m=re.search(r"\b("+month_pat+r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(\d{4})\b",raw,re.I)
    if m:
        mo=_MONTHS[m.group(1).lower()]
        day=int(m.group(2));yr=int(m.group(3))%100
        return f"{mo}/{day}/{yr:02d}"
    return ""

def dom_semantic_candidate(desired,item):
    """Extract a requested semantic value from an existing RTUDES DOM catalog item."""
    if not isinstance(item,dict):
        return {"value":"","score":0,"reason":"invalid DOM item"}
    d=" ".join(tokens(desired))
    sample=norm(item.get("sample"))
    context=norm(item.get("context"))
    attrs=norm(item.get("attributes"))
    path=norm(item.get("path"))
    behavior=norm(item.get("behavior"))
    combined=" ".join(x for x in (sample,context,attrs,path,behavior) if x)
    low=combined.lower()

    if "date" in d or "time" in d:
        value=flexible_date_candidate(sample) or flexible_date_candidate(context)
        if not value:
            return {"value":"","score":0,"reason":"no date in DOM candidate"}
        score=72
        if any(x in low for x in ("date","time","timeline","closing","closes","ends","deadline")): score+=15
        if "auction" in low or "bid" in low: score+=8
        return {"value":value,"score":min(100,score),"reason":"DOM date/time context"}

    if any(x in d for x in ("bid","estimate","price","value","amount","cost")):
        value=_money_near_label(desired,sample) or _money_near_label(desired,context)
        if value:
            return {"value":value,"score":96,"reason":"DOM labeled monetary context"}
        return {"value":"","score":0,"reason":"no labeled money in DOM candidate"}

    # Reuse the adaptive resolver for other generic semantic types.
    virtual_path=" ".join(x for x in (behavior,path,attrs) if x)
    value,score,reason=_adaptive_extract(desired,virtual_path,sample or context)
    return {"value":value,"score":score,"reason":"DOM "+reason if reason else "DOM candidate"}


# ---------- RECON047 Evidence-Safe Absence Classification ----------

def authoritative_record_scope(record):
    """Evidence-completeness signals for safe ABSENT classification."""
    if not isinstance(record,dict):
        return {
            "record_identified":False,
            "authoritative_scope":False,
            "qualified_urls":[],
            "successful_relationship_urls":[],
            "relationship_evidence":False,
            "dom_scope_verified":False,
        }

    urls=[]
    for u in _qualified_urls(record):
        if isinstance(u,dict):
            u=u.get("url") or u.get("href") or u.get("value") or ""
        u=norm(u)
        if u and u not in urls:
            urls.append(u)

    rel=record.get("_relationship_evidence")
    successful_urls=[]
    relationship_evidence=False
    if isinstance(rel,list):
        for x in rel:
            if not isinstance(x,dict):
                continue
            ok=(x.get("status")=="ok" or norm(x.get("text")) or bool(x.get("data")))
            if ok:
                relationship_evidence=True
                u=norm(x.get("url"))
                if u and u not in successful_urls:
                    successful_urls.append(u)

    dom_verified=bool(record.get("_dom_scope_verified"))
    record_identified=bool(canonical_record_identity(record,0))
    authoritative_scope=bool(relationship_evidence or dom_verified)

    return {
        "record_identified":record_identified,
        "authoritative_scope":authoritative_scope,
        "qualified_urls":urls,
        "successful_relationship_urls":successful_urls,
        "relationship_evidence":relationship_evidence,
        "dom_scope_verified":dom_verified,
    }

def can_classify_absent(record,desired):
    """ABSENT only after the correct authoritative scope is fully accounted for."""
    scope=authoritative_record_scope(record)
    if not scope["record_identified"]:
        return False, "record identity not established"
    if not scope["authoritative_scope"]:
        return False, "authoritative record scope not verified"

    exists=concept_evidence_exists(record,desired)
    if exists is True:
        return False, "concept evidence exists"

    # A verified DOM record scope is authoritative for rendered-page absence.
    if scope["dom_scope_verified"] and exists is False:
        return True, "verified record DOM scope checked; no evidence of requested concept"

    # For relationship acquisition, every qualified URL for the record must have been
    # successfully acquired before absence can be asserted.
    qualified=set(scope["qualified_urls"])
    successful=set(scope["successful_relationship_urls"])
    if scope["relationship_evidence"] and (not qualified or qualified.issubset(successful)):
        if exists is not True:
            return True, "all qualified relationship evidence acquired; requested concept not present"

    return False, "evidence incomplete or inconclusive"

def final_sweep_record_safe(record,processed,mapping,index=0,key_field="",cleaning_rules=None,evidence_items=None):
    """FOUND or evidence-safe ABSENT; otherwise preserve UNRESOLVED."""
    out=dict(processed or {})
    row=dict(out.get("row",{}))
    states=dict(out.get("states",{}))
    diagnostics=dict(out.get("diagnostics",{}))

    target=dict(record or {}) if isinstance(record,dict) else {"value":record}
    if evidence_items:
        target=merge_record_evidence(target,evidence_items)
        target["_relationship_evidence"]=list(evidence_items)

    by_name={r.get("desired",""):r for r in mapping or []}

    for desired,state in list(states.items()):
        if state not in (FIELD_UNRESOLVED,FIELD_ERROR):
            continue
        rule=by_name.get(desired)
        if not rule:
            continue
        try:
            value,info=resolve_record_field(target,rule)
            if value and cleaning_rules and desired in cleaning_rules:
                value,_=clean_semantic_value(desired,value,cleaning_rules[desired])
            gated,_=validate_resolved_output(desired,value)
            if gated:
                row[desired]=gated
                states[desired]=FIELD_FOUND
                diagnostics[desired]=info
                continue

            absent,reason=can_classify_absent(target,desired)
            if absent:
                row[desired]=""
                states[desired]=FIELD_ABSENT
                diagnostics[desired]={"method":"evidence-safe final sweep","reason":reason}
            else:
                row[desired]=""
                states[desired]=FIELD_UNRESOLVED
                diagnostics[desired]={"method":"evidence-safe final sweep","reason":reason,"urls":_qualified_urls(target)}
        except Exception as exc:
            row[desired]=""
            states[desired]=FIELD_ERROR
            diagnostics[desired]={"method":"evidence-safe final sweep","reason":str(exc)}

    out.update({
        "key":record_key(record,index,key_field),
        "row":row,
        "states":states,
        "diagnostics":diagnostics,
        "record_index":index
    })
    return out

def summarize_processed_states(processed):
    counts={FIELD_FOUND:0,FIELD_ABSENT:0,FIELD_UNRESOLVED:0,FIELD_ERROR:0,FIELD_NOT_CHECKED:0}
    for item in processed or []:
        for state in item.get("states",{}).values():
            counts[state]=counts.get(state,0)+1
    return counts


# ---------- CORE-ORCH001 terminal-state safety override ----------
def can_classify_absent(record,desired):
    """ABSENT requires verified record-bound listing DOM plus exhausted qualified deeper evidence."""
    scope=authoritative_record_scope(record)
    if not scope.get("record_identified"):
        return False,"record identity not established"
    if not scope.get("dom_scope_verified"):
        return False,"record-bound listing DOM not verified"
    exists=concept_evidence_exists(record,desired)
    if exists is True:
        return False,"concept evidence exists"
    qualified=set(scope.get("qualified_urls") or [])
    successful=set(scope.get("successful_relationship_urls") or [])
    if qualified and not qualified.issubset(successful):
        return False,"qualified deeper evidence not fully exhausted"
    if exists is False:
        return True,"record-bound listing DOM verified and all qualified deeper evidence exhausted"
    return False,"evidence remains inconclusive"


# ---------- SMART-RESOLVE001: Consume Bound Smart Evidence ----------

_SMART_GENERIC_ALIASES = {
    "description_family": ("description","summary","details","overview","remarks","notes"),
    "date_family": ("date","closing","closes","closing date","ends","end date","timeline","lots start closing","schedule"),
    "bid_family": ("bid","opening bid","starting bid","bidding starts","bid starts","opening amount"),
    "estimate_family": ("estimate","estimated value","market value","valuation","appraisal"),
}

def _smart_aliases(desired):
    d=" ".join(tokens(desired))
    dt=set(tokens(desired))
    aliases=[d] if d else []
    if "description" in dt or "details" in dt or "remarks" in dt:
        aliases.extend(_SMART_GENERIC_ALIASES["description_family"])
    if "date" in dt or "time" in dt:
        aliases.extend(_SMART_GENERIC_ALIASES["date_family"])
    if "bid" in dt or "bidding" in dt or "amount" in dt:
        aliases.extend(_SMART_GENERIC_ALIASES["bid_family"])
    if "estimate" in dt or "estimated" in dt or "value" in dt or "valuation" in dt:
        aliases.extend(_SMART_GENERIC_ALIASES["estimate_family"])
    return tuple(dict.fromkeys(x for x in aliases if x))

def _money_near_any_label(labels,text):
    text=norm(text)
    for label in labels or []:
        words=[re.escape(x) for x in tokens(label) if len(x)>2]
        if not words:
            continue
        pat=r"\s*[\s:/#_-]*\s*".join(words)
        m=re.search(pat+r".{0,80}?(\$\s*\d[\d,]*(?:\.\d{1,2})?)",text,re.I)
        if m:
            return re.sub(r"\s+","",m.group(1))
    return ""

def _labeled_text_near_any_label(labels,text,max_chars=700):
    text=norm(text)
    for label in labels or []:
        words=[re.escape(x) for x in tokens(label) if len(x)>2]
        if not words:
            continue
        pat=r"\s*[\s:/#_-]*\s*".join(words)
        m=re.search(pat+r"\s*[:=-]?\s*([^|]{2,"+str(max_chars)+r"})",text,re.I)
        if m:
            return m.group(1).strip(" .:-")
    return ""

def resolve_from_smart_bound_evidence(record,desired,example=""):
    """Resolve one requested field only from evidence already fused into this record."""
    if not isinstance(record,dict):
        return "",{"found":False,"method":"SMART evidence unavailable","score":0}

    smart_items=[]
    for path,value in flatten(record):
        if not str(path).startswith("SMART L1 ::"):
            continue
        text=norm(value)
        if text:
            smart_items.append((path,text))
    if not smart_items:
        return "",{"found":False,"method":"SMART L1 evidence empty","score":0}

    d=" ".join(tokens(desired))
    aliases=_smart_aliases(desired)
    candidates=[]

    for path,text in smart_items:
        v,score,why=_adaptive_extract(desired,path,text)
        if v and _strict_accept(desired,v):
            candidates.append((score,path,v,"SMART L1 adaptive: "+why))

        pseudo={"sample":text,"context":text,"attributes":path,"path":path,
                "behavior":"SMART L1 fused record evidence"}
        dom=dom_semantic_candidate(desired,pseudo)
        if dom.get("value") and _strict_accept(desired,dom.get("value")):
            candidates.append((dom.get("score",0),path,dom.get("value"),
                               "SMART L1 DOM semantic: "+dom.get("reason","")))

        if "date" in d or "time" in d:
            low=text.lower()
            if any(alias.lower() in low for alias in aliases):
                value=flexible_date_candidate(text)
                if value and _strict_accept(desired,value):
                    candidates.append((96,path,value,"SMART L1 alias date evidence"))
        elif any(x in d for x in ("bid","estimate","price","value","amount","cost")):
            value=_money_near_any_label(aliases,text)
            if value and _strict_accept(desired,value):
                candidates.append((97,path,value,"SMART L1 alias monetary evidence"))
        elif "description" in d:
            value=_labeled_text_near_any_label(aliases,text)
            if value and _strict_accept(desired,value):
                candidates.append((94,path,value,"SMART L1 alias descriptive evidence"))

        ex=norm(example)
        if ex and ex.lower() in text.lower():
            extracted=extract_semantic_value(desired,example,text) or ex
            if extracted and _strict_accept(desired,extracted):
                candidates.append((100,path,extracted,"SMART L1 exact user example"))

    candidates.sort(key=lambda x:(-x[0],x[1]))
    if candidates:
        score,path,value,method=candidates[0]
        return value,{
            "found":True,"source_path":path,"score":min(100,score),
            "method":method,"recovered_alternate":True,"smart_level":"L1"
        }
    return "",{
        "found":False,"source_path":"","score":0,
        "method":"SMART L1 exhausted without validated match",
        "recovered_alternate":False
    }

def apply_smart_bound_resolution(records,processed,mapping,cleaning_rules=None):
    """Resolve only unresolved/error fields from already-bound SMART L1 evidence."""
    by_name={m.get("desired",""):m for m in mapping or []}
    updated=[]
    stats={"field_checks":0,"records_improved":0,"fields_recovered":0,"smart_records":0}

    for record,item in zip(records or [],processed or []):
        new=dict(item)
        row=dict(new.get("row",{})); states=dict(new.get("states",{}))
        diagnostics=dict(new.get("diagnostics",{}))
        smart_present=any(str(k).startswith("SMART L1 ::") and norm(v) for k,v in (record or {}).items())
        if smart_present:
            stats["smart_records"]+=1

        improved=0
        for desired,state in list(states.items()):
            if state not in (FIELD_UNRESOLVED,FIELD_ERROR):
                continue
            stats["field_checks"]+=1
            rule=by_name.get(desired,{})
            value,info=resolve_from_smart_bound_evidence(record,desired,rule.get("example",""))
            if not value:
                continue
            if cleaning_rules and desired in cleaning_rules:
                value,_=clean_semantic_value(desired,value,cleaning_rules[desired])
            gated,_=validate_resolved_output(desired,value)
            if not gated:
                continue
            row[desired]=gated
            states[desired]=FIELD_FOUND
            diagnostics[desired]=info
            improved+=1
            stats["fields_recovered"]+=1
        if improved:
            stats["records_improved"]+=1
        new.update({"row":row,"states":states,"diagnostics":diagnostics})
        updated.append(new)
    return updated,stats


# ---------- CORE-INTEGRATION001: Canonical Record Evidence Bus ----------

def build_record_evidence_bus(record):
    """Collect record-owned evidence into one generic bus without site-specific rules."""
    bus=[]
    if not isinstance(record,dict):
        return bus
    for path,value in flatten(record):
        text=norm(value)
        if not text:
            continue
        p=str(path)
        low=p.lower()
        if p.startswith("SMART L1 ::"):
            source="SMART_L1"
        elif any(x in low for x in ("dom","html","rendered","text","context")):
            source="DOM"
        elif any(x in low for x in ("url","href","link","relationship","detail")):
            source="RELATIONSHIP"
        elif any(x in low for x in ("json","api","structured","dataset")):
            source="STRUCTURED"
        else:
            source="RECORD"
        bus.append({"source":source,"path":p,"value":text})
    return bus

def resolve_from_evidence_bus(record, desired, example=""):
    """Resolve a desired field from all evidence already owned by the canonical record."""
    bus=build_record_evidence_bus(record)
    candidates=[]
    for ev in bus:
        path,text=ev["path"],ev["value"]
        # Reuse protected semantic extractor.
        try:
            v=extract_semantic_value(desired,example,text)
        except Exception:
            v=""
        if v and _strict_accept(desired,v):
            candidates.append((98 if example and norm(example).lower() in text.lower() else 90,
                               path,v,"semantic extractor",ev["source"]))

        # Reuse adaptive extractor.
        try:
            v,score,why=_adaptive_extract(desired,path,text)
        except Exception:
            v,score,why="",0,""
        if v and _strict_accept(desired,v):
            candidates.append((score,path,v,"adaptive: "+why,ev["source"]))

        # Reuse DOM semantic resolver against any record-owned evidence.
        pseudo={"sample":text,"context":text,"attributes":path,"path":path,
                "behavior":"canonical record evidence"}
        try:
            dom=dom_semantic_candidate(desired,pseudo)
        except Exception:
            dom={}
        if dom.get("value") and _strict_accept(desired,dom.get("value")):
            candidates.append((dom.get("score",0),path,dom.get("value"),
                               "DOM semantic: "+dom.get("reason",""),ev["source"]))

        # Reuse SMART generic concept-family bridge.
        d=" ".join(tokens(desired))
        aliases=_smart_aliases(desired)
        if "date" in d or "time" in d:
            if any(a.lower() in text.lower() for a in aliases):
                v=flexible_date_candidate(text)
                if v and _strict_accept(desired,v):
                    candidates.append((96,path,v,"generic date family",ev["source"]))
        elif any(x in d for x in ("bid","estimate","price","value","amount","cost")):
            v=_money_near_any_label(aliases,text)
            if v and _strict_accept(desired,v):
                candidates.append((97,path,v,"generic monetary family",ev["source"]))
        elif "description" in d or "details" in d or "remarks" in d:
            v=_labeled_text_near_any_label(aliases,text)
            if v and _strict_accept(desired,v):
                candidates.append((94,path,v,"generic descriptive family",ev["source"]))

    candidates.sort(key=lambda x:(-x[0],x[1]))
    if not candidates:
        return "",{"found":False,"method":"evidence bus exhausted","score":0,
                   "evidence_items":len(bus)}
    score,path,value,method,source=candidates[0]
    return value,{"found":True,"source_path":path,"score":min(100,score),
                  "method":method,"evidence_source":source,
                  "evidence_items":len(bus),"recovered_alternate":True}

def apply_core_integration_resolution(records,processed,mapping,cleaning_rules=None):
    """One integrated pass across existing record-owned evidence before any new fetch."""
    by_name={m.get("desired",""):m for m in mapping or []}
    updated=[]; stats={"records":0,"evidence_items":0,"field_checks":0,
                      "fields_recovered":0,"records_improved":0}
    for record,item in zip(records or [],processed or []):
        stats["records"]+=1
        stats["evidence_items"]+=len(build_record_evidence_bus(record))
        new=dict(item); row=dict(new.get("row",{})); states=dict(new.get("states",{}))
        diagnostics=dict(new.get("diagnostics",{})); improved=0
        for desired,state in list(states.items()):
            if state not in (FIELD_UNRESOLVED,FIELD_ERROR):
                continue
            stats["field_checks"]+=1
            rule=by_name.get(desired,{})
            value,info=resolve_from_evidence_bus(record,desired,rule.get("example",""))
            if not value:
                diagnostics[desired]=info
                continue
            if cleaning_rules and desired in cleaning_rules:
                value,_=clean_semantic_value(desired,value,cleaning_rules[desired])
            gated,_=validate_resolved_output(desired,value)
            if not gated:
                info["found"]=False; info["method"]="candidate rejected by output validator"
                diagnostics[desired]=info
                continue
            ok,info=apply_semantic_qualification_gate(record,desired,info,gated)
            if not ok:
                diagnostics[desired]=info
                continue
            row[desired]=gated; states[desired]=FIELD_FOUND; diagnostics[desired]=info
            improved+=1; stats["fields_recovered"]+=1
        if improved:
            stats["records_improved"]+=1
        new.update({"row":row,"states":states,"diagnostics":diagnostics})
        updated.append(new)
    return updated,stats


# ---------- SEMANTIC-GATE001: Evidence Qualification Gate ----------

def _semantic_context_text(path,text):
    return (norm(path)+" "+norm(text)).lower()

def _has_any(text,phrases):
    low=norm(text).lower()
    return any(norm(p).lower() in low for p in phrases if norm(p))

def qualify_candidate_for_field(desired, path, source_text, candidate):
    """Require positive semantic evidence before a discovered value can become FOUND.

    Type compatibility alone is never sufficient. This gate is generic and
    industry-agnostic; it validates semantic context, datatype and record-owned evidence.
    """
    d=" ".join(tokens(desired))
    dt=set(tokens(desired))
    p=norm(path)
    text=norm(source_text)
    value=norm(candidate)
    ctx=_semantic_context_text(p,text)

    if not value:
        return False, "empty candidate", 0

    # Geographic fields: only accept from explicit semantic paths or parseable addresses.
    if "city" in dt or "locality" in dt or "town" in dt:
        path_ok=_has_any(p,("city","locality","town","municipality"))
        addr=parse_us_address(text) if re.search(r"\d",text) else {}
        addr_ok=bool(addr.get("city")) and norm(addr.get("city")).lower()==value.lower()
        return (path_ok or addr_ok,
                "qualified locality context" if (path_ok or addr_ok) else "no locality ownership/context",
                95 if (path_ok or addr_ok) else 0)

    if d=="state" or "state" in dt or "region" in dt:
        path_ok=_has_any(p,("state","region","province"))
        postal_context=bool(re.search(r"\b[A-Z]{2}\b\s+\d{5}(?:-\d{4})?\b",text))
        return (path_ok or postal_context,
                "qualified state context" if (path_ok or postal_context) else "no state context",
                95 if (path_ok or postal_context) else 0)

    if "zip" in dt or "postal" in dt:
        ok=bool(re.fullmatch(r"\d{5}(?:-\d{4})?",value)) and (
            _has_any(p,("zip","postal")) or bool(re.search(r"\b[A-Z]{2}\b\s+\d{5}",text))
        )
        return ok, "qualified postal context" if ok else "no postal context", 95 if ok else 0

    if "address" in dt or "street" in dt:
        ok=bool(re.search(r"\d",value)) and (
            _has_any(p,("address","street","location","name","title")) or
            bool(parse_us_address(text).get("street"))
        )
        return ok, "qualified address context" if ok else "no address context", 94 if ok else 0

    # Dates/times require both a valid date and event/date context.
    if "date" in dt or "time" in dt:
        date_ok=bool(flexible_date_candidate(value) or flexible_date_candidate(text))
        context_ok=_has_any(ctx,(
            "date","time","closing","closes","close","ends","end date",
            "timeline","schedule","starts","start date","auction"
        ))
        ok=date_ok and context_ok
        return ok, "qualified date context" if ok else "date lacks semantic event context", 97 if ok else 0

    # Monetary fields require money plus concept-specific context.
    if any(x in dt for x in ("bid","bidding","amount","price","value","estimate","estimated","cost","revenue","salary")):
        money_ok=bool(re.fullmatch(r"[$€£]\s*\d[\d,]*(?:\.\d{1,2})?",value))
        if "bid" in dt or "bidding" in dt:
            concept=("bid","bidding","opening amount","starting","minimum","reserve")
        elif "estimate" in dt or "estimated" in dt or "value" in dt:
            concept=("estimate","estimated","market value","valuation","appraisal","value")
        elif "price" in dt:
            concept=("price","asking","sale price","list price")
        elif "cost" in dt:
            concept=("cost","expense","fee")
        else:
            concept=tuple(dt)
        context_ok=_has_any(ctx,concept)
        ok=money_ok and context_ok
        return ok, "qualified monetary context" if ok else "money lacks requested-field context", 98 if ok else 0

    # Description-like fields require meaningful prose and description-like provenance/context.
    if "description" in dt or "details" in dt or "remarks" in dt or "summary" in dt:
        prose_ok=len(value)>=24 and len(value.split())>=4
        context_ok=_has_any(ctx,("description","summary","details","overview","remarks","notes"))
        timezone_like=bool(re.fullmatch(r"[A-Za-z_]+/[A-Za-z_]+",value))
        ok=prose_ok and context_ok and not timezone_like
        return ok, "qualified descriptive context" if ok else "text lacks descriptive provenance/context", 96 if ok else 0

    # Generic fields require a strongly semantic path match; arbitrary atomic values are rejected.
    ns=name_score(desired,p)
    ok=ns>=80
    return ok, "strong semantic source path" if ok else "generic candidate lacks strong semantic source", 90 if ok else 0

def apply_semantic_qualification_gate(record, desired, info, candidate):
    """Validate a candidate against the evidence item that produced it."""
    path=norm((info or {}).get("source_path"))
    source_text=""
    if isinstance(record,dict):
        # Recover original source evidence by exact flattened path.
        for p,v in flatten(record):
            if p==path:
                source_text=norm(v); break
    ok,reason,score=qualify_candidate_for_field(desired,path,source_text,candidate)
    out=dict(info or {})
    out["qualification_gate"]="PASS" if ok else "REJECT"
    out["qualification_reason"]=reason
    if ok:
        out["score"]=max(out.get("score",0),score)
    return ok,out


# ---------- LEVEL-HANDOFF001: Level Researcher -> Canonical Evidence Bus ----------
def _canonical_record_key(record, key_field="id"):
    if not isinstance(record,dict): return ""
    raw=norm(record.get(key_field))
    if raw: return f"{key_field}:{raw}"
    for k in ("url","detail_url","source_url","record_url","href","link"):
        raw=norm(record.get(k))
        if raw: return f"{k}:{raw}"
    return ""

def _extract_research_payload(entry):
    if not isinstance(entry,dict): return {}
    for k in ("research","level_research","fetched_record","fetched","payload","detail","detail_record","record","data","result","content"):
        v=entry.get(k)
        if isinstance(v,dict) and v: return v
    return entry

def bind_level_research_evidence(records, processed, mapping, research_results, key_field="id"):
    stats={"completed":0,"bound":0,"unbound":0,"evidence_items":0,"qualified_recoveries":0,"records_improved":0,"rejected":0}
    if not isinstance(research_results,(list,tuple)): return processed,stats
    rec_by_key={_canonical_record_key(r,key_field):r for r in records or [] if _canonical_record_key(r,key_field)}
    augmented=[]
    for rr in research_results:
        if not isinstance(rr,dict): continue
        stats["completed"]+=1
        rk=norm(rr.get("key")) or _canonical_record_key(rr,key_field)
        target=rec_by_key.get(rk)
        if target is None:
            stats["unbound"]+=1; continue
        payload=_extract_research_payload(rr)
        if not isinstance(payload,dict) or not payload:
            stats["unbound"]+=1; continue
        stats["bound"]+=1
        merged=dict(target)
        for p,v in flatten(payload):
            if norm(v):
                merged[f"LEVEL RESEARCH :: {p}"]=v
                stats["evidence_items"]+=1
        augmented.append(merged)
    if not augmented: return processed,stats
    before=sum(1 for x in processed or [] for v in (x.get("states") or {}).values() if v==FIELD_FOUND)
    updated,core=apply_core_integration_resolution(augmented,processed,mapping,{})
    after=sum(1 for x in updated or [] for v in (x.get("states") or {}).values() if v==FIELD_FOUND)
    stats["qualified_recoveries"]=max(0,after-before)
    stats["records_improved"]=core.get("records_improved",0)
    stats["rejected"]=sum(1 for x in updated or [] for d in (x.get("diagnostics") or {}).values()
                          if isinstance(d,dict) and d.get("qualification_gate")=="REJECT")
    return updated,stats
