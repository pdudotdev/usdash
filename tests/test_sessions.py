"""Sessions from records: dedupe, request starts, re-writes, names and the account."""
import json

import pytest
from conftest import (
    COMPACTED, DESKTOP_SESSION, NO_REQUEST_IDS, RECAP, T0, VSCODE, Transcript,
)

from usdash.prices import FIVE_MINUTES, ONE_HOUR
from usdash.sessions import Request, Store, command_text, desktop_sessions, subscription_account

# --- Requests and cost -----------------------------------------------------------


def test_replies_without_request_ids_are_still_separate_requests(fixture_store):
    # This real session's replies carry no requestId: keyed by it, all six
    # would collapse into one and the cost would read $0.04, not $1.33.
    session = fixture_store.sessions[NO_REQUEST_IDS]
    assert len(session.requests) == 6
    assert session.cost_total == pytest.approx(1.3277, abs=1e-4)
    assert session.cost_state == pytest.approx(1.4117, abs=1e-4)  # Claude Code's own, background requests included


def test_desktop_session_cost_matches_claude_codes_own_total(fixture_store):
    session = fixture_store.sessions[DESKTOP_SESSION]
    assert session.cost_total == pytest.approx(session.cost_state, abs=1e-4)


def test_streamed_blocks_are_one_request_and_the_last_record_wins(store):
    t = Transcript()
    t.user("hi", T0)
    t.reply(T0 + 5, read=40_000, write=1_000, out=50, blocks=3, message_id="msg_a")
    # A later record of the same reply with the final output count.
    t.reply(T0 + 9, read=40_000, write=1_000, out=900, message_id="msg_a")
    t.into(store)
    session = store.sessions["sess-1"]
    assert len(session.requests) == 1
    request = session.requests["msg_a"]
    assert request.usage["output"] == 900
    assert len(store.feed) == 1
    # Totals were corrected, not added twice.
    assert session.cost_total == pytest.approx(request.cost)
    assert store.days[next(iter(store.days))]["requests"] == 1


def test_a_request_starts_when_the_message_it_answers_was_written(store):
    t = Transcript()
    t.user("hi", T0)
    t.reply(T0 + 30, write=40_000, blocks=3)  # three blocks, 30-32 s after the prompt
    t.tool_result(T0 + 60)
    t.reply(T0 + 65, read=40_000, write=500)
    t.into(store)
    first, second = sorted(store.sessions["sess-1"].requests.values(), key=lambda r: r.start)
    assert (first.start, first.end) == (T0, T0 + 32)
    assert second.start == T0 + 60  # the tool result, not the previous reply
    assert store.sessions["sess-1"].main.touched == T0 + 60


def test_synthetic_and_error_replies_are_skipped(store):
    t = Transcript()
    t.user("hi", T0)
    t.reply(T0 + 1, model="<synthetic>")
    t.reply(T0 + 2, isApiErrorMessage=True)
    t.into(store)
    assert store.sessions["sess-1"].requests == {}


def test_models_missing_from_pricing_are_counted_not_priced(store):
    t = Transcript()
    t.turn(T0, model="claude-mystery-9")
    t.into(store)
    (request,) = store.sessions["sess-1"].requests.values()
    assert request.cost is None
    assert store.unpriced == {"Mystery 9": 1}
    # Fast mode on a model whose fast mode prices aren't known: left out too, not priced at half.
    t.turn(T0 + 60, model="claude-opus-4-7", speed="fast")
    t.into(store)
    assert store.sessions["sess-1"].last_request.cost is None and store.unpriced["Opus 4.7 fast"] == 1


# --- Cache lifetime and the account -----------------------------------------------


def test_cache_lifetime_comes_from_the_writes(fixture_store):
    assert fixture_store.sessions[NO_REQUEST_IDS].ttl == FIVE_MINUTES
    assert fixture_store.sessions[VSCODE].ttl == ONE_HOUR
    assert fixture_store.sessions[DESKTOP_SESSION].ttl == ONE_HOUR


@pytest.mark.parametrize(
    ("account", "expected"),
    [
        ({"oauthAccount": {"billingType": "stripe_subscription", "organizationType": "claude_pro"}}, True),
        ({"oauthAccount": {"billingType": "", "organizationType": "claude_max"}}, True),
        ({"oauthAccount": {"billingType": "api", "organizationType": "console"}}, False),
        ({}, False),
    ],
)
def test_subscription_account(tmp_path, account, expected):
    path = tmp_path / ".claude.json"
    path.write_text(json.dumps(account))
    assert subscription_account(path) is expected


def test_no_account_file_means_not_a_subscription(tmp_path):
    assert subscription_account(tmp_path / "missing.json") is False


# --- Re-writes and why -------------------------------------------------------------


def conversation(store: Store, **last) -> Request:
    """Two warm turns on Opus 5.5 (1-hour cache), then one more built from `last`."""
    t = Transcript()
    t.turn(T0, write=40_000)
    t.turn(T0 + 60, read=40_000, write=2_000)
    t.user("next", T0 + last.pop("after", 120), version=last.get("version") or t.version)
    t.reply(T0 + 130, **last)
    t.into(store)
    return max(store.sessions["sess-1"].requests.values(), key=lambda r: r.start)


def test_a_warm_turn_is_not_a_rewrite(store):
    request = conversation(store, read=42_000, write=1_000)
    assert request.reason is None
    assert request.rewritten == 0


def test_model_switch(store):
    request = conversation(store, model="claude-sonnet-5", write=43_000)
    assert request.reason == "model switch from Opus 5.5"
    assert request.rewritten == 42_002
    # 42,002 tokens written at Sonnet's 1-hour price instead of read: x ($4 - $0.20)
    assert request.rewrite_cost == pytest.approx(42_002 * 3.8 / 1e6)
    assert dict(store.rewrites[next(iter(store.rewrites))]) == {"model switch": pytest.approx(request.rewrite_cost)}


def test_cache_expired(store):
    request = conversation(store, write=43_000, after=60 + 3700)
    assert request.reason == "cache expired (idle 62 min)"


def test_claude_code_upgrade(store):
    request = conversation(store, write=43_000, version="2.1.290")
    assert request.reason == "Claude Code upgraded"


def test_effort_change_rewrites_on_most_models_but_not_opus_5_5(store):
    assert conversation(store, write=43_000, effort="low").reason == "cause unknown"
    sonnet = Transcript(session="sess-2")
    sonnet.turn(T0, model="claude-sonnet-5", write=40_000)
    sonnet.turn(T0 + 60, model="claude-sonnet-5", effort="low", write=41_000)
    sonnet.into(store)
    latest = max(store.sessions["sess-2"].requests.values(), key=lambda r: r.start)
    assert latest.reason == "effort change"


def test_compaction(fixture_store, store):
    session = fixture_store.sessions[COMPACTED]
    assert session.main.compacted
    assert session.compact_post_tokens == 1497
    t = Transcript()
    t.turn(T0, write=80_000)
    t.record("system", T0 + 30, subtype="compact_boundary", compactMetadata={"postTokens": 3_000})
    # Only the tool list and system prompt were read back; the summary and the rest were written.
    t.turn(T0 + 60, read=20_000, write=19_000)
    t.into(store)
    latest = max(store.sessions["sess-1"].requests.values(), key=lambda r: r.start)
    assert latest.reason == "/compact"
    assert not store.sessions["sess-1"].main.compacted


def test_subagents_have_their_own_cache(fixture_store):
    session = fixture_store.sessions[COMPACTED]
    subagent = [r for r in session.requests.values() if r.subagent]
    assert len(subagent) == 4
    assert all(r.reason is None for r in subagent)
    assert f"{COMPACTED}/{subagent[0].subagent}" in session.chains
    # The main conversation's model and size ignore the subagent's requests.
    assert not session.last_request.subagent


def test_a_recap_restarts_the_cache_clock(fixture_store, store):
    session = fixture_store.sessions[RECAP]
    last = session.last_request
    assert session.main.touched > last.start  # the recap after the last reply
    t = Transcript()
    t.turn(T0, write=40_000)
    t.record("system", T0 + 200, subtype="away_summary", content="recap")
    t.into(store)
    assert store.sessions["sess-1"].main.touched == T0 + 195


# --- Names ------------------------------------------------------------------------


def test_names_from_each_surface(fixture_store):
    cli, vscode, desktop = (fixture_store.sessions[s] for s in (NO_REQUEST_IDS, VSCODE, DESKTOP_SESSION))
    assert (cli.name, cli.project, cli.entrypoint) == ("Test plan vs test case", "company-client", "cli")
    assert (vscode.name, vscode.project, vscode.entrypoint) == ("Pong reply", "llm-trunk", "claude-vscode")
    # The name set in the Desktop app, also written to the transcript as a custom title.
    assert (desktop.name, desktop.entrypoint) == ("Three-word greeting", "claude-desktop")
    assert desktop.desktop_title == "Three-word greeting"


def test_name_precedence(store):
    t = Transcript()
    t.user("please refactor the parser so it handles empty files and trailing commas", T0)
    t.into(store)
    session = store.sessions["sess-1"]
    assert session.name == "please refactor the parser so it handle…"
    for kind, key, value in (("ai-title", "aiTitle", "Parser refactor"), ("agent-name", "agentName", "parser"),
                             ("custom-title", "customTitle", "PARSER")):
        t.record(kind, **{key: value})
        t.into(store)
        assert session.name == value


def test_the_latest_rename_wins(store):
    t = Transcript()
    t.record("custom-title", customTitle="first")
    t.record("custom-title", customTitle="second")
    t.into(store)
    assert store.sessions["sess-1"].name == "second"


def test_a_session_with_nothing_yet(store):
    store.session("empty")
    assert store.sessions["empty"].name == "(new session)"


def test_first_and_last_prompt_skip_generated_text(store):
    t = Transcript()
    t.user("<local-command-caveat>Caveat: the messages below…</local-command-caveat>", T0)
    t.user("<command-name>/review</command-name><command-args>the parser</command-args>", T0 + 1)
    t.user([{"type": "text", "text": "Base directory for this skill: /x"}], T0 + 2, isMeta=True)
    t.tool_result(T0 + 3)
    t.user("summary of the conversation", T0 + 4, isCompactSummary=True)
    t.user([{"type": "text", "text": "now fix   the\nbug"}], T0 + 5)
    t.user("in a subagent", T0 + 6, subagent="a1")
    t.into(store)
    session = store.sessions["sess-1"]
    assert session.first_prompt == "/review the parser"
    assert session.last_prompt == "now fix the bug"
    assert session.last_prompt_at == T0 + 5


def test_command_text():
    assert command_text("<command-name>/compact</command-name>") == "/compact"
    assert command_text("<system-reminder>x</system-reminder>") is None
    assert command_text("plain") == "plain"


@pytest.mark.parametrize(
    ("cwd", "branch", "where"),
    [
        ("/home/user/app", "main", "app"),
        ("/home/user/app", "HEAD", "app"),
        ("/home/user/app", "fix-login", "app@fix-login"),
        ("/Users/me/Library/Application Support/Claude/scratch-workspaces/abc/2026-09-20", None, "Desktop (no folder)"),
    ],
)
def test_where(store, cwd, branch, where):
    t = Transcript(cwd=cwd, branch=branch)
    t.user("hi", T0)
    t.into(store)
    assert store.sessions["sess-1"].project == where


def test_desktop_titles_and_archived_sessions(tmp_path, store):
    folder = tmp_path / "org" / "acct"
    folder.mkdir(parents=True)
    (folder / "local_1.json").write_text(json.dumps({"cliSessionId": "sess-1", "title": "Sidebar name", "isArchived": True}))
    (folder / "local_2.json").write_text("{broken")
    t = Transcript(entrypoint="claude-desktop")
    t.record("ai-title", aiTitle="Auto")
    t.into(store)
    store.apply_desktop(desktop_sessions(tmp_path))
    session = store.sessions["sess-1"]
    assert (session.name, session.archived) == ("Sidebar name", True)
    assert desktop_sessions(tmp_path / "no-desktop-app") == {}


def test_closing_and_resuming(store):
    t = Transcript()
    t.turn(T0, write=40_000)
    t.record("cost-state", totalCostUSD=0.5)
    records = list(t.records)
    t.into(store)
    session = store.sessions["sess-1"]
    assert session.ended and session.cost_state == 0.5
    assert session.total == 0.5  # Claude Code's own, above the transcripts' $0.32
    t.turn(T0 + 600, write=40_000)
    records += t.records
    t.into(store)
    assert not session.ended
    # Resumed: Claude Code's total so far, plus what the transcripts show since; never lower.
    assert session.total == pytest.approx(0.5 + (40_000 * 8 + 2 * 4 + 100 * 20) / 1e6)
    # The same file read again from the start (it was rewritten): nothing changes.
    total = session.total
    store.add_all(records)
    assert session.total == pytest.approx(total) and not session.ended


def test_fast_mode_changes_the_price_and_turning_it_on_re_writes(store):
    t = Transcript()
    t.turn(T0, write=40_000, out=0, speed="standard")
    t.turn(T0 + 60, write=41_000, out=0, speed="fast")
    t.into(store)
    last = store.sessions["sess-1"].last_request
    assert last.reason == "speed change" and last.speed == "fast"
    assert last.cost == pytest.approx((41_000 * 16 + 2 * 8) / 1e6)  # the 1-hour write and input at twice the price


def test_web_searches_are_charged_on_top_of_tokens(store):
    t = Transcript()
    t.turn(T0, write=40_000, out=0, tools={"web_search_requests": 3, "web_fetch_requests": 2})
    t.into(store)
    request = store.sessions["sess-1"].last_request
    assert request.usage["searches"] == 3  # web fetches cost only their tokens
    assert request.cost == pytest.approx((40_000 * 8 + 2 * 4) / 1e6 + 3 * 0.01)


def test_scripted_sessions(store):
    for session_id, entrypoint in (("a", "cli"), ("b", "claude-vscode"), ("c", "claude-desktop"), ("d", "sdk-cli")):
        t = Transcript(session=session_id, entrypoint=entrypoint)
        t.user("hi", T0)
        t.into(store)
    assert [store.sessions[s].scripted for s in "abcd"] == [False, False, False, True]


# --- Long turns and long subagents (research/CACHE-DECISIONS.md §6) --------------------


def test_a_long_skill_turn_stays_warm_while_each_step_starts_in_time(store):
    # A 20-minute skill run on a 5-minute cache: 8 steps, each starting 2.5 minutes
    # after the one before. Every step reads the cache and restarts its clock.
    t = Transcript()
    t.user("<command-name>/migrate</command-name><command-args>db</command-args>", T0)
    t.reply(T0 + 5, write=40_000, ttl="5m")
    prompt = 40_002
    for step in range(1, 9):
        t.tool_result(T0 + 150 * step)
        t.reply(T0 + 150 * step + 5, read=prompt, write=1_000, ttl="5m")
        prompt += 1_000
    t.into(store)
    session = store.sessions["sess-1"]
    assert [r.reason for r in session.requests.values()] == [None] * 9
    assert session.main.touched == T0 + 150 * 8


def test_one_tool_running_longer_than_the_cache_expires_it_mid_turn(store):
    # The same skill, but one step runs a 6-minute build: the step after it re-writes.
    t = Transcript()
    t.user("<command-name>/release</command-name>", T0)
    t.reply(T0 + 5, write=40_000, ttl="5m")
    t.tool_result(T0 + 5 + 360)
    t.reply(T0 + 370, write=41_000, ttl="5m")
    t.into(store)
    latest = max(store.sessions["sess-1"].requests.values(), key=lambda r: r.start)
    # Counted from the start of the step that ran the tool (T0), not from its reply.
    assert latest.reason == "cache expired (idle 6 min)"


def test_a_long_subagent_keeps_its_own_cache_but_not_the_parents(store):
    # The parent (5-minute cache) starts a subagent that works for 8 minutes,
    # with a request every 2 minutes. Its cache stays warm; the parent's expires.
    t = Transcript()
    t.user("research the parser bugs, use a subagent", T0)
    t.reply(T0 + 5, write=40_000, ttl="5m")  # the Agent tool call
    agent = Transcript(session="sess-1")
    agent.user("find the parser bugs", T0 + 10, subagent="a1")
    agent.reply(T0 + 15, write=12_000, ttl="5m", subagent="a1")
    context = 12_002
    for step in range(1, 5):
        agent.tool_result(T0 + 10 + 120 * step, subagent="a1")
        agent.reply(T0 + 15 + 120 * step, read=context, write=800, ttl="5m", subagent="a1")
        context += 800
    t.tool_result(T0 + 500)  # the subagent's answer comes back
    t.reply(T0 + 505, write=41_000, ttl="5m")
    store.add_all(sorted(t.records + agent.records, key=lambda r: r.when))
    session = store.sessions["sess-1"]
    subagent = [r for r in session.requests.values() if r.subagent]
    assert len(subagent) == 5 and all(r.reason is None for r in subagent)
    parent = max((r for r in session.requests.values() if not r.subagent), key=lambda r: r.start)
    assert parent.reason == "cache expired (idle 8 min)"
    # While the subagent worked, the parent's clock was never restarted by it.
    assert session.main.touched == T0 + 500


def test_on_a_one_hour_cache_the_same_subagent_run_costs_nothing_extra(store):
    t = Transcript()
    t.user("research the parser bugs, use a subagent", T0)
    t.reply(T0 + 5, write=40_000)
    t.tool_result(T0 + 500)
    t.reply(T0 + 505, read=40_002, write=1_000)
    t.into(store)
    parent = max(store.sessions["sess-1"].requests.values(), key=lambda r: r.start)
    assert parent.reason is None


def test_requests_from_a_file_found_late_go_in_their_place_in_the_feed(store):
    main = Transcript()
    main.turn(T0, write=40_000)
    main.turn(T0 + 60, read=40_000, write=1_000)
    main.into(store)
    late = Transcript()  # a subagent's transcript, found at the tailer's next scan
    late.user("look around", T0 + 20, subagent="a1")
    late.reply(T0 + 30, subagent="a1", write=5_000, ttl="5m")
    late.into(store)
    assert [r.end for r in store.feed] == [T0 + 70, T0 + 30, T0 + 10]  # newest first


def test_the_feed_is_in_order_of_when_requests_started(store):
    main = Transcript()
    main.user("go", T0)
    main.reply(T0 + 100, message_id="msg_a")  # its first block
    main.into(store)
    sub = Transcript()
    sub.user("look around", T0 + 50, subagent="a1")
    sub.reply(T0 + 101, subagent="a1")
    sub.into(store)
    main.reply(T0 + 110, message_id="msg_a")  # its last block: the end moves on, the start doesn't
    main.into(store)
    late = Transcript()  # a long subagent request, found late
    late.user("dig deeper", T0 + 20, subagent="a2")
    late.reply(T0 + 105, subagent="a2")
    late.into(store)
    assert [r.start for r in store.feed] == [T0 + 50, T0 + 20, T0]
