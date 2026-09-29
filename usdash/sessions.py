"""Sessions and their requests, built up from transcript records.

A streamed reply is written as several records (one per content block) that
share a `message.id`, so requests are keyed by that (then `requestId`, then
the record's `uuid`: some sessions carry no requestId at all), and the last
record written wins. The main conversation and each subagent are separate
"chains": each has its own prompt cache, so each is tracked on its own.
"""
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .facts import Facts, load_facts
from .models import model_key, pretty_model
from .prices import FIVE_MINUTES, ONE_HOUR, as_paid, load_pricing, request_cost, usage_parts, write_price
from .transcripts import Record, account_file

# A request re-wrote the conversation when it failed to read back at least
# this share of what it could have (the smaller of its prompt and the
# previous one, less the tool list, which usually stays cached anyway), and
# the miss is this big.
REWRITE_SHARE, REWRITE_MIN_TOKENS = 0.3, 5_000
# Recaps are logged a few seconds after their request started.
RECAP_LAG = 5
SNIPPET = 60
SLASH_COMMAND = re.compile(r"/[\w:.-]+(?:\s|$)")
# A session still at work goes this long without a new record, its subagents' included,
# at most: longer, and it was stopped mid-tool (killed, or its window closed).
WORKING_QUIET = 30 * 60


def day_of(when: float) -> str:
    return datetime.fromtimestamp(when).strftime("%Y-%m-%d")


def memo(owner, key, compute):
    """`compute()`, kept until `owner.revision` changes (a Session bumps it
    whenever one of its requests is added or replaced)."""
    revision, values = owner._memo
    if revision != owner.revision:
        values = {}
        owner._memo = (owner.revision, values)
    if key not in values:
        values[key] = compute()
    return values[key]


@dataclass
class ChainState:
    """The last thing one conversation (main or a subagent) sent, for the next request to compare with."""
    key: str | None = None
    prompt: int = 0
    model: str | None = None
    effort: str | None = None
    version: str | None = None
    touched: float | None = None  # when its cache was last read or written (a request's start)
    ttl: int | None = None
    compacted: bool = False  # /compact ran since the last request
    resumed: bool = False  # the session exited since the last request (and was resumed)
    speed: str | None = None  # "fast" or "standard", as the last request ran
    geo: str | None = None  # where it ran: "us" (US-only), "global", …


@dataclass
class Request:
    key: str
    session: str
    subagent: str | None
    model: str | None
    effort: str | None
    start: float
    end: float
    version: str | None = None
    usage: dict = field(default_factory=dict)  # usage_parts()
    cost: float | None = None
    prev: ChainState | None = None  # the chain as this request found it
    rewritten: int = 0  # tokens of the earlier conversation it had to write again
    rewrite_cost: float = 0.0  # what that cost beyond reading them back
    reason: str | None = None  # why, when it re-wrote
    speed: str | None = None  # usage.speed: "fast" or "standard"
    geo: str | None = None  # usage.inference_geo
    paid: dict | None = None  # the per-token prices it paid (as_paid: fast mode, US-only); None if unknown
    family: str | None = None  # model_key(model)
    day: str = ""  # the local day it ended on (day_of), as last counted in the totals

    @property
    def prompt(self) -> int:
        u = self.usage
        return u.get("fresh", 0) + u.get("read", 0) + u.get("write_5m", 0) + u.get("write_1h", 0)

    @property
    def ttl(self) -> int | None:
        if self.usage.get("write_1h"):
            return ONE_HOUR
        if self.usage.get("write_5m"):
            return FIVE_MINUTES
        return None


def rewrite_reason(request: Request, prev: ChainState, facts: Facts) -> str:
    """Why a request's cache missed, from what changed since the chain's last
    request. Not /compact: it replaces the conversation with a summary, which
    is new, and after it only the tool list could be read back."""
    if model_key(request.model) != model_key(prev.model):
        return f"model switch from {pretty_model(prev.model)}"
    ttl = prev.ttl or FIVE_MINUTES
    if prev.touched is not None and request.start - prev.touched >= ttl:
        return f"cache expired (idle {(request.start - prev.touched) / 60:.0f} min)"
    # An upgrade applies only when Claude Code starts, so it comes with a resume: check it first.
    # It usually changes the tool definitions at the start of every request.
    if request.version and prev.version and request.version != prev.version:
        return "Claude Code upgraded"
    if prev.resumed:
        # A resumed conversation keeps the system prompt it started with, so something else
        # changed at the restart: tools loaded up front (an MCP server, a plugin), or the
        # system prompt flags given to the resume.
        return "resumed"
    if request.speed == "fast" and prev.speed and prev.speed != "fast":
        # Turning fast mode on adds a header that's part of the cache key. Claude Code keeps
        # sending it for the rest of the conversation, so turning it off (or on again) keeps the cache.
        return "speed change"
    if request.effort != prev.effort and prev.effort and not facts.effort_keeps_cache(request.model):
        return "effort change"
    return "cause unknown"


def reason_group(reason: str | None) -> str:
    """'cache expired (idle 7 min)' -> 'cache expired', for the day's totals."""
    return re.sub(r" (?:from|\().*$", "", reason or "")


def command_name(text: str) -> str | None:
    """'/name args' if this is a slash command's record: Claude Code's tags, from
    the start (a typed prompt that mentions a tag isn't one)."""
    if not text.lstrip().startswith("<"):
        return None
    name = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    if not name:
        return None
    args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
    return f"{name.group(1).strip()} {args.group(1).strip() if args else ''}".strip()


def command_text(text: str) -> str | None:
    """A prompt as the user typed it: a slash command's tags -> '/name args';
    other tagged text (command output, reminders, caveats) and Claude Code's
    '[Request interrupted by user…]' -> None."""
    if command := command_name(text):
        return command
    if text.lstrip().startswith(("<", "[Request interrupted")) or text.startswith("Caveat:"):
        return None
    return text


def record_text(record: Record) -> str | None:
    """A user record's text: its string content, or its text blocks joined.
    None if it carries tool results."""
    content = (record.data.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return None
    texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
    return "\n".join(texts) if texts else None


def typed(record: Record) -> tuple[str, bool] | None:
    """(what the user typed, whether it's a slash command), if this user record
    is a real prompt (not a tool result, skill body, compaction summary or
    other generated text)."""
    data = record.data
    if record.subagent or data.get("isSidechain") or data.get("isMeta") or data.get("isCompactSummary"):
        return None
    raw = record_text(record)
    if not raw:
        return None
    text = " ".join((command_text(raw) or "").split())
    # Claude Code takes anything typed as `/name …` for a command (an unknown one isn't sent), and
    # logs some as typed, next to their tagged record.
    return (text, command_name(raw) is not None or bool(SLASH_COMMAND.match(text))) if text else None


def at_work_after(record: Record) -> bool | None:
    """Whether Claude Code is still at work after this main-conversation
    record: a reply that ended calling tools (a tool or a subagent runs), or
    tool results it hasn't answered yet. False after a reply that ended the
    turn, or an interrupt. None if the record doesn't say: a typed prompt
    looks the same as a command that never reaches the API (/model)."""
    data = record.data
    message = data.get("message") or {}
    if record.type == "assistant":
        return message.get("stop_reason") == "tool_use"
    content = message.get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
    texts = " ".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict))
    if "[Request interrupted" in texts:
        return False
    if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in blocks):
        return True
    return None


def snippet(text: str | None, width: int = SNIPPET) -> str | None:
    if not text:
        return None
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


@dataclass
class Session:
    id: str
    cwd: str | None = None
    branch: str | None = None
    entrypoint: str | None = None
    version: str | None = None
    custom_title: str | None = None
    agent_name: str | None = None
    ai_title: str | None = None
    desktop_title: str | None = None
    archived: bool = False
    first_prompt: str | None = None  # the first thing typed that says what the session is for
    first_command: str | None = None  # the first built-in command (/model), if nothing else says it
    pending_command: str | None = None  # a command just typed: built-in or not, the next record tells
    last_prompt: str | None = None
    last_prompt_at: float | None = None
    last_activity: float | None = None
    ended: bool = False  # Claude Code wrote its closing cost-state, and nothing since
    at_work: bool = False  # running a tool or a subagent, or answering their results (at_work_after)
    heard: float | None = None  # the latest record's time, its subagents' included
    subagent_heard: float | None = None  # the latest record's time in any of its subagents
    replied: float | None = None  # the latest reply's time in the main conversation
    cost_state: float | None = None  # Claude Code's own total, written when the session exits
    cost_at_state: float = 0.0  # cost_total when Claude Code last wrote it
    requests: dict[str, Request] = field(default_factory=dict)
    chains: dict[str, ChainState] = field(default_factory=lambda: defaultdict(ChainState))
    cost_total: float = 0.0
    cost_by_day: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    # The session's first main-conversation prompt, and the model it was on: mostly
    # Claude Code's tool list and system prompt. Only the first: after a /model
    # switch, the first prompt on the new model is the whole conversation.
    prefix: int = 0
    prefix_model: str | None = None
    # (tokens, model family) of its tool list, measured: what a request that couldn't read
    # the whole conversation back (the first, or one after /compact, a resume, an expired
    # cache or a model switch) read back all the same
    tool_list: tuple[int, str | None] | None = None
    # Everything typed in the main conversation, prompts and slash commands: record uuid ->
    # (when, text, whether it's a command). By uuid, so a file read again adds nothing.
    prompts: dict[str, tuple[float, str, bool]] = field(default_factory=dict)
    revision: int = 0  # bumped on every change to its requests
    _memo: tuple = field(default=(-1, None), repr=False)  # see memo()
    uuids: dict[str, tuple] = field(default_factory=dict)  # uuid -> (time, message id, parent uuid)

    # --- Naming ---------------------------------------------------------------

    @property
    def name(self) -> str:
        """What the user calls this session: /rename, the Desktop sidebar title,
        Claude Code's own title, or failing those the first thing they typed
        (a command that runs a prompt, like a skill or /init, counts; a
        built-in like /model only if that's all there is)."""
        return (
            self.custom_title or self.desktop_title or self.agent_name or self.ai_title
            or snippet(self.first_prompt or self.first_command or self.pending_command, 40) or "(new session)"
        )

    @property
    def folder(self) -> str:
        """The folder it runs in: 'no folder' for a Desktop session started
        without one (it runs in a scratch folder of the app's), '?' if unknown."""
        cwd = self.cwd or ""
        if "/Library/Application Support/Claude/" in cwd:
            return "no folder"
        return Path(cwd).name or "?"

    @property
    def project(self) -> str:
        """The folder, plus @branch unless it's main, master or a detached HEAD."""
        if self.branch and self.branch not in ("HEAD", "main", "master"):
            return f"{self.folder}@{self.branch}"
        return self.folder

    @property
    def label(self) -> str:
        return f"{self.name} · {self.project}"

    @property
    def short_id(self) -> str:
        return self.id[:4]

    @property
    def scripted(self) -> bool:
        """`claude -p` and the SDKs (sdk-cli, sdk-py, sdk-ts): no one types in them."""
        return (self.entrypoint or "").startswith("sdk-")

    # --- Main conversation ----------------------------------------------------

    @property
    def main(self) -> ChainState:
        return self.chains[self.id]

    @property
    def model(self) -> str | None:
        return self.main.model

    @property
    def effort(self) -> str | None:
        return self.main.effort

    @property
    def ttl(self) -> int | None:
        return self.main.ttl

    @property
    def last_request(self) -> Request | None:
        return self.requests.get(self.main.key) if self.main.key else None

    def main_requests(self) -> list[Request]:
        """The main conversation's requests, oldest first."""
        return memo(self, "main", lambda: sorted(
            (r for r in self.requests.values() if not r.subagent), key=lambda r: r.start))

    def working(self, now: float) -> bool:
        """Whether Claude Code is still at work in this session: a tool or a
        subagent running, or an answer to their results on its way."""
        return (self.at_work and not self.ended and self.heard is not None
                and now - self.heard < WORKING_QUIET)

    def subagent_running(self, now: float) -> bool:
        """Whether a subagent is at work in the background while the main
        conversation waits for it: one wrote a record after the main
        conversation's last reply, within WORKING_QUIET."""
        heard = self.subagent_heard
        return (not self.ended and heard is not None and heard > (self.replied or 0)
                and now - heard < WORKING_QUIET)

    @property
    def total(self) -> float:
        """What the session has cost since it started: Claude Code's own total
        at its last exit, which counts requests the transcripts never log
        (titles, prompt suggestions, /compact's summary, ...), plus what the
        transcripts show since. It carries across resumes. Only the
        transcripts, if it never exited."""
        if self.cost_state is None:
            return self.cost_total
        return self.cost_state + self.cost_total - self.cost_at_state


class Store:
    """All sessions, and each day's totals."""

    def __init__(self, prices: dict, facts: Facts | None = None, web_search: float | None = None) -> None:
        self.prices = prices
        self.web_search = load_pricing().web_search if web_search is None else web_search  # USD per search
        self.facts = facts or load_facts()  # models.yaml
        self.sessions: dict[str, Session] = {}
        self.days: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))  # day -> cost, read, prompt, requests
        # day -> reason -> $ paid to write again what could have been read back
        self.rewrites: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.unpriced: Counter = Counter()  # requests with no known price, by model ("Opus 5.5 fast": its fast prices)
        self.revision = 0  # bumped on every change to any request, prompt or session folder: the stats' cache
        self._paid: dict[tuple, dict | None] = {}  # (family, speed, geo) -> as_paid(): one dict each, shared

    def session(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(session_id)
        return self.sessions[session_id]

    def add_all(self, records) -> list[Request]:
        new = []
        for record in records:
            request = self.add(record)
            if request is not None:
                new.append(request)
        return new

    def add(self, record: Record) -> Request | None:
        """Take in one record; returns a request when it's one not seen before."""
        session = self.session(record.session)
        data, kind = record.data, record.type
        uuid = data.get("uuid")
        if uuid:
            message_id = (data.get("message") or {}).get("id") if kind == "assistant" else None
            session.uuids[uuid] = (record.when, message_id, data.get("parentUuid"))
        if kind in ("user", "assistant") and not record.subagent:
            for attr, key in (("cwd", "cwd"), ("branch", "gitBranch"), ("entrypoint", "entrypoint"), ("version", "version")):
                value = data.get(key)
                if value and value != getattr(session, attr):
                    setattr(session, attr, value)
                    if attr in ("cwd", "entrypoint"):
                        self.revision += 1  # the stats group sessions by folder
        if record.when is not None:
            session.heard = max(session.heard or 0, record.when)
            if record.subagent:
                session.subagent_heard = max(session.subagent_heard or 0, record.when)
            elif kind == "assistant" and not data.get("isSidechain"):
                session.replied = max(session.replied or 0, record.when)
        if kind in ("user", "assistant") and not record.subagent and not data.get("isSidechain"):
            if (at_work := at_work_after(record)) is not None:
                session.at_work = at_work
            self._settle_command(session, record)
        if kind == "assistant":
            return self._add_assistant(session, record)
        if kind == "user":
            found = typed(record)
            if found:
                text, command = found
                session.ended = False
                prompt_key = uuid or f"@{record.when}"
                if record.when is not None and prompt_key not in session.prompts:
                    session.prompts[prompt_key] = (record.when, text, command)
                    self.revision += 1  # the stats' prompt count and turns
                if command:
                    session.pending_command = text
                else:
                    session.first_prompt = session.first_prompt or text
                session.last_prompt = text
                session.last_prompt_at = record.when
                session.last_activity = max(session.last_activity or 0, record.when or 0) or None
        elif kind == "custom-title" and data.get("customTitle"):
            session.custom_title = data["customTitle"]
        elif kind == "agent-name" and data.get("agentName"):
            session.agent_name = data["agentName"]
        elif kind == "ai-title" and data.get("aiTitle"):
            session.ai_title = data["aiTitle"]
        elif kind == "last-prompt" and data.get("lastPrompt") and not session.last_prompt:
            session.last_prompt = " ".join(str(data["lastPrompt"]).split())
        elif kind == "cost-state" and isinstance(data.get("totalCostUSD"), (int, float)):
            total = float(data["totalCostUSD"])
            if total != session.cost_state:  # the same record read again (a file re-read) changes nothing
                session.cost_state, session.cost_at_state = total, session.cost_total
            session.ended = session.main.resumed = True
        elif kind == "system" and not record.subagent:
            main = session.main
            if data.get("subtype") == "compact_boundary":
                main.compacted = True
                took = (data.get("compactMetadata") or {}).get("durationMs")
                if record.when is not None and isinstance(took, (int, float)):
                    # The compaction's request isn't logged, but it read the cache back: like a
                    # recap, it restarts the clock, from when it started.
                    started = record.when - took / 1000
                    if main.touched is not None and started > main.touched:
                        main.touched = started
            elif data.get("subtype") == "away_summary" and record.when is not None:
                # A recap resends the conversation: it reads the cache and restarts its clock.
                started = record.when - RECAP_LAG
                if main.touched is not None and started > main.touched:
                    main.touched = started
        return None

    @staticmethod
    def _settle_command(session: Session, record: Record) -> None:
        """A command just typed is settled by the next main-conversation
        record. One that runs a prompt (a skill, /init) is followed by that
        prompt, logged as a meta record, or by a reply: it says what the
        session is for. Anything else (a built-in's `<local-command-stdout>`
        or stderr, a prompt typed next, another command, an interrupt) makes
        it a built-in (/model, /effort, /compact…), which doesn't."""
        command = session.pending_command
        if not command:
            return
        ran_prompt = record.type == "assistant"
        if record.type == "user":
            text = record_text(record)
            if text is None:
                return  # a tool result, or no text at all
            if record.data.get("isMeta"):
                if text.lstrip().startswith("<"):
                    return  # Claude Code's own notes, like the caveat before a command
                ran_prompt = True
        if ran_prompt:
            session.first_prompt = session.first_prompt or command
        else:
            session.first_command = session.first_command or command
        session.pending_command = None

    def _start_of(self, session: Session, record: Record, message_id: str | None) -> float | None:
        """When the request behind this reply started: the time of the record
        it answers (the user's message or a tool result), skipping earlier
        blocks of the same reply."""
        parent, seen = record.get("parentUuid"), 0
        while parent in session.uuids and seen < 50:
            when, parent_message, grandparent = session.uuids[parent]
            if not message_id or parent_message != message_id:
                return when
            parent, seen = grandparent, seen + 1
        return None

    def _add_assistant(self, session: Session, record: Record) -> Request | None:
        data = record.data
        message = data.get("message") or {}
        model = message.get("model")
        usage = message.get("usage")
        if not model or model == "<synthetic>" or not isinstance(usage, dict) or data.get("isApiErrorMessage"):
            return None
        key = message.get("id") or data.get("requestId") or data.get("uuid")
        if not key:
            return None
        chain_id = session.id if not record.subagent else f"{session.id}/{record.subagent}"
        chain = session.chains[chain_id]
        end = record.when
        old = session.requests.get(key)
        if old is None:
            start = self._start_of(session, record, message.get("id")) or end
            if start is None:
                return None
            request = Request(key, session.id, record.subagent, model, data.get("effort"), start, end or start,
                              version=data.get("version"), family=model_key(model))
            request.prev = ChainState(**vars(chain))
            session.requests[key] = request
        else:
            request = old
            self._account(session, request, -1)
            request.end = end or request.end
        request.usage = usage_parts(usage)
        request.speed, request.geo = usage.get("speed"), usage.get("inference_geo")
        base = self.prices.get(request.family)
        price = self.paid_price(request.family, request.speed, request.geo)  # fast mode, US-only
        if price is None and old is None:
            self.unpriced[pretty_model(model) + (" fast" if base else "")] += 1
        request.paid = price
        request.cost = request_cost(usage, price) + request.usage["searches"] * self.web_search if price else None
        if old is None and not record.subagent:
            self._measure_tool_list(session, request)
        self._classify(session, request, price)
        self._account(session, request, +1)
        if chain.key in (None, key) or old is None:
            chain.key, chain.prompt, chain.model, chain.effort = key, request.prompt, model, request.effort
            chain.version, chain.touched = request.version, request.start
            chain.speed, chain.geo = request.speed, request.geo
            chain.ttl = request.ttl or chain.ttl
            if old is None:
                chain.compacted = chain.resumed = False
        if not record.subagent:
            session.ended = False
        session.last_activity = max(session.last_activity or 0, request.end)
        return request if old is None else None

    def _measure_tool_list(self, session: Session, request: Request) -> None:
        """A main-conversation request that couldn't read the whole conversation
        back (the session's first, or one after /compact, a resume, an expired
        cache or a model switch) still reads back what is cached: Claude Code's tool
        list (and, when another session just sent the same, its system
        prompt). That is the tool list's size, measured, for _classify."""
        if not session.prefix and request.prompt:
            session.prefix, session.prefix_model = request.prompt, request.model
        prev, read = request.prev, request.usage.get("read", 0)
        if prev is None or not prev.prompt:
            cold, could_read = True, request.prompt
        else:
            expired = prev.touched is not None and request.start - prev.touched >= (prev.ttl or FIVE_MINUTES)
            switched = model_key(request.model) != model_key(prev.model)
            previous = round(self.facts.convert(prev.prompt, prev.model, request.model))  # in this request's tokens
            cold, could_read = prev.compacted or prev.resumed or expired or switched, min(request.prompt, previous)
        # Not more than the first prompt: a forked conversation reads back more than the tool list.
        first_prompt = self.facts.convert(session.prefix, session.prefix_model, request.model)
        if cold and 0 < read < could_read and read <= first_prompt:
            session.tool_list = (read, model_key(request.model))

    def _classify(self, session: Session, request: Request, price: dict | None) -> None:
        """Whether a request wrote again what it could have read back, and why.
        What counts is the conversation after the tool list: the tool list
        is often read back anyway (another session keeps it cached), so a
        request that misses the whole conversation can still read a big part
        of its prompt. Right after /compact, only the tool list could be read."""
        prev = request.prev
        request.rewritten, request.rewrite_cost, request.reason = 0, 0.0, None
        if prev is None or not prev.key or not prev.prompt:
            return
        tool_list = 0.0 if request.subagent else self.tool_list(session, request.model)
        # In this request's tokens: after a switch from an older tokenizer, the same conversation is ~1.3× the tokens.
        could_read = min(request.prompt, round(self.facts.convert(prev.prompt, prev.model, request.model)))
        if prev.compacted and not request.subagent:
            if not tool_list:
                return  # no telling what was still cached
            could_read = min(could_read, round(tool_list))
        missed = max(0, could_read - request.usage.get("read", 0))
        if missed < max(REWRITE_MIN_TOKENS, REWRITE_SHARE * max(could_read - tool_list, 0)):
            return
        request.rewritten = missed
        request.reason = rewrite_reason(request, prev, self.facts)
        if price:
            ttl = request.ttl or prev.ttl or FIVE_MINUTES
            request.rewrite_cost = missed * (write_price(price, ttl) - price["cache_read"]) / 1_000_000

    def _account(self, session: Session, request: Request, sign: int) -> None:
        """Add a request to (or take it out of) the running totals."""
        session.revision += 1
        self.revision += 1
        if sign > 0:
            request.day = day_of(request.end)  # taken out again on the day it was added on
        day = self.days[request.day]
        cost = request.cost or 0.0
        day["cost"] += sign * cost
        day["read"] += sign * request.usage.get("read", 0)
        day["prompt"] += sign * request.prompt
        day["requests"] += sign
        session.cost_total += sign * cost
        session.cost_by_day[request.day] += sign * cost
        if request.reason:
            self.rewrites[request.day][reason_group(request.reason)] += sign * request.rewrite_cost

    # --- Across sessions --------------------------------------------------------

    def price(self, model: str | None, session: Session | None = None) -> dict | None:
        """`model`'s list prices, or, given a session, as its next request
        would pay them there: at its speed (fast mode; another model runs fast
        only if it has fast mode prices) and where it runs (US-only inference).
        None if unknown."""
        price = self.prices.get(model_key(model))
        if price is None or session is None:
            return price
        own = model_key(model) == model_key(session.model)
        speed = session.main.speed if own or price.get("fast") else None
        return self.paid_price(model_key(model), speed, session.main.geo)

    def paid_price(self, family: str | None, speed: str | None, geo: str | None) -> dict | None:
        """as_paid() for a model family, speed and region: worked out once and
        shared (by every request that paid it, too). None with no known price."""
        key = (family, speed, geo)
        if key not in self._paid:
            base = self.prices.get(family)
            self._paid[key] = as_paid(base, family, speed, geo) if base else None
        return self._paid[key]

    def tool_list(self, session: Session, model: str | None) -> float:
        """Claude Code's tool list as `session` measured it (Session.tool_list),
        in `model`'s tokens; 0 until it has."""
        found = session.tool_list
        return 0.0 if found is None else self.facts.convert(found[0], found[1], model)

    def apply_desktop(self, desktop: dict[str, dict]) -> None:
        """Titles and archive state from the Desktop app's own session files."""
        for session_id, info in desktop.items():
            if session_id in self.sessions:
                session = self.sessions[session_id]
                session.desktop_title = info.get("title") or None
                session.archived = bool(info.get("archived"))


def desktop_sessions(root: Path | None = None) -> dict[str, dict]:
    """transcript session id -> {title, archived}, from the Claude Desktop
    app's metadata (macOS). Nothing if the app isn't installed."""
    root = root or Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
    found = {}
    if not root.is_dir():
        return found
    for path in root.glob("*/*/local_*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("cliSessionId"):
            found[data["cliSessionId"]] = {"title": data.get("title"), "archived": data.get("isArchived")}
    return found


def subscription_account(config: Path | None = None) -> bool:
    """Whether Claude Code is logged in to a Claude subscription (Pro/Max/Team).
    Reads only two fields of the account record, never names or email."""
    path = config or account_file()
    try:
        account = json.loads(path.read_text()).get("oauthAccount") or {}
    except (OSError, ValueError, AttributeError):
        return False
    billing = str(account.get("billingType") or "")
    organization = str(account.get("organizationType") or "")
    return "subscription" in billing or organization.startswith("claude_")
