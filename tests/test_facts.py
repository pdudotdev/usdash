"""Model facts: models.yaml, Anthropic's docs, and what this machine's transcripts show."""
import pytest
from conftest import FIXTURES, T0, Transcript

from usdash.facts import load_facts, parse_effort_models, parse_lineup

FACTS = load_facts()
MODELS_PAGE = (FIXTURES / "models-page.md").read_text()  # as fetched on 2026-09-27
EFFORT_PAGE = (FIXTURES / "effort-page.md").read_text()
ALIASES = next(line for line in MODELS_PAGE.splitlines() if line.startswith("| Claude API alias"))


def test_the_docs_name_the_lineup_and_where_effort_can_keep_the_cache():
    assert parse_lineup(MODELS_PAGE) == FACTS.lineup == [
        "claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"]
    assert parse_effort_models(EFFORT_PAGE) == {
        "claude-fable-5-1", "claude-mythos-5-1", "claude-opus-5-5", "claude-opus-5"}


@pytest.mark.parametrize(
    ("parse", "page", "old", "new"),
    [
        (parse_lineup, MODELS_PAGE, "| Claude API alias", "| Model alias"),  # the row is gone
        (parse_lineup, MODELS_PAGE, ALIASES, ALIASES.replace("claude-sonnet-5", "claude-sonnet-6")),  # name ≠ alias
        (parse_effort_models, EFFORT_PAGE, "use a per-message effort change, which keeps", "use per-message effort, which keeps"),
    ],
)
def test_a_page_that_reads_wrong_is_refused(parse, page, old, new):
    assert page.count(old) == 1
    with pytest.raises(ValueError):
        parse(page.replace(old, new))


def test_where_an_effort_change_keeps_the_cache():
    assert FACTS.effort_keeps_cache("claude-opus-5-5") and not FACTS.effort_keeps_cache("claude-sonnet-5")
    assert not FACTS.effort_keeps_cache("us.anthropic.claude-opus-5-5-v1:0")  # a cloud provider
    # Where the API can't keep it, neither can Claude Code.
    assert not FACTS.with_docs(None, {"claude-fable-5-1"}).effort_keeps_cache("claude-opus-5-5")
    seen = FACTS.with_seen({"claude-opus-5": (2, 0), "claude-opus-5-5": (0, 2), "claude-fable-5-1": (1, 1)}, [])
    assert seen.effort_keeps_cache("claude-opus-5")  # kept it twice
    assert not seen.effort_keeps_cache("claude-opus-5-5")  # re-wrote twice
    assert seen.effort_keeps_cache("claude-fable-5-1")  # the transcripts disagree: models.yaml
    assert not FACTS.with_seen({"claude-opus-5": (1, 0)}, []).effort_keeps_cache("claude-opus-5")  # once isn't enough


def test_tokenizers():
    assert FACTS.convert(50_000, "claude-opus-5-5", "claude-haiku-4-5") == pytest.approx(38_500)
    assert FACTS.convert(38_500, "claude-haiku-4-5", "claude-opus-5-5") == pytest.approx(50_000)
    assert FACTS.convert(50_000, "claude-opus-5-5", "claude-sonnet-5") == 50_000
    # Every model before Claude Opus 4.7 has the old tokenizer, not only Haiku 4.5.
    assert FACTS.convert(38_500, "claude-sonnet-4-6", "claude-opus-5-5") == pytest.approx(50_000)
    assert FACTS.convert(50_000, "claude-opus-4-6", "claude-haiku-4-5-20251001") == 50_000
    assert FACTS.convert(50_000, "claude-opus-4-7", "claude-opus-4-8") == 50_000
    assert FACTS.convert(50_000, "claude-opus-6", "claude-opus-5-5") == 50_000  # a new model: the current one
    assert FACTS.with_seen({}, [0.74, 0.75, 0.76]).convert(50_000, "claude-opus-5-5", "claude-haiku-4-5") == 37_500


def test_the_transcripts_teach_effort_and_the_tokenizer_ratio(store):
    # Opus 5 isn't in models.yaml, but here two effort changes read the whole conversation back.
    t = Transcript()
    t.turn(T0, model="claude-opus-5", effort="high", write=40_000)
    t.turn(T0 + 60, model="claude-opus-5", effort="medium", read=40_002, write=1_000)
    t.turn(T0 + 120, model="claude-opus-5", effort="high", read=41_002, write=1_000)
    # Opus 5.5 -> Haiku 4.5 -> Opus 5.5: the same conversation in both tokenizers.
    u = Transcript(session="sess-2")
    u.turn(T0, write=50_000)
    u.turn(T0 + 60, model="claude-haiku-4-5", effort=None, write=38_000)
    u.turn(T0 + 120, write=51_000)
    t.into(store)
    u.into(store)
    assert not store.base_facts.effort_keeps_cache("claude-opus-5")
    assert store.facts.effort_seen["claude-opus-5"] == (2, 0) and store.facts.effort_keeps_cache("claude-opus-5")
    assert store.facts.ratio == pytest.approx((38_002 / 50_002 + 38_002 / 51_002) / 2)
