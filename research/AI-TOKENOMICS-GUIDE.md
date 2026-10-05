# AI tokenomics: a field guide

How the cost of using a large language model is built, why it moves, and how to work out any case from ten principles, one formula and two decision trees. Written around Claude and Claude Code, where every number here was checked. The principles carry over to any provider that bills by the token and caches prompts.

**Checked on 2026-10-05** against Anthropic's docs (Appendix D), Claude Code 2.1.288's own code (for §19), and real Claude Code sessions (2.1.278–2.1.286) measured while building usdash (Appendix B). Prices change; the principles don't. Re-check prices on Anthropic's pricing page before relying on them.

## Contents

- [How to use this guide](#how-to-use-this-guide)
- [Part 1: The mental model](#part-1-the-mental-model): [1. Ten principles](#1-ten-principles) · [2. Tokens](#2-what-a-token-is-and-what-you-pay-for) · [3. Prices and the cost formula](#3-prices-and-the-cost-formula) · [4. Reading a usage record](#4-reading-a-usage-record)
- [Part 2: Prompt caching](#part-2-prompt-caching-the-biggest-lever): [5. How the cache works](#5-how-the-cache-works) · [6. When caching pays](#6-when-caching-pays) · [7. The cache lifetime](#7-the-cache-lifetime) · [8. What breaks the cache](#8-what-breaks-the-cache) · [9. Claude Code: the next request](#9-claude-code-what-the-next-request-costs) · [10. Rate limits](#10-caching-and-rate-limits)
- [Part 3: The other levers](#part-3-the-other-levers): [11. Model choice](#11-model-choice-and-tokenizers) · [12. Output, thinking and effort](#12-output-thinking-and-effort) · [13. Batch, fast mode, data residency](#13-batch-fast-mode-data-residency-cloud-endpoints) · [14. Tools](#14-tools-and-server-tools)
- [Part 4: Conversations and agents](#part-4-conversations-and-agents): [15. Agent turns](#15-agent-turns) · [16. Price now, saving later](#16-decisions-with-a-price-now-and-a-saving-later) · [17. Breaks, resumes, subagents](#17-breaks-resumes-subagents-and-parallel-requests) · [18. /compact, /clear, /rewind](#18-compact-clear-and-rewind) · [19. Requests you don't see](#19-requests-you-dont-see)
- [Part 5: Diagnosis and FinOps](#part-5-diagnosis-and-finops): [20. Diagnosing a surprising cost](#20-diagnosing-a-surprising-cost) · [21. Metrics](#21-metrics-to-watch) · [22. Unit economics](#22-unit-economics-and-forecasting) · [23. Governance](#23-governance-levers)
- [Part 6: Practice](#part-6-practice): [24. Practice problems](#24-practice-problems)
- Appendices: [A. Reference numbers](#appendix-a-reference-numbers) · [B. Evidence](#appendix-b-evidence-from-real-sessions) · [C. Glossary](#appendix-c-glossary) · [D. Sources](#appendix-d-sources)

## How to use this guide

Every cost question is answered the same way:

1. **List the requests.** Every tool step, retry and subagent, and the client's own background requests (§19).
2. **Split each request's input** into read, written and plain, with the rule in §5 (for Claude Code, the tree in §9).
3. **Add the output**, thinking included.
4. **Price it** with the formula in §3.
5. **Compare options over a horizon.** Separate what's paid once from what's paid on every request (§16).
6. **Check against reality.** A real request's usage fields (§4) are the ground truth.

When a result surprises you, it's one of three things: something changed early in the request (P4), more time passed than the cache lifetime (P5), or a request you didn't see (P8). §20 runs the method backwards, from a bill to its cause.

Three things run through the guide:
- **Principle tags.** (P4) means "this follows from principle 4".
- **The running session.** One Claude Code session on Opus 5.5, introduced in §3, comes back in each part with one thing changed at a time. Appendix A collects its figures.
- **On the wire.** Real usage records from Claude Code's transcripts, for the same situations.

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

**The tokenizer belongs to the model.** Claude Opus 4.7 and later models (Opus 5.5, Opus 5, Sonnet 5.5, Sonnet 5, Fable) use a newer tokenizer that produces about 30% more tokens for the same text than Haiku 4.5, Sonnet 4.6, Opus 4.6 and earlier (measured: 1.32–1.35×). So:
- **A price per token isn't comparable across tokenizers.** Compare the cost of the same text.
- **Token counts don't transfer.** A count measured on one model is wrong on the other; recount with the target model.

**What counts as input**, all of it, on every request (P1, P2):
- The tool definitions, plus a small system prompt the API adds when tools are present (about 300–700 tokens, depending on the model)
- The system prompt
- Every earlier message: yours, the model's replies, tool calls and tool results, images and documents
- On Opus 4.5 and later, Sonnet 4.6 and later, and the Fable models, the model's earlier thinking blocks (Haiku models drop them)

**What counts as output:** the reply, tool calls, and **thinking**. Thinking is billed in full even when the API only shows you a summary of it.

**The context window** is the most one request can hold: input plus output. It's 1M tokens on the current Opus, Sonnet and Fable models (200k on Haiku 4.5), with up to 128k output (64k on Haiku 4.5). On Claude 4.6 and later models, a 900k-token request costs the same per token as a 9k-token one: there's no long-context premium.

### 3. Prices and the cost formula

USD per million tokens (MTok), from Anthropic's pricing page on 2026-10-01. `p` is the base input price, `r` the cache-read multiplier.

| Model | Input `p` | 5-min write | 1-hour write | Cache read | Output | `r` | Min. cacheable |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fable 5.1, Mythos 5.1* | 10.00 | 12.50 | 20.00 | 0.25 | 50.00 | 0.025 | 512 |
| Fable 5, Mythos 5* | 10.00 | 12.50 | 20.00 | 1.00 | 50.00 | 0.1 | 512 |
| **Opus 5.5** | **4.00** | **5.00** | **8.00** | **0.20** | **20.00** | **0.05** | **512** |
| Opus 5 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 | 0.1 | 512 |
| Opus 4.8 / 4.7 / 4.6 / 4.5 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 | 0.1 | 1,024 / 2,048 / 4,096 / 4,096 |
| **Sonnet 5.5** | **2.00** | **2.50** | **4.00** | **0.20** | **10.00** | **0.1** | **512** |
| Sonnet 5 | 2.00 | 2.50 | 4.00 | 0.20 | 10.00 | 0.1 | 1,024 |
| Sonnet 4.6, 4.5 | 3.00 | 3.75 | 6.00 | 0.30 | 15.00 | 0.1 | 1,024 |
| Haiku 4.5 | 1.00 | 1.25 | 2.00 | 0.10 | 5.00 | 0.1 | 4,096 |

*Limited availability, by invitation.

**One request costs:**

```
cost = p × ( plain + 1.25·w5m + 2·w1h + r·read + 5·out ) ÷ 1,000,000
         × modifiers
     + $0.01 × web searches
```

| Modifier | Value | When |
|---|---|---|
| Batch | × 0.5 | The Batch API (§13) |
| Fast mode | × 2 | Opus 5.5 ($8 / $40), Opus 5 and Opus 4.8 ($10 / $50). Cache multiples apply on top |
| US-only inference | × 1.1 | `inference_geo: "us"`, Claude 4.6 and later |
| Regional endpoints | + 10% | Bedrock and Google Cloud regional or multi-region endpoints, Claude 4.5 and later (those platforms publish their own prices) |
| Contract | your rate | A negotiated discount |

The modifiers multiply each other and the token prices (P7); per-use fees are added after them.

Read the price table left to right and the whole game is visible. Reading a token from the cache is 12 to 80 times cheaper than writing it, depending on the model and the lifetime, and output is the most expensive token there is. Two things catch people out:
- **Opus 5.5, Sonnet 5.5 and Sonnet 5 read the cache at the same $0.20.** Moving a warm conversation from Opus 5.5 to Sonnet 5.5 saves nothing on its cached part.
- **Fable 5.1 reads at almost Opus 5.5's price**, despite costing 2.5× more for everything else.

**The running session.** The session this guide follows: Claude Code on Opus 5.5, with the 1-hour cache a subscription gets.
- **The start of every request** is the same 25k tokens: Claude Code's tool list (about 23k) and its system prompt (about 2k).
- **Your first message** adds 10k: what you type, plus what Claude Code attaches to a session's first prompt (the environment, the lists of skills and agents).
- **Each later message** adds 2k: the previous reply and your new message. Each reply is 1k output tokens.

Its **first request** sends 35k tokens. Other sessions on the machine already have the 25k start cached (P4):

| Tokens | Price per MTok | Cost |
|---|---:|---:|
| 25,000 read | $0.20 | $0.005 |
| 10,000 written (1-hour) | $8 | $0.080 |
| 1,000 output | $20 | $0.020 |
| **Total** | | **$0.105** |

With nothing cached yet, all 35k would be written: $0.30. The **second request** sends 37k: it reads the 35k back ($0.007), writes its new 2k ($0.016) and outputs 1k ($0.020), **$0.043**. Each warm message then costs a little more than the last, as the conversation it reads back grows (P2).

### 4. Reading a usage record

Every response reports its own `usage`, already split into the formula's variables:

| Field | Variable | Note |
|---|---|---|
| `input_tokens` | `plain` | Only what comes after the last breakpoint. **It isn't the prompt size** |
| `cache_read_input_tokens` | `read` | |
| `cache_creation.ephemeral_5m_input_tokens` | `w5m` | `cache_creation_input_tokens` is the 5-minute and 1-hour writes together |
| `cache_creation.ephemeral_1h_input_tokens` | `w1h` | |
| `output_tokens` | `out` | Thinking included; `output_tokens_details.thinking_tokens` says how much |
| `server_tool_use.web_search_requests` | web searches | |
| `speed`, `inference_geo` | modifiers | `"fast"` → × 2, `"us"` → × 1.1 |

```
prompt size = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
hit rate    = cache_read_input_tokens ÷ prompt size
```

**On the wire.** Claude Code writes each reply into its transcript (`~/.claude/projects/<folder>/<session>.jsonl`) with the usage the API reported. It doesn't write the request it sent, so a transcript shows what each request cost, not what it contained. The first request of a real session on Opus 5.5 with the 1-hour cache, trimmed to the fields that matter:

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

| Field | Tokens | Price per MTok | Cost |
|---|---:|---:|---:|
| `input_tokens` | 2 | $4 | $0.000008 |
| `cache_read_input_tokens` | 23,167 | $0.20 | $0.004633 |
| `ephemeral_1h_input_tokens` | 11,977 | $8 | $0.095816 |
| `output_tokens` | 121 | $20 | $0.002420 |
| **Total** | 35,146 in | | **$0.102877** |

It's the running session's first request, for real. The 23,167 tokens read back are Claude Code's tool list, kept warm by another session (P4), so the hit rate is 66%: a session's first request can only read back what it shares with others.

---

## Part 2: Prompt caching, the biggest lever

### 5. How the cache works

A request is sent in a fixed order: **tools → system → messages**. The cache stores the model's processed state for prefixes of that sequence, written only at marked points (*breakpoints*, `cache_control`, at most 4 per request). A later request reuses an entry only if its own start matches it byte for byte (P4).

```
┌──────────────┬──────────────┬─────────────────────────────────────────────┐
│ tools  ~23k  │ system  ~2k  │ messages: first prompt … latest (growing)   │
└──────────────┴──────────────┴─────────────────────────────────────────────┘
               ▲ breakpoint   ▲ breakpoint                       breakpoint ▲
A change in any layer re-processes that layer and everything to its right.
```

**The rule.** Walk a request from its start:

```
READ    = the longest prefix that ends at a cache entry which
            • is on the same model, in the same workspace (organisation on Bedrock and Google Cloud)
            • matches this request byte for byte up to that point
            • lies within 20 blocks before one of this request's breakpoints
            • was read or written less than one lifetime ago, counted from that request's start
            • belongs to a request whose response had already started
WRITTEN = from the end of READ to this request's last breakpoint     → 1.25p (5-minute) or 2p (1-hour)
PLAIN   = everything after the last breakpoint                       → p
```

Everything else about caching follows from these lines:
- **A read stops at an entry.** A change in the middle of the conversation reads back to the last entry before it, not to the byte before it (P4).
- **Below the minimum size (§3), nothing is cached**, and no error says so: both cache counts read 0.
- **A block** is one text, image, tool call or tool result. A run of consecutive tool calls counts as one block, and so does the run of their results. A turn that adds more than 20 blocks can lose the previous entry; a second breakpoint fixes it.
- **Requests sent at the same moment all write**, since none has started its response. Send one, then the rest once its response has begun (§17).
- **The start is shared** (P4). In Claude Code, the tool list is the same in every session on the same model, version and tool set, so other sessions usually keep it warm. The system prompt names the folder's memory paths, so only sessions in the same folder share it.

**On the wire:** a new session read back 23,167 tokens, the tool list other sessions had cached. Eight seconds later, a second new session in the same folder read back 25,084: the tool list plus the system prompt the first had just written.

**What Claude Code's request looks like.** A schematic, not a captured request, since transcripts don't keep what was sent. In real sessions, reads stop at the end of the tool list (23,167 tokens), the end of the system prompt (about 25k) and the latest message, so that's where the breakpoints are:

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

With the API, **automatic caching** (one `cache_control` at the top level of the request) moves the breakpoint to the last block of each request, which suits a growing conversation. Claude Code places its breakpoints itself (P10).

### 6. When caching pays

A write costs a premium over plain input; each read saves most of the input price. So a cached prefix pays for itself after this many reads:

```
reads to break even = (write multiplier − 1) ÷ (1 − r)
```

| | 5-minute cache | 1-hour cache |
|---|---:|---:|
| r = 0.1 (most models) | 0.28: **the first read** | 1.11: **the second read** |
| Opus 5.5 (r = 0.05) | 0.26 | 1.05 |
| Fable 5.1 (r = 0.025) | 0.26 | 1.03 |

**Example: the running session's first 20 messages** (P2, P4). Each request sends 2k more than the last, from 35k to 73k: 1.08M input tokens in all.
- **Without caching**, all of it is plain input: $4.32, plus $0.40 of output, **$4.72**.
- **With the 1-hour cache**, each request reads the previous one back and writes only its 2k. The input costs $0.59, and the 20 messages **$0.99**, 79% less. Output ($0.40) is now the biggest part.
- **The trap:** a 21st message after the cache has expired sends 75k. With the start still cached it writes the other 50k: **$0.43**, against $0.05 warm, over 40% of what the 20 messages cost together. With nothing cached, $0.62.

### 7. The cache lifetime

- **Two lifetimes:** 5 minutes or 1 hour. The 1-hour cache costs 2× input to write instead of 1.25×; reads cost the same.
- **The clock starts at the start of the request** that wrote or read the entry, not when the reply ends. A 4-minute reply on a 5-minute cache leaves the next request about a minute (P5).
- **Every read restarts the clock.** A long agent run stays warm as long as each step starts within one lifetime of the previous step's start. On the wire: six 100-second steps on a 5-minute cache each read the whole conversation back (35,258 tokens and growing), writing only the 162 or so new ones.
- **The lifetime is a minimum.** Entries are deleted "promptly, though not immediately" after it. One 5-minute entry was read back half a minute late; two others were gone at 5¾ and 10½ minutes (Appendix B). Plan on the minimum.
- **Requests you don't see restart it too** (P5, P8). On the wire: on a 5-minute cache, a message 6 minutes 36 seconds after the last request read all 36,330 tokens back, because Claude Code's recap had re-read the conversation 3 minutes in (§19). So a countdown worked out from your logs is a lower bound: the cache may still be warm after it reaches zero.

**When it runs out: the running session at lunch.** By midday the conversation is 100k tokens. You come back after 90 minutes, and the 1-hour cache has expired:
- A warm message would read 100k ($0.020), write 2k ($0.016) and output 1k ($0.020): **$0.056**.
- This one reads back only the 25k start, which other sessions kept warm, and writes the other 77k ($0.616): **$0.64**, eleven times more.
- usdash shows both ends before you send: `$0.02 now` while warm, `up to $0.80` once expired (all 100k written). The real $0.64 lands under the top because the start stayed cached.

On the wire: a session on a 5-minute cache ran a 331-second tool. The next request read back only the start (25,209 tokens) and wrote the rest again (10,224): $0.0562, against about $0.01 warm.

**5-minute or 1-hour?** The 1-hour cache costs an extra `0.75p` on every token written. The 5-minute cache costs an extra `(1.25 − r)p` on every token it has to write again after a pause that the 1-hour cache would have survived. So:

```
the 1-hour cache wins when
    tokens re-written after 5–60 minute pauses  >  0.75 ÷ (1.25 − r)  ×  all tokens written
                                                   (0.65 most models · 0.625 Opus 5.5 · 0.61 Fable 5.1)
```

| Typical gap between requests | Choose |
|---|---|
| Under 5 minutes | **5-minute.** Every request refreshes it, so the 1-hour price buys nothing |
| 5–60 minutes | **1-hour.** One such pause once the context is near full size usually pays for it |
| Over an hour | Neither survives it. Expect a full re-write |

**Example: a day in the running session**, in 6 bursts of 10 messages with 15-minute breaks, with the context kept around 100k to keep the arithmetic simple and nothing else keeping it cached:
- **5-minute cache:** a warm message costs $0.050 (its 2k written at $5). The day's first message and the first after each break write all 102k ($0.51, plus output). The day: 6 × $0.53 + 54 × $0.050 = **$5.88**.
- **1-hour cache:** a warm message costs $0.056 (its 2k written at $8). Only the day's first message writes all 102k ($0.816, plus output). The day: $0.84 + 59 × $0.056 = **$4.14**.

**Keep-alive pings (API).** A request with `max_tokens: 0` reads the prompt without generating anything, restarting a 5-minute entry's clock for one cache read (`r·p` per token). The 1-hour cache's premium is `0.75p` per token written, so pings win while their count stays under `0.75 ÷ r`: 7.5 on most models (about half an hour of idle time at one ping every 4½ minutes), 15 on Opus 5.5, 30 on Fable 5.1.

**Which lifetime Claude Code uses** (P10). It sorts every request into one of two buckets:

| Requests | Subscription, within plan usage | Usage credits, API key or cloud provider |
|---|---|---|
| The main conversation: your turns, `claude -p` runs, Agent SDK turns | 1 hour | 5 minutes |
| Everything else: subagents, forks, compaction, titles | 5 minutes (a few server-chosen helpers get 1 hour) | 5 minutes |

Going past your plan's usage onto usage credits drops the main conversation to 5 minutes. Fast mode, which always draws on usage credits, still got the 1-hour cache within plan usage (measured). Set either bucket yourself with `promptCacheTtl` / `CLAUDE_CODE_PROMPT_CACHE_TTL` and `subagentPromptCacheTtl` / `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` (`5m` or `1h`).

### 8. What breaks the cache

The principle: **anything that changes bytes early in the request re-processes everything after it** (P4). The layer it sits in says how much.

| Change | Tools | System | Messages |
|---|:---:|:---:|:---:|
| Tool definitions added, removed or edited | ✘ | ✘ | ✘ |
| A different model (each model has its own cache) | ✘ | ✘ | ✘ |
| System prompt edited | ✓ | ✘ | ✘ |
| Web search or citations turned on or off; speed switched (API) | ✓ | ✘ | ✘ |
| `tool_choice` changed; images added or removed | ✓ | ✓ | ✘ |
| Thinking settings, or effort, changed on the request | ✘ on some models | ✘ on some models | ✘ |
| Messages, tool calls and results appended | ✓ | ✓ | ✓ |
| More time passed than the lifetime | ✘ | ✘ | ✘ |

✓ read from the cache, ✘ written again. "On some models": the setting is written into the prompt, and some models place it ahead of the tools and system prompt. Effort changed *per message* instead (a beta) keeps the cache, and setting effort to the model's default is the same as leaving it out. After a lifetime passes, the shared start may still be warm from other sessions (P4).

**On the wire**, three changes in a row in one real Claude Code session on the 1-hour cache:

| What happened | Read back | Written | Cost |
|---|---:|---:|---:|
| Opus 5.5, effort high → low, 18 minutes after the last request | 34,989 of 34,991 | 229 | $0.0091 |
| Then a switch to Opus 5 | 23,167 (Opus 5's tool list, cached by another session) | 12,471 | $0.1368 |
| Then Opus 5, effort low → high | 23,167 | 12,698 | $0.1388 |

The first kept the cache because Claude Code sends Opus 5.5's effort per message (P10). The switch and the Opus 5 effort change both wrote the conversation again.

### 9. Claude Code: what the next request costs

Claude Code appends most changes to the end of the conversation, which keeps the cache (P4, P10). This tree covers the ones that don't, and the ones people expect to break it but don't:

```
What happened since the conversation's last request?
│
├─ Nothing special, within the lifetime ── read everything; write only what's new
│
├─ More time than the lifetime passed ──── read the shared start (tool list; system prompt too in the same
│                                          folder) if other sessions kept it warm; write the rest.
│                                          An unseen request may have kept it all warm (§19)
│
├─ Model switch ────────────────────────── like an expired cache, on the new model's cache (its tool list
│   also: opusplan entering or leaving     is often warm from other sessions)
│   plan mode, an automatic model fallback,
│   a skill whose frontmatter names another model
│
├─ Effort change
│   ├─ Opus 5.5, Sonnet 5.5, Fable 5.1, on an API key or subscription ── cache kept
│   └─ anything else (Opus 5, Bedrock, Google Cloud, …) ─────────────── treat it as a model switch
│
├─ Fast mode turned on ─────────────────── first time in the conversation: nothing is read (its header is
│                                          part of the cache key); the whole context is written at fast
│                                          prices. Later toggles, on or off, keep the cache
│
├─ Tool definitions changed ────────────── write everything again. With tool search (the default), MCP servers
│                                          connecting and deny rules for whole tools leave the tool list alone
│
├─ Old images dropped (over the limit) ─── re-process from the earliest dropped image, once per batch dropped
├─ /compact ────────────────────────────── §18: a summarising request, then a new, short history to write
├─ /clear ──────────────────────────────── free; the shared start stays cached
├─ /rewind ─────────────────────────────── reads the earlier turn's entry: later turns read through it, keeping it warm
├─ --resume ────────────────────────────── within the lifetime it usually reads everything back (8 of 10 measured);
│                                          after it, writes everything again
├─ Claude Code upgraded ────────────────── a new conversation writes from the top; a resumed one keeps its system
│                                          prompt and can read its cache (2 of 2 measured)
├─ Waited for a subagent ───────────────── the subagent has its own cache. The parent's clock keeps running,
│                                          so a long subagent can leave it expired (§17)
│
└─ Cache kept ──────────────────────────── editing files or CLAUDE.md (CLAUDE.md applies only after /clear,
                                           /compact or a restart), changing permission mode or output style,
                                           running skills and commands, plan mode, /recap, enabling plugins
                                           that add only skills, commands, agents or hooks
```

### 10. Caching and rate limits

Rate limits count requests per minute, input tokens per minute (ITPM) and output tokens per minute (OTPM). **On most models, tokens read from the cache don't count toward ITPM**; only plain input and cache writes do. With an 80% hit rate, a 2M ITPM limit processes 10M input tokens a minute. `max_tokens` doesn't count toward OTPM, so there's no rate-limit reason to set it low. Caching is a throughput lever as much as a cost lever.

---

## Part 3: The other levers

### 11. Model choice and tokenizers

Price per token is half the story. The other half is tokens per task (P9), which depends on:
- **The tokenizer.** The same text is ~30% more tokens on Opus 4.7 and later. 77,000 tokens on Haiku 4.5 are 100,000–104,000 on Opus 5.5 or Sonnet 5.5.
- **How much the model writes and thinks** for this task at this effort.
- **How many attempts it takes.** A cheaper model that needs a second pass costs two passes.

**Compare models on the same task, per finished result:** tokens in and out, times price, times attempts. The docs' own example: 10,000 support tickets of ~3,700 tokens each on Haiku 4.5 cost about $37.

### 12. Output, thinking and effort

- **Output is the expensive side:** 5× input on every current model. A 2,000-token reply on Opus 5.5 costs $0.04; reading a 100k conversation from its cache costs $0.02.
- **Thinking is output.** You pay for all of it, even when the response shows a summary or nothing. `thinking_tokens` in the usage says how much.
- **Effort** (`low`, `medium`, `high`, `xhigh`, `max`) scales every output token, including thinking, tool calls and explanations, and lower effort also means fewer tool calls. It's a behavioural signal, not a hard budget. The API's default is `medium` on Opus 5.5 and `high` on the others (Haiku 4.5 has no effort setting); a client can set its own, in Claude Code with `/effort`.
- **Earlier thinking stays in context** (§2), re-sent as input on every request: cheap from the cache, expensive after a miss.

**The effort decision on a warm cache:** where an effort change keeps the cache (§9), the saving is immediate: output before minus output after, times the output price. In the running session, going from ~3,000 to ~800 output tokens a message saves $0.044 a message. Where it doesn't keep the cache, treat it as a model switch (§16).

### 13. Batch, fast mode, data residency, cloud endpoints

All four are multipliers. They stack with each other and with the cache multipliers (P7).

| Lever | Effect | What to know |
|---|---|---|
| **Batch API** | × 0.5 on all tokens | Most batches finish within an hour; requests not done in 24 hours expire, unbilled. Cache hits are best-effort, so put a shared prefix on the 1-hour cache. No fast mode and no `max_tokens: 0` in a batch |
| **Fast mode** | × 2: Opus 5.5 $8 / $40, Opus 5 and 4.8 $10 / $50 | Up to 2.5× faster output. Claude API, and Claude Code subscriptions, where it draws on usage credits; not on Bedrock or Google Cloud. In Claude Code the first fast request writes the whole context again at fast prices: 200k tokens on Opus 5.5 cost $3.20 that way, $0.32 at a 20k start (§9) |
| **US-only inference** | × 1.1 on everything | Claude 4.6 and later: `inference_geo: "us"` on the request, or the workspace's default |
| **Regional endpoints** | + 10% over global | Bedrock and Google Cloud, Claude 4.5 and later |

**Stacking example:** a 1-hour cache write on Opus 5.5 lists at $8 a million. With US-only inference it's $8.80; in a batch as well, $4.40.

**On the wire:**
- **Fast mode, turned on in a live session:** the first fast request read nothing and wrote all 6,282 tokens at the fast 1-hour price ($16 a million): $0.1024, against $0.0230 for the standard request before it. After `/fast` off, the next request read the conversation back.
- **US-only inference:** a run's tokens cost exactly 1.1× list, $0.015279 instead of $0.013890, and Claude Code's own record agreed to the millionth.

### 14. Tools and server tools

- **Tool definitions are input on every request** (P1, P2): each tool's name, description and schema, plus the tool-use system prompt. Tools the client defers until needed (Claude Code's default for MCP tools) cost nothing until loaded.
- **Tool calls are output; tool results are input** in the request that follows, and in every request after it.
- **Web search:** $10 per 1,000 searches, plus its results as input tokens. A failed search isn't billed. Claude Code's WebSearch tool searches in a request of its own that its transcripts don't log (§19).
- **Web fetch:** only the fetched content's tokens. An average web page is ~2,500 tokens; a 500 kB PDF ~125,000.
- **Code execution:** free alongside web search or web fetch. Otherwise it's billed by container time (1,550 free hours per organisation a month, then $0.05 an hour per container, at least 5 minutes each time).

---

## Part 4: Conversations and agents

### 15. Agent turns

One prompt to an agent is not one request. It's one request per step: the model calls a tool, the result goes back, the model continues. **Every step re-sends the whole context** (P2).

**Example:** the running session at 100k, warm, and one prompt that takes 8 tool steps. Each step adds a 3k-token tool result and produces 300 output tokens. The turn costs **$0.42**:
- **Re-reading the context** (8 × ~110k tokens): $0.18
- **Writing the new tokens** (24k, at the 1-hour price): $0.19
- **Output** (2,400 tokens): $0.05

```
turn ≈ steps × context × read price  +  new tokens × write price  +  output × output price
```

So in a warm agent loop, **the new tokens cost about as much as re-reading the context**, and a big tool result is paid twice: once when written, then as reads on every later step. Keep tool output small: filter a test log down to its failures before the model sees it, or let a subagent read the big file and return a summary.

### 16. Decisions with a price now and a saving later

Most choices in a session trade a one-time cost for a per-request saving (P6):

```
P = what the change costs now, compared with not changing
s = what it saves on each later request
m = P ÷ s = requests until it pays back          → worth it if more than m requests are left
```

**Example: switching the running session to Sonnet 5.5** at 100k, warm, with 2k new tokens and 1k output per later message:
- **Now:** Sonnet 5.5 must write all 100k into its own cache (P4), $0.40, where Opus would read them for $0.02. P = **$0.38**.
- **Later:** both read the 100k at the same $0.20, so the saving is only on the new tokens and output: $0.056 a message on Opus against $0.038 on Sonnet. s = **$0.018**.
- **Pay-back:** 21 messages.
- **After a break**, the cache has expired and both must write everything (less any start other sessions keep cached): $0.40 on Sonnet against $0.80 on Opus. Switching down is then cheaper from the first message.

On the wire: a real switch from Opus 5.5 to Sonnet 5.5 at about 35k. Sonnet read back 23,167 tokens (its tool list, cached by another session) and wrote 11,724: $0.0519, against $0.0084 for the warm Opus message before it.

- **When you don't know how many requests are left**, use the rent-or-buy rule: keep paying the extra per request until what you've paid adds up to P, then switch. You never pay more than about twice what hindsight would have chosen.
- **A break resets the question.** Once the cache has expired, P usually drops to zero or below: switch or compact *before* you resume work.
- **Round trips:** switching away and coming back within the lifetime can reuse the first model's cache, but the lookback reaches only 20 blocks. Budget a full re-write and treat reuse as a bonus.

### 17. Breaks, resumes, subagents and parallel requests

- **A break longer than the lifetime** makes the next request write the whole context again, less any start other sessions keep cached (P4, P5). It's the most common reason a session "suddenly" costs more: the running session's lunch turns a $0.056 message into a $0.64 one (§7).
- **Resuming** (`claude --resume`) re-sends the whole conversation, keeping the system prompt it started with (P10). Within the lifetime it reads back what's still cached; after it, it writes it again (P5). On the wire: a resume 5 minutes after the last request read all 35,094 tokens back; one 22½ hours later read back only the 23,167-token tool list.
- **A subagent** is a separate conversation with its own system prompt, tools and cache (5 minutes by default). It doesn't read the parent's cache, and the parent's clock keeps running while it waits (P4, P5). On the wire: a parent on a 5-minute cache waited 10 minutes for a subagent. The subagent's six steps each read its own cache back (25–28k), but the parent's next request read back only the 23,292-token start and wrote 12,932 tokens again.
- **A fork** inherits the parent's system prompt, tools and conversation exactly, so its first request reads the parent's cache. Check that a feature really is a fork: a skill run in a forked subagent in this project's review read nothing back and wrote its 44k-token start again.
- **Parallel requests sharing a prefix:** an entry exists only once the first response has begun (§5). Ten requests sharing a 30k prefix on Sonnet 5.5, sent at once, write it ten times: **$0.75**. Sending one first and the other nine once it has started: **$0.13**.

### 18. /compact, /clear and /rewind

**`/compact`** replaces the conversation with a summary. It costs a summarising request now, and saves on every later message (P6).

- **The summarising request** has the same system prompt, tools and history as the conversation, plus the summarisation instruction, and isn't logged (P8). Measured on real compactions (Appendix B):
  - **While warm,** it read back what the latest turn's first request had cached (everything up to and including the last prompt you typed), and sent the rest at the plain input price.
  - **After a break,** it read back only the tool list and sent the rest at the plain input price.
  - **Its output,** the summary, was 1,065–3,804 tokens.
- **After it**, the next request sends the shared start, what Claude Code attaches to a session's first prompt, and the summary: about the session's first prompt plus the summary (within 6% on three real compactions). It writes all but the shared start; later requests read it.

**Example: compacting the running session at 100k**, its latest turn begun at 90k, with a 4k summary (about 2.5k output tokens):
- **Compacting now** costs **$0.108**: 90k read, 10k plain input, the summary generated.
- **Compacting after the cache expired** costs **$0.355**: 25k read, 75k plain input, the summary.
- **After it**, the conversation is 39k (the first prompt's 35k and the summary's 4k), so each later message reads 39k instead of 100k, saving **$0.012**. The first message after writes 14k it would otherwise have read: $0.109 more.
- **Pay-back:** about 18 messages. Compacting a warm 100k conversation only to save money pays back slowly; it pays sooner at larger contexts, and at once before a break: lunch after compacting costs $0.15 instead of $0.64 (§7). **Compact before a break, not after it.** When one prompt built most of the conversation (a single "read all these files" turn), the warm compaction can't read much back, and the two cost nearly the same.

On the wire: a 40k conversation compacted within a minute of its last reply. Claude Code's own record showed 75,114 more tokens read, 6,851 more plain input, 349 more written and 1,296 more output than its transcripts. That fits two unlogged requests: the compaction (about 35k read, 7k plain input, a 1.3k summary) and one more request that read the whole 40k, a prompt suggestion or a memory extraction (§19).

**`/clear`** starts over and costs nothing itself. The shared start usually stays cached. Use it when the topic changes.

**`/rewind`** goes back to an earlier turn and drops what came after it: the cheapest way to abandon a wrong path. The next request reads that turn's entry, which every later turn kept warm by reading through it.

### 19. Requests you don't see

Claude Code makes requests you never typed, and their usage never reaches the transcript (P8). This list comes from Claude Code 2.1.288's code; the costs are measured where given.

**Copies of the conversation.** These run on the session's model with the session's own start, so each reads the session's cache, restarting its clock (P5), for about one cache read of the whole context (550k on Opus 5.5: $0.11) plus its answer.

| Request | When | In the transcript |
|---|---|---|
| **Prompt suggestion** | After a turn, in interactive sessions (not `claude -p`), once the conversation has two replies. Skipped when the window isn't focused or the last request moved more than ~10k new tokens; throttled after a run of unused suggestions | Nothing |
| **Memory extraction** | Right after a turn, when auto memory is on and you wrote something new. It runs while the cache is still warm, for up to 5 steps of its own | Nothing |
| **Recap** | About 3 minutes after the last turn, or as soon as you switch away after that, if the window isn't focused and less than 90% of the lifetime has passed. Shown when you come back | A record (`away_summary`), no usage |
| **Compaction summary** | `/compact`, or automatic compaction (§18) | A record (`compact_boundary`), no usage |
| **`/btw` side question** | When you ask one | Nothing; the question is only in the input history |
| **`/rename` without a name** | When you run it | Nothing |
| **Auto dream** (memory consolidation) | At most once a day, once 5 sessions have run since the last one (the defaults), when auto memory is on | Nothing |
| **Subagent progress summary** | While a subagent runs | Nothing (it reads the subagent's cache, not the session's) |

Some of these also cache the conversation with its latest reply: in real sessions, 140 of 1,913 next messages read back more than the previous request had sent.

**Separate small requests.** These don't touch the session's cache.

| Request | When | Model and size |
|---|---|---|
| **Session title** | Early in the session | Haiku 4.5, a short excerpt: 893–975 tokens in, 10–15 out (measured) |
| **Tool-batch labels** | After tool batches (one-line labels for the mobile app) | A small model, no caching |
| **WebFetch** | Each fetch | A small model reads the page with your prompt; grows with the page |
| **WebSearch** | Each search | The session's model, or a small one when a server flag says so, no caching; the results count as input. Measured on Haiku 4.5: one search came to $0.023263 with its $0.01 fee (12,633 tokens in and 126 out, the session's title included) |
| **Auto mode classifier** | Tool calls that need a decision in auto mode | Its own model and copy of the conversation; not measured |
| **Prompt and agent hooks** | Each time a hook of those types that you configured fires | Its own request |
| **Background-agent naming and status** | When background agents run | Small requests |
| **Rare, on demand** | `/insights`, `/feedback`, validating a model name, MCP date parsing, artifact comment replies, auto mode setup and critique, plugin evals, a title when moving a session to the cloud | One-off, small |

- **How much the transcripts miss:** in real sessions they held 41–100% of Claude Code's own totals (a median of 89%; 84% weighted by cost), and only 28% in a session with four web searches.
- **The client's own total.** When a session exits, Claude Code writes what it counted, per model, into the transcript as a `cost-state` record. On the wire, for the compacted session in §18:

  ```json
  "totalCostUSD": 0.2289108,
  "modelUsage": {
    "claude-haiku-4-5-20251001": { "inputTokens": 975, "outputTokens": 15, "costUSD": 0.00105 },
    "claude-opus-5-5": { "inputTokens": 6855, "outputTokens": 1923, "cacheReadInputTokens": 133384,
                         "cacheCreationInputTokens": 16913, "costUSD": 0.2278608 }
  }
  ```

  The Haiku line matches a session title. The transcript's own requests came to $0.1567.
- **The FinOps rule:** reconcile against the provider's usage report, the client's own total (`/usage`, or `cost-state` at exit) or an OpenTelemetry export, not against transcripts alone.

---

## Part 5: Diagnosis and FinOps

### 20. Diagnosing a surprising cost

```
Cost higher than expected?
│
├─ Hit rate low (read ÷ prompt size)?
│   ├─ written ≈ the whole conversation ──── the cache expired (a gap, a long reply, a long subagent),
│   │                                         a model switch, an effort change, the first fast request,
│   │                                         a tool change, a resume after the lifetime (§9)
│   ├─ read stuck at the tool list's size ── the conversation's entry was lost; the shared start survived
│   ├─ read = 0, written = 0 ─────────────── no cache markers reached the API (caching off, a gateway
│   │                                         stripping them), or the prefix is under the minimum size
│   ├─ every request writes it all again ─── something early changes each time (a timestamp, unsorted JSON,
│   │                                         tools loading in a different order), or turns add over 20 blocks
│   └─ parallel requests all writing ─────── none had started its response when the others were sent
│
├─ Hit rate fine, output share high ──────── effort, thinking or long replies are the lever (§12)
├─ Hit rate fine, context just large ─────── big tool results; /compact or /clear (§15, §18)
└─ Bill above your logs ──────────────────── requests you don't see (§19)
```

### 21. Metrics to watch

| Metric | Formula | What it tells you |
|---|---|---|
| **Hit rate** | read ÷ prompt size | Below ~80% in a conversational workload, something keeps changing the prefix, or pauses outlast the lifetime |
| **Miss cost** | tokens re-written that could have been read × (write − read price) | What misses cost, by cause: model switch, expiry, resume, config change |
| **Output share** | output cost ÷ total cost | If high, work on effort and replies, not caching |
| **Effective input price** | input-side cost ÷ prompt tokens | How close you are to the cache-read price |
| **Cost per unit** | total ÷ units delivered | The only number the business sees |
| **Unlogged share** | 1 − logged cost ÷ billed cost | How much your own logs miss (§19) |

**Where to read them for Claude Code:** `/usage` shows the session's hit ratio, miss count, whether the cache is warm, and the likely cause of the last miss; a status-line script can read the same figures. usdash's Stats view shows the first four over 30 days, and how much of Claude Code's own totals the transcripts hold.

### 22. Unit economics and forecasting

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

Then run the formula, and **check it against a small pilot's real usage before scaling.**

**Reference points:**
- **Claude Code across enterprise deployments** (Anthropic's figures): about $13 per developer per active day, $150–250 per developer a month, under $30 a day for 90% of users.
- **A RAG service:** a 50k-token document and 1,000 questions an hour on Sonnet 5.5 (200 tokens in, 400 out) costs **$104 an hour without caching and $15 with it**. Each question then counts only 200 tokens toward the input rate limit.
- **Offline processing:** 10,000 documents of 3k tokens in and 500 out on Haiku 4.5 cost **$55, or $27.50 through the Batch API**.

### 23. Governance levers

| Where it acts | Levers |
|---|---|
| **Budget** | Spend limits per organisation and workspace; per-user limits on team plans; alerts on the daily trend |
| **Structure** | Stable content first in the prompt; breakpoints on the last stable block; small tool outputs; stable tool sets |
| **Routing** | The cheapest model that meets the quality bar, measured per task; subagents on small models for side work |
| **Time** | The 1-hour cache for work with pauses; batch for anything that can wait a day; pre-warming with `max_tokens: 0` when first-response latency matters |
| **Visibility** | Contracted rates in the reporting (Claude Code's `modelPricing`); OpenTelemetry per user and session; reconciliation against invoices; local logs kept long enough for a baseline (Claude Code deletes transcripts after 30 days by default: `cleanupPeriodDays`) |
| **Subscription vs API** | A subscription bills plan usage, not tokens; list-price estimates are for comparison. Going past the plan onto usage credits also drops Claude Code's main conversation to the 5-minute cache |

---

## Part 6: Practice

### 24. Practice problems

Work each one with the method at the top of the guide; the answers follow.

**1. A support bot keeps a 6k-token system prompt and gets a question every 2 minutes, all day. 5-minute or 1-hour cache?**
*5 minutes.* Every question arrives within the lifetime and refreshes it for free, so the 1-hour cache's higher write price buys nothing (P5, §7).

**2. The same bot, but questions come every 20 minutes.**
*1 hour.* On a 5-minute cache every question re-writes the 6k prompt. On a 1-hour cache it's written once and read at 0.1×; it breaks even on the second read (P5, §6).

**3. Your agent's hit rate dropped from 95% to 40% after a deploy. Where do you look?**
*At what changed early in the request*, in order: tool definitions, the system prompt (a timestamp, a per-user field), images, `tool_choice`, the thinking or effort setting, the model id (P4, §8). Then check whether the breakpoint sits on a block that changes every request (§5).

**4. A developer asks why a one-line question cost $0.80 after lunch.**
*The cache expired over lunch* (P5). The question re-sent the whole 100k-token conversation at the 1-hour write price on Opus 5.5: up to $0.80, less any start other sessions kept cached (§7).

**5. Should a nightly report job over 50,000 records use the Batch API?**
*Yes*, if the results can wait up to 24 hours: half price on every token, and caching still applies, best-effort (P7, §13).

**6. You fan out 20 subagents that share a 40k-token briefing. How do you cut the input cost?**
*Send one first, and the other 19 once its response has begun,* so they read the briefing instead of each writing it (P4, §5, §17). Or pre-warm the cache with `max_tokens: 0`.

**7. Is moving a warm 200k Opus 5.5 conversation to Sonnet 5.5 worth it to save money?**
*Rarely, while it's warm.* Sonnet must write 200k tokens (~$0.80 at the 1-hour price), and both read at the same $0.20 afterwards, so only the new tokens and output get cheaper. After a break it's different: both must write everything, and Sonnet does it for half (P4, P6, §16).

**8. A session resumed the next morning read 23,167 tokens from the cache and wrote the rest. Why 23,167, and why not 0?**
*The conversation's entry had expired overnight* (P5), *but the start of the request is shared.* 23,167 is Claude Code's tool list, the same in most sessions on that model and version, and another session kept it warm (P4, §5).

**9. A subagent ran for 10 minutes. When it reported back, the parent wrote its conversation again on a 5-minute cache, but not on a 1-hour one. Why?**
*The subagent has its own conversation and cache* (P4), *and the parent sent nothing while it waited*: its 5-minute entry expired and its 1-hour one didn't (P5, §17).

**10. On a 5-minute cache, a message 6½ minutes after the last request read everything back. Was the lifetime wrong?**
*No. A request you didn't see read the conversation*: Claude Code's recap, 3 minutes in, restarted the clock (P5, P8, §19).

**11. Claude Code's total for an exited session is 30% above what its transcripts show. Which number is right?**
*Both, for what they cover.* The transcripts leave out titles, prompt suggestions, memory extraction, recaps, compaction and `/btw`; the client's own total counts them (P8, §19). For a budget, use the client's total, or divide what your logs show by the logged share (§21).

**12. You're at 150k on Opus 5.5 with a warm cache and leaving for lunch. Compact now, or after?**
*Now.* A warm compaction reads most of the conversation from the cache; after lunch it would send nearly all of it as plain input. And after lunch, the first message writes about 40k instead of 150k (P5, P6, §18).

**13. Your team works in bursts with 15-minute breaks. 5-minute or 1-hour cache?**
*1 hour.* Each break costs the 5-minute cache a full re-write of the context, while the 1-hour cache costs a little more on each small addition (P5, P6, §7).

**14. Why does a `/btw` side question never show up in the transcripts?**
*Claude Code sends it as a separate request outside the conversation and doesn't log it* (P8, P10). Only its own total at exit counts it (§19).

**15. You change effort mid-session. On Opus 5.5 the next message read everything back; on Opus 5 it wrote the conversation again. Why?**
*How the client sends the change* (P10). Claude Code sends Opus 5.5's effort per message, which keeps the cache; on Opus 5 the change is on the request itself, which doesn't (P4, §8).

**16. You turn on fast mode halfway through a 200k Opus 5.5 session. What does it cost, and does turning it off and on again cost more?**
*$3.20 once:* the first fast request writes the whole 200k at the fast 1-hour price, $16 a million, because Claude Code's fast-mode header is part of the cache key (P4, P10). Later toggles keep the cache (§9, §13).

---

## Appendix A: Reference numbers

The running session: Claude Code on Opus 5.5, 1-hour cache, a 25k shared start, 2k new tokens and 1k output per message.

| Situation | Cost |
|---|---:|
| First request (35k; the start cached elsewhere / nothing cached) | $0.105 / $0.30 |
| 20 messages from a fresh start (35k → 73k): no cache / 1-hour cache | $4.72 / $0.99 |
| Warm message at 100k | $0.056 |
| Message at 100k after the cache expired (start cached elsewhere / nothing cached) | $0.64 / $0.84 |
| A day of 6 bursts of 10 messages at ~100k: 5-minute / 1-hour cache | $5.88 / $4.14 |
| One prompt that runs 8 tool steps at 100k (3k result + 300 output each) | $0.42 |
| Switch to Sonnet 5.5 while warm at 100k | P = $0.38, s = $0.018: pays back after **21** messages |
| `/compact` at 100k: warm / after the cache expired | $0.11 / $0.36; pays back after about **18** messages |
| Lunch break after compacting first | $0.15 instead of $0.64 |
| Turning fast mode on at 200k | $3.20, once |

## Appendix B: Evidence from real sessions

What building and reviewing usdash measured in real Claude Code transcripts, compared with Claude Code's own totals: evidence for one client's behaviour at the versions measured (2.1.278–2.1.286). Re-measure after an upgrade. The regression tests in [`tests/test_real_checks.py`](../tests/test_real_checks.py) hold usdash to the 2026-09-28 sessions in [`tests/fixtures/checks`](../tests/fixtures/checks): the next message reading the last prompt back, resuming within the lifetime, a model switch across tokenizers, and the size after `/compact` waiting for the next request.

| Question | What was measured |
|---|---|
| Does the next request read back the whole previous prompt? | Yes: 1,938 of 1,939 same-model pairs within the lifetime, to within 100 tokens (the uncached remainder was at most 0.04% of a prompt). The other changed effort on Sonnet 5, which re-writes |
| How big is Claude Code's tool list? | CLI 22–25k tokens; VS Code extension 20.8k; Desktop app 36.3k; `claude -p` 10–20k depending on model and version |
| Do other sessions keep the start of a request cached? | Yes: new sessions typically read back the 23,167-token tool list another session had cached. One started eight seconds after another in the same folder read back 25,084: the tool list and the system prompt the first had just written |
| Is the tool list cached when the conversation's cache isn't? | Usually. Own model on a 1-hour cache: 36 of 39 cold starts read it back, once after 11¾ hours idle. 5-minute cache: 9 of 48 with no other session using the model, 13 of 22 with one. Another model with no session on it: once yes (Sonnet 5), once no (Opus 5.5) |
| Is the lifetime exact? | No, a minimum, but only just. With nothing refreshing it in between, one 5-minute entry was read back 5.6 minutes after its last use (all but its newest 220 tokens); 2 others were gone at 5.8 and 10.6 minutes |
| Does Claude Code's recap refresh the cache? | Yes, it re-reads the conversation. Within the lifetime it keeps the cache alive: on a 5-minute cache, the next message, 6½ minutes after the last request and 3½ after a recap, read all of it back (2.1.286). 27 of 40 recaps came within 4 minutes of the last reply, the rest 4–74 minutes after; one written 74 minutes into a pause, after the 1-hour cache had run out, wrote it again (2.1.278–2.1.283). In 2.1.288's code a recap is skipped once 90% of the lifetime has passed |
| Do other unlogged requests touch the conversation's cache? | Sometimes: with no recap in between, 140 of 1,913 next messages within the lifetime read back more than the previous request had sent, so an unlogged request had cached the conversation with its latest reply |
| Tokenizer ratio, old to new? | 0.758 in one switch, 0.74 in another (1.32–1.35× more tokens on the new tokenizer); Anthropic says ~30% more |
| Does an effort change keep the cache? | Opus 5.5 in Claude Code: 7 of 7 read everything back. Opus 5 in Claude Code: no; the request after the change read back only the tool list, which other sessions keep warm, and wrote the conversation again (12,698 of 35,867 tokens in one check) |
| Does fast mode break the cache? | Its first request does: it read nothing back, not even the tool list, in a live session and in two `claude -p` runs (2.1.286). After `/fast` off, the next request read the conversation back. On a subscription within plan usage, fast requests got the 1-hour cache |
| Does a Claude Code upgrade break the cache? | Not for a resumed conversation: resumed across 2.1.284 → 2.1.285 and 2.1.285 → 2.1.286 within the lifetime, both read the whole conversation back |
| Does resuming keep the cache? | Within the lifetime: 8 of 10 resumes read the whole conversation back (1-hour and 5-minute caches, one after a file edit); the other two, 3½ and 40 minutes after the last request, read back only the tool list. After ≥ 92 minutes: 31 of 31 re-wrote it |
| What does a warm `/compact` request read? | What the latest turn's first request had cached: 46,885 of 56,584 tokens after a four-turn session, 15,804 of 58,787 when one turn built the context. The rest went at the input price; it wrote only 147–555 tokens |
| And a cold one? | The tool list (13,790), the rest (37,628) at the input price |
| How big is the summary? | Its output: 1,065–3,804 tokens. Its `postTokens`: 3.2–6k for 54–65k conversations, 13,984 at 450k, 16,088 at 972k |
| How big is the conversation right after `/compact`? | 33,335 / 24,987 / 20,315 tokens in three sessions. Tool list + `postTokens` was 13–32% short; first prompt + `postTokens` came within 6% |
| How much do the transcripts miss? | They held 41–100% of Claude Code's own totals in 34 exited sessions (quartiles 72%, 89%, 99%), least in very short sessions and in ones that ran many subagents; 28% in a session with four web searches; a Desktop app session, $0.16 of $0.23. Weighted by cost, 84% ($278.56 of $332.19): 84% on Opus 5.5, 71% on Sonnet 5, 23% on Haiku 4.5, which Claude Code uses for requests of its own |
| Do Claude Code's prices match the list? | Wherever it counted the same tokens as the transcripts, its cost equalled the list-price calculation to the millionth of a dollar: 13 of 13 sessions by 2026-09-30, and since then Fable 5.1, fast mode on Opus 5.5 and Opus 5, and US-only inference |
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
| **Read / written / plain** | Input served from the cache (`r × p`) / stored in it (1.25p or 2p) / neither (p) |
| **TTL (lifetime)** | How long a cache entry lives after the start of the last request that used it: 5 minutes or 1 hour, at least |
| **Hit rate** | Share of input tokens read from the cache |
| **Miss** | A request that writes again what it could have read |
| **Pay-back** | How many later requests it takes for a change's saving to cover its price now |
| **Keep-alive ping** | A `max_tokens: 0` request sent only to restart a cache entry's clock |
| **Recap** | The summary Claude Code writes while you're away; its request re-reads the conversation |
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
- Fast mode: https://platform.claude.com/docs/en/build-with-claude/fast-mode and https://code.claude.com/docs/en/fast-mode
- Data residency: https://platform.claude.com/docs/en/manage-claude/data-residency
- Rate limits: https://platform.claude.com/docs/en/api/rate-limits
- Compaction: https://platform.claude.com/docs/en/build-with-claude/compaction
- How Claude Code uses prompt caching: https://code.claude.com/docs/en/prompt-caching
- Managing Claude Code costs: https://code.claude.com/docs/en/costs
- Claude Code 2.1.288's own code: when each request in §19 runs, its model, and whether it reads the session's cache
