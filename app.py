# Protected historical marker: text="v1.8.9.3  •  SMART-ORCH001"\n# Protected historical marker: text="v1.8.9.3  •  SMART-ORCH001"
# Protected historical marker: text="v1.8.9.3  •  DYNAMIC-RELATIONSHIP-BIND001"
# Protected legacy UI marker: text="v1.8.9.3  •  DYNAMIC-RELATIONSHIP-BIND001"
# Compatibility: RECON040 evidence pipeline remains available via acquire_resolve_repair.
# Compatibility: RECON041 integrity/acquisition queue remains available via semantic_active_acquisition_queue.

import base64
# Protected predecessor baseline: RTUDES-1.8.3.0-RELTREE001
import csv
import html
import http.cookiejar
import io
import os
import mimetypes
import tempfile
import xml.etree.ElementTree as ET
import json
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.parse import urljoin, urlparse

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))
from interpretation_layer import interpret_website_snapshot, interpretation_summary
from semantic_intent import research_desired_fields


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

BLOCK_TAGS = {"div", "article", "li", "section", "tr"}


def clean_text(value):
    """Normalize scalar values safely before HTML/text cleanup."""
    if value is None:
        value = ""
    elif not isinstance(value, str):
        value = str(value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def flatten_json(value, prefix=""):
    output = {}
    if isinstance(value, dict):
        for key, child in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            output.update(flatten_json(child, next_prefix))
    elif isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            output[prefix or "value"] = " | ".join(str(item) for item in value)
        else:
            output[prefix or "value"] = json.dumps(value, ensure_ascii=False)
    else:
        output[prefix or "value"] = "" if value is None else value
    return output


def get_json_path(data, path):
    if not path.strip():
        return data
    current = data
    for token in path.strip().split("."):
        if isinstance(current, dict):
            current = current[token]
        elif isinstance(current, list):
            current = current[int(token)]
        else:
            raise KeyError(f"Cannot continue through '{token}'.")
    return current


def discover_json_arrays(data, path=""):
    found = []
    if isinstance(data, list):
        if data and all(isinstance(item, dict) for item in data):
            found.append((path, len(data)))
        for i, item in enumerate(data[:10]):
            found.extend(discover_json_arrays(item, f"{path}.{i}".strip(".")))
    elif isinstance(data, dict):
        for key, value in data.items():
            next_path = f"{path}.{key}" if path else key
            found.extend(discover_json_arrays(value, next_path))
    return found


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []
        self.text_parts = []

    def text(self):
        parts = list(self.text_parts)
        for child in self.children:
            text = child.text()
            if text:
                parts.append(text)
        return clean_text(" ".join(parts))

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def signature(self):
        classes = self.attrs.get("class", "")
        return f"{self.tag}|{' '.join(sorted(classes.split())[:4])}"


class DOMParser(HTMLParser):
    VOID_TAGS = {
        "meta", "link", "img", "br", "hr", "input", "source",
        "area", "base", "embed", "param", "track", "wbr"
    }

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.root = Node("document", {})
        self.stack = [self.root]
        self.title = ""
        self.in_title = False
        self.tables = []
        self.current_table = None
        self.current_row = None
        self.current_cell = None
        self.cell_text = []
        self.links = []
        self.current_link = None
        self.link_text = []
        self.counts = Counter()

    def handle_starttag(self, tag, attrs):
        self.counts[tag] += 1
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)

        if tag not in self.VOID_TAGS:
            self.stack.append(node)

        attrs_dict = dict(attrs)

        if tag == "title":
            self.in_title = True

        if tag == "a":
            href = attrs_dict.get("href", "")
            self.current_link = urljoin(self.base_url, href) if href else ""
            self.link_text = []

        if tag == "table":
            if self.current_table is None:
                self.current_table = []
        elif tag == "tr" and self.current_table is not None:
            self.current_row = []
        elif tag in ("td", "th") and self.current_row is not None:
            self.current_cell = {"header": tag == "th", "text": ""}
            self.cell_text = []

    def handle_startendtag(self, tag, attrs):
        self.counts[tag] += 1
        self.stack[-1].children.append(Node(tag, attrs, self.stack[-1]))

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            self.title = clean_text(self.title)

        if tag == "a" and self.current_link is not None:
            self.links.append({
                "text": clean_text(" ".join(self.link_text)),
                "url": self.current_link
            })
            self.current_link = None
            self.link_text = []

        if tag in ("td", "th") and self.current_cell is not None:
            self.current_cell["text"] = clean_text(" ".join(self.cell_text))
            self.current_row.append(self.current_cell)
            self.current_cell = None
            self.cell_text = []
        elif tag == "tr" and self.current_row is not None:
            if any(cell["text"] for cell in self.current_row):
                self.current_table.append(self.current_row)
            self.current_row = None
        elif tag == "table" and self.current_table is not None:
            if self.current_table:
                self.tables.append(self.current_table)
            self.current_table = None

        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, data):
        self.stack[-1].text_parts.append(data)
        if self.in_title:
            self.title += data
        if self.current_link is not None:
            self.link_text.append(data)
        if self.current_cell is not None:
            self.cell_text.append(data)


def normalize_table(table):
    width = max((len(row) for row in table), default=0)
    if width == 0:
        return [], []

    first = table[0]
    has_header = any(cell.get("header") for cell in first)
    if has_header:
        headers = [cell["text"] or f"Column {i+1}" for i, cell in enumerate(first)]
        rows = table[1:]
    else:
        headers = [f"Column {i+1}" for i in range(width)]
        rows = table

    while len(headers) < width:
        headers.append(f"Column {len(headers)+1}")

    output = []
    for row in rows:
        values = [cell["text"] for cell in row]
        values += [""] * (width - len(values))
        output.append(values[:width])
    return headers, output


def _direct_text(node):
    return clean_text(" ".join(node.text_parts))


def _node_segment(node):
    """Create a stable, readable relative selector segment."""
    classes = [c for c in node.attrs.get("class", "").split() if c][:3]
    segment = node.tag + ("." + ".".join(classes) if classes else "")
    if node.parent:
        siblings = [c for c in node.parent.children if c.tag == node.tag and c.attrs.get("class", "") == node.attrs.get("class", "")]
        if len(siblings) > 1:
            try:
                segment += f":nth({siblings.index(node)+1})"
            except ValueError:
                pass
    return segment


def _relative_path(container, node):
    parts = []
    current = node
    while current is not None and current is not container:
        parts.append(_node_segment(current))
        current = current.parent
    return " > ".join(reversed(parts))


def node_features(node, base_url):
    """Extract both convenient summary fields and deep DOM element fields.

    Deep fields preserve relative element paths so the Extraction Builder can
    expose nested spans, headings, anchors, image attributes, data-* values,
    and other useful fields inside a repeated record container.
    """
    features = {}
    headings, paragraphs, links, images, prices = [], [], [], [], []
    deep = {}
    useful_tags = {"h1","h2","h3","h4","h5","h6","p","span","a","strong","b","small","label","time","address","li","ul","ol","div","img"}

    for item in [node, *list(node.descendants())]:
        text = item.text()
        direct = _direct_text(item)

        if item.tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and text:
            headings.append(text)
        elif item.tag in {"p", "span", "strong", "b", "small"} and text:
            paragraphs.append(text)

        if item.tag == "a" and item.attrs.get("href"):
            links.append((text, urljoin(base_url, item.attrs["href"])))

        image_src = item.attrs.get("src") or item.attrs.get("data-src") or item.attrs.get("data-original")
        if item.tag == "img" and image_src:
            images.append(urljoin(base_url, image_src))

        if text:
            prices.extend(re.findall(
                r"(?:[$€£₱]|USD|PHP|EUR|GBP)\s?\d[\d,]*(?:\.\d{1,2})?",
                text, flags=re.I
            ))

        if item is node or item.tag not in useful_tags:
            continue
        path = _relative_path(node, item)
        if not path:
            continue
        if item.tag in {"ul", "ol"}:
            # v1.5.4: expose a semantic list as ONE selectable field instead of
            # forcing non-technical users to discover and select every <li>.
            list_items = []
            for child in item.children:
                if child.tag == "li":
                    value = clean_text(child.text())
                    if value:
                        list_items.append(value)
            if list_items:
                deep[f"{path} [list]"] = " | ".join(list_items)[:4000]
        if direct:
            deep[f"{path} [text]"] = direct[:1000]
        if item.tag == "a" and item.attrs.get("href"):
            deep[f"{path} [href]"] = urljoin(base_url, item.attrs["href"])
        if item.tag == "img" and image_src:
            deep[f"{path} [src]"] = urljoin(base_url, image_src)
        for attr in ("title", "aria-label", "alt", "datetime", "data-email", "data-phone", "data-id"):
            value = item.attrs.get(attr)
            if value:
                deep[f"{path} [{attr}]"] = clean_text(value)

    if headings:
        features["title"] = headings[0]
    elif links and links[0][0]:
        features["title"] = links[0][0]
    elif node.text():
        features["text"] = node.text()[:300]

    if prices:
        features["price"] = prices[0]
    if links:
        features["link_text"] = links[0][0]
        features["url"] = links[0][1]
    if images:
        features["image_url"] = images[0]

    descriptions = [p for p in paragraphs if p not in features.values() and len(p) >= 15]
    if descriptions:
        features["description"] = descriptions[0][:500]

    # Add granular fields after the summary fields. Duplicate values are kept
    # because their DOM paths let the user distinguish company, location,
    # profile links, badges, and other nested elements.
    features.update(deep)
    return features


def _absolute_dom_path(node):
    """Return a readable absolute DOM path for inspector/search purposes."""
    parts=[]
    cur=node
    while cur is not None and cur.tag != "document":
        parts.append(_node_segment(cur))
        cur=cur.parent
    return " > ".join(reversed(parts))


def _dom_element_behavior(node, base_url):
    """Classify a rendered DOM node by behavior and expose executable targets.

    Milestone 1 rule: use as much DOM evidence as possible. A URL is a
    relationship even when it is stored in data-* attributes, onclick code,
    router attributes, form actions, or plain visible text. Return
    (behavior, sample, target).
    """
    tag=(node.tag or "").lower()
    text=clean_text(node.text())[:900]
    direct=_direct_text(node)[:900]
    attrs=node.attrs or {}

    def first_attr(*names):
        for name in names:
            value=attrs.get(name)
            if value:
                return clean_text(str(value))
        return ""

    href=first_attr("href","data-href","data-url","data-link","data-target-url","routerlink","router-link","to","formaction")
    src=first_attr("src","data-src","data-original","data-lazy-src","data-full","data-large","poster")
    action=first_attr("action") if tag == "form" else ""
    onclick=first_attr("onclick")

    js_target=""
    if onclick:
        patterns=[
            r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
            r"window\.open\(\s*['\"]([^'\"]+)['\"]",
            r"(?:location\.assign|location\.replace)\(\s*['\"]([^'\"]+)['\"]",
        ]
        for pat in patterns:
            m=re.search(pat,onclick,re.I)
            if m:
                js_target=m.group(1); break

    candidate=href or js_target or action
    resolved_href=urljoin(base_url,candidate) if candidate else ""
    resolved_src=urljoin(base_url,src) if src else ""
    srcset=first_attr("srcset","data-srcset")
    if not resolved_src and srcset:
        first=srcset.split(",",1)[0].strip().split(" ",1)[0]
        if first:
            resolved_src=urljoin(base_url,first)

    corpus=" ".join([tag,text,direct," ".join(f"{k}={v}" for k,v in attrs.items())]).lower()

    if resolved_href:
        if resolved_href.startswith("mailto:") or "email" in corpus:
            return "Email Link", resolved_href, resolved_href
        if resolved_href.startswith("tel:") or "phone" in corpus or "telephone" in corpus:
            return "Phone Link", resolved_href, resolved_href
        if re.search(r"\.(pdf|docx?|xlsx?|csv|zip|pptx?|txt|rtf)(?:\?|#|$)", resolved_href, re.I):
            return "Document", resolved_href, resolved_href
        if re.search(r"\.(jpg|jpeg|png|webp|gif|svg|bmp|tiff?)(?:\?|#|$)", resolved_href, re.I):
            return "Image / Media Link", resolved_href, resolved_href
        if any(k in corpus for k in ("next","load more","show more","more results","pagination","page-next","next-page")):
            return "Pagination / Action", direct or text or resolved_href, resolved_href
        return "Relationship / Link", direct or text or resolved_href, resolved_href

    if tag == "img" and resolved_src:
        return "Image / Media", resolved_src, resolved_src
    if tag in {"video","audio","source","track"}:
        return "Media", resolved_src or src, resolved_src or src
    if tag in {"button"} or (tag == "input" and attrs.get("type","").lower() in {"button","submit","reset","image"}):
        if any(k in corpus for k in ("next","load more","show more","more results")):
            return "Pagination / Action", direct or text or attrs.get("value","") or onclick, js_target
        return "Action / Button", direct or text or attrs.get("value","") or onclick, js_target
    if tag in {"input","select","textarea","option"}:
        return "Form Input", attrs.get("value") or attrs.get("placeholder") or attrs.get("name") or direct or text, ""
    if tag == "form":
        return "Form", action, urljoin(base_url,action) if action else ""
    if tag == "iframe":
        target=urljoin(base_url,attrs.get("src", "")) if attrs.get("src") else ""
        return "Frame", target, target
    if tag in {"table","thead","tbody","tfoot","tr","td","th"}:
        return "Table / Structured Data", direct or text, ""
    if tag in {"ul","ol"}:
        items = [clean_text(child.text()) for child in node.children if child.tag == "li" and clean_text(child.text())]
        return "List / Group", " | ".join(items)[:4000] if items else (direct or text), ""
    if tag in {"html","body","nav","section","article","main","aside","header","footer","div","li","dl","dt","dd"}:
        return "Section / Container", direct or text, ""
    if tag in {"script","style","meta","link","noscript","template"}:
        target=attrs.get("content") or attrs.get("href") or attrs.get("src") or direct
        return "Metadata / Code", clean_text(str(target)), ""

    visible=direct or text
    m=re.search(r"https?://[^\s<>\"']+", visible, re.I)
    if m:
        target=m.group(0).rstrip(".,);]")
        return "Relationship / URL Candidate", target, target
    if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", visible):
        return "Email", visible, ""
    if re.search(r"(?:\+?\d[\d().\- ]{6,}\d)", visible):
        return "Phone", visible, ""
    return "Text / Content", visible, ""

def build_dom_catalog(root, base_url):
    """Catalog the rendered DOM for human inspection and target discovery.

    The catalog intentionally includes structural, interactive, media, form,
    link, and text elements. Each entry carries a real sample value so a user
    can identify useful elements without understanding CSS/XPath first.
    """
    rows=[]
    nodes=[*list(root.descendants())]
    for idx,node in enumerate(nodes, start=1):
        attrs=node.attrs or {}
        behavior,sample,target=_dom_element_behavior(node, base_url)
        path=_absolute_dom_path(node)
        if not path:
            continue
        # Preserve all attributes in compact form, while resolving navigable/media URLs.
        attrs_view=[]
        for k,v in attrs.items():
            val=clean_text(str(v))
            if k in {"href","src","data-src","data-original","data-href","data-url","data-link","data-target-url","routerlink","router-link","to","formaction","action","poster"} and val:
                val=urljoin(base_url,val)
            if val:
                attrs_view.append(f"{k}={val}")
        attr_text=" | ".join(attrs_view)[:1200]
        # Parent context helps distinguish otherwise similar nested elements.
        context=""
        cur=node.parent
        hops=0
        while cur is not None and cur.tag != "document" and hops < 3:
            candidate=_direct_text(cur) or cur.text()
            candidate=clean_text(candidate)
            if candidate and candidate != clean_text(sample):
                context=candidate[:350]
                break
            cur=cur.parent; hops+=1
        rows.append({
            "index": idx,
            "behavior": behavior,
            "tag": node.tag,
            "path": path,
            "sample": clean_text(str(sample))[:900],
            "target": clean_text(str(target))[:1200],
            "attributes": attr_text,
            "context": context,
        })
    return rows


def dom_search_score(item, query, example=""):
    """Rank DOM catalog entries against plain-language user intent."""
    q=clean_text(query).lower()
    if not q:
        return 1
    terms=[t for t in re.split(r"\s+",q) if t]
    behavior=item.get("behavior","").lower()
    target=item.get("target","").lower()
    sample=item.get("sample","").lower()
    attrs=item.get("attributes","").lower()
    path=item.get("path","").lower()
    context=item.get("context","").lower()
    tag=item.get("tag","").lower()
    hay=" ".join([behavior,sample,target,attrs,path,context,tag])
    score=0
    ex=clean_text(example).lower()
    if ex:
        if ex == sample: score+=120
        elif ex in sample: score+=80
        if ex in target: score+=70
        if ex in attrs: score+=45
        if ex in context: score+=30
    if q in sample: score+=40
    if q in target: score+=35
    if q in attrs: score+=30
    if q in behavior: score+=25
    if q in path: score+=20
    if q in context: score+=15
    synonyms={
        "link":("relationship","href","url","anchor","profile","detail"),
        "url":("relationship","href","link","website"),
        "profile":("relationship","href","detail","profile"),
        "email":("email","mailto"),
        "phone":("phone","telephone","tel"),
        "image":("image","media","src","photo","picture"),
        "document":("document","pdf","doc","download","file"),
        "next":("pagination","next","load more","show more"),
        "button":("action","button","submit"),
        "company":("company","business","organization","employer"),
        "address":("address","city","state","location","postal","zip"),
        "name":("name","title","heading","h1","h2","h3","person"),
        "price":("price","cost","amount","currency","usd","php","gbp","eur"),
        "date":("date","time","datetime","posted","updated","timeline","closing","closes","ends","deadline","schedule"),
        "download":("document","download","pdf","file","attachment"),
        "details":("relationship","href","profile","detail","view","read more"),
    }
    expanded=[]
    for t in terms:
        expanded.append(t)
        expanded.extend(synonyms.get(t,()))
    for term in expanded:
        if term in sample: score+=10
        if term in target: score+=9
        if term in attrs: score+=8
        if term in behavior: score+=8
        if term in path: score+=5
        if term in context: score+=3
    # Require some meaningful match when a query exists.
    return score if score > 0 else 0


def _relationship_url_family(url):
    """Normalize a navigable URL into a structural family.

    Dynamic path segments are replaced so repeated detail/profile/product links
    can be recognized as a dataset even when no useful repeated card/container
    exists. Query values are represented by their keys only.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path or "/"
    parts = [p for p in path.split("/") if p]
    normalized = []
    for part in parts:
        low = part.lower()
        if re.fullmatch(r"\d+", part):
            normalized.append("{id}")
        elif re.fullmatch(r"[0-9a-f]{8,}", low) or re.fullmatch(r"[a-z]*\d[a-z0-9_-]{3,}", low):
            normalized.append("{record}")
        elif len(part) >= 8 and ("-" in part or "_" in part):
            normalized.append("{record}")
        else:
            normalized.append(low)
    family = "/" + "/".join(normalized)
    if parsed.query:
        keys = sorted({k for k, _v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)})
        if keys:
            family += "?" + "&".join(f"{k}={{value}}" for k in keys)
    return f"{parsed.netloc.lower()}{family}"


def detect_relationship_datasets(root, base_url):
    """Discover record sets represented primarily by repeated relationships.

    This complements repeated-container detection. It never depends on a
    particular domain or path such as /merchant/. Each unique destination URL
    remains a separate record, allowing multiple branches with the same visible
    business name to survive as legitimate records.
    """
    rows = []
    seen_urls = set()
    for node in root.descendants():
        if node.tag != "a":
            continue
        href = clean_text(node.attrs.get("href", ""))
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base_url, href)
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        # Navigation/chrome links usually repeat the exact URL. Record identity is
        # destination-based, so only unique destinations enter the candidate set.
        if url in seen_urls:
            continue
        seen_urls.add(url)
        label = clean_text(node.text())
        if not label:
            label = clean_text(node.attrs.get("aria-label") or node.attrs.get("title") or "")
        if not label:
            continue
        rows.append({
            "label": label[:1000],
            "url": url,
            "family": _relationship_url_family(url),
        })

    grouped = defaultdict(list)
    for row in rows:
        if row["family"]:
            grouped[row["family"]].append(row)

    candidates = []
    for family, items in grouped.items():
        if len(items) < 3:
            continue
        # Require some label diversity so menus with repeated destinations are
        # less likely to be presented as record datasets.
        unique_labels = {r["label"].casefold() for r in items if r["label"]}
        if len(unique_labels) < 3:
            continue
        records = [
            {
                "Name / Link Text": r["label"],
                "Detail URL": r["url"],
                "Source Page URL": base_url,
            }
            for r in items
        ]
        candidates.append({
            "name": f"Relationship: {family}",
            "records": records,
            "fields": ["Name / Link Text", "Detail URL", "Source Page URL"],
            "score": len(records) * 10 + 24,
            "count": len(records),
            "dataset_type": "relationship",
            "relationship_family": family,
        })

    candidates.sort(key=lambda item: (item["score"], item["count"]), reverse=True)
    return candidates[:30]



def detect_document_datasets(root, base_url):
    """Discover pages whose useful records are downloadable resources.

    Directory pages are not always HTML card/list datasets. Some expose the
    actual directory as versioned PDF/CSV/XLS/XLSX/JSON downloads. Treat those
    resources as first-class records instead of letting surrounding navigation
    dominate relationship detection.
    """
    document_exts = {".pdf", ".csv", ".xls", ".xlsx", ".json", ".xml", ".zip"}
    records, seen = [], set()
    for node in root.descendants():
        if node.tag != "a":
            continue
        href = clean_text(node.attrs.get("href", ""))
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base_url, href)
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        ext = Path(parsed.path).suffix.lower()
        if ext not in document_exts:
            continue
        if url in seen:
            continue
        seen.add(url)
        label = clean_text(node.text()) or clean_text(node.attrs.get("title") or node.attrs.get("aria-label") or "")
        if not label:
            label = Path(parsed.path).name
        records.append({
            "Document Name": label[:1000],
            "Document URL": url,
            "Document Type": ext.lstrip(".").upper(),
            "Source Page URL": base_url,
        })
    if not records:
        return []
    return [{
        "name": "Downloadable directory/resources",
        "records": records,
        "fields": ["Document Name", "Document URL", "Document Type", "Source Page URL"],
        "score": len(records) * 10 + 80,
        "count": len(records),
        "dataset_type": "document_directory",
    }]


def classify_dataset_candidate(group, page_url=""):
    """Return a plain-language relevance label for nontechnical users."""
    dtype = group.get("dataset_type", "container")
    records = group.get("records", [])
    fields = [str(x).casefold() for x in group.get("fields", [])]
    samples = " ".join(
        clean_text(str(v)).casefold()
        for row in records[:12] for v in row.values() if v
    )
    name = clean_text(group.get("name", "")).casefold()
    blob = " ".join(fields) + " " + samples + " " + name

    if dtype == "document_directory":
        return "Likely Download Directory", "High"
    if dtype == "dynamic_api":
        # RECON011: transport/source type does not establish record relevance.
        # Evaluate the dataset's own schema and repeated-record behavior first.
        quality,_quality_note=_primary_dataset_quality(group)
        nav_score,_nav_note=_collection_navigation_score(group,page_url)
        metadata_score,_metadata_note=_page_metadata_support_score(group)
        classification_score,_classification_note=_classification_collection_score(group)

        entity_terms=(
            "email","phone","telephone","mobile","address","street","city","region",
            "postcode","postal","price","salary","date","image","photo","rating",
            "review","company","organization","description","summary"
        )
        entity_hits=sum(1 for t in entity_terms if t in " ".join(fields))

        if metadata_score>=0.62:
            return "Dynamic Page/Schema Metadata", "Low"
        if classification_score>=0.58:
            return "Dynamic Classification/Index Data", "Low"
        if nav_score>=0.62 and entity_hits<2:
            return "Dynamic Navigation/Index Data", "Low"
        if quality>=0.8 and (entity_hits>=2 or nav_score<0.50):
            return "Dynamic Entity Records", "High"
        return "Dynamic API Candidate", "Medium"

    nav_terms = ("about us", "privacy", "contact us", "training", "news", "events",
                 "login", "join", "resource center", "policies", "careers", "home")
    business_terms = ("company", "business", "merchant", "member", "branch", "address",
                      "postcode", "postal", "phone", "telephone", "email", "website",
                      "store", "supplier", "broker", "profile")
    form_terms = ("field_label", "gform-field-label", "required field", "validation",
                  "job title", "enter email address", "input", "captcha", "submit")
    nav_hits = sum(1 for t in nav_terms if t in blob)
    biz_hits = sum(1 for t in business_terms if t in blob)
    form_hits = sum(1 for t in form_terms if t in blob)

    # v1.6.1.2: form labels often contain words such as Company and Email.  Those
    # words must not make a newsletter/contact form look like a business dataset.
    if dtype != "document_directory" and form_hits >= 2:
        return "Likely Form Fields", "Low"

    if dtype == "relationship":
        urls = [r.get("Detail URL", "") for r in records[:20]]
        families = {_relationship_url_family(u) for u in urls if u}
        if nav_hits >= 4 and biz_hits <= 2 and len(records) < 40:
            return "Likely Site Navigation", "Low"
        if biz_hits >= 2 or len(records) >= 40:
            return "Likely Record Directory", "High" if biz_hits >= 3 else "Medium"
        if len(families) == 1 and len(records) >= 8:
            return "Possible Record Directory", "Medium"
        return "Relationship Links", "Medium"

    if biz_hits >= 2:
        return "Likely Business Directory", "High"
    if nav_hits >= 4 and len(records) < 40:
        return "Likely Page/Navigation Group", "Low"
    return "Repeated Page Data", "Medium"


def _primary_dataset_quality(group):
    """RECON006: rank record-like datasets above CMS/taxonomy/navigation support data."""
    records=list(group.get("records") or [])[:60]
    raw_fields=group.get("fields") or group.get("columns") or []
    if isinstance(raw_fields,dict):
        raw_fields=list(raw_fields.keys())
    fields=[clean_text(str(x)).lower() for x in raw_fields]
    if not fields and records:
        fields=[clean_text(str(x)).lower() for x in records[0].keys()]
    if not records:
        return -10.0, "empty"
    joined=" ".join(fields)
    cms_tokens=("taxonomy","taxonomies","yoast","robots","wp:","_links","curies",
                "collection","acf","parent","categories","category","tags","tag")
    cms_hits=sum(1 for t in cms_tokens if t in joined)
    identity=[f for f in fields if any(k in f for k in ("name","title","headline","label"))]
    url_fields=[f for f in fields if any(k in f for k in ("url","link","href","permalink"))]
    identity_density=0.0
    for f in identity:
        vals=[clean_text(str(r.get(f,""))) for r in records]
        vals=[v for v in vals if v and not (v.startswith("http://") or v.startswith("https://"))]
        if vals: identity_density=max(identity_density,len(vals)/len(records))
    unique_url=0.0
    for f in url_fields:
        vals=[clean_text(str(r.get(f,""))) for r in records]
        vals=[v for v in vals if (v.startswith("http://") or v.startswith("https://"))]
        if len(vals)>=2: unique_url=max(unique_url,len(set(vals))/len(vals))
    scalar=0; objectish=0
    for r in records[:25]:
        for v in r.values():
            text=clean_text(str(v))
            if not text: continue
            if text.startswith(("{","[","(")) and len(text)>80: objectish+=1
            elif len(text)<=240: scalar+=1
    scalar_ratio=scalar/max(1,scalar+objectish)
    score=2.4*identity_density+3.0*unique_url+1.2*scalar_ratio-min(5.0,cms_hits*.55)
    if cms_hits>=4 and unique_url<.70: score-=2.0
    return score, f"quality={score:.2f}; identity={identity_density:.2f}; unique-url={unique_url:.2f}; cms-noise={cms_hits}"

def _collection_navigation_score(group, page_url=""):
    """RECON008: score whether a repeated dataset behaves like an index/navigation collection."""
    records=list(group.get("records") or [])[:60]
    fields=list(group.get("fields") or [])
    if not fields and records:
        fields=list(records[0].keys())

    text_fields=[
        f for f in fields
        if any(k in str(f).lower() for k in ("name","title","text","label","link_text"))
    ]
    url_fields=[
        f for f in fields
        if any(k in str(f).lower() for k in ("url","link","href","permalink"))
    ]
    if not records or not url_fields:
        return 0.0, "no repeated navigation evidence"

    labels=[]
    urls=[]
    for r in records:
        label=""
        for f in text_fields:
            v=clean_text(str(r.get(f,"")))
            if v and not v.startswith(("http://","https://")):
                label=v
                break
        if label:
            labels.append(label)

        for f in url_fields:
            u=clean_text(str(r.get(f,"")))
            if u.startswith(("http://","https://")):
                urls.append(u)
                break

    if len(urls)<3:
        return 0.0, "insufficient URL collection evidence"

    families={}
    shallow=0
    short_terminal=0
    from urllib.parse import urlsplit
    for u in urls:
        try:
            parts=urlsplit(u)
            seg=[x for x in parts.path.split("/") if x]
        except Exception:
            seg=[]
        if not seg:
            continue
        parent="/".join(seg[:-1]).lower()
        terminal=seg[-1]
        families[parent]=families.get(parent,0)+1
        if len(seg)<=2:
            shallow+=1
        if len(terminal)<=30 and len(terminal.split("-"))<=4:
            short_terminal+=1

    family_ratio=max(families.values())/len(urls) if families else 0.0
    shallow_ratio=shallow/len(urls)
    short_ratio=short_terminal/len(urls)

    label_shape=0
    if labels:
        short_labels=sum(1 for x in labels if len(x.split())<=3 and len(x)<=40)
        alpha_labels=sum(1 for x in labels if re.fullmatch(r"[A-Za-z .&'-]{1,40}",x or ""))
        label_shape=max(short_labels/len(labels), alpha_labels/len(labels))

    field_blob=" ".join(str(f).lower() for f in fields)
    entity_semantics=sum(
        1 for t in (
            "email","phone","telephone","address","postcode","postal",
            "description","summary","price","date","image","photo","rating",
            "company","organization"
        )
        if t in field_blob
    )

    score=(0.40*family_ratio)+(0.25*shallow_ratio)+(0.20*short_ratio)+(0.15*label_shape)
    score-=min(0.55,entity_semantics*0.12)
    return max(0.0,min(1.0,score)), (
        f"collection-nav={score:.2f}; family={family_ratio:.2f}; "
        f"shallow={shallow_ratio:.2f}; short={short_ratio:.2f}; "
        f"label-shape={label_shape:.2f}; entity-semantics={entity_semantics}"
    )


def _page_metadata_support_score(group):
    """RECON009: distinguish page/schema support metadata from repeated entity data."""
    records=list(group.get("records") or [])[:80]
    fields=list(group.get("fields") or [])
    if not fields and records:
        fields=list(dict.fromkeys(k for r in records for k in r.keys()))
    blob=" ".join(str(f).lower() for f in fields)

    support_tokens=(
        "@context","inlanguage","ispartof","breadcrumb","potentialaction",
        "searchaction","entrypoint","urltemplate","publisher","webpage",
        "website","collectionpage","breadcrumblist","itemlistorder"
    )
    support_hits=sum(t in blob for t in support_tokens)

    type_values=[]
    for r in records:
        for k,v in r.items():
            if str(k).lower() in ("@type","type"):
                vals=v if isinstance(v,(list,tuple)) else [v]
                type_values.extend(str(x).lower() for x in vals)
    support_types=("website","webpage","collectionpage","breadcrumblist","searchaction","entrypoint")
    support_type_hits=sum(any(t==v or t in v for t in support_types) for v in type_values)

    row_count=len(records)
    shapes={tuple(sorted(str(k).lower() for k in r.keys())) for r in records}
    heterogeneity=(len(shapes)/row_count) if row_count else 0.0
    tiny_graph=1.0 if 0 < row_count <= 12 else 0.0

    entity_tokens=(
        "email","telephone","phone","streetaddress","addresslocality",
        "postalcode","price","ratingvalue","reviewcount","latitude","longitude",
        "jobtitle","first_name","last_name"
    )
    entity_hits=sum(t in blob for t in entity_tokens)

    score=(min(.45,support_hits*.07)+min(.25,support_type_hits*.08)
           +.15*tiny_graph+.15*min(1.0,heterogeneity)-min(.45,entity_hits*.12))
    score=max(0.0,min(1.0,score))
    return score,(f"page-metadata={score:.2f}; support-fields={support_hits}; "
                  f"support-types={support_type_hits}; rows={row_count}; "
                  f"heterogeneity={heterogeneity:.2f}; entity-fields={entity_hits}")


def _classification_collection_score(group):
    """RECON010: detect classification/taxonomy/index datasets without site-specific rules."""
    records=list(group.get("records") or [])[:80]
    fields=list(group.get("fields") or [])
    if not fields and records:
        fields=list(dict.fromkeys(k for r in records for k in r.keys()))
    low=[str(f).lower() for f in fields]
    blob=" ".join(low)

    classification_tokens=(
        "taxonomy","taxonomies","category","categories","tag","tags",
        "parent","slug","term","terms","count","classification","facet","facets"
    )
    class_hits=sum(1 for t in classification_tokens if t in blob)

    # Strong entity evidence is deliberately narrower than generic content.
    # A taxonomy/category record can legitimately have a "description" field;
    # that alone must not make the collection entity-ready.
    strong_entity_tokens=(
        "email","phone","telephone","mobile","address","street","city","region",
        "postcode","postal","price","salary","date","image","photo","rating",
        "review","company","organization"
    )
    weak_entity_tokens=("description","summary")
    entity_hits=sum(1 for t in strong_entity_tokens if t in blob)
    weak_entity_hits=sum(1 for t in weak_entity_tokens if t in blob)

    # Repeated numeric counters plus short labels are common classification/index evidence.
    numeric_fields=0
    for f in fields:
        vals=[clean_text(str(r.get(f,""))) for r in records[:40]]
        vals=[v for v in vals if v]
        if vals and sum(v.replace(",","").isdigit() for v in vals)/len(vals) >= .70:
            numeric_fields+=1

    label_fields=[f for f in fields if any(k in str(f).lower() for k in ("name","title","label","text"))]
    short_label_ratio=0.0
    for f in label_fields:
        vals=[clean_text(str(r.get(f,""))) for r in records[:40]]
        vals=[v for v in vals if v and not v.startswith(("http://","https://"))]
        if vals:
            ratio=sum(len(v)<=45 and len(v.split())<=5 for v in vals)/len(vals)
            short_label_ratio=max(short_label_ratio,ratio)

    # Generic URL-family structure, reusing the navigation evidence already computed.
    nav_score,_=_collection_navigation_score(group,"")

    # RECON013: DOM browse/filter collections may have no URL and no explicit
    # taxonomy field. Detect their generic control/label structure instead.
    selector_blob=" ".join(str(f).lower() for f in fields)
    control_tokens=(
        "cursor-pointer","button","option","select","dropdown","accordion",
        "menu","nav","filter","facet","toggle","items-center","aria-expanded"
    )
    control_hits=sum(1 for t in control_tokens if t in selector_blob)
    url_field_count=sum(
        1 for f in fields
        if any(t in str(f).lower() for t in ("url","href","link","permalink"))
    )
    label_only_structure=(
        len(records)>=8 and len(fields)<=4 and short_label_ratio>=0.70
        and url_field_count==0 and entity_hits==0
    )
    control_collection=(
        len(records)>=5 and short_label_ratio>=0.60
        and control_hits>=1 and entity_hits==0
    )

    score=(
        min(.58,class_hits*.095)
        + min(.15,numeric_fields*.08)
        + (.18*short_label_ratio)
        + (.22*nav_score)
        + (.34 if label_only_structure else 0)
        + min(.30,control_hits*.10)
        + (.18 if control_collection else 0)
        - min(.48,entity_hits*.10)
        - min(.06,weak_entity_hits*.03)
    )
    score=max(0.0,min(1.0,score))
    return score,(
        f"classification-collection={score:.2f}; classification-fields={class_hits}; "
        f"numeric-fields={numeric_fields}; short-labels={short_label_ratio:.2f}; "
        f"navigation={nav_score:.2f}; control-fields={control_hits}; "
        f"label-only={int(label_only_structure)}; entity-fields={entity_hits}; "
        f"weak-entity-fields={weak_entity_hits}"
    )


def _form_ui_support_score(group):
    """RECON014: detect form labels, validation/help text, and UI-control datasets."""
    records=list(group.get("records") or [])[:80]
    fields=list(group.get("fields") or [])
    if not fields and records:
        fields=list(dict.fromkeys(k for r in records for k in r.keys()))

    field_blob=" ".join(str(f).lower() for f in fields)
    values=[]
    for r in records[:40]:
        for v in r.values():
            text=clean_text(str(v))
            if text:
                values.append(text)
    value_blob=" | ".join(values[:240]).lower()

    control_tokens=(
        "form-field","form_field","field-label","field_label","input","textarea",
        "checkbox","radio","select","option","placeholder","required","validation",
        "captcha","honeypot","submit","button","aria-label","aria-describedby",
        "gform","gravityform","form-control","form_group"
    )
    control_hits=sum(1 for t in control_tokens if t in field_blob)

    validation_phrases=(
        "validation purposes","should be left unchanged","required field",
        "please enter","please select","invalid email","invalid phone",
        "this field is required","submit","send message"
    )
    validation_hits=sum(1 for t in validation_phrases if t in value_blob)

    # Very small label/value collections dominated by contact-field names are
    # interaction metadata unless they contain row-level entity values.
    label_words=("name","email","phone","telephone","address","message","subject","website")
    label_like_values=0
    for v in values:
        low=clean_text(v).lower().strip(" :*")
        if low in label_words or any(low==f"{x} *" for x in label_words):
            label_like_values+=1
    label_ratio=label_like_values/max(1,len(values))

    # Generic selector/control evidence.
    selector_hits=sum(
        1 for t in ("label.","input.","form.","button.","field.","[text]","[value]")
        if t in field_blob
    )

    score=(
        min(.50,control_hits*.10)
        + min(.28,validation_hits*.14)
        + min(.20,selector_hits*.07)
        + (.18 if label_ratio>=.35 else 0)
        + (.12 if 0 < len(records)<=5 and (control_hits or validation_hits or label_ratio>=.25) else 0)
    )
    score=max(0.0,min(1.0,score))
    return score,(
        f"form-ui={score:.2f}; control-fields={control_hits}; validation={validation_hits}; "
        f"label-ratio={label_ratio:.2f}; selector-evidence={selector_hits}; rows={len(records)}"
    )


def _entity_row_coherence_score(group):
    """RECON014: measure whether repeated rows behave like coherent entities, not isolated UI labels."""
    records=list(group.get("records") or [])[:60]
    fields=list(group.get("fields") or [])
    if not records:
        return 0.0,"no rows"

    semantic_groups={
        "identity":("name","title","company","organization","business","member","profile"),
        "contact":("email","phone","telephone","mobile"),
        "location":("address","street","city","state","region","county","postcode","postal","location"),
        "detail":("url","href","link","website","permalink"),
        "content":("description","summary","price","date","image","photo","rating","review"),
    }

    field_groups={}
    for f in fields:
        low=str(f).lower()
        matched=set()
        for group,tokens in semantic_groups.items():
            if any(t in low for t in tokens):
                matched.add(group)
        field_groups[f]=matched

    coherent=0
    nonempty=0
    identity_or_detail=0
    for r in records:
        present=set()
        scalar_values=0
        for f,v in r.items():
            text=clean_text(str(v))
            if not text:
                continue
            scalar_values+=1
            present.update(field_groups.get(f,set()))
        if scalar_values:
            nonempty+=1
        if "identity" in present or "detail" in present:
            identity_or_detail+=1
        # Two independent semantic dimensions in one row is strong entity evidence.
        if len(present)>=2 and ("identity" in present or "detail" in present):
            coherent+=1

    coherence=coherent/max(1,nonempty)
    backbone=identity_or_detail/max(1,nonempty)

    # Rich generic rows can still be coherent even when field names are opaque.
    avg_nonempty=sum(
        sum(1 for v in r.values() if clean_text(str(v)))
        for r in records
    )/max(1,len(records))
    if avg_nonempty>=5 and backbone>=.60:
        coherence=max(coherence,.65)

    score=min(1.0,(coherence*.72)+(backbone*.28))
    return score,(
        f"entity-coherence={score:.2f}; coherent-rows={coherent}/{max(1,nonempty)}; "
        f"backbone={backbone:.2f}; avg-nonempty={avg_nonempty:.1f}"
    )


def _entity_dataset_ready(group, page_url=""):
    """RECON008: decide whether a dataset is trustworthy enough to be a primary entity source."""
    quality=float(group.get("primary_quality",0) or 0)
    nav_score,nav_note=_collection_navigation_score(group,page_url)

    fields=list(group.get("fields") or [])
    field_blob=" ".join(str(f).lower() for f in fields)
    semantic_entity_hits=sum(
        1 for t in (
            "email","phone","telephone","address","city","state","region","postcode",
            "postal","description","summary","price","date","image","photo","company",
            "organization","rating","review"
        )
        if t in field_blob
    )

    metadata_score, metadata_note=_page_metadata_support_score(group)
    classification_score, classification_note=_classification_collection_score(group)
    form_score, form_note=_form_ui_support_score(group)
    coherence_score, coherence_note=_entity_row_coherence_score(group)

    # A dataset must exhibit repeated row-level entity structure. Generic contact
    # words in form labels are not enough. Small samples remain eligible when
    # coherence is strong.
    ready=(
        quality>=0.8
        and metadata_score<0.62
        and classification_score<0.58
        and form_score<0.58
        and coherence_score>=0.48
        and (
            nav_score<0.62
            or semantic_entity_hits>=2
        )
    )
    return ready, nav_score, (
        f"{nav_note}; {metadata_note}; {classification_note}; {form_note}; "
        f"{coherence_note}; primary-quality={quality:.2f}; "
        f"entity-semantics={semantic_entity_hits}"
    )


def annotate_dataset_candidates(groups, page_url=""):
    for group in groups:
        if "_raw_candidate_score" not in group:
            group["_raw_candidate_score"]=float(group.get("score",0) or 0)
        label, relevance = classify_dataset_candidate(group, page_url)
        group["discovery_label"] = label
        group["relevance"] = relevance
        boost = {"High": 60, "Medium": 10, "Low": -80}.get(relevance, 0)
        quality, quality_note = _primary_dataset_quality(group)
        group["primary_quality"] = quality
        group["primary_quality_note"] = quality_note
        nav_score, nav_note = _collection_navigation_score(group, page_url)
        group["collection_navigation_score"] = nav_score
        group["collection_navigation_note"] = nav_note
        metadata_score, metadata_note = _page_metadata_support_score(group)
        group["page_metadata_support_score"] = metadata_score
        group["page_metadata_support_note"] = metadata_note
        classification_score, classification_note = _classification_collection_score(group)
        group["classification_collection_score"] = classification_score
        group["classification_collection_note"] = classification_note
        form_score, form_note = _form_ui_support_score(group)
        group["form_ui_support_score"] = form_score
        group["form_ui_support_note"] = form_note
        coherence_score, coherence_note = _entity_row_coherence_score(group)
        group["entity_row_coherence_score"] = coherence_score
        group["entity_row_coherence_note"] = coherence_note
        # Quality is deliberately strong enough to demote CMS support datasets,
        # but it never removes them from discovery.
        group["score"] = (
            float(group.get("_raw_candidate_score",0) or 0)
            + boost + (quality * 22.0)
            - (nav_score * 55.0)
            - (metadata_score * 65.0)
            - (classification_score * 75.0)
            - (form_score * 80.0)
            + (coherence_score * 38.0)
        )
    groups.sort(key=lambda item: (item.get("score", 0), item.get("primary_quality",-10), item.get("count", 0)), reverse=True)
    return groups

def merge_dataset_candidates(container_groups, relationship_groups, document_groups=None, page_url=""):
    """Merge complementary discovery strategies without hiding either one."""
    combined = list(container_groups) + list(document_groups or [])
    seen = {
        (g.get("name", ""), g.get("count", 0),
         tuple(r.get("url", "") for r in g.get("records", [])[:3]))
        for g in combined
    }
    for group in relationship_groups:
        fp = (
            group.get("name", ""), group.get("count", 0),
            tuple(r.get("Detail URL", "") for r in group.get("records", [])[:3])
        )
        if fp not in seen:
            combined.append(group)
            seen.add(fp)
    return annotate_dataset_candidates(combined, page_url)[:60]



def _resource_extension(url, content_type=""):
    try:
        ext = Path(urlparse(url).path).suffix.lower()
    except Exception:
        ext = ""
    if ext:
        return ext
    ct=(content_type or "").lower()
    return {
        "application/pdf":".pdf",
        "text/csv":".csv",
        "application/json":".json",
        "application/xml":".xml",
        "text/xml":".xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":".xlsx",
        "application/vnd.ms-excel":".xls",
    }.get(ct.split(";",1)[0].strip(), "")


def _resource_records_to_group(name, records, dataset_type="resource_dataset", source_url=""):
    if not records:
        return None
    fields=[]
    for row in records:
        for key in row:
            if key not in fields:
                fields.append(key)
    # RECON011: structural size is useful evidence, but record count must not
    # overwhelm semantic/entity qualification. Use bounded/log-like evidence.
    count_score=min(60, 8 + int((len(records) ** 0.5) * 5))
    field_score=min(35, len(fields)*3)
    base_score=count_score+field_score
    return {
        "name": name,
        "records": records,
        "fields": fields,
        "score": base_score,
        "count": len(records),
        "dataset_type": dataset_type,
        "source_resource_url": source_url,
        "discovery_label": "Structured Resource Records",
        "relevance": "Medium",
    }


def _parse_csv_resource(data, url):
    raw=data.decode("utf-8-sig", errors="replace")
    sample=raw[:8192]
    try:
        dialect=csv.Sniffer().sniff(sample)
    except Exception:
        dialect=csv.excel
    reader=csv.reader(io.StringIO(raw), dialect)
    rows=list(reader)
    if not rows:
        return []
    width=max(len(r) for r in rows)
    first=[clean_text(x) for x in rows[0]]
    has_header=False
    try: has_header=csv.Sniffer().has_header(sample)
    except Exception: pass
    headers=(first if has_header else [f"Column {i+1}" for i in range(width)])
    if len(headers)<width: headers += [f"Column {i+1}" for i in range(len(headers),width)]
    body=rows[1:] if has_header else rows
    records=[]
    for r in body:
        vals=list(r)+[""]*(width-len(r))
        if any(clean_text(str(x)) for x in vals):
            records.append({headers[i] or f"Column {i+1}":clean_text(str(vals[i])) for i in range(width)})
    g=_resource_records_to_group("CSV Records",records,source_url=url)
    return [g] if g else []


def _parse_json_resource(data, url):
    obj=json.loads(data.decode("utf-8",errors="replace"))
    arrays=discover_json_arrays(obj)
    groups=[]
    for path,count in sorted(arrays,key=lambda x:x[1],reverse=True)[:10]:
        try: arr=get_json_path(obj,path)
        except Exception: continue
        records=[flatten_json(item) for item in arr if isinstance(item,dict)]
        g=_resource_records_to_group(f"JSON Records: {path or '$'}",records,source_url=url)
        if g: groups.append(g)
    if not groups and isinstance(obj,dict):
        g=_resource_records_to_group("JSON Object",[flatten_json(obj)],source_url=url)
        if g: groups.append(g)
    return groups


def _dynamic_json_groups(payload, url):
    """Convert a browser-observed JSON payload into dataset candidates.

    RELINT001HF002 deliberately works from response content rather than known
    endpoint names. Any repeated array of JSON objects can become a candidate.
    """
    try:
        if isinstance(payload, (bytes, bytearray)):
            obj=json.loads(bytes(payload).decode("utf-8", errors="replace"))
        elif isinstance(payload, str):
            obj=json.loads(payload)
        else:
            obj=payload
    except Exception:
        return []

    groups=[]
    arrays=discover_json_arrays(obj)
    for path,count in sorted(arrays, key=lambda x:x[1], reverse=True)[:12]:
        if count < 3:
            continue
        try:
            arr=get_json_path(obj,path)
        except Exception:
            continue
        records=[flatten_json(item) for item in arr if isinstance(item,dict)]
        g=_resource_records_to_group(
            f"Dynamic JSON: {path or '$'}", records,
            dataset_type="dynamic_api", source_url=url
        )
        if not g:
            continue
        label,relevance=classify_dataset_candidate(g,url)
        quality,quality_note=_primary_dataset_quality(g)
        g["discovery_label"]=label
        g["relevance"]=relevance
        g["primary_quality"]=quality
        g["primary_quality_note"]=quality_note

        # Keep this pre-annotation score intentionally small. The common
        # annotation pipeline is authoritative and will apply the full evidence.
        base_boost={"High":18,"Medium":4,"Low":-24}.get(relevance,0)
        g["score"]=min(90,float(g.get("score",0)))+base_boost
        groups.append(g)
    return groups


def _merge_dynamic_groups(groups):
    """Conservatively merge equivalent network datasets without hiding sources."""
    merged={}
    order=[]
    for group in groups:
        key=(tuple(group.get("fields",[])), group.get("name",""))
        if key not in merged:
            clone=dict(group); clone["records"]=[]; clone["count"]=0
            merged[key]=clone; order.append(key)
        target=merged[key]
        fields=group.get("fields",[])
        seen={tuple(clean_text(str(r.get(f,""))) for f in fields) for r in target["records"]}
        for row in group.get("records",[]):
            fp=tuple(clean_text(str(row.get(f,""))) for f in fields)
            if fp not in seen:
                target["records"].append(dict(row)); seen.add(fp)
        target["count"]=len(target["records"])
        # RECON012: merging must preserve the bounded candidate-scoring contract.
        # Record count is evidence of repetition, not evidence of entity relevance.
        count_score=min(60, 8 + int((target["count"] ** 0.5) * 5))
        field_score=min(35, len(fields)*3)
        target["score"]=count_score+field_score
        target.pop("_raw_candidate_score",None)
    result=[merged[k] for k in order if merged[k].get("count",0)>=3]
    result.sort(key=lambda g:(g.get("score",0),g.get("count",0)),reverse=True)
    return result[:40]


def _parse_xml_resource(data, url):
    root=ET.fromstring(data)
    groups=[]
    counts=Counter(child.tag for child in list(root))
    for tag,count in counts.most_common():
        if count<2: continue
        records=[]
        for child in list(root):
            if child.tag!=tag: continue
            row={}
            for elem in child.iter():
                if elem is child: continue
                key=elem.tag.split('}')[-1]
                val=clean_text(elem.text or "")
                if val and key not in row: row[key]=val
            row.update({f"@{k}":clean_text(v) for k,v in child.attrib.items() if clean_text(v)})
            if row: records.append(row)
        g=_resource_records_to_group(f"XML Records: {tag.split('}')[-1]}",records,source_url=url)
        if g: groups.append(g)
    return groups


def _pdf_lines_by_columns(page, columns=3):
    """Return top-to-bottom text lines independently for each visual column."""
    words=page.extract_words(x_tolerance=2,y_tolerance=3,use_text_flow=False) or []
    if not words: return [[] for _ in range(columns)]
    width=float(page.width or 1)
    buckets=[[] for _ in range(columns)]
    for w in words:
        cx=(float(w.get("x0",0))+float(w.get("x1",0)))/2
        idx=min(columns-1,max(0,int(cx/(width/columns))))
        buckets[idx].append(w)
    output=[]
    for bucket in buckets:
        bucket.sort(key=lambda w:(round(float(w.get("top",0))/3)*3,float(w.get("x0",0))))
        lines=[]; current=[]; current_top=None
        for w in bucket:
            top=float(w.get("top",0))
            if current_top is None or abs(top-current_top)<=3.5:
                current.append(w); current_top=top if current_top is None else (current_top+top)/2
            else:
                current.sort(key=lambda x:float(x.get("x0",0)))
                lines.append(clean_text(" ".join(str(x.get("text","")) for x in current)))
                current=[w]; current_top=top
        if current:
            current.sort(key=lambda x:float(x.get("x0",0)))
            lines.append(clean_text(" ".join(str(x.get("text","")) for x in current)))
        output.append([x for x in lines if x])
    return output



_PDF_GENERIC_NOISE_PATTERNS = (
    r"^\s*page\s+\d+(?:\s+of\s+\d+)?\s*$",
    r"^\s*\d+\s*$",
    r"^\s*directory(?:\s+of\b.*)?$",
    r"^\s*version\b",
    r"^\s*a[\s-]*z\s+of\b",
    r"^\s*(?:total\s+)?number\s+of\s+branches\b",
    r"^\s*branches\s+by\s+(?:country|region|area)\b",
    r"^\s*buying\s+group\b",
    r"^\s*total\s+number\b",
    r"^\s*(?:notes?|legend|key)\s*[:\-]?\s*$",
)

def _pdf_is_noise_line(line):
    """Reject generic page furniture, report summaries and obvious footer/header rows."""
    value = clean_text(line)
    if not value:
        return True
    low = value.lower()
    for pattern in _PDF_GENERIC_NOISE_PATTERNS:
        if re.search(pattern, low, re.I):
            return True
    # Dense summary/header rows often contain several schema-like phrases rather
    # than one business record. Penalize them without depending on a named PDF.
    schema_terms = sum(
        1 for phrase in (
            "number of branches", "branches by country", "buying group",
            "total number", "company details", "contact details",
            "telephone email website", "email website postcode",
            "england scotland wales", "n. ireland", "n ireland", "roi",
            "branches england", "branches scotland", "branches wales"
        ) if phrase in low
    )
    if schema_terms >= 2:
        return True

    # Generic aggregate/table-summary guard: a business record rarely contains
    # several geography/summary headings at once. This intentionally targets
    # report furniture rather than any named directory or company.
    aggregate_tokens = sum(
        1 for token in (
            "england", "scotland", "wales", "ireland", "roi",
            "buying group", "number of branches", "total branches"
        ) if token in low
    )
    if aggregate_tokens >= 3:
        return True

    # Rows made mostly of short heading-like fragments are usually table headers
    # or summary/footer text rather than company records.
    parts = [clean_text(p) for p in re.split(r"[|•·]+", value) if clean_text(p)]
    if len(parts) >= 4:
        headingish = sum(
            1 for p in parts
            if len(p.split()) <= 4 and not re.search(r"@|\d{4,}|\.[A-Z]{2,}", p, re.I)
        )
        if headingish >= max(4, len(parts) - 1):
            return True

    # Generic count/summary row guard. Directory reports often end with lines
    # composed of short organization labels plus small integer counts or legends.
    # A genuine business record is unlikely to have many short fragments where
    # several are bare counts and none contains a normal contact signal.
    if len(parts) >= 4:
        numeric_parts = sum(
            1 for p in parts
            if re.fullmatch(r"(?:\d{1,4}|\d{1,4}(?:\.\d+)?)", p)
        )
        short_label_parts = sum(
            1 for p in parts
            if not re.search(r"@|https?://|www\.|\b(?:GIR\s?0AA|[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", p, re.I)
            and len(p.split()) <= 5
            and len(p) <= 40
        )
        contact_parts = sum(
            1 for p in parts
            if re.search(
                r"@|https?://|www\.|\b(?:GIR\s?0AA|[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b|"
                r"(?:\+?44\s?\(?(?:0\)?\s?)?|0)\d[\d\s()/-]{7,}\d",
                p, re.I
            )
        )
        if numeric_parts >= 2 and short_label_parts >= 3 and contact_parts == 0:
            return True

    # Legend-style explanatory text with abbreviations/counts should not become
    # a company record, even if a site domain appears elsewhere in the footer.
    if re.search(r"\b(?:legend|key|republic of|abbreviation|total|branches?)\b", low, re.I):
        count_tokens = len(re.findall(r"(?<![A-Za-z])\d{1,4}(?![A-Za-z])", value))
        if count_tokens >= 2:
            return True
    return False

def _pdf_extract_websites(text):
    """Return public-looking web domains/URLs without swallowing trailing prose."""
    value = clean_text(text)
    # Full URLs first, then bare www/domain forms.
    candidates = re.findall(
        r"\b(?:https?://)?(?:www\.)?[A-Z0-9][A-Z0-9.-]*\.[A-Z]{2,}"
        r"(?:/[A-Z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?",
        value, re.I
    )
    cleaned = []
    for item in candidates:
        item = item.rstrip(".,;:)|]")
        # Avoid treating an email's right-hand side as a website duplicate.
        start = value.lower().find(item.lower())
        if start > 0 and value[start-1:start] == "@":
            continue
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned

def _pdf_clean_company_name(line):
    """Strip contact tokens accidentally collapsed onto a company-name line."""
    value = clean_text(line)
    if not value:
        return ""
    # Remove emails, URLs/domains, telephone labels/numbers and postcode suffixes.
    value = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", " ", value, flags=re.I)
    for web in sorted(_pdf_extract_websites(value), key=len, reverse=True):
        value = value.replace(web, " ")
    value = re.sub(
        r"(?:\b(?:tel|telephone|phone|fax)\s*[:\-]?\s*)?"
        r"(?:\+?44\s?\(?(?:0\)?\s?)?|0)\d[\d\s()/-]{7,}\d",
        " ", value, flags=re.I
    )
    value = re.sub(
        r"\b(?:GIR\s?0AA|[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b\s*$",
        " ", value, flags=re.I
    )
    value = re.sub(r"\s*[|•·]\s*$", " ", value)
    return clean_text(value).strip(" |,-")

def _pdf_quality_status(record):
    """Add transparent extraction-quality signals without changing user data."""
    notes = []
    name = clean_text(record.get("Name", ""))
    record_text = clean_text(record.get("Record Text", ""))
    if not name:
        notes.append("missing company/name")
    if name and (_pdf_is_noise_line(name) or len(name) > 140):
        notes.append("name resembles page furniture")
    if re.search(r"https?://|www\.", name, re.I):
        notes.append("website remained in name")
    if not any(clean_text(record.get(k, "")) for k in ("Email", "Phone", "Website", "Postcode")):
        notes.append("no structured contact fields")
    if _pdf_is_noise_line(record_text):
        notes.append("record resembles summary/header/footer")
    return ("Review" if notes else "Clean", "; ".join(notes))

def _looks_like_pdf_company_start(line):
    value=clean_text(line)
    low=value.lower().strip()
    if not value or len(value)>180: return False
    if _pdf_is_noise_line(value): return False
    if low.startswith(("directory", "version ", "a-z of company details", "www.", "http://", "https://",
                       "t:", "f:", "e:", "tel:", "email:", "phone:", "website:")): return False
    if re.fullmatch(r"\b(?:GIR\s?0AA|[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", value, re.I): return False
    # A line consisting only of a contact token is not a record start.
    if re.fullmatch(r"(?:https?://)?(?:www\.)?[A-Z0-9.-]+\.[A-Z]{2,}(?:/\S*)?", value, re.I): return False
    if re.fullmatch(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", value, re.I): return False
    cleaned=_pdf_clean_company_name(value)
    if not cleaned or len(cleaned)<2: return False
    return bool(re.search(r"[A-Za-z]",cleaned))


def _pdf_contact_blocks(lines, page_no, col_no):
    """Generic contact-directory segmentation with page-furniture suppression."""
    records=[]; block=[]; has_contact=False

    def flush():
        nonlocal block,has_contact
        block=[clean_text(x) for x in block if clean_text(x) and not _pdf_is_noise_line(x)]
        if not block:
            has_contact=False
            return
        text_value=" | ".join(block)
        emails=re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",text_value,re.I)
        phones=re.findall(r"(?:\+?44\s?\(?(?:0\)?\s?)?|0)\d[\d\s()/-]{7,}\d",text_value)
        websites=_pdf_extract_websites(text_value)
        postcodes=re.findall(r"\b(?:GIR\s?0AA|[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b",text_value,re.I)

        raw_name=next((x for x in block if _looks_like_pdf_company_start(x)),block[0])
        name=_pdf_clean_company_name(raw_name)
        if not name or _pdf_is_noise_line(name):
            block=[]; has_contact=False
            return

        if has_contact or emails or phones or websites:
            rec={
                "Name":name,
                "Record Text":text_value,
                "Email":" | ".join(dict.fromkeys(emails)),
                "Phone":" | ".join(dict.fromkeys(clean_text(x) for x in phones)),
                "Website":" | ".join(dict.fromkeys(websites)),
                "Postcode":" | ".join(dict.fromkeys(x.upper() for x in postcodes)),
                "PDF Page":str(page_no),
                "PDF Column":str(col_no),
            }
            status,notes=_pdf_quality_status(rec)
            rec["Quality Status"]=status
            rec["Quality Notes"]=notes
            # Page furniture and schema/footer lines should never become exported
            # records. Ambiguous business records remain available with Review status.
            aggregate_blob = " | ".join(
                clean_text(rec.get(k, "")) for k in ("Name", "Record Text", "Website")
            )
            # If the surviving row has no person/business contact identity and the
            # text is dominated by counts/short labels, treat it as aggregate data.
            structured_contacts = sum(
                1 for k in ("Email", "Phone", "Postcode")
                if clean_text(rec.get(k, ""))
            )
            summary_like = _pdf_is_noise_line(aggregate_blob)
            if structured_contacts == 0:
                fragments = [clean_text(p) for p in re.split(r"[|•·]+", aggregate_blob) if clean_text(p)]
                bare_counts = sum(1 for p in fragments if re.fullmatch(r"\d{1,4}", p))
                short_labels = sum(1 for p in fragments if len(p.split()) <= 5 and len(p) <= 40)
                if bare_counts >= 2 and short_labels >= 4:
                    summary_like = True

            if (
                "summary/header/footer" not in notes
                and "name resembles page furniture" not in notes
                and not summary_like
            ):
                records.append(rec)
        block=[]; has_contact=False

    for raw_line in lines:
        line=clean_text(raw_line)
        if not line or _pdf_is_noise_line(line):
            continue
        contact=bool(
            re.search(r"(^|\s)(t:|f:|e:|tel:|phone:|email:)|@",line,re.I)
            or _pdf_extract_websites(line)
        )
        if block and has_contact and _looks_like_pdf_company_start(line):
            flush()
        block.append(line)
        has_contact=has_contact or contact
    flush()
    return records


def _parse_pdf_resource(data, url):
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("PDF extraction requires pdfplumber. Run Install_Document_Engine.bat once, then restart RTUDES.") from exc
    records=[]; table_groups=[]
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page_no,page in enumerate(pdf.pages,1):
            # First preserve real tables when the PDF provides them.
            for ti,table in enumerate(page.extract_tables() or [],1):
                cleaned=[[clean_text(str(c or "")) for c in row] for row in table if row]
                cleaned=[r for r in cleaned if any(r)]
                if len(cleaned)>=3 and max(len(r) for r in cleaned)>=2:
                    width=max(len(r) for r in cleaned)
                    header=cleaned[0]+[f"Column {i+1}" for i in range(len(cleaned[0]),width)]
                    rows=[]
                    for r in cleaned[1:]:
                        vals=r+[""]*(width-len(r))
                        rows.append({header[i] or f"Column {i+1}":vals[i] for i in range(width)})
                    g=_resource_records_to_group(f"PDF Table p{page_no}.{ti}",rows,source_url=url)
                    if g: table_groups.append(g)
            # Generic multi-column directory fallback. Three columns is common for
            # printable A-Z directories; additionally try one/two columns when the
            # three-column parse yields too little evidence.
            best=[]
            for col_count in (3,2,1):
                candidate=[]
                for ci,lines in enumerate(_pdf_lines_by_columns(page,col_count),1):
                    candidate.extend(_pdf_contact_blocks(lines,page_no,ci))
                if len(candidate)>len(best): best=candidate
            records.extend(best)
    # Conservative dedupe: prefer structured identity while keeping legitimate
    # same-name branch/location records when their contact/address text differs.
    dedup=[]; seen=set()
    for r in records:
        if _pdf_is_noise_line(r.get("Name","")):
            continue
        key=(
            clean_text(r.get("Name","")).casefold(),
            clean_text(r.get("Email","")).casefold(),
            clean_text(r.get("Phone","")),
            clean_text(r.get("Postcode","")).casefold(),
            clean_text(r.get("Website","")).casefold(),
            clean_text(r.get("Record Text","")).casefold(),
        )
        if key in seen: continue
        seen.add(key); dedup.append(r)
    groups=[]
    g=_resource_records_to_group("PDF Directory Records",dedup,source_url=url)
    if g: groups.append(g)
    groups.extend(table_groups)
    groups.sort(key=lambda x:x.get("count",0),reverse=True)
    return groups


def _parse_excel_resource(data, url, ext):
    if ext==".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise RuntimeError("Legacy XLS extraction requires xlrd. Run Install_Document_Engine.bat once.") from exc
        book=xlrd.open_workbook(file_contents=data); groups=[]
        for sh in book.sheets():
            rows=[[clean_text(str(sh.cell_value(r,c))) for c in range(sh.ncols)] for r in range(sh.nrows)]
            if not rows: continue
            headers=rows[0]; records=[]
            for vals in rows[1:]:
                if any(vals): records.append({headers[i] or f"Column {i+1}":vals[i] for i in range(len(vals))})
            g=_resource_records_to_group(f"Excel Sheet: {sh.name}",records,source_url=url)
            if g: groups.append(g)
        return groups
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("XLSX extraction requires openpyxl. Run Install_Document_Engine.bat once.") from exc
    wb=openpyxl.load_workbook(io.BytesIO(data),read_only=True,data_only=True); groups=[]
    for ws in wb.worksheets:
        rows=[[clean_text(str(v if v is not None else "")) for v in row] for row in ws.iter_rows(values_only=True)]
        if not rows: continue
        width=max(len(r) for r in rows); headers=rows[0]+[f"Column {i+1}" for i in range(len(rows[0]),width)]
        records=[]
        for r in rows[1:]:
            vals=r+[""]*(width-len(r))
            if any(vals): records.append({headers[i] or f"Column {i+1}":vals[i] for i in range(width)})
        g=_resource_records_to_group(f"Excel Sheet: {ws.title}",records,source_url=url)
        if g: groups.append(g)
    return groups


def parse_structured_resource(url):
    request=urllib.request.Request(url,headers={"User-Agent":USER_AGENT})
    with urllib.request.urlopen(request,timeout=60) as response:
        data=response.read(); final_url=response.geturl(); content_type=response.headers.get("Content-Type","")
    ext=_resource_extension(final_url,content_type)
    if ext==".pdf": groups=_parse_pdf_resource(data,final_url)
    elif ext==".csv": groups=_parse_csv_resource(data,final_url)
    elif ext==".json": groups=_parse_json_resource(data,final_url)
    elif ext in {".xml"}: groups=_parse_xml_resource(data,final_url)
    elif ext in {".xlsx",".xls"}: groups=_parse_excel_resource(data,final_url,ext)
    else: raise RuntimeError(f"Unsupported structured resource type: {ext or content_type or 'unknown'}")
    return final_url,ext,groups

def merge_dataset_pages(page_groups):
    """Merge equivalent dataset candidates discovered across result pages.

    Relationship datasets are keyed by URL family; container datasets by
    structural signature + field schema. Records are conservatively deduplicated
    by their full row values, so legitimate branches with different detail URLs
    remain separate.
    """
    merged = {}
    order = []
    for groups in page_groups:
        for group in groups:
            if group.get("dataset_type") == "relationship":
                key = (
                    "relationship",
                    group.get("relationship_family", group.get("name", "")),
                    tuple(group.get("fields", [])),
                )
            else:
                key = (
                    "container",
                    group.get("name", ""),
                    tuple(group.get("fields", [])),
                )
            if key not in merged:
                clone = dict(group)
                clone["records"] = []
                clone["count"] = 0
                merged[key] = clone
                order.append(key)
            target = merged[key]
            fields = group.get("fields", [])
            seen = {
                tuple(clean_text(str(row.get(f, ""))) for f in fields)
                for row in target["records"]
            }
            for row in group.get("records", []):
                fp = tuple(clean_text(str(row.get(f, ""))) for f in fields)
                if fp not in seen:
                    target["records"].append(dict(row))
                    seen.add(fp)
            target["count"] = len(target["records"])
            target["score"] = target["count"] * 10 + max(0, len(fields) * 5)
    result = [merged[k] for k in order if merged[k]["count"] >= 3]
    result.sort(key=lambda item: (item.get("score",0), item.get("count",0)), reverse=True)
    return result[:60]


def _dataset_from_nodes(nodes, signature, base_url, promoted=False, ancestor_hops=0):
    """Build a repeated-container dataset from DOM nodes.

    RELINT001HF001 uses the same field-consistency rules as the protected
    detector, while allowing a proven repeated child structure to nominate a
    richer repeated ancestor (for example, hours -> enclosing store card).
    """
    records = [node_features(node, base_url) for node in nodes]
    records = [record for record in records if record]
    if len(records) < 3:
        return None

    counts = Counter()
    for record in records:
        counts.update(record.keys())
    fields = [
        key for key, count in counts.items()
        if count >= max(2, int(len(records) * 0.45))
    ]
    if not fields:
        return None

    score = len(records) * 10 + len(fields) * 5
    for preferred in ("title", "text", "url", "price", "image_url"):
        if preferred in fields:
            score += 8
    if promoted:
        # A small generic bonus lets a richer enclosing record outrank a
        # repetitive child component without suppressing the original choice.
        score += min(35, max(0, len(fields) - 2) * 4)

    return {
        "name": signature.replace("|", "."),
        "records": [
            {field: record.get(field, "") for field in fields}
            for record in records
        ],
        "fields": fields,
        "score": score,
        "count": len(records),
        "dataset_type": "container_promoted" if promoted else "container",
        "ancestor_hops": ancestor_hops if promoted else 0,
    }


def _promoted_ancestor_candidates(nodes, base_url, max_hops=4):
    """Promote repetitive child components to distinct richer ancestors.

    A valid promotion must preserve at least three distinct record containers.
    Shared page/body ancestors collapse to one node and are therefore rejected.
    No domain, CSS selector, or business-specific wording is required.
    """
    promoted=[]
    child_dataset=_dataset_from_nodes(nodes, nodes[0].signature(), base_url) if nodes else None
    child_field_count=len(child_dataset.get("fields", [])) if child_dataset else 0

    for hops in range(1, max_hops + 1):
        ancestors=[]
        for node in nodes:
            cur=node
            for _ in range(hops):
                cur=getattr(cur, "parent", None)
                if cur is None or cur.tag == "document":
                    break
            if cur is not None and cur.tag != "document" and cur.tag in BLOCK_TAGS:
                ancestors.append(cur)

        # Multiple child components may resolve to the same enclosing card.
        unique_nodes=[]
        seen_ids=set()
        for node in ancestors:
            ident=id(node)
            if ident not in seen_ids:
                seen_ids.add(ident); unique_nodes.append(node)
        if len(unique_nodes) < 3:
            continue

        grouped=defaultdict(list)
        for node in unique_nodes:
            if len(node.text()) >= 5:
                grouped[node.signature()].append(node)
        for signature, ancestor_nodes in grouped.items():
            if len(ancestor_nodes) < 3:
                continue
            item=_dataset_from_nodes(ancestor_nodes, signature, base_url, promoted=True, ancestor_hops=hops)
            if not item:
                continue
            # Promotion must add meaningful structure, not just move one wrapper up.
            if len(item.get("fields", [])) <= child_field_count:
                continue
            item["name"] = f"Enclosing records (+{hops}): " + item["name"]
            promoted.append(item)
    return promoted


def detect_repeated_groups(root, base_url):
    candidates = []
    promotion_sources=[]
    for parent in [root, *list(root.descendants())]:
        if len(parent.children) < 3:
            continue

        grouped = defaultdict(list)
        for child in parent.children:
            if child.tag not in BLOCK_TAGS or len(child.text()) < 5:
                continue
            grouped[child.signature()].append(child)

        for signature, nodes in grouped.items():
            if len(nodes) < 3:
                continue
            item=_dataset_from_nodes(nodes, signature, base_url)
            if not item:
                continue
            candidates.append(item)
            promotion_sources.append(nodes)

    # RELINT001HF001: nested repeated widgets (hours, badges, buttons, etc.) can
    # be structurally stronger than the business/result card containing them.
    # Inspect a few ancestor levels and retain richer repeated enclosing records
    # as additional candidates; never discard the original dataset.
    for nodes in promotion_sources:
        candidates.extend(_promoted_ancestor_candidates(nodes, base_url))

    candidates.sort(key=lambda item: (item["score"], item["count"]), reverse=True)

    unique, seen = [], set()
    for item in candidates:
        fingerprint = (
            item["name"], tuple(item["fields"]), item["count"],
            tuple(tuple(row.get(f, "") for f in item["fields"]) for row in item["records"][:2])
        )
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(item)
    return unique[:30]


UNIVERSAL_FIELDS = [
    "Page Title", "Detail URL", "Detected Fields", "Primary Image",
    "Image Count", "Image URLs", "Image Folder", "Document Count",
    "Documents", "Document Folder"
]

class LineTextParser(HTMLParser):
    """Convert HTML into ordered visible-text lines for resilient label parsing."""
    BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol",
        "p", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul"
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if not self.skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if not self.skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            value = clean_text(data)
            if value:
                self.parts.append(value)

    def lines(self):
        text = " ".join(self.parts)
        return [clean_text(line) for line in text.splitlines() if clean_text(line)]



class UniversalMediaParser(HTMLParser):
    """Discover image and document candidates without site-specific URL rules."""
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
    DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip", ".txt", ".ppt", ".pptx"}
    NEGATIVE_MARKERS = {
        "logo", "icon", "sprite", "avatar", "profile", "headshot", "captcha",
        "facebook", "twitter", "linkedin", "instagram", "favicon", "loading",
        "spinner", "arrow", "button", "badge", "pixel", "tracking"
    }
    POSITIVE_MARKERS = {"gallery", "photo", "image", "property", "product", "listing", "large", "zoom", "hero"}

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.images = {}
        self.documents = []
        self.title_parts = []
        self.in_title = False
        self.current_link = None
        self.current_link_text = []

    def _image_score(self, url, attrs):
        marker = " ".join([url, attrs.get("class", ""), attrs.get("id", ""), attrs.get("alt", ""), attrs.get("title", "")]).lower()
        score = 10
        if any(x in marker for x in self.NEGATIVE_MARKERS): score -= 100
        if any(x in marker for x in self.POSITIVE_MARKERS): score += 25
        for key in ("width", "height"):
            try:
                n = int(re.sub(r"[^0-9]", "", attrs.get(key, "")) or 0)
                if n >= 400: score += 20
                elif 0 < n <= 80: score -= 50
            except ValueError:
                pass
        if attrs.get("data-large") or attrs.get("data-original"): score += 30
        if "thumbnail" in marker or "thumb" in marker: score -= 10
        return score

    def _add_image(self, value, attrs=None):
        if not value or value.startswith("data:"):
            return
        url = urljoin(self.base_url, value.strip())
        ext = Path(urlparse(url).path).suffix.lower()
        if ext not in self.IMAGE_EXTENSIONS:
            return
        score = self._image_score(url, attrs or {})
        key = url.split("?", 1)[0]
        self.images[key] = max(score, self.images.get(key, -999))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "img":
            for key in ("data-large", "data-image", "data-full", "data-src", "data-original", "src"):
                self._add_image(attrs.get(key, ""), attrs)
            for item in attrs.get("srcset", "").split(","):
                self._add_image(item.strip().split(" ")[0], attrs)
        elif tag == "source":
            for item in attrs.get("srcset", "").split(","):
                self._add_image(item.strip().split(" ")[0], attrs)
        elif tag == "meta" and attrs.get("property", "").lower() in {"og:image", "twitter:image"}:
            self._add_image(attrs.get("content", ""), attrs)
        elif tag == "a":
            href = urljoin(self.base_url, attrs.get("href", ""))
            ext = Path(urlparse(href).path).suffix.lower()
            if ext in self.IMAGE_EXTENSIONS:
                self._add_image(href, attrs)
            self.current_link = href if ext in self.DOCUMENT_EXTENSIONS else None
            self.current_link_text = []

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.current_link:
            self.current_link_text.append(data)

    def handle_endtag(self, tag):
        if tag == "title": self.in_title = False
        if tag == "a" and self.current_link:
            label = clean_text(" ".join(self.current_link_text)) or Path(urlparse(self.current_link).path).name
            self.documents.append((label, self.current_link))
            self.current_link = None
            self.current_link_text = []

    def image_urls(self):
        ranked = sorted(self.images.items(), key=lambda x: x[1], reverse=True)
        positive = [url for url, score in ranked if score >= 10]
        return positive or [url for url, score in ranked if score > -50]

    def document_items(self):
        out, seen = [], set()
        for label, url in self.documents:
            if url not in seen:
                seen.add(url); out.append((label, url))
        return out

    def page_title(self):
        return clean_text(" ".join(self.title_parts))


def extract_universal_media(html_text, detail_url):
    parser = UniversalMediaParser(detail_url)
    parser.feed(html_text)
    return parser.image_urls(), parser.document_items(), parser.page_title()



class UniversalSemanticPairParser(HTMLParser):
    """Discover generic label/value pairs without domain-specific vocabulary.

    Sources include definition lists, two-cell table rows, form-style labels,
    metadata, and compact heading/value blocks. Field names come from the page.
    """
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url=base_url
        self.stack=[]
        self.current_tag=None
        self.current_parts=[]
        self.dt_text=""
        self.pending_label=""
        self.table_row=[]
        self.pairs=[]
        self.meta=[]

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs); self.stack.append(tag)
        if tag in {"dt","dd","th","td","label","h1","h2","h3","h4","h5","h6"}:
            self.current_tag=tag; self.current_parts=[]
        if tag=="tr": self.table_row=[]
        if tag=="meta":
            key=clean_text(attrs.get("property") or attrs.get("name") or "")
            val=clean_text(attrs.get("content") or "")
            if key and val and not key.lower().startswith(("twitter:","og:image")):
                self.meta.append((key,val))

    def handle_data(self, data):
        if self.current_tag:
            self.current_parts.append(data)

    def handle_endtag(self, tag):
        if self.current_tag==tag:
            text=clean_text(" ".join(self.current_parts))
            if tag=="dt": self.dt_text=text
            elif tag=="dd" and self.dt_text and text:
                self.pairs.append((self.dt_text,text,"definition")); self.dt_text=""
            elif tag in {"th","td"}: self.table_row.append(text)
            elif tag=="label" and text: self.pending_label=text
            elif tag in {"h1","h2","h3","h4","h5","h6"} and text:
                self.pending_label=text
            self.current_tag=None; self.current_parts=[]
        if tag=="tr":
            cells=[x for x in self.table_row if x]
            if len(cells)==2: self.pairs.append((cells[0],cells[1],"table"))
            self.table_row=[]
        if self.stack:
            try: self.stack.remove(tag)
            except ValueError: pass


def _semantic_label_ok(label):
    label=clean_text(label).rstrip(":").strip()
    if not label or len(label)>90 or len(label.split())>10: return False
    low=label.lower()
    if low.startswith(("http://","https://","www.")): return False
    if low in {"home","menu","search","login","sign in","next","previous","back"}: return False
    if sum(ch.isalpha() for ch in label)<2: return False
    return True


def _node_compact_text(node, max_len=4000):
    if node is None:
        return ""
    try:
        text=clean_text(node.text())
    except Exception:
        text=""
    return text if text and len(text)<=max_len else ""

def _is_probable_value_for_label(label, value):
    label=clean_text(label).rstrip(":").strip()
    value=clean_text(value).strip()
    if not label or not value or value.lower()==label.lower() or len(value)>4000:
        return False
    if value.lower() in {"home","menu","search","login","sign in","next","previous","back","view","details","more","close","submit"}:
        return False
    if value.endswith(":") and _semantic_label_ok(value[:-1]):
        return False
    return True

def _structural_neighbor_values(node):
    """Yield nearby DOM values in increasing structural distance."""
    seen=set()

    def emit(candidate, distance, source):
        text=_node_compact_text(candidate)
        key=text.lower()
        if text and key not in seen:
            seen.add(key)
            return distance,source,text
        return None

    parent=getattr(node,"parent",None)
    if not parent:
        return

    siblings=list(getattr(parent,"children",[]) or [])
    try:
        idx=siblings.index(node)
    except ValueError:
        idx=-1

    if idx>=0:
        for offset in (1,2,3):
            pos=idx+offset
            if pos<len(siblings):
                item=emit(siblings[pos],offset,"next-sibling")
                if item:
                    yield item

    grand=getattr(parent,"parent",None)
    if grand:
        peers=list(getattr(grand,"children",[]) or [])
        try:
            pidx=peers.index(parent)
        except ValueError:
            pidx=-1

        if pidx>=0:
            for offset in (1,2):
                pos=pidx+offset
                if pos<len(peers):
                    item=emit(peers[pos],3+offset,"parent-sibling")
                    if item:
                        yield item

        for child in peers[:12]:
            if child is parent:
                continue
            item=emit(child,6,"wrapper-peer")
            if item:
                yield item

    for child in siblings[:16]:
        if child is node:
            continue
        item=emit(child,5,"same-wrapper")
        if item:
            yield item

def recover_structural_label_value_pairs(parser):
    """DETAILSEM004: associate page-derived labels with nearby values generically."""
    pairs=[]
    nodes=[parser.root,*list(parser.root.descendants())]

    for node in nodes:
        tag=(getattr(node,"tag","") or "").lower()
        if tag in {"script","style","noscript","svg","path","meta","link","head","document"}:
            continue

        direct=clean_text(_direct_text(node))
        if not _semantic_label_ok(direct):
            continue

        label=direct.rstrip(":").strip()
        best=None

        for distance,source,value in _structural_neighbor_values(node):
            if label.lower() in value.lower() and len(value)>len(label)+8:
                remainder=clean_text(
                    re.sub(re.escape(label)+r"\s*:?\s*","",value,count=1,flags=re.I)
                )
                if _is_probable_value_for_label(label,remainder):
                    value=remainder
                else:
                    continue

            if not _is_probable_value_for_label(label,value):
                continue

            score=100-(distance*10)+(10 if direct.rstrip().endswith(":") else 0)
            if best is None or score>best[0]:
                best=(score,value,source,distance)

        if best:
            _,value,source,distance=best
            pairs.append((label,value,source,distance))

    return pairs


def extract_exhaustive_page_candidates(html_text, detail_url):
    """DISCOVERY001: preserve viable page-derived values before semantics rank them.

    This catalog is intentionally domain-neutral.  It keeps visible/direct text,
    useful attributes and nearby label/value relationships so low-confidence or
    not-yet-understood data remains available for manual selection.  Recommendation
    logic may rank/suppress candidates, but discovery must not silently discard them.
    """
    parser=DOMParser(detail_url)
    try:
        parser.feed(html_text)
    except Exception:
        return {}
    out={}
    noise_tags={"script","style","noscript","svg","path","meta","link","head"}
    useful_attrs=("href","src","data-src","data-original","title","aria-label","alt",
                  "datetime","data-email","data-phone","data-id","content","value")
    nodes=[parser.root, *list(parser.root.descendants())]
    for node in nodes:
        tag=(node.tag or "").lower()
        if not tag or tag in noise_tags or tag=="document":
            continue
        path=_absolute_dom_path(node)
        if not path:
            continue
        direct=clean_text(_direct_text(node))
        if direct and len(direct)<=4000:
            out.setdefault(f"raw:{path} [text]", direct)
        attrs=node.attrs or {}
        for attr in useful_attrs:
            val=clean_text(str(attrs.get(attr) or ""))
            if not val:
                continue
            if attr in {"href","src","data-src","data-original"}:
                val=urljoin(detail_url,val)
            if len(val)<=4000:
                out.setdefault(f"raw:{path} [{attr}]",val)

        # Generic adjacent label/value preservation.
        parent=getattr(node,"parent",None)
        if parent and direct:
            siblings=getattr(parent,"children",[]) or []
            try:
                idx=siblings.index(node)
            except ValueError:
                idx=-1
            if idx>0:
                prev=clean_text(_direct_text(siblings[idx-1])).rstrip(":").strip()
                if _semantic_label_ok(prev) and prev.lower()!=direct.lower():
                    out.setdefault(f"semantic:{prev}",direct)

    # Structural associations are additive. Raw values and unresolved labels stay visible.
    for label,value,source,distance in recover_structural_label_value_pairs(parser):
        out.setdefault(f"semantic:{label}",value)
        out.setdefault(f"association:{label} [{source};distance={distance}]",value)
    return out


def extract_universal_semantic_features(html_text, detail_url, base_features=None):
    """Return stable, page-derived semantic fields for any HTML detail page.

    This deliberately contains no domain names, fixed site fields, selectors, or
    website profiles. It augments DOM-path features rather than replacing them.
    """
    out=extract_exhaustive_page_candidates(html_text, detail_url)
    out.update(dict(base_features or {}))
    pair_parser=UniversalSemanticPairParser(detail_url)
    try: pair_parser.feed(html_text)
    except Exception: pass

    # Structured label/value constructs.
    for label,value,_source in pair_parser.pairs:
        label=clean_text(label).rstrip(":").strip(); value=clean_text(value)
        if _semantic_label_ok(label) and value and value.lower()!=label.lower() and len(value)<=4000:
            out.setdefault(f"semantic:{label}",value)

    # Structural neighborhood association supplements—never filters—the catalog.
    structural_parser=DOMParser(detail_url)
    try:
        structural_parser.feed(html_text)
        for label,value,source,distance in recover_structural_label_value_pairs(structural_parser):
            out.setdefault(f"semantic:{label}",value)
            out.setdefault(f"association:{label} [{source};distance={distance}]",value)
    except Exception:
        pass
    for label,value in pair_parser.meta:
        if _semantic_label_ok(label) and len(value)<=4000:
            out.setdefault(f"meta:{label}",value)

    # DOM-independent visible-line fallback. A label ending in ':' owns the next
    # useful line; a short standalone line may own the next line when the latter
    # looks value-like. This handles DIV/SPAN based record metadata generically.
    lp=LineTextParser()
    try: lp.feed(html_text); lines=lp.lines()
    except Exception: lines=[]
    noise={"cookie","privacy","terms","copyright","navigation","share"}
    for i,line in enumerate(lines[:-1]):
        raw=clean_text(line); nxt=clean_text(lines[i+1])
        if not raw or not nxt or len(nxt)>4000: continue
        label=raw.rstrip(":").strip()
        explicit=raw.endswith(":")
        value_like=bool(re.search(r"[@$£€#]|\d|https?://",nxt,re.I))
        if _semantic_label_ok(label) and (explicit or (len(label)<=45 and value_like)):
            if not any(x in label.lower() for x in noise) and nxt.lower()!=label.lower():
                out.setdefault(f"semantic:{label}",nxt)

    return out

def parse_universal_detail(html_text, detail_url):
    images, documents, title = extract_universal_media(html_text, detail_url)
    dom = DOMParser(detail_url)
    dom.feed(html_text)
    fields = {}
    # Detect common two-cell label/value table rows.
    for table in dom.tables:
        for row in table:
            if len(row) == 2:
                label = clean_text(row[0].get("text", ""))
                value = clean_text(row[1].get("text", ""))
                if label and value and len(label) <= 80:
                    fields.setdefault(label.rstrip(":"), value)
    # Add generic semantic label/value candidates discovered from page structure.
    semantic = extract_universal_semantic_features(html_text, detail_url, {})
    for source, value in semantic.items():
        if source.startswith("semantic:"):
            fields.setdefault(source.split(":",1)[1], value)
    doc_value = " | ".join(f"{label}: {url}" for label, url in documents)
    return {
        "Page Title": title, "Detail URL": detail_url,
        "Detected Fields": json.dumps(fields, ensure_ascii=False),
        "Primary Image": images[0] if images else "",
        "Image Count": str(len(images)), "Image URLs": " | ".join(images),
        "Image Folder": "", "Document Count": str(len(documents)),
        "Documents": doc_value, "Document Folder": ""
    }, images


BUILD_MARKER_RECON002 = "RTUDES-1.8.5.8-RECON015"
BUILD_MARKER_RECON045 = "RTUDES-1.8.8.8-RECON045"
# Preserved regression lineage: RTUDES-1.8.5.9-RECON016

class ScraperApp(tk.Tk):

    def _apply_level_research_handoff(self, records, processed, mapping, key_field="id"):
        from semantic_intent import bind_level_research_evidence
        candidates=[]
        for name in ("level_research_results","research_results","level_results","acquisition_results","fetched_results","detail_results"):
            v=getattr(self,name,None)
            if isinstance(v,(list,tuple)) and v:
                candidates=list(v); break
        if not candidates:
            self.level_handoff_diagnostics={"completed":0,"bound":0,"unbound":0,"evidence_items":0,"qualified_recoveries":0,"records_improved":0,"rejected":0}
            return processed
        try:
            updated,stats=bind_level_research_evidence(records,processed,mapping,candidates,key_field)
            self.level_handoff_diagnostics=stats
            return updated
        except Exception as exc:
            self.level_handoff_diagnostics={"error":str(exc)}
            return processed

    def __init__(self):
        super().__init__()
        self.title("RAM-Tech Universal Data Extraction Studio v1.8.5.8")
        self.geometry("1320x820")
        self.minsize(1050, 700)

        self.cookie_jar = http.cookiejar.CookieJar()
        self.session_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )

        self.detected_groups = []
        self.table_data = []
        self.links = []
        self.api_headers = []
        self.api_rows = []
        self.api_json = None
        self.multilevel_rows = []
        self.multilevel_stop = threading.Event()
        # Persist user-created profiles/projects outside the versioned application
        # folder so they survive upgrades and newly extracted release folders.
        local_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        self.app_data_dir = local_root / "RAM-Tech" / "UniversalDataExtractionStudio"
        self.projects_file = self.app_data_dir / "Projects" / "projects.json"
        self.settings_file = self.app_data_dir / "Settings" / "settings.json"
        self.saved_projects = []
        self.builder_mappings = []
        self.semantic_desired_fields = []
        self.semantic_research_results = []
        self.semantic_industry_profile = "Auto / General"
        self.semantic_transform_rules = {}
        self.semantic_saved_profile_name = ""
        self.semantic_key_field = ""
        self.semantic_processed_records = []
        self.semantic_processor_summary = {}
        self.builder_preview_headers = []
        self.builder_preview_rows = []
        self.builder_profiles_dir = self.app_data_dir / "Profiles" / "Custom"
        self.custom_profiles = {}
        self.builder_field_samples = {}
        self.builder_detail_fields = {}
        self.builder_detail_url = ""
        self.builder_detail_mappings = []
        # Guided Field & Level Manager state. Level 1 is the listing dataset;
        # subsequent levels are inspected relationship targets discovered by the user.
        self.builder_levels = {}
        self.builder_current_level = 1
        self.builder_detail_link_field = ""
        self.builder_guided_rows = []
        # v1.5.4 Multi-Level Extraction Workspace. Each inspected page level gets
        # its own tab and shares one live final-output summary.
        self.builder_page_trees = {}
        self.builder_page_frames = {}
        # DOM Intelligence: complete rendered-element catalog + plain-language search.
        self.dom_catalogs = {}
        self.dom_catalog_filtered = {}
        # UEI Foundation: current website analysis and human-readable extraction plan.
        self.uei_analysis = {}
        self.uei_field_rows = []
        self.uei_selected_fields = set()
        # PROFILEBATCH001 — persistent in-app website profiling harness.
        self.benchmark_sites = []
        self.benchmark_results = []
        self.benchmark_stop = threading.Event()
        self.benchmark_running = False

        self._migrate_legacy_user_data()
        self._configure_ramtech_theme()
        self._build_ui()


    def _migrate_legacy_user_data(self):
        """Copy user-created data from a version folder into persistent app data.

        Older builds stored Projects/Profiles/Settings beside app.py. Keeping a
        non-destructive copy here prevents saved profiles from disappearing when
        the user extracts a newer ZIP into a different folder.
        """
        import shutil
        for rel in (Path("Projects") / "projects.json", Path("Settings") / "settings.json"):
            src = Path.cwd() / rel
            dst = self.app_data_dir / rel
            try:
                if src.exists() and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            except Exception:
                pass
        legacy_profiles = Path.cwd() / "Profiles" / "Custom"
        try:
            if legacy_profiles.exists():
                self.builder_profiles_dir.mkdir(parents=True, exist_ok=True)
                for src in legacy_profiles.glob("*.json"):
                    dst = self.builder_profiles_dir / src.name
                    if not dst.exists():
                        shutil.copy2(src, dst)
        except Exception:
            pass

    def _configure_ramtech_theme(self):
        """Apply the RAM-Tech visual identity without changing scraper behavior."""
        self.configure(background="#EAF0F6")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        navy = "#0B1F33"
        blue = "#1769AA"
        light_blue = "#DCEAF7"
        surface = "#FFFFFF"
        canvas = "#EAF0F6"
        text = "#172B3A"

        style.configure("TFrame", background=canvas)
        style.configure("Card.TFrame", background=surface)
        style.configure("TLabel", background=canvas, foreground=text, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=surface, foreground=text, font=("Segoe UI", 10))
        style.configure("Header.TLabel", background=navy, foreground="white", font=("Segoe UI Semibold", 19))
        style.configure("Subheader.TLabel", background=navy, foreground="#BFD6EA", font=("Segoe UI", 10))
        style.configure("Version.TLabel", background=navy, foreground="#DCEAF7", font=("Segoe UI", 9))
        style.configure("Footer.TLabel", background=navy, foreground="#DCEAF7", font=("Segoe UI", 9))
        style.configure("TLabelFrame", background=surface, foreground=navy, borderwidth=1, relief="solid")
        style.configure("TLabelFrame.Label", background=surface, foreground=navy, font=("Segoe UI Semibold", 10))
        style.configure("TEntry", fieldbackground="white", foreground=text, padding=5)
        style.configure("TCombobox", fieldbackground="white", foreground=text, padding=4)
        style.configure("TCheckbutton", background=surface, foreground=text, font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI Semibold", 9), padding=(10, 6), background=light_blue, foreground=navy)
        style.map("TButton", background=[("active", "#C8DDF0"), ("pressed", "#AFCBE4")])
        style.configure("Accent.TButton", background=blue, foreground="white", font=("Segoe UI Semibold", 9), padding=(12, 7))
        style.map("Accent.TButton", background=[("active", "#145B93"), ("pressed", "#104B79")], foreground=[("disabled", "#D9E5EF")])
        style.configure("Danger.TButton", background="#B42318", foreground="white")
        style.map("Danger.TButton", background=[("active", "#912018")])
        style.configure("TNotebook", background=canvas, borderwidth=0, tabmargins=(2, 6, 2, 0))
        style.configure("TNotebook.Tab", background="#C9D8E6", foreground=navy, font=("Segoe UI Semibold", 9), padding=(14, 8))
        style.map("TNotebook.Tab", background=[("selected", surface), ("active", light_blue)], foreground=[("selected", blue)])
        style.configure("Treeview", background="white", fieldbackground="white", foreground=text, rowheight=26, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background=navy, foreground="white", font=("Segoe UI Semibold", 9), relief="flat", padding=6)
        style.map("Treeview.Heading", background=[("active", blue)])
        style.configure("Horizontal.TScrollbar", background="#B9CAD9")
        style.configure("Vertical.TScrollbar", background="#B9CAD9")

    def _build_ui(self):
        header = ttk.Frame(self, style="Card.TFrame")
        header.pack(fill="x")
        brand = tk.Frame(header, bg="#0B1F33", height=82)
        brand.pack(fill="x")
        brand.pack_propagate(False)
        title_area = ttk.Frame(brand, style="Card.TFrame")
        title_area.configure()
        # Native Tk frame is used here so the full banner keeps the RAM-Tech navy color.
        tk.Label(brand, text="RAM-TECH", bg="#0B1F33", fg="white", font=("Segoe UI Semibold", 20)).pack(side="left", padx=(20, 12), pady=(14, 0), anchor="n")
        text_box = tk.Frame(brand, bg="#0B1F33")
        text_box.pack(side="left", fill="y", pady=12)
        tk.Label(text_box, text="Universal Data Extraction Studio", bg="#0B1F33", fg="white", font=("Segoe UI Semibold", 18)).pack(anchor="w")
        tk.Label(text_box, text="Multi-level web data, media and document extraction", bg="#0B1F33", fg="#BFD6EA", font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
        tk.Label(brand, text="v1.8.9.14  •  DYNAMIC-RELATIONSHIP-BIND001", bg="#0B1F33", fg="#DCEAF7", font=("Segoe UI", 9)).pack(side="right", padx=20)

        self.main_notebook = ttk.Notebook(self)
        self.main_notebook.pack(fill="both", expand=True, padx=12, pady=(10, 8))

        self.workspace_tab = ttk.Frame(self.main_notebook)
        self.intelligence_tab = ttk.Frame(self.main_notebook)
        self.public_tab = ttk.Frame(self.main_notebook)
        self.login_tab = ttk.Frame(self.main_notebook)
        self.api_tab = ttk.Frame(self.main_notebook)
        self.multilevel_tab = ttk.Frame(self.main_notebook)
        self.benchmark_tab = ttk.Frame(self.main_notebook)

        self.main_notebook.add(self.workspace_tab, text="Projects")
        self.main_notebook.add(self.intelligence_tab, text="Intelligent Wizard")
        self.main_notebook.add(self.public_tab, text="Public Website")
        self.main_notebook.add(self.login_tab, text="Login Website")
        self.main_notebook.add(self.api_tab, text="JSON API")
        self.main_notebook.add(self.multilevel_tab, text="Multi-Level Crawl")
        self.main_notebook.add(self.benchmark_tab, text="Profiler Lab")

        self._build_workspace_tab()
        self._build_intelligence_tab()
        self._build_public_tab()
        self._build_login_tab()
        self._build_api_tab()
        self._build_multilevel_tab()
        self._build_benchmark_tab()

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var, style="Footer.TLabel", padding=(14, 7)).pack(fill="x")

    def _build_benchmark_tab(self):
        """Batch website profiler / regression harness.

        Runs each site in an isolated subprocess so one pathological website
        cannot freeze the RTUDES GUI or prevent the remaining benchmark from
        continuing.  This module is diagnostic and does not alter extraction
        profiles, mappings, projects or output files.
        """
        top = ttk.LabelFrame(self.benchmark_tab, text="Website Profiling Benchmark Runner", padding=10)
        top.pack(fill="x", padx=10, pady=10)
        ttk.Label(
            top,
            text=(
                "Load a website list, then profile every URL automatically. Each website runs in an isolated "
                "worker with its own time budget. Results separate Access, Discovery, Strategy and overall Qualification; failures are recorded and the batch continues."
            ),
            wraplength=1180
        ).grid(row=0,column=0,columnspan=8,sticky="w",pady=(0,8))

        ttk.Button(top,text="Load Development Set",command=self.benchmark_load_builtin).grid(row=1,column=0,padx=3,pady=3,sticky="w")
        ttk.Button(top,text="Load Blind Set 1",command=self.benchmark_load_blind).grid(row=1,column=1,padx=3,pady=3,sticky="w")
        ttk.Button(top,text="Load Fresh Blind Set 2",command=self.benchmark_load_blind2).grid(row=1,column=2,padx=3,pady=3,sticky="w")
        ttk.Button(top,text="Import CSV / XLSX",command=self.benchmark_import_file).grid(row=1,column=3,padx=3,pady=3,sticky="w")
        ttk.Button(top,text="Paste URLs",command=self.benchmark_paste_urls).grid(row=1,column=4,padx=3,pady=3,sticky="w")
        ttk.Button(top,text="Clear List",command=self.benchmark_clear).grid(row=1,column=5,padx=3,pady=3,sticky="w")

        ttk.Label(top,text="Per-site timeout (sec):").grid(row=1,column=6,padx=(18,3),sticky="e")
        self.benchmark_timeout=tk.StringVar(value="45")
        ttk.Spinbox(top,from_=15,to=300,width=6,textvariable=self.benchmark_timeout).grid(row=1,column=7,sticky="w")
        ttk.Label(top,text="Expansion sample actions:").grid(row=1,column=8,padx=(12,3),sticky="e")
        self.benchmark_actions=tk.StringVar(value="3")
        ttk.Spinbox(top,from_=0,to=20,width=5,textvariable=self.benchmark_actions).grid(row=1,column=9,sticky="w")

        controls=ttk.Frame(self.benchmark_tab,padding=(10,0,10,6)); controls.pack(fill="x")
        self.benchmark_run_button=ttk.Button(controls,text="Run Profiling Batch",style="Accent.TButton",command=self.benchmark_start)
        self.benchmark_run_button.pack(side="left")
        self.benchmark_cancel_button=ttk.Button(controls,text="Cancel After Current Site",command=self.benchmark_cancel,state="disabled")
        self.benchmark_cancel_button.pack(side="left",padx=6)
        ttk.Button(controls,text="Export Results CSV",command=self.benchmark_export_csv).pack(side="left",padx=6)
        self.benchmark_route_button=ttk.Button(
            controls,text="Use Recommended Workflow",style="Accent.TButton",
            command=self.benchmark_use_recommended_workflow
        )
        self.benchmark_route_button.pack(side="left",padx=6)
        self.benchmark_summary=tk.StringVar(value="No benchmark loaded.")
        ttk.Label(controls,textvariable=self.benchmark_summary).pack(side="left",padx=14)

        host=ttk.Frame(self.benchmark_tab); host.pack(fill="both",expand=True,padx=10,pady=(0,10))
        cols=("id","url","seed","access","discovery","strategy","qualification","engine","action","profile","records","workflow","seconds","error")
        tree=ttk.Treeview(host,columns=cols,show="headings",selectmode="extended")
        headings={
            "id":"ID","url":"Website URL","seed":"Seed / Setup",
            "access":"Access Outcome","discovery":"Discovery Outcome","strategy":"Strategy Outcome",
            "qualification":"Qualification","engine":"Recommended Engine","action":"Next Action",
            "profile":"Detected Profile","records":"Records",
            "workflow":"Source Strategy","seconds":"Time (s)","error":"Error / Notes"
        }
        widths={
            "id":60,"url":280,"seed":110,"access":160,"discovery":180,"strategy":180,
            "qualification":105,"engine":210,"action":380,"profile":170,"records":70,
            "workflow":300,"seconds":70,"error":260
        }
        for c in cols:
            tree.heading(c,text=headings[c]); tree.column(c,width=widths[c],minwidth=55,stretch=(c in {"url","engine","action","profile","workflow","error"}))
        y=ttk.Scrollbar(host,orient="vertical",command=tree.yview); x=ttk.Scrollbar(host,orient="horizontal",command=tree.xview)
        tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        tree.grid(row=0,column=0,sticky="nsew"); y.grid(row=0,column=1,sticky="ns"); x.grid(row=1,column=0,sticky="ew")
        host.rowconfigure(0,weight=1); host.columnconfigure(0,weight=1)
        self.benchmark_tree=tree

    def _benchmark_builtin_path(self):
        return Path(__file__).resolve().parent / "Tests" / "Benchmarks" / "RTUDES_Benchmark_Development_v1.csv"

    def _benchmark_blind_path(self):
        return Path(__file__).resolve().parent / "Tests" / "Benchmarks" / "RTUDES_Benchmark_Blind_v1.csv"

    def _benchmark_blind2_path(self):
        return Path(__file__).resolve().parent / "Tests" / "Benchmarks" / "RTUDES_Benchmark_Blind_v2.csv"

    def benchmark_load_builtin(self):
        path=self._benchmark_builtin_path()
        if not path.exists():
            messagebox.showerror("Benchmark Missing",f"Built-in benchmark not found:\n{path}")
            return
        self._benchmark_load_csv(path)
        self.benchmark_summary.set(f"Development Set loaded — {len(self.benchmark_sites)} website(s).")

    def benchmark_load_blind(self):
        path=self._benchmark_blind_path()
        if not path.exists():
            messagebox.showerror("Benchmark Missing",f"Built-in blind benchmark not found:\n{path}")
            return
        self._benchmark_load_csv(path)
        self.benchmark_summary.set(
            f"Blind Set 1 loaded — {len(self.benchmark_sites)} website(s). "
            "This set is already opened and is now regression-only."
        )

    def benchmark_load_blind2(self):
        path=self._benchmark_blind2_path()
        if not path.exists():
            messagebox.showerror("Benchmark Missing",f"Fresh Blind Set 2 not found:\n{path}")
            return
        self._benchmark_load_csv(path)
        self.benchmark_summary.set(
            f"Fresh Blind Set 2 loaded — {len(self.benchmark_sites)} website(s). "
            "Run the complete set once before changing SMARTCORE logic."
        )

    def benchmark_import_file(self):
        path=filedialog.askopenfilename(
            title="Import Website Benchmark",
            filetypes=[("Benchmark files","*.csv *.xlsx"),("CSV","*.csv"),("Excel","*.xlsx"),("All files","*.*")]
        )
        if not path:return
        p=Path(path)
        try:
            if p.suffix.lower()==".xlsx": self._benchmark_load_xlsx(p)
            else: self._benchmark_load_csv(p)
        except Exception as exc:
            messagebox.showerror("Import Failed",str(exc))

    @staticmethod
    def _benchmark_seed_from_setup(value):
        text=clean_text(str(value or ""))
        low=text.lower()
        if not text or low in {"no seed","none","n/a"}: return ""
        m=re.search(r"(?:seed|term)\s*:\s*(.+)$",text,re.I)
        if m:return clean_text(m.group(1))
        # Do not treat instructions such as Authentication required as literal search seeds.
        if any(x in low for x in ("authentication","required","url already","bounded analysis","no seed")): return ""
        return ""

    def _benchmark_normalize_rows(self, headers, rows):
        normalized=[]
        hmap={clean_text(str(h)).lower():i for i,h in enumerate(headers)}
        def pick(row,*names):
            for name in names:
                idx=hmap.get(name.lower())
                if idx is not None and idx < len(row): return clean_text(str(row[idx] or ""))
            return ""
        for n,row in enumerate(rows,1):
            url=pick(row,"Test URL","URL","Website URL","Website")
            if not self.valid_url(url): continue
            sid=pick(row,"ID","Test ID") or f"URL{n:03d}"
            setup=pick(row,"Setup / Seed","Seed","Search Seed","Setup")
            normalized.append({
                "id":sid,"url":url,"setup":setup,"seed":self._benchmark_seed_from_setup(setup),
                "expected_profile":pick(row,"Expected Profile","Hypothesized Profile"),
                "expected_router":pick(row,"Expected Router Decision"),
            })
        return normalized

    def _benchmark_load_csv(self, path):
        with open(path,"r",newline="",encoding="utf-8-sig") as f:
            rows=list(csv.reader(f))
        if not rows: raise ValueError("The benchmark file is empty.")
        self.benchmark_sites=self._benchmark_normalize_rows(rows[0],rows[1:])
        self._benchmark_render_sites()

    def _benchmark_load_xlsx(self, path):
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise RuntimeError("Excel import requires openpyxl. Save the benchmark as CSV or install openpyxl.") from exc
        wb=load_workbook(path,read_only=True,data_only=True)
        ws=wb["Development Set"] if "Development Set" in wb.sheetnames else wb[wb.sheetnames[0]]
        values=list(ws.iter_rows(values_only=True))
        if not values: raise ValueError("The selected worksheet is empty.")
        self.benchmark_sites=self._benchmark_normalize_rows(values[0],values[1:])
        self._benchmark_render_sites()

    @staticmethod
    def _normalize_benchmark_url(value):
        """PROFILEUX001 — normalize user-entered URL text without changing path/query."""
        raw=clean_text(str(value or "")).strip()
        if not raw:
            return {"ok":False,"url":"","normalized":False,"reason":"Blank entry"}
        candidate=raw
        normalized=False
        if "://" not in candidate:
            candidate="https://"+candidate
            normalized=True
        try:
            from urllib.parse import urlsplit, urlunsplit
            parts=urlsplit(candidate)
            if parts.scheme.lower() not in {"http","https"}:
                return {"ok":False,"url":"","normalized":normalized,"reason":"Only http:// or https:// URLs are supported"}
            if not parts.netloc:
                return {"ok":False,"url":"","normalized":normalized,"reason":"Missing website host"}
            host=parts.netloc.strip()
            if " " in host or "." not in host:
                return {"ok":False,"url":"","normalized":normalized,"reason":"Invalid website host"}
            scheme=parts.scheme.lower()
            candidate=urlunsplit((scheme,host,parts.path or "",parts.query or "",parts.fragment or ""))
            return {"ok":True,"url":candidate,"normalized":normalized,"reason":""}
        except Exception as exc:
            return {"ok":False,"url":"","normalized":normalized,"reason":f"Invalid URL: {exc}"}

    def benchmark_paste_urls(self):
        win=tk.Toplevel(self)
        win.title("Paste Website URLs")
        win.geometry("820x560")
        win.minsize(700,460)
        win.transient(self)
        win.grab_set()

        outer=ttk.Frame(win,padding=12)
        outer.pack(fill="both",expand=True)

        ttk.Label(
            outer,
            text="Paste one URL per line. Domains without http:// or https:// will be normalized to https://.",
            wraplength=760
        ).pack(fill="x")

        ttk.Label(
            outer,
            text="Optional format: ID<TAB>URL<TAB>search seed",
            foreground="#555555"
        ).pack(fill="x",pady=(2,8))

        box=tk.Text(outer,wrap="none",height=18)
        box.pack(fill="both",expand=True)

        xbar=ttk.Scrollbar(outer,orient="horizontal",command=box.xview)
        xbar.pack(fill="x")
        box.configure(xscrollcommand=xbar.set)

        status=tk.StringVar(value="Paste URLs, then choose Add URLs / Continue.")
        ttk.Label(outer,textvariable=status,wraplength=760).pack(fill="x",pady=(8,4))

        button_row=ttk.Frame(outer)
        button_row.pack(fill="x",pady=(4,0))

        def parse_lines():
            accepted=[]
            normalized_count=0
            invalid=[]
            duplicate_count=0
            existing={clean_text(str(x.get("url",""))).lower() for x in self.benchmark_sites}

            lines=box.get("1.0","end").splitlines()
            for line_no,line in enumerate(lines,1):
                line=line.strip()
                if not line:
                    continue
                parts=[p.strip() for p in line.split("\t")]
                if len(parts)==1:
                    sid=f"URL{len(self.benchmark_sites)+len(accepted)+1:03d}"
                    raw_url=parts[0]
                    seed=""
                else:
                    sid=parts[0] or f"URL{len(self.benchmark_sites)+len(accepted)+1:03d}"
                    raw_url=parts[1] if len(parts)>1 else ""
                    seed=parts[2] if len(parts)>2 else ""

                check=self._normalize_benchmark_url(raw_url)
                if not check.get("ok"):
                    invalid.append(f"Line {line_no}: {raw_url or '(blank)'} — {check.get('reason','Invalid URL')}")
                    continue

                url=check["url"]
                key=url.lower()
                if key in existing or any(x["url"].lower()==key for x in accepted):
                    duplicate_count += 1
                    continue

                if check.get("normalized"):
                    normalized_count += 1

                accepted.append({
                    "id":sid,
                    "url":url,
                    "setup":seed,
                    "seed":seed,
                    "expected_profile":"",
                    "expected_router":"",
                })

            return accepted,normalized_count,invalid,duplicate_count

        def commit():
            accepted,normalized_count,invalid,duplicate_count=parse_lines()
            if not accepted:
                summary=f"No URLs added."
                if invalid: summary+=f" {len(invalid)} invalid."
                if duplicate_count: summary+=f" {duplicate_count} duplicate(s)."
                status.set(summary)
                if invalid:
                    messagebox.showwarning(
                        "No URLs Added",
                        summary+"\n\n"+("\n".join(invalid[:8]))+
                        ("\n..." if len(invalid)>8 else ""),
                        parent=win
                    )
                return

            self.benchmark_sites.extend(accepted)
            self._benchmark_render_sites()

            parts=[f"{len(accepted)} URL(s) added"]
            if normalized_count:
                parts.append(f"{normalized_count} normalized to https://")
            if duplicate_count:
                parts.append(f"{duplicate_count} duplicate(s) skipped")
            if invalid:
                parts.append(f"{len(invalid)} invalid")

            summary=" • ".join(parts)
            self.benchmark_summary.set(
                summary+". Next: click Run Profiling Batch."
            )

            win.grab_release()
            win.destroy()

            if invalid:
                messagebox.showwarning(
                    "URLs Added with Warnings",
                    summary+"\n\nInvalid entries:\n"+("\n".join(invalid[:8]))+
                    ("\n..." if len(invalid)>8 else "")
                )
            else:
                messagebox.showinfo(
                    "URLs Ready",
                    summary+"\n\nThe URLs are queued in Profiler Lab.\nNext: click Run Profiling Batch."
                )

        def cancel():
            win.grab_release()
            win.destroy()

        ttk.Button(button_row,text="Cancel",command=cancel).pack(side="right")
        ttk.Button(
            button_row,
            text="Add URLs / Continue",
            style="Accent.TButton",
            command=commit
        ).pack(side="right",padx=(0,8))

        win.protocol("WM_DELETE_WINDOW",cancel)
        box.focus_set()

    def benchmark_clear(self):
        if self.benchmark_running:return
        self.benchmark_sites=[]; self.benchmark_results=[]
        if hasattr(self,"benchmark_tree"):
            for iid in self.benchmark_tree.get_children(): self.benchmark_tree.delete(iid)
        self.benchmark_summary.set("No benchmark loaded.")

    def _benchmark_render_sites(self):
        self.benchmark_results=[]
        for iid in self.benchmark_tree.get_children(): self.benchmark_tree.delete(iid)
        for idx,site in enumerate(self.benchmark_sites):
            self.benchmark_tree.insert("","end",iid=str(idx),values=(site["id"],site["url"],site.get("setup",""),"QUEUED","","","","","","","","","","",""))
        self.benchmark_summary.set(
            f"Loaded {len(self.benchmark_sites)} website(s). Next: click Run Profiling Batch."
        )

    def benchmark_start(self):
        if self.benchmark_running or not self.benchmark_sites:
            if not self.benchmark_sites: messagebox.showinfo("Profiler Lab","Load or paste at least one website first.")
            return
        try: timeout=max(15,min(300,int(self.benchmark_timeout.get() or "45")))
        except Exception: timeout=45
        try: actions=max(0,min(20,int(self.benchmark_actions.get() or "3")))
        except Exception: actions=3
        self.benchmark_stop.clear(); self.benchmark_running=True; self.benchmark_results=[]
        self.benchmark_run_button.configure(state="disabled"); self.benchmark_cancel_button.configure(state="normal")
        threading.Thread(target=self._benchmark_batch_worker,args=(timeout,actions),daemon=True).start()

    def benchmark_cancel(self):
        self.benchmark_stop.set(); self.benchmark_summary.set("Cancel requested — current site will finish or time out, then the batch will stop.")

    def _benchmark_worker_script(self):
        return Path(__file__).resolve().parent / "Tests" / "ProfileLab" / "profile_site_worker.py"

    def _benchmark_classify_error(self, message):
        msg=clean_text(message or "")
        low=msg.lower()
        # PROFILESEM001A — normalize browser/network codes before the semantics
        # layer so operational navigation failures are not collapsed into WORKER ERROR.
        if "err_name_not_resolved" in low or "name or service not known" in low or "dns" in low:
            return "NAVIGATION DNS"
        if "err_connection_timed_out" in low or "err_timed_out" in low or "timeout" in low or "timed out" in low:
            return "NAVIGATION TIMEOUT"
        if "err_connection_refused" in low or "connection refused" in low:
            return "NAVIGATION CONNECTION"
        if "err_network_changed" in low or "network changed" in low or "err_internet_disconnected" in low:
            return "NAVIGATION NETWORK"
        if "browser engine unavailable" in low or ("playwright" in low and "unavailable" in low):
            return "BROWSER ENGINE"
        if "unicodeencodeerror" in low or "charmap" in low:
            return "ENCODING / TRANSPORT"
        if "403" in low or "forbidden" in low or "access denied" in low or "cloudflare" in low:
            return "ACCESS / PROTECTION"
        return "WORKER ERROR"

    def _benchmark_route_recommendation(self, result):
        """PROFILEUX002 — turn profiler evidence into a concrete RTUDES next step.

        Routing is advisory and non-destructive. It never starts network activity
        automatically; the user explicitly opens the recommended workflow.
        """
        r=dict(result or {})
        access=clean_text(r.get("access_outcome") or r.get("access_state") or "")
        discovery=clean_text(r.get("discovery_outcome",""))
        qualification=clean_text(r.get("qualification",""))
        primary=clean_text(r.get("primary_source",""))
        internal=clean_text(r.get("internal_details",""))
        documents=clean_text(r.get("documents",""))
        auth=clean_text(r.get("authentication",""))

        engine="Profiler Lab"
        action="Review profiling result before continuing."
        target="benchmark"

        if access=="AUTHENTICATION REQUIRED" or auth=="Required":
            engine="Login Website"
            target="login"
            action="Open Login Website, provide authorized login/session details, then analyze the protected page."
        elif access!="ACCESSIBLE":
            engine="Profiler Lab"
            target="benchmark"
            action="Resolve or review the access/navigation condition before extraction. RTUDES will not route around access controls."
        elif discovery=="NO RECORD SOURCE IDENTIFIED" or qualification=="NEEDS INSPECTION":
            engine="Intelligent Wizard"
            target="intelligence"
            action="Open Intelligent Wizard to inspect the accessible page and identify a usable record source."
        elif discovery=="RESOURCE ONLY" or primary=="Document / Resource" or documents=="Primary":
            engine="Public Website — Resource Discovery"
            target="public"
            action="Open Public Website, analyze the URL, then use the detected document/resource workflow."
        elif primary=="Dynamic API" or discovery=="DYNAMIC SOURCE FOUND":
            engine="Public Website — Dynamic API"
            target="public"
            action="Open Public Website and Analyze Website; RTUDES should prefer the discovered structured network/API records."
        elif internal=="Likely required":
            engine="Public Website — Listing + Detail"
            target="public"
            action="Open Public Website and Analyze Website; use the listing as the record backbone and follow required internal detail relationships."
        elif primary=="HTML Table":
            engine="Public Website — Direct Table"
            target="public"
            action="Open Public Website and Analyze Website; use the direct table dataset before deeper traversal."
        elif qualification=="READY":
            engine="Public Website — Direct Structured"
            target="public"
            action="Open Public Website and Analyze Website; use the recommended structured listing source and preview before extraction."

        return {"recommended_engine":engine,"next_action":action,"route_target":target}

    def _benchmark_semantic_outcomes(self, result):
        """PROFILESEM001: separate execution, access, discovery and readiness.

        PASS no longer implies extraction readiness.  This helper derives
        orthogonal qualification outcomes from existing profiler evidence.
        """
        r=dict(result or {})
        raw_status=clean_text(r.get("status",""))
        access=clean_text(r.get("access_state",""))
        records=0
        try: records=int(r.get("records") or 0)
        except Exception: records=0
        dyn=0
        try: dyn=int(r.get("dynamic_records") or 0)
        except Exception: dyn=0
        documents=clean_text(r.get("documents",""))
        primary=clean_text(r.get("primary_source",""))
        workflow=clean_text(r.get("workflow",""))
        error=clean_text(r.get("error",""))
        low_error=error.lower()

        # Access outcome
        if raw_status=="TIMEOUT" or "navigation timeout" in low_error:
            access_outcome="NAVIGATION FAILURE / TIMEOUT"
        elif raw_status=="ERROR":
            if "navigation dns" in low_error:
                access_outcome="NAVIGATION FAILURE / DNS"
            elif "navigation connection" in low_error:
                access_outcome="NAVIGATION FAILURE / CONNECTION"
            elif "navigation network" in low_error:
                access_outcome="NAVIGATION FAILURE / NETWORK"
            elif "navigation timeout" in low_error:
                access_outcome="NAVIGATION FAILURE / TIMEOUT"
            else:
                access_outcome="WORKER / NAVIGATION FAILURE"
        elif access:
            access_outcome=access
        else:
            access_outcome="ACCESSIBLE" if raw_status=="PASS" else raw_status or "UNKNOWN"

        # Discovery outcome
        blocked_access=access_outcome not in {"ACCESSIBLE"}
        if blocked_access:
            discovery_outcome="NOT PROFILED"
        elif max(records,dyn)>0:
            if dyn>0 and primary=="Dynamic API":
                discovery_outcome="DYNAMIC SOURCE FOUND"
            else:
                discovery_outcome="RECORDS FOUND"
        elif documents and documents not in {"","None detected","Not evaluated"}:
            discovery_outcome="RESOURCE ONLY"
        else:
            discovery_outcome="NO RECORD SOURCE IDENTIFIED"

        # Strategy/readiness outcome
        if access_outcome!="ACCESSIBLE":
            strategy_outcome="NOT READY — ACCESS/NAVIGATION"
        elif discovery_outcome=="NO RECORD SOURCE IDENTIFIED":
            strategy_outcome="NEEDS INSPECTION"
        elif discovery_outcome=="RESOURCE ONLY":
            strategy_outcome="READY — RESOURCE WORKFLOW"
        elif workflow:
            strategy_outcome="READY — STRATEGY AVAILABLE"
        else:
            strategy_outcome="NEEDS STRATEGY"

        if access_outcome=="ACCESSIBLE" and strategy_outcome.startswith("READY"):
            qualification="READY"
        elif access_outcome=="ACCESSIBLE":
            qualification="NEEDS INSPECTION"
        else:
            qualification="NOT READY"

        r["execution_status"]=raw_status or "UNKNOWN"
        r["access_outcome"]=access_outcome
        r["discovery_outcome"]=discovery_outcome
        r["strategy_outcome"]=strategy_outcome
        r["qualification"]=qualification
        route=self._benchmark_route_recommendation(r)
        r.update(route)
        return r

    def _benchmark_run_one(self, site, timeout, actions):
        worker=self._benchmark_worker_script()
        cmd=[sys.executable,str(worker),"--url",site["url"],"--seed",site.get("seed","") or "","--actions",str(actions)]
        start=time.time()
        creationflags=0
        if os.name=="nt": creationflags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)
        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8",errors="replace",creationflags=creationflags)
        try:
            stdout,stderr=proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name=="nt":
                try: subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
                except Exception: proc.kill()
            else:
                proc.kill()
            try: proc.communicate(timeout=3)
            except Exception: pass
            return self._benchmark_semantic_outcomes({"status":"TIMEOUT","profile":"","confidence":"","records":"","workflow":"","seconds":round(time.time()-start,1),"error":f"NAVIGATION TIMEOUT: Exceeded {timeout}s profiling budget"})
        elapsed=round(time.time()-start,1)
        if proc.returncode!=0:
            raw=clean_text(stderr or stdout)[:500]
            category=self._benchmark_classify_error(raw)
            return self._benchmark_semantic_outcomes({"status":"ERROR","profile":"","confidence":"","records":"","workflow":"","seconds":elapsed,"error":f"{category}: {raw}"})
        lines=[x for x in stdout.splitlines() if x.strip()]
        try: result=json.loads(lines[-1]) if lines else {}
        except Exception: result={"status":"ERROR","error":clean_text(stdout)[-500:]}
        result.setdefault("status","PASS"); result["seconds"]=elapsed
        if str(result.get("status","")).upper()=="ERROR":
            raw=clean_text(result.get("error",""))
            result["error"]=f"{self._benchmark_classify_error(raw)}: {raw}"
        return self._benchmark_semantic_outcomes(result)

    def _benchmark_batch_worker(self, timeout, actions):
        total=len(self.benchmark_sites)
        completed=0
        for idx,site in enumerate(self.benchmark_sites):
            if self.benchmark_stop.is_set(): break
            self.after(0,lambda i=idx:self._benchmark_update_row(i,{"status":"RUNNING"}))
            self.after(0,lambda n=idx+1,t=total,u=site["url"]:self.benchmark_summary.set(f"Profiling {n}/{t}: {u}"))
            result=self._benchmark_run_one(site,timeout,actions)
            result={**site,**result}; self.benchmark_results.append(result); completed+=1
            self.after(0,lambda i=idx,r=result:self._benchmark_update_row(i,r))
        self.after(0,lambda:self._benchmark_finished(completed,total))

    def _benchmark_update_row(self, idx, result):
        iid=str(idx)
        if not self.benchmark_tree.exists(iid):return
        old=list(self.benchmark_tree.item(iid,"values"))
        site=self.benchmark_sites[idx]
        vals=(
            site["id"],site["url"],site.get("setup",""),
            result.get("access_outcome",old[3] if len(old)>3 else ""),
            result.get("discovery_outcome",""),result.get("strategy_outcome",""),
            result.get("qualification",""),result.get("recommended_engine",""),
            result.get("next_action",""),result.get("profile",""),
            result.get("records",""),result.get("workflow",""),
            result.get("seconds",""),result.get("error","")
        )
        self.benchmark_tree.item(iid,values=vals)
        self.benchmark_tree.see(iid)

    def _benchmark_finished(self, completed, total):
        self.benchmark_running=False
        self.benchmark_run_button.configure(state="normal")
        self.benchmark_cancel_button.configure(state="disabled")
        qcounts=Counter(r.get("qualification","") for r in self.benchmark_results)
        self.benchmark_summary.set(
            f"Batch complete: {completed}/{total}. "
            f"READY {qcounts.get('READY',0)} • "
            f"NEEDS INSPECTION {qcounts.get('NEEDS INSPECTION',0)} • "
            f"NOT READY {qcounts.get('NOT READY',0)}. "
            "Select a completed row, then click Use Recommended Workflow."
        )

    def benchmark_use_recommended_workflow(self):
        selected=list(self.benchmark_tree.selection()) if hasattr(self,"benchmark_tree") else []
        if not selected:
            messagebox.showinfo(
                "Use Recommended Workflow",
                "Select one completed website row first, then click Use Recommended Workflow."
            )
            return
        if len(selected)>1:
            messagebox.showinfo(
                "Use Recommended Workflow",
                "Select one website at a time when opening a recommended workflow."
            )
            return

        idx=int(selected[0])
        site=self.benchmark_sites[idx] if 0 <= idx < len(self.benchmark_sites) else {}
        result=None
        sid=site.get("id","")
        url=site.get("url","")
        for item in reversed(self.benchmark_results):
            if item.get("id","")==sid and item.get("url","")==url:
                result=item
                break
        if not result:
            messagebox.showinfo(
                "Use Recommended Workflow",
                "This website has not completed profiling yet. Run Profiling Batch first."
            )
            return

        # Older/in-memory result fallback.
        if not result.get("recommended_engine"):
            result.update(self._benchmark_route_recommendation(result))

        target=result.get("route_target","benchmark")
        engine=result.get("recommended_engine","Profiler Lab")
        action=result.get("next_action","Review the profiling result.")
        seed=site.get("seed","") or ""

        if target=="login":
            self.login_url.set(url)
            self.protected_url.set(url)
            self.main_notebook.select(self.login_tab)
        elif target=="intelligence":
            # Clear the prior site's transient extraction state before carrying
            # forward the new source URL for inspection.
            self.reset_current_extraction_task("public",confirm=False,clear_url=True)
            self.public_url.set(url)
            if hasattr(self,"public_search_seed"): self.public_search_seed.set(seed)
            self.main_notebook.select(self.intelligence_tab)
        elif target=="public":
            self.reset_current_extraction_task("public",confirm=False,clear_url=True)
            self.public_url.set(url)
            if hasattr(self,"public_search_seed"): self.public_search_seed.set(seed)
            self.main_notebook.select(self.public_tab)
        else:
            messagebox.showinfo(
                "Workflow Not Ready",
                f"Recommended engine: {engine}\n\n{action}"
            )
            return

        self.status_var.set(f"Recommended workflow opened: {engine}. {action}")
        messagebox.showinfo(
            "Recommended Workflow Ready",
            f"Engine: {engine}\n\nURL has been carried forward automatically.\n\nNext: {action}"
        )

    def benchmark_export_csv(self):
        if not self.benchmark_results:
            messagebox.showinfo("Profiler Lab","Run at least one website before exporting results."); return
        output_dir=Path.cwd()/"Output"/"Benchmark"; output_dir.mkdir(parents=True,exist_ok=True)
        path=filedialog.asksaveasfilename(
            title="Export Profiling Results",initialdir=str(output_dir),
            initialfile=f"profile_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            defaultextension=".csv",filetypes=[("CSV","*.csv")]
        )
        if not path:return
        headers=[
            "ID","URL","Setup / Seed","Expected Profile","Expected Router Decision",
            "Execution Status","Access Outcome","Discovery Outcome","Strategy Outcome","Qualification",
            "Recommended Engine","Next Action","Route Target",
            "Access State","Access Confidence","Access Reason",
            "Detected Profile","Confidence","Records","Recommended Workflow",
            "Primary Source","Internal Details","Documents","Pagination","Authentication",
            "Relationship Action","Listing Completeness","Dynamic Datasets","Dynamic Records",
            "HTTP Status","Final URL","Time Seconds","Error / Notes"
        ]
        with open(path,"w",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f); w.writerow(headers)
            for r in self.benchmark_results:
                w.writerow([
                    r.get("id",""),r.get("url",""),r.get("setup",""),
                    r.get("expected_profile",""),r.get("expected_router",""),
                    r.get("execution_status",r.get("status","")),
                    r.get("access_outcome",""),r.get("discovery_outcome",""),
                    r.get("strategy_outcome",""),r.get("qualification",""),
                    r.get("recommended_engine",""),r.get("next_action",""),r.get("route_target",""),
                    r.get("access_state",""),r.get("access_confidence",""),r.get("access_reason",""),
                    r.get("profile",""),r.get("confidence",""),
                    r.get("records",""),r.get("workflow",""),
                    r.get("primary_source",""),r.get("internal_details",""),
                    r.get("documents",""),r.get("pagination",""),r.get("authentication",""),
                    r.get("relationship_action",""),r.get("listing_completeness",""),
                    r.get("dynamic_datasets",""),r.get("dynamic_records",""),
                    r.get("http_status",""),r.get("final_url",""),
                    r.get("seconds",""),r.get("error","")
                ])
        messagebox.showinfo("Profiler Lab",f"Benchmark results exported:\n{path}")

    def _build_intelligence_tab(self):
        """Beginner-first website analysis based on semantic samples, not selectors."""
        top = ttk.LabelFrame(self.intelligence_tab, text="Universal Extraction Intelligence — Guided Analysis", padding=12)
        top.pack(fill="x", padx=10, pady=10)
        self.uei_url = tk.StringVar()
        ttk.Label(top, text="Website URL:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.uei_url).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="Analyze Rendered Website", style="Accent.TButton", command=self.start_uei_analysis).grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Use Public Website URL", command=self.uei_use_public_url).grid(row=0, column=3, padx=4)
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="The engine discovers repeating records, readable fields, likely detail pages, media, documents and navigation. Advanced selectors remain hidden unless needed.", wraplength=1150).grid(row=1,column=0,columnspan=4,sticky="w",pady=(8,0))

        summary = ttk.LabelFrame(self.intelligence_tab, text="Website Understanding", padding=10)
        summary.pack(fill="x", padx=10, pady=(0,8))
        self.uei_type = tk.StringVar(value="Website type: Not analyzed")
        self.uei_records = tk.StringVar(value="Records: —")
        self.uei_relationship = tk.StringVar(value="Detail relationship: —")
        self.uei_navigation = tk.StringVar(value="Navigation: —")
        self.uei_confidence = tk.StringVar(value="Analysis confidence: —")
        for i,var in enumerate((self.uei_type,self.uei_records,self.uei_relationship,self.uei_navigation,self.uei_confidence)):
            ttk.Label(summary,textvariable=var,style="Card.TLabel",padding=(8,5)).grid(row=0,column=i,sticky="ew",padx=3)
            summary.columnconfigure(i,weight=1)

        catalog = ttk.LabelFrame(self.intelligence_tab, text="Dynamic Field Catalog — Select What You Need", padding=8)
        catalog.pack(fill="both", expand=True, padx=10, pady=(0,8))
        self.uei_fields_tree = ttk.Treeview(catalog, columns=("use","type","output","samples","coverage","confidence","source"), show="headings", height=13)
        specs=(("use","Use",55),("type","Detected Data",135),("output","Suggested Column",155),("samples","Real Sample Values",400),("coverage","Coverage",80),("confidence","Confidence",85),("source","Technical Source (Advanced)",300))
        for col,title,width in specs:
            self.uei_fields_tree.heading(col,text=title); self.uei_fields_tree.column(col,width=width,minwidth=55,stretch=col in {"samples","source"})
        self.uei_fields_tree.pack(fill="both",expand=True)
        self.uei_fields_tree.bind("<Double-1>", self.uei_toggle_selected)
        tools=ttk.Frame(catalog); tools.pack(fill="x",pady=(7,0))
        ttk.Button(tools,text="Select Recommended",command=self.uei_select_recommended).pack(side="left")
        ttk.Button(tools,text="Clear Selection",command=self.uei_clear_selection).pack(side="left",padx=6)
        ttk.Button(tools,text="Preview Selected Data",style="Accent.TButton",command=self.uei_preview_selected).pack(side="left",padx=6)
        ttk.Button(tools,text="Create Reusable Profile",style="Accent.TButton",command=self.uei_create_profile).pack(side="right")
        ttk.Button(tools,text="Send to Multi-Level Crawl",command=self.uei_send_to_multilevel).pack(side="right",padx=6)

        lower=ttk.Panedwindow(self.intelligence_tab,orient="horizontal"); lower.pack(fill="both",expand=True,padx=10,pady=(0,10))
        rel=ttk.LabelFrame(lower,text="Relationships and Opportunities",padding=8)
        nav=ttk.LabelFrame(lower,text="Navigation and Interaction",padding=8)
        lower.add(rel,weight=1); lower.add(nav,weight=1)
        self.uei_relationship_tree=ttk.Treeview(rel,columns=("kind","sample","confidence"),show="headings",height=6)
        for c,t,w in (("kind","Opportunity",170),("sample","Sample / Evidence",430),("confidence","Confidence",85)):
            self.uei_relationship_tree.heading(c,text=t); self.uei_relationship_tree.column(c,width=w,minwidth=70)
        self.uei_relationship_tree.pack(fill="both",expand=True)
        self.uei_navigation_tree=ttk.Treeview(nav,columns=("kind","evidence","status"),show="headings",height=6)
        for c,t,w in (("kind","Detected Behavior",180),("evidence","Evidence",420),("status","Status",90)):
            self.uei_navigation_tree.heading(c,text=t); self.uei_navigation_tree.column(c,width=w,minwidth=70)
        self.uei_navigation_tree.pack(fill="both",expand=True)

    def uei_use_public_url(self):
        self.uei_url.set(self.public_url.get().strip() if hasattr(self,"public_url") else "")

    def start_uei_analysis(self):
        url=self.uei_url.get().strip()
        if not self.valid_url(url):
            messagebox.showerror("Invalid URL","Enter a complete http:// or https:// URL."); return
        self.status_var.set("RT-CORE is rendering and analyzing the website...")
        threading.Thread(target=self._uei_analysis_worker,args=(url,),daemon=True).start()

    def _uei_analysis_worker(self,url):
        try:
            html_text, final_url = self._render_page_html(url,wait_ms=3000,scroll=True)
            parser=DOMParser(final_url); parser.feed(html_text)
            groups=detect_repeated_groups(parser.root,final_url)
            if not groups:
                raise ValueError("No repeating records were detected. Try the Public Website DOM Inspector or a page containing listings/results.")
            # Prefer a substantial repeated group with useful fields, not a tiny navigation group.
            best=max(groups,key=lambda g:(g.get("count",0)*max(1,len(g.get("fields",[]))),g.get("count",0)))
            fields=[]
            for field in best.get("fields",[]):
                vals=[]
                for r in best.get("records",[])[:30]:
                    v=clean_text(str(r.get(field,"")))
                    if v and v not in vals: vals.append(v)
                    if len(vals)>=4: break
                present=sum(1 for r in best.get("records",[]) if clean_text(str(r.get(field,""))))
                coverage=round(100*present/max(1,best.get("count",0)))
                category,confidence=self._classify_discovered_field(field,vals)
                output=self._uei_output_name(category,field,vals)
                score=(2 if confidence=="High" else 1)+(1 if coverage>=80 else 0)
                recommended=score>=2 and category not in {"Text"}
                fields.append({"source":field,"type":category,"output":output,"samples":vals,"coverage":coverage,"confidence":confidence,"recommended":recommended})
            entity,entity_conf=self._uei_classify_entity(fields,html_text)
            links=[f for f in fields if f["type"]=="Link / Detail"]
            detail=self._uei_choose_detail_link(links)
            detail_catalog=[]; detail_mappings=[]; detail_sample_url=""
            if detail and detail.get("samples"):
                sample_url=next((clean_text(x) for x in detail.get("samples",[]) if self.valid_url(clean_text(x))), "")
                if sample_url:
                    try:
                        detail_catalog, detail_mappings, detail_sample_url = self._uei_analyze_detail_page(sample_url)
                    except Exception:
                        # Listing analysis remains usable even when one sample
                        # detail page temporarily fails to render.
                        detail_catalog=[]; detail_mappings=[]; detail_sample_url=sample_url
            navigation=self._uei_detect_navigation(parser,html_text,final_url)
            media=sum(1 for f in fields if f["type"]=="Image / Media")
            docs=sum(1 for f in fields if f["type"]=="Document")
            analysis={"url":final_url,"html":html_text,"groups":groups,"dataset":best,"fields":fields,"entity":entity,"entity_confidence":entity_conf,"detail":detail,"navigation":navigation,"media_fields":media,"document_fields":docs,"detail_catalog":detail_catalog,"detail_mappings":detail_mappings,"detail_sample_url":detail_sample_url}
            self.after(0,lambda:self._load_uei_analysis(analysis))
        except Exception as exc:
            self.after(0,lambda:self._show_error("Website Analysis Failed",str(exc)))

    @staticmethod
    def _uei_output_name(category,field,vals):
        field_text=clean_text(str(field))
        low=field_text.lower()
        if low.startswith(("semantic:","association:","meta:")):
            raw=clean_text(field_text.split(":",1)[1].split(" [",1)[0])
            if raw:
                if category=="Location":
                    # RECON025: explicit user selections receive the same generic
                    # value-aware location naming already used by the automatic
                    # schema normalizer. Clear value shape may correct a misleading
                    # source label (for example, a street address labelled State).
                    sample=next((clean_text(str(v)) for v in (vals or []) if clean_text(str(v))),"")
                    canonical=ScraperApp._canonical_location_output(field_text,sample)
                    if canonical and canonical!="Location":
                        return canonical
                return raw
        mapping={"Email":"Email","Phone":"Phone","Link / Detail":"Detail URL","Image / Media":"Image URL","Document":"Document URL","Price / Amount":"Price","Date / Time":"Date","Location":"Location","Identifier":"Identifier","Name / Title":"Name"}
        if category=="Location":
            sample=next((clean_text(str(v)) for v in (vals or []) if clean_text(str(v))),"")
            return ScraperApp._canonical_location_output(field_text,sample) or "Location"
        if category in mapping:
            return mapping[category]
        raw=field_text.split(" [",1)[0].split(" > ")[-1]
        return re.sub(r"[._-]+"," ",raw).strip().title() or "Text"

    @staticmethod
    def _uei_classify_entity(fields,html_text):
        corpus=" ".join(" ".join(f.get("samples",[])) for f in fields).lower()+" "+html_text[:30000].lower()
        candidates={
            "Business Directory":("company","broker","member","profile","directory"),
            "Property / Real Estate":("property","auction","bedroom","bathroom","parcel","real estate"),
            "Product Catalog":("price","product","sku","cart","buy now"),
            "Job Listings":("job","salary","employer","apply","position"),
            "Professional Directory":("attorney","doctor","agent","license","specialty"),
            "Government / Public Records":("county","case number","plaintiff","defendant","public record")}
        scores={k:sum(corpus.count(x) for x in words) for k,words in candidates.items()}
        entity=max(scores,key=scores.get)
        score=scores[entity]
        return (entity,"High" if score>=8 else "Medium" if score>=3 else "Low")

    @staticmethod
    def _uei_choose_detail_link(links):
        if not links: return None
        def score(f):
            text=(f["source"]+" "+" ".join(f["samples"])).lower()
            return sum(3 for k in ("profile","detail","view","read more","property","product") if k in text)+f["coverage"]/100
        return max(links,key=score)

    @staticmethod
    def _uei_detect_navigation(parser,html_text,final_url):
        low=html_text.lower(); out=[]
        link_text=[clean_text(x.get("text","")).lower() for x in parser.links]
        if any(x in {"next","next >",">","›","»"} or x.startswith("next") for x in link_text): out.append(("Next-page link","A visible Next control was found","Detected"))
        if any(x in {"load more","show more","more results","display more"} for x in link_text) or re.search(r"load[-_ ]?more|show[-_ ]?more",low): out.append(("Load more","A load/show-more control or script marker was found","Detected"))
        if "infinite" in low or "intersectionobserver" in low or "onscroll" in low: out.append(("Infinite scroll","Scroll-driven loading markers were found","Possible"))
        if re.search(r"[?&](page|p|pageindex|pagenumber)=\d+",final_url,re.I) or re.search(r"[?&](page|p|pageindex|pagenumber)=\d+",low,re.I): out.append(("URL page-number pattern","A page-number parameter was found","Detected"))
        if "show phone" in low or "show number" in low: out.append(("Hidden contact click","A reveal-phone/show-number action may be required","Possible"))
        if "cookie" in low and ("accept" in low or "consent" in low): out.append(("Cookie consent","A cookie/consent interaction may appear","Possible"))
        if not out: out.append(("Single page / unknown","No common pagination control was confidently detected","Review"))
        return out

    def _uei_analyze_detail_page(self, detail_url):
        """Render one discovered detail page and build a semantic field catalog.

        The resulting mappings are executable by Multi-Level Crawl. This makes
        the Wizard's detected listing-to-detail relationship actually collect
        fields from the linked page instead of merely exporting listing rows.
        """
        if not detail_url or not self.valid_url(detail_url):
            return [], [], ""
        html_text, final_url = self._render_page_html(detail_url, wait_ms=2200, scroll=True)
        parser = DOMParser(final_url)
        parser.feed(html_text)
        # DETAILSEM003: augment ordinary DOM features with generic semantic
        # label/value recovery before classification.  This is domain-neutral:
        # labels are taken from the rendered page itself and never from a fixed
        # target-site field dictionary.
        features = extract_universal_semantic_features(
            html_text, final_url, node_features(parser.root, final_url)
        )
        catalog = []
        seen = set()
        for field, raw_value in features.items():
            value = clean_text(str(raw_value))
            if not value or len(value) > 1800:
                continue
            low_field = field.lower()
            low_value = value.lower()
            category, confidence = self._classify_discovered_field(field, [value])

            # Add semantic types that generic DOM classification cannot infer
            # from markup alone.
            if any(k in low_field for k in ("company", "firm", "organization", "organisation", "employer")):
                category, confidence = "Company", "High"
            elif any(k in low_field for k in ("bio", "biography", "about", "description", "summary")) and len(value) >= 40:
                category, confidence = "Biography / Description", "Medium"
            elif any(d in low_value for d in ("linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com/", "youtube.com", "tiktok.com")):
                category, confidence = "Social Link", "High"
            elif category == "Link / Detail" and value.startswith(("http://", "https://")):
                # External, non-navigation links on a profile are usually the
                # subject's website rather than another detail relationship.
                if urlparse(value).netloc and urlparse(value).netloc != urlparse(final_url).netloc:
                    category, confidence = "Website", "High"

            # Skip common controls and low-information navigation labels.
            if category == "Text" and (len(value) < 3 or low_value in {
                "first", "last", "next", "previous", "message", "view profile",
                "home", "menu", "login", "join", "search"
            }):
                continue

            signature = (category, value[:180])
            if signature in seen:
                continue
            seen.add(signature)
            output_map = {
                "Email": "Email",
                "Phone": "Phone",
                "Company": "Company",
                "Location": "Address / Location",
                "Document": "Document URL",
                "Image / Media": "Detail Image",
                "Name / Title": "Detail Name",
                "Identifier": "Detail Identifier",
                "Website": "Website",
                "Social Link": "Social Link",
                "Biography / Description": "Biography",
                "Price / Amount": "Detail Price",
                "Date / Time": "Detail Date",
            }
            # A recovered semantic label is already the page's own field identity.
            # Preserve it as the selectable output name instead of collapsing
            # every labelled value containing words such as "name" into Detail Name.
            semantic_label = clean_text(field.split(":", 1)[1]).rstrip(":").strip() if field.startswith("semantic:") else ""
            if semantic_label and _semantic_label_ok(semantic_label):
                output = semantic_label
            else:
                output = output_map.get(category, self._uei_output_name(category, field, [value]))
            context_score = self._detail_candidate_score(category, field, value, confidence)
            if semantic_label and value.lower() != semantic_label.lower():
                context_score += 18
            recommended = (
                category in {
                    "Email", "Phone", "Company", "Location", "Document",
                    "Image / Media", "Name / Title", "Identifier", "Website",
                    "Social Link"
                } and context_score >= 38
            ) or (category == "Biography / Description" and context_score >= 38 and len(value) >= 60)
            # Explicit page-derived label/value pairs are legitimate record-field
            # candidates even when their value type is plain Text.  Recommendation
            # remains subject to the existing ownership/schema gates downstream.
            if semantic_label and value.lower() != semantic_label.lower() and context_score >= 34:
                recommended = True
            catalog.append({
                "source": field,
                "type": category,
                "output": output,
                "sample": value,
                "confidence": confidence,
                "context_score": context_score,
                "recommended": recommended,
            })

        # Prefer actionable contact/business/media fields. Keep only one field
        # per output name unless the semantic category legitimately repeats.
        priority = {
            "Email": 100, "Phone": 95, "Company": 90, "Website": 88,
            "Social Link": 86, "Location": 82, "Document": 80,
            "Image / Media": 78, "Name / Title": 70,
            "Biography / Description": 65, "Identifier": 60,
        }
        catalog.sort(key=lambda x: (x["recommended"], x.get("context_score", 0), priority.get(x["type"], 0), x["confidence"] == "High"), reverse=True)
        mappings = []
        output_counts = defaultdict(int)
        singleton_types = {"Email", "Phone", "Company", "Location", "Website", "Name / Title", "Biography / Description", "Identifier"}
        chosen_singletons = set()
        for item in catalog:
            if not item["recommended"]:
                continue
            if item["type"] in singleton_types and item["type"] in chosen_singletons:
                continue
            base_output = item["output"]
            output_counts[base_output] += 1
            output = base_output if output_counts[base_output] == 1 else f"{base_output} {output_counts[base_output]}"
            transform = "Email / mailto Clean" if item["type"] == "Email" else "Text Clean"
            mappings.append({"source": item["source"], "output": output, "transform": transform, "semantic_type": item["type"], "sample": item.get("sample", ""), "context_score": item.get("context_score", 0)})
            if item["type"] in singleton_types:
                chosen_singletons.add(item["type"])
            if len(mappings) >= 15:
                break
        return catalog, mappings, final_url

    def _load_uei_analysis(self,analysis):
        self.uei_analysis=analysis; self.uei_field_rows=analysis["fields"]
        self.uei_selected_fields={i for i,f in enumerate(self.uei_field_rows) if f["recommended"]}
        self.clear_tree(self.uei_fields_tree)
        for i,f in enumerate(self.uei_field_rows):
            self.uei_fields_tree.insert("","end",iid=str(i),values=("✓" if i in self.uei_selected_fields else "",f["type"],f["output"]," | ".join(f["samples"])[:700],f'{f["coverage"]}%',f["confidence"],f["source"]))
        self.clear_tree(self.uei_relationship_tree)
        detail=analysis.get("detail")
        if detail:
            self.uei_relationship_tree.insert("","end",values=("Listing → Detail page"," | ".join(detail["samples"])[:700],detail["confidence"]))
            detail_count=len(analysis.get("detail_mappings",[]))
            if detail_count:
                sample_names=" | ".join(m.get("output","") for m in analysis.get("detail_mappings",[])[:8])
                self.uei_relationship_tree.insert("","end",values=("Detail-page fields",f"{detail_count} recommended field(s): {sample_names}","High"))
        if analysis["media_fields"]: self.uei_relationship_tree.insert("","end",values=("Images / media",f'{analysis["media_fields"]} media field(s) discovered',"High"))
        if analysis["document_fields"]: self.uei_relationship_tree.insert("","end",values=("Documents",f'{analysis["document_fields"]} document field(s) discovered',"High"))
        self.clear_tree(self.uei_navigation_tree)
        for row in analysis["navigation"]: self.uei_navigation_tree.insert("","end",values=row)
        self.uei_type.set(f'Website type: {analysis["entity"]}')
        self.uei_records.set(f'Records: {analysis["dataset"].get("count",0)}')
        self.uei_relationship.set(f'Detail relationship: {"Detected" if detail else "Not confirmed"}')
        self.uei_navigation.set(f'Navigation: {analysis["navigation"][0][0]}')
        high=sum(1 for f in analysis["fields"] if f["confidence"]=="High")
        overall=min(99,55+min(30,analysis["dataset"].get("count",0)//2)+min(14,high*2))
        self.uei_confidence.set(f'Analysis confidence: {overall}%')
        self.status_var.set(f'Website analysis complete — {analysis["dataset"].get("count",0)} listing records, {len(analysis["fields"])} listing fields, and {len(analysis.get("detail_mappings",[]))} recommended detail fields found.')

    def uei_toggle_selected(self,_event=None):
        sel=self.uei_fields_tree.selection()
        if not sel:return
        i=int(sel[0]);
        if i in self.uei_selected_fields:self.uei_selected_fields.remove(i)
        else:self.uei_selected_fields.add(i)
        vals=list(self.uei_fields_tree.item(str(i),"values")); vals[0]="✓" if i in self.uei_selected_fields else ""; self.uei_fields_tree.item(str(i),values=vals)

    def uei_select_recommended(self):
        self.uei_selected_fields={i for i,f in enumerate(self.uei_field_rows) if f["recommended"]}
        for i in range(len(self.uei_field_rows)):
            vals=list(self.uei_fields_tree.item(str(i),"values")); vals[0]="✓" if i in self.uei_selected_fields else ""; self.uei_fields_tree.item(str(i),values=vals)

    def uei_clear_selection(self):
        self.uei_selected_fields.clear()
        for i in range(len(self.uei_field_rows)):
            vals=list(self.uei_fields_tree.item(str(i),"values")); vals[0]=""; self.uei_fields_tree.item(str(i),values=vals)

    def uei_preview_selected(self):
        if not self.uei_analysis or not self.uei_selected_fields:
            messagebox.showinfo("Nothing Selected","Analyze a website and select at least one field."); return
        dataset=self.uei_analysis["dataset"]
        chosen=[self.uei_field_rows[i] for i in sorted(self.uei_selected_fields)]
        win=tk.Toplevel(self); win.title("Selected Data Preview"); win.geometry("1150x520")
        tree=self.make_tree(win); headers=[f["output"] for f in chosen]; self.configure_tree(tree,headers)
        for record in dataset.get("records",[])[:25]: tree.insert("","end",values=[record.get(f["source"],"") for f in chosen])

    def uei_create_profile(self):
        if not self.uei_analysis or not self.uei_selected_fields:
            messagebox.showinfo("Nothing Selected","Analyze a website and select fields first."); return
        name=simpledialog.askstring("Create Reusable Profile","Profile name:",initialvalue=self.uei_analysis.get("entity","Website")+" Extraction",parent=self)
        name=clean_text(name or "")
        if not name:return
        chosen=[self.uei_field_rows[i] for i in sorted(self.uei_selected_fields)]
        detail=self.uei_analysis.get("detail")
        payload={"product":"RAM-Tech Universal Data Extraction Studio","profile_name":name,"version":"1.5.2","profile_type":"visual_extraction","source_url":self.uei_analysis["url"],"dataset_name":self.uei_analysis["dataset"]["name"],"detected_record_count":self.uei_analysis["dataset"]["count"],"source_fields":self.uei_analysis["dataset"]["fields"],"mappings":[{"source":f["source"],"output":f["output"],"transform":"Email / mailto Clean" if f["type"]=="Email" else "Text Clean"} for f in chosen],"detail_link_field":detail["source"] if detail else "","detail_mappings":self.uei_analysis.get("detail_mappings",[]),"uei":{"entity":self.uei_analysis["entity"],"navigation":self.uei_analysis["navigation"],"detail_sample_url":self.uei_analysis.get("detail_sample_url",""),"created_by":"Intelligent Wizard"},"created_at":datetime.now().isoformat(timespec="seconds")}
        safe=re.sub(r"[^A-Za-z0-9._-]+","_",name).strip("_") or "uei_profile"
        self.builder_profiles_dir.mkdir(parents=True,exist_ok=True); path=self.builder_profiles_dir/f"{safe}.json"; path.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding="utf-8")
        self.refresh_custom_profiles(); self.multilevel_profile.set(name); self._on_multilevel_profile_changed()
        messagebox.showinfo("Reusable Profile Created",f"'{name}' is now available in Multi-Level Crawl.\n\n{path}")

    @staticmethod
    def _uei_detail_pattern(detail):
        """Return a stable path fragment from detected detail-page sample URLs."""
        if not detail:
            return ""
        samples = [clean_text(x) for x in detail.get("samples", []) if clean_text(x)]
        paths = []
        for sample in samples:
            try:
                path = urlparse(sample).path
            except Exception:
                path = ""
            parts = [x for x in path.split("/") if x]
            if parts:
                paths.append(parts)
        if not paths:
            return ""
        common = []
        for values in zip(*paths):
            if len(set(values)) == 1:
                common.append(values[0])
            else:
                break
        # Keep the relationship root, while excluding the record slug.
        if common:
            return "/" + "/".join(common) + "/"
        first = paths[0]
        return "/" + "/".join(first[:-1] or first[:1]) + "/"

    def uei_send_to_multilevel(self):
        if not self.uei_analysis:
            messagebox.showinfo("No Analysis","Analyze a website first."); return

        analysis = self.uei_analysis
        detail = analysis.get("detail")
        chosen = [self.uei_field_rows[i] for i in sorted(self.uei_selected_fields)]
        if not chosen:
            chosen = [f for f in self.uei_field_rows if f.get("recommended")]

        # Create an executable analysis-session profile so the crawl receives
        # the selected fields and detected relationship, not stale preset state.
        session_name = "UEI Current Analysis"
        payload = {
            "product": "RAM-Tech Universal Data Extraction Studio",
            "profile_name": session_name,
            "version": "1.5.2",
            "profile_type": "visual_extraction",
            "source_url": analysis["url"],
            "dataset_name": analysis["dataset"]["name"],
            "detected_record_count": analysis["dataset"]["count"],
            "source_fields": analysis["dataset"]["fields"],
            "mappings": [
                {"source": f["source"], "output": f["output"],
                 "transform": "Email / mailto Clean" if f["type"] == "Email" else "Text Clean"}
                for f in chosen
            ],
            "detail_link_field": detail["source"] if detail else "",
            "detail_mappings": analysis.get("detail_mappings", []),
            "uei": {
                "entity": analysis["entity"],
                "navigation": analysis["navigation"],
                "detail_pattern": self._uei_detail_pattern(detail),
                "created_by": "Intelligent Wizard session transfer"
            },
            "created_at": datetime.now().isoformat(timespec="seconds")
        }
        self.builder_profiles_dir.mkdir(parents=True, exist_ok=True)
        session_path = self.builder_profiles_dir / "UEI_Current_Analysis.json"
        session_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        self.refresh_custom_profiles()
        self.multilevel_profile.set(session_name)
        self._on_multilevel_profile_changed()
        self.multilevel_url.set(analysis["url"])
        self.detail_link_pattern.set(self._uei_detail_pattern(detail))
        self.main_notebook.select(self.multilevel_tab)
        self.multilevel_progress.set(
            f"Ready — {analysis['entity']} analysis transferred with "
            f"{len(chosen)} listing field(s), {len(analysis.get('detail_mappings', []))} detail field(s), and the detected detail relationship."
        )
        self.status_var.set("Complete UEI analysis session sent to Multi-Level Crawl.")

    def _build_workspace_tab(self):
        top = ttk.LabelFrame(self.workspace_tab, text="Project Manager", padding=12)
        top.pack(fill="x", padx=10, pady=10)
        self.project_name = tk.StringVar(value="")
        ttk.Label(top, text="Project name:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.project_name, width=42).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Save Current Crawl", style="Accent.TButton", command=self.save_current_project).grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Load Selected", command=self.load_selected_project).grid(row=0, column=3, padx=4)
        ttk.Button(top, text="Delete Selected", style="Danger.TButton", command=self.delete_selected_project).grid(row=0, column=4, padx=4)
        ttk.Button(top, text="Settings", command=self.show_settings).grid(row=0, column=5, padx=4)
        ttk.Button(top, text="About", command=self.show_about).grid(row=0, column=6, padx=4)
        top.columnconfigure(1, weight=1)

        self.projects_tree = ttk.Treeview(self.workspace_tab, columns=("name","profile","url","updated"), show="headings")
        for col, title, width in (("name","Project",220),("profile","Profile",160),("url","Listing URL",560),("updated","Updated",160)):
            self.projects_tree.heading(col, text=title)
            self.projects_tree.column(col, width=width, anchor="w")
        self.projects_tree.pack(fill="both", expand=True, padx=10, pady=(0,10))
        self.projects_tree.bind("<Double-1>", lambda _e: self.load_selected_project())
        self._load_projects_file()
        self._refresh_projects_tree()

    def _load_projects_file(self):
        try:
            if self.projects_file.exists():
                payload = json.loads(self.projects_file.read_text(encoding="utf-8"))
                self.saved_projects = payload if isinstance(payload, list) else []
        except Exception:
            self.saved_projects = []

    def _write_projects_file(self):
        self.projects_file.parent.mkdir(parents=True, exist_ok=True)
        self.projects_file.write_text(json.dumps(self.saved_projects, indent=2), encoding="utf-8")

    def _refresh_projects_tree(self):
        if not hasattr(self, "projects_tree"):
            return
        self.projects_tree.delete(*self.projects_tree.get_children())
        for idx, item in enumerate(self.saved_projects):
            self.projects_tree.insert("", "end", iid=str(idx), values=(item.get("name",""), item.get("profile",""), item.get("listing_url",""), item.get("updated_at","")))

    def save_current_project(self):
        if not hasattr(self, "multilevel_url"):
            messagebox.showinfo("Project Manager", "Open the Multi-Level Crawl tab first.")
            return
        name = clean_text(self.project_name.get()) or "Untitled Project"

        # Resolve the active website/profile instead of blindly saving the startup
        # Multi-Level defaults. This matters when a user configures a website in
        # Public Website / Extraction Builder and then switches to Projects.
        self.refresh_custom_profiles()
        public_url = self.public_url.get().strip() if hasattr(self, "public_url") else ""
        selected_profile = self.multilevel_profile.get()
        selected_url = self.multilevel_url.get().strip()
        selected_pattern = self.detail_link_pattern.get()

        # Prefer a custom visual profile whose source URL matches the currently
        # inspected public website. If several profiles match, use the most
        # recently created/saved profile.
        matching = []
        if public_url:
            for profile_name, payload in self.custom_profiles.items():
                if clean_text(payload.get("source_url")) == public_url:
                    matching.append((payload.get("created_at", ""), profile_name, payload))
        if matching:
            _created, selected_profile, payload = sorted(matching)[-1]
            selected_url = public_url
            selected_pattern = ""  # custom profiles follow their saved href field
            self.multilevel_profile.set(selected_profile)
            self.multilevel_url.set(selected_url)
            self.detail_link_pattern.set("")
            self._on_multilevel_profile_changed()
        elif public_url and selected_url in ("", "https://www.ohiosheriffsales.com/upcoming_auctions"):
            # A newly inspected website may not have a saved visual profile yet.
            # Preserve the actual website rather than the Ohio starter value.
            selected_profile = "Universal Auto-Detect"
            selected_url = public_url
            selected_pattern = ""
            self.multilevel_profile.set(selected_profile)
            self.multilevel_url.set(selected_url)
            self.detail_link_pattern.set("")

        record = {
            "name": name,
            "profile": selected_profile,
            "listing_url": selected_url,
            "detail_link_pattern": selected_pattern,
            "delay": self.multilevel_delay.get(),
            "max_pages": self.multilevel_max.get(),
            "download_images": bool(self.download_images.get()),
            "download_documents": bool(self.download_documents.get()),
            "resume": bool(self.resume_crawl.get()),
            "image_output_dir": self.image_output_dir.get(),
            "document_output_dir": self.document_output_dir.get(),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "profile_payload": self.custom_profiles.get(selected_profile)
        }
        existing = next((i for i,p in enumerate(self.saved_projects) if p.get("name","").lower()==name.lower()), None)
        if existing is None: self.saved_projects.append(record)
        else: self.saved_projects[existing] = record
        self._write_projects_file(); self._refresh_projects_tree()
        messagebox.showinfo("Project Saved", f"Saved project: {name}")

    def load_selected_project(self):
        selected = self.projects_tree.selection() if hasattr(self, "projects_tree") else ()
        if not selected:
            messagebox.showinfo("Project Manager", "Select a project first.")
            return
        item = self.saved_projects[int(selected[0])]
        embedded = item.get("profile_payload")
        if isinstance(embedded, dict) and embedded.get("profile_type") == "visual_extraction":
            profile_name = clean_text(embedded.get("profile_name")) or clean_text(item.get("profile"))
            if profile_name:
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", profile_name).strip("_") or "custom_profile"
                self.builder_profiles_dir.mkdir(parents=True, exist_ok=True)
                (self.builder_profiles_dir / f"{safe}.json").write_text(json.dumps(embedded, indent=2, ensure_ascii=False), encoding="utf-8")
        self.refresh_custom_profiles()
        self.project_name.set(item.get("name",""))
        self.multilevel_profile.set(item.get("profile","Universal Auto-Detect"))
        self.multilevel_url.set(item.get("listing_url",""))
        self.detail_link_pattern.set(item.get("detail_link_pattern",""))
        self.multilevel_delay.set(str(item.get("delay","1.0")))
        self.multilevel_max.set(str(item.get("max_pages","0")))
        self.download_images.set(bool(item.get("download_images",True)))
        self.download_documents.set(bool(item.get("download_documents",True)))
        self.resume_crawl.set(bool(item.get("resume",True)))
        self.image_output_dir.set(item.get("image_output_dir", str(Path.cwd()/"Output"/"Images")))
        self.document_output_dir.set(item.get("document_output_dir", str(Path.cwd()/"Output"/"Documents")))
        self._on_multilevel_profile_changed()
        self.main_notebook.select(self.multilevel_tab)
        self.status_var.set(f"Loaded project: {item.get('name','')}")

    def delete_selected_project(self):
        selected = self.projects_tree.selection() if hasattr(self, "projects_tree") else ()
        if not selected: return
        idx=int(selected[0]); name=self.saved_projects[idx].get("name","")
        if messagebox.askyesno("Delete Project", f"Delete saved project '{name}'?"):
            self.saved_projects.pop(idx); self._write_projects_file(); self._refresh_projects_tree()

    def show_settings(self):
        win=tk.Toplevel(self); win.title("Settings"); win.transient(self); win.grab_set(); win.resizable(False,False)
        frame=ttk.Frame(win,padding=16); frame.pack(fill="both",expand=True)
        default_delay=tk.StringVar(value=self.multilevel_delay.get() if hasattr(self,"multilevel_delay") else "1.0")
        default_images=tk.StringVar(value=self.image_output_dir.get() if hasattr(self,"image_output_dir") else str(Path.cwd()/"Output"/"Images"))
        default_docs=tk.StringVar(value=self.document_output_dir.get() if hasattr(self,"document_output_dir") else str(Path.cwd()/"Output"/"Documents"))
        for r,(label,var) in enumerate((("Default delay (seconds):",default_delay),("Images folder:",default_images),("Documents folder:",default_docs))):
            ttk.Label(frame,text=label).grid(row=r,column=0,sticky="w",pady=5); ttk.Entry(frame,textvariable=var,width=62).grid(row=r,column=1,padx=8,pady=5)
        def save():
            try: float(default_delay.get())
            except ValueError: messagebox.showerror("Invalid Setting","Delay must be numeric.",parent=win); return
            payload={"default_delay":default_delay.get(),"image_output_dir":default_images.get(),"document_output_dir":default_docs.get()}
            self.settings_file.parent.mkdir(parents=True,exist_ok=True); self.settings_file.write_text(json.dumps(payload,indent=2),encoding="utf-8")
            self.multilevel_delay.set(default_delay.get()); self.image_output_dir.set(default_images.get()); self.document_output_dir.set(default_docs.get()); win.destroy()
        ttk.Button(frame,text="Save",command=save).grid(row=3,column=1,sticky="e",pady=(12,0))

    def show_about(self):
        messagebox.showinfo("About RAM-Tech", "RAM-Tech Universal Data Extraction Studio\nVersion 1.8.8.1\nBuild RTUDES-1.8.8.8-RECON045\n\nProfile: Universal Auto-Detect\n\nApplication Development • Automation • Integration • Business Intelligence\n© 2026 RAM-Tech")

    def _build_public_tab(self):
        top = ttk.Frame(self.public_tab, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Website URL:").pack(side="left")
        self.public_url = tk.StringVar()
        ttk.Entry(top, textvariable=self.public_url).pack(
            side="left", fill="x", expand=True, padx=8
        )
        ttk.Button(
            top, text="Analyze Website",
            style="Accent.TButton",
            command=lambda: self.start_rendered_fetch(self.public_url.get().strip(), "public")
        ).pack(side="left", padx=(6,0))
        ttk.Button(
            top, text="New Task / Clear Session",
            command=lambda: self.reset_current_extraction_task("public")
        ).pack(side="left", padx=(6,0))
        self.public_advanced_button = ttk.Button(
            top, text="Advanced Tools", command=self.toggle_public_advanced_tools
        )
        self.public_advanced_button.pack(side="left", padx=(6,0))

        expand_bar = ttk.Frame(self.public_tab, padding=(10,0,10,6))
        expand_bar.pack(fill="x")
        self.public_expand_results = tk.BooleanVar(value=True)
        self.public_expand_limit = tk.StringVar(value="100")
        ttk.Checkbutton(
            expand_bar,
            text="Expand accessible results before analysis",
            variable=self.public_expand_results
        ).pack(side="left")
        ttk.Label(
            expand_bar,
            text="Max expansion actions:"
        ).pack(side="left", padx=(14,4))
        ttk.Spinbox(
            expand_bar,
            from_=1, to=500, width=6,
            textvariable=self.public_expand_limit
        ).pack(side="left")
        ttk.Label(
            expand_bar,
            text="(Load More / infinite scroll / Next-page traversal)"
        ).pack(side="left", padx=(8,0))

        # v1.6.2.0 — optional generic seed for search-driven directories.
        # This intentionally avoids site-specific selectors: the browser engine
        # scores visible search/location inputs and submits the best candidate
        # before the existing result-expansion workflow runs.
        search_bar = ttk.Frame(self.public_tab, padding=(10,0,10,6))
        search_bar.pack(fill="x")
        self.public_search_seed = tk.StringVar()
        ttk.Label(search_bar, text="Search / location seed (optional):").pack(side="left")
        ttk.Entry(search_bar, textvariable=self.public_search_seed, width=28).pack(side="left", padx=(6,8))
        ttk.Label(
            search_bar,
            text="Use for store finders or directories that load records only after a search (for example a city or postcode)."
        ).pack(side="left", fill="x", expand=True)

        # SMARTCORE001 — Website Profiler summary.  The existing extraction
        # engines remain authoritative; this panel explains what RTUDES detected
        # and which existing workflow it recommends.
        profiler = ttk.LabelFrame(self.public_tab, text="Smart Website Profile", padding=(10,6))
        profiler.pack(fill="x", padx=10, pady=(0,6))
        self.public_profile_summary = tk.StringVar(
            value="Enter a URL and click Analyze Website. RTUDES will profile the source and recommend a workflow."
        )
        ttk.Label(
            profiler, textvariable=self.public_profile_summary,
            wraplength=1180, justify="left"
        ).pack(fill="x")

        semantic = ttk.LabelFrame(self.public_tab, text="Semantic Extraction Plan", padding=(10,6))
        semantic.pack(fill="x", padx=10, pady=(0,6))
        semantic_row=ttk.Frame(semantic)
        semantic_row.pack(fill="x")
        self.public_semantic_plan_summary=tk.StringVar(
            value="Analyze a website. RTUDES will identify the likely page purpose, primary records, useful fields, and relationship roles."
        )
        ttk.Label(
            semantic_row,textvariable=self.public_semantic_plan_summary,
            wraplength=1020,justify="left"
        ).pack(side="left",fill="x",expand=True)
        self.public_apply_semantic_button=ttk.Button(
            semantic_row,text="Apply Recommended Plan",style="Accent.TButton",
            command=lambda:self.apply_semantic_extraction_plan("public")
        )
        self.public_apply_semantic_button.pack(side="right",padx=(10,0))

        interpretation = ttk.LabelFrame(
            self.public_tab,
            text="Website Interpretation — Observation Only",
            padding=(10,6)
        )
        interpretation.pack(fill="x", padx=10, pady=(0,6))
        self.public_interpretation_summary=tk.StringVar(
            value="Analyze a website. RTUDES will describe delivery, structure, controls, relationships and resources without changing extraction."
        )
        ttk.Label(
            interpretation,
            textvariable=self.public_interpretation_summary,
            wraplength=1180,
            justify="left"
        ).pack(fill="x")

        self.public_results = ttk.Notebook(self.public_tab)
        self.public_results.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._build_html_result_tabs(self.public_results)

    @staticmethod
    def _detect_access_state(http_status, final_url, html_text="", title=""):
        """SMARTCORE004: classify access state before profiling page architecture.

        This does not bypass or defeat access controls.  It identifies whether
        the browser reached normal target content, authentication, verification,
        restriction, or a blocking/interstitial page so architecture confidence
        is not assigned to the wrong page.
        """
        try:
            status=int(http_status or 0)
        except Exception:
            status=0
        url=(final_url or "").strip()
        low_url=url.lower()
        sample=(html_text or "")
        # Limit inspection cost while retaining title/head/body signals.
        low=(str(title or "")+" "+sample[:220000]).lower()

        def hit(*tokens):
            return any(t in low or t in low_url for t in tokens)

        # Explicit protocol-level restrictions have highest precedence.
        if status in (401,):
            return {
                "state":"AUTHENTICATION REQUIRED",
                "accessible":False,
                "confidence":"High",
                "reason":f"HTTP {status} requires authentication.",
                "recommended_action":"Use Login Website with authorized credentials/session."
            }
        if status in (403, 407, 451):
            return {
                "state":"ACCESS RESTRICTED",
                "accessible":False,
                "confidence":"High",
                "reason":f"HTTP {status} indicates restricted access.",
                "recommended_action":"Report the restriction; do not treat the response as target data."
            }

        # Strong human-verification/challenge signals, including status-200 pages.
        verification_url_tokens=(
            "/verify_human","verify-human","/captcha","/challenge","/verify/traffic",
            "challenge-platform","cf-chl","recaptcha","hcaptcha"
        )
        verification_phrase_tokens=(
            "verify you are human","verify that you are human","human verification",
            "checking your browser","complete the security check",
            "unusual traffic","automated queries","are you a robot",
            "press and hold","verify to continue"
        )
        verification_markup_tokens=(
            "g-recaptcha","h-captcha","cf-challenge","cf-turnstile",
            "challenge-form","captcha-container","captcha__"
        )
        verification_url_hit=any(t in low_url for t in verification_url_tokens)
        phrase_hits=sum(1 for t in verification_phrase_tokens if t in low)
        markup_hit=any(t in low for t in verification_markup_tokens)
        # A bare mention of "captcha", recaptcha script, or security text in a
        # normal page is not enough. Require a challenge URL, a strong phrase,
        # or challenge markup corroborated by sparse/interstitial content.
        visible_text=re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>"," ",sample[:160000],flags=re.I|re.S)
        visible_len=len(re.sub(r"\s+"," ",visible_text).strip())
        challenge_sparse=visible_len < 5000
        if verification_url_hit or phrase_hits >= 1 or (markup_hit and challenge_sparse):
            return {
                "state":"HUMAN VERIFICATION",
                "accessible":False,
                "confidence":"High" if verification_url_hit or phrase_hits else "Medium",
                "reason":"The browser reached a corroborated human-verification or traffic-challenge page instead of normal target content.",
                "recommended_action":"Report the verification requirement; do not bypass CAPTCHA or challenge controls."
            }

        # Blocking/interstitial pages can return HTTP 200.
        blocked_url_tokens=("/blocked","access-denied","access_denied","denied.html","blockpage")
        blocked_text_tokens=(
            "access denied","request blocked","you have been blocked","temporarily blocked",
            "sorry, you have been blocked","this request has been blocked","permission denied",
            "the requested url was rejected","your request has been denied"
        )
        if any(t in low_url for t in blocked_url_tokens) or any(t in low for t in blocked_text_tokens):
            return {
                "state":"BLOCKED / INTERSTITIAL",
                "accessible":False,
                "confidence":"High",
                "reason":"The returned page appears to be a blocking/interstitial page rather than the requested content.",
                "recommended_action":"Report the blocked state and preserve the original URL for later authorized/browser-assisted review."
            }

        # Detect an actual login wall, not merely a site that contains a Login link.
        has_password=bool(re.search(r'<input[^>]+type=["\\\']password["\\\']', sample, re.I))
        login_context=hit(
            "sign in to continue","login to continue","log in to continue",
            "members only","member login","authentication required",
            "please sign in","please log in","account login"
        )
        sparse=len(re.sub(r"<[^>]+>"," ",sample[:120000]).strip()) < 3500
        if has_password and (login_context or sparse):
            return {
                "state":"AUTHENTICATION REQUIRED",
                "accessible":False,
                "confidence":"Medium" if not login_context else "High",
                "reason":"A login form appears to gate the requested content.",
                "recommended_action":"Route to Login Website and use only authorized credentials/session."
            }

        # Generic 4xx/5xx responses should not be profiled as normal architecture.
        if status >= 400:
            return {
                "state":"ACCESS / HTTP ERROR",
                "accessible":False,
                "confidence":"High",
                "reason":f"HTTP {status} returned instead of normal target content.",
                "recommended_action":"Record the HTTP failure and retry only through normal permitted access."
            }

        return {
            "state":"ACCESSIBLE",
            "accessible":True,
            "confidence":"High",
            "reason":"Normal target content appears accessible.",
            "recommended_action":"Continue source profiling."
        }

    def _smart_profile_website(self, final_url, parser, groups, tables, expansion_summary=None, access_state=None):
        """SMARTCORE002 evidence-scored source profiler.

        Primary architecture is selected from competing evidence instead of
        letting any single discovered relationship/resource override the page.
        Existing extraction engines remain untouched; this method recommends
        the least-complex reliable route.
        """
        access_state = access_state or {"state":"ACCESSIBLE","accessible":True,"confidence":"High","reason":"","recommended_action":""}
        if not access_state.get("accessible", True):
            state=access_state.get("state","ACCESS RESTRICTED")
            return {
                "profile":f"Access State — {state}",
                "confidence":access_state.get("confidence","High"),
                "signals":[access_state.get("reason","Target content was not accessible.")],
                "recommendations":[access_state.get("recommended_action","Review access state before extraction.")],
                "counts":{"datasets":0,"tables":0,"links":0,"dynamic_datasets":0,"dynamic_records":0,"expansion_actions":0,"pages":1},
                "best_records":0,
                "listing_completeness":0,
                "relationship_action":"",
                "source_strategy":{
                    "primary_records":"Unavailable",
                    "primary_record_count":0,
                    "internal_details":"Not evaluated",
                    "relationship_default":"",
                    "documents":"Not evaluated",
                    "pagination":"Not evaluated",
                    "authentication":"Required" if state=="AUTHENTICATION REQUIRED" else "Not evaluated",
                    "dynamic_api":"Not evaluated",
                    "recommended_plan":access_state.get("recommended_action","Review access state"),
                },
                "recommended_plan":access_state.get("recommended_action","Review access state"),
                "scores":{},
                "access_state":access_state,
                "url":final_url,
            }

        expansion_summary = expansion_summary or {}
        groups = list(groups or [])
        links = list(getattr(self, "links", []) or [])
        dyn = dict(getattr(self, "dynamic_endpoint_summary", {}) or {})

        def _ival(v, default=0):
            try: return int(v or default)
            except Exception: return default

        counts={
            "datasets":len(groups),
            "tables":len(tables or []),
            "links":len(links),
            "dynamic_datasets":_ival(dyn.get("datasets")),
            "dynamic_records":_ival(dyn.get("records")),
            "expansion_actions":_ival(expansion_summary.get("actions")),
            "pages":max(1,_ival(expansion_summary.get("pages"),1)),
        }

        ranked=sorted(groups, key=lambda g:_ival(g.get("count")), reverse=True)
        best=ranked[0] if ranked else {}
        best_count=_ival(best.get("count"))
        best_label=(str(best.get("discovery_label",""))+" "+str(best.get("dataset_type",""))).lower()

        rel_groups=[]
        doc_groups=[]
        structured_groups=[]
        for g in groups:
            label=(str(g.get("discovery_label",""))+" "+str(g.get("dataset_type",""))).lower()
            dtype=str(g.get("dataset_type","")).lower()
            if dtype=="relationship" or "relationship" in label:
                rel_groups.append(g)
            if any(t in label for t in ("document","download","pdf","resource")):
                doc_groups.append(g)
            if dtype not in ("relationship","resource","document") and not any(t in label for t in ("document","download","pdf","resource")):
                structured_groups.append(g)

        qualified_structured=[]
        rejected_structured=[]
        for g in structured_groups:
            ready,_nav,note=_entity_dataset_ready(g,final_url)
            if ready:
                qualified_structured.append(g)
            else:
                rejected_structured.append((g,note))

        structured_best=max((_ival(g.get("count")) for g in qualified_structured), default=0)
        observed_structured_best=max((_ival(g.get("count")) for g in structured_groups), default=0)
        relationship_best=max((_ival(g.get("count")) for g in rel_groups), default=0)
        document_best=max((_ival(g.get("count")) for g in doc_groups), default=0)

        # Evidence scores.  Incidental resources/links are supporting evidence,
        # not automatic primary architecture decisions.
        scores={
            "Dynamic / API-backed Website":0,
            "Document / Resource-driven Website":0,
            "Listing → Detail Website":0,
            "HTML Table Website":0,
            "Repeated / Structured Website":0,
        }
        qualified_dynamic=[
            g for g in qualified_structured
            if str(g.get("dataset_type","")).lower()=="dynamic_api"
        ]
        qualified_dynamic_best=max((_ival(g.get("count")) for g in qualified_dynamic),default=0)
        if counts["dynamic_datasets"]:
            scores["Dynamic / API-backed Website"] += 3
            if qualified_dynamic_best >= 10:
                scores["Dynamic / API-backed Website"] += 4
            elif qualified_dynamic_best >= 5:
                scores["Dynamic / API-backed Website"] += 2

        if tables:
            scores["HTML Table Website"] += 5
            if not groups: scores["HTML Table Website"] += 3

        if structured_best:
            scores["Repeated / Structured Website"] += 4
            if structured_best >= 10: scores["Repeated / Structured Website"] += 2
            if structured_best >= 50: scores["Repeated / Structured Website"] += 1

        if rel_groups:
            scores["Listing → Detail Website"] += 3
            # Relationships are primary only when they scale with the record set.
            if structured_best and relationship_best >= max(3, int(structured_best * .60)):
                scores["Listing → Detail Website"] += 3
            elif relationship_best >= 10:
                scores["Listing → Detail Website"] += 1

        if doc_groups:
            scores["Document / Resource-driven Website"] += 2
            # A document becomes primary only when the strongest evidence itself
            # is document/resource-like or structured listing evidence is weak.
            if any(t in best_label for t in ("document","download","pdf","resource")):
                scores["Document / Resource-driven Website"] += 5
            elif structured_best <= 3 and document_best:
                scores["Document / Resource-driven Website"] += 3

        expanded=counts["expansion_actions"]>0 or counts["pages"]>1
        if expanded:
            scores["Repeated / Structured Website"] += 1

        # Cost-aware tie breaking: dynamic structured data > direct structured
        # listing > necessary internal detail > document > raw table.
        priority=[
            "Dynamic / API-backed Website",
            "Repeated / Structured Website",
            "Listing → Detail Website",
            "Document / Resource-driven Website",
            "HTML Table Website",
        ]
        profile=max(priority, key=lambda p:(scores[p], -priority.index(p)))
        top_score=scores[profile]
        ordered=sorted(scores.items(), key=lambda kv:kv[1], reverse=True)
        margin=top_score-(ordered[1][1] if len(ordered)>1 else 0)
        confidence="High" if top_score>=7 and margin>=2 else ("Medium" if top_score>=4 else "Low")

        # Approximate listing completeness from discovered field metadata.  This
        # is deliberately generic: semantic names/types, not site-specific rules.
        semantic_tokens=("name","company","business","title","address","city","town","county",
                         "region","postcode","postal","phone","telephone","email","website","url",
                         "date","price","case","description")
        field_tokens=set()
        for g in qualified_structured[:12]:
            fields=g.get("fields") or g.get("columns") or []
            if isinstance(fields, dict): fields=list(fields.keys())
            for f in fields:
                if isinstance(f, dict):
                    raw=" ".join(str(f.get(k,"")) for k in ("name","label","output","field","type"))
                else: raw=str(f)
                low=raw.lower()
                for token in semantic_tokens:
                    if token in low: field_tokens.add(token)
        completeness=min(100, len(field_tokens)*12)
        if structured_best>=10 and completeness<35:
            # record volume itself is useful evidence even when older discovery
            # objects do not expose semantic field metadata.
            completeness=max(completeness,40)

        signals=[]
        if counts["dynamic_datasets"]:
            signals.append(
                f"{counts['dynamic_datasets']} structured network dataset(s) observed, "
                f"up to {counts['dynamic_records']} record(s)"
            )
        if structured_best:
            signals.append(f"qualified primary-record candidate: {structured_best} record(s)")
        elif observed_structured_best:
            signals.append(
                f"no structured dataset passed primary-entity qualification "
                f"(largest observed set: {observed_structured_best} record(s))"
            )
        if rel_groups:
            signals.append(f"relationship candidates detected; strongest relationship set: {relationship_best}")
        if doc_groups:
            signals.append(f"document/resource candidates detected; strongest set: {document_best}")
        if expanded:
            signals.append(f"bounded expansion observed: {counts['expansion_actions']} action(s), {counts['pages']} page snapshot(s)")
        signals.append("evidence scores: "+", ".join(f"{k.split()[0]}={v}" for k,v in ordered if v))

        recommendations=[]
        if profile=="Dynamic / API-backed Website":
            recommendations.append("Prefer structured network/API records only after they pass primary-entity qualification; otherwise continue discovery/search.")
        elif profile=="Document / Resource-driven Website":
            recommendations.append("Use Resource Discovery and DOWNLOAD/FOLLOW the primary document into the document/PDF engine.")
        elif profile=="Listing → Detail Website":
            recommendations.append("Use the listing as the record backbone and FOLLOW internal detail only when it adds missing requested fields.")
        elif profile=="HTML Table Website":
            recommendations.append("Extract the HTML table directly before considering deeper relationships.")
        else:
            recommendations.append("Prefer direct structured listing extraction; avoid deeper crawling unless requested fields are missing.")

        if not qualified_structured and observed_structured_best:
            recommendations.insert(
                0,
                "No trustworthy primary entity dataset is loaded yet; preserve browse/filter "
                "collections as navigation and trigger the page's normal search/interaction to load records."
            )

        if rel_groups:
            if completeness>=60:
                recommendations.append("Listing appears substantially complete: CAPTURE relationship URLs by default; FOLLOW only for requested enrichment.")
            else:
                recommendations.append("Classify each relationship as CAPTURE, FOLLOW, DOWNLOAD, or IGNORE before traversal.")
        if doc_groups and profile!="Document / Resource-driven Website":
            recommendations.append("Treat incidental documents/resources as optional DOWNLOAD relationships, not the primary workflow.")
        if best_count>=100:
            recommendations.append("Large source: validate a bounded sample first, then expand while preserving UI responsiveness.")

        title=clean_text(getattr(parser,"title","") or "")
        if title: signals.append(f"page title: {title[:80]}")

        # SMARTCORE003 — preserve independent source dimensions instead of
        # compressing the entire site into one mutually-exclusive label.
        if qualified_dynamic_best:
            primary_source="Qualified Dynamic API"
            primary_records=qualified_dynamic_best
        elif structured_best:
            primary_source="Qualified Structured Records"
            primary_records=structured_best
        elif tables and not qualified_structured:
            primary_source="HTML Table — qualification pending"
            primary_records=max(0,len(tables or []))
        elif document_best:
            primary_source="Document / Resource"
            primary_records=document_best
        elif observed_structured_best:
            primary_source="No trustworthy primary entity dataset loaded"
            primary_records=0
        else:
            primary_source="Unknown"
            primary_records=0

        relationship_ratio=(relationship_best / float(max(1, structured_best))) if rel_groups and structured_best else 0.0
        if not qualified_structured and observed_structured_best:
            detail_requirement="Primary records not loaded"
            relationship_action="CLASSIFY / SEARCH"
        elif not rel_groups:
            detail_requirement="None detected"
            relationship_action=""
        elif completeness >= 60:
            detail_requirement="Optional enrichment"
            relationship_action="CAPTURE"
        elif relationship_ratio >= .60 or relationship_best >= 10:
            detail_requirement="Likely required"
            relationship_action="FOLLOW/CLASSIFY"
        else:
            detail_requirement="Optional / inspect"
            relationship_action="CLASSIFY"

        if not doc_groups:
            document_role="None detected"
        elif primary_source=="Document / Resource":
            document_role="Primary"
        else:
            document_role="Optional / supporting"

        pagination_role="Detected / bounded sample" if expanded else "Not detected"
        auth_role="Not detected in public profile"
        # A public profiler cannot prove that credentials are required merely
        # from sparse content; expose the uncertainty rather than inventing it.
        if best_count <= 3 and not counts["dynamic_datasets"] and not tables:
            auth_role="Possible — sparse public result; verify Login Website workflow"

        strategy_parts=[primary_source]
        if not qualified_structured and observed_structured_best:
            strategy_parts.append("Search / interaction required")
        if detail_requirement=="Likely required":
            strategy_parts.append("Internal Detail")
        if document_role=="Primary":
            strategy_parts.append("Document/PDF")
        elif document_role=="Optional / supporting":
            strategy_parts.append("Optional Resources")
        if expanded:
            strategy_parts.append("Expansion/Pagination")
        recommended_plan=" → ".join(strategy_parts)

        source_strategy={
            "primary_records":primary_source,
            "primary_record_count":primary_records,
            "internal_details":detail_requirement,
            "relationship_default":relationship_action,
            "documents":document_role,
            "pagination":pagination_role,
            "authentication":auth_role,
            "dynamic_api":"Available" if counts["dynamic_datasets"] else "Not detected",
            "recommended_plan":recommended_plan,
        }

        return {
            "profile":profile,
            "confidence":confidence,
            "signals":signals[:7],
            "recommendations":recommendations[:4],
            "counts":counts,
            "best_records":best_count,
            "listing_completeness":completeness,
            "relationship_action":relationship_action,
            "source_strategy":source_strategy,
            "recommended_plan":recommended_plan,
            "scores":scores,
            "access_state":access_state,
            "url":final_url,
        }

    @staticmethod
    def _semantic_url_role(source_url, candidate_url, label=""):
        """SEMPLAN001: classify a relationship without following it."""
        from urllib.parse import urlsplit, parse_qs
        src=clean_text(str(source_url or ""))
        url=clean_text(str(candidate_url or ""))
        lab=clean_text(str(label or "")).lower()
        if not url:
            return "IGNORE"
        low=url.lower()
        if re.search(r"\.(?:pdf|csv|xlsx?|docx?|zip|json|xml)(?:$|[?#])",low):
            return "RESOURCE"
        if re.search(r"\.(?:jpg|jpeg|png|gif|webp|svg)(?:$|[?#])",low):
            return "MEDIA"
        if low.startswith(("mailto:","tel:","javascript:","#")):
            return "IGNORE"
        try:
            sp=urlsplit(src); cp=urlsplit(url)
            same_host=(sp.netloc.lower().removeprefix("www.")==cp.netloc.lower().removeprefix("www."))
            q=parse_qs(cp.query)
            # RELTRAV003: pagination evidence must come from pagination-shaped
            # query/path structure or a label that is itself a page control.
            # Digits embedded in a URL/title are not pagination evidence.
            page_label=bool(re.fullmatch(
                r"(?:next|previous|prev|page\s*\d+|\d{1,3})",
                clean_text(lab).strip(),
                flags=re.I
            ))
            pageish=(
                any(k.lower() in {"page","paged","p","offset","start"} for k in q)
                or bool(re.search(r"/page/\d+/?$",cp.path.lower()))
                or page_label
            )
            if pageish:
                return "PAGINATION"
            if not same_host and cp.netloc:
                return "EXTERNAL"
            if any(x in lab for x in ("next","previous","prev","home","menu","category","categories","login","sign in","contact")):
                return "NAVIGATION"
            return "DETAIL" if same_host else "EXTERNAL"
        except Exception:
            return "CLASSIFY"

    def _record_region_qualification(self, final_url, fields, records, role_counts):
        """SEMREL003: estimate whether a repeated set behaves like real records.

        Uses generic evidence only: record richness, content diversity, action/control
        vocabulary, URL topology and relationship depth. It does not contain
        site/domain-specific rules.
        """
        from urllib.parse import urlsplit, parse_qs
        fields=list(fields or [])
        records=list(records or [])
        if not records:
            return {"score":-20,"reasons":[],"penalties":["empty candidate"],"richness":0}

        action_terms=(
            "submit","new link","new post","next","previous","prev","login","log in",
            "sign in","sign up","register","menu","home","about","contact","privacy",
            "terms","help","settings","preferences","expand","collapse","more",
            "back to top","download app","mobile website"
        )
        content_terms=(
            "description","summary","content","body","author","date","time","price",
            "rating","review","comments","score","votes","location","address","email",
            "phone","image","thumbnail","headline","title","name","product","job"
        )
        url_fields=[f for f in fields if any(x in str(f).lower() for x in ("url","href","link","permalink","website"))]
        text_fields=[f for f in fields if f not in url_fields]

        nonempty_counts=[]
        text_lengths=[]
        unique_signatures=set()
        action_rows=0
        useful_rows=0
        deep_urls=0
        shallow_urls=0
        utility_urls=0
        fragment_urls=0
        query_only_urls=0
        total_urls=0

        base=urlsplit(final_url or "")
        base_path=(base.path or "/").rstrip("/")

        for rec in records[:60]:
            vals=[]
            row_text=[]
            nonempty=0
            for f in fields:
                v=clean_text(str(rec.get(f,"")))
                if not v:
                    continue
                nonempty+=1
                vals.append(v)
                if f in text_fields:
                    row_text.append(v)
            nonempty_counts.append(nonempty)
            text=" ".join(row_text).strip()
            if text:
                text_lengths.append(len(text))
            sig="|".join(vals[:8]).lower()
            if sig:
                unique_signatures.add(sig)

            low=text.lower()
            if any(term in low for term in action_terms):
                action_rows+=1
            if len(text)>=35 or sum(1 for t in content_terms if t in low)>=2:
                useful_rows+=1

            for uf in url_fields:
                u=clean_text(str(rec.get(uf,"")))
                if not u.startswith(("http://","https://","#")):
                    continue
                total_urls+=1
                if u.startswith("#"):
                    fragment_urls+=1
                    utility_urls+=1
                    continue
                try:
                    p=urlsplit(u)
                    path=(p.path or "/").rstrip("/")
                    q=parse_qs(p.query)
                    segs=[x for x in path.split("/") if x]
                    if path in {"",base_path,"/"} and p.query:
                        query_only_urls+=1
                    if any(k.lower() in {"page","p","paged","offset","start","count","after","before"} for k in q):
                        utility_urls+=1
                    if any(x in path.lower() for x in ("/submit","/login","/register","/settings","/preferences")):
                        utility_urls+=1
                    if len(segs)>=3:
                        deep_urls+=1
                    elif len(segs)<=1:
                        shallow_urls+=1
                except Exception:
                    pass

        n=max(1,min(len(records),60))
        avg_nonempty=sum(nonempty_counts)/max(1,len(nonempty_counts))
        avg_text=sum(text_lengths)/max(1,len(text_lengths)) if text_lengths else 0
        diversity=len(unique_signatures)/n
        action_ratio=action_rows/n
        useful_ratio=useful_rows/n

        score=0
        reasons=[]
        penalties=[]

        if avg_nonempty>=4:
            score+=6; reasons.append("multi-attribute record richness")
        elif avg_nonempty>=3:
            score+=3
        elif avg_nonempty<=1.5:
            score-=5; penalties.append("thin records")

        if avg_text>=80:
            score+=5; reasons.append("substantive record text")
        elif avg_text>=35:
            score+=3
        elif avg_text<12:
            score-=3; penalties.append("very short labels")

        if diversity>=0.75:
            score+=4; reasons.append("high record diversity")
        elif diversity<0.35:
            score-=6; penalties.append("low content diversity")

        if useful_ratio>=0.45:
            score+=4; reasons.append("content-rich rows")

        if action_ratio>=0.35:
            score-=12; penalties.append("action/control dominated rows")
        elif action_ratio>=0.15:
            score-=6; penalties.append("many action/control rows")

        if total_urls:
            deep_ratio=deep_urls/total_urls
            utility_ratio=utility_urls/total_urls
            shallow_ratio=shallow_urls/total_urls
            if deep_ratio>=0.45:
                score+=6; reasons.append("deep record/detail URL topology")
            if utility_ratio>=0.30:
                score-=10; penalties.append("utility/pagination URL topology")
            if shallow_ratio>=0.55 and avg_nonempty<=3:
                score-=7; penalties.append("shallow navigation URL topology")
            if fragment_urls or query_only_urls:
                frac=(fragment_urls+query_only_urls)/total_urls
                if frac>=0.25:
                    score-=6; penalties.append("fragment/query control links")

        detail=role_counts.get("DETAIL",0)
        pagination=role_counts.get("PAGINATION",0)
        if detail>=max(3,pagination*2):
            score+=4; reasons.append("detail relationships dominate pagination")
        if pagination>=max(4,detail):
            score-=7; penalties.append("pagination dominates detail relationships")

        return {
            "score":score,
            "reasons":reasons,
            "penalties":penalties,
            "richness":round(avg_nonempty,2),
            "avg_text":round(avg_text,1),
            "diversity":round(diversity,2),
            "action_ratio":round(action_ratio,2),
            "useful_ratio":round(useful_ratio,2),
        }

    def _semantic_extraction_plan(self, final_url, parser, groups, tables):
        """SEMREL001: choose the dataset most relevant to the page's primary subject.

        Structural repetition is necessary but no longer sufficient. Candidate
        datasets are scored for page-purpose compatibility and penalized for
        navigation/footer/privacy/cookie/tracker/policy/app-store noise.
        """
        from urllib.parse import urlsplit
        groups=list(groups or [])
        title=clean_text(getattr(parser,"title","") or "")
        path=urlsplit(final_url or "").path.lower()
        page_text=(title+" "+path).lower()

        purpose_scores={
            "Product / E-commerce Listing":0,
            "Business / Member Directory":0,
            "Auction / Property Listing":0,
            "Job Listing":0,
            "Article / Content Page":0,
            "Community / Discussion Listing":0,
            "Generic Structured Listing":1,
        }
        purpose_terms={
            "Product / E-commerce Listing":("product","products","shop","store","catalog","price","sku","cart","e-commerce","ecommerce"),
            "Business / Member Directory":("directory","member","members","merchant","broker","company","companies","business"),
            "Auction / Property Listing":("auction","property","properties","sheriff sale","real estate"),
            "Job Listing":("jobs","job","career","vacancy","vacancies","employment"),
            "Article / Content Page":("blog","article","news","guide","tutorial","how-to","post"),
            "Community / Discussion Listing":("reddit","forum","community","discussion","comments","subreddit"),
        }
        for purpose,terms in purpose_terms.items():
            purpose_scores[purpose]+=sum(2 for t in terms if t in page_text)

        semantic_tokens={
            "name":("name","title","company","business","product","job","member","merchant","headline"),
            "price":("price","cost","amount","salary"),
            "location":("address","city","state","region","county","postcode","postal","location"),
            "contact":("email","phone","telephone","mobile"),
            "description":("description","summary","content","body","details","snippet"),
            "date":("date","posted","published","auction date","created"),
            "image":("image","photo","logo","thumbnail","img"),
            "id":("id","sku","case","reference","number"),
            "rating":("rating","review","reviews"),
            "url":("url","href","link","website","permalink"),
            "author":("author","user","username","submitted by"),
            "engagement":("comments","score","votes","points"),
        }

        hard_noise_fields=(
            "cookie","tracker","tracking","expire","expiry","service_policy",
            "privacy","consent","analytics","pixel","purpose","tracker_type",
            "advertising","tag_manager","gtm","clarity"
        )
        nav_noise_values=(
            "about us","contact us","contacts","privacy policy","website privacy policy",
            "terms","terms of service","documentation","video tutorials","test sites",
            "forum","status","media kit","careers","advertising","help center",
            "site rules","reddiquette","mod guidelines","reddit for iphone",
            "reddit for android","mobile website","submit a new link",
            "submit a new text post","submit","log in","sign up","register"
        )
        policy_hosts=("policies.google.com","privacy.microsoft.com","clarity.ms","google-analytics.com")
        app_hosts=("itunes.apple.com","apps.apple.com","play.google.com")

        plans=[]
        for idx,g in enumerate(groups):
            fields=list(g.get("fields") or [])
            records=list(g.get("records") or [])
            count=int(g.get("count") or len(records) or 0)
            dtype=clean_text(g.get("dataset_type","")).lower()
            label=clean_text(g.get("discovery_label",""))
            low_fields=" ".join(str(f).lower() for f in fields)

            categories=set()
            for cat,tokens in semantic_tokens.items():
                if any(t in low_fields for t in tokens):
                    categories.add(cat)

            sample_values=[]
            for rec in records[:25]:
                for f in fields:
                    v=clean_text(str(rec.get(f,"")))
                    if v:
                        sample_values.append(v)
            sample_blob=" | ".join(sample_values[:180]).lower()

            # Relationship role evidence.
            role_counts=Counter()
            url_fields=[f for f in fields if any(t in str(f).lower() for t in ("url","href","link","website","permalink"))]
            label_fields=[f for f in fields if any(t in str(f).lower() for t in ("name","title","text","label","headline"))]
            for rec in records[:30]:
                lab=""
                for lf in label_fields:
                    val=clean_text(str(rec.get(lf,"")))
                    if val:
                        lab=val; break
                for uf in url_fields:
                    val=clean_text(str(rec.get(uf,"")))
                    if val.startswith(("http://","https://","mailto:","tel:","#")):
                        role_counts[self._semantic_url_role(final_url,val,lab)] += 1

            pagination=role_counts.get("PAGINATION",0)
            detail=role_counts.get("DETAIL",0)
            external=role_counts.get("EXTERNAL",0)

            # First identify page purpose from page-level evidence + candidate field evidence.
            if "price" in categories:
                purpose_scores["Product / E-commerce Listing"]+=3
            if "contact" in categories and "location" in categories:
                purpose_scores["Business / Member Directory"]+=3
            if "date" in categories and any(x in low_fields for x in ("auction","case","property","plaintiff","defendant")):
                purpose_scores["Auction / Property Listing"]+=4
            if any(x in low_fields for x in ("salary","employer","job title","position")):
                purpose_scores["Job Listing"]+=4
            if {"author","engagement"} & categories:
                purpose_scores["Community / Discussion Listing"]+=2

            score=0
            reasons=[]
            penalties=[]

            # Structural evidence.
            if count>=3: score+=2; reasons.append("repeated records")
            if count>=10: score+=2
            if count>=50: score+=1
            score+=min(4,len(categories))
            if g.get("relevance")=="High": score+=2

            primary_quality=float(g.get("primary_quality",0) or 0)
            score+=max(-12,min(12,primary_quality*2.4))
            if primary_quality>=2.5:
                reasons.append("strong repeated-entity evidence")
            elif primary_quality<0:
                penalties.append("weak primary-record evidence")

            if dtype=="relationship": score-=2

            cms_schema_terms=(
                "taxonomy","taxonomies","yoast","robots","curies","_links",
                "collection","parent","categories","category","tags","tag","acf"
            )
            cms_schema_hits=sum(1 for t in cms_schema_terms if t in low_fields)
            if cms_schema_hits>=5:
                score-=14
                penalties.append("CMS/taxonomy support schema")
            elif cms_schema_hits>=3:
                score-=8
                penalties.append("CMS metadata-heavy schema")

            # Strong generic noise rejection.
            noise_field_hits=sum(1 for t in hard_noise_fields if t in low_fields)
            noise_value_hits=sum(1 for t in nav_noise_values if t in sample_blob)
            policy_hits=sum(1 for t in policy_hosts if t in sample_blob)
            app_hits=sum(1 for t in app_hosts if t in sample_blob)

            if noise_field_hits>=2:
                score-=12
                penalties.append("tracking/cookie/policy fields")
            elif noise_field_hits:
                score-=5
                penalties.append("noise-like field semantics")
            if noise_value_hits>=3:
                score-=10
                penalties.append("navigation/footer content")
            elif noise_value_hits:
                score-=3
            if policy_hits:
                score-=6
                penalties.append("privacy/policy destinations")
            if app_hits and count<=40:
                score-=4
                penalties.append("app-store/navigation destinations")

            # Primary-content-region evidence from generic selector/path semantics.
            # This is intentionally domain-agnostic: main/feed/result/card/entry/post
            # structures outrank nav/menu/sidebar/footer/category structures.
            primary_region_tokens=(
                " main","> main","main.","content","result","results","record",
                "item","product","card","listing","entry","post","article","feed",
                "thing","search-result","result-item","content-item"
            )
            secondary_region_tokens=(
                " nav","> nav","navbar","menu","footer","header","sidebar","side-bar",
                "breadcrumb","pager","pagination","a.choice","separator","category-list",
                "category_menu","tab-list","site-menu"
            )
            primary_hits=sum(1 for t in primary_region_tokens if t in (" "+low_fields))
            secondary_hits=sum(1 for t in secondary_region_tokens if t in (" "+low_fields))
            if primary_hits:
                bonus=min(8,primary_hits*2)
                score+=bonus
                reasons.append("primary-content selector evidence")
            if secondary_hits:
                penalty=min(14,secondary_hits*4)
                score-=penalty
                penalties.append("secondary navigation/sidebar region")

            # Generic community/feed link semantics: shallow collection/category
            # links are secondary; deep discussion/permalink-style links are primary.
            sample_urls=[]
            for rec in records[:30]:
                for uf in url_fields:
                    u=clean_text(str(rec.get(uf,"")))
                    if u.startswith(("http://","https://")):
                        sample_urls.append(u.lower())
            shallow_collection=sum(
                1 for u in sample_urls
                if re.search(r"/(?:r|c|category|categories|tag|topic|topics)/[^/?#]+/?(?:[?#].*)?$",u)
            )
            discussion_links=sum(
                1 for u in sample_urls
                if re.search(r"/(?:comments|discussion|thread|threads|post|posts)/",u)
            )
            if shallow_collection >= max(3, int(len(sample_urls)*0.45)):
                score-=10
                penalties.append("collection/category navigation links")
            if discussion_links >= max(2, int(len(sample_urls)*0.20)):
                score+=8
                reasons.append("deep discussion/post links")

            # Pagination is never a record backbone.
            if pagination and pagination >= max(2,detail):
                score-=10
                penalties.append("pagination-heavy relationship set")
            if categories <= {"url","name"} and pagination:
                score-=4

            region_q=self._record_region_qualification(final_url,fields,records,role_counts)
            score+=region_q.get("score",0)
            reasons.extend(region_q.get("reasons",[]))
            penalties.extend(region_q.get("penalties",[]))

            nav_score=float(g.get("collection_navigation_score",0) or 0)
            if nav_score>=0.72:
                score-=18
                penalties.append("strong index/navigation collection")
            elif nav_score>=0.60:
                score-=10
                penalties.append("likely browse/index collection")

            metadata_score=float(g.get("page_metadata_support_score",0) or 0)
            if metadata_score>=0.72:
                score-=22
                penalties.append("strong page/schema support metadata")
            elif metadata_score>=0.60:
                score-=12
                penalties.append("likely page/schema support metadata")

            classification_score=float(g.get("classification_collection_score",0) or 0)
            if classification_score>=0.72:
                score-=26
                penalties.append("strong classification/taxonomy collection")
            elif classification_score>=0.58:
                score-=15
                penalties.append("likely classification/index collection")

            form_score=float(g.get("form_ui_support_score",0) or 0)
            coherence_score=float(g.get("entity_row_coherence_score",0) or 0)
            if form_score>=0.72:
                score-=26
                penalties.append("strong form/UI support dataset")
            elif form_score>=0.58:
                score-=16
                penalties.append("likely form/control metadata")
            if coherence_score<0.30:
                score-=16
                penalties.append("weak row-level entity coherence")
            elif coherence_score>=0.65:
                score+=8
                reasons.append("strong row-level entity coherence")

            plans.append({
                "index":idx,"score":score,"count":count,"label":label,
                "dataset_type":dtype,"categories":sorted(categories),
                "roles":dict(role_counts),"fields":fields,
                "sample_blob":sample_blob,
                "reasons":reasons,"penalties":penalties,
                "record_region":region_q,
                "collection_navigation_score":float(g.get("collection_navigation_score",0) or 0),
                "collection_navigation_note":g.get("collection_navigation_note",""),
                "page_metadata_support_score":float(g.get("page_metadata_support_score",0) or 0),
                "page_metadata_support_note":g.get("page_metadata_support_note",""),
                "classification_collection_score":float(g.get("classification_collection_score",0) or 0),
                "classification_collection_note":g.get("classification_collection_note",""),
                "form_ui_support_score":float(g.get("form_ui_support_score",0) or 0),
                "form_ui_support_note":g.get("form_ui_support_note",""),
                "entity_row_coherence_score":float(g.get("entity_row_coherence_score",0) or 0),
                "entity_row_coherence_note":g.get("entity_row_coherence_note",""),
            })

        page_purpose=max(purpose_scores,key=purpose_scores.get)
        if purpose_scores[page_purpose] < 2:
            page_purpose="Generic Structured Listing"

        # Second pass: score candidate compatibility with the chosen page purpose.
        for p in plans:
            cats=set(p["categories"])
            fields_blob=" ".join(str(f).lower() for f in p["fields"])
            samples=p["sample_blob"]
            align=0

            if page_purpose=="Product / E-commerce Listing":
                if "price" in cats: align+=8
                if "image" in cats: align+=3
                if "name" in cats: align+=3
                if "rating" in cats: align+=2
                if any(k in fields_blob for k in ("product","price","sku")): align+=5
                if not ({"price","name","image"} & cats): align-=7

            elif page_purpose=="Article / Content Page":
                if "description" in cats: align+=8
                if "author" in cats: align+=3
                if "date" in cats: align+=3
                if any(k in fields_blob for k in ("article","headline","content","body","author")): align+=6
                if any(k in fields_blob for k in hard_noise_fields): align-=12
                if p["dataset_type"]=="relationship": align-=5

            elif page_purpose=="Community / Discussion Listing":
                if "name" in cats: align+=3
                if "url" in cats: align+=2
                if "author" in cats: align+=5
                if "engagement" in cats: align+=7
                if any(k in fields_blob for k in ("score","comments","author","permalink","entry","thing","post","thread")): align+=9
                elif "title" in fields_blob: align+=3
                if any(k in fields_blob for k in ("a.choice","separator","sidebar","menu","category")): align-=12
                if sum(1 for t in nav_noise_values if t in samples)>=2: align-=10

            elif page_purpose=="Business / Member Directory":
                if "name" in cats: align+=4
                if "location" in cats: align+=5
                if "contact" in cats: align+=5
                if "url" in cats: align+=2
                if not ({"name","location","contact"} & cats): align-=5

            elif page_purpose=="Auction / Property Listing":
                for cat in ("name","location","date","id","price"):
                    if cat in cats: align+=3
                if any(k in fields_blob for k in ("auction","property","case","plaintiff","defendant")): align+=6

            elif page_purpose=="Job Listing":
                if "name" in cats: align+=4
                if "location" in cats: align+=4
                if "date" in cats: align+=2
                if any(k in fields_blob for k in ("job","position","employer","salary")): align+=7

            else:
                if "name" in cats: align+=2
                if len(cats)>=3: align+=2

            p["purpose_alignment"]=align
            p["score"]+=align

            # Build useful fields only after relevance is established.
            useful=[]
            relationship_fields={}
            records=list(groups[p["index"]].get("records") or [])
            for f in p["fields"]:
                fl=str(f).lower()
                if any(x in fl for x in ("source page url","back2top","breadcrumb")):
                    continue
                if any(x in fl for x in hard_noise_fields):
                    continue
                samples=[clean_text(str(r.get(f,""))) for r in records[:15] if clean_text(str(r.get(f,"")))]
                role=""
                if any(t in fl for t in ("url","href","link","website","permalink")) and samples:
                    roles=Counter(self._semantic_url_role(final_url,v,"") for v in samples if v.startswith(("http://","https://","mailto:","tel:","#")))
                    role=roles.most_common(1)[0][0] if roles else ""
                    relationship_fields[f]=role
                    if role in {"PAGINATION","NAVIGATION","IGNORE","EXTERNAL"}:
                        continue
                # Exclude fields dominated by known navigation/footer values.
                sv=" | ".join(samples).lower()
                if sum(1 for t in nav_noise_values if t in sv)>=2:
                    continue
                useful.append(f)
            p["useful_fields"]=useful
            p["relationship_fields"]=relationship_fields

        ranked=sorted(plans,key=lambda x:(x["score"],x["purpose_alignment"],x["count"]),reverse=True)

        # RECON012: recommendation is qualification-first. Do not take ranked[0]
        # and merely try to veto it. Walk the ranking and select the first
        # candidate that independently passes the primary-entity contract.
        chosen=None
        for candidate in ranked:
            candidate_group=groups[candidate["index"]]
            candidate_quality=float(candidate_group.get("primary_quality",0) or 0)
            candidate_fields=" ".join(str(f).lower() for f in candidate.get("fields",[]))
            candidate_cms_hits=sum(
                1 for t in (
                    "taxonomy","taxonomies","yoast","robots","curies","_links",
                    "collection","parent","categories","category","tags","tag","acf",
                    "slug","term","terms","facet","facets"
                )
                if t in candidate_fields
            )
            entity_ready, nav_score, readiness_note=_entity_dataset_ready(
                candidate_group, final_url
            )
            classification_score=float(
                candidate_group.get("classification_collection_score",0) or 0
            )
            metadata_score=float(
                candidate_group.get("page_metadata_support_score",0) or 0
            )
            form_score=float(
                candidate_group.get("form_ui_support_score",0) or 0
            )
            coherence_score=float(
                candidate_group.get("entity_row_coherence_score",0) or 0
            )
            candidate["entity_ready"]=entity_ready
            candidate["entity_readiness_note"]=readiness_note

            reject_reasons=[]
            if candidate["score"]<5:
                reject_reasons.append("insufficient primary-record score")
            if candidate_quality<0.5 and candidate_cms_hits>=3:
                reject_reasons.append("CMS/classification support structure")
            if classification_score>=0.58:
                reject_reasons.append("classification/taxonomy collection")
            if metadata_score>=0.62:
                reject_reasons.append("page/schema support metadata")
            if form_score>=0.58:
                reject_reasons.append("form/UI support metadata")
            if coherence_score<0.48:
                reject_reasons.append("insufficient row-level entity coherence")
            if not entity_ready:
                reject_reasons.append("not primary-entity ready")
            candidate["qualification_rejections"]=reject_reasons
            if reject_reasons:
                for reason in reject_reasons:
                    if reason not in candidate["penalties"]:
                        candidate["penalties"].append(reason)
                continue
            chosen=candidate
            break

        relationship_summary=Counter()
        for p in plans:
            relationship_summary.update(p.get("roles",{}))

        confidence="Low"
        if chosen:
            qualified_others=[
                p for p in ranked
                if p is not chosen and not p.get("qualification_rejections")
            ]
            margin=chosen["score"]-(qualified_others[0]["score"] if qualified_others else 0)
            region_score=(chosen.get("record_region") or {}).get("score",0)
            if chosen["score"]>=14 and margin>=3 and region_score>=0:
                confidence="High"
            elif chosen["score"]>=8 and region_score>=-2:
                confidence="Medium"

        warning=""
        if not chosen and ranked:
            warning=(
                "No trustworthy primary entity dataset is loaded yet. "
                "Only browse/index/navigation collections or support datasets are currently available. "
                "Filter/category label collections are also treated as navigation/support data. "
                "Form labels, validation/help text, and UI-control datasets are support data as well. "
                "Page/schema metadata is also treated as support data, not as a primary entity dataset. "
                "A search/location input or another user action may be required before the real repeated entity records load."
            )
        elif chosen and (chosen["score"]<8 or (chosen.get("record_region") or {}).get("score",-99)<-2):
            warning="The strongest repeated set does not behave enough like primary content records; manual review is recommended."
        elif chosen and not chosen.get("useful_fields"):
            warning="The strongest candidate has no confidently useful fields after noise rejection."

        plan={
            "page_purpose":page_purpose,
            "recommended_dataset_index":chosen["index"] if chosen else -1,
            "recommended_dataset_label":chosen["label"] if chosen else "None",
            "recommended_record_count":chosen["count"] if chosen else 0,
            "recommended_fields":chosen.get("useful_fields",[]) if chosen else [],
            "relationship_roles":dict(relationship_summary),
            "dataset_rankings":ranked[:8],
            "confidence":confidence,
            "warning":warning,
        }
        return plan


    def _refresh_website_interpretation(self, final_url, parser, groups, tables, prefix):
        """RECON029: reverse-engineering summary; reads discovery state only."""
        if prefix!="public":
            return
        try:
            model=interpret_website_snapshot(
                source_url=final_url,
                title=getattr(parser,"title",""),
                element_counts=dict(getattr(parser,"counts",{}) or {}),
                datasets=groups or [],
                tables=tables or [],
                links=list(getattr(self,"links",[]) or []),
                dom_count=len((getattr(self,"dom_catalogs",{}) or {}).get(prefix,[]) or []),
                semantic_plan=getattr(self,"semantic_extraction_plan",{}) or {},
            )
            self.website_interpretation=model
            var=getattr(self,"public_interpretation_summary",None)
            if var is not None:
                var.set(interpretation_summary(model))
        except Exception as exc:
            self.website_interpretation={"mode":"OBSERVATION_ONLY","error":str(exc)}
            var=getattr(self,"public_interpretation_summary",None)
            if var is not None:
                var.set(
                    "Observation-only interpretation unavailable for this analysis. "
                    "Extraction behavior was not changed."
                )


    def _show_semantic_extraction_plan(self, plan):
        self.semantic_extraction_plan=plan or {}
        var=getattr(self,"public_semantic_plan_summary",None)
        if var is None:
            return
        apply_button=getattr(self,"public_apply_semantic_button",None)
        if not plan or plan.get("recommended_dataset_index",-1)<0:
            if apply_button is not None:
                try: apply_button.configure(state="disabled")
                except Exception: pass
            warning=(plan or {}).get("warning","")
            var.set(
                (warning or
                 "No trustworthy primary entity dataset is currently loaded. "
                 "Review available navigation/search actions before traversal.")
                + " Next: enter a normal search/location seed if this directory requires one, "
                  "then click Analyze Website again."
            )
            return
        if apply_button is not None:
            try: apply_button.configure(state="normal")
            except Exception: pass
        roles=plan.get("relationship_roles",{})
        role_text=", ".join(f"{k}: {v}" for k,v in roles.items() if v) or "none"
        fields=", ".join(plan.get("recommended_fields",[])[:8]) or "none confidently identified"
        warning=plan.get("warning","")
        ranked=plan.get("dataset_rankings",[]) or []
        rejected=[]
        for item in ranked[1:4]:
            if item.get("penalties"):
                rejected.append(f"Dataset {item.get('index',0)+1}: {', '.join(item.get('penalties',[])[:2])}")
        rejected_text=(" Rejected alternatives: "+"; ".join(rejected)+".") if rejected else ""
        var.set(
            f"{plan.get('page_purpose')} — {plan.get('confidence')} confidence. "
            f"Recommended dataset: Dataset {plan.get('recommended_dataset_index',0)+1} "
            f"({plan.get('recommended_record_count',0)} records). "
            f"Suggested fields: {fields}. Relationship roles: {role_text}."
            + rejected_text
            + (f" Warning: {warning}" if warning else "")
        )

    @staticmethod
    def _semantic_field_concept(source, category, output, samples=None):
        text=(" ".join([str(source or ""),str(output or "")])).lower()
        vals=[clean_text(str(v)) for v in (samples or []) if clean_text(str(v))]
        blob=" | ".join(vals).lower()

        if vals and all(re.fullmatch(r"\d+\s*(?:min|minute)s?\s+read",v.lower()) for v in vals[:3]):
            return "reading_time"
        if any(k in text for k in ("review count","reviews","review_count","comments","comment count")):
            return "reviews"
        if any(k in text for k in ("rating","stars","score")):
            return "rating"
        if any(k in text for k in ("description","summary","snippet","content","body")):
            return "description"
        if any(k in text for k in ("author","username","submitted by")):
            return "author"
        if any(k in text for k in ("published","posted","created","date","time")) and category=="Date / Time":
            return "date"
        if category=="Price / Amount":
            return "price"
        if category=="Image / Media":
            return "image"
        if category=="Link / Detail":
            return "detail_url"
        if category=="Email":
            return "email"
        if category=="Phone":
            return "phone"
        if category=="Location":
            return "location:"+clean_text(str(output or source)).lower()
        if category=="Identifier":
            return "id"

        # DETAILSEM006: website-derived labels are separate business fields.
        # Do not collapse every labelled person/company/title field into "name".
        source_low=clean_text(str(source or "")).lower()
        output_clean=clean_text(str(output or ""))
        if source_low.startswith(("semantic:","association:","meta:")) and output_clean:
            key=re.sub(r"[^a-z0-9]+"," ",output_clean.lower()).strip()
            if key and key not in {"name","title","text","field"}:
                return "labeled:"+key

        if category=="Name / Title":
            return "name"
        if any(k in text for k in ("title","name","link_text","link text","headline")):
            return "name"

        # Person-like short repeated values in article/card records are commonly authors.
        if vals and len(vals)>=2 and all(
            1 <= len(v.split()) <= 4 and
            not re.search(r"https?://|[$€£]|\d+\s*(?:min|minute)s?\s+read",v.lower())
            for v in vals[:3]
        ) and any(k in text for k in ("strong","byline","meta")):
            return "author"

        return "field:"+clean_text(str(output or source)).lower()


    @staticmethod
    def _semantic_field_quality(source, category, output, samples):
        src=clean_text(str(source or ""))
        low=src.lower()
        leaf=re.split(r"[. >\\[]",low)[-1].replace("]","")
        score=0
        canonical={
            "price","title","name","description","summary","image_url","image","url",
            "detail url","email","phone","address","city","state","region","postcode",
            "rating","reviews","review_count","author","date"
        }
        if low in canonical or leaf in canonical: score+=10
        if low in {"price","title","name","description","image_url","url","link_text"}: score+=5
        if any(x in low for x in ("div.","span.","p.","a.","h1","h2","h3","h4","[text]","[href]","[src]")):
            score-=3
        if low in {"p","span","div","a","strong"} or re.fullmatch(r"(?:p|span|div|a|strong)(?:\s+text.*)?",low):
            score-=8
        if len(src)>55: score-=4
        if category in {"Price / Amount","Image / Media","Email","Phone","Location","Identifier","Name / Title"}:
            score+=3
        vals=[clean_text(str(v)) for v in (samples or []) if clean_text(str(v))]
        if vals: score+=1
        return score

    def _consolidate_semantic_fields(self, wanted, relationship_fields):
        """SEMREL003: one strongest field per concept; suppress raw DOM duplicates."""
        candidates={}
        metadata={}
        for mapping in self.builder_mappings:
            source=mapping.get("source","")
            if source not in wanted:
                continue
            role=relationship_fields.get(source,"")
            if role in {"PAGINATION","NAVIGATION","IGNORE","EXTERNAL"}:
                continue
            sample_text=getattr(self,"builder_field_samples",{}).get(source,"")
            samples=[x.strip() for x in sample_text.split("|") if x.strip()][:3]
            category,_=self._classify_discovered_field(source,samples)
            output=clean_text(str(mapping.get("output") or source))
            concept=self._semantic_field_concept(source,category,output,samples)
            quality=self._semantic_field_quality(source,category,output,samples)

            # Raw selector-only fields must prove they add a distinct semantic concept.
            low=source.lower()
            generic_dom=(
                low in {"p","span","div","a","strong"} or
                len(source)>55 or
                (" > " in source and any(x in low for x in ("div.","span.","p.","a.","strong")))
            )
            if generic_dom and concept.startswith("field:") and quality<0:
                continue

            current=candidates.get(concept)
            if current is None or quality>current[0]:
                candidates[concept]=(quality,source)
                metadata[source]=(concept,category)

        selected={source for _quality,source in candidates.values()}

        canonical_names={
            "name":"Name","price":"Price","description":"Description",
            "detail_url":"Detail URL","image":"Image URL","reviews":"Review Count",
            "rating":"Rating","author":"Author","reading_time":"Reading Time",
            "date":"Date","email":"Email","phone":"Phone","id":"Identifier",
        }
        for mapping in self.builder_mappings:
            source=mapping.get("source","")
            if source not in selected:
                continue
            concept,_category=metadata.get(source,("",""))
            if concept in canonical_names:
                mapping["output"]=canonical_names[concept]
        return selected


    def _semantic_relationship_candidates(self, dataset_index, plan=None):
        """RELTRAV001: rank Page-1 relationships without following bad targets."""
        from urllib.parse import urlsplit
        if not (0 <= int(dataset_index) < len(getattr(self,"detected_groups",[]) or [])):
            return []
        item=self.detected_groups[int(dataset_index)]
        records=list(item.get("records") or [])
        fields=list(item.get("fields") or [])
        source_url=clean_text(self.public_url.get().strip()) if hasattr(self,"public_url") else ""
        ranked=[]
        for field in fields:
            low=field.lower()
            if not any(k in low for k in ("url","href","link","website","permalink")):
                continue
            urls=[]; labels=[]
            for rec in records[:60]:
                u=clean_text(str(rec.get(field,"")))
                if not self.valid_url(u): continue
                urls.append(u)
                lab=""
                for lf in fields:
                    if any(k in lf.lower() for k in ("title","name","link_text","headline")):
                        lab=clean_text(str(rec.get(lf,"")))
                        if lab: break
                labels.append(lab)
            if not urls: continue
            roles=Counter(); depths=[]; unique=set(); same_host=0
            for i,u in enumerate(urls):
                role=self._semantic_url_role(source_url,u,labels[i] if i<len(labels) else "")
                roles[role]+=1; unique.add(u)
                try:
                    sp=urlsplit(source_url); up=urlsplit(u)
                    if sp.netloc.lower().removeprefix("www.")==up.netloc.lower().removeprefix("www."):
                        same_host+=1
                    depths.append(len([x for x in up.path.split("/") if x]))
                except Exception: pass
            dominant=roles.most_common(1)[0][0]
            n=max(1,len(urls))
            detail_ratio=roles.get("DETAIL",0)/n
            pagination_ratio=roles.get("PAGINATION",0)/n
            external_ratio=roles.get("EXTERNAL",0)/n
            unique_ratio=len(unique)/n
            avg_depth=sum(depths)/max(1,len(depths)) if depths else 0
            score=0; reasons=[]; penalties=[]
            if dominant=="DETAIL": score+=30
            if detail_ratio>=.75: score+=20; reasons.append("mostly internal detail URLs")
            elif detail_ratio>=.50: score+=10
            if unique_ratio>=.75: score+=10; reasons.append("record-specific URL diversity")
            if avg_depth>=3: score+=8; reasons.append("deep record URL paths")
            if same_host/n>=.9: score+=5
            if pagination_ratio: score-=40*pagination_ratio; penalties.append("pagination URLs")
            if external_ratio>=.25: score-=25; penalties.append("external destinations")
            if dominant in {"NAVIGATION","IGNORE"}: score-=45; penalties.append("navigation/control relationship")
            if dominant=="RESOURCE": score-=20; penalties.append("resource relationship")
            if any(k in low for k in ("source page","back2top","breadcrumb","next","prev","pagination")):
                score-=35; penalties.append("navigation-like field")
            if len(unique)<=2 and len(urls)>=5:
                score-=15; penalties.append("low URL diversity")
            ranked.append({"field":field,"score":round(score,2),"role":dominant,
                           "detail_ratio":round(detail_ratio,2),"unique_ratio":round(unique_ratio,2),
                           "sample_url":urls[0],"reasons":reasons,"penalties":penalties})
        ranked.sort(key=lambda x:(x["score"],x["detail_ratio"],x["unique_ratio"]),reverse=True)
        return ranked

    def _recommended_detail_relationship(self, dataset_index, plan=None):
        ranked=self._semantic_relationship_candidates(dataset_index,plan)
        best=next((x for x in ranked if x.get("role")=="DETAIL" and x.get("score",0)>=20),None)
        return best,ranked

    def _bind_recommended_detail_relationship(self, prefix, dataset_index, plan, best=None, ranked=None):
        """RELTRAV002: persist the Recommended Plan's qualified DETAIL/FOLLOW field."""
        ranked=list(ranked or [])
        relationship_fields={}
        for item in (plan.get("dataset_rankings",[]) or []):
            if int(item.get("index",-1))==int(dataset_index):
                relationship_fields=item.get("relationship_fields",{}) or {}
                break

        candidate=best
        if not candidate:
            detail_fields=[f for f,r in relationship_fields.items() if r=="DETAIL"]
            if detail_fields:
                field=detail_fields[0]
                candidate=next((x for x in ranked if x.get("field")==field),None) or {
                    "field":field,"score":20,"role":"DETAIL","sample_url":""
                }

        if not candidate:
            enabled_sources={m.get("source") for m in self.builder_mappings if m.get("enabled",True)}
            item=self.detected_groups[int(dataset_index)] if 0 <= int(dataset_index) < len(self.detected_groups) else {}
            records=list(item.get("records") or [])
            base=(clean_text(self.public_url.get().strip()) if prefix=="public"
                  else clean_text(self.protected_url.get().strip()))
            for field in item.get("fields",[]) or []:
                if field not in enabled_sources:
                    continue
                urls=[clean_text(str(r.get(field,""))) for r in records[:30]]
                urls=[u for u in urls if self.valid_url(u)]
                if not urls:
                    continue
                roles=Counter(self._semantic_url_role(base,u,"") for u in urls)
                if roles.get("DETAIL",0) >= max(1,(len(urls)+1)//2):
                    candidate={"field":field,"score":18,"role":"DETAIL","sample_url":urls[0]}
                    break

        self.builder_relationship_candidates=ranked
        self.builder_relationship_recommendation=candidate or {}
        if not candidate:
            return None
        field=clean_text(candidate.get("field",""))
        self.builder_detail_link_field=field
        box=getattr(self,f"{prefix}_builder_detail_url_field",None)
        if box is not None:
            try: box.set(field)
            except Exception: pass
        return candidate

    def apply_semantic_extraction_plan(self, prefix="public"):
        plan=getattr(self,"semantic_extraction_plan",{}) or {}
        if int(plan.get("recommended_dataset_index",-1) or -1) < 0:
            messagebox.showinfo(
                "No Qualified Primary Dataset",
                "There is no recommended extraction dataset to apply yet. "
                "Use the website's normal search/location interaction if required, then analyze again."
            )
            return
        idx=int(plan.get("recommended_dataset_index",-1))
        if not (0 <= idx < len(getattr(self,"detected_groups",[]) or [])):
            messagebox.showinfo("No Recommended Dataset","Analyze the website first. RTUDES has not identified a usable primary dataset.")
            return
        if plan.get("confidence")=="Low":
            messagebox.showwarning(
                "Recommendation Needs Review",
                "RTUDES does not have enough semantic confidence to apply this plan automatically.\n\n"
                + (plan.get("warning") or "Review the detected datasets before selecting fields.")
            )
            return
        choice=getattr(self,f"{prefix}_builder_dataset")
        choice.current(idx)
        self.builder_load_dataset(prefix)

        wanted=set(plan.get("recommended_fields",[]) or [])
        relationship_fields=(plan.get("dataset_rankings",[{}])[0].get("relationship_fields",{})
                             if plan.get("dataset_rankings") else {})
        consolidated=self._consolidate_semantic_fields(wanted,relationship_fields)
        used_outputs=set()
        item=self.detected_groups[idx]
        for mapping in self.builder_mappings:
            source=mapping.get("source","")
            role=relationship_fields.get(source,"")
            sample_values=[
                clean_text(str(r.get(source,"")))
                for r in (item.get("records",[]) or [])[:20]
                if clean_text(str(r.get(source,"")))
            ]
            category,confidence=self._classify_discovered_field(source,sample_values[:3])
            mapping["semantic_type"]=category
            mapping["sample"]=sample_values[0] if sample_values else ""
            mapping["confidence"]=confidence
            mapping["auto_selected"]=True
            mapping["recommendation_engine"]="semantic_plan"
            enabled=source in consolidated and role not in {"PAGINATION","NAVIGATION","IGNORE","EXTERNAL"}
            out=clean_text(str(mapping.get("output") or source))
            key=out.lower()
            if enabled and key in used_outputs:
                enabled=False
            if enabled:
                used_outputs.add(key)
            mapping["enabled"]=enabled
        self._normalize_auto_schema_mappings()
        self._suppress_equivalent_auto_relationship_urls()
        self._apply_final_auto_schema_gate()
        self._builder_suppress_same_level_duplicates(1)
        self._builder_refresh_mapping_tree(prefix)

        relationship_best,relationship_ranked=self._recommended_detail_relationship(idx,plan)
        relationship_best=self._bind_recommended_detail_relationship(
            prefix,idx,plan,relationship_best,relationship_ranked
        )

        selected=sum(1 for m in self.builder_mappings if m.get("enabled"))
        getattr(self,f"{prefix}_result_notebook").select(getattr(self,f"{prefix}_builder_tab"))
        if relationship_best:
            self.status_var.set(
                f"Semantic plan applied — Dataset {idx+1}, {selected} consolidated field(s). "
                f"Recommended detail relationship: {relationship_best.get('field')} "
                f"({relationship_best.get('score')} score). Next: click Follow as Next Page."
            )
        else:
            self.status_var.set(
                f"Semantic plan applied — Dataset {idx+1}, {selected} consolidated field(s). "
                "No qualified detail relationship was found; Page 1 can be previewed/extracted directly."
            )

    def _show_smart_profile(self, profile):
        self.smart_website_profile = profile or {}
        var = getattr(self, "public_profile_summary", None)
        if var is None:
            return
        signals = "; ".join(profile.get("signals", [])) or "No strong structural signal yet."
        recs = " ".join(f"{i+1}. {r}" for i,r in enumerate(profile.get("recommendations", [])))
        completeness=profile.get("listing_completeness",0)
        relation=profile.get("relationship_action","")
        strategy=profile.get("source_strategy",{}) or {}
        access=profile.get("access_state",{}) or {}
        access_label=access.get("state","ACCESSIBLE")
        decision=(
            f" Access: {access_label}."
            f" Primary records: {strategy.get('primary_records','Unknown')} "
            f"({strategy.get('primary_record_count',0)}). "
            f"Internal details: {strategy.get('internal_details','Unknown')}. "
            f"Documents: {strategy.get('documents','Unknown')}. "
            f"Pagination: {strategy.get('pagination','Unknown')}. "
            f"Authentication: {strategy.get('authentication','Unknown')}. "
            f"Plan: {strategy.get('recommended_plan',profile.get('profile','Unknown'))}."
            f" Listing completeness: {completeness}%."
        )
        if relation:
            decision+=f" Relationship default: {relation}."
        var.set(
            f"{profile.get('profile','Unknown')} — {profile.get('confidence','Low')} confidence."
            f"{decision} Detected: {signals}. Recommended: {recs}"
        )

    def reset_current_extraction_task(self, prefix="public", confirm=True, clear_url=True):
        """Clear transient extraction state without closing the application.

        Saved profiles/projects, application settings, downloaded output files and
        the browser/login cookie session are deliberately preserved.  This gives a
        user a clean workspace for the next website without forcing an app restart.
        """
        if confirm and not messagebox.askyesno(
            "Start New Task",
            "Clear the current extraction workspace and start a new task?\n\n"
            "This clears analyzed pages, detected datasets, field selections, "
            "page levels, previews and temporary crawl results.\n\n"
            "Saved profiles, settings, output files and the current login/browser "
            "session will be preserved."
        ):
            return

        try:
            self.multilevel_stop.set()
        except Exception:
            pass

        # Transient discovery/extraction models.
        self.detected_groups = []
        self.table_data = []
        self.links = []
        self.api_headers = []
        self.api_rows = []
        self.api_json = None
        self.multilevel_rows = []

        self.builder_mappings = []
        self.builder_preview_headers = []
        self.builder_preview_rows = []
        self.builder_field_samples = {}
        self.builder_detail_fields = {}
        self.builder_detail_url = ""
        self.builder_detail_mappings = []
        self.builder_levels = {}
        self.builder_current_level = 1
        self.builder_detail_link_field = ""
        self.builder_guided_rows = []
        self.builder_resource_parent_record = None
        self.builder_page_trees = {}
        self.builder_page_frames = {}
        self.builder_page_search_vars = {}
        self.builder_page_selected_only_vars = {}
        self._builder_detached_iids = {}

        self.dom_catalogs = {}
        self.dom_catalog_filtered = {}
        self.uei_analysis = {}
        self.uei_field_rows = []
        self.uei_selected_fields = set()
        self.expansion_summary = {}

        # Clear the visible URL only for the currently reset Public Website task.
        if clear_url and prefix == "public" and hasattr(self, "public_url"):
            self.public_url.set("")
            if hasattr(self, "public_search_seed"):
                self.public_search_seed.set("")

        # Clear simple/advanced Public Website result controls when they exist.
        for attr in (
            f"{prefix}_group_tree", f"{prefix}_table_tree", f"{prefix}_links_tree",
            f"{prefix}_dom_tree", f"{prefix}_builder_mapping_tree",
            f"{prefix}_builder_preview", f"{prefix}_builder_sample_tree",
            f"{prefix}_builder_detail_tree", f"{prefix}_builder_detail_mapping_tree",
            f"{prefix}_builder_level_tree"
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    self.clear_tree(widget)
                except Exception:
                    pass

        for attr in (f"{prefix}_group_choice", f"{prefix}_table_choice", f"{prefix}_builder_dataset"):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget["values"] = ()
                    widget.set("")
                except Exception:
                    pass

        for attr in (
            f"{prefix}_dom_search", f"{prefix}_dom_example",
            f"{prefix}_builder_source", f"{prefix}_builder_output"
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.delete(0, "end")
                except Exception:
                    pass

        raw = getattr(self, f"{prefix}_inspector", None)
        if raw is not None:
            try:
                raw.delete("1.0", "end")
            except Exception:
                pass

        hint = getattr(self, f"{prefix}_dom_hint", None)
        if hint is not None:
            try:
                hint.set(
                    "Ready for a new task. Enter a website URL and click Analyze Website."
                )
            except Exception:
                pass

        detail_status = getattr(self, f"{prefix}_builder_detail_status", None)
        if detail_status is not None:
            try:
                detail_status.set("No detail page inspected.")
            except Exception:
                pass

        detail_field = getattr(self, f"{prefix}_builder_detail_url_field", None)
        if detail_field is not None:
            try:
                detail_field.set("")
            except Exception:
                pass

        breadcrumb = getattr(self, f"{prefix}_builder_level_breadcrumb", None)
        if breadcrumb is not None:
            try:
                breadcrumb.set("Page 1 — Listing Page")
            except Exception:
                pass

        # Rebuild only the empty workspace shell. This is the same state a user gets
        # after starting the application, but without losing persistent preferences
        # or an authenticated browser session.
        try:
            self._builder_refresh_workspace(prefix)
        except Exception:
            pass

        # Return Simple Mode to its normal clean starting location.
        try:
            notebook = getattr(self, f"{prefix}_result_notebook")
            notebook.select(getattr(self, f"{prefix}_builder_tab"))
        except Exception:
            pass

        try:
            self.multilevel_stop.clear()
        except Exception:
            pass
        if prefix == "public" and hasattr(self, "public_profile_summary"):
            self.public_profile_summary.set(
                "Enter a URL and click Analyze Website. RTUDES will profile the source and recommend a workflow."
            )
        self.smart_website_profile = {}
        self.semantic_extraction_plan = {}
        self.website_interpretation = {}
        if prefix=="public" and hasattr(self,"public_interpretation_summary"):
            self.public_interpretation_summary.set(
                "Analyze a website. RTUDES will describe delivery, structure, controls, relationships and resources without changing extraction."
            )
        if prefix=="public" and hasattr(self,"public_semantic_plan_summary"):
            self.public_semantic_plan_summary.set(
                "Analyze a website. RTUDES will identify the likely page purpose, primary records, useful fields, and relationship roles."
            )
            btn=getattr(self,"public_apply_semantic_button",None)
            if btn is not None:
                try: btn.configure(state="normal")
                except Exception: pass
        self.status_var.set(
            "New task ready — temporary analysis/extraction state cleared. "
            "Saved profiles, settings, outputs and browser login session were preserved."
        )

    def toggle_public_advanced_tools(self):
        """Show/hide technical Public Website result tabs without removing them."""
        notebook = getattr(self, "public_results", None)
        tabs = getattr(self, "public_advanced_tabs", [])
        if notebook is None or not tabs:
            return
        visible = not bool(getattr(self, "public_advanced_visible", False))
        for tab, label in tabs:
            try:
                if visible:
                    notebook.add(tab, text=label)
                else:
                    notebook.hide(tab)
            except tk.TclError:
                pass
        self.public_advanced_visible = visible
        if hasattr(self, "public_advanced_button"):
            self.public_advanced_button.configure(text="Hide Advanced Tools" if visible else "Advanced Tools")
        if not visible:
            try: notebook.select(getattr(self, "public_builder_tab"))
            except Exception: pass

    def toggle_builder_advanced(self, prefix="public"):
        frame = getattr(self, f"{prefix}_builder_advanced_container", None)
        if frame is None:
            return
        if frame.winfo_manager():
            frame.pack_forget()
            self.status_var.set("Simple Mode active — technical mapping controls hidden.")
        else:
            frame.pack(fill="x", padx=8, pady=(8,0))
            self.status_var.set("Advanced Controls visible — use only for selector/mapping diagnostics.")

    def _build_login_tab(self):
        form = ttk.LabelFrame(self.login_tab, text="Authentication Configuration", padding=10)
        form.pack(fill="x", padx=10, pady=10)

        self.auth_mode = tk.StringVar(value="Form Login")
        ttk.Label(form, text="Mode:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            form, textvariable=self.auth_mode, state="readonly",
            values=["Form Login", "Basic Auth", "Cookie Header"], width=20
        ).grid(row=0, column=1, sticky="w", padx=6)

        self.login_url = tk.StringVar()
        self.protected_url = tk.StringVar()
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.username_field = tk.StringVar(value="username")
        self.password_field = tk.StringVar(value="password")
        self.extra_fields = tk.StringVar(value="{}")
        self.cookie_header = tk.StringVar()

        labels = [
            ("Login URL:", self.login_url),
            ("Protected Page URL:", self.protected_url),
            ("Username:", self.username),
            ("Password:", self.password),
            ("Username field name:", self.username_field),
            ("Password field name:", self.password_field),
            ("Extra form fields JSON:", self.extra_fields),
            ("Cookie header:", self.cookie_header),
        ]

        for i, (label, variable) in enumerate(labels, 1):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", pady=3)
            show = "*" if label == "Password:" else ""
            ttk.Entry(form, textvariable=variable, show=show).grid(
                row=i, column=1, columnspan=4, sticky="ew", padx=6, pady=3
            )

        ttk.Button(form, text="Login and Fetch", command=self.login_and_fetch).grid(
            row=9, column=4, sticky="e", pady=(8, 0)
        )
        form.columnconfigure(1, weight=1)
        form.columnconfigure(2, weight=1)
        form.columnconfigure(3, weight=1)

        ttk.Label(
            self.login_tab,
            text=(
                "Credentials are used only in memory for the current session. "
                "This connector does not bypass CAPTCHA, MFA, SSO, or access restrictions."
            ),
            padding=(12, 0)
        ).pack(fill="x")

        self.login_results = ttk.Notebook(self.login_tab)
        self.login_results.pack(fill="both", expand=True, padx=10, pady=10)
        self._build_html_result_tabs(self.login_results, prefix="login")

    def _build_html_result_tabs(self, notebook, prefix="public"):
        auto_tab = ttk.Frame(notebook)
        table_tab = ttk.Frame(notebook)
        links_tab = ttk.Frame(notebook)
        inspector_tab = ttk.Frame(notebook)
        builder_tab = ttk.Frame(notebook)

        if prefix == "public":
            # Simple Mode is the default.  Ordinary users see one guided workspace;
            # technical discovery tools remain available only when Advanced Tools
            # is explicitly enabled from the Public Website header.
            notebook.add(builder_tab, text="Extraction Workspace")
            notebook.add(auto_tab, text="Auto-Detected Data")
            notebook.add(table_tab, text="HTML Tables")
            notebook.add(links_tab, text="Links")
            notebook.add(inspector_tab, text="DOM Inspector")
            for hidden_tab in (auto_tab, table_tab, links_tab, inspector_tab):
                notebook.hide(hidden_tab)
            self.public_advanced_tabs = [
                (auto_tab,"Auto-Detected Data"),(table_tab,"HTML Tables"),
                (links_tab,"Links"),(inspector_tab,"DOM Inspector")
            ]
            self.public_advanced_visible = False
        else:
            notebook.add(auto_tab, text="Auto-Detected Data")
            notebook.add(table_tab, text="HTML Tables")
            notebook.add(links_tab, text="Links")
            notebook.add(inspector_tab, text="DOM Inspector")
            notebook.add(builder_tab, text="Extraction Workspace")
        setattr(self, f"{prefix}_result_notebook", notebook)
        setattr(self, f"{prefix}_inspector_tab", inspector_tab)
        setattr(self, f"{prefix}_builder_tab", builder_tab)

        auto_bar = ttk.Frame(auto_tab, padding=8)
        auto_bar.pack(fill="x")
        ttk.Label(auto_bar, text="Detected dataset:").pack(side="left")
        choice = ttk.Combobox(auto_bar, state="readonly", width=55)
        choice.pack(side="left", padx=8)
        ttk.Button(
            auto_bar, text="Export CSV",
            command=lambda: self.export_detected(choice)
        ).pack(side="right")
        tree = self.make_tree(auto_tab)

        table_bar = ttk.Frame(table_tab, padding=8)
        table_bar.pack(fill="x")
        ttk.Label(table_bar, text="Detected table:").pack(side="left")
        table_choice = ttk.Combobox(table_bar, state="readonly", width=45)
        table_choice.pack(side="left", padx=8)
        ttk.Button(
            table_bar, text="Export CSV",
            command=lambda: self.export_table(table_choice)
        ).pack(side="right")
        table_tree = self.make_tree(table_tab)

        link_bar = ttk.Frame(links_tab, padding=8)
        link_bar.pack(fill="x")
        ttk.Button(link_bar, text="Export Links CSV", command=self.export_links).pack(side="right")
        links_tree = self.make_tree(links_tab)

        inspector_nb = ttk.Notebook(inspector_tab)
        inspector_nb.pack(fill="both", expand=True, padx=8, pady=8)
        catalog_tab = ttk.Frame(inspector_nb)
        raw_tab = ttk.Frame(inspector_nb)
        inspector_nb.add(catalog_tab, text="Element Catalog")
        inspector_nb.add(raw_tab, text="Raw Rendered DOM")

        catalog_tools = ttk.Frame(catalog_tab, padding=(6,6))
        catalog_tools.pack(fill="x")
        ttk.Label(catalog_tools, text="What data do you want to find?").pack(side="left")
        dom_search = ttk.Entry(catalog_tools, width=32)
        dom_search.pack(side="left", padx=(8,6), fill="x", expand=True)
        ttk.Label(catalog_tools, text="Example value (optional):").pack(side="left", padx=(4,2))
        dom_example = ttk.Entry(catalog_tools, width=28)
        dom_example.pack(side="left", padx=(2,6))
        ttk.Button(catalog_tools, text="Find Matching Elements", style="Accent.TButton", command=lambda: self.search_dom_catalog(prefix)).pack(side="left", padx=4)
        ttk.Button(catalog_tools, text="Show All Elements", command=lambda: self.clear_dom_catalog_search(prefix)).pack(side="left", padx=4)

        dom_hint = tk.StringVar(value="Fetch a page first. Describe what you want (email, company, profile link, image, PDF, next page) and optionally paste an example value visible on the site.")
        ttk.Label(catalog_tab, textvariable=dom_hint, wraplength=1250).pack(fill="x", padx=8, pady=(0,5))

        dom_tree = ttk.Treeview(catalog_tab, columns=("behavior","tag","sample","target","context","attributes","path"), show="headings", height=18)
        for col,title,width in (("behavior","Behavior / Type",165),("tag","Element",75),("sample","Real Sample Value",280),("target","Target / Action",300),("context","Section / Context",240),("attributes","Attributes",280),("path","DOM Path",360)):
            dom_tree.heading(col,text=title); dom_tree.column(col,width=width,minwidth=60,stretch=col in {"sample","context","attributes","path"})
        dom_tree.pack(fill="both", expand=True, padx=6, pady=(0,6))
        dom_actions=ttk.Frame(catalog_tab,padding=(6,0,6,6)); dom_actions.pack(fill="x")
        ttk.Button(dom_actions,text="Copy Selected DOM Path",command=lambda:self.copy_dom_catalog_path(prefix)).pack(side="left")
        ttk.Button(dom_actions,text="Find Related Elements",command=lambda:self.show_dom_related(prefix)).pack(side="left",padx=6)
        ttk.Button(dom_actions,text="Use Selected as Example",command=lambda:self.use_dom_sample_as_example(prefix)).pack(side="left",padx=6)
        ttk.Button(dom_actions,text="Follow Selected Link + Inspect Detail",style="Accent.TButton",command=lambda:self.follow_dom_catalog_link(prefix)).pack(side="left",padx=6)

        inspector = tk.Text(raw_tab, wrap="none")
        inspector.pack(fill="both", expand=True, padx=4, pady=4)

        dom_search.bind("<Return>", lambda e: self.search_dom_catalog(prefix))
        dom_example.bind("<Return>", lambda e: self.search_dom_catalog(prefix))

        # Extraction Builder uses a scrollable work surface so every control remains
        # reachable on laptop-height displays as the guided workflow grows.
        builder_canvas = tk.Canvas(builder_tab, highlightthickness=0, borderwidth=0)
        builder_vscroll = ttk.Scrollbar(builder_tab, orient="vertical", command=builder_canvas.yview)
        builder_canvas.configure(yscrollcommand=builder_vscroll.set)
        builder_canvas.pack(side="left", fill="both", expand=True)
        builder_vscroll.pack(side="right", fill="y")
        builder_content = ttk.Frame(builder_canvas)
        builder_window = builder_canvas.create_window((0, 0), window=builder_content, anchor="nw")

        def _builder_sync_scrollregion(_event=None):
            builder_canvas.configure(scrollregion=builder_canvas.bbox("all"))

        def _builder_sync_width(event):
            builder_canvas.itemconfigure(builder_window, width=max(event.width, 1))

        def _builder_mousewheel(event):
            delta = -1 if event.delta > 0 else 1
            builder_canvas.yview_scroll(delta * 3, "units")

        builder_content.bind("<Configure>", _builder_sync_scrollregion)
        builder_canvas.bind("<Configure>", _builder_sync_width)
        builder_canvas.bind("<Enter>", lambda _e: builder_canvas.bind_all("<MouseWheel>", _builder_mousewheel))
        builder_canvas.bind("<Leave>", lambda _e: builder_canvas.unbind_all("<MouseWheel>"))

        # v1.5.4 Simple Mode dataset selector.  This is the only dataset control
        # exposed in the normal workflow; the old technical dataset/mapping panels
        # are retained below in a hidden Advanced Controls container.
        # v1.5.4 — Universal Multi-Level Extraction Workspace.  Page levels are
        # presented as tabs instead of making ordinary users jump between DOM
        # Inspector, detail mappings and crawler settings.  Every level follows the
        # same interaction: inspect -> see real samples -> choose fields -> follow a
        # relationship to create the next page tab.
        level_box = ttk.LabelFrame(builder_content, text="Multi-Level Extraction Workspace", padding=8)
        level_box.pack(fill="x", padx=8, pady=(8,0))
        level_breadcrumb = tk.StringVar(value="Page 1 — Listing Page")
        ttk.Label(level_box, textvariable=level_breadcrumb, font=("Segoe UI",10,"bold")).pack(anchor="w", pady=(0,5))
        ttk.Label(
            level_box,
            text="Analyze each page, choose only the fields you want, then follow a relationship to create the next page. Double-click a row to include/exclude it.",
            wraplength=1250
        ).pack(anchor="w", pady=(0,6))

        dataset_bar = ttk.Frame(level_box)
        dataset_bar.pack(fill="x", pady=(0,7))
        ttk.Label(dataset_bar, text="Detected dataset:").pack(side="left")
        builder_dataset = ttk.Combobox(dataset_bar, state="readonly", width=58)
        builder_dataset.pack(side="left", fill="x", expand=True, padx=7)
        ttk.Button(dataset_bar, text="Load Dataset", command=lambda:self.builder_load_dataset(prefix)).pack(side="left")

        workspace_body = ttk.Panedwindow(level_box, orient="horizontal")
        workspace_body.pack(fill="both", expand=True)
        page_host = ttk.Frame(workspace_body)
        summary_host = ttk.LabelFrame(workspace_body, text="Live Selected-Field Summary", padding=6)
        workspace_body.add(page_host, weight=4)
        workspace_body.add(summary_host, weight=1)

        page_notebook = ttk.Notebook(page_host)
        page_notebook.pack(fill="both", expand=True)
        empty_page = ttk.Frame(page_notebook, padding=12)
        ttk.Label(empty_page, text="Load a detected dataset to create Page 1.", font=("Segoe UI",10,"bold")).pack(anchor="w")
        ttk.Label(empty_page, text="After Page 1 appears, choose the fields you want and follow a Link / Detail element to create Page 2.", wraplength=850).pack(anchor="w", pady=(5,0))
        page_notebook.add(empty_page, text="Page 1 — Waiting")

        workspace_actions = ttk.Frame(page_host)
        workspace_actions.pack(fill="x", pady=(6,0))
        ttk.Button(workspace_actions,text="◀ Previous Page",command=lambda:self.builder_workspace_move_page(prefix,-1)).pack(side="left")
        ttk.Button(workspace_actions,text="Next Page ▶",command=lambda:self.builder_workspace_move_page(prefix,1)).pack(side="left",padx=4)
        ttk.Button(workspace_actions,text="Include / Exclude",command=lambda:self.builder_workspace_toggle_row(prefix)).pack(side="left",padx=(12,4))
        ttk.Button(workspace_actions,text="Rename Field",command=lambda:self.builder_workspace_rename(prefix)).pack(side="left",padx=4)
        ttk.Button(workspace_actions,text="Recommended",command=lambda:self.builder_workspace_recommended(prefix)).pack(side="left",padx=4)
        ttk.Button(workspace_actions,text="Follow as Next Page",style="Accent.TButton",command=lambda:self.builder_workspace_follow(prefix)).pack(side="left",padx=(12,4))
        ttk.Button(workspace_actions,text="Run Extraction",style="Accent.TButton",command=lambda:self.builder_export(prefix)).pack(side="right")
        ttk.Button(workspace_actions,text="Preview Final Dataset",command=lambda:self.builder_guided_multilevel_preview(prefix,3)).pack(side="right",padx=4)

        summary_text = tk.Text(summary_host, height=13, width=34, wrap="word", state="disabled", borderwidth=0)
        summary_text.pack(fill="both", expand=True)
        ttk.Label(summary_host, text="Only included fields become final output columns. Updates are immediate.", wraplength=280).pack(anchor="w", pady=(5,0))
        ttk.Button(summary_host,text="Show Advanced Controls",command=lambda:self.toggle_builder_advanced(prefix)).pack(fill="x",pady=(7,0))

        # Retain the original level tree as an internal compatibility model.  The
        # visible workflow is now the page notebook above; existing functions can
        # continue addressing levels by numeric id without a crawler rewrite.
        level_tree = ttk.Treeview(level_box, columns=("level","page","relationship","status"), show="headings", height=1)
        for col,title,width in (("level","Level",75),("page","Page / Sample URL",520),("relationship","Reached By",240),("status","Status",150)):
            level_tree.heading(col,text=title); level_tree.column(col,width=width,minwidth=70,stretch=col in {"page","relationship"})
        # intentionally not packed

        # Technical controls are preserved for power users/regression diagnostics,
        # but hidden by default so Simple Mode has no duplicate/competing workflow.
        advanced_container = ttk.Frame(builder_content)
        setattr(self, f"{prefix}_builder_advanced_container", advanced_container)

        # Visual Extraction Builder: converts detected repeated web elements into
        # a user-defined export schema without changing the crawl engine.
        builder_top = ttk.LabelFrame(advanced_container, text="Advanced — Listing Dataset Controls", padding=10)
        builder_top.pack(fill="x", padx=8, pady=8)
        ttk.Label(builder_top, text="Use Fetch Rendered DOM for JavaScript-generated records. Load Fields now shows a real sample value beside every detected element.", wraplength=1100).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8,0))
        ttk.Label(builder_top, text="Simple Mode owns dataset selection. These controls are diagnostic only.").grid(row=0,column=0,columnspan=3,sticky="w")
        builder_top.columnconfigure(1, weight=1)

        sample_box = ttk.LabelFrame(advanced_container, text="Advanced — Listing Element Samples", padding=8)
        sample_box.pack(fill="x", padx=8, pady=(0,8))
        sample_tree = ttk.Treeview(sample_box, columns=("category","field","sample","coverage","confidence"), show="headings", height=5)
        sample_tree.heading("category", text="Type"); sample_tree.column("category", width=105, minwidth=80, stretch=False)
        sample_tree.heading("field", text="Element / Attribute"); sample_tree.column("field", width=470, minwidth=180)
        sample_tree.heading("sample", text="Typical Sample Values"); sample_tree.column("sample", width=520, minwidth=180)
        sample_tree.heading("coverage", text="Coverage"); sample_tree.column("coverage", width=85, minwidth=70, stretch=False)
        sample_tree.heading("confidence", text="Confidence"); sample_tree.column("confidence", width=90, minwidth=75, stretch=False)
        sample_tree.pack(fill="x")
        sample_tools = ttk.Frame(sample_box); sample_tools.pack(fill="x", pady=(6,0))
        ttk.Button(sample_tools, text="Add Recommended Fields", command=lambda: self.builder_add_recommended(prefix)).pack(side="left")
        ttk.Label(sample_tools, text="The engine classifies text, links, contacts, media, documents, dates, identifiers and prices using the actual sample values.", wraplength=1000).pack(side="left", padx=10)

        detail_box = ttk.LabelFrame(advanced_container, text="Advanced — Detail Inspector / Mapping", padding=8)
        detail_box.pack(fill="x", padx=8, pady=(0,8))
        ttk.Label(detail_box, text="URL field:").grid(row=0,column=0,sticky="w")
        detail_url_field = ttk.Combobox(detail_box, state="readonly", width=62)
        detail_url_field.grid(row=0,column=1,sticky="ew",padx=6)
        ttk.Button(detail_box,text="Follow First URL + Inspect",style="Accent.TButton",command=lambda: self.builder_follow_sample_url(prefix)).grid(row=0,column=2,padx=6)
        detail_status = tk.StringVar(value="Choose a detected href/url field. The app will open one sample detail page and list extractable elements with sample data.")
        ttk.Label(detail_box,textvariable=detail_status,wraplength=1200).grid(row=1,column=0,columnspan=3,sticky="w",pady=(6,4))
        detail_tree = ttk.Treeview(detail_box, columns=("category","field","sample","confidence"), show="headings", height=5)
        detail_tree.heading("category",text="Type"); detail_tree.column("category",width=105,minwidth=80,stretch=False)
        detail_tree.heading("field",text="Detail-Page Element / Attribute"); detail_tree.column("field",width=470,minwidth=180)
        detail_tree.heading("sample",text="Sample Value from Followed Page"); detail_tree.column("sample",width=520,minwidth=180)
        detail_tree.heading("confidence",text="Confidence"); detail_tree.column("confidence",width=90,minwidth=75,stretch=False)
        detail_tree.grid(row=2,column=0,columnspan=3,sticky="ew")
        detail_actions = ttk.Frame(detail_box); detail_actions.grid(row=3,column=0,columnspan=3,sticky="ew",pady=(6,0))
        ttk.Button(detail_actions,text="Add Selected Detail Field",command=lambda: self.builder_add_detail_mapping(prefix)).pack(side="left")
        ttk.Button(detail_actions,text="Add Recommended Detail Fields",command=lambda: self.builder_add_recommended_detail(prefix)).pack(side="left",padx=6)
        ttk.Button(detail_actions,text="Exclude Selected",command=lambda:self.builder_toggle_detail_mapping(prefix,False)).pack(side="left",padx=(12,4))
        ttk.Button(detail_actions,text="Keep Selected",command=lambda:self.builder_toggle_detail_mapping(prefix,True)).pack(side="left",padx=4)
        ttk.Button(detail_actions,text="Rename Output",command=lambda:self.builder_rename_detail_output(prefix)).pack(side="left",padx=4)
        ttk.Button(detail_actions,text="Preview Guided Multi-Level (3)",style="Accent.TButton",command=lambda: self.builder_guided_multilevel_preview(prefix,3)).pack(side="right")
        detail_map_tree = ttk.Treeview(detail_box,columns=("use","source","output","transform"),show="headings",height=3)
        detail_map_tree.heading("use",text="Use"); detail_map_tree.column("use",width=55,minwidth=45,stretch=False,anchor="center")
        detail_map_tree.heading("source",text="Selected Detail Source"); detail_map_tree.column("source",width=470,minwidth=180)
        detail_map_tree.heading("output",text="Output Field"); detail_map_tree.column("output",width=240,minwidth=120)
        detail_map_tree.heading("transform",text="Transformation"); detail_map_tree.column("transform",width=160,minwidth=120)
        detail_map_tree.grid(row=4,column=0,columnspan=3,sticky="ew",pady=(6,0))
        detail_box.columnconfigure(1,weight=1)

        mapping_box = ttk.LabelFrame(advanced_container, text="Advanced — Field Mapping", padding=10)
        mapping_box.pack(fill="x", padx=8, pady=(0,8))
        builder_source = ttk.Combobox(mapping_box, state="readonly", width=24)
        builder_output = tk.StringVar()
        builder_transform = ttk.Combobox(mapping_box, state="readonly", width=22,
            values=["None", "Person Name Split", "City, State Split", "Email / mailto Clean", "Text Clean"])
        builder_transform.set("None")
        ttk.Label(mapping_box, text="Source field:").grid(row=0,column=0,sticky="w")
        builder_source.grid(row=0,column=1,padx=6,sticky="ew")
        ttk.Label(mapping_box, text="Output field:").grid(row=0,column=2,sticky="w")
        ttk.Entry(mapping_box, textvariable=builder_output, width=24).grid(row=0,column=3,padx=6,sticky="ew")
        ttk.Label(mapping_box, text="Transform:").grid(row=0,column=4,sticky="w")
        builder_transform.grid(row=0,column=5,padx=6)
        ttk.Button(mapping_box, text="Add Mapping", style="Accent.TButton",
            command=lambda: self.builder_add_mapping(prefix)).grid(row=0,column=6,padx=(8,0))
        mapping_box.columnconfigure(1,weight=1); mapping_box.columnconfigure(3,weight=1)

        mapping_tree = ttk.Treeview(advanced_container, columns=("use","source","output","transform"), show="headings", height=4)
        for col, title, width in (("use","Use",55),("source","Source Field",220),("output","Output Field",220),("transform","Transformation",190)):
            mapping_tree.heading(col,text=title); mapping_tree.column(col,width=width,minwidth=45 if col=="use" else 100,stretch=col!="use",anchor="center" if col=="use" else "w")
        mapping_tree.pack(fill="x", padx=8)
        mapping_actions = ttk.Frame(advanced_container, padding=(8,6))
        mapping_actions.pack(fill="x")
        ttk.Button(mapping_actions,text="Keep Selected",command=lambda:self.builder_toggle_mapping(prefix,True)).pack(side="left")
        ttk.Button(mapping_actions,text="Exclude Selected",command=lambda:self.builder_toggle_mapping(prefix,False)).pack(side="left",padx=4)
        ttk.Button(mapping_actions,text="Select All",command=lambda:self.builder_set_all_mappings(prefix,True)).pack(side="left",padx=(12,4))
        ttk.Button(mapping_actions,text="Clear Selection",command=lambda:self.builder_set_all_mappings(prefix,False)).pack(side="left",padx=4)
        ttk.Button(mapping_actions,text="Recommended Only",command=lambda:self.builder_add_recommended(prefix)).pack(side="left",padx=4)
        ttk.Button(mapping_actions,text="Rename Output",command=lambda:self.builder_rename_output(prefix)).pack(side="left",padx=(12,4))
        ttk.Button(mapping_actions,text="Remove Selected",command=lambda: self.builder_remove_mapping(prefix)).pack(side="left",padx=4)
        ttk.Button(mapping_actions,text="Save Profile",command=lambda: self.builder_save_profile(prefix)).pack(side="right")
        ttk.Button(mapping_actions,text="Export Selected Data",command=lambda: self.builder_export(prefix)).pack(side="right",padx=6)
        ttk.Button(mapping_actions,text="Preview Selected Columns",style="Accent.TButton",command=lambda: self.builder_preview(prefix)).pack(side="right")
        builder_preview = self.make_tree(advanced_container)

        choice.bind("<<ComboboxSelected>>", lambda e: self.show_detected(choice, tree))
        table_choice.bind("<<ComboboxSelected>>", lambda e: self.show_table(table_choice, table_tree))

        setattr(self, f"{prefix}_group_choice", choice)
        setattr(self, f"{prefix}_group_tree", tree)
        setattr(self, f"{prefix}_table_choice", table_choice)
        setattr(self, f"{prefix}_table_tree", table_tree)
        setattr(self, f"{prefix}_links_tree", links_tree)
        setattr(self, f"{prefix}_inspector", inspector)
        setattr(self, f"{prefix}_dom_search", dom_search)
        setattr(self, f"{prefix}_dom_example", dom_example)
        setattr(self, f"{prefix}_dom_tree", dom_tree)
        setattr(self, f"{prefix}_dom_hint", dom_hint)
        setattr(self, f"{prefix}_builder_dataset", builder_dataset)
        setattr(self, f"{prefix}_builder_source", builder_source)
        setattr(self, f"{prefix}_builder_output", builder_output)
        setattr(self, f"{prefix}_builder_transform", builder_transform)
        setattr(self, f"{prefix}_builder_mapping_tree", mapping_tree)
        setattr(self, f"{prefix}_builder_preview", builder_preview)
        setattr(self, f"{prefix}_builder_sample_tree", sample_tree)
        setattr(self, f"{prefix}_builder_detail_url_field", detail_url_field)
        setattr(self, f"{prefix}_builder_detail_tree", detail_tree)
        setattr(self, f"{prefix}_builder_detail_status", detail_status)
        setattr(self, f"{prefix}_builder_detail_mapping_tree", detail_map_tree)
        setattr(self, f"{prefix}_builder_level_tree", level_tree)
        setattr(self, f"{prefix}_builder_level_breadcrumb", level_breadcrumb)
        setattr(self, f"{prefix}_builder_page_notebook", page_notebook)
        setattr(self, f"{prefix}_builder_summary_text", summary_text)
        page_notebook.bind("<<NotebookTabChanged>>", lambda e: self.builder_workspace_tab_changed(prefix))
        sample_tree.bind("<Double-1>", lambda e: self.builder_use_sample_field(prefix))
        detail_tree.bind("<Double-1>", lambda e: self.builder_use_detail_field(prefix))
        mapping_tree.bind("<Double-1>", lambda e: self.builder_toggle_mapping(prefix))
        detail_map_tree.bind("<Double-1>", lambda e: self.builder_toggle_detail_mapping(prefix))
        level_tree.bind("<<TreeviewSelect>>", lambda e: self.builder_show_selected_level(prefix))

    def _build_multilevel_tab(self):
        config = ttk.LabelFrame(self.multilevel_tab, text="List Page → Detail Pages", padding=10)
        config.pack(fill="x", padx=10, pady=10)

        self.multilevel_profile = tk.StringVar(value="Universal Auto-Detect")
        self.multilevel_url = tk.StringVar(value="")
        self.detail_link_pattern = tk.StringVar(value="")
        self.multilevel_delay = tk.StringVar(value="1.0")
        self.multilevel_max = tk.StringVar(value="0")
        self.download_images = tk.BooleanVar(value=True)
        self.download_documents = tk.BooleanVar(value=True)
        self.resume_crawl = tk.BooleanVar(value=True)
        self.image_output_dir = tk.StringVar(value=str(Path.cwd() / "Output" / "Images"))
        self.document_output_dir = tk.StringVar(value=str(Path.cwd() / "Output" / "Documents"))

        ttk.Label(config, text="Profile:").grid(row=0, column=0, sticky="w")
        self.multilevel_profile_box = ttk.Combobox(config, textvariable=self.multilevel_profile, values=["Universal Auto-Detect"], state="readonly", width=28)
        self.multilevel_profile_box.grid(row=0, column=1, sticky="w", padx=6)
        self.multilevel_profile_box.bind("<<ComboboxSelected>>", self._on_multilevel_profile_changed)
        ttk.Button(config, text="Refresh Profiles", command=self.refresh_custom_profiles).grid(row=0, column=2, sticky="w", padx=4)
        ttk.Label(config, text="Listing URL:").grid(row=1, column=0, sticky="w")
        ttk.Entry(config, textvariable=self.multilevel_url).grid(row=1, column=1, columnspan=5, sticky="ew", padx=6)
        ttk.Label(config, text="Detail-link contains:").grid(row=2, column=0, sticky="w")
        ttk.Entry(config, textvariable=self.detail_link_pattern).grid(row=2, column=1, columnspan=5, sticky="ew", padx=6)
        ttk.Label(config, text="Delay (seconds):").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(config, textvariable=self.multilevel_delay, width=10).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(config, text="Max detail pages (0 = all):").grid(row=3, column=2, sticky="w")
        ttk.Entry(config, textvariable=self.multilevel_max, width=10).grid(row=3, column=3, sticky="w", padx=6)
        ttk.Button(config, text="Start Multi-Level Crawl", command=self.start_multilevel_crawl).grid(row=3, column=4, padx=6)
        ttk.Button(config, text="Stop", command=self.stop_multilevel_crawl).grid(row=3, column=5)
        ttk.Checkbutton(config, text="Download property images", variable=self.download_images).grid(row=4, column=0, sticky="w", pady=6)
        ttk.Entry(config, textvariable=self.image_output_dir).grid(row=4, column=1, columnspan=4, sticky="ew", padx=6)
        ttk.Button(config, text="Browse…", command=self.choose_image_output_dir).grid(row=4, column=5)
        ttk.Checkbutton(config, text="Download documents", variable=self.download_documents).grid(row=5, column=0, sticky="w", pady=6)
        ttk.Entry(config, textvariable=self.document_output_dir).grid(row=5, column=1, columnspan=4, sticky="ew", padx=6)
        ttk.Button(config, text="Browse…", command=self.choose_document_output_dir).grid(row=5, column=5)
        ttk.Checkbutton(config, text="Resume interrupted crawl", variable=self.resume_crawl).grid(row=6, column=0, columnspan=2, sticky="w", pady=(2, 6))
        config.columnconfigure(1, weight=1)

        bar = ttk.Frame(self.multilevel_tab, padding=(10, 0, 10, 6))
        bar.pack(fill="x")
        self.multilevel_progress = tk.StringVar(value="Ready — universal relationship and semantic field discovery.")
        ttk.Label(bar, textvariable=self.multilevel_progress).pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="Export CSV", command=self.export_multilevel).pack(side="right")

        self.multilevel_tree = self.make_tree(self.multilevel_tab)
        self.configure_tree(self.multilevel_tree, UNIVERSAL_FIELDS)
        self.refresh_custom_profiles()

    def refresh_custom_profiles(self):
        self.custom_profiles = {}
        self.builder_profiles_dir.mkdir(parents=True, exist_ok=True)

        # Canonical persistent profile store.
        paths = list(self.builder_profiles_dir.glob("*.json"))
        # Compatibility: also discover profiles left in any legacy app folder.
        legacy_root = Path.cwd() / "Profiles"
        if legacy_root.exists():
            paths.extend(legacy_root.rglob("*.json"))

        for path in sorted(set(paths)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("profile_type") == "visual_extraction":
                    name = clean_text(payload.get("profile_name")) or path.stem.replace("_", " ")
                    payload["_path"] = str(path)
                    self.custom_profiles[name] = payload
            except Exception:
                continue

        # A project also carries an embedded copy of its extraction profile. This
        # means project loading remains functional even if a JSON file was moved.
        for project in getattr(self, "saved_projects", []):
            payload = project.get("profile_payload")
            if isinstance(payload, dict) and payload.get("profile_type") == "visual_extraction":
                name = clean_text(payload.get("profile_name")) or clean_text(project.get("profile"))
                if name:
                    self.custom_profiles[name] = dict(payload)

        values = ["Universal Auto-Detect"] + sorted(self.custom_profiles.keys(), key=str.lower)
        if hasattr(self, "multilevel_profile_box"):
            current = self.multilevel_profile.get()
            self.multilevel_profile_box["values"] = values
            if current not in values:
                self.multilevel_profile.set("Universal Auto-Detect")
        self.status_var.set(f"Profiles refreshed — {len(self.custom_profiles)} custom profile(s) available.") if hasattr(self, "status_var") else None
        return values

    def _selected_custom_profile(self):
        return self.custom_profiles.get(self.multilevel_profile.get())

    def _render_page_html(self, url, wait_ms=2500, scroll=True):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Browser Engine is not installed. Run Install_Browser_Engine.bat once, then restart the application.") from exc
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT, viewport={"width":1600,"height":1000})
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(wait_ms)
            if scroll:
                for _ in range(8):
                    page.evaluate("""() => { window.scrollTo(0, document.body.scrollHeight); for (const el of document.querySelectorAll('*')) { if (el.scrollHeight > el.clientHeight + 100) el.scrollTop = el.scrollHeight; } }""")
                    page.wait_for_timeout(350)
            html_text, final_url = page.content(), page.url
            browser.close()
        return html_text, final_url

    def _on_multilevel_profile_changed(self, _event=None):
        selected = self.multilevel_profile.get()
        custom = self._selected_custom_profile()
        if custom:
            self.multilevel_url.set(custom.get("source_url", self.multilevel_url.get()))
            self.detail_link_pattern.set("")
            headers = [m.get("output", "Field") for m in custom.get("mappings", [])]
            if custom.get("detail_link_field") and "Detail URL" not in headers: headers.append("Detail URL")
            for m in custom.get("detail_mappings", []):
                if m.get("output") not in headers: headers.append(m.get("output"))
            self.configure_tree(self.multilevel_tree, headers or UNIVERSAL_FIELDS)
            self.multilevel_progress.set("Ready — custom profile will discover its saved record group and follow its saved link field automatically.")
        else:
            self.detail_link_pattern.set("")
            self.configure_tree(self.multilevel_tree, UNIVERSAL_FIELDS)
            self.multilevel_progress.set("Ready — enter a listing URL. A link pattern is optional for Universal Auto-Detect.")

    def choose_image_output_dir(self):
        folder = filedialog.askdirectory(title="Choose image output folder")
        if folder:
            self.image_output_dir.set(folder)

    def choose_document_output_dir(self):
        folder = filedialog.askdirectory(title="Choose document output folder")
        if folder:
            self.document_output_dir.set(folder)

    @staticmethod
    def safe_folder_name(value):
        value = clean_text(value) or "property"
        value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
        return value[:100] or "property"

    def _download_property_images(self, image_urls, row):
        if not image_urls or not self.download_images.get():
            return ""
        root = Path(self.image_output_dir.get().strip() or (Path.cwd() / "Output" / "Images"))
        identity = row.get("Case #") or row.get("Property Address") or row.get("Page Title") or Path(urlparse(row.get("Detail URL", "")).path).name
        folder = root / self.safe_folder_name(identity)
        folder.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        for index, image_url in enumerate(image_urls, 1):
            if self.multilevel_stop.is_set():
                break
            try:
                req = urllib.request.Request(image_url, headers={"User-Agent": USER_AGENT, "Referer": row.get("Detail URL", "")})
                with self.session_opener.open(req, timeout=40) as response:
                    data = response.read()
                    content_type = response.headers.get_content_type()
                    ext = Path(urlparse(response.geturl()).path).suffix.lower()
                    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                        ext = mimetypes.guess_extension(content_type) or ".jpg"
                    (folder / f"{index:03d}{ext}").write_bytes(data)
                    downloaded += 1
            except Exception:
                continue
        return str(folder) if downloaded else ""

    def _download_documents(self, document_value, row):
        if not document_value or not self.download_documents.get():
            return ""
        root = Path(self.document_output_dir.get().strip() or (Path.cwd() / "Output" / "Documents"))
        identity = row.get("Case #") or row.get("Property Address") or row.get("Page Title") or "page"
        folder = root / self.safe_folder_name(identity)
        folder.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        for index, item in enumerate(document_value.split(" | "), 1):
            if ": http" not in item:
                continue
            label, url = item.split(": ", 1)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": row.get("Detail URL", "")})
                with self.session_opener.open(req, timeout=40) as response:
                    data = response.read()
                    ext = Path(urlparse(response.geturl()).path).suffix.lower()
                    if not ext:
                        ext = mimetypes.guess_extension(response.headers.get_content_type()) or ".pdf"
                    filename = f"{index:02d}_{self.safe_folder_name(label)}{ext}"
                    (folder / filename).write_bytes(data)
                    downloaded += 1
            except Exception:
                continue
        return str(folder) if downloaded else ""

    def _checkpoint_path(self):
        host = self.safe_folder_name(urlparse(self.multilevel_url.get().strip()).netloc or "crawl")
        return Path.cwd() / "Output" / f"{host}_crawl_checkpoint.json"

    def _save_checkpoint(self, listing_url, completed_urls):
        path = self._checkpoint_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"listing_url": listing_url, "completed_urls": sorted(completed_urls), "saved_at": time.time()}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_checkpoint(self, listing_url):
        path = self._checkpoint_path()
        if not self.resume_crawl.get() or not path.exists():
            return set()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("listing_url") == listing_url:
                return set(payload.get("completed_urls", []))
        except Exception:
            pass
        return set()

    def start_multilevel_crawl(self):
        url = self.multilevel_url.get().strip()
        if not self.valid_url(url):
            messagebox.showerror("Invalid URL", "Enter a complete listing URL.")
            return
        try:
            delay = max(0.0, float(self.multilevel_delay.get().strip() or "1"))
            max_pages = max(0, int(self.multilevel_max.get().strip() or "0"))
        except ValueError:
            messagebox.showerror("Invalid Setting", "Delay must be a number and maximum pages must be an integer.")
            return
        self.multilevel_stop.clear()
        self.multilevel_rows = []
        fields = UNIVERSAL_FIELDS
        self.configure_tree(self.multilevel_tree, fields)
        self.status_var.set("Starting multi-level crawl...")
        threading.Thread(target=self._multilevel_worker, args=(url, delay, max_pages), daemon=True).start()

    def stop_multilevel_crawl(self):
        self.multilevel_stop.set()
        self.multilevel_progress.set("Stopping after the current request...")

    def _fetch_text(self, url, timeout=40):
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with self.session_opener.open(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace"), response.geturl()

    def _multilevel_worker(self, listing_url, delay, max_pages):
        try:
            custom = self._selected_custom_profile()
            if custom:
                return self._custom_profile_worker(custom, listing_url, delay, max_pages)
            listing_html, final_url = self._fetch_text(listing_url)
            parser = DOMParser(final_url)
            parser.feed(listing_html)
            detail_urls = []
            seen = set()
            for item in parser.links:
                url = item.get("url", "")
                pattern = self.detail_link_pattern.get().strip()
                matches = (pattern in url) if pattern else (urlparse(url).netloc == urlparse(final_url).netloc and url != final_url and not url.startswith(("mailto:", "tel:", "javascript:")))
                if matches and url not in seen:
                    seen.add(url)
                    detail_urls.append(url)
            if max_pages:
                detail_urls = detail_urls[:max_pages]
            if not detail_urls:
                raise ValueError("No detail links were found. Set a distinctive value in Detail-link contains (for example /product/ or /property_details/).")

            completed_urls = self._load_checkpoint(listing_url)
            pending_urls = [u for u in detail_urls if u not in completed_urls]
            total = len(detail_urls)
            completed_before = len(completed_urls)
            start_time = time.time()
            images_total = 0
            documents_total = 0
            for run_index, detail_url in enumerate(pending_urls, 1):
                index = completed_before + run_index
                if self.multilevel_stop.is_set():
                    break
                elapsed = max(0.001, time.time() - start_time)
                rate = run_index / elapsed
                remaining = max(0, len(pending_urls) - run_index)
                eta = int(remaining / rate) if rate else 0
                self.after(0, lambda i=index, t=total, u=detail_url, e=eta, im=images_total, dc=documents_total: self.multilevel_progress.set(
                    f"Properties {i}/{t} | Images {im} | Documents {dc} | ETA {e//60:02d}:{e%60:02d} | {u}"
                ))
                try:
                    detail_html, resolved_url = self._fetch_text(detail_url)
                    row, image_urls = parse_universal_detail(detail_html, resolved_url)
                    row["Image Folder"] = self._download_property_images(image_urls, row)
                    row["Document Folder"] = self._download_documents(row.get("Documents", ""), row)
                    images_total += len(image_urls)
                    documents_total += len([x for x in row.get("Documents", "").split(" | ") if ": http" in x])
                    self.multilevel_rows.append(row)
                    completed_urls.add(detail_url)
                    self._save_checkpoint(listing_url, completed_urls)
                    self.after(0, lambda r=row: self.multilevel_tree.insert(
                        "", "end", values=[r.get(field, "") for field in UNIVERSAL_FIELDS]
                    ))
                except Exception as exc:
                    active_fields = UNIVERSAL_FIELDS
                    error_row = {field: "" for field in active_fields}
                    error_row["Detail URL"] = detail_url
                    error_key = "Short Description" if "Short Description" in error_row else "Detected Fields"
                    error_row[error_key] = f"ERROR: {exc}"
                    self.multilevel_rows.append(error_row)
                    self.after(0, lambda r=error_row: self.multilevel_tree.insert(
                        "", "end", values=[r.get(field, "") for field in UNIVERSAL_FIELDS]
                    ))
                if delay and run_index < len(pending_urls) and not self.multilevel_stop.is_set():
                    time.sleep(delay)

            stopped = self.multilevel_stop.is_set()
            count = len(self.multilevel_rows)
            if not stopped and len(completed_urls) >= total:
                try:
                    self._checkpoint_path().unlink(missing_ok=True)
                except Exception:
                    pass
            self.after(0, lambda: self.multilevel_progress.set(
                f"{'Stopped' if stopped else 'Complete'} — {len(completed_urls)}/{total} properties; {images_total} images; {documents_total} documents."
            ))
            self.after(0, lambda: self.status_var.set(
                f"Multi-level crawl {'stopped' if stopped else 'completed'} — {count} record(s)."
            ))
        except Exception as exc:
            self.after(0, lambda: self._show_error("Multi-Level Crawl Failed", str(exc)))

    def _custom_profile_worker(self, profile, listing_url, delay, max_pages):
        html_text, final_url = self._render_page_html(listing_url, wait_ms=3000, scroll=True)
        parser = DOMParser(final_url); parser.feed(html_text)
        groups = detect_repeated_groups(parser.root, final_url)
        target_name = profile.get("dataset_name", "")
        target = next((g for g in groups if g.get("name") == target_name), None)
        if target is None and groups:
            # Prefer a group containing the saved link field, then the largest group.
            link_field = profile.get("detail_link_field", "")
            target = next((g for g in groups if link_field in g.get("fields", [])), None)
            target = target or max(groups, key=lambda g: g.get("count", 0))
        if not target:
            raise ValueError("The saved repeating-record group was not found on the rendered listing page. Re-open Extraction Builder and refresh the profile.")
        link_field = profile.get("detail_link_field", "")
        records = target.get("records", [])
        if max_pages: records = records[:max_pages]
        if not link_field:
            raise ValueError("This custom profile has no saved detail-link field. In Extraction Builder choose an href/URL field, inspect a detail page, then save the profile again.")
        valid_records = [(r, str(r.get(link_field, "")).strip()) for r in records if self.valid_url(str(r.get(link_field, "")).strip())]
        if not valid_records:
            raise ValueError("The saved detail-link field did not contain usable URLs in the rendered listing records. Refresh the profile from Extraction Builder.")
        headers = [m.get("output", "Field") for m in profile.get("mappings", [])]
        if "Detail URL" not in headers: headers.append("Detail URL")
        for m in profile.get("detail_mappings", []):
            if m.get("output") not in headers: headers.append(m.get("output"))
        if profile.get("detail_mappings") and "Detail Fetch Status" not in headers:
            headers.append("Detail Fetch Status")
        self.after(0, lambda: self.configure_tree(self.multilevel_tree, headers))
        rows=[]; total=len(valid_records); start=time.time(); detail_fetches=0
        for idx,(record,url) in enumerate(valid_records,1):
            if self.multilevel_stop.is_set(): break
            row={}
            for m in profile.get("mappings", []):
                row[m.get("output","Field")] = self._apply_single_transform(record.get(m.get("source"), ""), m.get("transform","None"))
            row["Detail URL"] = url
            if profile.get("detail_mappings"):
                detail_html, detail_final = self._render_page_html(url, wait_ms=2200, scroll=True)
                detail_fetches += 1
                detail_values, detail_status = self._extract_detail_values_from_rendered_page(
                    detail_html, detail_final, profile.get("detail_mappings", [])
                )
                row.update(detail_values)
                row["Detail Fetch Status"] = detail_status
            rows.append(row)
            elapsed=max(.001,time.time()-start); eta=(elapsed/idx)*(total-idx)
            self.after(0, lambda i=idx,t=total,e=eta,u=url: self.multilevel_progress.set(f"Custom profile {i}/{t} | ETA {self.format_seconds(e)} | {u}"))
            self.after(0, lambda r=row,h=headers: self.multilevel_tree.insert("", "end", values=[r.get(x,"") for x in h]))
            if delay: time.sleep(delay)
        self.multilevel_rows = rows
        self.multilevel_headers = headers
        stopped = self.multilevel_stop.is_set()
        self.after(0, lambda: self.multilevel_progress.set(
            f"{'Stopped' if stopped else 'Complete'} — {len(rows)}/{total} records; {detail_fetches} detail page(s) followed; {len(profile.get('detail_mappings', []))} detail field(s) extracted."
        ))
        self.after(0, lambda: self.status_var.set(
            f"Custom multi-level crawl {'stopped' if stopped else 'completed'} — {len(rows)} record(s), {detail_fetches} linked detail page(s)."
        ))

    @staticmethod
    def _listing_identity_name(listing_context):
        listing_context=listing_context or {}
        preferred=("name","title","link text","link_text","headline","product name","member name")
        items=[(clean_text(str(k)).lower(),clean_text(str(v))) for k,v in listing_context.items()]
        for wanted in preferred:
            for key,val in items:
                if val and (key==wanted or wanted in key):
                    if not val.startswith(("http://","https://")):
                        return val
        return ""

    def _extract_detail_values_from_rendered_page(self, html_text, detail_url, mappings, listing_context=None):
        """Extract detail fields with context-aware semantic ranking.

        The previous implementation accepted an exact DOM-path value as soon as
        it existed. That allowed globally repeated header/footer values to win
        even when the profile/content area contained a better candidate. This
        version scores every semantic candidate, uses the saved path only as
        supporting evidence, and chooses the highest-quality value.
        """
        parser = DOMParser(detail_url)
        parser.feed(html_text)
        features = extract_universal_semantic_features(html_text, detail_url, node_features(parser.root, detail_url))
        candidates = []
        for source, raw in features.items():
            value = clean_text(str(raw))
            if not value or len(value) > 4000:
                continue
            category, confidence = self._classify_discovered_field(source, [value])
            low_source = source.lower(); low_value = value.lower()
            if any(k in low_source for k in ("company", "firm", "organization", "organisation", "employer")):
                category, confidence = "Company", "High"
            elif any(k in low_source for k in ("bio", "biography", "about", "description", "summary")) and len(value) >= 40:
                category, confidence = "Biography / Description", "Medium"
            elif any(d in low_value for d in ("linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com/", "youtube.com", "tiktok.com")):
                category, confidence = "Social Link", "High"
            elif category == "Link / Detail" and value.startswith(("http://", "https://")):
                if urlparse(value).netloc and urlparse(value).netloc != urlparse(detail_url).netloc:
                    category, confidence = "Website", "High"
            candidates.append({"source": source, "value": value, "type": category, "confidence": confidence})

        used_sources = set()
        output = {}
        matched = 0
        diagnostics = []
        aliases = {
            "email": "Email", "phone": "Phone", "company": "Company",
            "website": "Website", "social": "Social Link",
            "address": "Location", "location": "Location",
            "document": "Document", "image": "Image / Media",
            "bio": "Biography / Description", "description": "Biography / Description",
            "name": "Name / Title", "identifier": "Identifier",
        }
        for mapping in mappings:
            out_name = mapping.get("output", "Field")
            semantic = mapping.get("semantic_type", "")
            source_label=self._candidate_source_label(mapping.get("source",""))
            if (
                mapping.get("auto_selected")
                and mapping.get("structural_authority")
                and source_label
                and not self._label_value_semantic_compatible(
                    source_label,mapping.get("sample","")
                )
            ):
                output[out_name]=""
                diagnostics.append(f"{out_name}: incompatible label/value association suppressed")
                continue
            if (
                mapping.get("ownership")=="SHARED_CONTEXT"
                and mapping.get("auto_selected",True)
                and not mapping.get("manual_override")
            ):
                output[out_name]=""
                diagnostics.append(f"{out_name}: shared context suppressed")
                continue
            if not semantic:
                name_low = out_name.lower()
                semantic = next((typ for key, typ in aliases.items() if key in name_low), "")
            # SEMATTR001: synthetic record identity. Prefer an actual
            # detail-page title/name that agrees with Page 1. If the detail page
            # exposes no trustworthy name field, retain the verified Page-1 name.
            value = ""
            if mapping.get("synthetic_identity") or mapping.get("source")=="__record_identity_name__":
                chosen=self._best_detail_candidate(
                    candidates,"Name / Title",mapping,used_sources,
                    detail_url=detail_url,listing_context=listing_context
                )
                if chosen:
                    value=chosen["value"]
                    used_sources.add(chosen["source"])
                    diagnostics.append(f"{out_name}: {chosen['source']} [identity match]")
                else:
                    value=self._listing_identity_name(listing_context)
                    if value:
                        diagnostics.append(f"{out_name}: Page-1 identity fallback")
                if value:
                    matched += 1
                output[out_name]=self._apply_single_transform(
                    value,mapping.get("transform","None")
                )
                continue

            # The semantic meaning of an automatically generated Detail Page URL
            # is the page actually being visited, not the first anchor on it.
            if semantic=="Link / Detail" and out_name.lower() in {
                "detail page url","current page url","page url"
            }:
                value=detail_url
                matched += 1
                diagnostics.append(f"{out_name}: current detail page")
                output[out_name]=self._apply_single_transform(
                    value,mapping.get("transform","None")
                )
                continue

            # Prefer a context-ranked semantic candidate. Exact source is only
            # supporting evidence; Page-1 identity agreement can outrank shell UI.
            chosen = self._best_detail_candidate(
                candidates, semantic, mapping, used_sources,
                detail_url=detail_url, listing_context=listing_context
            )
            if chosen:
                value = chosen["value"]
                used_sources.add(chosen["source"])
                matched += 1
                diagnostics.append(f"{out_name}: {chosen['source']}")
            else:
                # Preserve custom/manual mappings for types the semantic engine
                # does not yet understand, but still require a non-empty value.
                source = mapping.get("source", "")
                raw = clean_text(str(features.get(source, "")))
                if raw:
                    value = raw
                    used_sources.add(source)
                    matched += 1
                    diagnostics.append(f"{out_name}: exact fallback")
            output[out_name] = self._apply_single_transform(value, mapping.get("transform", "None"))
        status = f"Followed; {matched}/{len(mappings)} detail fields matched"
        if diagnostics:
            status += "; context-ranked"
        return output, status

    def export_multilevel(self):
        if not self.multilevel_rows:
            messagebox.showinfo("No Data", "Run the multi-level crawl before exporting.")
            return
        custom = self._selected_custom_profile()
        if custom and getattr(self, "multilevel_headers", None):
            fields = self.multilevel_headers
            default_name = f"{self.safe_folder_name(custom.get('profile_name','custom_profile'))}_multilevel_export.csv"
        else:
            fields = UNIVERSAL_FIELDS
            default_name = "universal_multilevel_export.csv"
        rows = [[record.get(field, "") for field in fields] for record in self.multilevel_rows]
        self.save_csv(fields, rows, default_name)

    def _build_api_tab(self):
        request_box = ttk.LabelFrame(self.api_tab, text="API Request", padding=10)
        request_box.pack(fill="x", padx=10, pady=10)

        self.api_method = tk.StringVar(value="GET")
        self.api_url = tk.StringVar()
        self.api_auth = tk.StringVar(value="None")
        self.api_token = tk.StringVar()
        self.api_key_name = tk.StringVar(value="X-API-Key")
        self.api_headers_json = tk.StringVar(value="{}")
        self.api_params_json = tk.StringVar(value="{}")
        self.api_body_json = tk.StringVar(value="{}")
        self.api_record_path = tk.StringVar()

        ttk.Label(request_box, text="Method:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            request_box, textvariable=self.api_method,
            values=["GET", "POST", "PUT", "PATCH"],
            state="readonly", width=10
        ).grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(request_box, text="Endpoint URL:").grid(row=1, column=0, sticky="w")
        ttk.Entry(request_box, textvariable=self.api_url).grid(
            row=1, column=1, columnspan=5, sticky="ew", padx=6
        )

        ttk.Label(request_box, text="Authentication:").grid(row=2, column=0, sticky="w")
        ttk.Combobox(
            request_box, textvariable=self.api_auth,
            values=["None", "Bearer Token", "API Key Header", "Basic Auth"],
            state="readonly", width=18
        ).grid(row=2, column=1, sticky="w", padx=6)

        ttk.Label(request_box, text="Token / Username:").grid(row=2, column=2, sticky="e")
        ttk.Entry(request_box, textvariable=self.api_token).grid(
            row=2, column=3, sticky="ew", padx=6
        )
        ttk.Label(request_box, text="API key header / Password:").grid(row=2, column=4, sticky="e")
        ttk.Entry(request_box, textvariable=self.api_key_name).grid(
            row=2, column=5, sticky="ew", padx=6
        )

        api_fields = [
            ("Query parameters JSON:", self.api_params_json),
            ("Headers JSON:", self.api_headers_json),
            ("JSON request body:", self.api_body_json),
            ("Record path:", self.api_record_path),
        ]

        for row, (label, variable) in enumerate(api_fields, 3):
            ttk.Label(request_box, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(request_box, textvariable=variable).grid(
                row=row, column=1, columnspan=5, sticky="ew", padx=6, pady=3
            )

        ttk.Button(request_box, text="Send API Request", command=self.send_api_request).grid(
            row=7, column=5, sticky="e", pady=(8, 0)
        )

        for col in range(1, 6):
            request_box.columnconfigure(col, weight=1)

        api_tabs = ttk.Notebook(self.api_tab)
        api_tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        preview_tab = ttk.Frame(api_tabs)
        raw_tab = ttk.Frame(api_tabs)
        api_tabs.add(preview_tab, text="Records Preview")
        api_tabs.add(raw_tab, text="Raw JSON")

        preview_bar = ttk.Frame(preview_tab, padding=8)
        preview_bar.pack(fill="x")
        self.api_array_choice = ttk.Combobox(preview_bar, state="readonly", width=55)
        self.api_array_choice.pack(side="left")
        self.api_array_choice.bind("<<ComboboxSelected>>", lambda e: self.use_detected_api_path())
        ttk.Button(preview_bar, text="Export CSV", command=self.export_api).pack(side="right")

        self.api_tree = self.make_tree(preview_tab)
        self.api_raw = tk.Text(raw_tab, wrap="none")
        self.api_raw.pack(fill="both", expand=True, padx=8, pady=8)

    def make_tree(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        tree = ttk.Treeview(frame, show="headings")
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)

        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    def _refresh_dom_catalog_tree(self, prefix, query="", example=""):
        tree=getattr(self,f"{prefix}_dom_tree",None)
        hint=getattr(self,f"{prefix}_dom_hint",None)
        if tree is None:
            return
        self.clear_tree(tree)
        catalog=self.dom_catalogs.get(prefix,[])
        ranked=[]
        for item in catalog:
            score=dom_search_score(item,query,example)
            if score:
                ranked.append((score,item))
        ranked.sort(key=lambda x:(x[0], 1 if x[1].get("sample") else 0), reverse=True)
        visible=[item for _,item in ranked[:1500]]
        self.dom_catalog_filtered[prefix]=visible
        for i,item in enumerate(visible):
            tree.insert("","end",iid=str(i),values=(item.get("behavior",""),item.get("tag",""),item.get("sample",""),item.get("target",""),item.get("context",""),item.get("attributes",""),item.get("path","")))
        if hint is not None:
            if query:
                hint.set(f'Found {len(visible)} matching element(s) for "{query or "any target"}" from {len(catalog)} rendered DOM elements. Example filter: "{example}". Results are ranked by behavior, real sample, executable target, attributes, path, and nearby context.')
            else:
                hint.set(f"Cataloged {len(catalog)} rendered DOM elements. Search by what you want (for example: email, company, profile link, PDF, image, next page) or by a sample value you can see on the website.")

    def search_dom_catalog(self,prefix="public"):
        entry=getattr(self,f"{prefix}_dom_search",None)
        query=entry.get().strip() if entry is not None else ""
        ex_entry=getattr(self,f"{prefix}_dom_example",None)
        example=ex_entry.get().strip() if ex_entry is not None else ""
        self._refresh_dom_catalog_tree(prefix,query,example)
        self.status_var.set(f'DOM Inspector search complete — {len(self.dom_catalog_filtered.get(prefix,[]))} matching element(s).')

    def clear_dom_catalog_search(self,prefix="public"):
        entry=getattr(self,f"{prefix}_dom_search",None)
        if entry is not None:
            entry.delete(0,"end")
        ex_entry=getattr(self,f"{prefix}_dom_example",None)
        if ex_entry is not None:
            ex_entry.delete(0,"end")
        self._refresh_dom_catalog_tree(prefix,"","")
        self.status_var.set(f'DOM Inspector showing all {len(self.dom_catalogs.get(prefix,[]))} rendered elements.')

    def _selected_dom_catalog_item(self,prefix):
        tree=getattr(self,f"{prefix}_dom_tree",None)
        if tree is None or not tree.selection():
            return None
        try:
            idx=int(tree.selection()[0])
            return self.dom_catalog_filtered.get(prefix,[])[idx]
        except Exception:
            return None

    def copy_dom_catalog_path(self,prefix="public"):
        item=self._selected_dom_catalog_item(prefix)
        if not item:
            messagebox.showinfo("Select Element","Select an element in the DOM catalog first."); return
        self.clipboard_clear(); self.clipboard_append(item.get("path","")); self.update()
        self.status_var.set(f'Copied DOM path: {item.get("path","")}')

    def use_dom_sample_as_example(self,prefix="public"):
        item=self._selected_dom_catalog_item(prefix)
        if not item:
            messagebox.showinfo("Select Element","Select an element in the DOM catalog first."); return
        ex=getattr(self,f"{prefix}_dom_example",None)
        if ex is not None:
            ex.delete(0,"end")
            ex.insert(0,item.get("sample","") or item.get("target",""))
        self.search_dom_catalog(prefix)

    def show_dom_related(self,prefix="public"):
        """Show elements in the same local DOM section as the selected result."""
        item=self._selected_dom_catalog_item(prefix)
        if not item:
            messagebox.showinfo("Select Element","Select an element in the DOM catalog first."); return
        path=item.get("path","")
        parts=[p.strip() for p in path.split(" > ") if p.strip()]
        parent=" > ".join(parts[:-1]) if len(parts)>1 else path
        catalog=self.dom_catalogs.get(prefix,[])
        related=[]
        for candidate in catalog:
            cpath=candidate.get("path","")
            if parent and (cpath.startswith(parent+" > ") or cpath==parent):
                related.append(candidate)
        if len(related)<2:
            ctx=item.get("context","")
            related=[c for c in catalog if ctx and c.get("context","")==ctx]
        self.dom_catalog_filtered[prefix]=related[:1000]
        tree=getattr(self,f"{prefix}_dom_tree",None)
        self.clear_tree(tree)
        for i,c in enumerate(self.dom_catalog_filtered[prefix]):
            tree.insert("","end",iid=str(i),values=(c.get("behavior",""),c.get("tag",""),c.get("sample",""),c.get("target",""),c.get("context",""),c.get("attributes",""),c.get("path","")))
        hint=getattr(self,f"{prefix}_dom_hint",None)
        if hint is not None:
            hint.set(f"Showing {len(self.dom_catalog_filtered[prefix])} element(s) from the same local DOM section. This helps identify companion fields around the selected value/link without manually reading the HTML tree.")
        self.status_var.set(f"DOM Inspector related-element view — {len(self.dom_catalog_filtered[prefix])} element(s).")

    def follow_dom_catalog_link(self,prefix="public"):
        item=self._selected_dom_catalog_item(prefix)
        if not item:
            messagebox.showinfo("Select Link","Select a link/relationship element first."); return
        attrs=item.get("attributes","")
        candidates=[]
        if self.valid_url(item.get("target","")):
            candidates.append(item.get("target",""))
        for m in re.finditer(r'(?:href|src|data-src|data-original)=(https?://[^| ]+)',attrs,re.I):
            candidates.append(m.group(1).strip())
        if self.valid_url(item.get("sample","")):
            candidates.insert(0,item.get("sample",""))
        url=next((u for u in candidates if self.valid_url(u)),"")
        if not url:
            messagebox.showinfo("Not Followable","The selected element does not expose a complete HTTP/HTTPS navigation target. Choose an element classified as Relationship / Link or a similar navigable type."); return
        self.builder_detail_link_field=item.get("path","")
        getattr(self,f"{prefix}_builder_detail_status").set(f"Opening DOM Inspector-selected link: {url}")
        self.status_var.set("Following selected DOM relationship and inspecting the rendered detail page...")
        threading.Thread(target=self._builder_detail_worker,args=(url,prefix,True),daemon=True).start()

    @staticmethod
    def clear_tree(tree):
        for item in tree.get_children():
            tree.delete(item)

    def configure_tree(self, tree, headers):
        self.clear_tree(tree)
        columns = [f"c{i}" for i in range(len(headers))]
        tree["columns"] = columns
        for index, header in enumerate(headers):
            column = columns[index]
            tree.heading(column, text=header)
            width = 180
            if "url" in header.lower() or "description" in header.lower():
                width = 380
            tree.column(column, width=width, minwidth=80)

    def _expansion_status(self, message):
        try:
            self.after(0, lambda m=message: self.status_var.set(m))
        except Exception:
            pass

    @staticmethod
    def _rendered_metrics(page):
        return page.evaluate("""
            () => {
                const hrefs = new Set();
                for (const a of document.querySelectorAll('a[href]')) {
                    try { hrefs.add(new URL(a.href, location.href).href); } catch(e) {}
                }
                return {
                    hrefs: hrefs.size,
                    height: Math.max(
                        document.body ? document.body.scrollHeight : 0,
                        document.documentElement ? document.documentElement.scrollHeight : 0
                    ),
                    elements: document.querySelectorAll('*').length
                };
            }
        """)

    @staticmethod
    def _click_result_expander(page):
        """Click one visible same-page result-expansion control, if found."""
        return page.evaluate("""
            () => {
                const exact = /^(load\\s*more|show\\s*more|view\\s*more|more\\s*results|see\\s*more|show\\s*all|view\\s*all)$/i;
                const candidates = [...document.querySelectorAll(
                    'button, a, [role="button"], input[type="button"], input[type="submit"]'
                )];
                for (const el of candidates) {
                    const style = getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    if (style.display === 'none' || style.visibility === 'hidden' ||
                        r.width < 2 || r.height < 2 || el.disabled) continue;
                    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                    if (!exact.test(text)) continue;
                    // "View all" may be a normal navigation link; only click it when
                    // it does not leave the current pathname. Dedicated pagination is
                    // handled separately so we can preserve every page snapshot.
                    if (el.tagName === 'A' && el.href) {
                        try {
                            const u = new URL(el.href, location.href);
                            if (u.origin !== location.origin) continue;
                            if (/view\\s*all/i.test(text) && u.pathname !== location.pathname) continue;
                        } catch(e) { continue; }
                    }
                    el.scrollIntoView({block:'center'});
                    el.click();
                    return {clicked:true, text:text};
                }
                return {clicked:false, text:''};
            }
        """)

    @staticmethod
    def _find_next_page_url(page):
        """Return a conservative same-origin Next-page URL or empty string."""
        return page.evaluate("""
            () => {
                const current = new URL(location.href);
                const rel = document.querySelector('a[rel~="next"][href]');
                const links = rel ? [rel] : [...document.querySelectorAll('a[href]')];
                const nextRx = /^(next|next\\s+page|older|more\\s+results|›|»|→)$/i;
                for (const a of links) {
                    const r = a.getBoundingClientRect(), s=getComputedStyle(a);
                    if (s.display==='none' || s.visibility==='hidden' || r.width<2 || r.height<2) continue;
                    const label = (a.innerText || a.getAttribute('aria-label') || a.title || '').trim();
                    if (!rel && !nextRx.test(label)) continue;
                    try {
                        const u = new URL(a.href, location.href);
                        if (u.origin !== current.origin || u.href === current.href) continue;
                        return u.href;
                    } catch(e) {}
                }
                return '';
            }
        """)

    @staticmethod
    def _apply_search_seed(page, seed):
        """Generic bounded search / interaction executor.

        Uses visible-control semantics rather than site-specific selectors.  It
        supports normal search boxes, location/autocomplete widgets, comboboxes,
        native selects, suggestion lists, and nearby Search/Find/Apply controls.
        The routine is intentionally bounded so a hostile or unusual page cannot
        trap the UI in an interaction loop.
        """
        seed = clean_text(seed)
        if not seed:
            return {"applied": False, "reason": "no seed", "field": "", "action": "", "changed": False}

        before = page.evaluate("""() => ({
            url: location.href,
            text: (document.body && document.body.innerText || '').length,
            html: document.documentElement.outerHTML.length,
            links: document.querySelectorAll('a[href]').length,
            rows: document.querySelectorAll('article,li,tr,[role="option"],[role="row"]').length
        })""")

        result = page.evaluate(r"""
            (seed) => {
                const visible = (el) => {
                    if (!el) return false;
                    const s=getComputedStyle(el), r=el.getBoundingClientRect();
                    return s.display!=='none' && s.visibility!=='hidden' && r.width>=4 && r.height>=4 && !el.disabled;
                };
                const txt = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').trim();
                const positive = /(search|location|postcode|postal|zip|town|city|address|place|area|near|query|keyword|where)/i;
                const negative = /(password|passcode|username|login|email|phone|telephone|newsletter|coupon|promo|name\b)/i;
                const fields=[...document.querySelectorAll(
                    'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), textarea, [contenteditable="true"], [role="combobox"]'
                )].filter(visible).map((el,idx) => {
                    const label = el.labels && el.labels.length ? [...el.labels].map(x=>x.innerText||'').join(' ') : '';
                    const meta=[el.type,el.name,el.id,el.placeholder,el.getAttribute('aria-label'),el.getAttribute('role'),el.getAttribute('autocomplete'),label].filter(Boolean).join(' ');
                    let score=0;
                    if ((el.type||'').toLowerCase()==='search') score+=100;
                    if ((el.getAttribute('role')||'').toLowerCase()==='combobox') score+=55;
                    if (positive.test(meta)) score+=70;
                    if (/location|postcode|postal|zip|town|city|place|area|near|where/i.test(meta)) score+=35;
                    if (negative.test(meta)) score-=140;
                    if ((el.type||'').toLowerCase()==='text' || !el.type) score+=10;
                    return {el,idx,meta,score};
                }).sort((a,b)=>b.score-a.score);

                // Native select fallback for location/category-driven directories.
                const selects=[...document.querySelectorAll('select')].filter(visible).map(el=>{
                    const label=el.labels&&el.labels.length?[...el.labels].map(x=>x.innerText||'').join(' '):'';
                    const meta=[el.name,el.id,el.getAttribute('aria-label'),label].filter(Boolean).join(' ');
                    let score=positive.test(meta)?80:0;
                    if (negative.test(meta)) score-=120;
                    return {el,meta,score};
                }).sort((a,b)=>b.score-a.score);

                if ((!fields.length || fields[0].score < 10) && (!selects.length || selects[0].score < 10))
                    return {applied:false,reason:'no likely search control',field:'',action:'',phase:'detect'};

                if (selects.length && selects[0].score > (fields[0]?.score || -999)) {
                    const chosen=selects[0], el=chosen.el;
                    const opts=[...el.options];
                    const exact=opts.find(o=>(o.text||'').trim().toLowerCase()===seed.toLowerCase());
                    const partial=opts.find(o=>(o.text||'').toLowerCase().includes(seed.toLowerCase()) || seed.toLowerCase().includes((o.text||'').toLowerCase()));
                    const opt=exact||partial;
                    if (!opt) return {applied:false,reason:'no matching select option',field:chosen.meta.slice(0,180),action:'',phase:'select'};
                    el.value=opt.value; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));
                    return {applied:true,reason:'',field:chosen.meta.slice(0,180),action:'select option',phase:'select'};
                }

                const chosen=fields[0], el=chosen.el;
                el.scrollIntoView({block:'center'}); el.focus();
                try {
                    if (el instanceof HTMLInputElement) {
                        const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')?.set;
                        if (setter) setter.call(el,seed); else el.value=seed;
                    } else if (el instanceof HTMLTextAreaElement) {
                        const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')?.set;
                        if (setter) setter.call(el,seed); else el.value=seed;
                    } else if (el.isContentEditable) el.textContent=seed;
                    else el.value=seed;
                } catch(e) { try { el.value=seed; } catch(_) {} }
                for (const ev of ['input','change','keyup']) el.dispatchEvent(new Event(ev,{bubbles:true}));
                return {applied:true,reason:'',field:chosen.meta.slice(0,180),action:'filled',phase:'fill'};
            }
        """, seed)
        if not result.get("applied"):
            return result

        # Give reactive/autocomplete widgets time to materialize suggestions.
        page.wait_for_timeout(900)
        action = page.evaluate(r"""
            (seed) => {
                const visible=(el)=>{const s=getComputedStyle(el),r=el.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>=4&&r.height>=4&&!el.disabled};
                const norm=(x)=>(x||'').replace(/\s+/g,' ').trim().toLowerCase();
                const seedN=norm(seed);
                // Prefer a visible autocomplete/listbox suggestion matching the seed.
                const suggestions=[...document.querySelectorAll('[role="option"], [role="listbox"] li, .autocomplete li, .suggestions li, .typeahead li, .pac-item')]
                    .filter(visible).map(el=>({el,t:norm(el.innerText||el.textContent)}))
                    .filter(x=>x.t && (x.t.includes(seedN) || seedN.includes(x.t)));
                if (suggestions.length) { suggestions[0].el.click(); return {action:'autocomplete selection',phase:'suggestion'}; }

                const focused=document.activeElement;
                const form=focused && focused.closest ? focused.closest('form') : null;
                const scope=form || document;
                const buttons=[...scope.querySelectorAll('button,input[type="submit"],input[type="button"],[role="button"]')].filter(visible);
                const ranked=buttons.map(el=>{
                    const t=(el.innerText||el.value||el.getAttribute('aria-label')||el.title||'').trim();
                    let score=0;
                    if (/^(search|find|go|submit|apply)$/i.test(t)) score+=100;
                    if (/(search|find|show results|view results|apply)/i.test(t)) score+=70;
                    return {el,t,score};
                }).sort((a,b)=>b.score-a.score);
                if (ranked.length && ranked[0].score>0) { ranked[0].el.click(); return {action:ranked[0].t||'button click',phase:'button'}; }
                if (form && typeof form.requestSubmit==='function') { form.requestSubmit(); return {action:'form submit',phase:'form'}; }
                if (focused) {
                    for (const type of ['keydown','keypress','keyup']) focused.dispatchEvent(new KeyboardEvent(type,{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
                    return {action:'Enter',phase:'enter'};
                }
                return {action:'value change only',phase:'change'};
            }
        """, seed)
        result.update(action or {})

        # Bounded observation window: network-idle is helpful but never mandatory.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(900)

        after = page.evaluate("""() => ({
            url: location.href,
            text: (document.body && document.body.innerText || '').length,
            html: document.documentElement.outerHTML.length,
            links: document.querySelectorAll('a[href]').length,
            rows: document.querySelectorAll('article,li,tr,[role="option"],[role="row"]').length
        })""")
        changed = (
            before.get("url") != after.get("url") or
            abs(after.get("html",0)-before.get("html",0)) > 300 or
            abs(after.get("text",0)-before.get("text",0)) > 80 or
            after.get("links",0) != before.get("links",0) or
            after.get("rows",0) != before.get("rows",0)
        )
        result["changed"] = bool(changed)
        result["before"] = before
        result["after"] = after
        return result

    def _expand_same_page_results(self, page, remaining_actions):
        """Expand Load More and infinite-scroll results without leaving the page."""
        actions = 0
        growth_events = 0
        stagnant = 0
        last = self._rendered_metrics(page)

        while actions < remaining_actions and stagnant < 3:
            clicked = self._click_result_expander(page)
            if clicked.get("clicked"):
                actions += 1
                self._expansion_status(
                    f"Expanding results — action {actions}: {clicked.get('text','Load More')}..."
                )
                page.wait_for_timeout(900)
            else:
                # Give infinite-scroll pages an opportunity to append records.
                page.evaluate("""
                    () => {
                        window.scrollTo(0, document.body.scrollHeight);
                        for (const el of document.querySelectorAll('*')) {
                            if (el.scrollHeight > el.clientHeight + 120) {
                                el.scrollTop = el.scrollHeight;
                            }
                        }
                    }
                """)
                page.wait_for_timeout(900)
                actions += 1

            now = self._rendered_metrics(page)
            grew = (
                now["hrefs"] > last["hrefs"] or
                now["height"] > last["height"] + 50 or
                now["elements"] > last["elements"] + 5
            )
            if grew:
                growth_events += 1
                stagnant = 0
                last = now
            else:
                stagnant += 1

        page.evaluate("window.scrollTo(0,0)")
        return actions, growth_events, last

    def _render_and_expand_snapshots(self, page, start_url, max_actions):
        """Return rendered HTML snapshots after universal result expansion.

        Same-page expansion is exhausted first. Conservative same-origin Next
        traversal then captures additional pages while preserving earlier pages.
        """
        snapshots = []
        visited = set()
        actions_used = 0
        page_no = 0

        while actions_used < max_actions:
            page_no += 1
            same_used, growth, metrics = self._expand_same_page_results(
                page, max(1, max_actions - actions_used)
            )
            actions_used += same_used
            current_url = page.url
            snapshots.append({
                "url": current_url,
                "html": page.content(),
                "metrics": metrics,
                "growth_events": growth,
            })
            visited.add(current_url)
            self._expansion_status(
                f"Expansion captured page {page_no}: {metrics['hrefs']} unique links; "
                f"{actions_used}/{max_actions} actions used."
            )

            if actions_used >= max_actions:
                break
            next_url = self._find_next_page_url(page)
            if not next_url or next_url in visited:
                break

            actions_used += 1
            self._expansion_status(
                f"Following result page {page_no + 1} — {actions_used}/{max_actions} expansion actions..."
            )
            try:
                page.goto(next_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1200)
            except Exception:
                break

        return snapshots, actions_used

    def start_rendered_fetch(self, url, prefix="public"):
        """Fetch the browser-rendered DOM using optional Playwright support."""
        if not self.valid_url(url):
            messagebox.showerror("Invalid URL", "Enter a complete http:// or https:// URL.")
            return
        self.status_var.set("Launching browser engine and rendering page...")
        threading.Thread(target=self._rendered_fetch_worker, args=(url, prefix), daemon=True).start()

    def _rendered_fetch_worker(self, url, prefix):
        try:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "Browser Engine is not installed. Run Install_Browser_Engine.bat once, "
                    "then restart the application."
                ) from exc

            expand_enabled = True
            max_actions = 100
            if prefix == "public":
                try:
                    expand_enabled = bool(self.public_expand_results.get())
                    max_actions = max(1, min(500, int(self.public_expand_limit.get() or "100")))
                except Exception:
                    expand_enabled, max_actions = True, 100

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(user_agent=USER_AGENT, viewport={"width":1600,"height":1000})

                # RELINT001HF002: observe structured network responses generated
                # naturally by the page and by the optional user search seed.
                # We do not guess, synthesize, or hard-code any endpoint.
                dynamic_responses=[]
                dynamic_bytes=[0]
                dynamic_seen=set()
                def capture_dynamic_response(resp):
                    try:
                        if len(dynamic_responses) >= 80 or dynamic_bytes[0] >= 12_000_000:
                            return
                        rtype=(resp.request.resource_type or "").lower()
                        headers={str(k).lower():str(v) for k,v in (resp.headers or {}).items()}
                        ctype=headers.get("content-type","").lower()
                        endpoint=resp.url
                        likely_json=("json" in ctype or endpoint.lower().split("?",1)[0].endswith(".json"))
                        if rtype not in {"xhr","fetch"} and not likely_json:
                            return
                        body=resp.body()
                        if not body or len(body) > 3_000_000:
                            return
                        sample=body.lstrip()[:1]
                        if not likely_json and sample not in (b"{",b"["):
                            return
                        # Validate JSON before retaining the response.
                        obj=json.loads(body.decode("utf-8",errors="replace"))
                        fp=(endpoint, len(body), body[:240])
                        if fp in dynamic_seen:
                            return
                        dynamic_seen.add(fp)
                        dynamic_responses.append({"url":endpoint,"payload":obj,"resource_type":rtype,"content_type":ctype})
                        dynamic_bytes[0]+=len(body)
                    except Exception:
                        return
                page.on("response", capture_dynamic_response)

                response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                status = response.status if response else 200

                search_seed = ""
                if prefix == "public" and hasattr(self, "public_search_seed"):
                    try:
                        search_seed = self.public_search_seed.get().strip()
                    except Exception:
                        search_seed = ""
                if search_seed:
                    self._expansion_status(f"Applying search/location seed: {search_seed}...")
                    seed_result = self._apply_search_seed(page, search_seed)
                    if seed_result.get("applied"):
                        self._expansion_status(
                            f"Search interaction via {seed_result.get('action','search')} — page change: {'detected' if seed_result.get('changed') else 'not detected'}; analyzing results..."
                        )
                    else:
                        self._expansion_status(
                            f"Search seed was not applied automatically ({seed_result.get('reason','no matching field')}); continuing with normal analysis."
                        )

                if expand_enabled:
                    self._expansion_status(
                        f"Rendered page ready — expanding accessible results (max {max_actions} actions)..."
                    )
                    snapshots, actions_used = self._render_and_expand_snapshots(
                        page, url, max_actions
                    )
                else:
                    # Retain the proven lazy-render trigger when explicit expansion is disabled.
                    for _ in range(8):
                        page.evaluate("""
                            () => {
                                window.scrollTo(0, document.body.scrollHeight);
                                for (const el of document.querySelectorAll('*')) {
                                    if (el.scrollHeight > el.clientHeight + 100) el.scrollTop = el.scrollHeight;
                                }
                            }
                        """)
                        page.wait_for_timeout(450)
                    page.evaluate("window.scrollTo(0,0)")
                    snapshots=[{"url":page.url,"html":page.content(),"metrics":self._rendered_metrics(page),"growth_events":0}]
                    actions_used=0

                # Allow late fetch/XHR completion after the final expansion action.
                page.wait_for_timeout(900)
                network_groups=[]
                for captured in dynamic_responses:
                    network_groups.extend(_dynamic_json_groups(captured.get("payload"), captured.get("url","")))
                network_groups=_merge_dynamic_groups(network_groups)
                browser.close()

            all_page_groups=[]
            parsers=[]
            tables=[]
            for snap in snapshots:
                parser=DOMParser(snap["url"])
                parser.feed(snap["html"])
                parsers.append(parser)
                container_groups=detect_repeated_groups(parser.root, snap["url"])
                relationship_groups=detect_relationship_datasets(parser.root, snap["url"])
                document_groups=detect_document_datasets(parser.root, snap["url"])
                page_groups=merge_dataset_candidates(container_groups, relationship_groups, document_groups, snap["url"])
                all_page_groups.append(page_groups)
                tables.extend([normalize_table(table) for table in parser.tables])

            groups=merge_dataset_pages(all_page_groups)
            if network_groups:
                groups=list(groups)+list(network_groups)
                groups=annotate_dataset_candidates(groups, snapshots[0]["url"])[:60]
            self.dynamic_endpoint_summary={
                "responses":len(dynamic_responses),
                "datasets":len(network_groups),
                "records":max([g.get("count",0) for g in network_groups] or [0]),
            }
            # Use the first page DOM for normal catalog/inspection while presenting
            # merged datasets from every expanded result page.
            parser=parsers[0]
            text=snapshots[0]["html"]
            final_url=snapshots[0]["url"]
            expansion_summary={
                "enabled":expand_enabled,
                "actions":actions_used,
                "pages":len(snapshots),
                "unique_relationship_records":max(
                    [g.get("count",0) for g in groups if g.get("dataset_type")=="relationship"] or [0]
                ),
            }
            self.expansion_summary=expansion_summary
            access_state=self._detect_access_state(status, final_url, text, getattr(parser,"title",""))
            smart_profile = self._smart_profile_website(final_url, parser, groups, tables, expansion_summary, access_state)
            if prefix == "public":
                self.after(0, lambda p=smart_profile: self._show_smart_profile(p))
            self.after(
                0,
                lambda: self._load_html_results(
                    text, final_url, status, parser, groups, tables, prefix
                )
            )
            self._expansion_status(
                f"Expansion complete — {len(snapshots)} result page(s), "
                f"{actions_used} action(s), strongest relationship dataset "
                f"{expansion_summary['unique_relationship_records']} record(s); "
                f"dynamic JSON: {len(dynamic_responses)} response(s), {len(network_groups)} dataset(s)."
            )
        except Exception as exc:
            self.after(0, lambda: self._show_error("Rendered Fetch Failed", str(exc)))

    def start_html_fetch(self, url, opener, prefix="public"):
        if not self.valid_url(url):
            messagebox.showerror("Invalid URL", "Enter a complete http:// or https:// URL.")
            return
        self.status_var.set("Fetching and analyzing website...")
        threading.Thread(
            target=self._html_fetch_worker,
            args=(url, opener or urllib.request.build_opener(), prefix),
            daemon=True
        ).start()

    def _html_fetch_worker(self, url, opener, prefix):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(request, timeout=35) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                final_url = response.geturl()
                status = getattr(response, "status", 200)

            parser = DOMParser(final_url)
            parser.feed(text)
            container_groups = detect_repeated_groups(parser.root, final_url)
            relationship_groups = detect_relationship_datasets(parser.root, final_url)
            document_groups = detect_document_datasets(parser.root, final_url)
            groups = merge_dataset_candidates(container_groups, relationship_groups, document_groups, final_url)
            tables = [normalize_table(table) for table in parser.tables]

            self.after(
                0, lambda: self._load_html_results(
                    text, final_url, status, parser, groups, tables, prefix
                )
            )
        except Exception as exc:
            self.after(0, lambda: self._show_error("Fetch Failed", str(exc)))

    def _load_html_results(self, text, final_url, status, parser, groups, tables, prefix):
        self.detected_groups = groups
        self.table_data = tables

        seen = set()
        self.links = []
        for link in parser.links:
            key = (link["text"], link["url"])
            if key not in seen:
                seen.add(key)
                self.links.append(key)

        group_choice = getattr(self, f"{prefix}_group_choice")
        group_tree = getattr(self, f"{prefix}_group_tree")
        table_choice = getattr(self, f"{prefix}_table_choice")
        table_tree = getattr(self, f"{prefix}_table_tree")
        links_tree = getattr(self, f"{prefix}_links_tree")
        inspector = getattr(self, f"{prefix}_inspector")

        dataset_labels = [
            (
                f"Dataset {i+1}: [{item.get('discovery_label', 'Detected Data')}] "
                f"{item.get('relevance', 'Medium')} relevance — {item['count']} records"
            )
            for i, item in enumerate(groups)
        ]
        group_choice["values"] = dataset_labels
        builder_dataset = getattr(self, f"{prefix}_builder_dataset", None)
        if builder_dataset is not None:
            # RECON015: do not expose rejected candidates as the normal workspace
            # default. Advanced Auto-Detected Data still keeps the complete catalog.
            builder_dataset["values"] = dataset_labels
        if groups:
            group_choice.current(0)
            self.show_detected(group_choice, group_tree)
            if prefix == "public":
                try:
                    plan=self._semantic_extraction_plan(final_url,parser,groups,tables)
                    self.semantic_extraction_plan=plan
                    self._show_semantic_extraction_plan(plan)
                    recommended=int(plan.get("recommended_dataset_index",-1))
                    if 0 <= recommended < len(groups):
                        group_choice.current(recommended)
                        if builder_dataset is not None:
                            builder_dataset["values"] = dataset_labels
                            builder_dataset.current(recommended)
                        self.show_detected(group_choice, group_tree)
                        # Load the semantic recommendation, but do not silently
                        # select output fields until the user applies the plan.
                        self.builder_load_dataset(prefix)
                    else:
                        self.builder_loaded_dataset_index=-1
                        if builder_dataset is not None:
                            builder_dataset["values"]=[
                                "No qualified primary dataset — search/interaction required"
                            ]
                            builder_dataset.current(0)
                        self.builder_mappings=[]
                        self.builder_detail_mappings=[]
                        self.builder_levels={}
                        self.builder_current_level=1
                        try:
                            self._builder_refresh_workspace(prefix)
                        except Exception:
                            pass
                        self.status_var.set(
                            "Analysis complete — no qualified primary entity dataset is loaded. "
                            "Rejected datasets remain available in Advanced Tools. "
                            "Use the page's normal search/location interaction, then analyze again."
                        )
                    getattr(self, f"{prefix}_result_notebook").select(getattr(self, f"{prefix}_builder_tab"))
                except Exception as exc:
                    self.status_var.set(f"Website analyzed; semantic planner warning: {exc}")
        else:
            self.configure_tree(group_tree, ["No repeated dataset detected"])

        table_choice["values"] = [
            f"Table {i+1} — {len(rows)} rows"
            for i, (headers, rows) in enumerate(tables)
        ]
        if tables:
            table_choice.current(0)
            self.show_table(table_choice, table_tree)
        else:
            self.configure_tree(table_tree, ["No HTML table detected"])

        self.configure_tree(links_tree, ["Link Text", "URL"])
        for row in self.links:
            links_tree.insert("", "end", values=row)

        # Build a complete rendered-DOM catalog for user-driven discovery.
        self.dom_catalogs[prefix] = build_dom_catalog(parser.root, final_url)
        self._refresh_dom_catalog_tree(prefix, "")
        self._refresh_website_interpretation(final_url,parser,groups,tables,prefix)

        inspector.delete("1.0", "end")
        inspector.insert(
            "1.0",
            (
                f"HTTP {status}\nURL: {final_url}\nTitle: {parser.title}\n"
                f"Tables: {len(tables)}\nDatasets: {len(groups)}\n"
                f"Links: {len(self.links)}\nElements: "
                f"{json.dumps(dict(parser.counts.most_common(15)), indent=2)}\n\n"
                f"{text[:250000]}"
            )
        )

        self.status_var.set(
            f"Complete — {len(groups)} dataset(s), {len(tables)} table(s), "
            f"{len(self.links)} link(s), {len(self.dom_catalogs.get(prefix, []))} DOM element(s) cataloged"
        )

    def login_and_fetch(self):
        mode = self.auth_mode.get()
        protected = self.protected_url.get().strip()

        if not self.valid_url(protected):
            messagebox.showerror("Invalid URL", "Enter a valid protected page URL.")
            return

        self.status_var.set("Authenticating...")
        threading.Thread(target=self._login_worker, args=(mode,), daemon=True).start()

    def _login_worker(self, mode):
        try:
            opener = self.session_opener
            headers = {"User-Agent": USER_AGENT}

            if mode == "Basic Auth":
                credentials = f"{self.username.get()}:{self.password.get()}".encode("utf-8")
                headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode("ascii")

            elif mode == "Cookie Header":
                if not self.cookie_header.get().strip():
                    raise ValueError("Cookie header is required.")
                headers["Cookie"] = self.cookie_header.get().strip()

            elif mode == "Form Login":
                login_url = self.login_url.get().strip()
                if not self.valid_url(login_url):
                    raise ValueError("A valid login URL is required.")

                fields = {
                    self.username_field.get().strip(): self.username.get(),
                    self.password_field.get().strip(): self.password.get(),
                }
                extra = json.loads(self.extra_fields.get().strip() or "{}")
                if not isinstance(extra, dict):
                    raise ValueError("Extra form fields must be a JSON object.")
                fields.update(extra)

                payload = urllib.parse.urlencode(fields).encode("utf-8")
                request = urllib.request.Request(
                    login_url,
                    data=payload,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Content-Type": "application/x-www-form-urlencoded",
                    }
                )
                with opener.open(request, timeout=35) as response:
                    response.read()

            protected = self.protected_url.get().strip()
            request = urllib.request.Request(protected, headers=headers)
            with opener.open(request, timeout=35) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                final_url = response.geturl()
                status = getattr(response, "status", 200)

            parser = DOMParser(final_url)
            parser.feed(text)
            container_groups = detect_repeated_groups(parser.root, final_url)
            relationship_groups = detect_relationship_datasets(parser.root, final_url)
            document_groups = detect_document_datasets(parser.root, final_url)
            groups = merge_dataset_candidates(container_groups, relationship_groups, document_groups, final_url)
            tables = [normalize_table(table) for table in parser.tables]

            self.after(
                0, lambda: self._load_html_results(
                    text, final_url, status, parser, groups, tables, "login"
                )
            )
        except Exception as exc:
            self.after(0, lambda: self._show_error("Login or Fetch Failed", str(exc)))

    def send_api_request(self):
        if not self.valid_url(self.api_url.get().strip()):
            messagebox.showerror("Invalid URL", "Enter a complete API endpoint URL.")
            return
        self.status_var.set("Sending API request...")
        threading.Thread(target=self._api_worker, daemon=True).start()

    def _api_worker(self):
        try:
            method = self.api_method.get()
            url = self.api_url.get().strip()
            params = json.loads(self.api_params_json.get().strip() or "{}")
            headers = json.loads(self.api_headers_json.get().strip() or "{}")
            body = json.loads(self.api_body_json.get().strip() or "{}")

            if not isinstance(params, dict) or not isinstance(headers, dict):
                raise ValueError("Parameters and headers must be JSON objects.")

            if params:
                separator = "&" if "?" in url else "?"
                url += separator + urllib.parse.urlencode(params, doseq=True)

            headers = {str(k): str(v) for k, v in headers.items()}
            headers.setdefault("User-Agent", USER_AGENT)
            headers.setdefault("Accept", "application/json")

            auth = self.api_auth.get()
            if auth == "Bearer Token":
                headers["Authorization"] = f"Bearer {self.api_token.get().strip()}"
            elif auth == "API Key Header":
                headers[self.api_key_name.get().strip() or "X-API-Key"] = self.api_token.get().strip()
            elif auth == "Basic Auth":
                raw = f"{self.api_token.get()}:{self.api_key_name.get()}".encode("utf-8")
                headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")

            data = None
            if method in {"POST", "PUT", "PATCH"}:
                data = json.dumps(body).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")

            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(request, timeout=40) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                parsed = json.loads(text)

            self.after(0, lambda: self._load_api_results(parsed))
        except Exception as exc:
            self.after(0, lambda: self._show_error("API Request Failed", str(exc)))

    def _load_api_results(self, parsed):
        self.api_json = parsed
        self.api_raw.delete("1.0", "end")
        self.api_raw.insert("1.0", json.dumps(parsed, ensure_ascii=False, indent=2)[:500000])

        arrays = discover_json_arrays(parsed)
        self.api_array_choice["values"] = [
            f"{path or '(root)'} — {count} records"
            for path, count in arrays
        ]

        selected_path = self.api_record_path.get().strip()
        if not selected_path and arrays:
            selected_path = arrays[0][0]
            self.api_record_path.set(selected_path)
            self.api_array_choice.current(0)

        self.build_api_preview(selected_path)
        self.status_var.set("API request completed successfully.")

    def use_detected_api_path(self):
        index = self.api_array_choice.current()
        arrays = discover_json_arrays(self.api_json)
        if 0 <= index < len(arrays):
            path = arrays[index][0]
            self.api_record_path.set(path)
            self.build_api_preview(path)

    def build_api_preview(self, path):
        try:
            records = get_json_path(self.api_json, path)
            if isinstance(records, dict):
                records = [records]
            if not isinstance(records, list):
                records = [{"value": records}]

            flattened = [
                flatten_json(record) if isinstance(record, (dict, list))
                else {"value": record}
                for record in records
            ]

            headers = []
            seen = set()
            for row in flattened:
                for key in row:
                    if key not in seen:
                        seen.add(key)
                        headers.append(key)

            self.api_headers = headers
            self.api_rows = [[row.get(header, "") for header in headers] for row in flattened]

            self.configure_tree(self.api_tree, headers or ["value"])
            for row in self.api_rows:
                self.api_tree.insert("", "end", values=row)
        except Exception as exc:
            self._show_error("Invalid Record Path", str(exc))

    def open_desired_data_dialog(self,prefix="public"):
        """RECON042: intent editor with logical Open/Load/Edit/Save/Save-As profile workflow."""
        profiles_dir=Path(__file__).resolve().parent/"Profiles"
        profiles_dir.mkdir(parents=True,exist_ok=True)
        registry_path=profiles_dir/"semantic_profiles.json"

        def read_registry():
            try:
                if registry_path.exists():
                    data=json.loads(registry_path.read_text(encoding="utf-8"))
                    return data if isinstance(data,dict) else {}
            except Exception:
                pass
            return {}

        def write_registry(data):
            registry_path.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding="utf-8")

        win=tk.Toplevel(self)
        win.title("Desired Data & Examples — Intent First")
        win.geometry("980x610")
        win.transient(self)
        top=ttk.Frame(win,padding=10); top.pack(fill="both",expand=True)

        ttk.Label(
            top,
            text=(
                "Define the outputs you want. Examples are optional evidence RTUDES can search for "
                "inside any field, nested value, array or free text. Profiles preserve this setup for reuse."
            ),
            wraplength=930,justify="left"
        ).pack(anchor="w",pady=(0,8))

        profile_row=ttk.Frame(top); profile_row.pack(fill="x",pady=(0,8))
        ttk.Label(profile_row,text="Industry profile:").pack(side="left")
        profile=tk.StringVar(value=getattr(self,"semantic_industry_profile","Auto / General"))
        industry_values=(
            "Auto / General","Real Estate","Real Estate — Auctions / Foreclosures",
            "Legal / Court Records","Business Directories","E-commerce / Retail",
            "Healthcare Directories","Government / Procurement","Automotive",
            "Recruitment / HR","Financial Records","Transportation / Logistics"
        )
        profile_box=ttk.Combobox(
            profile_row,textvariable=profile,state="readonly",width=34,values=industry_values
        )
        profile_box.pack(side="left",padx=(6,12))

        ttk.Label(profile_row,text="Saved profile:").pack(side="left")
        saved_name=tk.StringVar(value=getattr(self,"semantic_saved_profile_name",""))
        saved_box=ttk.Combobox(profile_row,textvariable=saved_name,state="readonly",width=28)
        saved_box.pack(side="left",padx=(6,6))

        tree=ttk.Treeview(top,columns=("field","example"),show="headings",height=14)
        tree.heading("field",text="Desired Output Field"); tree.column("field",width=310)
        tree.heading("example",text="Example / Reference Value (optional)"); tree.column("example",width=610)
        tree.pack(fill="both",expand=True)

        def populate_fields(fields):
            for iid in tree.get_children(""): tree.delete(iid)
            for i,spec in enumerate(fields or []):
                tree.insert("","end",iid=str(i),values=(spec.get("name",""),spec.get("example","")))

        populate_fields(getattr(self,"semantic_desired_fields",[]) or [])

        def refresh_saved_profiles(select_name=None):
            reg=read_registry()
            current_industry=profile.get()
            names=sorted(
                name for name,data in reg.items()
                if not current_industry or data.get("industry","Auto / General")==current_industry
            )
            saved_box["values"]=names
            if select_name and select_name in names:
                saved_name.set(select_name)
            elif saved_name.get() not in names:
                saved_name.set(names[0] if names else "")

        def snapshot(profile_name=None):
            return {
                "profile_name":profile_name or saved_name.get() or "",
                "industry":profile.get(),
                "desired_fields":[
                    {"name":clean_text(tree.item(i,"values")[0]),
                     "example":clean_text(tree.item(i,"values")[1])}
                    for i in tree.get_children("") if tree.item(i,"values")
                ],
                "transform_rules":dict(getattr(self,"semantic_transform_rules",{}) or {}),
                "key_field":key_var.get().strip(),
                "version":1
            }

        def apply_profile_data(data,name=""):
            if not isinstance(data,dict): return
            industry=data.get("industry") or "Auto / General"
            if industry in industry_values: profile.set(industry)
            populate_fields(data.get("desired_fields") or [])
            self.semantic_transform_rules=dict(data.get("transform_rules") or {})
            key_var.set(data.get("key_field") or "")
            saved_name.set(name or data.get("profile_name") or "")
            self.semantic_saved_profile_name=saved_name.get()
            refresh_saved_profiles(self.semantic_saved_profile_name)

        def load_selected():
            name=saved_name.get()
            if not name:
                messagebox.showinfo("Load Profile","Select a saved profile first."); return
            reg=read_registry()
            data=reg.get(name)
            if not data:
                messagebox.showwarning("Load Profile",f"Profile '{name}' was not found."); return
            apply_profile_data(data,name)
            self.status_var.set(f"Semantic profile loaded — {name}.")

        def edit_selected():
            # Loading into this editor is the edit operation.
            load_selected()

        def save_profile():
            name=saved_name.get().strip()
            if not name:
                return save_as_profile()
            reg=read_registry()
            reg[name]=snapshot(name)
            write_registry(reg)
            self.semantic_saved_profile_name=name
            refresh_saved_profiles(name)
            self.status_var.set(f"Semantic profile saved — {name}.")
            messagebox.showinfo("Save Profile",f"Saved profile: {name}")

        def save_as_profile():
            name=simpledialog.askstring("Save Profile As","Profile name:",parent=win)
            name=clean_text(name)
            if not name:return
            reg=read_registry()
            if name in reg:
                if not messagebox.askyesno("Replace Profile",f"'{name}' already exists. Replace it?",parent=win):
                    return
            reg[name]=snapshot(name)
            write_registry(reg)
            saved_name.set(name)
            self.semantic_saved_profile_name=name
            refresh_saved_profiles(name)
            self.status_var.set(f"Semantic profile saved as — {name}.")
            messagebox.showinfo("Save Profile As",f"Saved profile: {name}")

        def open_profile_file():
            path=filedialog.askopenfilename(
                title="Open Semantic Profile",
                filetypes=[("RTUDES profile","*.json"),("JSON files","*.json"),("All files","*.*")]
            )
            if not path:return
            try:
                data=json.loads(Path(path).read_text(encoding="utf-8"))
                if "desired_fields" not in data:
                    raise ValueError("This file is not an RTUDES semantic profile.")
                apply_profile_data(data,data.get("profile_name",""))
                self.status_var.set(f"Semantic profile opened — {Path(path).name}.")
            except Exception as exc:
                messagebox.showerror("Open Profile",str(exc),parent=win)

        def export_profile_file():
            data=snapshot(saved_name.get())
            path=filedialog.asksaveasfilename(
                title="Save Semantic Profile File",
                defaultextension=".json",
                filetypes=[("RTUDES profile","*.json"),("JSON files","*.json")]
            )
            if not path:return
            try:
                Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding="utf-8")
                self.status_var.set(f"Semantic profile file saved — {path}")
            except Exception as exc:
                messagebox.showerror("Save Profile File",str(exc),parent=win)

        def industry_changed(_evt=None):
            refresh_saved_profiles()

        profile_box.bind("<<ComboboxSelected>>",industry_changed)

        profile_actions=ttk.Frame(top); profile_actions.pack(fill="x",pady=(0,8))
        ttk.Button(profile_actions,text="Load",command=load_selected).pack(side="left")
        ttk.Button(profile_actions,text="Edit",command=edit_selected).pack(side="left",padx=(5,0))
        ttk.Button(profile_actions,text="Save",command=save_profile).pack(side="left",padx=(5,0))
        ttk.Button(profile_actions,text="Save As",command=save_as_profile).pack(side="left",padx=(5,0))
        ttk.Separator(profile_actions,orient="vertical").pack(side="left",fill="y",padx=10)
        ttk.Button(profile_actions,text="Open File",command=open_profile_file).pack(side="left")
        ttk.Button(profile_actions,text="Save Profile File",command=export_profile_file).pack(side="left",padx=(5,0))

        key_row=ttk.Frame(top); key_row.pack(fill="x",pady=(0,8))
        ttk.Label(key_row,text="Record key field:").pack(side="left")
        key_var=tk.StringVar(value=getattr(self,"semantic_key_field",""))
        ttk.Entry(key_row,textvariable=key_var,width=28).pack(side="left",padx=(6,6))
        ttk.Label(key_row,text="optional; blank = RTUDES auto/canonical key").pack(side="left")

        edit=ttk.Frame(top); edit.pack(fill="x",pady=(8,0))
        field_var=tk.StringVar(); example_var=tk.StringVar()
        ttk.Entry(edit,textvariable=field_var,width=34).pack(side="left")
        ttk.Entry(edit,textvariable=example_var,width=56).pack(side="left",padx=(6,0))

        def add():
            name=clean_text(field_var.get())
            if not name:return
            tree.insert("","end",values=(name,clean_text(example_var.get())))
            field_var.set(""); example_var.set("")
        def remove():
            for iid in tree.selection():tree.delete(iid)
        def use_profile():
            self.semantic_industry_profile=profile.get()
            self.semantic_saved_profile_name=saved_name.get()
            self.semantic_key_field=key_var.get().strip()
            self.semantic_desired_fields=[
                {"name":clean_text(tree.item(i,"values")[0]),
                 "example":clean_text(tree.item(i,"values")[1])}
                for i in tree.get_children("") if tree.item(i,"values")
            ]
            self.status_var.set(
                f"Desired schema loaded — {len(self.semantic_desired_fields)} output field(s), "
                f"profile: {self.semantic_industry_profile}"
                + (f", saved profile: {self.semantic_saved_profile_name}." if self.semantic_saved_profile_name else ".")
            )
            win.destroy()

        ttk.Button(edit,text="Add",command=add).pack(side="left",padx=(6,0))
        ttk.Button(edit,text="Remove Selected",command=remove).pack(side="left",padx=(6,0))
        ttk.Button(edit,text="Use / Load Into Task",command=use_profile).pack(side="right")

        refresh_saved_profiles(saved_name.get())


    def open_semantic_cleaning_dialog(self,prefix="public"):
        """RECON036: Power-Query-style semantic transformation assignments."""
        desired=list(getattr(self,"semantic_desired_fields",[]) or [])
        if not desired:
            messagebox.showinfo("Desired schema","Define Desired Data / Examples first.")
            return
        if not hasattr(self,"semantic_transform_rules"):
            self.semantic_transform_rules={}

        win=tk.Toplevel(self)
        win.title("Data Cleaning & Transformation Rules")
        win.geometry("1080x620")
        win.transient(self)
        outer=ttk.Frame(win,padding=10)
        outer.pack(fill="both",expand=True)
        ttk.Label(
            outer,
            text=(
                "Assign reusable cleaning/normalization rules to desired output fields. "
                "Rules run after semantic extraction and before final output."
            ),
            wraplength=1020,justify="left"
        ).pack(anchor="w",pady=(0,8))

        cols=("field","semantic","transform","validation","format")
        tree=ttk.Treeview(outer,columns=cols,show="headings",height=15)
        for c,t,w in (
            ("field","Output Field",190),("semantic","Semantic Type",170),
            ("transform","Transform Chain",300),("validation","Validation",220),
            ("format","Output Format",130)
        ):
            tree.heading(c,text=t); tree.column(c,width=w,stretch=True)
        tree.pack(fill="both",expand=True)

        def default_rule(name):
            low=name.lower()
            rule={"semantic":"Text","transform":"Text Clean","validation":"Non-empty","format":"Original"}
            if "address" in low:
                rule={"semantic":"Address","transform":"Address Parse > Street","validation":"Address structure","format":"Street"}
            elif "city" in low:
                rule={"semantic":"City","transform":"Address Parse > City","validation":"Locality context","format":"Title Case"}
            elif "state" in low or "province" in low:
                rule={"semantic":"State / Province","transform":"Address Parse > State > Upper","validation":"Region context","format":"Abbrev"}
            elif "zip" in low or "postal" in low:
                rule={"semantic":"Postal Code","transform":"Address Parse > Postal","validation":"Postal context","format":"Postal"}
            elif "date" in low or "time" in low:
                rule={"semantic":"Date / Time","transform":"Date Detect","validation":"Date context","format":"MM/DD/YY"}
            elif any(x in low for x in ("price","bid","value","amount","cost","estimate","revenue","salary")):
                rule={"semantic":"Currency / Amount","transform":"Labeled Money Extract","validation":"Money + semantic label","format":"Currency"}
            elif "email" in low:
                rule={"semantic":"Email","transform":"Email Clean","validation":"Email pattern","format":"Lowercase"}
            elif "phone" in low:
                rule={"semantic":"Phone","transform":"Phone Normalize","validation":"Phone pattern","format":"Normalized"}
            return rule

        for i,spec in enumerate(desired):
            name=clean_text(spec.get("name",""))
            rule=default_rule(name)
            rule.update(self.semantic_transform_rules.get(name,{}) or {})
            tree.insert("","end",iid=str(i),values=(name,rule["semantic"],rule["transform"],rule["validation"],rule["format"]))

        form=ttk.Frame(outer); form.pack(fill="x",pady=(10,0))
        semantic=tk.StringVar(); transform=tk.StringVar(); validation=tk.StringVar(); fmt=tk.StringVar()

        ttk.Label(form,text="Semantic").grid(row=0,column=0,sticky="w")
        ttk.Combobox(form,textvariable=semantic,width=22,state="readonly",values=(
            "Text","Address","City","State / Province","Postal Code","Country",
            "Person Name","Company / Organization","Email","Phone","URL",
            "Date / Time","Currency / Amount","Number","Percentage",
            "Identifier","Measurement / Unit"
        )).grid(row=1,column=0,padx=(0,6),sticky="ew")

        ttk.Label(form,text="Transform Chain").grid(row=0,column=1,sticky="w")
        ttk.Combobox(form,textvariable=transform,width=36,values=(
            "Text Clean","Trim > Collapse Spaces","Upper","Lower","Proper Case",
            "Remove HTML","Split / Extract","Address Parse > Street","Address Parse > City",
            "Address Parse > State > Upper","Address Parse > Postal","Date Detect",
            "Labeled Money Extract","Number Extract","Email Clean","Phone Normalize"
        )).grid(row=1,column=1,padx=(0,6),sticky="ew")

        ttk.Label(form,text="Validation").grid(row=0,column=2,sticky="w")
        ttk.Combobox(form,textvariable=validation,width=28,values=(
            "Non-empty","Address structure","Locality context","Region context",
            "Postal context","Date context","Money + semantic label",
            "Email pattern","Phone pattern","Identifier pattern"
        )).grid(row=1,column=2,padx=(0,6),sticky="ew")

        ttk.Label(form,text="Output Format").grid(row=0,column=3,sticky="w")
        ttk.Combobox(form,textvariable=fmt,width=18,values=(
            "Original","Street","Title Case","Uppercase","Lowercase","Abbrev",
            "Postal","MM/DD/YY","YYYY-MM-DD","Currency","Number","Normalized"
        )).grid(row=1,column=3,sticky="ew")

        def load_selected(_evt=None):
            sel=tree.selection()
            if not sel: return
            vals=tree.item(sel[0],"values")
            if not vals: return
            semantic.set(vals[1]); transform.set(vals[2]); validation.set(vals[3]); fmt.set(vals[4])

        def apply_selected():
            sel=tree.selection()
            if not sel: return
            iid=sel[0]
            vals=list(tree.item(iid,"values"))
            vals[1]=semantic.get() or vals[1]
            vals[2]=transform.get() or vals[2]
            vals[3]=validation.get() or vals[3]
            vals[4]=fmt.get() or vals[4]
            tree.item(iid,values=vals)

        tree.bind("<<TreeviewSelect>>",load_selected)
        ttk.Button(form,text="Apply to Selected",command=apply_selected).grid(row=1,column=4,padx=(8,0))

        def save():
            rules={}
            for iid in tree.get_children(""):
                vals=tree.item(iid,"values")
                if not vals: continue
                rules[clean_text(vals[0])]={
                    "semantic":clean_text(vals[1]),"transform":clean_text(vals[2]),
                    "validation":clean_text(vals[3]),"format":clean_text(vals[4])
                }
            self.semantic_transform_rules=rules
            self.status_var.set(f"Cleaning rules saved — {len(rules)} desired field(s) configured.")
            win.destroy()

        actions=ttk.Frame(outer); actions.pack(fill="x",pady=(8,0))
        def load_geo_reference():
            from geo_reference import load_reference_csv
            path=filedialog.askopenfilename(title="Load Geographic Reference CSV",filetypes=[("CSV files","*.csv"),("All files","*.*")])
            if not path: return
            try:
                count=load_reference_csv(path)
                self.geo_reference_path=path
                self.status_var.set(f"Geographic reference loaded — {count} City/State/Postal row(s).")
                messagebox.showinfo("Geographic Reference",f"Loaded {count} reference row(s).")
            except Exception as exc:
                messagebox.showerror("Geographic Reference",str(exc))
        ttk.Button(actions,text="Load Geography CSV",command=load_geo_reference).pack(side="left")
        ttk.Button(actions,text="Save Rules",command=save).pack(side="right")


    # ---------- DYNAMIC-RELATIONSHIP-BIND001: bind canonical records to the existing Smart Analysis DOM/relationship state ----------
    @staticmethod
    def _smart_identity_text(record):
        """Return a normalized entity identity using fields already discovered by Smart Analysis."""
        if not isinstance(record,dict):
            return ""
        preferred=[]
        fallback=[]
        for key,value in record.items():
            val=clean_text(str(value))
            if not val or val.startswith(("http://","https://")):
                continue
            low=str(key).lower()
            if any(t in low for t in ("address","title","name","headline","property")):
                preferred.append(val)
            elif 5 <= len(val) <= 220:
                fallback.append(val)
        raw=(preferred[0] if preferred else (max(fallback,key=len) if fallback else ""))
        raw=raw.lower().replace("ohio","oh")
        return " ".join(re.findall(r"[a-z0-9]+",raw))

    @classmethod
    def _smart_identity_score(cls,left,right):
        """Score whether records from two already-detected datasets describe the same entity."""
        a=cls._smart_identity_text(left); b=cls._smart_identity_text(right)
        if not a or not b:
            return 0
        if a==b:
            return 100
        if a in b or b in a:
            return 94
        at=[t for t in a.split() if len(t)>=2]
        bt=[t for t in b.split() if len(t)>=2]
        if not at or not bt:
            return 0
        aset,bset=set(at),set(bt)
        overlap=len(aset & bset)/max(1,len(aset | bset))
        # Street number + ZIP are unusually strong generic identity evidence.
        nums_a={t for t in at if t.isdigit()}
        nums_b={t for t in bt if t.isdigit()}
        numeric_bonus=18 if nums_a and nums_b and (nums_a & nums_b) else 0
        return min(100,int(overlap*100)+numeric_bonus)

    @classmethod
    def _smart_relationship_identity_score(cls,left,right):
        """Generic relationship identity score using only evidence already discovered.

        Unlike _smart_identity_score, this compares every useful non-URL textual value
        on both records. It is intentionally site-agnostic: no field name, URL shape,
        domain, or page-specific selector is required.
        """
        def values(rec):
            out=[]
            if not isinstance(rec,dict):
                return out
            for k,v in rec.items():
                text=clean_text(str(v))
                if not text or text.startswith(("http://","https://")):
                    continue
                if str(k).startswith("_"):
                    continue
                norm=" ".join(re.findall(r"[a-z0-9]+",text.lower().replace("ohio","oh")))
                if len(norm)>=2 and norm not in out:
                    out.append(norm)
            return out
        lv,rv=values(left),values(right)
        if not lv or not rv:
            return 0
        best=0
        for a in lv:
            aset=set(a.split())
            for b in rv:
                if a==b:
                    score=100
                elif a in b or b in a:
                    score=96
                else:
                    bset=set(b.split())
                    inter=aset & bset
                    if not inter:
                        score=0
                    else:
                        # containment is more useful than Jaccard when one side is a
                        # short link/title and the other is a richer canonical record.
                        contain=max(len(inter)/max(1,len(aset)),len(inter)/max(1,len(bset)))
                        jacc=len(inter)/max(1,len(aset|bset))
                        nums_a={x for x in aset if x.isdigit()}
                        nums_b={x for x in bset if x.isdigit()}
                        numeric=20 if nums_a and nums_b and nums_a & nums_b else 0
                        score=min(100,int(contain*55+jacc*25)+numeric)
                best=max(best,score)
        return best

    def _smart_fuse_primary_evidence(self,prefix,dataset_index):
        """Fuse complementary Smart Analysis datasets into the chosen primary records.

        This does NOT rediscover the page. It reuses detected_groups created by the existing
        Smart Website Profile: structured/API primary records, repeated DOM-container records,
        and relationship datasets. Evidence is joined by record identity and namespaced so
        original primary fields remain authoritative.
        """
        groups=list(getattr(self,'detected_groups',[]) or [])
        if not (0 <= int(dataset_index) < len(groups)):
            return [],{"primary":0,"matched_dom":0,"matched_relationships":0,"companion_groups":0}
        primary=groups[int(dataset_index)]
        base_records=[dict(r) for r in (primary.get('records') or [])]
        if not base_records:
            return [],{"primary":0,"matched_dom":0,"matched_relationships":0,"companion_groups":0}

        # Companion DOM/container datasets are already built by detect_repeated_groups.
        companions=[]
        relationships=[]
        for gi,g in enumerate(groups):
            if gi==int(dataset_index):
                continue
            dtype=str(g.get('dataset_type','')).lower()
            count=int(g.get('count',0) or 0)
            if dtype=='relationship':
                relationships.append(g); continue
            if dtype in {'container','container_promoted'}:
                # Count proximity is evidence that the datasets describe the same repeated entities.
                if abs(count-len(base_records)) <= max(3,int(len(base_records)*.20)):
                    companions.append(g)

        matched_dom=0; matched_rel=0
        used_dom=set(); used_rel=set()
        relationship_bind_sources={'dom_scope':0,'identity':0}
        relationship_audit={
            'dataset_records':sum(len(g.get('records') or []) for g in relationships),
            'families':sorted({clean_text(str(g.get('relationship_family',''))) for g in relationships if clean_text(str(g.get('relationship_family','')))}),
            'valid_relationship_urls':0,
            'scope_url_targets':0,
            'exact_intersections':0,
            'normalized_intersections':0,
            'sample_relationship_urls':[],
            'sample_scope_urls':[],
        }
        relationship_audit['valid_relationship_urls']=sum(
            1 for g in relationships for cand in (g.get('records') or [])
            if self.valid_url(clean_text(str(cand.get('Detail URL',''))))
        )
        relationship_audit['sample_relationship_urls']=[
            clean_text(str(cand.get('Detail URL','')))
            for g in relationships for cand in (g.get('records') or [])
            if self.valid_url(clean_text(str(cand.get('Detail URL',''))))
        ][:3]
        for i,record in enumerate(base_records):
            best=None
            for gi,g in enumerate(companions):
                for ri,cand in enumerate(g.get('records') or []):
                    if (gi,ri) in used_dom: continue
                    score=self._smart_identity_score(record,cand)
                    if best is None or score>best[0]:
                        best=(score,gi,ri,g,cand)
            if best and best[0]>=68:
                score,gi,ri,g,cand=best; used_dom.add((gi,ri)); matched_dom+=1
                for field,value in cand.items():
                    if clean_text(str(value)):
                        record[f"SMART L1 :: {field}"]=value
                record['_smart_level1_dataset']=clean_text(str(g.get('name','')))
                record['_smart_level1_identity_score']=score
            else:
                # DYNAMIC-RELATIONSHIP-BIND001: the Smart Website Profile already built a DOM catalog.
                # Bind the canonical record directly to that existing catalog using the
                # proven record-anchor + record-scope modules instead of rediscovering DOM.
                catalog=list(getattr(self,'dom_catalogs',{}).get(prefix,[]) or [])
                anchor=self._record_dom_anchor(record,catalog) if catalog else None
                scope=self._dom_record_scope(anchor,catalog) if anchor else []
                if scope:
                    matched_dom+=1
                    samples=[]; contexts=[]; targets=[]
                    for d in scope:
                        sv=clean_text(str(d.get('sample',''))); cv=clean_text(str(d.get('context','')))
                        tv=clean_text(str(d.get('target','')))
                        if sv: samples.append(sv)
                        if cv: contexts.append(cv)
                        if self.valid_url(tv): targets.append(tv)
                    # Keep record-owned evidence namespaced. The semantic engine can now
                    # search the complete card/timeline/summary without changing source data.
                    if samples: record['SMART L1 :: DOM Text']=' | '.join(dict.fromkeys(samples))[:30000]
                    if contexts: record['SMART L1 :: DOM Context']=' | '.join(dict.fromkeys(contexts))[:30000]
                    record['_smart_level1_dataset']='Smart DOM Catalog'
                    record['_smart_level1_identity_score']=100
                    # Reuse record-bound links already present in the verified scope.
                    ranked_targets=[]
                    base=(clean_text(self.public_url.get().strip()) if prefix=='public' else clean_text(self.protected_url.get().strip()))
                    for tv in dict.fromkeys(targets):
                        role=self._semantic_url_role(base,tv,'')
                        score=100 if role=='DETAIL' else (55 if role=='RESOURCE' else 0)
                        if score: ranked_targets.append((score,tv))
                    ranked_targets.sort(reverse=True)
                    if ranked_targets:
                        record['SMART Relationship URL']=ranked_targets[0][1]

            # DYNAMIC-RELATIONSHIP-BIND001: cache the verified DOM ownership boundary once
            # per primary record before evaluating relationship candidates.
            relationship_catalog=list(getattr(self,'dom_catalogs',{}).get(prefix,[]) or [])
            relationship_anchor=self._record_dom_anchor(record,relationship_catalog) if relationship_catalog else None
            relationship_scope=self._dom_record_scope(relationship_anchor,relationship_catalog) if relationship_anchor else []
            relationship_scope_targets={
                clean_text(str(d.get('target','')))
                for d in relationship_scope
                if self.valid_url(clean_text(str(d.get('target',''))))
            }
            relationship_audit['scope_url_targets'] += len(relationship_scope_targets)
            if relationship_scope_targets and len(relationship_audit['sample_scope_urls']) < 3:
                relationship_audit['sample_scope_urls'].extend(
                    [u for u in sorted(relationship_scope_targets)
                     if u not in relationship_audit['sample_scope_urls']]
                    [:3-len(relationship_audit['sample_scope_urls'])]
                )
            # Diagnostic only: compare exact and normalized representations without
            # changing the existing binder's acceptance rule.
            def _audit_norm_url(u):
                from urllib.parse import urlsplit,unquote
                try:
                    p=urlsplit(clean_text(str(u)))
                    path=re.sub(r'/+','/',unquote(p.path or '/')).rstrip('/') or '/'
                    return (p.scheme.lower(),p.netloc.lower(),path.lower(),p.query)
                except Exception:
                    return ('','','','')
            rel_urls={
                clean_text(str(c.get('Detail URL','')))
                for g in relationships for c in (g.get('records') or [])
                if self.valid_url(clean_text(str(c.get('Detail URL',''))))
            }
            relationship_audit['exact_intersections'] += len(relationship_scope_targets & rel_urls)
            norm_scope={_audit_norm_url(u) for u in relationship_scope_targets}
            norm_rel={_audit_norm_url(u) for u in rel_urls}
            relationship_audit['normalized_intersections'] += len(norm_scope & norm_rel)

            # Reuse existing relationship datasets detected during the same Smart analysis.
            # DYNAMIC-RELATIONSHIP-BIND001: the relationship detector intentionally emits
            # only Name/Link Text + Detail URL + Source Page URL. Therefore a full
            # primary-record identity score can under-score every valid relationship.
            # Reuse the already-working DOM record scope as the ownership boundary:
            # a relationship URL belongs to this record only when the same URL is
            # present inside that record's verified DOM card/scope. Text identity is
            # retained as the fallback for sites where the relationship dataset itself
            # carries enough identifying text.
            rbest=None
            for gi,g in enumerate(relationships):
                for ri,cand in enumerate(g.get('records') or []):
                    if (gi,ri) in used_rel: continue
                    url=clean_text(str(cand.get('Detail URL','')))
                    if not self.valid_url(url): continue
                    legacy_score=self._smart_identity_score(record,cand)
                    evidence_score=self._smart_relationship_identity_score(record,cand)
                    score=max(legacy_score,evidence_score)
                    ownership=100 if url in relationship_scope_targets else 0
                    final_score=max(score,ownership)
                    if rbest is None or final_score>rbest[0]:
                        rbest=(final_score,gi,ri,g,cand,score,ownership)
            if rbest and rbest[0]>=68:
                final_score,gi,ri,g,cand,text_score,ownership=rbest
                url=clean_text(str(cand.get('Detail URL','')))
                if self.valid_url(url):
                    used_rel.add((gi,ri)); matched_rel+=1
                    if not self.valid_url(clean_text(str(record.get('SMART Relationship URL','')))):
                        record['SMART Relationship URL']=url
                    record['_smart_relationship_identity_score']=final_score
                    record['_smart_relationship_text_score']=text_score
                    record['_smart_relationship_dom_ownership']=ownership
                    record['_smart_relationship_family']=clean_text(str(g.get('relationship_family','')))
                    bind_source='dom_scope' if ownership>=68 else 'identity'
                    record['_smart_relationship_bind_source']=bind_source
                    relationship_bind_sources[bind_source]=relationship_bind_sources.get(bind_source,0)+1

        diag={
            'primary':len(base_records),
            'matched_dom':matched_dom,
            'matched_relationships':matched_rel,
            'companion_groups':len(companions),
            'relationship_groups':len(relationships),
            'primary_dataset':clean_text(str(primary.get('name',''))),
            'primary_type':clean_text(str(primary.get('dataset_type',''))),
            'relationship_bind_sources':relationship_bind_sources,
        }
        diag['relationship_audit']=relationship_audit
        self.smart_fusion_diagnostics=diag
        self.smart_fused_records=base_records
        return base_records,diag

    def _orch_relationship_plan(self,dataset_index,records,processed,prefix="public"):
        """Resolve record-specific next-page URLs using ALL proven relationship sources.

        Order: configured/recommended relationship field -> record-bound DOM links.
        The DOM path is deliberately record-owned; page-level links are never borrowed.
        """
        best,ranked=self._recommended_detail_relationship(dataset_index)
        if not best:
            # Reuse the existing binder before declaring that no relationship exists.
            try:
                plan=getattr(self,"semantic_extraction_plan",{}) or {}
                best=self._bind_recommended_detail_relationship(prefix,dataset_index,plan,best,ranked)
            except Exception:
                best=None
        field=clean_text((best or {}).get("field",""))
        urls={}
        catalog=list(getattr(self,"dom_catalogs",{}).get(prefix,[]) or [])
        base=(clean_text(self.public_url.get().strip()) if prefix=="public"
              else clean_text(self.protected_url.get().strip()))

        for i,(record,item) in enumerate(zip(records,processed)):
            unresolved=[k for k,v in item.get("states",{}).items() if v in ("UNRESOLVED","ERROR")]
            if not unresolved:
                continue
            url=""; source=""
            # DYNAMIC-RELATIONSHIP-BIND001: first reuse the record-specific relationship already found
            # by Smart Website Analysis and joined to this canonical primary record.
            smart_url=clean_text(str(record.get("SMART Relationship URL","")))
            if self.valid_url(smart_url):
                url=smart_url; source="Smart Analysis relationship dataset"
            elif field:
                candidate=self._builder_record_relationship_url(record,field)
                if self.valid_url(candidate):
                    url=candidate; source=field

            # If the dataset did not expose the relationship as a field, reuse the
            # DOM researcher to enumerate links INSIDE this record's card.
            if not url and catalog:
                anchor=self._record_dom_anchor(record,catalog)
                scope=self._dom_record_scope(anchor,catalog) if anchor else []
                candidates=[]
                for d in scope:
                    target=clean_text(d.get("target",""))
                    if not self.valid_url(target):
                        continue
                    role=self._semantic_url_role(base,target,clean_text(d.get("sample","")))
                    if role not in {"DETAIL","RESOURCE"}:
                        continue
                    score=100 if role=="DETAIL" else 55
                    low=(clean_text(d.get("sample",""))+" "+clean_text(d.get("attributes",""))+" "+target).lower()
                    if any(k in low for k in ("detail","view lot","view lots","property","auction","catalog","lot")):
                        score+=20
                    if any(k in low for k in ("terms","login","register","privacy","contact")):
                        score-=70
                    candidates.append((score,target,d))
                candidates.sort(key=lambda x:-x[0])
                if candidates and candidates[0][0]>=60:
                    _score,url,d=candidates[0]
                    source="record-bound DOM relationship"
                    item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                        "stage":"Record Link Enumerator","status":"FOUND","url":url,
                        "dom_path":clean_text(d.get("path","")),"score":_score
                    })

            if url:
                urls[i]=url
                item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                    "stage":"Next-Page URL Locator","status":"FOUND","field":source,"url":url,
                    "unresolved_fields":list(unresolved)
                })
            else:
                item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                    "stage":"Next-Page URL Locator","status":"NO TARGET","field":field,
                    "unresolved_fields":list(unresolved)
                })
        return {"field":field,"best":best or {},"ranked":ranked or [],"urls":urls}

    def _orch_detail_dom_resolve(self,item,detail_catalog,mapping):
        """Reuse existing DOM researcher on one authoritative detail page."""
        from semantic_intent import dom_semantic_candidate, FIELD_FOUND, FIELD_UNRESOLVED, FIELD_ERROR
        if not detail_catalog:
            return item,0
        row=dict(item.get("row",{}));states=dict(item.get("states",{}));diag=dict(item.get("diagnostics",{}))
        by_name={m.get("desired",""):m for m in mapping or []}
        recovered=0
        for desired,state in list(states.items()):
            if state not in (FIELD_UNRESOLVED,FIELD_ERROR):
                continue
            rule=by_name.get(desired,{})
            candidates=[]
            for dom_item in detail_catalog:
                rank=dom_search_score(dom_item,desired,clean_text(rule.get("example","")))
                sem=dom_semantic_candidate(desired,dom_item)
                if not sem.get("value"):
                    continue
                total=min(100,int(rank*.55+sem.get("score",0)*.65))
                if total>=78:
                    candidates.append((total,dom_item,sem))
            candidates.sort(key=lambda x:-x[0])
            if candidates:
                score,dom_item,sem=candidates[0]
                row[desired]=sem.get("value","")
                states[desired]=FIELD_FOUND
                diag[desired]={
                    "method":"existing DOM researcher — detail level",
                    "source_path":"DOM: "+clean_text(dom_item.get("path","")),
                    "score":score,
                    "dom_sample":clean_text(dom_item.get("sample","")),
                    "reason":sem.get("reason","")
                }
                recovered+=1
        new=dict(item);new.update({"row":row,"states":states,"diagnostics":diag})
        return new,recovered

    def _orch_level_research(self,record,item,url,mapping):
        """Reuse rendered-page level researcher + semantic features + DOM catalog."""
        from semantic_intent import final_sweep_record_safe
        html_text,final_url=self._render_page_html(url,wait_ms=1800,scroll=True)
        parser=DOMParser(final_url);parser.feed(html_text)
        features=extract_universal_semantic_features(html_text,final_url,node_features(parser.root,final_url))
        evidence=[{
            "url":final_url,"status":"ok","title":parser.title,
            "text":clean_text(parser.root.text())[:120000],"data":features
        }]
        swept=final_sweep_record_safe(
            record,item,mapping,int(item.get("record_index",0)),getattr(self,"semantic_key_field",""),
            getattr(self,"semantic_transform_rules",{}) or {},evidence_items=evidence
        )
        detail_catalog=build_dom_catalog(parser.root,final_url)
        swept,dom_recovered=self._orch_detail_dom_resolve(swept,detail_catalog,mapping)
        swept.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
            "stage":"Level Researcher","status":"COMPLETE","url":final_url,
            "feature_count":len(features),"dom_elements":len(detail_catalog),
            "detail_dom_recovered":dom_recovered
        })
        return swept,evidence,dom_recovered

    def _orch_configured_deep_crawl(self,record,item,relationship_field,mapping):
        """Reuse the proven Page-1 -> Page-N extractor when multi-level levels are configured."""
        max_level=max(getattr(self,"builder_levels",{1:{}}).keys() or [1])
        if max_level<3 or not relationship_field:
            item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                "stage":"Deep Crawler","status":"SKIPPED","reason":"no configured Page-3+ path"
            })
            return item,False
        try:
            from playwright.sync_api import sync_playwright
            from semantic_intent import final_sweep_record_safe
            active=[m for m in getattr(self,"builder_detail_mappings",[]) if m.get("enabled",True)]
            mappings_by_level=self._builder_mappings_by_level(active)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page(user_agent=USER_AGENT,viewport={"width":1600,"height":1000})
                deep_row,status=self._builder_extract_record_across_levels(
                    page,record,relationship_field,mappings_by_level,timeout_ms=30000
                )
                browser.close()
            evidence=[{"url":"","status":"ok","title":"Configured multi-level crawl","text":clean_text(deep_row),"data":deep_row}]
            item=final_sweep_record_safe(
                record,item,mapping,int(item.get("record_index",0)),getattr(self,"semantic_key_field",""),
                getattr(self,"semantic_transform_rules",{}) or {},evidence_items=evidence
            )
            item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                "stage":"Deep Crawler","status":"COMPLETE","detail":status
            })
            return item,True
        except Exception as exc:
            item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                "stage":"Deep Crawler","status":"ERROR","reason":str(exc)[:250]
            })
            return item,False

    def _orch_trace_summary(self,processed):
        stages=Counter()
        for item in processed or []:
            for event in item.get("diagnostics",{}).get("_orchestration",[]):
                stages[(event.get("stage","?"),event.get("status","?"))]+=1
        return stages

    def preview_desired_dataset(self,prefix="public"):
        """RECON034: apply validated semantic mappings across the full loaded dataset."""
        from semantic_intent import learn_semantic_mapping, apply_adaptive_semantic_mapping, apply_cleaning_rules
        desired=list(getattr(self,"semantic_desired_fields",[]) or [])
        research=list(getattr(self,"semantic_research_results",[]) or [])
        if not desired:
            self.open_desired_data_dialog(prefix); return
        if not research:
            messagebox.showinfo("Research required","Run Research Desired Data first.")
            return
        idx=int(getattr(self,"builder_loaded_dataset_index",-1))
        if not (0 <= idx < len(getattr(self,"detected_groups",[]) or [])):
            messagebox.showinfo("Load Dataset","Load a detected dataset first."); return
        # DYNAMIC-RELATIONSHIP-BIND001: Smart Website Analysis has already done the expensive work:
        # rendered expansion, pagination snapshots, repeated-container discovery,
        # relationship discovery and DOM cataloging. Always reuse/fuse that evidence
        # for Desired Dataset Preview. The old length-equality shortcut incorrectly
        # skipped fusion whenever the research backbone and primary dataset happened
        # to contain the same number of records.
        research_records=list(getattr(self,"semantic_research_source_records",[]) or [])
        fused_records,smart_diag=self._smart_fuse_primary_evidence(prefix,idx)
        primary_records=list(self.detected_groups[idx].get("records",[]) or [])
        if fused_records and len(fused_records)==len(primary_records):
            records=fused_records
        elif research_records:
            records=research_records
        else:
            records=primary_records
        mapping=learn_semantic_mapping(research)
        if not mapping:
            messagebox.showwarning("No validated mappings","No Medium/High semantic mappings are available."); return
        # RECON043: keyed record processor, field-by-field through EOF.
        from semantic_intent import (
            process_dataset_keyed, rows_from_processed,
            acquisition_queue_from_processed, recommend_key_field
        )
        key_field=getattr(self,"semantic_key_field","") or ""
        if not key_field:
            recommendation=recommend_key_field(records)
            key_field=recommendation.get("field","")
        processed,processor_summary=process_dataset_keyed(
            records,mapping,key_field,getattr(self,"semantic_transform_rules",{}) or {}
        )

        # DYNAMIC-RELATIONSHIP-BIND001: consume the SMART L1 evidence already bound by Smart Analysis
        # before any relationship/Page-2/deep-crawl escalation.
        from semantic_intent import apply_smart_bound_resolution
        processed,smart_resolve_diag=apply_smart_bound_resolution(
            records,processed,mapping,getattr(self,"semantic_transform_rules",{}) or {}
        )
        self.smart_resolve_diagnostics=smart_resolve_diag

        # DYNAMIC-RELATIONSHIP-BIND001: one canonical evidence-bus pass using all existing
        # record-owned evidence before relationship/deep-crawl escalation.
        from semantic_intent import apply_core_integration_resolution
        processed,core_integration_diag=apply_core_integration_resolution(
            records,processed,mapping,getattr(self,"semantic_transform_rules",{}) or {}
        )
        self.core_integration_diagnostics=core_integration_diag
        processed=self._apply_level_research_handoff(records,processed,mapping,key_field)
        for item in processed:
            item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                "stage":"Smart Semantic Resolution",
                "status":"RECOVERED" if smart_resolve_diag.get("fields_recovered",0) else "NO RECOVERY",
                "smart_records":smart_resolve_diag.get("smart_records",0),
                "fields_recovered":smart_resolve_diag.get("fields_recovered",0),
                "records_improved":smart_resolve_diag.get("records_improved",0),
            })
        for item in processed:
            item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                "stage":"Smart Evidence Fusion",
                "status":"READY" if smart_diag.get("matched_dom",0) else "NO DOM MATCH",
                "matched_dom_records":smart_diag.get("matched_dom",0),
                "primary_records":smart_diag.get("primary",0),
                "relationship_matches":smart_diag.get("matched_relationships",0),
            })

        # RECON045: automatic fallback to the already-existing rendered DOM catalog.
        processed,dom_fallback_diag=self._apply_existing_dom_fallback(prefix,records,processed,mapping)
        # DYNAMIC-RELATIONSHIP-BIND001 Stage 3: use the existing semantic next-page relationship locator
        # now, before any deeper traversal is attempted.
        relationship_plan=self._orch_relationship_plan(idx,records,processed,prefix)
        self.semantic_orchestration_relationship_plan=relationship_plan
        # Recompute state counts after DOM recovery.
        state_counts={}
        for item in processed:
            for state in item.get("states",{}).values():
                state_counts[state]=state_counts.get(state,0)+1
        processor_summary["states"]=state_counts
        processor_summary["dom_fallback"]=dom_fallback_diag

        rows=rows_from_processed(processed,mapping)
        diagnostics=[item.get("diagnostics",{}) for item in processed]
        cleaning_diagnostics={}
        quality_diagnostics={"rejected":0}
        self.semantic_processed_records=processed
        self.semantic_processor_summary=processor_summary
        self.semantic_key_field=key_field
        self.semantic_active_acquisition_queue=acquisition_queue_from_processed(records,processed)
        # If the generic evidence envelope did not surface a URL, reuse the existing
        # relationship locator's record-specific Page-2 URL rather than guessing.
        relation_urls=relationship_plan.get("urls",{})
        by_idx={q.get("record_index"):q for q in self.semantic_active_acquisition_queue}
        for rec_index,url in relation_urls.items():
            q=by_idx.get(rec_index)
            if q is not None and self.valid_url(url):
                if url not in q.get("urls",[]): q.setdefault("urls",[]).insert(0,url)
                q["has_fetch_target"]=True
                q["relationship_source"]=relationship_plan.get("field","")
        self.semantic_record_integrity={
            "source_count":len(records),"output_count":len(rows),"preserved":len(records)==len(rows)
        }
        self.semantic_preview_rows=rows
        self.semantic_preview_mapping=mapping
        self.semantic_cleaning_diagnostics=cleaning_diagnostics

        win=tk.Toplevel(self); win.title(f"Desired Dataset Preview — {len(rows)} record(s)")
        win.geometry("1180x650"); win.transient(self)
        outer=ttk.Frame(win,padding=10); outer.pack(fill="both",expand=True)
        ttk.Label(outer,text=(
            f"Profile: {getattr(self,'semantic_industry_profile','Auto / General')}  •  DYNAMIC-RELATIONSHIP-BIND001  •  "
            f"{len(mapping)} learned mapping(s) applied to {len(rows)} record(s). "
            "Blank values mean the learned source/pattern produced no evidence for that record."
        ),wraplength=1120,justify="left").pack(anchor="w",pady=(0,8))
        frame=ttk.Frame(outer); frame.pack(fill="both",expand=True)
        cols=[m["desired"] for m in mapping]
        tree=ttk.Treeview(frame,columns=cols,show="headings")
        y=ttk.Scrollbar(frame,orient="vertical",command=tree.yview)
        x=ttk.Scrollbar(frame,orient="horizontal",command=tree.xview)
        tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        for c in cols:
            tree.heading(c,text=c); tree.column(c,width=165,stretch=True)
        for row in rows:
            tree.insert("","end",values=[row.get(c,"") for c in cols])
        tree.grid(row=0,column=0,sticky="nsew"); y.grid(row=0,column=1,sticky="ns"); x.grid(row=1,column=0,sticky="ew")
        frame.rowconfigure(0,weight=1); frame.columnconfigure(0,weight=1)
        total=len(rows)*len(cols); filled=sum(bool(row.get(c)) for row in rows for c in cols)
        alternate=sum(
            1 for d in diagnostics for info in d.values()
            if isinstance(info,dict) and info.get("recovered_alternate")
        )
        unresolved=total-filled
        psummary=getattr(self,"semantic_processor_summary",{}) or {}
        scounts=psummary.get("states",{}) or {}
        summary_var=tk.StringVar(value=(
            f"Coverage: {filled}/{total} populated ({100*filled/total if total else 0:.1f}%).  "
            f"FOUND {scounts.get('FOUND',0)} | ABSENT {scounts.get('ABSENT',0)} | "
            f"UNRESOLVED {scounts.get('UNRESOLVED',0)} | ERROR {scounts.get('ERROR',0)}.  "
            f"Key: {getattr(self,'semantic_key_field','') or 'canonical auto'}"
        ))
        ttk.Label(outer,textvariable=summary_var).pack(anchor="w",pady=(7,0))
        workflow_var=tk.StringVar()
        ttk.Label(outer,textvariable=workflow_var).pack(anchor="w",pady=(4,0))
        actions=ttk.Frame(outer); actions.pack(fill="x",pady=(5,0))
        def export_desired_csv():
            if not workflow.get("finalized",False):
                messagebox.showwarning("Export Locked","Complete acquisition/final sweep before exporting the desired dataset.")
                return
            counts=current_counts()
            remaining=counts.get("UNRESOLVED",0)+counts.get("ERROR",0)
            if remaining and not messagebox.askyesno(
                "Export With Unresolved Fields",
                f"{remaining} field(s) remain UNRESOLVED/ERROR after final processing. Export anyway?"
            ):
                return
            path=filedialog.asksaveasfilename(title="Export Desired Dataset",defaultextension=".csv",filetypes=[("CSV files","*.csv"),("All files","*.*")])
            if not path: return
            try:
                with open(path,"w",encoding="utf-8-sig",newline="") as f:
                    writer=csv.DictWriter(f,fieldnames=cols); writer.writeheader()
                    writer.writerows([{c:row.get(c,"") for c in cols} for row in rows])
                self.status_var.set(f"Desired dataset exported — {len(rows)} record(s) to {path}")
                messagebox.showinfo("Export Complete",f"Exported {len(rows)} record(s).")
            except Exception as exc:
                messagebox.showerror("Export Desired Dataset",str(exc))
        workflow={"acquired":False,"finalized":False}

        def refresh_tree():
            for iid in tree.get_children(""):
                tree.delete(iid)
            for row in rows:
                tree.insert("","end",values=[row.get(c,"") for c in cols])

        def current_counts():
            counts={}
            for item in getattr(self,"semantic_processed_records",[]) or []:
                for state in item.get("states",{}).values():
                    counts[state]=counts.get(state,0)+1
            return counts

        def update_workflow_controls():
            counts=current_counts()
            unresolved_count=counts.get("UNRESOLVED",0)+counts.get("ERROR",0)
            queue=list(getattr(self,"semantic_active_acquisition_queue",[]) or [])
            with_targets=sum(1 for q in queue if q.get("has_fetch_target"))

            if unresolved_count==0 and not workflow["acquired"]:
                workflow["finalized"]=True
                acquire_btn.configure(state="disabled")
                final_btn.configure(state="disabled")
                export_btn.configure(state="normal")
                workflow_var.set("Workflow: Initial Process ✓ → Acquire not required → Finalized ✓ → Export ready")
            elif not workflow["acquired"] and with_targets>0:
                acquire_btn.configure(state="normal")
                final_btn.configure(state="disabled")
                export_btn.configure(state="disabled")
                workflow_var.set(
                    f"Workflow: Initial Process ✓ → Acquire Unresolved REQUIRED "
                    f"({with_targets} record(s) have fetch targets) → Final Sweep locked → Export locked"
                )
            elif not workflow["finalized"]:
                acquire_btn.configure(state="disabled")
                final_btn.configure(state="disabled")
                export_btn.configure(state="disabled")
                workflow_var.set(
                    f"Workflow: Processing complete → Review unresolved/error evidence → Export locked "
                    f"({unresolved_count} unresolved/error)"
                )
            else:
                acquire_btn.configure(state="disabled")
                final_btn.configure(state="disabled")
                export_btn.configure(state="normal")
                workflow_var.set(
                    f"Workflow: Initial Process ✓ → Acquisition complete → Final Sweep ✓ → Export enabled "
                    f"({unresolved_count} unresolved/error remain)"
                )

        def acquire_unresolved():
            from semantic_intent import execute_active_acquisition, rows_from_processed, acquisition_queue_from_processed
            current=list(getattr(self,"semantic_processed_records",[]) or [])
            queue=list(getattr(self,"semantic_active_acquisition_queue",[]) or [])
            with_targets=sum(1 for q in queue if q.get("has_fetch_target"))
            if not queue:
                messagebox.showinfo("Acquire Unresolved","Nothing is queued for acquisition."); return
            if with_targets==0:
                workflow["acquired"]=True
                self.status_var.set("DYNAMIC-RELATIONSHIP-BIND001 acquisition skipped — no qualified fetch targets; proceed to Final Sweep.")
                update_workflow_controls()
                return

            # Responsive acquisition monitor. Network work never runs on the Tk thread.
            progress_win=tk.Toplevel(win)
            progress_win.title("Acquiring Additional Evidence")
            progress_win.geometry("620x300")
            progress_win.transient(win)
            progress_win.grab_set()
            pf=ttk.Frame(progress_win,padding=14);pf.pack(fill="both",expand=True)
            stage_var=tk.StringVar(value="Preparing unresolved-record acquisition...")
            record_var=tk.StringVar(value=f"0 / {len(records)} records processed")
            stats_var=tk.StringVar(value="URLs checked: 0  |  Fields recovered: 0  |  Errors: 0")
            elapsed_var=tk.StringVar(value="Elapsed: 0s")
            ttk.Label(pf,text="Acquire Unresolved",font=("Segoe UI",11,"bold")).pack(anchor="w")
            ttk.Label(pf,textvariable=stage_var,wraplength=580).pack(anchor="w",pady=(8,5))
            bar=ttk.Progressbar(pf,mode="determinate",maximum=max(1,len(records)))
            bar.pack(fill="x",pady=(4,6))
            ttk.Label(pf,textvariable=record_var).pack(anchor="w")
            ttk.Label(pf,textvariable=stats_var).pack(anchor="w",pady=(3,0))
            ttk.Label(pf,textvariable=elapsed_var).pack(anchor="w",pady=(3,8))
            cancel_event=threading.Event()
            cancel_btn=ttk.Button(pf,text="Cancel After Current Request",command=cancel_event.set)
            cancel_btn.pack(anchor="e")

            result_box={}
            start_time=time.time()

            # Use a local worker implementation so progress and cancellation are visible
            # without weakening the generic semantic acquisition executor.
            def worker():
                try:
                    from semantic_intent import (
                        acquisition_queue_from_processed, fetch_evidence_url,
                        final_sweep_record_safe, FIELD_FOUND, FIELD_UNRESOLVED, FIELD_ERROR,
                        FIELD_ABSENT, FIELD_NOT_CHECKED
                    )
                    recs=list(records);proc=list(current)
                    aq=acquisition_queue_from_processed(recs,proc)
                    by_index={x["record_index"]:x for x in aq}
                    updated=[]
                    stats={"queued_records":len(aq),"fetch_target_records":0,"urls_attempted":0,
                           "urls_ok":0,"records_improved":0,"fields_recovered":0,"fetch_errors":0}
                    for i,record in enumerate(recs):
                        item=proc[i]
                        qitem=by_index.get(i)
                        if qitem:
                            raw_urls=[u for u in qitem.get("urls",[]) if clean_text(u)]
                            base=(clean_text(self.public_url.get().strip()) if prefix=="public"
                                  else clean_text(self.protected_url.get().strip()))
                            plan_url=clean_text(
                                str((getattr(self,"semantic_orchestration_relationship_plan",{}) or {})
                                    .get("urls",{}).get(i,""))
                            )
                            ranked_urls=[];seen=set()
                            if self.valid_url(plan_url):
                                ranked_urls.append((120,plan_url,"PLAN"));seen.add(plan_url)
                            for candidate in raw_urls:
                                if candidate in seen or not self.valid_url(candidate):
                                    continue
                                role=self._semantic_url_role(base,candidate,"")
                                score={"DETAIL":100,"RESOURCE":35,"EXTERNAL":10}.get(role,0)
                                if score:
                                    ranked_urls.append((score,candidate,role));seen.add(candidate)
                            ranked_urls.sort(key=lambda x:-x[0])
                            detail_urls=[x for x in ranked_urls if x[2] in ("PLAN","DETAIL")]
                            urls=[x[1] for x in detail_urls[:4]]

                            if urls: stats["fetch_target_records"]+=1
                            before=sum(1 for v in item.get("states",{}).values() if v==FIELD_FOUND)

                            if urls and not cancel_event.is_set():
                                url=urls[0]
                                stats["urls_attempted"]+=1
                                try:
                                    item,_evidence,_domrec=self._orch_level_research(record,item,url,mapping)
                                    stats["urls_ok"]+=1
                                    from semantic_intent import merge_record_evidence, apply_core_integration_resolution
                                    owned_record=merge_record_evidence(record,_evidence)
                                    one_updated,rt_diag=apply_core_integration_resolution(
                                        [owned_record],[item],mapping,
                                        getattr(self,"semantic_transform_rules",{}) or {}
                                    )
                                    if one_updated:
                                        item=one_updated[0]
                                    item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                                        "stage":"Runtime Evidence Handoff","status":"COMPLETE",
                                        "url":url,"url_role":self._semantic_url_role(base,url,""),
                                        "evidence_items":len(_evidence or []),
                                        "evidence_chars":sum(len(clean_text(str(e.get("text","")))) for e in (_evidence or []) if isinstance(e,dict)),
                                        "qualified_recovery":int((rt_diag or {}).get("fields_recovered",0)),
                                        "records_improved":int((rt_diag or {}).get("records_improved",0))
                                    })
                                except Exception as exc:
                                    stats["fetch_errors"]+=1
                                    item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                                        "stage":"Level Researcher","status":"ERROR","url":url,"reason":str(exc)[:250]
                                    })
                            else:
                                item.setdefault("diagnostics",{}).setdefault("_orchestration",[]).append({
                                    "stage":"Runtime URL Qualification","status":"NO DETAIL TARGET",
                                    "candidate_count":len(raw_urls),
                                    "roles":[self._semantic_url_role(base,u,"") for u in raw_urls[:6] if self.valid_url(u)]
                                })
                            # Existing configured Page-N crawler is used only if fields remain unresolved.
                            remaining=any(v in (FIELD_UNRESOLVED,FIELD_ERROR) for v in item.get("states",{}).values())
                            if remaining and not cancel_event.is_set():
                                relation_field=getattr(self,"semantic_orchestration_relationship_plan",{}).get("field","")
                                item,_deep=self._orch_configured_deep_crawl(record,item,relation_field,mapping)
                            after=sum(1 for v in item.get("states",{}).values() if v==FIELD_FOUND)
                            if after>before:
                                stats["records_improved"]+=1;stats["fields_recovered"]+=(after-before)
                        updated.append(item)

                        snapshot=dict(stats)
                        progress_win.after(0,lambda ii=i,ss=snapshot: (
                            bar.configure(value=ii+1),
                            record_var.set(f"{ii+1} / {len(recs)} records processed"),
                            stage_var.set("Cancellation requested — finishing current request..." if cancel_event.is_set()
                                          else f"Checking record {ii+1} of {len(recs)} for unresolved evidence..."),
                            stats_var.set(f"URLs checked: {ss.get('urls_attempted',0)}  |  "
                                          f"Fields recovered: {ss.get('fields_recovered',0)}  |  "
                                          f"Errors: {ss.get('fetch_errors',0)}")
                        ))
                        if cancel_event.is_set():
                            # Preserve untouched remaining records.
                            updated.extend(proc[i+1:])
                            break

                    counts={"FOUND":0,"ABSENT":0,"UNRESOLVED":0,"ERROR":0,"NOT CHECKED":0}
                    for it in updated:
                        for state in it.get("states",{}).values():
                            counts[state]=counts.get(state,0)+1
                    result_box["updated"]=updated
                    result_box["summary"]={"records":len(updated),"states":counts,
                                           "key_field":getattr(self,"semantic_key_field",""),
                                           "acquisition":stats}
                    result_box["cancelled"]=cancel_event.is_set()
                except Exception as exc:
                    result_box["error"]=str(exc)

            def poll():
                elapsed=int(time.time()-start_time)
                elapsed_var.set(f"Elapsed: {elapsed//60}m {elapsed%60}s")
                if "error" in result_box:
                    progress_win.grab_release();progress_win.destroy()
                    messagebox.showerror("Acquisition Error",result_box["error"])
                    return
                if "updated" not in result_box:
                    progress_win.after(250,poll);return

                updated=result_box["updated"];summary=result_box["summary"]
                # DYNAMIC-RELATIONSHIP-BIND001: Level-1 record DOM ran before relationship traversal;
                # Page-2 DOM ran inside _orch_level_research; configured Page-N crawler
                # ran only if unresolved remained. Now perform the evidence-safe final sweep.
                from semantic_intent import summarize_processed_states, final_sweep_record_safe
                finalized=[]
                for i,(record,item) in enumerate(zip(records,updated)):
                    finalized.append(final_sweep_record_safe(
                        record,item,mapping,i,getattr(self,"semantic_key_field",""),
                        getattr(self,"semantic_transform_rules",{}) or {}
                    ))
                updated=finalized
                counts=summarize_processed_states(updated)
                summary["states"]=counts
                summary["orchestration_trace"]=dict((f"{a} / {b}",n) for (a,b),n in self._orch_trace_summary(updated).items())
                dom_stats={"fields_recovered":0}
                self.semantic_processed_records=updated
                self.semantic_processor_summary=summary
                rows[:] = rows_from_processed(updated,mapping)
                self.semantic_preview_rows=rows
                self.semantic_active_acquisition_queue=acquisition_queue_from_processed(records,updated)
                workflow["acquired"]=True
                workflow["finalized"]=(counts.get("UNRESOLVED",0)+counts.get("ERROR",0)==0)
                refresh_tree()
                total_now=len(rows)*len(cols)
                filled_now=sum(bool(row.get(c)) for row in rows for c in cols)
                summary_var.set(
                    f"Coverage: {filled_now}/{total_now} populated "
                    f"({100*filled_now/total_now if total_now else 0:.1f}%).  "
                    f"FOUND {counts.get('FOUND',0)} | ABSENT {counts.get('ABSENT',0)} | "
                    f"UNRESOLVED {counts.get('UNRESOLVED',0)} | ERROR {counts.get('ERROR',0)}.  "
                    f"Key: {getattr(self,'semantic_key_field','') or 'canonical auto'}"
                )
                stats=summary.get("acquisition",{})
                progress_win.grab_release();progress_win.destroy()

                if result_box.get("cancelled"):
                    self.status_var.set(
                        f"DYNAMIC-RELATIONSHIP-BIND001 acquisition cancelled safely — completed work preserved; "
                        f"{stats.get('urls_attempted',0)} URL(s) checked."
                    )
                    messagebox.showinfo(
                        "Acquisition Cancelled",
                        "Acquisition stopped safely after the current request. Completed results were preserved."
                    )
                else:
                    self.status_var.set(
                        f"DYNAMIC-RELATIONSHIP-BIND001 acquisition — attempted {stats.get('urls_attempted',0)} URL(s), "
                        f"recovered {stats.get('fields_recovered',0)} field(s) across "
                        f"{stats.get('records_improved',0)} record(s)."
                    )
                    messagebox.showinfo(
                        "Acquisition Complete",
                        f"Queued records: {stats.get('queued_records',0)}\n"
                        f"URLs attempted: {stats.get('urls_attempted',0)}\n"
                        f"Successful fetches: {stats.get('urls_ok',0)}\n"
                        f"Fields recovered: {stats.get('fields_recovered',0)}\n"
                        f"Records improved by acquisition: {stats.get('records_improved',0)}\n"
                        f"DOM fields recovered automatically: {dom_stats.get('fields_recovered',0)}\n"
                        f"Remaining unresolved/error: {counts.get('UNRESOLVED',0)+counts.get('ERROR',0)}\n\n"
                        "Unified processing path complete. Review remaining UNRESOLVED/ERROR items before export."
                    )
                update_workflow_controls()

            threading.Thread(target=worker,daemon=True).start()
            progress_win.after(250,poll)

        def run_final_sweep():
            from semantic_intent import final_sweep_record_safe, rows_from_processed, acquisition_queue_from_processed, summarize_processed_states
            current=list(getattr(self,"semantic_processed_records",[]) or [])
            updated=[]
            for i,(record,item) in enumerate(zip(records,current)):
                updated.append(final_sweep_record_safe(
                    record,item,mapping,i,getattr(self,"semantic_key_field",""),
                    getattr(self,"semantic_transform_rules",{}) or {}
                ))
            # RECON047: after the evidence-safe semantic sweep, reuse the existing
            # record-scoped DOM fallback for additional recovery. Do NOT convert
            # remaining uncertainty to ABSENT merely because a DOM scope was visited.
            updated,dom_stats=self._apply_existing_dom_fallback(prefix,records,updated,mapping)

            self.semantic_processed_records=updated
            rows[:] = rows_from_processed(updated,mapping)
            self.semantic_preview_rows=rows
            self.semantic_active_acquisition_queue=acquisition_queue_from_processed(records,updated)
            counts=summarize_processed_states(updated)
            self.semantic_processor_summary={
                "records":len(updated),
                "states":counts,
                "key_field":getattr(self,"semantic_key_field",""),
                "final_sweep_complete":True
            }
            workflow["finalized"]=True
            refresh_tree()

            total_now=len(rows)*len(cols)
            filled_now=sum(bool(row.get(c)) for row in rows for c in cols)
            summary_var.set(
                f"Coverage: {filled_now}/{total_now} populated "
                f"({100*filled_now/total_now if total_now else 0:.1f}%).  "
                f"FOUND {counts.get('FOUND',0)} | ABSENT {counts.get('ABSENT',0)} | "
                f"UNRESOLVED {counts.get('UNRESOLVED',0)} | ERROR {counts.get('ERROR',0)}.  "
                f"Key: {getattr(self,'semantic_key_field','') or 'canonical auto'}"
            )
            self.status_var.set(
                f"RECON048 final sweep — FOUND {counts.get('FOUND',0)}, ABSENT {counts.get('ABSENT',0)}, "
                f"UNRESOLVED {counts.get('UNRESOLVED',0)}, ERROR {counts.get('ERROR',0)}."
            )
            messagebox.showinfo(
                "Final Sweep Complete",
                f"Records: {len(updated)}\nFOUND: {counts.get('FOUND',0)}\n"
                f"ABSENT: {counts.get('ABSENT',0)}\nUNRESOLVED: {counts.get('UNRESOLVED',0)}\n"
                f"ERROR: {counts.get('ERROR',0)}\n"
                f"DOM fields recovered: {dom_stats.get('fields_recovered',0)}\n"
                f"Evidence-safe ABSENT classifications: {counts.get('ABSENT',0)}"
            )
            update_workflow_controls()

        def save_processor_state():
            from semantic_intent import serialize_processor_state
            path=filedialog.asksaveasfilename(
                title="Save Processing State",defaultextension=".json",
                filetypes=[("RTUDES processing state","*.json"),("JSON files","*.json")]
            )
            if not path:
                return
            serialize_processor_state(
                getattr(self,"semantic_processed_records",[]) or [],
                getattr(self,"semantic_processor_summary",{}) or {},
                path
            )
            self.status_var.set(f"Processing state saved — {path}")

        acquire_btn=ttk.Button(actions,text="Continue Processing",command=acquire_unresolved)
        acquire_btn.pack(side="left")
        final_btn=ttk.Button(actions,text="Final Sweep",command=run_final_sweep)
        # Advanced compatibility control: workflow controller no longer requires users to click it.
        final_btn.pack_forget()
        ttk.Button(actions,text="Save State",command=save_processor_state).pack(side="left",padx=(6,0))
        export_btn=ttk.Button(actions,text="Export Desired CSV",command=export_desired_csv)
        export_btn.pack(side="left",padx=(6,0))
        def show_execution_trace():
            trace=self._orch_trace_summary(getattr(self,"semantic_processed_records",[]) or [])
            lines=["UNIFIED MODULE EXECUTION TRACE","="*34]
            for (stage,status),count in sorted(trace.items()):
                lines.append(f"{stage} — {status}: {count}")
            runtime_complete=sum(n for (stage,status),n in trace.items()
                                 if stage=="Runtime Evidence Handoff" and status=="COMPLETE")
            runtime_no_target=sum(n for (stage,status),n in trace.items()
                                  if stage=="Runtime URL Qualification" and status=="NO DETAIL TARGET")
            lines.append(f"Runtime evidence handoff complete: {runtime_complete}")
            lines.append(f"Runtime no-detail-target records: {runtime_no_target}")
            fusion=getattr(self,"smart_fusion_diagnostics",{}) or {}
            lines.append(
                f"Smart evidence reused: primary {fusion.get('primary',0)} | "
                f"DOM-bound {fusion.get('matched_dom',0)} | "
                f"relationship-bound {fusion.get('matched_relationships',0)} | "
                f"relationship datasets {fusion.get('relationship_groups',0)}"
            )
            sources=fusion.get('relationship_bind_sources',{}) or {}
            lines.append(
                "Relationship binding mode: dynamic evidence "
                f"(DOM scope {sources.get('dom_scope',0)} | identity {sources.get('identity',0)})"
            )
            audit=fusion.get('relationship_audit',{}) or {}
            lines.extend([
                "",
                "RELATIONSHIP EVIDENCE AUDIT",
                f"Dataset relationship records: {audit.get('dataset_records',0)}",
                f"Relationship families: {', '.join(audit.get('families',[]) or []) or '(none)'}",
                f"Valid relationship URLs: {audit.get('valid_relationship_urls',0)}",
                f"DOM-scope URL targets: {audit.get('scope_url_targets',0)}",
                f"Exact URL intersections: {audit.get('exact_intersections',0)}",
                f"Normalized URL intersections: {audit.get('normalized_intersections',0)}",
            ])
            for n,u in enumerate(audit.get('sample_relationship_urls',[]) or [],1):
                lines.append(f"Relationship URL {n}: {u}")
            for n,u in enumerate(audit.get('sample_scope_urls',[]) or [],1):
                lines.append(f"DOM-scope URL {n}: {u}")
            plan=getattr(self,"semantic_orchestration_relationship_plan",{}) or {}
            lines.extend(["",f"Recommended relationship field: {plan.get('field','(none)')}",
                          f"Record-specific relationship URLs: {len(plan.get('urls',{}))}"])
            messagebox.showinfo("Execution Trace","\n".join(lines) if lines else "No trace available.")
        ttk.Button(actions,text="Execution Trace",command=show_execution_trace).pack(side="left",padx=(6,0))
        ttk.Button(actions,text="Close",command=win.destroy).pack(side="right")
        update_workflow_controls()
        q=getattr(self,"semantic_active_acquisition_queue",[])
        integ=getattr(self,"semantic_record_integrity",{})
        scounts=(getattr(self,"semantic_processor_summary",{}) or {}).get("states",{})
        self.status_var.set(
            f"RECON043 integrity {integ.get('source_count',0)}→{integ.get('output_count',0)}; "
            f"key {getattr(self,'semantic_key_field','') or 'canonical'}; "
            f"FOUND {scounts.get('FOUND',0)}, ABSENT {scounts.get('ABSENT',0)}, "
            f"UNRESOLVED {scounts.get('UNRESOLVED',0)}, ERROR {scounts.get('ERROR',0)}; "
            f"acquisition queue {len(q)}; "
            f"DOM recovered {((getattr(self,'semantic_processor_summary',{}) or {}).get('dom_fallback',{}) or {}).get('fields_recovered',0)} field(s)."
        )

    def _record_dom_anchor(self,record,catalog):
        """Find the DOM element that most strongly identifies this record."""
        if not isinstance(record,dict) or not catalog:
            return None
        preferred=[]
        for key,value in record.items():
            lk=str(key).lower()
            v=clean_text(value)
            if not v or len(v)<6 or len(v)>180:
                continue
            pri=0
            if any(x in lk for x in ("address","name","title")): pri=4
            elif any(x in lk for x in ("id","case","parcel","listing")): pri=3
            elif "url" not in lk: pri=1
            if pri:
                preferred.append((pri,len(v),v))
        preferred.sort(reverse=True)

        best=None;best_score=0
        for _pri,_ln,needle in preferred[:8]:
            nl=needle.lower()
            for item in catalog:
                corpus=" ".join(clean_text(item.get(k,"")) for k in ("sample","context","attributes","target")).lower()
                score=0
                if nl and nl in corpus: score=100
                else:
                    # Address/name prefixes are often enough when the page normalizes punctuation.
                    toks=[t for t in re.split(r"\W+",nl) if len(t)>=3]
                    hits=sum(1 for t in toks if t in corpus)
                    if toks: score=int(90*hits/len(toks))
                if score>best_score:
                    best_score=score;best=item
        return best if best_score>=65 else None

    def _dom_record_scope(self,anchor_item,catalog):
        """Return the complete repeated-record/card scope owning an anchor.

        Prefer semantic container ancestors (card/lot/listing/result/item/property)
        before the old size heuristic. This prevents choosing an inner heading/details
        div that excludes timeline, summary, or record links.
        """
        if not anchor_item:
            return []
        path=clean_text(anchor_item.get("path",""))
        segs=[x.strip() for x in path.split(" > ") if x.strip()]
        if not segs:
            return []
        container_words=("auction-card","auction_card","card","listing","property","lot","result","record","item")
        # Deepest named record container wins.
        for cut in range(len(segs)-1,1,-1):
            seg=segs[cut-1].lower()
            if not any(w in seg for w in container_words):
                continue
            prefix=" > ".join(segs[:cut])
            scoped=[x for x in catalog if clean_text(x.get("path","")).startswith(prefix+" > ") or clean_text(x.get("path",""))==prefix]
            if 4 <= len(scoped) <= 300:
                return scoped
        # Fallback: choose the BROADEST reasonable ancestor, not the first/deepest.
        choices=[]
        for cut in range(len(segs)-1,1,-1):
            prefix=" > ".join(segs[:cut])
            scoped=[x for x in catalog if clean_text(x.get("path","")).startswith(prefix+" > ") or clean_text(x.get("path",""))==prefix]
            if 4 <= len(scoped) <= 220:
                choices.append(scoped)
        return choices[-1] if choices else []

    def _apply_existing_dom_fallback(self,prefix,records,processed,mapping):
        """Use the already-working DOM catalog only for unresolved/error fields."""
        from semantic_intent import dom_semantic_candidate, FIELD_FOUND, FIELD_UNRESOLVED, FIELD_ERROR
        catalog=list(getattr(self,"dom_catalogs",{}).get(prefix,[]) or [])
        if not catalog:
            return processed,{"records_recovered":0,"fields_recovered":0,"candidates_checked":0}

        by_name={m.get("desired",""):m for m in mapping or []}
        records_recovered=0;fields_recovered=0;checked=0
        updated=[]

        for i,(record,item) in enumerate(zip(records,processed)):
            states=dict(item.get("states",{}));row=dict(item.get("row",{}));diag=dict(item.get("diagnostics",{}))
            targets=[k for k,v in states.items() if v in (FIELD_UNRESOLVED,FIELD_ERROR)]
            if not targets:
                updated.append(item);continue

            anchor_item=self._record_dom_anchor(record,catalog)
            # DYNAMIC-RELATIONSHIP-BIND001: DOM evidence MUST be owned by this canonical record.
            # If RTUDES cannot anchor the record to a DOM container, do not borrow
            # candidates from the page-level catalog. Leave the field unresolved.
            if not anchor_item:
                new=dict(item)
                diag.setdefault("_orchestration",[]).append({
                    "stage":"Level-1 DOM Research",
                    "status":"SKIPPED",
                    "reason":"record DOM anchor not established"
                })
                new.update({"row":row,"states":states,"diagnostics":diag})
                updated.append(new)
                continue
            scoped=self._dom_record_scope(anchor_item,catalog)
            if not scoped:
                new=dict(item)
                diag.setdefault("_orchestration",[]).append({
                    "stage":"Level-1 DOM Research",
                    "status":"SKIPPED",
                    "reason":"record DOM scope not established"
                })
                new.update({"row":row,"states":states,"diagnostics":diag})
                updated.append(new)
                continue
            # Mark the underlying record so evidence-safe ABSENT logic knows
            # that its listing-level DOM boundary was actually inspected.
            if isinstance(record,dict):
                record["_dom_scope_verified"]=True
                record["_dom_scope_size"]=len(scoped)
            diag.setdefault("_orchestration",[]).append({
                "stage":"Level-1 DOM Research",
                "status":"RUN",
                "scope_elements":len(scoped)
            })
            improved=0

            for desired in targets:
                rule=by_name.get(desired,{})
                example=clean_text(rule.get("example",""))
                candidates=[]
                for dom_item in scoped:
                    rank=dom_search_score(dom_item,desired,example)
                    sem=dom_semantic_candidate(desired,dom_item)
                    checked+=1
                    value=sem.get("value","")
                    if not value:
                        continue
                    total=min(100,int(rank*.55 + sem.get("score",0)*.65))
                    # Require stronger confidence when no record-specific DOM anchor exists.
                    threshold=78 if anchor_item else 90
                    if total>=threshold:
                        candidates.append((total,dom_item,value,sem.get("reason","DOM semantic fallback")))
                candidates.sort(key=lambda x:-x[0])
                if candidates:
                    score,dom_item,value,reason=candidates[0]
                    row[desired]=value
                    states[desired]=FIELD_FOUND
                    diag[desired]={
                        "method":"existing DOM intelligence fallback",
                        "source_path":"DOM: "+clean_text(dom_item.get("path","")),
                        "score":score,
                        "reason":reason,
                        "dom_sample":clean_text(dom_item.get("sample","")),
                    }
                    improved+=1;fields_recovered+=1

            if improved:
                records_recovered+=1
            new=dict(item);new.update({"row":row,"states":states,"diagnostics":diag})
            updated.append(new)

        return updated,{
            "records_recovered":records_recovered,
            "fields_recovered":fields_recovered,
            "candidates_checked":checked
        }


    def research_desired_data(self,prefix="public"):
        """RECON031: research requested semantics across the complete current record."""
        desired=list(getattr(self,"semantic_desired_fields",[]) or [])
        if not desired:
            self.open_desired_data_dialog(prefix); return
        idx=int(getattr(self,"builder_loaded_dataset_index",-1))
        if not (0 <= idx < len(getattr(self,"detected_groups",[]) or [])):
            messagebox.showinfo("Load Dataset","Load a detected dataset before researching desired data.")
            return
        records,smart_diag=self._smart_fuse_primary_evidence(prefix,idx)
        if not records:
            records=list(self.detected_groups[idx].get("records",[]) or [])
        self.semantic_research_source_records=records
        results=research_desired_fields(records,desired,max_records=50,profile=getattr(self,"semantic_industry_profile","Auto / General"))

        # RECON045: automatically consult the existing rendered-DOM catalog only
        # when record-based semantic research is weak/unresolved.
        from semantic_intent import dom_semantic_candidate
        dom_catalog=list(getattr(self,"dom_catalogs",{}).get(prefix,[]) or [])
        if dom_catalog:
            for result in results:
                if result.get("score",0)>=90:
                    continue
                candidates=[]
                for dom_item in dom_catalog:
                    rank=dom_search_score(dom_item,result.get("desired",""),result.get("example",""))
                    sem=dom_semantic_candidate(result.get("desired",""),dom_item)
                    if sem.get("value"):
                        combined=min(100,int(rank*.55+sem.get("score",0)*.65))
                        if combined>=82:
                            candidates.append((combined,dom_item,sem))
                candidates.sort(key=lambda x:-x[0])
                if candidates and candidates[0][0] > result.get("score",0):
                    score,dom_item,sem=candidates[0]
                    result["dom_candidate"]={
                        "path":dom_item.get("path",""),"sample":dom_item.get("sample",""),
                        "value":sem.get("value",""),"score":score
                    }
                    # Keep original record source for mapping; expose DOM as corroborating/fallback evidence.
                    result["mode"]=result.get("mode","")+" + DOM fallback available"
                    result["score"]=max(result.get("score",0),min(89,score))
                    if result.get("confidence")=="Unresolved":
                        result["confidence"]="Medium"

        self.semantic_research_results=results

        win=tk.Toplevel(self)
        win.title("Deep Semantic Research Results")
        win.geometry("1180x560")
        win.transient(self)
        frame=ttk.Frame(win,padding=10); frame.pack(fill="both",expand=True)
        ttk.Label(
            frame,
            text=(
                f"Profile: {getattr(self,'semantic_industry_profile','Auto / General')}  •  "
                "Results are research candidates only. RTUDES has not changed the extraction schema."
            ),
            wraplength=1120,justify="left"
        ).pack(anchor="w",pady=(0,8))
        cols=("desired","example","source","extracted","record","sample","mode","confidence","score")
        tree=ttk.Treeview(frame,columns=cols,show="headings",height=18)
        specs=(
            ("desired","Desired Output",140),("example","Example",135),("source","Best Source / Path",190),
            ("extracted","Extracted Candidate",160),("record","Record",75),
            ("sample","Observed Source Value",260),("mode","How Found",130),
            ("confidence","Confidence",85),("score","Score",55)
        )
        for c,t,w in specs:
            tree.heading(c,text=t); tree.column(c,width=w,stretch=c in {"source","sample"})
        y=ttk.Scrollbar(frame,orient="vertical",command=tree.yview)
        x=ttk.Scrollbar(frame,orient="horizontal",command=tree.xview)
        tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        tree.pack(fill="both",expand=True,side="left"); y.pack(side="right",fill="y")
        for r in results:
            record_label = (
                f"#{r.get('record_index',-1)+1} ✓"
                if r.get("record_bound") else
                (f"#{r.get('record_index',-1)+1}" if r.get("record_index",-1)>=0 else "—")
            )
            tree.insert("","end",values=(
                r["desired"],r["example"],r["source_path"],r.get("extracted_value",""),
                record_label,r["sample_value"][:500],r["mode"],r["confidence"],r["score"]
            ))
        resolved=sum(1 for r in results if r.get("score",0)>=70)
        unresolved=[r for r in results if r.get("score",0)<70]
        deeper=sum(1 for r in unresolved if r.get("qualified_urls"))
        anchor=next((r.get("anchor_record_index",-1) for r in results if r.get("anchor_record_index",-1)>=0),-1)
        self.status_var.set(
            f"RECON032 research complete — {resolved}/{len(results)} resolved; "
            f"{len(unresolved)} unresolved; {deeper} have qualified deeper-source candidate(s). "
            + (f"Example context bound to record #{anchor+1}. " if anchor>=0 else "No stable example-record anchor established. ")
            + f"Smart evidence fusion: {smart_diag.get('matched_dom',0)}/{smart_diag.get('primary',0)} record DOM matches; "
              f"{smart_diag.get('matched_relationships',0)} relationship match(es)."
        )


    def builder_load_dataset(self, prefix="public"):
        choice = getattr(self, f"{prefix}_builder_dataset")

        if prefix=="public":
            plan=getattr(self,"semantic_extraction_plan",{}) or {}
            recommended=int(plan.get("recommended_dataset_index",-1) or -1)
            values=list(choice.cget("values") or ())
            sentinel=(
                len(values)==1
                and str(values[0]).startswith("No qualified primary dataset")
            )
            if recommended < 0 or sentinel:
                messagebox.showinfo(
                    "No Qualified Primary Dataset",
                    "RTUDES has not found a trustworthy primary entity dataset yet.\n\n"
                    "Rejected browse, taxonomy, form/UI, and support datasets remain available "
                    "under Advanced Tools for inspection, but they are blocked from the normal "
                    "Extraction Workspace.\n\n"
                    "If the site is search-driven, enter a normal search/location seed and analyze again."
                )
                self.builder_loaded_dataset_index=-1
                return

        index = choice.current()
        if not (0 <= index < len(self.detected_groups)):
            messagebox.showinfo("No Dataset", "Fetch a page and select a detected dataset first.")
            return
        item = self.detected_groups[index]
        self.builder_loaded_dataset_index = index
        source = getattr(self, f"{prefix}_builder_source")
        source["values"] = item["fields"]
        if item["fields"]:
            source.current(0)
        sample_tree = getattr(self, f"{prefix}_builder_sample_tree")
        self.clear_tree(sample_tree)
        self.builder_field_samples = {}
        for field in item["fields"]:
            samples = []
            for record in item["records"][:25]:
                value = clean_text(str(record.get(field, "")))
                if value and value not in samples:
                    samples.append(value)
                if len(samples) >= 3:
                    break
            sample = "  |  ".join(samples)[:900]
            self.builder_field_samples[field] = sample
            present = sum(1 for record in item["records"] if clean_text(str(record.get(field, ""))))
            coverage = f"{round(100*present/max(1,item['count']))}%"
            category, confidence = self._classify_discovered_field(field, samples)
            sample_tree.insert("", "end", values=(category, field, sample, coverage, confidence))
        url_fields = [f for f in item["fields"] if any(k in f.lower() for k in ("[href]", "url", "link"))]
        url_box = getattr(self, f"{prefix}_builder_detail_url_field")
        url_box["values"] = url_fields
        if url_fields:
            if item.get("dataset_type") == "relationship" and "Detail URL" in url_fields:
                url_box.current(url_fields.index("Detail URL"))
            else:
                url_box.current(0)
        # Discovery is not selection.  v1.5.0 catalogs every field but starts it
        # unchecked so the final schema contains only what the user explicitly keeps.
        self.builder_mappings = []
        for field in item["fields"]:
            sample_values = [clean_text(str(r.get(field, ""))) for r in item["records"][:25] if clean_text(str(r.get(field, "")))]
            category, _confidence = self._classify_discovered_field(field, sample_values[:3])
            output = self._uei_output_name(category, field, sample_values[:3])
            transform = "Email / mailto Clean" if category == "Email" else "Text Clean"
            self.builder_mappings.append({"source": field, "output": output, "transform": transform, "enabled": False})
        self.builder_levels = {1:{"url": (self.public_url.get().strip() if prefix=="public" else self.protected_url.get().strip()), "relationship":"Start / Listing Page", "status":f"{item['count']} records", "fields":dict(self.builder_field_samples)}}
        self.builder_current_level = 1
        self._builder_refresh_levels(prefix)
        self._builder_refresh_mapping_tree(prefix)
        self.status_var.set(f"Loaded {len(item['fields'])} fields with samples from {item['count']} repeated records. Double-click a sample row to map it.")

    def builder_use_sample_field(self, prefix="public"):
        tree = getattr(self, f"{prefix}_builder_sample_tree")
        selected = tree.selection()
        if not selected:
            return
        values = tree.item(selected[0], "values")
        field = values[1] if len(values) > 2 else values[0]
        source = getattr(self, f"{prefix}_builder_source")
        source.set(field)
        getattr(self, f"{prefix}_builder_output").set(field.split(" [",1)[0].split(" > ")[-1].replace("_"," ").title())

    def builder_use_detail_field(self, prefix="public"):
        self.builder_add_detail_mapping(prefix)

    def builder_follow_sample_url(self, prefix="public"):
        choice = getattr(self, f"{prefix}_builder_dataset")
        index = choice.current()
        if not (0 <= index < len(self.detected_groups)):
            messagebox.showinfo("No Dataset", "Load a repeated dataset first.")
            return
        field = getattr(self, f"{prefix}_builder_detail_url_field").get().strip()
        if not field:
            messagebox.showinfo("No URL Field", "Choose an href, URL, or link field first.")
            return
        url = ""
        for record in self.detected_groups[index]["records"]:
            candidate = str(record.get(field, "")).strip()
            if self.valid_url(candidate):
                url = candidate
                break
        if not url:
            messagebox.showerror("No Valid URL", "No complete http/https URL was found in the selected field.")
            return
        getattr(self, f"{prefix}_builder_detail_status").set(f"Opening sample detail page: {url}")
        self.status_var.set("Rendering and inspecting one sample detail page...")
        threading.Thread(target=self._builder_detail_worker,args=(url,prefix),daemon=True).start()

    def _builder_detail_worker(self, url, prefix, focus_builder=False):
        try:
            # Use the same rendered-page service used elsewhere in the application.
            # This keeps DOM Inspector link-following and Extraction Builder inspection
            # on one navigation path instead of maintaining duplicate browser logic.
            html_text, final_url = self._render_page_html(url, wait_ms=2500, scroll=True)
            parser = DOMParser(final_url); parser.feed(html_text)
            features = extract_universal_semantic_features(html_text, final_url, node_features(parser.root, final_url))
            # DISCOVERY001: preserve all non-empty viable candidates; ranking affects order, not visibility.
            fields = [(k, clean_text(str(v))) for k,v in features.items() if clean_text(str(v))]
            fields.sort(key=lambda kv: (0 if any(x in kv[0].lower()+" "+kv[1].lower() for x in ("mail","email","phone","company","city","address")) else 1, len(kv[0])))
            self.after(0, lambda: self._complete_detail_inspection(prefix, final_url, fields, focus_builder))
        except RuntimeError as exc:
            self.after(0, lambda e=str(exc): self._show_error("Browser Engine Missing", e))
        except Exception as exc:
            self.after(0, lambda e=str(exc): self._show_error("Detail Inspection Failed", e))

    def _complete_detail_inspection(self, prefix, final_url, fields, focus_builder=False):
        """Finish a rendered detail-page inspection on the Tk main thread.

        When the request originated from DOM Inspector, automatically reveal the
        Extraction Builder so the result is visible instead of appearing to do nothing.
        """
        self._load_builder_detail_fields(prefix, final_url, fields)
        if focus_builder:
            result_notebook = getattr(self, f"{prefix}_result_notebook", None)
            builder_tab = getattr(self, f"{prefix}_builder_tab", None)
            if result_notebook is not None and builder_tab is not None:
                try:
                    result_notebook.select(builder_tab)
                except tk.TclError:
                    pass
            self.status_var.set(
                f"Followed selected relationship → {final_url} — {len(fields)} detail field(s) found. "
                "Extraction Builder opened so you can select the second-level fields."
            )

    def _load_builder_detail_fields(self, prefix, final_url, fields, level=None, relationship="Detail Link", page_title="", field_profiles=None):
        tree = getattr(self, f"{prefix}_builder_detail_tree")
        self.clear_tree(tree)
        self.builder_detail_fields = dict(fields); self.builder_detail_url = final_url
        if level is None:
            level = max(2, self.builder_current_level if self.builder_current_level > 1 else 2)
        self.builder_current_level = level
        self.builder_levels[level] = {
            "url":final_url,"title":clean_text(page_title),"relationship":relationship,
            "status":f"{len(fields)} fields found","fields":dict(fields),
            "field_profiles":dict(field_profiles or {}),
            "relationship_role":"DETAIL" if level>1 else "LISTING"
        }
        self._builder_refresh_levels(prefix)
        profiles=dict(field_profiles or {})
        field_items=list(fields.items()) if hasattr(fields,"items") else list(fields)
        repeated_sample_counts=Counter(
            re.sub(r"\s+"," ",clean_text(str(v))).strip().lower()
            for _field,v in field_items
            if clean_text(str(v)) and len(clean_text(str(v)))<=90
        )
        candidate_labels={
            self._candidate_source_label(field)
            for field,_value in field_items
            if self._candidate_source_label(field)
        }
        field_items=sorted(
            field_items,
            key=lambda item: (
                -self._candidate_catalog_rank(
                    item[0],item[1],repeated_sample_counts,candidate_labels
                ),
                str(item[0]).lower()
            )
        )
        for field, sample in field_items:
            category, confidence = self._classify_discovered_field(field, [sample])
            rank=self._candidate_catalog_rank(
                field,sample,repeated_sample_counts,candidate_labels
            )
            if rank>=115:
                confidence="High"
            elif rank<0:
                confidence="Low"
            profile=profiles.get(field,{})
            ownership,ownership_score=self._field_ownership_class(
                category,field,sample,profile,
                detail_url=final_url,page_title=page_title
            )
            if ownership=="SHARED_CONTEXT" or self._semantic_identity_strength(
                category,field,sample
            ) < 30:
                confidence="Low"
            tree.insert("", "end", values=(category, field, sample[:900], confidence))
        getattr(self, f"{prefix}_builder_detail_status").set(f"Inspected {final_url} — {len(fields)} extractable detail-page fields found. Select fields to add to the batch detail-page mapping.")
        self.status_var.set(f"Detail-page inspection complete — {len(fields)} fields found.")

    @staticmethod
    def _looks_like_phone_value(value):
        """SEMATTR002: conservative international phone-shape validator."""
        text=clean_text(str(value))
        if not text or text.startswith(("http://","https://")):
            return False
        if text.lower().startswith("tel:"):
            text=text[4:].strip()
        scrub=re.sub(r"(?:ext\.?|extension|x)\s*\d+\s*$","",text,flags=re.I).strip()
        if re.search(r"[A-Za-z]",scrub):
            return False
        digits=re.sub(r"\D","",scrub)
        if not (7 <= len(digits) <= 15):
            return False
        groups=[int(x) for x in re.findall(r"\d+",scrub)]
        if len(groups)>=3:
            increasing=all(groups[i] < groups[i+1] for i in range(len(groups)-1))
            powers_two=all(g>0 and (g & (g-1))==0 for g in groups)
            near_geometric=all(groups[i] and groups[i+1] % groups[i] == 0
                               for i in range(len(groups)-1))
            if increasing and (powers_two or near_geometric):
                return False
        if not any(ch in scrub for ch in "+()-"):
            raw_groups=re.findall(r"\d+",scrub)
            if len(raw_groups)>4:
                return False
            if len(raw_groups)>=2 and len(raw_groups[0])>4:
                return False
        return True

    @staticmethod
    def _looks_like_complete_date_value(value):
        """Require more evidence than a lone month token such as 'Aug'."""
        text=clean_text(str(value))
        if not text:
            return False
        month=r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        patterns=(
            rf"\b{month}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{2,4}})?\b",
            rf"\b\d{{1,2}}\s+{month}(?:\s+\d{{2,4}})?\b",
            r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
            r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
        )
        return any(re.search(p,text,re.I) for p in patterns)

    @classmethod
    def _semantic_candidate_quality(cls, semantic, field, value):
        """Return 0..100 semantic plausibility from value shape + source."""
        semantic=clean_text(str(semantic))
        source=clean_text(str(field)).lower()
        value=clean_text(str(value))
        if not value:
            return 0
        if semantic=="Phone":
            return 100 if cls._looks_like_phone_value(value) else 0
        if semantic=="Email":
            return 100 if bool(re.fullmatch(
                r"(?:mailto:)?[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?:\?.*)?", value
            )) else 0
        if semantic=="Date / Time":
            return 90 if cls._looks_like_complete_date_value(value) else 20
        if semantic=="Price / Amount":
            return 95 if bool(re.search(r"(?:[$€£₱]|usd|php|eur|gbp)\s*\d",value,re.I)) else 35
        if semantic=="Image / Media":
            return 95 if bool(re.search(r"\.(?:jpg|jpeg|png|webp|gif|svg|bmp|tiff?)(?:\?|#|$)",value,re.I)) else 25
        if semantic=="Document":
            return 95 if bool(re.search(r"\.(?:pdf|docx?|xlsx?|csv|zip|pptx?|txt|rtf)(?:\?|#|$)",value,re.I)) else 20
        if semantic in {"Link / Detail","Website","Social Link"}:
            return 90 if value.startswith(("http://","https://")) else 0
        if semantic=="Name / Title":
            if len(value)>240 or value.lower() in {"home","menu","search","test sites","contact","about"}:
                return 20
            return 80
        if semantic in {"Identifier","Location","Company","Biography / Description"}:
            return 75
        return 60

    @staticmethod
    def _is_generic_structural_source(field):
        """SEMATTR004: raw structural source without meaningful record semantics."""
        low=clean_text(str(field)).strip().lower()
        if not low:
            return True
        structural_tags=(
            "html","body","main","section","article","aside","header","footer","nav",
            "div","span","p","a","img","ul","ol","li","dl","dt","dd","table","tbody",
            "thead","tr","td","th","h1","h2","h3","h4","h5","h6","strong","em","small",
            "label","button","input","form"
        )
        simple=low.split(" > ")[-1].strip()
        tag_match=re.match(r"^([a-z][a-z0-9]*)(?=[.#:\[\s]|$)",simple)
        if tag_match and tag_match.group(1) in structural_tags:
            semantic_cues=(
                "name","title","price","amount","date","time","address","location",
                "country","state","city","postal","zip","case","reference","number",
                "identifier","category","type","status","email","phone","telephone",
                "mobile","website","company","organization","description","summary",
                "document","download","file","image","photo","rating","review"
            )
            if not any(cue in simple for cue in semantic_cues):
                return True
        if ">" in low:
            meaningful=(
                "name","title","price","amount","date","time","address","location",
                "country","state","city","postal","zip","case","reference","number",
                "identifier","category","type","status","email","phone","telephone",
                "mobile","website","company","organization","description","summary",
                "document","download","file","image","photo","rating","review"
            )
            return not any(k in low for k in meaningful)
        return False

    @staticmethod
    def _candidate_source_label(field):
        field_text=clean_text(str(field))
        low=field_text.lower()
        if low.startswith(("semantic:","association:","meta:")):
            return clean_text(field_text.split(":",1)[1].split(" [",1)[0])
        return ""

    @classmethod
    def _candidate_catalog_rank(cls, field, sample, repeated_sample_counts=None, candidate_labels=None):
        """DETAILSEM006: rank clean field/value direction without hiding data."""
        field_text=clean_text(str(field))
        sample_text=clean_text(str(sample))
        low=field_text.lower()
        score=0

        if low.startswith("semantic:"):
            score+=82
        elif low.startswith("association:"):
            score+=48
        elif low.startswith("meta:"):
            score+=28
        elif low.startswith("raw:"):
            score-=40

        if "[next-sibling;distance=1]" in low:
            score+=50
        elif "next-sibling" in low:
            score+=24
        elif "parent-sibling" in low:
            score-=4
        elif "same-wrapper" in low:
            score-=18
        elif "wrapper-peer" in low:
            score-=42

        label=cls._candidate_source_label(field_text)
        normalized_sample=re.sub(r"\s+"," ",sample_text).strip().lower()
        candidate_labels={
            re.sub(r"\s+"," ",clean_text(str(x))).strip().lower()
            for x in (candidate_labels or set()) if clean_text(str(x))
        }

        # Direction validation: if the proposed VALUE is itself one of the page's
        # detected labels, this is likely the reverse association. This avoids
        # penalizing two correct representations that happen to share a value.
        if label and normalized_sample and normalized_sample in candidate_labels:
            if normalized_sample!=re.sub(r"\s+"," ",label).strip().lower():
                score-=130

        if len(sample_text)>260:
            score-=38
        elif len(sample_text)>140:
            score-=18

        parts=[clean_text(x) for x in re.split(r"\s+\|\s+",sample_text) if clean_text(x)]
        if low.startswith("semantic:") and 1<=len(parts)<=20:
            score+=12

        # Strong page-label -> value evidence.
        if label and sample_text and sample_text.lower()!=label.lower():
            score+=18

        if label and sample_text.lower()==label.lower():
            score-=120

        return score

    @staticmethod
    def _normalized_candidate_value(value):
        return re.sub(r"[^a-z0-9]+"," ",clean_text(str(value)).lower()).strip()

    @classmethod
    def _strong_labeled_values(cls, fields):
        """Values attached to independently supported close label/value pairs."""
        values=set()
        strong_labels=cls._strong_structural_labels(fields or {})
        for field,value in (fields or {}).items():
            label=cls._candidate_source_label(field)
            if cls._normalized_candidate_value(label) not in strong_labels:
                continue
            text=clean_text(str(value))
            if text:
                values.add(text)
        return values

    @classmethod
    def _generic_candidate_duplicates_labeled_value(cls, field, sample, labeled_values):
        """True when an unlabeled candidate is already represented by a page label."""
        low=clean_text(str(field)).lower()
        if low.startswith(("semantic:","association:","meta:")):
            return False
        target=cls._normalized_candidate_value(sample)
        if not target:
            return False
        for value in labeled_values or set():
            other=cls._normalized_candidate_value(value)
            if not other:
                continue
            if target==other:
                return True
            # Conservative overlap test for container/raw variants.
            if len(target)>=14 and len(other)>=14 and (target in other or other in target):
                return True
        return False

    @staticmethod
    def _looks_like_scalar_labeled_value(value):
        """RECON004: distinguish scalar names/codes/short facts from prose."""
        text=clean_text(str(value))
        if not text:
            return False
        words=[w for w in re.split(r"\s+",text) if w]
        probe=re.sub(r"\b[A-Za-z]\.\s+", "", text)
        probe=re.sub(
            r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Mt|Inc|Ltd|Co|Corp)\.\s+",
            "", probe, flags=re.I
        )
        sentence_breaks=len(re.findall(
            r"[.!?](?:\s+[A-Z][a-z]{2,}\b|$)", probe
        ))
        if len(words)>16:
            return False
        if len(words)>11 and sentence_breaks>=1:
            return False
        return sentence_breaks<2

    @classmethod
    def _label_semantic_intent(cls, label):
        """RECON023: infer broad field intent from the label alone."""
        text=clean_text(str(label)).lower()
        if not text:
            return ""
        cue_map=(
            ("Email",("email","e-mail")),
            ("Phone",("phone","telephone","mobile","fax")),
            ("Location",("address","street","city","state","province","region","county","country","postal","postcode","zip","location")),
            ("Name / Title",("name","title","contact person","owner","defendant","plaintiff","broker","agent")),
            ("Company",("company","organization","organisation","firm","business name","employer")),
            ("Website",("website","web site","homepage")),
            ("Date / Time",("date","time","day","deadline")),
            ("Price / Amount",("price","amount","cost","fee","deposit","balance")),
            ("Identifier",("identifier","case","reference","number","code")),
            ("Biography / Description",("description","summary","bio","biography","about","details","information")),
            ("Text",("specialty","specialties","category","categories","industry","industries","service","services","type","status")),
        )
        for semantic,cues in cue_map:
            if any(cue in text for cue in cues):
                return semantic
        return ""

    @classmethod
    def _label_value_semantic_compatible(cls, label, value):
        """RECON023: reject close DOM pairings when label meaning conflicts with value shape."""
        label_text=clean_text(str(label))
        value_text=clean_text(str(value))
        if not label_text or not value_text:
            return True

        if re.fullmatch(r"(?:a|p|div|span|small|strong|label|st|li|td|th|h[1-6])(?:[.:#_-]?\d+)?", label_text, re.I):
            return False

        parts=[clean_text(v) for v in re.split(r"\s+\|\s+",value_text) if clean_text(v)] or [value_text]
        expected=cls._label_semantic_intent(label_text)
        observed=[]
        for part in parts[:5]:
            kind,_confidence=cls._classify_discovered_field("__value__", [part])
            if kind:
                observed.append(kind)

        strong_observed={
            x for x in observed
            if x in {
                "Email","Phone","Location","Link / Detail","Document","Image / Media",
                "Date / Time","Price / Amount","Identifier"
            }
        }

        compatible={
            "Email":{"Email"},
            "Phone":{"Phone"},
            "Location":{"Location"},
            "Website":{"Link / Detail"},
            "Date / Time":{"Date / Time"},
            "Price / Amount":{"Price / Amount"},
            "Identifier":{"Identifier","Text","Name / Title"},
            "Name / Title":{"Name / Title","Text"},
            "Company":{"Name / Title","Text"},
            "Biography / Description":{"Text","Name / Title"},
            "Text":{"Text","Name / Title","List / Group"},
        }
        if expected and strong_observed:
            allowed=compatible.get(expected,{expected})
            if not any(obs in allowed for obs in strong_observed):
                return False
        if not expected and strong_observed:
            return False
        return True


    @classmethod
    def _label_role_fitness(cls, label, value=""):
        """DETAILSEM009: conservative reusable-field-label test for auto-selection."""
        label_text=clean_text(str(label)).rstrip(":").strip()
        value_text=clean_text(str(value)).strip()
        if not label_text:
            return False

        words=[w for w in re.split(r"\s+",label_text) if w]
        if len(label_text)>72 or len(words)>8:
            return False

        if label_text.endswith((".", "!", "?")):
            return False
        if sum(ch in ".!?" for ch in label_text)>=2:
            return False
        if sum(ch.isdigit() for ch in label_text)>=4:
            return False
        if any(ch in label_text for ch in ("$", "€", "£", "¥", "%")):
            return False
        if len(words)>=5 and "," in label_text:
            return False

        alpha=sum(ch.isalpha() for ch in label_text)
        visible=sum(ch.isalnum() for ch in label_text)
        if alpha<2 or visible==0:
            return False

        if value_text:
            # Discovery may aggregate sampled values from multiple records using
            # " | ". Evaluate each observed value independently so a reusable
            # field is not rejected merely because the aggregate string is long.
            value_parts=[
                clean_text(v)
                for v in re.split(r"\s+\|\s+",value_text)
                if clean_text(v)
            ] or [value_text]

            valid_parts=0
            for part in value_parts:
                if cls._normalized_candidate_value(label_text)==cls._normalized_candidate_value(part):
                    continue
                if cls._looks_like_scalar_labeled_value(part):
                    valid_parts+=1

            # A heading followed only by long prose is still not promoted to a
            # scalar field automatically. That content remains manually selectable.
            if valid_parts==0:
                return False
            if not cls._label_value_semantic_compatible(label_text,value_text):
                return False
        return True

    @classmethod
    def _strong_structural_labels(cls, fields):
        """Return page-derived labels independently supported by close structure."""
        labels=set()
        for field,value in (fields or {}).items():
            field_text=clean_text(str(field))
            low=field_text.lower()
            if not low.startswith("association:"):
                continue
            if "[next-sibling;distance=1]" not in low:
                continue
            label=cls._candidate_source_label(field_text)
            if not cls._label_role_fitness(label,value):
                continue
            norm=cls._normalized_candidate_value(label)
            if norm:
                labels.add(norm)
        return labels

    @classmethod
    def _recommendation_provenance_gate(cls, category, field, sample,
                                        page_title="", labeled_values=None,
                                        strong_structural_labels=None):
        """DETAILSEM010: recommendation admission only; never affects discovery visibility."""
        field_text=clean_text(str(field))
        sample_text=clean_text(str(sample))
        low=field_text.lower()

        strong_structural_labels={
            cls._normalized_candidate_value(x)
            for x in (strong_structural_labels or set())
            if cls._normalized_candidate_value(x)
        }
        source_label=cls._candidate_source_label(field_text)
        source_label_norm=cls._normalized_candidate_value(source_label)

        # DETAILSEM008 principle retained: semantic discovery alone does not prove fieldness.
        # DETAILSEM009 adds role validation: closeness is necessary but not sufficient.
        # The left side must resemble a reusable field label, not prose/value text.
        label_role_ok=cls._label_role_fitness(source_label,sample_text)

        if low.startswith("semantic:"):
            return bool(
                label_role_ok and
                source_label_norm and
                source_label_norm in strong_structural_labels
            )

        if low.startswith("association:"):
            return bool(
                label_role_ok and
                "[next-sibling;distance=1]" in low
            )

        if low.startswith("meta:"):
            return True

        # If a clean labelled representation already exists, keep this raw/generic
        # representation visible but do not auto-select it.
        if cls._generic_candidate_duplicates_labeled_value(
            field_text,sample_text,labeled_values or set()
        ):
            return False

        # Machine-shaped values are useful even without a visible page label.
        if category in {
            "Email","Phone","Date / Time","Price / Amount",
            "Image / Media","Document","Identifier"
        }:
            return True

        if category=="Link / Detail":
            return bool(sample_text.startswith(("http://","https://")))

        # Generic location is auto-selected only when the source itself carries
        # structural location/address evidence; arbitrary city-like visible text
        # remains available for manual selection.
        if category=="Location":
            return any(token in low for token in (
                "address","location","street","city","state","postal","postcode",
                "zip","region","country","map"
            ))

        # Generic title/name is allowed only when it strongly represents the
        # inspected record's own page title.
        if category=="Name / Title":
            if not page_title:
                return False
            a=cls._normalized_candidate_value(sample_text)
            b=cls._normalized_candidate_value(page_title)
            if not a or not b:
                return False
            return a==b or (len(a)>=12 and len(b)>=12 and (a in b or b in a))

        # Description-like fields require explicit structural/source evidence.
        if category=="Biography / Description":
            return any(token in low for token in (
                "description","summary","bio","biography","content","body","details"
            ))

        # Plain Text and unknown generic visible text stay selectable but are
        # intentionally excluded from automatic schema construction.
        return False

    @classmethod
    def _semantic_identity_strength(cls, semantic, field, sample):
        """Return 0..100 for semantic meaningfulness of a discovered source."""
        semantic=clean_text(str(semantic))
        field_text=clean_text(str(field))
        sample_text=clean_text(str(sample))
        if cls._is_generic_structural_source(field_text):
            return 15
        low=field_text.lower()
        score=45
        cue_map={
            "Name / Title":("name","title","headline"),
            "Price / Amount":("price","amount","cost","fee"),
            "Date / Time":("date","time","day"),
            "Email":("email","e-mail","mailto"),
            "Phone":("phone","telephone","mobile","tel"),
            "Location":("address","location","city","state","country","postal","zip"),
            "Identifier":("id","identifier","case","reference","number","code"),
            "Image / Media":("image","photo","thumbnail","media","src"),
            "Document":("document","download","file","pdf","attachment"),
            "Biography / Description":("description","summary","bio","content"),
            "Company":("company","organization","business"),
            "Website":("website","site","homepage"),
        }
        if any(c in low for c in cue_map.get(semantic,())):
            score+=35
        if semantic=="Link / Detail" and sample_text.startswith(("http://","https://")):
            score+=25
        if semantic=="Name / Title" and 1<=len(sample_text.split())<=16:
            score+=10
        return max(0,min(100,score))

    @classmethod
    def _auto_output_eligible(cls, semantic, field, output, sample,
                              ownership="AMBIGUOUS", identity_strength=0):
        """SEMATTR005: final admission gate for automatic output fields."""
        semantic=clean_text(str(semantic))
        field_text=clean_text(str(field))
        output_text=clean_text(str(output))

        if cls._is_generic_structural_source(field_text):
            return False

        structural_labels={
            "html","body","main","section","article","aside","header","footer","nav",
            "div","span","p","a","img","ul","ol","li","dl","dt","dd","table","tbody",
            "thead","tr","td","th","h1","h2","h3","h4","h5","h6","strong","em","small",
            "label","button","input","form","text","value","content"
        }
        normalized=re.sub(r"[^a-z0-9]+"," ",output_text.lower()).strip()
        if normalized in structural_labels:
            return False

        if semantic in {"Text",""}:
            if identity_strength < 55:
                return False
            if normalized in structural_labels or len(normalized) < 3:
                return False

        if ownership=="SHARED_CONTEXT":
            return False

        if semantic in {
            "Email","Phone","Website","Social Link","Company","Image / Media"
        } and ownership!="RECORD":
            return False

        if not re.search(r"[a-zA-Z]",output_text):
            return False

        return True

    @classmethod
    def _record_ownership_score(cls, semantic, field, sample, profile=None,
                                detail_url="", page_title=""):
        """SEMATTR003: estimate whether a valid value belongs to the record.

        This score deliberately combines several generic signals. A constant
        value is not rejected merely for being constant: explicit record labels
        can keep legitimate shared attributes (for example country/currency),
        while shell/contact/social values repeated across sibling records are
        treated as shared context rather than record attributes.
        """
        profile = profile or {}
        semantic = clean_text(str(semantic))
        field_text = clean_text(str(field))
        low = field_text.lower()
        sample_text = clean_text(str(sample))
        score = 50

        sample_count = int(profile.get("sample_count") or 0)
        presence_count = int(profile.get("presence_count") or 0)
        unique_count = int(profile.get("unique_count") or 0)
        presence_ratio = float(profile.get("presence_ratio") or 0.0)
        dominant_ratio = float(profile.get("dominant_ratio") or 0.0)
        unique_ratio = float(profile.get("unique_ratio") or 0.0)

        # Record-varying values are strong ownership evidence.
        if sample_count >= 3:
            if presence_ratio >= 0.60 and unique_ratio >= 0.60:
                score += 32
            elif presence_ratio >= 0.60 and unique_ratio >= 0.35:
                score += 18

            # Repeated identical values are ambiguous; penalize only in
            # combination with semantic/source context below.
            if presence_ratio >= 0.80 and dominant_ratio >= 0.80:
                score -= 18

        identity_strength=cls._semantic_identity_strength(
            semantic,field_text,sample_text
        )
        if identity_strength < 30:
            score -= 38
        elif identity_strength >= 70:
            score += 16

        # Explicit label/value structures are stronger than generic raw paths.
        explicit_label = bool(re.search(
            r"(?:^|[>:/._\-\s])"
            r"(?:name|title|price|amount|date|time|address|location|country|state|"
            r"city|postal|zip|case|reference|number|id|identifier|category|type|"
            r"status|email|phone|telephone|mobile|website|company|organization|"
            r"description|summary|document|download|file)"
            r"(?:$|[>:/._\-\s])",
            low
        ))
        if explicit_label:
            score += 14

        # Site-shell / chrome sources are weak ownership evidence.
        if any(k in low for k in (
            "header","footer","navbar","navigation","menu","cookie","consent",
            "sidebar","topbar","toolbar","branding","brand","logo","site-title",
            "site_title","social","share","follow-us","follow_us","copyright",
            "announcement","promo","promotion"
        )):
            score -= 45

        # These types are frequently globally repeated. Constant values need
        # stronger record-local evidence before automatic promotion.
        context_sensitive = {
            "Email","Phone","Website","Social Link","Company","Image / Media"
        }
        if semantic in context_sensitive and sample_count >= 3:
            if presence_ratio >= 0.80 and dominant_ratio >= 0.80:
                score -= 56
                if explicit_label and identity_strength >= 75:
                    score += 14

        # Structured record facts may legitimately repeat across records.
        stable_record_types = {
            "Location","Identifier","Date / Time","Price / Amount",
            "Biography / Description","Document"
        }
        if semantic in stable_record_types and explicit_label:
            score += 10

        # Current page identity is record-local evidence.
        if semantic == "Name / Title" and page_title:
            score += cls._detail_context_match_score(
                sample_text, semantic, {"Page Title": page_title}
            )

        # The actual current record URL is always record-owned.
        if semantic == "Link / Detail" and detail_url:
            vals = [clean_text(v) for v in re.split(r"\s+\|\s+", sample_text) if clean_text(v)]
            if any(v.rstrip("/") == detail_url.rstrip("/") for v in vals):
                score += 60

        return max(-100, min(140, score))

    @classmethod
    def _field_ownership_class(cls, semantic, field, sample, profile=None,
                               detail_url="", page_title=""):
        score = cls._record_ownership_score(
            semantic, field, sample, profile,
            detail_url=detail_url, page_title=page_title
        )
        if score >= 68:
            return "RECORD", score
        if score <= 28:
            return "SHARED_CONTEXT", score
        return "AMBIGUOUS", score

    @staticmethod
    def _classify_discovered_field(field, samples):
        joined = " ".join([field, *[str(x) for x in samples]]).lower()
        vals = [clean_text(str(x)) for x in samples if clean_text(str(x))]
        field_low = field.lower()
        if field_low.startswith(("semantic:","association:")):
            semantic_label=clean_text(field.split(":",1)[1].split(" [",1)[0])
            joined=(" ".join([semantic_label,*vals])).lower()
            field_low=semantic_label.lower()
        leaf = re.split(r"[. >\[]", field_low)[-1].replace("]", "")
        if "[list]" in field_low:
            return "List / Group", "High"

        # Structured/API fields often expose numeric coordinates that can look
        # phone-like. Field semantics take precedence for known geo attributes.
        if leaf in {"latitude", "longitude", "lat", "lng", "lon"} or any(
            token in field_low for token in (".latitude", ".longitude", "[latitude]", "[longitude]")
        ):
            return "Coordinate", "High"
        if any(k in field_low for k in (
            "address", "city", "state", "region", "location", "postal", "postcode", "zip"
        )):
            return "Location", "High" if vals else "Medium"

        # Validate the extracted value before trusting semantic words in a DOM path.
        # A tracking/image URL containing digits must never be promoted to Phone.
        if any(v.startswith("mailto:") or re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", v, re.I) for v in vals):
            return "Email", "High"
        if any(v.startswith("tel:") and ScraperApp._looks_like_phone_value(v) for v in vals):
            return "Phone", "High"
        if "[src]" in field_low or any(re.search(r"\.(jpg|jpeg|png|webp|gif|svg|bmp|tiff?)(?:\?|#|$)", v, re.I) for v in vals):
            return "Image / Media", "High"
        if "[href]" in field_low or any(v.startswith(("http://","https://")) for v in vals):
            if any(re.search(r"\.(pdf|docx?|xlsx?|csv|zip|pptx?|txt|rtf)(?:\?|#|$)", v, re.I) for v in vals):
                return "Document", "High"
            return "Link / Detail", "High"
        if "email" in joined and not vals:
            return "Email", "Medium"
        non_url_vals = [v for v in vals if not v.startswith(("http://", "https://"))]
        if any(ScraperApp._looks_like_phone_value(v) for v in non_url_vals):
            return "Phone", "High"
        # Do not infer Phone merely because ordinary anchor/text samples contain
        # words such as "iPhone" or "telephone". A phone classification requires
        # a phone-semantic field name or an actually phone-like value.
        if any(k in field_low for k in ("phone","telephone","mobile","tel")) and non_url_vals:
            if any(ScraperApp._looks_like_phone_value(v) for v in non_url_vals):
                return "Phone", "Medium"
        if any(re.search(r"(?:[$€£₱]|usd|php|eur|gbp)\s?\d", v, re.I) for v in vals): return "Price / Amount", "High"
        if any(ScraperApp._looks_like_complete_date_value(v) for v in vals):
            return "Date / Time", "Medium"
        if any(k in joined for k in ("address","city","state","region","location","postal","postcode","zip")): return "Location", "Medium"
        if any(k in joined for k in ("id]","case","sku","number","code")): return "Identifier", "Medium"
        if any(k in joined for k in ("title","h1","h2","h3","h4","name")): return "Name / Title", "Medium"
        return "Text", "Medium" if vals else "Low"

    @staticmethod
    def _detail_candidate_score(semantic, source, value, confidence="Medium"):
        """Score a detail-page candidate using value evidence and DOM context.

        This deliberately separates *what a value looks like* from *where it
        lives*. Global header/footer/cookie/tracking controls are heavily
        penalized while profile/contact/content containers are preferred.
        The scorer is website-agnostic: it uses semantic value shapes and
        structural vocabulary rather than site names or fixed selectors.
        """
        source_low = clean_text(str(source)).lower()
        value_clean = clean_text(str(value))
        value_low = value_clean.lower()
        score = 18 if confidence == "High" else 8 if confidence == "Medium" else 0

        # Global chrome, consent, analytics and non-content areas should not win
        # merely because they contain a valid-looking phone, URL or label.
        global_noise = (
            "header", "footer", "navbar", "navigation", "menu", "topbar",
            "cookie", "consent", "cky-", "modal", "popup", "noscript",
            "analytics", "tracking", "pixel", "recaptcha", "captcha",
            "social-share", "site-header", "site-footer"
        )
        for token in global_noise:
            if token in source_low:
                score -= 34

        content_signals = (
            "profile", "contact", "information", "info", "detail", "member",
            "person", "broker", "agent", "content", "main", "address",
            "company", "firm", "organization", "organisation", "phone",
            "email", "website", "bio", "about"
        )
        for token in content_signals:
            if token in source_low:
                score += 5

        if semantic == "Phone":
            if value_low.startswith("tel:"):
                score += 35
                phone = value_clean[4:]
            elif value_clean.startswith(("http://", "https://")):
                return -100
            else:
                phone = value_clean
            digits = re.sub(r"\D", "", phone)
            if 7 <= len(digits) <= 15:
                score += 30
            else:
                score -= 45
            if any(k in source_low for k in ("phone", "telephone", "mobile", "contact", "informationphone")):
                score += 28

        elif semantic == "Email":
            if value_low.startswith("mailto:") or re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", value_clean, re.I):
                score += 45
            else:
                score -= 40
            if any(k in source_low for k in ("email", "mail", "contact")):
                score += 22

        elif semantic == "Location":
            # Prefer genuine address/location shapes over nearby company names.
            street = re.search(r"\b\d{1,6}\s+[A-Za-z0-9 .'-]{2,}\b(?:st|street|rd|road|ave|avenue|blvd|boulevard|dr|drive|ln|lane|ct|court|way|pkwy|parkway|suite|ste)\b", value_clean, re.I)
            city_state = re.search(r"\b[A-Za-z .'-]{2,},\s*[A-Z]{2}(?:\s+\d{5}(?:-\d{4})?)?\b", value_clean)
            zip_code = re.search(r"\b\d{5}(?:-\d{4})?\b", value_clean)
            if street: score += 48
            if city_state: score += 30
            if zip_code: score += 18
            if any(k in source_low for k in ("address", "location", "city", "state", "postal", "zip", "map")):
                score += 28
            if re.search(r"\b(?:llc|inc\.?|corp\.?|company|partners|advisors|advisory|group|realty|brokers?)\b", value_clean, re.I) and not (street or city_state):
                score -= 32

        elif semantic == "Company":
            if any(k in source_low for k in ("company", "firm", "organization", "organisation", "employer", "business")):
                score += 35
            if re.search(r"\b(?:llc|inc\.?|corp\.?|company|partners|advisors|advisory|group|realty|brokers?|associates)\b", value_clean, re.I):
                score += 24
            if re.search(r"\b\d{1,6}\s+.*\b(?:st|street|rd|road|ave|avenue|blvd|boulevard|dr|drive)\b", value_clean, re.I):
                score -= 28

        elif semantic in {"Website", "Social Link", "Link / Detail"}:
            if value_clean.startswith(("http://", "https://")):
                score += 32
            else:
                score -= 35
            if semantic == "Social Link" and any(d in value_low for d in ("linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com/", "youtube.com", "tiktok.com")):
                score += 35

        elif semantic == "Biography / Description":
            if len(value_clean) >= 80: score += 35
            elif len(value_clean) < 35: score -= 25
            if any(k in source_low for k in ("bio", "biography", "about", "description", "summary")):
                score += 25

        elif semantic == "Name / Title":
            if any(k in source_low for k in ("h1", "h2", "h3", "name", "title", "profile")):
                score += 20
            if 1 <= len(value_clean.split()) <= 8:
                score += 12

        elif semantic == "Image / Media":
            if re.search(r"\.(?:jpg|jpeg|png|webp|gif)(?:\?|#|$)", value_clean, re.I):
                score += 30
            if any(k in source_low for k in ("avatar", "profile", "photo", "image")):
                score += 18
            if any(k in source_low for k in ("logo", "icon", "tracking", "pixel")):
                score -= 25

        elif semantic == "Document":
            if re.search(r"\.(?:pdf|docx?|xlsx?|csv|zip|pptx?|txt|rtf)(?:\?|#|$)", value_clean, re.I):
                score += 45

        return score

    @staticmethod
    def _detail_context_match_score(value, semantic, listing_context=None):
        """DETAILSEM001: score a Page-2 value against the Page-1 record identity.

        This is deliberately domain agnostic.  A detail title/description/image
        that agrees with the listing record is stronger evidence than a global
        site heading, logo, breadcrumb or section link.
        """
        listing_context=listing_context or {}
        value_clean=clean_text(str(value))
        if not value_clean:
            return 0

        def norm_tokens(text):
            return {
                t for t in re.findall(r"[a-z0-9]+",clean_text(str(text)).lower())
                if len(t)>=2 and t not in {
                    "the","and","for","with","from","this","that","www","http","https",
                    "com","org","net","html","static","test","site","sites"
                }
            }

        semantic_low=clean_text(str(semantic)).lower()
        best=0
        context_values=[]
        for key,val in listing_context.items():
            if val is None:
                continue
            k=clean_text(str(key)).lower()
            text=clean_text(str(val))
            if not text:
                continue
            if semantic=="Name / Title" and any(x in k for x in ("name","title","link text","link_text","headline")):
                context_values.append(text)
            elif semantic=="Biography / Description" and any(x in k for x in ("description","summary","snippet","content")):
                context_values.append(text)
            elif semantic=="Image / Media" and any(x in k for x in ("image","photo","thumbnail","media")):
                context_values.append(text)
            elif semantic=="Price / Amount" and "price" in k:
                context_values.append(text)
            elif semantic=="Identifier" and any(x in k for x in ("id","sku","code","number")):
                context_values.append(text)

        for ref in context_values:
            if value_clean.lower()==ref.lower():
                best=max(best,60)
                continue
            if semantic=="Image / Media":
                try:
                    if urlparse(value_clean).path.rsplit("/",1)[-1] == urlparse(ref).path.rsplit("/",1)[-1]:
                        best=max(best,45)
                except Exception:
                    pass
                continue
            a=norm_tokens(value_clean); b=norm_tokens(ref)
            if not a or not b:
                continue
            overlap=len(a & b)
            union=len(a | b)
            ratio=overlap/max(1,union)
            coverage=overlap/max(1,min(len(a),len(b)))
            if coverage>=0.80:
                best=max(best,48)
            elif coverage>=0.60:
                best=max(best,34)
            elif ratio>=0.35:
                best=max(best,22)
        return best

    @staticmethod
    def _detail_scalar_parts(value, semantic):
        """RECON017: split aggregate contact values without site-specific rules.

        Discovery intentionally preserves aggregate evidence.  On a single-entity
        detail page, however, Email/Phone should resolve to one record-owned value,
        not a pipe-separated set that may include related cards or site chrome.
        """
        text=clean_text(str(value))
        if semantic not in {"Email","Phone"} or " | " not in text:
            return [text] if text else []
        parts=[clean_text(x) for x in re.split(r"\s+\|\s+",text) if clean_text(x)]
        return list(dict.fromkeys(parts)) or ([text] if text else [])

    @staticmethod
    def _detail_identity_affinity(value, semantic, listing_context=None):
        """RECON017: generic Page-1 identity affinity for scalar contact values."""
        if semantic!="Email":
            return 0
        refs=[]
        for key,val in (listing_context or {}).items():
            k=clean_text(str(key)).lower()
            if any(t in k for t in ("name","title","link text","link_text","headline")):
                refs.append(clean_text(str(val)))
        email=clean_text(str(value)).lower().removeprefix("mailto:").split("?",1)[0]
        local=email.split("@",1)[0] if "@" in email else ""
        local_tokens=set(re.findall(r"[a-z]{3,}",re.sub(r"[._+\-]+"," ",local)))
        compact=re.sub(r"[^a-z0-9]","",local)
        best=0
        for ref in refs:
            tokens=[t for t in re.findall(r"[a-z0-9]+",ref.lower()) if len(t)>=3]
            if not tokens:
                continue
            hits=sum(1 for t in tokens if t in local_tokens or t in compact)
            if hits>=2: best=max(best,55)
            elif hits==1: best=max(best,28)
        return best

    def _best_detail_candidate(self, candidates, semantic, mapping=None, used_sources=None,
                               detail_url="", listing_context=None):
        """Return the best entity-context candidate for one mapping.

        RECON017 resolves scalar contact aggregates to one value and uses Page-1
        identity plus DOM context to avoid neighboring entities and shared chrome.
        """
        used_sources = used_sources or set()
        mapping = mapping or {}
        wanted_source = mapping.get("source", "")
        wanted_sample = clean_text(str(mapping.get("sample", "")))
        ranked = []
        expanded=[]
        for candidate in candidates:
            parts=self._detail_scalar_parts(candidate.get("value",""), semantic or candidate.get("type",""))
            if len(parts)<=1:
                expanded.append(candidate)
                continue
            for pos,part in enumerate(parts):
                child=dict(candidate)
                child["value"]=part
                child["aggregate_count"]=len(parts)
                child["aggregate_position"]=pos
                expanded.append(child)
        for candidate in expanded:
            if candidate.get("source") in used_sources:
                continue
            if semantic and candidate.get("type") != semantic:
                continue
            candidate_semantic=semantic or candidate.get("type","Text")
            quality=self._semantic_candidate_quality(
                candidate_semantic,candidate.get("source",""),candidate.get("value","")
            )
            if candidate_semantic in {
                "Phone","Email","Date / Time","Price / Amount",
                "Image / Media","Document","Link / Detail","Website","Social Link"
            } and quality < 50:
                continue

            score = self._detail_candidate_score(
                candidate_semantic,
                candidate.get("source", ""),
                candidate.get("value", ""),
                candidate.get("confidence", "Medium")
            )
            if quality:
                score += min(16,int(quality/7))
            # Stable DOM paths are useful evidence, but they no longer trump a
            # semantically implausible value or a global header/footer match.
            if wanted_source and candidate.get("source") == wanted_source:
                score += 24
            elif wanted_source:
                wanted_tokens = set(re.findall(r"[a-z][a-z0-9_-]{2,}", wanted_source.lower()))
                candidate_tokens = set(re.findall(r"[a-z][a-z0-9_-]{2,}", candidate.get("source", "").lower()))
                score += min(18, len(wanted_tokens & candidate_tokens) * 3)
            if wanted_sample and candidate.get("value") == wanted_sample:
                # Same sample value is weak evidence across different records.
                score += 4

            # DETAILSEM001: identity agreement with Page 1 is strong evidence.
            score += self._detail_context_match_score(
                candidate.get("value",""), semantic or candidate.get("type","Text"),
                listing_context
            )
            score += self._detail_identity_affinity(
                candidate.get("value",""), semantic or candidate.get("type","Text"),
                listing_context
            )
            # Single-entity detail pages should not export a neighboring-entity
            # contact bundle. Preserve discovery, but prefer a scalar child. DOM
            # order is only a weak tie-breaker after semantic/context evidence.
            if candidate.get("aggregate_count",0)>1:
                score -= 6
                score += max(0,4-int(candidate.get("aggregate_position",0))*2)

            # For link candidates on a detail page, prefer the current page or
            # equally/deeper record paths; penalize shallower site-shell links.
            if (semantic or candidate.get("type"))=="Link / Detail" and detail_url:
                cand_url=clean_text(str(candidate.get("value","")))
                if cand_url.startswith(("http://","https://")):
                    try:
                        here=urlparse(detail_url); there=urlparse(cand_url)
                        here_depth=len([x for x in here.path.split("/") if x])
                        there_depth=len([x for x in there.path.split("/") if x])
                        if cand_url.rstrip("/")==detail_url.rstrip("/"):
                            score += 70
                        elif here.netloc==there.netloc and there_depth < max(1,here_depth-1):
                            score -= 45
                    except Exception:
                        pass
            ranked.append((score, candidate))
        ranked.sort(key=lambda x: x[0], reverse=True)
        if not ranked or ranked[0][0] < 18:
            return None
        return ranked[0][1]

    @staticmethod
    def _friendly_recommended_output(field, category):
        """Return a useful, collision-resistant output name for nested records."""
        field_low = clean_text(str(field)).lower()
        leaf = re.split(r"[. >\[]", field_low)[-1].replace("]", "")
        scope = ""
        if ".company." in field_low or field_low.startswith("company."):
            scope = "Company "
        elif field_low.startswith("branch."):
            scope = "Branch "

        specific = {
            "postcode": "Postcode", "postalcode": "Postcode", "zipcode": "Postcode", "zip": "Postcode",
            "city": "City", "region": "Region", "state": "State",
            "address1": "Address 1", "address2": "Address 2", "address3": "Address 3", "address4": "Address 4",
            "shortaddress": "Short Address", "longaddress": "Long Address",
            "latitude": "Latitude", "longitude": "Longitude", "lat": "Latitude", "lng": "Longitude", "lon": "Longitude",
            "managername": "Manager Name", "website": f"{scope}Website".strip(),
            "email": f"{scope}Email".strip(), "phone": f"{scope}Phone".strip(),
            "name": f"{scope}Name".strip(),
        }
        if leaf in specific:
            return specific[leaf]
        defaults={
            "Name / Title": f"{scope}Name".strip() or "Name",
            "Link / Detail": f"{scope}Website".strip() or "Detail URL",
            "Email": f"{scope}Email".strip() or "Email",
            "Phone": f"{scope}Phone".strip() or "Phone",
            "Location": leaf.replace("_"," ").title() if leaf else "Location",
            "Coordinate": leaf.title() if leaf else "Coordinate",
            "Image / Media":"Image URL", "Document":"Document URL",
            "Price / Amount":"Price", "Date / Time":"Date", "Identifier":"Identifier"
        }
        return defaults.get(category, field.split(" [",1)[0].split(" > ")[-1].replace("_"," ").replace("."," ").title())

    @staticmethod
    def _listing_recommendation_score(field, category, confidence, samples):
        """Rank useful business fields ahead of internal API plumbing.

        The ranking is source-agnostic and relies on semantic path vocabulary,
        not site names or fixed endpoints.
        """
        low = clean_text(str(field)).lower()
        leaf = re.split(r"[. >\[]", low)[-1].replace("]", "")
        score = 24 if confidence == "High" else 12 if confidence == "Medium" else 0

        # Core business/contact/location information should dominate defaults.
        if category == "Name / Title": score += 72
        elif category == "Email": score += 68
        elif category == "Phone": score += 66
        elif category == "Location": score += 64
        elif category == "Link / Detail": score += 58
        elif category == "Coordinate": score += 18
        elif category == "Document": score += 26
        elif category == "Image / Media": score += 8
        elif category == "Identifier": score += 4
        elif category == "Text": score += 2

        boosts = {
            "postcode": 34, "postalcode": 34, "zipcode": 34, "zip": 34,
            "address1": 32, "city": 31, "region": 27, "state": 27,
            "longaddress": 24, "shortaddress": 22, "managername": 14,
            "website": 22, "email": 22, "phone": 22, "name": 20,
        }
        score += boosts.get(leaf, 0)
        if ".company." in low or low.startswith("company."):
            score += 4
        if low.startswith("branch.") and leaf in {"name","email","phone","postcode","city","region","address1","website"}:
            score += 7

        # Internal identifiers, presentation media, operating-hour fragments and
        # search metadata remain selectable manually but should not crowd out
        # business fields in Recommended.
        penalties = (
            ("branchid", 70), ("reference", 62), (".id", 58), ("logo", 50),
            ("image", 42), ("hours", 48), ("alt", 36), ("charity", 28),
            ("showroom", 24), ("tradecounter", 24), ("freeparking", 24),
            ("distance", 34), ("letter", 34), ("counter", 28),
        )
        for token, penalty in penalties:
            if token in low:
                score -= penalty
        if samples and all(str(v).strip().lower() in {"true","false","0","1"} for v in samples[:8]):
            score -= 38
        return score

    @classmethod
    def _listing_schema_admission(cls, field, category, confidence, samples):
        """RECON019: keep Page-1 Recommended semantic and suppress raw DOM duplicates."""
        field_text=clean_text(str(field))
        low=field_text.lower()
        vals=[clean_text(str(v)) for v in (samples or []) if clean_text(str(v))]
        output=cls._friendly_recommended_output(field_text,category)
        normalized=re.sub(r"[^a-z0-9]+"," ",clean_text(output).lower()).strip()

        if cls._is_generic_structural_source(field_text):
            return False

        generic_outputs={
            "a","p","span","div","text","field","value","content",
            "span nth 1","span nth 2","span nth 3","a nth 1","a nth 2",
            "h1","h2","h3","h4","small","strong","label"
        }
        if normalized in generic_outputs:
            return False

        if category in {
            "Name / Title","Email","Phone","Location","Link / Detail",
            "Coordinate","Document","Image / Media","Price / Amount",
            "Date / Time","Identifier"
        }:
            return True

        # Description/summary-like listing content is useful even when the
        # generic classifier calls it Text.
        if any(t in low for t in ("description","summary","snippet","excerpt","bio")):
            return bool(vals)

        if category=="Text":
            identity=cls._semantic_identity_strength(category,field_text," | ".join(vals[:3]))
            if identity<55:
                return False
            # Raw selector-shaped text fields stay discoverable but are not
            # automatically promoted unless their source carries a semantic label.
            if not low.startswith(("semantic:","association:","meta:")):
                return False

        return confidence!="Low"


    def builder_add_recommended(self, prefix="public"):
        choice=getattr(self,f"{prefix}_builder_dataset"); idx=choice.current()
        if not (0 <= idx < len(self.detected_groups)): return
        item=self.detected_groups[idx]; self.builder_mappings=[]
        ranked=[]
        for position,field in enumerate(item["fields"]):
            samples=[clean_text(str(r.get(field,""))) for r in item["records"][:20] if clean_text(str(r.get(field,"")))]
            category,confidence=self._classify_discovered_field(field,samples)
            if confidence=="Low": continue
            if not self._listing_schema_admission(field,category,confidence,samples):
                continue
            score=self._listing_recommendation_score(field,category,confidence,samples)
            output=self._friendly_recommended_output(field,category)
            concept=self._semantic_field_concept(field,category,output,samples[:3])
            ranked.append((score,-position,field,category,confidence,samples,concept))

        # RECON019: one strongest automatic field per semantic concept.
        best_by_concept={}
        for row in ranked:
            concept=row[-1]
            current=best_by_concept.get(concept)
            if current is None or row[:2] > current[:2]:
                best_by_concept[concept]=row
        ranked=sorted(best_by_concept.values(),reverse=True)

        used_outputs=set()
        for score,_negpos,field,category,confidence,samples,_concept in ranked:
            if score < 28:
                continue
            output=self._friendly_recommended_output(field,category)
            base=output; suffix=2
            while output.lower() in used_outputs:
                parts=[p for p in re.split(r"[. >\[]", clean_text(str(field))) if p and p != "]"]
                qualifier=(parts[-2].replace("_"," ").title() if len(parts)>=2 else "Field")
                output=f"{qualifier} {base}"
                if output.lower() in used_outputs:
                    output=f"{base} {suffix}"; suffix+=1
            used_outputs.add(output.lower())
            transform="Email / mailto Clean" if category=="Email" else "Text Clean"
            self.builder_mappings.append({
                "source":field,"output":output,"transform":transform,"enabled":True,
                "semantic_type":category,
                "sample":samples[0] if samples else "",
                "confidence":confidence,
                "context_score":score,
                "auto_selected":True
            })
            if len(self.builder_mappings)>=8: break
        self._builder_refresh_mapping_tree(prefix)
        self.status_var.set(
            f"Added {len(self.builder_mappings)} entity-qualified recommended listing fields. "
            "Duplicate/raw DOM representations were consolidated; every detected field remains available for manual selection."
        )

    def builder_add_detail_mapping(self, prefix="public"):
        tree=getattr(self,f"{prefix}_builder_detail_tree"); selected=tree.selection()
        if not selected: return
        values=tree.item(selected[0],"values"); field=values[1] if len(values)>2 else values[0]; sample=values[2] if len(values)>2 else values[1]
        category,confidence=self._classify_discovered_field(field,[sample])
        output={"Email":"Email","Phone":"Phone","Location":"Address / Location","Image / Media":"Detail Image","Document":"Detail Document","Name / Title":"Detail Name","Price / Amount":"Detail Price","Date / Time":"Detail Date","Identifier":"Detail Identifier"}.get(category, field.split(" [",1)[0].split(" > ")[-1].replace("_"," ").title())
        transform="Email / mailto Clean" if category=="Email" else "Text Clean"
        if not any(m["source"]==field and int(m.get("level",2))==int(self.builder_current_level) for m in self.builder_detail_mappings):
            self.builder_detail_mappings.append({"source":field,"output":output,"transform":transform,"semantic_type":category,"sample":sample,"context_score":self._detail_candidate_score(category,field,sample,confidence),"enabled":True,"level":self.builder_current_level})
        self._builder_refresh_detail_mapping_tree(prefix)

    def builder_add_recommended_detail(self, prefix="public"):
        """DETAILSEM013: one authoritative recommendation path for detail pages."""
        current_level=int(self.builder_current_level)
        if current_level<=1 or current_level not in self.builder_levels:
            return
        selected=self._auto_recommend_detail_level(prefix,current_level)
        self._builder_update_live_summary(prefix)
        self.status_var.set(
            f"Recommended Page {current_level} schema refreshed — "
            f"{selected} enabled field(s). Structural label/value authority is preserved."
        )


    def _builder_refresh_detail_mapping_tree(self,prefix="public",refresh_workspace=True):
        tree=getattr(self,f"{prefix}_builder_detail_mapping_tree"); self.clear_tree(tree)
        for i,m in enumerate(self.builder_detail_mappings): tree.insert("","end",iid=str(i),values=("✓" if m.get("enabled",True) else "",m["source"],m["output"],m["transform"]))
        if refresh_workspace:
            self._builder_refresh_workspace(prefix)

    @staticmethod
    def _apply_single_transform(value, transform):
        if transform=="Email / mailto Clean": return re.sub(r"^mailto:","",clean_text(str(value)),flags=re.I).split("?")[0]
        return clean_text(str(value)) if transform=="Text Clean" else value

    def _builder_resolve_page1_relationship_field(self,prefix,records):
        """RECON028: resolve the configured Page-1 -> Page-2 relationship once."""
        records=list(records or [])
        candidates=[]

        level2=self.builder_levels.get(2,{}) or {}
        candidates.append(clean_text(str(level2.get("relationship",""))))

        box=getattr(self,f"{prefix}_builder_detail_url_field",None)
        if box is not None:
            try:
                candidates.append(clean_text(str(box.get())))
            except Exception:
                pass

        candidates.append(clean_text(str(getattr(self,"builder_detail_link_field",""))))

        for m in self.builder_mappings:
            if not m.get("enabled",True):
                continue
            if clean_text(str(m.get("semantic_type","")))=="Link / Detail":
                candidates.append(clean_text(str(m.get("source",""))))

        ordered=[]
        for field in candidates:
            if field and field not in ordered:
                ordered.append(field)

        best_field=""
        best_coverage=-1
        for field in ordered:
            coverage=sum(
                1 for record in records
                if self.valid_url(self._builder_record_relationship_url(record,field))
            )
            if coverage>best_coverage:
                best_field=field
                best_coverage=coverage

        return best_field if best_coverage>0 else ""

    def _builder_record_relationship_url(self,record,relationship_field):
        """Resolve one configured relationship value from one parent record."""
        record=record or {}
        field=clean_text(str(relationship_field))
        value=clean_text(str(record.get(field,"")))
        if self.valid_url(value):
            return value

        target=field.lower()
        for key,raw in record.items():
            if clean_text(str(key)).lower()==target:
                value=clean_text(str(raw))
                if self.valid_url(value):
                    return value

        for mapping in self.builder_mappings:
            if not mapping.get("enabled",True):
                continue
            output=clean_text(str(mapping.get("output","")))
            source=clean_text(str(mapping.get("source","")))
            if output.lower()==target and source:
                value=clean_text(str(record.get(source,"")))
                if self.valid_url(value):
                    return value
        return ""


    def builder_guided_multilevel_preview(self,prefix="public",limit=3):
        self._converge_final_schema_state()
        resource_level=self._builder_has_resource_dataset()
        if resource_level:
            rows=self._builder_resource_output_rows(resource_level,limit=limit)
            if not rows:
                messagebox.showinfo("No Resource Fields","Select at least one field from the structured resource first."); return
            for row in rows: row["Detail Preview Status"]=f"PASS — structured resource Page {resource_level}"
            self._load_guided_preview(prefix,rows)
            return
        choice=getattr(self,f"{prefix}_builder_dataset")
        idx=getattr(self,"builder_loaded_dataset_index",choice.current())
        if not (0 <= idx < len(self.detected_groups)): return
        active_detail = [m for m in self.builder_detail_mappings if m.get("enabled",True)]
        configured_later_levels = sorted(k for k in self.builder_levels if int(k) > 1)

        # RECON027 RECOVERY: stale/legacy state can already contain the same page
        # twice. Keep only the first occurrence for preview traversal.
        _deduped_later_levels=[]
        _seen_page_keys=set()
        for _lvl in configured_later_levels:
            _info=self.builder_levels.get(_lvl,{}) or {}
            _page_url=clean_text(str(_info.get("url","")))
            try:
                _parsed=urlparse(_page_url)
                _page_key=(
                    _parsed.scheme.lower(),_parsed.netloc.lower(),
                    (re.sub(r"/+$","",_parsed.path or "") or "/"),_parsed.query
                )
            except Exception:
                _page_key=_page_url.rstrip("/")
            if _page_key in _seen_page_keys:
                continue
            _seen_page_keys.add(_page_key)
            _deduped_later_levels.append(_lvl)
        configured_later_levels=_deduped_later_levels

        if not configured_later_levels:
            # RELINT001HF004: Page 1 can itself be the complete dataset (including
            # browser-observed Dynamic API Records). Do not force users to invent
            # a Page 2 relationship merely to preview valid selected listing fields.
            self.builder_preview(prefix, dataset_index=idx)
            return
        if not active_detail:
            messagebox.showinfo("Choose Detail Fields","Keep at least one field on Page 2 or a later page before previewing."); return
        all_records=list(self.detected_groups[idx].get("records",[]) or [])
        link_field=self._builder_resolve_page1_relationship_field(prefix,all_records)
        if not link_field:
            messagebox.showinfo(
                "Choose Link Field",
                "The configured Page 1 relationship does not resolve to a valid URL in the current dataset."
            )
            return
        self.builder_detail_link_field=link_field
        records=all_records[:limit]
        if not records:
            messagebox.showinfo("No Records","The selected dataset has no records to preview.")
            return

        # Make the preview operation observable. Previously it ran in a background
        # thread with no visible progress and could wait up to 60 seconds per page,
        # which made the application appear frozen even though Playwright was busy.
        win=tk.Toplevel(self)
        win.title("Guided Multi-Level Preview")
        win.geometry("620x180")
        win.transient(self)
        win.resizable(False,False)
        ttk.Label(win,text="Testing the configured multi-page extraction path on sample records",font=("Segoe UI",11,"bold")).pack(anchor="w",padx=16,pady=(16,6))
        progress_text=tk.StringVar(value=f"Preparing browser for {len(records)} sample record(s)...")
        ttk.Label(win,textvariable=progress_text,wraplength=580).pack(fill="x",padx=16,pady=(0,8))
        bar=ttk.Progressbar(win,mode="determinate",maximum=max(1,len(records)),value=0)
        bar.pack(fill="x",padx=16,pady=(0,8))
        detail=tk.StringVar(value="Each record has a bounded navigation timeout. A failed page will be reported instead of blocking the whole preview.")
        ttk.Label(win,textvariable=detail,wraplength=580).pack(fill="x",padx=16)
        win.protocol("WM_DELETE_WINDOW", lambda: None)
        self._guided_preview_window=win
        self._guided_preview_progress_text=progress_text
        self._guided_preview_progress_bar=bar
        self._guided_preview_detail_text=detail
        self.status_var.set(f"Guided multi-level preview started — 0/{len(records)} records.")
        threading.Thread(target=self._guided_preview_worker,args=(prefix,records,link_field,active_detail),daemon=True).start()

    def _guided_preview_progress(self,index,total,url,message=""):
        if hasattr(self,"_guided_preview_progress_text"):
            self._guided_preview_progress_text.set(f"Record {index}/{total}: {url}")
        if hasattr(self,"_guided_preview_progress_bar"):
            self._guided_preview_progress_bar["value"]=index-1
        if hasattr(self,"_guided_preview_detail_text"):
            self._guided_preview_detail_text.set(message or "Opening linked detail page and extracting the selected fields...")
        self.status_var.set(f"Guided multi-level preview — record {index}/{total}: {url}")

    def _builder_mappings_by_level(self, active_detail=None):
        detail = active_detail if active_detail is not None else [m for m in self.builder_detail_mappings if m.get("enabled", True)]
        by_level = {}
        for mapping in detail:
            level = int(mapping.get("level", 2))
            by_level.setdefault(level, []).append(mapping)
        return by_level

    def _builder_relationship_from_html(self, html_text, base_url, source):
        """Resolve the relationship field selected on one page to the next URL."""
        if not source:
            return ""
        try:
            parser = DOMParser(base_url); parser.feed(html_text)
            features = extract_universal_semantic_features(html_text, base_url, node_features(parser.root, base_url))
            value = clean_text(str(features.get(source, "")))
            if self.valid_url(value):
                return value
        except Exception:
            pass
        return ""

    def _builder_extract_record_across_levels(self, page, record, link_field, mappings_by_level, timeout_ms=25000):
        """Execute Page 1 -> Page N for one listing record.

        Page relationships are configuration, not output columns.  Page 2 starts
        from the selected Page-1 relationship; every later page uses the
        relationship that created that page tab.  Only fields checked in the live
        workspace are merged into the output row.
        """
        row = self._builder_transform_row(record) if self.builder_mappings else {}
        current_url = self._builder_record_relationship_url(record,link_field)
        statuses = []
        max_level = max(self.builder_levels.keys() or [1])
        for level in range(2, max_level + 1):
            if not self.valid_url(current_url):
                statuses.append(f"Page {level}: invalid/empty relationship")
                # Keep schema stable for fields that could not be reached.
                for future_level in range(level, max_level + 1):
                    for mapping in mappings_by_level.get(future_level, []):
                        row.setdefault(mapping.get("output", "Detail Field"), "")
                break
            page.goto(current_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1000)
            final_url = page.url; html_text = page.content()
            mappings = mappings_by_level.get(level, [])
            if mappings:
                listing_context={}
                listing_context.update(record or {})
                listing_context.update(row or {})
                values, detail_status = self._extract_detail_values_from_rendered_page(
                    html_text, final_url, mappings, listing_context=listing_context
                )
                row.update(values)
                statuses.append(f"Page {level}: {detail_status}")
            else:
                statuses.append(f"Page {level}: followed")
            next_level = level + 1
            if next_level <= max_level:
                relationship_source = clean_text(self.builder_levels.get(next_level, {}).get("relationship", ""))
                current_url = self._builder_relationship_from_html(html_text, final_url, relationship_source)
        return row, "; ".join(statuses)

    def _guided_preview_worker(self,prefix,records,link_field,active_detail):
        output=[]; total=len(records); mappings_by_level=self._builder_mappings_by_level(active_detail)
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page(user_agent=USER_AGENT,viewport={"width":1600,"height":1000})
                for index,record in enumerate(records,1):
                    url=self._builder_record_relationship_url(record,link_field)
                    self.after(0,lambda i=index,t=total,u=url:self._guided_preview_progress(i,t,u,"Following the configured Page 1 → Page N path and extracting only checked fields..."))
                    try:
                        row,status=self._builder_extract_record_across_levels(page,record,link_field,mappings_by_level,timeout_ms=25000)
                        row["Detail Preview Status"]=f"PASS — {status}"
                    except Exception as exc:
                        row=self._builder_transform_row(record) if self.builder_mappings else {}
                        for mappings in mappings_by_level.values():
                            for mapping in mappings: row.setdefault(mapping.get("output","Detail Field"),"")
                        row["Detail Preview Status"]=f"ERROR — {type(exc).__name__}: {str(exc)[:220]}"
                    output.append(row)
                    self.after(0,lambda i=index,t=total,u=url:self._guided_preview_record_done(i,t,u))
                browser.close()
            self.after(0,lambda:self._load_guided_preview(prefix,output))
        except Exception as exc:
            self.after(0,lambda:self._guided_preview_failed(exc))

    def _guided_preview_record_done(self,index,total,url):
        if hasattr(self,"_guided_preview_progress_bar"):
            self._guided_preview_progress_bar["value"]=index
        if hasattr(self,"_guided_preview_detail_text"):
            self._guided_preview_detail_text.set(f"Completed record {index}/{total}. Continuing to the next sample...")
        self.status_var.set(f"Guided multi-level preview — completed {index}/{total} sample record(s).")

    def _guided_preview_failed(self,exc):
        try:
            if hasattr(self,"_guided_preview_window") and self._guided_preview_window.winfo_exists():
                self._guided_preview_window.destroy()
        except Exception:
            pass
        self._show_error("Guided Multi-Level Preview Failed",str(exc))

    def _load_guided_preview(self,prefix,rows):
        headers=[]
        for r in rows:
            for k in r:
                if k not in headers: headers.append(k)
        self.builder_preview_headers=headers; self.builder_preview_rows=[[r.get(h,"") for h in headers] for r in rows]
        tree=getattr(self,f"{prefix}_builder_preview"); self.configure_tree(tree,headers or ["No fields"])
        self.clear_tree(tree)
        for row in self.builder_preview_rows: tree.insert("","end",values=row)

        try:
            if hasattr(self,"_guided_preview_window") and self._guided_preview_window.winfo_exists():
                self._guided_preview_window.destroy()
        except Exception:
            pass

        # The embedded preview tree is below several configuration panels and can
        # be outside the visible window on common laptop resolutions. Always show
        # the result in a dedicated window so completion is unmistakable.
        result=tk.Toplevel(self)
        result.title(f"Guided Multi-Level Preview Results — {len(rows)} record(s)")
        result.geometry("1400x620")
        result.transient(self)
        ttk.Label(result,text=f"Preview complete — {len(rows)} listing record(s) tested against their linked detail pages.",font=("Segoe UI",11,"bold")).pack(anchor="w",padx=10,pady=(10,4))
        ttk.Label(result,text="Review Detail Preview Status first. PASS confirms the relationship was followed; the remaining columns show the merged listing/detail values.").pack(anchor="w",padx=10,pady=(0,8))
        frame=ttk.Frame(result); frame.pack(fill="both",expand=True,padx=10,pady=(0,10))
        preview=ttk.Treeview(frame,columns=headers or ["No fields"],show="headings")
        for h in headers or ["No fields"]:
            preview.heading(h,text=h); preview.column(h,width=180,minwidth=90,stretch=True)
        y=ttk.Scrollbar(frame,orient="vertical",command=preview.yview); x=ttk.Scrollbar(frame,orient="horizontal",command=preview.xview)
        preview.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        preview.grid(row=0,column=0,sticky="nsew"); y.grid(row=0,column=1,sticky="ns"); x.grid(row=1,column=0,sticky="ew")
        frame.rowconfigure(0,weight=1); frame.columnconfigure(0,weight=1)
        for row in self.builder_preview_rows: preview.insert("","end",values=row)
        ttk.Button(result,text="Close",command=result.destroy).pack(side="right",padx=10,pady=(0,10))
        self.status_var.set(f"Guided multi-level preview complete — {len(rows)} sample records combined from listing and detail pages.")

    def builder_add_mapping(self, prefix="public"):
        source = getattr(self, f"{prefix}_builder_source").get().strip()
        output = getattr(self, f"{prefix}_builder_output").get().strip()
        transform = getattr(self, f"{prefix}_builder_transform").get().strip() or "None"
        if not source:
            messagebox.showinfo("Missing Source", "Select a detected source field.")
            return
        if not output and transform not in ("Person Name Split", "City, State Split"):
            output = source.replace("_", " ").title()
        self.builder_mappings.append({"source": source, "output": output, "transform": transform, "enabled": True})
        self._builder_refresh_mapping_tree(prefix)

    def _builder_refresh_mapping_tree(self, prefix="public", refresh_workspace=True):
        tree = getattr(self, f"{prefix}_builder_mapping_tree")
        self.clear_tree(tree)
        for i, item in enumerate(self.builder_mappings):
            tree.insert("", "end", iid=str(i), values=("✓" if item.get("enabled",True) else "", item["source"], item["output"], item["transform"]))
        if refresh_workspace:
            self._builder_refresh_workspace(prefix)

    def builder_toggle_mapping(self, prefix="public", enabled=None):
        tree=getattr(self,f"{prefix}_builder_mapping_tree"); selected=tree.selection()
        if not selected: return
        for iid in selected:
            idx=int(iid)
            if 0 <= idx < len(self.builder_mappings):
                current=self.builder_mappings[idx].get("enabled",True)
                self.builder_mappings[idx]["enabled"] = (not current) if enabled is None else bool(enabled)
        self._builder_refresh_mapping_tree(prefix)

    def builder_set_all_mappings(self,prefix="public",enabled=True):
        for item in self.builder_mappings: item["enabled"]=bool(enabled)
        self._builder_refresh_mapping_tree(prefix)

    def builder_rename_output(self,prefix="public"):
        tree=getattr(self,f"{prefix}_builder_mapping_tree"); selected=tree.selection()
        if not selected: return
        idx=int(selected[0]); item=self.builder_mappings[idx]
        name=simpledialog.askstring("Rename Output Column","Output column name:",initialvalue=item.get("output",""),parent=self)
        if name and clean_text(name): item["output"]=clean_text(name); self._builder_refresh_mapping_tree(prefix)

    def builder_toggle_detail_mapping(self,prefix="public",enabled=None):
        tree=getattr(self,f"{prefix}_builder_detail_mapping_tree"); selected=tree.selection()
        if not selected: return
        for iid in selected:
            idx=int(iid)
            if 0 <= idx < len(self.builder_detail_mappings):
                current=self.builder_detail_mappings[idx].get("enabled",True)
                self.builder_detail_mappings[idx]["enabled"]=(not current) if enabled is None else bool(enabled)
        self._builder_refresh_detail_mapping_tree(prefix)

    def builder_rename_detail_output(self,prefix="public"):
        tree=getattr(self,f"{prefix}_builder_detail_mapping_tree"); selected=tree.selection()
        if not selected:return
        idx=int(selected[0]); item=self.builder_detail_mappings[idx]
        name=simpledialog.askstring("Rename Detail Output","Output column name:",initialvalue=item.get("output",""),parent=self)
        if name and clean_text(name): item["output"]=clean_text(name); self._builder_refresh_detail_mapping_tree(prefix)

    def _builder_refresh_levels(self,prefix="public"):
        tree=getattr(self,f"{prefix}_builder_level_tree",None); crumb=getattr(self,f"{prefix}_builder_level_breadcrumb",None)
        if tree is None:return
        self.clear_tree(tree)
        for level in sorted(self.builder_levels):
            info=self.builder_levels[level]
            tree.insert("","end",iid=str(level),values=(f"Level {level}",info.get("url",""),info.get("relationship",""),info.get("status","")))
        parts=[]
        for level in sorted(self.builder_levels):
            label="Listing Page" if level==1 else f"Detail Page {level-1}"
            parts.append(f"Page {level} — {label}")
        if crumb is not None: crumb.set("  >  ".join(parts) if parts else "Page 1 — Listing Page")
        self._builder_refresh_workspace(prefix)

    def builder_show_selected_level(self,prefix="public"):
        tree=getattr(self,f"{prefix}_builder_level_tree"); selected=tree.selection()
        if not selected:return
        level=int(selected[0]); self.builder_current_level=level
        if level <= 1:return
        info=self.builder_levels.get(level,{})
        fields=list((info.get("fields") or {}).items())
        if fields:
            detail_tree=getattr(self,f"{prefix}_builder_detail_tree"); self.clear_tree(detail_tree)
            self.builder_detail_fields=dict(fields); self.builder_detail_url=info.get("url","")
            for field,sample in fields:
                category,confidence=self._classify_discovered_field(field,[sample])
                detail_tree.insert("","end",values=(category,field,str(sample)[:900],confidence))
            getattr(self,f"{prefix}_builder_detail_status").set(f"Viewing Level {level}: {info.get('url','')} — {len(fields)} extractable fields.")

    def builder_follow_selected_as_next_level(self,prefix="public"):
        detail_tree=getattr(self,f"{prefix}_builder_detail_tree"); selected=detail_tree.selection()
        if not selected:
            messagebox.showinfo("Select a Relationship","Select a Link / Detail row from the current detail-page field list first.")
            return
        values=detail_tree.item(selected[0],"values")
        category=values[0] if values else ""; field=values[1] if len(values)>1 else ""; sample=str(values[2] if len(values)>2 else "").strip()
        if category != "Link / Detail" or not self.valid_url(sample):
            messagebox.showinfo("Not a Followable Link","The selected field is not a complete http/https relationship target.")
            return
        next_level=max(self.builder_levels.keys() or [1])+1
        getattr(self,f"{prefix}_builder_detail_status").set(f"Opening Level {next_level}: {sample}")
        threading.Thread(
            target=self._builder_next_level_worker,
            args=(sample,prefix,next_level,field,[sample]),
            daemon=True
        ).start()

    def _builder_resource_worker(self,url,prefix,level,relationship):
        try:
            final_url,ext,groups=parse_structured_resource(url)
            if not groups:
                raise RuntimeError("The resource opened successfully but no structured records were detected.")
            best=groups[0]
            self.after(0,lambda:self._complete_resource_inspection(prefix,level,relationship,final_url,ext,best,groups))
        except Exception as exc:
            self.after(0,lambda e=str(exc):self._show_error("Structured Resource Inspection Failed",e))

    def _complete_resource_inspection(self,prefix,level,relationship,final_url,ext,best,groups):
        records=list(best.get("records",[])); fields=list(best.get("fields",[]))
        samples={}
        for field in fields:
            vals=[]
            for record in records[:30]:
                value=clean_text(str(record.get(field,"")))
                if value and value not in vals: vals.append(value)
                if len(vals)>=3: break
            samples[field]="  |  ".join(vals)[:1200]
        title=f"{ext.lstrip('.').upper()} Records"
        self.builder_current_level=level
        self.builder_levels[level]={
            "url":final_url,"title":title,"relationship":relationship,
            "status":f"{len(records)} resource record(s)","fields":samples,
            "records":records,"level_kind":"resource_dataset","resource_groups":groups,
        }
        # Resource mappings start empty so only explicitly chosen fields are output.
        self.builder_detail_mappings=[m for m in self.builder_detail_mappings if int(m.get("level",2))!=int(level)]
        self._builder_refresh_levels(prefix)
        self._builder_refresh_workspace(prefix)
        self.builder_show_level(prefix,level)
        self.status_var.set(
            f"Structured resource inspected — {len(records)} record(s), {len(fields)} field(s). "
            "Select the fields you want from the resource and Preview/Run Extraction."
        )
        if len(groups)>1:
            messagebox.showinfo(
                "Structured Resource Detected",
                f"RTUDES found {len(groups)} candidate dataset(s) inside this resource.\n\n"
                f"Using the strongest candidate: {best.get('name','Resource Records')} — {len(records)} records.\n\n"
                "Additional candidates remain available to the engine for future selector UI improvements."
            )

    def _builder_resource_output_rows(self, level, limit=None):
        info=self.builder_levels.get(level,{})
        records=list(info.get("records",[]))
        if limit is not None: records=records[:limit]
        mappings=[m for m in self.builder_detail_mappings if m.get("enabled",True) and int(m.get("level",2))==int(level)]
        parent=self.builder_resource_parent_record or {}
        parent_row=self._builder_transform_row(parent) if self.builder_mappings and parent else {}
        out=[]
        for record in records:
            row=dict(parent_row)
            for m in mappings:
                val=record.get(m.get("source",""),"")
                row[m.get("output") or m.get("source")]=self._apply_single_transform(val,m.get("transform","Text Clean"))
            out.append(row)
        return out

    def _builder_has_resource_dataset(self):
        return next((lvl for lvl,info in sorted(self.builder_levels.items()) if lvl>1 and info.get("level_kind")=="resource_dataset"),None)

    def _builder_next_level_worker(self,url,prefix,level,relationship,sample_urls=None):
        """Inspect a detail page plus a small sibling sample for field behavior."""
        try:
            urls=[]
            for candidate in [url,*(sample_urls or [])]:
                candidate=clean_text(str(candidate))
                if self.valid_url(candidate) and candidate not in urls:
                    urls.append(candidate)
                if len(urls)>=3:
                    break

            aggregate={}
            primary_values={}
            presence=Counter()
            primary_final=url
            primary_title=""
            successful=0
            for pos,target in enumerate(urls):
                html_text,final_url=self._render_page_html(
                    target,wait_ms=2500 if pos==0 else 1200,scroll=(pos==0)
                )
                parser=DOMParser(final_url)
                parser.feed(html_text)
                features=extract_universal_semantic_features(
                    html_text,final_url,node_features(parser.root,final_url)
                )
                successful+=1
                if pos==0:
                    primary_final=final_url
                    primary_title=parser.title
                seen_on_page=set()
                for key,raw in features.items():
                    value=clean_text(str(raw))
                    if not value or len(value)>4000:
                        continue
                    if pos==0:
                        primary_values.setdefault(key,Counter())[value] += 1
                    if key not in seen_on_page:
                        presence[key] += 1
                        seen_on_page.add(key)
                    bucket=aggregate.setdefault(key,Counter())
                    bucket[value] += 1

            fields=[]
            profiles={}
            for key,value_counts in aggregate.items():
                ranked_values=[
                    value for value,_count in value_counts.most_common(8)
                ]
                primary_ranked=[
                    value for value,_count in
                    (primary_values.get(key) or Counter()).most_common(8)
                ]
                display_values=primary_ranked or ranked_values
                sample=" | ".join(display_values[:3])
                fields.append((key,sample))
                present=int(presence.get(key,0))
                total_occurrences=sum(value_counts.values())
                dominant_count=max(value_counts.values()) if value_counts else 0
                unique_count=len(value_counts)
                presence_ratio=(present/successful) if successful else 0.0
                dominant_ratio=(dominant_count/present) if present else 0.0
                unique_ratio=(unique_count/present) if present else 0.0
                profiles[key]={
                    "sample_count":successful,
                    "presence_count":present,
                    "presence_ratio":presence_ratio,
                    "unique_count":unique_count,
                    "unique_ratio":unique_ratio,
                    "dominant_ratio":dominant_ratio,
                    "values":ranked_values[:5],
                    "primary_values":primary_ranked[:5],
                    "sibling_values":[v for v in ranked_values[:8] if v not in primary_ranked[:5]],
                    "shared_context":bool(
                        successful>=3 and
                        presence_ratio>=0.80 and
                        dominant_ratio>=0.80
                    ),
                }

            fields.sort(key=lambda kv:len(kv[0]))
            # DISCOVERY001: do not truncate Page-N candidates before the user can select them.

            self.after(
                0,
                lambda:self._complete_next_level_inspection(
                    prefix,level,relationship,primary_final,fields,primary_title,profiles
                )
            )
        except Exception as exc:
            self.after(0,lambda e=str(exc):self._show_error("Next-Level Inspection Failed",e))

    def _resolve_cross_level_output_collisions(self, level):
        """RELTRAV002: give later-page fields unique semantic final names."""
        used=set()
        for m in self.builder_mappings:
            if m.get("enabled",True):
                name=clean_text(str(m.get("output") or m.get("source") or ""))
                if name: used.add(name.lower())
        for m in self.builder_detail_mappings:
            if not m.get("enabled",True) or int(m.get("level",2))>=int(level):
                continue
            name=clean_text(str(m.get("output") or m.get("source") or ""))
            if name: used.add(name.lower())

        semantic_prefix={
            "Name / Title":"Detail Name",
            "Image / Media":"Detail Image URL",
            "Link / Detail":"Detail Page URL",
            "Email":"Detail Email",
            "Phone":"Detail Phone",
            "Location":"Detail Location",
            "Price / Amount":"Detail Price",
            "Identifier":"Detail Identifier",
            "Date / Time":"Detail Date",
        }
        renamed=[]
        for m in self.builder_detail_mappings:
            if not m.get("enabled",True) or int(m.get("level",2))!=int(level):
                continue
            original=clean_text(str(m.get("output") or m.get("source") or "Field"))
            if original.lower() not in used:
                used.add(original.lower())
                continue
            if m.get("structural_authority"):
                base="Detail "+original
            else:
                base=semantic_prefix.get(m.get("semantic_type",""),"Detail "+original)
            candidate=base
            n=2
            while candidate.lower() in used:
                candidate=f"{base} {n}"
                n+=1
            m["output"]=candidate
            used.add(candidate.lower())
            renamed.append((original,candidate))
        return renamed

    def _ensure_detail_identity_mapping(self, level, selected):
        """SEMATTR001: ensure later pages retain a record-specific Detail Name."""
        has_name=any(
            m.get("enabled",True)
            and int(m.get("level",2))==int(level)
            and m.get("semantic_type")=="Name / Title"
            for m in selected
        )
        if has_name:
            return False
        selected.append({
            "source":"__record_identity_name__",
            "output":"Detail Name",
            "transform":"Text Clean",
            "semantic_type":"Name / Title",
            "sample":"",
            "context_score":90,
            "enabled":True,
            "level":int(level),
            "synthetic_identity":True,
        })
        return True

    @classmethod
    def _is_value_as_column_name(cls, field, output, sample, page_title=""):
        """Detect automatic columns that are really just a record value."""
        field_text=clean_text(str(field))
        output_text=clean_text(str(output))
        sample_text=clean_text(str(sample))
        low=field_text.lower()

        # Real page-derived labels are allowed to name the column.
        if low.startswith(("association:","meta:")):
            return False

        out_norm=cls._normalized_candidate_value(output_text)
        sample_parts=[
            cls._normalized_candidate_value(v)
            for v in re.split(r"\s+\|\s+",sample_text)
            if cls._normalized_candidate_value(v)
        ]
        if not out_norm:
            return True

        # A semantic candidate whose own label is simply one of its values is
        # value-derived, unless it is independently confirmed structurally.
        if out_norm in sample_parts:
            page_norm=cls._normalized_candidate_value(page_title)
            if page_norm and out_norm==page_norm:
                return False
            return True

        # Also catch direct source/value identity for unlabeled generic fields.
        source_label=cls._candidate_source_label(field_text)
        if source_label:
            src_norm=cls._normalized_candidate_value(source_label)
            if src_norm and src_norm in sample_parts and low.startswith("semantic:"):
                return True

        return False

    @classmethod
    def _structural_sample_parts(cls, sample):
        """Split aggregated discovery samples into independent record values."""
        return [
            clean_text(v)
            for v in re.split(r"\s+\|\s+",clean_text(str(sample)))
            if clean_text(v)
        ]

    @classmethod
    def _structural_field_promotions(cls, level_fields):
        """Return strongest reusable label -> value fields for auto schema.

        Discovery remains unchanged; this only creates high-priority promotion
        candidates from independently proven immediate structural relationships.
        """
        best={}
        for field,sample in (level_fields or {}).items():
            field_text=clean_text(str(field))
            low=field_text.lower()
            if not low.startswith("association:"):
                continue
            if "[next-sibling;distance=1]" not in low:
                continue

            label=cls._candidate_source_label(field_text)
            if not cls._label_role_fitness(label,sample):
                continue
            label_role=cls._normalized_candidate_value(label)
            label_raw=clean_text(str(label))
            if (
                re.fullmatch(
                    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
                    r"(?:,?\s+)?(?:january|february|march|april|may|june|july|august|"
                    r"september|october|november|december)?(?:\s+\d{1,2})?",
                    label_raw, re.I
                )
                or re.search(r"\b(?:sign\s*up|subscribe|newsletter)\b",label_raw,re.I)
            ):
                continue
            if label_role in {
                "map","map data","satellite","signup","sign up","subscribe","next","previous",
                "close","open","share","print","login","log in","sign in","register","search",
                "monday","tuesday","wednesday","thursday","friday","saturday","sunday",
                "january","february","march","april","may","june","july","august","september",
                "october","november","december","day","days","hour","hours","min","mins",
                "minute","minutes","sec","secs","second","seconds"
            }:
                continue

            label_norm=cls._normalized_candidate_value(label)
            sample_norm=cls._normalized_candidate_value(sample)
            if not label_norm or not sample_norm or label_norm==sample_norm:
                continue

            sample_parts=[
                cls._normalized_candidate_value(v)
                for v in cls._structural_sample_parts(sample)
                if cls._normalized_candidate_value(v)
            ]
            distinct_values={v for v in sample_parts if v and v!=label_norm}

            rank=cls._candidate_catalog_rank(
                field_text,sample,
                repeated_sample_counts=None,
                candidate_labels={label}
            )
            current=best.get(label_norm)
            item={
                "field":field,
                "sample":sample,
                "label":label,
                "rank":rank,
                "distinct_value_count":len(distinct_values),
                "repeatable":len(distinct_values)>=2,
            }
            if current is None or rank>current["rank"]:
                best[label_norm]=item
        return list(best.values())

    @classmethod
    def _structural_authority_mapping(cls, promoted, level, level_info):
        """Create a protected mapping from a proven reusable label/value pair."""
        field=promoted.get("field","")
        sample=promoted.get("sample","")
        label=clean_text(str(promoted.get("label","")))
        sample_parts=[
            clean_text(v) for v in re.split(r"\s+\|\s+",clean_text(str(sample)))
            if clean_text(v)
        ]
        category,confidence=cls._classify_discovered_field(field,sample_parts[:3])
        profile=(level_info.get("field_profiles") or {}).get(field,{})
        ownership,ownership_score=cls._field_ownership_class(
            category,field,sample,profile,
            detail_url=clean_text(level_info.get("url","")),
            page_title=clean_text(level_info.get("title",""))
        )

        # RECON002: a proven immediate label -> value relationship is itself
        # record-structure evidence. Ownership scoring was previously allowed to
        # veto these candidates before structural authority was applied. That made
        # the UI show a High/Capture field while Recommended silently dropped it.
        #
        # Reject only explicit shell/chrome sources here. Do not use a vocabulary
        # of business field names; the proof is structural and page-derived.
        repeatable=bool(promoted.get("repeatable"))
        source_low=clean_text(str(field)).lower()
        shell_tokens=(
            "header","footer","navbar","navigation","menu","cookie","consent",
            "sidebar","topbar","toolbar","branding","brand","logo","site-title",
            "site_title","social","share","follow-us","follow_us","copyright",
            "announcement","promo","promotion","modal","popup","noscript",
            "tracking","analytics","recaptcha","captcha"
        )
        if any(token in source_low for token in shell_tokens):
            return None
        if not label:
            return None

        profile=(level_info.get("field_profiles") or {}).get(field,{})
        shared_context=bool(profile.get("shared_context"))
        stable_semantics={
            "Location","Identifier","Date / Time","Price / Amount",
            "Biography / Description","Document"
        }
        if shared_context and category not in stable_semantics:
            return None
        if shared_context and cls._semantic_identity_strength(category,field,sample) < 65:
            return None

        # RECON019: automatic schema admission is positive-evidence based.
        # A clean immediate label/value pair may still be navigation, taxonomy,
        # or a page widget. It remains visible in the full candidate catalog,
        # but structural authority is granted only when its semantic provenance
        # independently supports a record attribute.
        if not cls._recommendation_provenance_gate(
            category,field,sample,
            page_title=clean_text(level_info.get("title","")),
            labeled_values=set(),
            strong_structural_labels={label}
        ):
            return None

        # Plain Text is deliberately conservative for automatic Page-N output.
        # Site-specific/custom text attributes remain manually selectable.
        if category in {"Text","List / Group"}:
            return None

        # Immediate next-sibling label/value evidence upgrades ambiguous generic
        # ownership. Repetition across sampled records strengthens it further,
        # but a field does not have to vary in every sample to remain selectable.
        ownership="RECORD"
        ownership_score=max(
            int(ownership_score or 0),
            92 if repeatable else 78
        )

        return {
            "source":field,
            "output":label,
            "transform":"Email / mailto Clean" if category=="Email" else "Text Clean",
            "semantic_type":category,
            "sample":sample,
            "context_score":2000 + int(promoted.get("rank",0)),
            "ownership":ownership,
            "ownership_score":ownership_score,
            "auto_selected":True,
            "structural_promotion":True,
            "structural_authority":True,
            "structural_repeatable":repeatable,
            "structural_label":label,
            "structural_identity":cls._normalized_candidate_value(label),
            "recommendation_engine":"canonical_structural",
            "enabled":True,
            "level":int(level),
        }

    # RECON002 compatibility contract: _structural_field_promotions(fields)
    def _structural_schema_invariant(self, level):
        """Return authoritative structural identities missing from enabled schema."""
        level=int(level); info=self.builder_levels.get(level,{}) or {}; fields=info.get("fields") or {}
        # RECON002 compatibility: _structural_field_promotions(fields)
        expected=[]
        for item in self._structural_field_promotions(fields):
            m=self._structural_authority_mapping(item,level,info)
            if m: expected.append(m)
        enabled=set()
        for m in self.builder_detail_mappings:
            if int(m.get("level",2) or 2)!=level or not m.get("enabled",True): continue
            ident=clean_text(str(m.get("structural_identity") or self._normalized_candidate_value(
                m.get("structural_label") or self._candidate_source_label(m.get("source","")) or m.get("output","")
            )))
            if ident: enabled.add(ident)
        return [m for m in expected if clean_text(str(m.get("structural_identity",""))) not in enabled]

    def _repair_structural_schema_invariant(self, level):
        missing=self._structural_schema_invariant(level)
        for m in missing:
            m=dict(m); m.update({"recommendation_engine":"inventory_structural_repair",
                "promotion_integrity":"canonical_inventory_to_schema_repair","auto_selected":True,
                "enabled":True,"level":int(level)})
            self.builder_detail_mappings.append(m)
        if missing:self._builder_suppress_same_level_duplicates(level)
        return len(missing)


        # Compatibility contract: DETAILSEM014 originally named this
        # promotion_integrity value "ui_inventory_to_schema"; RECON003 supersedes
        # it with canonical identity reconciliation.
        # "promotion_integrity"]="ui_inventory_to_schema"
    # RECON001 compatibility contract: _structural_authority_mapping(item,level,level_info)
    def _promote_structural_candidates_to_mappings(self, level):
        """Reconcile exhaustive UI inventory to schema by canonical structural identity."""
        level=int(level); level_info=self.builder_levels.get(level,{}) or {}; level_fields=level_info.get("fields") or {}
        info=level_info; fields=level_fields
        if not isinstance(fields,dict) or not fields:return 0
        auth=[]
        for item in self._structural_field_promotions(level_fields):
            # RECON001 compatibility: _structural_authority_mapping(item,level,level_info)
            m=self._structural_authority_mapping(item,level,level_info)
            if m:auth.append(m)
        existing={}
        for m in self.builder_detail_mappings:
            if int(m.get("level",2) or 2)!=level:continue
            ident=clean_text(str(m.get("structural_identity") or self._normalized_candidate_value(
                m.get("structural_label") or self._candidate_source_label(m.get("source","")) or m.get("output","")
            )))
            if ident and m.get("structural_authority"):existing[ident]=m
        added=0
        for m in auth:
            ident=clean_text(str(m.get("structural_identity","")))
            if not ident:continue
            old=existing.get(ident)
            if old:
                rejected=bool(old.get("final_gate_rejected"))
                old.update(m)
                old.update({
                    "enabled":False if rejected else True,
                    "auto_selected":True,
                    "promotion_integrity":"canonical_inventory_to_schema"
                })
            else:
                m=dict(m);m.update({"enabled":True,"auto_selected":True,
                    "promotion_integrity":"canonical_inventory_to_schema"})
                self.builder_detail_mappings.append(m);existing[ident]=m;added+=1
        if auth:self._builder_suppress_same_level_duplicates(level)
        return added


    @classmethod
    def _detail_entity_noise_score(cls, category, field, sample, profile=None, page_title="", listing_context=None):
        profile=profile or {}
        field_text=clean_text(str(field))
        sample_text=clean_text(str(sample))
        low=(field_text+" "+sample_text).lower()
        score=0
        shell_tokens=(
            "header","footer","navbar","navigation","menu","sidebar","topbar",
            "breadcrumb","cookie","consent","login","sign in","register","search",
            "newsletter","subscribe","membership","events","resources","education",
            "contact us","more ","cancel","close","share","follow"
        )
        score += min(90,sum(18 for t in shell_tokens if t in low))
        if profile.get("shared_context"):
            score += 70
        if category=="Text":
            parts=[clean_text(x) for x in re.split(r"\s+\|\s+",sample_text) if clean_text(x)]
            if parts and all(len(x)<=70 and len(x.split())<=8 for x in parts):
                score += 22
        affinity=cls._detail_context_match_score(sample_text,category,listing_context or {})
        if affinity:
            score -= min(70,affinity)
        if category in {"Email","Phone","Company","Location","Name / Title","Biography / Description"}:
            score -= 18
        return max(0,min(160,score))


    @classmethod
    def _detail_entity_membership_score(cls, category, field, sample, profile=None,
                                        detail_url="", page_title="", listing_context=None):
        """RECON019: require positive evidence that a candidate belongs to the current entity."""
        profile=profile or {}
        listing_context=listing_context or {}
        semantic=clean_text(str(category))
        field_text=clean_text(str(field))
        sample_text=clean_text(str(sample))
        low=field_text.lower()
        score=0

        core={
            "Name / Title","Email","Phone","Location","Company","Website",
            "Social Link","Biography / Description","Identifier","Date / Time",
            "Price / Amount","Image / Media","Document","Link / Detail"
        }
        if semantic in core:
            score += 24

        ownership,ownership_score=cls._field_ownership_class(
            semantic,field_text,sample_text,profile,
            detail_url=detail_url,page_title=page_title
        )
        if ownership=="RECORD":
            score += 28
        elif ownership=="SHARED_CONTEXT":
            score -= 80
        else:
            score -= 12

        if int(profile.get("sample_count") or 0)>=3:
            presence=float(profile.get("presence_ratio") or 0)
            unique=float(profile.get("unique_ratio") or 0)
            dominant=float(profile.get("dominant_ratio") or 0)
            if presence>=.60 and unique>=.60:
                score += 24
            elif presence>=.60 and unique>=.35:
                score += 12
            if presence>=.80 and dominant>=.80:
                score -= 34

        affinity=cls._detail_context_match_score(
            sample_text,semantic,listing_context
        )
        score += min(55,affinity)

        # Strong, explicit semantic source cues are positive record evidence.
        identity=cls._semantic_identity_strength(semantic,field_text,sample_text)
        if identity>=70:
            score += 18
        elif identity<30:
            score -= 35

        # Current detail URL is record-owned.
        if semantic=="Link / Detail" and detail_url:
            vals=[clean_text(v) for v in re.split(r"\s+\|\s+",sample_text) if clean_text(v)]
            if any(v.rstrip("/")==detail_url.rstrip("/") for v in vals):
                score += 55

        # Generic/plain text has no automatic membership without strong
        # independent identity agreement.
        if semantic in {"Text","List / Group",""}:
            score -= 42
            if affinity<30:
                score -= 24

        noise=cls._detail_entity_noise_score(
            semantic,field_text,sample_text,profile,
            page_title=page_title,listing_context=listing_context
        )
        score -= min(100,noise)

        return max(-160,min(180,score))


    def _auto_recommend_detail_level(self,prefix,level):
        self._promote_structural_candidates_to_mappings(level)
        preserved=[m for m in self.builder_detail_mappings if int(m.get("level",2))!=int(level)]
        candidates=[]
        level_info=self.builder_levels.get(level,{}) or {}
        detail_url=clean_text(level_info.get("url",""))
        page_title=clean_text(level_info.get("title",""))
        level_fields=level_info.get("fields") or {}
        repeated_sample_counts=Counter(
            re.sub(r"\s+"," ",clean_text(str(v))).strip().lower()
            for v in level_fields.values()
            if clean_text(str(v)) and len(clean_text(str(v)))<=90
        )
        candidate_labels={
            self._candidate_source_label(field)
            for field in level_fields
            if self._candidate_source_label(field)
        }
        labeled_values={
            clean_text(str(value))
            for field,value in level_fields.items()
            if clean_text(str(field)).lower().startswith(("semantic:","association:","meta:"))
            and clean_text(str(value))
        }
        strong_structural_labels=self._strong_structural_labels(level_fields)
        strong_labeled_values=self._strong_labeled_values(level_fields)
        listing_context=dict(getattr(self,"builder_detail_parent_record",{}) or {})

        # DETAILSEM011: structural fields are admitted in a protected first
        # tier. Ordinary recommendation logic then fills the remaining capacity.
        structural_promotions=self._structural_field_promotions(level_fields)
        authoritative=[]
        for promoted in structural_promotions:
            mapping=self._structural_authority_mapping(promoted,level,level_info)
            if mapping:
                authoritative.append(mapping)

        # Prefer repeatable structural fields, then the strongest structural rank.
        authoritative.sort(
            key=lambda m: (
                1 if m.get("structural_repeatable") else 0,
                float(m.get("context_score",0) or 0)
            ),
            reverse=True
        )
        structural_sources={
            clean_text(str(m.get("source",""))) for m in authoritative
        }

        for field,sample in level_fields.items():
            # Authoritative structural sources are not re-scored as ordinary fields.
            if clean_text(str(field)) in structural_sources:
                continue
            sample_parts=[clean_text(v) for v in re.split(r"\s+\|\s+",clean_text(str(sample))) if clean_text(v)]
            category,confidence=self._classify_discovered_field(field,sample_parts[:3])
            presentation_rank=self._candidate_catalog_rank(
                field,sample,repeated_sample_counts,candidate_labels
            )
            if presentation_rank < -20:
                continue
            if confidence=="Low":
                continue
            if not self._recommendation_provenance_gate(
                category,field,sample,
                page_title=page_title,
                labeled_values=strong_labeled_values or labeled_values,
                strong_structural_labels=strong_structural_labels
            ):
                continue
            score=self._detail_candidate_score(category,field,sample,confidence)
            score+=max(-90,min(55,presentation_rank))
            low=field.lower()

            # Page-labelled fields are stronger business-field evidence than
            # arbitrary visible text. Raw/unlabelled title-like text remains
            # available in the catalog but should not dominate recommendations.
            if "[next-sibling;distance=1]" in low:
                source_label=self._candidate_source_label(field)
                if self._label_role_fitness(source_label,sample):
                    score+=85
                else:
                    score-=120
            elif low.startswith("semantic:"):
                score+=25
            elif category=="Name / Title" and not low.startswith(("semantic:","association:","meta:")):
                score-=40

            quality=max(
                [self._semantic_candidate_quality(category,field,v) for v in sample_parts] or [0]
            )
            if category in {
                "Phone","Email","Date / Time","Price / Amount",
                "Image / Media","Document","Link / Detail"
            } and quality < 50:
                score -= 70
            elif quality:
                score += min(18,int(quality/6))

            profile=(level_info.get("field_profiles") or {}).get(field,{})
            noise_score=self._detail_entity_noise_score(
                category,field,sample,profile,
                page_title=page_title,listing_context=listing_context
            )
            score-=noise_score
            membership_score=self._detail_entity_membership_score(
                category,field,sample,profile,
                detail_url=detail_url,page_title=page_title,
                listing_context=listing_context
            )
            score+=membership_score
            ownership,ownership_score=self._field_ownership_class(
                category,field,sample,profile,
                detail_url=detail_url,page_title=page_title
            )
            identity_strength=self._semantic_identity_strength(
                category,field,sample
            )
            if ownership=="SHARED_CONTEXT":
                score -= 100
            elif ownership=="AMBIGUOUS":
                score -= 24
            else:
                score += min(26,max(0,int((ownership_score-50)/2)))
            if identity_strength < 30:
                score -= 100
            if category in {
                "Email","Phone","Website","Social Link","Company","Image / Media"
            } and ownership != "RECORD":
                score -= 90

            # The inspected page's own title/URL are stronger than generic site
            # navigation values.  This improves recommendation without relying on
            # any site's domain or fixed selectors.
            if category=="Name / Title" and page_title:
                score += self._detail_context_match_score(
                    sample,category,{"Page Title":page_title}
                )
            score += self._detail_context_match_score(
                sample,category,listing_context
            )
            if category=="Link / Detail" and detail_url:
                try:
                    sample_url=next((x for x in sample_parts if self.valid_url(x)),"")
                    if sample_url.rstrip("/")==detail_url.rstrip("/"):
                        score += 65
                    elif sample_url:
                        here=urlparse(detail_url); there=urlparse(sample_url)
                        here_depth=len([x for x in here.path.split("/") if x])
                        there_depth=len([x for x in there.path.split("/") if x])
                        if here.netloc==there.netloc and there_depth < max(1,here_depth-1):
                            score -= 45
                except Exception:
                    pass
            if any(x in low for x in (
                "header","footer","nav","cookie","tracking","breadcrumb","modal",
                "script","style","navbar","menu","brand","logo","site-title",
                "site_title","topbar","sidebar"
            )):
                score-=50
            if category in {"Email","Phone","Location","Name / Title","Image / Media","Document","Identifier","Date / Time","Price / Amount"}:
                score+=12
            if category=="Name / Title":
                generic_label=clean_text(str(sample)).strip().lower()
                if generic_label in {
                    "home","about","contact","products","services","test sites",
                    "menu","login","sign in","search","documentation"
                }:
                    score-=55
            if category=="Link / Detail": score-=4
            candidates.append((score,field,sample,category,confidence))
        candidates.sort(
            reverse=True,
            key=lambda x: (
                x[0],
                1 if "[next-sibling;distance=1]" in clean_text(str(x[1])).lower() else 0,
                1 if clean_text(str(x[1])).lower().startswith("semantic:") else 0
            )
        )
        selected=list(authoritative)
        structural_reserved_count=len(selected)
        ordinary_capacity=max(0,10-structural_reserved_count)
        ordinary_added=0
        concepts=set()
        selected_label_values=set()

        # Reserve semantic concepts/output identities occupied by structural
        # authority so later generic candidates cannot displace or duplicate them.
        for m in authoritative:
            samples=[
                clean_text(v)
                for v in re.split(r"\s+\|\s+",clean_text(str(m.get("sample",""))))
                if clean_text(v)
            ][:3]
            concept=self._semantic_field_concept(
                m.get("source",""),
                m.get("semantic_type",""),
                m.get("output",""),
                samples
            )
            concepts.add(concept)
            label=self._candidate_source_label(m.get("source",""))
            if label:
                selected_label_values.add((
                    self._normalized_candidate_value(label),
                    self._normalized_candidate_value(m.get("sample",""))
                ))

        for score,field,sample,category,confidence in candidates:
            if score<42:
                continue
            profile=(level_info.get("field_profiles") or {}).get(field,{})
            if self._detail_entity_noise_score(
                category,field,sample,profile,
                page_title=page_title,listing_context=listing_context
            ) >= 70:
                continue
            membership_score=self._detail_entity_membership_score(
                category,field,sample,profile,
                detail_url=detail_url,page_title=page_title,
                listing_context=listing_context
            )
            if membership_score < 34:
                continue
            ownership,ownership_score=self._field_ownership_class(
                category,field,sample,profile,
                detail_url=detail_url,page_title=page_title
            )
            identity_strength=self._semantic_identity_strength(
                category,field,sample
            )
            is_structural_promotion=False
            if identity_strength < 30:
                continue
            if ownership=="SHARED_CONTEXT":
                continue
            if category in {
                "Email","Phone","Website","Social Link","Company","Image / Media"
            } and ownership != "RECORD":
                continue
            samples=[clean_text(v) for v in re.split(r"\s+\|\s+",clean_text(str(sample))) if clean_text(v)][:3]
            if category in {"Email","Phone"} and len(samples)>1:
                scalar_candidates=[
                    {"source":field,"value":v,"type":category,"confidence":confidence}
                    for v in samples
                ]
                best_scalar=self._best_detail_candidate(
                    scalar_candidates,category,
                    mapping={"source":field,"sample":sample},
                    detail_url=detail_url,
                    listing_context=listing_context
                )
                if best_scalar:
                    sample=clean_text(str(best_scalar.get("value","")))
                    samples=[sample]
            source_label=self._candidate_source_label(field)
            if is_structural_promotion and source_label:
                output=source_label
            else:
                output=self._uei_output_name(category,field,samples)

            # Prevent standalone values from becoming automatic column names.
            # They remain visible and manually selectable in the full catalog.
            if (
                not is_structural_promotion and
                self._is_value_as_column_name(field,output,sample,page_title=page_title)
            ):
                continue

            # Consolidate duplicate representations of the same labelled
            # field/value while preserving every source candidate in the catalog.
            if source_label:
                label_value_key=(
                    self._normalized_candidate_value(source_label),
                    self._normalized_candidate_value(sample)
                )
                if label_value_key in selected_label_values:
                    continue

            if not self._auto_output_eligible(
                category,field,output,sample,
                ownership=ownership,identity_strength=identity_strength
            ):
                continue
            concept=self._semantic_field_concept(field,category,output,samples)
            if concept in concepts and not concept.startswith("location:"): continue
            concepts.add(concept)
            if source_label:
                selected_label_values.add(label_value_key)
            if ordinary_added>=ordinary_capacity:
                break
            selected.append({
                "source":field,"output":output,
                "transform":"Email / mailto Clean" if category=="Email" else "Text Clean",
                "semantic_type":category,"sample":sample,"context_score":score,
                "ownership":ownership,"ownership_score":ownership_score,
                "auto_selected":True,
                "structural_promotion":bool(is_structural_promotion),
                "enabled":True,"level":int(level)
            })
            ordinary_added+=1
        if ordinary_added<ordinary_capacity:
            before_identity=len(selected)
            self._ensure_detail_identity_mapping(level,selected)
            if len(selected)>before_identity:
                ordinary_added+=1
        selected=[
            m for m in selected
            if m.get("structural_authority")
            or m.get("structural_promotion")
            or self._auto_output_eligible(
                m.get("semantic_type",""),
                m.get("source",""),
                m.get("output",""),
                m.get("sample",""),
                ownership=m.get("ownership","RECORD"),
                identity_strength=self._semantic_identity_strength(
                    m.get("semantic_type",""),
                    m.get("source",""),
                    m.get("sample","")
                )
            )
            or m.get("output") in {"Detail Page URL","Detail Name"}
        ]
        level_info["structural_reserved_count"]=sum(
            1 for m in selected
            if m.get("enabled",True) and m.get("structural_authority")
        )
        level_info["ordinary_recommended_count"]=sum(
            1 for m in selected
            if m.get("enabled",True) and not m.get("structural_authority")
        )
        self.builder_detail_mappings=preserved+selected
        self._builder_suppress_same_level_duplicates(level)
        # RECON001: recommendation may rebuild current-level mappings; reconcile
        # once more from the same exhaustive inventory displayed in the UI.
        self._promote_structural_candidates_to_mappings(level)
        # RECON002 hard invariant: a visible immediate label/value candidate
        # cannot silently vanish between Recommended and Final Output Schema.
        missing_structural=self._structural_schema_invariant(level)
        if missing_structural:
            self._repair_structural_schema_invariant(level)
            missing_structural=self._structural_schema_invariant(level)
        level_info["structural_schema_missing"]=len(missing_structural)
        self._normalize_auto_schema_mappings()
        self._suppress_equivalent_auto_relationship_urls()
        self._apply_final_auto_schema_gate()
        self._builder_suppress_same_level_duplicates(level)
        renamed=self._resolve_cross_level_output_collisions(level)
        self.builder_last_collision_renames=renamed
        self._builder_refresh_detail_mapping_tree(prefix)
        return len([m for m in selected if m.get("enabled",True)])

    def _complete_next_level_inspection(self,prefix,level,relationship,final_url,fields,page_title="",field_profiles=None):
        self._load_builder_detail_fields(
            prefix,final_url,fields,level=level,relationship=relationship,
            page_title=page_title,field_profiles=field_profiles
        )
        selected=self._auto_recommend_detail_level(prefix,level)
        self.builder_show_level(prefix,level)
        renames=getattr(self,"builder_last_collision_renames",[]) or []
        rename_note=(f" {len(renames)} cross-level column collision(s) renamed automatically." if renames else "")
        next_rel=self._relationship_row_for_recommendation(prefix,level)
        next_note=(
            f" Recommended next relationship: {next_rel.get('field')} "
            f"[{next_rel.get('relationship_role','DETAIL')}]."
            if next_rel else
            " No confident lower-level relationship was auto-recommended."
        )
        self.status_var.set(
            f"Page {level} inspected — {len(fields)} fields found; "
            f"{selected} context-ranked record field(s) recommended."+rename_note+
            next_note+
            " Preview Final Dataset to test the merged listing/detail path. "
            "Use Follow as Next Page again only after explicitly choosing a different relationship that leads to a genuinely deeper source."
        )

    def builder_show_level(self,prefix,level):
        tree=getattr(self,f"{prefix}_builder_level_tree",None)
        if tree is not None and tree.exists(str(level)):
            tree.selection_set(str(level)); tree.see(str(level))
        self.builder_current_level=level
        notebook=getattr(self,f"{prefix}_builder_page_notebook",None)
        levels=sorted(self.builder_levels)
        if notebook is not None and level in levels:
            try: notebook.select(levels.index(level))
            except Exception: pass
        self.builder_show_selected_level(prefix)
        self._builder_update_live_summary(prefix)

    # ------------------------------------------------------------------
    # v1.5.4 Multi-Level Extraction Workspace
    # ------------------------------------------------------------------
    def _builder_level_title(self, level):
        if level == 1:
            return "Page 1 — Listing"
        info = self.builder_levels.get(level, {})
        if info.get("level_kind") == "resource_dataset":
            label = clean_text(info.get("title", "Structured Resource")) or "Structured Resource"
            return f"Page {level} — {label}"
        label = clean_text(info.get("title", ""))
        if not label:
            url = clean_text(info.get("url", ""))
            try:
                slug = [x for x in urlparse(url).path.split("/") if x][-1]
                label = re.sub(r"[-_]+", " ", slug).strip().title()
            except Exception:
                label = ""
        if not label:
            label = "Detail Page"
        if len(label) > 30:
            label = label[:27] + "..."
        return f"Page {level} — {label}"

    def _builder_level_mapping_lookup(self, level):
        if level == 1:
            return {m.get("source", ""): m for m in self.builder_mappings}
        return {
            m.get("source", ""): m for m in self.builder_detail_mappings
            if int(m.get("level", 2)) == int(level)
        }

    def _builder_refresh_workspace(self, prefix="public"):
        notebook = getattr(self, f"{prefix}_builder_page_notebook", None)
        if notebook is None:
            return
        current_level = self.builder_current_level

        # v1.5.4 — preserve the user's exact working position whenever a field is
        # included/excluded/renamed.  Earlier builds rebuilt every Treeview from
        # scratch and silently returned the user to the first row, which made
        # selecting several fields on long DOM catalogs unnecessarily painful.
        view_state = {}
        for level, old_tree in getattr(self, "builder_page_trees", {}).items():
            try:
                selected_fields = []
                for iid in old_tree.selection():
                    values = old_tree.item(iid, "values")
                    if values and len(values) >= 3:
                        selected_fields.append(str(values[2]))
                view_state[level] = {
                    "y": old_tree.yview()[0] if old_tree.yview() else 0.0,
                    "x": old_tree.xview()[0] if old_tree.xview() else 0.0,
                    "selected_fields": selected_fields,
                }
            except Exception:
                pass

        # Rebuild from the level model.  The visual position is restored below so
        # model refreshes no longer interrupt field-selection work.
        for tab_id in notebook.tabs():
            notebook.forget(tab_id)
        self.builder_page_trees = {}
        self.builder_page_frames = {}
        if not self.builder_levels:
            frame = ttk.Frame(notebook, padding=12)
            ttk.Label(frame, text="Load a detected dataset to create Page 1.").pack(anchor="w")
            notebook.add(frame, text="Page 1 — Waiting")
            self._builder_update_live_summary(prefix)
            return

        for level in sorted(self.builder_levels):
            info = self.builder_levels[level]
            frame = ttk.Frame(notebook, padding=6)
            self.builder_page_frames[level] = frame
            notebook.add(frame, text=self._builder_level_title(level))
            url = clean_text(info.get("url", ""))
            reached = clean_text(info.get("relationship", "")) or ("Start / Listing Page" if level == 1 else "Relationship")
            ttk.Label(frame, text=f"Sample page: {url}", wraplength=980).pack(anchor="w")
            ttk.Label(frame, text=f"Reached by: {reached}").pack(anchor="w")
            ttk.Label(frame, text="Discovered Elements — all candidates remain available. ✓ = selected for extraction; recommendations only preselect.", wraplength=980).pack(anchor="w", pady=(0,5))

            # v1.5.4: plain-language search belongs on EVERY page tab.
            search_bar = ttk.Frame(frame)
            search_bar.pack(fill="x", pady=(0,5))
            ttk.Label(search_bar, text="Find data:").pack(side="left")
            if not hasattr(self, "builder_page_search_vars"):
                self.builder_page_search_vars = {}
            if not hasattr(self, "builder_page_selected_only_vars"):
                self.builder_page_selected_only_vars = {}
            search_var = self.builder_page_search_vars.get(level)
            if search_var is None:
                search_var = tk.StringVar()
                self.builder_page_search_vars[level] = search_var
            selected_only_var = self.builder_page_selected_only_vars.get(level)
            if selected_only_var is None:
                selected_only_var = tk.BooleanVar(value=False)
                self.builder_page_selected_only_vars[level] = selected_only_var
            search_entry = ttk.Entry(search_bar, textvariable=search_var)
            search_entry.pack(side="left", fill="x", expand=True, padx=(6,6))
            ttk.Button(search_bar, text="Clear", command=lambda l=level: self.builder_workspace_clear_search(prefix,l)).pack(side="left")
            ttk.Checkbutton(search_bar, text="Show selected only", variable=selected_only_var, command=lambda l=level: self.builder_workspace_apply_filter(prefix,l)).pack(side="left", padx=(10,0))
            if level==1:
                ttk.Button(
                    search_bar,text="Desired Data / Examples",
                    command=lambda: self.open_desired_data_dialog(prefix)
                ).pack(side="left",padx=(10,0))
                ttk.Button(
                    search_bar,text="Research Desired Data",
                    command=lambda: self.research_desired_data(prefix)
                ).pack(side="left",padx=(6,0))
                ttk.Button(
                    search_bar,text="Preview Desired Dataset",
                    command=lambda: self.preview_desired_dataset(prefix)
                ).pack(side="left",padx=(6,0))
                ttk.Button(
                    search_bar,text="Configure Cleaning",
                    command=lambda: self.open_semantic_cleaning_dialog(prefix)
                ).pack(side="left",padx=(6,0))

            columns=("use","type","field","sample","output","confidence","action")

            # v1.5.4: keep the Treeview and BOTH scrollbars in a dedicated grid.
            # The previous pack(side=left/right/bottom) combination let the Treeview
            # consume almost all remaining width before the horizontal scrollbar was
            # packed, leaving only the tiny strip seen during regression testing.
            table_frame = ttk.Frame(frame)
            table_frame.pack(fill="both", expand=True)
            table_frame.columnconfigure(0, weight=1)
            table_frame.rowconfigure(0, weight=1)

            tree=ttk.Treeview(table_frame,columns=columns,show="headings",height=9,selectmode="extended")
            specs=(
                ("use","Use",48,False),("type","Type",115,False),("field","Element / Attribute",360,True),
                ("sample","Real Sample Value",430,True),("output","Output Field",170,True),
                ("confidence","Confidence",85,False),("action","Behavior",90,False)
            )
            for col,title,width,stretch in specs:
                tree.heading(col,text=title); tree.column(col,width=width,minwidth=45,stretch=stretch,anchor="center" if col in {"use","confidence","action"} else "w")
            y=ttk.Scrollbar(table_frame,orient="vertical",command=tree.yview)
            x=ttk.Scrollbar(table_frame,orient="horizontal",command=tree.xview)
            tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
            tree.grid(row=0,column=0,sticky="nsew")
            y.grid(row=0,column=1,sticky="ns")
            x.grid(row=1,column=0,sticky="ew")
            # Reserve the lower-right corner so the bars never overlap or collapse.
            ttk.Frame(table_frame,width=14,height=14).grid(row=1,column=1,sticky="nsew")
            self.builder_page_trees[level]=tree
            mappings=self._builder_level_mapping_lookup(level)
            fields=info.get("fields") or {}
            field_to_iid = {}
            repeated_sample_counts=Counter(
                re.sub(r"\s+"," ",clean_text(str(v))).strip().lower()
                for v in fields.values()
                if clean_text(str(v)) and len(clean_text(str(v)))<=90
            )
            candidate_labels={
                self._candidate_source_label(field)
                for field in fields
                if self._candidate_source_label(field)
            }
            ordered_fields=sorted(
                fields.items(),
                key=lambda item: (
                    -self._candidate_catalog_rank(
                        item[0],item[1],repeated_sample_counts,candidate_labels
                    ),
                    str(item[0]).lower()
                )
            )
            for row_index,(field,sample) in enumerate(ordered_fields):
                sample_text=clean_text(str(sample))
                sample_parts=[clean_text(v) for v in re.split(r"\s+\|\s+",sample_text) if clean_text(v)] or ([sample_text] if sample_text else [])
                category,confidence=self._classify_discovered_field(field,sample_parts[:3])
                presentation_rank=self._candidate_catalog_rank(
                    field,sample,repeated_sample_counts,candidate_labels
                )
                if presentation_rank >= 115:
                    confidence="High"
                elif presentation_rank >= 35 and confidence=="Low":
                    confidence="Medium"
                elif presentation_rank < 0:
                    confidence="Low"
                profile=(info.get("field_profiles") or {}).get(field,{})
                noise_score=self._detail_entity_noise_score(
                    category,field,sample,profile,
                    page_title=clean_text(info.get("title","")),
                    listing_context=dict(getattr(self,"builder_detail_parent_record",{}) or {})
                ) if int(level)>1 else 0
                if noise_score>=70:
                    confidence="Low"
                mapping=mappings.get(field)
                enabled=bool(mapping and mapping.get("enabled",True))
                output=(
                    mapping.get("output","") if mapping
                    else self._uei_output_name(category,field,sample_parts[:3])
                )
                action="Capture"
                if category in {"Link / Detail","Document","Image / Media"}:
                    candidate_url=next((u for u in sample_parts if self.valid_url(u)),"")
                    role=self._semantic_url_role(url,candidate_url,"") if candidate_url else "IGNORE"
                    action=(
                        "Follow" if role=="DETAIL"
                        else "Download" if role=="RESOURCE"
                        else "Media" if role=="MEDIA"
                        else "Ignore"
                    )
                iid=str(row_index)
                tree.insert("","end",iid=iid,values=("✓" if enabled else "",category,field,sample_text[:1000],output,confidence,action))
                field_to_iid[str(field)] = iid
            tree.bind("<Double-1>",lambda e,l=level:self.builder_workspace_toggle_row(prefix,l))
            search_var.trace_add("write", lambda *_args, l=level: self.builder_workspace_apply_filter(prefix,l))
            self.after_idle(lambda l=level: self.builder_workspace_apply_filter(prefix,l))

            state = view_state.get(level)
            if state:
                selected_iids = [field_to_iid[f] for f in state.get("selected_fields", []) if f in field_to_iid]
                if selected_iids:
                    try: tree.selection_set(selected_iids)
                    except Exception: pass
                # Defer scrolling until Tk has calculated the rebuilt Treeview's
                # viewport.  This prevents selection from snapping back to row 1.
                self.after_idle(lambda t=tree, y=state.get("y",0.0), x=state.get("x",0.0): (t.yview_moveto(y), t.xview_moveto(x)))

        if current_level not in self.builder_levels:
            current_level=min(self.builder_levels)
        self.builder_current_level=current_level
        tabs=list(sorted(self.builder_levels))
        if current_level in tabs:
            try: notebook.select(tabs.index(current_level))
            except Exception: pass
        self._builder_update_live_summary(prefix)

    def _builder_sync_workspace_level(self, prefix="public", level=None):
        """Update visible row state in place without rebuilding the page Treeview."""
        level = int(level or self.builder_current_level)
        tree = getattr(self, "builder_page_trees", {}).get(level)
        if tree is None:
            return
        mappings = self._builder_level_mapping_lookup(level)
        for iid in list(tree.get_children("")) + list(getattr(self, "_builder_detached_iids", {}).get(level, [])):
            try:
                vals = list(tree.item(iid, "values"))
            except tk.TclError:
                continue
            if len(vals) < 7:
                continue
            field = str(vals[2])
            mapping = mappings.get(field)
            vals[0] = "✓" if (mapping and mapping.get("enabled", True)) else ""
            vals[4] = mapping.get("output", "") if mapping else ""
            tree.item(iid, values=vals)
        self.builder_workspace_apply_filter(prefix, level)
        self._builder_update_live_summary(prefix)

    def builder_workspace_apply_filter(self, prefix="public", level=None):
        level = int(level or self.builder_current_level)
        tree = getattr(self, "builder_page_trees", {}).get(level)
        if tree is None:
            return
        search_vars = getattr(self, "builder_page_search_vars", {})
        selected_vars = getattr(self, "builder_page_selected_only_vars", {})
        query = clean_text(search_vars.get(level).get() if search_vars.get(level) else "").lower()
        selected_only = bool(selected_vars.get(level).get()) if selected_vars.get(level) else False
        mappings = self._builder_level_mapping_lookup(level)
        # No filtering requested: leave the Treeview structure completely alone.
        # This is essential for preserving the exact Page 2+ scroll position after
        # Include/Exclude and Rename operations.
        if not query and not selected_only:
            return
        existing = set(tree.get_children(""))
        all_iids = list(existing) + [iid for iid in getattr(self, "_builder_detached_iids", {}).get(level, []) if iid not in existing]
        all_iids = sorted(set(all_iids), key=lambda x: int(x) if str(x).isdigit() else str(x))
        detached = []
        for iid in all_iids:
            try:
                vals = tree.item(iid, "values")
            except tk.TclError:
                continue
            if not vals or len(vals) < 7:
                continue
            field = str(vals[2]); mapping = mappings.get(field)
            enabled = bool(mapping and mapping.get("enabled", True))
            hay = " ".join(str(v) for v in (vals[1], vals[2], vals[3], vals[4], vals[5], vals[6])).lower()
            show = (not query or all(term in hay for term in query.split())) and (not selected_only or enabled)
            try:
                if show:
                    tree.reattach(iid, "", "end")
                else:
                    tree.detach(iid); detached.append(iid)
            except tk.TclError:
                pass
        if not hasattr(self, "_builder_detached_iids"):
            self._builder_detached_iids = {}
        self._builder_detached_iids[level] = detached

    def builder_workspace_clear_search(self, prefix="public", level=None):
        level = int(level or self.builder_current_level)
        var = getattr(self, "builder_page_search_vars", {}).get(level)
        if var is not None:
            var.set("")
        selected = getattr(self, "builder_page_selected_only_vars", {}).get(level)
        if selected is not None:
            selected.set(False)
        self.builder_workspace_apply_filter(prefix, level)

    def builder_workspace_tab_changed(self, prefix="public"):
        notebook=getattr(self,f"{prefix}_builder_page_notebook",None)
        if notebook is None or not self.builder_levels:
            return
        try:
            idx=notebook.index(notebook.select())
        except Exception:
            return
        levels=sorted(self.builder_levels)
        if not (0 <= idx < len(levels)):
            return
        level=levels[idx]
        self.builder_current_level=level
        # Keep the legacy/advanced inspector synchronized with the active page.
        if level > 1:
            info=self.builder_levels.get(level,{})
            fields=info.get("fields") or {}
            detail_tree=getattr(self,f"{prefix}_builder_detail_tree",None)
            if detail_tree is not None:
                self.clear_tree(detail_tree)
                self.builder_detail_fields=dict(fields); self.builder_detail_url=info.get("url","")
                repeated_sample_counts=Counter(
                    re.sub(r"\s+"," ",clean_text(str(v))).strip().lower()
                    for v in fields.values()
                    if clean_text(str(v)) and len(clean_text(str(v)))<=90
                )
                candidate_labels={
                    self._candidate_source_label(field)
                    for field in fields
                    if self._candidate_source_label(field)
                }
                ordered_fields=sorted(
                    fields.items(),
                    key=lambda item: (
                        -self._candidate_catalog_rank(
                            item[0],item[1],repeated_sample_counts,candidate_labels
                        ),
                        str(item[0]).lower()
                    )
                )
                for field,sample in ordered_fields:
                    category,confidence=self._classify_discovered_field(field,[clean_text(str(sample))])
                    rank=self._candidate_catalog_rank(
                        field,sample,repeated_sample_counts,candidate_labels
                    )
                    if rank>=115:
                        confidence="High"
                    elif rank<0:
                        confidence="Low"
                    detail_tree.insert("","end",values=(category,field,str(sample)[:900],confidence))
        self._builder_update_live_summary(prefix)

    def _builder_workspace_selection(self, level):
        tree=self.builder_page_trees.get(level)
        if tree is None:
            return []
        rows=[]
        for iid in tree.selection():
            vals=tree.item(iid,"values")
            if vals and len(vals)>=7:
                rows.append({"iid":iid,"type":vals[1],"field":vals[2],"sample":vals[3],"output":vals[4],"confidence":vals[5],"action":vals[6]})
        return rows

    def builder_workspace_toggle_row(self,prefix="public",level=None):
        level=level or self.builder_current_level
        rows=self._builder_workspace_selection(level)
        if not rows:return
        lookup=self._builder_level_mapping_lookup(level)
        enable=not all(bool(lookup.get(r["field"]) and lookup[r["field"]].get("enabled",True)) for r in rows)
        self.builder_workspace_set_selected(prefix,enable,level=level)

    def builder_workspace_set_selected(self,prefix="public",enabled=True,level=None):
        level=level or self.builder_current_level
        rows=self._builder_workspace_selection(level)
        if not rows:
            messagebox.showinfo("Select Fields","Select one or more element rows in the active page tab first.")
            return
        if level==1:
            lookup={m.get("source",""):m for m in self.builder_mappings}
            for row in rows:
                mapping=lookup.get(row["field"])
                if mapping is None:
                    transform="Email / mailto Clean" if row["type"]=="Email" else "Text Clean"
                    mapping={"source":row["field"],"output":self._uei_output_name(row["type"],row["field"],[row["sample"]]),"transform":transform,"enabled":bool(enabled),"semantic_type":row["type"],"sample":row["sample"],"auto_selected":False,"user_selected":bool(enabled)}
                    self.builder_mappings.append(mapping); lookup[row["field"]]=mapping
                else:
                    mapping["enabled"]=bool(enabled)
                    mapping["auto_selected"]=False
                    mapping["user_selected"]=bool(enabled)
                    mapping.pop("final_gate_rejected",None)
            self._builder_refresh_mapping_tree(prefix, refresh_workspace=False)
        else:
            lookup={(int(m.get("level",2)),m.get("source","")):m for m in self.builder_detail_mappings}
            for row in rows:
                key=(int(level),row["field"]); mapping=lookup.get(key)
                if mapping is None:
                    transform="Email / mailto Clean" if row["type"]=="Email" else "Text Clean"
                    mapping={"source":row["field"],"output":self._uei_output_name(row["type"],row["field"],[row["sample"]]),"transform":transform,"semantic_type":row["type"],"sample":row["sample"],"context_score":self._detail_candidate_score(row["type"],row["field"],row["sample"],row["confidence"]),"enabled":bool(enabled),"level":int(level),"auto_selected":False,"user_selected":bool(enabled)}
                    self.builder_detail_mappings.append(mapping); lookup[key]=mapping
                else:
                    mapping["enabled"]=bool(enabled)
                    mapping["auto_selected"]=False
                    mapping["user_selected"]=bool(enabled)
                    mapping.pop("final_gate_rejected",None)
            suppressed = []
            self._builder_refresh_detail_mapping_tree(prefix, refresh_workspace=False)
            if suppressed:
                self.status_var.set("Duplicate output suppressed automatically — kept the strongest context-ranked candidate for: " + ", ".join(sorted(set(str(x) for x in suppressed))))
        self._builder_sync_workspace_level(prefix, level)

    def _builder_suppress_same_level_duplicates(self, level):
        """Keep one active mapping for the same logical output on one page.

        Repeated DOM candidates (for example a global header phone and the
        profile phone) can classify to the same friendly output name.  Export is
        dictionary-based, so allowing both creates a misleading summary and one
        value silently overwrites the other.  Prefer the stronger context-ranked
        candidate and disable the weaker duplicate automatically.
        """
        if int(level) <= 1:
            mappings = [m for m in self.builder_mappings if m.get("enabled", True)]
        else:
            mappings = [m for m in self.builder_detail_mappings if m.get("enabled", True) and int(m.get("level",2)) == int(level)]
        groups = {}
        for m in mappings:
            output = clean_text(str(m.get("output") or m.get("source") or ""))
            key = re.sub(r"\s+", " ", output).strip().lower()
            if not key:
                continue
            groups.setdefault(key, []).append(m)
        suppressed = []
        for _key, items in groups.items():
            if len(items) < 2:
                continue
            def rank(m):
                score = float(m.get("context_score", 0) or 0)
                if m.get("structural_authority"):
                    score += 100000
                elif m.get("structural_promotion"):
                    score += 50000
                source = clean_text(str(m.get("source", ""))).lower()
                # Generic structural hints: page-specific content beats global
                # chrome when two candidates claim the same business field.
                if any(tok in source for tok in ("profile", "contact", "information", "content", "detail", "member")):
                    score += 12
                if any(tok in source for tok in ("header", "footer", "nav", "cookie", "modal", "noscript", "tracking")):
                    score -= 18
                return score
            manual_items=[m for m in items if m.get("user_selected") or not m.get("auto_selected")]
            if manual_items:
                for m in items:
                    if m not in manual_items and m.get("auto_selected"):
                        m["enabled"]=False
                        suppressed.append(m.get("output") or m.get("source"))
                continue
            authority_items=[m for m in items if m.get("structural_authority")]
            keep = max(authority_items or items, key=rank)
            for m in items:
                if m is not keep:
                    m["enabled"] = False
                    suppressed.append(m.get("output") or m.get("source"))
        return suppressed

    def builder_workspace_rename(self,prefix="public"):
        level=self.builder_current_level; rows=self._builder_workspace_selection(level)
        if not rows:
            messagebox.showinfo("Select Field","Select one field in the active page tab first."); return
        row=rows[0]
        lookup=self._builder_level_mapping_lookup(level); mapping=lookup.get(row["field"])
        if mapping is None or not mapping.get("enabled",True):
            messagebox.showinfo("Keep Field First","Keep/check the field before renaming its output column."); return
        name=simpledialog.askstring("Rename Output Column","Output column name:",initialvalue=mapping.get("output",""),parent=self)
        if name and clean_text(name):
            mapping["output"]=clean_text(name)
            if level==1:self._builder_refresh_mapping_tree(prefix, refresh_workspace=False)
            else:self._builder_refresh_detail_mapping_tree(prefix, refresh_workspace=False)
            self._builder_sync_workspace_level(prefix, level)

    def builder_workspace_move_page(self, prefix="public", direction=1):
        notebook = getattr(self, f"{prefix}_builder_page_notebook", None)
        levels = sorted(self.builder_levels)
        if notebook is None or not levels:
            return
        try:
            current = levels.index(self.builder_current_level)
        except ValueError:
            current = 0
        target = max(0, min(len(levels)-1, current + int(direction)))
        self.builder_current_level = levels[target]
        try: notebook.select(target)
        except Exception: pass
        self._builder_update_live_summary(prefix)

    def builder_workspace_recommended(self,prefix="public"):
        """DETAILSEM013: workspace recommendation delegates to canonical engine."""
        level=self.builder_current_level
        if level==1:
            self.builder_add_recommended(prefix)
        else:
            self._auto_recommend_detail_level(prefix,level)
        self._builder_update_live_summary(prefix)


    def _resolve_relationship_record_field(self, dataset, row, prefix="public"):
        """SEMATTR001: resolve the actual record field behind a Follow row."""
        records=list(dataset.get("records") or [])
        fields=list(dataset.get("fields") or [])
        row_field=clean_text(str((row or {}).get("field","")))
        row_output=clean_text(str((row or {}).get("output","")))

        def has_urls(field):
            return field and any(
                self.valid_url(clean_text(str(r.get(field,""))))
                for r in records[:80]
            )

        if row_field in fields and has_urls(row_field):
            return row_field

        rec=getattr(self,"builder_relationship_recommendation",{}) or {}
        bound=clean_text(str(rec.get("field","")))
        if bound in fields and has_urls(bound):
            return bound

        for m in getattr(self,"builder_mappings",[]) or []:
            if not m.get("enabled",True):
                continue
            source=clean_text(str(m.get("source","")))
            output=clean_text(str(m.get("output","")))
            if (row_field and row_field in {source,output}) or (row_output and row_output in {source,output}):
                if source in fields and has_urls(source):
                    return source

        base=(clean_text(self.public_url.get().strip()) if prefix=="public"
              else clean_text(self.protected_url.get().strip()))
        ranked=[]
        for field in fields:
            if not has_urls(field):
                continue
            urls=[clean_text(str(r.get(field,""))) for r in records[:60]]
            urls=[u for u in urls if self.valid_url(u)]
            if not urls:
                continue
            roles=Counter(self._semantic_url_role(base,u,"") for u in urls)
            detail_ratio=roles.get("DETAIL",0)/len(urls)
            pagination_ratio=roles.get("PAGINATION",0)/len(urls)
            score=detail_ratio*100-pagination_ratio*80
            low=field.lower()
            if any(k in low for k in ("url","href","detail","link","website")):
                score+=10
            ranked.append((score,field))
        ranked.sort(reverse=True)
        if ranked and ranked[0][0]>=50:
            return ranked[0][1]
        return ""

    def _relationship_row_for_recommendation(self, prefix, level):
        """RELTREE001: strongest followable relationship on any configured level."""
        level=int(level)
        tree=getattr(self,"builder_page_trees",{}).get(level)

        if level==1:
            rec=getattr(self,"builder_relationship_recommendation",{}) or {}
            field=clean_text(rec.get("field",""))
            if field:
                if tree is not None:
                    for iid in tree.get_children(""):
                        vals=tree.item(iid,"values")
                        if vals and len(vals)>=7 and str(vals[2])==field:
                            return {
                                "iid":iid,"type":vals[1],"field":vals[2],
                                "sample":vals[3],"output":vals[4],
                                "confidence":vals[5],"action":vals[6]
                            }
                return {
                    "iid":"","type":"Link / Detail","field":field,
                    "sample":rec.get("sample_url",""),"output":"",
                    "confidence":"High","action":"Follow"
                }

        info=(self.builder_levels or {}).get(level,{})
        source_url=clean_text(str(info.get("url","")))
        profiles=info.get("field_profiles") or {}
        ranked=[]

        for field,sample in (info.get("fields") or {}).items():
            sample_text=clean_text(str(sample))
            values=[
                clean_text(v) for v in re.split(r"\s+\|\s+",sample_text)
                if clean_text(v)
            ]
            urls=[u for u in values if self.valid_url(u)]
            if not urls:
                continue

            category,confidence=self._classify_discovered_field(field,urls[:3])
            if category not in {"Link / Detail","Document","Image / Media"}:
                continue

            roles=Counter(self._semantic_url_role(source_url,u,"") for u in urls[:5])
            role=roles.most_common(1)[0][0] if roles else "IGNORE"
            if role not in {"DETAIL","RESOURCE","MEDIA"}:
                continue

            score={"DETAIL":100,"RESOURCE":88,"MEDIA":62}.get(role,0)
            identity=self._semantic_identity_strength(category,field,sample_text)
            score+=min(24,int(identity/4))

            profile=profiles.get(field,{})
            score+=int(float(profile.get("presence_ratio") or 0.0)*14)
            score+=int(float(profile.get("unique_ratio") or 0.0)*12)
            if float(profile.get("dominant_ratio") or 0.0)>=0.90 and role=="DETAIL":
                score-=14

            low=clean_text(str(field)).lower()
            if any(k in low for k in (
                "detail","profile","record","item","product","property",
                "member","case","article","post","document","file","download"
            )):
                score+=16
            if any(k in low for k in (
                "home","contact","login","privacy","terms","footer","header",
                "navigation","menu","social"
            )):
                score-=40

            ranked.append((score,role,field,sample_text,category,confidence))

        if not ranked:
            return None
        ranked.sort(reverse=True,key=lambda x:x[0])
        score,role,field,sample_text,category,confidence=ranked[0]
        if score<70:
            return None

        return {
            "iid":"",
            "type":"Document" if role=="RESOURCE" else "Image / Media" if role=="MEDIA" else "Link / Detail",
            "field":field,
            "sample":sample_text,
            "output":"",
            "confidence":confidence,
            "action":"Follow" if role=="DETAIL" else "Download",
            "relationship_role":role,
            "relationship_score":score,
        }

    @classmethod
    def _record_detail_relationship_fitness(cls, source_url, field, records):
        """RECON005: prove that a URL field behaves like record -> detail, not taxonomy/navigation."""
        records=list(records or [])[:40]
        urls=[]
        value_pairs=[]
        field_low=clean_text(str(field)).lower()
        hard_field_noise=("taxonomy","category","categories","tag","tags","industry","sector",
                          "collection","about","author","wp:","curies","self","parent")
        if any(tok in field_low for tok in hard_field_noise):
            return 0.0, "field semantics indicate taxonomy/navigation metadata"
        for rec in records:
            u=clean_text(str(rec.get(field,"")))
            if not cls.valid_url(u):
                continue
            urls.append(u)
            identity=""
            for key,val in rec.items():
                kl=str(key).lower()
                if key==field or not any(t in kl for t in ("name","title","headline","slug")):
                    continue
                candidate=clean_text(str(val))
                if candidate and cls.valid_url(candidate) is False and len(candidate)<=180:
                    identity=candidate
                    break
            value_pairs.append((u,identity))
        if len(urls)<2:
            return 0.0, "insufficient repeated-record URL evidence"
        unique_ratio=len(set(urls))/len(urls)
        same_host=0
        path_diversity=set()
        identity_hits=0
        try:
            source_host=urlsplit(source_url).netloc.lower().removeprefix("www.")
        except Exception:
            source_host=""
        for u,identity in value_pairs:
            try:
                parts=urlsplit(u)
                host=parts.netloc.lower().removeprefix("www.")
                if source_host and host==source_host:
                    same_host+=1
                seg=[x for x in parts.path.lower().split("/") if x]
                if seg:
                    path_diversity.add(tuple(seg[:-1]))
                slug=(seg[-1] if seg else "").replace("-"," ").replace("_"," ")
            except Exception:
                slug=""
            if identity:
                tokens=[t for t in re.findall(r"[a-z0-9]+",identity.lower()) if len(t)>=3]
                if tokens and sum(1 for t in tokens if t in slug)>=max(1,min(2,len(tokens))):
                    identity_hits+=1
        host_ratio=same_host/len(urls) if urls else 0.0
        identity_ratio=identity_hits/len(value_pairs) if value_pairs else 0.0
        score=(0.45*unique_ratio)+(0.25*host_ratio)+(0.30*identity_ratio)
        if unique_ratio<0.55:
            score-=0.25
        if len(path_diversity)==1 and identity_ratio<0.20:
            score-=0.10
        return max(0.0,min(1.0,score)), (
            f"record/detail fitness={score:.2f}; unique={unique_ratio:.2f}; "
            f"same-host={host_ratio:.2f}; identity={identity_ratio:.2f}"
        )

    def _validate_relationship_before_follow(self, source_url, target_url, label="", field="", records=None):
        """RELTRAV003: validate traversal using the same semantic URL evidence."""
        role=self._semantic_url_role(source_url,target_url,label)
        if records and field:
            fitness,fitness_note=self._record_detail_relationship_fitness(
                source_url,field,records
            )
            if fitness < 0.42:
                return False,"NAVIGATION",(
                    "The URL is valid, but the repeated-record evidence does not prove "
                    "a record-to-detail relationship. " + fitness_note
                )
            if role=="DETAIL" and fitness >= 0.42:
                return True,role,fitness_note
        if role=="DETAIL":
            return True,role,""

        # A single URL may be ambiguous. For soft PAGINATION ambiguity only,
        # repeated-record evidence may confirm that this field behaves as Detail.
        # Hard structural roles are never overridden.
        hard_roles={"RESOURCE","MEDIA","EXTERNAL","NAVIGATION","IGNORE"}
        if role not in hard_roles and field and records:
            roles=Counter()
            valid=0
            for rec in list(records)[:60]:
                u=clean_text(str(rec.get(field,"")))
                if not self.valid_url(u):
                    continue
                valid+=1
                rec_label=""
                for lf in rec.keys():
                    if any(k in str(lf).lower() for k in ("title","name","link_text","headline")):
                        rec_label=clean_text(str(rec.get(lf,"")))
                        if rec_label:
                            break
                roles[self._semantic_url_role(source_url,u,rec_label)]+=1
            if valid:
                detail_ratio=roles.get("DETAIL",0)/valid
                pagination_ratio=roles.get("PAGINATION",0)/valid
                if detail_ratio>=0.75 and pagination_ratio<=0.10:
                    return True,"DETAIL","Record-set consensus confirms a detail relationship."

        messages={
            "PAGINATION":"This relationship is pagination, not a record-detail page.",
            "NAVIGATION":"This relationship appears to be site navigation rather than a record-detail page.",
            "EXTERNAL":"This relationship leaves the current website. Capture it as data unless deeper external extraction is explicitly requested.",
            "RESOURCE":"This relationship is a downloadable resource and should use Resource Discovery.",
            "MEDIA":"This relationship is media, not a detail page.",
            "IGNORE":"This relationship is not a followable record-detail target.",
        }
        return False,role,messages.get(role,f"This relationship is classified as {role}, not Detail.")

    def builder_workspace_follow(self,prefix="public"):
        level=self.builder_current_level; rows=self._builder_workspace_selection(level)
        if not rows:
            recommended=self._relationship_row_for_recommendation(prefix,level)
            if not recommended and int(level)==1:
                plan=getattr(self,"semantic_extraction_plan",{}) or {}
                idx=getattr(self,"builder_loaded_dataset_index",-1)
                if not (0 <= int(idx) < len(getattr(self,"detected_groups",[]) or [])):
                    idx=getattr(self,f"{prefix}_builder_dataset").current()
                if 0 <= int(idx) < len(getattr(self,"detected_groups",[]) or []):
                    best,ranked=self._recommended_detail_relationship(idx,plan)
                    best=self._bind_recommended_detail_relationship(prefix,idx,plan,best,ranked)
                    recommended=self._relationship_row_for_recommendation(prefix,level) if best else None
            rows=[recommended] if recommended else []
        if not rows:
            messagebox.showinfo(
                "Select Relationship",
                "Select a Link / Detail or Document relationship in the active page tab. "
                "RTUDES also checks the current page for a recommended next relationship automatically."
            ); return
        row=rows[0]
        chosen_label=""
        if row["type"] not in {"Link / Detail","Document"}:
            messagebox.showinfo("Not a Relationship","Select a Link / Detail or Document URL row to continue to the next source."); return
        url=""; chosen_record=None
        resolved_field=clean_text(str(row.get("field","")))
        if level==1:
            choice=getattr(self,f"{prefix}_builder_dataset"); idx=choice.current()
            if 0 <= idx < len(self.detected_groups):
                dataset=self.detected_groups[idx]
                resolved_field=self._resolve_relationship_record_field(dataset,row,prefix) or resolved_field
                valid_records=[r for r in dataset.get("records",[]) if self.valid_url(clean_text(str(r.get(resolved_field,""))))]
                # A download-directory page may expose several distinct resources.
                # Let the user choose the document instead of silently taking item 1.
                if row["type"]=="Document" and len(valid_records)>1:
                    lines=[]
                    for i,r in enumerate(valid_records[:20],1):
                        label=clean_text(str(r.get("Document Name") or r.get("Name / Link Text") or r.get(resolved_field,"")))
                        dtype=clean_text(str(r.get("Document Type", "")))
                        lines.append(f"{i}. {label}" + (f" [{dtype}]" if dtype else ""))
                    prompt="Choose the structured resource to inspect:\n\n" + "\n".join(lines)
                    if len(valid_records)>20: prompt += f"\n...and {len(valid_records)-20} more"
                    choice_no=simpledialog.askinteger("Choose Structured Resource",prompt,initialvalue=1,minvalue=1,maxvalue=min(20,len(valid_records)),parent=self)
                    if not choice_no: return
                    chosen_record=valid_records[choice_no-1]
                elif valid_records:
                    base=(clean_text(self.public_url.get().strip()) if prefix=="public"
                          else clean_text(self.protected_url.get().strip()))
                    fitness,fitness_note=self._record_detail_relationship_fitness(
                        base,resolved_field,valid_records
                    )
                    if fitness >= 0.42:
                        # Bounded deterministic sample: inspect one representative record.
                        chosen_record=valid_records[0]
                    else:
                        self.status_var.set(
                            "Relationship rejected before traversal — " + fitness_note
                        )
                if chosen_record:
                    url=clean_text(str(chosen_record.get(resolved_field,"")))
                    for lf in dataset.get("fields",[]):
                        if any(k in lf.lower() for k in ("title","name","link_text","headline")):
                            chosen_label=clean_text(str(chosen_record.get(lf,"")))
                            if chosen_label:
                                break
            if url:
                self.builder_detail_link_field=resolved_field
                box=getattr(self,f"{prefix}_builder_detail_url_field",None)
                if box is not None:
                    try: box.set(resolved_field)
                    except Exception: pass
        else:
            samples=[clean_text(x) for x in re.split(r"\s+\|\s+",row["sample"]) if clean_text(x)]
            url=next((x for x in samples if self.valid_url(x)),"")
            chosen_label=clean_text(str(row.get("output","")))
        if not self.valid_url(url):
            messagebox.showinfo(
                "No Sample URL",
                "RTUDES could not resolve a complete http/https sample URL from the selected relationship. "
                "The workspace field, recommended relationship, enabled mapping, and detected URL fields were checked."
            ); return
        source_page=(
            clean_text(self.public_url.get().strip()) if int(level)==1 and prefix=="public"
            else clean_text(self.protected_url.get().strip()) if int(level)==1
            else clean_text(self.builder_levels.get(level,{}).get("url",""))
        )
        if row["type"]!="Document" and _resource_extension(url) not in {".pdf",".csv",".xls",".xlsx",".json",".xml"}:
            consensus_records=[]
            if int(level)==1:
                choice=getattr(self,f"{prefix}_builder_dataset")
                didx=choice.current()
                if 0 <= didx < len(self.detected_groups):
                    consensus_records=self.detected_groups[didx].get("records",[]) or []
            ok,role,msg=self._validate_relationship_before_follow(
                source_page,url,chosen_label,resolved_field,consensus_records
            )
            if not ok:
                messagebox.showwarning(
                    "Relationship Not Qualified",
                    f"RTUDES classified this relationship as {role}.\n\n{msg}\n\n"
                    "Choose a genuine record-detail relationship instead."
                )
                return
        # RECON027 RECOVERY: each followed level must advance to a genuinely
        # different source. Do not create Page N+1 if the target is already one
        # of the configured page URLs.
        def _norm_follow_url(value):
            try:
                parsed=urlparse(clean_text(str(value)))
                path=re.sub(r"/+$","",parsed.path or "") or "/"
                return (parsed.scheme.lower(),parsed.netloc.lower(),path,parsed.query)
            except Exception:
                return ("","",clean_text(str(value)).rstrip("/"),"")
        target_key=_norm_follow_url(url)
        existing_level=None
        for _lvl,_info in sorted(self.builder_levels.items()):
            existing_url=clean_text(str((_info or {}).get("url","")))
            if existing_url and _norm_follow_url(existing_url)==target_key:
                existing_level=int(_lvl)
                break
        if existing_level is not None:
            messagebox.showwarning(
                "Duplicate Page Relationship",
                f"This relationship points back to Page {existing_level}, so RTUDES will not create another level.\n\n"
                "Choose another Link / Detail only when it leads to a genuinely different deeper source."
            )
            self.status_var.set(
                f"Follow blocked — target already configured as Page {existing_level}. "
                "No duplicate page level was created."
            )
            return

        next_level=max(self.builder_levels.keys() or [1])+1
        # v1.6.1.2: downloadable PDF/CSV/Excel/JSON/XML resources are not ordinary
        # web pages. Parse the resource contents into a real row dataset instead.
        ext=_resource_extension(url)
        if row["type"]=="Document" or ext in {".pdf",".csv",".xls",".xlsx",".json",".xml"}:
            self.status_var.set(f"Analyzing structured resource for Page {next_level}: {url}")
            self.builder_resource_parent_record = chosen_record
            if level==1 and self.builder_resource_parent_record is None:
                choice=getattr(self,f"{prefix}_builder_dataset"); idx=choice.current()
                if 0 <= idx < len(self.detected_groups):
                    dataset=self.detected_groups[idx]
                    self.builder_resource_parent_record=next((r for r in dataset.get("records",[]) if clean_text(str(r.get(resolved_field,"")))==url),None)
            threading.Thread(target=self._builder_resource_worker,args=(url,prefix,next_level,resolved_field),daemon=True).start()
            return
        if int(level)==1:
            self.builder_detail_parent_record = dict(chosen_record or {})
        self.status_var.set(f"Opening Page {next_level}: {url}")
        sibling_samples=[url]
        if int(level)==1:
            choice=getattr(self,f"{prefix}_builder_dataset")
            didx=choice.current()
            if 0 <= didx < len(self.detected_groups):
                for rec in self.detected_groups[didx].get("records",[]) or []:
                    candidate=clean_text(str(rec.get(resolved_field,"")))
                    if self.valid_url(candidate) and candidate not in sibling_samples:
                        sibling_samples.append(candidate)
                    if len(sibling_samples)>=3:
                        break
        else:
            profile=(self.builder_levels.get(int(level),{}).get("field_profiles") or {}).get(resolved_field,{})
            for candidate in profile.get("values",[]) or []:
                candidate=clean_text(str(candidate))
                if self.valid_url(candidate) and candidate not in sibling_samples:
                    sibling_samples.append(candidate)
                if len(sibling_samples)>=5:
                    break
        threading.Thread(
            target=self._builder_next_level_worker,
            args=(url,prefix,next_level,resolved_field,sibling_samples),
            daemon=True
        ).start()

    def _preserve_structural_authority_in_final_schema(self, level=None):
        """Preserve and promote proven structural mappings through final-schema stages."""
        levels_to_promote=(
            [int(level)] if level is not None
            else sorted(int(k) for k in self.builder_levels if int(k)>1)
        )
        for lvl in levels_to_promote:
            self._promote_structural_candidates_to_mappings(lvl)
            self._repair_structural_schema_invariant(lvl)
        restored=[]
        for m in self.builder_detail_mappings:
            if level is not None and int(m.get("level",2))!=int(level):
                continue
            if not m.get("structural_authority"):
                continue
            if not m.get("auto_selected",True):
                continue
            if not m.get("enabled",True):
                m["enabled"]=bool(m.get("enabled",True))
                restored.append(m.get("output") or m.get("source"))

        levels=(
            [int(level)] if level is not None
            else sorted({int(m.get("level",2)) for m in self.builder_detail_mappings})
        )
        for lvl in levels:
            self._builder_suppress_same_level_duplicates(lvl)
        return restored

    @staticmethod
    def _location_value_kind(value):
        """RECON022: classify a location value by shape, independent of source label."""
        text=clean_text(str(value))
        if not text:
            return "Location"
        low=text.lower()

        street_tokens=(
            " street"," st"," road"," rd"," avenue"," ave"," boulevard"," blvd",
            " drive"," dr"," lane"," ln"," court"," ct"," highway"," hwy",
            " parkway"," pkwy"," circle"," cir"," terrace"," ter"," way",
            " route"," rte"," suite"," ste"," unit"," floor"," fl "
        )
        if re.match(r"^\s*\d+[a-z0-9\-]*\s+",low) and any(t in low for t in street_tokens):
            return "Address"
        if re.search(r"\b(?:suite|ste\.?|unit|floor|fl\.?)(?:\s+|#)\s*[#a-z0-9-]+",low,re.I):
            return "Address"

        if re.search(r",\s*[a-z]{2}\b",low) and re.search(r"\b\d{5}(?:-\d{4})?\b",low):
            return "Location"
        if re.search(r",\s*[a-z]{2}\b",low):
            return "Location"
        if re.search(r"\b\d{5}(?:-\d{4})?\b",low):
            return "Location"

        if len(text)<=70 and not re.search(r"\d",text) and 1<=len(text.split())<=6:
            return "City"
        return "Location"

    @classmethod
    def _canonical_location_output(cls, source, sample):
        """Choose Address/City/State/Postcode/Country/Location using source+value evidence."""
        low=clean_text(str(source)).lower()
        sample_kind=cls._location_value_kind(sample)

        if sample_kind=="Address":
            return "Address"
        if any(t in low for t in ("postcode","postalcode","postal_code","zipcode","zip code","[zip]",".zip")):
            return "Postcode"
        if any(t in low for t in ("country","countrycode","country_code")):
            return "Country"
        if any(t in low for t in ("address","street","address1","address2","address3","address4")):
            return "Address"
        if any(t in low for t in ("state","province","region")) and sample_kind!="Location":
            return "State"
        if "city" in low and sample_kind=="City":
            return "City"
        return sample_kind


    @classmethod
    def _canonical_auto_output_name(cls, level, mapping):
        """RECON021: canonical final column identity for system-selected mappings."""
        source=clean_text(str(mapping.get("source","")))
        current=clean_text(str(mapping.get("output") or source))
        sample=clean_text(str(mapping.get("sample","")))
        category=clean_text(str(mapping.get("semantic_type","")))
        low=source.lower()

        # Canonical semantic identities. Source paths remain intact for extraction.
        canonical={
            "Name / Title":"Name" if int(level)<=1 else "Detail Name",
            "Link / Detail":"Detail URL" if int(level)<=1 else "Detail Page URL",
            "Email":"Email" if int(level)<=1 else "Detail Email",
            "Phone":"Phone" if int(level)<=1 else "Detail Phone",
            "Company":"Company",
            "Website":"Website",
            "Social Link":"Social URL",
            "Biography / Description":"Description" if int(level)<=1 else "About",
            "Image / Media":"Image URL",
            "Document":"Document URL",
            "Price / Amount":"Price",
            "Date / Time":"Date",
            "Identifier":"Identifier",
            "Coordinate":"Coordinate",
        }
        if category in canonical:
            return canonical[category]

        # RECON022: validate the actual value shape before trusting source labels.
        if category=="Location":
            return cls._canonical_location_output(source,sample)

        # Preserve a proven page-derived label only when it behaves like a
        # schema label rather than a literal data value.
        label=cls._candidate_source_label(source)
        if label:
            candidate=clean_text(str(label))
            if candidate and not cls._is_value_as_column_name(
                source,candidate,sample,page_title=""
            ):
                return candidate

        # Raw metadata keys are source details, not final business column names.
        if low.startswith(("meta:","og:","twitter:","schema:")):
            return ""

        # Generic presentation outputs are never canonical names.
        norm=re.sub(r"[^a-z0-9]+"," ",current.lower()).strip()
        if norm in {
            "a","p","span","div","text","field","value","content",
            "span nth 1","span nth 2","span nth 3","a nth 1","a nth 2",
            "h1","h2","h3","h4","small","strong","label"
        }:
            return ""

        # Unclassified automatic outputs are kept only if they are true labels
        # and not values masquerading as column names.
        if category in {"Text","List / Group",""}:
            if cls._is_value_as_column_name(source,current,sample,page_title=""):
                return ""
            if not source.lower().startswith(("semantic:","association:")):
                return ""
        return current

    def _suppress_equivalent_auto_relationship_urls(self):
        """RECON022: suppress redundant automatic relationship URLs across levels."""
        listing_urls=[
            m for m in self.builder_mappings
            if m.get("enabled",True)
            and m.get("auto_selected")
            and clean_text(str(m.get("semantic_type","")))=="Link / Detail"
        ]
        if not listing_urls:
            return []

        changed=[]
        for m in self.builder_detail_mappings:
            if not m.get("enabled",True) or not m.get("auto_selected"):
                continue
            if clean_text(str(m.get("semantic_type","")))!="Link / Detail":
                continue
            level=int(m.get("level",2) or 2)
            info=self.builder_levels.get(level,{}) or {}
            current_url=clean_text(str(info.get("url",""))).rstrip("/")
            sample=clean_text(str(m.get("sample",""))).rstrip("/")
            if current_url and sample and sample==current_url:
                m["enabled"]=False
                m["equivalent_relationship_url"]=True
                changed.append((level,m.get("output") or m.get("source")))
        return changed


    def _normalize_auto_schema_mappings(self):
        """Normalize/disable automatic mappings before the final gate and display."""
        changed=[]
        for level,mappings in [
            (1,self.builder_mappings),
            *[
                (lvl,[m for m in self.builder_detail_mappings if int(m.get("level",2))==int(lvl)])
                for lvl in sorted(k for k in self.builder_levels if int(k)>1)
            ]
        ]:
            seen={}
            for m in mappings:
                if not m.get("enabled",True) or not m.get("auto_selected"):
                    continue
                canonical=self._canonical_auto_output_name(level,m)
                if not canonical:
                    m["enabled"]=False
                    changed.append((level,m.get("output") or m.get("source"),"disabled"))
                    continue
                if canonical != clean_text(str(m.get("output") or "")):
                    old=m.get("output") or m.get("source")
                    m["output"]=canonical
                    changed.append((level,old,canonical))

                # One automatic mapping per canonical identity per page.
                key=canonical.lower()
                score=float(m.get("context_score",0) or 0)
                incumbent=seen.get(key)
                if incumbent is None:
                    seen[key]=(score,m)
                else:
                    keep_score,keep=incumbent
                    if score>keep_score:
                        keep["enabled"]=False
                        seen[key]=(score,m)
                    else:
                        m["enabled"]=False
        return changed

    def _final_auto_schema_gate(self, level, mapping):
        """RECON020: last, source-agnostic gate before an AUTO mapping may enter Final Output Schema.

        Manual user selections are intentionally untouched.  Every discovery path
        (dynamic API, structural authority, association, semantic and ordinary
        candidate ranking) converges here.
        """
        if not mapping.get("auto_selected"):
            return True

        source=clean_text(str(mapping.get("source","")))
        output=clean_text(str(mapping.get("output") or source))
        sample=clean_text(str(mapping.get("sample","")))
        category=clean_text(str(mapping.get("semantic_type","")))
        low_source=source.lower()
        normalized_output=re.sub(r"[^a-z0-9]+"," ",output.lower()).strip()

        # Never auto-admit raw presentation/selector names as final columns.
        raw_names={
            "a","p","span","div","text","field","value","content",
            "span nth 1","span nth 2","span nth 3","a nth 1","a nth 2",
            "h1","h2","h3","h4","small","strong","label"
        }
        if normalized_output in raw_names:
            return False
        if self._is_generic_structural_source(source):
            return False
        if self._is_value_as_column_name(source,output,sample,page_title=""):
            return False

        # Page 1: semantic listing schema only. Keep the user's full catalog intact.
        if int(level)<=1:
            confidence=clean_text(str(mapping.get("confidence","Medium"))) or "Medium"
            samples=[sample] if sample else []
            return self._listing_schema_admission(source,category,confidence,samples)

        info=self.builder_levels.get(int(level),{}) or {}
        profile=(info.get("field_profiles") or {}).get(source,{})
        detail_url=clean_text(str(info.get("url","")))
        page_title=clean_text(str(info.get("title","")))
        listing_context=dict(getattr(self,"builder_detail_parent_record",{}) or {})

        # Metadata is useful only when it identifies the current record; raw
        # metadata keys should not become duplicate final fields.
        if low_source.startswith(("meta:og:url","og:url","meta:twitter:url")):
            return category=="Link / Detail" and bool(detail_url) and (
                sample.rstrip("/")==detail_url.rstrip("/")
            )

        membership=self._detail_entity_membership_score(
            category,source,sample,profile,
            detail_url=detail_url,page_title=page_title,
            listing_context=listing_context
        )
        ownership,_score=self._field_ownership_class(
            category,source,sample,profile,
            detail_url=detail_url,page_title=page_title
        )
        identity=self._semantic_identity_strength(category,source,sample)
        noise=self._detail_entity_noise_score(
            category,source,sample,profile,
            page_title=page_title,listing_context=listing_context
        )

        # Final admission is intentionally stricter than candidate discovery.
        # Strong record semantics may pass with a lower membership threshold;
        # arbitrary Text/List fields require unusually strong independent proof.
        core={
            "Name / Title","Email","Phone","Location","Company","Website",
            "Social Link","Biography / Description","Identifier","Date / Time",
            "Price / Amount","Image / Media","Document","Link / Detail"
        }
        if noise>=70 or ownership=="SHARED_CONTEXT":
            return False
        if category in {"Text","List / Group",""}:
            label=self._candidate_source_label(source)
            if not label:
                return False
            if self._is_value_as_column_name(source,output,sample,page_title=page_title):
                return False
            return membership>=72 and identity>=70 and ownership=="RECORD"
        if category in core:
            return membership>=34 and identity>=30 and ownership=="RECORD"
        return membership>=58 and identity>=55 and ownership=="RECORD"

    def _apply_final_auto_schema_gate(self):
        """Disable only automatic mappings that fail the unified final gate."""
        changed=[]
        for m in self.builder_mappings:
            if m.get("enabled",True) and not self._final_auto_schema_gate(1,m):
                m["enabled"]=False
                m["final_gate_rejected"]=True
                changed.append((1,m.get("output") or m.get("source")))
        for m in self.builder_detail_mappings:
            level=int(m.get("level",2) or 2)
            if m.get("enabled",True) and not self._final_auto_schema_gate(level,m):
                m["enabled"]=False
                m["final_gate_rejected"]=True
                changed.append((level,m.get("output") or m.get("source")))
        return changed

    def _converge_final_schema_state(self):
        """RECON023: preview/export use the same final automatic mapping state as the schema."""
        self._preserve_structural_authority_in_final_schema()
        self._normalize_auto_schema_mappings()
        self._suppress_equivalent_auto_relationship_urls()
        self._apply_final_auto_schema_gate()
        self._builder_suppress_same_level_duplicates(1)
        for level in sorted(k for k in self.builder_levels if int(k)>1):
            self._builder_suppress_same_level_duplicates(level)


    def _builder_update_live_summary(self,prefix="public"):
        self._preserve_structural_authority_in_final_schema()
        self._normalize_auto_schema_mappings()
        self._suppress_equivalent_auto_relationship_urls()
        self._apply_final_auto_schema_gate()
        self._builder_suppress_same_level_duplicates(1)
        for _level in sorted(k for k in self.builder_levels if int(k)>1):
            self._builder_suppress_same_level_duplicates(_level)
        widget=getattr(self,f"{prefix}_builder_summary_text",None)
        if widget is None:return
        lines=[]; total=0
        listing=[m for m in self.builder_mappings if m.get("enabled",True)]
        lines.append("FINAL OUTPUT SCHEMA")
        lines.append("="*28)
        lines.append("Page 1 — Listing")
        if listing:
            for m in listing:
                lines.append(f"  ✓ {m.get('output') or m.get('source')}")
                total+=1
        else: lines.append("  (no fields selected)")
        for level in sorted(k for k in self.builder_levels if k>1):
            lines.append("")
            lines.append(self._builder_level_title(level))
            selected=[m for m in self.builder_detail_mappings if m.get("enabled",True) and int(m.get("level",2))==int(level)]
            if selected:
                for m in selected:
                    lines.append(f"  ✓ {m.get('output') or m.get('source')}")
                    total+=1
            else: lines.append("  (no fields selected)")
        lines.append("")
        lines.append("-"*28)
        # Surface any legacy/profile configuration conflict instead of silently
        # allowing two checked fields to overwrite the same CSV column.
        outputs = []
        for m in [x for x in self.builder_mappings if x.get("enabled",True)] + [x for x in self.builder_detail_mappings if x.get("enabled",True)]:
            name = clean_text(str(m.get("output") or m.get("source") or ""))
            if name: outputs.append(name)
        duplicates = sorted({name for name in outputs if sum(1 for x in outputs if x.lower()==name.lower()) > 1})
        lines.append(f"Final output columns: {total}")
        if duplicates:
            lines.append("⚠ Duplicate output names: " + ", ".join(duplicates))
            lines.append("  Re-select one candidate or rename it before export.")
        if self.builder_levels:
            lines.append(f"Page levels configured: {len(self.builder_levels)}")
        widget.configure(state="normal"); widget.delete("1.0","end"); widget.insert("1.0","\n".join(lines)); widget.configure(state="disabled")

    def builder_remove_mapping(self, prefix="public"):
        tree = getattr(self, f"{prefix}_builder_mapping_tree")
        selected = sorted((int(x) for x in tree.selection()), reverse=True)
        for idx in selected:
            if 0 <= idx < len(self.builder_mappings):
                self.builder_mappings.pop(idx)
        self._builder_refresh_mapping_tree(prefix)

    def builder_clear_mappings(self, prefix="public"):
        self.builder_mappings = []
        self._builder_refresh_mapping_tree(prefix)
        self.configure_tree(getattr(self, f"{prefix}_builder_preview"), ["No preview"])

    @staticmethod
    def _split_person_name(value):
        value = clean_text(value)
        # Preserve the source value but remove common trailing credentials.
        value = re.sub(r",\s*(?:[A-Z&.]{2,}(?:\s*,\s*)?)+\s*$", "", value).strip(" ,")
        parts = value.split()
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return " ".join(parts[:-1]), parts[-1]

    @staticmethod
    def _split_city_state(value):
        value = clean_text(value)
        if "," in value:
            city, state = value.rsplit(",", 1)
            return city.strip(), state.strip()
        match = re.match(r"^(.*?)(?:\s+)([A-Z]{2})$", value)
        if match:
            return match.group(1).strip(), match.group(2)
        return value, ""

    def _builder_transform_row(self, record):
        output = {}
        for mapping in self.builder_mappings:
            if not mapping.get("enabled", True):
                continue
            value = record.get(mapping["source"], "")
            transform = mapping["transform"]
            target = mapping["output"] or mapping["source"].replace("_", " ").title()
            if transform == "Person Name Split":
                first, last = self._split_person_name(value)
                output["First Name"] = first
                output["Last Name"] = last
            elif transform == "City, State Split":
                city, state = self._split_city_state(value)
                output["City"] = city
                output["State"] = state
            elif transform == "Email / mailto Clean":
                output[target or "Email"] = re.sub(r"^mailto:", "", clean_text(value), flags=re.I).split("?")[0]
            else:
                output[target] = clean_text(value) if transform == "Text Clean" else value
        return output

    def _show_builder_preview_window(self, title="Extraction Preview"):
        """Show the current builder preview in a visible, scrollable window.

        RELINT001HF005 keeps the embedded Advanced Controls preview for backward
        compatibility, but Simple Mode users must not have to reveal Advanced
        Controls to see the result of Preview Final Dataset.
        """
        headers = list(self.builder_preview_headers or [])
        rows = list(self.builder_preview_rows or [])
        try:
            if hasattr(self, "_builder_preview_window") and self._builder_preview_window.winfo_exists():
                self._builder_preview_window.destroy()
        except Exception:
            pass

        result = tk.Toplevel(self)
        self._builder_preview_window = result
        result.title(f"{title} — {len(rows)} record(s)")
        result.geometry("1400x620")
        result.transient(self)
        ttk.Label(
            result,
            text=f"Preview ready — {len(rows)} record(s), {len(headers)} output field(s).",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 4))
        ttk.Label(
            result,
            text="This preview uses the dataset currently loaded in the Extraction Workspace and the fields currently marked for output.",
        ).pack(anchor="w", padx=10, pady=(0, 8))

        frame = ttk.Frame(result)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        preview = ttk.Treeview(frame, columns=headers or ["No fields"], show="headings")
        for h in headers or ["No fields"]:
            preview.heading(h, text=h)
            preview.column(h, width=180, minwidth=90, stretch=True)
        y = ttk.Scrollbar(frame, orient="vertical", command=preview.yview)
        x = ttk.Scrollbar(frame, orient="horizontal", command=preview.xview)
        preview.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        preview.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for row in rows:
            preview.insert("", "end", values=row)
        ttk.Button(result, text="Close", command=result.destroy).pack(side="right", padx=10, pady=(0, 10))

    def builder_preview(self, prefix="public", dataset_index=None, show_window=True):
        choice = getattr(self, f"{prefix}_builder_dataset")
        index = getattr(self,"builder_loaded_dataset_index",choice.current()) if dataset_index is None else dataset_index
        if not (0 <= index < len(self.detected_groups)):
            messagebox.showinfo("No Dataset", "Select a detected dataset.")
            return
        if not any(m.get("enabled",True) for m in self.builder_mappings):
            messagebox.showinfo("No Selected Columns", "Keep at least one output column before previewing.")
            return
        transformed = [self._builder_transform_row(r) for r in self.detected_groups[index]["records"]]
        headers = []
        for row in transformed:
            for key in row:
                if key not in headers:
                    headers.append(key)
        self.builder_preview_headers = headers
        self.builder_preview_rows = [[row.get(h, "") for h in headers] for row in transformed]
        tree = getattr(self, f"{prefix}_builder_preview")
        self.configure_tree(tree, headers or ["No fields"])
        self.clear_tree(tree)
        for row in self.builder_preview_rows:
            tree.insert("", "end", values=row)
        if show_window:
            self._show_builder_preview_window("Final Dataset Preview")
            self.status_var.set(f"Extraction preview ready — {len(transformed)} record(s), {len(headers)} output field(s).")
        else:
            self.status_var.set(f"Extraction prepared — {len(transformed)} record(s), {len(headers)} output field(s).")

    def builder_export(self, prefix="public"):
        self._converge_final_schema_state()
        resource_level=self._builder_has_resource_dataset()
        if resource_level:
            rows=self._builder_resource_output_rows(resource_level)
            if not rows:
                messagebox.showinfo("No Resource Fields","Select at least one field from the structured resource before export."); return
            headers=[]
            for row in rows:
                for key in row:
                    if key not in headers: headers.append(key)
            csv_rows=[[row.get(h,"") for h in headers] for row in rows]
            self.builder_preview_headers=headers; self.builder_preview_rows=csv_rows
            tree=getattr(self,f"{prefix}_builder_preview"); self.configure_tree(tree,headers or ["No fields"]); self.clear_tree(tree)
            for csv_row in csv_rows: tree.insert("","end",values=csv_row)
            self.status_var.set(f"Structured resource extraction complete — {len(rows)} record(s), {len(headers)} output column(s).")
            self.save_csv(headers,csv_rows,"structured_resource_extraction_export.csv")
            return
        """Export exactly the fields the user kept in the Extraction Builder.

        v1.4.11 exported only the Level-1 transformed records even when the user
        had already selected Level-2 detail mappings.  That made the guided
        preview look correct while the exported CSV silently dropped the detail
        fields.  When active Level-2 mappings exist, export now runs the same
        proven listing -> detail -> merge pipeline across the selected dataset.
        """
        choice = getattr(self, f"{prefix}_builder_dataset")
        index = getattr(self,"builder_loaded_dataset_index",choice.current())
        if not (0 <= index < len(self.detected_groups)):
            messagebox.showinfo("No Dataset", "Select a detected dataset before exporting.")
            return
        if not any(m.get("enabled", True) for m in self.builder_mappings):
            messagebox.showinfo("No Selected Columns", "Keep at least one listing output column before exporting.")
            return

        active_detail = [m for m in self.builder_detail_mappings if m.get("enabled", True)]

        # RELTRAV002: the Recommended workflow must remain export-safe.
        for lvl in sorted({int(m.get("level",2)) for m in active_detail}):
            self._resolve_cross_level_output_collisions(lvl)
        self._builder_refresh_detail_mapping_tree(prefix)
        active_detail = [m for m in self.builder_detail_mappings if m.get("enabled", True)]

        selected_outputs = []
        for m in [x for x in self.builder_mappings if x.get("enabled",True)] + active_detail:
            name = clean_text(str(m.get("output") or m.get("source") or ""))
            if name: selected_outputs.append(name)
        duplicate_outputs = sorted({name for name in selected_outputs if sum(1 for x in selected_outputs if x.lower()==name.lower()) > 1})
        if duplicate_outputs:
            messagebox.showwarning(
                "Duplicate Output Columns",
                "Two or more selected fields use the same final column name:\n\n" +
                "\n".join(f"• {name}" for name in duplicate_outputs) +
                "\n\nRename or exclude one of the duplicates before Run Extraction so no value is silently overwritten."
            )
            return

        if not active_detail:
            # RELINT001HF006: listing-only/single-level export reuses the exact
            # preview transformation without opening the visible Preview window.
            # The dedicated Preview Final Dataset button remains the explicit
            # user action for viewing the dataset before export.
            self.builder_preview(prefix, dataset_index=index, show_window=False)
            if self.builder_preview_headers and self.builder_preview_rows:
                self.save_csv(self.builder_preview_headers, self.builder_preview_rows, "visual_extraction_export.csv")
            return

        records = list(self.detected_groups[index].get("records", []))
        link_field = self._builder_resolve_page1_relationship_field(prefix,records)
        if not link_field:
            messagebox.showinfo(
                "Choose Page 2 Relationship",
                "The configured Page 1 relationship does not resolve to a valid URL in the current dataset.\n\n"
                "Return to Page 1, select the intended Link / Detail, and use Follow Selected as Next Page."
            )
            return

        if not records:
            messagebox.showinfo("No Records", "The selected dataset has no records to export.")
            return

        self.builder_detail_link_field = link_field
        self._start_builder_multilevel_export(prefix, records, link_field, active_detail)

    def _start_builder_multilevel_export(self, prefix, records, link_field, active_detail):
        win = tk.Toplevel(self)
        win.title("Exporting Selected Multi-Level Data")
        win.geometry("660x205")
        win.transient(self)
        win.resizable(False, False)
        ttk.Label(
            win,
            text="Building the final CSV from the fields you selected",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", padx=16, pady=(16, 6))
        progress_text = tk.StringVar(value=f"Preparing browser for {len(records)} record(s)...")
        ttk.Label(win, textvariable=progress_text, wraplength=620).pack(fill="x", padx=16, pady=(0, 8))
        bar = ttk.Progressbar(win, mode="determinate", maximum=max(1, len(records)), value=0)
        bar.pack(fill="x", padx=16, pady=(0, 8))
        detail_text = tk.StringVar(value="Selected fields from every configured page tab will be merged into one output row per listing record.")
        ttk.Label(win, textvariable=detail_text, wraplength=620).pack(fill="x", padx=16)
        cancel_event = threading.Event()
        ttk.Button(win, text="Cancel after current record", command=cancel_event.set).pack(side="right", padx=16, pady=12)

        self._builder_export_window = win
        self._builder_export_progress_text = progress_text
        self._builder_export_progress_bar = bar
        self._builder_export_detail_text = detail_text
        self._builder_export_cancel_event = cancel_event
        self.status_var.set(f"Multi-level CSV export started — 0/{len(records)} records.")
        threading.Thread(
            target=self._builder_multilevel_export_worker,
            args=(prefix, records, link_field, active_detail),
            daemon=True
        ).start()

    def _builder_export_progress(self, index, total, url, message=""):
        if hasattr(self, "_builder_export_progress_text"):
            self._builder_export_progress_text.set(f"Record {index}/{total}: {url}")
        if hasattr(self, "_builder_export_progress_bar"):
            self._builder_export_progress_bar["value"] = max(0, index - 1)
        if hasattr(self, "_builder_export_detail_text"):
            self._builder_export_detail_text.set(message or "Following the configured page relationships and extracting the checked fields...")
        self.status_var.set(f"Multi-level CSV export — record {index}/{total}.")

    def _builder_multilevel_export_worker(self, prefix, records, link_field, active_detail):
        output = []; failures = []; total = len(records)
        mappings_by_level = self._builder_mappings_by_level(active_detail)
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(user_agent=USER_AGENT, viewport={"width":1600,"height":1000})
                for index, record in enumerate(records, 1):
                    cancel_event = getattr(self, "_builder_export_cancel_event", None)
                    if cancel_event is not None and cancel_event.is_set(): break
                    url = self._builder_record_relationship_url(record,link_field)
                    self.after(0, lambda i=index,t=total,u=url:self._builder_export_progress(i,t,u))
                    try:
                        row,status = self._builder_extract_record_across_levels(page,record,link_field,mappings_by_level,timeout_ms=35000)
                        if "invalid/empty" in status.lower(): failures.append((index,url,status))
                    except Exception as exc:
                        row = self._builder_transform_row(record)
                        for mappings in mappings_by_level.values():
                            for mapping in mappings: row.setdefault(mapping.get("output","Detail Field"),"")
                        failures.append((index,url,f"{type(exc).__name__}: {str(exc)[:180]}"))
                    output.append(row)
                    self.after(0, lambda i=index:self._builder_export_progress_bar.configure(value=i) if hasattr(self,"_builder_export_progress_bar") else None)
                browser.close()
            self.after(0, lambda:self._complete_builder_multilevel_export(prefix, output, active_detail, failures, total))
        except Exception as exc:
            self.after(0, lambda e=str(exc):self._builder_multilevel_export_failed(e))

    def _complete_builder_multilevel_export(self, prefix, rows, active_detail, failures, requested_total):
        try:
            if hasattr(self, "_builder_export_window") and self._builder_export_window.winfo_exists():
                self._builder_export_window.destroy()
        except Exception:
            pass
        if not rows:
            messagebox.showinfo("Export Cancelled", "No rows were completed, so no CSV was created.")
            return

        # Build the final schema from enabled listing mappings + enabled detail
        # mappings only. Internal relationship/status fields are not forced into
        # the customer's export unless the user explicitly selected them.
        headers = []
        for row in rows:
            for key in row:
                if key not in headers:
                    headers.append(key)
        csv_rows = [[row.get(h, "") for h in headers] for row in rows]
        self.builder_preview_headers = headers
        self.builder_preview_rows = csv_rows
        tree = getattr(self, f"{prefix}_builder_preview")
        self.configure_tree(tree, headers or ["No fields"]); self.clear_tree(tree)
        for csv_row in csv_rows:
            tree.insert("", "end", values=csv_row)

        self.status_var.set(
            f"Multi-level extraction complete — {len(rows)}/{requested_total} records; "
            f"{len(active_detail)} selected later-page field(s) merged across {len(self.builder_levels)} page level(s); {len(failures)} issue(s)."
        )
        if failures:
            sample = "\n".join(f"Record {i}: {reason}" for i, _url, reason in failures[:5])
            messagebox.showwarning(
                "Multi-Level Export Completed with Issues",
                f"Completed {len(rows)} record(s). {len(failures)} record(s) had a detail-page issue.\n\n"
                f"The listing row was preserved and unavailable detail values were left blank.\n\n{sample}"
            )
        self.save_csv(headers, csv_rows, "visual_multilevel_extraction_export.csv")

    def _builder_multilevel_export_failed(self, message):
        try:
            if hasattr(self, "_builder_export_window") and self._builder_export_window.winfo_exists():
                self._builder_export_window.destroy()
        except Exception:
            pass
        self._show_error("Multi-Level Export Failed", message)

    def builder_save_profile(self, prefix="public"):
        choice = getattr(self, f"{prefix}_builder_dataset")
        index = choice.current()
        if not (0 <= index < len(self.detected_groups)):
            messagebox.showinfo("Nothing to Save", "Fetch a page and select a detected repeating dataset first.")
            return
        if not any(m.get("enabled",True) for m in self.builder_mappings):
            messagebox.showinfo("No Fields Selected", "Keep at least one output field before saving the profile.")
            return

        proposed = clean_text(self.project_name.get()) if hasattr(self, "project_name") else ""
        proposed = proposed or "Custom Extraction Profile"
        profile_name = simpledialog.askstring("Save Extraction Profile", "Profile name:", initialvalue=proposed, parent=self)
        profile_name = clean_text(profile_name or "")
        if not profile_name:
            return

        dataset = self.detected_groups[index]
        payload = {
            "product": "RAM-Tech Universal Data Extraction Studio",
            "profile_name": profile_name,
            "version": "1.5.2",
            "profile_type": "visual_extraction",
            "source_url": self.public_url.get().strip() if prefix == "public" else self.protected_url.get().strip(),
            "dataset_name": dataset["name"],
            "detected_record_count": dataset["count"],
            "source_fields": dataset["fields"],
            "mappings": [m for m in self.builder_mappings if m.get("enabled",True)],
            "detail_link_field": self.builder_detail_link_field or getattr(self, f"{prefix}_builder_detail_url_field").get().strip(),
            "detail_mappings": [m for m in self.builder_detail_mappings if m.get("enabled",True)],
            "created_at": datetime.now().isoformat(timespec="seconds")
        }
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", profile_name).strip("_") or "custom_profile"
        path = self.builder_profiles_dir / f"{safe}.json"
        self.builder_profiles_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        self.refresh_custom_profiles()
        self.multilevel_profile.set(profile_name)
        self.multilevel_url.set(payload["source_url"])
        self.detail_link_pattern.set("")
        self._on_multilevel_profile_changed()
        messagebox.showinfo("Profile Saved", f"Profile '{profile_name}' is now available in Multi-Level Crawl.\n\nPersistent location:\n{path}")

    def show_detected(self, choice, tree):
        index = choice.current()
        if not (0 <= index < len(self.detected_groups)):
            return
        item = self.detected_groups[index]
        self.configure_tree(tree, item["fields"])
        for row in item["records"]:
            tree.insert("", "end", values=[row.get(field, "") for field in item["fields"]])

    def show_table(self, choice, tree):
        index = choice.current()
        if not (0 <= index < len(self.table_data)):
            return
        headers, rows = self.table_data[index]
        self.configure_tree(tree, headers)
        for row in rows:
            tree.insert("", "end", values=row)

    def export_detected(self, choice):
        index = choice.current()
        if not (0 <= index < len(self.detected_groups)):
            messagebox.showinfo("No Dataset", "No detected dataset is selected.")
            return
        item = self.detected_groups[index]
        rows = [[record.get(field, "") for field in item["fields"]] for record in item["records"]]
        self.save_csv(item["fields"], rows, f"detected_dataset_{index+1}.csv")

    def export_table(self, choice):
        index = choice.current()
        if not (0 <= index < len(self.table_data)):
            messagebox.showinfo("No Table", "No table is selected.")
            return
        headers, rows = self.table_data[index]
        self.save_csv(headers, rows, f"html_table_{index+1}.csv")

    def export_links(self):
        if not self.links:
            messagebox.showinfo("No Links", "No links are available.")
            return
        self.save_csv(["Link Text", "URL"], self.links, "page_links.csv")

    def export_api(self):
        if not self.api_rows:
            messagebox.showinfo("No API Data", "No API records are available.")
            return
        self.save_csv(self.api_headers, self.api_rows, "api_records.csv")

    def save_csv(self, headers, rows, default_name):
        # Always open the Save dialog in the application's dedicated CSV folder.
        # The user may still choose another location when needed.
        csv_dir = Path(__file__).resolve().parent / "Output" / "CSV"
        csv_dir.mkdir(parents=True, exist_ok=True)

        source_name = Path(default_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        timestamped_name = f"{source_name.stem}_{timestamp}{source_name.suffix or '.csv'}"

        path = filedialog.asksaveasfilename(
            title="Export CSV",
            defaultextension=".csv",
            initialdir=str(csv_dir),
            initialfile=timestamped_name,
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(headers)
                writer.writerows(rows)
            messagebox.showinfo(
                "Export Complete",
                f"CSV exported successfully.\n\nRecords: {len(rows)}\nSaved to:\n{path}"
            )
        except Exception as exc:
            self._show_error("Export Failed", str(exc))

    @staticmethod
    def valid_url(url):
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def _show_error(self, title, message):
        self.status_var.set(title)
        messagebox.showerror(title, message)


if __name__ == "__main__":
    ScraperApp().mainloop()
