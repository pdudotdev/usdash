"""The cost engine against research/CACHE-DECISIONS.md's worked examples and three
real moves logged by llm-trunk's dev-day runs
(scenarios/results/20260926-155134-dev-day.json)."""
import pytest
from conftest import PRICES, T0, Transcript

from usdash import engine
from usdash.prices import FIVE_MINUTES, ONE_HOUR, output_cost, prompt_cost

OPUS, SONNET, HAIKU = PRICES["claude-opus-5-5"], PRICES["claude-sonnet-5"], PRICES["claude-haiku-4-5"]


def penalty(price_e, price_l, size_e, cached_e, size_l, cached_l, out_e=0, out_l=0, add_e=1_000, add_l=1_000,
            ttl=FIVE_MINUTES):
    """(STAY, MOVE, MOVE − STAY, s) for one message, from the token counts on
    each side (research/CACHE-DECISIONS.md §3)."""
    stay = prompt_cost(price_e, size_e, cached_e, ttl) + output_cost(price_e, out_e)
    move = prompt_cost(price_l, size_l, cached_l, ttl) + output_cost(price_l, out_l)
    saving = engine.message_saving(price_e, price_l, size_e, size_l, out_e, out_l, add_e, add_l, ttl)
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


def test_coming_back_re_sends_everything_on_its_model_or_a_cheaper_one(store):
    session = opus_session(store)
    ctx, costs = engine.comeback(store, session)
    assert ctx.exact and ctx.tokens == 42_000
    assert costs == [("claude-opus-5-5", pytest.approx(0.336)), ("claude-sonnet-5", pytest.approx(0.168)),
                     ("claude-haiku-4-5", pytest.approx(42_000 * 0.77 * 2 / 1e6))]
    # Another session in the folder keeping Haiku's tool list cached doesn't count:
    # a resumed session's fresh system prompt may not match it.
    other = Transcript(session="sess-2")
    other.turn(T0 + 100, model="claude-haiku-4-5", write=30_000)
    other.into(store)
    assert engine.comeback(store, session)[1][2][1] == pytest.approx(42_000 * 0.77 * 2 / 1e6)
    haiku = opus_session(store, model="claude-haiku-4-5", session="sess-3")
    assert [model for model, _ in engine.comeback(store, haiku)[1]] == ["claude-haiku-4-5"]


def test_growth_and_typical_output(store):
    session = opus_session(store, prompts=(40_000, 43_000, 44_000), out=800)
    assert session.growth() == 2_000  # the mean of +3k and +1k
    assert engine.typical_output(store, session, "claude-opus-5-5", "high") == 800
    assert engine.typical_output(store, session, "claude-haiku-4-5") == engine.DEFAULT_OUTPUT


def test_growth_is_the_recent_mean_on_one_model(store):
    # Five big tool results long ago, then twenty ordinary messages: only the last twenty count.
    prompts = [40_000]
    for rise in [30_000] * 5 + [1_000] * 20:
        prompts.append(prompts[-1] + rise)
    session = opus_session(store, prompts=tuple(prompts))
    assert session.growth() == 1_000
    # The mean, not the median: one big tool result is a real cost.
    lumpy = opus_session(store, prompts=(40_000, 41_000, 42_000, 62_000, 63_000), session="sess-2")
    assert lumpy.growth() == round((1_000 + 1_000 + 20_000 + 1_000) / 4)
    # A rise across a model switch compares two tokenizers: left out.
    t = Transcript(session="sess-3")
    t.turn(T0, write=40_000)
    t.turn(T0 + 60, model="claude-haiku-4-5", write=50_000)
    t.turn(T0 + 120, model="claude-haiku-4-5", read=50_002, write=500)
    t.into(store)
    assert store.sessions["sess-3"].growth() == 502  # 500 written + 2 uncached


def test_a_model_with_no_history_is_assumed_to_reply_at_the_same_length(store):
    session = opus_session(store, out=3_000)
    move = engine.model_move(store, session, "claude-haiku-4-5", T0 + 130)
    # Per later message: Haiku writes the same reply, 3,000 Opus tokens = 2,310 Haiku
    # tokens, at $5 instead of $20; reads 32,340 instead of 42,000 at $0.10 instead of
    # $0.20; and writes an average message (1,000 Opus tokens) at $2 instead of $8.
    output = 3_000 * 20 - 2_310 * 5
    prompt = 42_000 * 0.2 + 1_000 * 8 - (32_340 * 0.1 + 770 * 2)
    assert move.saving == pytest.approx((output + prompt) / 1e6, abs=1e-5)


def test_switching_now_costs_the_difference_in_re_sending(store):
    session = opus_session(store)
    down = engine.model_move(store, session, "claude-sonnet-5", T0 + 130)
    assert (down.stay, down.move) == (pytest.approx(42_000 * 0.2 / 1e6), pytest.approx(42_000 * 4 / 1e6))
    assert down.penalty == pytest.approx(42_000 * (4 - 0.2) / 1e6) and down.exact
    up = engine.model_move(store, session, "claude-fable-5-1", T0 + 130)
    assert up.penalty == pytest.approx(42_000 * (20 - 0.2) / 1e6)
    assert up.saving < 0 and up.payback is None  # each message costs more there
    # Once the cache has expired, staying writes it all too: moving down is cheaper right away.
    cold = engine.model_move(store, session, "claude-sonnet-5", T0 + 120 + ONE_HOUR)
    assert cold.penalty == pytest.approx(42_000 * (4 - 8) / 1e6)


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
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 130) == 30_002
    assert warm_target.move < cold_target.move
    assert warm_target.penalty == pytest.approx(cold_target.penalty - 30_002 * (1.25 - 0.1) / 1e6)
    assert cold_target.exact and not warm_target.exact  # the shared tool list is an inference
    # Its cache expires too.
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 110 + FIVE_MINUTES) == 0


def test_effort_on_opus_5_5_keeps_the_cache(store):
    session = opus_session(store, out=3_000)
    both = Transcript(session="sess-2")  # replies got ~73% shorter at medium there
    both.turn(T0, write=40_000, out=3_000)
    both.turn(T0 + 60, effort="medium", read=40_000, write=500, out=800)
    both.into(store)
    # $0.044 a message, as in research/CACHE-DECISIONS.md §6, and nothing to re-write.
    assert engine.effort_saving(store, session, "medium") == pytest.approx((3_000 - 800) * 20 / 1e6)
    assert engine.effort_rewrite(store, session, T0 + 130) is None


def test_elsewhere_an_effort_change_re_writes_the_conversation(store):
    sonnet = opus_session(store, model="claude-sonnet-5")
    assert engine.effort_saving(store, sonnet, "medium") is None
    assert engine.effort_rewrite(store, sonnet, T0 + 130) == pytest.approx(42_000 * (4 - 0.2) / 1e6)
    assert engine.effort_rewrite(store, sonnet, T0 + 120 + ONE_HOUR) == 0  # already cold: re-written anyway
    bedrock = opus_session(store, model="us.anthropic.claude-opus-5-5-v1:0", session="sess-2")
    assert not store.facts.effort_keeps_cache(bedrock.model)
    assert engine.effort_rewrite(store, bedrock, T0 + 130) == pytest.approx(42_000 * (8 - 0.2) / 1e6)


def test_payback():
    move = engine.Move("x", stay=0.02, move=0.12, penalty=0.1, saving=0.01, exact=True)
    assert move.payback == pytest.approx(10)
    assert engine.Move("x", 0.1, 0.05, -0.05, 0.01, True).payback == 0
    assert engine.Move("x", 0.1, 0.2, 0.1, 0.0, True).payback is None


def test_other_sessions_short_replies_dont_make_a_model_look_cheap(store):
    session = opus_session(store, out=2_000)
    scripted = Transcript(session="sess-2", cwd="/home/user/other")
    scripted.turn(T0, model="claude-haiku-4-5", write=30_000, out=5)  # a "say OK" run
    scripted.into(store)
    move = engine.model_move(store, session, "claude-haiku-4-5", T0 + 130)
    # Haiku is assumed to reply at this session's length (2,000 Opus tokens = 1,540 Haiku tokens), not 5.
    later_opus = (42_000 * 0.2 + 1_000 * 8 + 2_000 * 20) / 1e6
    later_haiku = (32_340 * 0.1 + 770 * 2 + 1_540 * 5) / 1e6
    assert move.saving == pytest.approx(later_opus - later_haiku, abs=1e-5)


def test_scripted_runs_dont_make_a_lower_effort_look_cheap(store):
    session = opus_session(store, out=2_000)
    scripted = Transcript(session="sess-2", entrypoint="sdk-cli")
    scripted.turn(T0, write=40_000, out=2_000)
    scripted.turn(T0 + 60, effort="low", read=40_000, write=500, out=5)  # claude -p 'say OK'
    scripted.into(store)
    assert engine.effort_saving(store, session, "low") is None  # no interactive history at low
    interactive = Transcript(session="sess-3")
    interactive.turn(T0, write=40_000, out=2_000)
    interactive.turn(T0 + 60, effort="low", read=40_000, write=500, out=800)
    interactive.into(store)
    assert engine.effort_saving(store, session, "low") == pytest.approx((2_000 - 800) * 20 / 1e6)


def test_a_session_whose_replies_logged_no_output_keeps_its_own_average(store):
    session = opus_session(store, out=0)
    other = Transcript(session="sess-2")
    other.turn(T0, write=40_000, out=3_000)  # high
    other.turn(T0 + 60, effort="medium", read=40_000, write=500, out=800)
    other.into(store)
    # 0 is this session's real average at high, not "no history": medium can't beat it.
    assert engine.effort_saving(store, session, "medium") == 0


def test_a_pasted_first_message_does_not_count_as_the_tool_list(store):
    session = opus_session(store, ttl="5m")  # first prompt 40,000
    pasted = Transcript(session="sess-2", entrypoint="sdk-cli")
    pasted.turn(T0 + 100, model="claude-haiku-4-5", write=80_000, ttl="5m")  # claude -p "$(cat big.log)"
    pasted.into(store)
    # Haiku has at most what both first prompts share: this session's 40,000, in Haiku's tokens.
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 130) == round(40_000 * 0.77)
    assert store.shared_prefix(session, "claude-haiku-4-5", T0 + 110 + FIVE_MINUTES) == 0


def test_a_lower_effort_uses_the_sessions_own_replies_there(store):
    t = Transcript()
    t.turn(T0, effort="low", write=40_000, out=300)
    t.turn(T0 + 60, read=40_000, write=1_000, out=600)  # now on high
    t.into(store)
    other = Transcript(session="sess-2")  # another task, with long replies at low
    other.turn(T0, effort="low", write=40_000, out=5_000)
    other.into(store)
    assert engine.effort_saving(store, store.sessions["sess-1"], "low") == pytest.approx((600 - 300) * 20 / 1e6)


def test_other_sessions_replies_at_a_lower_effort_count_only_as_a_ratio(store):
    session = opus_session(store, out=6_000)  # long replies at high
    short = Transcript(session="sess-2")
    short.turn(T0, effort="low", write=40_000, out=500)  # another task, only at low
    short.into(store)
    assert engine.effort_saving(store, session, "low") is None
    both = Transcript(session="sess-3")
    both.turn(T0, write=40_000, out=1_000)
    both.turn(T0 + 60, effort="low", read=40_000, write=500, out=500)  # half as long at low
    both.into(store)
    assert engine.effort_saving(store, session, "low") == pytest.approx((6_000 - 3_000) * 20 / 1e6)


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
    # Turning it off keeps only the tool list cached: the rest is written again, at standard prices.
    cost, saving = engine.fast_off(store, session, T0 + 130)
    assert cost == pytest.approx((40_000 * 0.2 + 2_000 * 8 - 42_000 * 0.4) / 1e6)
    output = 500 * (40 - 20)  # an average reply, and a message's growth (1,000) written at $16 instead of $8
    assert saving == pytest.approx((42_000 * (0.4 - 0.2) + 1_000 * (16 - 8) + output) / 1e6)
    assert engine.fast_off(store, opus_session(store, session="sess-2"), T0 + 130) is None  # not fast

