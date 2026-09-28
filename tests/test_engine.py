"""The cost engine: the conversation's size, its cache clock, what re-sending it costs on each model, /compact."""
import pytest
from conftest import PRICES, T0, Transcript

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


def test_resending_the_conversation_is_priced_from_the_last_prompt(store):
    session = opus_session(store)  # last prompt 42,000 tokens, 1-hour cache
    assert engine.context(store, session) == engine.Context(42_000, 42_000, True)
    assert engine.resend(store, session, "claude-opus-5-5", warm=True) == pytest.approx(42_000 * 0.20 / 1e6)
    assert engine.resend(store, session, "claude-opus-5-5", warm=False) == pytest.approx(42_000 * 8 / 1e6)
    # Another model has none of it cached, and counts it on its own tokenizer.
    assert engine.resend(store, session, "claude-sonnet-5", warm=True) == pytest.approx(42_000 * 4 / 1e6)
    assert engine.resend(store, session, "claude-haiku-4-5", warm=True) == pytest.approx(42_000 * 0.77 * 2 / 1e6)


def test_coming_back_re_sends_everything_on_each_model(store):
    session = opus_session(store)
    ctx, costs = engine.comeback(store, session)
    assert ctx.exact and ctx.tokens == 42_000
    assert costs == [("claude-fable-5-1", pytest.approx(0.84)), ("claude-opus-5-5", pytest.approx(0.336)),
                     ("claude-sonnet-5", pytest.approx(0.168)), ("claude-haiku-4-5", pytest.approx(42_000 * 0.77 * 2 / 1e6))]
    # Another session in the folder keeping Haiku's tool list cached doesn't count:
    # a resumed session's fresh system prompt may not match it.
    other = Transcript(session="sess-2")
    other.turn(T0 + 100, model="claude-haiku-4-5", write=30_000)
    other.into(store)
    assert engine.comeback(store, session)[1][3][1] == pytest.approx(42_000 * 0.77 * 2 / 1e6)
    # A model outside the current lineup comes first, then the lineup.
    older = opus_session(store, model="claude-opus-4-6", session="sess-3")
    assert [model for model, _ in engine.comeback(store, older)[1]] == [
        "claude-opus-4-6", "claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"]










def test_after_compact_only_the_tool_list_is_cached(store):
    session = opus_session(store)
    t = Transcript()
    t.record("system", T0 + 150, subtype="compact_boundary", compactMetadata={"postTokens": 2_500, "preTokens": 43_000})
    t.into(store)
    assert engine.context(store, session) == engine.Context(40_000 + 2_500, 40_000, False)
    assert engine.compact(store, session, T0 + 200) is None  # nothing left to compact
    assert list(store.summaries.values()) == [(43_000, 2_500)]


def test_a_compaction_read_twice_counts_once(store):
    # A transcript rewritten from scratch is read again from its first line.
    t = Transcript()
    t.turn(T0, write=50_000)
    t.record("system", T0 + 30, subtype="compact_boundary", compactMetadata={"postTokens": 4_000, "preTokens": 50_000})
    store.add_all(t.records)
    store.add_all(t.records)
    assert list(store.summaries.values()) == [(50_000, 4_000)]


def test_compact_restarts_the_cache_clock_from_when_it_started(store):
    session = opus_session(store, ttl="5m")  # last request at T0 + 120
    t = Transcript()
    # Its request isn't logged; the boundary is written when it ends, 20 seconds after it started.
    t.record("system", T0 + 380, subtype="compact_boundary",
             compactMetadata={"postTokens": 2_500, "preTokens": 43_000, "durationMs": 20_000})
    t.into(store)
    assert engine.cache_clock(session, T0 + 450) == (True, 360 + FIVE_MINUTES - 450, FIVE_MINUTES)
    assert not engine.cache_clock(session, T0 + 360 + FIVE_MINUTES)[0]


def test_compact_now_or_after_a_break(store):
    # research/CACHE-DECISIONS.md §6: 140k on Opus, a ~3k-token summary: ≈$0.09 warm, ≈$0.76 cold.
    earlier = Transcript(session="sess-2")
    earlier.record("system", T0 - 600, subtype="compact_boundary", compactMetadata={"postTokens": 3_000, "preTokens": 140_000})
    earlier.into(store)
    session = opus_session(store, prompts=(40_000, 138_000, 139_000))  # the tool list: a 40k first prompt
    costs = engine.compact(store, session, T0 + 200)
    assert costs.now == pytest.approx((139_000 * 0.2 + 3_000 * 20) / 1e6)  # read back, and the summary written
    assert costs.after_break == pytest.approx((139_000 * 5 + 3_000 * 20) / 1e6)  # the 5-minute cache, even on a plan
    # Later messages read the tool list and the summary instead of the conversation.
    assert costs.saving == pytest.approx((139_000 - 40_000 - 3_000) * 0.2 / 1e6)


def test_the_summary_size_is_learned_and_grows_with_the_conversation(store):
    # Nothing seen yet: 3% of the conversation, at least 3,800 and at most 16,000
    # (research/CACHE-DECISIONS.md §7: 3.8k at ~58k, 14k at 450k, 16k at 972k).
    assert [store.summary_size(n) for n in (58_000, 450_000, 972_000)] == [3_800, 13_500, 16_000]
    store.summaries.update({"u1": (58_000, 3_807), "u2": (450_000, 13_984)})
    assert store.summary_size(60_000) == 3_807
    assert store.summary_size(500_000) == 13_984
    assert store.summary_size(2_000_000) == 16_000  # none within 2×


def test_the_tool_list_is_measured_right_after_compact(store):
    t = Transcript()
    t.turn(T0, write=40_000)
    t.record("system", T0 + 30, subtype="compact_boundary", compactMetadata={"postTokens": 4_000, "preTokens": 41_000})
    t.turn(T0 + 60, read=36_829, write=4_500)  # the tool list and system prompt, read back
    t.into(store)
    session = store.sessions["sess-1"]
    assert store.tool_list(session, "claude-opus-5-5") == 36_829  # not the 40,002-token first prompt
    assert store.tool_list(session, "claude-haiku-4-5") == pytest.approx(36_829 * 0.77)
    other = opus_session(store, session="sess-2")  # the same app in the same folder
    assert store.tool_list(other, "claude-opus-5-5") == 36_829
    # A read bigger than the session's first prompt isn't the tool list (a forked conversation).
    big = Transcript(session="sess-3", cwd="/home/user/other")
    big.turn(T0, write=40_000)
    big.record("system", T0 + 30, subtype="compact_boundary", compactMetadata={"postTokens": 4_000})
    big.turn(T0 + 60, read=90_000, write=500)
    big.into(store)
    assert ("/home/user/other", "cli") not in store.measured_prefix


def test_the_tool_list_is_measured_after_a_switch_from_haiku(store):
    # The first prompt was counted on Haiku's smaller tokenizer; the read after
    # /compact on Opus is the same tool list in Opus tokens, so it's bigger.
    t = Transcript()
    t.turn(T0, model="claude-haiku-4-5", write=30_800)
    t.turn(T0 + 60, write=60_000)
    t.record("system", T0 + 90, subtype="compact_boundary", compactMetadata={"postTokens": 4_000})
    t.turn(T0 + 120, read=36_829, write=4_500)
    t.into(store)
    assert store.measured_prefix[("/home/user/proj", "cli")] == (36_829, "claude-opus-5-5")


def test_the_next_message_elsewhere_uses_what_that_model_has_cached(store):
    session = opus_session(store, ttl="5m")
    # A Haiku session in another folder: its cache is no use here.
    elsewhere = Transcript(session="sess-3", cwd="/home/user/other")
    elsewhere.turn(T0 + 100, model="claude-haiku-4-5", write=30_000, ttl="5m")
    elsewhere.into(store)
    cold, cold_exact = engine.next_message(store, session, "claude-haiku-4-5", T0 + 130)
    assert cold == pytest.approx(42_000 * 0.77 * 1.25 / 1e6) and cold_exact
    # Another session in the same folder used Haiku 30 seconds ago.
    other = Transcript(session="sess-2")
    other.turn(T0 + 100, model="claude-haiku-4-5", write=30_000, ttl="5m")
    other.into(store)
    warm, warm_exact = engine.next_message(store, session, "claude-haiku-4-5", T0 + 130)
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 130) == 30_002
    assert warm == pytest.approx(cold - 30_002 * (1.25 - 0.1) / 1e6)
    assert not warm_exact  # the shared tool list is an inference
    # Its own model reads its cache back while warm, and writes it all once it has expired.
    assert engine.next_message(store, session, "claude-opus-5-5", T0 + 130) == (pytest.approx(42_000 * 0.2 / 1e6), True)
    assert engine.next_message(store, session, "claude-opus-5-5", T0 + 120 + FIVE_MINUTES)[0] == pytest.approx(0.21)
    # Its cache expires too.
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 110 + FIVE_MINUTES) == 0














def test_a_pasted_first_message_does_not_count_as_the_tool_list(store):
    session = opus_session(store, ttl="5m")  # first prompt 40,000
    pasted = Transcript(session="sess-2", entrypoint="sdk-cli")
    pasted.turn(T0 + 100, model="claude-haiku-4-5", write=80_000, ttl="5m")  # claude -p "$(cat big.log)"
    pasted.into(store)
    # Haiku has at most what both first prompts share: this session's 40,000, in Haiku's tokens.
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 130) == round(40_000 * 0.77)
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 110 + FIVE_MINUTES) == 0






def test_a_small_warm_script_does_not_shrink_what_another_session_shares(store):
    session = opus_session(store, ttl="5m")  # first prompt 40,000
    other = Transcript(session="sess-2")
    other.turn(T0 + 100, model="claude-haiku-4-5", write=30_000, ttl="5m")
    other.into(store)
    script = Transcript(session="sess-3", entrypoint="sdk-cli")  # its own, smaller system prompt
    script.turn(T0 + 100, model="claude-haiku-4-5", write=2_000, ttl="5m")
    script.into(store)
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 130) == 30_002


def test_the_model_a_session_just_left_still_has_its_tool_list(store):
    t = Transcript()
    t.turn(T0, model="claude-haiku-4-5", write=30_000, ttl="5m")
    t.turn(T0 + 60, write=40_000, ttl="5m")  # /model opus
    t.into(store)
    session = store.sessions["sess-1"]
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 299) == 30_002
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + FIVE_MINUTES) == 0


def test_after_compact_a_pasted_first_message_is_not_counted_as_cached(store):
    t = Transcript()
    t.turn(T0, write=80_000)  # the first message pasted a big file
    t.record("system", T0 + 30, subtype="compact_boundary", compactMetadata={"postTokens": 4_000})
    t.into(store)
    session = store.sessions["sess-1"]
    assert engine.context(store, session).cached == 80_002  # nothing to compare it with
    other = Transcript(session="sess-2")  # the same app in the same folder, with a short first message
    other.turn(T0 + 5, write=40_000)
    other.into(store)
    script = Transcript(session="sess-3", entrypoint="sdk-cli")  # a system prompt of its own
    script.turn(T0 + 5, write=2_000)
    script.into(store)
    assert engine.context(store, session) == engine.Context(40_002 + 4_000, 40_002, False)


def test_a_session_moving_folder_moves_what_it_shares(store):
    session = opus_session(store, ttl="5m")
    other = Transcript(session="sess-2", cwd="/home/user/other")
    other.turn(T0 + 100, model="claude-haiku-4-5", write=30_000, ttl="5m")
    other.into(store)
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 130) == 0
    other.cwd = session.cwd  # resumed from this folder: a prompt, no reply yet
    other.user("carry on", T0 + 120)
    other.into(store)
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 130) == 30_002


def test_fast_mode_prices_what_comes_next_at_its_rates(store):
    session = opus_session(store, speed="fast")  # a 40k first prompt, 42k now, 1-hour cache
    # Read back at fast mode's cache price ($0.40, twice standard); Sonnet 5 has no fast mode.
    assert engine.resend(store, session, "claude-opus-5-5", warm=True) == pytest.approx(42_000 * 0.4 / 1e6)
    assert engine.resend(store, session, "claude-sonnet-5", warm=True) == pytest.approx(42_000 * 4 / 1e6)

