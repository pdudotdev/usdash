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
WIDTH, HEIGHT = 140, 28
T = datetime(2026, 9, 28, 14, 0).timestamp()  # a Monday, 14:00 local time


def sessions() -> Store:
    store = Store(PRICES)
    # A subscription session on Opus 5.5, still warm, with a subagent.
    fix = Transcript(session="3f9a0c1e", cwd="/home/you/shop", branch="checkout-fix")
    fix.record("ai-title", aiTitle="Fix checkout rounding")
    prompt = 0
    texts = ["why is the total off by one cent", "show me where the rounding happens", "fix it and add a test",
             "run the suite and tell me what fails"]
    for i, size in enumerate((41_000, 45_000, 49_000, 52_000)):
        fix.turn(T + 120 * i, text=texts[i], read=prompt, write=size - prompt - 2, out=1_800)
        prompt = size
    agent = Transcript(session="3f9a0c1e", cwd="/home/you/shop", branch="checkout-fix")
    agent.user("find every place totals are rounded", T + 250, subagent="a1")
    agent.reply(T + 262, write=14_000, ttl="5m", out=600, subagent="a1")
    agent.tool_result(T + 270, subagent="a1")
    agent.reply(T + 281, read=14_002, write=2_000, ttl="5m", out=400, subagent="a1")
    # An API-key session in VS Code: switched from Opus to Sonnet, now cold.
    notes = Transcript(session="b21e77d4", cwd="/home/you/shop", entrypoint="claude-vscode")
    notes.record("custom-title", customTitle="Release notes")
    notes.turn(T + 30, text="draft the release notes", write=38_000, ttl="5m", out=1_200)
    notes.turn(T + 200, text="shorter, please", model="claude-sonnet-5", write=40_500, ttl="5m", out=900)
    notes.turn(T + 320, text="add a note about the database migration", model="claude-sonnet-5", read=40_502,
               write=1_500, ttl="5m", out=700)
    store.add_all(sorted(fix.records + agent.records + notes.records, key=lambda r: r.when or 0))
    return store


def main() -> None:
    view = ui.View(now=T + 360 + 1500, subscription=True, prices_verified="2026-09-26")
    console = Console(record=True, width=WIDTH, height=HEIGHT, color_system="truecolor", file=io.StringIO())
    console.print(ui.render(sessions(), view, HEIGHT, WIDTH))
    if "--text" in sys.argv:
        print(console.export_text().rstrip("\n"))
        return
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(console.export_svg(title="usdash"))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
