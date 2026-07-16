"""On-demand public full-text DISCOVERY across Project Gutenberg + Internet
Archive — the "go find it even though the eager badge didn't fire" path.

Why this is separate from the eager `openlibrary_ia` badge: the badge must be
high-precision (it asserts "full text is here"), so it only trusts OpenLibrary's
`public` flag. But many genuinely public-domain works are missed that way — the
catalog often holds only a later reprint whose edition ids don't line up with the
public-domain scan (the same edition-mismatch that caps the HathiTrust join). So
when a user explicitly asks for a work's full text, we search the open
full-text sources directly and return **candidates the caller must verify**,
rather than silently trusting a fuzzy match.

Verification is the whole point (see ../../DESIGN.md "honesty"):
  - **Project Gutenberg** (via the gutendex API) returns the author's birth/death
    years, so a candidate can be confirmed on surname + life dates + title — a
    high-confidence, non-fuzzy match. Its text is clean plaintext (no OCR noise).
  - **Internet Archive** scans have no life dates and often a generic/series
    title, so IA candidates are inherently lower-confidence — returned as
    "verify". Drawn ONLY from IA's vetted library/institutional digitizations;
    its open *community-upload* collections (`opensource`, `folkscanomy`), where
    anyone can post in-copyright books, are never searched.

`discover()` returns ranked candidates with a `confidence` and `matchNotes`; it
pulls NO text. The caller shows them, confirms the right one, then fetches.
"""

from __future__ import annotations

import re

import lib_common as lc

NAME = "public_fulltext"

_GUTENDEX = "https://gutendex.com/books"
_IA_SEARCH = "https://archive.org/advancedsearch.php"
_GUTENBERG_FILE = "https://www.gutenberg.org/files"
_GUTENBERG_EBOOKS = "https://www.gutenberg.org/ebooks"

_DATES_RE = re.compile(r"(\d{4})\s*-\s*(\d{4})?")
_TITLE_STOP = {"the", "and", "for", "with", "from", "that", "this"}


# --------------------------------------------------------------------------
# Name / title helpers (verification)
# --------------------------------------------------------------------------


def surname_of(author):
    """Most distinctive surname token. Library/Gutenberg names are 'Last, First'."""
    if not author:
        return ""
    base = author.split(",", 1)[0] if "," in author else author
    toks = [t for t in re.split(r"[^\w]+", base.lower()) if len(t) >= 2]
    return toks[-1] if toks else ""


def life_dates(author):
    """(birth, death) as strings from a catalog author heading, or (None, None)."""
    m = _DATES_RE.search(author or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def title_tokens(title):
    return {
        t
        for t in re.split(r"[^\w]+", (title or "").lower())
        if len(t) >= 4 and t not in _TITLE_STOP
    }


def _first_tokens(author):
    """Significant given-name tokens (drops the surname, initials, and dates)."""
    if not author:
        return set()
    if "," in author:
        first = author.split(",", 1)[1]
    else:
        first = " ".join(author.split()[:-1])
    return {
        t for t in re.split(r"[^\w]+", first.lower()) if len(t) >= 3 and not t.isdigit()
    }


def author_matches(req_author, cand_author):
    """Require the candidate's author to share the requested surname AND, when
    both expose a given name, a given-name token. Rejects 'Mason, Charlotte' vs
    'Mason, John' — the surname-only collisions that cause IA false positives."""
    rs, cs = surname_of(req_author), surname_of(cand_author)
    if not rs or rs != cs:
        return False
    rf, cf = _first_tokens(req_author), _first_tokens(cand_author)
    if rf and cf and not (rf & cf):
        return False
    return True


def title_overlap_ok(req_title, cand_title):
    """Guard against single-common-token matches ('school' alone). Needs >=2
    shared meaningful tokens, or one distinctive (>=6 char) token for very short
    requested titles."""
    req_tt, cand_tt = title_tokens(req_title), title_tokens(cand_title)
    overlap = req_tt & cand_tt
    if len(overlap) >= 2:
        return True
    if len(req_tt) <= 1 and overlap and max(len(t) for t in overlap) >= 6:
        return True
    return False


# --------------------------------------------------------------------------
# Project Gutenberg
# --------------------------------------------------------------------------


def _gutenberg_text_url(formats):
    """Best plain-text URL from a gutendex formats map (prefer utf-8, no zip)."""
    fallback = None
    for k, v in (formats or {}).items():
        if k.startswith("text/plain") and not v.endswith(".zip"):
            if "utf-8" in k:
                return v
            fallback = fallback or v
    return fallback


def search_gutenberg(req_title, req_author, cfg):
    req_surname = surname_of(req_author)
    req_dates = life_dates(req_author)
    terms = " ".join([req_surname] + sorted(title_tokens(req_title))[:3])
    data = lc.get_json_soft(
        _GUTENDEX, params={"search": terms}, timeout=cfg["http_timeout"], retries=1
    )
    if not data:
        return []
    out = []
    for b in (data.get("results") or [])[:10]:
        author = (b.get("authors") or [{}])[0]
        if not author_matches(req_author, author.get("name")):
            continue  # surname + given-name guard against the wrong author
        text_url = _gutenberg_text_url(b.get("formats"))
        if not text_url:
            continue
        cand_birth = str(author.get("birth_year")) if author.get("birth_year") else None
        title_overlap = title_overlap_ok(req_title, b.get("title"))
        dates_match = bool(req_dates[0] and cand_birth and cand_birth == req_dates[0])
        # author identity is confirmed by matching life dates OR a shared given
        # name (the surname+given-name guard already blocks 'Mason, John').
        shared_given = bool(
            _first_tokens(req_author) & _first_tokens(author.get("name"))
        )
        author_confirmed = dates_match or shared_given
        if author_confirmed and title_overlap:
            conf = "high"
            notes = (
                "author confirmed by life dates + title"
                if dates_match
                else "author name + title match"
            )
        elif title_overlap:
            conf = "medium"
            notes = "surname + title match — confirm the author"
        elif author_confirmed:
            conf = "medium"
            notes = "right author, but title differs — may be a variant/related work"
        else:
            conf = "low"
            notes = "surname match only — verify this is the right work"
        out.append(
            {
                "source": "Project Gutenberg",
                "id": b.get("id"),
                "title": b.get("title"),
                "author": author.get("name"),
                "authorDates": f"{author.get('birth_year')}-{author.get('death_year')}",
                "format": "plaintext (clean)",
                "url": f"{_GUTENBERG_EBOOKS}/{b.get('id')}",
                "textUrl": text_url,
                "confidence": conf,
                "matchNotes": notes,
            }
        )
    return out


def _strip_gutenberg_boilerplate(text):
    """Drop the PG license header/footer, leaving the work itself."""
    start = re.search(
        r"\*\*\* *START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    end = re.search(
        r"\*\*\* *END OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    s = start.end() if start else 0
    e = end.start() if end else len(text)
    body = text[s:e].strip()
    return body or text


def fetch_gutenberg_text(gid, cfg):
    """Pull a Project Gutenberg book's plain text by id. Returns
    {source, id, title?, source_url, chars, text}. Raises on hard failure."""
    meta = lc.get_json_soft(f"{_GUTENDEX}/{gid}", timeout=cfg["http_timeout"])
    title = (meta or {}).get("title")
    text_url = _gutenberg_text_url((meta or {}).get("formats"))
    if not text_url:
        # common canonical fallbacks
        text_url = f"{_GUTENBERG_FILE}/{gid}/{gid}-0.txt"
    text = lc.get_text(text_url, timeout=cfg["fulltext_timeout"])
    text = _strip_gutenberg_boilerplate(text)
    return {
        "source": "Project Gutenberg",
        "id": gid,
        "title": title,
        "source_url": text_url,  # raw .txt (pulled)
        "readUrl": f"{_GUTENBERG_EBOOKS}/{gid}",  # human reading page
        "chars": len(text),
        "text": text,
    }


# --------------------------------------------------------------------------
# Internet Archive (library/institutional scans only — never community uploads)
# --------------------------------------------------------------------------

# Internet Archive is two things under one roof: vetted library/institutional
# digitizations (copyright-cleared, with named sponsors), and open
# *community-upload* collections where anyone can post anything — including
# in-copyright books. We only ever draw from the former. Items in these
# collections (and their `*_sub` variants) are user uploads; skip them.
_COMMUNITY_COLLECTIONS = ("opensource", "folkscanomy", "community")


def _is_community_upload(collection):
    cols = collection if isinstance(collection, list) else [collection]
    for c in cols:
        c = str(c or "").lower()
        if any(c == cc or c.startswith(cc) for cc in _COMMUNITY_COLLECTIONS):
            return True
    return False


def search_ia(req_title, req_author, cfg, rows=10):
    req_surname = surname_of(req_author)
    tt = sorted(title_tokens(req_title))[:4]
    q = f'title:({" ".join(tt)}) AND creator:({req_surname}) AND mediatype:texts'
    params = {
        "q": q,
        "rows": str(rows),
        "output": "json",
        "fl[]": [
            "identifier",
            "title",
            "creator",
            "year",
            "collection",
            "access-restricted-item",
        ],
    }
    data = lc.get_json_soft(
        _IA_SEARCH, params=params, timeout=cfg["http_timeout"], retries=1
    )
    docs = lc.dig(data, "response", "docs") or []
    out = []
    for d in docs:
        if d.get("access-restricted-item") in (True, "true"):
            continue  # lending-only — we can't actually pull it, so don't offer it
        if _is_community_upload(d.get("collection")):
            continue  # user-uploaded collection — not a vetted source
        creator = d.get("creator")
        creator = creator[0] if isinstance(creator, list) else creator
        # Author rigor (surname + given name) + a real (>=2-token) title overlap
        # reject the surname-only + common-word IA false positives.
        if not author_matches(req_author, creator):
            continue
        if not title_overlap_ok(req_title, d.get("title")):
            continue
        out.append(
            {
                "source": "Internet Archive",
                "id": d.get("identifier"),
                "title": d.get("title"),
                "author": creator,
                "year": d.get("year"),
                "format": "OCR text",
                "url": f"https://archive.org/details/{d.get('identifier')}",
                # IA has no life dates and titles are often generic/series, so even a
                # verified library-scan match is at best medium confidence.
                "confidence": "medium",
                "matchNotes": "library/institutional scan; creator + title match — "
                "confirm it's the right edition before relying on it",
            }
        )
    return out


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

_CONF_RANK = {"high": 0, "medium": 1, "low": 2}


def discover(title, author, cfg):
    """Find verifiable public full-text candidates for a work. Returns
    {query, candidates[]} ranked by confidence (Gutenberg's life-date matches
    first). Pulls no text."""
    cands = []
    if surname_of(author):
        cands += search_gutenberg(title, author, cfg)
        cands += search_ia(title, author, cfg)
    # Gutenberg (clean text + date-verifiable) outranks IA at equal confidence.
    cands.sort(
        key=lambda c: (
            _CONF_RANK.get(c["confidence"], 3),
            0 if c["source"] == "Project Gutenberg" else 1,
        )
    )
    return {"query": {"title": title, "author": author}, "candidates": cands}
