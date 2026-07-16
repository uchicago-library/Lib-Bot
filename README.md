# Lib-Bot

A harness-neutral collection of AI **skills** and MCP servers for
interacting with University of Chicago Library systems. Built for Claude Code
first, structured so any agent harness can drive the same skills.

The first skill is **`catalog-search`**: it searches the Library catalog and
enriches the results with data the catalog itself can't surface.

**Jump to:**

- [Getting started](#getting-started)
- [Running it](#running-it)
- [How it works](#how-it-works)
- [Data sources](#data-sources)
- [How it's organized](#how-it’s-organized)
- [Status](#status)

**Dig deeper:**

- [`DESIGN.md`](DESIGN.md) — architecture, enricher contract & source roster
- [`EXAMPLES.md`](skills/catalog-search/EXAMPLES.md) — annotated demo queries

## What the `catalog-search` skill does

- **Search & filter** the catalog by title / author / subject / ISBN, narrowing
  by format, year, or language, with facet counts to guide further narrowing.
  Each search returns a `searchUrl` (a live link to the full results page in the
  catalog UI) alongside per-record permalinks.
- **Author context (every result):** a one-line bio, a few notable works ("also
  wrote…"), and a VIAF link, from **WikiData**.
- **Full text (public-domain items):** a badge flags titles whose complete text
  can be pulled from the **Internet Archive**, then on request retrieves it to
  **read, summarize, or answer questions** about the actual book — and always
  hands you a `readUrl` to open and read it yourself.
- **Find full text the badge missed (verify-first):** when the catalog holds only
  a reprint of a public-domain work, locate the free full text on **Project
  Gutenberg** or Internet Archive *library scans* and return candidates to
  confirm first — preferring Gutenberg's clean, life-date-verifiable plaintext
  over OCR. Once you confirm one, it's pulled for the same **read, summarize, or
  answer questions** use, with a `readUrl` to open it yourself.
- **Content analysis (incl. in-copyright):** for books that *can't* be read in
  full, **HathiTrust/HTRC** Extracted Features yield a theme + named-entity
  fingerprint from non-consumptive word statistics.
- **Topical evidence (biomedical):** an opt-in **PubMed** block surfaces recent
  review counts and key MeSH terms for clinical/biological topics.

## How it works

A natural-language request flows through a four-stage pipeline:

```
query → 1. SEARCH → 2. FILTER → 3. ANNOTATE → results shown → 4. ACT (on demand)
        VuFind API   filter[]/    cheap inline   user picks    expensive,
        normalized   facets       enrichment     an item +     single-item
        records                   (top N)         an action     actions
```

Enrichment comes in **two tiers, split by cost**:

- **Inline annotations** — cheap, eager, run across the top results to help the
  user *triage*: the WikiData author note and the "full text available" badge
  (plus, opt-in, a HathiTrust "content analysis available" badge). Fail-soft — a
  slow or missing source just drops its annotation; the search still returns.
- **On-demand actions** — expensive, single-item, run only when the user asks:
  pull a book's full text, run verify-first full-text discovery, fingerprint an
  in-copyright book's contents, or (set-level, opt-in) pull PubMed evidence.

Three rules keep it **honest**:

1. **Badges assert; so they must be precise.** A "full text available" badge only
   fires on a verified public-domain scan — never a fuzzy guess. Recall for the
   long tail is a separate *verify-first discovery* step that returns candidates
   to confirm, not a badge.
2. **High-trust sources only.** Every source below is a library catalog or a
   reputable public dataset. The discovery step draws from Internet Archive's
   vetted *library/institutional* scans only — never its open community-upload
   collections (where in-copyright books can be posted by anyone).
3. **No invented enrichment.** A wrong-person author match is suppressed rather
   than shown; a missing annotation is an honest "nothing found," not a failure.

## Data sources

| Source | What it provides | How it's used | Access |
|---|---|---|---|
| **VuFind catalog** (Solr) | the catalog itself — records, search, filters, facets, permalinks, `searchUrl` | baseline: the search | configurable host; **IP-gated** API |
| **WikiData** | author identity: one-line bio, notable works, VIAF | inline annotation, every result | public, no key |
| **OpenLibrary** | whether a public-domain full-text scan exists | drives the inline "full text available" badge | public, no key |
| **Internet Archive** | full OCR text of public-domain books; library scans for discovery | on-demand full-text pull + discovery (**institutional scans only**) | public; never community uploads |
| **Project Gutenberg** | clean public-domain plain text | verify-first discovery (preferred; verified on author life-dates) | public, curated |
| **HathiTrust / HTRC** | a book's theme + named-entity fingerprint from word statistics (works for in-copyright) | on-demand content analysis | public; non-consumptive; needs the venv dep |
| **PubMed** (NCBI) | recent review counts + key MeSH terms for a topic | opt-in, set-level topical evidence | public, no key |
| **FOLIO** | live checkout availability | *later phase* — not yet wired | test instance |

Only the **catalog (VuFind) leg is IP-gated**; every enrichment source is public
internet, so the skill runs from a laptop with the catalog leg whitelisted.
*Deferred by decision:* **WorldCat** (vendor's API-key posture) and
**OpenSyllabus** (no free live route) — revisit on librarian request.

## Getting started

**1. Install.** From the repo root:

```bash
python3 -m venv .venv && .venv/bin/pip install .[all]
```

`.[all]` installs every tool's dependencies. The core skill is pure Python
stdlib, so `pip install .` alone runs everything except the HTRC content-analysis
action — which needs `pip install .[catalog-search]`. Contributing? Add
`pip install .[dev]` for the flake8/black/isort lint toolchain (see AGENTS.md).

**2. Configure.** Point the skill at your catalog — copy the example config and
set `catalog_base` to your VuFind instance's base URL:

```bash
cp config.example.json config.json
```

`config.json` lives at the repo root and is **gitignored** (keep it and any
secrets out of version control). Settings are **per tool**, nested under the
tool's key — these are catalog-search's, not global:

```json
{ "catalog-search": { "catalog_base": "https://your-vufind-host/vufind" } }
```

Every key is optional except `catalog_base`; precedence is built-in defaults <
`config.json` < `LIBBOT_*` env vars.

| Key (under `catalog-search`) | What it controls |
|---|---|
| `catalog_base` | **(required)** your VuFind catalog's base URL |
| `contact_email` | contact string in the outbound User-Agent |
| `probe_depth_n` | how many top results get eager annotation |
| `inline_enrichers` | enrichers that run *eagerly* on every `--annotate` search — the two defaults are the always-on inline probes; HathiTrust (`--htrc`), PubMed (`--pubmed`), and full-text discovery are opt-in/on-demand (see SKILL.md) |
| `http_timeout` / `fulltext_timeout` | request timeouts (seconds) |

Every key has a `LIBBOT_<KEY>` env override (e.g. `LIBBOT_CATALOG_BASE`,
`LIBBOT_INLINE_ENRICHERS`). Two more env vars tune the runtime rather than the
app: `LIBBOT_CONFIG` (path to a config file outside the repo root) and
`LIBBOT_PYTHON` (interpreter to run scripts with — defaults to the repo's
`.venv`).

**3. Catalog access.** If your catalog's API is **IP-gated** (as the UChicago one
is), searches only succeed from a whitelisted host (e.g. on campus); other IPs
get a `403`. Every enrichment source (WikiData, OpenLibrary/IA, Gutenberg,
PubMed, HathiTrust) is public, so enrichment runs from anywhere.

## Running it

```bash
# In practice you just talk to the agent — it picks the script and flags.
# Each command shows what a natural-language request runs under the hood.
# ${LIBBOT_PYTHON:-.venv/bin/python} is the repo venv by default; override with LIBBOT_PYTHON.

# "Find Pride and Prejudice and tell me about the author"
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/catalog_search.py "pride and prejudice" \
    --type Title --limit 5 --annotate --pretty

# "Pull the full text of that book so I can summarize it"
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/fulltext.py --record-id 1060305 --out book.txt

# "The library only has a reprint — find me the free full text of
#  Charlotte Mason's 'An Essay Towards a Philosophy of Education'"
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/findtext.py \
    --title "An Essay Towards a Philosophy of Education" --author "Charlotte Mason"

# "This one's in copyright — what themes and names does it cover?"
${LIBBOT_PYTHON:-.venv/bin/python} skills/catalog-search/scripts/analyze.py --htid uc1.31822031154305
```

See **[`skills/catalog-search/EXAMPLES.md`](skills/catalog-search/EXAMPLES.md)**
for a full tour of test/demo queries, each annotated with what it demonstrates,
and **[`skills/catalog-search/SKILL.md`](skills/catalog-search/SKILL.md)** for the
agent-facing instructions (every command + flag).

### Running outside the repo (advanced)

Running from the repo is the supported path, but nothing stops you using a skill
on its own — e.g. copied into a global `~/.claude/skills/` (or Codex) directory:

- **Interpreter:** set `LIBBOT_PYTHON` to a Python that has the deps. The core
  skill is stdlib, so a bare `python3` runs it; HTRC needs `htrc-feature-reader`
  on that interpreter (`pip install .[catalog-search]` from a checkout).
- **Config:** with no repo root to hold `config.json`, configure via `LIBBOT_*`
  env vars, or point `LIBBOT_CONFIG` at a config file anywhere.

## How it's organized

Canonical skills live in `skills/`. Harness discovery directories
(`.claude/skills/`, later `.agents/skills/`) are **symlink adapters** back to
them — no content is duplicated. Dependencies are declared in `pyproject.toml`
(per-tool extras; see [Getting started](#getting-started)) and scripts run with
`${LIBBOT_PYTHON:-.venv/bin/python}`. Configuration is an optional, gitignored
repo-root `config.json` (copy `config.example.json`) plus `LIBBOT_*` env vars.

- [`AGENTS.md`](AGENTS.md) — runtime-neutral guide for agents driving this repo.
- [`CLAUDE.md`](CLAUDE.md) — thin adapter that imports `AGENTS.md` (`@AGENTS.md`)
  so Claude Code loads it; no content of its own.
- [`DESIGN.md`](DESIGN.md) — architecture, enricher contract, source roster, and
  the integration facts each enricher relies on.
- [`skills/catalog-search/SKILL.md`](skills/catalog-search/SKILL.md) — agent-facing
  skill instructions.
- [`skills/catalog-search/EXAMPLES.md`](skills/catalog-search/EXAMPLES.md) —
  annotated test/demo queries.

> **Windows note — symlinks.** The `.claude/skills/` discovery entries are Git
> symlinks. On **macOS and Linux** a clone restores them automatically — nothing
> to do. On **Windows**, Git creates real symlinks only when symlink support is
> enabled; otherwise it checks each one out as a small text file containing the
> target path, which breaks skill discovery. Before cloning, enable **Developer
> Mode** (or use an elevated shell) and run
> `git config --global core.symlinks true`. If you already cloned without it, the
> simplest fix is to re-clone after enabling; alternatively recreate the link from
> the repo root in a Developer-Mode terminal:
>
> ```
> del .claude\skills\catalog-search
> mklink /D .claude\skills\catalog-search ..\..\skills\catalog-search
> ```

## Status

`catalog-search` is built and cold-verified: search + filter + facets +
`searchUrl`, WikiData inline enrichment, Internet Archive full-text action,
verify-first Gutenberg/IA discovery, PubMed topical evidence, and HathiTrust/HTRC
content analysis. **Deferred:** WorldCat and OpenSyllabus (see above); FOLIO
availability and an MCP broker (to front the catalog's IP-gated API for many
users from one trusted host) are later phases. See [`DESIGN.md`](DESIGN.md) for
the architecture.
