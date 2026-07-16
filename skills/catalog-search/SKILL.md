---
name: catalog-search
description: Search the University of Chicago Library catalog (VuFind) and enrich the results with data the catalog itself can't surface — author context from WikiData (one-line bio, "also wrote", VIAF) and a "full text available" badge for public-domain titles whose full OCR text can be pulled on demand from the Internet Archive to read, summarize, or answer questions about. Use when the user wants to find books/items in the UChicago Library or catalog, look up holdings by title/author/subject/ISBN, narrow a search by format/year/language, learn more about an item's author, or read/summarize/ask questions about the full text of a public-domain book the library holds. Handles natural-language queries ("books on X since 2015", "what else did this author write", "pull the full text of #2 and summarize it"). Searches a configured VuFind catalog (set `catalog_base`).
---

# catalog-search

Search the UChicago Library catalog and progressively **enrich** the results
with things the catalog record alone doesn't give the user. The point isn't just
to return hits — it's to add value on top of them:

- **WikiData** (inline, every result): who the author is in one line, a few of
  their notable works ("also wrote…"), and a VIAF link.
- **Full text** (on demand, public-domain items): a badge advertises that the
  complete OCR text is pullable from the Internet Archive; when the user asks,
  pull it and **read / summarize / answer questions** about the actual book.

Covers and basic availability are *baseline* (already in the catalog) and are
not this skill's job — enrichment means going **beyond** the record.

## When to use

- "Find / search the library catalog for …", "does the library have …",
  "books on \<subject>", "\<title> by \<author>".
- "Narrow that to books / to English / since 2015."
- "Tell me about the author of #2", "what else did they write?"
- "Pull the full text of #1 and summarize it" / "…and find where it discusses X."

Do **not** use this skill for live FOLIO checkout availability, or to claim full
text for an item that didn't earn the badge (see *Honesty* below).

## Setup

Run **every** command with `${LIBBOT_PYTHON:-.venv/bin/python}` from the
**Lib-Bot repo root** (the scripts add their own directory to the path, so the
canonical `skills/catalog-search/scripts/...` paths work as-is). That resolves to
the project venv by default; set `LIBBOT_PYTHON` to override the interpreter when
the skill runs outside the repo.

Set the venv up once:

```bash
python3 -m venv .venv && .venv/bin/pip install .[all]
```

The core skill is pure stdlib — `pip install .` alone runs everything except the
HTRC content-analysis action, which needs the dep from `.[catalog-search]`
(`.[all]` installs every tool's deps). Config (catalog base URL, contact email,
probe depth `N`, `inline_enrichers`) comes from baked-in defaults, overridden by
this skill's section in a repo-root `config.json` — `{"catalog-search": {...}}`
(copy `config.example.json`) — or `LIBBOT_<KEY>` env vars; set `catalog_base` to
your VuFind instance. `inline_enrichers` is the *eager* set only; `--htrc`,
`--pubmed`, and `findtext.py` add the opt-in/on-demand sources.

## Workflow

### 1 & 2 — Search + filter

```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "<query>" \
    --type <AllFields|Title|Author|Subject|ISN> \
    [--filter '<facet:value>'] ... \
    [--facets format,language,publishDate] \
    [--limit 10] [--annotate] [--pretty]
```

Map the user's natural language to `--type` and `--filter`:

| User says | `--type` | `--filter` (repeatable) |
|---|---|---|
| "by Jane Austen", "author …" | `Author` | |
| "the book titled …" | `Title` | |
| "about / on <topic>" | `Subject` (or `AllFields`) | |
| an ISBN/ISSN | `ISN` | |
| "just books" | | `format:"Book"` |
| "print copies only" | | `format:"Print"` |
| "online / e-book" | | `format:"E-Resource"` |
| "in English" | | `language:English` |
| "since 2015" | | `publishDate:[2015 TO *]` |
| "between 2015 and 2020" | | `publishDate:[2015 TO 2020]` |

Filter values use VuFind syntax (quote multi-word values: `format:"Book"`). Pass
`--facets` to get counts back (under `facets` in the output) so you can offer the
user a way to narrow: *"9,911 are books, 5,945 e-resources — want to limit?"*

**Use `--annotate` for user-facing searches** — enrichment is the whole point.
It runs the cheap probes (WikiData + full-text badge) in parallel on the top `N`
results (`N` from config, default 5), fail-soft. Skip `--annotate` only for a
quick count or an internal lookup.

### 3 — Present the results

`catalog_search.py` prints one JSON object: `{ query, type, resultCount,
searchUrl, records[], facets? }`. For each record show the essentials, then fold
in any annotations as **enrichment, clearly attributed**:

- Core: `title` · `authors` · `year` · `format` · `permalink` (a real catalog
  link) · `callNumber` when present.
- `annotations.wikidata` → a one-liner: *"Jane Austen — English novelist
  (1775–1817); also wrote Emma, Persuasion, Sense and Sensibility."*
- `annotations.openlibrary_ia` (and the matching entry in `actions`) → a badge:
  *"📖 Full text available — read it at `<readUrl>`, or say the word and I'll pull
  it to read/summarize."*

**Always give the user the human read link, not just the AI ingest.** Anywhere
full text is surfaced — the badge, a `fulltext.py` pull, a `findtext.py`
candidate, or an `analyze.py` result — there is a `readUrl` (or `url`) pointing
at the actual reading page (Archive.org / Project Gutenberg / HathiTrust). Offer
it alongside any summary so the user can open and read the book themselves. (It's
distinct from `source_url`, the raw text file we pull from — show `readUrl`, not
that.)

**Always offer the `searchUrl`** when you report a count — it's a live link to
the full result set in the catalog UI, where the user can browse all of them,
paginate, refine, or export. Especially do this whenever there are more results
than you're showing: *"404 books by Michel Foucault — here are the top 10; see
them all in the catalog: <searchUrl>."* It reflects the same query and filters
you ran, so the page matches what you searched.

Keep it scannable. A missing annotation just means the probe found nothing —
never invent one.

### 3b — Topical evidence (biomedical/health topics only)

For a biomedical or health subject, add `--pubmed` to the search to attach a
**set-level** `topicEvidence` block (at the top of the output, not inside a
record) drawn from PubMed: `totalArticles`, `recentReviews` (last 5 yrs),
`topMeSH` (key Medical Subject Headings), `sampleReviews` (title/year/link), and
a `searchUrl`.

Use it only when the topic is plausibly clinical/biological. **Heuristic:** add
`--pubmed` when the core subject is a disease, drug, organism, biological
mechanism, or clinical intervention (e.g. "type 2 diabetes", "CRISPR gene
editing"); skip it when the subject is primarily cultural, historical, or
artistic (e.g. "Jane Austen's novels", "Gothic cathedrals"). It's fail-soft and
self-gating: topics without real biomedical literature return no block, so a
missing `topicEvidence` just means "not a PubMed topic."

**Present catalog results first**, then offer the evidence as *secondary*
context — never lead with PubMed stats or let them bury the library's holdings:
*"The library has N items on this. PubMed also indexes ~279,000 articles,
including ~18,000 recent reviews; key themes: Type 2 Diabetes, GLP-1 receptor
agonists, … — want the top reviews?"*

### 4 — Act: pull full text (on demand only)

Only when the user asks to work with a specific item's full text, and only for a
record that carried the full-text badge. Use the `ocaid` from that record's
`actions[].params.ocaid` (or `annotations.openlibrary_ia.ocaid`):

```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/fulltext.py --ocaid <ocaid> \
    --out <working-file.txt>            # or omit --out for a temp file
# Alternatively, re-resolve from a catalog id:
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/fulltext.py --record-id <catalog id>
```

It prints `{ source, id, title, chars, savedTo, readUrl, source_url, head }`
(offer `readUrl` to the user) and writes the
**full** text to `savedTo` (not to stdout — it can be ~700 KB). Then **read the
`savedTo` file** (in chunks if large) to summarize, quote, or answer the user's
question about the book. Never paste the whole text back at the user; deliver
what they asked for (a summary, an answer, the relevant passage).

### 4b — Find full text the eager badge missed (on-demand, verify-first)

The step-4 badge is deliberately **high-precision** (it only trusts OpenLibrary's
`public` flag), so it *misses* many genuinely public-domain works — typically
when the catalog holds only a later reprint whose edition ids don't line up with
the public-domain scan. Use this discovery path whenever a user asks for the full
text of a specific work and **no step-4 badge fired** — that alone is enough to
try it (a reprint-only catalog copy is the common reason, but you don't need to
confirm reprinthood first; there's no "reprint" field — infer public-domain
eligibility from the author's death date if you want a hint). It **widens recall
but returns candidates you must verify** — it does not assert "we have it."

```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/findtext.py --record-id <catalog id>
# or specify the work directly (a catalog "Last, First, dates" heading verifies best):
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/findtext.py --title "<title>" --author "<author>"
```

It returns ranked `candidates[]`, each with `source`, `confidence`
(high/medium/low), and `matchNotes`:

- **Project Gutenberg** — clean plaintext, and verified on author surname +
  given name (+ life dates when available) + title, so a match is usually
  `high`. Prefer it.
- **Internet Archive** — a downloadable scan from IA's vetted library/
  institutional digitizations only (its open community-upload collections, which
  can hold in-copyright books, are never searched). Verified on author + title
  but with no life dates and often a series-level title, so at best `medium` —
  **confirm the edition**.

**Present candidates and confirm before trusting/pulling**, especially for
`medium`/IA: *"The catalog only has a 1954 reprint, but the full text is on
Project Gutenberg — 'An Essay Towards a Philosophy of Education' by Charlotte M.
Mason (1842–1923). Want me to pull it?"* Don't claim it's the right book on a
`low` match. Then pull the confirmed candidate:

```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/fulltext.py --gutenberg <id>   # Project Gutenberg
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/fulltext.py --ocaid <id>       # Internet Archive
```

(You may also offer this proactively when a search result is clearly old enough
to be public domain but carries no full-text badge.)

### 5 — Act: content analysis of a book you *can't* read (HathiTrust/HTRC)

This is the complement to step 4. It reports what a book is **about** — its
dominant themes (common nouns) and the people/places it names (proper nouns) —
from HathiTrust's Extracted Features, which are non-consumptive statistics. So it
works on **in-copyright** volumes that nobody can read in full, where the
Internet Archive full-text action does not. Use it when the user asks "what is
this book about / what does it cover" for a title with no public full text.

```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/analyze.py --htid <htid>
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/analyze.py --url "<HathiTrust page URL>"
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/analyze.py --record-id <catalog id>
```

Getting the `htid`: the cleanest source is a HathiTrust page URL the user
provides (`--url`), or the `htid` from a `--htrc` badge (below). The
`--record-id` catalog→HathiTrust auto-join **matches only ~1 in 8 records** (a
catalog edition's ids rarely equal HathiTrust's scan ids), so when it returns the
"no volume auto-matched" error, ask the user for the item's HathiTrust link.

It prints `{ htid, title, year, pageCount, readUrl, rights?, topThemes[], topNames[] }`
(offer `readUrl` — the HathiTrust page — alongside the fingerprint)
(small — fine to use directly). Present it as a fingerprint: *"It's Kuhn's The
Structure of Scientific Revolutions — its vocabulary centers on science,
paradigms, theory, and research, and it discusses Newton, Lavoisier, Galileo, and
Einstein. It's in-copyright so we can't read it in full; that profile comes
purely from word statistics."* Be clear it's a vocabulary profile, not a summary
of read text.

**Optional inline badge:** add `--htrc` to the search (step 1) to attach a
`hathitrust` "content analysis available" badge to the top-N where a volume is
found (one Bib-API call each; fires ~1 in 8, honestly absent otherwise). The
badge carries the `htid` to hand straight to `analyze.py`.

## Notes & edge cases

- **Zero results.** If `resultCount` is 0, don't invent anything — tell the user
  no matches were found and offer to broaden the query, drop a filter, or switch
  `--type` to `AllFields`.
- **Author searches are broad.** An `Author` search on a name matches everyone
  who shares it (e.g. "Tolkien" returns J. R. R., Christopher, *and* Simon
  Tolkien — and narrowing to `Tolkien, J. R. R.` barely helps). That's expected.
  Use each record's `annotations.wikidata` — which disambiguates them ("English
  writer (1892–1973)" vs "literary scholar (1924–2020)") — and the `permalink`
  to show the user which is which, rather than assuming a single person.
- **`format` is multi-valued.** `format:"Book"` marks books broadly; to restrict
  by carrier use `format:"Print"` (physical) or `format:"E-Resource"` (online).
  A single record can carry several format values.

## Output schema

Top-level envelope:

```
{ query, type, resultCount,
  searchUrl,            # live link to the full results page in the catalog UI
  records: [ <normalized record> ],
  facets?,              # only with --facets
  topicEvidence? }      # only with --pubmed (see below)
```

Normalized record:

```
{ id, title, authors[], year, format, formats[], language, subjects[],
  callNumber, permalink,
  identifiers: { isbn[], cleanIsbn, oclc[], cleanOclcNumber },   # join keys
  annotations: {
    wikidata?:       { qid, label, summary, notableWorks[], occupations[], viaf, url },
    openlibrary_ia?: { fullText:true, access:"public", source:"Internet Archive",
                       ocaid, readUrl, iaCandidates[], olKey, matchedTitle },
    hathitrust?:     { contentAnalysis:true, htid, rights, rightsNote, readUrl, note }  # only with --htrc
  },
  actions: [ { id:"fulltext", enricher:"openlibrary_ia", label, params:{ocaid} },
             { id:"content_analysis", enricher:"hathitrust", label, params:{htid} } ]
}
```

Plus, only with `--pubmed` on a biomedical topic, a top-level sibling of
`records`:

```
topicEvidence?: { source:"PubMed", topic, totalArticles, recentReviews,
                  recentWindow, topMeSH[], sampleReviews[{pmid,title,year,url}],
                  searchUrl }
```

## Principles

- **Honesty.** Only state an enrichment that's actually present. The full-text
  badge appears *only* for genuinely public-domain scans, so most in-copyright
  items will (correctly) have none. Don't promise full text without the badge.
- **Fail-soft.** Enrichment is best-effort and parallel; if a source is slow or
  down its badge is simply absent and the search still returns. Don't retry the
  whole search because one annotation is missing.
- **Lean & on-demand.** Annotate the top `N` only; never pull full text eagerly
  across a result set — that's the single-item action in step 4.
- **Harness-neutral.** If you need to fan out heavy work, *spawn a sub-agent*
  (abstract verb); each harness maps it to its own primitive.

## Extending (enricher contract)

Each source under `scripts/enrichers/` is a module exposing `NAME`, `TIER`
(`inline`|`action`), `probe(record, cfg) -> patch|None` (cheap, fail-soft), and
optionally `act(record, cfg, **params) -> artifact` (expensive). Register it in
`scripts/enrichers/__init__.py:REGISTRY`. See `../../DESIGN.md` for the roster of
sources queued for later phases (HTRC, PubMed, OpenSyllabus, WorldCat).
