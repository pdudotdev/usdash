"""Model ids: any spelling -> one family key, a short name for the screen,
and its version; and a model's name in Anthropic's docs -> its key.

Copied from llm-trunk (policy/models.py, scripts/events.py), plus Vertex ids.
"""
import re


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


def model_version(family: str | None) -> tuple[int, int] | None:
    """A family key's version: 'claude-opus-4-6' -> (4, 6), 'claude-opus-5' -> (5, 0),
    'claude-3-5-haiku' -> (3, 5); None if it has none."""
    match = re.fullmatch(r"claude-(?:[a-z]+-)?(\d+)(?:-(\d{1,2}))?(?:-[a-z]+)?", family or "")
    return (int(match.group(1)), int(match.group(2) or 0)) if match else None


def name_key(name: str) -> str | None:
    """A model's name in Anthropic's docs -> its family key: 'Claude Opus 4.5
    ([retired](…))' -> 'claude-opus-4-5'. Before Claude 4 the version comes
    first, as in their ids: 'Claude Haiku 3.5' -> 'claude-3-5-haiku'."""
    match = re.match(r"\s*Claude ([A-Z][a-z]+) (\d+)(?:\.(\d+))?(?![\d.])", name)
    if not match:
        return None
    family, major, minor = match.group(1).lower(), match.group(2), match.group(3)
    version = f"{major}-{minor}" if minor else major
    return f"claude-{version}-{family}" if int(major) < 4 else f"claude-{family}-{version}"
