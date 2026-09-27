# Smart cache decisions: the math

How to decide, in dollars, whether to switch model or effort, `/compact`, `/clear`, or just keep going in a Claude Code session. This is the reference for usdash's cost numbers and advice.

Written 2026-09-27. Prices and rules were checked against Anthropic's pricing page, the Claude API prompt-caching docs, and Claude Code's prompt-caching docs (links at the end) on that date. Every example was computed by script, not by hand. Updated the same day with what building usdash v1 found in real transcripts (§7, §8, §9), and again for its session-first screen (§3, §6, §7, §9, §10); the examples in §4 and §6 and the three real moves in §8 are automated tests in [`tests/test_engine.py`](../tests/test_engine.py).

---

## 0. The idea in one minute

- Every message you send re-sends the **whole conversation**. Claude Code's tool list and system prompt alone are ~35–50k tokens, depending on the model's tokenizer and your MCP servers (33k–52k in this machine's sessions).
- If the model saw that same conversation recently, it **reads it back from its cache**, which is cheap. Otherwise it has to **write it into the cache**, which costs more than normal input.
- **Each model has its own cache.** Switching model means the new model writes the whole conversation again.
- So every change has two prices: **what it costs now** (a one-time re-write) and **what it saves per message afterwards**. The questions are "how many messages until it pays back?" and "will I actually send that many before a break?"

---

## 1. How the bill works

Prices are in USD per **million** tokens (verified 2026-09-27).

| Model | Read from cache | Write (5-min cache) | Write (1-hour cache) | Normal input | Output |
|---|---|---|---|---|---|
| Opus 5.5 | 0.20 | 5.00 | 8.00 | 4 | 20 |
| Sonnet 5 | 0.20 | 2.50 | 4.00 | 2 | 10 |
| Haiku 4.5 | 0.10 | 1.25 | 2.00 | 1 | 5 |
| Fable 5.1 | 0.25 | 12.50 | 20.00 | 10 | 50 |

The pattern behind these numbers:
- A 5-minute write costs 1.25× normal input and a 1-hour write costs 2×.
- A read costs 0.1× normal input, except on Opus 5.5 (0.05×) and Fable 5.1 (0.025×).
- **Opus 5.5 and Sonnet 5 read the cache at the same price.** Moving a cached conversation from Opus to Sonnet saves nothing on the cached part.

Rules that matter:

| Rule | Consequence |
|---|---|
| The cache matches the **start** of the prompt exactly | Anything added at the end is cheap. A change earlier on re-writes everything after it |
| Each model has its **own** cache | A model switch re-writes the whole conversation |
| In Claude Code, an effort change keeps the cache **only on Opus 5.5 and Fable 5.1** with an API key or subscription (not on Bedrock, Google Cloud or a Claude apps gateway, nor with `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` or a HIPAA setup) | Elsewhere, changing effort re-writes the conversation too |
| The cache lives **5 minutes or 1 hour** after the **start** of the last request that read or wrote it, and every read restarts the clock | A break longer than that means the next message re-writes everything |
| Claude Code's main conversation uses the **1-hour cache on a subscription** (within the plan's usage) and the **5-minute cache on an API key, usage credits or a cloud provider**, unless changed with `promptCacheTtl` or `CLAUDE_CODE_PROMPT_CACHE_TTL`. Subagents, compaction and titles use 5 minutes | Read which one a session uses from its usage data (§7) |
| Sessions running **in parallel in the same folder** share the cached tool list and system prompt; a session started later shares it only if its git status snapshot matches | A model that another open session is already using often has ~35–50k tokens cached for you |
| Tokenizers differ: Opus 5.5 and Sonnet 5 count **~30% more tokens** than Haiku 4.5 for the same text | The same conversation is ~0.77× the tokens on Haiku (measured 0.758 in a real run) |

---

## 2. Notation

| Symbol | Meaning |
|---|---|
| **E** | Current setup: a model and effort |
| **L** | The setup you're considering |
| **N** | Tokens in the next prompt (≈ the whole conversation), counted in that model's tokens |
| **W** | Part of it already cached for E: the previous message's prompt, if E's cache is still alive |
| **S** | Part of it already cached for L: usually just the tool list and system prompt, if some session used L within its cache lifetime |
| **read(M), write(M), out(M)** | Model M's prices per token (divide the table by 1,000,000). Use the 5-minute or 1-hour write price to match the session |
| **output(M)** | Expected output tokens per message on M, thinking included. Learn it from the session's history |

---

## 3. The formulas

**Cost of one message on model M, with `cached` tokens already in its cache:**

```
cost(M) = cached × read(M)  +  (N − cached) × write(M)  +  output(M) × out(M)
```

**Next message, if you change nothing:**
```
next_now  = cost(E) with cached = W      (cache alive)
next_cold = cost(E) with cached = 0      (after a break; or cached = S if another session keeps the tool list warm)
```

**Changing from E to L:**
```
STAY = cost(E) with cached = W
MOVE = cost(L) with cached = S
P    = MOVE − STAY          the one-time price of changing now (negative means changing is already cheaper)
s    = saving per later message, once both would be cached
     = cost(E) − cost(L), with each model's cached part = everything but the new bit
m    = P ÷ s                messages until the change pays back
```

**What usdash shows of these.** Only the part it can measure is shown without `≈`: re-sending the conversation as its last request sent it (**C**, §7), read back (`C × read`) or written (`C × write`). `next_now` and `next_cold` without the new message and the reply are its `stay` row; the same part of `P` (C written on L, less C read back on E) is a switch row's "now" amount. The new message's text and the reply go into the per-message `s` (≈, averages from the session's history) from the next message on. So usdash's "evens out after ≈m" can come up to one message later than `P ÷ s`, and its rent-or-buy switch (§5) a little later than the pure rule: the safe direction.

When E and L are the **same model with a different effort** on Opus 5.5 or Fable 5.1, the cache is kept, so `MOVE = STAY` on input. Only the output changes, so `P ≈ (output(L) − output(E)) × out(E)`, which is usually negative: an instant saving.

---

## 4. Worked examples

All examples: a 50,000-token conversation on **Opus 5.5 with its cache alive**, 5-minute cache, and 500 output tokens per message on any model. Real outputs differ by model and effort; usdash learns them per session.

### Example 1: Opus → Sonnet while cached (don't switch yet)

| | Calculation | Cost |
|---|---|---|
| STAY (Opus) | 49,000 read × $0.20 + 1,000 write × $5.00 + 500 out × $20 | **$0.0248** |
| MOVE (Sonnet) | 50,000 write × $2.50 + 500 out × $10 | **$0.1300** |
| P | 0.1300 − 0.0248 | **$0.105** |
| Saving per later message | Both read 50k at $0.20, so only the new 1,000 tokens and the output get cheaper: 0.0250 − 0.0175 | **$0.0075** |
| Pays back after | 0.105 ÷ 0.0075 | **~14 messages** |

**Advice:** stay on Opus, or switch after your next break (see example 3). This matches a real run: switching at that point cost **$0.143** where staying would have cost about **$0.02**.

### Example 2: Opus → Haiku (it depends on what Haiku already has cached)

On Haiku the same conversation is ~38,500 tokens (0.77×).

| Case | MOVE (Haiku) | P | Advice |
|---|---|---|---|
| Another session keeps Claude Code's tool list (~30,000 Haiku tokens) cached on Haiku | 30,000 × $0.10 + 8,500 × $1.25 + 500 × $5 = **$0.0161** | **−$0.009** | Switch now: it's already cheaper |
| Nothing cached on Haiku | 38,500 × $1.25 + 500 × $5 = **$0.0506** | **+$0.026** | Pays back in ~1.5 messages (saving $0.0177 per message). Switch if you'll send 2+ more |

### Example 3: the cache has expired (6 minutes later, 5-minute cache)

| | Cost |
|---|---|
| STAY on Opus, everything re-written: 50,000 × $5.00 + 500 × $20 | $0.2600 |
| MOVE to Sonnet, everything re-written: 50,000 × $2.50 + 500 × $10 | $0.1300 |
| MOVE to Haiku: 38,500 × $1.25 + 500 × $5 | $0.0506 |

Usually, once the cache has expired, switching down costs nothing extra: you pay to re-write either way, and the cheaper model re-writes for less.

**But not always.** If another open session keeps Claude Code's tool list (say 40,000 tokens) cached on **Opus**, staying costs 40,000 × $0.20 + 10,000 × $5.00 + 500 × $20 = **$0.068**. That's cheaper than moving to Sonnet ($0.130). So never assume; always compute both sides with the right `cached` amounts.

---

## 5. When to switch: the rent-or-buy rule

The catch is that nobody knows how many more messages you'll send. The classic answer is the **rent-or-buy rule** (the "ski rental" problem): keep renting skis until the rent you've paid equals the purchase price, then buy.

Here, *renting* means staying on the pricier setup and paying the per-message extra `s`. *Buying* means paying the one-time `P` to switch.

```
before each message:
  if P ≤ 0:  switch   (it's already cheaper)
  if R ≥ P:  switch   (staying has now cost as much as switching)
  else:      stay, then R = R + (this message's cost on E − what it would have cost on L, with L already caching it)
R starts at 0 when the switch first becomes desirable, and resets after switching.
Recompute P each time: the conversation grows, and caches expire.
```

**Walk-through** (example 2, nothing cached on Haiku: P = $0.0258, s = $0.0177):

| Message | Extra paid by staying so far (R) | vs P | Decision |
|---|---|---|---|
| 1 | $0.0000 | < $0.0258 | stay |
| 2 | $0.0177 | < $0.0258 | stay |
| 3 | $0.0354 | ≥ $0.0258 | switch |

What it guarantees:
- **If you take a break**, the cache expires and switching becomes cheap.
  - After message 1: you paid $0.0177 extra, less than the $0.0258 an immediate switch would have cost.
  - After message 2 (the worst case): you paid $0.0354. That's more than $0.0258, but still less than P plus one message's extra.
- **If you keep going for a long time**, you paid R + P = $0.0612, where switching at message 1 would have cost $0.0258.
- **In general:** the rule never pays more than **twice the best choice in hindsight, plus one message's extra**. The overshoot comes from messages being whole steps: 2.37× in this example.
- This bound is exact only when P stays fixed. P grows as the conversation grows, so treat it as a good rule of thumb, not a proof.

**For a human, the same rule in one sentence:** "switching pays back after m messages; if you're unsure how long you'll keep going, continue and switch once you've sent m more, or right after your next break."

---

## 6. Other decisions

### Lower the effort (Opus 5.5 / Fable 5.1 only)
The cache is kept, so the saving is immediate: `(output_before − output_after) × out(E)`.
- **Example:** Opus 5.5 from ~3,000 to ~800 output tokens per message saves **$0.044 per message**, with no re-write.
- On other models, an effort change re-writes the conversation like a model switch; use the §3 formulas.

### Compact now or later
Compaction re-sends the whole conversation plus an instruction, and writes a summary. Right afterwards, the tool list and system prompt are still cached, but the summary is new.
- **Timing (140,000-token Opus conversation, ~3,000-token summary):**
  - with the cache alive: 140,000 × $0.20 + 3,000 × $20 = **$0.088** (usdash computes exactly this, with the summary's size learned from earlier compactions, §7; with none seen yet it assumes 3% of the conversation, 4,200 tokens here, so ≈$0.11);
  - after a break: 140,000 × $5.00 + $0.06 = **$0.76**. Claude Code sends compaction with the 5-minute cache even on a subscription, so this price applies either way;
  - **So: if you're going to compact, compact before you step away.**
- **Is it worth it, on cost alone?** (API key, 5-minute cache)
  - Extra cost: the compaction ($0.088), plus a first message that re-writes ~10,000 new tokens ($0.057), minus the message you'd have sent anyway ($0.033) = **$0.112**.
  - Saving: each later message reads ~45,000 instead of ~140,000 tokens, **$0.019 per message**.
  - Result: it pays back after **~6 messages**. You also lose detail from the conversation, which is a quality cost the numbers can't see.

### `/clear`
Starts a fresh conversation. The tool list and system prompt usually stay cached, so it's nearly free.
- **Saving per later message:** `(old size − new size) × read(E)`. For example, 120k → 35k on Opus saves **$0.017 per message**.
- The cost is losing all context, so only suggest it when the topic changes.

### A side task: subagent vs `/model`
- **A subagent** runs on its own separate conversation and cache; the main conversation's cache is untouched. It pays only for its own, much smaller, context.
- **`/model`** (or a skill whose frontmatter sets `model`) re-writes the whole main conversation on the new model. On the way back, the old model re-writes whatever its cache no longer covers: little if you return within its lifetime, everything after a break (see round trips below).
- **A skill with `context: fork`** runs in a forked subagent, and its frontmatter `model` then applies to that subagent only: the main conversation's cache is untouched.
- **Rule:** for a cheaper-model side task, use a subagent (or a forked skill).

### Switching back soon (round trips)
The old model's cache stays alive for its lifetime.
- **Coming back after just a turn or two:** the old model may reuse most of it (seen in a real run: 55.5k of 57.9k tokens reused 75 s later).
- **Coming back after many turns:** a request only looks back **20 content blocks** for an earlier cache entry (a run of tool calls counts as one). So only the tool list and system prompt are reused.
- **Planning:** budget a full re-write, and treat any reuse as a bonus.

### Long requests, long tool runs, and subagents
The cache clock starts when a request **starts**, and nothing during the request extends it. Anthropic's docs: *"if a response takes 4 minutes to stream, a follow-up request that reuses the same cached prefix must start within about 1 minute of that response completing."*
- **One API request that runs longer than the cache lifetime:**
  - Its cache entry expires *during* the run.
  - The request itself isn't affected: it read the cache when it started.
  - The next request misses and re-writes.
- **A Claude Code turn** (a prompt or a skill run) is many API requests: one per tool-loop step. Each one reads the cache and restarts the clock, so a 20-minute turn stays cached as long as each step starts within the lifetime of the previous step's start.
  - It expires mid-turn only if one step generates for longer than that, or a single tool (a long build or test run) runs longer than that between two steps.
- **Waiting on a subagent:**
  - The subagent refreshes its own cache, not the parent's.
  - A parent that only waits sends no requests, so its cache can expire during a long subagent run.
  - When the result comes back, the parent's next request re-writes its whole conversation.
- These cases are covered by usdash's tests (`tests/test_sessions.py`) and manual tests 22–26 (`tests/sanity/manual.py`).
- **After a turn ends:** the cache expires one lifetime after the **start of the turn's last request**. That's slightly less than 5 minutes after you see the answer, minus however long that answer took to generate.
- Claude Code's own `/model` warning treats the cache as warm for one lifetime after "Claude Code last sent a request in this conversation or Claude last responded". Counting from the response is a lenient estimate. Billing follows the API rule above, so usdash counts from request starts.

### 5-minute vs 1-hour cache (API key, usage credits, cloud providers; a subscription's main conversation already gets 1 hour)
The 1-hour cache makes every write cost 2× instead of 1.25× input. In return, breaks of 5–60 minutes don't force a re-write. Turn it on with `promptCacheTtl: "1h"` (or `CLAUDE_CODE_PROMPT_CACHE_TTL=1h`); on Bedrock, `ENABLE_PROMPT_CACHING_1H=1` also works, for the models that support it there.
- **X** = all tokens written in a session. **B** = tokens you'd have to re-write after 5–60 minute breaks on the 5-minute cache.
- The 1-hour cache is cheaper when:

```
B > X × 0.75 ÷ (1.25 − read/input)       → B > 0.652·X  (Sonnet 5, Haiku 4.5)
                                            B > 0.625·X  (Opus 5.5)
                                            B > 0.612·X  (Fable 5.1)
```
- **Rule of thumb:** one 5–60 minute break once the conversation is near its full size is enough for the 1-hour cache to win.

---

## 7. Getting the inputs from Claude Code's transcripts

| Needed | Where it comes from |
|---|---|
| Cost of each request | `message.usage` on assistant records: `input_tokens` (uncached part only) × input price + `cache_read_input_tokens` × read + `cache_creation.ephemeral_5m_input_tokens` × 5-min write + `ephemeral_1h_input_tokens` × 1-hour write + `output_tokens` × output price. A write without the 5m/1h split counts as 5-minute |
| One request, not several | A streamed reply is written as several records sharing a `message.id`; the last one written wins. Deduplicate by `message.id`, then `requestId`, then the record's `uuid`. **Not by `requestId` alone:** some sessions' records carry none, and one real session then read $0.04 instead of $1.33 |
| **N** (conversation size) | usdash shows the part it can measure: **C**, the last request's prompt (`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`). The whole of it is cached (on this machine the uncached remainder was at most 0.04% of a prompt), and resuming a session re-sends all of it (31 resumes: the first request after was 0.1–4% bigger, the new message). What the next message adds is only in per-message amounts, as the mean rise between the session's consecutive prompts on one model, over its last 20 messages. The mean, not the median: big tool results are a real cost, and the median undercounted the average about 2× here. Right after `/compact`: the tool list and system prompt plus the summary (`compactMetadata.postTokens` on the `compact_boundary` record) |
| Tool list and system prompt | Measured when the transcripts reveal it: the request right after `/compact` reads back exactly that (36,829 tokens in four sessions here), per folder and app; a read bigger than the session's first prompt (both in the same model's tokens) isn't the tool list (a forked or resumed conversation) and is ignored. Otherwise ≈ the smallest first prompt among this session and the other interactive sessions in its folder started from the same app (39.9k here): every first prompt also holds a first message, which may paste a whole file. A scripted run can bring a system prompt of its own, so it's compared with nothing else |
| The summary `/compact` writes | The median `postTokens` of earlier compactions of conversations within 2× the size; else 3% of the conversation, between 3,800 and 16,000 tokens. Seen here: 3,807 typical (~58k conversations), 13,984 at 450k, 16,088 at 972k. The compaction request itself isn't in the transcripts, so its price stays an estimate; the difference between compacting now and after a break (re-reading vs re-writing the conversation) is exact |
| **W** | The last request's full prompt size, if its cache is still alive |
| The session's cache lifetime | From its latest main-conversation write: 1 hour if it shows `ephemeral_1h_input_tokens > 0`, 5 minutes if `ephemeral_5m_input_tokens > 0` |
| Effort | The `effort` field on each assistant record (`low` … `max`) |
| Is the cache alive? | Time since the **start** of the last request is less than the lifetime. Use the timestamp of the record that triggered it (the user message or tool result before it), which errs on the safe side. Recaps (`system` records with subtype `away_summary`) read the cache too, so they count as a refresh. Their timestamp is a few seconds after the request started, so subtract a few seconds to stay safe |
| **S** for another model | Whether any session in the same folder used that model within its lifetime, as its current model or one it left less than a lifetime ago. This session counts too: it may have just left that model. Size ≈ this session's tool list and system prompt (above), but for another session at most its own **first** prompt, which a scripted run with a system prompt of its own can keep small; the best match among those sessions is the estimate. Converted to the model's tokenizer. Not a first prompt *on that model*: after a mid-session `/model` switch, that's the whole conversation. Not this session's whole conversation on a model it just left either: how much of it a request finds depends on how far back it is (§6, round trips). Only a live session's switch rows subtract S (their amount then gets `≈`); an idle session's line doesn't, since a resumed session gets a fresh system prompt (git status, date) that may not match |
| output(M) | For the current model: the session's own average output per message on that model and effort, else on that model, else across interactive sessions (scripted `claude -p` runs are left out: a "say OK" run makes a model or effort look nearly free). For a model you'd move to: the session's own average on it if it has used it, else the current model's, converted to the target's tokenizer: the task is the same, so assume a reply of the same length. Other sessions' replies on that model come from other tasks and would mislead. For a lower effort: the session's own average at that effort if it has used it; else its current average, scaled by how much shorter replies got at that effort in the interactive sessions that used both (each compares a task with itself). Without such a session there's no estimate: another session's replies alone come from another task |
| When a request started | The timestamp of the record it answers, found through `parentUuid`, skipping earlier blocks of the same reply |
| Claude Code's own total | The `cost-state` record (`totalCostUSD`, per-model `modelUsage`). It's written when a session closes, not as it goes, so it can only check finished sessions. It includes the background requests the transcript lacks |
| Converting N between models | × 0.77 from Opus 5.5 / Sonnet 5 to Haiku 4.5, × 1.3 the other way. Opus 5.5 and Sonnet 5 need no conversion |
| Effort changes | On Opus 5.5 the request after an effort change read 100% of the previous prompt back (4 of 4 here); on Opus 5, 7% |

---

## 8. Checked against a real run

A scripted Claude Code session through llm-trunk, on an API key with the 5-minute cache. The input side of P for three real moves; output differences would push each a little further toward moving:

| Real move | P (input side) from the formula | Logged reality | Right call |
|---|---|---|---|
| Opus → Sonnet, 55.9k tokens, nothing cached on Sonnet | +$0.126 | Moving cost $0.143; staying ≈ $0.02 | Stay |
| Sonnet → Haiku, Haiku had 36.8k of 43.5k cached | −$0.001 | Moving cost $0.013 | Move |
| Opus → Haiku right after `/compact` | −$0.035 | Moving cost $0.012 | Move |

---

## 9. Assumptions and limits

1. **Output per message is a guess from history.** A model or effort change also changes how much the model writes and thinks, and that's often the biggest part of the saving. Show it as an estimate.
2. **Re-pricing a logged message at L** (to update R) is an estimate:
   - Convert its token counts first (×0.77 to Haiku), or the saving is undercounted.
   - It still can't know that a cheaper model or lower effort would have thought less. That also undercounts the saving, so the rule stays a bit longer than ideal: the safe direction.
3. **The ×0.77 conversion is an average.** Code, prose and other languages differ.
4. **The tool list and system prompt are only shared when the prompts are identical**: same folder, same Claude Code version, and for sessions started later, the same git status snapshot.
5. **The rent-or-buy guarantee** holds for a fixed P; see §5.
6. **Background requests** (session titles, prompt suggestions, and `/compact`'s own summarising request) don't appear in transcripts, so costs read low. Measured against Claude Code's own `cost-state` totals on this machine: 6–18% low for most sessions, 35% for one long session with many subagents, a gap not yet explained.
7. **Prices change.** Keep them in one file with a "verified on" date.
8. **Cloud prices differ.** On Bedrock and Google Cloud, regional and multi-region endpoints cost 10% more than global ones (Claude 4.5 models and later), and Bedrock's flex and priority service tiers are priced differently again. The tables here are Anthropic's list prices, which match the global endpoints.
9. **Cost is not the only goal.** A stronger model or higher effort can finish a task in fewer messages. Advice should show the dollars, and the user decides.
10. **Exact and ≈ on screen.** Exact: re-sending C now or after a break, on any model (Haiku through the ×0.77 conversion). ≈: per-message amounts (1), `/compact`'s absolute price (its summary is learned; the request itself isn't logged), anything that subtracts another session's cached tool list, and the conversation right after `/compact` (tool list + summary until the next request).
11. **Resuming soon after `/exit`.** Within a cache lifetime, a resumed session may still read its cache: every resume seen here came 92+ minutes later and read none of its conversation back. The idle line shows the full re-send, the most it can cost, until manual check 27 settles it.

---

## 10. The advice engine, in short

What usdash does, per session in the last 24 hours (`usdash/engine.py`, `usdash/advice.py`):

```
lifetime = from the session's latest main-conversation write: 1 hour or 5 minutes
alive    = now − start_of_last_request < lifetime
C        = the last request's prompt (exact); right after /compact, tool list + summary (≈)
live     = alive and not closed; idle = everything else

idle session: "resuming re-sends Ck tokens: $a on E, $b on L1, $c on L2"   (C written again, on E and each
              cheaper model, no S subtracted; "continuing" if not closed; closed script runs fold
              into one row per folder)

live session: one row per option
    stay       "re-sends Ck tokens: $X now, $Y after a break"            (C read back / written again)
    ↑ / ↓ L    "$Z more (less) now, then ≈$s less (more) a message · evens out after ≈m messages"
               Z = C on L, written − C read back here (≈ if another session keeps L's tool list cached)
    /compact   "≈$A now, ≈$B after a break; then ≈$c less a message"      (summary size learned; shown
               when not compacted since the last request and c ≥ $0.005)
    /effort    "costs nothing now" on Opus 5.5 / Fable 5.1 ("/effort X: costs nothing now, then ≈$s less a
               message" once a lower effort's saving is known), else "re-sends Ck tokens: $W more now"
    (no amounts if the session's model has no price)

and one action, the first that applies:
    ⚡ C ≥ 100k, not compacted since the last request, c ≥ $0.005, and at most min(10 min, half the lifetime) left:
         "Taking a break? /compact first: ≈$A now, ≈$B once the cache expires in m:ss."
    (below, "a cheaper L" is one that saves $0.005+ a message; if several qualify, the largest saving)
    ⚡ a cheaper L with Z ≤ 0:   "Switch to L now: it's already cheaper."
    💡 a cheaper L with R ≥ Z:   "Switch to L now: it would have saved $R by now; switching costs $Z."
                                 (R counts from when switching to L first saved $0.005+ a message)
    💡 a lower effort saves $0.005+ a message (Opus 5.5 / Fable 5.1, with replies at that effort):
                                 "Try /effort X: ≈$s less a message, at no cost now."
    💡 a cheaper L saves $0.005+ a message:
                                 "Stay on E for now; switching is free after your next 1-hour (5-minute) break."
```

/clear and "use a subagent instead of /model" are not in usdash yet.

---

## Sources
- Pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Prompt caching (API): https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- How Claude Code uses prompt caching: https://code.claude.com/docs/en/prompt-caching
- Claude Code on Amazon Bedrock: https://code.claude.com/docs/en/amazon-bedrock
- Prompt caching on Amazon Bedrock: https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html
- Real-run data: llm-trunk `scenarios/results/20260926-155134-dev-day.json` and `20260926-160246-dev-day.json`
