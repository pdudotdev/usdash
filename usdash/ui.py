"""The screen: a header, then either the sessions (the default) or every request.

Sessions from the last 5 days, newest first, in three panes, in the order
you'd come back to them: live (cache warm), expired (still open), exited
(resumed with `claude --resume`). A live session shows what you last typed and
what its next message costs on each model; the others, what coming back costs.
Finished script runs fold into one row per folder. `r` swaps in the
request feed (rendering copied in spirit from llm-trunk's scripts/dashboard.py:
a NOTE column that runs to the end of the line, the same scrolling keys).
"""
import itertools
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Group
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

from .advice import Advice, advise, clock, money, plural, resends, tokens_text
from .engine import cache_clock, comeback, context
from .models import model_key, pretty_model
from .sessions import Request, Session, Store, day_of, snippet

MODEL_STYLES = {"Opus": "bright_magenta", "Sonnet": "bright_blue", "Haiku": "bright_green", "Fable": "bright_yellow"}
# The VS Code extension also runs in its forks (Cursor, Windsurf, …): "IDE" is true for all of them.
APPS = {"cli": "CLI", "claude-vscode": "IDE", "claude-desktop": "Desktop"}
SESSION_STYLES = ["cyan", "yellow", "magenta", "green", "blue", "bright_cyan", "bright_yellow", "bright_magenta"]
HEADER_HEIGHT = 5  # three lines and the frame
DEFAULT_WINDOW = 5 * 86400  # sessions active this recently are listed
PANE_FRAME = 2  # a pane's top and bottom border
GAP = "  "


@dataclass
class View:
    """What the screen needs besides the data: the time, the account, which
    view is showing, and where each one is scrolled to."""
    now: float
    subscription: bool = False
    window: int = DEFAULT_WINDOW  # sessions idle longer than this are hidden
    prices: str = "current API list prices"  # which prices, and how fresh (app.prices_label)
    docs_changed: list[str] = field(default_factory=list)  # Anthropic's pages that no longer read as expected
    unknown_types: int = 0
    mode: str = "sessions"  # or "requests"
    session_scroll: int = 0  # sessions hidden above the view
    session_page: int = 1  # sessions shown at the last render
    session_last: int = 0  # the furthest the sessions could scroll at the last render
    scroll: int = 0  # feed rows hidden above the view (newest first)
    top: Request | None = None  # the request at the top of the view, while scrolled back
    page: int = 1  # feed rows shown at the last render
    lines: int = 1  # lines the feed had for its rows and day separators at the last render
    unseen: int = 0


# --- Small pieces ----------------------------------------------------------------


def _money(value: float | None) -> str:
    return "?" if value is None else money(value)


_tokens = tokens_text


def duration_text(seconds: int) -> str:
    """3600 -> '1h', 5400 -> '90m', 86400 -> '24h', 172800 -> '2d': exact, never rounded."""
    if seconds and seconds % 86400 == 0 and seconds > 86400:
        return f"{seconds // 86400}d"
    if seconds and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m" if seconds % 60 == 0 else f"{seconds}s"


def age_text(seconds: float) -> str:
    """How long ago, rounded down: '5m', '3h', '2d'."""
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m"
    return f"{seconds // 3600}h" if seconds < 86400 else f"{seconds // 86400}d"


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds}s ago" if seconds < 60 else f"{age_text(seconds)} ago"


def session_style(session_id: str) -> str:
    return SESSION_STYLES[zlib.crc32(session_id.encode()) % len(SESSION_STYLES)]


def session_tag(session: Session) -> Text:
    """The short id in the session's own colour: the tie-breaker between look-alike names."""
    return Text(session.short_id, style=f"bold {session_style(session.id)}")


def model_style(model: str | None) -> str:
    return MODEL_STYLES.get(pretty_model(model).split(" ")[0], "")


def model_text(model: str | None, effort: str | None = None, speed: str | None = None) -> Text:
    """'Opus 5.5 high', and 'fast' in fast mode."""
    text = Text(pretty_model(model), style=model_style(model))
    if effort:
        text.append(f" {effort}")
    if speed == "fast":
        text.append(" fast", style="bold")
    return text


def app_name(session: Session) -> str:
    """Where a session runs: CLI, IDE (the VS Code extension, or a fork of it),
    Desktop, or script (`claude -p`, the SDKs). Any other entrypoint as Claude
    Code wrote it, so a new one names itself; '?' if the transcript doesn't say."""
    if session.scripted:
        return "script"
    return APPS.get(session.entrypoint or "") or snippet(session.entrypoint, 16) or "?"


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
    # One line each: the header has room for exactly three.
    line = Text.assemble(("TODAY ", "bold"), (_money(spent), "bold green"), no_wrap=True, overflow="ellipsis")
    if hit is not None:
        line.append(f"  ·  {hit:.0%} of input read from cache", style="green" if hit >= 0.9 else "yellow")
    if rewrites:
        total = sum(rewrites.values())
        parts = ", ".join(f"{reason} {_money(cost)}" for reason, cost in sorted(rewrites.items(), key=lambda i: -i[1]))
        # What requests paid to write the conversation again instead of reading it back (the feed's ⟳ rows).
        line.append(f"  ·  ⟳ cache misses added {_money(total)} ({parts})", style="red")
    detail = Text(
        f"At {view.prices}; your subscription isn't billed per token" if view.subscription
        else f"Estimated at {view.prices}",
        style="dim", no_wrap=True, overflow="ellipsis",
    )
    # Warnings first: the line is cut at the terminal's edge.
    if store.unpriced:
        count, models = sum(store.unpriced.values()), ", ".join(sorted(store.unpriced))
        detail.append(f"  ·  {plural(count, 'request')} with no known price, left out ({models})", style="yellow")
    if view.docs_changed:
        pages = " and ".join(view.docs_changed)
        detail.append(f"  ·  Anthropic's {pages} page{'s' if len(view.docs_changed) > 1 else ''} changed: "
                      f"usdash may need an update", style="yellow")
    if view.unknown_types:
        detail.append(f"  ·  {view.unknown_types} records of unknown types (newer Claude Code?)", style="yellow")
    # Claude Code bills requests its transcripts never log (titles, prompt suggestions, /compact's
    # summary); an exited session's TOTAL is Claude Code's own, which counts them.
    caveat = Text("Amounts can be lower than actual: Claude Code doesn't log some requests (titles, suggestions…). "
                  "An exited session's TOTAL is complete.", style="dim", no_wrap=True, overflow="ellipsis")
    return Panel(Group(line, detail, caveat), title=Text("💲 usdash · live", style="bold"), title_align="left")


# --- Tables ------------------------------------------------------------------------


def column_widths(columns, rows: list[list[Text]]) -> tuple[list[list[str]], list[int]]:
    """Column names split into lines (a name can take two: "COST\nTODAY", lined
    up at the bottom), and each column's width: its widest name or cell."""
    names = [name.split("\n") for name, _ in columns]
    depth = max(len(parts) for parts in names)
    names = [[""] * (depth - len(parts)) + parts for parts in names]
    widths = [max(len(part) for part in parts) for parts in names]
    for cells in rows:
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], cell.cell_len)
    return names, widths


def grid_line(columns, widths: list[int], cells: list[Text], trailing: Text | None = None,
              overflow: str = "ellipsis") -> Text:
    """One line of cells, each padded to its column (numbers to the right); a
    trailing text runs on after the last column. The panel cuts what doesn't fit."""
    line = Text(no_wrap=True, overflow=overflow)
    for i, cell in enumerate(cells):
        pad = " " * (widths[i] - cell.cell_len)
        line.append_text(Text(pad) + cell if columns[i][1] else cell + Text(pad))
        if i < len(cells) - 1 or trailing:
            line.append(GAP)
    if trailing:
        line.append_text(trailing)
    line.rstrip()
    return line


def header_lines(columns, names: list[list[str]], widths: list[int], trailing: str | None = None) -> list[Text]:
    """The column names. A cut header is cropped, not ended with "…" (its top
    line would be a lone "…")."""
    depth = len(names[0])
    return [grid_line(columns, widths, [Text(parts[level], style="dim") for parts in names],
                      Text(trailing, style="dim") if trailing and level == depth - 1 else None, overflow="crop")
            for level in range(depth)]


# --- Sessions ----------------------------------------------------------------------

# Which session first, then its state and its numbers: a narrow terminal cuts from the right.
SESSION_COLUMNS = (("ID", False), ("SESSION", False), ("PROJECT", False), ("WHERE", False), ("MODEL", False),
                   ("CACHE", False), ("CONTEXT", True), ("TODAY", True), ("TOTAL", True))


# The panes, top to bottom, and what their titles say about their sessions. An expired
# session may still be open in its window, or have been killed without exiting: either
# way, `claude --resume <id>` brings it back.
PANES = {"live": "cache warm", "expired": "cache ran out, not exited", "exited": "claude --resume <id>"}


@dataclass
class Entry:
    """One session (or a folder's exited script runs) and the lines under it."""
    pane: str  # a key of PANES
    cells: list[Text]
    below: list[Text] = field(default_factory=list)  # from the SESSION column on
    sessions: int = 1  # a folded row of script runs stands for several


def cache_cell(session: Session, view: View) -> Text:
    warm, left, _ = cache_clock(session, view.now)
    age = age_text(view.now - (session.last_activity or view.now))
    if session.ended:
        return Text(f"exited · {age}", style="dim")
    if warm:
        return Text(f"● {clock(left)}", style="green")
    if session.working(view.now):  # its next request will re-write it all
        return Text.assemble(("○ expired · ", "red"), ("working", "yellow"))
    if session.subagent_running(view.now):  # the same, once the subagent reports back
        return Text.assemble(("○ expired · ", "red"), ("subagent", "yellow"))
    return Text(f"○ expired · {age}", style="red")


def today_cell(cost: float) -> Text:
    return Text("—", style="dim") if abs(cost) < 1e-9 else Text(_money(cost))


def session_cells(store: Store, session: Session, view: View) -> list[Text]:
    name = Text(snippet(session.name, 26) or "", style="bold")
    ctx = context(store, session)
    size = Text(("" if ctx is None or ctx.exact else "≈") + _tokens(ctx.tokens if ctx else None))
    return [session_tag(session), name, Text(snippet(session.project, 18) or "", style="dim"),
            Text(app_name(session), style="dim"), model_text(session.model, session.effort, session.main.speed),
            cache_cell(session, view), size,
            today_cell(session.cost_by_day.get(day_of(view.now), 0.0)), Text(_money(session.total))]


def prompt_text(session: Session, view: View) -> Text:
    """How long ago you last typed in that window, and what."""
    line = Text(f"{_ago(view.now - session.last_prompt_at)} · " if session.last_prompt_at else "", style="dim")
    line.append(f"\"{snippet(session.last_prompt, 200)}\"" if session.last_prompt else "no prompt yet", style="italic")
    return line


def prices_text(verb: str, ctx, prices: list[tuple[str, float, bool]], own: str | None,
                cached: bool = False, best: str | None = None) -> Text:
    """'next message re-sends 632k tokens: $12.63 on Fable 5.1, $0.13 on Opus
    5.5 (cached) ✅, …': the same line in every pane, the
    session's own model in bold, ✅ on `best`."""
    line = Text(f"{verb} {resends(ctx)}")
    for i, (model, cost, exact) in enumerate(prices):
        mine = model_key(model) == model_key(own)
        line.append(": " if i == 0 else ", ")
        line.append(f"{'' if exact else '≈'}{_money(cost)} on ")
        line.append(pretty_model(model), style=" ".join(filter(None, [model_style(model), "bold" if mine else ""])))
        if mine and cached:
            line.append(" (cached)")
        if model == best:
            line.append(" ✅")
    return line


def live_lines(session: Session, view: View, advice: Advice) -> list[Text]:
    """Under a live session: your last prompt, the ⚡ warning if it applies,
    and what the next message costs on each model."""
    lines = [prompt_text(session, view)]
    if advice.warning:
        lines.append(Text.assemble("⚡ ", (advice.warning, "bold")))
    if advice.prices:
        lines.append(prices_text("next message", advice.ctx, advice.prices, session.model, cached=True,
                                 best=advice.cheapest))
    return lines


def comeback_lines(store: Store, session: Session, view: View) -> list[Text]:
    """Under an expired or exited session: what coming back to it costs on each model (≈: engine.cold_resend)."""
    found = comeback(store, session, view.now)
    if found is None:
        return []
    ctx, costs = found
    verb = "resuming" if session.ended else "continuing"
    return [prices_text(verb, ctx, [(m, cost, False) for m, cost in costs], session.model)]


def tree(lines: list[Text]) -> list[Text]:
    """├ before each line, └ before the last."""
    return [Text.assemble(("└ " if i == len(lines) - 1 else "├ ", "dim"), line) for i, line in enumerate(lines)]


def script_runs(runs: list[Session], view: View) -> Entry:
    """A folder's closed script runs (claude -p, SDKs) as one row: a loop of them would bury the sessions."""
    latest = max(runs, key=lambda s: s.last_activity or 0)
    today = sum(s.cost_by_day.get(day_of(view.now), 0.0) for s in runs)
    name = Text(plural(len(runs), "run"), style="bold")
    age = age_text(view.now - (latest.last_activity or view.now))
    return Entry("exited", [Text(""), name, Text(snippet(latest.project, 18) or "", style="dim"),
                         Text(app_name(latest), style="dim"), model_text(latest.model),
                         Text(f"exited · {age}", style="dim"), Text(""),
                         today_cell(today), Text(_money(sum(s.total for s in runs)))], sessions=len(runs))


def session_entries(store: Store, view: View) -> list[Entry]:
    """Live sessions, then expired ones, then exited ones, each newest first
    (visible_sessions' order; the folded script runs go in by their latest)."""
    panes: dict[str, list[tuple[float, Entry]]] = {pane: [] for pane in PANES}
    runs = defaultdict(list)
    for session in visible_sessions(store, view):
        advice = advise(store, session, view.now)
        cells, when = session_cells(store, session, view), session.last_activity or 0
        if advice is not None:
            panes["live"].append((when, Entry("live", cells, tree(live_lines(session, view, advice)))))
        elif session.scripted and session.ended:
            runs[(session.cwd, session.entrypoint)].append(session)
        else:
            pane = "exited" if session.ended else "expired"
            panes[pane].append((when, Entry(pane, cells, tree(comeback_lines(store, session, view)))))
    panes["exited"] += [(max(s.last_activity or 0 for s in group), script_runs(group, view)) for group in runs.values()]
    panes["exited"].sort(key=lambda item: -item[0])
    return [entry for pane in PANES for _, entry in panes[pane]]


def entry_lines(columns, widths: list[int], entry: Entry) -> list[Text]:
    lines = [grid_line(columns, widths, entry.cells)]
    for below in entry.below:
        line = Text(" " * (widths[0] + len(GAP)), no_wrap=True, overflow="ellipsis")
        line.append_text(below)
        lines.append(line)
    return lines


def page_from(entries: list[Entry], blocks: list[list[Text]], start: int, room: int,
              head: int) -> tuple[list[tuple[str, list[Text]]], int]:
    """The sessions from `start` on that fit in `room` lines, split into their
    panes: each pane costs `head` lines (its frame and the column names) plus
    its sessions, with a dim line between two sessions. Also how many sessions
    that is: at least one (a live block taller than the room is cut, not
    skipped)."""
    panes: list[tuple[str, list[Text]]] = []
    used = shown = 0
    for entry, block in zip(entries[start:], blocks[start:]):
        new = not panes or panes[-1][0] != entry.pane
        cost = len(block) + (head if new else 1)
        if shown and used + cost > room:
            break
        if new:
            panes.append((entry.pane, []))
        else:
            panes[-1][1].append(Text("─" * 500, style="dim", no_wrap=True, overflow="crop"))  # cut at the pane's edge
        panes[-1][1].extend(block)
        used, shown = used + cost, shown + 1
    return panes, shown


def sessions_view(store: Store, view: View, rows: int) -> Layout | Panel:
    """The sessions in their panes (PANES), scrolled together by whole session
    (a live one's lines never split); the columns line up across all of them."""
    window = duration_text(view.window)
    entries = session_entries(store, view)
    keys = Text("↑↓/wheel/j/k: scroll · r: every request · q: quit", style="dim")
    if not entries:
        return Panel(Text(f"no Claude Code activity in the last {window}", style="dim"), subtitle=keys,
                     title=Text(f"sessions · last {window}", style="bold"), title_align="left", subtitle_align="right")
    names, widths = column_widths(SESSION_COLUMNS, [entry.cells for entry in entries])
    columns = header_lines(SESSION_COLUMNS, names, widths)
    blocks = [entry_lines(SESSION_COLUMNS, widths, entry) for entry in entries]
    head = PANE_FRAME + len(columns)
    # The furthest the list can scroll: the first session from which all the rest fit.
    last = len(entries) - 1
    while last > 0 and page_from(entries, blocks, last - 1, rows, head)[1] == len(entries) - last + 1:
        last -= 1
    view.session_last = last
    view.session_scroll = max(0, min(view.session_scroll, last))
    panes, shown = page_from(entries, blocks, view.session_scroll, rows, head)
    view.session_page = shown
    counts: Counter = Counter()
    for entry in entries:
        counts[entry.pane] += entry.sessions
    parts = []
    for i, (key, lines) in enumerate(panes):
        final = i == len(panes) - 1
        title, subtitle = f"{key} · {plural(counts[key], 'session')}", None
        if final:  # what matters most first: a narrow terminal cuts the title from the right
            if shown < len(entries):
                title += f" · rows {view.session_scroll + 1}–{view.session_scroll + shown} of {len(entries)}"
            title += f" · last {window}"
            subtitle = Text("g: back to the top", style="bold yellow") if view.session_scroll else keys
        title += f" · {PANES[key]}"
        pane = Panel(Group(*columns, *lines), title=Text(title, style="bold"), title_align="left",
                     subtitle=subtitle, subtitle_align="right")
        parts.append(Layout(pane) if final else Layout(pane, size=head + len(lines)))  # the last takes what's left
    layout = Layout()
    layout.split_column(*parts)
    return layout


# --- Feed --------------------------------------------------------------------------

FEED_COLUMNS = (("TIME", False), ("ID", False), ("SESSION", False), ("MODEL", False), ("PROMPT", True),
                ("CACHED", True), ("OUT", True), ("COST", True))


def searches_text(n: int) -> str:
    return f"{n} web search" if n == 1 else f"{n} web searches"


def feed_row(store: Store, request: Request) -> tuple[list[Text], Text | None]:
    """(a cell for every column, the NOTE that runs on to the end of the line)."""
    session = store.sessions[request.session]
    prompt = request.prompt
    cached = request.usage.get("read", 0) / prompt if prompt else 0
    cells = [
        Text(datetime.fromtimestamp(request.start).strftime("%H:%M:%S"), style="dim"),  # the feed's order
        session_tag(session),
        Text(snippet(session.name, 24) or "", style=session_style(session.id)),
        model_text(request.model, request.effort, request.speed),
        Text(_tokens(prompt)),
        Text(f"{cached:.0%}", style="green" if cached >= 0.8 else "yellow" if cached >= 0.3 else "red"),
        Text(_tokens(request.usage.get("output", 0)), style="dim"),
        Text(_money(request.cost)),
    ]
    note = Text()
    if request.subagent:
        note.append("🤖 subagent ", style="dim")
    if searches := request.usage.get("searches", 0):
        note.append(f"🔍 {searches_text(searches)} (+{_money(searches * store.web_search)}) ", style="dim")
    if searches := len(request.tool_searches):  # Claude Code's WebSearch tool: run in a request not logged
        note.append(f"🔍 {searches_text(searches)} (cost not logged) ", style="dim")
    if request.reason:
        note.append(f"⟳ re-wrote {_tokens(request.rewritten)}: {request.reason} (+{_money(request.rewrite_cost)})",
                    style="bold red")
    return cells, note if note.plain else None


def feed_day(request: Request) -> str:
    """The day a feed row's TIME is on."""
    return day_of(request.start)


def day_line(day: str, today: str) -> Text:
    """'── Sun 27 Sep ───…': above the first row of each day before today (TIME has no date)."""
    when = datetime.strptime(day, "%Y-%m-%d")
    label = when.strftime("%a %d %b" if day[:4] == today[:4] else "%a %d %b %Y").replace(" 0", " ")
    return Text(f"── {label} {'─' * 400}", style="dim", no_wrap=True, overflow="crop")


def feed_fits(store: Store, view: View, start: int) -> int:
    """How many feed rows from `start` fit in view.lines, with a day line before each
    row on another day than the one above it (the first row's: today)."""
    used, shown, above = 0, 0, day_of(view.now)
    for request in itertools.islice(store.feed, start, None):
        need = 1 + (feed_day(request) != above)
        if used + need > view.lines:
            break
        used, shown, above = used + need, shown + 1, feed_day(request)
    return shown


def feed_lines(rows: list[tuple[list[Text], Text | None]], days: list[str], today: str,
               above: str | None = None) -> list[Text]:
    """The feed's rows in columns sized to what's shown; a row's NOTE runs on to the end of the line.
    A day line goes above each row on another day than the one above it (the first row's: `above`,
    today unless told otherwise)."""
    names, widths = column_widths(FEED_COLUMNS, [cells for cells, _ in rows])
    lines = header_lines(FEED_COLUMNS, names, widths, "NOTE" if any(note for _, note in rows) else None)
    above = above or today
    for (cells, note), day in zip(rows, days):
        if day != above:
            lines.append(day_line(day, today))
        lines.append(grid_line(FEED_COLUMNS, widths, cells, note))
        above = day
    return lines


def feed_panel(store: Store, view: View, rows: int) -> Panel:
    view.lines = max(1, rows)
    scroll_to(store, view, view.scroll)
    fits = feed_fits(store, view, view.scroll)
    view.page = max(1, fits)
    shown = list(itertools.islice(store.feed, view.scroll, view.scroll + view.page))
    days = [feed_day(r) for r in shown]
    # Room for one line only: the row, not its day line.
    lines = feed_lines([feed_row(store, request) for request in shown], days, day_of(view.now),
                       above=days[0] if shown and not fits else None)
    total = len(store.feed)
    if view.scroll:
        title = f"requests · paused · rows {view.scroll + 1}–{view.scroll + len(shown)} of {total}"
        if view.unseen:
            title += f" · {view.unseen} new above"
        subtitle = Text("g: back to live", style="bold yellow")
    else:
        title = f"requests · {total}" if total > len(shown) else "requests"
        subtitle = Text("↑↓/wheel/j/k: scroll · space/b: page · g/G: newest/oldest · r: sessions · q: quit",
                        style="dim")
    return Panel(Group(*lines), title=Text(title, style="bold"), subtitle=subtitle, title_align="left",
                 subtitle_align="right")


# --- Scrolling ---------------------------------------------------------------------


def max_scroll(store: Store, view: View) -> int:
    """The furthest the feed can scroll: the first row from which all the rest fit,
    their day lines included."""
    feed, used, start = store.feed, 0, len(store.feed)
    while start > 0:
        request = feed[start - 1]
        # Putting this row on top: its own line, a day line under it if the row that was on top
        # starts another day, and one above it unless it's today (the one the old top had is dropped).
        below = feed_day(feed[start]) if start < len(feed) else None
        need = used + 1 + (below is not None and below != feed_day(request))
        top_line = feed_day(request) != day_of(view.now)
        if need + top_line > view.lines:
            break
        used, start = need, start - 1
    return max(0, start if start < len(feed) else len(feed) - 1)


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
    """`view` swaps sessions and requests. Otherwise scroll whichever is showing:
    up/down one (session or row), pgup/pgdn a page, home/end to either end."""
    if key == "view":
        view.mode = "requests" if view.mode == "sessions" else "sessions"
    elif view.mode == "requests":
        steps = {"up": -1, "down": 1, "pgup": -view.page, "pgdn": view.page}
        if key in steps:
            scroll_to(store, view, view.scroll + steps[key])
        elif key == "home":
            scroll_to(store, view, 0)
        elif key == "end":
            scroll_to(store, view, max_scroll(store, view))
    else:
        steps = {"up": -1, "down": 1, "pgup": -view.session_page, "pgdn": view.session_page}
        if key in steps:
            view.session_scroll = max(0, min(view.session_scroll + steps[key], view.session_last))
        elif key == "home":
            view.session_scroll = 0
        elif key == "end":
            view.session_scroll = view.session_last


# --- Whole screen ------------------------------------------------------------------


def render(store: Store, view: View, height: int) -> Layout:
    body = max(3, height - HEADER_HEIGHT)
    if view.mode == "requests":
        panel = feed_panel(store, view, max(1, body - 3))  # the panel's border and the column names
    else:
        panel = sessions_view(store, view, body)  # the panes, their frames included
    layout = Layout()
    layout.split_column(Layout(header(store, view), size=HEADER_HEIGHT), Layout(panel))
    return layout
