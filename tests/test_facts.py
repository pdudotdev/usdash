"""Model facts: models.yaml."""
import pytest

from usdash.facts import load_facts

FACTS = load_facts()


def test_where_an_effort_change_keeps_the_cache():
    # Claude Code's docs: Opus 5.5, Sonnet 5.5 and Fable 5.1, with an API key or a subscription.
    assert all(FACTS.effort_keeps_cache(m) for m in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"))
    assert not FACTS.effort_keeps_cache("claude-sonnet-5")
    assert not FACTS.effort_keeps_cache("claude-opus-5")  # the API docs say it could; Claude Code re-wrote
    assert not FACTS.effort_keeps_cache("us.anthropic.claude-opus-5-5-v1:0")  # a cloud provider


def test_tokenizers():
    assert FACTS.convert(50_000, "claude-opus-5-5", "claude-haiku-4-5") == pytest.approx(38_500)
    assert FACTS.convert(38_500, "claude-haiku-4-5", "claude-opus-5-5") == pytest.approx(50_000)
    assert FACTS.convert(50_000, "claude-opus-5-5", "claude-sonnet-5") == 50_000
    # Every model before Claude Opus 4.7 has the old tokenizer, not only Haiku 4.5.
    assert FACTS.convert(38_500, "claude-sonnet-4-6", "claude-opus-5-5") == pytest.approx(50_000)
    assert FACTS.convert(50_000, "claude-opus-4-6", "claude-haiku-4-5-20251001") == 50_000
    assert FACTS.convert(50_000, "claude-opus-4-7", "claude-opus-4-8") == 50_000
    assert FACTS.convert(50_000, "claude-opus-6", "claude-opus-5-5") == 50_000  # a new model: the current one
