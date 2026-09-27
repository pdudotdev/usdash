"""Anthropic's docs, read once at start: the prices, the current models, and
where an effort change can keep the cache.

Each page is fetched as Markdown (plain HTTP, no model involved) and parsed
strictly: a page that doesn't read as expected is refused, not half-used.
What was read is saved; a page that can't be fetched or read falls back to
its saved copy, if that's newer than what ships with usdash, else to the
shipped files (pricing.yaml, models.yaml).
"""
import json
import os
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .facts import parse_effort_models, parse_lineup
from .prices import parse_pricing_page

DOCS = "https://platform.claude.com/docs/en"
PAGES = {  # name -> (Markdown URL, parser)
    "pricing": (f"{DOCS}/about-claude/pricing.md", parse_pricing_page),
    "models": (f"{DOCS}/about-claude/models/overview.md", parse_lineup),
    "effort": (f"{DOCS}/build-with-claude/effort.md", lambda text: sorted(parse_effort_models(text))),
}
TIMEOUT = 3.0  # seconds, for each page (they're read at the same time)


@dataclass
class Page:
    data: object | None  # what the page says, parsed; None if neither it nor a copy could be read
    as_of: str | None  # None if read now, else the date of the saved copy used
    changed: bool = False  # fetched, but it no longer reads as expected: usdash may need an update


def saved_docs_file() -> Path:
    """Where what was last read from each page is kept."""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "usdash" / "docs.json"


def _tls_contexts():
    """The system's certificates, then certifi's: a python.org Python on macOS
    has none of its own until its "Install Certificates" step is run."""
    yield ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return
    yield ssl.create_default_context(cafile=certifi.where())


def fetch_text(url: str, timeout: float = TIMEOUT) -> str | None:
    """A page's text, or None if it can't be fetched."""
    request = urllib.request.Request(url, headers={"User-Agent": "usdash"})
    for context in _tls_contexts():
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return response.read().decode("utf-8")
        except ssl.SSLCertVerificationError:
            continue
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                continue
            return None
        except (OSError, ValueError):
            return None
    return None


def read_docs(fetch=fetch_text, saved: Path | None = None, shipped: dict[str, str] | None = None,
              today: date | None = None) -> dict[str, Page]:
    """Every page in PAGES, read now if possible, else from its saved copy,
    if that's no older than what ships with usdash (`shipped`: page -> date).
    `fetch` is None to read nothing (--offline)."""
    saved = saved or saved_docs_file()
    try:
        kept = json.loads(saved.read_text())
        kept = kept if isinstance(kept, dict) else {}
    except (OSError, ValueError):
        kept = {}
    texts: dict[str, str | None] = dict.fromkeys(PAGES)
    if fetch is not None:
        with ThreadPoolExecutor(len(PAGES)) as pool:
            texts = dict(zip(PAGES, pool.map(fetch, (url for url, _ in PAGES.values()))))
    pages, fresh = {}, {}
    for name, (_, parse) in PAGES.items():
        changed = False
        if texts[name] is not None:
            try:
                pages[name] = Page(parse(texts[name]), None)
                fresh[name] = {"date": (today or date.today()).isoformat(), "data": pages[name].data}
                continue
            except ValueError:
                changed = True
        copy = kept.get(name)
        if isinstance(copy, dict) and "data" in copy and str(copy.get("date", "")) >= (shipped or {}).get(name, ""):
            pages[name] = Page(copy["data"], str(copy["date"]), changed)
        else:
            pages[name] = Page(None, None, changed)
    if fresh:
        try:
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_text(json.dumps({**kept, **fresh}, indent=1))
        except OSError:
            pass
    return pages
