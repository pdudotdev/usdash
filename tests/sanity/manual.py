"""
## Manual checklist

Checks against live Claude Code sessions, for what the automated tests can't
reach: what Claude Code really writes, and how the dashboard behaves while it
runs. Run them after a Claude Code update, or if the header reports records of
unknown types.

**Setup:** in one terminal, run `usdash`. Use other windows for Claude Code.
The checks work on a subscription or an API key unless they say otherwise.

### Sessions and names

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 1 | Open two Claude Code windows in the same repo (say `shop`), and a third in a worktree of it on another branch (`git worktree add ../shop-fix -b fix`). Send a different message in each | Three rows. You can tell which is which from SESSION (your first words, until Claude Code titles the session), WHERE (`shop`, `shop-fix@fix`) and the `└` line (what you last typed there), without looking at the ID | Rows must map to your windows, not to session ids |
| 2 | In one window, `/rename parser work` | Its SESSION becomes `parser work` within a second or two | A rename beats the automatic title |
| 3 | One session each in the terminal, the VS Code extension and the Desktop app's Code tab | All three rows appear within about 5 seconds of their first message. VS Code is tagged `vscode`; Desktop is tagged `desktop` and shows the sidebar's title | All local surfaces write to the same transcripts folder |
| 4 | In a terminal, `claude -p "say hi"` | A row tagged `sdk-cli`, with its cost | Scripted runs are sessions too |
| 5 | Over VS Code Remote-SSH to a Linux machine, start a session from the extension there, and run `usdash` in that machine's terminal | That session appears; your laptop's sessions don't | usdash shows the sessions of the machine it runs on |
| 6 | In the Desktop app, archive a session that usdash lists | Its row leaves the sessions pane within about 10 seconds; its requests stay in the feed | Archived sessions are done with |
| 7 | Restart with `usdash --window 30m` while one session has been idle for more than 30 minutes | The sessions pane title says `last 30m`, and the idle session isn't listed | `--window` decides which sessions are listed |

### Cache clock and costs

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 8 | Send a message, then watch the CACHE column | `● 59:xx` on a subscription (`sub`), `● 4:xx` on an API key (`api`), counting down, and back near the top after each message. NEXT MESSAGE shows `$… now · $… cold` | The lifetime comes from the session's own usage, counted from the request's start |
| 9 | Leave a session idle past its cache lifetime | CACHE shows `○ cold`; NEXT MESSAGE turns red and says how much it re-writes. On Opus or Sonnet, the advice says switching model costs nothing extra now | A break re-writes the conversation whatever you do, so a cheaper model re-writes for less |
| 10 | Quit a session (`/exit`) | Its CACHE shows `closed`, NEXT MESSAGE shows `if resumed: $…`, `(CC $…)` appears after COST ALL DAYS, and it gets no advice | Claude Code writes its own total when a session closes |
| 11 | `claude --resume` the session from test 10 and send a message | The same row (same ID) comes back, with a countdown instead of `closed` | Resuming appends to the same transcript |
| 12 | Look at the header | `TODAY $… est.` and the share of input read from cache. On a subscription account, the second line starts with `API-equivalent prices`; on an API-key account it doesn't | A subscription isn't billed per token: its dollars are for comparison |

### Re-writes and advice

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 13 | In a warm Opus 5.5 session, read the advice line | `Switching to Sonnet 5 now costs $… extra; its cheaper messages make that back in ~N messages. Switching is free once Opus 5.5's cache expires, in mm:ss.` The line names the session, not just its id | Switching model re-writes the whole conversation; after the cache expires it's free |
| 14 | `/model sonnet`, then send a message | The feed shows `⟳ re-wrote …: model switch from Opus 5.5 (+$…)` in red, and the header's `cache misses added` total goes up | Each model has its own cache |
| 15 | In an Opus 5.5 session, `/effort low` and send a message. Then do the same in a Sonnet 5 session | Opus 5.5: no `⟳` note on its next row, and CACHED stays high. Sonnet 5: `⟳ re-wrote …: effort change` | Only Opus 5.5 and Fable 5.1 keep the cache when effort changes |
| 16 | On Opus 5.5 at high effort, send a few messages that need some thought. `/effort low` and send a couple more, then `/effort high` and send one | `💡 … Lower /effort to low: ≈$… less per message, no cache cost on Opus 5.5.`, if the low-effort replies were at least ~250 output tokens shorter (the $0.005 threshold) | The estimate compares this session's own replies at each effort |
| 17 | In a session over 100k tokens, wait until less than 10 minutes of cache remain (2.5 minutes on a 5-minute cache) | `⚡ … Context …k, cache expires in m:ss: /compact now ≈$…; after a break ≈$…` | /compact reads the cache while it's warm and re-writes everything after a break |
| 18 | `/compact` | Before you send anything, NEXT MESSAGE drops to about the price of the tool list plus the summary, and the /compact tip is gone. The next row reads most of its prompt from cache, and CONTEXT TOKENS drops | Compaction replaces the history with a summary; the tool list and system prompt stay cached |
| 19 | In a clean folder, start sessions A and B, with no file edits in between. Build up some context in B on Opus 5.5. In A, `/model haiku` and send a message. Right away, `/compact` in B | B's advice says `Switch to Haiku 4.5 now: already cheaper …`. Then `/model haiku` in B and send a message: B's new row has CACHED well above 0% and no `⟳` note | Sessions in the same folder share the cached tool list and system prompt on each model |

### Request feed

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 20 | While another session works, scroll the feed with the wheel or j/k, then press g | The title says `paused · rows …`, the rows in view stay put as new ones arrive, new rows are counted as `new above`, and g returns to live | Reading back must not jump while requests arrive |
| 21 | Ask for two subagents in parallel: one runs `sleep 30`, the other `sleep 60`, and each then replies "done" | `🤖 subagent` rows from both, and TIME never goes up as you read down the feed | Rows are in the order their requests started, including those from subagent transcripts found a few seconds late |

### Long skills and long subagents

These check how usdash counts the cache clock when one turn runs longer than the cache lifetime. On a subscription the main conversation has a 1-hour cache, so start Claude Code with **`CLAUDE_CODE_PROMPT_CACHE_TTL=5m claude`** to test the 5-minute cases (an API key uses 5 minutes anyway). Subagents always use 5 minutes.

**Setup:** in a scratch folder, `mkdir -p .claude/skills && cp -r <usdash>/tests/sanity/skills/* .claude/skills/`, then start a fresh session there for each test.

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 22 | 5-minute cache: `/slow-steps` (six `sleep 100` calls, about 10 minutes in all) | A row per step. No `⟳ re-wrote` notes, CACHED stays near 100%, and CACHE jumps back to about `● 4:5x` after every step | A turn is many API requests; each step reads the cache and restarts its clock, so a long turn stays warm while each step starts in time |
| 23 | 5-minute cache: `/slow-tool` (one `sleep 330`) | During the sleep, CACHE runs down to `○ cold`. The step after it shows `⟳ re-wrote …: cache expired (idle 6 min)` (5½–6 minutes, rounded) and the header's `cache misses added` total goes up | The clock counts from the start of the step that ran the tool. Nothing refreshes it while the tool runs |
| 24 | 1-hour cache (plain `claude` on a subscription): `/slow-tool` | CACHE counts down from about `● 59:xx` and stays warm; no `⟳ re-wrote` note | 5½ minutes is well inside a 1-hour cache |
| 25 | 5-minute cache: "Use a subagent to run `sleep 100` six times, as six separate Bash calls, then report done" | `🤖 subagent` rows every ~100 s, none of them re-writes. The parent's CACHE runs down to `○ cold` while it waits. When the subagent returns, the parent's next row shows `⟳ re-wrote …: cache expired (idle ~10 min)` | A subagent refreshes its own cache, not the parent's; a parent that only waits sends no requests |
| 26 | 1-hour cache: repeat test 25 | The parent stays warm, and its next row has no `⟳ re-wrote` note | The parent's 1-hour cache outlasts a 10-minute subagent |

**Overall pass:** a closed session's COST ALL DAYS is close to Claude Code's own `(CC $…)`, usually a little lower (background requests aren't in the transcripts; the README's Limitations has the measured gap). On an API key, a session's COST ALL DAYS is also close to `/cost` in that session. Nothing in the header says `records of unknown types`.
"""

if __name__ == "__main__":
    print(__doc__)
