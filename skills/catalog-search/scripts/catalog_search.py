#!/usr/bin/env python3
"""Stage 1-3 of catalog-search: search the VuFind catalog, normalize records,
and (optionally) attach cheap inline enrichment annotations.

Usage:
  catalog_search.py "tolkien" --type Author --limit 5 --annotate
  catalog_search.py "climate change" --filter 'format:"Book"' \
      --filter 'publishDate:[2015 TO *]' --facets format,language
  catalog_search.py "pride and prejudice" --annotate --pretty

Output: a single JSON object on stdout:
  { query, type, resultCount, records: [ <normalized record>, ... ],
    facets?: { field: [ {value, count}, ... ] } }

Normalized record shape (see ../../DESIGN.md):
  { id, title, authors[], year, format, formats[], language, subjects[],
    callNumber, permalink,
    identifiers: { isbn[], cleanIsbn, oclc[], cleanOclcNumber },
    annotations: { ... },   # filled by --annotate
    actions: [ ... ] }      # capability affordances offered to the user

Diagnostics go to stderr; stdout is always clean JSON for piping.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse

import lib_common as lc

# Fields we explicitly request. Identifiers are absent from VuFind's default
# field set and must be named here (verified against the VuFind Search API).
SEARCH_FIELDS = [
    "id",
    "title",
    "authors",
    "formats",
    "languages",
    "subjects",
    "publicationDates",
    "callNumbers",
    "recordPage",
    "isbns",
    "cleanIsbn",
    "oclc",
    "cleanOclcNumber",
]

_YEAR_RE = re.compile(r"(\d{4})")


def _first(seq):
    if isinstance(seq, list) and seq:
        return seq[0]
    return None


def _author_names(authors):
    """VuFind returns authors as {primary|secondary|corporate: {name: {...}}}.
    Flatten to an ordered, de-duplicated list of name strings."""
    names = []
    if not isinstance(authors, dict):
        return names
    for bucket in ("primary", "secondary", "corporate"):
        block = authors.get(bucket)
        if isinstance(block, dict):
            names.extend(block.keys())
        elif isinstance(block, list):
            names.extend(str(x) for x in block)
        elif isinstance(block, str):
            names.append(block)
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _year(record):
    for d in record.get("publicationDates") or []:
        m = _YEAR_RE.search(str(d))
        if m:
            return int(m.group(1))
    return None


def _subjects(record, limit=6):
    """subjects[] can be a list of strings or a list of lists (heading parts).
    Flatten each sub-list into a single heading string."""
    out = []
    for s in record.get("subjects") or []:
        if isinstance(s, list):
            out.append(" -- ".join(str(p) for p in s))
        else:
            out.append(str(s))
    return out[:limit]


def normalize(record, origin):
    isbns = record.get("isbns") or []
    oclc = record.get("oclc") or []
    record_page = record.get("recordPage") or ""
    permalink = (origin + record_page) if record_page else None
    return {
        "id": record.get("id"),
        "title": record.get("title"),
        "authors": _author_names(record.get("authors")),
        "year": _year(record),
        "format": _first(record.get("formats")),
        "formats": record.get("formats") or [],
        "language": _first(record.get("languages")),
        "subjects": _subjects(record),
        "callNumber": _first(record.get("callNumbers")),
        "permalink": permalink,
        "identifiers": {
            "isbn": isbns,
            "cleanIsbn": record.get("cleanIsbn"),
            "oclc": oclc,
            "cleanOclcNumber": record.get("cleanOclcNumber"),
        },
        "annotations": {},
        "actions": [],
    }


def parse_facets(payload):
    """Turn VuFind's facet block into { field: [ {value, count}, ... ] }."""
    facets = payload.get("facets")
    if not isinstance(facets, dict):
        return None
    out = {}
    for field, entries in facets.items():
        bucket = []
        for e in entries or []:
            if isinstance(e, dict):
                bucket.append({"value": e.get("value"), "count": e.get("count")})
        if bucket:
            out[field] = bucket
    return out or None


def search_results_url(base, query, type_, filters, sort=""):
    """Human-browsable VuFind results page mirroring this search, so the user
    can open the full result set in the catalog UI (paginate, refine, export).
    Same path family as the API but the front-end route (/Search/Results) and
    the same lookfor/type/filter params."""
    params = [("lookfor", query), ("type", type_)]
    for f in filters or []:
        params.append(("filter[]", f))
    if sort:
        params.append(("sort", sort))
    return f"{base}/Search/Results?" + urllib.parse.urlencode(params, doseq=True)


def search(query, type_, filters, limit, page, sort, facets, cfg):
    base = lc.catalog_base()
    origin = lc.origin_of(base)
    params = [
        ("lookfor", query),
        ("type", type_),
        ("limit", str(limit)),
        ("page", str(page)),
    ]
    for f in SEARCH_FIELDS:
        params.append(("field[]", f))
    for filt in filters or []:
        params.append(("filter[]", filt))
    for fac in facets or []:
        params.append(("facet[]", fac))
    if sort:
        params.append(("sort", sort))

    payload = lc.get_json(
        f"{base}/api/v1/search", params=params, timeout=cfg["http_timeout"]
    )
    records = [normalize(r, origin) for r in (payload.get("records") or [])]
    result = {
        "query": query,
        "type": type_,
        "resultCount": payload.get("resultCount", 0),
        "searchUrl": search_results_url(base, query, type_, filters, sort),
        "records": records,
    }
    if facets:
        fc = parse_facets(payload)
        if fc:
            result["facets"] = fc
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Search the UChicago Library catalog (VuFind) and emit "
        "normalized, optionally enriched records as JSON."
    )
    ap.add_argument("query", help="search terms")
    ap.add_argument(
        "--type",
        default="AllFields",
        help="AllFields | Title | Author | Subject | ISN " "(default: AllFields)",
    )
    ap.add_argument(
        "--filter",
        dest="filters",
        action="append",
        default=[],
        help="VuFind filter, repeatable, e.g. --filter 'format:\"Book\"' "
        "--filter 'publishDate:[2015 TO *]'",
    )
    ap.add_argument(
        "--facets",
        default="",
        help="comma-separated facet fields to request counts for, "
        "e.g. --facets format,language,publishDate",
    )
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--sort", default="")
    ap.add_argument(
        "--annotate",
        action="store_true",
        help="run cheap inline enrichers on the top-N results "
        "(N from config probe_depth_n)",
    )
    ap.add_argument(
        "--pubmed",
        action="store_true",
        help="for biomedical/health topics: add a set-level PubMed "
        "evidence block (recent reviews + top MeSH terms)",
    )
    ap.add_argument(
        "--htrc",
        action="store_true",
        help="add a HathiTrust 'content analysis available' badge to "
        "the top-N where a volume is found (one Bib-API call "
        "each; matches ~1 in 8). The analysis itself is the "
        "analyze.py action.",
    )
    ap.add_argument("--pretty", action="store_true", help="indent JSON output")
    args = ap.parse_args(argv)

    cfg = lc.load_config()
    facets = [f.strip() for f in args.facets.split(",") if f.strip()]

    try:
        result = search(
            args.query,
            args.type,
            args.filters,
            args.limit,
            args.page,
            args.sort,
            facets,
            cfg,
        )
    except Exception as exc:  # primary search failure is reported, not swallowed
        print(
            json.dumps({"error": f"search failed: {exc}", "query": args.query}),
            file=sys.stdout,
        )
        lc.warn(f"search failed: {exc}")
        return 1

    if (args.annotate or args.htrc) and result["records"]:
        names = list(cfg.get("inline_enrichers", [])) if args.annotate else []
        if args.htrc and "hathitrust" not in names:
            names.append("hathitrust")
        try:
            import enrichers

            enrichers.annotate(result["records"], cfg, names=names)
        except Exception as exc:  # annotation must never break the search
            lc.warn(f"annotation skipped: {exc}")

    if args.pubmed:
        try:
            from enrichers import pubmed

            evidence = pubmed.topic_probe(args.query, cfg)
            if evidence:
                result["topicEvidence"] = evidence
        except Exception as exc:  # topical evidence is best-effort
            lc.warn(f"pubmed evidence skipped: {exc}")

    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
