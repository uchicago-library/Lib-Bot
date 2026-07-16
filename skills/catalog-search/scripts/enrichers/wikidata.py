"""WikiData inline enricher — author context.

Cheap probe: take a record's primary author, reconcile to a WikiData item, and
attach a compact fact the catalog itself doesn't surface — who the author is
(one-line description) plus a few notable works ("also wrote...").

This is genuine enrichment: the catalog has the author's *name*; WikiData adds
*context*. Fail-soft: any miss returns None and the badge simply doesn't appear.

Verified path (2026-06-24): wbsearchentities for reconciliation, wbgetentities
for claims/labels (Wikibase Action API). Authority-ID reconciliation (VIAF) is
the production upgrade; name search is the phase-0 fallback.
"""

from __future__ import annotations

import re
import threading

import lib_common as lc

NAME = "wikidata"
TIER = "inline"

_API = "https://www.wikidata.org/w/api.php"
_Q_HUMAN = "Q5"

# Session cache: many records share an author; reconcile each name once.
_cache = {}
_lock = threading.Lock()

_DATES_RE = re.compile(r",?\s*\d{3,4}\s*-\s*(?:\d{3,4})?\.?")
_PARENS_RE = re.compile(r"\([^)]*\)")
_WS_RE = re.compile(r"\s+")


def clean_author_name(name):
    """'Tolkien, J. R. R. (John Ronald Reuel), 1892-1973' -> 'J. R. R. Tolkien'."""
    if not name:
        return ""
    name = _DATES_RE.sub("", name)
    name = _PARENS_RE.sub("", name)
    name = name.strip().strip(",").strip()
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
        if first:
            name = f"{first} {last}"
    return _WS_RE.sub(" ", name).strip()


_PERSON_WORDS = (
    "author",
    "writer",
    "poet",
    "novelist",
    "historian",
    "scholar",
    "philosopher",
    "professor",
    "journalist",
    "philologist",
    "playwright",
    "scientist",
    "academic",
    "editor",
    "translator",
    "essayist",
    "critic",
    "sociologist",
    "economist",
    "linguist",
)


def _sig_tokens(s):
    """Lowercased word tokens >= 3 chars, excluding pure numbers."""
    return {
        t
        for t in re.split(r"[^\w]+", (s or "").lower())
        if len(t) >= 3 and not t.isdigit()
    }


def _name_matches(cleaned, original, label):
    """Guard against wrong-person matches before trusting a WikiData hit.

    Library catalogs invert *personal* names with a comma ("Last, First") but
    not corporate names. For a personal name we require BOTH the surname and a
    first name to appear in the matched label, which rejects same-surname
    impostors ('Marsh, Nicholas' -> 'Nick Marsh, DJ') and different-surname ones
    ('Marsh, Nicholas' -> 'Justin Marshall'). For corporate/mononym names we
    fall back to significant-token overlap. Erring toward *no* annotation is
    deliberate: a wrong fact is worse than a missing one."""
    if not label:
        return False
    label_tokens = _sig_tokens(label)
    if not label_tokens:
        return False
    if "," in original:
        # Personal name. Use the normalized "First Last" form for tokens.
        toks = [
            t
            for t in re.split(r"[^\w]+", cleaned.lower())
            if len(t) >= 3 and not t.isdigit()
        ]
        if not toks:
            return False
        if toks[-1] not in label_tokens:  # surname must match
            return False
        firsts = set(toks[:-1])
        return (not firsts) or bool(firsts & label_tokens)  # and a first name
    return bool(_sig_tokens(cleaned) & label_tokens)  # corporate/mononym


def _search_entity(name, original, cfg):
    """Return (qid, label, description) for the best item match whose label
    actually matches `original`'s name, or None. Prefers person-like hits."""
    data = lc.get_json_soft(
        _API,
        params={
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "uselang": "en",
            "format": "json",
            "type": "item",
            "limit": 5,
        },
        timeout=cfg["http_timeout"],
    )
    if not data:
        return None
    # Only consider hits whose label genuinely matches the queried name.
    hits = [
        h
        for h in (data.get("search") or [])
        if _name_matches(
            name, original, h.get("label") or h.get("match", {}).get("text", "")
        )
    ]
    if not hits:
        return None
    for h in hits:  # prefer a hit that reads like a person
        desc = (h.get("description") or "").lower()
        if any(w in desc for w in _PERSON_WORDS):
            return h.get("id"), h.get("label"), h.get("description")
    h = hits[0]
    return h.get("id"), h.get("label"), h.get("description")


def _claim_ids(claims, prop, limit):
    out = []
    for stmt in (claims.get(prop) or [])[: limit * 2]:
        try:
            qid = stmt["mainsnak"]["datavalue"]["value"]["id"]
        except (KeyError, TypeError):
            continue
        if qid not in out:
            out.append(qid)
        if len(out) >= limit:
            break
    return out


def _resolve_labels(ids, cfg):
    if not ids:
        return {}
    data = lc.get_json_soft(
        _API,
        params={
            "action": "wbgetentities",
            "ids": "|".join(ids),
            "props": "labels",
            "languages": "en",
            "format": "json",
        },
        timeout=cfg["http_timeout"],
    )
    if not data:
        return {}
    out = {}
    for qid, ent in (data.get("entities") or {}).items():
        label = lc.dig(ent, "labels", "en", "value")
        if label:
            out[qid] = label
    return out


def _build(qid, label, description, cfg):
    """Fetch claims and assemble the annotation dict."""
    fact = {
        "qid": qid,
        "label": label,
        "summary": description,
        "url": f"https://www.wikidata.org/wiki/{qid}",
    }
    data = lc.get_json_soft(
        _API,
        params={
            "action": "wbgetentities",
            "ids": qid,
            "props": "claims",
            "format": "json",
        },
        timeout=cfg["http_timeout"],
    )
    claims = lc.dig(data, "entities", qid, "claims") or {}

    work_ids = _claim_ids(claims, "P800", 5)  # notable works
    occ_ids = _claim_ids(claims, "P106", 3)  # occupation
    viaf = None
    for stmt in (claims.get("P214") or [])[:1]:  # VIAF id
        viaf = lc.dig(stmt, "mainsnak", "datavalue", "value")

    labels = _resolve_labels(work_ids + occ_ids, cfg)
    works = [labels[i] for i in work_ids if i in labels]
    occs = [labels[i] for i in occ_ids if i in labels]
    if works:
        fact["notableWorks"] = works
    if occs:
        fact["occupations"] = occs
    if viaf:
        fact["viaf"] = viaf
    return fact


def _reconcile(cleaned, original, cfg):
    key = cleaned.lower()
    with _lock:
        if key in _cache:
            return _cache[key]
    found = _search_entity(cleaned, original, cfg)
    result = None
    if found and found[0]:
        result = _build(found[0], found[1], found[2], cfg)
    with _lock:
        _cache[key] = result
    return result


def probe(record, cfg):
    authors = record.get("authors") or []
    if not authors:
        return None
    original = authors[0]
    cleaned = clean_author_name(original)
    if not cleaned:
        return None
    fact = _reconcile(cleaned, original, cfg)
    if not fact:
        return None
    # Only surface WikiData when it actually adds *identifying* context. For a
    # personal name (collision-prone), require a bio summary or notable works —
    # occupations alone aren't enough to trust a same-name match. For corporate
    # names, any context (e.g. "British production company") suffices.
    summary, works, occs = (
        fact.get("summary"),
        fact.get("notableWorks"),
        fact.get("occupations"),
    )
    if "," in original:
        if not (summary or works):
            return None
    elif not (summary or works or occs):
        return None
    return {"annotations": {NAME: fact}}
