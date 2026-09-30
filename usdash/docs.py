"""Anthropic's docs, read once at start: the pricing page.

Each page is fetched as Markdown (plain HTTP, no model involved) and parsed
strictly: a page that doesn't read as expected is refused, not half-used.
What was read is saved; a page that can't be fetched or read falls back to
its saved copy, if that's no older than what ships with usdash, else to the
shipped pricing.yaml.
"""
import http.client
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import certifi

from .models import name_key
from .prices import FIELDS

DOCS = "https://platform.claude.com/docs/en"
TIMEOUT = 3.0  # seconds for each network step
DEADLINE = 4.0  # seconds for all the pages together: after that, usdash starts without the rest
SAVED_FORMAT = 2  # bumped when a page's parsed shape changes: older copies are ignored
# The model table's columns on the pricing page, in FIELDS' order.
PRICE_COLUMNS = ["Base input tokens", "5m cache writes", "1h cache writes", "Cache hits and refreshes", "Output tokens"]


# --- Reading the pages -------------------------------------------------------------


def _table(text: str, heading: str) -> list[list[str]]:
    """The cells of the first Markdown table under `heading`, its column names first."""
    at = text.find(f"\n{heading}\n")
    if at < 0:
        raise ValueError(f"no {heading!r} section")
    rows: list[list[str]] = []
    for line in text[at + len(heading) + 2:].splitlines():
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not all(set(cell) <= set(":- ") for cell in cells):  # not the |:---| line
                rows.append(cells)
        elif rows:
            break
    return rows


def _dollars(cell: str) -> float:
    match = re.fullmatch(r"\$(\d+(?:\.\d+)?) / MTok(?:<sup>\d+</sup>)?", cell)
    if not match:
        raise ValueError(f"not a price: {cell!r}")
    return float(match.group(1))


def parse_pricing_page(text: str) -> dict:
    """The pricing page -> {"models": model family -> prices, as in
    pricing.yaml, fast mode's included; "web_search": USD per search}.
    Raises ValueError unless every row reads as expected: the same columns,
    prices in line with each other (cache reads < input < cache writes,
    output ≥ input, fast ≥ standard), and the search price is there."""
    rows = _table(text, "## Model pricing")
    if not rows or rows[0][1:] != PRICE_COLUMNS:
        raise ValueError("the model table's columns changed")
    models = {}
    for cells in rows[1:]:
        key = name_key(cells[0])
        if key is None or len(cells) != len(FIELDS) + 1:
            raise ValueError(f"unexpected row: {cells[0]!r}")
        price = dict(zip(FIELDS, map(_dollars, cells[1:])))
        if not (0 < price["cache_read"] < price["input"] < price["cache_write"] <= price["cache_write_1h"]
                and price["output"] >= price["input"]):
            raise ValueError(f"prices out of line for {key}")
        models[key] = price
    if len(models) < 5:
        raise ValueError("too few models")
    fast = _table(text, "### Fast mode pricing")
    if not fast or fast[0] != ["Model", "Input", "Output"]:
        raise ValueError("the fast mode table's columns changed")
    for cells in fast[1:]:
        if len(cells) != 3:
            raise ValueError(f"unexpected fast mode row: {cells[0]!r}")
        speed = {"input": _dollars(cells[1]), "output": _dollars(cells[2])}
        for name in cells[0].split(" / "):
            key = name_key(name)
            if key not in models or speed["input"] < models[key]["input"] or speed["output"] < models[key]["output"]:
                raise ValueError(f"fast mode prices out of line for {name!r}")
            models[key]["fast"] = speed
    search = re.search(r"Web search is available on the Claude API for \*\*\$(\d+(?:\.\d+)?) per 1,000 searches\*\*", text)
    if not search:
        raise ValueError("no web search price")
    return {"models": models, "web_search": float(search.group(1)) / 1000}


PAGES = {  # name -> (Markdown URL, parser)
    "pricing": (f"{DOCS}/about-claude/pricing.md", parse_pricing_page),
}


def is_docs(text: str) -> bool:
    """Whether this is a page of Anthropic's docs at all (their front matter),
    not a Wi-Fi sign-in page or a proxy's answer in its place."""
    head = text[:600]
    return head.startswith("---\n") and "\nurl: https://platform.claude.com/docs/" in head


# --- Fetching them -----------------------------------------------------------------


def fetch_text(url: str, timeout: float = TIMEOUT) -> str | None:
    """A page's text, or None if it can't be fetched. Checked against the
    system's certificates, then certifi's: a python.org Python on macOS has
    none of its own until its "Install Certificates" step is run."""
    request = urllib.request.Request(url, headers={"User-Agent": "usdash"})
    for context in (ssl.create_default_context(), ssl.create_default_context(cafile=certifi.where())):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return response.read().decode("utf-8")
        except ssl.SSLCertVerificationError:
            continue
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                continue
            return None
        except (OSError, ValueError, http.client.HTTPException):  # a reply cut short, a bad status line, …
            return None
    return None


def fetch_all(fetch, urls: list[str], deadline: float = DEADLINE) -> dict[str, str | None]:
    """Every URL fetched at once, waiting at most `deadline` seconds in all:
    a lookup that hangs (DNS has no timeout of its own) can't hold up the start."""
    results: dict[str, str | None] = dict.fromkeys(urls)

    def one(url: str) -> None:
        results[url] = fetch(url)

    threads = [threading.Thread(target=one, args=(url,), daemon=True) for url in urls]
    for thread in threads:
        thread.start()
    end = time.monotonic() + deadline
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    return dict(results)  # a copy: a fetch that finishes late changes nothing


# --- Falling back ------------------------------------------------------------------


@dataclass
class Page:
    data: object | None  # what the page says, parsed; None if neither it nor a copy could be read
    as_of: str | None  # None if read now, else the date of the saved copy used
    changed: bool = False  # it's Anthropic's page, but it no longer reads as expected: usdash may need an update


def saved_docs_file() -> Path:
    """Where what was last read from each page is kept."""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "usdash" / "docs.json"


def _is_date(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def read_docs(fetch=fetch_text, saved: Path | None = None, shipped: dict[str, str] | None = None,
              today: date | None = None) -> dict[str, Page]:
    """Every page in PAGES, read now if possible, else from its saved copy,
    if that's no older than what ships with usdash (`shipped`: page -> date).
    `fetch` is None to read nothing."""
    saved = saved or saved_docs_file()
    try:
        kept = json.loads(saved.read_text())
        kept = kept if isinstance(kept, dict) and kept.get("format") == SAVED_FORMAT else {}
    except (OSError, ValueError):
        kept = {}
    urls = [url for url, _ in PAGES.values()]
    texts = fetch_all(fetch, urls) if fetch is not None else dict.fromkeys(urls)
    pages, fresh = {}, {}
    for name, (url, parse) in PAGES.items():
        text, changed = texts[url], False
        if text is not None and is_docs(text):
            try:
                pages[name] = Page(parse(text), None)
                fresh[name] = {"date": (today or date.today()).isoformat(), "data": pages[name].data}
                continue
            except ValueError:
                changed = True
        copy, floor = kept.get(name), (shipped or {}).get(name, "")
        floor = floor if _is_date(floor) else ""  # a shipped file without a date doesn't rule out copies
        if isinstance(copy, dict) and "data" in copy and _is_date(copy.get("date")) and copy["date"] >= floor:
            pages[name] = Page(copy["data"], copy["date"], changed)
        else:
            pages[name] = Page(None, None, changed)
    if fresh:
        current = {name: kept[name] for name in PAGES if name in kept}  # pages usdash no longer reads are dropped
        try:
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_text(json.dumps({**current, **fresh, "format": SAVED_FORMAT}, indent=1))
        except OSError:
            pass
    return pages
