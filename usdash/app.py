"""usdash: a live terminal dashboard of your own Claude Code costs and prompt caches.

    usdash                  # the last 24 hours of sessions, then live
    usdash --since 2d       # load more history first
    usdash --window 8h      # show sessions active this recently (default 24h)
    usdash --once           # print one screen and exit (no live view)
    usdash --offline        # don't read Anthropic's docs; use the last ones read

Read-only: it reads Claude Code's transcripts on this machine, and nothing
about them leaves it. At start it reads three pages of Anthropic's docs
(prices, the current models, effort), sending nothing about you, unless
--offline. Scroll the request feed with the arrow keys, the mouse wheel or
j/k, a page with space/b, jump to the newest with g and the oldest with G;
q quits.
"""
import argparse
import os
import queue
import re
import select
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live

from .docs import fetch_text, read_docs
from .facts import Facts, load_facts
from .prices import load_prices, prices_verified
from .sessions import Store, desktop_sessions, subscription_account
from .transcripts import Tailer, default_projects_dir
from .ui import View, press, render, track_feed

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
    "r": "view",
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


def duration(text: str) -> int:
    """'30m' / '2h' / '1d' -> seconds; anything else is an error, not a guess."""
    match = re.fullmatch(r"(\d+)([mhd])", text.strip())
    if not match or int(match.group(1)) == 0:
        raise argparse.ArgumentTypeError(f"{text!r}: use minutes, hours or days, e.g. 30m, 2h or 1d")
    return int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400}[match.group(2)]


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


def knowledge(offline: bool) -> tuple[dict, Facts, str, list[str]]:
    """What Anthropic's docs say, read now (or their saved copies, or the
    shipped files): (prices, model facts, the header's name for the prices,
    the pages that no longer read as expected)."""
    facts, prices = load_facts(), load_prices()
    shipped = {"pricing": prices_verified(), "models": facts.verified, "effort": facts.verified}
    pages = read_docs(None if offline else fetch_text, shipped=shipped)
    pricing, effort = pages["pricing"], pages["effort"].data
    if pricing.data:
        prices.update(pricing.data)
    facts = facts.with_docs(pages["models"].data, set(effort) if effort is not None else None)
    fresh = pricing.data is not None and pricing.as_of is None
    label = prices_label(None if fresh else pricing.as_of or shipped["pricing"], offline)
    return prices, facts, label, [name for name, page in pages.items() if page.changed]


def start_of_today(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def history_start(now: float, since: int | None, window: int) -> float:
    """Where to start reading: --since ago if given, else midnight, but never
    later than the start of the window, so every session the window lists
    (some maybe still warm) is loaded."""
    start = now - since if since else start_of_today(now)
    return min(start, now - window)


class App:
    def __init__(self, projects: Path, since: float, window: int, clock=time.time,
                 known: tuple[dict, Facts, str, list[str]] | None = None) -> None:
        """`known`: what knowledge() returns; without it, the shipped files only."""
        prices, facts, label, changed = known or (load_prices(), load_facts(),
                                                  prices_label(prices_verified(), offline=True), [])
        self.clock = clock
        self.tailer = Tailer(projects, since=since)
        self.store = Store(prices, facts)
        self.view = View(now=clock(), subscription=subscription_account(), window=window, prices=label,
                         docs_changed=changed)
        self.desktop_read = 0.0

    def poll(self) -> bool:
        """Take in whatever the transcripts gained; True if anything did."""
        added = self.store.add_all(self.tailer.poll())
        now = self.clock()
        changed = bool(added)
        if now - self.desktop_read >= DESKTOP_SECONDS:
            self.store.apply_desktop(desktop_sessions())
            self.desktop_read = now
        self.view.unknown_types = sum(self.tailer.unknown_types.values())
        track_feed(self.store, self.view)
        return changed

    def frame(self, height: int):
        self.view.now = self.clock()
        return render(self.store, self.view, height)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="usdash", description="Live dashboard of your Claude Code costs and prompt caches.")
    parser.add_argument("--since", type=duration,
                        help="history to load first, e.g. 2d (default: --window, or since midnight if that's earlier)")
    parser.add_argument("--window", type=duration, default=24 * 3600,
                        help="show sessions active this recently, e.g. 3h or 2d (default: 24h)")
    parser.add_argument("--projects", type=Path, default=None,
                        help="Claude Code's transcripts folder (default: $CLAUDE_CONFIG_DIR/projects or ~/.claude/projects)")
    parser.add_argument("--once", action="store_true", help="print one screen and exit")
    parser.add_argument("--offline", action="store_true",
                        help="don't read Anthropic's docs at start; use the last prices and model facts read")
    args = parser.parse_args(argv)

    now = time.time()
    since = history_start(now, args.since, args.window)
    projects = (args.projects or default_projects_dir()).expanduser()
    app = App(projects, since, args.window, known=knowledge(args.offline))
    if not projects.is_dir():
        print(f"usdash: no Claude Code transcripts at {projects} (use --projects)", file=sys.stderr)
    app.poll()
    console = Console()
    if args.once:
        console.print(app.frame(console.size.height))
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
        with Live(app.frame(console.size.height), screen=True, auto_refresh=False, console=console) as live:
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
                    live.update(app.frame(console.size.height), refresh=True)
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
