# AI tokenomics: a field guide

How the cost of using a large language model is built, why it moves, and how to work out any new case from a few principles. Written around Claude and Claude Code, where every number here was checked. The principles carry over to any provider that bills by the token and caches prompts.

**Checked on 2026-10-01** against Anthropic's docs (sources at the end) and against real Claude Code sessions (2.1.278–2.1.286) measured while building usdash (Appendix B). Prices change; the principles don't. Appendix A says where to re-check the numbers.

## Contents

- [How to use this guide](#how-to-use-this-guide)
- [Part 1: The mental model](#part-1-the-mental-model): [1. Ten principles](#1-ten-principles) · [2. What a token is](#2-what-a-token-is-and-what-you-pay-for) · [3. The cost equation](#3-the-cost-equation) · [4. Reading a usage record](#4-reading-a-usage-record)
- [Part 2: Prompt caching](#part-2-prompt-caching-the-biggest-lever): [5. How the cache works](#5-how-the-cache-works) · [6. When caching pays](#6-when-caching-pays) · [7. The cache lifetime](#7-the-cache-lifetime) · [8. What breaks the cache](#8-what-breaks-the-cache) · [9. Caching and rate limits](#9-caching-and-rate-limits)
- [Part 3: The other levers](#part-3-the-other-levers): [10. Model choice](#10-model-choice-and-tokenizers) · [11. Output, thinking and effort](#11-output-thinking-and-effort) · [12. Batch, fast mode, data residency](#12-batch-fast-mode-data-residency-cloud-endpoints) · [13. Tools](#13-tools-and-server-tools)
- [Part 4: Conversations and agents](#part-4-conversations-and-agents): [14. An agent turn](#14-anatomy-of-an-agent-turn) · [15. Price now, saving later](#15-decisions-with-a-price-now-and-a-saving-later) · [16. /compact, /clear, /rewind](#16-compact-clear-and-rewind) · [17. Breaks, resumes, subagents](#17-breaks-resumes-subagents-and-parallel-requests) · [18. Costs you don't see](#18-costs-you-dont-see)
- [Part 5: FinOps practice](#part-5-finops-practice): [19. Unit economics](#19-unit-economics-and-forecasting) · [20. Metrics](#20-metrics-to-watch) · [21. Governance](#21-governance-levers)
- [Part 6: Solving any new question](#part-6-solving-any-new-question): [22. The method](#22-the-method) · [23. Practice problems](#23-practice-problems)
- Appendices: [A. Price sheet](#appendix-a-price-sheet) · [B. Evidence](#appendix-b-evidence-from-real-sessions) · [C. Glossary](#appendix-c-glossary) · [D. Sources](#appendix-d-sources)

## How to use this guide

You don't need to memorise cases. Almost every question ("is this switch worth it?", "why did yesterday cost double?", "should we batch this?") comes down to four questions:

1. **Which requests will actually be sent?**
2. **For each request, which tokens are read from the cache, which are written, which are plain input, and how many come out?**
3. **What does each kind of token cost, after modifiers?**
4. **Over what horizon do I compare the options?**

Part 1 gives the mental model: ten principles, the cost equation, and how to read what a request reports. Parts 2 and 3 cover the levers. Part 4 applies them to conversations and agents, where the surprises live. Part 5 turns them into FinOps practice, and Part 6 is the method, with practice problems.

Three things run through the guide:
- **Principle tags.** (P4) means "this follows from principle 4". Every rule and case is one of the ten principles applied.
- **The running session.** One Claude Code session on Opus 5.5, introduced in §3, comes back in each part: its first message, a warm message, a lunch break, a model switch, a compaction, an agent turn. Each step changes one thing.
- **On the wire.** Real usage records from Claude Code's transcripts, for the same situations, so you see the numbers the API actually reports.

---

## Part 1: The mental model

### 1. Ten principles

These are the whole subject on one page. Everything later is one of them applied.

1. **You pay for tokens moved, in and out.** Everything the model reads is input: instructions, tool definitions, the whole conversation, files, tool results, images. Everything it writes is output, including thinking you never see.
2. **The model remembers nothing.** Every request re-sends the entire context. The main cost driver is *context size × number of requests*, not the length of your last message.
3. **Know one number per model: its input price.** Everything else is a fixed multiple of it. Output costs 5×, a cache write 1.25× (5-minute) or 2× (1-hour), and a cache read 0.1×, or less on some models.
4. **The cache is a prefix, in layers, and shared.** It serves only an exact byte-for-byte match from the start of the request (tools, then system prompt, then messages), on the same model, within its lifetime. Change anything early and everything after it is paid for again. The early layers are the same in many requests, so other sessions keep them cached: a miss usually still reads the start back.
5. **Time is money.** The cache expires a fixed time after the start of the last request that read or wrote it: any request, including ones you don't see. A pause longer than that turns the next cheap request into an expensive one.
6. **Every change has a price now and a saving later.** Switching models, compacting, changing the cache lifetime: pay-back = price now ÷ saving per later request. Whether it's worth it depends on how many requests are left.
7. **Discounts multiply.** Batch, cache, data residency and fast mode are multipliers on each other, not additions.
8. **What you can't see still costs.** Clients send requests you never typed: titles, summaries, suggestions, sub-requests for tools. Reconcile against the bill, not only against your logs.
9. **Optimise cost per outcome, not per token.** A cheaper model or lower effort that needs more steps, or more retries, can cost more for the same result.
10. **The client builds the request.** What it puts in, in what order, and when, decides what's cached. Where the API allows several behaviours, check what your client does.

### 2. What a token is, and what you pay for

A **token** is the unit a model reads and writes: roughly 4 characters or ¾ of a word of English text. Code, other languages and unusual formatting take more tokens per character.

**The tokenizer belongs to the model.** Claude Opus 4.7 and later models (including Opus 5.5, Sonnet 5.5, Sonnet 5 and Fable 5.1) use a newer tokenizer that produces about 30% more tokens for the same text than earlier models (Haiku 4.5, Sonnet 4.6, Opus 4.6 and before). So the same conversation is about 0.74–0.77× the tokens on an older model. That means:
- **A price per token isn't comparable across tokenizers.** Compare the cost of the same text.
- **Token counts don't transfer.** A count measured on one model is wrong on the other; recount with the target model.

**What counts as input**, all of it, on every request (P1, P2):
- The tool definitions, plus a small system prompt the API adds when tools are present (about 300–700 tokens, depending on the model)
- The system prompt
- Every earlier message: yours, the model's replies, tool calls and tool results, images and documents
- On Opus 4.5 and later, Sonnet 4.6 and later, and the Fable models, the model's earlier thinking blocks, which stay in the conversation and are re-sent as input (Haiku models drop them)

**What counts as output:** the reply, tool calls, and **thinking**. Thinking is billed in full even when the API only shows you a summary of it.

**The context window** is the most one request can hold: input plus output. It's 1M tokens on the current Opus, Sonnet and Fable models (200k on Haiku 4.5), with up to 128k output (64k on Haiku 4.5). On Claude 4.6 and later models, a 900k-token request costs the same per token as a 9k-token one: no long-context premium.

### 3. The cost equation

One request costs:

```
cost = ( input  × (plain input tokens)
       + 1.25 × input × (tokens written to the 5-minute cache)
       + 2    × input × (tokens written to the 1-hour cache)
       + r    × input × (tokens read from the cache)
       + 5    × input × (output tokens, thinking included) )
       × modifiers (batch 0.5 · US-only inference 1.1 · fast mode · contract discount)
     + per-use fees (web search: $10 per 1,000 searches)
```

Here `input` is the model's base input price, and `r` is its cache-read multiplier: 0.1 on most models, 0.05 on Opus 5.5, 0.025 on Fable 5.1. The modifiers multiply token prices; per-use fees are added on top.

**The price ladder**, per million tokens (Appendix A has every model):

| Model | Cache read | Input | 5-min write | 1-hour write | Output |
|---|---|---|---|---|---|
| Fable 5.1 | $0.25 | $10 | $12.50 | $20 | $50 |
| Opus 5.5 | $0.20 | $4 | $5 | $8 | $20 |
| Sonnet 5.5 | $0.20 | $2 | $2.50 | $4 | $10 |
| Haiku 4.5 | $0.10 | $1 | $1.25 | $2 | $5 |

Read it left to right and the whole game is visible. Reading a token from the cache is 12 to 80 times cheaper than writing it, depending on the model and the lifetime. Output is the most expensive token there is. Two things stand out:
- **Opus 5.5 and Sonnet 5.5 read the cache at the same price** (so does Sonnet 5). Moving a large cached conversation from Opus 5.5 to Sonnet 5.5 saves nothing on its cached part.
- **Fable 5.1 reads at almost Opus 5.5's price**, despite costing 2.5× more for everything else.

**The running session.** This is the session the guide follows: Claude Code on Opus 5.5, with the 1-hour cache a subscription gets.
- **The start of every request** is the same 25k tokens: Claude Code's tool list (about 23k) and its system prompt (about 2k).
- **Your first message** adds 10k: what you type, plus what Claude Code attaches to a session's first prompt (the environment, the lists of skills and agents).
- **Each later message** adds 2k: the previous reply and your new message. Each reply is 1k output tokens.

Its **first request** sends 35k tokens. Other sessions on the machine already have the 25k start cached (P4):

| Tokens | Price per million | Cost |
|---|---|---|
| 25,000 read from the cache | $0.20 | $0.005 |
| 10,000 written to the 1-hour cache | $8 | $0.080 |
| 1,000 output | $20 | $0.020 |
| **Total** | | **$0.105** |

With nothing cached yet, all 35k would be written: $0.30. The **second request** sends 37k: it reads the 35k back ($0.007), writes its new 2k ($0.016) and outputs 1k ($0.020), **$0.043**. From then on each warm message costs a little more than the last, as the conversation it reads back grows (P2).

### 4. Reading a usage record

Every response reports what it consumed. With caching, the input comes in three parts that add up to the whole prompt:

```
total input = input_tokens                (after the last cache breakpoint: plain input)
            + cache_read_input_tokens     (read back)
            + cache_creation_input_tokens (written; split into ephemeral_5m / ephemeral_1h)
```

`input_tokens` alone is **not** the prompt size. With a 200k cached document and a 50-token question it reads 50. `output_tokens` includes thinking; `output_tokens_details.thinking_tokens` says how much of it was thinking. `server_tool_use.web_search_requests` counts billable searches. `speed` and `inference_geo` tell you whether fast mode or US-only pricing applied.

**On the wire.** Claude Code writes each reply into its transcript (`~/.claude/projects/<folder>/<session>.jsonl`) with the usage the API reported. It doesn't write the request it sent, so a transcript shows what each request cost, not what it contained. This is the first request of a real session on Opus 5.5 with the 1-hour cache, trimmed to the fields that matter:

```json
"model": "claude-opus-5-5",
"usage": {
  "input_tokens": 2,
  "cache_read_input_tokens": 23167,
  "cache_creation_input_tokens": 11977,
  "cache_creation": { "ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 11977 },
  "output_tokens": 121,
  "output_tokens_details": { "thinking_tokens": 0 },
  "server_tool_use": { "web_search_requests": 0, "web_fetch_requests": 0 },
  "speed": "standard"
}
```

Priced line by line:

| Field | Tokens | Price per million | Cost |
|---|---|---|---|
| `input_tokens` (plain input) | 2 | $4 | $0.000008 |
| `cache_read_input_tokens` | 23,167 | $0.20 | $0.004633 |
| `ephemeral_1h_input_tokens` (written) | 11,977 | $8 | $0.095816 |
| `output_tokens` | 121 | $20 | $0.002420 |
| **Total** | | | **$0.102877** |

It's the running session's first request, for real: of the 35,146-token prompt, the 23,167 read back are Claude Code's tool list, kept warm by another session (P4), and the rest was written to the 1-hour cache.

**The cache hit rate** of a request or a day is `read ÷ total input`. It's the single most useful health metric (§20). For this request it's 66%: a session's first request can only read back what it shares with others.

---

## Part 2: Prompt caching, the biggest lever

### 5. How the cache works

A request is sent in a fixed order: **tools → system → messages**. The cache stores the model's processed state for a prefix of that sequence, keyed by a hash of every byte up to a marked point (a *breakpoint*). The next request reuses it only if its own beginning is byte-for-byte identical (P4).

```
┌──────────────┬──────────────┬─────────────────────────────────────────────┐
│ tools  ~23k  │ system  ~2k  │ messages: first prompt … latest (growing)   │
└──────────────┴──────────────┴─────────────────────────────────────────────┘
               ▲ breakpoint   ▲ breakpoint                       breakpoint ▲
A change in any layer re-processes that layer and everything to its right.
```

**What the request looks like.** This is a schematic, not a captured request, since transcripts don't keep what was sent. In real Claude Code sessions, reads stop at three points, the end of the tool list (23,167 tokens), the end of the system prompt (about 25k) and the latest message, so that's where its breakpoints are:

```
{
  "model": "claude-opus-5-5",
  "tools":    [ …, { …the last tool…, "cache_control": { "type": "ephemeral", "ttl": "1h" } } ],
  "system":   [ { "type": "text", "text": "…", "cache_control": { "type": "ephemeral", "ttl": "1h" } } ],
  "messages": [ …every earlier message…,
                { "role": "user", "content": [ { "type": "text", "text": "…",
                  "cache_control": { "type": "ephemeral", "ttl": "1h" } } ] } ]
}
```

**Layered and shared** (P4). The early layers are the same in many requests, so other sessions keep them warm. On the wire: a new session read back 23,167 tokens, the tool list other sessions on the machine had cached. Eight seconds later, a second new session in the same folder read back 25,084: the tool list plus the system prompt the first had just written.

The rules that matter:

| Rule | Consequence |
|---|---|
| Matching is exact, from the start | Appending is cheap. Any change earlier re-processes everything after it (P4) |
| Writes happen only at breakpoints (up to 4 per request) | Put the breakpoint on the last block that stays the same, not on one that changes every time (a timestamp, the new question) |
| A read looks back at most 20 blocks from each breakpoint for an earlier write. A *block* is one piece of a message: a text, an image, a tool call, a tool result | A turn that adds more than 20 blocks can miss the previous entry; a second breakpoint fixes it. A run of parallel tool calls counts as one block, and so does the run of their results |
| Each model has its own cache | A model switch re-sends everything as new |
| There's a minimum cacheable size: 512 tokens on Opus 5.5, Opus 5, Sonnet 5.5 and Fable 5.1, 1,024 on Sonnet 5, 4,096 on Haiku 4.5 | Below it, nothing is cached and no error says so; both cache counts read 0 |
| Caches are isolated per workspace (per organisation on Bedrock and Google Cloud) | Identical prompts in two workspaces don't share |
| An entry exists only once the first response has begun | Parallel requests sent at the same instant all write; none reads |

**Automatic caching** (one `cache_control` at the top level of the request) moves the breakpoint to the last block of each request, which suits a growing conversation. Claude Code places its breakpoints for you (P10).

### 6. When caching pays

Writing costs a premium over plain input; each read saves most of the input price. So a cached prefix pays for itself after this many reads:

```
reads to break even = (write multiplier − 1) ÷ (1 − r)
```

| | 5-minute cache | 1-hour cache |
|---|---|---|
| Most models (r = 0.1) | 0.28: **the first read** | 1.11: **the second read** |
| Opus 5.5 (r = 0.05) | 0.26 | 1.05 |

**Example: the running session's first 20 messages** (P2, P4). Each request sends 2k more than the last, from 35k to 73k: 1.08M input tokens in all.
- **Without caching**, all of it is plain input: $4.32, plus $0.40 of output, **$4.72**.
- **With the 1-hour cache**, each request reads the previous one back and writes only its 2k. The input costs $0.59, and the 20 messages **$0.99**, 79% less. Output ($0.40) is now the biggest part.
- **The trap:** a 21st message after the cache has expired sends 75k. With the start still cached it writes the other 50k: **$0.43**, against $0.05 warm, over 40% of what the 20 messages cost together. With nothing cached, $0.62.

### 7. The cache lifetime

- **Two lifetimes:** 5 minutes (the default) or 1 hour. The 1-hour cache costs 2× input to write instead of 1.25×; reads cost the same.
- **The clock starts at the start of the request** that wrote or read the entry, not when the reply ends. Generation time eats into it: after a 4-minute reply on a 5-minute cache, the next request has about a minute (P5).
- **Every read restarts the clock.** A long agent run stays warm as long as each step starts within the lifetime of the previous step's start. On the wire: six 100-second steps on a 5-minute cache each read the whole conversation back (35,258 tokens and growing), writing only the 162 or so new ones.
- **The lifetime is a minimum.** Entries are deleted "promptly, though not immediately" after it. In real sessions one 5-minute entry was read back half a minute past its lifetime, and two others were gone at 5¾ and 10½ minutes (Appendix B). Plan on the minimum; treat anything longer as luck.
- **Requests you don't see use it too** (P5, P8). Claude Code's recap, usually written within 4 minutes of its last reply, re-sends the conversation: it restarts the clock, and one written after the cache had run out wrote the conversation again. On the wire: on a 5-minute cache, a message 6 minutes 36 seconds after the last request read all 36,330 tokens back, because a recap 3 minutes in had re-sent the conversation. Other unlogged requests sometimes cache the conversation with its latest reply (Appendix B).

**When it runs out: the running session at lunch.** By midday the conversation is 100k tokens. You come back after 90 minutes, and the 1-hour cache has expired:
- A warm message would read 100k ($0.020), write 2k ($0.016) and output 1k ($0.020): **$0.056**.
- This one reads back only the 25k start, which other sessions kept warm, and writes the other 77k ($0.616): **$0.64**, eleven times more.
- usdash shows both ends before you send: `$0.02 now` while the cache is warm, `up to $0.80` once it has expired (all 100k written). The real figure, $0.64, lands under the top because the start stayed cached.

On the wire: a session on a 5-minute cache ran a 331-second tool. The next request read back only the start (25,209 tokens) and wrote the rest again (10,224): $0.0562, against about $0.01 warm.

**Choosing between them.** The 1-hour cache costs an extra 0.75 × input for every token written. A pause of between 5 and 60 minutes on a 5-minute cache costs re-writing the whole context. So the 1-hour cache wins when:

```
tokens re-written after 5–60 min pauses  >  0.75 ÷ (1.25 − r)  ×  all tokens written
                                            (0.65 on most models, 0.625 on Opus 5.5)
```

The rule of thumb: **one 5–60 minute pause once the context is near its full size is enough for the 1-hour cache to win.** For example, a day in the running session in 6 bursts of 10 messages, with 15-minute breaks between them and, to keep the arithmetic simple, a context that stays around 100k and nothing else keeping it cached:
- **5-minute cache:** a warm message costs $0.050, its 2k written at $5. The day's first message and the first after each break write all 102k ($0.51, plus output). The day: 6 × $0.53 + 54 × $0.050 = **$5.88**.
- **1-hour cache:** a warm message costs $0.056, its 2k written at $8. Only the day's first message writes all 102k ($0.816, plus output). The day: $0.84 + 59 × $0.056 = **$4.14**.

The five breaks each cost the 5-minute cache a full re-write, far more than the 1-hour cache's higher price on the small additions (P5, P6).

**Who gets which, in Claude Code** (P10): on a subscription within plan usage, the main conversation uses 1 hour. On an API key, usage credits or a cloud provider it uses 5 minutes, unless you set `promptCacheTtl` or `CLAUDE_CODE_PROMPT_CACHE_TTL`. Subagents, compaction and titles use 5 minutes by default; `subagentPromptCacheTtl` (or `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`) sets theirs.

### 8. What breaks the cache

The principle: **anything that changes bytes early in the request re-processes everything after it** (P4). The layer it sits in says how much.

| Change | Tools | System | Messages |
|---|---|---|---|
| Tool definitions added, removed or edited | ✘ | ✘ | ✘ |
| Turning web search or citations on or off; switching fast mode (API) | ✓ | ✘ | ✘ |
| `tool_choice`; adding or removing images | ✓ | ✓ | ✘ |
| Thinking settings (mode, budget) | ✘ on some models | ✘ on some models | ✘ |
| Effort, changed on the request itself | ✘ on some models | ✘ on some models | ✘ |
| **A different model** | all new | all new | all new |
| **The lifetime passing** | gone | gone | gone |

"On some models": the setting is written into the prompt, and some models place it ahead of the tools and system prompt, so everything after it is re-processed. Effort changed per message instead (a beta, below) keeps the cache, and setting effort to the model's default is the same as leaving it out.

What doesn't break it: appending messages, tool calls and results. Mid-conversation additions that are sent as new messages (a system message, a skill's instructions) leave the cached prefix intact.

**Where the API and a client differ, check the client** (P10). Examples from Claude Code:
- **Effort:** the API keeps the cache across an effort change on Fable 5.1, Mythos 5.1 (a limited-availability model priced like Fable 5.1), Opus 5.5, Sonnet 5.5 and Opus 5, when the change is sent as a per-message setting. Claude Code keeps it on Opus 5.5, Sonnet 5.5 and Fable 5.1 (with an API key or subscription; not on Bedrock or Google Cloud). On Opus 5 it re-wrote the conversation in real sessions.
- **Fast mode:** the API table says switching speed invalidates system and messages. Claude Code sends the fast-mode header once per conversation, so only turning it on the first time costs a re-write. Turning it off and on again later keeps the cache.
- **Resuming:** Claude Code keeps the system prompt a conversation started with, so a resumed session reads back whatever is still within the lifetime (8 of 10 real resumes did; the other two read back only the tool list; Appendix B).
- **MCP servers:** connecting or removing one changes the tool list only if its tools aren't deferred; by default they are.
- **Editing CLAUDE.md mid-session:** doesn't break the cache, and doesn't take effect until `/clear`, `/compact` or a restart.

**On the wire**, three changes in a row in one real session on the 1-hour cache:

| What happened | Read back | Written | Cost |
|---|---|---|---|
| Opus 5.5, effort high → low, 18 minutes after the last request | 34,989 of 34,991 | 229 | $0.0091 |
| Then a switch to Opus 5 | 23,167 (Opus 5's tool list, cached by another session) | 12,471 | $0.1368 |
| Then Opus 5, effort low → high | 23,167 | 12,698 | $0.1388 |

The first kept the cache, because Claude Code sends Opus 5.5's effort per message. The switch and the Opus 5 effort change both wrote the conversation again.

### 9. Caching and rate limits

Rate limits count requests per minute, input tokens per minute (ITPM) and output tokens per minute (OTPM). **On most models, tokens read from the cache don't count toward ITPM**; only plain input and cache writes do. With an 80% hit rate, a 2M ITPM limit processes 10M input tokens a minute.

Two more details:
- `max_tokens` doesn't count toward OTPM, so there's no rate-limit reason to set it low.
- Caching is a throughput lever as much as a cost lever.

---

## Part 3: The other levers

### 10. Model choice and tokenizers

Price per token is half the story. The other half is tokens per task, which depends on (P9):
- **The tokenizer.** The same text is ~30% more tokens on Opus 4.7 and later. 77,000 tokens on Haiku 4.5 are 100,000–104,000 on Opus 5.5 or Sonnet 5.5.
- **How much the model writes and thinks** for this task at this effort.
- **How many attempts it takes.** A cheaper model that needs a second pass costs two passes.

**Compare models on the same task, per finished result:** tokens in and out, times price, times attempts. The docs' own example: 10,000 support tickets of ~3,700 tokens each on Haiku 4.5 cost about $37.

### 11. Output, thinking and effort

- **Output is the expensive side:** 5× input on every current model. A 2,000-token reply on Opus 5.5 costs $0.04; reading a 100k conversation from its cache costs $0.02.
- **Thinking is output.** You pay for all of it, even when the response shows a summary or nothing. `thinking_tokens` in the usage says how much.
- **Effort** (`low`, `medium`, `high`, `xhigh`, `max`) scales every output token, including thinking, tool calls and explanations. Lower effort means fewer and terser tool calls too. It's a behavioural signal, not a hard budget. The API's defaults are `medium` on Opus 5.5 and `high` on the others; a client can set its own (in Claude Code, `/effort`).
- **Earlier thinking stays in context** on Opus 4.5 and later, Sonnet 4.6 and later, and the Fable models, and is re-sent as input every request. That's cheap when read from the cache, expensive after a miss.

**The effort decision on a warm cache:** where an effort change keeps the cache, the saving is immediate. It's output before minus output after, times the output price. In the running session, going from ~3,000 to ~800 output tokens per message saves $0.044 a message. Where it doesn't keep the cache, treat it as a model switch (§15).

### 12. Batch, fast mode, data residency, cloud endpoints

All four are multipliers, and they stack with each other and with the cache multipliers (P7).

| Lever | Effect | Notes |
|---|---|---|
| **Batch API** | ×0.5 on input and output | Asynchronous: most batches finish within an hour, all within 24 hours or they expire unbilled. Caching works, best-effort. For a shared prefix, write it once to the 1-hour cache, then submit the rest |
| **Fast mode** | Opus 5.5: $8 / $40 (2×); Opus 5 and 4.8: $10 / $50 (2×) | Up to 2.5× faster output. Not with Batch. The first fast request re-writes the context at fast prices: 200k tokens on Opus 5.5 cost $3.20 to switch deep into a session, $0.32 at a 20k start |
| **US-only inference** (`inference_geo: "us"`) | ×1.1 on everything | Claude 4.6 and later |
| **Bedrock and Google Cloud regional endpoints** | +10% over global | Claude 4.5 and later |

**Stacking example:** a 1-hour cache write on Opus 5.5 lists at $8/M. With US-only inference it's $8.80, and in a batch as well, $4.40.

### 13. Tools and server tools

- **Tool definitions are input on every request** (P1, P2). Each tool's name, description and schema, plus the tool-use system prompt. Tools the client defers until needed (Claude Code's default for MCP tools) cost nothing until loaded.
- **Tool calls are output; tool results are input** in the request that follows, and in every request after it.
- **Web search:** $10 per 1,000 searches, plus its results as input tokens. A failed search isn't billed. Claude Code's WebSearch tool searches in a request of its own on Haiku 4.5, which its transcripts don't log (§18).
- **Web fetch:** only the fetched content's tokens. An average web page is ~2,500 tokens; a 500 kB PDF ~125,000.
- **Code execution:** free alongside web search or web fetch. Otherwise it's billed by container time (1,550 free hours per organisation per month, then $0.05 an hour per container, at least 5 minutes each time).

---

## Part 4: Conversations and agents

### 14. Anatomy of an agent turn

One prompt to an agent is not one request. It's one request per step: the model calls a tool, the tool result goes back, the model continues. **Every step re-sends the whole context** (P2).

**Example:** the running session at 100k, warm, and one prompt that takes 8 tool steps. Each step adds a 3k-token tool result and produces 300 output tokens. The turn costs **$0.42**:
- **Re-reading the context** (8 × ~110k tokens): $0.18
- **Writing the new tokens** (24k, at the 1-hour price): $0.19
- **Output** (2,400 tokens): $0.05

So in a warm agent loop, **the new tokens you add cost as much as the context you re-read**, and big tool results are expensive twice: once when written, then as reads on every later step. Keeping tool output small is one of the best levers you have. Examples: filter a test log down to its failures before the model sees it, or let a subagent read the big file and return a summary.

Cost per turn, in general:

```
turn ≈ steps × context × read price  +  new tokens × write price  +  output × output price
```

### 15. Decisions with a price now and a saving later

Most choices in a session trade a one-time cost for a per-request saving (P6). Name them:
- **P** is what the change costs now, compared with not changing.
- **s** is what it saves on each later request.
- **m = P ÷ s** is how many requests until it has paid for itself.

**Example: switching the running session to Sonnet 5.5** at 100k, warm, with 2k new tokens and 1k output per later message:
- **Now:** Sonnet 5.5 must write all 100k into its own cache (P4), $0.40, where Opus would read them for $0.02. P = **$0.38**.
- **Later:** both models read the 100k at the same $0.20, so the saving is only on the new tokens and output: $0.056 a message on Opus against $0.038 on Sonnet. s = **$0.018**.
- **Pay-back:** 21 messages.
- **After a break**, the cache has expired and both must write everything (less whatever start other sessions keep cached). The same switch costs $0.40 on Sonnet against $0.80 to stay on Opus: switching down is then cheaper from the first message.

On the wire: a real switch from Opus 5.5 to Sonnet 5.5 at about 35k. Sonnet read back 23,167 tokens (its tool list, cached by another session) and wrote 11,724: $0.0519, against $0.0084 for the warm Opus message before it.

**When you don't know how many requests are left**, use the rent-or-buy rule. Keep paying the extra per request (renting) until what you've paid adds up to P, then switch (buy). You never pay more than twice what hindsight would have cost, plus one request. And a break resets the question, because after the cache expires P usually drops to zero or below.

**Round trips:** switching away and coming back within the lifetime can reuse the first model's cache. The lookback reaches at most 20 blocks, though, so after many turns only the start is reused. Budget a full re-write and treat reuse as a bonus.

### 16. /compact, /clear and /rewind

**`/compact`** replaces the conversation with a summary. It costs a summarising request now, and saves on every later message (P6).

- **The summarising request:** Claude Code sends it with the same system prompt, tools and history, and it doesn't cache what it sends (P10). Measured on real compactions (Appendix B):
  - **While warm,** it read back what the latest turn's first request had cached (everything up to and including the last prompt you typed), and sent the rest at the plain input price.
  - **After a break,** it read back only the tool list, and sent the rest at the input price.
  - **Its output** (the summary, with any thinking) was 1,065–3,804 tokens.
- **After it**, the next request sends the tool list, the system prompt, what Claude Code attaches at the start of every session (environment, listings, re-read files) and the summary. That's about the session's first prompt plus the summary, within 6% on three real compactions. The next request writes this; later ones read it.

**Example: compacting the running session at 100k**, its latest turn begun at 90k, with a 4k summary (about 2.5k output tokens):
- **Compacting now** costs **$0.108**: 90k read, 10k plain input, the summary generated.
- **Compacting after the cache expired** costs **$0.355**: 25k read, 75k plain input, the summary.
- **After it**, the conversation is 39k: the first prompt's 35k and the summary's 4k. Each later message reads 39k instead of 100k, saving **$0.012**.
- **The first message after** writes 14k it would otherwise have read: $0.109 more.
- **Pay-back:** about 18 messages. Compacting a warm 100k conversation only to save money pays back slowly. It pays sooner at larger contexts, and at once before a break: lunch after compacting costs $0.15 instead of $0.64 (§7). Compact before a break, not after it. When one prompt built most of the conversation (a single "read all these files" turn), the warm compaction can't read much back, and the two cost nearly the same.

On the wire: a 40k conversation compacted within a minute of its last reply. Claude Code's own record showed 75,114 more tokens read, 6,851 more plain input, 349 more written and 1,296 more output than its transcripts. That fits two unlogged requests: the compaction, reading back about 35k (what the turn's first request had cached), sending about 7k as plain input and generating a 1.3k summary, and a prompt suggestion reading the whole 40k.

**`/clear`** starts over and costs nothing itself. The tool list and system prompt usually stay cached. Use it when the topic changes.

**`/rewind`** goes back to an earlier turn and drops what came after it, so later messages re-send less: the cheapest way to abandon a wrong path. That turn's prefix is read back if it's still cached; the docs don't say whether later, longer reads keep it alive, so after many turns budget for writing it again.

### 17. Breaks, resumes, subagents and parallel requests

- **A break longer than the lifetime** makes the next request re-write the whole context, less whatever start other sessions keep cached (P4, P5). It's the most common reason a session "suddenly" costs more: the running session's lunch turns a $0.056 message into a $0.64 one (§7).
- **Resuming** a session (`claude --resume`) re-sends the whole conversation. Within the lifetime it reads back what's still cached; after it, it re-writes it (P5, P10). On the wire: a resume 5 minutes after the last request read all 35,094 tokens back; one 22½ hours later read back only the 23,167-token tool list and wrote the conversation again.
- **A subagent** is a separate conversation with its own prompt and its own cache (5 minutes by default). It doesn't read the parent's cache, and the parent's cache doesn't refresh while it waits (P4, P5). A long subagent can leave the parent to re-write when it returns. On the wire: a parent on a 5-minute cache waited 10 minutes for a subagent. The subagent's six steps each read its own cache back (25–28k), but the parent's next request read back only the 23,292-token start and wrote 12,932 tokens again.
- **A fork** (a subagent that inherits the parent's system prompt, tools and conversation exactly) reads the parent's cache on its first request. Check that a feature really is a fork: a skill run in a forked subagent in this project's review read nothing back and wrote its 44k-token start again.
- **Parallel requests sharing a prefix:** an entry exists only once the first response has begun. Ten requests sharing a 30k prefix on Sonnet 5.5, sent at once, write it ten times: **$0.75**. Sending one first and the other nine once it has started: **$0.13**.

### 18. Costs you don't see

Clients make requests you don't type, and not all of them appear in session logs (P8):
- **Claude Code** (measured at 2.1.278–2.1.286; Appendix B has the details):

  | Request | Model | What was measured |
  |---|---|---|
  | Session titles | Haiku 4.5 | 893 tokens in, 10 out, in one session |
  | Prompt suggestions | | Mostly cache reads |
  | The recap written while you're away | The session's | Re-sends the conversation and restarts the cache clock; one written after the 1-hour cache had run out wrote it again. 27 of 40 came within 4 minutes of the last reply, the rest 4–74 minutes after |
  | `/compact`'s summarising request | The session's | Warm: reads what the latest turn's first request had cached, the rest at the input price, writing only 147–555 tokens. Cold: reads the tool list, the rest at the input price. Output 1,065–3,804 tokens |
  | WebSearch's searches | Haiku 4.5 | A request of their own, plus $10 per 1,000 searches. In one session, Claude Code counted 12,633 tokens in, 126 out and one search on Haiku 4.5, the session's title included: $0.023263. Four searches were 72% of one `claude -p` session's cost |
  | Background summaries for `--resume` | | Not measured |
  | `/btw` side questions | | Not in the transcript at all: only in Claude Code's input history (`~/.claude/history.jsonl`), which has no cost |
  | The Desktop app's own requests | The session's | Claude Code counted $0.23 for a session whose transcripts show $0.16 |

- **The client's own total** (P8, P10). When a session exits, Claude Code writes what it counted, per model, into the transcript as a `cost-state` record. On the wire, for the compacted session in §16:

  ```json
  "totalCostUSD": 0.2289108,
  "modelUsage": {
    "claude-haiku-4-5-20251001": { "inputTokens": 975, "outputTokens": 15, "costUSD": 0.00105 },
    "claude-opus-5-5": { "inputTokens": 6855, "outputTokens": 1923, "cacheReadInputTokens": 133384,
                         "cacheCreationInputTokens": 16913, "costUSD": 0.2278608 }
  }
  ```

  The Haiku line matches a session title, which the transcript never logged. Its transcripts' own requests came to $0.1567.
- **How much, in real sessions:** the transcripts held 41–100% of what Claude Code itself billed (a median of 89%; 84% weighted by cost), least in very short sessions and in ones that ran many subagents. In one `claude -p` session with four web searches they held only 28%: the searches' own requests were 72% of the cost.
- **The FinOps rule:** reconcile against the provider's usage report, the client's own total (`/usage`, `cost-state`), or an OpenTelemetry export, not only against your logs.

---

## Part 5: FinOps practice

### 19. Unit economics and forecasting

**Pick the unit the business cares about** (per ticket, per document, per pull request, per developer-day) and cost it end to end:

```
cost per unit = Σ over the requests the unit needs of (tokens by kind × price) × modifiers
              + per-use fees + infrastructure (containers, runtime)
```

**To forecast a workload**, estimate:
1. Requests per unit, including tool steps and hidden requests (P2, P8)
2. Context per request, and how it grows
3. Cache hits, from the traffic pattern: steady traffic stays warm; bursty traffic with long gaps re-writes (P5)
4. Output per request, including thinking at the chosen effort
5. Volume

Then run the equation, and **check it against a small pilot's real usage before scaling.**

**Reference points:**
- **Claude Code across enterprise deployments** (Anthropic's figures): about $13 per developer per active day, $150–250 per developer a month, under $30 a day for 90% of users.
- **A RAG service:** a 50k-token document and 1,000 questions an hour on Sonnet 5.5 (200 tokens in, 400 out) costs **$104 an hour without caching and $15 with it**. Each question then counts only 200 tokens toward the input rate limit.
- **Offline processing:** 10,000 documents of 3k tokens in and 500 out on Haiku 4.5 cost **$55, or $27.50 through the Batch API**.

### 20. Metrics to watch

| Metric | Formula | What it tells you |
|---|---|---|
| **Cache hit rate** | read ÷ total input | Below ~80% in a conversational workload means something keeps changing the prefix, or pauses outlast the lifetime |
| **Miss cost** | tokens re-written that could have been read × (write − read price) | What misses cost you, by cause: model switch, expiry, resume, config change |
| **Output share** | output cost ÷ total cost | High means effort, thinking or verbose replies are the lever, not caching |
| **Effective input price** | input-side cost ÷ total input tokens | How close you are to the cache-read price |
| **Cost per unit** | total ÷ units delivered | The only number the business sees |
| **Unlogged share** | 1 − logged cost ÷ billed cost | How much your own logs miss |

For your own Claude Code use, usdash's Stats view shows the first four: the share read from cache, cache misses by cause, spend by kind of token, and what input costs on average. It also shows the logged share, as how much of Claude Code's own totals its transcripts hold.

### 21. Governance levers

| Where it acts | Levers |
|---|---|
| **Budget** | Spend limits per organisation and workspace; per-user limits on team plans; alerts on the daily trend |
| **Structure** | Stable content first in the prompt; breakpoints on the last stable block; tool outputs kept small; stable tool sets |
| **Routing** | The cheapest model that meets the quality bar, measured per task; subagents on small models for side work |
| **Time** | The 1-hour cache for work with pauses; batch for anything that can wait a day; pre-warming with `max_tokens: 0` when first-response latency matters |
| **Visibility** | Contracted rates in the reporting (Claude Code's `modelPricing`); OpenTelemetry per user and session; reconciliation against invoices; local logs kept long enough for the baseline (Claude Code deletes transcripts after 30 days by default: `cleanupPeriodDays`) |
| **Subscription vs API** | A subscription bills plan usage, not tokens; list-price estimates are for comparison. Going past the plan onto usage credits also drops Claude Code's main conversation to the 5-minute cache |

---

## Part 6: Solving any new question

### 22. The method

1. **List the requests.** Every request the scenario sends, including tool steps, retries, subagents and the client's own background requests.
2. **For each request, find its prefix and ask four questions.** Is it the same model? The same bytes up to here? Within the lifetime of the last request that used it? At least the minimum cacheable size? The answers split its tokens into read, written (5m or 1h) and plain input.
3. **Add output**, thinking included, at the chosen effort.
4. **Price it**: the equation in §3, the modifiers in §12, fees in §13.
5. **Compare options over a horizon.** Separate one-time costs from per-request ones; pay-back is one ÷ the other (§15).
6. **Check against reality.** The usage fields of a real request (§4) are the ground truth; a pilot beats a spreadsheet.

When an answer surprises you, name the principle it follows from. If none fits, look for a request you didn't list (P8) or a choice the client made (P10).

### 23. Practice problems

Answers follow each one; work them out first.

**1. A support bot keeps a 6k-token system prompt and gets a question every 2 minutes, all day. 5-minute or 1-hour cache?**
*5 minutes.* Every question arrives within the lifetime and refreshes it for free, so the 1-hour cache's higher write price buys nothing (P5, §7).

**2. The same bot, but questions come every 20 minutes.**
*1 hour.* On a 5-minute cache every question re-writes the 6k prompt. On a 1-hour cache it's written once and read at 0.1×. It breaks even on the second read (P5, §6).

**3. Your agent's cache hit rate dropped from 95% to 40% after a deploy. Where do you look?**
*At what changed early in the request*, in order: tool definitions, the system prompt (a timestamp, a per-user field), images, tool_choice, the thinking or effort configuration, the model id (P4, §8). Then check whether the breakpoint is on a block that changes every request (§5).

**4. A developer asks why a one-line question cost $0.80 after lunch.**
*The cache expired over lunch* (P5). The one-line question re-sent the whole 100k-token conversation at the 1-hour write price on Opus 5.5: up to $0.80, less whatever start other sessions kept cached (§7).

**5. Should a nightly report job over 50,000 records use the Batch API?**
*Yes*, if the results can wait up to 24 hours: half price on every token, and caching still applies, best-effort (P7, §12).

**6. You fan out 20 subagents that share a 40k-token briefing. How do you cut the input cost?**
*Send one first, and the other 19 once its response has begun,* so they read the briefing instead of each writing it (P4, §5, §17). Or pre-warm the cache with `max_tokens: 0`.

**7. Is moving a warm 200k Opus 5.5 conversation to Sonnet 5.5 worth it to save money?**
*Rarely, while it's warm.* Sonnet must write 200k tokens (~$0.80 at the 1-hour price), and both models read at the same $0.20/M afterwards. Only the new tokens and output get cheaper. After a break it's a different answer: both must re-write, and Sonnet does it for half (P4, P6, §15).

**8. A session resumed the next morning read 23,167 tokens from the cache and wrote the rest. Why 23,167, and why not 0?**
*The conversation's entry had expired overnight* (P5), *but the start of the request is shared.* 23,167 is Claude Code's tool list, the same in most sessions on that model and version, and another session kept it warm (P4, §5).

**9. A subagent ran for 10 minutes. When it reported back, the parent re-wrote its conversation on a 5-minute cache, but not on a 1-hour one. Why?**
*The subagent has its own conversation and cache* (P4), *and the parent sent nothing while it waited*, so its 5-minute entry expired and its 1-hour one didn't (P5, §17).

**10. On a 5-minute cache, a message 6½ minutes after the last request read everything back. Was the lifetime wrong?**
*No. A request you didn't see re-sent the conversation*: Claude Code's recap, 3 minutes in, read the cache and restarted the clock (P5, P8, §7).

**11. Claude Code's total for an exited session is 30% above what its transcripts show. Which number is right?**
*Both, for what they cover.* The transcripts leave out requests like titles, prompt suggestions, recaps, `/compact`'s request and `/btw`; the client's own total counts them (P8, §18). For a budget, use the client's total, or divide what your logs show by the logged share (§20).

**12. You're at 150k on Opus 5.5 with a warm cache and leaving for lunch. Compact now, or after?**
*Now.* A warm compaction reads most of the conversation from the cache; after lunch it sends nearly all of it at the plain input price. And after lunch, the first message re-writes a context of about 40k instead of 150k (P5, P6, §16).

**13. Your team works in bursts with 15-minute breaks. 5-minute or 1-hour cache?**
*1 hour.* Each break costs the 5-minute cache a full re-write of the context; the 1-hour cache costs a little more on each small addition (P5, P6, §7).

**14. Why does a `/btw` side question never show up in the transcripts?**
*The client sends it outside the conversation and doesn't log it* (P8, P10). Only Claude Code's own total at exit counts it (§18).

**15. You change effort mid-session. On Opus 5.5 the next message read everything back; on Opus 5 it re-wrote the conversation. Why the difference?**
*How the client sends the change* (P10). Claude Code sends Opus 5.5's effort per message, which keeps the cache; on Opus 5 it re-wrote, as a change on the request itself does (P4, §8).

---

## Appendix A: Price sheet

Per million tokens, from Anthropic's [pricing page](https://platform.claude.com/docs/en/about-claude/pricing) on 2026-10-01. **Re-check there before using these numbers for anything that matters.**

| Model | Input | 5-min write | 1-hour write | Cache read | Output | Batch in / out |
|---|---|---|---|---|---|---|
| Fable 5.1, Mythos 5.1* | $10 | $12.50 | $20 | $0.25 | $50 | $5 / $25 |
| Fable 5, Mythos 5* | $10 | $12.50 | $20 | $1 | $50 | $5 / $25 |
| Opus 5.5 | $4 | $5 | $8 | $0.20 | $20 | $2 / $10 |
| Opus 5, 4.8, 4.7, 4.6, 4.5 | $5 | $6.25 | $10 | $0.50 | $25 | $2.50 / $12.50 |
| Sonnet 5.5, Sonnet 5 | $2 | $2.50 | $4 | $0.20 | $10 | $1 / $5 |
| Sonnet 4.6, 4.5 | $3 | $3.75 | $6 | $0.30 | $15 | $1.50 / $7.50 |
| Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 | $0.50 / $2.50 |

*Limited availability, by invitation.

| Multiplier | Value |
|---|---|
| 5-minute cache write | 1.25 × input |
| 1-hour cache write | 2 × input |
| Cache read | 0.1 × input (0.05 on Opus 5.5; 0.025 on Fable 5.1 and Mythos 5.1) |
| Output | 5 × input on every model above |
| Batch API | × 0.5 |
| US-only inference, Claude 4.6 and later | × 1.1 |
| Bedrock / Google Cloud regional or multi-region endpoints | + 10% |
| Fast mode | Opus 5.5 $8 / $40; Opus 5 and 4.8 $10 / $50; cache multipliers apply on top |
| Web search | $10 per 1,000 searches |
| Long context (up to 1M on Claude 4.6 and later) | No premium |

## Appendix B: Evidence from real sessions

What building and reviewing usdash measured in real Claude Code transcripts, compared with Claude Code's own totals. It's evidence for one client's behaviour at the versions measured (2.1.278–2.1.286). Re-measure after an upgrade. The regression tests in [`tests/test_real_checks.py`](../tests/test_real_checks.py) hold usdash to the 2026-09-28 sessions in [`tests/fixtures/checks`](../tests/fixtures/checks): the next message reading the last prompt back, resuming within the lifetime, a model switch across tokenizers, and the size after `/compact` waiting for the next request.

| Question | What was measured |
|---|---|
| Does the next request read back the whole previous prompt? | Yes: 1,938 of 1,939 same-model pairs within the lifetime, to within 100 tokens (the uncached remainder was at most 0.04% of a prompt). The other changed effort on Sonnet 5, which re-writes |
| How big is Claude Code's tool list? | CLI 22–25k tokens; VS Code extension 20.8k; Desktop app 36.3k; `claude -p` 10–20k depending on model and version |
| Do other sessions keep the start of a request cached? | Yes: new sessions typically read back the 23,167-token tool list another session had cached. One started eight seconds after another in the same folder read back 25,084: the tool list and the system prompt the first had just written |
| Is the tool list cached when the conversation's cache isn't? | Usually. Own model on a 1-hour cache: 36 of 39 cold starts read it back, once after 11¾ hours idle. 5-minute cache: 9 of 48 with no other session using the model, 13 of 22 with one. Another model with no session on it: once yes (Sonnet 5), once no (Opus 5.5) |
| Is the lifetime exact? | No, a minimum, but only just. With nothing refreshing it in between, one 5-minute entry was read back 5.6 minutes after its last use (all but its newest 220 tokens); 2 others were gone at 5.8 and 10.6 minutes |
| Does Claude Code's recap refresh the cache? | It re-sends the conversation: a recap written 74 minutes into a pause, after the 1-hour cache had run out, wrote it again, and the next message, 15 minutes later, read all of it back. 27 of 40 recaps came within 4 minutes of the last reply, the rest 4–74 minutes after. A recap within the lifetime keeps the cache alive: on a 5-minute cache, the next message, 6½ minutes after the last request and 3½ after a recap, read all of it back (2.1.286) |
| Do other unlogged requests touch the conversation's cache? | Sometimes: with no recap in between, 140 of 1,913 next messages within the lifetime read back more than the previous request had sent, so an unlogged request had cached the conversation with its latest reply |
| Tokenizer ratio, old to new? | 0.758 in one switch, 0.74 in another; Anthropic says ~30% more tokens (0.77) |
| Does an effort change keep the cache? | Opus 5.5 in Claude Code: 7 of 7 read everything back. Opus 5 in Claude Code: no; the request after the change read back only the tool list, which other sessions keep warm, and wrote the conversation again (12,698 of 35,867 tokens in one check) |
| Does a Claude Code upgrade break the cache? | Not necessarily: resumed across 2.1.284 → 2.1.285 and 2.1.285 → 2.1.286 within the lifetime, both read the whole conversation back. One that changes the tool definitions or system prompt would |
| Does resuming keep the cache? | Within the lifetime: 8 of 10 resumes read the whole conversation back (1-hour and 5-minute caches, one after a file edit); the other two, 3½ and 40 minutes after the last request, read back only the tool list. After ≥ 92 minutes: 31 of 31 re-wrote it |
| What does a warm `/compact` request read? | What the latest turn's first request had cached: 46,885 of 56,584 tokens after a four-turn session, 15,804 of 58,787 when one turn built the context. The rest went at the input price; it wrote only 147–555 tokens |
| And a cold one? | The tool list (13,790), the rest (37,628) at the input price |
| How big is the summary? | Its output: 1,065–3,804 tokens. Its `postTokens`: 3.2–6k for 54–65k conversations, 13,984 at 450k, 16,088 at 972k |
| How big is the conversation right after `/compact`? | 33,335 / 24,987 / 20,315 tokens in three sessions. Tool list + `postTokens` was 13–32% short; first prompt + `postTokens` came within 6% |
| How much do the transcripts miss? | They held 41–100% of Claude Code's own totals in 34 exited sessions (quartiles 72%, 89%, 99%), least in very short sessions and in ones that ran many subagents; 28% in a session with four web searches. Weighted by cost, 84% ($278.56 of $332.19): 84% on Opus 5.5, 71% on Sonnet 5, 23% on Haiku 4.5, which Claude Code uses for requests of its own. Where Claude Code counted the same tokens the transcripts show, its cost equalled usdash's to the micro-dollar (13 of 13) |
| A real model switch? | Moving a warm 55.9k conversation from Opus 5.5 to Sonnet 5 cost $0.143, against about $0.02 to stay |

## Appendix C: Glossary

| Term | Meaning |
|---|---|
| **Token** | The unit a model reads and writes; ~4 characters of English |
| **Context** | Everything a request sends: tools, system prompt, conversation |
| **Block** | One piece of a message: a text, an image, a tool call, a tool result |
| **Prefix** | The start of a request, up to some point; what the cache matches |
| **Layer** | One part of the request's fixed order: tools, then system prompt, then messages |
| **Breakpoint** | A marked block (`cache_control`) where the cache writes an entry |
| **Lookback** | How far back from a breakpoint a read searches for an earlier entry: 20 blocks |
| **Plain input** | Input that is neither read from nor written to the cache, at the base input price |
| **Cache write / read** | Storing a prefix (1.25× or 2× input) / reusing one (0.025–0.1× input) |
| **TTL (lifetime)** | How long a cache entry lives after the start of the last request that used it: 5 minutes or 1 hour, at least |
| **Hit rate** | Share of input tokens read from the cache |
| **Miss** | A request that re-writes what it could have read |
| **Pay-back** | How many later requests it takes for a change's saving to cover its price now |
| **Recap** | The summary Claude Code writes while you're away; its request re-sends the conversation |
| **Unlogged request** | A request the client makes that its own logs don't record |
| **Effort** | How much work (and output) the model puts into a response |
| **Thinking** | Reasoning the model does before answering; billed as output |
| **Compaction** | Replacing a conversation with a summary to shrink its context |
| **Subagent / fork** | A separate conversation a session starts; a fork inherits the parent's history and cache |
| **Batch** | Asynchronous processing at half price, within 24 hours |
| **ITPM / OTPM** | Input / output tokens per minute: rate limits; cached reads don't count toward ITPM on most models |

## Appendix D: Sources

- Pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Models overview: https://platform.claude.com/docs/en/models/overview
- Context windows: https://platform.claude.com/docs/en/build-with-claude/context-windows
- Token counting: https://platform.claude.com/docs/en/build-with-claude/token-counting
- Thinking pricing: https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost
- Effort: https://platform.claude.com/docs/en/build-with-claude/effort
- Batch processing: https://platform.claude.com/docs/en/build-with-claude/batch-processing
- Fast mode: https://platform.claude.com/docs/en/build-with-claude/fast-mode
- Rate limits: https://platform.claude.com/docs/en/api/rate-limits
- Compaction: https://platform.claude.com/docs/en/build-with-claude/compaction
- How Claude Code uses prompt caching: https://code.claude.com/docs/en/prompt-caching
- Managing Claude Code costs: https://code.claude.com/docs/en/costs
