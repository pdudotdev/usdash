"""Model ids: any spelling -> one family key, and a short name for the screen.

Copied from llm-trunk (policy/models.py, scripts/events.py), plus Vertex ids.
"""
import re

# The same text is ~30% fewer tokens on Haiku 4.5's tokenizer than on the
# newer models' (measured 0.758 in a real run; research/CACHE-DECISIONS.md §1).
SMALL_TOKENIZER = {"claude-haiku-4-5"}
TO_SMALL_TOKENIZER = 0.77

# Where Claude Code keeps the cache across an effort change (the per-message
# effort beta). Everywhere else an effort change re-writes the conversation.
EFFORT_KEEPS_CACHE = {"claude-opus-5-5", "claude-fable-5-1"}


def model_key(model: str | None) -> str | None:
    """Any spelling of a Claude model id -> its family, e.g.
    anthropic/claude-opus-5-5[1m], claude-haiku-4-5-20251001,
    us.anthropic.claude-haiku-4-5-20251001-v1:0 and claude-haiku-4-5@20251001
    -> claude-opus-5-5 / claude-haiku-4-5."""
    if not model:
        return None
    name = model.lower().split("/")[-1].replace("[1m]", "")
    name = re.sub(r"^(?:[a-z-]+\.)?anthropic\.", "", name)  # Bedrock: us.anthropic.claude-...
    name = re.sub(r"-v\d+(?::\d+)?$", "", name)  # Bedrock: ...-v1:0
    name = re.sub(r"@\d{8}$", "", name)  # Vertex: ...@20251001
    return re.sub(r"-\d{8}$", "", name)  # dated snapshot: ...-20251001


def on_cloud_provider(model: str | None) -> bool:
    """Bedrock and Vertex spell model ids their own way; there, an effort
    change re-writes the cache on every model."""
    return bool(model) and ("anthropic." in model or "@" in model)


def pretty_model(model_id: str | None) -> str:
    """claude-haiku-4-5-20251001 -> Haiku 4.5; anything else as-is."""
    if not model_id:
        return "?"
    name = model_key(model_id) or model_id
    # The minor version is 1-2 digits, so a trailing -YYYYMMDD date isn't
    # mistaken for one ("claude-opus-4-20250514" -> "Opus 4").
    match = re.fullmatch(r"claude-([a-z]+)-(\d+(?:-\d{1,2})?)", name)
    if not match:
        return name
    return f"{match.group(1).capitalize()} {match.group(2).replace('-', '.')}"


def convert_tokens(tokens: float, source: str | None, target: str | None) -> float:
    """A token count on one model's tokenizer -> roughly the same text on another's."""
    small_source, small_target = source in SMALL_TOKENIZER, target in SMALL_TOKENIZER
    if small_source == small_target:
        return tokens
    return tokens * TO_SMALL_TOKENIZER if small_target else tokens / TO_SMALL_TOKENIZER
