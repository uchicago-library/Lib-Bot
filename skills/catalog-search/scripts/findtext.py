#!/usr/bin/env python3
"""On-demand public full-text DISCOVERY: find where a work's complete text can
be read for free (Project Gutenberg + Internet Archive), even when the catalog
holds only a later reprint and the eager full-text badge didn't fire.

This is the deliberate, *verify-before-trust* counterpart to the eager badge.
The badge stays high-precision; this widens recall but returns CANDIDATES the
caller must confirm (it pulls no text). Gutenberg candidates are verified on
author surname + life dates + title (high confidence, clean plaintext); Internet
Archive candidates are downloadable scans verified on author + title (medium —
confirm the edition).

Usage (one source of the work required):
  findtext.py --title "An Essay Towards a Philosophy of Education" --author "Charlotte Mason"
  findtext.py --record-id 8921787      # resolve title + author (with life dates) from the catalog

Output: a JSON object on stdout:
  { query: {title, author}, candidates: [
      { source, id, title, author, authorDates|year, format, url, textUrl?,
        confidence: high|medium|low, matchNotes } ] }

Then, after the user confirms a candidate, pull it with:
  fulltext.py --gutenberg <id>     (Project Gutenberg)
  fulltext.py --ocaid <id>         (Internet Archive)
"""

from __future__ import annotations

import argparse
import json

import catalog_search as cs
import lib_common as lc
from enrichers import public_fulltext as pf


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
    ap.add_argument("--title", help="work title")
    ap.add_argument(
        "--author",
        help="author (a catalog 'Last, First, dates' " "heading verifies best)",
    )
    ap.add_argument(
        "--record-id", help="catalog record id to resolve title+author from"
    )
    args = ap.parse_args(argv)

    cfg = lc.load_config()

    title, author = args.title, args.author
    if args.record_id:
        try:
            rec = _resolve_record(args.record_id, cfg)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": str(exc)}))
            return 1
        title = title or rec.get("title")
        author = author or (rec.get("authors") or [None])[0]

    if not title or not author:
        print(json.dumps({"error": "need --title and --author (or --record-id)"}))
        return 1

    result = pf.discover(title, author, cfg)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
