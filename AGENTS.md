# Lib-Bot

Lib-Bot is a harness-neutral agentic system for interacting with University of
Chicago Library systems. It targets Claude Code first but is structured so any
agent harness can drive the same skills.

The canonical skills live under `skills/`. Harness-specific discovery
directories such as `.claude/skills/` (and, later, `.agents/skills/`) are
adapters — usually symlinks — back to the canonical skill directories. No skill
content is duplicated; to support a new harness, add its discovery directory and
symlink the same canonical skills in.

## Skills

- **`catalog-search`** — Search the UChicago Library catalog (VuFind) and enrich
  results with data the catalog can't surface. Every search returns normalized
  records (with a `searchUrl` to the full results page in the catalog UI) plus:
  WikiData author context (inline, every result); an Internet Archive "full text
  available" badge with an on-demand pull for public-domain titles; a verify-first
  full-text **discovery** path (Project Gutenberg + IA library scans) for works
  the badge misses; opt-in **PubMed** topical evidence for biomedical subjects;
  and **HathiTrust/HTRC** content analysis that fingerprints a book's themes and
  named entities even when it's in-copyright and unreadable. Plain requests to
  *search the catalog, find a book, look up holdings, narrow by
  format/year/language, learn about an item's author, read/summarize a
  public-domain title, find where a work's full text is freely available, see
  recent evidence on a health topic, or learn what an in-copyright book is about*
  route here. Sources are high-trust only (catalog, WikiData, Gutenberg,
  OpenLibrary, IA institutional scans, HathiTrust, PubMed) — never community
  uploads. Does **not** do live FOLIO checkout availability, and never claims
  full text it can't actually deliver.

## Conventions

- **Skill instructions are harness-agnostic.** They describe fan-out with the
  abstract verb **"spawn a sub-agent"** rather than a Claude-Code-specific tool.
  Each harness adapter maps "spawn" to its own primitive (Claude Code →
  `Agent(...)`, etc.) and a capability class to a concrete model.
- **Scripts run with `${LIBBOT_PYTHON:-.venv/bin/python}`** from the repo root —
  the project venv by default, or whatever `LIBBOT_PYTHON` points at. Set the venv
  up once with `python3 -m venv .venv && .venv/bin/pip install .[all]`
  (dependencies are per-tool extras in `pyproject.toml`; the core skill is pure
  stdlib). Each script adds its own directory to the path, so the canonical
  `skills/<name>/scripts/...` paths work as-is.
- **Configuration is data, not code.** Knobs live in an optional, gitignored
  repo-root `config.json` — **per tool**, nested under the tool's key
  (`{"catalog-search": {...}}`) — or `LIBBOT_*` env vars;
  secrets (API keys) never go inline or in version control.
- **Enrichment must be honest and fail-soft.** Only surface enrichment that was
  actually retrieved; a slow or missing source drops its annotation without
  breaking the underlying search.
- **Lint before committing.** `pip install .[dev]` pulls flake8, black, and
  isort (config in `.flake8` and `pyproject.toml`); CI runs the same checks in
  `.github/workflows/lint.yml`. Run `black . && isort . && flake8 .` locally
  before opening a PR.

## Layout

```
skills/<name>/
  SKILL.md                 # canonical skill instructions (harness-neutral)
  scripts/                 # run with ${LIBBOT_PYTHON:-.venv/bin/python}
.claude/skills/<name> -> ../../skills/<name>   # Claude Code discovery (symlink)
config.example.json        # copy to repo-root config.json to override defaults
pyproject.toml             # deps as per-tool extras (.[all], .[<tool>])
DESIGN.md                  # architecture + integration facts
```

See `DESIGN.md` for the full architecture (pipeline, normalized record shape,
enricher contract, source roster, integration facts) and the README for current
build status.
