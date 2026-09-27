"""What tokens cost, in USD, at Anthropic's list prices.

The prices come from Anthropic's pricing page, read at start; if it can't be
read (or doesn't read as expected), from the last copy read from it; failing
that, from pricing.yaml, which ships with usdash.

Claude Code's transcripts count a request's prompt in three parts that add
up to the whole: `input_tokens` (the uncached remainder only),
`cache_read_input_tokens` and `cache_creation_input_tokens`, the last split
into 5-minute and 1-hour writes. (LiteLLM, used by llm-trunk, counts input
differently: its "input" already includes the cached parts.)
"""
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import yaml

from .models import model_version

PRICING = Path(__file__).with_name("pricing.yaml")
PRICING_PAGE = "https://platform.claude.com/docs/en/about-claude/pricing.md"
FIVE_MINUTES, ONE_HOUR = 300, 3600
FIELDS = ("input", "cache_write", "cache_write_1h", "cache_read", "output")
# The model table's columns on the pricing page, in FIELDS' order.
PAGE_COLUMNS = ["Base input tokens", "5m cache writes", "1h cache writes", "Cache hits and refreshes", "Output tokens"]
# US-only inference (`inference_geo: "us"`) costs 1.1× on Claude 4.6 and later (the page's Data residency).
US_ONLY, US_ONLY_FROM = 1.1, (4, 6)


def load_prices(path: str | Path = PRICING) -> dict[str, dict]:
    """Model family -> list prices in USD per million tokens."""
    return yaml.safe_load(Path(path).read_text())["models"]


def prices_verified(path: str | Path = PRICING) -> str:
    return str(yaml.safe_load(Path(path).read_text()).get("verified", "?"))


# --- The pricing page ------------------------------------------------------------


def saved_prices_file() -> Path:
    """Where the prices last read from the pricing page are kept."""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "usdash" / "prices.json"


def page_model_key(name: str) -> str | None:
    """A model's name on the page -> its family key: 'Claude Opus 4.5 ([retired](…))'
    -> 'claude-opus-4-5'. Before Claude 4 the version comes first, as in
    their ids: 'Claude Haiku 3.5' -> 'claude-3-5-haiku'."""
    match = re.match(r"\s*Claude ([A-Z][a-z]+) (\d+)(?:\.(\d+))?(?![\d.])", name)
    if not match:
        return None
    family, major, minor = match.group(1).lower(), match.group(2), match.group(3)
    version = f"{major}-{minor}" if minor else major
    return f"claude-{version}-{family}" if int(major) < 4 else f"claude-{family}-{version}"


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


def parse_pricing_page(text: str) -> dict[str, dict]:
    """Anthropic's pricing page (its Markdown) -> model family -> prices, as
    in pricing.yaml, fast mode's included. Raises ValueError unless every row
    reads as expected: the same columns, and prices in line with each other
    (cache reads < input < cache writes, output ≥ input, fast ≥ standard)."""
    rows = _table(text, "## Model pricing")
    if not rows or rows[0][1:] != PAGE_COLUMNS:
        raise ValueError("the model table's columns changed")
    models = {}
    for cells in rows[1:]:
        key = page_model_key(cells[0])
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
        speed = {"input": _dollars(cells[1]), "output": _dollars(cells[2])}
        for name in cells[0].split(" / "):
            key = page_model_key(name)
            if key not in models or speed["input"] < models[key]["input"] or speed["output"] < models[key]["output"]:
                raise ValueError(f"fast mode prices out of line for {name!r}")
            models[key]["fast"] = speed
    return models


def _tls_contexts():
    """The system's certificates, then certifi's: a python.org Python on macOS
    has none of its own until its "Install Certificates" step is run."""
    yield ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return
    yield ssl.create_default_context(cafile=certifi.where())


def fetch_prices(url: str = PRICING_PAGE, timeout: float = 3.0) -> dict[str, dict] | None:
    """The prices on Anthropic's pricing page now; None if it can't be read or doesn't read right."""
    request = urllib.request.Request(url, headers={"User-Agent": "usdash"})
    for context in _tls_contexts():
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return parse_pricing_page(response.read().decode("utf-8"))
        except ssl.SSLCertVerificationError:
            continue
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                continue
            return None
        except (OSError, ValueError):
            return None
    return None


def current_prices(fetch=fetch_prices, saved: Path | None = None, bundled: Path = PRICING,
                   today: date | None = None) -> tuple[dict[str, dict], str | None]:
    """(the prices to use, the date they're from if they couldn't be read
    from the page now; None if they were). The page now, else the copy last
    read from it, else pricing.yaml, whichever is newest; a model the page no
    longer lists keeps its older price."""
    saved = saved or saved_prices_file()
    prices, as_of = load_prices(bundled), prices_verified(bundled)
    try:
        kept = json.loads(saved.read_text())
        if str(kept["date"]) >= as_of:
            prices.update(kept["models"])
            as_of = str(kept["date"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    fresh = fetch()
    if not fresh:
        return prices, as_of
    prices.update(fresh)
    try:
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(json.dumps({"date": (today or date.today()).isoformat(), "models": fresh}, indent=1))
    except OSError:
        pass
    return prices, None


# --- Costs -------------------------------------------------------------------------


def as_paid(price: dict, usage: dict, family: str | None) -> dict:
    """`price` as a request with this `usage` block paid it: fast mode's input
    and output prices, when it ran fast (cache prices keep their ratio to
    input), and ×US_ONLY for US-only inference on Claude 4.6 and later."""
    paid = dict(price)
    if usage.get("speed") == "fast" and price.get("fast"):
        scale = price["fast"]["input"] / price["input"]
        for name in ("input", "cache_write", "cache_write_1h", "cache_read"):
            paid[name] = price[name] * scale
        paid["output"] = price["fast"]["output"]
    version = model_version(family)
    if usage.get("inference_geo") == "us" and version is not None and version >= US_ONLY_FROM:
        for name in FIELDS:
            paid[name] *= US_ONLY
    return paid


def write_price(price: dict, ttl: int) -> float:
    """Per million tokens written to the cache with this lifetime."""
    return price["cache_write_1h"] if ttl >= ONE_HOUR else price["cache_write"]


def usage_parts(usage: dict) -> dict[str, int]:
    """A transcript `usage` block -> fresh / read / write_5m / write_1h / output token counts.
    Missing fields count as 0; writes without the 5m/1h split count as 5-minute."""
    def count(value) -> int:
        return int(value) if isinstance(value, (int, float)) else 0

    split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    write_1h = count(split.get("ephemeral_1h_input_tokens"))
    write_5m = count(split.get("ephemeral_5m_input_tokens"))
    unsplit = count(usage.get("cache_creation_input_tokens")) - write_1h - write_5m
    return {
        "fresh": count(usage.get("input_tokens")),
        "read": count(usage.get("cache_read_input_tokens")),
        "write_5m": write_5m + max(unsplit, 0),
        "write_1h": write_1h,
        "output": count(usage.get("output_tokens")),
    }


def request_cost(usage: dict, price: dict) -> float:
    """One request's logged tokens at one model's list prices, in USD."""
    parts = usage_parts(usage)
    return (
        parts["fresh"] * price["input"] + parts["read"] * price["cache_read"]
        + parts["write_5m"] * price["cache_write"] + parts["write_1h"] * price["cache_write_1h"]
        + parts["output"] * price["output"]
    ) / 1_000_000


def prompt_cost(price: dict, tokens: float, cached: float, ttl: int = FIVE_MINUTES) -> float:
    """The input side of one request: `cached` tokens read back from the cache,
    the rest written to it (Claude Code marks its prompts for caching)."""
    cached = min(max(cached, 0), tokens)
    return (cached * price["cache_read"] + (tokens - cached) * write_price(price, ttl)) / 1_000_000


def output_cost(price: dict, tokens: float) -> float:
    return tokens * price["output"] / 1_000_000
