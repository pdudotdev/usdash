"""What tokens cost, in USD, at Anthropic's list prices: from its pricing
page, read at start (docs.py), else pricing.yaml, which ships with usdash.

Claude Code's transcripts count a request's prompt in three parts that add
up to the whole: `input_tokens` (the uncached remainder only),
`cache_read_input_tokens` and `cache_creation_input_tokens`, the last split
into 5-minute and 1-hour writes. (LiteLLM, used by llm-trunk, counts input
differently: its "input" already includes the cached parts.)
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import model_version

PRICING = Path(__file__).with_name("pricing.yaml")
FIVE_MINUTES, ONE_HOUR = 300, 3600
FIELDS = ("input", "cache_write", "cache_write_1h", "cache_read", "output")
# US-only inference (`inference_geo: "us"`) costs 1.1× on Claude 4.6 and later (the page's Data residency).
US_ONLY, US_ONLY_FROM = 1.1, (4, 6)


@dataclass
class Pricing:
    """pricing.yaml, or the pricing page (docs.py): the same three things."""
    models: dict[str, dict]  # model family -> list prices in USD per million tokens
    web_search: float  # USD per server-side web search
    verified: str  # the date they were checked


def load_pricing(path: str | Path = PRICING) -> Pricing:
    data = yaml.safe_load(Path(path).read_text())
    return Pricing(data["models"], float(data["web_search"]) / 1000, str(data.get("verified", "")))


# --- Costs -------------------------------------------------------------------------


def as_paid(price: dict, family: str | None, speed: str | None = None, geo: str | None = None) -> dict | None:
    """`price` as a request paid it: fast mode's input and output prices if it
    ran fast (cache prices keep their ratio to input), and ×US_ONLY for
    US-only inference on Claude 4.6 and later. None if it ran fast and this
    model's fast mode prices aren't known: better no amount than half of it."""
    paid = dict(price)
    if speed == "fast":
        if not price.get("fast"):
            return None
        scale = price["fast"]["input"] / price["input"]
        for name in ("input", "cache_write", "cache_write_1h", "cache_read"):
            paid[name] = price[name] * scale
        paid["output"] = price["fast"]["output"]
    version = model_version(family)
    if geo == "us" and version is not None and version >= US_ONLY_FROM:
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
