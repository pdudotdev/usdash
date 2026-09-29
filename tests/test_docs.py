"""Anthropic's docs at start: read now, else the saved copy, else what ships with usdash."""
import http.client
import json
import time
from datetime import date

import pytest
from conftest import FIXTURES, PRICES

from usdash import app, docs
from usdash.prices import load_pricing

PRICING_PAGE = (FIXTURES / "pricing-page.md").read_text()  # as fetched on 2026-09-29
URLS = [url for url, _ in docs.PAGES.values()]
PAGES = {URLS[0]: PRICING_PAGE}


# --- Reading the pages -------------------------------------------------------------


def test_the_pricing_page_reads_as_the_bundled_prices():
    page = docs.parse_pricing_page(PRICING_PAGE)
    for model, price in PRICES.items():
        assert page["models"][model] == price, model  # fast mode's included
    assert page["models"]["claude-3-5-haiku"]["input"] == 0.8  # and models pricing.yaml leaves out
    assert page["web_search"] == load_pricing().web_search == 0.01  # $10 per 1,000 searches


def test_usdash_reads_the_pricing_page_only():
    assert list(docs.PAGES) == ["pricing"]


@pytest.mark.parametrize(
    ("parse", "page", "old", "new"),
    [
        (docs.parse_pricing_page, PRICING_PAGE, "Cache hits and refreshes | Output tokens",
         "Cache hits and refreshes | Output tokens (base)"),  # new columns
        (docs.parse_pricing_page, PRICING_PAGE, "$0.20 / MTok<sup>2</sup>", "$4.20 / MTok<sup>2</sup>"),  # reads > input
        (docs.parse_pricing_page, PRICING_PAGE, "| Claude Opus 5.5                 | $8 / MTok  |",
         "| Claude Opus 5.5                 | $2 / MTok  |"),  # fast mode below standard
        (docs.parse_pricing_page, PRICING_PAGE, "| Claude Opus 5.5                 | $8 / MTok  | $40 / MTok |",
         "| Claude Opus 5.5 | $8 / MTok |"),  # a short row
        (docs.parse_pricing_page, PRICING_PAGE, "### Fast mode pricing", "### Speed pricing"),
        (docs.parse_pricing_page, PRICING_PAGE, "**$10 per 1,000 searches**", "**$10 per search**"),
    ],
)
def test_a_page_that_reads_wrong_is_refused(parse, page, old, new):
    assert page.count(old) == 1
    with pytest.raises(ValueError):
        parse(page.replace(old, new))


# --- Fetching them -----------------------------------------------------------------


def test_a_reply_cut_short_is_a_page_not_read(monkeypatch):
    def cut_short(request, timeout, context):
        raise http.client.IncompleteRead(b"partial")  # not an OSError
    monkeypatch.setattr(docs.urllib.request, "urlopen", cut_short)
    assert docs.fetch_text(URLS[0]) is None


def test_the_pages_get_one_deadline_in_all():
    def hangs(url):
        time.sleep(0.5)
        return "late"
    start = time.monotonic()
    assert docs.fetch_all(hangs, URLS, deadline=0.1) == dict.fromkeys(URLS)
    assert time.monotonic() - start < 0.3


# --- Falling back ------------------------------------------------------------------


def test_pages_read_now_are_used_and_saved(tmp_path):
    saved = tmp_path / "docs.json"
    pages = docs.read_docs(PAGES.get, saved, today=date(2026, 10, 1))
    assert all(page.as_of is None and not page.changed for page in pages.values())
    assert pages["pricing"].data["models"]["claude-sonnet-5-5"] == PRICES["claude-sonnet-5-5"]
    kept = json.loads(saved.read_text())
    assert set(kept) == {"format", "pricing"} and kept["pricing"]["date"] == "2026-10-01"


def test_a_saved_page_usdash_no_longer_reads_is_dropped(tmp_path):
    saved = tmp_path / "docs.json"
    saved.write_text(json.dumps({"format": docs.SAVED_FORMAT, "models": {"date": "2026-09-27", "data": ["x"]}}))
    assert set(docs.read_docs(PAGES.get, saved)) == {"pricing"}
    assert set(json.loads(saved.read_text())) == {"format", "pricing"}


def test_unreachable_pages_fall_back_to_the_saved_copy_then_the_shipped_files(tmp_path):
    saved = tmp_path / "docs.json"
    docs.read_docs(PAGES.get, saved, today=date(2026, 10, 1))
    pages = docs.read_docs(lambda url: None, saved)
    assert pages["pricing"].as_of == "2026-10-01" and pages["pricing"].data
    # A copy older than what ships with usdash isn't used; a shipped file without a date rules nothing out.
    assert docs.read_docs(lambda url: None, saved, shipped={"pricing": "2026-12-01"})["pricing"].data is None
    assert docs.read_docs(lambda url: None, saved, shipped={"pricing": "?"})["pricing"].as_of == "2026-10-01"
    assert docs.read_docs(None, tmp_path / "none.json")["pricing"] == docs.Page(None, None)  # --offline, no copy


def test_a_page_that_changed_is_flagged_and_its_copy_used(tmp_path):
    saved = tmp_path / "docs.json"
    docs.read_docs(PAGES.get, saved, today=date(2026, 10, 1))
    redesigned = {URLS[0]: PRICING_PAGE.replace("### Fast mode pricing", "### Speed pricing")}
    pages = docs.read_docs(redesigned.get, saved)
    assert pages["pricing"].changed and pages["pricing"].as_of == "2026-10-01"
    assert docs.read_docs(PAGES.get, saved)["pricing"] == docs.Page(pages["pricing"].data, None)


def test_a_wifi_sign_in_page_is_not_a_changed_page(tmp_path):
    pages = docs.read_docs(lambda url: "<html>Sign in to the hotel Wi-Fi</html>", tmp_path / "docs.json")
    assert not any(page.changed for page in pages.values())


def test_a_copy_saved_by_an_older_usdash_is_ignored(tmp_path):
    saved = tmp_path / "docs.json"
    saved.write_text(json.dumps({"pricing": {"date": "2030-01-01", "data": {"claude-opus-5-5": {}}}}))  # no format
    assert docs.read_docs(None, saved)["pricing"] == docs.Page(None, None)


def test_offline_the_shipped_files_say_so():
    known = app.knowledge(offline=True)  # no copy saved (conftest)
    assert known.prices == PRICES and known.web_search == 0.01
    assert known.label == "API list prices of Sep 29 (offline)" and known.changed == []
