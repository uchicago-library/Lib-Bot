#!/usr/bin/env python3
"""Stage 4 action: pull full-text OCR from the Internet Archive for a record
that earned the "full text available" badge, so the agent can read / summarize
/ answer questions about the actual book.

This is expensive and on-demand — run it only when the user asks to work with a
specific item's full text, never eagerly across a result set.

Usage:
  # Most common: the search --annotate step gave you the ocaid in the badge.
  fulltext.py --ocaid synapseml_gutenberg_pride_and_prejudice_by_jane_austen

  # Or re-resolve from a catalog record id (looks the item up + probes):
  fulltext.py --record-id 8921787

  # Control where the text lands and how much preview to print:
  fulltext.py --ocaid <id> --out /path/to/text.txt --head 2000

Output: a JSON summary on stdout:
  { ocaid, title, chars, savedTo, source_url, head }
The FULL text is written to `savedTo` (not printed). Read that file — in chunks
if large — to summarize or answer questions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile

import catalog_search as cs
import lib_common as lc
from enrichers import openlibrary_ia as ia
from enrichers import public_fulltext as pf


def _resolve_record(record_id, cfg):
    """Fetch a single catalog record by id and normalize it."""
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


def _safe_slug(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)[:120]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ocaid", help="Internet Archive item id (from the badge)")
    src.add_argument("--gutenberg", help="Project Gutenberg book id (from findtext.py)")
    src.add_argument("--record-id", help="catalog record id to re-resolve + probe")
    ap.add_argument(
        "--out", default="", help="where to write the full text (default: a temp file)"
    )
    ap.add_argument(
        "--head",
        type=int,
        default=1200,
        help="characters of the text to include in the JSON preview",
    )
    args = ap.parse_args(argv)

    cfg = lc.load_config()

    try:
        if args.gutenberg:
            result = pf.fetch_gutenberg_text(args.gutenberg, cfg)
        elif args.ocaid:
            result = ia.fetch_fulltext(args.ocaid, cfg)
        else:
            record = _resolve_record(args.record_id, cfg)
            result = ia.act(record, cfg)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        lc.warn(f"full-text pull failed: {exc}")
        return 1

    text = result.pop("text", "")
    item_id = result.get("ocaid") or result.get("id")
    out_path = args.out or os.path.join(
        tempfile.gettempdir(), f"libbot_fulltext_{_safe_slug(str(item_id))}.txt"
    )
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        print(json.dumps({"error": f"could not write {out_path}: {exc}"}))
        return 1

    summary = {
        "source": result.get("source", "Internet Archive"),
        "id": item_id,
        "title": result.get("title"),
        "chars": result.get("chars", len(text)),
        "savedTo": out_path,
        "readUrl": result.get("readUrl"),  # human page to offer the user
        "source_url": result.get("source_url"),  # raw text file (provenance)
        "head": text[: args.head],
    }
    if result.get("ocaid"):
        summary["ocaid"] = result["ocaid"]  # backward-compatible field
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
