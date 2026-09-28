"""Draw the README's picture of the dashboard: made-up sessions, rendered by
usdash's own screen code, in colour.

    python3 scripts/readme_screen.py          # writes docs/dashboard.svg
    python3 scripts/readme_screen.py --text   # the same screen as plain text

Needs the test requirements (requirements-dev.txt): the sessions are built
with the tests' transcript builder.
"""
import io
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from conftest import PRICES, Transcript  # noqa: E402
from rich.console import Console  # noqa: E402

from usdash import ui  # noqa: E402
from usdash.sessions import Store  # noqa: E402

OUT = ROOT / "docs" / "dashboard.svg"
WIDTH, HEIGHT = 140, 23
T = datetime(2026, 9, 28, 14, 0).timestamp()  # a Monday, 14:00 local time


def grow(t: Transcript, start: float, prompts: tuple[int, ...], texts: tuple[str, ...], out: int, **reply) -> None:
    """A conversation whose prompt grows through `prompts`, one message every two minutes. Its
    first request reads Claude Code's tool list back, as a real one usually does on a 1-hour cache."""
    previous = 23_300
    for i, (size, text) in enumerate(zip(prompts, texts)):
        t.turn(start + 120 * i, text=text, read=previous, write=size - previous - 2, out=out, **reply)
        previous = size


def sessions() -> Store:
    store = Store(PRICES)
    # Live: a subscription session on Opus 5.5, big enough for /compact to be worth pricing.
    fix = Transcript(session="3f9a0c1e", cwd="/home/you/shop", branch="checkout-fix")
    fix.record("ai-title", aiTitle="Fix checkout rounding")
    grow(fix, T, (41_000, 60_000, 95_000, 140_000, 182_000),
         ("why is the total off by one cent", "show me where the rounding happens", "read the pricing module",
          "fix it and add a test", "run the suite and tell me what fails"), out=1_800)
    # Expired, still open: a VS Code session moved from Opus 5.5 to Sonnet 5 (a cache miss), now cold.
    notes = Transcript(session="b21e77d4", cwd="/home/you/shop", entrypoint="claude-vscode")
    notes.record("custom-title", customTitle="Release notes")
    notes.turn(T - 2 * 3600, text="draft the release notes", write=38_000, ttl="5m", out=1_200)
    notes.turn(T - 2 * 3600 + 120, text="shorter, please", model="claude-sonnet-5", write=40_500, ttl="5m", out=900)
    notes.turn(T - 2 * 3600 + 240, text="add a note about the migration", model="claude-sonnet-5", read=40_502,
               write=1_500, ttl="5m", out=700)
    # Exited: a long session from this morning, expensive to come back to.
    billing = Transcript(session="7c40d2aa", cwd="/home/you/billing")
    billing.record("ai-title", aiTitle="Migrate billing to v2")
    grow(billing, T - 5 * 3600, (42_000, 150_000, 290_000, 420_000),
         ("plan the migration", "move the invoice models", "port the webhooks", "run the backfill"), out=2_500)
    billing.record("cost-state", totalCostUSD=6.9)
    # Closed script runs in one folder: one row.
    runs = []
    for i in range(3):
        run = Transcript(session=f"9{i}e1b2c3", cwd="/home/you/shop", entrypoint="sdk-cli")
        run.turn(T - 3 * 3600 + 600 * i, text="summarize the CI log", write=24_000, ttl="5m", out=300)
        run.record("cost-state", totalCostUSD=0.14)
        runs.append(run)
    for transcript in (billing, *runs, notes, fix):  # each in its own order: closing records carry no time
        store.add_all(transcript.records)
    return store


def main() -> None:
    view = ui.View(now=T + 480 + 1380, subscription=True)
    console = Console(record=True, width=WIDTH, height=HEIGHT, color_system="truecolor", file=io.StringIO())
    console.print(ui.render(sessions(), view, HEIGHT))
    if "--text" in sys.argv:
        print(console.export_text().rstrip("\n"))
        return
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(console.export_svg(title="usdash"))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
