"""Anthropic's docs at start: read now, else the saved copy, else what ships with usdash."""
import json
from datetime import date

from conftest import FIXTURES

from usdash import app, docs
from usdash.facts import load_facts
from usdash.prices import load_prices

URLS = [url for url, _ in docs.PAGES.values()]
PAGES = dict(zip(URLS, ((FIXTURES / name).read_text() for name in ("pricing-page.md", "models-page.md", "effort-page.md"))))


def test_pages_read_now_are_used_and_saved(tmp_path):
    saved = tmp_path / "docs.json"
    pages = docs.read_docs(PAGES.get, saved, today=date(2026, 10, 1))
    assert all(page.as_of is None and not page.changed for page in pages.values())
    assert pages["models"].data == load_facts().lineup
    assert all(pages["pricing"].data["models"][model] == price for model, price in load_prices().items())
    kept = json.loads(saved.read_text())
    assert set(kept) == {"format", "pricing", "models", "effort"} and kept["effort"]["date"] == "2026-10-01"


def test_unreachable_pages_fall_back_to_the_saved_copy_then_the_shipped_files(tmp_path):
    saved = tmp_path / "docs.json"
    docs.read_docs(PAGES.get, saved, today=date(2026, 10, 1))
    pages = docs.read_docs(lambda url: None, saved)
    assert pages["pricing"].as_of == "2026-10-01" and pages["pricing"].data
    # A copy older than what ships with usdash isn't used.
    pages = docs.read_docs(lambda url: None, saved, shipped={"pricing": "2026-12-01"})
    assert pages["pricing"].data is None and pages["models"].as_of == "2026-10-01"
    assert docs.read_docs(None, tmp_path / "none.json")["effort"] == docs.Page(None, None)  # --offline, no copy


def test_a_page_that_changed_is_flagged_and_its_copy_used(tmp_path):
    saved = tmp_path / "docs.json"
    docs.read_docs(PAGES.get, saved, today=date(2026, 10, 1))
    redesigned = {**PAGES, URLS[1]: "# Models\n\nA page usdash can't read."}
    pages = docs.read_docs(redesigned.get, saved)
    assert pages["models"].changed and pages["models"].as_of == "2026-10-01"
    assert not pages["pricing"].changed and pages["pricing"].as_of is None


def test_offline_the_shipped_files_say_so(monkeypatch):
    known = app.knowledge(offline=True)  # no copy saved (conftest)
    assert known.prices == load_prices() and known.web_search == 0.01
    assert known.facts.lineup == load_facts().lineup and known.facts.effort_possible is None
    assert known.label == "API list prices of Sep 26 (offline)" and known.changed == []


def test_a_copy_saved_by_an_older_usdash_is_ignored(tmp_path):
    saved = tmp_path / "docs.json"
    saved.write_text(json.dumps({"pricing": {"date": "2030-01-01", "data": {"claude-opus-5-5": {}}}}))  # no format
    assert docs.read_docs(None, saved)["pricing"] == docs.Page(None, None)
