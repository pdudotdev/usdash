# usdash redesign plan: a session-first dashboard

Status: built, as planned here, with the two deviations in §11. §8 is the evidence the numbers rest on.

## 1. Goals

1. The screen is about **sessions**: what each one has cost, what continuing it costs, and what to do about it. The request list is one key away, not the main view.
2. Every dollar amount is either **exact** (measured from the transcripts, times list prices) or marked **≈** (it assumes something about messages not sent yet). No hard-coded "expensive" thresholds, no colour judgements.
3. Sessions from the **last 24 hours**, organized so the ones in use stand out without highlighting rules.
4. Advice reads as plain, actionable language: one action per session, then what each option costs.

## 2. The screen

```
╭─ 💲 usdash · live ──────────────────────────────────────────────────────────────────────────────────────────╮
│ TODAY $47.05  ·  98% of input read from cache  ·  ⟳ cache misses added $2.60 (cache expired $2.38, /compact $0.22)
│ What this would cost at API list prices (2026-09-26); your subscription isn't billed per token
╰───────────────────────────────────────────── history from Sun 13:42 ──────────────────────────────────────────╯
╭─ sessions · last 24h · 1 live · 16 idle ───────────────────────────────────────────────────────────────────╮
│                                                                          CONTEXT     COST     COST
│   ID    SESSION                  WHERE      MODEL           CACHE         TOKENS    TODAY    TOTAL
│ LIVE
│   b379  Fix all 12               usdash     Opus 5.5 xhigh  ● 58:30         504k   $18.18   $18.18
│         └ 2m ago · "write this into a plan for the redesign…"
│         💡 Stay on Opus 5.5 for now; switching is free after your next 1-hour break.
│              stay          re-sends 504k tokens: $0.10 now, $4.03 after a break
│            ↑ Fable 5.1     $9.98 more now, then ≈$0.09 more a message
│            ↓ Sonnet 5      $1.92 more now, then ≈$0.02 less a message · evens out after ≈80 messages
│            ↓ Haiku 4.5     $0.68 more now, then ≈$0.10 less a message · evens out after ≈7 messages
│              /compact      ≈$0.18 now, ≈$4.11 after a break; then ≈$0.09 less a message
│              /effort       costs nothing now
│ IDLE
│   3b4c  Usdash bug confirmation  llm-trunk  Opus 5.5 high   closed · 2h      54k    $1.08    $1.08  (CC $1.78)
│         └ resuming re-sends 54k tokens: $0.43 on Opus 5.5, $0.22 on Sonnet 5, $0.08 on Haiku 4.5
│   393f  Cache-aware switching…   llm-trunk  Opus 5.5 high   closed · 5h     533k   $12.03   $17.56  (CC $…)
│         └ resuming re-sends 533k tokens: $4.27 on Opus 5.5, $2.13 on Sonnet 5, $0.82 on Haiku 4.5
│   1c1c  LLM gateway request-r…   Desktop    Sonnet 5 high   ○ cold · 20h    119k        —    $0.90
│         └ continuing re-sends 119k tokens: $0.48 on Sonnet 5, $0.19 on Haiku 4.5
│   …
╰──────────────────────────────────────── ↑↓ scroll · r: every request · q: quit ───────────────────────────╯
```

### 2.1 Header (unchanged content)
- Line 1: `TODAY $…` · share of input read from cache · `⟳ cache misses added $… (cause $…, …)`.
- Line 2: `What this would cost at API list prices (date); your subscription isn't billed per token`, or `Estimated at API list prices (date)` on an API-key account; unknown record types appended as now.
- Bottom edge: `history from <day time>`.

### 2.2 Sessions pane
- **Title:** `sessions · last 24h · N live · M idle`; `· sessions a–b of N` added when the list doesn't fit.
- **Columns** (numbers right-aligned, two-line names bottom-aligned): ID · SESSION (name + surface tag `vscode` / `desktop` / scripted entrypoint; no `sub`/`api`) · WHERE · MODEL (family colour, effort dim) · CACHE · CONTEXT TOKENS · COST TODAY (`—` when nothing today) · COST TOTAL · unnamed `(CC $…)`.
- **CONTEXT TOKENS:** the last prompt `P`. Right after `/compact` (no request since): `≈` + tool list + summary (§3.3), since `P` no longer describes the conversation.
- **CACHE:** `● mm:ss` (green) while warm; `○ cold · 20h` (red) for an open session past its lifetime; `closed · 2h` (dim) after `/exit`. The age is time since the session's last activity (`duration_text`-style: `45m`, `2h`, `1d`).
- **Sections:** dim `LIVE` and `IDLE` labels. Live = cache warm and not closed. Idle = everything else. Newest first within each.
- **Live session** (row, then):
  1. `└ <ago> · "<last prompt>"` (as today).
  2. Action line: `💡` or `⚡` + one sentence (§4.1).
  3. Option rows, an aligned mini-table (§4.2): `stay`, then `↑` models, `↓` models, `/compact`, `/effort`.
- **Idle session** (row, then one line): `└ resuming re-sends Nk tokens: $A on <own model>, $B on <cheaper>, $C on <cheapest>` for a closed session; `└ continuing re-sends …` for an open one (§3.2). Model names in their family colour.
- **Scripted runs:** closed sessions with a scripted entrypoint (`claude -p` and SDKs) collapse into one idle row per (folder, entrypoint): `12 runs · sdk-cli`, their summed COST TODAY / TOTAL, no resume line. Open or live scripted sessions show like any other. Keeps a loop of `claude -p` from burying the real sessions.
- **Scrolling:** by whole session (a live block never splits). ↑/↓/wheel/j/k one session, space/b a page, g/G top/bottom. The list re-sorts as sessions become active; scrolling keeps its offset.

### 2.3 Requests view (`r`)
- `r` switches the pane to the full request feed, everything loaded (current feed, unchanged: newest first by start, paused-scroll behaviour, `⟳` notes); `r` again returns. The header stays.
- Scroll keys act on whichever view is showing; each view keeps its own position.

### 2.4 Removed
- The advice pane (its content moves into live sessions).
- The `sub`/`api` tag and `(on your plan, this saves usage, not money)`: the header already says it; the countdown shows the lifetime.
- NEXT MESSAGE / AFTER A BREAK columns: their exact parts move into the `stay` row and the idle line; their guessed parts are dropped (§3).

## 3. Every number: what it is, exact or ≈

Notation: `P` = the main conversation's last prompt (`input + cache_read + cache_creation` of its last request), measured. `read(M)`, `write(M, ttl)` = list prices; `ttl` = the session's cache lifetime.

### 3.1 Exact
| Shown | Formula | Why it's exact |
|---|---|---|
| `stay … re-sends Nk tokens: $X now` | `P × read(own)` | The whole last prompt is cached (§8a) |
| `… $Y after a break` | `P × write(own, ttl)` | After expiry the next request writes it all |
| Idle line, own model | `P × write(own, ttl)` | Resuming re-sends the conversation (§8d) |
| Idle line, other model M | `convert(P, M) × write(M, ttl)` | Same, on M's tokenizer (Haiku: ×0.77, §8c) |
| `↑/↓ M: $Z more/less now` | `convert(P, M) × write(M, ttl) − P × read(own)` | Re-send on M vs reading back here. When another session keeps M's tool list cached, it's subtracted and the amount gets ≈ (§3.3) |
| `/effort: costs nothing now` | 0 on Opus 5.5 / Fable 5.1 (not on a cloud provider) | Cache kept (§8e) |
| `/effort: re-sends Nk tokens: $W more now` (other models) | `P × (write − read)` | Effort change re-writes (§8e) |
| COST TODAY / TOTAL / CONTEXT TOKENS | as today | Logged usage |

Not counted anywhere exact: the new message you'll type, tool results, the reply. They depend on what you ask.

### 3.2 Idle line rules
- Models listed: the session's own, then each cheaper model in `LADDER` order (never more expensive ones). A session on the cheapest model lists only its own.
- Other sessions' cached tool lists are **not** subtracted: a resumed session gets a fresh system prompt (git status, date) that may not match them (§8d saw only 0 or ~22k read back).
- A session closed less than one cache lifetime ago might still read its cache if resumed at once; the line shows the full re-send (worst case) until manual check 27 settles it.
- After `/compact` with no request since: `P` is replaced by `tool list + summary` (§3.3) and the line is marked ≈.
- Own model unpriced: `└ resuming re-sends Nk tokens` with no amounts; unpriced other models are left out.

### 3.3 Estimated (≈), and how to make them better than today
| Shown | Formula | Assumption | Improvement in this plan |
|---|---|---|---|
| `then ≈$s less/more a message` | per-message cost here − there, once both are cached: `read` of the conversation + `write` of an average message's growth + an average reply | Replies stay as long; messages grow as they have | Growth: **mean** rise over the session's last 20 messages, not the all-time median (§8g: the median undercounts the average ~2×; recent messages describe the current work better). Replies: mean (already) |
| `evens out after ≈N messages` | `now difference ÷ s` | as above | — |
| `/compact ≈$A now, ≈$B after a break` | `P × read` (or `× write`) + instruction + **summary** × out price | Summary size | Summary = median of `postTokens` seen on this machine, scaled up for large conversations (§8b: 3.8k typical, 14–16k at 450k–970k) |
| `/compact … then ≈$c less a message` | `(P − tool list − summary) × read` | Tool list and summary sizes | Tool list measured where the transcripts reveal it (below) |
| M's cached tool list (switch rows) | subtracted when another session in the same folder used M within its lifetime | Identical prefix | Size measured where revealed, else smallest first prompt |
| `/effort low: then ≈$s less a message` | this session's replies at that effort, else ratio from sessions that used both | Replies at that effort | — |

**Measured tool list.** When a main request is the first after a `/compact`, or a session's first request, and it read part of its prompt from the cache, that `cache_read` is the cached tool list + system prompt for (folder, entrypoint, model family). Keep the latest per key; use it before the first-prompt estimate (§8b: 36,829 measured vs 39.9k estimated).

## 4. Advice

### 4.1 Action line, first match wins (live sessions only)
1. `⚡ Taking a break? /compact first: ≈$A now, ≈$B once the cache expires in m:ss.` when the conversation is 100k+, at most min(10 min, half the lifetime) is left, and it saves ≥ $0.005 a message (as the `/compact` row requires).
2. `⚡ Switch to M now: it's already cheaper.` when a cheaper model's now-difference ≤ 0 (the one with the largest per-message saving).
3. `💡 Switch to M now: it would have saved $R by now; switching costs $Z.` when rent-or-buy triggers for **any** cheaper model (today only the nearest is checked); if several, the largest saving.
4. `💡 Try /effort X: ≈$s less a message, at no cost now.` when known and ≥ $0.005.
5. `💡 Stay on <model> for now; switching is free after your next 1-hour break.` (`5-minute` on a 5-minute cache), only when some cheaper model saves ≥ $0.005 a message. Otherwise no action line: the option rows alone (on Haiku 4.5, that's `stay`, `↑` rows, `/compact`, `/effort`).

### 4.2 Option rows
- `stay`: `re-sends Nk tokens: $X now, $Y after a break` (≈ only right after `/compact`).
- One row per other priced model in `LADDER`, `↓` for a cheaper one (its output costs less: the models the switch actions consider), `↑` for the rest. Text: `$Z more now` / `$Z less now`, then `≈$s less/more a message`, then `· evens out after ≈N messages` when it saves per message and costs now.
- `/compact`: shown when not compacted since the last request and it saves ≥ $0.005 a message.
- `/effort`: always on a live session; per-message part only when known and ≥ $0.005.
- Rows for unpriced models are left out. If the session's own model is unpriced, the live block shows only the last-prompt line.
- Minimum saving to mention a switch in the action line stays $0.005 a message; option rows show every model regardless.

### 4.3 Removed rules
- The cold-cache tip (`Good time to switch model…`): an idle session's line now shows what continuing costs on each model.

## 5. Other behaviour
- `--window` default `24h`; `history_start` still loads at least the window.
- Sessions without requests, and archived Desktop sessions, stay hidden.
- `--once` prints the sessions view.
- Performance: engine work per render = live sessions × models (≤ 4) + idle sessions × models (arithmetic only). Memoize per store revision where it repeats.

## 6. Code changes

| File | Change |
|---|---|
| `usdash/engine.py` | New `resend(store, session, model, warm)` → `(tokens, dollars, exact)`. `model_move` → now-difference from `resend`; per-message `s` with mean growth. `compact_cost` → learned summary; add per-message saving. `effort_move` + rewrite cost off Opus 5.5/Fable 5.1. Drop `CacheState.next_now/next_cold` if unused. |
| `usdash/sessions.py` | `Session.growth()` → mean. Store learns: summary sizes (`postTokens` + the `preTokens` they came from), measured tool list per (cwd, entrypoint, family). `tool_list()` prefers the measurement. |
| `usdash/advice.py` | `advise()` → `SessionAdvice(action, urgent, options: list[Option])`; rent-or-buy over all cheaper models; action priority §4.1; option texts §4.2. `Memory` unchanged. |
| `usdash/ui.py` | `View.mode` (`sessions`/`requests`), `View.session_scroll`; new sessions pane (sections, child lines, scrolling by session); requests view = current feed panel; footer keys per view; CACHE ages; COST TODAY `—`; remove advice pane, `sub/api` tags, plan note. |
| `usdash/app.py` | `--window` default 24h (help text); key `r`; keys route to the current view. |
| `scripts/readme_screen.py` | Scenario with a live block, a closed large session, a cold open one; render the sessions view. |
| `docs/dashboard.svg` | Regenerate. |

## 7. Tests, docs, checks

### 7.1 Automated tests (replace or add)
- `resend`: exact values warm/cold, other model with tokenizer conversion, after `/compact` (≈ flag).
- Idle line: closed vs open wording, models listed (own + cheaper only), no tool-list discount.
- Switch rows: now-difference = re-send difference (with and without another session's cached tool list); `↑`/`↓` sets for Opus 5.5, Haiku 4.5, Fable 5.1, a model outside the ladder.
- Per-message: mean growth; evens-out count.
- `/compact`: learned summary (median, and scaled for a large conversation); measured tool list beats the first-prompt estimate.
- `/effort` row: nothing-now on Opus 5.5; re-write cost on Sonnet 5 and on a Bedrock id; per-message part only with history.
- Action priority: each of the five cases, and rent-or-buy firing for Haiku while Sonnet hasn't.
- UI: scripted runs collapse into one row per folder and entrypoint; CONTEXT shows `≈` after `/compact`; LIVE/IDLE sections and counts in the title; CACHE ages; `—` for no spend today; `(CC $…)` column; scrolling by session keeps live blocks whole; `r` toggles and each view keeps its scroll; 24h default; narrow terminal cuts lines, never wraps them.
- Keep: every existing engine/sessions/transcripts test that still applies; drop tests of removed UI (advice pane, NEXT MESSAGE column, cold tip).

### 7.2 Docs
- README: overview (one sessions pane + requests on `r`), the picture, the column table, the live block and idle line with what's exact vs ≈, advice rules rewritten, example session, `--window` default 24h, keys, limitations (estimates section: exact vs ≈).
- `research/CACHE-DECISIONS.md` §7 (inputs: re-send, mean growth, learned summary, measured tool list), §10 (engine summary).
- `tests/sanity/manual.py`: update checks that mention panes/columns/tips; add:
  - 27: `/exit`, then `claude --resume` within a minute: does the first row read from cache? (decides §3.2's worst case)
  - 28: resume a large idle session: its first row's cost matches the idle line (± the new message and reply)
  - 29: `r` toggles views; scrolling each view keeps its place
  - 30: `usdash` with no flags lists sessions from the last 24 hours

## 8. Challenge: assumptions checked against this machine's transcripts
88 sessions, 2,202 main requests, all history.

a. **"The whole last prompt is cached."** Uncached remainder: median 0.0007%, max 0.04% of the prompt. ✔ `P × read` is exact.

b. **"/compact writes a ~3,000-token summary."** ✘ 9 compactions: median 3,807; 13,984 at 450k and 16,088 at 972k before. The dashboard understates `/compact now` by up to ~$0.25 on big Opus sessions today. → learned, size-aware summary (§3.3). Also: the request after a `/compact` read exactly 36,829 tokens in four different sessions: the real cached tool list + system prompt, vs 39.9k from first prompts. → measured tool list.

c. **"Haiku counts ~0.77× the tokens."** No Haiku switches in this history to check; stays 0.77 from the research run (0.758). Haiku figures ±a few %.

d. **"Resuming re-sends the whole conversation."** 31 resumes: the first request after resuming was 0.1–4% larger than the last one before (the new message) and read 0 or ~22k from cache. ✔ Not seen: a resume within a cache lifetime (all gaps ≥ 92 min) → manual check 27.

e. **"Effort changes keep the cache on Opus 5.5 only."** Opus 5.5: 4 of 4 read 100% back. Opus 5: 7%. ✔

f. **Reply lengths.** Mean is 1.2–25× the median (long tail). Mean is right for per-message averages (costs add up); a single message varies a lot → per-message amounts stay ≈.

g. **"Growth per message = median rise."** ✘ for averages: mean is ~2× the median in most sessions (big tool results are real cost). → mean for per-message amounts.

h. **Not checkable from transcripts:** `/compact`'s own request (never logged) and background requests. The now-vs-after-a-break difference for `/compact` is exact; its absolute price is ≈.

## 9. Challenge: the plan itself
Fixed after a second read of this plan:
- The switch rows' tool-list discount is an inference, so those amounts get ≈ (§3.1), not "exact".
- On the cheapest model there's nothing to switch down to: no "Stay …" action line (§4.1).
- CONTEXT TOKENS right after `/compact` described the old conversation (§2.2).
- The requests view shows everything loaded, not just today (§2.3).
- A loop of `claude -p` runs in 24 hours would bury the real sessions: collapse closed scripted runs (§2.2).
- Unpriced models: explicit rules (§3.2, §4.2).
- Per-message growth: recent mean, not all-time (§3.3).

Considered and not adopted:
- **Price columns (NOW / AFTER A BREAK)** in the table: the idle line and the `stay` row carry the same exact numbers with their model names, and the table stays narrow (fits ~120 columns).
- **A per-model matrix** (a column per model with the resume price): exact and scannable, but 3–4 more money columns on every row; the idle line says the same in words.
- **Learning the tokenizer ratio** from mid-session switches: none in this history; revisit if they appear.
- **Colour or stripe for "expensive"**: needs a threshold (§1.2).

## 10. Order of work
1. Engine + sessions (§3, §6) with tests.
2. Advice structure (§4) with tests.
3. UI sessions view, requests view, keys, window (§2, §5) with tests.
4. README picture script, README, research doc, manual checklist.
5. Full test run, `--once` on real data at 100/140/200 columns, commit.

## 11. Deviations in the build
- **Measured tool list:** taken only from the request right after `/compact`. A session's first request can carry a resumed or forked conversation, so what it read back isn't reliably the tool list.
- **Idle count in the title:** counts rows, so a folder's folded script runs count once.
