"""The screen, rendered to text, and the CLI."""
import argparse
import json
from collections import deque
from datetime import datetime

import pytest
from conftest import PROJECTS, T0, Transcript
from rich.console import Console

from usdash import app, ui
from usdash.prices import ONE_HOUR
from usdash.sessions import subscription_account
from usdash.transcripts import account_file


def screen(store, view, width=220, height=40) -> str:
    console = Console(record=True, width=width, height=height, color_system=None)
    console.print(ui.render(store, view, height, width))
    return console.export_text()


def two_sessions(store):
    a = Transcript(session="aaaa-1111", cwd="/home/user/shop", branch="checkout-fix")
    a.record("ai-title", aiTitle="Fix checkout totals")
    a.turn(T0, text="why is the total off by one cent", write=40_000, out=2_000)
    a.turn(T0 + 60, text="ok, fix it and add a test", read=40_000, write=2_000, out=2_000)
    b = Transcript(session="bbbb-2222", cwd="/home/user/shop", entrypoint="claude-vscode")
    b.record("custom-title", customTitle="Release notes")
    b.turn(T0 + 30, text="draft the notes", model="claude-sonnet-5", write=30_000, ttl="5m")
    a.into(store)
    b.into(store)


def test_sessions_are_told_apart_by_name_place_prompt_and_id(store):
    two_sessions(store)
    text = screen(store, ui.View(now=T0 + 60 + 600, subscription=True))
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if "Fix checkout totals" in line and "aaaa" in line)
    assert "shop@checkout-fix" in lines[i]
    assert "Opus 5.5 high" in lines[i] and "● 50:00" in lines[i] and " sub " in lines[i]
    assert '└ 10m ago · "ok, fix it and add a test"' in lines[i + 1]  # what was last typed in that window
    j = next(n for n, line in enumerate(lines) if "Release notes" in line and "bbbb" in line)
    assert "vscode api" in lines[j] and "○ cold" in lines[j] and "shop " in lines[j]
    assert '└ 10m ago · "draft the notes"' in lines[j + 1]


def test_a_narrow_terminal_keeps_the_last_prompt_and_cuts_the_least_useful_numbers(store):
    two_sessions(store)
    text = screen(store, ui.View(now=T0 + 60 + 600), width=100)
    assert '└ 10m ago · "ok, fix it and add a test"' in text
    row = next(line for line in text.splitlines() if "Fix checkout totals" in line and "aaaa" in line)
    assert "● 50:00" in row and "now ·" in row


def test_advice_names_the_session_not_just_its_id(store):
    two_sessions(store)
    text = screen(store, ui.View(now=T0 + 60 + 600, subscription=True))
    line = next(line for line in text.splitlines() if "Warm on Opus 5.5" in line)
    assert "aaaa Fix checkout totals · shop@checkout-fix: Warm on Opus 5.5 for 50:00 more" in line
    assert "(uses less of your plan)" in line


def test_header_shows_todays_spend_hit_rate_and_rewrites(store):
    two_sessions(store)
    t = Transcript(session="aaaa-1111", cwd="/home/user/shop", branch="checkout-fix")
    t.turn(T0 + 60 + 7200, text="back", write=44_000)  # after a 2-hour break
    t.into(store)
    text = screen(store, ui.View(now=T0 + 60 + 7300))
    assert "TODAY $" in text and "est." in text and "of input read from cache" in text
    assert "re-writes cost $" in text and "(cache expired $" in text
    assert "⟳ re-wrote 42k: cache expired (idle 120 min)" in text
    assert "API-equivalent" not in text  # an API-key account


def test_feed_marks_subagents_and_closed_sessions_show_claude_codes_total(fixture_store):
    last = max(s.last_activity for s in fixture_store.sessions.values())
    text = screen(fixture_store, ui.View(now=last + 60, window=10 ** 9), height=80)
    assert "🤖 subagent" in text
    assert "closed" in text and "(CC $" in text and "if resumed: $" in text
    assert "Three-word greeting desktop" in text
    assert "List .claude/skills" in text


def test_idle_and_archived_sessions_are_hidden(store):
    two_sessions(store)
    store.sessions["bbbb-2222"].archived = True
    text = screen(store, ui.View(now=T0 + 60 + 600))
    assert "Release notes vscode" not in text  # gone from the sessions pane (its requests stay in the feed)
    assert "Fix checkout totals api" in text
    text = screen(store, ui.View(now=T0 + 60 + 5 * 3600))
    assert "no Claude Code activity in the last 3h" in text


def test_scrolling_the_feed(store):
    t = Transcript()
    for i in range(30):
        t.turn(T0 + 60 * i, read=40_000 + i, write=100)
    t.into(store)
    view = ui.View(now=T0 + 1800)
    ui.track_feed(store, view)  # the app does this after every poll
    screen(store, view, height=30)
    assert view.page < 30
    ui.press(store, view, "down")
    assert view.scroll == 1
    ui.press(store, view, "end")
    assert view.scroll == 30 - view.page
    more = Transcript()
    more.turn(T0 + 1900, read=41_000, write=100)
    more.into(store)
    ui.track_feed(store, view)
    assert view.unseen == 1  # the rows in view stay put
    ui.press(store, view, "home")
    assert (view.scroll, view.unseen) == (0, 0)


def test_a_late_row_below_the_view_is_not_new_above(store):
    t = Transcript()
    for i in range(30):
        t.turn(T0 + 60 * i, read=40_000 + i, write=100)
    t.into(store)
    view = ui.View(now=T0 + 1800, page=5)
    ui.press(store, view, "pgdn")
    shown = list(store.feed)[5:10]
    late = Transcript()  # a subagent's transcript, found late: older than every row in view
    late.user("look around", T0 + 5, subagent="a1")
    late.reply(T0 + 8, subagent="a1", write=5_000, ttl="5m")
    late.into(store)
    ui.track_feed(store, view)
    assert (view.scroll, view.unseen) == (5, 0)
    assert list(store.feed)[5:10] == shown


def test_a_row_the_full_feed_drops_moves_nothing(store):
    store.feed = deque(maxlen=5)
    t = Transcript()
    for i in range(8):
        t.turn(T0 + 60 * i, read=40_000 + i, write=100)
    t.into(store)
    view = ui.View(now=T0 + 600)
    ui.press(store, view, "down")
    late = Transcript()  # older than everything the feed still keeps
    late.user("look around", T0 + 5, subagent="a1")
    late.reply(T0 + 8, subagent="a1", write=5_000, ttl="5m")
    late.into(store)
    ui.track_feed(store, view)
    assert (len(store.feed), view.scroll, view.unseen) == (5, 1, 0)
    newer = Transcript()  # a new row pushes the oldest out
    newer.turn(T0 + 600, read=41_000, write=100)
    newer.into(store)
    ui.track_feed(store, view)
    assert (len(store.feed), view.scroll, view.unseen) == (5, 2, 1)


def test_parse_keys():
    assert app.parse_keys("\x1b[Aj q\x1b[6~x") == ["up", "down", "pgdn", "quit", "pgdn"]


@pytest.mark.parametrize(("text", "seconds"), [("30m", 1800), ("2h", 7200), ("1d", 86400)])
def test_duration(text, seconds):
    assert app.duration(text) == seconds


@pytest.mark.parametrize("text", ["soon", "0m", "5", "2w"])
def test_bad_duration(text):
    with pytest.raises(argparse.ArgumentTypeError):
        app.duration(text)


def test_once_prints_a_screen_from_a_folder(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))  # no account file: API wording
    monkeypatch.setenv("COLUMNS", "200")
    app.main(["--projects", str(PROJECTS), "--since", "3650d", "--window", "3650d", "--once"])
    out = capsys.readouterr().out
    assert "usdash · live" in out and "Test plan vs test case" in out and "Pong reply" in out


def test_claude_config_dir_moves_the_account_file(monkeypatch, tmp_path):
    (tmp_path / ".claude.json").write_text(json.dumps({"oauthAccount": {"organizationType": "claude_max"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert account_file() == tmp_path / ".claude.json"
    assert subscription_account()  # its default follows CLAUDE_CONFIG_DIR too
    assert app.App(PROJECTS, since=0, window=ONE_HOUR, clock=lambda: T0).view.subscription


@pytest.mark.parametrize(("seconds", "text"), [(3600, "1h"), (5400, "90m"), (10800, "3h"), (86400, "1d"), (1800, "30m")])
def test_window_labels_are_exact(seconds, text):
    assert ui.duration_text(seconds) == text


def test_a_90_minute_window_is_labelled_as_such(store):
    two_sessions(store)
    assert "sessions active in the last 90m" in screen(store, ui.View(now=T0 + 660, window=5400))
    assert "no Claude Code activity in the last 90m" in screen(store, ui.View(now=T0 + 10 * 3600, window=5400))


def test_the_header_keeps_its_second_line_when_the_first_is_long(store):
    t = Transcript()
    t.turn(T0, write=40_000)
    t.turn(T0 + 60, model="claude-sonnet-5", write=41_000)  # model switch
    t.turn(T0 + 7300, model="claude-sonnet-5", write=42_000)  # cache expired
    t.record("system", T0 + 7310, subtype="compact_boundary", compactMetadata={"postTokens": 2_000})
    t.turn(T0 + 7320, model="claude-sonnet-5", read=10_000, write=30_000)  # /compact
    t.into(store)
    text = screen(store, ui.View(now=T0 + 7400, prices_verified="2026-09-26"), width=90)
    assert "re-writes cost" in text
    assert "list prices of 2026-09-26" in text


@pytest.mark.parametrize(
    ("now", "since", "window", "start"),
    [
        # 00:30 with a 3-hour window: reach back to 21:30 the day before, not just midnight.
        (datetime(2026, 9, 28, 0, 30).timestamp(), None, 3 * 3600, datetime(2026, 9, 27, 21, 30).timestamp()),
        # 15:00: midnight is further back than the window.
        (datetime(2026, 9, 28, 15, 0).timestamp(), None, 3 * 3600, datetime(2026, 9, 28, 0, 0).timestamp()),
        # --since further back than the window wins.
        (datetime(2026, 9, 28, 15, 0).timestamp(), 86400, 3 * 3600, datetime(2026, 9, 27, 15, 0).timestamp()),
        # --since shorter than the window: the window still loads, so every listed session is there.
        (datetime(2026, 9, 28, 15, 0).timestamp(), 3600, 3 * 3600, datetime(2026, 9, 28, 12, 0).timestamp()),
    ],
)
def test_history_reaches_back_to_the_window_after_midnight(now, since, window, start):
    assert app.history_start(now, since, window) == start


def test_long_advice_wraps_instead_of_being_cut(store):
    two_sessions(store)
    text = screen(store, ui.View(now=T0 + 60 + 600, subscription=True), width=100)
    assert "after that it's free." in text
    assert "…" not in "".join(line for line in text.splitlines() if "Warm on" in line)
