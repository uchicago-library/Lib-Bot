#!/usr/bin/env python3
"""HTRC content-analysis action: tell the user what a HathiTrust book is *about*
from its Extracted Features — the dominant themes (common nouns) and the people
/ places it names (proper nouns) — WITHOUT its readable text. Works even for
in-copyright volumes, which is its whole point (the Internet Archive full-text
action only reaches public-domain titles).

Needs the `htrc-feature-reader` dependency from the project venv. One-time setup:
    python3 -m venv .venv && .venv/bin/pip install .
Run with `.venv/bin/python skills/catalog-search/scripts/analyze.py ...`.

Usage (one source required):
  analyze.py --htid mdp.39076001538441
  analyze.py --url "https://babel.hathitrust.org/cgi/pt?id=mdp.39076001538441"
  analyze.py --record-id 1060305     # best-effort catalog->HathiTrust join

Note: the catalog->HathiTrust auto-join (--record-id) matches only ~1 in 8
records, because a catalog edition's OCLC/ISBN rarely equals HathiTrust's
scanned-edition ids. When it misses, give an --htid or --url instead (e.g. from
the item's HathiTrust page).

Output: a JSON report on stdout:
  { htid, title, year, pageCount, rights?, rightsNote?, topThemes[], topNames[] }
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse

import catalog_search as cs
import lib_common as lc
from enrichers import hathitrust as ht


def htid_from_url(url):
    """Pull a HathiTrust volume id out of a pt?id=... or hdl handle URL."""
    parts = urllib.parse.urlsplit(url)
    qs = urllib.parse.parse_qs(parts.query)
    if "id" in qs and qs["id"]:
        return qs["id"][0]
    m = re.search(r"/2027/([^/?#]+)", url)  # hdl.handle.net/2027/<htid>
    if m:
        return m.group(1)
    return None


def _resolve_record(record_id, cfg):
    base = lc.catalog_base()
    origin = lc.origin_of(base)
    params = [("id", record_id)]
    for f in cs.SEARCH_FIELDS:
        params.append(("field[]", f))
    payload = lc.get_json(
        f"{base}/api/v1/record", params=params, timeout=cfg["http_timeout"]
    )
    records = payload.get("records") or []
    if not records:
        raise RuntimeError(f"no catalog record for id {record_id}")
    return cs.normalize(records[0], origin)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--htid", help="HathiTrust volume id")
    src.add_argument("--url", help="a HathiTrust page URL (pt?id=… or hdl handle)")
    src.add_argument("--record-id", help="catalog record id (best-effort join)")
    ap.add_argument("--themes", type=int, default=20, help="how many theme terms")
    ap.add_argument("--names", type=int, default=15, help="how many proper nouns")
    args = ap.parse_args(argv)

    cfg = lc.load_config()
    htid = None
    rights = rights_note = None

    if args.htid:
        htid = args.htid
    elif args.url:
        htid = htid_from_url(args.url)
        if not htid:
            print(
                json.dumps({"error": f"couldn't find a HathiTrust id in: {args.url}"})
            )
            return 1
    else:
        try:
            record = _resolve_record(args.record_id, cfg)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": str(exc)}))
            return 1
        matches = ht.find_htids(record, cfg)
        if not matches:
            print(
                json.dumps(
                    {
                        "error": "no HathiTrust volume auto-matched this record "
                        "(the id join misses ~7/8 of the time). Re-run with "
                        "--htid or --url from the item's HathiTrust page.",
                        "record": {
                            "title": record.get("title"),
                            "permalink": record.get("permalink"),
                        },
                    },
                    ensure_ascii=False,
                )
            )
            return 2
        htid = matches[0]["htid"]
        rights = matches[0].get("rights")
        rights_note = matches[0].get("rightsString")

    try:
        report = ht.analyze(htid, cfg, top_terms=args.themes, top_names=args.names)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        lc.warn(f"content analysis failed: {exc}")
        return 1

    if rights:
        report["rights"] = rights
        report["rightsNote"] = rights_note
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
