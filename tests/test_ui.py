"""The screen, rendered to text, and the CLI."""
import json
import os
import time
from datetime import datetime

import pytest
from conftest import PROJECTS, T0, Transcript
from rich.console import Console

from usdash import app, stats, ui
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
    assert a[2].strip("│ ") == "└ resuming re-sends 42k tokens: $0.01 now · up to $0.34 once the cache expires"


def test_an_exited_session_says_what_claude_code_counted_beyond_its_transcripts(store):
    two_sessions(store)
    closing = Transcript(session="aaaa-1111")
    closing.record("cost-state", totalCostUSD=0.50)
    closing.into(store)
    a = block(screen(store, ui.View(now=NOW)), "aaaa")
    gap = store.sessions["aaaa-1111"].claude_total - store.sessions["aaaa-1111"].cost_at_state
    assert a[1].strip("│ ") == f"├ Claude Code counted ${gap:.2f} more than its transcripts show (requests it doesn't log)"
    # Under a cent, or at what the transcripts show, TOTAL and TODAY agree: nothing to explain.
    closing = Transcript(session="aaaa-1111")
    closing.record("cost-state", totalCostUSD=store.sessions["aaaa-1111"].cost_total + 0.004)
    closing.into(store)
    a = block(screen(store, ui.View(now=NOW)), "aaaa")
    assert not any("Claude Code counted" in line for line in a)


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


def test_which_sessions_show_is_counted_as_the_panes_count_them(store):
    # A folded row of 3 script runs is 3 sessions in its pane's title, and so in the header's range:
    # a count of rows (13) next to the pane's count of sessions (15) wouldn't add up.
    for i in range(3):
        run = Transcript(session=f"run-{i}", cwd="/home/user/jobs", entrypoint="sdk-cli")
        run.turn(T0 + 60 * i, text="say OK", write=20_000)
        run.record("cost-state", totalCostUSD=0.1)
        run.into(store)
    for i in range(12):
        old = Transcript(session=f"old-{i:02}", cwd="/home/user/other")
        old.turn(T0 - 600 - 60 * i, text=f"old task {i:02}", write=30_000)
        old.record("cost-state", totalCostUSD=0.2)
        old.into(store)
    view = ui.View(now=T0 + 3600)
    text = screen(store, view, height=20)
    assert "exited · 15 sessions" in text and "rows" not in text
    title = next(line for line in text.splitlines() if "usdash · sessions" in line)
    assert "usdash · sessions 1–" in title and " of 15 " in title
    ui.press(store, view, "down")  # past the folded row: its 3 runs
    assert "usdash · sessions 4–" in screen(store, view, height=20)
    assert "usdash · sessions ─" in screen(store, ui.View(now=T0 + 3600), height=80)  # all of them fit: no range


def test_an_archived_session_is_in_the_exited_pane_and_says_so(store):
    # Archived in the Desktop app: done with, but its spend and what coming back costs still show.
    two_sessions(store)
    store.sessions["bbbb-2222"].archived = True
    b = block(screen(store, ui.View(now=NOW)), "bbbb")
    assert "archived · 10m" in b[0] and b[1].strip("│ ") == "└ resuming re-sends 30k tokens: up to $0.08"
    assert "╭─ exited · 1 session" in screen(store, ui.View(now=NOW))
    store.sessions["aaaa-1111"].archived = True  # still warm: it's out of the live pane all the same
    a = block(screen(store, ui.View(now=NOW)), "aaaa")
    assert "archived · ● 50:00" in a[0] and "╭─ live" not in screen(store, ui.View(now=NOW))


def test_long_idle_sessions_are_hidden(store):
    two_sessions(store)
    assert "no Claude Code activity in the last 5d" in screen(store, ui.View(now=T0 + 6 * 86400))


def test_folded_runs_show_their_model_and_effort_only_when_they_share_them(store):
    def runs(folder, *models):
        for i, (model, effort) in enumerate(models):
            run = Transcript(session=f"{folder}-{i}", cwd=f"/home/user/{folder}", entrypoint="sdk-cli")
            run.turn(T0 + 60 * i, text="say OK", model=model, effort=effort, write=20_000)
            run.record("cost-state", totalCostUSD=0.2)
            run.into(store)
    runs("same", ("claude-opus-5-5", "high"), ("claude-opus-5-5", "high"))
    runs("effort", ("claude-opus-5-5", "high"), ("claude-opus-5-5", "low"))
    runs("models", ("claude-opus-5-5", "high"), ("claude-sonnet-5-5", "high"), ("claude-opus-5", "high"))
    rows = {line.split()[3]: line for line in screen(store, ui.View(now=T0 + 3600)).splitlines() if " runs " in line}
    assert "Opus 5.5 high" in rows["same"]
    assert "Opus 5.5 · mixed" in rows["effort"]
    assert "3 models" in rows["models"] and "Opus 5.5" not in rows["models"]


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
    # Which sessions show is the header's: the panes' own titles count only their own (1 + 13 = 14).
    assert "usdash · sessions 1–6 of 14" in text and len(block(text, "aaaa")) == 3  # the live one, whole, and five idle
    assert "expired · 13 sessions · last 5d · cache ran out" in text
    assert "usdash · sessions 1–6 of 14" in screen(store, view, width=80, height=30)
    ui.press(store, view, "down")
    text = screen(store, view, height=30)
    assert "aaaa" not in text and "usdash · sessions 2–" in text and "g: back to the top" in text
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


def offline(monkeypatch):
    """main() reads the pricing page at start: not in a test."""
    monkeypatch.setattr(app, "fetch_text", lambda url, timeout=0: None)


def test_once_prints_a_screen_from_claude_codes_folder(capsys, monkeypatch, fixture_store):
    # The transcripts folder is $CLAUDE_CONFIG_DIR/projects; the fixtures' folder has no account file: API wording.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(PROJECTS.parent))
    monkeypatch.setenv("COLUMNS", "200")
    offline(monkeypatch)
    last = max(s.last_activity for s in fixture_store.sessions.values())
    monkeypatch.setattr(app.time, "time", lambda: last + 60)  # the fixtures' sessions, within the 5-day window
    app.main(["--once"])
    out = capsys.readouterr().out
    assert "usdash · sessions" in out and "Pong reply" in out and "Three-word greeting" in out
    assert "Test plan vs test case" not in out  # 5 days before the others: outside the window


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
    ("now", "window", "start"),
    [
        # 00:30 with a 3-hour window: reach back to 21:30 the day before, not just midnight.
        (datetime(2026, 9, 28, 0, 30).timestamp(), 3 * 3600, datetime(2026, 9, 27, 21, 30).timestamp()),
        # 15:00: midnight is further back than the window.
        (datetime(2026, 9, 28, 15, 0).timestamp(), 3 * 3600, datetime(2026, 9, 28, 0, 0).timestamp()),
    ],
)
def test_history_reaches_back_to_the_window_after_midnight(now, window, start):
    assert app.history_start(now, window) == start


def test_a_poll_is_a_change_when_any_session_gained_a_request(tmp_path):
    folder = tmp_path / "-home-user-proj"
    folder.mkdir()
    first, last = Transcript(session="aaaa"), Transcript(session="bbbb")  # read in that order
    first.turn(T0, write=40_000)
    last.turn(T0, write=40_000)
    for t in (first, last):
        (folder / f"{t.session}.jsonl").write_text("".join(json.dumps(r.data) + "\n" for r in t.records))
    dash = app.App(tmp_path, since=0, window=ONE_HOUR, clock=lambda: T0 + 60)
    assert dash.poll()
    more_first, more_last = Transcript(session="aaaa"), Transcript(session="bbbb")
    more_first.turn(T0 + 30, read=40_002, write=100, message_id="m2")
    more_last.record("custom-title", customTitle="Renamed")  # no request
    for t in (more_first, more_last):
        with (folder / f"{t.session}.jsonl").open("a") as file:
            file.write("".join(json.dumps(r.data) + "\n" for r in t.records))
    assert dash.poll()  # the first session's new request counts, whatever came after it
    assert not dash.poll()


def test_an_older_transcript_on_disk_means_the_history_reaches_back_past_it(tmp_path):
    # Too old to read for the period, but there: the days before the first record read weren't missing data.
    folder = tmp_path / "-home-user-proj"
    folder.mkdir()
    for session, when in (("new", T0), ("old", T0 - 40 * 86400)):
        t = Transcript(session=session)
        t.turn(when, write=40_000)
        path = folder / f"{session}.jsonl"
        path.write_text("".join(json.dumps(r.data) + "\n" for r in t.records))
        os.utime(path, (when + 60,) * 2)
    dash = app.App(tmp_path, since=T0 - 30 * 86400, window=ONE_HOUR, clock=lambda: T0 + 60)
    dash.poll()
    assert "old" not in dash.store.sessions  # not read
    assert dash.store.history_from == pytest.approx(T0 - 40 * 86400 + 60)  # its last write
    assert stats.compute(dash.store, T0 + 60, 30 * 86400).since is None


def test_history_reaches_back_to_the_stats_period():
    now = datetime(2026, 9, 28, 15, 0).timestamp()
    assert app.history_start(now, 5 * 86400, 30 * 86400) == now - 30 * 86400
    assert app.history_start(now, 40 * 86400, 30 * 86400) == now - 40 * 86400  # or the window, if that's longer



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
    offline(monkeypatch)
    app.main(["--once"])
    out = capsys.readouterr().out
    assert "expired · 1 session · last 5d" in out and "from 4 days ago" in out and "from 6 days ago" not in out


def test_a_miss_cause_under_half_a_cent_is_left_off_the_header(store):
    two_sessions(store)
    today = ui.day_of(NOW)
    store.rewrites[today].update({"model switch": 0.05, "effort change": 0.004})
    line = next(line for line in ui.top_lines(store, ui.View(now=NOW)) if line.plain.startswith("⟳"))
    assert line.plain == "⟳ cache misses added $0.05: model switch $0.05"


def test_a_share_read_from_cache_never_rounds_up_to_100_percent():
    # 99.65% of a day's input read back, beside $0.28 of misses, read "100%".
    assert [ui.cached_share(x) for x in (0.8649, 0.9899, 0.9965, 0.99999, 1.0)] == ["86%", "99%", "99.6%", "99.9%", "100%"]


def test_the_share_read_from_cache_has_one_colour_rule():
    # The README: green from 90%, yellow from 30%, red below.
    assert [ui.cached_style(share) for share in (0.9, 0.89, 0.3, 0.29)] == ["green", "yellow", "yellow", "red"]
    assert ui.cached_text(None).plain == "—"


@pytest.mark.parametrize(("read", "share", "colour"), [(38_000, "95%", "green"), (34_000, "85%", "yellow"),
                                                        (8_000, "20%", "red")])
def test_the_header_the_summary_and_by_day_colour_the_same_share_alike(store, read, share, colour):
    # They once had rules of their own: 85% was green in by day but yellow above it, 20% red there only.
    t = Transcript()
    t.turn(T0, read=read, write=40_000 - read)  # `read` of 40,002 tokens read back
    t.into(store)
    figures = stats.compute(store, T0 + 60, 86400)
    header = ui.top_lines(store, ui.View(now=T0 + 60))[0]
    summary = ui.summary_lines(figures, 200)[0]

    def style(line, words):
        return next(str(span.style) for span in line.spans if words in line.plain[span.start:span.end])

    assert style(header, f"{share} of input read from cache") == style(summary, f"{share} read from cache") == colour
    assert str(ui.cached_text(figures.days[-1].cached).style) == colour  # its day, in by day


def test_the_stats_cover_30_days_by_default_and_the_sessions_5(capsys, monkeypatch, tmp_path):
    folder = tmp_path / "projects" / "-home-user-proj"
    folder.mkdir(parents=True)
    now = time.time()
    for session, days, text in (("week", 7, "from a week ago"), ("month", 29, "from 29 days ago"),
                                ("older", 31, "from 31 days ago")):
        t = Transcript(session=session)
        t.turn(now - days * 86400, text=text, write=40_000)
        path = folder / f"{session}.jsonl"
        path.write_text("".join(json.dumps(r.data) + "\n" for r in t.records))
        os.utime(path, (now - days * 86400 + 60,) * 2)  # last written then: loaded only if history reaches back
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")
    dash = app.App(tmp_path / "projects", app.history_start(now, ui.DEFAULT_WINDOW, ui.DEFAULT_PERIOD),
                   ui.DEFAULT_WINDOW, clock=lambda: now)  # as main() starts it
    dash.poll()
    dash.view.mode = "stats"
    dash.view.now = now
    out = screen(dash.store, dash.view, width=200, height=60)  # room for every row of panels
    assert "summary · last 30d" in out and "2 requests from 2 prompts" in out
    assert "from a week ago" in out and "from 29 days ago" in out and "from 31 days ago" not in out
    capsys.readouterr()  # the screen above printed too
    offline(monkeypatch)
    app.main(["--once"])
    out = capsys.readouterr().out  # the sessions: 5 days, as before
    assert "no Claude Code activity in the last 5d" in out and "from a week ago" not in out


def test_version(capsys):
    with pytest.raises(SystemExit):
        app.main(["--version"])
    assert capsys.readouterr().out.startswith("usdash ")
