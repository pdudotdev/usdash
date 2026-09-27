"""Advice: the action a live session gets, and the options with what each costs."""
import re

import pytest
from conftest import T0, Transcript

from usdash import advice, engine
from usdash.prices import FIVE_MINUTES, ONE_HOUR

MONEY = r"\$\d+\.\d\d"


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


def live(store, session, now, memory=None) -> advice.Advice:
    """The advice for a session that has to be live at `now`."""
    tip = advice.advise(store, session, now, memory)
    assert tip is not None, "not a live session"
    return tip


def rows(tip: advice.Advice) -> dict[str, advice.Option]:
    return {option.label: option for option in tip.options}


def test_a_warm_session_says_stay_and_prices_every_option(store):
    session = session_on(store, out=2_000)
    tip = live(store, session, LAST_START + 600)
    assert tip.action == "Stay on Opus 5.5 for now; switching is free after your next 1-hour break."
    assert not tip.urgent
    options = rows(tip)
    assert list(options) == ["stay", "Fable 5.1", "Sonnet 5", "Haiku 4.5", "/effort"]  # a 42k chat: no /compact
    assert [o.arrow for o in tip.options] == ["", "↑", "↓", "↓", ""]
    # 42,000 tokens read back at $0.20, or written again at $8 (the 1-hour cache).
    assert options["stay"].text == "re-sends 42k tokens: $0.01 now, $0.34 after a break"
    assert re.fullmatch(rf"{MONEY} more now, then ≈{MONEY} less a message · evens out after ≈\d+ messages",
                        options["Sonnet 5"].text)
    assert re.fullmatch(rf"{MONEY} more now, then ≈{MONEY} more a message", options["Fable 5.1"].text)
    assert options["/effort"].text == "costs nothing now"
    api = session_on(store, out=500, ttl="5m", session="sess-2")  # short replies: switching now still costs extra
    assert live(store, api, LAST_START + 60).action.endswith("after your next 5-minute break.")


def test_idle_sessions_get_no_advice(store):
    session = session_on(store, out=2_000)
    assert advice.advise(store, session, LAST_START + ONE_HOUR + 1) is None  # cold: its line shows the way back
    closing = Transcript()
    closing.record("cost-state", totalCostUSD=1.0)
    closing.into(store)
    assert advice.advise(store, session, LAST_START + 60) is None


def test_switch_now_when_the_target_already_has_it_cheaper(store):
    session = session_on(store, ttl="5m", out=1_000)
    # Right after /compact, staying re-writes all but the tool list; Haiku has the tool list cached.
    other = Transcript(session="sess-2")
    other.turn(T0 + 100, model="claude-haiku-4-5", write=35_000, ttl="5m")
    other.into(store)
    compact = Transcript()
    compact.record("system", T0 + 125, subtype="compact_boundary", compactMetadata={"postTokens": 3_000})
    compact.into(store)
    tip = live(store, session, T0 + 130)
    assert (tip.action, tip.urgent) == ("Switch to Haiku 4.5 now: it's already cheaper.", True)
    # Every amount leans on the estimated tool list and summary.
    assert rows(tip)["stay"].text.startswith("re-sends ≈43k tokens: ≈$")
    assert re.fullmatch(rf"≈{MONEY} less now, then ≈{MONEY} less a message", rows(tip)["Haiku 4.5"].text)


def test_rent_or_buy_counts_from_when_the_tip_first_appeared_for_every_cheaper_model(store):
    memory = advice.Memory()
    session = session_on(store, out=3_000)
    assert live(store, session, LAST_START + 10, memory).action.startswith("Stay on Opus 5.5 for now")
    # Three more messages on Opus: each costs ~$0.06 more than on Haiku, ~$0.03 more than on Sonnet.
    t = Transcript()
    prompt = 42_000
    for i in range(1, 4):
        t.turn(LAST_START + 20 * i, read=prompt, write=1_000, out=3_000)
        prompt += 1_000
    t.into(store)
    # Enough to cover switching to Haiku (~$0.06), not Sonnet (~$0.17): the nearest model isn't the only one checked.
    later = live(store, session, LAST_START + 80, memory)
    assert re.fullmatch(rf"Switch to Haiku 4\.5 now: it would have saved {MONEY} by now; switching costs {MONEY}\.",
                        later.action)
    # Without the memory, the count starts now: no history yet.
    assert live(store, session, LAST_START + 80, advice.Memory()).action.startswith("Stay on Opus 5.5")


def test_no_stay_action_on_the_cheapest_model(store):
    session = session_on(store, model="claude-haiku-4-5")
    tip = live(store, session, LAST_START + 10)
    assert tip.action is None  # nothing cheaper to switch to
    assert [(o.arrow, o.label) for o in tip.options] == [
        ("", "stay"), ("↑", "Fable 5.1"), ("↑", "Opus 5.5"), ("↑", "Sonnet 5"), ("", "/effort")]
    # On Haiku an effort change re-writes the conversation: 42,000 × ($2 − $0.10).
    assert rows(tip)["/effort"].text == "re-sends 42k tokens: $0.08 more now"


def test_compact_action_only_when_big_warm_and_about_to_expire(store):
    session = session_on(store, prompts=(40_000, 138_000, 139_000))
    assert not live(store, session, LAST_START + 60).action.startswith("Taking a break")  # 59 min left
    tip = live(store, session, LAST_START + ONE_HOUR - 300)
    # 139k read back at $0.20 (or written at $5 on the 5-minute cache) plus a ~4.2k summary at $20.
    assert tip.action == "Taking a break? /compact first: ≈$0.11 now, ≈$0.78 once the cache expires in 5:00."
    assert tip.urgent
    assert rows(tip)["/compact"].text == "≈$0.11 now, ≈$0.78 after a break; then ≈$0.02 less a message"
    assert advice.advise(store, session, LAST_START + ONE_HOUR + 1) is None  # already cold
    small = session_on(store, session="sess-2")
    assert not live(store, small, LAST_START + ONE_HOUR - 300).action.startswith("Taking a break")


def test_no_compact_action_when_compacting_saves_too_little(store):
    # Haiku reads at $0.10: 101k tokens with a 60k tool list and a ~3.8k summary save < $0.005 a message.
    session = session_on(store, model="claude-haiku-4-5", prompts=(60_000, 100_000, 101_000), ttl="5m", effort=None)
    tip = live(store, session, LAST_START + FIVE_MINUTES - 120)
    assert tip.action is None and "/compact" not in rows(tip)


def test_compact_action_on_a_five_minute_cache(store):
    session = session_on(store, prompts=(40_000, 138_000, 139_000), ttl="5m")
    assert not live(store, session, LAST_START + 60).urgent
    assert live(store, session, LAST_START + FIVE_MINUTES - 120).action.startswith("Taking a break?")


def test_effort_action_on_opus_5_5(store):
    session = session_on(store, out=3_000)
    both = Transcript(session="sess-2")  # replies got ~73% shorter at medium there
    both.turn(T0, write=40_000, out=3_000)
    both.turn(T0 + 60, effort="medium", read=40_000, write=500, out=800)
    both.into(store)
    tip = live(store, session, LAST_START + 10)
    assert tip.action == "Try /effort medium: ≈$0.04 less a message, at no cost now."
    assert rows(tip)["/effort medium"].text == "costs nothing now, then ≈$0.04 less a message"


def test_arrows_point_down_only_to_the_models_switch_advice_considers(store):
    # Fable 5.1's output costs the same as Fable 5's: not cheaper, so ↑ like any peer or better model.
    session = session_on(store, model="claude-fable-5")
    tip = live(store, session, LAST_START + 10)
    assert [(o.arrow, o.label) for o in tip.options][1:5] == [
        ("↑", "Fable 5.1"), ("↓", "Opus 5.5"), ("↓", "Sonnet 5"), ("↓", "Haiku 4.5")]


def test_effort_row_where_it_would_rewrite_the_cache(store):
    session = session_on(store, model="claude-sonnet-5", out=3_000)
    medium = Transcript(session="sess-2")
    medium.turn(T0, model="claude-sonnet-5", effort="medium", write=40_000, out=800)
    medium.into(store)
    tip = live(store, session, LAST_START + 10)
    assert not tip.action.startswith("Try /effort")
    assert rows(tip)["/effort"].text == "re-sends 42k tokens: $0.16 more now"  # 42,000 × ($4 − $0.20)


def test_effort_skips_a_level_that_saves_too_little(store):
    session = session_on(store, out=3_000)
    other = Transcript(session="sess-2")
    other.turn(T0, write=40_000, out=3_000)
    other.turn(T0 + 60, effort="medium", read=40_000, write=500, out=2_950)  # saves $0.001 per message
    other.turn(T0 + 120, effort="low", read=40_500, write=500, out=800)  # saves $0.044
    other.into(store)
    assert advice.lower_effort(store, session) == ("low", pytest.approx(0.044))


def test_an_unpriced_model_gets_no_numbers(store):
    session = session_on(store, model="claude-mystery-9")
    tip = live(store, session, LAST_START + 10)
    assert (tip.action, tip.options) == (None, [])


def test_money_tokens_and_clock():
    assert advice.money(0.0415) == "$0.04" and advice.money(-1.5) == "$1.50" and advice.money(1234.5) == "$1,234.50"
    assert advice.money(0.003) == "<$0.01" and advice.money(0.006) == "$0.01" and advice.money(0) == "$0.00"
    assert advice.tokens_text(504_113) == "504k" and advice.tokens_text(9_500) == "9.5k"
    assert advice.clock(3125) == "52:05" and advice.clock(3600) == "1h00m"
    assert advice.plural(1, "message") == "1 message" and advice.plural(6, "message") == "6 messages"


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
    # priced all of B's ~38.5k Haiku tokens as cheap reads.)
    b = session_on(store, prompts=(40_000, 45_000, 50_000), ttl="5m", session="sess-b", out=500)
    move = engine.model_move(store, b, "claude-haiku-4-5", LAST_START + 30)
    size = 50_000 * 0.77  # B's conversation, in Haiku's tokens
    shared = round(40_000 * 0.77)  # the smaller first prompt: B's 40,000, not A's 40,002
    assert move.move == pytest.approx((shared * 0.10 + (size - shared) * 1.25) / 1e6)
