"""The Stats view's figures: where the money went over the sessions window.

Everything here is a sum over logged requests with a known price, whose end
falls in the period (the window, up to now): the same requests and amounts
as the sessions view, grouped other ways. Nothing is estimated. Pure data:
ui.py draws it.
"""
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .models import model_key
from .sessions import Request, Session, Store, day_of, reason_group

# Context-size bands, by the prompt a request sent: (upper bound, label).
BANDS = ((50_000, "under 50k"), (100_000, "50–100k"), (200_000, "100–200k"), (500_000, "200–500k"),
         (None, "500k and more"))
MAX_DAYS = 14  # rows in "by day": older days fold into one "earlier" row
TOP = 5  # rows in "top sessions", "by project" (before "others") and "costliest prompts"
RECENT = 5 * 3600  # "last 5 hours"
# What each kind of token costs, as (usage part, price field).
KINDS = (("cache reads", "read", "cache_read"), ("cache writes (1h)", "write_1h", "cache_write_1h"),
         ("cache writes (5m)", "write_5m", "cache_write"), ("uncached input", "fresh", "input"),
         ("output", "output", "output"))
EARLIER = "earlier"


@dataclass
class Day:
    day: str  # YYYY-MM-DD, or EARLIER
    spend: float = 0.0
    requests: int = 0
    read: int = 0
    prompt: int = 0  # input-side tokens: fresh + read + writes
    misses: float = 0.0

    @property
    def cached(self) -> float | None:
        return self.read / self.prompt if self.prompt else None


@dataclass
class Kind:
    name: str
    tokens: int
    spend: float


@dataclass
class ModelRow:
    model: str  # the model id of its latest request
    fast: bool
    effort: str | None  # its latest request's
    requests: int = 0
    spend: float = 0.0
    latest: float = 0.0


@dataclass
class Cause:
    cause: str  # reason_group(): 'cache expired', 'model switch', …
    misses: int = 0
    rewritten: int = 0
    extra: float = 0.0


@dataclass
class Band:
    label: str
    requests: int = 0
    spend: float = 0.0


@dataclass
class SessionRow:
    session: Session
    spend: float = 0.0
    peak: int = 0  # the largest main-conversation prompt in the period


@dataclass
class Project:
    name: str  # Session.folder, or 'others'
    sessions: int = 0
    spend: float = 0.0


@dataclass
class Turn:
    """A prompt or slash command typed, and the requests it set off."""
    session: Session
    when: float
    text: str
    requests: int = 0
    spend: float = 0.0


@dataclass
class Stats:
    start: float  # the period: from here up to `now`
    now: float
    window: int
    spend: float = 0.0
    requests: int = 0
    prompts: int = 0  # typed, not slash commands
    sessions: int = 0
    subagents: float = 0.0  # their spend
    recent: float | None = None  # spend in the last RECENT seconds; None when the window is shorter
    read: int = 0
    prompt: int = 0
    misses: float = 0.0
    searches: int = 0  # server-side web searches in logged requests
    search_spend: float = 0.0
    days: list[Day] = field(default_factory=list)
    kinds: list[Kind] = field(default_factory=list)
    models: list[ModelRow] = field(default_factory=list)
    causes: list[Cause] = field(default_factory=list)
    bands: list[Band] = field(default_factory=list)
    top: list[SessionRow] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)

    @property
    def per_day(self) -> float | None:
        """SPEND a day, over a window longer than a day."""
        return self.spend / (self.window / 86400) if self.window > 86400 else None

    @property
    def cached(self) -> float | None:
        return self.read / self.prompt if self.prompt else None

    @property
    def input_price(self) -> float | None:
        """What input tokens (reads, writes, uncached) cost on average, per million."""
        kinds = [k for k in self.kinds if k.name != "output"]
        tokens = sum(k.tokens for k in kinds)
        return sum(k.spend for k in kinds) / tokens * 1e6 if tokens else None

    @property
    def top_share(self) -> float:
        return sum(row.spend for row in self.top) / self.spend if self.spend else 0.0


def priced(store: Store, start: float) -> list[Request]:
    """Every request with a known price whose end falls in the period."""
    return [r for s in store.sessions.values() for r in s.requests.values()
            if r.cost is not None and r.paid is not None and r.end >= start]


def local_days(start: float, now: float) -> list[str]:
    """Each local day from the one `start` falls on to today, oldest first."""
    first, last = datetime.fromtimestamp(start).date(), datetime.fromtimestamp(now).date()
    days, day = [], first
    while day <= last:
        days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def compute(store: Store, now: float, window: int) -> Stats:
    """The stats for the period `window` back from `now`, kept while nothing
    changes within the same minute."""
    key = (store.revision, int(now // 60), window)
    cached = getattr(store, "_stats", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    stats = _compute(store, now, window)
    store._stats = (key, stats)
    return stats


def _compute(store: Store, now: float, window: int) -> Stats:
    start = now - window
    stats = Stats(start, now, window)
    requests = priced(store, start)
    stats.recent = 0.0 if window > RECENT else None
    days = {day: Day(day) for day in local_days(start, now)}
    kinds = {name: Kind(name, 0, 0.0) for name, _, _ in KINDS}
    models: dict[tuple[str | None, bool], ModelRow] = {}
    causes: dict[str, Cause] = {}
    bands = [Band(label) for _, label in BANDS]
    by_session: dict[str, SessionRow] = {}
    for r in requests:
        cost, u = r.cost, r.usage
        stats.spend += cost
        stats.requests += 1
        stats.read += u.get("read", 0)
        stats.prompt += r.prompt
        stats.misses += r.rewrite_cost
        if r.subagent:
            stats.subagents += cost
        if stats.recent is not None and r.end >= now - RECENT:
            stats.recent += cost
        day = days.setdefault(day_of(r.end), Day(day_of(r.end)))  # a clock set back: its own row
        day.spend += cost
        day.requests += 1
        day.read += u.get("read", 0)
        day.prompt += r.prompt
        day.misses += r.rewrite_cost
        for name, part, price in KINDS:
            kinds[name].tokens += u.get(part, 0)
            kinds[name].spend += u.get(part, 0) * r.paid[price] / 1e6
        stats.searches += u.get("searches", 0)
        stats.search_spend += u.get("searches", 0) * store.web_search
        fast = r.speed == "fast"
        row = models.setdefault((model_key(r.model), fast), ModelRow(r.model, fast, r.effort))
        row.requests += 1
        row.spend += cost
        if r.end >= row.latest:
            row.model, row.effort, row.latest = r.model, r.effort, r.end
        if r.reason:
            group = reason_group(r.reason)
            cause = causes.setdefault(group, Cause(group))
            cause.misses += 1
            cause.rewritten += r.rewritten
            cause.extra += r.rewrite_cost
        band = next(i for i, (upper, _) in enumerate(BANDS) if upper is None or r.prompt < upper)
        bands[band].requests += 1
        bands[band].spend += cost
        session = store.sessions[r.session]
        top = by_session.setdefault(r.session, SessionRow(session))
        top.spend += cost
        if not r.subagent:
            top.peak = max(top.peak, r.prompt)

    stats.sessions = len(by_session)
    stats.days = fold_days(sorted(days.values(), key=lambda d: d.day))
    stats.kinds = [kind for kind in kinds.values() if kind.tokens]
    stats.models = sorted(models.values(), key=lambda m: -m.spend)
    stats.causes = sorted(causes.values(), key=lambda c: -c.extra)
    stats.bands = [b for b in bands if b.requests]
    ranked = sorted(by_session.values(), key=lambda row: -row.spend)
    stats.top = ranked[:TOP]
    stats.projects = projects(ranked)
    stats.prompts = sum(1 for s in store.sessions.values() for when, _, command in s.prompts.values()
                        if not command and when >= start)
    stats.turns = costliest_turns(store, start)
    return stats


def fold_days(days: list[Day]) -> list[Day]:
    """At most MAX_DAYS rows: the oldest days fold into one EARLIER row."""
    if len(days) <= MAX_DAYS:
        return days
    older, kept = days[: len(days) - MAX_DAYS + 1], days[len(days) - MAX_DAYS + 1:]
    earlier = Day(EARLIER)
    for day in older:
        earlier.spend += day.spend
        earlier.requests += day.requests
        earlier.read += day.read
        earlier.prompt += day.prompt
        earlier.misses += day.misses
    return [earlier, *kept]


def projects(ranked: list[SessionRow]) -> list[Project]:
    """Spend by folder, the biggest TOP, then the rest as 'others'."""
    found: dict[str, Project] = {}
    for row in ranked:
        project = found.setdefault(row.session.folder, Project(row.session.folder))
        project.sessions += 1
        project.spend += row.spend
    ordered = sorted(found.values(), key=lambda p: (-p.spend, p.name))
    if len(ordered) <= TOP:
        return ordered
    others = Project("others")
    for project in ordered[TOP:]:
        others.sessions += project.sessions
        others.spend += project.spend
    return [*ordered[:TOP], others]


def costliest_turns(store: Store, start: float) -> list[Turn]:
    """The TOP turns with the highest spend among those typed in the period. A
    turn's requests are its session's (main and subagent) that started at or
    after it and before the session's next prompt or command. A subagent still
    at work after the next prompt counts toward that one."""
    turns = []
    for session in store.sessions.values():
        typed = sorted(session.prompts.values())
        if not typed or typed[-1][0] < start:
            continue
        spent = sorted((r.start, r.cost) for r in session.requests.values()
                       if r.cost is not None and r.paid is not None)
        starts = [when for when, _ in spent]
        for i, (when, text, _) in enumerate(typed):
            if when < start:
                continue
            until = typed[i + 1][0] if i + 1 < len(typed) else float("inf")
            lo, hi = bisect_left(starts, when), bisect_left(starts, until)
            if hi > lo:
                turns.append(Turn(session, when, text, hi - lo, sum(cost for _, cost in spent[lo:hi])))
    turns.sort(key=lambda t: (-t.spend, -t.when))
    return turns[:TOP]


def day_label(day: str, today: str) -> str:
    """'2026-09-24' -> 'Thu 24 Sep' (with the year if it isn't this year's)."""
    if day == EARLIER:
        return EARLIER
    when = date.fromisoformat(day)
    text = when.strftime("%a %d %b" if day[:4] == today[:4] else "%a %d %b %Y")
    return text[:4] + text[4:].lstrip("0") if text[4] == "0" else text
