"""The screen: a header, the sessions, advice, and a scrollable feed of requests.

Rendering is copied in spirit from llm-trunk's scripts/dashboard.py: rich
panels in a Layout, a feed whose last column (NOTE) runs to the end of the
line, a stable colour per session, and the same scrolling keys.
"""
import itertools
import zlib
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

from .advice import Advice, Memory, advise, clock, money
from .engine import cache_state
from .models import pretty_model
from .sessions import Request, Session, Store, day_of, snippet

MODEL_STYLES = {"Opus": "magenta", "Sonnet": "blue", "Haiku": "green", "Fable": "yellow"}
SESSION_STYLES = ["cyan", "yellow", "magenta", "green", "blue", "bright_cyan", "bright_yellow", "bright_magenta"]
HEADER_HEIGHT = 4
MAX_SESSION_ROWS = 6
MAX_ADVICE_ROWS = 6
GAP = "  "


@dataclass
class View:
    """What the screen needs besides the data: the time, the account, and the feed's scroll position."""
    now: float
    subscription: bool = False
    window: int = 3 * 3600  # sessions idle longer than this are hidden
    prices_verified: str = "?"
    unknown_types: int = 0
    scroll: int = 0  # feed rows hidden above the view (newest first)
    top: Request | None = None  # the request at the top of the view, while scrolled back
    page: int = 1
    unseen: int = 0
    memory: Memory = field(default_factory=Memory)  # when each switch tip first appeared


# --- Small pieces ----------------------------------------------------------------


def _money(value: float | None) -> str:
    return "?" if value is None else money(value)


def _tokens(value: int | float | None) -> str:
    if value is None:
        return "?"
    return f"{value / 1000:.0f}k" if value >= 10_000 else f"{value / 1000:.1f}k"


def duration_text(seconds: int) -> str:
    """3600 -> '1h', 5400 -> '90m', 86400 -> '1d': exact, never rounded."""
    if seconds and seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m" if seconds % 60 == 0 else f"{seconds}s"


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def session_style(session_id: str) -> str:
    return SESSION_STYLES[zlib.crc32(session_id.encode()) % len(SESSION_STYLES)]


def session_tag(session: Session) -> Text:
    """The short id in the session's own colour: the tie-breaker between look-alike names."""
    return Text(session.short_id, style=f"bold {session_style(session.id)}")


def model_text(model: str | None, effort: str | None = None) -> Text:
    name = pretty_model(model)
    text = Text(name, style=MODEL_STYLES.get(name.split(" ")[0], ""))
    if effort:
        text.append(f" {effort}", style="dim")
    return text


def visible_sessions(store: Store, view: View) -> list[Session]:
    """Sessions active within the window, most recent first; archived Desktop sessions hidden."""
    shown = [
        s for s in store.sessions.values()
        if s.last_activity and view.now - s.last_activity <= view.window and not s.archived and s.last_request
    ]
    return sorted(shown, key=lambda s: -(s.last_activity or 0))


# --- Header ------------------------------------------------------------------------


def header(store: Store, view: View) -> Panel:
    today = day_of(view.now)
    day = store.days.get(today, {})
    spent = day.get("cost", 0.0)
    hit = day.get("read", 0) / day["prompt"] if day.get("prompt") else None
    rewrites = {reason: cost for reason, cost in store.rewrites.get(today, {}).items() if cost >= 0.005}
    # One line each: the header has room for exactly two.
    line = Text.assemble(("TODAY ", "bold"), (_money(spent), "bold green"), (" est.", "dim"), no_wrap=True,
                         overflow="ellipsis")
    if hit is not None:
        line.append(f"  ·  {hit:.0%} of input read from cache", style="green" if hit >= 0.9 else "yellow")
    if rewrites:
        total = sum(rewrites.values())
        parts = ", ".join(f"{reason} {_money(cost)}" for reason, cost in sorted(rewrites.items(), key=lambda i: -i[1]))
        # What requests paid to write the conversation again instead of reading it back (the feed's ⟳ rows).
        line.append(f"  ·  ⟳ cache misses added {_money(total)} ({parts})", style="red")
    plan = "API-equivalent prices: your subscription isn't billed per token  ·  " if view.subscription else ""
    detail = Text(
        f"{plan}totals a bit low: Claude Code doesn't log background requests",
        style="dim", no_wrap=True, overflow="ellipsis",
    )
    if view.unknown_types:
        detail.append(f"  ·  {view.unknown_types} records of unknown types (newer Claude Code?)", style="yellow")
    since = f"history from {datetime.fromtimestamp(store.first):%a %H:%M}" if store.first else "waiting for transcripts"
    return Panel(Group(line, detail), title=Text("💲 usdash · live", style="bold"),
                 subtitle=Text(f"list prices of {view.prices_verified} · {since}", style="dim"), title_align="left")


# --- Sessions ----------------------------------------------------------------------


def cache_text(store: Store, session: Session, view: View) -> tuple[Text, Text]:
    """(CACHE column, NEXT MESSAGE column)."""
    state = cache_state(store, session, view.now)
    if state is None:
        return Text("—", style="dim"), Text("")
    if session.ended:
        cache = Text("closed", style="dim")
    elif state.warm:
        cache = Text(f"● {clock(state.left)}", style="green")
    else:
        cache = Text("○ cold", style="bold red")
    if state.next_now is None or state.next_cold is None:
        return cache, Text("")
    if session.ended:
        return cache, Text(f"if resumed: {_money(state.next_cold)}", style="dim")
    if state.warm:
        return cache, Text(f"{_money(state.next_now)} now · {_money(state.next_cold)} cold", style="dim")
    return cache, Text(f"{_money(state.next_cold)} (re-writes {_tokens(state.size)})", style="red")


# The numbers come first, most useful first: a narrow terminal cuts from the right. The last,
# unnamed column is Claude Code's own total once a session closes: apart, so it doesn't widen TOTAL.
SESSION_COLUMNS = (("ID", False), ("SESSION", False), ("WHERE", False), ("MODEL", False), ("CACHE", False),
                   ("NEXT MESSAGE", False), ("CONTEXT", True), ("TODAY", True), ("TOTAL", True), ("", False))


def session_row(store: Store, session: Session, view: View) -> tuple[list[Text], Text]:
    """(the cells of the session's first line, its second line: what you last typed there)."""
    name = Text(snippet(session.name, 34) or "", style="bold")
    tags = []
    if session.scripted:
        tags.append(session.entrypoint or "script")
    elif session.entrypoint == "claude-vscode":
        tags.append("vscode")
    elif session.entrypoint == "claude-desktop":
        tags.append("desktop")
    tags.append(session.billing(view.subscription))
    name.append(f" {' '.join(tags)}", style="dim")
    last = session.last_request
    cache, next_message = cache_text(store, session, view)
    own_total = Text(f"(CC {_money(session.cost_state)})" if session.cost_state is not None else "", style="dim")
    cells = [
        session_tag(session), name, Text(snippet(session.where, 26) or "", style="dim"),
        model_text(session.model, session.effort), cache, next_message,
        Text(_tokens(last.prompt if last else None)), Text(_money(session.cost_by_day.get(day_of(view.now), 0.0))),
        Text(_money(session.cost_total)), own_total,
    ]
    prompt = Text("└ ", style="dim")
    if session.last_prompt_at:
        prompt.append(f"{_ago(view.now - session.last_prompt_at)} · ", style="dim")
    prompt.append(f"\"{snippet(session.last_prompt, 200)}\"" if session.last_prompt else "no prompt yet", style="italic")
    return cells, prompt


def sessions_panel(store: Store, view: View, sessions: list[Session], rows: int) -> Panel:
    window = duration_text(view.window)
    title = f"sessions active in the last {window} · sub: 1h cache · api: 5m, billed per token"
    if not sessions:
        return Panel(Text(f"no Claude Code activity in the last {window}", style="dim"), title=title, title_align="left")
    lines = grid_lines(SESSION_COLUMNS, [session_row(store, s, view) for s in sessions[:rows]], note_below=True)
    hidden = len(sessions) - rows
    if hidden > 0:
        lines.append(Text(f"+{hidden} more (--window to change which are shown)", style="dim"))
    return Panel(Group(*lines), title=title, title_align="left")


# --- Advice ------------------------------------------------------------------------


def advice_lines(advice: list[Advice], subscription: bool) -> list[Text]:
    lines = []
    for item in advice:
        line = Text()  # wraps: the advice is the part worth reading in full
        line.append("⚡ " if item.urgent else "💡 ")
        line.append_text(session_tag(item.session))
        line.append(f" {snippet(item.session.name, 28)} · {item.session.where}: ", style=session_style(item.session.id))
        line.append(item.text)
        if subscription and item.session.billing(subscription) == "sub":
            line.append("  (uses less of your plan)", style="dim")
        lines.append(line)
    return lines


def advice_panel(lines: list[Text]) -> Panel:
    body = Group(*lines) if lines else Text("nothing to change right now", style="dim")
    return Panel(body, title="advice · estimates; cost isn't the only goal", title_align="left")


# --- Feed --------------------------------------------------------------------------

FEED_COLUMNS = (("TIME", False), ("ID", False), ("SESSION", False), ("MODEL", False), ("PROMPT", True),
                ("CACHED", True), ("OUT", True), ("COST", True))


def feed_row(store: Store, request: Request) -> tuple[list[Text], Text | None]:
    """(a cell for every column, the NOTE that runs on to the end of the line)."""
    session = store.sessions[request.session]
    prompt = request.prompt
    cached = request.usage.get("read", 0) / prompt if prompt else 0
    cells = [
        Text(datetime.fromtimestamp(request.start).strftime("%H:%M:%S"), style="dim"),  # the feed's order
        session_tag(session),
        Text(snippet(session.name, 24) or "", style=session_style(session.id)),
        model_text(request.model, request.effort),
        Text(_tokens(prompt)),
        Text(f"{cached:.0%}", style="green" if cached >= 0.8 else "yellow" if cached >= 0.3 else "red"),
        Text(_tokens(request.usage.get("output", 0)), style="dim"),
        Text(_money(request.cost)),
    ]
    note = Text()
    if request.subagent:
        note.append("🤖 subagent ", style="dim")
    if request.reason:
        note.append(f"⟳ re-wrote {_tokens(request.rewritten)}: {request.reason} (+{_money(request.rewrite_cost)})",
                    style="bold red")
    return cells, note if note.plain else None


def grid_lines(columns, rows: list[tuple[list[Text], Text | None]], note_below: bool = False) -> list[Text]:
    """Lay rows out in columns sized to what's shown. A row's note either runs
    on after the last column (the feed's NOTE) or gets its own line under the
    row, starting at the second column; the panel cuts whatever doesn't fit."""
    widths = [len(name) for name, _ in columns]
    for cells, _ in rows:
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], cell.cell_len)
    header = [Text(name, style="dim") for name, _ in columns]
    has_notes = not note_below and any(note for _, note in rows)
    lines = []
    for cells, note in [(header, Text("NOTE", style="dim") if has_notes else None), *rows]:
        line = Text(no_wrap=True, overflow="ellipsis")
        trailing = note if not note_below else None
        for i, cell in enumerate(cells):
            pad = " " * (widths[i] - cell.cell_len)
            line.append_text(Text(pad) + cell if columns[i][1] else cell + Text(pad))
            if i < len(cells) - 1 or trailing:
                line.append(GAP)
        if trailing:
            line.append_text(trailing)
        line.rstrip()
        lines.append(line)
        if note_below and note is not None:
            below = Text(" " * (widths[0] + len(GAP)), no_wrap=True, overflow="ellipsis")
            below.append_text(note)
            lines.append(below)
    return lines


def feed_lines(rows: list[tuple[list[Text], Text | None]]) -> list[Text]:
    return grid_lines(FEED_COLUMNS, rows)


def feed_panel(store: Store, view: View, rows: int) -> Panel:
    view.page = max(1, rows)
    scroll_to(store, view, view.scroll)
    shown = list(itertools.islice(store.feed, view.scroll, view.scroll + view.page))
    lines = feed_lines([feed_row(store, request) for request in shown])
    total = len(store.feed)
    if view.scroll:
        title = f"requests · paused · rows {view.scroll + 1}–{view.scroll + len(shown)} of {total}"
        if view.unseen:
            title += f" · {view.unseen} new above"
        subtitle = Text("g: back to live", style="bold yellow")
    else:
        title = f"requests · {total}" if total > len(shown) else "requests"
        subtitle = Text("↑↓/wheel/j/k: scroll · space/b: page · g/G: newest/oldest · q: quit", style="dim")
    return Panel(Group(*lines), title=title, subtitle=subtitle, title_align="left", subtitle_align="right")


# --- Scrolling ---------------------------------------------------------------------


def max_scroll(store: Store, view: View) -> int:
    return max(0, len(store.feed) - view.page)


def scroll_to(store: Store, view: View, row: int) -> None:
    """Show the feed from `row` down, and remember the request there (see track_feed)."""
    view.scroll = max(0, min(row, max_scroll(store, view)))
    view.top = store.feed[view.scroll] if view.scroll else None
    if not view.scroll:
        view.unseen = 0


def track_feed(store: Store, view: View) -> None:
    """New rows arrived: while scrolled back, keep the rows in view where they
    are. A row can land anywhere (a transcript found late brings older
    requests), so follow the request at the top of the view, and count as new
    only the rows that landed above it."""
    if not view.scroll or view.top is None:
        return
    row = next((i for i, request in enumerate(store.feed) if request is view.top), None)
    if row is None:  # it fell off the end of the full feed
        row = max_scroll(store, view)
    view.unseen += max(0, row - view.scroll)
    scroll_to(store, view, row)


def press(store: Store, view: View, key: str) -> None:
    """Scroll the feed: up/down a row, pgup/pgdn a page, home/end to either end."""
    steps = {"up": -1, "down": 1, "pgup": -view.page, "pgdn": view.page}
    if key in steps:
        scroll_to(store, view, view.scroll + steps[key])
    elif key == "home":
        scroll_to(store, view, 0)
    elif key == "end":
        scroll_to(store, view, max_scroll(store, view))


# --- Whole screen ------------------------------------------------------------------


def render(store: Store, view: View, height: int, width: int = 200) -> Layout:
    sessions = visible_sessions(store, view)
    advice = [item for session in sessions for item in advise(store, session, view.now, view.memory)]
    advice.sort(key=lambda item: not item.urgent)
    lines = advice_lines(advice[:MAX_ADVICE_ROWS], view.subscription)
    session_rows = min(max(len(sessions), 1), MAX_SESSION_ROWS)
    sessions_height = 2 * session_rows + 3 + (1 if len(sessions) > session_rows else 0)
    inside = max(20, width - 4)  # the panel's border and padding
    measure = Console(width=inside)
    advice_height = max(sum(len(line.wrap(measure, inside)) for line in lines), 1) + 2
    feed_rows = max(1, height - HEADER_HEIGHT - sessions_height - advice_height - 3)
    layout = Layout()
    layout.split_column(
        Layout(header(store, view), size=HEADER_HEIGHT),
        Layout(sessions_panel(store, view, sessions, session_rows), size=sessions_height),
        Layout(advice_panel(lines), size=advice_height),
        Layout(feed_panel(store, view, feed_rows)),
    )
    return layout
