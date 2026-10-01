"""
## Manual checklist

Checks against live Claude Code sessions, for what the automated tests can't
reach: what Claude Code really writes, and how the dashboard behaves while it
runs. Run them after a Claude Code update, after a change to usdash's screen or
prices, or if the header reports records of unknown types.

**What they cost:** real requests. On an API key, a full run costs a few
dollars (checks 18, 30, 33 and 34 cost the most); on a subscription, it uses
plan usage, except fast mode, which draws on usage credits. The long-turn checks (23–27) take about 10 minutes each.

### How to run them (a person, or an agent with a terminal)

- **The dashboard:** run it in tmux, so its screen can be read and keys sent:
  `tmux new-session -d -s usdash -x 200 -y 60 usdash`, read it with
  `tmux capture-pane -p -t usdash`, and send keys with
  `tmux send-keys -t usdash s` (`s` switches to the stats and back).
  `COLUMNS=200 LINES=60 usdash --once` prints one screen of the sessions.
- **Claude Code sessions:** one tmux session each, started in the folder the
  check names: `tmux new-session -d -s a -x 200 -y 50 -c <folder> claude`, then
  type with `tmux send-keys -t a '<message>' Enter` (slash commands the same
  way). Tell sessions apart on the dashboard by the ID column (the first 4
  characters of the session id, as in `/status`). The Claude Code session running
  these checks shows up on the dashboard too: ignore it.
- **Labels:** `[GUI]` needs the VS Code extension or the Desktop app;
  `[remote]` needs a second machine; `[API key]` needs a Claude Console API key; `[record]` settles an open question: report
  what you see, there's no fail. Skip what you can't do, and say why.
- **Transcripts,** where a check asks for one: one file per session,
  `~/.claude/projects/<its folder's path, with / and spaces as ->/<session id>.jsonl`
  (`ls -t ~/.claude/projects/*/*.jsonl | head` lists the newest), one JSON record
  per line; a reply's token counts are in `message.usage`.
- **Transcript saving:** a terminal started from inside a Claude Code session
  inherits `CLAUDE_CODE_CHILD_SESSION`, and sessions started from it save no
  transcript (they show `⚠ Transcript saving is off`), so they never appear.
  Start the terminals for these checks fresh.
- **Report** each check as pass, fail (what you saw instead) or skipped (why).

**Prices to check amounts against** (per million tokens): Opus 5.5 reads its
cache at $0.20 and writes it at $5 (5-minute cache) or $8 (1-hour); Sonnet 5.5
reads at $0.20 and writes at $2.50 / $4; Haiku 4.5 reads at $0.10 and writes at
$1.25 / $2. A subscription's main conversation uses the 1-hour cache, an API key
the 5-minute one.

### Sessions and names

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 1 | In a git repo (say `shop`, on `main`), start two sessions, and a third in a worktree of it on another branch (`git worktree add ../shop-fix -b fix`). Send a different message in each | Three sessions in the live pane. PROJECT says `shop`, `shop`, `shop-fix@fix`, and the `├` line under each shows the message you sent there, so you can tell them apart without the ID | Rows must map to your windows, not to session ids |
| 2 | In one of them, `/rename parser work`. Then start a new session, run `/model` as its first command, and send a message | Within about 2 seconds the first one's SESSION becomes `parser work`. The new one is never named `/model`: it's named after your message until Claude Code's title arrives | A rename beats Claude Code's automatic title; a built-in command says nothing about the task (a skill or `/init` does) |
| 3 | `[GUI]` Start one session each in a terminal, the VS Code extension's chat panel (not `claude` in its terminal: that's `CLI`) and the Desktop app's Code tab (the Desktop one without choosing a folder), and send a message in each | All three appear within about 5 seconds. WHERE says `CLI`, `IDE` and `Desktop`; the Desktop one's PROJECT says `no folder`, and within about 10 seconds its SESSION shows its title in the app's sidebar | Every local surface writes to the same transcripts folder |
| 4 | In one folder, run `claude -p "say hi"` twice | One row for both in the exited pane: `2 runs`, WHERE `script`, CACHE `exited · …`, TOTAL the two runs' costs added | A loop of script runs mustn't bury the real sessions |
| 5 | `[remote]` Over VS Code Remote-SSH to a Linux machine, start a session from the extension there, and run `usdash` in that machine's terminal. Then do the same from the Desktop app's SSH connection to it | Both sessions appear there (the Desktop one under Claude Code's own title); your laptop's sessions don't, and neither remote session appears on the laptop | usdash shows the sessions of the machine it runs on; both run Claude Code on the remote machine |
| 6 | `[GUI]` In the Desktop app, archive a session that usdash lists | Within about 10 seconds it moves to the exited pane, CACHE `archived · …` (`archived · ● mm:ss` while its cache lasts), with its `└ resuming re-sends …` line; its spend stays in the stats | Archiving closes a session: done with, but what it cost and what coming back costs still count |
| 7 | Run `usdash --once`, then `usdash --help` | The first prints one screen and exits: the last pane's title says `last 5d`, and no session's CACHE age is over 5 days. `--help` lists only `--once` and `--version` | The sessions cover 5 days and the stats 30 (check 19 shows `summary · last 30d`); nothing else is set by flags |

### Cache clock and what coming back costs

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 8 | Send a message in a session, then watch it | It's in the live pane. CACHE counts down from about `● 59:5x` on a subscription (`● 4:5x` on an API key), green, and starts over after each message. Its `└ next message re-sends …k tokens: $… now · up to $… once the cache expires` line has the same token count as CONTEXT | The lifetime comes from the session's own usage, counted from each request's start |
| 9 | In a warm Opus 5.5 session, read its `└ next message` line | The `now` amount ≈ CONTEXT × $0.20 per million; the `up to` amount ≈ CONTEXT × $8 per million on a 1-hour cache ($5 on a 5-minute one) | Warm, the conversation is read back; once the cache expires, it's written again |
| 10 | Leave a session idle past its cache lifetime. Note its `up to` amount and TODAY, then send a message | While idle: the expired pane, CACHE `○ expired · …`, and `└ continuing re-sends …k tokens: up to $…` (a recap Claude Code writes meanwhile restarts the countdown, and one after the cache ran out brings the session back to the live pane: wait for the countdown to run out). After the message: the header's `⟳ cache misses added` line names `cache expired`, the session is back in the live pane, and its TODAY has gone up by no more than the noted amount plus your message and the reply (less when Claude Code's tool list was still cached) | Once the cache has expired, the next message writes the conversation again: `up to` is its upper bound |
| 11 | `/exit` a session | It moves to the exited pane with CACHE `exited · ● mm:ss` (its cache outlives it; `exited · …` once that runs out) and a `└ resuming re-sends …` line: `$… now · up to $…` while the cache lasts, `up to $…` after. TOTAL becomes Claude Code's own figure, usually a little higher than before. `python3 tests/sanity/manual.py ledger <id>` shows the gap per model | Claude Code writes its own total when a session exits, and it counts the requests its transcripts miss |
| 12 | `claude --resume` the session from check 11 and send a message | The same ID comes back in the live pane with a countdown, and TOTAL doesn't drop: Claude Code's figure plus the new requests | Resuming appends to the same transcript, and Claude Code's total carries across resumes |
| 13 | `/exit` a session with some context, note its `now` amount and TODAY, `claude --resume` it within a minute, and send a message | Before you resume: `$… now` ≈ CONTEXT × the cache read price. After: the header gains no `resumed` cause, and TODAY has gone up by about the noted amount plus your message and the reply. If the header shows `resumed $…` instead, report it, with anything that changed at the restart (an MCP server or plugin whose tools load up front, system prompt flags) | A resumed conversation keeps the system prompt it started with, so within the lifetime the cache is read back (8 of 10 real resumes on Claude Code 2.1.283; the other two read back only the tool list) |
| 14 | Read the header | First line: `TODAY $…` and the share of input read from cache. Then, in red, `⟳ cache misses added $…: <cause> $…, …` with the causes of today's misses, the biggest first (checks 10 and 15 make some; with none, there's no such line). Then `At current API list prices; your subscription isn't billed per token` on a subscription, `Estimated at current API list prices` on an API key, and nothing in yellow. Last: `Amounts can be lower than actual: Claude Code doesn't log some requests (titles, suggestions…). An exited session's TOTAL is complete.` | The dollars are list prices; on a subscription they're for comparison |

### Cache misses, the countdown, and /compact

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 15 | In a warm Opus 5.5 session with CONTEXT C, note its TODAY, then `/model sonnet` and send a message | The header's `cache misses added` names `model switch`, and Stats (`s`) counts one more under **cache misses**, `model switch`, with about C tokens re-written. TODAY has gone up by about C × Sonnet 5.5's write price ($4 per million on a 1-hour cache, $2.50 on a 5-minute one) plus your message and the reply | Each model has its own cache |
| 16 | In a warm Opus 5.5 session, `/effort low` and send a message. Then switch to Opus 5 (`/model claude-opus-5`), send a message, `/effort high` and send another | Opus 5.5: no new cause in the header. Opus 5: `effort change` appears, after the `model switch` from switching to it. Stats' by model has a row for each model and effort you used, each with only its own requests | With an API key or a subscription, Claude Code keeps the cache across an effort change on Opus 5.5, Sonnet 5.5 and Fable 5.1 only |
| 17 | Start `CLAUDE_CODE_PROMPT_CACHE_TTL=5m claude`, send a message, and watch CACHE | Green down to `● 2:31`, **yellow** from `● 2:30`, together with the `up to` amount in the line under it. On a 1-hour cache, yellow from `● 10:00`. If Claude Code writes its recap (about 3 minutes into the pause), the countdown first jumps back to about `● 4:55` | The countdown warns before the cache runs out, in any terminal. The recap re-sends the conversation, which restarts the clock |
| 18 | In a session with some context (say 50k tokens, e.g. after reading a few large files), `/compact` | Before you send anything: CONTEXT says `compacted`, and the line under it `└ compacted: the next message measures the new size`. After your next message: CONTEXT shows the new size, the line shows amounts again, and the header gains no cause (writing the summary isn't a miss) | Compaction replaces the history with a summary; its size is known only once a request sends it |

### Stats

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 19 | Press `s`, in a terminal 144 columns wide, then 143 | The title says `usdash · stats`, and the summary's `summary · last 30d`. A summary (SPEND, per day, last 5 hours, read from cache, cache misses; requests, prompts, sessions, subagents and, once a session has exited, `transcripts hold N% of what Claude Code counted`), then the panels in pairs: by day and where the money goes, by model and by context size, by project and top sessions, cache misses and costliest prompts. At 144 wide each pair is side by side; at 143, one panel a row, in the same order. `s` again goes back to the sessions | The stats are one key away, and read in the same order at any width |
| 20 | Read the numbers, with a month of Claude Code use behind you | By day is titled `last 5 days` and has 5 day rows, today last, plus an `earlier` row above them for the rest of the 30 days (none if nothing was spent then). Today's row has the same SPEND as the header's TODAY. The summary's per day is SPEND ÷ 30, or, when it says `since` a day, SPEND ÷ the days since that day began (the first day in your transcripts); `transcripts hold N%` is under 100%. The rows of by day, by model, where the money goes, by context size and by project each add up to SPEND (to a cent or two of rounding), and the cache-misses rows to the summary's cache misses. A session active only today has the same SPEND in top sessions as its TODAY in the sessions view | Every panel splits the same requests |
| 21 | Ask a session for two subagents in parallel: one waits 30 seconds, the other 60 (in Bash, with `python3 -c 'import time; time.sleep(30)'`), and each then replies "done". Then press `s` | The summary's subagents share is above 0%, and costliest prompts lists that prompt with REQS counting the subagents' requests too | A prompt's cost is everything it set off |
| 22 | In a terminal 30 lines tall, press `s`, then `j`, then `s`, `j`, `s` | In the stats, the summary's title says `rows 1–…`; `j` moves one row of panels and shows `g: back to the top`. In the sessions, if they don't all fit, the header's title says `sessions 1–… of N`, N being the panes' counts added up, and `j` moves it on. Each view comes back where you left it | The stats scroll by whole rows; each view keeps its place |

### Long turns and long subagents

These check the cache clock when one turn runs longer than the cache lifetime. On a subscription, start Claude Code with `CLAUDE_CODE_PROMPT_CACHE_TTL=5m claude` for the 5-minute cases (an API key uses 5 minutes anyway); subagents use 5 minutes unless `subagentPromptCacheTtl` sets otherwise.

**Setup:** in a scratch folder, `mkdir -p .claude/skills && cp -r <usdash>/tests/sanity/skills/* .claude/skills/`, then start a fresh session there for each check. The skills wait with `python3 … time.sleep`, not `sleep`: Claude Code blocks a plain `sleep` in the foreground.

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 23 | 5-minute cache: `/slow-steps` (six 100-second waits, as separate foreground steps: about 10 minutes in all) | CACHE back to about `● 4:5x` after every step, and no new cause in the header or in Stats' cache misses | A turn is many requests; each step reads the cache and restarts its clock |
| 24 | 5-minute cache: `/slow-tool` (one 330-second foreground wait) | About 5 minutes into the sleep, the session moves to the expired pane with CACHE `○ expired · working`. Report the step after it: `cache expired` added to the header (and to Stats, with about the conversation's size re-written), or nothing new | The clock counts from the start of the step that ran the tool; nothing refreshes it while the tool runs. The lifetime is a minimum (a cache was once read back half a minute past it), so either is possible |
| 25 | 1-hour cache (plain `claude` on a subscription): `/slow-tool` | CACHE counts down from about `● 59:xx` and stays warm; no new cause | 5½ minutes is well inside a 1-hour cache |
| 26 | 5-minute cache: "Use a subagent to run `python3 -c 'import time; time.sleep(100)'` six times, as six separate foreground Bash calls, then report done" | The parent ends its turn (Claude Code runs the subagent in the background) and goes to `○ expired · subagent` while it waits. When the subagent returns, the header adds `cache expired`, and Stats' cache misses counts the parent's miss only (the subagent's steps kept its own cache) | A subagent refreshes its own cache, not the parent's; a parent that only waits sends no requests |
| 27 | 1-hour cache: repeat check 26 | The parent stays warm, and no new cause appears | The parent's 1-hour cache outlasts a 10-minute subagent |

### Prices, fast mode and web search

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 28 | Start `usdash --once` with the network off | The header's prices line (the one before the last) says `API list prices of <date> (couldn't refresh them)`: the date of the last prices read. Every amount is the same as with the network on | It falls back to the last prices it read, and says how fresh they are |
| 29 | Start `usdash --once` with the network on, then read `~/.cache/usdash/docs.json` (under `$XDG_CACHE_HOME` if that's set) | The header says `current API list prices`. The file has today's date under `pricing`, and nothing else but its format | The copy it falls back to is refreshed on every start |
| 30 | In an Opus 5.5 session with some context, turn `/fast` on and send a message. Then turn it off and send another | With fast on: MODEL says `Opus 5.5 … fast`, the header adds `speed change`, the `now` amount is about twice CONTEXT × $0.20 per million, and Stats' by model has an `Opus 5.5 … fast` row. With it off again: MODEL drops `fast`, and no new cause | Fast mode is priced from each reply's `usage.speed`. Turning it on adds a header that's part of the cache key; Claude Code keeps sending it, so turning it off keeps the cache |
| 31 | Ask Claude Code to search the web for something. Note the session's TOTAL, then `/exit` | Before `/exit`, TOTAL shows only the logged requests. After, TOTAL (now Claude Code's figure) is at least N × $0.01 above the noted one, N being the `searchCount` in the tool result's `toolUseResult` in the transcript | Claude Code's WebSearch tool searches in a request of its own that the transcripts don't log; Claude Code's own total counts it |

### Amounts against Claude Code's own records

When a session exits, Claude Code writes its own cost for each model it used (`modelUsage`, in the transcript's last `cost-state` record). `python3 tests/sanity/manual.py ledger <session id, or its first characters>`, run from the usdash clone with its virtual environment active, prints each model's tokens and cost as the transcripts show them (usdash's figures, at the prices usdash last read) next to Claude Code's. Where the tokens are the same, the costs must be too, to the millionth of a dollar: `✓ same tokens, same cost`. A model whose tokens differ ran requests the transcripts don't log, such as Claude Code's own on Haiku 4.5. The checks use one run, in a scratch folder: `claude -p "Run date, then count from 1 to 100 in words, one per line" --allowedTools "Bash(date)"` (the prompt first: `--allowedTools` takes a list, and would swallow it). It exits at once, and its two requests price every kind of token: the second reads back what the first wrote, and writes a few hundred tokens of output. `ls -t ~/.claude/projects/*/*.jsonl | head -1` finds its transcript.

| # | Do this | Pass criteria (`ledger`) | Why |
|---|---|---|---|
| 32 | The run on your usual model, then `ledger` on its session. Then again with `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` in front, for the 5-minute cache's write price (or `1h`, on an API key) | Its model's row: `✓ same tokens, same cost`, both times. Any other model is counted by Claude Code only | The method the checks below rely on: Claude Code prices the same tokens as usdash does |
| 33 | The run with `--model claude-sonnet-5-5`, then `--model claude-opus-5`, then `--model claude-fable-5-1` (skip one your plan doesn't offer: on a subscription, Fable 5.1 needs usage credits; about $1 in all at list prices) | Each run's model: `✓ same tokens, same cost` | usdash prices every model from the pricing page; these are the ones your own use hasn't checked yet |
| 34 | Fast mode: the run with `--settings '{"fastMode": true}'` on Opus 5.5, then the same with `--model claude-opus-5` | Each row notes `speed fast` and says `✓ same tokens, same cost`. On a subscription without usage credits, Claude Code runs at standard speed without saying so: the rows then lack `speed fast`, and the check can't be done | Fast mode's input and output prices, with the cache multipliers on top of them |
| 35 | `[API key]` US-only inference: with an API key from a Console workspace whose default inference geo is US, the run on Opus 5.5 | The Opus 5.5 row notes `region us` and says `✓ same tokens, same cost`. `[record]` Whether Claude Code's own requests on Haiku 4.5, which can't run US-only, fail there | US-only inference costs 1.1× on every kind of token for Claude 4.6 and later; usdash reads it from each reply's `inference_geo` |
| 36 | Ask a session to search the web for today's date, `/exit`, then `ledger` | The searches show under Claude Code only, and that model's Claude Code cost is above the transcripts' by at least the searches × $0.01. `[record]` Whether a logged request carried searches of its own (Stats' where the money goes then has a `web searches` row) | Claude Code's WebSearch tool searches in a request of its own that the transcripts don't log |
| 37 | `[GUI]` In the Desktop app's Code tab, start a local session (your Mac, not the cloud: a cloud session leaves no transcript here), send a message or two, archive it (archiving closes it and writes Claude Code's total), then `ledger` on its session | Its model's row says `Claude Code counted more` (the app makes requests of its own on the same model) or `✓ same tokens, same cost`, never less. The dashboard shows it in the exited pane as `archived · …`, TOTAL Claude Code's own. `[record]` Whether Claude Code's total comes out $0.00 instead, with no models listed: one Desktop session here recorded $0.00 for $0.90 of Sonnet 5 requests, reopened days later without sending anything. Either way, the dashboard's TOTAL for it is at least what its transcripts show | Once a session exits, TOTAL shows Claude Code's own total, unless that falls short of the transcripts |
| 38 | `[GUI]` In the Desktop app's Code tab, start a local session, send a message, check it shows in the dashboard, then delete it (not archive) | `[record]` What's left in `~/.claude/projects`: on Claude Code 2.1.285, only `<id>.desktop-released.json` (`"reason": "delete"`, no costs). After a restart, the dashboard has neither the session nor its spend | Deleting takes the transcript, usdash's only record of what the session cost |
| 39 | Start a new session and have it read a few large files (say 50k tokens of context). Within a minute of the reply, `/compact`; when it's done, `/exit` straight away (a recap would add requests of its own), then `ledger` on it | On the session's model, `Claude Code counted more`, and the extra is mostly `/compact`'s own request: READ about what the last turn's first request had cached, the rest as INPUT, WRITTEN up by no more than a few hundred tokens, and OUTPUT up by the summary (1–4k tokens). `[record]` The numbers, next to the research guide's Appendix B ("What does a warm `/compact` request read?") | A warm `/compact` reads the conversation from the cache rather than writing it again, so compacting before the countdown runs out is cheaper |

**Overall pass:** after `/exit`, each session's TOTAL is Claude Code's own figure. On an API key, an open session's TOTAL is a little below `/cost` in that session (Claude Code doesn't log some requests; the README's Limitations has the measured gap). Nothing in the header is yellow.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def ledger(session: str, projects: Path | None = None) -> str:
    """For a session that exited: each model's tokens and cost as its transcripts show them
    (usdash's figures) next to Claude Code's own, the `modelUsage` of its last `cost-state`."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # this clone's usdash
    from usdash.app import knowledge
    from usdash.models import model_key
    from usdash.sessions import Store
    from usdash.transcripts import Tailer, default_projects_dir

    root = Path(projects or default_projects_dir()).expanduser()
    found = sorted(root.glob(f"*/{session}*.jsonl"))
    if len(found) != 1:
        return f"{session}: {len(found)} transcripts match in {root}"
    main = found[0]
    tailer = Tailer(root)
    # Only this session's files, read the way the dashboard reads them.
    tailer.transcripts = lambda: [main, *sorted((main.parent / main.stem / "subagents").glob("*.jsonl"))]
    known = knowledge(offline=True)  # the prices usdash last read, else those it ships with: no network
    store = Store(known.prices, known.facts, known.web_search)
    store.add_all(tailer.poll())
    s = store.sessions[main.stem]
    states = [json.loads(line) for line in main.open() if '"cost-state"' in line]
    states = [d for d in states if d.get("type") == "cost-state"]
    if not states:
        return f"{s.id[:8]}: no total of Claude Code's yet: /exit the session first"
    if not s.ended:
        return f"{s.id[:8]}: resumed since it last exited: /exit it again"

    def blank():
        return {"tokens": [0, 0, 0, 0], "searches": 0, "cost": 0.0, "unpriced": 0, "notes": set()}

    ours, theirs = defaultdict(blank), defaultdict(blank)
    for r in s.requests.values():
        row, u = ours[r.family], r.usage
        for i, value in enumerate((u["fresh"], u["output"], u["read"], u["write_5m"] + u["write_1h"])):
            row["tokens"][i] += value
        row["searches"] += u["searches"]
        if r.cost is None:
            row["unpriced"] += 1
        else:
            row["cost"] += r.cost
        row["notes"] |= {note for note, on in (("speed fast", r.speed == "fast"), ("region us", r.geo == "us")) if on}
    for name, use in (states[-1].get("modelUsage") or {}).items():
        row = theirs[model_key(name)]
        for i, key in enumerate(("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")):
            row["tokens"][i] += use.get(key) or 0
        row["searches"] += use.get("webSearchRequests") or 0
        row["cost"] += use.get("costUSD") or 0.0

    lines = [f"{s.id[:8]} ({s.project}): Claude Code's total ${states[-1].get('totalCostUSD') or 0:.4f}, "
             f"the transcripts' ${s.cost_at_state:.4f}", "",
             f"{'MODEL':20}{'':13}{'INPUT':>9}{'OUTPUT':>10}{'READ':>12}{'WRITTEN':>12}{'SEARCHES':>10}{'COST':>13}"]
    for family in sorted(set(ours) | set(theirs), key=str):
        a, b = ours.get(family), theirs.get(family)
        if a is None:
            verdict = "counted by Claude Code only: requests the transcripts don't log"
        elif b is None:
            verdict = "✗ not in Claude Code's record"
        elif a["unpriced"]:
            verdict = "✗ usdash has no price for it"
        elif a["tokens"] == b["tokens"]:
            verdict = "✓ same tokens, same cost" if abs(a["cost"] - b["cost"]) < 1e-6 else "✗ same tokens, different cost"
        elif all(x <= y for x, y in zip(a["tokens"], b["tokens"])):
            verdict = "Claude Code counted more: requests the transcripts don't log"
        else:
            verdict = "✗ the transcripts show more than Claude Code counted"
        for who, row, note in (("transcripts", a, " · ".join(sorted(a["notes"])) if a else ""), ("Claude Code", b, verdict)):
            row = row or blank()
            numbers = "".join(f"{value:>{width},}" for value, width in zip([*row["tokens"], row["searches"]], (9, 10, 12, 12, 10)))
            lines.append(f"{family if who == 'transcripts' else '':20}{who:13}{numbers}{'$' + format(row['cost'], '.6f'):>13}  {note}".rstrip())
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "ledger":
        print(ledger(sys.argv[2]))
    else:
        print(__doc__)
