"""Sessions and their requests, built up from transcript records.

A streamed reply is written as several records (one per content block) that
share a `message.id`, so requests are keyed by that (then `requestId`, then
the record's `uuid`: some sessions carry no requestId at all), and the last
record written wins. The main conversation and each subagent are separate
"chains": each has its own prompt cache, so each is tracked on its own.
"""
import json
import re
from bisect import bisect_left
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median

from .models import convert_tokens, effort_keeps_cache, model_key, pretty_model
from .prices import FIVE_MINUTES, ONE_HOUR, request_cost, usage_parts, write_price
from .transcripts import Record, account_file

FEED_HISTORY = 10_000
# A request re-wrote the conversation when it failed to read back at least
# this share of what it could have (the smaller of its prompt and the
# previous one), and the miss is this big.
REWRITE_SHARE, REWRITE_MIN_TOKENS = 0.3, 5_000
# Recaps are logged a few seconds after their request started.
RECAP_LAG = 5
SNIPPET = 60
DEFAULT_GROWTH = 1_000  # tokens a message adds, before a session has history


def day_of(when: float) -> str:
    return datetime.fromtimestamp(when).strftime("%Y-%m-%d")


def memo(owner, key, compute):
    """`compute()`, kept until `owner.revision` changes (a Session or the Store:
    both bump it whenever a request is added or replaced, and the Store also
    when a session's folder or entrypoint changes)."""
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


def rewrite_reason(request: Request, prev: ChainState) -> str:
    """Why a request's cache missed, from what changed since the chain's last request."""
    if prev.compacted:
        return "/compact"
    if model_key(request.model) != model_key(prev.model):
        return f"model switch from {pretty_model(prev.model)}"
    ttl = prev.ttl or FIVE_MINUTES
    if prev.touched is not None and request.start - prev.touched >= ttl:
        return f"cache expired (idle {(request.start - prev.touched) / 60:.0f} min)"
    if request.version and prev.version and request.version != prev.version:
        return "Claude Code upgraded"
    if request.effort != prev.effort and prev.effort and not effort_keeps_cache(request.model):
        return "effort change"
    return "cause unknown"


def reason_group(reason: str | None) -> str:
    """'cache expired (idle 7 min)' -> 'cache expired', for the day's totals."""
    return re.sub(r" (?:from|\().*$", "", reason or "")


def command_text(text: str) -> str | None:
    """A prompt as the user typed it: a slash command's tags -> '/name args';
    other tagged text (command output, reminders, caveats) -> None."""
    name = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    if name:
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        return f"{name.group(1).strip()} {args.group(1).strip() if args else ''}".strip()
    if text.lstrip().startswith("<") or text.startswith("Caveat:"):
        return None
    return text


def prompt_text(record: Record) -> str | None:
    """What the user typed, if this user record is a real prompt (not a tool
    result, skill body, compaction summary or other generated text)."""
    data = record.data
    if record.subagent or data.get("isSidechain") or data.get("isMeta") or data.get("isCompactSummary"):
        return None
    content = (data.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        if not texts or any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
            return None
        text = "\n".join(texts)
    else:
        return None
    text = command_text(text)
    return " ".join(text.split()) or None if text else None


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
    first_prompt: str | None = None
    last_prompt: str | None = None
    last_prompt_at: float | None = None
    last_activity: float | None = None
    quota_seen: bool = False
    ended: bool = False  # Claude Code wrote its closing cost-state, and nothing since
    cost_state: float | None = None  # Claude Code's own total, written when the session closes
    compact_post_tokens: int | None = None
    requests: dict[str, Request] = field(default_factory=dict)
    chains: dict[str, ChainState] = field(default_factory=lambda: defaultdict(ChainState))
    cost_total: float = 0.0
    cost_by_day: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    # The session's first main-conversation prompt, and the model it was on: mostly
    # Claude Code's tool list and system prompt. Only the first: after a /model
    # switch, the first prompt on the new model is the whole conversation.
    prefix: int = 0
    prefix_model: str | None = None
    revision: int = 0  # bumped on every change to its requests
    _memo: tuple = field(default=(-1, None), repr=False)  # see memo()
    # (model family, effort) -> [output tokens, requests], main chain only
    outputs: dict[tuple, list] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    uuids: dict[str, tuple] = field(default_factory=dict)  # uuid -> (time, message id, parent uuid)

    # --- Naming ---------------------------------------------------------------

    @property
    def name(self) -> str:
        """What the user calls this session: /rename, the Desktop sidebar title,
        Claude Code's own title, or failing those the first thing they typed."""
        return (
            self.custom_title or self.desktop_title or self.agent_name or self.ai_title
            or snippet(self.first_prompt, 40) or "(new session)"
        )

    @property
    def where(self) -> str:
        cwd = self.cwd or ""
        if "/Library/Application Support/Claude/" in cwd:
            folder = "Desktop (no folder)"
        else:
            folder = Path(cwd).name or "?"
        if self.branch and self.branch not in ("HEAD", "main", "master"):
            return f"{folder}@{self.branch}"
        return folder

    @property
    def label(self) -> str:
        return f"{self.name} · {self.where}"

    @property
    def short_id(self) -> str:
        return self.id[:4]

    def billing(self, subscription_account: bool) -> str:
        """'sub' when this session runs on the subscription (1-hour cache, or
        rate-limit info on its replies), else 'api': an API key, usage credits
        or a cloud provider, all billed per token with the 5-minute cache."""
        on_plan = self.main.ttl == ONE_HOUR or self.quota_seen
        return "sub" if subscription_account and on_plan else "api"

    @property
    def scripted(self) -> bool:
        return self.entrypoint not in (None, "cli", "claude-vscode", "claude-desktop")

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

    def prefix_on(self, model: str | None) -> float:
        """The tool list and system prompt, counted in `model`'s tokens."""
        return convert_tokens(self.prefix, model_key(self.prefix_model), model_key(model))

    def main_requests(self) -> list[Request]:
        """The main conversation's requests, oldest first."""
        return memo(self, "main", lambda: sorted(
            (r for r in self.requests.values() if not r.subagent), key=lambda r: r.start))

    def cached_on(self, model: str | None, now: float) -> bool:
        """Whether the main conversation still has a cache on `model`: the model
        it's on, or one it left less than a cache lifetime ago."""
        family = model_key(model)
        if family == model_key(self.model):
            touched, ttl = self.main.touched, self.main.ttl  # recaps restart this clock too
        else:
            last = memo(self, ("last on", family), lambda: next(
                (r for r in reversed(self.main_requests()) if model_key(r.model) == family), None))
            if last is None:
                return False
            touched, ttl = last.start, last.ttl or self.main.ttl
        return touched is not None and now - touched < (ttl or FIVE_MINUTES)

    def growth(self) -> int:
        """Typical tokens one message adds to the conversation: the median rise
        between consecutive main-conversation prompts."""
        def compute() -> int:
            prompts = [r.prompt for r in self.main_requests()]
            rises = [b - a for a, b in zip(prompts, prompts[1:]) if b > a]
            return int(median(rises)) if rises else DEFAULT_GROWTH
        return memo(self, "growth", compute)

    def output_totals(self, model: str | None, effort: str | None = None) -> tuple[int, int]:
        """(output tokens, requests) in the main conversation on this model (and effort, if given)."""
        family = model_key(model)
        total = count = 0
        for (m, e), (tokens, n) in self.outputs.items():
            if m == family and (effort is None or e == effort):
                total, count = total + tokens, count + n
        return total, count

    def average_output(self, model: str | None, effort: str | None = None) -> float | None:
        """Output tokens per main-conversation request on this model (and effort, if given)."""
        total, count = self.output_totals(model, effort)
        return total / count if count else None


class Store:
    """All sessions, the live feed, and each day's totals."""

    def __init__(self, prices: dict) -> None:
        self.prices = prices
        self.sessions: dict[str, Session] = {}
        self.feed: deque[Request] = deque(maxlen=FEED_HISTORY)
        self.days: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))  # day -> cost, read, prompt, requests
        # day -> reason -> $ paid to write again what could have been read back
        self.rewrites: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.unpriced: Counter = Counter()  # models missing from pricing.yaml
        self.first: float | None = None
        self.revision = 0  # bumped on every change to any request
        self._memo: tuple = (-1, None)  # see memo()

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
        if record.when is not None:
            self.first = min(self.first or record.when, record.when)
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
                        self.revision += 1  # memo()s group sessions by these
        if kind == "assistant":
            return self._add_assistant(session, record)
        if kind == "user":
            text = prompt_text(record)
            if text:
                session.ended = False
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
            session.cost_state = float(data["totalCostUSD"])
            session.ended = True
        elif kind == "system" and not record.subagent:
            main = session.main
            if data.get("subtype") == "compact_boundary":
                main.compacted = True
                post = (data.get("compactMetadata") or {}).get("postTokens")
                session.compact_post_tokens = post if isinstance(post, int) else None
            elif data.get("subtype") == "away_summary" and record.when is not None:
                # A recap resends the conversation: it reads the cache and restarts its clock.
                started = record.when - RECAP_LAG
                if main.touched is not None and started > main.touched:
                    main.touched = started
        return None

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
        if data.get("quotaLimits") is not None:
            session.quota_seen = True
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
                              version=data.get("version"))
            request.prev = ChainState(**vars(chain))
            session.requests[key] = request
        else:
            request = old
            self._account(session, request, -1)
            request.end = end or request.end
        request.usage = usage_parts(usage)
        price = self.prices.get(model_key(model))
        if price is None:
            self.unpriced[model_key(model)] += 1 if old is None else 0
        request.cost = request_cost(usage, price) if price else None
        self._classify(request, price)
        self._account(session, request, +1)
        if chain.key in (None, key) or old is None:
            chain.key, chain.prompt, chain.model, chain.effort = key, request.prompt, model, request.effort
            chain.version, chain.touched = request.version, request.start
            chain.ttl = request.ttl or chain.ttl
            if old is None:
                chain.compacted = False
        if not record.subagent:
            session.ended = False
            if not session.prefix and request.prompt:
                session.prefix, session.prefix_model = request.prompt, model
        session.last_activity = max(session.last_activity or 0, request.end)
        if old is None:
            self._to_feed(request)
            return request
        return None

    def _to_feed(self, request: Request) -> None:
        """Newest first, by when each request started (its end moves on as
        later blocks of the reply come in). A transcript file found late (the
        tailer looks for new files every few seconds) can bring requests older
        than ones already in the feed; they go in their place, not on top."""
        feed = self.feed
        at = bisect_left(feed, -request.start, key=lambda r: -r.start)
        if len(feed) == feed.maxlen:
            if at == len(feed):
                return  # older than everything the feed still keeps
            feed.pop()
        feed.insert(at, request)

    def _classify(self, request: Request, price: dict | None) -> None:
        prev = request.prev
        request.rewritten, request.rewrite_cost, request.reason = 0, 0.0, None
        if prev is None or not prev.key or not prev.prompt:
            return
        could_read = min(request.prompt, prev.prompt)
        missed = max(0, could_read - request.usage.get("read", 0))
        if missed < max(REWRITE_MIN_TOKENS, REWRITE_SHARE * could_read):
            return
        request.rewritten = missed
        request.reason = rewrite_reason(request, prev)
        if price:
            ttl = request.ttl or prev.ttl or FIVE_MINUTES
            request.rewrite_cost = missed * (write_price(price, ttl) - price["cache_read"]) / 1_000_000

    def _account(self, session: Session, request: Request, sign: int) -> None:
        """Add a request to (or take it out of) the running totals."""
        session.revision += 1
        self.revision += 1
        day = self.days[day_of(request.end)]
        cost = request.cost or 0.0
        day["cost"] += sign * cost
        day["read"] += sign * request.usage.get("read", 0)
        day["prompt"] += sign * request.prompt
        day["requests"] += sign
        session.cost_total += sign * cost
        session.cost_by_day[day_of(request.end)] += sign * cost
        if request.reason:
            self.rewrites[day_of(request.end)][reason_group(request.reason)] += sign * request.rewrite_cost
        if not request.subagent:
            stats = session.outputs[(model_key(request.model), request.effort)]
            stats[0] += sign * request.usage.get("output", 0)
            stats[1] += sign

    # --- Across sessions --------------------------------------------------------

    def folder(self, cwd: str | None) -> list[Session]:
        """The sessions in this folder that have sent a main-conversation request."""
        return memo(self, ("folder", cwd), lambda: [s for s in self.sessions.values() if s.cwd == cwd and s.prefix])

    def tool_list(self, session: Session, model: str | None) -> float:
        """Claude Code's tool list and system prompt as `session` sends them,
        in `model`'s tokens. Every first prompt is those plus a first message,
        which may paste a whole file, so the estimate is the smallest first
        prompt among this session and the other interactive ones in its folder
        started from the same app. A scripted run can bring a system prompt of
        its own, so it's compared with nothing else."""
        peers = [session] if session.scripted else [
            s for s in self.folder(session.cwd) if s.entrypoint == session.entrypoint]
        return min((s.prefix_on(model) for s in peers if s.prefix), default=0.0)

    def shared_prefix(self, session: Session, model: str | None, now: float) -> int:
        """Tokens `model` likely already has cached for `session`: its tool list
        and system prompt, if a session in the same folder used that model
        within its cache lifetime (research/CACHE-DECISIONS.md §7). This
        session counts too, if it left that model a moment ago; only its tool
        list, as the rest may be too far back to be found (§6, round trips).

        Another session shares at most its own first prompt (a scripted run's
        can be small, with a system prompt of its own), so the best match
        among those still cached is the estimate."""
        own, shared = self.tool_list(session, model), 0.0
        for other in self.folder(session.cwd):
            if other.cached_on(model, now):
                shared = max(shared, min(own, other.prefix_on(model)))
        return round(shared)

    def average_output(self, model: str | None, effort: str | None = None) -> float | None:
        """Output tokens per main-conversation request on this model (and effort),
        across interactive sessions. Scripted runs are left out: a `claude -p
        'say OK'` would make a model or effort look nearly free."""
        def compute() -> float | None:
            totals = [s.output_totals(model, effort) for s in self.sessions.values() if not s.scripted]
            tokens, count = sum(t for t, _ in totals), sum(n for _, n in totals)
            return tokens / count if count else None
        return memo(self, ("output", model_key(model), effort), compute)

    def output_ratio(self, model: str | None, effort: str | None, other: str | None) -> float | None:
        """How long replies on this model are at effort `other`, compared with
        `effort`: from the interactive sessions that used both, so that each
        compares a task with itself. None if none did."""
        def compute() -> float | None:
            pairs = [(s.average_output(model, effort), s.average_output(model, other))
                     for s in self.sessions.values() if not s.scripted]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
            base = sum(a for a, _ in pairs)
            return sum(b for _, b in pairs) / base if base else None
        return memo(self, ("ratio", model_key(model), effort, other), compute)

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
