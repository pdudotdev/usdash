"""The cost engine against research/CACHE-DECISIONS.md's worked examples and three
real moves logged by llm-trunk's dev-day runs
(scenarios/results/20260926-155134-dev-day.json)."""
import pytest
from conftest import PRICES, T0, Transcript

from usdash import engine
from usdash.prices import FIVE_MINUTES, ONE_HOUR

OPUS, SONNET, HAIKU = PRICES["claude-opus-5-5"], PRICES["claude-sonnet-5"], PRICES["claude-haiku-4-5"]


def penalty(price_e, price_l, size_e, cached_e, size_l, cached_l, out_e=0, out_l=0, add_e=1_000, add_l=1_000,
            ttl=FIVE_MINUTES):
    stay, move, saving = engine.move_penalty(price_e, price_l, size_e, cached_e, size_l, cached_l, out_e, out_l,
                                             add_e, add_l, ttl)
    return stay, move, move - stay, saving


# --- Real moves (input side, as research/CACHE-DECISIONS.md §8 reports them) ------------------


def test_real_opus_to_sonnet_with_sonnet_cold_should_stay():
    # Opus 5.5 had just read 55,457 tokens; the moved request was 55,858 tokens,
    # written whole on Sonnet 5 (nothing cached there). Logged: moving cost $0.1427.
    _, move, p, _ = penalty(OPUS, SONNET, 55_858, 55_457, 55_858, 0)
    assert p == pytest.approx(0.126, abs=0.001)
    assert move + 301 * SONNET["output"] / 1e6 == pytest.approx(0.1427, abs=0.0002)
    assert p > 0  # stay


def test_real_sonnet_to_haiku_that_had_the_tool_list_cached_should_move():
    # The conversation was on Sonnet 5 (the unit-tests skill), 56,495 tokens cached.
    # Haiku 4.5 had 36,829 of the 43,466 tokens cached (another session kept
    # Claude Code's tool list warm); on Sonnet the same text is ~57,343 tokens.
    # Logged: moving cost $0.0128.
    _, move, p, _ = penalty(SONNET, HAIKU, 57_343, 56_495, 43_466, 36_829)
    assert p == pytest.approx(-0.0014, abs=0.0002)
    assert move + 173 * HAIKU["output"] / 1e6 == pytest.approx(0.0128, abs=0.0002)


def test_real_move_to_haiku_right_after_compact_should_move():
    # After /compact only Opus's tool list and system prompt (~48.6k Opus tokens)
    # are cached, so staying re-writes the rest too. Logged: moving cost $0.0117.
    _, move, p, _ = penalty(OPUS, HAIKU, 55_082, 48_587, 41_752, 36_829)
    assert p == pytest.approx(-0.035, abs=0.004)
    assert move + 367 * HAIKU["output"] / 1e6 == pytest.approx(0.0117, abs=0.0002)


# --- research/CACHE-DECISIONS.md §4: a 50k conversation on Opus 5.5, 500 output tokens -------


def test_example_1_opus_to_sonnet_while_cached():
    stay, move, p, saving = penalty(OPUS, SONNET, 50_000, 49_000, 50_000, 0, 500, 500)
    assert (stay, move, p, saving) == (pytest.approx(0.0248), pytest.approx(0.13), pytest.approx(0.1052),
                                       pytest.approx(0.0075))
    assert p / saving == pytest.approx(14, abs=0.1)


def test_example_2_opus_to_haiku_depends_on_what_haiku_has_cached():
    _, move, p, _ = penalty(OPUS, HAIKU, 50_000, 49_000, 38_500, 30_000, 500, 500)
    assert (move, p) == (pytest.approx(0.0161, abs=1e-4), pytest.approx(-0.009, abs=5e-4))
    _, move, p, saving = penalty(OPUS, HAIKU, 50_000, 49_000, 38_500, 0, 500, 500, add_l=770)
    assert (move, p, saving) == (pytest.approx(0.0506, abs=1e-4), pytest.approx(0.026, abs=5e-4),
                                 pytest.approx(0.0177, abs=1e-4))


def test_example_3_after_the_cache_expired():
    stay, sonnet, _, _ = penalty(OPUS, SONNET, 50_000, 0, 50_000, 0, 500, 500)
    _, haiku, _, _ = penalty(OPUS, HAIKU, 50_000, 0, 38_500, 0, 500, 500)
    assert (stay, sonnet, haiku) == (pytest.approx(0.26), pytest.approx(0.13), pytest.approx(0.0506, abs=1e-4))
    # ... unless another session keeps the tool list cached on Opus.
    warm_prefix, _, _, _ = penalty(OPUS, SONNET, 50_000, 40_000, 50_000, 0, 500, 500)
    assert warm_prefix == pytest.approx(0.068)


# --- A session's cache clock and next message ----------------------------------------


def opus_session(store, prompts=(40_000, 41_000, 42_000), out=500, ttl="1h", **reply):
    t = Transcript()
    previous = 0
    for i, prompt in enumerate(prompts):
        t.turn(T0 + 60 * i, read=previous, write=prompt - previous - 2, out=out, ttl=ttl, **reply)
        previous = prompt
    t.into(store)
    return store.sessions["sess-1"]


def test_cache_state_counts_down_from_the_last_request_start(store):
    session = opus_session(store)
    last_start = T0 + 120
    state = engine.cache_state(store, session, last_start + 60)
    assert (state.warm, state.left, state.ttl) == (True, ONE_HOUR - 60, ONE_HOUR)
    assert (state.size, state.cached) == (43_000, 42_000)  # the last prompt plus a typical message
    # 42k read + 1k written (1-hour) + 500 out
    assert state.next_now == pytest.approx((42_000 * 0.2 + 1_000 * 8 + 500 * 20) / 1e6)
    assert state.next_cold == pytest.approx((43_000 * 8 + 500 * 20) / 1e6)
    cold = engine.cache_state(store, session, last_start + ONE_HOUR)
    assert (cold.warm, cold.left, cold.cached) == (False, 0, 0)


def test_five_minute_sessions_expire_in_five_minutes(store):
    session = opus_session(store, ttl="5m")
    assert engine.cache_state(store, session, T0 + 120 + 299).warm
    assert not engine.cache_state(store, session, T0 + 120 + 300).warm


def test_growth_and_typical_output(store):
    session = opus_session(store, prompts=(40_000, 43_000, 44_000), out=800)
    assert engine.growth(session) == 2_000  # median of +3k and +1k
    assert engine.typical_output(store, session, "claude-opus-5-5", "high") == 800
    assert engine.typical_output(store, session, "claude-haiku-4-5") == engine.DEFAULT_OUTPUT
    assert engine.typical_output(store, session, "claude-haiku-4-5", default=800) == 800


def test_a_model_with_no_history_is_assumed_to_reply_at_the_same_length(store):
    session = opus_session(store, out=3_000)
    move = engine.model_move(store, session, "claude-haiku-4-5", T0 + 130)
    # Haiku writes 3,000 tokens too: output at $5 instead of $20, plus the input side
    # (43k read + 1k written on Opus's 1-hour cache vs 33,110 + 770 on Haiku's).
    output = 3_000 * (20 - 5)
    prompt = 43_000 * 0.2 + 1_000 * 8 - (33_110 * 0.1 + 770 * 2)
    assert move.saving == pytest.approx((output + prompt) / 1e6, abs=1e-5)


def test_after_compact_only_the_tool_list_is_cached(store):
    session = opus_session(store)
    t = Transcript()
    t.record("system", T0 + 150, subtype="compact_boundary", compactMetadata={"postTokens": 2_500})
    t.into(store)
    state = engine.cache_state(store, session, T0 + 200)
    assert (state.size, state.cached) == (40_000 + 2_500, 40_000)
    assert engine.compact_cost(store, session, T0 + 200) is None  # nothing left to compact


def test_compact_now_or_after_a_break(store):
    # research/CACHE-DECISIONS.md §6: 140k on Opus, ~3k-token summary: ≈$0.09 warm, ≈$0.76 cold.
    session = opus_session(store, prompts=(137_000, 138_000, 139_000))
    warm, cold = engine.compact_cost(store, session, T0 + 200)
    assert warm == pytest.approx(0.093, abs=0.001)
    assert cold == pytest.approx(0.76, abs=0.001)


def test_model_move_uses_what_the_target_has_cached(store):
    session = opus_session(store, ttl="5m")
    # A Haiku session in another folder: its cache is no use here (but it gives Haiku an output history).
    elsewhere = Transcript(session="sess-3", cwd="/home/user/other")
    elsewhere.turn(T0 + 100, model="claude-haiku-4-5", write=30_000, ttl="5m")
    elsewhere.into(store)
    cold_target = engine.model_move(store, session, "claude-haiku-4-5", T0 + 130)
    # Another session in the same folder used Haiku 30 seconds ago.
    other = Transcript(session="sess-2")
    other.turn(T0 + 100, model="claude-haiku-4-5", write=30_000, ttl="5m")
    other.into(store)
    warm_target = engine.model_move(store, session, "claude-haiku-4-5", T0 + 130)
    assert store.shared_prefix("/home/user/proj", "claude-haiku-4-5", T0 + 130) == 30_002
    assert warm_target.move < cold_target.move
    assert warm_target.penalty == pytest.approx(cold_target.penalty - 30_002 * (1.25 - 0.1) / 1e6)
    # Its cache expires too.
    assert store.shared_prefix("/home/user/proj", "claude-haiku-4-5", T0 + 110 + FIVE_MINUTES) == 0


def test_effort_move_on_opus_5_5_keeps_the_cache(store):
    session = opus_session(store, out=3_000)
    medium = Transcript(session="sess-2")
    medium.turn(T0, effort="medium", write=40_000, out=800)
    medium.into(store)
    move = engine.effort_move(store, session, "medium", T0 + 130)
    assert move.saving == pytest.approx((3_000 - 800) * 20 / 1e6)  # $0.044, as in research/CACHE-DECISIONS.md §6
    assert move.penalty == pytest.approx(-move.saving)


def test_no_effort_move_where_effort_rewrites_the_cache(store):
    session = opus_session(store, model="claude-sonnet-5")
    assert engine.effort_move(store, session, "medium", T0 + 130) is None
    assert not engine.effort_keeps_cache("us.anthropic.claude-opus-5-5-v1:0")


def test_payback():
    move = engine.Move("x", stay=0.02, move=0.12, penalty=0.1, saving=0.01, warm=True, left=100)
    assert move.payback == pytest.approx(10)
    assert engine.Move("x", 0.1, 0.05, -0.05, 0.01, True, 100).payback == 0
    assert engine.Move("x", 0.1, 0.2, 0.1, 0.0, True, 100).payback is None
