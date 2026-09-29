"""
## Manual checklist

Checks against live Claude Code sessions, for what the automated tests can't
reach: what Claude Code really writes, and how the dashboard behaves while it
runs. Run them after a Claude Code update, after a change to usdash's screen or
prices, or if the header reports records of unknown types.

**What they cost:** real requests. On an API key, a full run costs a few
dollars (checks 18 and 30 cost the most); on a subscription, it uses plan
usage. The long-turn checks (23–27) take about 10 minutes each.

### How to run them (a person, or an agent with a terminal)

- **The dashboard:** run it in tmux, so its screen can be read and keys sent:
  `tmux new-session -d -s usdash -x 200 -y 60 usdash`, read it with
  `tmux capture-pane -p -t usdash`, and send keys with
  `tmux send-keys -t usdash s` (`s` switches to the stats and back).
  `COLUMNS=200 LINES=60 usdash --once` prints one screen of the sessions, and
  `usdash --once --stats` one of the stats.
- **Claude Code sessions:** one tmux session each, started in the folder the
  check names: `tmux new-session -d -s a -x 200 -y 50 -c <folder> claude`, then
  type with `tmux send-keys -t a '<message>' Enter` (slash commands the same
  way). Tell sessions apart on the dashboard by the ID column (the first 4
  characters of the session id, as in `/status`). The Claude Code session running
  these checks shows up on the dashboard too: ignore it.
- **Labels:** `[GUI]` needs the VS Code extension or the Desktop app;
  `[remote]` needs a second machine; `[record]` settles an open question: report
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
| 6 | `[GUI]` In the Desktop app, archive a session that usdash lists | Within about 10 seconds it leaves the sessions; its spend stays in the stats | An archived session is done with, not undone |
| 7 | Run `usdash --once`, then `usdash --window 30m --once` | First: the last pane's title says `last 5d`, and no session's CACHE age is over 5 days. Then: only sessions used in the last 30 minutes, and the last pane's title says `last 30m` (with no session at all: `no Claude Code activity in the last 30m`) | The default window is 5 days; `--window` changes it |

### Cache clock and what coming back costs

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 8 | Send a message in a session, then watch it | It's in the live pane. CACHE counts down from about `● 59:5x` on a subscription (`● 4:5x` on an API key), green, and starts over after each message. Its `└ next message re-sends …k tokens: $… now · up to $… once the cache expires` line has the same token count as CONTEXT | The lifetime comes from the session's own usage, counted from each request's start |
| 9 | In a warm Opus 5.5 session, read its `└ next message` line | The `now` amount ≈ CONTEXT × $0.20 per million; the `up to` amount ≈ CONTEXT × $8 per million on a 1-hour cache ($5 on a 5-minute one) | Warm, the conversation is read back; once the cache expires, it's written again |
| 10 | Leave a session idle past its cache lifetime. Note its `up to` amount and TODAY, then send a message | While idle: the expired pane, CACHE `○ expired · …`, and `└ continuing re-sends …k tokens: up to $…`. After the message: the header's `⟳ cache misses added` line names `cache expired`, the session is back in the live pane, and its TODAY has gone up by no more than the noted amount plus your message and the reply (less when Claude Code's tool list was still cached) | Once the cache has expired, the next message writes the conversation again: `up to` is its upper bound |
| 11 | `/exit` a session | It moves to the exited pane with CACHE `exited · ● mm:ss` (its cache outlives it; `exited · …` once that runs out) and a `└ resuming re-sends …` line: `$… now · up to $…` while the cache lasts, `up to $…` after. TOTAL becomes Claude Code's own figure, usually a little higher than before | Claude Code writes its own total when a session exits, and it counts the requests its transcripts miss |
| 12 | `claude --resume` the session from check 11 and send a message | The same ID comes back in the live pane with a countdown, and TOTAL doesn't drop: Claude Code's figure plus the new requests | Resuming appends to the same transcript, and Claude Code's total carries across resumes |
| 13 | `/exit` a session with some context, note its `now` amount and TODAY, `claude --resume` it within a minute, and send a message | Before you resume: `$… now` ≈ CONTEXT × the cache read price. After: the header gains no `resumed` cause, and TODAY has gone up by about the noted amount plus your message and the reply. If the header shows `resumed $…` instead, report it: the resumed session's system prompt changed | A resumed session sends a fresh system prompt; while it's the same (same day, same CLAUDE.md), the cache is read back (6 of 6 resumes on Claude Code 2.1.283) |
| 14 | Read the header | First line: `TODAY $…` and the share of input read from cache. Then, in red, `⟳ cache misses added $…: <cause> $…, …` with the causes of today's misses, the biggest first (checks 10 and 15 make some; with none, there's no such line). Then `At current API list prices; your subscription isn't billed per token` on a subscription, `Estimated at current API list prices` on an API key, and nothing in yellow. Last: `Amounts can be lower than actual: Claude Code doesn't log some requests (titles, suggestions…). An exited session's TOTAL is complete.` | The dollars are list prices; on a subscription they're for comparison |

### Cache misses, the countdown, and /compact

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 15 | In a warm Opus 5.5 session with CONTEXT C, note its TODAY, then `/model sonnet` and send a message | The header's `cache misses added` names `model switch`, and Stats (`s`) counts one more under **cache misses**, `model switch`, with about C tokens re-written. TODAY has gone up by about C × $4 per million (Sonnet 5.5's 1-hour write) plus your message and the reply | Each model has its own cache |
| 16 | In a warm Opus 5.5 session, `/effort low` and send a message. Then do the same in a warm Sonnet 5.5 session | Opus 5.5: no new cause in the header. Sonnet 5.5: `effort change` appears | Claude Code keeps the cache across an effort change only on Opus 5.5 and Fable 5.1 |
| 17 | Start `CLAUDE_CODE_PROMPT_CACHE_TTL=5m claude`, send a message, and watch CACHE | Green down to `● 2:31`, **yellow** from `● 2:30`, together with the `up to` amount in the line under it. On a 1-hour cache, yellow from `● 10:00` | The countdown warns before the cache runs out, in any terminal |
| 18 | In a session with some context (say 50k tokens, e.g. after reading a few large files), `/compact` | Before you send anything: CONTEXT says `compacted`, and the line under it `└ compacted: the next message measures the new size`. After your next message: CONTEXT shows the new size, the line shows amounts again, and the header gains no cause (writing the summary isn't a miss) | Compaction replaces the history with a summary; its size is known only once a request sends it |

### Stats

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 19 | Press `s` | The title says `usdash · stats`. A summary (SPEND, per day, last 5 hours, read from cache, cache misses; requests, prompts, sessions, subagents), then by day, by model, where the money goes, cache misses, by context size, top sessions, by project and costliest prompts; two side by side from 160 columns wide. `s` again goes back to the sessions | The stats are one key away |
| 20 | Read the numbers | By day's last row (today) has the same SPEND as the header's TODAY. The rows of by day, by model, where the money goes, by context size and by project each add up to SPEND (to a cent or two of rounding), and the cache-misses rows to the summary's cache misses. A session active only today has the same SPEND in top sessions as its TODAY in the sessions view | Every panel splits the same requests |
| 21 | Ask a session for two subagents in parallel: one waits 30 seconds, the other 60 (in Bash, with `python3 -c 'import time; time.sleep(30)'`), and each then replies "done". Then press `s` | The summary's subagents share is above 0%, and costliest prompts lists that prompt with REQS counting the subagents' requests too | A prompt's cost is everything it set off |
| 22 | In a terminal 30 lines tall, press `s`, then `j`, then `s`, `j`, `s` | In the stats, the summary's title says `rows 1–…`; `j` moves one row of panels and shows `g: back to the top`. Each view comes back where you left it | The stats scroll by whole rows; each view keeps its place |

### Long turns and long subagents

These check the cache clock when one turn runs longer than the cache lifetime. On a subscription, start Claude Code with `CLAUDE_CODE_PROMPT_CACHE_TTL=5m claude` for the 5-minute cases (an API key uses 5 minutes anyway); subagents always use 5 minutes.

**Setup:** in a scratch folder, `mkdir -p .claude/skills && cp -r <usdash>/tests/sanity/skills/* .claude/skills/`, then start a fresh session there for each check. The skills wait with `python3 … time.sleep`, not `sleep`: Claude Code blocks a plain `sleep` in the foreground.

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 23 | 5-minute cache: `/slow-steps` (six 100-second waits, as separate foreground steps: about 10 minutes in all) | CACHE back to about `● 4:5x` after every step, and no new cause in the header or in Stats' cache misses | A turn is many requests; each step reads the cache and restarts its clock |
| 24 | 5-minute cache: `/slow-tool` (one 330-second foreground wait) | About 5 minutes into the sleep, the session moves to the expired pane with CACHE `○ expired · working`. Report the step after it: `cache expired` added to the header (and to Stats, with about the conversation's size re-written), or nothing new | The clock counts from the start of the step that ran the tool; nothing refreshes it while the tool runs. The lifetime is a minimum: a cache often outlasts it by a minute or two, so either is possible |
| 25 | 1-hour cache (plain `claude` on a subscription): `/slow-tool` | CACHE counts down from about `● 59:xx` and stays warm; no new cause | 5½ minutes is well inside a 1-hour cache |
| 26 | 5-minute cache: "Use a subagent to run `python3 -c 'import time; time.sleep(100)'` six times, as six separate foreground Bash calls, then report done" | The parent ends its turn (Claude Code runs the subagent in the background) and goes to `○ expired · subagent` while it waits. When the subagent returns, the header adds `cache expired`, and Stats' cache misses counts the parent's miss only (the subagent's steps kept its own cache) | A subagent refreshes its own cache, not the parent's; a parent that only waits sends no requests |
| 27 | 1-hour cache: repeat check 26 | The parent stays warm, and no new cause appears | The parent's 1-hour cache outlasts a 10-minute subagent |

### Prices, fast mode and web search

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 28 | Start `usdash --once` with the network off, then `usdash --once --offline` with it on | The header's prices line (the one before the last) says `API list prices of <date> (couldn't refresh them)`, then `(offline)`: the date of the last prices read. Every amount is the same as with the network on | It falls back to the last prices it read, and says how fresh they are |
| 29 | Start `usdash --once` with the network on, then read `~/.cache/usdash/docs.json` | The header says `current API list prices`. The file has today's date under `pricing`, and nothing else but its format | The copy it falls back to is refreshed on every start |
| 30 | `[record]` In an Opus 5.5 session, turn `/fast` on and send a message. Then `/model`, pick Opus 5 (it has fast mode too), and send another | With fast on: MODEL says `Opus 5.5 … fast`, the header adds `speed change`, the `now` amount is about twice CONTEXT × $0.20 per million, and Stats' by model has an `Opus 5.5 … fast` row. After `/model`: report whether MODEL says `fast` for Opus 5 | Fast mode is priced from each reply's `usage.speed` |
| 31 | Ask Claude Code to search the web for something. Note the session's TOTAL, then `/exit` | Before `/exit`, TOTAL shows only the logged requests. After, TOTAL (now Claude Code's figure) is at least N × $0.01 above the noted one, N being the `searchCount` in the tool result's `toolUseResult` in the transcript | Claude Code's WebSearch tool searches in a request of its own that the transcripts don't log; Claude Code's own total counts it |

**Overall pass:** after `/exit`, each session's TOTAL is Claude Code's own figure. On an API key, an open session's TOTAL is a little below `/cost` in that session (Claude Code doesn't log some requests; the README's Limitations has the measured gap). Nothing in the header is yellow.
"""

if __name__ == "__main__":
    print(__doc__)
