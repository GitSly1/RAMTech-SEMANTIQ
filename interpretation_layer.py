"""RTUDES Universal Website Interpretation Layer — observation only."""
from collections import Counter
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
import re


_RESOURCE_EXTENSIONS = {
    "csv", "doc", "docx", "epub", "json", "ods", "odt", "pdf", "ppt",
    "pptx", "rar", "rtf", "tar", "tgz", "txt", "xls", "xlsx", "xml",
    "zip",
}
_MEDIA_EXTENSIONS = {
    "avif", "bmp", "gif", "ico", "jpeg", "jpg", "m4a", "m4v", "mov",
    "mp3", "mp4", "ogg", "ogv", "png", "svg", "tif", "tiff", "wav",
    "webm", "webp",
}
_ACTION_SCHEMES = {"blob", "data", "javascript", "mailto", "sms", "tel"}
_PAGINATION_QUERY_KEYS = {
    "after", "before", "continuation", "cursor", "offset", "p", "page",
    "paged", "start",
}
_NAVIGATION_LABELS = {
    "about", "about us", "account", "contact", "contact us", "home",
    "log in", "login", "main menu", "menu", "my account", "privacy",
    "privacy policy", "search", "sign in", "sign out", "sitemap",
    "terms", "terms and conditions", "terms of service",
}
_NAVIGATION_PATHS = {
    "/", "/about", "/about-us", "/account", "/contact", "/contact-us",
    "/home", "/login", "/privacy", "/privacy-policy", "/search",
    "/signin", "/sitemap", "/terms", "/terms-and-conditions",
    "/terms-of-service",
}


def _text(value):
    return " ".join(str(value or "").split())


def _hostname(parts):
    """Return a normalized hostname without allowing malformed ports to escape."""
    try:
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _path_extension(path):
    match = re.search(r"\.([a-z0-9]{1,8})$", unquote(path or "").lower())
    return match.group(1) if match else ""


def _query_formats(query):
    formats = set()
    for key in ("format", "type", "extension", "ext"):
        for value in query.get(key, []):
            normalized = value.lower().strip().lstrip(".")
            if normalized:
                formats.add(normalized)
    return formats


def _is_resource(path, query, label):
    extension = _path_extension(path)
    if extension in _RESOURCE_EXTENSIONS:
        return True
    if _query_formats(query) & _RESOURCE_EXTENSIONS:
        return True
    if any(key in query for key in ("attachment", "download")):
        return True
    path_segments = {part for part in unquote(path).lower().split("/") if part}
    if path_segments & {"attachment", "attachments", "download", "downloads"}:
        return True
    return bool(re.search(r"\b(?:attachment|download)\b", label))


def _is_media(path, query):
    extension = _path_extension(path)
    return extension in _MEDIA_EXTENSIONS or bool(
        _query_formats(query) & _MEDIA_EXTENSIONS
    )


def _is_continuation(path, query, label):
    if any(key.lower() in _PAGINATION_QUERY_KEYS for key in query):
        return True
    normalized_path = unquote(path or "").lower().rstrip("/")
    if re.search(r"/(?:page|paged|p)/\d+$", normalized_path):
        return True
    normalized_label = label.strip().lower()
    return bool(
        re.fullmatch(
            r"(?:"
            r"next|previous|prev|older|newer|"
            r"next\s+page|previous\s+page|prev\s+page|"
            r"older\s+(?:posts|results|entries)|"
            r"newer\s+(?:posts|results|entries)|"
            r"load\s+more|show\s+more|view\s+more|more\s+results|"
            r"page\s*\d+|\d{1,3}|[›»«‹]"
            r")",
            normalized_label,
            re.IGNORECASE,
        )
    )


def _is_navigation(path, label):
    normalized_label = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
    if normalized_label in _NAVIGATION_LABELS:
        return True
    if re.fullmatch(r"(?:back|return)\s+to\s+home", normalized_label):
        return True
    normalized_path = unquote(path or "/").lower().rstrip("/") or "/"
    return normalized_path in _NAVIGATION_PATHS


def _url_role(source_url, candidate_url, label=""):
    """Classify a link from observable URL and label evidence only."""
    url = _text(candidate_url)
    lab = _text(label).lower()
    if not url:
        return "IGNORE"
    if url.startswith("#"):
        return "FIELD_OR_ACTION"

    try:
        raw_destination = urlsplit(url)
        scheme = raw_destination.scheme.lower()
        if scheme in _ACTION_SCHEMES:
            return "FIELD_OR_ACTION"

        source = urlsplit(_text(source_url))
        is_relative = not raw_destination.scheme and not raw_destination.netloc
        destination = urlsplit(urljoin(_text(source_url), url)) if is_relative else raw_destination
        query = {
            key.lower(): values
            for key, values in parse_qs(
                destination.query, keep_blank_values=True
            ).items()
        }

        if _is_media(destination.path, query):
            return "MEDIA"
        if _is_resource(destination.path, query, lab):
            return "RESOURCE"

        source_host = _hostname(source)
        destination_host = _hostname(destination)
        same_site = is_relative or bool(
            source_host and destination_host and source_host == destination_host
        )

        if destination.netloc and not same_site:
            return "EXTERNAL"
        if destination.scheme.lower() in {"http", "https"} and not destination_host:
            return "UNKNOWN"
        if not same_site:
            return "EXTERNAL"
        if _is_continuation(destination.path, query, lab):
            return "CONTINUATION"
        if _is_navigation(destination.path, lab):
            return "NAVIGATION"
        return "RELATIONSHIP"
    except (TypeError, ValueError):
        return "UNKNOWN"


def interpret_website_snapshot(
    *, source_url="", title="", element_counts=None, datasets=None, tables=None,
    links=None, dom_count=0, semantic_plan=None
):
    counts = Counter(element_counts or {})
    datasets = list(datasets or [])
    tables = list(tables or [])
    links = list(links or [])
    semantic_plan = dict(semantic_plan or {})
    delivery = {"Rendered HTML / DOM"}
    structures = set()
    controls = set()
    resources = set()
    relationships = Counter()
    evidence = []

    labels_blob = " ".join(
        _text(d.get("discovery_label") or d.get("dataset_type") or d.get("name")).lower()
        for d in datasets
    )
    if any(k in labels_blob for k in ("api", "json", "network", "dynamic")):
        delivery.add("Structured / dynamic data")
    if any(k in labels_blob for k in ("document", "resource", "pdf", "file")):
        delivery.add("Downloadable resources")
    if any(k in labels_blob for k in ("relationship", "detail")):
        delivery.add("Linked detail sources")

    repeated = [d for d in datasets if int(d.get("count", 0) or 0) > 1]
    if repeated:
        structures.add("Repeated record collection")
        evidence.append(f"{len(repeated)} repeated dataset candidate(s)")
    if tables:
        structures.add("Table / grid")
        evidence.append(f"{len(tables)} HTML table(s)")
    if counts.get("article", 0):
        structures.add("Article / card containers")
    if counts.get("li", 0) >= 3:
        structures.add("List / repeated items")
    if counts.get("form", 0) or counts.get("input", 0):
        structures.add("Interactive form/search surface")

    if counts.get("input", 0):
        controls.add("Input / search / filter")
    if counts.get("select", 0):
        controls.add("Select / filter")
    if counts.get("button", 0):
        controls.add("Buttons / actions")
    if counts.get("form", 0):
        controls.add("Form submission")

    for label, url in links[:5000]:
        role = _url_role(source_url, url, label)
        relationships[role] += 1
        if role == "RESOURCE":
            resources.add("Document / file links")
        elif role == "MEDIA":
            resources.add("Media links")
        elif role == "CONTINUATION":
            controls.add("Pagination / continuation")

    plan_roles = semantic_plan.get("relationship_roles") or {}
    if plan_roles.get("DETAIL"):
        relationships["RELATIONSHIP"] += int(plan_roles.get("DETAIL") or 0)
    if plan_roles.get("PAGINATION"):
        relationships["CONTINUATION"] += int(plan_roles.get("PAGINATION") or 0)
        controls.add("Pagination / continuation")
    if plan_roles.get("RESOURCE"):
        resources.add("Document / file links")

    if "Repeated record collection" in structures and relationships.get("RELATIONSHIP", 0):
        page_model = "Collection → Detail relationship"
    elif "Repeated record collection" in structures:
        page_model = "Collection / listing"
    elif "Table / grid" in structures:
        page_model = "Tabular dataset"
    else:
        page_model = "Single page / mixed content"

    return {
        "mode": "OBSERVATION_ONLY",
        "page_model": page_model,
        "delivery_sources": sorted(delivery),
        "structures": sorted(structures),
        "controls": sorted(controls),
        "resources": sorted(resources),
        "relationship_roles": dict(relationships),
        "dataset_candidates": len(datasets),
        "primary_record_count": int(semantic_plan.get("recommended_record_count", 0) or 0),
        "field_examples": list(semantic_plan.get("recommended_fields") or [])[:8],
        "dom_elements": int(dom_count or 0),
        "confidence": _text(semantic_plan.get("confidence") or "Unrated"),
        "title": _text(title),
        "evidence": evidence,
    }


def interpretation_summary(model):
    if not model:
        return "No interpretation available."
    delivery = ", ".join(model.get("delivery_sources") or ["unknown source"])
    structures = ", ".join(model.get("structures") or ["mixed content"])
    controls = ", ".join(model.get("controls") or ["none detected"])
    resources = ", ".join(model.get("resources") or ["none detected"])
    roles = model.get("relationship_roles") or {}
    role_text = ", ".join(f"{k}: {v}" for k, v in roles.items() if v) or "none detected"
    return (
        f"OBSERVATION ONLY — {model.get('page_model', 'Unknown')}. "
        f"Delivery: {delivery}. Structure: {structures}. Controls: {controls}. "
        f"Relationships: {role_text}. Resources: {resources}. "
        f"Candidates: {model.get('dataset_candidates', 0)} dataset(s), "
        f"{model.get('dom_elements', 0)} DOM element(s). "
        "This interpretation does not select, exclude, follow, rename, preview, or extract anything."
    )
