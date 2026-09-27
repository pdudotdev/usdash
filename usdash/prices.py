"""What tokens cost, in USD, from the list prices in pricing.yaml.

Claude Code's transcripts count a request's prompt in three parts that add
up to the whole: `input_tokens` (the uncached remainder only),
`cache_read_input_tokens` and `cache_creation_input_tokens`, the last split
into 5-minute and 1-hour writes. (LiteLLM, used by llm-trunk, counts input
differently: its "input" already includes the cached parts.)
"""
from pathlib import Path

import yaml

PRICING = Path(__file__).with_name("pricing.yaml")
FIVE_MINUTES, ONE_HOUR = 300, 3600


def load_prices(path: str | Path = PRICING) -> dict[str, dict]:
    """Model family -> list prices in USD per million tokens."""
    return yaml.safe_load(Path(path).read_text())["models"]


def prices_verified(path: str | Path = PRICING) -> str:
    return str(yaml.safe_load(Path(path).read_text()).get("verified", "?"))


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


def hold_or_move(penalty: float, held: float) -> bool:
    """True to stay on the warm setup: moving still costs more than staying has
    (the rent-or-buy rule; research/CACHE-DECISIONS.md §5)."""
    return penalty > held
