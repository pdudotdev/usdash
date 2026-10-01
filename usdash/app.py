"""usdash: a live terminal dashboard of your own Claude Code costs and prompt caches.

    usdash                  # sessions of the last 5 days, stats of the last 30, then live
    usdash --once           # print one screen and exit (no live view)

Read-only: it reads Claude Code's transcripts on this machine
($CLAUDE_CONFIG_DIR/projects, else ~/.claude/projects), and nothing about
them leaves it. At start it reads Anthropic's pricing page, sending nothing
about you. `s` swaps the sessions and the stats.
Scroll with the arrow keys, the mouse wheel or j/k, a page with space/b, and
jump to the top with g and the bottom with G; q quits.
"""
import argparse
import importlib.metadata
import os
import queue
import select
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live

from .docs import fetch_text, read_docs
from .facts import Facts, load_facts
from .prices import load_pricing
from .sessions import Store, desktop_sessions, subscription_account
from .transcripts import Tailer, default_projects_dir
from .ui import DEFAULT_PERIOD, DEFAULT_WINDOW, View, duration_text, press, render

POLL_SECONDS = 1.0
DESKTOP_SECONDS = 10.0  # how often to re-read the Desktop app's session titles
FRAME_SECONDS = 0.5  # at most two redraws a second: easy on a slow SSH link


# Key presses -> actions. Terminals send the mouse wheel in the full-screen
# view as arrow keys; the letter keys cover a laptop keyboard whose Page
# Up/Home/End the terminal keeps for itself.
KEYS = {
    "\x1b[A": "up", "\x1bOA": "up", "k": "up",
    "\x1b[B": "down", "\x1bOB": "down", "j": "down",
    "\x1b[5~": "pgup", "b": "pgup",
    "\x1b[6~": "pgdn", " ": "pgdn",
    "\x1b[H": "home", "\x1bOH": "home", "\x1b[1~": "home", "g": "home",
    "\x1b[F": "end", "\x1bOF": "end", "\x1b[4~": "end", "G": "end",
    "s": "stats",
    "q": "quit",
}


def parse_keys(data: str) -> list[str]:
    """Raw terminal input -> action names; anything else is skipped."""
    keys, i = [], 0
    ordered = sorted(KEYS, key=len, reverse=True)
    while i < len(data):
        match = next((seq for seq in ordered if data.startswith(seq, i)), None)
        if match:
            keys.append(KEYS[match])
            i += len(match)
        else:
            i += 1
    return keys


def read_keys(fd: int, keys: queue.Queue, stop: threading.Event) -> None:
    while not stop.is_set():
        if select.select([fd], [], [], 0.2)[0]:
            for key in parse_keys(os.read(fd, 1024).decode(errors="ignore")):
                keys.put(key)


def prices_label(as_of: str | None, offline: bool) -> str:
    """How the header names the prices: read from the pricing page now, or
    the last ones read, with their date and why they weren't refreshed."""
    if as_of is None:
        return "current API list prices"
    try:
        day = datetime.strptime(as_of, "%Y-%m-%d")
        as_of = f"{day:%b} {day.day}"
    except ValueError:
        pass
    why = "offline" if offline else "couldn't refresh them"
    return f"API list prices of {as_of} ({why})"


@dataclass
class Known:
    """What usdash goes by: the prices, the model facts, and how fresh they are."""
    prices: dict  # model family -> prices
    web_search: float  # USD per search
    facts: Facts
    label: str  # the header's name for the prices (prices_label)
    changed: list[str]  # Anthropic's pages that no longer read as expected


def knowledge(offline: bool | None = None) -> Known:
    """What Anthropic's docs say, read now, else their saved copies, else the
    shipped files. None reads nothing at all, not even the saved copies."""
    pricing, facts = load_pricing(), load_facts()
    known = Known(pricing.models, pricing.web_search, facts, prices_label(pricing.verified, offline=True), [])
    if offline is None:
        return known
    pages = read_docs(None if offline else fetch_text, shipped={"pricing": pricing.verified})
    page = pages["pricing"]
    if page.data:
        known.prices.update(page.data["models"])
        known.web_search = page.data["web_search"]
    fresh = page.data is not None and page.as_of is None
    known.label = prices_label(None if fresh else page.as_of or pricing.verified, offline)
    known.changed = [name for name, found in pages.items() if found.changed]
    return known


def start_of_today(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def history_start(now: float, window: int, period: int = 0) -> float:
    """Where to start reading: the start of the window or of the stats period,
    whichever is earlier, so every session the window lists (some maybe still
    warm) and every request the stats count is loaded; and midnight at the
    latest, for today's spend."""
    return min(start_of_today(now), now - window, now - period)


class App:
    def __init__(self, projects: Path, since: float, window: int, clock=None, known: Known | None = None,
                 period: int = DEFAULT_PERIOD) -> None:
        """`known`: what knowledge() returns; without it, the shipped files only."""
        known = known or knowledge()
        self.clock = clock or time.time
        self.tailer = Tailer(projects, since=since)
        self.store = Store(known.prices, known.facts, known.web_search)
        self.view = View(now=self.clock(), subscription=subscription_account(), window=window, period=period,
                         prices=known.label, docs_changed=known.changed)
        self.desktop_read = 0.0

    def poll(self) -> bool:
        """Take in whatever the transcripts gained; True if anything did."""
        changed = False
        for batch in self.tailer.batches():  # a session at a time: its records are let go before the next
            changed = bool(self.store.add_all(batch)) or changed
        oldest, history = self.tailer.oldest, self.store.history_from
        if oldest is not None and (history is None or oldest < history):
            self.store.history_from = oldest  # a transcript too old to read: the history reaches back past it
            self.store.revision += 1  # the stats' average a day changes with it
        now = self.clock()
        if now - self.desktop_read >= DESKTOP_SECONDS:
            self.store.apply_desktop(desktop_sessions())
            self.desktop_read = now
        self.view.unknown_types = sum(self.tailer.unknown_types.values())
        self.view.deleted = sum(1 for when in self.tailer.deleted.values() if when >= now - self.view.period)
        return changed

    def frame(self, height: int, width: int = 160):
        self.view.now = self.clock()
        return render(self.store, self.view, height, width)


def version() -> str:
    try:
        return importlib.metadata.version("usdash")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="usdash", description="Live dashboard of your Claude Code costs and prompt caches.")
    parser.add_argument("--once", action="store_true", help="print one screen and exit")
    parser.add_argument("--version", action="version", version=f"usdash {version()}")
    args = parser.parse_args(argv)

    now = time.time()
    since = history_start(now, DEFAULT_WINDOW, DEFAULT_PERIOD)
    projects = default_projects_dir().expanduser()
    app = App(projects, since, DEFAULT_WINDOW, known=knowledge(offline=False), period=DEFAULT_PERIOD)
    if not projects.is_dir():
        print(f"usdash: no Claude Code transcripts at {projects} (set CLAUDE_CONFIG_DIR if Claude Code keeps them "
              f"elsewhere)", file=sys.stderr)
    elif sys.stderr.isatty():  # weeks of history take a few seconds to read
        print(f"usdash: reading the last {duration_text(round(now - since))} of transcripts…", file=sys.stderr)
    app.poll()
    console = Console()
    if args.once:
        console.print(app.frame(console.size.height, console.size.width))
        return

    keys: queue.Queue = queue.Queue()
    stop = threading.Event()
    saved_tty = None
    if sys.stdin.isatty():
        import termios
        import tty

        # Read keys one at a time, unechoed; Ctrl+C still stops the dashboard.
        fd = sys.stdin.fileno()
        saved_tty = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        threading.Thread(target=read_keys, args=(fd, keys, stop), daemon=True).start()
    try:
        with Live(app.frame(console.size.height, console.size.width), screen=True, auto_refresh=False, console=console) as live:
            # Ask the terminal to send the mouse wheel as arrow keys (most do by default).
            console.file.write("\x1b[?1007h")
            last_poll = last_frame = 0.0
            shown_second = None
            pending = False  # something changed since the last frame
            while True:
                pressed = []
                try:
                    pressed.append(keys.get(timeout=FRAME_SECONDS / 2))
                    while not keys.empty():
                        pressed.append(keys.get())
                except queue.Empty:
                    pass
                if "quit" in pressed:
                    break
                pending = pending or bool(pressed)
                for key in pressed:
                    press(app.store, app.view, key)
                clock_now = time.time()
                if clock_now - last_poll >= POLL_SECONDS:
                    pending = app.poll() or pending
                    last_poll = clock_now
                # Countdowns tick once a second; otherwise redraw only on change.
                second = int(clock_now)
                if (pending or second != shown_second) and clock_now - last_frame >= FRAME_SECONDS:
                    live.update(app.frame(console.size.height, console.size.width), refresh=True)
                    last_frame, shown_second, pending = clock_now, second, False
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        console.file.write("\x1b[?1007l")
        if saved_tty is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved_tty)


if __name__ == "__main__":
    main()
