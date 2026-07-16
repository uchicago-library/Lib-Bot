# Lib-Bot — Design

Lib-Bot is a harness-neutral agentic system for interacting with University of
Chicago Library systems. It targets Claude Code first but is structured so any
agent harness can drive the same skills.

This document covers the architecture and the reasoning behind it: the
repository pattern, the enrichment pipeline, the enricher contract, the source
roster, and the integration facts each enricher relies on. For what is currently
built versus deferred, see the README; for agent-facing usage, see
`skills/catalog-search/SKILL.md`.

---

## Repository structure (harness-neutral)

Canonical-source + adapter pattern:

- `skills/` — **canonical source of truth.** Each skill is a directory with a
  `SKILL.md` (+ optional scripts/assets). All skill content lives here and
  nowhere else.
- `.claude/skills/` — Claude Code's discovery path; entries are **symlinks** to
  `../../skills/<name>`, with no content of their own.
- `mcp/` — *(future)* MCP servers, for capabilities a `SKILL.md` + scripts
  can't cleanly provide (broker access, shared state/cache, long-running
  services).

To support another harness later (e.g. `.agents/skills/`), create its discovery
directory and symlink the same canonical skills in — no content is duplicated.
Keep skill instructions harness-agnostic: describe fan-out with a neutral verb
("spawn a sub-agent") rather than a Claude-Code-specific tool.

---

## First skill: `catalog-search`

Search the UChicago Library catalog (VuFind) and progressively enrich results
with data the catalog itself can't surface.

### Guiding principles

1. **Enrichment = beyond the catalog.** If a librarian could already get it from
   the existing record (covers, basic metadata), it's *baseline*, not
   enrichment. Every feature must go meaningfully beyond what the catalog already
   offers.
2. **Baseline vs. enrichment are distinct layers.**
   - *Baseline* (already in the catalog): VuFind/Solr results, Syndetics covers,
     FOLIO availability.
   - *Enrichment* (beyond the catalog): the plugin sources in the roster.
3. **Two tiers of enrichment, split by cost.**
   - *Inline annotations* — cheap, compact, applied across the result set to help
     the user **triage** (facts + capability badges).
   - *On-demand actions* — expensive, large, single-item; **advertised** by a
     badge, **executed** only when the user asks.
4. **Pluggable enrichers** behind a uniform contract — adding a source is just
   implementing the contract.

### Pipeline

```
 user query
     │
     ▼
┌──────────────┐  NL → params
│  1. SEARCH   │  VuFind /api/v1/search ──► normalized records[]
└──────────────┘
     │
     ▼
┌──────────────┐  filter[] from query  +  conversational facet narrowing
│  2. FILTER   │
└──────────────┘
     │
     ▼
┌──────────────┐  top-N · parallel · fail-soft
│  3. ANNOTATE │  cheap probes → inline facts + capability badges
└──────────────┘  WikiData fact · "full text available" · "on N syllabi" …
     │
     ▼
  results shown ──► user picks an item + an action
     │
     ▼
┌──────────────┐  single item / topic · expensive
│   4. ACT     │  full-text read · EF analysis · PubMed synthesis · dossier
└──────────────┘
```

### Stages

**1. Search — VuFind JSON API**
- `GET {base}/api/v1/search` with `lookfor`, `type`
  (AllFields/Title/Author/Subject/ISN), `field[]` (request *only* needed fields
  to keep payloads lean), `limit`, `page`, `sort`.
- Agent maps natural language → `lookfor` + a sensible `type`.
- Emits **normalized records**. Must extract ISBN/OCLC/LCCN and author authority
  IDs — these are the join keys for every enricher.

**2. Filter / limit**
- *Eager:* constraints in the query ("English, since 2015") →
  `filter[]=language:English`, `filter[]=publishDate:[2015 TO *]`,
  `filter[]=format:Book`.
- *Conversational:* VuFind returns facet counts → agent offers "narrow by
  format / year / subject?"

**3. Annotate — cheap, eager, fail-soft (the badges)**
- For the top **N** results (default 5, configurable), run light probes **in
  parallel**; each attaches an inline fact and/or registers an available action.
- A probe that fails just drops its badge — it never breaks the search.
- Probes determine which tier-2 actions get offered (e.g. a light OpenLibrary
  Read-API status check yields the "full text available" badge *without* pulling
  any text).

**4. Act — expensive, on-demand, single-item**
- Triggered by the user ("pull full text of #2 and summarize").
- Each is a heavy enricher: IA full-text read/Q&A · HTRC EF vocabulary/topic
  analysis · PubMed evidence synthesis · WikiData dossier.

### Normalized record (the spine everything hangs off)

```
{
  id, title, authors[], year, format, language,
  identifiers: { isbn[], oclc, lccn, authorAuthorityIds[] },  ← join keys
  callNumber, permalink: "{base}/Record/{id}",
  annotations: {},      ← filled by stage 3
  actions: []           ← capability affordances offered to the user
}
```

The search envelope around `records[]` also carries a **`searchUrl`** — the
human-browsable VuFind results page (`{base}/Search/Results?lookfor=…&type=…&
filter[]=…`) mirroring the same query + filters — so the agent can hand the user
a "see all N in the catalog" link alongside the per-record permalinks.

Likewise, every surface that exposes full text carries a human **`readUrl`** to
the actual reading page (Archive.org / Project Gutenberg / HathiTrust) — on the
full-text badge, the `fulltext.py` pull, each `findtext.py` candidate, and the
`analyze.py` result — distinct from the machine `source_url` (the raw text file
we ingest). The agent offers `readUrl` so a user can read the book themselves,
not just receive the AI's summary.

### Enricher contract (pluggability, made concrete)

Every source is a uniform plugin; adding one = implementing this. Verbs stay
harness-neutral.

```
Enricher:
  name · tier (inline | action) · granularity (item | topic)
  probe(record) -> { annotation?, badge?, action_id? }   # cheap, fail-soft
  act(record, params) -> artifact                         # expensive, on-demand
```

### Source roster

| Source | Bucket | Tier | Inline shows | On-demand does | Access |
|---|---|---|---|---|---|
| VuFind / Solr | baseline (search) | — | the result itself | — | IP-gated API |
| Syndetics | baseline (covers) | — | cover image | — | already in catalog |
| FOLIO | baseline (availability) | — | our availability | — | test instance |
| WikiData | enrichment | inline | author/subject context, "also wrote…" | linked-data dossier | public, no key |
| OpenLibrary → IA | enrichment | action | "full text available" (high-precision badge) | pull text → read / summarize / Q&A | public; OL `public` flag only |
| Public full-text discovery (Gutenberg + IA) | enrichment | action | — (on-demand, verify-first) | `findtext.py` finds candidates the badge missed (catalog holds a reprint); Gutenberg verified on surname+given-name+dates+title, IA = downloadable scan to confirm | public, no key |
| HathiTrust (HTRC EF) | enrichment | action | "content analysis available" (`--htrc`) | EF → theme + named-entity fingerprint (`analyze.py`) | public; incl. in-copyright (features only); needs venv dep |
| PubMed | enrichment | inline *(set-level)* | "N recent reviews on topic" (`--pubmed`) | evidence pull + MeSH synthesis | public |
| OpenSyllabus | enrichment | inline | "on N syllabi, top field X" | teaching-impact / co-assignment report | **Deferred** — no clean free live route post-subscription (API is subscription-only, Explorer is Cloudflare-gated, Galaxy API undocumented / ToS-gray); the sanctioned free path is the offline bulk research dataset. Revisit on librarian request or to commit to the dataset. |
| WorldCat / BTAA | enrichment | inline | "held by N libraries" | (request elsewhere) | **Dropped** — vendor hostile to API-key use; revisit only on librarian request. |

### Cross-cutting

- **Access — now vs. later.** Only the **catalog (VuFind) leg is IP-gated**;
  every enrichment source (WikiData/OL/IA/PubMed/HTRC) is public internet. So
  *today*, the only thing needing whitelisting is the VuFind call — all
  enrichment runs from a laptop with zero setup. *Later, for users:* an MCP
  broker on one trusted, whitelisted host fronts the catalog leg (whitelist one
  IP, not hundreds; rate-limit + audit at the choke point); enrichment can stay
  direct.
- **Skill-first.** The first phase is a pure skill (`SKILL.md` + small scripts
  for API calls / normalization). It graduates to MCP only when we need the
  broker, shared caching / rate-limiting, or session state — and the plugin
  logic ports over largely unchanged.
- **Discipline:** fail-soft probes; lean payloads (request only needed fields);
  cache probe results within a session.
- **Eager badges are high-precision; recall is a verify-first action.** An
  inline badge *asserts* something ("full text is here"), so it must not fire on
  a fuzzy match — it only trusts strong signals (OL `public`; identifier joins).
  That deliberately misses cases (e.g. a public-domain work the library holds
  only as a reprint). Closing that gap is an **on-demand discovery** step that
  returns ranked candidates the caller verifies before trusting — never a new
  badge source. Keeps the badge trustworthy while still reaching the long tail.

---

## Environment & integration facts

The catalog base URL is configurable; everything else here is either a public
third-party API or a UChicago architectural fact a developer integrating against
the catalog will need.

### Catalog (VuFind)

- **Base URL must be configured** (`catalog_base`) — there is no default
  catalog; point it at your VuFind instance (a dev instance during development,
  production later). The origin is derived from the base for permalinks.
- **Search & Record JSON API** (`/api/v1/search`, `/api/v1/record`) is enabled
  but **IP-gated** via VuFind's `permissions.ini` (`[api.SearchAndRecord]`) to
  localhost plus a UChicago allowlist (campus + internal ranges). The catalog
  site is also fronted by Cloudflare Turnstile. This allowlist is separate from
  the `RateLimiter.yaml` whitelist. *(For users later, an MCP broker on one
  whitelisted host fronts this leg — see Cross-cutting.)*
- **Holdings / availability** is served by a separate internal REST service
  (FOLIO-backed), reachable from campus.

### VuFind API shape

- **Endpoints:** `GET /api/v1/search`, `GET /api/v1/record` → 200 JSON.
- **Envelope:** `{ resultCount, records[], status }`.
- **Identifiers must be requested via `field[]`** (absent from the default set).
  Field names: `isbns[]` + `cleanIsbn`, `oclc[]` + `cleanOclcNumber` (the
  enrichment **join keys**), plus `publicationDates[]`, `callNumbers[]`,
  `formats[]`, `languages[]`, `subjects[]`,
  `authors{primary|secondary|corporate}`, `title`, `recordPage`.
- **Permalink:** `recordPage` already contains `/vufind` (e.g.
  `/vufind/Record/8921787`), so permalink = **origin** + `recordPage` — *not*
  base + recordPage.
- **Filters / facets:** `facet[]=format|language` returns counts; filter syntax
  is `filter[]=format:"Book"` (quoted), taken from the facet `href`.
- **Author authority IDs (VIAF/LCNAF) are not exposed** in these API fields, so
  WikiData reconciliation starts name-based unless pulled from the full MARC.

### Enrichment endpoints

- **WikiData** — author name → entity via the Wikibase **Action API**
  (`wbsearchentities` to reconcile, `wbgetentities` for claims/labels); pull
  notable works (`P800`), occupation (`P106`), and VIAF id (`P214`).
  Reconciliation is name-based with match guards; authority-ID reconciliation
  (via VIAF) is the intended upgrade.
- **OpenLibrary** — Read API (`/api/books?bibkeys=ISBN:…&jscmd=viewapi`) returns
  a status (`full access` / `lendable` / `checked out` / `restricted`); Search
  API (`/search.json?fields=…,ia,ebook_access`) with `ebook_access=public` +
  `ia` finds full-text public-domain items.
- **Internet Archive** — `archive.org/metadata/{ocaid}` lists files;
  `archive.org/download/{ocaid}/{ocaid}_djvu.txt` returns the full OCR text dump.
- **HathiTrust** — the live Data API (per-page OCR plaintext) was **retired
  2024-07**; full text now requires a bulk dataset request. **HTRC Extracted
  Features** (JSON-LD, page-level features, incl. in-copyright, no auth) is the
  live content-features path; the 2025.04 set uses a **stubbytree** layout (not
  pairtree), so resolve HTIDs with `htrc-feature-reader` (`htid2rsync` /
  `utils.download_file`). The **Bib API**
  (`catalog.hathitrust.org/api/volumes/brief/json/{id}`) returns rights
  (`Full view` / `Limited (search-only)`) + HTID; the catalog→HathiTrust join by
  OCLC/ISBN works but **match rate is low per single edition**, so query with
  *all* of a record's identifiers and consider a title/author fallback.
- **PubMed** — E-utilities `esearch` + `efetch` return results + MeSH
  descriptors; no key needed (a key only raises the rate limit).

Outbound calls send a descriptive User-Agent; the contact comes from
`contact_email` (no default — omitted if unset).

### Configuration (knobs)

Config values, not hard-coded:

- **Catalog base URL** (`catalog_base`) — **required, no default**; origin
  derived for permalinks.
- **Contact email** (`contact_email`) — optional User-Agent contact for upstream
  APIs; no default (omitted from the User-Agent if unset).
- **Probe depth `N`** (`probe_depth_n`) — how many top results get eager
  annotation (default 5).
- **Inline enrichers** (`inline_enrichers`) — which enrichers run *eagerly* in
  the annotate stage (the always-on inline set); opt-in/on-demand sources are
  not listed here.
- **Secrets** — API keys (OCLC, optional NCBI) in an uncommitted config, never
  inline.

Settings are **per tool**, nested under the tool's key in `config.json` (e.g.
`{"catalog-search": {...}}`); each skill reads its own section. Top-level keys
are reserved for genuinely global settings (none yet). Every key also has a
`LIBBOT_<KEY>` env override.
