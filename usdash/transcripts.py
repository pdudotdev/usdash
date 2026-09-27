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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Record types usdash reads or knowingly ignores; anything else is counted,
# so a Claude Code release that adds one shows up in the status line.
KNOWN_TYPES = {
    "assistant", "user", "system", "custom-title", "ai-title", "agent-name", "last-prompt", "cost-state",
    "attachment", "file-history-snapshot", "file-history-delta", "mode", "permission-mode", "queue-operation",
    "bridge-session", "atis-latch", "frame-link", "artifact-autoreact-ledger", "artifact-comment-monitor",
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


def read_record(line: bytes | str, session: str, subagent: str | None = None) -> Record | None:
    try:
        data = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return Record(data, session, subagent, parse_time(data.get("timestamp")))


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
    and history are in its early lines."""
    root: Path
    since: float = 0.0
    # How often to look for new transcript files. In between, only the files
    # already being followed are checked for new lines.
    scan_every: float = 5.0
    clock: object = time.monotonic
    files: dict[Path, _Followed] = field(default_factory=dict)
    last_scan: float | None = None
    unknown_types: Counter = field(default_factory=Counter)
    bad_lines: int = 0

    def transcripts(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted([*self.root.glob("*/*.jsonl"), *self.root.glob("*/*/subagents/*.jsonl")])

    def poll(self) -> list[Record]:
        """Everything written since the last poll, oldest first. A file found
        at a scan can hold lines older than ones an earlier poll returned."""
        now = self.clock()
        if self.last_scan is None or now - self.last_scan >= self.scan_every:
            paths, self.last_scan = self.transcripts(), now
        else:
            paths = list(self.files)  # between scans: only the files already being followed
        records = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            state = self.files.get(path)
            if state is None:
                if stat.st_mtime < self.since:
                    continue
                state = self.files[path] = _Followed()
            if stat.st_size < state.offset:
                state.offset, state.partial = 0, b""  # rewritten from scratch
            if stat.st_size == state.offset:
                continue
            records.extend(self._read(path, state))
        # Main transcripts sort before their subagents', but a batch spanning
        # several files should still arrive in the order it happened.
        records.sort(key=lambda record: record.at)
        return records

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
