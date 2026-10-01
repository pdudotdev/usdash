"""Following Claude Code's transcripts as they're written.

Every session Claude Code runs on this machine (terminal, VS Code, the
Desktop app's Code tab, scripts) appends JSON lines to
`<config>/projects/<folder-slug>/<session-id>.jsonl`, and each subagent to
`<session-id>/subagents/agent-<id>.jsonl`. The format is internal to Claude
Code and changes between releases, so everything here is tolerant: a line
that doesn't parse is skipped (the last one may be half-written), a missing
field reads as None.
"""
import json
import os
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Record types usdash reads or knowingly ignores; anything else is counted,
# so a Claude Code release that adds one shows up in the status line.
KNOWN_TYPES = {
    "assistant", "user", "system", "custom-title", "ai-title", "agent-name", "last-prompt", "cost-state",
    "attachment", "file-history-snapshot", "file-history-delta", "mode", "permission-mode", "queue-operation",
    "bridge-session", "atis-latch", "frame-link", "artifact-autoreact-ledger", "artifact-comment-monitor",
    "pr-link",
}


def config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


def default_projects_dir() -> Path:
    return config_dir() / "projects"


def account_file() -> Path:
    """Claude Code's account record: inside CLAUDE_CONFIG_DIR when set, else ~/.claude.json."""
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return config_dir() / ".claude.json"
    return Path.home() / ".claude.json"


def parse_time(value) -> float | None:
    """ISO-8601 (as in the transcripts, e.g. 2026-09-26T11:37:44.094Z) -> epoch seconds."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Record:
    """One transcript line, with where it came from."""
    data: dict
    session: str  # the main session this belongs to (a subagent's parent)
    subagent: str | None = None  # the agent id, for lines from a subagent's file
    when: float | None = None
    # For ordering: its own time, else that of the line before it in its file
    # (titles and cost-state lines carry no timestamp).
    at: float = 0.0

    @property
    def type(self) -> str | None:
        return self.data.get("type")

    def get(self, key: str, default=None):
        return self.data.get(key, default)


# Fields usdash never reads, and most of a transcript's bytes: tool results and file
# contents, attachments, tool inputs. Dropped as each line is read, so loading weeks of
# history holds only what the dashboard needs.
UNUSED = ("toolUseResult", "attachment", "rendered", "wireToolInputs", "serverClassifierContext")


def slim(data: dict) -> dict:
    """A record without what usdash never reads: the fields in UNUSED (but what a queued
    message says), and a message's content blocks down to their type, and a user message's text."""
    attachment = data.get("attachment")
    for key in UNUSED:
        data.pop(key, None)
    if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
        # A message typed while Claude Code was at work, slipped into the running turn: it's a prompt.
        data["attachment"] = {key: attachment.get(key) for key in ("type", "prompt", "commandMode", "origin")}
    message = data.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        keep_text = data.get("type") == "user"  # what was typed; a reply's text is never read
        message["content"] = [
            {"type": block.get("type"), **({"text": block["text"]} if keep_text and "text" in block else {})}
            if isinstance(block, dict) else block
            for block in content
        ]
    return data


def read_record(line: bytes | str, session: str, subagent: str | None = None) -> Record | None:
    try:
        data = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return Record(slim(data), session, subagent, parse_time(data.get("timestamp")))


def file_owner(path: Path) -> tuple[str, str | None]:
    """(session id, subagent id or None) from a transcript's path."""
    if path.parent.name == "subagents":
        return path.parent.parent.name, path.stem.removeprefix("agent-")
    return path.stem, None


@dataclass
class _Followed:
    offset: int = 0
    partial: bytes = b""
    last_when: float = 0.0


@dataclass
class Tailer:
    """Reads every transcript under `root`, then only what's appended.

    Files last written before `since` are left alone until they change (a
    resumed session), and are then read from the start: the session's name
    and history are in its early lines. A followed session's subagent files
    are read whatever their age."""
    root: Path
    since: float = 0.0
    # How often to look for new transcript files. In between, only the files
    # already being followed are checked for new lines.
    scan_every: float = 5.0
    clock: object = time.monotonic
    files: dict[Path, _Followed] = field(default_factory=dict)
    last_scan: float | None = None
    # The oldest transcript's last write, those left alone included: the history reaches back that far.
    oldest: float | None = None
    unknown_types: Counter = field(default_factory=Counter)
    bad_lines: int = 0
    # Desktop sessions deleted in the app: session id -> when (deleted_desktop_sessions)
    deleted: dict[str, float] = field(default_factory=dict)

    def transcripts(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        # Main transcripts first: whether a subagent's file is read depends on its session's.
        return sorted(self.root.glob("*/*.jsonl")) + sorted(self.root.glob("*/*/subagents/*.jsonl"))

    def poll(self) -> list[Record]:
        """Everything written since the last poll, oldest first. A file found
        at a scan can hold lines older than ones an earlier poll returned."""
        return sorted((record for batch in self.batches() for record in batch), key=lambda record: record.at)

    def batches(self) -> Iterator[list[Record]]:
        """What poll() returns, a session at a time: each session's new lines,
        its subagents' among them, oldest first. Sessions don't depend on each
        other, and weeks of history, read whole, would all be held at once."""
        now = self.clock()
        if self.last_scan is None or now - self.last_scan >= self.scan_every:
            paths, self.last_scan = self.transcripts(), now
            self.deleted = self.deleted_desktop_sessions()
        else:
            paths = list(self.files)  # between scans: only the files already being followed
        sessions: dict[str, list[Path]] = {}
        for path in paths:  # main transcripts first, so each session's comes before its subagents'
            sessions.setdefault(file_owner(path)[0], []).append(path)
        for files in sessions.values():
            records = []
            for path in files:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                self.oldest = stat.st_mtime if self.oldest is None else min(self.oldest, stat.st_mtime)
                state = self.files.get(path)
                if state is None:
                    if stat.st_mtime < self.since and not self._session_followed(path):
                        continue
                    state = self.files[path] = _Followed()
                if stat.st_size < state.offset:
                    state.offset, state.partial = 0, b""  # rewritten from scratch
                if stat.st_size == state.offset:
                    continue
                records.extend(self._read(path, state))
            if records:
                # Main transcripts are read before their subagents', but the session's lines
                # should still arrive in the order they happened.
                records.sort(key=lambda record: record.at)
                yield records

    def deleted_desktop_sessions(self) -> dict[str, float]:
        """Desktop sessions deleted in the app: session id -> when. Deleting one takes its
        transcript, and with it what the session cost, and leaves only `<id>.desktop-released.json`
        ({"reason": "delete", "releasedAt": …}). Archiving leaves no such marker."""
        found: dict[str, float] = {}
        if not self.root.is_dir():
            return found
        for marker in self.root.glob("*/*.desktop-released.json"):
            session = marker.name.removesuffix(".desktop-released.json")
            if marker.with_name(f"{session}.jsonl").exists():
                continue  # its transcript is still there: nothing was lost
            try:
                data = json.loads(marker.read_text())
                if not isinstance(data, dict) or data.get("reason") != "delete":
                    continue
                when = parse_time(data.get("releasedAt"))
                found[session] = when if when is not None else marker.stat().st_mtime
            except (OSError, ValueError):
                continue
        return found

    def _session_followed(self, path: Path) -> bool:
        """Whether this is a subagent's transcript of a session being followed:
        its requests are part of that session's cost, however long ago they ran."""
        return path.parent.name == "subagents" and path.parent.parent.with_suffix(".jsonl") in self.files

    def _read(self, path: Path, state: _Followed) -> list[Record]:
        try:
            with path.open("rb") as file:
                file.seek(state.offset)
                chunk = file.read()
        except OSError:
            return []
        state.offset += len(chunk)
        lines = (state.partial + chunk).split(b"\n")
        state.partial = lines.pop()  # "" after a complete line, else a line still being written
        session, subagent = file_owner(path)
        records = []
        for line in lines:
            if not line.strip():
                continue
            record = read_record(line, session, subagent)
            if record is None:
                self.bad_lines += 1
                continue
            state.last_when = record.when or state.last_when
            record.at = state.last_when
            if record.type not in KNOWN_TYPES:
                self.unknown_types[record.type] += 1
            records.append(record)
        return records
