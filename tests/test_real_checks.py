"""usdash's numbers against what real Claude Code sessions sent and paid.

The transcripts in fixtures/checks come from Claude Code 2.1.283 (scripts/make_fixture.py
removed their text), run for a review of usdash on 2026-09-28. Claude Code's own
`cost-state` totals, written each time a session exits, show what the requests the
transcripts don't log cost: /compact's is one of them.

    4fe42f1c  interactive, Haiku 4.5: files read, /compact, a message, /model sonnet,
              /model opus, /effort low, /exit
    366dd95e  claude -p, Haiku 4.5: four turns (resumed), /compact, a message
    e5a4a381  claude -p, Opus 5.5: one turn, /compact, a message
    b2f89bb3  claude -p, Haiku 4.5: one turn, /compact
    23b13756  claude -p, Haiku 4.5, 5-minute cache: two turns, /compact 6.8 minutes later
    dd5d04e6  interactive, Haiku 4.5: a file read and a file written, /exit, resumed a minute later
    3ba7fa9a  the same with no file written
    5b175e45  the same on a 5-minute cache, resumed 18 seconds after the last reply
"""
import pytest
from conftest import FIXTURES, PRICES

from usdash import engine
from usdash.models import model_key
from usdash.prices import request_cost
from usdash.sessions import Store
from usdash.transcripts import Tailer

CHECKS = FIXTURES / "checks"


@pytest.fixture(scope="module")
def records():
    found = {}
    for record in Tailer(CHECKS).poll():
        found.setdefault(record.session, []).append(record)
    return found


def prompt(record) -> int:
    usage = record.data["message"]["usage"]
    return usage["input_tokens"] + usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"]


def is_compaction(record) -> bool:
    return record.type == "system" and record.get("subtype") == "compact_boundary"


def replay(records, stop):
    """A store fed a session's records up to the first one `stop` picks (not included)."""
    store = Store(PRICES)
    for i, record in enumerate(records):
        if stop(i, record):
            return store, store.sessions[record.session], i
        store.add(record)
    raise AssertionError("never stopped")


def after(records, i, kind):
    return next(j for j in range(i + 1, len(records)) if records[j].type == kind)


@pytest.mark.parametrize("session_id", ["4fe42f1c-2edd-4d6e-a495-66c024aca7fb", "366dd95e-bf09-4482-9d77-393030b6edb4",
                                        "e5a4a381-7dab-4369-bef3-2d0a1847a5c7"])
def test_right_after_compact_the_size_waits_for_the_next_request(records, session_id):
    # No estimate in between (the tool list plus compactMetadata.postTokens was 13–32% short);
    # the next request measures it exactly.
    session_records = records[session_id]
    boundary = next(i for i, r in enumerate(session_records) if is_compaction(r))
    nxt = after(session_records, boundary, "assistant")
    store, session, _ = replay(session_records, lambda i, r: i == nxt)
    assert engine.context(session) is None and engine.resend_costs(store, session, session_records[nxt].when) is None
    store.add(session_records[nxt])
    assert engine.context(session) == prompt(session_records[nxt])


def test_the_next_message_reads_the_last_prompt_back(records):
    # "now" is C at the read price: every request that followed a warm one in the same
    # conversation, on the same model, read back all of the previous prompt (to 100 tokens).
    pairs = 0
    for session_records in records.values():
        store = Store(PRICES)
        store.add_all(session_records)
        for session in store.sessions.values():
            for request in session.main_requests():
                prev = request.prev
                if (prev is None or not prev.prompt or prev.compacted or prev.resumed
                        or model_key(prev.model) != model_key(request.model)
                        or request.start - prev.touched >= (prev.ttl or 300)):
                    continue
                assert request.usage["read"] >= prev.prompt - 100, request.key
                pairs += 1
    assert pairs >= 10


@pytest.mark.parametrize("session_id", ["dd5d04e6-db04-42e7-aa28-c9356584909a", "3ba7fa9a-8313-47cf-b1cd-1a97da6a3155",
                                        "5b175e45-3c0b-44b5-b5b3-957630e1fdda"])
def test_resuming_while_the_cache_lasts_reads_it_back(records, session_id):
    session_records = records[session_id]
    exit_at = next(i for i, r in enumerate(session_records) if r.type == "cost-state")
    resumed = after(session_records, exit_at, "assistant")
    typed = next(r for r in session_records[exit_at:resumed] if r.type == "user")  # the message sent on resuming
    store, session, stop = replay(session_records, lambda i, r: i > exit_at and r.type in ("user", "assistant"))
    assert session.ended and engine.cache_clock(session, typed.when)[0]
    tokens = engine.context(session)
    now, _ = engine.resend_costs(store, session, typed.when)
    # The resumed session's first request read back all of the conversation, and paid for that
    # and its new message: what the exited session's line said, give or take the message.
    store.add_all(session_records[stop:])
    request = store.sessions[session_id].requests[session_records[resumed].data["message"]["id"]]
    usage = dict(session_records[resumed].data["message"]["usage"], output_tokens=0)
    paid = request_cost(usage, PRICES[model_key(request.model)])
    assert request.usage["read"] >= tokens - 100 and request.reason is None
    assert now == pytest.approx(paid, rel=0.15)  # "up to" would have been ~7× that


def test_a_switch_from_the_older_tokenizer_is_a_rewrite(records):
    # Haiku 4.5 (33k tokens) to Sonnet 5, which read back its own cached tool list (29.8k) and wrote
    # 15.4k again: compared in Haiku's tokens, that looked like 3.5k missed, under the 5k threshold.
    session_records = records["4fe42f1c-2edd-4d6e-a495-66c024aca7fb"]
    store = Store(PRICES)
    store.add_all(session_records)
    session = store.sessions["4fe42f1c-2edd-4d6e-a495-66c024aca7fb"]
    requests = session.main_requests()
    sonnet, opus, low = requests[-3:]
    assert (sonnet.model, sonnet.reason) == ("claude-sonnet-5", "model switch from Haiku 4.5")
    assert 12_000 < sonnet.rewritten < 16_000  # 15,428 written; the rest is the new message and the reply
    assert (opus.reason, low.reason, low.effort) == ("model switch from Sonnet 5", None, "low")  # effort kept the cache
    misses = sum(day.get("model switch", 0) for day in store.rewrites.values())
    assert misses == pytest.approx(sonnet.rewrite_cost + opus.rewrite_cost)
    assert session.total == pytest.approx(0.6367772)  # exited: Claude Code's own total
