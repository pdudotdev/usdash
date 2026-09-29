"""The Stats view's figures: where the money went over the sessions window.

Everything here is a sum over logged requests with a known price, whose end
falls in the period (the window, up to now): the same requests and amounts
as the sessions view, grouped other ways. Nothing is estimated. Pure data:
ui.py draws it.
"""
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import PurePath

from .sessions import Session, Store, reason_group

# Context-size bands, by the prompt a request sent: (upper bound, label).
BANDS = ((50_000, "under 50k"), (100_000, "50–100k"), (200_000, "100–200k"), (500_000, "200–500k"),
         (None, "500k and more"))
MAX_DAYS = 14  # rows in "by day": older days fold into one "earlier" row
TOP = 5  # rows in "top sessions", "by project" (before "others") and "costliest prompts"
RECENT = 5 * 3600  # "last 5 hours"
REFRESH = 5  # seconds: while sessions are at work, the stats are worked out again at most this often
# What each kind of token costs, as (usage part, price field).
KINDS = (("cache reads", "read", "cache_read"), ("cache writes (1h)", "write_1h", "cache_write_1h"),
         ("cache writes (5m)", "write_5m", "cache_write"), ("uncached input", "fresh", "input"),
         ("output", "output", "output"))
EARLIER = "earlier"
NO_FOLDER = "no folder"


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
    name: str  # the folder's name, with as much of its path as tells it apart; or 'others'
    sessions: int = 0
    spend: float = 0.0


@dataclass
class Turn:
    """Something typed (a prompt or a slash command) and the requests it set off in the period."""
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
    prompts: int = 0  # the prompts and commands the period's requests answer (Turn)
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
    turns: list[Turn] = field(default_factory=list)  # the costliest TOP

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
        """The top sessions' share of SPEND, together."""
        return sum(row.spend for row in self.top) / self.spend if self.spend else 0.0


@dataclass
class _Cached:
    key: tuple  # (minute, window)
    revision: int  # store.revision when worked out
    at: float  # when
    stats: Stats


def local_days(start: float, now: float) -> list[str]:
    """Each local day from the one `start` falls on to today, oldest first."""
    first, last = datetime.fromtimestamp(start).date(), datetime.fromtimestamp(now).date()
    days, day = [], first
    while day <= last:
        days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def compute(store: Store, now: float, window: int) -> Stats:
    """The stats for the period `window` back from `now`. Kept for the rest of
    the minute while nothing changes, and for REFRESH seconds while something
    does: a busy session brings new records every second, and working the
    stats out walks every request loaded."""
    key = (int(now // 60), window)
    cached = getattr(store, "_stats", None)
    if cached is not None and cached.key == key and (cached.revision == store.revision or now - cached.at < REFRESH):
        return cached.stats
    stats = _compute(store, now, window)
    store._stats = _Cached(key, store.revision, now, stats)
    return stats


def _compute(store: Store, now: float, window: int) -> Stats:
    start = now - window
    stats = Stats(start, now, window)
    recent_from = now - RECENT if window > RECENT else None
    stats.recent = 0.0 if recent_from is not None else None
    days = {day: Day(day) for day in local_days(start, now)}
    kinds = [[0, 0.0] for _ in KINDS]  # tokens, spend
    models: dict[tuple[str | None, bool], ModelRow] = {}
    causes: dict[str, Cause] = {}
    bands = [Band(label) for _, label in BANDS]
    rows: list[SessionRow] = []
    turns: list[Turn] = []
    for session in store.sessions.values():
        typed = sorted(session.prompts.values())
        times = [when for when, _, _ in typed]
        answers: dict[int, Turn] = {}  # index in `typed` -> its turn
        row, counted = SessionRow(session), 0
        for r in session.requests.values():
            cost, paid = r.cost, r.paid
            if cost is None or paid is None or r.end < start:
                continue
            counted += 1
            u, prompt = r.usage, r.prompt
            read = u.get("read", 0)
            stats.spend += cost
            stats.requests += 1
            stats.read += read
            stats.prompt += prompt
            stats.misses += r.rewrite_cost
            if r.subagent:
                stats.subagents += cost
            elif prompt > row.peak:
                row.peak = prompt
            if recent_from is not None and r.end >= recent_from:
                stats.recent += cost
            day = days.get(r.day)
            if day is None:  # a clock set back: its own row
                day = days[r.day] = Day(r.day)
            day.spend += cost
            day.requests += 1
            day.read += read
            day.prompt += prompt
            day.misses += r.rewrite_cost
            for kind, (_, part, price) in zip(kinds, KINDS):
                tokens = u.get(part, 0)
                if tokens:
                    kind[0] += tokens
                    kind[1] += tokens * paid[price] / 1e6
            if searches := u.get("searches", 0):
                stats.searches += searches
                stats.search_spend += searches * store.web_search
            fast = r.speed == "fast"
            model = models.get((r.family, fast))
            if model is None:
                model = models[(r.family, fast)] = ModelRow(r.model, fast, r.effort)
            model.requests += 1
            model.spend += cost
            if r.end >= model.latest:
                model.model, model.effort, model.latest = r.model, r.effort, r.end
            if r.reason:
                group = reason_group(r.reason)
                cause = causes.get(group)
                if cause is None:
                    cause = causes[group] = Cause(group)
                cause.misses += 1
                cause.rewritten += r.rewritten
                cause.extra += r.rewrite_cost
            band = bands[band_of(prompt)]
            band.requests += 1
            band.spend += cost
            row.spend += cost
            # The turn it belongs to: the latest thing typed in its session at or before it started.
            if (i := bisect_right(times, r.start) - 1) >= 0:
                turn = answers.get(i)
                if turn is None:
                    turn = answers[i] = Turn(session, typed[i][0], typed[i][1])
                turn.requests += 1
                turn.spend += cost
        if counted:
            rows.append(row)
        turns.extend(answers.values())

    stats.sessions = len(rows)
    stats.days = fold_days(sorted(days.values(), key=lambda d: d.day))
    stats.kinds = [Kind(name, tokens, spend) for (name, _, _), (tokens, spend) in zip(KINDS, kinds) if tokens]
    stats.models = sorted(models.values(), key=lambda m: -m.spend)
    stats.causes = sorted(causes.values(), key=lambda c: -c.extra)
    stats.bands = [b for b in bands if b.requests]
    ranked = sorted(rows, key=lambda row: -row.spend)
    stats.top = ranked[:TOP]
    stats.projects = projects(ranked)
    stats.prompts = len(turns)
    stats.turns = sorted(turns, key=lambda t: (-t.spend, -t.when))[:TOP]
    return stats


def band_of(prompt: int) -> int:
    """Which of BANDS a request of this size falls in."""
    for i, (upper, _) in enumerate(BANDS):
        if upper is None or prompt < upper:
            return i
    raise AssertionError("the last band has no upper bound")


def fold_days(days: list[Day]) -> list[Day]:
    """At most MAX_DAYS rows: the oldest days fold into one EARLIER row, left
    out if nothing was spent on them."""
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
    return [earlier, *kept] if earlier.requests else kept


def folder_key(session: Session) -> str:
    """What sessions are grouped by in "by project": the folder's whole path (two
    folders can share a name), or NO_FOLDER for Desktop sessions without one,
    each of which runs in a scratch folder of its own."""
    return NO_FOLDER if session.folder == NO_FOLDER else session.cwd or "?"


def folder_names(keys: list[str]) -> dict[str, str]:
    """Each folder's name, with as much of the path before it as it takes to
    tell it apart from the others: 'api', or 'work/api' and 'personal/api'."""
    parts = {key: PurePath(key).parts if key.startswith("/") else (key,) for key in keys}
    depth = dict.fromkeys(keys, 1)
    while True:
        names = {key: "/".join(parts[key][-depth[key]:]) for key in keys}
        seen: dict[str, list[str]] = {}
        for key, name in names.items():
            seen.setdefault(name, []).append(key)
        clashes = [key for same in seen.values() if len(same) > 1 for key in same
                   if depth[key] < len(parts[key]) - (parts[key][0] == "/")]
        if not clashes:
            return names
        for key in clashes:
            depth[key] += 1


def projects(ranked: list[SessionRow]) -> list[Project]:
    """Spend by folder, the biggest TOP, then the rest as 'others'."""
    found: dict[str, Project] = {}
    for row in ranked:
        project = found.setdefault(folder_key(row.session), Project(""))
        project.sessions += 1
        project.spend += row.spend
    for key, name in folder_names(list(found)).items():
        found[key].name = name
    ordered = sorted(found.values(), key=lambda p: (-p.spend, p.name))
    if len(ordered) <= TOP:
        return ordered
    others = Project("others")
    for project in ordered[TOP:]:
        others.sessions += project.sessions
        others.spend += project.spend
    return [*ordered[:TOP], others]


def day_label(day: str, today: str) -> str:
    """'2026-09-24' -> 'Thu 24 Sep' (with the year if it isn't this year's)."""
    if day == EARLIER:
        return EARLIER
    when = date.fromisoformat(day)
    text = when.strftime("%a %d %b" if day[:4] == today[:4] else "%a %d %b %Y")
    return text[:4] + text[4:].lstrip("0") if text[4] == "0" else text
