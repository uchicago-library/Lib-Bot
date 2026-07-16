# catalog-search — Example Queries

A tour of what the skill can do, for testing and demoing. Each example gives the
natural-language **ask** a user would make, the **command** it maps to, what it
**shows** (the capability/principle under test), and what to **look for** in the
output.

## Flag quick reference

| Flag | What it does |
|---|---|
| `--type` | `AllFields` (default) · `Title` · `Author` · `Subject` · `ISN` |
| `--filter` | VuFind filter, repeatable: `format:"Book"`, `language:English`, `publishDate:[2015 TO *]` |
| `--facets` | comma list (`format,language,publishDate`) → counts for narrowing |
| `--annotate` | inline enrichers on top-N: WikiData author context + IA full-text badge |
| `--pubmed` | add set-level PubMed evidence (biomedical topics only; self-gating) |
| `--htrc` | add a HathiTrust "content analysis available" badge where a volume is found |
| `--limit` / `--pretty` | result count / indented JSON |

---

## 1. Search & disambiguation

**Ask:** "Find books by Tolkien."
```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "tolkien" --type Author --limit 5 --annotate --pretty
```
- **Shows:** catalog search → normalized records (title/author/year/format/
  permalink/identifiers) **+** WikiData inline enrichment, including
  disambiguation of people who share a surname.
- **Look for:** J. R. R. vs **Christopher** vs **Simon** Tolkien each get a
  *different, correct* one-line bio; real `permalink` catalog links; ISBN/OCLC
  join keys populated under `identifiers`; and a top-level **`searchUrl`** — a
  live link that opens this whole search in the catalog UI (browse all results,
  paginate, refine, export), in addition to the per-record permalinks.

**Ask:** "Anything by Toni Morrison / Borges / Foucault?" — swap the name to see
each author's bio + "also wrote" + VIAF.

---

## 2. Filtering & facet narrowing

**Ask:** "Books about artificial intelligence, just books, published since 2015 —
and how could I narrow further?"
```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "artificial intelligence" --type Subject \
  --filter 'format:"Book"' --filter 'publishDate:[2015 TO *]' \
  --facets format,language,publishDate --limit 5
```
- **Shows:** eager filters (format + date range) **and** facet counts that let
  the agent offer the next narrowing step.
- **Look for:** `resultCount` drops as filters apply; a `facets` block with
  counts per format/language/year. Try `format:"Print"` (physical only) or
  `format:"E-Resource"` (online) to narrow by carrier.

---

## 3. Author context — WikiData (including honesty)

**Ask:** "Tell me about the authors in a Jane Austen search."
```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "Jane Austen novels" --type AllFields --limit 6 --annotate --pretty
```
- **Shows:** genuine enrichment beyond the record **and** the honesty guard —
  the system would rather show *nothing* than a wrong-person bio.
- **Look for:** Jane Austen (and notable critics like Robert Liddell) get correct
  bios; **minor/ambiguous authors get no WikiData block** rather than a confident
  mismatch. (We specifically fixed a bug where "Marsh, Nicholas" matched the
  wrong person — it's now suppressed.)

---

## 4. Full text of a public-domain book — Internet Archive  *(headline)*

**Ask:** "Find Pride and Prejudice and pull the full text — summarize chapter 1."
```bash
# 1) see the badge in a search:
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "pride and prejudice" --type Title --limit 3 --annotate --pretty
# 2) pull the actual OCR text (re-resolves the record, finds the public scan):
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/fulltext.py --record-id 1060305 --out /tmp/pp.txt
```
- **Shows:** the two-tier model — a cheap inline **badge** advertises full text,
  the expensive **action** pulls it on demand → the agent can read / summarize /
  answer questions from the *real* text.
- **Look for:** `annotations.openlibrary_ia.fullText: true` + a `fulltext` action
  in step 1; step 2 reports `chars` ≈ 727,556 and writes the full OCR to
  `savedTo`. Other public-domain classics to try: **Frankenstein, Dracula, Moby
  Dick, Walden, The Origin of Species.**

---

## 4b. Find full text the badge missed — verify-first (Gutenberg / IA)

**Ask:** "Does the library have *An Essay Towards a Philosophy of Education* by
Charlotte Mason? I'd like to read it."

The catalog's only copy is a reprint with no full-text badge — even though the
work is public-domain and freely available. This path finds it and **returns
candidates to confirm** (it never auto-claims a match):
```bash
# 1) discover (resolve the work from the catalog record, or pass --title/--author):
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/findtext.py \
    --title "An Essay Towards a Philosophy of Education" --author "Charlotte Mason"
# 2) after confirming the right candidate, pull the clean Gutenberg text:
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/fulltext.py --gutenberg 66369 --out /tmp/essay.txt
```
- **Shows:** the honest answer to "the badge is high-precision and misses public
  domain reprints." Recall without lying: Gutenberg matches are verified on author
  surname + given name + life dates + title; IA matches are downloadable scans
  flagged "confirm the edition."
- **Look for:** a **`high`**-confidence `Project Gutenberg` candidate (#66369,
  Charlotte M. Mason 1842–1923) plus a **`medium`** IA scan to verify; step 2
  pulls ~759 K chars of clean plaintext. Then watch the **honesty guard**: a
  search for *"School education"* by Charlotte Mason returns only the correct
  Gutenberg match — the earlier false positives (*"Solar Energy: A Middle School
  Unit"*, *"Insurance Curriculum Guide for High School"*) are rejected by the
  author + title checks.

---

## 5. Content analysis of a book you *can't* read — HathiTrust/HTRC  *(headline)*

**Ask:** "Before I request Thomas Kuhn's *The Structure of Scientific
Revolutions*, what does it actually cover? The library only has it on HathiTrust
as search-only, so I can't read the text."
```bash
# from the item's HathiTrust page URL (the usual way you'll have the id):
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/analyze.py --url "https://babel.hathitrust.org/cgi/pt?id=uc1.31822031154305"
# or directly by HathiTrust id:
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/analyze.py --htid uc1.31822031154305
```
- **Shows:** the complement to #4 — characterizing an **in-copyright** book from
  non-consumptive Extracted Features (word/POS statistics), where pulling the
  text is *not* allowed. IA covers public domain; HTRC covers the rest, so almost
  any held book can be characterized.
- **Look for:** `topThemes` like *science, **paradigm**, theory, research,
  scientists* and `topNames` like *Newton, Lavoisier, Galileo, Einstein,
  Priestley* — a vocabulary fingerprint that captures the book's argument (this
  is the text that coined "paradigm shift") and the scientists it discusses,
  **without reading a word of it**. Present it as a statistical profile, not a
  summary of read text. Public-domain volumes work too, e.g. `--htid
  njp.32101075725117` (Peloponnesian War) or `--htid nyp.33433042068894` (Tom
  Sawyer).

**The honest miss:** the catalog→HathiTrust auto-join only hits ~1 in 8, so:
```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/analyze.py --record-id 1060305
```
- **Shows:** graceful, honest failure. **Look for:** a clear error telling you
  the id didn't auto-match and to supply an `--htid`/`--url` instead.

**The badge in a search:** add `--htrc` to advertise availability on results that
do join:
```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "peloponnesian war" --type Title --limit 3 --htrc --pretty
```

---

## 6. Topical evidence — PubMed (with the honest negative)

**Ask:** "I'm reviewing type 2 diabetes treatments — what's the recent evidence?"
```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "type 2 diabetes" --type Subject --limit 5 --pubmed
```
- **Shows:** set-level enrichment for biomedical topics — total articles, recent
  reviews, top MeSH terms, sample reviews.
- **Look for:** a top-level `topicEvidence` block (≈280k articles, ≈18k recent
  reviews, MeSH like *Hypoglycemic Agents, GLP-1 Receptor Agonists*). Try
  `"CRISPR gene editing"` or `"Alzheimer disease"` too.

**The self-gating negative:**
```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "gothic cathedrals" --type Subject --limit 3 --pubmed
```
- **Shows:** `--pubmed` is honest — it stays silent on non-biomedical topics.
  **Look for:** **no** `topicEvidence` block at all.

---

## 7. Honesty & edge cases (the part worth showing skeptics)

**In-copyright title → no full-text offer:**
```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "tomorrow and tomorrow and tomorrow" --type Title --limit 3 --annotate
```
- **Shows:** honesty — a 2022 in-copyright novel gets a correct author bio but
  **no** full-text badge. **Look for:** `actions: []`, no `openlibrary_ia`.

**Zero results:**
```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "zxqwvk nonsense plurp" --limit 3 --annotate
```
- **Shows:** graceful empty handling. **Look for:** `resultCount: 0`, empty
  `records`, no error, no invented hits.

**Fail-soft enrichment:** if an enrichment source is slow/down, its annotation is
simply absent and the search still returns — annotations never break a search.

---

## 8. Everything at once

**Ask:** "Find books on CRISPR since 2020, just books — annotate them and show me
the recent medical evidence."
```bash
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "CRISPR" --type Subject \
  --filter 'format:"Book"' --filter 'publishDate:[2020 TO *]' \
  --limit 5 --annotate --pubmed --htrc --pretty
```
- **Shows:** the whole pipeline in one call — search → filter → inline annotate
  (WikiData + IA badge + HathiTrust badge) → set-level PubMed evidence.
- **Look for:** filtered results, per-record annotations where available, and a
  `topicEvidence` block — all degrading gracefully where a source has nothing.

---

## What to notice across all of these

- **Permalinks are real** catalog links you can open; the top-level `searchUrl`
  opens the whole search in the catalog UI.
- **Every full-text surface carries a `readUrl`** — the badge, a `fulltext.py`
  pull, a `findtext.py` candidate, and an `analyze.py` result all link to the
  actual reading page (Archive.org / Gutenberg / HathiTrust), so you can open and
  read the book yourself, not just get the AI's summary.
- **Missing annotations are honest absences**, not errors.
- **IA (public domain) + HTRC (in-copyright)** are complementary, so almost any
  held book has *some* answer — readable text or a statistical fingerprint.
- Enrichment is **fail-soft and parallel**: one slow source never blocks a search.
