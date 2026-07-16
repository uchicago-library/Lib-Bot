"""OpenLibrary -> Internet Archive enricher (the headline action).

Two tiers, by cost:

  probe(record, cfg)  -- CHEAP. Asks OpenLibrary whether a *public-domain*
      full-text scan exists on the Internet Archive for this record. If so it
      attaches a "full text available" badge and registers a `fulltext` action.
      It pulls NO text. Honest by construction: only ebook_access == "public"
      earns a badge, so in-copyright items correctly get nothing.

  act(record, cfg, ocaid=..., out_path=...) -- EXPENSIVE, on demand only.
      Pulls the full OCR plaintext from the Internet Archive so the agent can
      read / summarize / answer questions about the actual book.

Verified path (2026-06-24): OpenLibrary Search API exposes `ebook_access` +
`ia`; archive.org/metadata/<ocaid> lists files; the `<ocaid>_djvu.txt` download
is the full OCR dump (~700 KB pulled for a public-domain Pride and Prejudice).
"""

from __future__ import annotations

import re

import lib_common as lc

NAME = "openlibrary_ia"
TIER = "action"

_OL_SEARCH = "https://openlibrary.org/search.json"
_IA_META = "https://archive.org/metadata"
_IA_DOWNLOAD = "https://archive.org/download"
_FIELDS = "key,title,ia,ebook_access,public_scan_b"


def _pick_isbn(record):
    ids = record.get("identifiers") or {}
    if ids.get("cleanIsbn"):
        return ids["cleanIsbn"]
    isbns = ids.get("isbn") or []
    return isbns[0] if isbns else None


def _clean_title(title):
    """Trim VuFind subtitle/statement-of-responsibility noise for searching."""
    if not title:
        return title
    return title.split("/")[0].split(":")[0].strip()


# Internet Archive id prefixes that are NOT readable scans. bwb_* are Better
# World Books purchase listings (no OCR text), so they must never be chosen.
_NON_SCAN_PREFIXES = ("bwb_",)
# Substrings flagging derivative datasets (audio, ML corpora) with no book OCR.
_DERIVATIVE_HINTS = ("synapseml", "librivox", "_dataset", "spectrogram", "audio")
# Classic library-digitization ids look like "title0000author" (letters, a
# 4-digit sequence, then letters) and reliably carry a *_djvu.txt OCR file.
_LIBRARY_SCAN_RE = re.compile(r"[a-z]\d{4}[a-z]")


def _real_scans(ia_list):
    return [x for x in (ia_list or []) if not x.startswith(_NON_SCAN_PREFIXES)]


def _scan_rank(ocaid):
    """Higher = more likely a readable, OCR'd library book scan."""
    if ocaid.startswith(_NON_SCAN_PREFIXES):
        return -100
    score = 0
    if _LIBRARY_SCAN_RE.search(ocaid):
        score += 10
    if any(h in ocaid for h in _DERIVATIVE_HINTS):
        score -= 50
    score -= ocaid.count("_")  # derivative ids tend to be underscore-heavy
    return score


def _ranked_scans(ia_list):
    """Real scans, best book-scan candidates first."""
    return sorted(_real_scans(ia_list), key=_scan_rank, reverse=True)


def _pick_ocaid(ia_list):
    """Choose the most promising readable scan id."""
    ranked = _ranked_scans(ia_list)
    if ranked:
        return ranked[0]
    return ia_list[0] if ia_list else None


def _first_public_scan(docs):
    """Return the first public-domain doc that has a genuinely readable scan;
    fall back to a public doc with only placeholder ids if that's all there is."""
    fallback = None
    for doc in docs or []:
        if doc.get("ebook_access") == "public" and (doc.get("ia") or []):
            if _real_scans(doc["ia"]):
                return doc
            fallback = fallback or doc
    return fallback


def _find_public_scan(record, cfg):
    """Does a public-domain full-text scan of this WORK exist on IA?

    Primary signal is a title+author search: OpenLibrary's `ebook_access` is
    work-level (best across all editions), so it surfaces an old public-domain
    scan even when the catalog record only carries a modern reprint's ISBN.
    ISBN search is a precise fallback. Returns the matching doc or None."""
    title = _clean_title(record.get("title"))
    authors = record.get("authors") or []
    if title:
        params = {"title": title, "fields": _FIELDS, "limit": 5}
        if authors:
            params["author"] = lc_author(authors[0])
        data = lc.get_json_soft(
            _OL_SEARCH, params=params, timeout=cfg["http_timeout"], retries=2
        )
        hit = _first_public_scan((data or {}).get("docs"))
        if hit:
            return hit
    isbn = _pick_isbn(record)
    if isbn:
        data = lc.get_json_soft(
            _OL_SEARCH,
            params={
                "isbn": isbn,
                "fields": _FIELDS,
                "limit": 5,
            },
            timeout=cfg["http_timeout"],
            retries=2,
        )
        hit = _first_public_scan((data or {}).get("docs"))
        if hit:
            return hit
    return None


def lc_author(name):
    """Light author cleanup for OL author= queries ('Austen, Jane' -> 'Jane Austen')."""
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
        # drop trailing life dates from the first-name part
        first = first.split("(")[0]
        first = "".join(c for c in first if not c.isdigit()).strip(" -,.")
        if first:
            return f"{first} {last}"
    return name


def probe(record, cfg):
    # Honest by construction: _find_public_scan only returns a doc when a
    # genuine public-domain full scan exists, so in-copyright items get nothing.
    doc = _find_public_scan(record, cfg)
    if not doc:
        return None
    candidates = _ranked_scans(doc.get("ia")) or (doc.get("ia") or [])
    ocaid = candidates[0] if candidates else None
    badge = {
        "fullText": True,
        "access": "public",
        "source": "Internet Archive",
        "ocaid": ocaid,
        # Human "read it here" page (offer to the user); distinct from the raw
        # OCR text file we pull from internally.
        "readUrl": f"https://archive.org/details/{ocaid}" if ocaid else None,
        "iaCandidates": candidates[:5],
        "olKey": doc.get("key"),
        "matchedTitle": doc.get("title"),
    }
    action = {
        "id": "fulltext",
        "enricher": NAME,
        "label": "Pull full text from the Internet Archive (read / summarize / Q&A)",
        "params": {"ocaid": ocaid},
    }
    return {"annotations": {NAME: badge}, "actions": [action]}


# -------------------------------------------------------------------------
# Action tier
# -------------------------------------------------------------------------

_META_TXT = ("_meta.txt", "_files.txt", "_reviews.txt")


def _find_text_file(files):
    """Choose the best full-text OCR file from an IA metadata file list.
    Order of preference: DjVuTXT format, *_djvu.txt name, then any other plain
    .txt that isn't an IA metadata sidecar."""
    by_fmt = [f for f in files if f.get("format") == "DjVuTXT"]
    if by_fmt:
        return by_fmt[0].get("name")
    for f in files:
        name = f.get("name", "")
        if name.endswith("_djvu.txt"):
            return name
    for f in files:
        name = f.get("name", "")
        if name.endswith(".txt") and not name.endswith(_META_TXT):
            return name
    return None


def fetch_fulltext(ocaid, cfg):
    """Pull the full OCR plaintext for an IA item. Returns
    {ocaid, title, source_url, chars, text} or raises on hard failure."""
    meta = lc.get_json_soft(f"{_IA_META}/{ocaid}", timeout=cfg["http_timeout"])
    if not meta:
        raise RuntimeError(f"could not read IA metadata for {ocaid}")
    files = meta.get("files") or []
    fname = _find_text_file(files)
    if not fname:
        raise RuntimeError(f"no OCR text file found for {ocaid}")
    url = f"{_IA_DOWNLOAD}/{ocaid}/{lc_urlquote(fname)}"
    text = lc.get_text(url, timeout=cfg["fulltext_timeout"])
    title = lc.dig(meta, "metadata", "title")
    return {
        "ocaid": ocaid,
        "title": title,
        "source_url": url,  # raw OCR file (pulled)
        "readUrl": f"https://archive.org/details/{ocaid}",  # human reading page
        "chars": len(text),
        "text": text,
    }


def lc_urlquote(s):
    import urllib.parse

    return urllib.parse.quote(s)


def act(record, cfg, ocaid=None, **_params):
    """On-demand: pull full text. ocaid may come from the action params; if not,
    re-derive candidates from the record via a fresh probe and try each until
    one yields OCR text (some scans lack a text file)."""
    if ocaid:
        candidates = [ocaid]
    else:
        badge = lc.dig(probe(record, cfg) or {}, "annotations", NAME) or {}
        candidates = badge.get("iaCandidates") or (
            [badge["ocaid"]] if badge.get("ocaid") else []
        )
    if not candidates:
        raise RuntimeError("no public Internet Archive full text for this record")
    last_err = None
    for oc in candidates:
        try:
            return fetch_fulltext(oc, cfg)
        except Exception as exc:  # noqa: BLE001 - try the next candidate scan
            last_err = exc
            lc.warn(f"full-text fetch failed for {oc}: {exc}")
    raise RuntimeError(f"could not pull full text (tried {candidates}): {last_err}")
