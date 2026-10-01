"""The screen: a header, then either the sessions (the default) or the stats (`s`).

Sessions from the last 5 days, newest first, in three panes, in the order
you'd come back to them: live (cache warm), expired (still open), exited
(resumed with `claude --resume`). Under each, what its next message re-sends
and what that costs: read back now, or written again once the cache expires.
A live session also shows what you last typed. Finished script runs fold into
one row per folder.
"""
import io
import math
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .engine import cache_clock, context, resend_costs
from .fmt import clock, money, plural, tokens_text
from .models import model_key, pretty_model
from .sessions import Session, Store, day_of, snippet
from .stats import DAYS, EARLIER, Stats, compute, day_label

MODEL_STYLES = {"Opus": "bright_magenta", "Sonnet": "bright_blue", "Haiku": "bright_green", "Fable": "bright_yellow"}
# The VS Code extension also runs in its forks (Cursor, Windsurf, …): "IDE" is true for all of them.
APPS = {"cli": "CLI", "claude-vscode": "IDE", "claude-desktop": "Desktop"}
SESSION_STYLES = ["cyan", "yellow", "magenta", "green", "blue", "bright_cyan", "bright_yellow", "bright_magenta"]
DEFAULT_WINDOW = 5 * 86400  # sessions active this recently are listed
DEFAULT_PERIOD = 30 * 86400  # what the stats cover
PANE_FRAME = 2  # a pane's top and bottom border
GAP = "  "
EXPIRING = 600  # seconds: the countdown turns yellow for the last of these (at most half the lifetime)
# The share of input read back from the cache is green from the first, yellow from the second,
# red below: in the header, the stats' summary and by day alike.
CACHED_GOOD, CACHED_POOR = 0.9, 0.3


@dataclass
class View:
    """What the screen needs besides the data: the time, the account, which
    view is showing, and where each one is scrolled to."""
    now: float
    subscription: bool = False
    window: int = DEFAULT_WINDOW  # sessions idle longer than this are hidden
    period: int = DEFAULT_PERIOD  # what the stats cover
    prices: str = "current API list prices"  # which prices, and how fresh (app.prices_label)
    docs_changed: list[str] = field(default_factory=list)  # Anthropic's pages that no longer read as expected
    unknown_types: int = 0
    deleted: int = 0  # Desktop sessions deleted in the app within the period: what they cost is gone
    mode: str = "sessions"  # or "stats"
    session_scroll: int = 0  # sessions hidden above the view
    session_page: int = 1  # sessions shown at the last render
    session_last: int = 0  # the furthest the sessions could scroll at the last render
    session_shown: str = ""  # which sessions showed at the last render, if not all: '1–16 of 55'
    stats_scroll: int = 0  # rows of stats panels hidden above the view
    stats_page: int = 1  # rows shown at the last render
    stats_last: int = 0  # the furthest the stats could scroll at the last render


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


def cached_share(share: float) -> str:
    """A share of input read back from the cache: '86%', one decimal from 99% ('99.6%': rounded,
    it would read '100%' next to a day's misses), and '100%' only when nothing was written."""
    if share >= 1:
        return "100%"
    if share >= 0.99:
        return f"{math.floor(share * 1000) / 10:.1f}%"
    return f"{share:.0%}"


def cached_style(share: float) -> str:
    """The colour of a share of input read back from the cache, the same wherever it shows."""
    return "green" if share >= CACHED_GOOD else "yellow" if share >= CACHED_POOR else "red"


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
    """Sessions active within the window, most recent first (archived Desktop sessions too: their pane says so)."""
    shown = [
        s for s in store.sessions.values()
        if s.last_activity and view.now - s.last_activity <= view.window and s.last_request
    ]
    return sorted(shown, key=lambda s: -(s.last_activity or 0))


# --- Header ------------------------------------------------------------------------


def top_lines(store: Store, view: View) -> list[Text]:
    """The header's lines, each cut at the terminal's edge: today's spend, what
    cache misses added (only when there are any), the prices, the caveat."""
    today = day_of(view.now)
    day = store.days.get(today, {})
    spent = day.get("cost", 0.0)
    hit = day.get("read", 0) / day["prompt"] if day.get("prompt") else None
    rewrites = {reason: cost for reason, cost in store.rewrites.get(today, {}).items() if cost >= 0.005}
    line = Text.assemble(("TODAY ", "bold"), (_money(spent), "bold green"), no_wrap=True, overflow="ellipsis")
    if hit is not None:
        line.append(f"  ·  {cached_share(hit)} of input read from cache", style=cached_style(hit))
    lines = [line]
    if rewrites:
        # What requests paid to write the conversation again instead of reading it back (Stats has them by cause):
        # a line of its own, the biggest cause first, so a long list of causes cuts only the smallest.
        total = sum(rewrites.values())
        parts = ", ".join(f"{reason} {_money(cost)}" for reason, cost in sorted(rewrites.items(), key=lambda i: -i[1]))
        lines.append(Text(f"⟳ cache misses added {_money(total)}: {parts}", style="red", no_wrap=True,
                          overflow="ellipsis"))
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
        detail.append(f"  ·  {plural(view.unknown_types, 'record')} of unknown types (newer Claude Code?)",
                      style="yellow")
    if view.deleted:
        detail.append(f"  ·  {plural(view.deleted, 'Desktop session')} deleted in the last {duration_text(view.period)}: "
                      f"{'its' if view.deleted == 1 else 'their'} cost isn't counted", style="yellow")
    # Claude Code bills requests its transcripts never log (titles, prompt suggestions, /compact's
    # summary); an exited session's TOTAL is Claude Code's own, which counts them.
    caveat = Text("Amounts can be lower than actual: Claude Code doesn't log some requests (titles, suggestions…). "
                  "An exited session's TOTAL is complete.", style="dim", no_wrap=True, overflow="ellipsis")
    return [*lines, detail, caveat]


def header(lines: list[Text], mode: str = "sessions", shown: str = "") -> Panel:
    """Titled with the view, and which of its sessions show when they don't all fit."""
    title = f"💲 usdash · {mode}" + (f" {shown}" if shown else "")
    return Panel(Group(*lines), title=Text(title, style="bold"), title_align="left")


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
# way, `claude --resume` brings it back. It takes a whole session id or a name, not the 4
# characters shown, so the hint is its picker.
PANES = {"live": "cache warm", "expired": "cache ran out, not exited", "exited": "claude --resume, then pick it"}


@dataclass
class Entry:
    """One session (or a folder's exited script runs) and the lines under it."""
    pane: str  # a key of PANES
    cells: list[Text]
    below: list[Text] = field(default_factory=list)  # from the SESSION column on
    sessions: int = 1  # a folded row of script runs stands for several


def expiring(left: int, ttl: int) -> bool:
    """The countdown's last stretch: 10 minutes of a 1-hour cache, half of a 5-minute one."""
    return left <= min(EXPIRING, ttl // 2)


def cache_cell(session: Session, view: View) -> Text:
    warm, left, ttl = cache_clock(session, view.now)
    age = age_text(view.now - (session.last_activity or view.now))
    style = "yellow" if expiring(left, ttl) else "green"
    if session.ended or session.archived:  # quit, or archived in the Desktop app
        word = "archived" if session.archived else "exited"
        if warm:  # its cache outlives it: resuming reads it back until it runs out
            return Text.assemble((f"{word} · ", "dim"), (f"● {clock(left)}", style))
        return Text(f"{word} · idle {age}", style="dim")
    if warm:
        return Text(f"● {clock(left)}", style=style)
    if session.working(view.now):  # its next request will re-write it all
        return Text.assemble(("○ expired · ", "red"), ("working", "yellow"))
    if session.subagent_running(view.now):  # the same, once the subagent reports back
        return Text.assemble(("○ expired · ", "red"), ("subagent", "yellow"))
    return Text(f"○ expired · idle {age}", style="red")


def today_cell(cost: float) -> Text:
    return Text("—", style="dim") if abs(cost) < 1e-9 else Text(_money(cost))


def session_cells(store: Store, session: Session, view: View) -> list[Text]:
    name = Text(snippet(session.name, 26) or "", style="bold")
    tokens = context(session)
    size = Text("compacted", style="dim") if tokens is None and session.main.compacted else Text(_tokens(tokens))
    return [session_tag(session), name, Text(snippet(session.project, 18) or "", style="dim"),
            Text(app_name(session), style="dim"), model_text(session.model, session.effort, session.main.speed),
            cache_cell(session, view), size,
            today_cell(session.cost_by_day.get(day_of(view.now), 0.0)), Text(_money(session.total))]


def prompt_text(session: Session, view: View) -> Text:
    """How long ago you last typed in that window, and what."""
    line = Text(f"{_ago(view.now - session.last_prompt_at)} · " if session.last_prompt_at else "", style="dim")
    line.append(f"\"{snippet(session.last_prompt, 200)}\"" if session.last_prompt else "no prompt yet", style="italic")
    return line


def resend_line(store: Store, session: Session, view: View) -> Text | None:
    """'next message re-sends 182k tokens: $0.04 now · up to $1.46 once the
    cache expires': the same shape in every pane (engine.resend_costs)."""
    if session.main.compacted:
        return Text("compacted: the next message measures the new size", style="dim")
    tokens = context(session)
    if tokens is None:
        return None
    warm, left, ttl = cache_clock(session, view.now)
    verb = "resuming" if session.ended or session.archived else "next message" if warm else "continuing"
    line = Text(f"{verb} re-sends {_tokens(tokens)} tokens")
    costs = resend_costs(store, session, view.now)
    if costs is None:
        return line  # no known price
    now, up_to = costs
    line.append(": ")
    if now is not None:
        line.append(_money(now), style="green")
        line.append(" now · up to ")
        line.append(_money(up_to), style="yellow" if expiring(left, ttl) else "")
        line.append(" once the cache expires")
    else:
        line.append("up to ")
        line.append(_money(up_to))
    return line


def tree(lines: list[Text]) -> list[Text]:
    """├ before each line, └ before the last."""
    return [Text.assemble(("└ " if i == len(lines) - 1 else "├ ", "dim"), line) for i, line in enumerate(lines)]


def script_runs(runs: list[Session], view: View) -> Entry:
    """A folder's closed script runs (claude -p, SDKs) as one row: a loop of them would bury the sessions."""
    latest = max(runs, key=lambda s: s.last_activity or 0)
    today = sum(s.cost_by_day.get(day_of(view.now), 0.0) for s in runs)
    name = Text(plural(len(runs), "run"), style="bold")
    age = age_text(view.now - (latest.last_activity or view.now))
    # Their model and effort, as any row shows them, when the runs share them; else how many there were.
    kinds = {(model_key(s.model), s.effort, s.main.speed) for s in runs}
    models = {family for family, _, _ in kinds}
    if len(kinds) == 1:
        model = model_text(latest.model, latest.effort, latest.main.speed)
    elif len(models) == 1:
        model = model_text(latest.model).append(" · mixed", style="dim")
    else:
        model = Text(plural(len(models), "model"), style="dim")
    return Entry("exited", [Text(""), name, Text(snippet(latest.project, 18) or "", style="dim"),
                         Text(app_name(latest), style="dim"), model,
                         Text(f"exited · {age}", style="dim"), Text(""),
                         today_cell(today), Text(_money(sum(s.total for s in runs)))], sessions=len(runs))


def session_entries(store: Store, view: View) -> list[Entry]:
    """Live sessions, then expired ones, then exited ones, each newest first
    (visible_sessions' order; the folded script runs go in by their latest)."""
    panes: dict[str, list[tuple[float, Entry]]] = {pane: [] for pane in PANES}
    runs = defaultdict(list)
    for session in visible_sessions(store, view):
        cells, when = session_cells(store, session, view), session.last_activity or 0
        resend = resend_line(store, session, view)
        if not (session.ended or session.archived) and cache_clock(session, view.now)[0]:
            below = [prompt_text(session, view), *([resend] if resend else [])]
            panes["live"].append((when, Entry("live", cells, tree(below))))
        elif session.scripted and session.ended:
            runs[(session.cwd, session.entrypoint)].append(session)
        else:
            pane = "exited" if session.ended or session.archived else "expired"
            panes[pane].append((when, Entry(pane, cells, tree([resend] if resend else []))))
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
    keys = Text("↑↓/wheel/j/k: scroll · s: stats · q: quit", style="dim")
    view.session_shown = ""
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
    if shown < len(entries):
        # Which sessions show, for the header's title: counted as the panes count them (a folded
        # row of script runs is each of its runs), so the panes' counts add up to its total.
        before = sum(entry.sessions for entry in entries[:view.session_scroll])
        showing = sum(entry.sessions for entry in entries[view.session_scroll:view.session_scroll + shown])
        view.session_shown = f"{before + 1:,}–{before + showing:,} of {sum(counts.values()):,}"
    parts = []
    for i, (key, lines) in enumerate(panes):
        final = i == len(panes) - 1
        title, subtitle = f"{key} · {plural(counts[key], 'session')}", None
        if final:  # what matters most first: a narrow terminal cuts the title from the right
            title += f" · last {window}"
            subtitle = Text("g: back to the top", style="bold yellow") if view.session_scroll else keys
        title += f" · {PANES[key]}"
        pane = Panel(Group(*columns, *lines), title=Text(title, style="bold"), title_align="left",
                     subtitle=subtitle, subtitle_align="right")
        parts.append(Layout(pane) if final else Layout(pane, size=head + len(lines)))  # the last takes what's left
    layout = Layout()
    layout.split_column(*parts)
    return layout


# --- Stats -------------------------------------------------------------------------

TWO_COLUMNS = 144  # from this terminal width, the stats panels go two by two
BAR = 16  # cells in a day's spend bar
EIGHTHS = " ▏▎▍▌▋▊▉"
_measure = Console(file=io.StringIO(), width=TWO_COLUMNS, color_system=None, legacy_windows=False)


def percent(value: float) -> str:
    """'43%'; a share too small to round to 1% but not zero, '<1%'."""
    return "<1%" if 0 < value < 0.005 else f"{value:.0%}"


def share(part: float, whole: float) -> str:
    return percent(part / whole) if whole else "—"


def cached_text(value: float | None) -> Text:
    if value is None:
        return Text("—", style="dim")
    return Text(cached_share(value), style=cached_style(value))


def bar(value: float, top: float) -> Text:
    """`value` as a bar BAR cells long at `top`, in eighths of a cell."""
    eighths = round(value / top * BAR * 8) if top > 0 else 0
    return Text("█" * (eighths // 8) + (EIGHTHS[eighths % 8] if eighths % 8 else ""), style="cyan")


def stats_table(*columns: tuple[str, bool] | tuple[str, bool, bool]) -> Table:
    """A table of the stats panels' shape: no lines, dim column names,
    numbers to the right, nothing wraps (a long text is cut with …).
    Columns marked to shrink (a name, a prompt: fill them with cut()) give up
    room first when the panel is narrow; the others keep every character."""
    table = Table(box=None, padding=(0, 1), pad_edge=False, show_edge=False, header_style="dim", expand=False)
    for name, right, *shrinks in columns:
        # rich takes room only from columns that may wrap; cut() keeps their text on one line.
        table.add_column(name, justify="right" if right else "left", no_wrap=not shrinks, overflow="ellipsis")
    return table


def cut(text: str | None, width: int, style: str = "") -> Text:
    """A shrinking column's text: at most `width` characters, and cut with … to fit a narrower column."""
    return Text(snippet(text, width) or "", style=style, no_wrap=True, overflow="ellipsis")


def stats_panel(title: str, body: RenderableType) -> Panel:
    return Panel(body, title=Text(title, style="bold"), title_align="left", padding=(0, 1))


def summary_lines(stats: Stats, width: int) -> list[Text]:
    """The summary's figures as parts joined by ' · ', a new line where the next part wouldn't fit."""
    cached = stats.cached
    first = [Text.assemble(("SPEND ", "bold"), (money(stats.spend), "bold green"))]
    if stats.per_day is not None:  # over the days the transcripts cover: since when, if they begin after the period
        since = f" since {day_label(stats.since, day_of(stats.now))}" if stats.since else ""
        first.append(Text(f"{money(stats.per_day)} a day{since}"))
    if stats.recent is not None:
        first.append(Text(f"last 5 hours {money(stats.recent)}"))
    if cached is not None:
        first.append(Text(f"{cached_share(cached)} read from cache", style=cached_style(cached)))
    if stats.causes:  # as the cache-misses panel lists them: a miss under half a cent is still one
        first.append(Text(f"cache misses {money(stats.misses)} ({share(stats.misses, stats.spend)})", style="red"))
    else:
        first.append(Text("no cache misses", style="green"))
    work = plural(stats.requests, "request")
    if stats.prompts:
        work += f" from {plural(stats.prompts, 'prompt')} ({stats.requests / stats.prompts:.1f} each)"
    second = [Text(f"{work} in {plural(stats.sessions, 'session')}")]
    if stats.subagents >= 0.005:
        second.append(Text(f"subagents {share(stats.subagents, stats.spend)} of spend"))
    if stats.logged is not None:  # exited sessions: how much of Claude Code's own total their transcripts held
        second.append(Text(f"transcripts hold {percent(stats.logged)} of what Claude Code counted"))
    lines = []
    for parts in (first, second):
        line = Text()
        for part in parts:
            if line.plain and line.cell_len + 3 + part.cell_len > width:
                lines.append(line)
                line = Text()
            if line.plain:
                line.append(" · ", style="dim")
            line.append_text(part)
        lines.append(line)
    return lines


def by_day(stats: Stats, today: str) -> Panel:
    table = stats_table(("DAY", False), ("SPEND", True), ("", False), ("REQS", True), ("CACHED", True),
                        ("MISSES", True))
    # The bars compare days: the "earlier" row, many days together, gets none and sets no scale.
    top = max((day.spend for day in stats.days if day.day != EARLIER), default=0.0)
    for day in stats.days:
        label = Text(day_label(day.day, today), style="dim" if day.day == EARLIER else "")
        if not day.requests:
            table.add_row(label, Text("—", style="dim"), Text(""), Text("0", style="dim"), cached_text(None),
                          Text("—", style="dim"))
            continue
        misses = Text(money(day.misses), style="red") if day.misses > 0 else Text("—", style="dim")
        table.add_row(label, money(day.spend), Text("") if day.day == EARLIER else bar(day.spend, top),
                      f"{day.requests:,}", cached_text(day.cached), misses)
    # Over a longer period, the days before the last DAYS are the one "earlier" row.
    return stats_panel(f"by day · last {DAYS} days" if stats.period > DAYS * 86400 else "by day", table)


def by_model(stats: Stats) -> Panel:
    table = stats_table(("MODEL", False), ("% REQS", True), ("SPEND", True), ("% SPEND", True))
    for row in stats.models:
        table.add_row(model_text(row.model, row.effort, "fast" if row.fast else None),
                      share(row.requests, stats.requests), money(row.spend), share(row.spend, stats.spend))
    return stats_panel("by model", table)


def money_kinds(stats: Stats) -> Panel:
    table = stats_table(("KIND", False), ("TOKENS", True), ("% TOKENS", True), ("SPEND", True), ("% SPEND", True))
    tokens = sum(kind.tokens for kind in stats.kinds)
    for kind in stats.kinds:
        table.add_row(kind.name, _tokens(kind.tokens), share(kind.tokens, tokens), money(kind.spend),
                      share(kind.spend, stats.spend))
    if stats.searches:
        table.add_row("web searches", f"{stats.searches:,}", "", money(stats.search_spend),
                      share(stats.search_spend, stats.spend))
    body: list[RenderableType] = [table]
    if (price := stats.input_price) is not None:
        body.append(Text(f"input averages {money(price)} per million tokens", style="dim"))
    return stats_panel("where the money goes", Group(*body))


def cache_misses(stats: Stats, period: str) -> Panel:
    if not stats.causes:
        return stats_panel("cache misses", Text(f"no cache misses in the last {period}", style="green"))
    table = stats_table(("CAUSE", False), ("MISSES", True), ("RE-WRITTEN", True), ("EXTRA", True))
    for cause in stats.causes:
        table.add_row(cause.cause, f"{cause.misses:,}", _tokens(cause.rewritten), Text(money(cause.extra), style="red"))
    return stats_panel(f"cache misses · {money(stats.misses)}", table)


def by_context(stats: Stats) -> Panel:
    table = stats_table(("CONTEXT", False), ("% REQS", True), ("SPEND", True), ("% SPEND", True))
    for band in stats.bands:
        table.add_row(band.label, share(band.requests, stats.requests), money(band.spend),
                      share(band.spend, stats.spend))
    return stats_panel("by context size", table)


def top_sessions(stats: Stats) -> Panel:
    table = stats_table(("ID", False), ("SESSION", False, True), ("PROJECT", False, True), ("SPEND", True),
                        ("% SPEND", True), ("PEAK", True))
    for row in stats.top:
        session = row.session
        table.add_row(session_tag(session), cut(session.name, 24, "bold"), cut(session.project, 16, "dim"),
                      money(row.spend),
                      share(row.spend, stats.spend), _tokens(row.peak) if row.peak else Text("—", style="dim"))
    title = "top sessions"
    if stats.sessions > len(stats.top):
        title += f" · top {len(stats.top)} = {percent(stats.top_share)} of spend"
    return stats_panel(title, table)


def by_project(stats: Stats) -> Panel:
    table = stats_table(("PROJECT", False, True), ("SESSIONS", True), ("SPEND", True), ("% SPEND", True))
    for project in stats.projects:
        name = cut(project.name, 24, "dim" if project.name == "others" else "")
        table.add_row(name, f"{project.sessions:,}", money(project.spend), share(project.spend, stats.spend))
    return stats_panel("by project", table)


def costliest_prompts(stats: Stats) -> Panel:
    table = stats_table(("ID", False), ("PROMPT", False, True), ("REQS", True), ("SPEND", True))
    for turn in stats.turns:
        table.add_row(session_tag(turn.session), cut(turn.text, 36, "italic"),
                      f"{turn.requests:,}", money(turn.spend))
    return stats_panel("costliest prompts", table)


def height(renderable: RenderableType, width: int) -> int:
    return len(_measure.render_lines(renderable, _measure.options.update(width=width), pad=False))


def stats_rows(stats: Stats, view: View, width: int) -> list[tuple[RenderableType, int]]:
    """The panels after the summary, in their rows (two by two from TWO_COLUMNS
    wide), each row with its height in lines. They tell the story in pairs: when
    and on what the money went, what drove it, where it went, what to act on."""
    period, today = duration_text(view.period), day_of(view.now)
    panels = [by_day(stats, today), money_kinds(stats),  # when, and on what
              by_model(stats), by_context(stats),  # the two things that set the price of a request
              by_project(stats), top_sessions(stats),  # where it went
              cache_misses(stats, period), costliest_prompts(stats)]  # what to act on
    if width < TWO_COLUMNS:
        return [(panel, height(panel, width)) for panel in panels]
    half = (width - 1) // 2
    rows = []
    for left, right in zip(panels[::2], panels[1::2]):
        tall = max(height(left, half), height(right, half))
        left.height = right.height = tall
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(width=1)
        grid.add_column(ratio=1)
        grid.add_row(left, "", right)
        rows.append((grid, tall))
    return rows


def stats_view(store: Store, view: View, width: int, rows: int) -> RenderableType:
    """The summary, then the panels in rows, scrolled by whole row."""
    stats = compute(store, view.now, view.period)
    period = duration_text(view.period)
    keys = Text("↑↓/wheel/j/k: scroll · s: sessions · q: quit", style="dim")
    if not stats.requests:
        return Panel(Text(f"no priced requests in the last {period}", style="dim"), subtitle=keys,
                     title=Text(f"stats · last {period}", style="bold"), title_align="left", subtitle_align="right")
    lines = summary_lines(stats, width - 4)  # the panel's border and padding
    room = rows - (len(lines) + PANE_FRAME)
    blocks = stats_rows(stats, view, width)
    last = len(blocks) - 1  # the furthest it can scroll: the first row from which all the rest fit
    while last > 0 and sum(tall for _, tall in blocks[last - 1:]) <= room:
        last -= 1
    view.stats_last = last
    view.stats_scroll = max(0, min(view.stats_scroll, last))
    shown, used = [], 0
    for block, tall in blocks[view.stats_scroll:]:
        if shown and used + tall > room:
            break
        shown.append(block)
        used += tall
    view.stats_page = len(shown)
    title = f"summary · last {period}"
    if len(shown) < len(blocks):
        title += f" · rows {view.stats_scroll + 1}–{view.stats_scroll + len(shown)} of {len(blocks)}"
    subtitle = Text("g: back to the top", style="bold yellow") if view.stats_scroll else keys
    summary = Panel(Group(*lines), title=Text(title, style="bold"), title_align="left", subtitle=subtitle,
                    subtitle_align="right", padding=(0, 1))
    return Group(summary, *shown)


# --- Scrolling ---------------------------------------------------------------------


def press(store: Store, view: View, key: str) -> None:
    """`stats` swaps sessions and stats. Otherwise scroll whichever is showing:
    up/down one (session or row of panels), pgup/pgdn a page, home/end to either end."""
    if key == "stats":
        view.mode = "stats" if view.mode == "sessions" else "sessions"
        return
    prefix = "stats" if view.mode == "stats" else "session"
    at, page, last = (getattr(view, f"{prefix}_{name}") for name in ("scroll", "page", "last"))
    steps = {"up": -1, "down": 1, "pgup": -page, "pgdn": page}
    if key in steps:
        at = max(0, min(at + steps[key], last))
    elif key == "home":
        at = 0
    elif key == "end":
        at = last
    setattr(view, f"{prefix}_scroll", at)


# --- Whole screen ------------------------------------------------------------------


def render(store: Store, view: View, height: int, width: int = 160) -> Layout:
    lines = top_lines(store, view)
    top = len(lines) + PANE_FRAME
    body = max(3, height - top)
    if view.mode == "stats":
        panel, shown = stats_view(store, view, width, body), ""
    else:
        panel, shown = sessions_view(store, view, body), view.session_shown  # the panes, their frames included
    layout = Layout()
    layout.split_column(Layout(header(lines, view.mode, shown), size=top), Layout(panel))
    return layout
