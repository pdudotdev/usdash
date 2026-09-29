"""The cost engine: the conversation's size, its cache clock, and what re-sending it costs."""
import pytest
from conftest import T0, Transcript

from usdash import engine
from usdash.prices import FIVE_MINUTES, ONE_HOUR


# --- A session's cache clock, and what re-sending its conversation costs ------------------


def opus_session(store, prompts=(40_000, 41_000, 42_000), out=500, ttl="1h", session="sess-1", **reply):
    t = Transcript(session=session)
    previous = 0
    for i, prompt in enumerate(prompts):
        t.turn(T0 + 60 * i, read=previous, write=prompt - previous - 2, out=out, ttl=ttl, **reply)
        previous = prompt
    t.into(store)
    return store.sessions[session]


def test_the_cache_clock_counts_down_from_the_last_request_start(store):
    session = opus_session(store)
    last_start = T0 + 120
    assert engine.cache_clock(session, last_start + 60) == (True, ONE_HOUR - 60, ONE_HOUR)
    assert engine.cache_clock(session, last_start + ONE_HOUR) == (False, 0, ONE_HOUR)


def test_five_minute_sessions_expire_in_five_minutes(store):
    session = opus_session(store, ttl="5m")
    assert engine.cache_clock(session, T0 + 120 + 299)[0]
    assert not engine.cache_clock(session, T0 + 120 + 300)[0]


def test_now_reads_the_last_prompt_back_and_up_to_writes_it_again(store):
    session = opus_session(store)  # last prompt 42,000 tokens, 1-hour cache
    assert engine.context(session) == 42_000
    now, up_to = engine.resend_costs(store, session, T0 + 180)
    assert now == pytest.approx(42_000 * 0.20 / 1e6)
    assert up_to == pytest.approx(42_000 * 8 / 1e6)  # the 1-hour write price
    # Once the cache has expired there's no "now": only the upper bound.
    assert engine.resend_costs(store, session, T0 + 120 + ONE_HOUR) == (None, pytest.approx(42_000 * 8 / 1e6))


def test_a_five_minute_cache_is_written_again_at_the_five_minute_price(store):
    session = opus_session(store, ttl="5m")
    assert engine.resend_costs(store, session, T0 + 150) == (
        pytest.approx(42_000 * 0.20 / 1e6), pytest.approx(42_000 * 5 / 1e6))


def test_fast_mode_and_us_only_price_what_comes_next_at_their_rates(store):
    fast = opus_session(store, speed="fast")  # fast mode: twice the prices, cache ones included
    assert engine.resend_costs(store, fast, T0 + 150) == (
        pytest.approx(42_000 * 0.40 / 1e6), pytest.approx(42_000 * 16 / 1e6))
    us = opus_session(store, session="sess-2", geo="us")  # US-only: ×1.1
    assert engine.resend_costs(store, us, T0 + 150) == (
        pytest.approx(42_000 * 0.22 / 1e6), pytest.approx(42_000 * 8.8 / 1e6))


def test_no_price_no_amounts(store):
    session = opus_session(store, model="claude-opus-9")
    assert engine.context(session) == 42_000
    assert engine.resend_costs(store, session, T0 + 150) is None


def test_after_compact_the_size_is_unknown_until_the_next_request(store):
    session = opus_session(store)
    t = Transcript()
    t.record("system", T0 + 150, subtype="compact_boundary", compactMetadata={"postTokens": 2_500, "preTokens": 43_000})
    t.into(store)
    assert engine.context(session) is None  # not tool list + postTokens: that was 14–66% short
    assert engine.resend_costs(store, session, T0 + 160) is None
    t.turn(T0 + 200, read=36_000, write=10_000)  # the next request measures it
    t.into(store)
    assert engine.context(session) == 46_002


def test_compact_restarts_the_cache_clock_from_when_it_started(store):
    session = opus_session(store, ttl="5m")  # last request at T0 + 120
    t = Transcript()
    # Its request isn't logged; the boundary is written when it ends, 20 seconds after it started.
    t.record("system", T0 + 380, subtype="compact_boundary",
             compactMetadata={"postTokens": 2_500, "preTokens": 43_000, "durationMs": 20_000})
    t.into(store)
    assert engine.cache_clock(session, T0 + 450) == (True, 360 + FIVE_MINUTES - 450, FIVE_MINUTES)
    assert not engine.cache_clock(session, T0 + 360 + FIVE_MINUTES)[0]


# --- The session's own tool list: what tells a real cache miss from a normal read ---------


def test_the_tool_list_is_measured_right_after_compact(store):
    t = Transcript()
    t.turn(T0, write=40_000)
    t.record("system", T0 + 30, subtype="compact_boundary", compactMetadata={"postTokens": 4_000, "preTokens": 41_000})
    t.turn(T0 + 60, read=36_829, write=4_500)  # the tool list and system prompt, read back
    t.into(store)
    session = store.sessions["sess-1"]
    assert store.tool_list(session, "claude-opus-5-5") == 36_829  # not the 40,002-token first prompt
    assert store.tool_list(session, "claude-haiku-4-5") == pytest.approx(36_829 * 0.77)
    # Another session doesn't borrow it: only what a session measured itself counts.
    other = opus_session(store, session="sess-2")
    assert store.tool_list(other, "claude-opus-5-5") == 0


def test_a_read_bigger_than_the_first_prompt_is_not_the_tool_list(store):
    big = Transcript(session="sess-3")  # a forked conversation reads back more than the tool list
    big.turn(T0, write=40_000)
    big.record("system", T0 + 30, subtype="compact_boundary", compactMetadata={"postTokens": 4_000})
    big.turn(T0 + 60, read=90_000, write=500)
    big.into(store)
    assert store.sessions["sess-3"].tool_list is None


def test_the_tool_list_is_measured_after_a_switch_from_haiku(store):
    # The first prompt was counted on Haiku's smaller tokenizer; the read after
    # /compact on Opus is the same tool list in Opus tokens, so it's bigger.
    t = Transcript()
    t.turn(T0, model="claude-haiku-4-5", write=30_800)
    t.turn(T0 + 60, write=60_000)
    t.record("system", T0 + 90, subtype="compact_boundary", compactMetadata={"postTokens": 4_000})
    t.turn(T0 + 120, read=36_829, write=4_500)
    t.into(store)
    assert store.sessions["sess-1"].tool_list == (36_829, "claude-opus-5-5")


def test_the_tool_list_is_what_a_cold_start_reads_back(store):
    # A new session's first request reads back the tool list another session keeps cached.
    t = Transcript()
    t.turn(T0, read=24_856, write=9_910)
    t.into(store)
    session = store.sessions["sess-1"]
    assert store.tool_list(session, "claude-opus-5-5") == 24_856  # not its 34,768-token first prompt
    # Back after the cache expired: what it reads back then is the latest measure.
    t.turn(T0 + ONE_HOUR + 60, read=23_308, write=12_000)
    t.into(store)
    assert store.tool_list(session, "claude-opus-5-5") == 23_308
    # Within the cache lifetime nothing is cold: a partial read then isn't the tool list.
    t.turn(T0 + ONE_HOUR + 120, read=30_000, write=5_310)
    t.into(store)
    assert store.tool_list(session, "claude-opus-5-5") == 23_308
    # A session that found nothing cached has measured nothing.
    cold = Transcript(session="sess-2")
    cold.turn(T0 + 2 * ONE_HOUR, write=35_000)
    cold.into(store)
    assert store.tool_list(store.sessions["sess-2"], "claude-opus-5-5") == 0
