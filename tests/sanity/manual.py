"""
## Manual checklist

Checks against live Claude Code sessions, for what the automated tests can't
reach: what Claude Code really writes, and how the dashboard behaves while it
runs. Run them after a Claude Code update, after a change to usdash's screen or
prices, or if the header reports records of unknown types.

**What they cost:** real requests. On an API key, a full run costs a few
dollars (checks 17 and 30 cost the most); on a subscription, it uses plan
usage. The long-turn checks (23–27) take about 10 minutes each.

### How to run them (a person, or an agent with a terminal)

- **The dashboard:** run it in tmux, so its screen can be read and keys sent:
  `tmux new-session -d -s usdash -x 200 -y 60 usdash`, read it with
  `tmux capture-pane -p -t usdash`, and send keys with
  `tmux send-keys -t usdash r` (`r` switches to the request list and back).
  `COLUMNS=200 LINES=60 usdash --once` prints one screen of the sessions.
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
- **Report** each check as pass, fail (what you saw instead) or skipped (why).

**Prices to check amounts against** (per million tokens): Opus 5.5 reads its
cache at $0.20 and writes it at $5 (5-minute cache) or $8 (1-hour); Sonnet 5
writes at $2.50 / $4; Haiku 4.5 at $1.25 / $2, and counts the same text as about
0.77× the tokens; Fable 5.1 at $12.50 / $20. A subscription's main conversation
uses the 1-hour cache, an API key the 5-minute one.

### Sessions and names

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 1 | In a git repo (say `shop`, on `main`), start two sessions, and a third in a worktree of it on another branch (`git worktree add ../shop-fix -b fix`). Send a different message in each | Three sessions in the live pane. PROJECT says `shop`, `shop`, `shop-fix@fix`, and the `├` line under each shows the message you sent there, so you can tell them apart without the ID | Rows must map to your windows, not to session ids |
| 2 | In one of them, `/rename parser work` | Within about 2 seconds its SESSION becomes `parser work` | A rename beats Claude Code's automatic title |
| 3 | `[GUI]` Start one session each in a terminal, the VS Code extension's chat panel (not `claude` in its terminal: that's `CLI`) and the Desktop app's Code tab (the Desktop one without choosing a folder), and send a message in each | All three appear within about 5 seconds. WHERE says `CLI`, `IDE` and `Desktop`; the Desktop one's PROJECT says `no folder`, and within about 10 seconds its SESSION shows its title in the app's sidebar | Every local surface writes to the same transcripts folder |
| 4 | In one folder, run `claude -p "say hi"` twice | One row for both in the exited pane: `2 runs`, WHERE `script`, CACHE `exited · …`, TOTAL the two runs' costs added | A loop of script runs mustn't bury the real sessions |
| 5 | `[remote]` Over VS Code Remote-SSH to a Linux machine, start a session from the extension there, and run `usdash` in that machine's terminal | That session appears; your laptop's sessions don't | usdash shows the sessions of the machine it runs on |
| 6 | `[GUI]` In the Desktop app, archive a session that usdash lists | Within about 10 seconds it leaves the sessions; its requests stay in the request list | An archived session is done with |
| 7 | Run `usdash --once`, then `usdash --window 30m --once` | First: the last pane's title says `last 5d`, and no session's CACHE age is over 5 days. Then: only sessions used in the last 30 minutes, and the last pane's title says `last 30m` (with no session at all: `no Claude Code activity in the last 30m`) | The default window is 5 days; `--window` changes it |

### Cache clock and prices

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 8 | Send a message in a session, then watch it | It's in the live pane. CACHE counts down from about `● 59:5x` on a subscription (`● 4:5x` on an API key) and starts over after each message. Its `└ next message re-sends …k tokens` line has the same token count as CONTEXT, and its own model is marked `(cached) ✅` | The lifetime comes from the session's own usage, counted from each request's start |
| 9 | In a warm Opus 5.5 session, read its `└ next message` line | Models in this order: Fable 5.1, Opus 5.5, Sonnet 5, Haiku 4.5. Opus 5.5's amount ≈ CONTEXT × $0.20 per million, without `≈`; Sonnet 5's `≈$…` ≈ CONTEXT × $4 per million on a 1-hour cache ($2.50 on a 5-minute one) | Its own model reads the conversation back; any other writes all of it into its own cache (`≈`: it may have the tool list cached) |
| 10 | Leave a session idle past its cache lifetime. Note the amount on its own model in its line, then send a message | While idle: the expired pane, CACHE `○ expired · …`, and `└ continuing re-sends …k tokens: ≈$… on Fable 5.1, …` with every model. After the message: the request list shows `⟳ re-wrote …: cache expired (idle … min)`, that row's COST is about the noted amount plus your message and the reply, and the session is back in the live pane | Once the cache has expired, the next message writes the whole conversation again, on any model |
| 11 | `/exit` a session | It moves to the exited pane with CACHE `exited · …` and a `└ resuming re-sends …` line. TOTAL becomes Claude Code's own figure, usually a little higher than before | Claude Code writes its own total when a session exits, and it counts the requests its transcripts miss |
| 12 | `claude --resume` the session from check 11 and send a message | The same ID comes back in the live pane with a countdown, and TOTAL doesn't drop: Claude Code's figure plus the new requests | Resuming appends to the same transcript, and Claude Code's total carries across resumes |
| 13 | `/exit` a session with some context, `claude --resume` it within a minute, and send a message | Its first row: `⟳ re-wrote …: resumed`, CACHED about the tool list's share of the prompt (e.g. 25k of 35k), and COST about the amount on its own model in its `└ resuming` line plus your message and the reply | A resumed session sends a fresh system prompt: only the tool list before it can be read back, even a minute later |
| 14 | Read the header | Line 1: `TODAY $…` and the share of input read from cache. Line 2: `At current API list prices; your subscription isn't billed per token` on a subscription, `Estimated at current API list prices` on an API key, and nothing in yellow. Line 3: `Amounts can be lower than actual: Claude Code doesn't log some requests (titles, suggestions…). An exited session's TOTAL is complete.` | The dollars are list prices; on a subscription they're for comparison |

### Re-writes, and the /compact warning

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 15 | In a warm Opus 5.5 session, note the amount on Sonnet 5 in its `└ next message` line, then `/model sonnet` and send a message | The request list shows a red `⟳ re-wrote …: model switch from Opus 5.5 (+$…)`, and the header's `cache misses added` goes up. That row's COST is about the noted amount plus your message and the reply | Each model has its own cache; the next-message amounts are exact |
| 16 | In a warm Opus 5.5 session, `/effort low` and send a message. Then do the same in a warm Sonnet 5 session | Opus 5.5: its new row has no `⟳` note and CACHED stays near 100%. Sonnet 5: `⟳ re-wrote …: effort change` | Claude Code keeps the cache across an effort change only on Opus 5.5 and Fable 5.1 |
| 17 | Start `CLAUDE_CODE_PROMPT_CACHE_TTL=5m claude` on Opus 5.5 and grow it past 100k tokens of CONTEXT (e.g. ask it to read a few large files). Then wait until CACHE shows under 2:30 | Between the `├` prompt line and the `└ next message` line: `├ ⚡ Taking a break? /compact first: ≈$… now, ≈$… once the cache expires in m:ss.` It isn't there while more than half the lifetime is left | Compacting while the cache is warm reads the conversation back; after it expires, it has to write it all first |
| 18 | Then `/compact` | Before you send anything: CONTEXT shows `≈` (the tool list plus the summary), the ⚡ line is gone, and the next-message amounts drop, with `≈`. After your next message: CONTEXT shows the new size without `≈`, and that request's row reads most of its prompt from cache | Compaction replaces the history with a summary; the tool list and system prompt stay cached |
| 19 | In a folder with no other sessions, start A and B, with no file edits in between. Build up some context in B on Opus 5.5. In A, `/model haiku` and send a message. Right away, `/compact` in B | B's ✅ moves to Haiku 4.5, with `≈` on the amounts. Then `/model haiku` in B and send a message: its new row's CACHED is well above 0% | Sessions in the same folder share the cached tool list and system prompt on each model |

### Request list

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 20 | Press `r`. While another session works, scroll down a few rows (`j`), then press `g` | While scrolled: the title says `paused · rows …`, the rows in view stay put as new ones arrive, and the new ones are counted as `… new above`. After `g`: the newest row is on top again | Reading back must not jump while requests arrive |
| 21 | Ask a session for two subagents in parallel: one waits 30 seconds, the other 60 (in Bash, with `python3 -c 'import time; time.sleep(30)'`), and each then replies "done" | `🤖 subagent` rows from both, and TIME never goes up as you read down the list | Rows are in the order their requests started, including those from a subagent's transcript found a few seconds late |
| 22 | Scroll the sessions down a session or two (`j`), press `r`, scroll the request list, then press `r` twice | Each view comes back where you left it | Each view keeps its own place |

### Long turns and long subagents

These check the cache clock when one turn runs longer than the cache lifetime. On a subscription, start Claude Code with `CLAUDE_CODE_PROMPT_CACHE_TTL=5m claude` for the 5-minute cases (an API key uses 5 minutes anyway); subagents always use 5 minutes.

**Setup:** in a scratch folder, `mkdir -p .claude/skills && cp -r <usdash>/tests/sanity/skills/* .claude/skills/`, then start a fresh session there for each check. The skills wait with `python3 … time.sleep`, not `sleep`: Claude Code blocks a plain `sleep` in the foreground.

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 23 | 5-minute cache: `/slow-steps` (six 100-second waits, as separate foreground steps: about 10 minutes in all) | A row per step, none with a `⟳ re-wrote` note, CACHED near 100%, and CACHE back to about `● 4:5x` after every step | A turn is many requests; each step reads the cache and restarts its clock |
| 24 | 5-minute cache: `/slow-tool` (one 330-second foreground wait) | About 5 minutes into the sleep, the session moves to the expired pane with CACHE `○ expired · working`. Report the step after it: `⟳ re-wrote …: cache expired (idle 6 min)` (5½–6 minutes, rounded) and the header's `cache misses added` going up, or CACHED near 100% | The clock counts from the start of the step that ran the tool; nothing refreshes it while the tool runs. The lifetime is a minimum: a cache often outlasts it by a minute or two, so either is possible |
| 25 | 1-hour cache (plain `claude` on a subscription): `/slow-tool` | CACHE counts down from about `● 59:xx` and stays warm; no `⟳ re-wrote` note | 5½ minutes is well inside a 1-hour cache |
| 26 | 5-minute cache: "Use a subagent to run `python3 -c 'import time; time.sleep(100)'` six times, as six separate foreground Bash calls, then report done" | `🤖 subagent` rows every ~100 s, none of them a re-write. The parent ends its turn (Claude Code runs the subagent in the background) and goes to `○ expired · subagent` while it waits. When the subagent returns, the parent's next row shows `⟳ re-wrote …: cache expired (idle ~10 min)`, with CACHED about the tool list's share | A subagent refreshes its own cache, not the parent's; a parent that only waits sends no requests |
| 27 | 1-hour cache: repeat check 26 | The parent stays warm, and its next row has no `⟳ re-wrote` note | The parent's 1-hour cache outlasts a 10-minute subagent |

### Prices, fast mode and web search

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 28 | Start `usdash --once` with the network off, then `usdash --once --offline` with it on | The header's second line says `API list prices of <date> (couldn't refresh them)`, then `(offline)`: the date of the last prices read. Every amount is the same as with the network on | It falls back to the last prices it read, and says how fresh they are |
| 29 | Start `usdash --once` with the network on, then read `~/.cache/usdash/docs.json` | The header says `current API list prices`. The file has today's date under `pricing` and `models`, and `models` lists the models overview's comparison table, most capable first | The copy it falls back to is refreshed on every start |
| 30 | `[record]` In an Opus 5.5 session, turn `/fast` on and send a message. Then `/model`, pick Opus 5 (it has fast mode too), and send another | With fast on: MODEL says `Opus 5.5 … fast`, the request list shows `⟳ … speed change` and about twice the usual cost, and the next message on Opus 5.5 costs about twice as much. After `/model`: report whether the Opus 5 row says `fast` | Fast mode is priced from each reply's `usage.speed`. The next message on Opus 5 or Opus 4.8 is priced as if fast mode stays on; this settles whether it does |
| 31 | `[record]` Ask Claude Code to search the web for something, then `/exit` | The request list shows `🔍 N web searches (+$…)` on that reply, and its COST is its tokens plus $0.01 a search. If there's no 🔍, report that reply's `message.usage` from the transcript | Server-side web search costs $10 per 1,000 searches on top of tokens; this settles whether Claude Code's search is logged that way |

**Overall pass:** after `/exit`, each session's TOTAL is Claude Code's own figure. On an API key, an open session's TOTAL is a little below `/cost` in that session (Claude Code doesn't log some requests; the README's Limitations has the measured gap). Nothing in the header is yellow.
"""

if __name__ == "__main__":
    print(__doc__)
