"""
## Manual checklist

Checks against live Claude Code sessions, for what the automated tests can't
reach: what Claude Code really writes, and how the dashboard behaves while it
runs. Run them after a Claude Code update, or if the header reports records of
unknown types.

**Setup:** in one terminal, run `usdash`. Use other windows for Claude Code.
The checks work on a subscription or an API key unless they say otherwise.
"Rows" of requests are in the request list: press `r` to show it, `r` again
to go back to the sessions.

### Sessions and names

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 1 | Open two Claude Code windows in the same repo (say `shop`), and a third in a worktree of it on another branch (`git worktree add ../shop-fix -b fix`). Send a different message in each | Three live sessions. You can tell which is which from SESSION (your first words, until Claude Code titles the session), PROJECT (`shop`, `shop-fix@fix`) and the `└` line (what you last typed there), without looking at the ID | Rows must map to your windows, not to session ids |
| 2 | In one window, `/rename parser work` | Its SESSION becomes `parser work` within a second or two | A rename beats the automatic title |
| 3 | One session each in the terminal, the VS Code extension and the Desktop app's Code tab | All three appear within about 5 seconds of their first message. WHERE says `CLI`, `IDE` and `Desktop`, and the Desktop one shows the sidebar's title | All local surfaces write to the same transcripts folder |
| 4 | In one folder, run `claude -p "say hi"` twice | One row in the idle pane: `2 runs`, WHERE `script`, `exited · …`, with their summed cost | A loop of script runs mustn't bury the real sessions |
| 5 | Over VS Code Remote-SSH to a Linux machine, start a session from the extension there, and run `usdash` in that machine's terminal | That session appears; your laptop's sessions don't | usdash shows the sessions of the machine it runs on |
| 6 | In the Desktop app, archive a session that usdash lists | It leaves the list within about 10 seconds; its requests stay in the request list | Archived sessions are done with |
| 7 | Restart with `usdash --window 30m` while one session has been idle for more than 30 minutes | The pane title says `last 30m`, and the idle session isn't listed | `--window` decides which sessions are listed |

### Cache clock and costs

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 8 | Send a message, then watch the session | It's in the live pane. CACHE shows `● 59:xx` on a subscription, `● 4:xx` on an API key, counting down, and back near the top after each message. Its `└ next message re-sends …k tokens` line has the same token count as CONTEXT, and its own model is bold, marked `(cached) ✅` | The lifetime comes from the session's own usage, counted from the request's start |
| 9 | Leave a session idle past its cache lifetime | It moves to the idle pane with `○ expired · …`, and its line reads `└ continuing re-sends …k tokens: $… on <its model>, …` | A break re-writes the conversation whatever you do, so a cheaper model re-writes for less |
| 10 | Quit a session (`/exit`) | `exited · 1m`, TOTAL switches to Claude Code's own figure (usually a little higher), a `└ resuming re-sends …` line, and no advice | Claude Code writes its own total, which counts the requests transcripts miss, when a session exits |
| 11 | `claude --resume` the session from test 10 and send a message | The same ID comes back in the live pane, with a countdown | Resuming appends to the same transcript |
| 12 | Look at the header | `TODAY $…` and the share of input read from cache. On a subscription account, the second line says `At current API list prices; your subscription isn't billed per token`; on an API-key account, `Estimated at current API list prices`. The third line says `Amounts can be lower than actual: Claude Code doesn't log some requests …` | A subscription isn't billed per token: its dollars are for comparison |

### Re-writes and advice

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 13 | In a warm Opus 5.5 session, read its lines | `├` what you last typed, then `└ next message re-sends …k tokens: $… on Fable 5.1, $… on Opus 5.5 (cached) ✅, $… on Sonnet 5, $… on Haiku 4.5`: Opus 5.5 a small fraction of the others | Your own model reads the conversation back; any other writes all of it into its own cache |
| 14 | Note the next-message line's amount on Sonnet 5, then `/model sonnet` and send a message | The request list shows `⟳ re-wrote …: model switch from Opus 5.5 (+$…)` in red, and the header's `cache misses added` total goes up. That row's COST is about the noted amount, plus your message and the reply | Each model has its own cache; the next-message amounts are exact |
| 15 | In an Opus 5.5 session, `/effort low` and send a message. Then do the same in a Sonnet 5 session | Opus 5.5: its next row has no `⟳` note, and CACHED stays high. Sonnet 5: `⟳ re-wrote …: effort change` | Only Opus 5.5 and Fable 5.1 keep the cache when effort changes |
| 16 | In a session over 100k tokens, wait until less than 10 minutes of cache remain (2.5 minutes on a 5-minute cache) | `⚡ Taking a break? /compact first: ≈$… now, ≈$… once the cache expires in m:ss.` | /compact reads the cache while it's warm, and re-writes everything once it has expired |
| 17 | `/compact` | Before you send anything: CONTEXT shows `≈` (the tool list plus the summary), and the next-message amounts drop, with `≈`. After the next message: CONTEXT shows its new size without `≈`, and that request's row reads most of its prompt from cache | Compaction replaces the history with a summary; the tool list and system prompt stay cached |
| 18 | In a clean folder, start sessions A and B, with no file edits in between. Build up some context in B on Opus 5.5. In A, `/model haiku` and send a message. Right away, `/compact` in B | B's ✅ moves to Haiku 4.5: its next message is cheaper there. Then `/model haiku` in B and send a message: B's new row has CACHED well above 0% and no `⟳` note | Sessions in the same folder share the cached tool list and system prompt on each model |

### Request list

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 19 | Press `r`. While another session works, scroll with the wheel or j/k, then press g | The title says `paused · rows …`, the rows in view stay put as new ones arrive, new rows are counted as `new above`, and g returns to live | Reading back must not jump while requests arrive |
| 20 | Ask for two subagents in parallel: one runs `sleep 30`, the other `sleep 60`, and each then replies "done" | `🤖 subagent` rows from both, and TIME never goes up as you read down the list | Rows are in the order their requests started, including those from subagent transcripts found a few seconds late |

### Long skills and long subagents

These check how usdash counts the cache clock when one turn runs longer than the cache lifetime. On a subscription the main conversation has a 1-hour cache, so start Claude Code with **`CLAUDE_CODE_PROMPT_CACHE_TTL=5m claude`** to test the 5-minute cases (an API key uses 5 minutes anyway). Subagents always use 5 minutes.

**Setup:** in a scratch folder, `mkdir -p .claude/skills && cp -r <usdash>/tests/sanity/skills/* .claude/skills/`, then start a fresh session there for each test.

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 21 | 5-minute cache: `/slow-steps` (six `sleep 100` calls, about 10 minutes in all) | A row per step. No `⟳ re-wrote` notes, CACHED stays near 100%, and CACHE jumps back to about `● 4:5x` after every step | A turn is many API requests; each step reads the cache and restarts its clock, so a long turn stays warm while each step starts in time |
| 22 | 5-minute cache: `/slow-tool` (one `sleep 330`) | During the sleep, the session moves to the idle pane, `○ expired`. The step after it shows `⟳ re-wrote …: cache expired (idle 6 min)` (5½–6 minutes, rounded) and the header's `cache misses added` total goes up | The clock counts from the start of the step that ran the tool. Nothing refreshes it while the tool runs |
| 23 | 1-hour cache (plain `claude` on a subscription): `/slow-tool` | CACHE counts down from about `● 59:xx` and stays warm; no `⟳ re-wrote` note | 5½ minutes is well inside a 1-hour cache |
| 24 | 5-minute cache: "Use a subagent to run `sleep 100` six times, as six separate Bash calls, then report done" | `🤖 subagent` rows every ~100 s, none of them re-writes. The parent goes `○ expired` while it waits. When the subagent returns, the parent's next row shows `⟳ re-wrote …: cache expired (idle ~10 min)` | A subagent refreshes its own cache, not the parent's; a parent that only waits sends no requests |
| 25 | 1-hour cache: repeat test 24 | The parent stays warm, and its next row has no `⟳ re-wrote` note | The parent's 1-hour cache outlasts a 10-minute subagent |

### Coming back, and the window

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 26 | `/exit` a session with some context, then `claude --resume` it within a minute and send a message | Record what its first row read from cache. If most of the prompt was read back, a quick resume costs less than the idle line said (it shows the full re-send, the worst case) | Settles whether a session closed less than a cache lifetime ago can still read its cache |
| 27 | Resume a large idle session (100k+) after its cache has expired | Its first row's COST is about the idle line's amount for its model, plus your message and the reply | The idle line is exact: resuming re-sends the whole conversation |
| 28 | Scroll the sessions a few rows, press `r`, scroll the request list, then press `r` twice | Each view comes back where you left it | The two views keep their own place |
| 29 | Run `usdash` with no options while a session from yesterday afternoon (under 24 hours ago) and one from two days ago exist | The title says `last 24h`; yesterday's session is listed, the older one isn't | The default window is 24 hours |

### Prices, fast mode and web search

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 30 | Start usdash with the network off, then again with `--offline` | The header says `API list prices of <date> (couldn't refresh them)`, then `(offline)`, with the date of the last prices read, and the amounts don't change | It falls back to the last prices it read, and says how fresh they are |
| 31 | Start usdash with the network on, then look at `~/.cache/usdash/docs.json` | The header says `current API list prices`; the file has today's date for `pricing` and `models`, and `models` lists the models overview's comparison table, most capable first | The copy it falls back to is refreshed on every start |
| 32 | In an Opus 5.5 session, turn `/fast` on and send a message. Then `/model`, pick Opus 5 (it has fast mode too), and send another | With fast on: MODEL says `Opus 5.5 high fast`, the request list shows `⟳ … speed change` and about twice the cost, and the next message on Opus 5.5 costs about twice as much. After `/model`: record whether the Opus 5 row says `fast` | Fast mode is priced from each reply's `usage.speed`. The next message on Opus 5 or Opus 4.8 is priced as if fast mode stays on; this settles whether it does |
| 33 | Ask Claude Code to search the web for something, then `/exit` | The request list shows `🔍 N web searches (+$…)` on that reply, and its COST is the tokens plus $0.01 a search. After `/exit`, TOTAL (Claude Code's own) is at least the transcripts' figure | Server-side web search costs $10 per 1,000 searches on top of tokens |

**Overall pass:** after `/exit`, a session's TOTAL is Claude Code's own figure. On an API key, a session's TOTAL is also close to `/cost` in that session (a little lower while it's open: background requests aren't in the transcripts; the README's Limitations has the measured gap). Nothing in the header says `records of unknown types`.
"""

if __name__ == "__main__":
    print(__doc__)
