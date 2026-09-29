"""The Stats view's figures, against sums worked out by hand from the tokens logged.

Times are local (datetime(...).timestamp()), so the days come out the same in any
timezone; CI runs the suite in UTC and in Asia/Tokyo.
"""
from datetime import datetime

import pytest
from conftest import PRICES, Transcript

from usdash import stats as st
from usdash import ui
from usdash.sessions import Store

NOW = datetime(2026, 9, 29, 15, 0).timestamp()
WINDOW = 3 * 86400  # from Sat 26 Sep 15:00
OPUS, SONNET, HAIKU = PRICES["claude-opus-5-5"], PRICES["claude-sonnet-5-5"], PRICES["claude-haiku-4-5"]


def at(day: int, hour: int, minute: int = 0, second: int = 0) -> float:
    return datetime(2026, 9, day, hour, minute, second).timestamp()


def cost(price, fresh=2, read=0, w5=0, w1h=0, out=100, scale=1.0):
    """One request at `price` (× `scale`: fast mode's 2, US-only's 1.1), per the pricing page."""
    return (fresh * price["input"] + read * price["cache_read"] + w5 * price["cache_write"]
            + w1h * price["cache_write_1h"] + out * price["output"]) * scale / 1e6


# The requests below, priced by hand.
A1 = cost(OPUS, w1h=40_000)  # Sun: "fix totals", a day after the 9k request before the period
A1_MISS = 9_002 * (OPUS["cache_write_1h"] - OPUS["cache_read"]) / 1e6  # that one's cache had expired
A2 = cost(OPUS, read=40_000, w1h=1_000)  # its tool call's answer
A3 = cost(OPUS, w1h=41_000)  # Mon: back after 26 hours, everything written again
A3_MISS = 41_002 * (OPUS["cache_write_1h"] - OPUS["cache_read"]) / 1e6
B1 = cost(SONNET, w5=20_000, out=1_000)  # Tue 09:00: "draft notes"
B2 = cost(HAIKU, w5=5_000)  # its subagent
B3 = cost(SONNET, read=20_000, w5=3_000, out=500)  # Tue 09:03: "next", after /compact
C1 = cost(OPUS, w1h=100_000, scale=2)  # Tue 14:00, fast mode
C2 = cost(OPUS, read=100_002, w1h=450_000, scale=2)  # 550k: the biggest band
SPEND = A1 + A2 + A3 + B1 + B2 + B3 + C1 + C2


def build(store: Store) -> Store:
    a = Transcript(session="aaaa-1111", cwd="/home/user/shop")
    a.turn(at(26, 10), text="before the period", write=9_000)  # ends before 26 Sep 15:00
    a.turn(at(27, 10), text="fix totals", write=40_000)
    a.tool_result(at(27, 10, 1))
    a.reply(at(27, 10, 1, 5), read=40_000, write=1_000)
    a.turn(at(28, 12), text="add tests", write=41_000)
    b = Transcript(session="bbbb-2222", cwd="/home/user/billing")
    b.turn(at(29, 9), text="draft notes", model="claude-sonnet-5-5", write=20_000, out=1_000, ttl="5m")
    b.user("look around", at(29, 9, 0, 30), subagent="a1")
    b.reply(at(29, 9, 1), subagent="a1", model="claude-haiku-4-5", write=5_000, ttl="5m")
    b.user("<command-name>/compact</command-name>", at(29, 9, 2))
    b.turn(at(29, 9, 3), text="next", model="claude-sonnet-5-5", read=20_000, write=3_000, out=500, ttl="5m")
    c = Transcript(session="cccc-3333", cwd="/home/user/shop", branch="speedy")
    c.turn(at(29, 14), text="go fast", write=100_000, speed="fast")
    c.tool_result(at(29, 14, 1))
    c.reply(at(29, 14, 1, 5), read=100_002, write=450_000, speed="fast")
    for t in (a, b, c):
        t.into(store)
    return store


@pytest.fixture
def figures(store):
    return st.compute(build(store), NOW, WINDOW)


def test_the_summary(figures):
    assert figures.spend == pytest.approx(SPEND)
    assert figures.per_day == pytest.approx(SPEND / 3)
    assert figures.recent == pytest.approx(C1 + C2)  # since 10:00
    assert (figures.requests, figures.prompts, figures.sessions) == (8, 5, 3)  # /compact isn't a prompt
    assert figures.subagents == pytest.approx(B2)
    read = 40_000 + 20_000 + 100_002
    prompt = 40_002 + 41_002 + 41_002 + 20_002 + 5_002 + 23_002 + 100_002 + 550_004
    assert (figures.read, figures.prompt) == (read, prompt) and figures.cached == pytest.approx(read / prompt)
    assert figures.misses == pytest.approx(A1_MISS + A3_MISS)


def test_by_day(figures):
    assert [d.day for d in figures.days] == ["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29"]
    empty, sun, mon, tue = figures.days
    assert (empty.spend, empty.requests, empty.cached) == (0, 0, None)  # the period's first day: nothing after 15:00
    assert (sun.spend, sun.requests, sun.misses) == (pytest.approx(A1 + A2), 2, pytest.approx(A1_MISS))
    assert (mon.spend, mon.misses, mon.cached) == (pytest.approx(A3), pytest.approx(A3_MISS), 0)
    assert tue.spend == pytest.approx(B1 + B2 + B3 + C1 + C2) and tue.requests == 5


def test_where_the_money_goes(figures):
    kinds = {k.name: k for k in figures.kinds}
    assert list(kinds) == ["cache reads", "cache writes (1h)", "cache writes (5m)", "uncached input", "output"]
    assert kinds["cache reads"].tokens == 160_002
    assert kinds["cache reads"].spend == pytest.approx(
        (40_000 * OPUS["cache_read"] + 20_000 * SONNET["cache_read"] + 100_002 * OPUS["cache_read"] * 2) / 1e6)
    assert kinds["cache writes (1h)"].tokens == 40_000 + 1_000 + 41_000 + 100_000 + 450_000
    assert kinds["cache writes (5m)"].tokens == 28_000
    assert kinds["uncached input"].tokens == 16 and kinds["output"].tokens == 100 * 6 + 1_000 + 500
    # Each at the price its request paid (fast mode's twice), so they add up to the spend exactly.
    assert sum(k.spend for k in figures.kinds) == pytest.approx(SPEND, abs=1e-9)
    inputs = [k for k in figures.kinds if k.name != "output"]
    assert figures.input_price == pytest.approx(sum(k.spend for k in inputs) / sum(k.tokens for k in inputs) * 1e6)
    assert figures.searches == 0


def test_by_model(figures):
    rows = [(r.model, r.fast, r.effort, r.requests) for r in figures.models]
    assert rows == [("claude-opus-5-5", True, "high", 2), ("claude-opus-5-5", False, "high", 3),
                    ("claude-sonnet-5-5", False, "high", 2), ("claude-haiku-4-5", False, "high", 1)]
    assert [r.spend for r in figures.models] == pytest.approx([C1 + C2, A1 + A2 + A3, B1 + B3, B2])


def test_cache_misses_by_cause(figures):
    (cause,) = figures.causes  # the miss before the period isn't counted: only what the period's requests paid
    assert (cause.cause, cause.misses, cause.rewritten) == ("cache expired", 2, 9_002 + 41_002)
    assert cause.extra == pytest.approx(A1_MISS + A3_MISS)


def test_by_context_size(figures):
    bands = {b.label: (b.requests, b.spend) for b in figures.bands}
    assert bands == {
        "under 50k": (6, pytest.approx(A1 + A2 + A3 + B1 + B2 + B3)),
        "100–200k": (1, pytest.approx(C1)),
        "500k and more": (1, pytest.approx(C2)),
    }  # empty bands are left out


def test_top_sessions_and_projects(figures):
    assert [(r.session.id, r.peak) for r in figures.top] == [
        ("cccc-3333", 550_004), ("aaaa-1111", 41_002), ("bbbb-2222", 23_002)]  # a subagent's prompt isn't a peak
    assert figures.top[0].spend == pytest.approx(C1 + C2)
    assert figures.top[1].spend == pytest.approx(A1 + A2 + A3)  # in the period only: not the 9k before it
    assert figures.top_share == pytest.approx(1)
    assert [(p.name, p.sessions) for p in figures.projects] == [("shop", 2), ("billing", 1)]  # without the branch
    assert figures.projects[0].spend == pytest.approx(A1 + A2 + A3 + C1 + C2)


def test_costliest_prompts(figures):
    turns = [(t.text, t.requests) for t in figures.turns]
    # A turn runs until the next thing typed: its tool calls and its subagents count in it.
    assert turns == [("go fast", 2), ("fix totals", 2), ("add tests", 1), ("draft notes", 2), ("next", 1)]
    assert [t.spend for t in figures.turns] == pytest.approx([C1 + C2, A1 + A2, A3, B1 + B2, B3])


def test_the_invariants(store):
    build(store)
    s = st.compute(store, NOW, WINDOW)
    for panel in (s.days, s.kinds, s.models, s.bands, s.projects):
        assert sum(row.spend for row in panel) == pytest.approx(s.spend, abs=1e-9)
    assert sum(c.extra for c in s.causes) == pytest.approx(s.misses, abs=1e-9)
    for day in s.days[1:]:  # every day wholly inside the period: the header's totals agree
        assert day.misses == pytest.approx(sum(store.rewrites.get(day.day, {}).values()), abs=1e-9)


def test_a_request_counts_in_the_period_it_ended_in(store):
    t = Transcript()
    t.turn(at(26, 14, 30), write=10_000, took=10)  # ended at 14:30:10, before the period
    t.turn(at(26, 14, 59, 50), read=10_002, write=100, took=15)  # started before 15:00, ended after
    t.into(store)
    s = st.compute(store, NOW, WINDOW)
    assert s.requests == 1 and s.spend == pytest.approx(cost(OPUS, read=10_002, w1h=100))


def test_the_folded_earlier_row_does_not_set_the_scale(store):
    t = Transcript()
    for day in range(1, 30):  # the same request every day, Sep 1..29
        t.turn(at(day, 10), text=f"day {day}", write=40_000)
    t.into(store)
    lines = render(store, ui.View(now=NOW, window=30 * 86400, mode="stats"), 120).splitlines()
    earlier = next(line for line in lines if "earlier" in line)
    assert "█" not in earlier  # 16 days' sum: no bar to compare with a day's
    days = [line for line in lines if " Sep " in line and "$" in line]
    assert len(days) == 13 and all("█" * 16 in line for line in days)  # every day, the full bar


def test_an_earlier_row_with_nothing_in_it_is_left_out(store):
    build(store)  # everything in the last 3 days
    days = st.compute(store, NOW, 3650 * 86400).days
    assert len(days) == 13 and days[0].day == "2026-09-17" and all(d.day != st.EARLIER for d in days)


def test_today_has_a_row_even_with_nothing_spent_yet(store):
    t = Transcript()
    t.turn(at(28, 12), write=10_000)
    t.into(store)
    days = st.compute(store, NOW, WINDOW).days
    assert days[-1].day == "2026-09-29" and days[-1].requests == 0


def test_a_models_row_shows_its_latest_effort(store):
    t = Transcript()
    t.turn(at(29, 10), write=10_000, effort="high")
    t.turn(at(29, 10, 5), read=10_002, write=100, effort="low")
    t.into(store)
    (row,) = st.compute(store, NOW, WINDOW).models
    assert (row.effort, row.requests) == ("low", 2)


def test_a_request_ending_just_before_midnight_counts_on_that_day(store):
    t = Transcript()
    t.turn(at(27, 23, 59, 40), write=10_000, took=19)  # ends 23:59:59
    t.turn(at(27, 23, 59, 50), read=10_002, write=100, took=11)  # ends 00:00:01
    t.into(store)
    days = {d.day: d.requests for d in st.compute(store, NOW, WINDOW).days}
    assert (days["2026-09-27"], days["2026-09-28"]) == (1, 1)


def test_more_than_14_days_fold_the_oldest_into_one_row(store):
    t = Transcript()
    for day in (1, 5, 10, 20, 29):
        t.turn(at(day, 12), write=10_000)
    t.into(store)
    s = st.compute(store, NOW, 30 * 86400)
    assert len(s.days) == 14 and s.days[0].day == st.EARLIER and s.days[1].day == "2026-09-17"  # and 12 more
    assert s.days[0].requests == 3 and s.days[0].spend == pytest.approx(3 * cost(OPUS, w1h=10_000))
    assert sum(d.spend for d in s.days) == pytest.approx(s.spend)


def test_web_searches_logged_in_a_request_are_their_own_row(store):
    t = Transcript()
    t.turn(at(29, 12), write=10_000, tools={"web_search_requests": 2})
    t.into(store)
    s = st.compute(store, NOW, WINDOW)
    assert (s.searches, s.search_spend) == (2, pytest.approx(0.02))
    assert sum(k.spend for k in s.kinds) + s.search_spend == pytest.approx(s.spend)


def test_us_only_requests_are_split_at_the_prices_they_paid(store):
    t = Transcript()
    t.turn(at(29, 12), write=10_000, out=1_000, geo="us")
    t.into(store)
    kinds = {k.name: k.spend for k in st.compute(store, NOW, WINDOW).kinds}
    assert kinds["output"] == pytest.approx(1_000 * 20 * 1.1 / 1e6)


def test_requests_with_no_known_price_are_left_out(store):
    t = Transcript()
    t.turn(at(29, 12), model="claude-opus-9", write=10_000)
    t.into(store)
    s = st.compute(store, NOW, WINDOW)
    assert (s.requests, s.spend, s.sessions) == (0, 0, 0)


def test_a_short_window_has_no_last_5_hours(store):
    build(store)
    assert st.compute(store, NOW, 3 * 3600).recent is None


def test_a_window_of_a_day_or_less_has_no_per_day_figure(store):
    build(store)
    assert st.compute(store, NOW, 86400).per_day is None
    assert "a day" not in render(store, ui.View(now=NOW, window=86400, mode="stats"), 160)


def test_kinds_with_no_tokens_are_left_out(store):
    t = Transcript()
    t.turn(at(29, 12), write=10_000, ttl="5m")  # no 1-hour writes, no reads
    t.into(store)
    assert [k.name for k in st.compute(store, NOW, WINDOW).kinds] == ["cache writes (5m)", "uncached input", "output"]


def test_folders_with_the_same_name_are_different_projects(store):
    for session, cwd in (("w", "/home/user/work/api"), ("p", "/home/user/personal/api"),
                         ("d1", "/Users/me/Library/Application Support/Claude/local-agent/one"),
                         ("d2", "/Users/me/Library/Application Support/Claude/local-agent/two")):
        t = Transcript(session=session, cwd=cwd)
        t.turn(at(29, 10), write=10_000)
        t.into(store)
    projects = {p.name: p.sessions for p in st.compute(store, NOW, WINDOW).projects}
    # Desktop sessions without a folder each get a scratch folder of their own: one row for all of them.
    assert projects == {"work/api": 1, "personal/api": 1, "no folder": 2}


def test_folder_names_take_as_much_of_the_path_as_they_need():
    assert st.folder_names(["/home/a/api", "/home/b/api", "/srv/web"]) == {
        "/home/a/api": "a/api", "/home/b/api": "b/api", "/srv/web": "web"}
    assert st.folder_names(["/a/x/api", "/b/x/api"]) == {"/a/x/api": "a/x/api", "/b/x/api": "b/x/api"}
    assert st.folder_names(["/api", "/x/api"]) == {"/api": "api", "/x/api": "x/api"}
    assert st.folder_names(["no folder", "?"]) == {"no folder": "no folder", "?": "?"}


def test_prompts_are_what_the_periods_requests_answer(store):
    t = Transcript()
    t.turn(at(26, 14, 59), text="started before the period", write=10_000, took=5)  # its answer: before
    t.tool_result(at(26, 15, 1))
    t.reply(at(26, 15, 1, 5), read=10_002, write=100)  # a step of it: in the period
    t.user("<command-name>/model</command-name>", at(27, 9))  # a built-in: sends nothing
    t.user("<command-name>/review</command-name>", at(27, 10))  # a skill: sends requests
    t.reply(at(27, 10, 0, 20), read=10_102, write=500)
    t.into(store)
    other = Transcript(session="mmmm-2222")  # a model with no known price: nothing of it counts
    for i in range(3):
        other.turn(at(28, 10, i), text=f"unpriced {i}", model="claude-mystery-9", write=10_000)
    other.into(store)
    s = st.compute(store, NOW, WINDOW)
    assert (s.requests, s.prompts, s.sessions) == (2, 2, 1)
    assert sorted((turn.text, turn.requests) for turn in s.turns) == [
        ("/review", 1), ("started before the period", 1)]  # only its step in the period


def test_more_than_5_projects_end_with_others(store):
    for i in range(7):
        t = Transcript(session=f"p{i}", cwd=f"/home/user/proj{i}")
        t.turn(at(29, 10, i), write=10_000 * (i + 1))
        t.into(store)
    projects = st.compute(store, NOW, WINDOW).projects
    assert [p.name for p in projects] == ["proj6", "proj5", "proj4", "proj3", "proj2", "others"]
    assert projects[-1].sessions == 2


def test_the_figures_are_kept_while_nothing_changes_and_refreshed_every_few_seconds_while_it_does(store):
    build(store)
    first = st.compute(store, NOW, WINDOW)
    assert st.compute(store, NOW + 30, WINDOW) is first  # nothing new
    t = Transcript(session="dddd-4444")
    t.turn(NOW - 10, write=1_000)
    t.into(store)
    # A busy session brings records every second: walking every request each time would be wasted.
    assert st.compute(store, NOW + st.REFRESH - 1, WINDOW) is first
    assert st.compute(store, NOW + st.REFRESH, WINDOW).requests == 9
    later = st.compute(store, NOW + 60, WINDOW)  # a new minute: the period moved on
    assert later is not first and later.requests == 9


def test_what_is_typed_counts_as_a_change(store):
    t = Transcript()
    t.turn(at(29, 12), text="one", write=1_000)
    t.into(store)
    before = store.revision
    t.user("/model sonnet", at(29, 12, 1))  # no request, but it starts a new turn
    records = list(t.records)
    t.into(store)
    assert store.revision == before + 1
    store.add_all(records)  # the same record read again changes nothing
    assert store.revision == before + 1


def test_day_labels():
    assert st.day_label("2026-09-04", "2026-09-29") == "Fri 4 Sep"
    assert st.day_label("2025-12-31", "2026-01-02") == "Wed 31 Dec 2025"
    assert st.day_label(st.EARLIER, "2026-09-29") == "earlier"


# --- On screen ---------------------------------------------------------------------


def render(store, view, width, height=200):
    from rich.console import Console
    console = Console(record=True, width=width, height=height, color_system=None)
    console.print(ui.render(store, view, height, width))
    return console.export_text()


@pytest.mark.parametrize("width", [80, 120, 160])
def test_every_panel_fits_without_wrapping(store, width):
    build(store)
    text = render(store, ui.View(now=NOW, window=WINDOW, mode="stats"), width)
    lines = text.splitlines()
    assert all(len(line) <= width for line in lines)
    for title in ("by day", "by model", "where the money goes", "cache misses", "by context size", "top sessions",
                  "by project", "costliest prompts"):
        assert f"╭─ {title}" in text, title
    # Every row on one line: a day with its spend and hit rate, a prompt with its spend.
    assert any("Mon 28 Sep" in line and "$0.33" in line and "0%" in line for line in lines)
    assert any("add tests" in line and "$0.33" in line for line in lines)
    side_by_side = any("╭─ by day" in line and "╭─ by model" in line for line in lines)
    assert side_by_side == (width >= 160)


def test_the_summary_reads_as_figures(store):
    build(store)
    text = render(store, ui.View(now=NOW, window=WINDOW, mode="stats"), 160)
    assert f"SPEND ${SPEND:,.2f} · ${SPEND / 3:,.2f} a day · last 5 hours ${C1 + C2:,.2f}" in text
    assert "8 requests from 5 prompts (1.6 each) in 3 sessions · subagents <1% of spend" in text
    assert f"cache misses · ${A1_MISS + A3_MISS:,.2f}" in text and "no cache misses" not in text


def test_a_miss_under_half_a_cent_is_still_a_miss_in_the_summary():
    prices = dict(PRICES, **{"claude-3-haiku": {"input": 0.25, "output": 1.25, "cache_read": 0.03,
                                                "cache_write": 0.30, "cache_write_1h": 0.50}})
    store = Store(prices)
    t = Transcript()
    t.turn(at(29, 10), model="claude-3-haiku-20240307", write=12_000, ttl="5m")
    t.turn(at(29, 11), model="claude-3-haiku-20240307", write=12_000, ttl="5m")  # the cache expired
    t.into(store)
    s = st.compute(store, NOW, WINDOW)
    assert 0 < s.misses < 0.005 and len(s.causes) == 1
    summary = " ".join(line.plain for line in ui.summary_lines(s, 200))
    assert "cache misses <$0.01" in summary and "no cache misses" not in summary  # as the panel below says
    today = next(line for line in render(store, ui.View(now=NOW, window=WINDOW, mode="stats"), 120).splitlines()
                 if "Tue 29 Sep" in line)
    assert today.rstrip(" │").endswith("<$0.01")  # and in its day's MISSES


def test_no_misses_is_said_in_green(store):
    t = Transcript()
    t.turn(at(29, 12), write=10_000)
    t.into(store)
    panel = ui.cache_misses(st.compute(store, NOW, WINDOW), "3d")
    assert panel.renderable.plain == "no cache misses in the last 3d" and str(panel.renderable.style) == "green"


def test_the_stats_scroll_one_row_at_a_time(store):
    build(store)
    view = ui.View(now=NOW, window=WINDOW, mode="stats")
    text = render(store, view, 160, height=30)
    assert "· rows 1–" in text and view.stats_page < 4
    ui.press(store, view, "down")
    text = render(store, view, 160, height=30)
    assert "╭─ by day" not in text and "╭─ where the money goes" in text and "g: back to the top" in text
    ui.press(store, view, "end")
    assert "╭─ costliest prompts" in render(store, view, 160, height=30)
    ui.press(store, view, "home")
    assert view.stats_scroll == 0


def test_an_empty_period_says_so(store):
    text = render(store, ui.View(now=NOW, window=WINDOW, mode="stats"), 120)
    assert "no priced requests in the last 3d" in text
