"""Draw the README's pictures of the dashboard: made-up sessions over five days,
rendered by usdash's own screen code, in colour.

    python3 scripts/readme_screen.py          # writes docs/dashboard.svg and docs/stats.svg
    python3 scripts/readme_screen.py --text   # the same screens as plain text

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

# (file, view, width, height)
SCREENS = (("dashboard.svg", "sessions", 140, 24), ("stats.svg", "stats", 160, 44))
DAY = 86400
T = datetime(2026, 9, 28, 14, 0).timestamp()  # a Monday, 14:00 local time


def grow(t: Transcript, start: float, prompts: tuple[int, ...], texts: tuple[str, ...], out: int, steps: int = 2,
         **reply) -> None:
    """A conversation whose prompt grows through `prompts`, one message every two minutes, each
    answered in `steps` tool calls. Its first request reads Claude Code's tool list back, as a
    real one usually does on a 1-hour cache."""
    previous = 23_300
    for i, (size, text) in enumerate(zip(prompts, texts)):
        at = start + 120 * i
        t.turn(at, text=text, read=previous, write=size - previous - 2, out=out, **reply)
        for step in range(steps):
            t.tool_result(at + 20 + 20 * step)
            t.reply(at + 30 + 20 * step, read=size + 1_502 * step, write=1_500, out=out // 4, **reply)
        previous = size + 1_502 * steps


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
    notes.turn(T - 2 * 3600 + 120, text="shorter, please", model="claude-sonnet-5-5", write=40_500, ttl="5m",
               out=900)
    notes.turn(T - 2 * 3600 + 240, text="add a note about the migration", model="claude-sonnet-5-5", read=40_502,
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
    for transcript in (*history(), billing, *runs, notes, fix):  # each in its own order: closing records carry no time
        store.add_all(transcript.records)
    return store


def history() -> list[Transcript]:
    """The four days before: exited sessions, each with a cache miss of its own kind."""
    # Thursday: back after lunch, the cache had expired.
    sdk = Transcript(session="71be09f2", cwd="/home/you/shop")
    sdk.record("ai-title", aiTitle="Upgrade the payments SDK")
    grow(sdk, T - 4 * DAY - 3 * 3600, (60_000, 110_000, 170_000), ("bump the SDK", "fix what breaks",
                                                                    "and the webhooks"), out=2_000)
    sdk.turn(T - 4 * DAY, text="where were we", write=172_000, out=1_500)
    sdk.user("check the other callers", T - 4 * DAY + 60)
    sdk.user("find the callers", T - 4 * DAY + 70, subagent="a1")
    sdk.reply(T - 4 * DAY + 90, subagent="a1", model="claude-haiku-4-5", effort=None, write=30_000, ttl="5m", out=800)
    sdk.reply(T - 4 * DAY + 120, read=172_002, write=6_000, out=1_200)
    sdk.record("cost-state", totalCostUSD=4.1)
    # Friday: effort turned down on Sonnet 5.5, which re-writes the conversation.
    login = Transcript(session="c4d2e8a1", cwd="/home/you/shop")
    login.record("ai-title", aiTitle="Flaky login test")
    login.turn(T - 3 * DAY, text="why does the login test flake", model="claude-sonnet-5-5", read=23_300,
               write=40_000, out=1_500)
    login.turn(T - 3 * DAY + 120, text="run it 50 times", model="claude-sonnet-5-5", read=63_302, write=8_000,
               out=900)
    login.turn(T - 3 * DAY + 300, text="just rerun the last command", model="claude-sonnet-5-5", effort="low",
               read=23_300, write=49_000, out=300)
    login.record("cost-state", totalCostUSD=0.9)
    # Saturday: resumed, and the fresh system prompt missed the cache.
    docs = Transcript(session="0a9e5f13", cwd="/home/you/billing")
    docs.record("ai-title", aiTitle="Docs pass")
    docs.turn(T - 2 * DAY, text="tidy the API docs", read=23_300, write=45_000, out=2_200)
    docs.record("cost-state", totalCostUSD=0.5)
    docs.turn(T - 2 * DAY + 600, text="and the changelog", read=23_300, write=47_000, out=1_000)
    docs.record("cost-state", totalCostUSD=0.95)
    # Sunday: fast mode turned off mid-session.
    infra = Transcript(session="e6c1a7b0", cwd="/home/you/infra")
    infra.record("ai-title", aiTitle="Review the Terraform plan")
    infra.turn(T - DAY, text="review this plan", read=23_300, write=60_000, out=3_000, speed="fast")
    infra.turn(T - DAY + 120, text="now the staging one", read=23_300, write=64_000, out=2_000, speed="standard")
    infra.record("cost-state", totalCostUSD=2.3)
    return [sdk, login, docs, infra]


def main() -> None:
    store = sessions()
    for name, mode, width, height in SCREENS:
        view = ui.View(now=T + 480 + 1380, subscription=True, mode=mode)
        console = Console(record=True, width=width, height=height, color_system="truecolor", file=io.StringIO())
        console.print(ui.render(store, view, height, width))
        if "--text" in sys.argv:
            print(console.export_text().rstrip("\n"))
            continue
        out = ROOT / "docs" / name
        out.parent.mkdir(exist_ok=True)
        out.write_text(console.export_svg(title="usdash"))
        print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
