"""What tokens cost, in USD, at Anthropic's list prices: from its pricing
page, read at start (docs.py), else pricing.yaml, which ships with usdash.

Claude Code's transcripts count a request's prompt in three parts that add
up to the whole: `input_tokens` (the uncached remainder only),
`cache_read_input_tokens` and `cache_creation_input_tokens`, the last split
into 5-minute and 1-hour writes. (LiteLLM, used by llm-trunk, counts input
differently: its "input" already includes the cached parts.)
"""
import re
from pathlib import Path

import yaml

from .models import model_version, name_key

PRICING = Path(__file__).with_name("pricing.yaml")
FIVE_MINUTES, ONE_HOUR = 300, 3600
FIELDS = ("input", "cache_write", "cache_write_1h", "cache_read", "output")
# The model table's columns on the pricing page, in FIELDS' order.
PAGE_COLUMNS = ["Base input tokens", "5m cache writes", "1h cache writes", "Cache hits and refreshes", "Output tokens"]
# US-only inference (`inference_geo: "us"`) costs 1.1× on Claude 4.6 and later (the page's Data residency).
US_ONLY, US_ONLY_FROM = 1.1, (4, 6)


def load_prices(path: str | Path = PRICING) -> dict[str, dict]:
    """Model family -> list prices in USD per million tokens."""
    return yaml.safe_load(Path(path).read_text())["models"]


def load_web_search(path: str | Path = PRICING) -> float:
    """USD per server-side web search."""
    return float(yaml.safe_load(Path(path).read_text())["web_search"]) / 1000


def prices_verified(path: str | Path = PRICING) -> str:
    return str(yaml.safe_load(Path(path).read_text()).get("verified", "?"))


# --- The pricing page ------------------------------------------------------------


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
    """Anthropic's pricing page (its Markdown) -> {"models": model family ->
    prices, as in pricing.yaml, fast mode's included; "web_search": USD per
    search}. Raises ValueError unless every row reads as expected: the same
    columns, and prices in line with each other (cache reads < input < cache
    writes, output ≥ input, fast ≥ standard), and the search price is there."""
    rows = _table(text, "## Model pricing")
    if not rows or rows[0][1:] != PAGE_COLUMNS:
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
    """A transcript `usage` block -> fresh / read / write_5m / write_1h / output
    token counts, and server-side web searches. Missing fields count as 0;
    writes without the 5m/1h split count as 5-minute."""
    def count(value) -> int:
        return int(value) if isinstance(value, (int, float)) else 0

    split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    tools = usage.get("server_tool_use") if isinstance(usage.get("server_tool_use"), dict) else {}
    write_1h = count(split.get("ephemeral_1h_input_tokens"))
    write_5m = count(split.get("ephemeral_5m_input_tokens"))
    unsplit = count(usage.get("cache_creation_input_tokens")) - write_1h - write_5m
    return {
        "fresh": count(usage.get("input_tokens")),
        "read": count(usage.get("cache_read_input_tokens")),
        "write_5m": write_5m + max(unsplit, 0),
        "write_1h": write_1h,
        "output": count(usage.get("output_tokens")),
        "searches": count(tools.get("web_search_requests")),
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
