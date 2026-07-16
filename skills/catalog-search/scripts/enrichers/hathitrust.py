"""HathiTrust enricher — content analysis of books *including in-copyright ones*.

The headline value: HathiTrust Research Center "Extracted Features" (EF) publish
the per-page *statistics* of a book — every token with its part-of-speech and
count — but NOT the readable, in-order text. Because that's non-consumptive
data, it's available even for IN-COPYRIGHT volumes. So where the Internet
Archive action only reaches public-domain titles, this can tell you what a
brand-new in-copyright monograph is *about* (its dominant vocabulary, the people
and places it names) without anyone being able to read it.

Two stages, split by cost AND by dependency:
  - find_htids() / probe() : a keyless HathiTrust Bib-API join (catalog ids ->
        HathiTrust volume id + rights). Pure standard library, so the optional
        inline "content analysis available" badge runs under plain python3.
  - analyze() : fetch EF and compute the content fingerprint. Needs the project
        venv (the `htrc-feature-reader` dependency), imported lazily so this
        module still imports fine under stdlib python3 for the badge path.

Verified 2026-06-24: Bib API returns htid + rights; EF fetch + POS-filtered
content terms work (e.g. a Tom Sawyer volume -> tom, jim, river, ...).
"""

from __future__ import annotations

import lib_common as lc

NAME = "hathitrust"
TIER = "action"

_BIB_API = "https://catalog.hathitrust.org/api/volumes/brief/json"

# Rights codes that mean "full view" (public domain); everything else is some
# flavour of limited/in-copyright. EF content analysis works regardless.
_FULL_VIEW_RIGHTS = {
    "pd",
    "pdus",
    "world",
    "cc-by",
    "cc-by-nd",
    "cc-zero",
    "cc-by-nc",
    "cc-by-nc-nd",
    "cc-by-sa",
    "cc-by-nc-sa",
}


def _id_list(record):
    """Build a HathiTrust Bib-API record list from a normalized record's join
    keys: oclc + isbns. More ids = higher match odds (match rate per single id
    is low)."""
    ids = record.get("identifiers") or {}
    parts = []
    for oclc in ids.get("oclc") or []:
        parts.append(f"oclc:{oclc}")
    for isbn in ids.get("isbn") or []:
        parts.append(f"isbn:{isbn}")
    # de-dup, cap length to keep the URL sane
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:10]


def find_htids(record, cfg):
    """Return [{htid, rights, fullView}] for HathiTrust volumes matching this
    record, best (full-view) first. Empty if no match. Fail-soft."""
    id_list = _id_list(record)
    if not id_list:
        return []
    query = "|".join(id_list)
    data = lc.get_json_soft(
        f"{_BIB_API}/{lc_quote(query)}", timeout=cfg["http_timeout"], retries=1
    )
    if not isinstance(data, dict):
        return []
    found, seen = [], set()
    for entry in data.values():
        for item in entry.get("items") or []:
            htid = item.get("htid")
            if not htid or htid in seen:
                continue
            seen.add(htid)
            rights = (item.get("rightsCode") or item.get("rights") or "").lower()
            found.append(
                {
                    "htid": htid,
                    "rights": rights,
                    "rightsString": item.get("usRightsString"),
                    "fullView": rights in _FULL_VIEW_RIGHTS,
                }
            )
    found.sort(key=lambda x: not x["fullView"])  # full-view first
    return found


def probe(record, cfg):
    """Optional inline badge (opt-in via the search --htrc flag): advertise that
    EF content analysis is available for this record. Cheap-ish: one Bib-API
    call. No EF fetch here."""
    htids = find_htids(record, cfg)
    if not htids:
        return None
    best = htids[0]
    badge = {
        "contentAnalysis": True,
        "source": "HathiTrust / HTRC Extracted Features",
        "htid": best["htid"],
        "rights": best["rights"],
        "rightsNote": best.get("rightsString"),
        # HathiTrust reading page: full-view items are readable here, limited ones
        # are at least searchable / borrowable.
        "readUrl": f"https://babel.hathitrust.org/cgi/pt?id={best['htid']}",
        "note": (
            "full view"
            if best["fullView"]
            else "analyzable even though not readable (features only)"
        ),
    }
    action = {
        "id": "content_analysis",
        "enricher": NAME,
        "label": "Analyze what this book is about (HTRC Extracted Features)",
        "params": {"htid": best["htid"]},
    }
    return {"annotations": {NAME: badge}, "actions": [action]}


# -------------------------------------------------------------------------
# Action tier — needs the venv (htrc-feature-reader), imported lazily.
# -------------------------------------------------------------------------

_STOP = set("""the and a an to of it is was were be been being have has had do does did
in on at by for with from up out down into over under again about after before of as
this that these those there here i you he she they we me him her them us my your his
its our their not no nor so than then too very can will would could should may might
must just only also said one two three said who whom which what when where why how all
any both each few more most other some such own same""".split())

_COMMON_NOUNS = {"NN", "NNS"}
_PROPER_NOUNS = {"NNP", "NNPS"}


def _content_terms(vol, pos_set, top, drop_stop=True):
    tl = vol.tokenlist(pages=False, pos=True, case=False, section="body")
    df = tl.reset_index()
    tokcol = (
        "lowercase"
        if "lowercase" in df.columns
        else "token" if "token" in df.columns else df.columns[0]
    )
    df = df[df["pos"].isin(pos_set)]
    df = df[df[tokcol].str.isalpha() & (df[tokcol].str.len() >= 3)]
    if drop_stop:
        df = df[~df[tokcol].isin(_STOP)]
    agg = df.groupby(tokcol)["count"].sum().sort_values(ascending=False)
    return [{"term": t, "count": int(c)} for t, c in agg.head(top).items()]


def analyze(htid, cfg, top_terms=20, top_names=15):
    """Fetch EF for a HathiTrust volume and compute a content fingerprint:
    dominant common-noun vocabulary (themes) and proper nouns (people/places).
    Returns a report dict. Raises on hard failure (no EF available)."""
    try:
        from htrc_features import Volume
    except ImportError as exc:
        raise RuntimeError(
            "HTRC content analysis needs the project venv. One-time setup:\n"
            "  python3 -m venv .venv && .venv/bin/pip install .\n"
            "then run this with .venv/bin/python."
        ) from exc

    vol = Volume(htid)  # downloads EF on demand
    return {
        "htid": htid,
        "title": vol.title,
        "year": getattr(vol, "year", None),
        "pageCount": vol.page_count,
        "readUrl": f"https://babel.hathitrust.org/cgi/pt?id={htid}",  # HathiTrust page
        "topThemes": _content_terms(vol, _COMMON_NOUNS, top_terms),
        "topNames": _content_terms(vol, _PROPER_NOUNS, top_names, drop_stop=False),
    }


def act(record, cfg, htid=None, **_params):
    if not htid:
        htids = find_htids(record, cfg)
        if htids:
            htid = htids[0]["htid"]
    if not htid:
        raise RuntimeError("no HathiTrust volume found for this record")
    return analyze(htid, cfg)


def lc_quote(s):
    import urllib.parse

    return urllib.parse.quote(s, safe=":|")
