"""Model ids, prices and the cost of a logged request."""
import re

import pytest
from conftest import FIXTURES

from usdash import prices
from usdash.models import model_key, name_key, on_cloud_provider, pretty_model

PRICES = prices.load_prices()


@pytest.mark.parametrize(
    ("spelling", "key"),
    [
        ("claude-opus-5-5", "claude-opus-5-5"),
        ("anthropic/claude-haiku-4-5", "claude-haiku-4-5"),
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("claude-opus-4-6[1m]", "claude-opus-4-6"),
        ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5"),
        ("global.anthropic.claude-sonnet-5", "claude-sonnet-5"),
        ("claude-haiku-4-5@20251001", "claude-haiku-4-5"),
        (None, None),
    ],
)
def test_model_key(spelling, key):
    assert model_key(spelling) == key


def test_pretty_model():
    assert pretty_model("claude-haiku-4-5-20251001") == "Haiku 4.5"
    assert pretty_model("claude-opus-5-5") == "Opus 5.5"
    assert pretty_model("claude-opus-4-20250514") == "Opus 4"
    assert pretty_model(None) == "?"


def test_cloud_provider_ids():
    assert on_cloud_provider("us.anthropic.claude-opus-5-5-v1:0")
    assert on_cloud_provider("claude-opus-5-5@20260101")
    assert not on_cloud_provider("claude-opus-5-5")


def test_every_model_has_every_price_and_1h_writes_cost_twice_input():
    for model, price in PRICES.items():
        assert set(price) - {"fast"} == {"input", "output", "cache_read", "cache_write", "cache_write_1h"}, model
        assert price["cache_write"] == pytest.approx(price["input"] * 1.25), model
        assert price["cache_write_1h"] == pytest.approx(price["input"] * 2), model


def test_pricing_file_says_when_it_was_verified():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", prices.prices_verified())  # shown in the header


# --- The pricing page -------------------------------------------------------------

PAGE = (FIXTURES / "pricing-page.md").read_text()  # as fetched on 2026-09-27


def test_the_pricing_page_reads_as_the_bundled_prices():
    page = prices.parse_pricing_page(PAGE)
    for model, price in PRICES.items():
        assert page["models"][model] == price, model  # fast mode's included
    assert page["models"]["claude-3-5-haiku"]["input"] == 0.8  # and models pricing.yaml leaves out
    assert page["web_search"] == prices.load_web_search() == 0.01  # $10 per 1,000 searches


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Claude Opus 5.5", "claude-opus-5-5"),
        ("Claude Opus 4 ([retired, except on Google Cloud](https://…))", "claude-opus-4"),
        ("Claude Mythos 5.1 ([limited availability](https://anthropic.com/glasswing))", "claude-mythos-5-1"),
        ("Claude Haiku 3.5", "claude-3-5-haiku"),
        ("Batch API", None),
    ],
)
def test_name_key(name, key):
    assert name_key(name) == key


@pytest.mark.parametrize(
    "change",
    [
        ("Cache hits and refreshes | Output tokens", "Cache hits and refreshes | Output tokens (base)"),  # new columns
        ("$0.20 / MTok<sup>2</sup>", "$4.20 / MTok<sup>2</sup>"),  # Opus 5.5's cache reads above its input
        ("| Claude Opus 5.5                 | $8 / MTok  |", "| Claude Opus 5.5                 | $2 / MTok  |"),
        ("### Fast mode pricing", "### Speed pricing"),
        ("**$10 per 1,000 searches**", "**$10 per search**"),
    ],
)
def test_a_page_that_reads_wrong_is_refused(change):
    assert PAGE.count(change[0]) == 1
    with pytest.raises(ValueError):
        prices.parse_pricing_page(PAGE.replace(*change))



def test_fast_mode_and_us_only_inference_cost_more():
    usage = {"input_tokens": 1_000, "cache_read_input_tokens": 100_000, "output_tokens": 1_000}
    opus = PRICES["claude-opus-5-5"]
    standard = prices.request_cost(usage, prices.as_paid(opus, {**usage, "speed": "standard"}, "claude-opus-5-5"))
    fast = prices.request_cost(usage, prices.as_paid(opus, {**usage, "speed": "fast"}, "claude-opus-5-5"))
    assert standard == pytest.approx((1_000 * 4 + 100_000 * 0.2 + 1_000 * 20) / 1e6)
    assert fast == pytest.approx(2 * standard)  # $8/$40, and cache reads keep their 0.05× of input
    us = prices.request_cost(usage, prices.as_paid(opus, {**usage, "inference_geo": "us"}, "claude-opus-5-5"))
    assert us == pytest.approx(1.1 * standard)
    haiku = PRICES["claude-haiku-4-5"]  # before Claude 4.6: no US-only premium
    assert prices.as_paid(haiku, {"inference_geo": "us"}, "claude-haiku-4-5") == haiku


def test_request_cost_uses_the_transcript_convention():
    # input_tokens is the uncached remainder only; 5m and 1h writes are priced apart.
    usage = {"input_tokens": 1_000, "cache_read_input_tokens": 60_000, "cache_creation_input_tokens": 30_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 10_000, "ephemeral_1h_input_tokens": 20_000},
             "output_tokens": 2_000}
    # Opus 5.5: 1k x $4 + 60k x $0.20 + 10k x $5 + 20k x $8 + 2k x $20, per million
    assert prices.request_cost(usage, PRICES["claude-opus-5-5"]) == pytest.approx(0.266)


def test_writes_without_the_ttl_split_count_as_5_minute_writes():
    usage = {"input_tokens": 0, "cache_creation_input_tokens": 10_000, "output_tokens": 0}
    assert prices.request_cost(usage, PRICES["claude-sonnet-5"]) == pytest.approx(0.025)


def test_missing_or_odd_usage_fields_count_as_zero():
    assert prices.request_cost({"cache_creation": None, "output_tokens": "?"}, PRICES["claude-opus-5-5"]) == 0


def test_prompt_cost_matches_the_cache_lifetime():
    opus = PRICES["claude-opus-5-5"]
    assert prices.prompt_cost(opus, 50_000, 49_000) == pytest.approx(0.0148)  # 49k x $0.20 + 1k x $5
    assert prices.prompt_cost(opus, 50_000, 49_000, prices.ONE_HOUR) == pytest.approx(0.0178)  # 1k x $8
    assert prices.prompt_cost(opus, 1_000, 5_000) == pytest.approx(0.0002)  # cached can't exceed the prompt
