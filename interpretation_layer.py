"""RTUDES Universal Website Interpretation Layer — observation only."""
from collections import Counter
from urllib.parse import urlsplit, parse_qs
import re

def _text(value):
    return " ".join(str(value or "").split())

def _url_role(source_url, candidate_url, label=""):
    url=_text(candidate_url); lab=_text(label).lower()
    if not url: return "IGNORE"
    low=url.lower()
    if re.search(r"\.(?:pdf|csv|xlsx?|docx?|zip|json|xml)(?:$|[?#])",low): return "RESOURCE"
    if re.search(r"\.(?:jpg|jpeg|png|gif|webp|svg|mp4|webm)(?:$|[?#])",low): return "MEDIA"
    if low.startswith(("mailto:","tel:","javascript:","#")): return "FIELD_OR_ACTION"
    try:
        src=urlsplit(source_url or ""); dst=urlsplit(url)
        same_host=src.netloc.lower().removeprefix("www.")==dst.netloc.lower().removeprefix("www.")
        q=parse_qs(dst.query)
        page_label=bool(re.fullmatch(r"(?:next|previous|prev|page\s*\d+|\d{1,3})",lab,re.I))
        pageish=(
            any(k.lower() in {"page","paged","p","offset","start"} for k in q)
            or bool(re.search(r"/page/\d+/?$",dst.path.lower()))
            or page_label
        )
        if pageish: return "CONTINUATION"
        if not same_host and dst.netloc: return "EXTERNAL"
        if any(x in lab for x in ("home","menu","login","sign in","privacy","terms")): return "NAVIGATION"
        return "RELATIONSHIP" if same_host else "EXTERNAL"
    except Exception:
        return "UNKNOWN"

def interpret_website_snapshot(
    *, source_url="", title="", element_counts=None, datasets=None, tables=None,
    links=None, dom_count=0, semantic_plan=None
):
    counts=Counter(element_counts or {})
    datasets=list(datasets or []); tables=list(tables or []); links=list(links or [])
    semantic_plan=dict(semantic_plan or {})
    delivery={"Rendered HTML / DOM"}; structures=set(); controls=set(); resources=set()
    relationships=Counter(); evidence=[]

    labels_blob=" ".join(
        _text(d.get("discovery_label") or d.get("dataset_type") or d.get("name")).lower()
        for d in datasets
    )
    if any(k in labels_blob for k in ("api","json","network","dynamic")):
        delivery.add("Structured / dynamic data")
    if any(k in labels_blob for k in ("document","resource","pdf","file")):
        delivery.add("Downloadable resources")
    if any(k in labels_blob for k in ("relationship","detail")):
        delivery.add("Linked detail sources")

    repeated=[d for d in datasets if int(d.get("count",0) or 0)>1]
    if repeated:
        structures.add("Repeated record collection")
        evidence.append(f"{len(repeated)} repeated dataset candidate(s)")
    if tables:
        structures.add("Table / grid")
        evidence.append(f"{len(tables)} HTML table(s)")
    if counts.get("article",0): structures.add("Article / card containers")
    if counts.get("li",0)>=3: structures.add("List / repeated items")
    if counts.get("form",0) or counts.get("input",0): structures.add("Interactive form/search surface")

    if counts.get("input",0): controls.add("Input / search / filter")
    if counts.get("select",0): controls.add("Select / filter")
    if counts.get("button",0): controls.add("Buttons / actions")
    if counts.get("form",0): controls.add("Form submission")

    for label,url in links[:5000]:
        role=_url_role(source_url,url,label); relationships[role]+=1
        if role=="RESOURCE": resources.add("Document / file links")
        elif role=="MEDIA": resources.add("Media links")
        elif role=="CONTINUATION": controls.add("Pagination / continuation")

    plan_roles=semantic_plan.get("relationship_roles") or {}
    if plan_roles.get("DETAIL"): relationships["RELATIONSHIP"]+=int(plan_roles.get("DETAIL") or 0)
    if plan_roles.get("PAGINATION"):
        relationships["CONTINUATION"]+=int(plan_roles.get("PAGINATION") or 0)
        controls.add("Pagination / continuation")
    if plan_roles.get("RESOURCE"): resources.add("Document / file links")

    if "Repeated record collection" in structures and relationships.get("RELATIONSHIP",0):
        page_model="Collection → Detail relationship"
    elif "Repeated record collection" in structures:
        page_model="Collection / listing"
    elif "Table / grid" in structures:
        page_model="Tabular dataset"
    else:
        page_model="Single page / mixed content"

    return {
        "mode":"OBSERVATION_ONLY",
        "page_model":page_model,
        "delivery_sources":sorted(delivery),
        "structures":sorted(structures),
        "controls":sorted(controls),
        "resources":sorted(resources),
        "relationship_roles":dict(relationships),
        "dataset_candidates":len(datasets),
        "primary_record_count":int(semantic_plan.get("recommended_record_count",0) or 0),
        "field_examples":list(semantic_plan.get("recommended_fields") or [])[:8],
        "dom_elements":int(dom_count or 0),
        "confidence":_text(semantic_plan.get("confidence") or "Unrated"),
        "title":_text(title),
        "evidence":evidence,
    }

def interpretation_summary(model):
    if not model: return "No interpretation available."
    delivery=", ".join(model.get("delivery_sources") or ["unknown source"])
    structures=", ".join(model.get("structures") or ["mixed content"])
    controls=", ".join(model.get("controls") or ["none detected"])
    resources=", ".join(model.get("resources") or ["none detected"])
    roles=model.get("relationship_roles") or {}
    role_text=", ".join(f"{k}: {v}" for k,v in roles.items() if v) or "none detected"
    return (
        f"OBSERVATION ONLY — {model.get('page_model','Unknown')}. "
        f"Delivery: {delivery}. Structure: {structures}. Controls: {controls}. "
        f"Relationships: {role_text}. Resources: {resources}. "
        f"Candidates: {model.get('dataset_candidates',0)} dataset(s), "
        f"{model.get('dom_elements',0)} DOM element(s). "
        "This interpretation does not select, exclude, follow, rename, preview, or extract anything."
    )
