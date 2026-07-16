"""Shared helpers for the catalog-search skill. Provides:
  - config loading (baked-in defaults + optional config.json override + a few
    env-var overrides);
  - polite, fail-soft HTTP GET helpers (descriptive User-Agent, timeouts);
  - small utilities (permalink origin, safe nested get).

Design notes live in ../../DESIGN.md. The cardinal rule for enrichment is
fail-soft: a probe that errors must return nothing, never raise into the search.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

# Baked-in defaults for this skill. Overridable by the skill's own section in a
# config.json — the `"<skill-name>": {...}` block (at the repo root, or wherever
# $LIBBOT_CONFIG points) — and by the matching LIBBOT_<KEY> environment variable.
# These are catalog-search's settings, not global ones. Works with no config.
DEFAULTS = {
    # VuFind catalog base URL. No default — it must be configured (there is no
    # public default catalog). The path (e.g. /vufind) stays on the base;
    # permalinks use the ORIGIN only, since the API's recordPage already
    # includes the path.
    "catalog_base": "",
    # Contact address embedded in the User-Agent for polite identification to
    # upstream APIs (WikiData / Internet Archive / NCBI etc.). No default — a
    # real contact is deployer-specific; configure it, or leave it unset and the
    # contact is simply omitted from the User-Agent.
    "contact_email": "",
    # How many top results get eager (cheap) annotation in stage 3.
    "probe_depth_n": 5,
    # Enrichers that run eagerly on every --annotate search (the cheap,
    # always-on inline probes). HathiTrust (--htrc), PubMed (--pubmed), and
    # full-text discovery (findtext.py) are opt-in / on-demand, not listed here.
    "inline_enrichers": ["wikidata", "openlibrary_ia"],
    # HTTP timeouts (seconds): short for probes, long for full-text pulls.
    "http_timeout": 20,
    "fulltext_timeout": 90,
}

# This skill's name = its directory under skills/ (e.g. "catalog-search"). The
# skill reads its settings from the matching section of the shared config.json.
_SKILL_NAME = os.path.basename(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

_config_cache = None


def _repo_root():
    """Walk up from this file to the repo root (the dir holding pyproject.toml).
    Returns None when run detached from the repo (e.g. a skill copied into a
    global skills dir); file-based config then comes only from $LIBBOT_CONFIG."""
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.exists(os.path.join(d, "pyproject.toml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _config_path():
    """The config file to read: $LIBBOT_CONFIG if set, else the repo-root
    config.json. None when neither is available."""
    explicit = os.environ.get("LIBBOT_CONFIG")
    if explicit:
        return explicit
    root = _repo_root()
    return os.path.join(root, "config.json") if root else None


def _coerce(default, raw):
    """Coerce an env-var string to the type of its matching default."""
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    if isinstance(default, list):
        return [v.strip() for v in raw.split(",") if v.strip()]
    return raw


def load_config():
    """Return the merged config dict (cached). Precedence:
    DEFAULTS < this skill's section in config.json ($LIBBOT_CONFIG or repo-root)
    < LIBBOT_<KEY> env vars. Never raises — a malformed config file is reported
    to stderr and ignored so the skill still runs."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    cfg = dict(DEFAULTS)

    path = _config_path()
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            warn(f"ignoring unreadable config file {path}: {exc}")
            data = None
        if isinstance(data, dict):
            section = data.get(_SKILL_NAME)
            if isinstance(section, dict):
                # Skip "_comment"-style documentation keys from the example file.
                cfg.update({k: v for k, v in section.items() if not k.startswith("_")})
            # Scalar keys at the top level look like misplaced tool settings.
            for k, v in data.items():
                if not k.startswith("_") and not isinstance(v, dict):
                    warn(
                        f"ignoring top-level config key '{k}' — put tool "
                        f"settings under the '{_SKILL_NAME}' section"
                    )

    for key in DEFAULTS:
        raw = os.environ.get("LIBBOT_" + key.upper())
        if raw is not None and raw != "":
            cfg[key] = _coerce(DEFAULTS[key], raw)

    _config_cache = cfg
    return cfg


def origin_of(url: str) -> str:
    """Scheme + host of a URL (no path). Used to build catalog permalinks:
    origin + recordPage, since recordPage already includes /vufind/Record/..."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def catalog_base() -> str:
    """The configured VuFind base URL, trailing slash stripped. Raises a clear
    error when unset — there is no default catalog. Only the catalog paths call
    this; the Gutenberg / Internet Archive / HathiTrust paths don't need it."""
    base = (load_config().get("catalog_base") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "catalog_base is not configured. Copy config.example.json to "
            "config.json and set `catalog_base` to your VuFind base URL, or set "
            "the LIBBOT_CATALOG_BASE environment variable."
        )
    return base


def user_agent() -> str:
    contact = (load_config().get("contact_email") or "").strip()
    suffix = f"; {contact}" if contact else ""
    return f"Lib-Bot/0.1 (UChicago Library catalog-search{suffix})"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _open(url: str, params=None, timeout=None, accept=None):
    """Low-level GET returning the decoded body as text. Raises on failure;
    callers that must stay fail-soft use the _soft wrappers below."""
    cfg = load_config()
    if timeout is None:
        timeout = cfg["http_timeout"]
    if params:
        # doseq handles repeated keys like field[]=a&field[]=b and filter[].
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}?{query}"
    headers = {"User-Agent": user_agent()}
    if accept:
        headers["Accept"] = accept
    headers["Accept-Encoding"] = "gzip"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def get_json(url: str, params=None, timeout=None):
    """GET + parse JSON. Raises on HTTP/parse failure (use for the primary
    search, where the agent should learn the call failed)."""
    body = _open(url, params=params, timeout=timeout, accept="application/json")
    return json.loads(body)


def get_text(url: str, params=None, timeout=None):
    """GET raw text. Raises on failure."""
    return _open(url, params=params, timeout=timeout)


_SOFT_ERRORS = (
    urllib.error.URLError,
    urllib.error.HTTPError,
    ValueError,
    TimeoutError,
    OSError,
)


def get_json_soft(url: str, params=None, timeout=None, retries=0, backoff=1.0):
    """Fail-soft GET+JSON for enrichment probes: returns None on ANY error
    (HTTP, network, timeout, parse) instead of raising. This is what keeps a
    flaky enricher from breaking the search. `retries` extra attempts with
    `backoff`-second sleeps cover slow/flaky upstreams (e.g. OpenLibrary)."""
    import time

    attempt = 0
    while True:
        try:
            return get_json(url, params=params, timeout=timeout)
        except _SOFT_ERRORS as exc:
            if attempt >= retries:
                warn(f"soft GET failed ({url}): {exc}")
                return None
            time.sleep(backoff * (attempt + 1))
            attempt += 1


def get_text_soft(url: str, params=None, timeout=None, retries=0, backoff=1.0):
    """Fail-soft GET+text. Returns None on any error (with optional retries)."""
    import time

    attempt = 0
    while True:
        try:
            return get_text(url, params=params, timeout=timeout)
        except _SOFT_ERRORS as exc:
            if attempt >= retries:
                warn(f"soft GET failed ({url}): {exc}")
                return None
            time.sleep(backoff * (attempt + 1))
            attempt += 1


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------


def warn(msg: str) -> None:
    """Diagnostics go to stderr so stdout stays clean JSON for piping."""
    print(f"[catalog-search] {msg}", file=sys.stderr)


def dig(obj, *path, default=None):
    """Safe nested lookup through dicts/lists; returns default if any hop is
    missing. dig(rec, 'authors', 'primary') etc."""
    cur = obj
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif (
            isinstance(cur, list)
            and isinstance(key, int)
            and -len(cur) <= key < len(cur)
        ):
            cur = cur[key]
        else:
            return default
        if cur is None:
            return default
    return cur
