"""
## Manual checklist

**Setup:** in one terminal, run `usdash`. Use other windows for Claude Code.

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 1 | Open two Claude Code windows in the same repo, one on another branch, and send a message in each | Two rows. You can tell which is which from the NAME, WHERE (`repo@branch`) and LAST PROMPT columns alone, without looking at the ID | Rows must map to your windows, not to session ids |
| 2 | In one window, `/rename parser work` | Its row's NAME becomes `parser work` within a second | A rename beats the automatic title |
| 3 | Send a message, then watch the CACHE column | `● 59:xx` on a subscription (`sub`), `● 4:xx` on an API key (`api`), counting down, and back to the top after each message | The lifetime comes from the session's own usage, counted from the request's start |
| 4 | One session each in the terminal, the VS Code extension and the Desktop app's Code tab | All three rows appear. VS Code is tagged `vscode`; Desktop is tagged `desktop` and shows the sidebar's title | All local surfaces write to the same transcripts folder |
| 5 | Over VS Code Remote-SSH to a Linux machine, start a session from the extension there, and run `usdash` in that machine's terminal | That session appears; your laptop's sessions don't | usdash shows the sessions of the machine it runs on |
| 6 | In a warm Opus session, read the advice line | `Warm on Opus 5.5 for mm:ss more. Switching to Sonnet 5 now costs +$… (pays back after ~N messages); after that it's free.` The line names the session, not just its id | Switching model re-writes the whole conversation; after the cache expires it's free |
| 7 | `/model sonnet`, then send a message | The feed shows `⟳ re-wrote …: model switch from Opus 5.5 (+$…)` in red, and the header's `re-writes cost` goes up | Each model has its own cache |
| 8 | In a session over 100k tokens, wait until less than 10 minutes of cache remain (or 2.5 minutes on a 5-minute cache) | `⚡ … Context …k, cache expires in m:ss: /compact now ≈$…; after a break ≈$…` | /compact reads the cache while it's warm and re-writes everything after a break |
| 9 | `/compact` | A row for the next request. If it missed most of the cache, the note says `/compact`. The CONTEXT column drops | Compaction rewrites the history; only the tool list and system prompt stay cached |
| 10 | Leave a session idle past its cache lifetime | CACHE shows `○ cold`; NEXT MESSAGE turns red; the advice says switching model costs nothing extra now | A break re-writes the conversation whatever you do, so a cheaper model re-writes for less |
| 11 | Quit a session (`/exit`) | Its CACHE shows `closed`, TOTAL shows `(CC $…)`, and it gets no advice | Claude Code writes its own total when a session closes |
| 12 | Scroll the feed with the wheel or j/k, then press g | The title says `paused`, new rows are counted as `new above`, and g returns to live | The same keys as llm-trunk's dashboard |

### Long skills and long subagents

These check how usdash counts the cache clock when one turn runs longer than the cache lifetime. On a subscription the main conversation has a 1-hour cache, so start Claude Code with **`CLAUDE_CODE_PROMPT_CACHE_TTL=5m claude`** to test the 5-minute cases (an API key uses 5 minutes anyway). Subagents always use 5 minutes.

**Setup:** in a scratch folder, `mkdir -p .claude/skills && cp -r <usdash>/tests/sanity/skills/* .claude/skills/`, then start a fresh session there for each test.

| # | Do this | Pass criteria (dashboard) | Why |
|---|---|---|---|
| 13 | 5-minute cache: `/slow-steps` (six `sleep 100` calls, about 10 minutes in all) | A row per step. No `⟳ re-wrote` notes, CACHED stays near 100%, and CACHE jumps back to about `● 4:5x` after every step | A turn is many API requests; each step reads the cache and restarts its clock, so a long turn stays warm while each step starts in time |
| 14 | 5-minute cache: `/slow-tool` (one `sleep 330`) | During the sleep, CACHE runs down to `○ cold`. The step after it shows `⟳ re-wrote …: cache expired (idle 6 min)` (5½–6 minutes, rounded) and the header's `re-writes cost` goes up | The clock counts from the start of the step that ran the tool. Nothing refreshes it while the tool runs |
| 15 | 1-hour cache (plain `claude` on a subscription): `/slow-tool` | CACHE counts down from about `● 59:xx` and stays warm; no `⟳ re-wrote` note | 5½ minutes is well inside a 1-hour cache |
| 16 | 5-minute cache: "Use a subagent to run `sleep 100` six times, as six separate Bash calls, then report done" | `🤖 subagent` rows every ~100 s, none of them re-writes. The parent's CACHE runs down to `○ cold` while it waits. When the subagent returns, the parent's next row shows `⟳ re-wrote …: cache expired (idle ~10 min)` | A subagent refreshes its own cache, not the parent's; a parent that only waits sends no requests |
| 17 | 1-hour cache: repeat test 16 | The parent stays warm, and its next row has no `⟳ re-wrote` note | The parent's 1-hour cache outlasts a 10-minute subagent |

**Overall pass:** the TODAY figure is close to `/cost` (or `/usage`), usually a little lower (background requests), and nothing in the header says `records of unknown types`.
"""

if __name__ == "__main__":
    print(__doc__)
