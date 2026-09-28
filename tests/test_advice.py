"""What a live session shows: its next message on each model, ✅ on the cheapest, and the ⚡ /compact warning."""
import pytest
from conftest import T0, Transcript

from usdash import advice
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


def live(store, session, now) -> advice.Advice:
    """What a session that has to be live at `now` shows."""
    tip = advice.advise(store, session, now)
    assert tip is not None, "not a live session"
    return tip


def prices(tip: advice.Advice) -> dict[str, float]:
    return {model: cost for model, cost, _ in tip.prices}


def test_the_next_message_on_each_model_and_the_cheapest(store):
    session = session_on(store)  # 42,000 tokens on Opus 5.5, 1-hour cache
    tip = live(store, session, LAST_START + 600)
    # Read back on its own model at $0.20; written on the others at their 1-hour price (×0.77 on Haiku).
    assert list(prices(tip)) == ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"]
    assert prices(tip) == pytest.approx({"claude-fable-5-1": 42_000 * 20 / 1e6, "claude-opus-5-5": 42_000 * 0.2 / 1e6,
                                         "claude-sonnet-5": 42_000 * 4 / 1e6,
                                         "claude-haiku-4-5": 42_000 * 0.77 * 2 / 1e6})
    # Exact on its own model; ≈ on the others, which may have the tool list cached.
    assert [exact for _, _, exact in tip.prices] == [False, True, False, False]
    assert tip.cheapest == "claude-opus-5-5" and tip.warning is None


def test_idle_sessions_get_nothing(store):
    session = session_on(store)
    assert advice.advise(store, session, LAST_START + ONE_HOUR + 1) is None  # cold: its line shows the way back
    closing = Transcript()
    closing.record("cost-state", totalCostUSD=1.0)
    closing.into(store)
    assert advice.advise(store, session, LAST_START + 60) is None


def test_another_model_can_be_the_cheapest_next_message(store):
    session = session_on(store, ttl="5m", out=1_000)
    # Right after /compact, staying re-writes all but the tool list; Haiku has the tool list cached.
    other = Transcript(session="sess-2")
    other.turn(T0 + 100, model="claude-haiku-4-5", write=35_000, ttl="5m")
    other.into(store)
    compact = Transcript()
    compact.record("system", T0 + 125, subtype="compact_boundary", compactMetadata={"postTokens": 3_000})
    compact.into(store)
    tip = live(store, session, T0 + 130)
    assert tip.cheapest == "claude-haiku-4-5"
    assert not any(exact for _, _, exact in tip.prices)  # every amount leans on the estimated tool list and summary


def test_the_cheapest_is_the_sessions_own_model_on_a_tie():
    tip = advice.Advice(None, "claude-sonnet-5", [("claude-opus-5-5", 0.1, True), ("claude-sonnet-5", 0.1, True)])
    assert tip.cheapest == "claude-sonnet-5"


def test_another_sessions_cached_tool_list_is_subtracted_but_not_its_conversation(store):
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
    # Session B, warm on Opus at 50k: on Haiku its next message reads only the tool list back and writes the rest.
    b = session_on(store, prompts=(40_000, 45_000, 50_000), ttl="5m", session="sess-b", out=500)
    tip = live(store, b, LAST_START + 30)
    size = 50_000 * 0.77  # B's conversation, in Haiku's tokens
    shared = round(40_000 * 0.77)  # the smaller first prompt: B's 40,000, not A's 40,002
    haiku = next(entry for entry in tip.prices if entry[0] == "claude-haiku-4-5")
    assert haiku[1] == pytest.approx((shared * 0.10 + (size - shared) * 1.25) / 1e6) and not haiku[2]


def test_compact_warning_only_when_big_warm_and_about_to_expire(store):
    session = session_on(store, prompts=(40_000, 138_000, 139_000))
    assert live(store, session, LAST_START + 60).warning is None  # 59 min left
    # 139k read back at $0.20 (or written at $5 on the 5-minute cache) plus a ~4.2k summary at $20.
    assert (live(store, session, LAST_START + ONE_HOUR - 300).warning
            == "Taking a break? /compact first: ≈$0.11 now, ≈$0.78 once the cache expires in 5:00.")
    assert advice.advise(store, session, LAST_START + ONE_HOUR + 1) is None  # already cold
    small = session_on(store, session="sess-2")
    assert live(store, small, LAST_START + ONE_HOUR - 300).warning is None


def test_no_compact_warning_when_compacting_saves_too_little(store):
    # Haiku reads at $0.10: 101k tokens with a 60k tool list and a ~3.8k summary save < $0.005 a message.
    session = session_on(store, model="claude-haiku-4-5", prompts=(60_000, 100_000, 101_000), ttl="5m", effort=None)
    assert live(store, session, LAST_START + FIVE_MINUTES - 120).warning is None


def test_compact_warning_on_a_five_minute_cache(store):
    session = session_on(store, prompts=(40_000, 138_000, 139_000), ttl="5m")
    assert live(store, session, LAST_START + 60).warning is None
    assert live(store, session, LAST_START + FIVE_MINUTES - 120).warning.startswith("Taking a break?")


def test_an_unpriced_model_gets_no_numbers(store):
    # Staying has no known price, so no other model can be called cheaper.
    session = session_on(store, model="claude-mystery-9")
    tip = live(store, session, LAST_START + 10)
    assert (tip.prices, tip.cheapest, tip.warning) == ([], None, None)


def test_money_tokens_and_clock():
    assert advice.money(0.0415) == "$0.04" and advice.money(-1.5) == "$1.50" and advice.money(1234.5) == "$1,234.50"
    assert advice.money(0.003) == "<$0.01" and advice.money(0.006) == "$0.01" and advice.money(0) == "$0.00"
    assert advice.tokens_text(504_113) == "504k" and advice.tokens_text(9_500) == "9.5k"
    assert advice.clock(3125) == "52:05" and advice.clock(3600) == "1h00m"
    assert advice.plural(1, "message") == "1 message" and advice.plural(6, "message") == "6 messages"
