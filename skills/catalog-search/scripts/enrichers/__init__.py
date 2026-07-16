"""Enricher registry + orchestrator.

Implements the pluggable-enricher contract from ../../DESIGN.md. Each enricher
is a module exposing:

    NAME : str
    TIER : "inline" | "action"
    probe(record, cfg) -> patch | None      # cheap, fail-soft

where `patch` is a dict that may contain:
    {"annotations": {NAME: <fact>}, "actions": [<action dict>]}

Action-tier enrichers also expose:
    act(record, cfg, **params) -> artifact   # expensive, on-demand

`annotate()` runs every enabled enricher's probe across the top-N records in
parallel and merges the patches. Any probe that raises is swallowed (fail-soft):
a flaky enricher drops its badge, it never breaks the search.
"""

from __future__ import annotations

import concurrent.futures
import importlib

import lib_common as lc

# name -> import path. Add a source by dropping in a module and one line here.
REGISTRY = {
    "wikidata": "enrichers.wikidata",
    "openlibrary_ia": "enrichers.openlibrary_ia",
    # pubmed is a TIER="topic" (set-level) enricher: invoked directly by the
    # search CLI (--pubmed), not through the per-record annotate() loop.
    "pubmed": "enrichers.pubmed",
    # hathitrust badge is opt-in via the search --htrc flag (one Bib-API call per
    # record); the heavy EF analysis lives in the analyze.py action (needs venv).
    "hathitrust": "enrichers.hathitrust",
}


def _load(names):
    mods = []
    for n in names:
        path = REGISTRY.get(n)
        if not path:
            lc.warn(f"unknown enricher '{n}' — skipping")
            continue
        try:
            mods.append(importlib.import_module(path))
        except Exception as exc:  # noqa: BLE001 - never let a bad module break search
            lc.warn(f"enricher '{n}' failed to load: {exc}")
    return mods


def load_enricher(name):
    """Import a single enricher module by name (for the act() path)."""
    path = REGISTRY.get(name)
    if not path:
        raise KeyError(f"unknown enricher '{name}'")
    return importlib.import_module(path)


def _safe_probe(mod, rec, cfg):
    try:
        return mod.probe(rec, cfg)
    except Exception as exc:  # noqa: BLE001 - fail-soft per contract
        lc.warn(f"{getattr(mod, 'NAME', '?')} probe error: {exc}")
        return None


def _merge(rec, patch):
    if not patch:
        return
    ann = patch.get("annotations")
    if isinstance(ann, dict):
        rec["annotations"].update(ann)
    acts = patch.get("actions")
    if isinstance(acts, list):
        rec["actions"].extend(acts)


def annotate(records, cfg, names=None):
    """Mutate `records` in place: attach inline annotations + action badges to
    the top-N (config probe_depth_n). `names` overrides which enrichers run
    (defaults to cfg['inline_enrichers']). Returns the same list."""
    n = cfg.get("probe_depth_n", 5)
    targets = records[:n]
    mods = _load(names if names is not None else cfg.get("inline_enrichers", []))
    if not mods or not targets:
        return records

    max_workers = min(8, max(1, len(targets) * len(mods)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {}
        for rec in targets:
            for mod in mods:
                futs[ex.submit(_safe_probe, mod, rec, cfg)] = rec
        for fut in concurrent.futures.as_completed(futs):
            try:
                _merge(futs[fut], fut.result())
            except Exception as exc:  # noqa: BLE001
                lc.warn(f"merge error: {exc}")
    return records
