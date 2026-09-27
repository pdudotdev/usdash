"""Following transcript files: partial lines, bad lines, subagents, old files."""
import json
import os

from conftest import PROJECTS

from usdash.transcripts import Tailer, file_owner, parse_time


def write(path, *records, end="\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as file:
        file.write("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in records) + end)


def test_reads_only_complete_lines_and_picks_up_the_rest_later(tmp_path):
    path = tmp_path / "-home-user-proj" / "s1.jsonl"
    write(path, {"type": "user", "timestamp": "2026-09-27T10:00:00Z"})
    with path.open("a") as file:
        file.write('{"type": "ai-title", "aiTi')  # still being written
    tailer = Tailer(tmp_path)
    assert [r.type for r in tailer.poll()] == ["user"]
    with path.open("a") as file:
        file.write('tle": "Hello"}\n')
    records = tailer.poll()
    assert [(r.type, r.get("aiTitle")) for r in records] == [("ai-title", "Hello")]
    assert tailer.poll() == []
    assert tailer.bad_lines == 0


def test_skips_lines_that_dont_parse_and_counts_unknown_types(tmp_path):
    path = tmp_path / "p" / "s1.jsonl"
    write(path, "not json", "[1, 2]", {"type": "brand-new-thing"}, {"type": "user"})
    tailer = Tailer(tmp_path)
    assert [r.type for r in tailer.poll()] == ["brand-new-thing", "user"]
    assert tailer.bad_lines == 2
    assert tailer.unknown_types == {"brand-new-thing": 1}


def test_subagent_lines_belong_to_their_parent_session(tmp_path):
    main = tmp_path / "p" / "s1.jsonl"
    sub = tmp_path / "p" / "s1" / "subagents" / "agent-abc123.jsonl"
    write(main, {"type": "user", "timestamp": "2026-09-27T10:00:00Z"})
    write(sub, {"type": "assistant", "timestamp": "2026-09-27T10:00:05Z"})
    records = Tailer(tmp_path).poll()
    assert [(r.session, r.subagent) for r in records] == [("s1", None), ("s1", "abc123")]
    assert file_owner(sub) == ("s1", "abc123")


def test_batches_are_in_time_order_and_untimed_lines_keep_their_place(tmp_path):
    main = tmp_path / "p" / "s1.jsonl"
    sub = tmp_path / "p" / "s1" / "subagents" / "agent-a.jsonl"
    write(main, {"type": "user", "timestamp": "2026-09-27T10:00:00Z"}, {"type": "user", "timestamp": "2026-09-27T10:00:10Z"},
          {"type": "cost-state"})
    write(sub, {"type": "assistant", "timestamp": "2026-09-27T10:00:05Z"})
    records = Tailer(tmp_path).poll()
    # cost-state has no timestamp: it stays after the line before it, not first.
    assert [r.type for r in records] == ["user", "assistant", "user", "cost-state"]


def test_old_files_wait_until_they_change_then_are_read_whole(tmp_path):
    path = tmp_path / "p" / "old.jsonl"
    write(path, {"type": "custom-title", "customTitle": "Old work"})
    os.utime(path, (1_000_000, 1_000_000))
    tailer = Tailer(tmp_path, since=2_000_000)
    assert tailer.poll() == []
    write(path, {"type": "user", "timestamp": "2026-09-27T10:00:00Z"})  # resumed
    assert [r.type for r in tailer.poll()] == ["custom-title", "user"]


def test_a_missing_folder_is_not_an_error(tmp_path):
    assert Tailer(tmp_path / "nowhere").poll() == []


def test_parse_time():
    assert parse_time("2026-09-27T10:00:00.500Z") == parse_time("2026-09-27T10:00:00Z") + 0.5
    assert parse_time("yesterday") is None
    assert parse_time(None) is None


def test_every_fixture_record_type_is_known():
    tailer = Tailer(PROJECTS)
    assert tailer.poll()
    assert not tailer.unknown_types
    assert tailer.bad_lines == 0
