"""The screen, rendered to text, and the CLI."""
import argparse
import json
import time
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


def block(text: str, marker: str) -> list[str]:
    """The lines of the session whose row contains `marker`, up to the next session or section."""
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if marker in line)
    j = i + 1
    while j < len(lines) and lines[j].startswith("│       ") and lines[j].strip("│ "):
        j += 1
    return lines[i:j]


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


NOW = T0 + 660  # a is warm (1-hour cache), b has gone cold (5-minute cache)


def test_sessions_are_told_apart_by_name_place_and_id(store):
    two_sessions(store)
    text = screen(store, ui.View(now=NOW, subscription=True))
    a = block(text, "aaaa")
    assert "Fix checkout totals" in a[0] and "shop@checkout-fix  CLI" in a[0] and "Opus 5.5 high" in a[0]
    assert "● 50:00" in a[0] and " sub" not in a[0]  # the countdown shows the lifetime; no billing tag
    assert '├ 10m ago · "ok, fix it and add a test"' in a[1]  # what was last typed in that window
    b = block(text, "bbbb")
    assert "Release notes" in b[0] and "shop               IDE" in b[0] and "○ expired · 10m" in b[0]


def test_sections_and_counts(store):
    two_sessions(store)
    text = screen(store, ui.View(now=NOW))
    # A pane each for live and expired sessions (no one exited yet), with the column names lined up;
    # the last one says the window.
    titles = [line.split(" ─")[0] for line in text.splitlines() if line.startswith("╭─") and "usdash" not in line]
    assert titles == ["╭─ live · 1 session · cache warm", "╭─ expired · 1 session · last 5d · cache ran out, not exited"]
    names = [line for line in text.splitlines() if "SESSION" in line and "CACHE" in line]
    assert len(names) == 2 and names[0] == names[1]
    closing = Transcript(session="bbbb-2222")
    closing.record("cost-state", totalCostUSD=0.08)
    closing.into(store)
    assert "╭─ exited · 1 session · last 5d · claude --resume <id> ─" in screen(store, ui.View(now=NOW))


def test_a_live_session_shows_now_and_up_to(store):
    two_sessions(store)
    a = block(screen(store, ui.View(now=NOW)), "aaaa")
    # 42,002 tokens: read back at Opus 5.5's $0.20, or written again at its 1-hour $8.
    assert len(a) == 3 and a[2].strip("│ ") == (
        "└ next message re-sends 42k tokens: $0.01 now · up to $0.34 once the cache expires")


def test_an_idle_session_shows_what_coming_back_costs_at_most(store):
    two_sessions(store)
    b = block(screen(store, ui.View(now=NOW)), "bbbb")
    # 30,002 tokens written again on Sonnet 5's 5-minute cache: $2.50 a million.
    assert b[1].strip("│ ") == "└ continuing re-sends 30k tokens: up to $0.08"
    closing = Transcript(session="bbbb-2222")
    closing.record("cost-state", totalCostUSD=0.08)
    closing.into(store)
    b = block(screen(store, ui.View(now=NOW)), "bbbb")
    assert "exited · 10m" in b[0] and b[1].strip("│ ") == "└ resuming re-sends 30k tokens: up to $0.08"


def test_an_exited_session_still_warm_can_be_resumed_from_the_cache(store):
    two_sessions(store)
    closing = Transcript(session="aaaa-1111")
    closing.record("cost-state", totalCostUSD=0.50)
    closing.into(store)
    a = block(screen(store, ui.View(now=NOW)), "aaaa")
    assert "exited · ● 50:00" in a[0]
    assert a[1].strip("│ ") == "└ resuming re-sends 42k tokens: $0.01 now · up to $0.34 once the cache expires"


def test_a_model_with_no_known_price_shows_the_size_only(store):
    t = Transcript(session="mmmm-1111")
    t.turn(T0, text="try the new one", model="claude-opus-9", write=40_000)
    t.into(store)
    lines = block(screen(store, ui.View(now=T0 + 60)), "mmmm")
    assert lines[2].strip("│ ") == "└ next message re-sends 40k tokens"


def test_the_session_numbers_say_what_they_are(store):
    two_sessions(store)
    lines = screen(store, ui.View(now=NOW)).splitlines()
    i = next(n for n, line in enumerate(lines) if "SESSION" in line and "CACHE" in line)
    assert lines[i].split()[1:-1] == ["ID", "SESSION", "PROJECT", "WHERE", "MODEL", "CACHE", "CONTEXT", "TODAY", "TOTAL"]
    assert lines[i - 1].startswith("╭─ live")  # one line of names, under the pane's title


def test_an_exited_sessions_total_is_claude_codes_own(store):
    two_sessions(store)
    closing = Transcript(session="bbbb-2222")
    closing.record("cost-state", totalCostUSD=12.345)  # it counts requests the transcripts never log
    closing.into(store)
    lines = screen(store, ui.View(now=NOW)).splitlines()
    exited = next(line for line in lines if "bbbb" in line and "Release notes" in line)
    assert exited.split()[-3:] == ["$0.08", "$12.35", "│"]  # TODAY from the transcripts, TOTAL Claude Code's
    open_row = next(line for line in lines if "aaaa" in line and "Fix checkout totals" in line)
    assert open_row.split()[-2] == "$0.42" and "(CC" not in "\n".join(lines)


def test_right_after_compact_the_size_waits_for_the_next_request(store):
    t = Transcript()
    t.turn(T0, text="tidy the parser", write=40_000)
    t.record("system", T0 + 30, subtype="compact_boundary", compactMetadata={"postTokens": 3_000})
    t.into(store)
    lines = block(screen(store, ui.View(now=T0 + 60)), "tidy the parser")
    assert "compacted" in lines[0] and "k " not in lines[0].split("compacted")[1][:12]  # no estimate
    assert lines[2].strip("│ ") == "└ compacted: the next message measures the new size"
    t.turn(T0 + 90, read=36_000, write=8_000)
    t.into(store)
    lines = block(screen(store, ui.View(now=T0 + 120)), "tidy the parser")
    assert "44k" in lines[0] and lines[2].strip("│ ").startswith("└ next message re-sends 44k tokens: ")


def test_no_spend_today_shows_a_dash(store):
    now = datetime(2026, 9, 22, 1, 0).timestamp()  # 1 a.m.: the session ran yesterday evening
    t = Transcript()
    t.turn(now - 5 * 3600, text="late-night fix", write=40_000)
    t.into(store)
    row = block(screen(store, ui.View(now=now)), "late-night fix")[0]
    assert row.split()[-3:] == ["—", "$0.32", "│"]


def test_closed_script_runs_fold_into_one_row(store):
    for i in range(3):
        run = Transcript(session=f"run-{i}", cwd="/home/user/jobs", entrypoint="sdk-cli")
        run.turn(T0 + 60 * i, text="say OK", write=20_000)
        run.record("cost-state", totalCostUSD=0.1)
        run.into(store)
    still_open = Transcript(session="open-run", cwd="/home/user/jobs", entrypoint="sdk-cli")
    still_open.turn(T0 + 200, text="summarize the log", write=20_000, ttl="5m")
    still_open.into(store)
    text = screen(store, ui.View(now=T0 + 3600))
    rows = [line for line in text.splitlines() if " script " in line]
    assert len(rows) == 2
    assert "3 runs" in rows[1] and "jobs" in rows[1] and "exited · " in rows[1]
    assert "summarize the log" in rows[0]  # an open run shows like any session
    assert text.count("└ ") == 1  # only the open run has a line under it
    assert "╭─ live" not in text  # no pane without a session in it
    assert "expired · 1 session · cache ran out, not exited" in text  # the run still going (or killed)
    assert "exited · 3 sessions · last 5d" in text  # the folded row counts every run in it


def test_archived_and_long_idle_sessions_are_hidden(store):
    two_sessions(store)
    store.sessions["bbbb-2222"].archived = True
    text = screen(store, ui.View(now=NOW))
    assert "Release notes" not in text  # gone from the sessions (its requests still count in Stats)
    assert "Fix checkout totals" in text
    assert "no Claude Code activity in the last 5d" in screen(store, ui.View(now=T0 + 6 * 86400))


def test_a_narrow_terminal_cuts_lines_and_never_wraps(store):
    two_sessions(store)
    wide, narrow = screen(store, ui.View(now=NOW)), screen(store, ui.View(now=NOW), width=100)
    assert len(block(narrow, "aaaa")) == len(block(wide, "aaaa"))
    assert all(len(line) <= 100 for line in narrow.splitlines())
    assert '├ 10m ago · "ok, fix it and add a test"' in narrow


def test_scrolling_the_sessions_keeps_a_live_one_whole(store):
    two_sessions(store)
    for i in range(12):
        idle = Transcript(session=f"idle-{i:02}", cwd="/home/user/other")
        idle.turn(T0 - 600 - 60 * i, text=f"idle task {i:02}", write=30_000, ttl="5m")
        idle.into(store)
    view = ui.View(now=NOW)
    text = screen(store, view, height=30)
    assert "· rows 1–6 of 14" in text and len(block(text, "aaaa")) == 3  # the live one, whole, and five idle
    assert "expired · 13 sessions · rows 1–6 of 14 ·" in screen(store, view, width=80, height=30)  # before the hint
    ui.press(store, view, "down")
    text = screen(store, view, height=30)
    assert "aaaa" not in text and "· rows 2–" in text and "g: back to the top" in text
    assert "╭─ live" not in text  # the live pane scrolled away with its only session
    ui.press(store, view, "end")
    text = screen(store, view, height=30)
    assert "of 14" in text and "idle task 11" in text  # the oldest, at the bottom of the last page
    ui.press(store, view, "home")
    assert view.session_scroll == 0
    # Keys between two frames each count: End then up is one above the end.
    ui.press(store, view, "end")
    ui.press(store, view, "up")
    assert view.session_scroll == view.session_last - 1 > 0


@pytest.mark.parametrize(("ttl", "left", "style"), [
    ("5m", 151, "green"), ("5m", 150, "yellow"),  # the last half of a 5-minute cache
    ("1h", 601, "green"), ("1h", 600, "yellow"),  # the last 10 minutes of a 1-hour one
])
def test_the_countdown_turns_yellow_near_the_end(store, ttl, left, style):
    t = Transcript(session="eeee-1111")
    t.turn(T0, text="read the whole codebase", write=40_000, ttl=ttl)
    t.into(store)
    session = store.sessions["eeee-1111"]
    lifetime = 300 if ttl == "5m" else ONE_HOUR
    now = T0 + lifetime - left  # the clock runs from the request's start: when the prompt was sent
    cell = ui.cache_cell(session, ui.View(now=now))
    assert cell.plain == f"● {left // 60}:{left % 60:02d}" and str(cell.style) == style
    line = ui.resend_line(store, session, ui.View(now=now))
    at = line.plain.index("up to $") + len("up to ")
    yellow = [span for span in line.spans if span.start == at and str(span.style) == "yellow"]
    assert bool(yellow) == (style == "yellow")  # what's at stake, as the clock runs out


def test_a_session_still_at_work_after_its_cache_expired_says_so(store):
    t = Transcript(session="wwww-1111", cwd="/home/user/other")
    t.turn(T0, text="run the slow step", write=40_000, ttl="5m", stop="tool_use")  # a tool that runs 6 minutes
    t.into(store)
    row = block(screen(store, ui.View(now=T0 + 370)), "wwww")[0]
    assert "○ expired · working" in row

    row = block(screen(store, ui.View(now=T0 + 40 * 60)), "wwww")[0]
    assert "○ expired · 39m" in row  # 39 minutes without a word: no longer at work


def test_a_session_waiting_on_a_subagent_after_its_cache_expired_says_so(store):
    t = Transcript(session="ssss-1111", cwd="/home/user/other")
    t.turn(T0, text="use a subagent", write=40_000, ttl="5m", stop="end_turn")
    t.reply(T0 + 360, subagent="agent-1", write=25_000)
    t.into(store)
    assert "○ expired · subagent" in block(screen(store, ui.View(now=T0 + 370)), "ssss")[0]


def test_an_app_usdash_doesnt_know_shows_as_claude_code_names_it(store):
    t = Transcript(session="jjjj-1111", entrypoint="claude-jetbrains")
    t.turn(T0, text="hello from the ide", write=40_000)
    t.into(store)
    row = block(screen(store, ui.View(now=T0 + 60)), "jjjj")[0]
    assert "claude-jetbrains" in row and "script" not in row


def test_a_fast_session_says_so(store):
    t = Transcript(session="ffff-1111")
    t.turn(T0, text="ship it fast", write=40_000, speed="fast")
    t.turn(T0 + 60, text="and the tests", read=40_000, write=2_000, speed="fast")
    t.into(store)
    lines = block(screen(store, ui.View(now=T0 + 120)), "ffff")
    assert "Opus 5.5 high fast" in lines[0]
    assert "$0.02 now · up to $0.67" in lines[2]  # fast mode's $0.40 read and $16 1-hour write


def test_the_last_session_can_always_be_scrolled_to(store):
    # Two sections on the last page take two labels: at every height, the oldest can be reached.
    two_sessions(store)
    more = Transcript(session="cccc-3333", cwd="/home/user/shop")
    more.turn(T0 + 100, text="another live one", write=40_000, out=2_000)
    more.turn(T0 + 160, read=40_000, write=2_000, out=2_000)
    old = Transcript(session="dddd-4444", cwd="/home/user/other")
    old.turn(T0 - 5000, text="the oldest", write=30_000, ttl="5m")
    more.into(store)
    old.into(store)
    for height in range(12, 60):
        view = ui.View(now=NOW)
        screen(store, view, height=height)
        ui.press(store, view, "end")
        assert "dddd" in screen(store, view, height=height), height


def test_only_the_markers_are_dim(store):
    two_sessions(store)
    view = ui.View(now=NOW)
    live, idle = store.sessions["aaaa-1111"], store.sessions["bbbb-2222"]
    lines = [*ui.tree([ui.prompt_text(live, view), ui.resend_line(store, live, view)]),
             *ui.tree([ui.resend_line(store, idle, view)])]
    for line in lines:
        assert not line.style, line.plain  # a base style would cover the amounts too
    dim = [line.plain[span.start:span.end] for line in lines for span in line.spans if span.style == "dim"]
    assert "├ " in dim and "└ " in dim and not any("$" in text for text in dim)


def test_s_swaps_views_and_each_keeps_its_scroll(store):
    two_sessions(store)
    busy = Transcript(session="cccc-3333")
    busy.turn(T0 - 3600, read=40_000, write=100)
    busy.into(store)
    view = ui.View(now=NOW)
    ui.press(store, view, "stats")
    assert view.mode == "stats" and "usdash · stats" in screen(store, view, width=100, height=20)
    ui.press(store, view, "down")
    ui.press(store, view, "stats")
    assert (view.mode, view.stats_scroll, view.session_scroll) == ("sessions", 1, 0)
    screen(store, view, height=15)  # too short for all three sessions
    ui.press(store, view, "down")
    ui.press(store, view, "stats")
    assert (view.mode, view.stats_scroll, view.session_scroll) == ("stats", 1, 1)


def test_header_shows_todays_spend_hit_rate_and_rewrites(store):
    two_sessions(store)
    t = Transcript(session="aaaa-1111", cwd="/home/user/shop", branch="checkout-fix")
    t.turn(T0 + 60 + 7200, text="back", write=44_000)  # after a 2-hour break
    t.into(store)
    text = screen(store, ui.View(now=T0 + 60 + 7300))
    assert "TODAY $" in text and "of input read from cache" in text
    assert "⟳ cache misses added $" in text and ": cache expired $" in text
    assert "Estimated at current API list prices" in text
    assert "Amounts can be lower than actual: Claude Code doesn't log some requests (titles, suggestions…)." in text
    assert "subscription" not in text  # an API-key account
    subscription = screen(store, ui.View(now=T0 + 60 + 7300, subscription=True))
    assert "At current API list prices; your subscription isn't billed per token" in subscription


def test_closed_sessions_show_claude_codes_total_and_the_way_back(fixture_store):
    last = max(s.last_activity for s in fixture_store.sessions.values())
    text = screen(fixture_store, ui.View(now=last + 60, window=10 ** 9), height=80)
    assert "exited · " in text and "└ resuming re-sends" in text
    # Its TOTAL is Claude Code's own $1.41, above the $1.33 its transcript logged; TODAY stays the transcript's.
    row = next(line for line in text.splitlines() if "Test plan vs test case" in line)
    assert row.split()[-2] == "$1.41"
    assert "List .claude/skills" in text
    assert "Desktop" in next(line for line in text.splitlines() if "Three-word greeting" in line)


def test_parse_keys():
    assert app.parse_keys("\x1b[Aj q\x1b[6~xs") == ["up", "down", "pgdn", "quit", "pgdn", "stats"]
    assert app.parse_keys("r") == []  # the request list is gone


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
    app.main(["--projects", str(PROJECTS), "--since", "3650d", "--window", "3650d", "--once", "--offline"])
    out = capsys.readouterr().out
    assert "usdash · sessions" in out and "Test plan vs test case" in out and "Pong reply" in out


def test_claude_config_dir_moves_the_account_file(monkeypatch, tmp_path):
    (tmp_path / ".claude.json").write_text(json.dumps({"oauthAccount": {"organizationType": "claude_max"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert account_file() == tmp_path / ".claude.json"
    assert subscription_account()  # its default follows CLAUDE_CONFIG_DIR too
    assert app.App(PROJECTS, since=0, window=ONE_HOUR, clock=lambda: T0).view.subscription


@pytest.mark.parametrize(("seconds", "text"), [(3600, "1h"), (5400, "90m"), (10800, "3h"), (86400, "24h"), (172800, "2d"), (1800, "30m")])
def test_window_labels_are_exact(seconds, text):
    assert ui.duration_text(seconds) == text


def test_a_90_minute_window_is_labelled_as_such(store):
    two_sessions(store)
    assert "expired · 1 session · last 90m" in screen(store, ui.View(now=T0 + 660, window=5400))
    assert "no Claude Code activity in the last 90m" in screen(store, ui.View(now=T0 + 10 * 3600, window=5400))


def test_cache_misses_get_a_header_line_of_their_own(store):
    start = datetime(2026, 9, 21, 11, 0).timestamp()  # local time: every request below falls on the same local day
    view = ui.View(now=start + 7400, prices=app.prices_label("2026-09-26", offline=False))
    t = Transcript()
    t.turn(start, write=40_000)
    t.into(store)
    lines = screen(store, view, width=90).splitlines()
    assert lines[1].strip("│ ").startswith("TODAY $") and lines[2].strip("│ ").startswith("Estimated at")  # no misses, no line
    t = Transcript()
    t.turn(start + 60, model="claude-sonnet-5", write=41_000)  # model switch
    t.turn(start + 7300, model="claude-sonnet-5", write=42_000)  # cache expired
    t.record("system", start + 7310, subtype="compact_boundary", compactMetadata={"postTokens": 2_000})
    t.turn(start + 7320, model="claude-sonnet-5", read=10_000, write=30_000)  # /compact
    t.into(store)
    lines = [line.strip("│ ") for line in screen(store, view, width=90).splitlines()]
    assert lines[1].startswith("TODAY $") and "⟳" not in lines[1]
    # Every cause, the biggest first, where a narrow terminal still has room for them.
    assert lines[2].startswith("⟳ cache misses added $") and "cache expired $" in lines[2] and "model switch $" in lines[2]
    assert lines[2].index("cache expired") < lines[2].index("model switch")
    assert lines[3] == "Estimated at API list prices of Sep 26 (couldn't refresh them)"
    assert lines[4].startswith("Amounts can be lower than actual") and lines[5].startswith("╰")


def test_warnings_come_first_on_the_headers_second_line(store):
    two_sessions(store)
    unknown = Transcript(session="cccc-3333")
    unknown.turn(T0, model="claude-mystery-9")
    unknown.into(store)
    text = screen(store, ui.View(now=NOW, docs_changed=["pricing"]), width=140)  # the README's width
    assert "Estimated at current API list prices  ·  1 request with no known price, left out (Mystery 9)  ·  " in text
    assert "Anthropic's pricing page changed: usdash may need an update" in screen(
        store, ui.View(now=NOW, docs_changed=["pricing"]), width=220)


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


def test_the_default_window_is_5_days(capsys, monkeypatch, tmp_path):
    folder = tmp_path / "projects" / "-home-user-proj"
    folder.mkdir(parents=True)
    now = time.time()
    for session, hours, text in (("recent", 4 * 24, "from 4 days ago"), ("older", 6 * 24, "from 6 days ago")):
        t = Transcript(session=session)
        t.turn(now - hours * 3600, text=text, write=40_000)
        (folder / f"{session}.jsonl").write_text("".join(json.dumps(r.data) + "\n" for r in t.records))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")
    app.main(["--projects", str(tmp_path / "projects"), "--once", "--offline"])
    out = capsys.readouterr().out
    assert "expired · 1 session · last 5d" in out and "from 4 days ago" in out and "from 6 days ago" not in out


def test_a_miss_cause_under_half_a_cent_is_left_off_the_header(store):
    two_sessions(store)
    today = ui.day_of(NOW)
    store.rewrites[today].update({"model switch": 0.05, "effort change": 0.004})
    line = next(line for line in ui.top_lines(store, ui.View(now=NOW)) if line.plain.startswith("⟳"))
    assert line.plain == "⟳ cache misses added $0.05: model switch $0.05"


def test_cached_is_coloured_by_how_much_was_read_back():
    # The README: green from 80%, yellow from 30%, red below.
    assert [str(ui.cached_text(share).style) for share in (0.8, 0.3, 0.29)] == ["green", "yellow", "red"]
    assert ui.cached_text(None).plain == "—"


def test_stats_prints_the_stats_view(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "160")
    app.main(["--projects", str(PROJECTS), "--window", "3650d", "--once", "--offline", "--stats"])
    out = capsys.readouterr().out
    assert "usdash · stats" in out and "summary · last 3650d" in out and "╭─ by day" in out


def test_version(capsys):
    with pytest.raises(SystemExit):
        app.main(["--version"])
    assert capsys.readouterr().out.startswith("usdash ")
