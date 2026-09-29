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


def test_an_upgrade_is_the_cause_even_though_it_comes_with_a_resume(store):
    # Claude Code applies an upgrade when it next starts: after an exit, so every upgrade is a resume
    # too. A resume alone keeps the system prompt; the upgrade changes the tool definitions.
    t = Transcript()
    t.turn(T0, write=40_000)
    t.record("cost-state", totalCostUSD=0.30)
    t.turn(T0 + 120, write=40_100, version="2.1.290")
    t.into(store)
    latest = max(store.sessions["sess-1"].requests.values(), key=lambda r: r.start)
    assert latest.reason == "Claude Code upgraded"


def test_effort_change_rewrites_on_most_models_but_not_opus_5_5(store):
    assert conversation(store, write=43_000, effort="low").reason == "cause unknown"
    sonnet = Transcript(session="sess-2")
    sonnet.turn(T0, model="claude-sonnet-5", write=40_000)
    sonnet.turn(T0 + 60, model="claude-sonnet-5", effort="low", write=41_000)
    sonnet.into(store)
    latest = max(store.sessions["sess-2"].requests.values(), key=lambda r: r.start)
    assert latest.reason == "effort change"
    # Sonnet 5.5 keeps the cache across an effort change: a miss then has another cause.
    newer = Transcript(session="sess-3")
    newer.turn(T0, model="claude-sonnet-5-5", write=40_000)
    newer.turn(T0 + 60, model="claude-sonnet-5-5", effort="low", write=41_000)
    newer.into(store)
    latest = max(store.sessions["sess-3"].requests.values(), key=lambda r: r.start)
    assert latest.reason == "cause unknown"


def test_compaction(fixture_store, store):
    session = fixture_store.sessions[COMPACTED]
    assert session.main.compacted  # its last record: the next request would measure the new size
    t = Transcript()
    t.turn(T0, write=80_000)
    t.record("system", T0 + 30, subtype="compact_boundary", compactMetadata={"postTokens": 3_000})
    # The tool list was read back and the summary written: new, not a re-write. It measures the tool list.
    t.turn(T0 + 60, read=20_000, write=19_000)
    t.into(store)
    session = store.sessions["sess-1"]
    latest = max(session.requests.values(), key=lambda r: r.start)
    assert latest.reason is None and store.tool_list(session, "claude-opus-5-5") == 20_000
    assert not session.main.compacted
    # Compacted again, and back once the cache expired: the tool list itself was written again.
    t.record("system", T0 + 90, subtype="compact_boundary", compactMetadata={"postTokens": 3_000})
    t.turn(T0 + 60 + ONE_HOUR + 120, write=23_002)
    t.into(store)
    latest = max(session.requests.values(), key=lambda r: r.start)
    assert (latest.reason, latest.rewritten) == ("cache expired (idle 62 min)", 20_000)


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
    assert session.first_prompt == "/review the parser"  # a skill: the next record is its body
    assert session.last_prompt == "now fix the bug"
    assert session.last_prompt_at == T0 + 5
    # A built-in command says nothing about the task: the first prompt names the session.
    builtin = Transcript(session="sess-2")
    builtin.user("<command-name>/model</command-name><command-args>sonnet</command-args>", T0)
    builtin.user("<local-command-stdout>Set model to Sonnet 5</local-command-stdout>", T0 + 1)
    builtin.user("fix the parser", T0 + 2)
    builtin.into(store)
    assert store.sessions["sess-2"].name == "fix the parser"
    only = Transcript(session="sess-3")
    only.user("<command-name>/model</command-name>", T0)
    only.user("<local-command-stdout>Set model to Sonnet 5</local-command-stdout>", T0 + 1)
    only.into(store)
    assert store.sessions["sess-3"].name == "/model"  # all there is


def test_a_command_that_runs_a_prompt_names_the_session(store):
    # /init runs a prompt of its own (no skill body): it says what the session is for.
    t = Transcript()
    t.user("<command-name>/model</command-name><command-args>sonnet</command-args>", T0)
    t.user("<local-command-stdout>Set model to Sonnet 5</local-command-stdout>", T0 + 1)
    t.user("<command-name>/init</command-name>", T0 + 2)
    t.user([{"type": "text", "text": "Please analyze this codebase and create a CLAUDE.md"}], T0 + 3, isMeta=True)
    t.reply(T0 + 10, write=30_000)
    t.user("thanks", T0 + 20)
    t.into(store)
    assert store.sessions["sess-1"].name == "/init"
    # A skill the model loads later, after a reply, doesn't turn an earlier built-in into the name.
    later = Transcript(session="sess-2")
    later.user("<command-name>/model</command-name><command-args>sonnet</command-args>", T0)
    later.user("<local-command-stdout>Set model to Sonnet 5</local-command-stdout>", T0 + 1)
    later.reply(T0 + 5, write=30_000)
    later.user([{"type": "text", "text": "Base directory for this skill: /x"}], T0 + 6, isMeta=True)
    later.user("fix the parser", T0 + 7)
    later.into(store)
    assert store.sessions["sess-2"].name == "fix the parser"
    # Only what was typed counts: an attached document that quotes a command tag doesn't.
    doc = Transcript(session="sess-3")
    doc.user([{"type": "text", "text": "summarise this"},
              {"type": "document", "source": {"type": "text", "data": "<command-name>/model</command-name>"}}], T0)
    doc.into(store)
    assert store.sessions["sess-3"].name == "summarise this"


def test_only_a_prompt_or_a_reply_after_a_command_makes_it_the_name(store):
    def name(session: str, *records) -> str:
        t = Transcript(session=session)
        t.user("<command-name>/model</command-name><command-args>foo</command-args>", T0)
        for i, record in enumerate(records):
            if record == "reply":
                t.reply(T0 + 1 + i, write=30_000)
            else:
                t.user(record, T0 + 1 + i)
        t.user("fix the parser", T0 + 10)
        t.into(store)
        return store.sessions[session].name

    # A built-in that failed, printed nothing, or was interrupted says nothing about the task.
    assert name("err", "<local-command-stderr>Unknown model: foo</local-command-stderr>") == "fix the parser"
    assert name("quiet") == "fix the parser"
    assert name("stop", [{"type": "text", "text": "[Request interrupted by user]"}]) == "fix the parser"
    # Nor does one followed by another command: neither is lost for the one that ran a prompt.
    t = Transcript(session="next")
    t.user("<command-name>/config</command-name>", T0)
    t.user([{"type": "text", "text": "<local-command-caveat>Caveat: …</local-command-caveat>"}], T0 + 1, isMeta=True)
    t.user("<command-name>/review</command-name>", T0 + 2)
    t.user([{"type": "text", "text": "Review the current diff"}], T0 + 3, isMeta=True)
    t.into(store)
    assert store.sessions["next"].name == "/review"
    # A reply to the command, with no prompt logged apart, means it ran one.
    assert name("replied", "reply") == "/model foo"
    # A typed prompt that mentions a command tag is a prompt.
    typed = Transcript(session="typed")
    typed.user("why does the <command-name> tag break the parser?", T0)
    typed.user("<command-name>/model</command-name>", T0 + 1)
    typed.user("<local-command-stdout>Set model to Sonnet 5</local-command-stdout>", T0 + 2)
    typed.user("thanks", T0 + 3)
    typed.into(store)
    assert store.sessions["typed"].first_prompt == "why does the <command-name> tag break the parser?"


def test_command_text():
    assert command_text("<command-name>/compact</command-name>") == "/compact"
    assert command_text("<system-reminder>x</system-reminder>") is None
    assert command_text("plain") == "plain"
    assert command_text("[Request interrupted by user for tool use]") is None  # Claude Code's, not typed
    assert command_text("see <command-name>/model</command-name> here") == "see <command-name>/model</command-name> here"


@pytest.mark.parametrize(
    ("cwd", "branch", "where"),
    [
        ("/home/user/app", "main", "app"),
        ("/home/user/app", "HEAD", "app"),
        ("/home/user/app", "fix-login", "app@fix-login"),
        ("/Users/me/Library/Application Support/Claude/scratch-workspaces/abc/2026-09-20", None, "no folder"),
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
    # Turning it off keeps the cache (Claude Code keeps sending the header): a miss then isn't down to it.
    t.turn(T0 + 120, write=42_000, out=0, speed="standard")
    t.into(store)
    assert store.sessions["sess-1"].last_request.reason == "cause unknown"


def test_web_searches_are_charged_on_top_of_tokens(store):
    t = Transcript()
    t.turn(T0, write=40_000, out=0, tools={"web_search_requests": 3, "web_fetch_requests": 2})
    t.into(store)
    request = store.sessions["sess-1"].last_request
    assert request.usage["searches"] == 3  # web fetches cost only their tokens
    assert request.cost == pytest.approx((40_000 * 8 + 2 * 4) / 1e6 + 3 * 0.01)


def test_what_says_claude_code_is_still_at_work(store):
    t = Transcript()
    t.turn(T0, write=40_000, stop="tool_use")  # a reply that calls a tool
    t.into(store)
    session = store.sessions["sess-1"]
    assert session.working(T0 + 400)  # the tool runs on, past a 5-minute cache
    t.tool_result(T0 + 400)
    t.into(store)
    assert session.working(T0 + 450)  # its result is in; Claude's answer is on its way
    t.reply(T0 + 460, read=40_000, write=500, stop="end_turn")
    t.into(store)
    assert not session.working(T0 + 470)  # the turn is done: waiting for you
    t.turn(T0 + 500, write=100, stop="tool_use")
    t.user([{"type": "tool_result", "tool_use_id": "t1", "content": "stopped", "is_error": True},
            {"type": "text", "text": "[Request interrupted by user for tool use]"}], T0 + 510)
    t.into(store)
    assert not session.working(T0 + 520)  # interrupted
    t.turn(T0 + 600, write=100, stop="tool_use")
    t.user("<command-name>/model</command-name><command-args>sonnet</command-args>", T0 + 610)
    t.into(store)
    assert session.working(T0 + 620)  # a command typed meanwhile says nothing either way


def test_a_miss_counts_against_the_conversation_after_the_tool_list(store):
    # Check 26 in tests/sanity/manual.py: a 5-minute cache, back after 10½ minutes. The tool list
    # (24,981, kept cached elsewhere) was read back and the 10,569-token conversation written again:
    # under 30% of the prompt, but all of the conversation.
    t = Transcript()
    t.turn(T0, read=24_981, write=9_909, ttl="5m")
    t.turn(T0 + 5, read=34_890, write=658, ttl="5m")
    t.turn(T0 + 5 + 636, read=24_981, write=11_072, ttl="5m")
    t.into(store)
    latest = max(store.sessions["sess-1"].requests.values(), key=lambda r: r.start)
    assert (latest.reason, latest.rewritten) == ("cache expired (idle 11 min)", 10_569)


def test_a_resume_that_misses_the_cache_says_so(store):
    # Exited, and resumed 3½ minutes later on a 5-minute cache, reading back only the tool list:
    # something changed at the restart, such as tools an MCP server loads up front. (Usually nothing
    # has: a resumed conversation keeps its system prompt, and a resume within the cache lifetime
    # reads it all back: tests/test_real_checks.py.)
    t = Transcript()
    t.turn(T0, read=24_981, write=9_907, ttl="5m")
    t.record("cost-state", totalCostUSD=0.05)
    t.turn(T0 + 210, read=24_981, write=9_970, ttl="5m")
    t.into(store)
    latest = max(store.sessions["sess-1"].requests.values(), key=lambda r: r.start)
    assert (latest.reason, latest.rewritten) == ("resumed", 9_909)
    # Resumed once the cache expired: that's what the miss is down to.
    t.record("cost-state", totalCostUSD=0.10)
    t.turn(T0 + 210 + 600, read=24_981, write=10_000, ttl="5m")
    t.into(store)
    latest = max(store.sessions["sess-1"].requests.values(), key=lambda r: r.start)
    assert latest.reason == "cache expired (idle 10 min)"


def test_a_subagent_at_work_in_the_background(store):
    # The main conversation starts a subagent in the background and ends its turn, then waits.
    t = Transcript()
    t.turn(T0, write=40_000, stop="end_turn")
    t.reply(T0 + 20, subagent="agent-1", write=25_000)
    t.into(store)
    session = store.sessions["sess-1"]
    assert not session.working(T0 + 400) and session.subagent_running(T0 + 400)
    assert not session.subagent_running(T0 + 20 + 31 * 60)  # quiet too long: it was stopped
    t.reply(T0 + 600, read=40_000, write=500, stop="end_turn")  # it reported back, and was answered
    t.into(store)
    assert not session.subagent_running(T0 + 610)


def test_a_session_quiet_too_long_is_not_at_work(store):
    t = Transcript()
    t.turn(T0, write=40_000, stop="tool_use")  # e.g. it was killed while the tool ran
    t.into(store)
    session = store.sessions["sess-1"]
    assert session.working(T0 + 29 * 60) and not session.working(T0 + 31 * 60)
    # A subagent's records count: while it works, so does the session.
    sub = Transcript()
    sub.reply(T0 + 40 * 60, subagent="agent-1", write=5_000)
    sub.into(store)
    assert session.working(T0 + 50 * 60)
    closing = Transcript()
    closing.record("cost-state", totalCostUSD=1.0)
    closing.into(store)
    assert not session.working(T0 + 50 * 60)  # it exited


def test_scripted_sessions(store):
    for session_id, entrypoint in (("a", "cli"), ("b", "claude-vscode"), ("c", "claude-desktop"), ("d", "sdk-cli")):
        t = Transcript(session=session_id, entrypoint=entrypoint)
        t.user("hi", T0)
        t.into(store)
    assert [store.sessions[s].scripted for s in "abcd"] == [False, False, False, True]
    t = Transcript(session="e", entrypoint="claude-jetbrains")  # an app usdash doesn't know yet: not a script
    t.user("hi", T0)
    t.into(store)
    assert not store.sessions["e"].scripted


# --- Long turns and long subagents (research/AI-TOKENOMICS-GUIDE.md §7, §17) ------------


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


# --- What the Stats view groups by ----------------------------------------------------


def test_what_was_typed_is_kept_once_with_its_time(store):
    t = Transcript()
    t.turn(T0, text="fix the parser")
    t.user("<command-name>/compact</command-name>", T0 + 60)
    t.user([{"type": "tool_result", "tool_use_id": "t9", "content": "ok"}], T0 + 70)  # not typed
    t.user("summary", T0 + 80, isCompactSummary=True)  # generated
    t.user("look around", T0 + 90, subagent="a1")  # a subagent's prompt
    records = list(t.records)
    t.into(store)
    store.add_all(records)  # the file read again from the start
    assert sorted(store.sessions["sess-1"].prompts.values()) == [
        (T0, "fix the parser", False), (T0 + 60, "/compact", True)]


def test_a_command_logged_as_typed_is_still_a_command(store):
    # Claude Code 2.1.283 logs `/compact` twice: as typed, and as its tagged command record.
    t = Transcript()
    t.turn(T0, text="write a story")
    t.user("/compact", T0 + 60)
    t.user("<command-name>/compact</command-name>\n<command-message>compact</command-message>", T0 + 60.004)
    t.user("/tmp/build is full, why?", T0 + 90)  # a path, not a command
    t.into(store)
    assert [(text, command) for _, text, command in sorted(store.sessions["sess-1"].prompts.values())] == [
        ("write a story", False), ("/compact", True), ("/compact", True), ("/tmp/build is full, why?", False)]


def test_each_request_keeps_the_prices_it_paid(store):
    t = Transcript()
    t.turn(T0, write=10_000, speed="fast")
    t.turn(T0 + 60, model="claude-opus-9", write=10_000)
    t.into(store)
    fast, unknown = sorted(store.sessions["sess-1"].requests.values(), key=lambda r: r.start)
    assert fast.paid["input"] == 8 and fast.paid["cache_read"] == pytest.approx(0.40)
    assert unknown.paid is None and unknown.cost is None


def test_requests_that_paid_the_same_prices_share_them(store):
    t = Transcript()
    for i in range(3):
        t.turn(T0 + 60 * i, write=10_000, speed="fast")
    t.turn(T0 + 300, write=10_000)
    t.into(store)
    session = store.sessions["sess-1"]
    *fast, standard = sorted(session.requests.values(), key=lambda r: r.start)
    assert fast[0].paid is fast[1].paid is fast[2].paid  # one dict for all of them, not one each
    assert standard.paid is not fast[0].paid and standard.paid["input"] == 4
    assert store.price("claude-opus-5-5", session) is standard.paid  # the session's price: the same one


@pytest.mark.parametrize(("cwd", "folder"), [
    ("/home/user/shop", "shop"),
    ("/Users/me/Library/Application Support/Claude/local-agent/abc", "no folder"),
    (None, "?"),
])
def test_the_folder_is_the_project_without_its_branch(store, cwd, folder):
    session = store.session("s")
    session.cwd, session.branch = cwd, "feature"
    assert session.folder == folder and session.project == f"{folder}@feature"
