"""Shared test setup: the real (redacted) fixture transcripts, and a builder
for small synthetic ones shaped like Claude Code's."""
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from usdash.prices import load_prices
from usdash.sessions import Store, desktop_sessions
from usdash.transcripts import Record, Tailer, read_record

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROJECTS = FIXTURES / "projects"
DESKTOP = FIXTURES / "desktop"
PRICES = load_prices()
T0 = 1_790_000_000.0  # 2026-09-21, a fixed "now" for synthetic transcripts

# Fixture sessions (see scripts/make_fixture.py), by what each one covers.
NO_REQUEST_IDS = "a530d9c1-52cb-4b37-b12c-e5fdacdb1b92"  # CLI, 5-minute cache, replies without requestId
COMPACTED = "dd12b1b0-0401-4323-9f80-8a87362fa771"  # CLI, a subagent, then /compact
VSCODE = "4e70088d-7087-4c0c-a8e1-90698d1002b1"  # VS Code extension, 1-hour cache
DESKTOP_SESSION = "92880323-95fc-4d70-81a3-fd13ff9ee971"  # Desktop app Code tab, /rename, 1-hour cache
RECAP = "61cc6050-12d0-451b-9ae5-49d54c851af6"  # CLI with an away_summary recap


@pytest.fixture(autouse=True)
def saved_prices_elsewhere(tmp_path, monkeypatch):
    """Keep the pricing page's saved copy out of the real cache folder."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture(scope="session")
def fixture_store() -> Store:
    store = Store(PRICES)
    store.add_all(Tailer(PROJECTS).poll())
    store.apply_desktop(desktop_sessions(DESKTOP))
    return store


def iso(when: float) -> str:
    return datetime.fromtimestamp(when, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Transcript:
    """Builds Claude Code-shaped records for one session, in order."""

    ids = itertools.count(1)

    def __init__(self, session: str = "sess-1", cwd: str = "/home/user/proj", branch: str = "main",
                 entrypoint: str = "cli", version: str = "2.1.283") -> None:
        self.session, self.cwd, self.branch, self.entrypoint, self.version = session, cwd, branch, entrypoint, version
        self.records: list[Record] = []
        self.last_uuid: str | None = None

    def _add(self, data: dict, subagent: str | None = None) -> dict:
        data.setdefault("sessionId", self.session)
        record = read_record(json.dumps(data), self.session, subagent)
        assert record is not None
        self.records.append(record)
        return data

    def _uuid(self) -> str:
        return f"u{next(self.ids)}"

    def user(self, text: str | list, at: float, subagent: str | None = None, **extra) -> str:
        uuid = self._uuid()
        self._add({
            "type": "user", "uuid": uuid, "parentUuid": self.last_uuid, "timestamp": iso(at),
            "isSidechain": bool(subagent), "cwd": self.cwd, "gitBranch": self.branch,
            "entrypoint": self.entrypoint, "version": extra.pop("version", self.version),
            "message": {"role": "user", "content": text}, **extra,
        }, subagent)
        self.last_uuid = uuid
        return uuid

    def tool_result(self, at: float, subagent: str | None = None) -> str:
        return self.user([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}], at, subagent)

    def reply(self, at: float, model: str = "claude-opus-5-5", effort: str | None = "high", read: int = 0,
              write: int = 0, fresh: int = 2, out: int = 100, ttl: str = "1h", blocks: int = 1,
              subagent: str | None = None, request_id: str | None = "auto", message_id: str | None = None,
              version: str | None = None, speed: str | None = None, **extra) -> str:
        """One API reply, written as `blocks` records sharing a message id,
        each a second apart (streamed content blocks)."""
        message_id = message_id or f"msg_{next(self.ids)}"
        split = {"ephemeral_1h_input_tokens": write if ttl == "1h" else 0,
                 "ephemeral_5m_input_tokens": write if ttl == "5m" else 0}
        usage = {"input_tokens": fresh, "cache_creation_input_tokens": write, "cache_read_input_tokens": read,
                 "output_tokens": out, "cache_creation": split}
        if speed:
            usage["speed"] = speed
        for block in range(blocks):
            uuid = self._uuid()
            data = {
                "type": "assistant", "uuid": uuid, "parentUuid": self.last_uuid, "timestamp": iso(at + block),
                "isSidechain": bool(subagent), "cwd": self.cwd, "gitBranch": self.branch,
                "entrypoint": self.entrypoint, "version": version or self.version, "effort": effort,
                "message": {"id": message_id, "model": model, "role": "assistant", "usage": usage},
                **extra,
            }
            if request_id:
                data["requestId"] = f"req_{message_id}" if request_id == "auto" else request_id
            self._add(data, subagent)
            self.last_uuid = uuid
        return message_id

    def turn(self, at: float, text: str = "do it", took: float = 10, **reply) -> str:
        """A prompt and its reply `took` seconds later."""
        self.user(text, at)
        return self.reply(at + took, **reply)

    def record(self, kind: str, at: float | None = None, **fields) -> None:
        data = {"type": kind, **fields}
        if at is not None:
            data["timestamp"] = iso(at)
            data["uuid"] = self._uuid()
            data["parentUuid"] = self.last_uuid
        self._add(data)

    def into(self, store: Store) -> Store:
        store.add_all(self.records)
        self.records = []
        return store


@pytest.fixture
def store() -> Store:
    return Store(PRICES)
