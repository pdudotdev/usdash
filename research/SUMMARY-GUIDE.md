# Token costs: how to calculate and read them

A short field card for Claude and Claude Code. Prices were checked on **2026-10-01** against Anthropic's pricing page and cross-checked against the Claude API reference on 2026-10-03. Prices change, so re-check them before using these numbers for anything that matters. The principles don't change.

All prices are **USD per million tokens (MTok)**. `p` is the model's base input price.

---

## 1. The method

You can answer any cost question by working through these steps:

1. **List every request.** Count each tool step, retry and subagent. Count the client's own background requests too (§9).
2. **Split each request's input into three buckets: read, written, plain** (§4).
3. **Add the output.** Thinking counts as output and is billed in full, even when it's hidden.
4. **Price it** with the formula in §3.
5. **Compare options over a horizon.** Some costs are paid once and others on every request. Pay-back = one-time cost ÷ saving per request (§6).
6. **Check against the usage fields** of a real request (§3). They are the ground truth.

If a number surprises you, look for something that changed early in the prompt, a gap longer than the cache lifetime, or a request you didn't see.

---

## 2. Prices

| Model | Input `p` | 5-min write | 1-hour write | Cache read | Output | `r` | Min. cacheable |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fable 5.1, Mythos 5.1 | 10.00 | 12.50 | 20.00 | 0.25 | 50.00 | 0.025 | 512 |
| Fable 5, Mythos 5 | 10.00 | 12.50 | 20.00 | 1.00 | 50.00 | 0.1 | 512 |
| **Opus 5.5** | **4.00** | **5.00** | **8.00** | **0.20** | **20.00** | **0.05** | **512** |
| Opus 5 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 | 0.1 | 512 |
| Opus 4.8 / 4.7 / 4.6 / 4.5 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 | 0.1 | 1,024 / 2,048 / 4,096 / 4,096 |
| **Sonnet 5.5** | **2.00** | **2.50** | **4.00** | **0.20** | **10.00** | **0.1** | **512** |
| Sonnet 5 | 2.00 | 2.50 | 4.00 | 0.20 | 10.00 | 0.1 | 1,024 |
| Sonnet 4.6, 4.5 | 3.00 | 3.75 | 6.00 | 0.30 | 15.00 | 0.1 | 1,024 |
| Haiku 4.5 | 1.00 | 1.25 | 2.00 | 0.10 | 5.00 | 0.1 | 4,096 |

Mythos models are limited availability.

**Everything is a multiple of `p`:** a 5-minute cache write costs 1.25×, a 1-hour write 2×, a cache read `r`× and output 5×. If you remember one number per model, make it `p`.

**Two things that catch people out:**
- **Opus 5.5 and Sonnet 5.5 (and Sonnet 5) read the cache at the same $0.20.** Moving a warm conversation from Opus to Sonnet saves nothing on the cached part.
- **Tokenizers differ.** Opus 4.7 and later, Sonnet 5 and later, and Fable count about **1.3×** (up to 1.35×) as many tokens for the same text as Haiku 4.5, Sonnet 4.6 and Opus 4.6. So don't compare price per token across tokenizers: compare the cost of the same text, and recount tokens on the target model.

**Limits:** the context window is 1M tokens (200k on Haiku 4.5) and output is capped at 128k (64k on Haiku 4.5). On Claude 4.6 and later there's no long-context premium.

---

## 3. The formula and the usage record

### Cost of one request

```
cost = p × ( plain + 1.25·w5m + 2·w1h + r·read + 5·out ) / 1,000,000
         × batch × fast × geo × contract
     + $0.01 × web_searches
```

| Modifier | Value | When it applies |
|---|---|---|
| `batch` | 0.5 | Batch API. Results arrive within 24 hours. Caching works, best-effort |
| `fast` | 2 | Fast mode, on Opus 5.5 ($8/$40), Opus 5 and Opus 4.8 ($10/$50) only. Claude API only, and not with Batch. Cache multiples apply on top of the fast price |
| `geo` | 1.1 | `inference_geo: "us"` (Claude 4.6 and later). Bedrock or Google Cloud regional and multi-region endpoints are also +10% over global (Claude 4.5 and later). Those platforms publish their own price sheets |
| `contract` | your rate | Negotiated discount |

All the modifiers multiply each other; none of them add. Web search is added after the multipliers. Web fetch costs only the tokens it fetches. Code execution is free alongside web search or web fetch; otherwise it's billed by container time.

### Reading a usage record

Every response's `usage` already splits the input into buckets. Map each field to a variable in the formula:

| Field | Formula variable | Note |
|---|---|---|
| `input_tokens` | `plain` | Only what comes after the last breakpoint. **This isn't the prompt size** |
| `cache_read_input_tokens` | `read` | |
| `cache_creation.ephemeral_5m_input_tokens` | `w5m` | `cache_creation_input_tokens` is the 5-minute and 1-hour writes added together |
| `cache_creation.ephemeral_1h_input_tokens` | `w1h` | |
| `output_tokens` | `out` | Includes thinking. `output_tokens_details.thinking_tokens` says how much of it was thinking |
| `server_tool_use.web_search_requests` | `web_searches` | |
| `speed` / `inference_geo` | `fast` / `geo` | `"fast"` → ×2, `"us"` → ×1.1 |

```
prompt size = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
hit rate    = cache_read_input_tokens ÷ prompt size
```

**Worked example.** This is the first request of a real Claude Code session on Opus 5.5 with the 1-hour cache:

| Field | Tokens | × price/MTok | Cost |
|---|---:|---:|---:|
| `input_tokens` | 2 | $4 | $0.000008 |
| `cache_read_input_tokens` | 23,167 | $0.20 | $0.004633 |
| `ephemeral_1h_input_tokens` | 11,977 | $8 | $0.095816 |
| `output_tokens` | 121 | $20 | $0.002420 |
| **Total** | 35,146 in | | **$0.102877** |

The hit rate is 66%. The 23,167 tokens it read back are Claude Code's tool list, which another session had already cached.

---

## 4. Which tokens are read, written or plain

A request is always laid out in this order: **tools → system → messages**. The cache stores *prefixes* of that sequence, and it stores them only at breakpoints (`cache_control`, at most 4 per request). Claude Code puts its breakpoints at the end of the tool list (about 23k tokens), the end of the system prompt (about 25k in total) and the latest message.

```
┌──────────────┬──────────────┬──────────────────────────────────────────┐
│ tools  ~23k  │ system  ~2k  │ messages (grows every turn)              │
└──────────────┴──────────────┴──────────────────────────────────────────┘
               ▲ breakpoint   ▲ breakpoint                    breakpoint ▲
   A change anywhere re-processes everything to its right.
```

To classify a request's input, walk it from the start:

```
READ    = the longest prefix ending at a cache entry that
            • is on the same model and in the same workspace
            • matches byte for byte up to that point
            • is within 20 blocks of one of this request's breakpoints
            • was read or written less than one lifetime ago (counted from that request's START)
            • belongs to a request whose response had already started
WRITTEN = everything from the end of READ to this request's last breakpoint   → 1.25p or 2p
PLAIN   = everything after the last breakpoint                                → p
```

Consequences:
- **A read always stops at a breakpoint.** A change in the middle of the messages reads back to the previous breakpoint before the change, not to the byte just before it.
- **Below the minimum size, nothing is cached.** Both cache counts read 0 and no error says so.
- **A block** is one text, image, tool call or tool result. A run of parallel tool calls counts as one block, and so does the run of their results.
- **Parallel requests sent at the same moment all write.** Send one, then send the rest once its response has started.
- **Each read restarts the lifetime clock, from the start of the request.** A 4-minute reply on a 5-minute cache leaves about 1 minute.
- **The lifetime is a minimum.** Entries are deleted promptly after it, but not instantly. Plan on the minimum.
- **The start is shared.** Other sessions on the same model, client version and workspace keep the tool list (and often the system prompt) warm. So after an expiry, the tool list is usually still read back.

### What a change resets

| Change | Tools | System | Messages |
|---|:---:|:---:|:---:|
| Tool definitions added, removed or edited | ✘ | ✘ | ✘ |
| A different model (each model has its own cache) | ✘ | ✘ | ✘ |
| Web search or citations toggled; fast mode switched (API) | ✓ | ✘ | ✘ |
| System prompt content | ✓ | ✘ | ✘ |
| `tool_choice`; images added or removed | ✓ | ✓ | ✘ |
| Thinking or effort changed on the request | ✘ on some models | ✘ on some models | ✘ |
| Messages, tool calls and tool results appended | ✓ | ✓ | ✓ |
| More time passed than the lifetime | ✘ | ✘ | ✘ |

✓ = still read from the cache, ✘ = written again.

### Cache lifetime in Claude Code

```
Main conversation, on a subscription within plan usage  →  1 hour
API key, usage credits or a cloud provider              →  5 minutes   (set it with promptCacheTtl / CLAUDE_CODE_PROMPT_CACHE_TTL)
Subagents, compaction, titles                           →  5 minutes   (set it with subagentPromptCacheTtl / CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL)
```

Going past your plan onto usage credits also drops the main conversation to the 5-minute cache.

---

## 5. What the next request costs (Claude Code)

```
What happened since the last request?
│
├─ Nothing special, within the lifetime ── read everything; write only the new message
│
├─ A gap longer than the lifetime ──────── read the tool list (+ system) if another session kept it warm;
│                                           write the rest of the conversation.
│                                           A recap sent inside the lifetime may have kept it warm (§9)
│
├─ Model switch ─────────────────────────── same as an expired gap, but against the NEW model's cache
│                                           (its tool list is often warm from other sessions)
│
├─ Effort change
│   ├─ Opus 5.5, Sonnet 5.5, Fable 5.1, with an API key or subscription ── cache kept
│   │                                       (Claude Code sends the change per message)
│   └─ anything else (e.g. Opus 5, Bedrock, Google Cloud) ── treat it as a model switch
│
├─ Fast mode ────────────────────────────── first time it's turned on in this conversation:
│                                           write everything again at fast prices.
│                                           Later toggles keep the cache
│
├─ Tools changed ────────────────────────── write everything again. MCP tools are deferred by default,
│                                           so connecting a server usually doesn't change the tool list
│
├─ /compact ─────────────────────────────── a summarising request (unlogged, not cached):
│     warm: read what the latest turn's first request cached; the rest is plain input
│     cold: read the tool list; the rest is plain input
│     output (the summary): about 1k–4k tokens
│     The next request is about the size of the session's first prompt + the summary,
│     and is written (only the shared start is read)
│
├─ /clear ───────────────────────────────── free; the tool list and system prompt usually stay cached
├─ /rewind ──────────────────────────────── that turn's prefix is read if it's still within the lifetime;
│                                           after many turns, budget for writing it again
├─ --resume ─────────────────────────────── within the lifetime it usually reads everything back (8 of 10 in tests);
│                                           after it, it writes everything again
├─ Edited CLAUDE.md ─────────────────────── no effect until /clear, /compact or a restart
├─ Upgraded Claude Code ─────────────────── breaks the cache only if the tool list or system prompt changed
│
└─ Waited for a subagent ────────────────── the subagent has its own (5-minute) cache. The parent's
                                            clock keeps running while it waits, so it can expire
                                            (a true fork reads the parent's cache; check that yours is one)
```

---

## 6. Decisions

### Does caching pay?

```
reads to break even = (write multiplier − 1) ÷ (1 − r)
```

| | 5-minute | 1-hour |
|---|---:|---:|
| r = 0.1 | 0.28 → pays from the **1st** read | 1.11 → pays from the **2nd** read |
| Opus 5.5 (r = 0.05) | 0.26 | 1.05 |
| Fable 5.1 (r = 0.025) | 0.26 | 1.03 |

### 5-minute or 1-hour cache?

The 1-hour cache costs an extra `0.75p` on every token written. The 5-minute cache costs `(1.25 − r)p` extra on every token it has to write again after a pause.

```
The 1-hour cache wins when
    tokens rewritten after 5–60 min pauses  >  0.75 ÷ (1.25 − r)  ×  all tokens written
                                               (0.65 most models · 0.625 Opus 5.5 · 0.61 Fable 5.1)
```

| Typical gap between requests | Choose |
|---|---|
| Under 5 minutes | **5-minute.** Every request refreshes it, so the 1-hour price buys nothing |
| 5–60 minutes | **1-hour.** One such pause once the context is near full size is usually enough to pay for it |
| Over an hour | Neither helps. Expect a full re-write |

With the API on Fable 5.1, a `max_tokens: 0` keep-alive sent every 4–5 minutes usually beats the 1-hour cache.

### Is a switch, compaction or reset worth it?

```
P = extra cost now (versus not changing)
s = saving on each later request
m = P ÷ s = requests until it pays back        → do it if more than m requests are left
```

- **When you don't know how many requests are left:** keep paying the extra per request until it adds up to P, then switch. This never costs more than 2× what hindsight would have chosen, plus one request.
- **After a break**, P usually drops to zero or below, because the cache is gone either way. Switch or compact *before* you resume work.
- **Compact before a break, not after it.** A warm compaction reads most of the conversation from the cache; a cold one sends it all at the plain input price.

### Cost per outcome, not per token

A cheaper model or a lower effort that needs more steps or retries can cost more for the same result. Compare: tokens in and out × price × attempts, per finished result.

---

## 7. Agent turns

One prompt sent to an agent is **one request per tool step**, and every step re-sends the whole context.

```
turn ≈ steps × context × read price  +  new tokens × write price  +  output × output price
```

In a warm agent loop, writing the new tokens costs about as much as re-reading the context. So large tool results are paid twice: once when they're written, then again as reads on every later step. Keep tool output small: filter logs before the model sees them, or let a subagent read the big file and return a summary.

---

## 8. Diagnosing a surprising cost

```
Cost higher than expected?
│
├─ Hit rate low (read ÷ prompt size)?
│   ├─ written ≈ whole conversation ─── expiry (gap, slow reply, long subagent), model switch,
│   │                                   effort change, tool change, resume after the lifetime
│   ├─ read stuck at the tool list size ── the conversation's entry was lost; the shared start survived
│   ├─ read = 0 and written = 0 ──────── prefix below the minimum size, or no breakpoints
│   ├─ every request writes it all again with the same payload ── >20 blocks per turn (lookback);
│   │                                   or something early changes each time (timestamp, unsorted JSON)
│   └─ parallel requests all writing ── none had started streaming when the others were sent
│
├─ Hit rate fine, output share high ─── effort, thinking or verbose replies are the lever
│
├─ Hit rate fine, context just large ── big tool results; /compact or /clear
│
└─ Bill > your logs ─────────────────── unlogged requests (§9)
```

| Metric | Formula | Healthy signal |
|---|---|---|
| Hit rate | read ÷ prompt size | Above ~80% in a conversational workload |
| Output share | output cost ÷ total cost | If high, work on effort and replies, not caching |
| Effective input price | input-side cost ÷ prompt tokens | Close to the cache-read price |
| Unlogged share | 1 − logged cost ÷ billed cost | Know it before you trust your logs |

---

## 9. What the logs don't show

Claude Code makes requests you never typed, and many of them never reach its transcripts:

| Request | Model | Effect |
|---|---|---|
| Recap written while you're away | The session's | Re-sends the conversation: restarts the clock, or writes it all again if the cache had expired |
| Prompt suggestions | The session's | Read the conversation back |
| `/compact`'s summarising request | The session's | See §5 |
| Session title | Haiku 4.5 | Small (about 900 tokens in). Doesn't touch your conversation's cache |
| WebSearch | Haiku 4.5 | Its own request + $10 per 1,000 searches |
| `/btw` side questions, Desktop app requests | | Missing from the transcripts |

- In real sessions the transcripts held **41–100%** of Claude Code's own totals (a median of 89%, 84% weighted by cost), and only **28%** in a session with four web searches. Where both counted the same tokens, the costs matched to the micro-dollar.
- **Reconcile against** the provider's usage report, Claude Code's own total (`/usage`, or the `cost-state` record written at exit) or an OpenTelemetry export, not against transcripts alone.

---

## Reference numbers

These are for Opus 5.5 with the 1-hour cache, a 100k-token conversation, 2k new tokens and 1k output per message:

| Situation | Cost |
|---|---:|
| Warm message | $0.056 |
| Message after the cache expired (25k start still cached elsewhere) | $0.64 |
| The same, with nothing cached | $0.84 |
| Switch to Sonnet 5.5 while warm | P = $0.38, s = $0.018 → pays back after **21** messages |
| `/compact` while warm (4k summary) | $0.11, pays back after about **18** messages |
| `/compact` after the cache expired | $0.36 |
| Lunch break after compacting first | $0.15 instead of $0.64 |
| One prompt that runs 8 tool steps (3k result + 300 output each) | $0.42 |
| 20 messages from a fresh start (35k → 73k): no cache vs 1-hour cache | $4.72 vs $0.99 |
