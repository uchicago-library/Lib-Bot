"""PubMed topical-evidence enricher (set-level, not per-record).

Unlike WikiData / Internet Archive, which enrich individual records, this answers
a question about the *topic* of a search: for a biomedical/health subject, how
much recent peer-reviewed evidence exists, and what is it about? It surfaces:

  - total PubMed articles on the topic,
  - count of recent review articles (last 5 years),
  - the most common MeSH (Medical Subject Heading) terms across those reviews,
  - a few sample recent reviews (title + year + link),
  - a PubMed search URL the user can open.

It's opt-in (the caller decides a topic is biomedical) and fail-soft: if PubMed
errors or the topic clearly isn't biomedical (no hits), it returns None and the
search output simply omits the evidence block.

Verified path (2026-06-24): NCBI E-utilities esearch + efetch, no API key (a key
only raises the rate limit). Politeness: tool + email params on every call.
"""

from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET
from collections import Counter

import lib_common as lc

NAME = "pubmed"
TIER = "topic"

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_REVIEW_SAMPLE = 20  # PMIDs to fetch for MeSH aggregation + samples
_RECENT_YEARS = 5
_TOP_MESH = 8
# Below this many total articles, the topic isn't meaningfully biomedical —
# stay quiet rather than surface a noisy near-empty block (e.g. a stray match
# for a humanities query). Opt-in flag + this floor together keep it honest.
_MIN_TOTAL = 50


def _common_params(cfg):
    return {"tool": "lib-bot", "email": cfg.get("contact_email", "")}


def _esearch_count(term, cfg, extra=None):
    """Return (count, idlist) for a PubMed query."""
    params = dict(_common_params(cfg))
    params.update(
        {"db": "pubmed", "term": term, "retmode": "json", "retmax": str(_REVIEW_SAMPLE)}
    )
    if extra:
        params.update(extra)
    data = lc.get_json_soft(
        f"{_EUTILS}/esearch.fcgi", params=params, timeout=cfg["http_timeout"], retries=1
    )
    res = (data or {}).get("esearchresult") or {}
    try:
        count = int(res.get("count", 0))
    except (TypeError, ValueError):
        count = 0
    return count, res.get("idlist") or []


def _text(node):
    return "".join(node.itertext()).strip() if node is not None else None


def _efetch_articles(pmids, cfg):
    """Fetch article XML for PMIDs; return (mesh_counter, samples[])."""
    if not pmids:
        return Counter(), []
    params = dict(_common_params(cfg))
    params.update({"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"})
    xml = lc.get_text_soft(
        f"{_EUTILS}/efetch.fcgi", params=params, timeout=cfg["http_timeout"], retries=1
    )
    if not xml:
        return Counter(), []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        lc.warn(f"pubmed efetch parse error: {exc}")
        return Counter(), []

    mesh = Counter()
    samples = []
    for art in root.findall(".//PubmedArticle"):
        for d in art.findall(".//MeshHeading/DescriptorName"):
            term = _text(d)
            if term:
                mesh[term] += 1
        title = _text(art.find(".//Article/ArticleTitle"))
        pmid = _text(art.find(".//MedlineCitation/PMID"))
        year = _text(art.find(".//Article/Journal/JournalIssue/PubDate/Year")) or _text(
            art.find(".//Article/Journal/JournalIssue/PubDate/MedlineDate")
        )
        if title and len(samples) < 3:
            samples.append(
                {
                    "pmid": pmid,
                    "title": title,
                    "year": year,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                }
            )
    return mesh, samples


def topic_probe(query, cfg):
    """Set-level evidence for a topic, or None if not biomedically relevant /
    unavailable. `query` is the user's search terms."""
    total, _ = _esearch_count(query, cfg)
    if total < _MIN_TOTAL:
        return None  # not a biomedical topic (or PubMed unavailable): stay quiet

    this_year = datetime.date.today().year
    recent = {
        "mindate": str(this_year - _RECENT_YEARS),
        "maxdate": str(this_year),
        "datetype": "pdat",
    }
    review_count, review_ids = _esearch_count(
        f"({query}) AND review[pt]", cfg, extra=recent
    )
    mesh, samples = _efetch_articles(review_ids, cfg)

    search_url = (
        "https://pubmed.ncbi.nlm.nih.gov/?term="
        + lc_urlquote(query)
        + "+AND+review%5Bpt%5D"
    )
    block = {
        "source": "PubMed",
        "topic": query,
        "totalArticles": total,
        "recentReviews": review_count,
        "recentWindow": f"{this_year - _RECENT_YEARS}–{this_year}",
        "topMeSH": [t for t, _ in mesh.most_common(_TOP_MESH)],
        "sampleReviews": samples,
        "searchUrl": search_url,
    }
    return block


def lc_urlquote(s):
    import urllib.parse

    return urllib.parse.quote(s)
