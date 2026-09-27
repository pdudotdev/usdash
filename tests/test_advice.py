"""Advice lines: when each rule speaks up, and what it says."""
import pytest
from conftest import T0, Transcript

from usdash import advice, engine
from usdash.prices import FIVE_MINUTES, ONE_HOUR


def session_on(store, model="claude-opus-5-5", prompts=(40_000, 41_000, 42_000), out=500, ttl="1h", effort="high",
               session="sess-1"):
    t = Transcript(session=session)
    previous = 0
    for i, prompt in enumerate(prompts):
        t.turn(T0 + 60 * i, model=model, effort=effort, read=previous, write=prompt - previous - 2, out=out, ttl=ttl)
        previous = prompt
    t.into(store)
    return store.sessions[session]


LAST_START = T0 + 120


def test_warm_opus_says_what_switching_costs_now_and_that_it_is_free_later(store):
    session = session_on(store, out=2_000)
    (tip,) = [a for a in advice.advise(store, session, LAST_START + 600) if a.kind == "switch"]
    assert tip.text.startswith("Warm on Opus 5.5 for 50:00 more. Switching to Sonnet 5 now costs +$")
    assert "pays back after ~" in tip.text and tip.text.endswith("after that it's free.")


def test_cold_cache_says_switching_costs_nothing_extra(store):
    session = session_on(store, out=2_000)
    (tip,) = [a for a in advice.advise(store, session, LAST_START + ONE_HOUR + 1) if a.kind == "switch"]
    assert tip.text.startswith("Cache expired, so switching model costs nothing extra now: next message ≈$")
    assert "on Sonnet 5" in tip.text and "on Haiku 4.5" in tip.text and "on Opus 5.5." in tip.text


def test_switch_now_when_the_target_already_has_it_cheaper(store):
    session = session_on(store, ttl="5m", out=1_000)
    # Right after /compact, staying re-writes all but the tool list; Haiku has the tool list cached.
    other = Transcript(session="sess-2")
    other.turn(T0 + 100, model="claude-haiku-4-5", write=35_000, ttl="5m")
    other.into(store)
    compact = Transcript()
    compact.record("system", T0 + 125, subtype="compact_boundary", compactMetadata={"postTokens": 3_000})
    compact.into(store)
    (tip,) = [a for a in advice.advise(store, session, T0 + 130) if a.kind == "switch"]
    assert tip.text.startswith("Switch to Haiku 4.5 now: already cheaper")
    assert tip.urgent


def test_rent_or_buy_counts_from_when_the_tip_first_appeared(store):
    memory = advice.Memory()
    session = session_on(store, out=3_000)
    first = advice.switch_advice(store, session, LAST_START + 10, memory)
    assert first.text.startswith("Warm on Opus 5.5")
    # The user keeps going on Opus: every message costs more than on Sonnet.
    t = Transcript()
    prompt = 42_000
    for i in range(1, 40):
        t.turn(LAST_START + 20 * i, read=prompt, write=1_000, out=3_000)
        prompt += 1_000
    t.into(store)
    later = advice.switch_advice(store, session, LAST_START + 20 * 40, memory)
    assert later.text.startswith("Switching to Sonnet 5 pays off now: since this tip appeared, staying on Opus 5.5 cost $")
    # Without the memory, the count starts now: no history yet.
    fresh = advice.switch_advice(store, session, LAST_START + 20 * 40, advice.Memory())
    assert fresh.text.startswith("Warm on Opus 5.5")


def test_no_switch_advice_on_the_cheapest_model(store):
    session = session_on(store, model="claude-haiku-4-5")
    assert advice.switch_advice(store, session, LAST_START + 10, advice.Memory()) is None


def test_compact_advice_only_when_big_warm_and_about_to_expire(store):
    session = session_on(store, prompts=(137_000, 138_000, 139_000))
    memory = advice.Memory()
    assert advice.compact_advice(store, session, LAST_START + 60, memory) is None  # 59 min left
    tip = advice.compact_advice(store, session, LAST_START + ONE_HOUR - 300, memory)
    assert tip.text == "Context 140k, cache expires in 5:00: /compact now ≈$0.093; after a break ≈$0.760."
    assert tip.urgent
    assert advice.compact_advice(store, session, LAST_START + ONE_HOUR + 1, memory) is None  # already cold
    small = session_on(store, session="sess-2")
    assert advice.compact_advice(store, small, LAST_START + ONE_HOUR - 300, memory) is None


def test_compact_advice_on_a_five_minute_cache(store):
    session = session_on(store, prompts=(137_000, 138_000, 139_000), ttl="5m")
    assert advice.compact_advice(store, session, LAST_START + 60, advice.Memory()) is None
    assert advice.compact_advice(store, session, LAST_START + FIVE_MINUTES - 120, advice.Memory())


def test_effort_advice_on_opus_5_5(store):
    session = session_on(store, out=3_000)
    medium = Transcript(session="sess-2")
    medium.turn(T0, effort="medium", write=40_000, out=800)
    medium.into(store)
    tip = advice.effort_advice(store, session, LAST_START + 10, advice.Memory())
    assert tip.text == "Lower /effort to medium: ≈$0.044 less per message, no cache cost on Opus 5.5."


def test_no_effort_advice_where_it_would_rewrite_the_cache(store):
    session = session_on(store, model="claude-sonnet-5", out=3_000)
    medium = Transcript(session="sess-2")
    medium.turn(T0, model="claude-sonnet-5", effort="medium", write=40_000, out=800)
    medium.into(store)
    assert advice.effort_advice(store, session, LAST_START + 10, advice.Memory()) is None


def test_closed_sessions_get_no_advice(store):
    session = session_on(store, out=2_000)
    closing = Transcript()
    closing.record("cost-state", totalCostUSD=1.0)
    closing.into(store)
    assert advice.advise(store, session, LAST_START + 60) == []


def test_money_and_clock():
    assert advice.money(0.0415) == "$0.042" and advice.money(-1.5) == "$1.50"
    assert advice.clock(3125) == "52:05" and advice.clock(3600) == "1h00m"


def test_effort_advice_skips_a_level_that_saves_too_little(store):
    session = session_on(store, out=3_000)
    other = Transcript(session="sess-2")
    other.turn(T0, effort="medium", write=40_000, out=2_950)  # saves $0.001 per message
    other.turn(T0 + 60, effort="low", read=40_000, write=500, out=800)  # saves $0.044
    other.into(store)
    tip = advice.effort_advice(store, session, LAST_START + 10, advice.Memory())
    assert tip.text.startswith("Lower /effort to low: ≈$0.044 less per message")


def test_a_model_switch_mid_session_does_not_count_as_the_tool_list(store):
    # Session A in the folder: 120k on Opus, then /model haiku (Haiku writes it all, ~92k).
    a = Transcript(session="sess-a")
    a.turn(T0, write=40_000, ttl="5m")
    a.turn(T0 + 30, read=40_000, write=80_000, ttl="5m")
    a.turn(T0 + 60, model="claude-haiku-4-5", write=92_000, ttl="5m")
    a.into(store)
    first_prompt = 40_002
    assert store.sessions["sess-a"].prefix == first_prompt
    # Haiku has A's tool list cached (A's first prompt, in Haiku's tokens), not A's whole conversation.
    assert store.shared_prefix(store.sessions["sess-a"], "claude-haiku-4-5", T0 + 90) == round(first_prompt * 0.77)
    # Session B, warm on Opus at 50k: moving it to Haiku reads only the tool list
    # back and writes the rest. (Counting A's whole Haiku conversation as cached
    # priced all of B's ~42k Haiku tokens as cheap reads.)
    b = session_on(store, prompts=(40_000, 45_000, 50_000), ttl="5m", session="sess-b", out=500)
    move = engine.model_move(store, b, "claude-haiku-4-5", LAST_START + 30)
    size = 55_000 * 0.77  # the next prompt, in Haiku's tokens
    shared = round(40_000 * 0.77)  # the smaller first prompt: B's 40,000, not A's 40,002
    expected = (shared * 0.10 + (size - shared) * 1.25 + 500 * 0.77 * 5) / 1e6  # B's 500-token replies, in Haiku's tokens
    assert move.move == pytest.approx(expected)
