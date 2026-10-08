# AI tokenomics: a field guide

How the cost of using a large language model is built, why it moves, and how to work out any case from ten principles, one formula and two decision trees. Written around Claude and Claude Code; the principles apply to any provider that bills by the token and caches prompts.

**Checked on 2026-10-08** against Anthropic's docs and pricing pages (Appendix E), Claude Code 2.1.293's code, and real Claude Code sessions measured while building usdash (Appendix B). Prices change and the principles don't: re-check prices before relying on them.

## Contents

- [How to use this guide](#how-to-use-this-guide)
- [Part 1: Foundations](#part-1-foundations): [1. Ten principles](#1-ten-principles) · [2. Tokens](#2-tokens-what-you-pay-for) · [3. Prices and the cost formula](#3-prices-and-the-cost-formula) · [4. Reading a usage record](#4-reading-a-usage-record)
- [Part 2: Prompt caching](#part-2-prompt-caching): [5. How the cache works](#5-how-the-cache-works) · [6. The cache lifetime](#6-the-cache-lifetime) · [7. What breaks the cache](#7-what-breaks-the-cache) · [8. Claude Code: the next request](#8-claude-code-what-the-next-request-costs) · [9. Caching decisions](#9-caching-decisions)
- [Part 3: Other levers](#part-3-other-levers): [10. Model choice](#10-model-choice-and-tokenizers) · [11. Output, thinking and effort](#11-output-thinking-and-effort) · [12. Tools](#12-tools-and-server-tools)
- [Part 4: Conversations and agents](#part-4-conversations-and-agents): [13. Agent turns](#13-agent-turns) · [14. Price now, saving later](#14-price-now-saving-later) · [15. Breaks, resumes, subagents](#15-breaks-resumes-subagents-and-parallel-requests) · [16. /compact, /clear, /rewind](#16-compact-clear-and-rewind) · [17. Requests you don't see](#17-requests-you-dont-see)
- [Part 5: Diagnosis and FinOps](#part-5-diagnosis-and-finops): [18. Diagnosing a surprising cost](#18-diagnosing-a-surprising-cost) · [19. Metrics](#19-metrics) · [20. Unit economics](#20-unit-economics-and-forecasting) · [21. Governance](#21-governance)
- [Part 6: Practice](#part-6-practice): [22. Practice problems](#22-practice-problems)
- Appendices: [A. Reference numbers](#appendix-a-reference-numbers) · [B. Evidence](#appendix-b-evidence-from-real-sessions) · [C. Claude Code's unlogged requests](#appendix-c-claude-codes-unlogged-requests) · [D. Glossary](#appendix-d-glossary) · [E. Sources](#appendix-e-sources)

## How to use this guide

Answer any cost question in six steps:

1. **List every request:** tool steps, retries, subagents, and the client's own background requests (§17).
2. **Split each request's input** into read, written and plain (§5; for Claude Code, §8).
3. **Add the output**, thinking included.
4. **Price it** with the formula (§3).
5. **Compare options over a horizon:** one-time costs against per-request savings (§14).
6. **Check against reality:** a real request's usage record is the ground truth (§4).

A surprising cost almost always comes from one of three things: something changed early in the request (P4), more time passed than the cache lifetime (P5), or a request you didn't see (P8). §18 works backwards from a bill to its cause.

**Conventions:** (P4) points to the principle a rule follows from. *The running session*, introduced in §3, is the example used throughout; Appendix A collects its figures. *On the wire* marks a real usage record from Claude Code's transcripts.

---

## Part 1: Foundations

### 1. Ten principles

The whole subject on one page. Everything later is one of them applied.

1. **You pay for tokens moved, in and out.** Input is everything the model reads: instructions, tool definitions, the conversation, files, tool results, images. Output is everything it writes, thinking included.
2. **The model remembers nothing.** Every request re-sends the whole context, so cost grows with *context size × number of requests*, not with the length of your last message.
3. **Know one number per model: its input price.** Everything else is a multiple of it: output 5×, a cache write 1.25× (5-minute) or 2× (1-hour), a cache read 0.1× or less.
4. **The cache is a prefix, in layers, and shared.** It serves only an exact byte-for-byte match from the start of the request (tools, then system prompt, then messages), on the same model, within its lifetime. A change early on re-processes everything after it. The early layers are common to many requests, so other sessions keep them warm: a miss usually still reads the start back.
5. **Time is money.** The cache expires a fixed time after the start of the last request that used it, including requests you don't see. A pause longer than that makes the next request expensive.
6. **Every change has a price now and a saving later.** Pay-back = price now ÷ saving per request. A change is worth it only if more requests than that are left.
7. **Discounts multiply.** Batch, caching, data residency and fast mode multiply each other; they don't add.
8. **What you can't see still costs.** Clients send requests you never typed. Reconcile against the bill, not just your logs.
9. **Optimise cost per outcome, not per token.** A cheaper model or lower effort that needs more steps or retries can cost more for the same result.
10. **The client builds the request.** What it sends, in what order and when, decides what's cached. Check what your client actually does.

### 2. Tokens: what you pay for

A **token** is about 4 characters, or ¾ of an English word; code and other languages take more.

- **Input,** on every request (P1, P2): the tool definitions (plus about 290–800 tokens of tool instructions the API adds, by model and `tool_choice`), the system prompt, and every earlier message: yours, the replies, tool calls and results, images, and the model's earlier thinking (kept on Opus 4.5 and later, Sonnet 4.6 and later, Haiku 5.5 and Fable; Haiku 4.5 drops it).
- **Output:** the reply, tool calls and thinking. Thinking is billed in full even when only a summary is shown.
- **Limits:** a context window of 1M tokens (200k on Haiku 4.5), up to 128k of it output (64k on Haiku 4.5). From Claude 4.6 on, a long request costs the same per token as a short one, except on Haiku 5.5: a prompt over 100k tokens pays 5× for every token (§3).
- **The tokenizer belongs to the model.** Opus 4.7 and later models (Opus 5.5, Opus 5, Sonnet 5.5, Sonnet 5, Haiku 5.5, Fable) count about 30% more tokens for the same text than Haiku 4.5, Sonnet 4.6 and older models (measured: 1.32–1.35×). Compare models on the cost of the same text, and recount tokens on the target model.

### 3. Prices and the cost formula

USD per million tokens (MTok), from Anthropic's pricing pages on 2026-10-08. `p` is the input price, `r` the cache-read multiplier. In bold, the models behind Claude Code's `opus`, `sonnet` and `haiku` on the Anthropic API.

| Model | Input `p` | 5-min write | 1-hour write | Cache read | Output | `r` | Min. cacheable |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fable 5.1, Mythos 5.1* | 10.00 | 12.50 | 20.00 | 0.25 | 50.00 | 0.025 | 512 |
| Fable 5, Mythos 5* | 10.00 | 12.50 | 20.00 | 1.00 | 50.00 | 0.1 | 512 |
| **Opus 5.5** | **4.00** | **5.00** | **8.00** | **0.20** | **20.00** | **0.05** | **512** |
| Opus 5 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 | 0.1 | 512 |
| Opus 4.8 / 4.7 / 4.6 / 4.5 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 | 0.1 | 1,024 / 2,048 / 4,096 / 4,096 |
| **Sonnet 5.5** | **2.00** | **2.50** | **4.00** | **0.10** | **10.00** | **0.05** | **512** |
| Sonnet 5 | 2.00 | 2.50 | 4.00 | 0.20 | 10.00 | 0.1 | 1,024 |
| Sonnet 4.6, 4.5 | 3.00 | 3.75 | 6.00 | 0.30 | 15.00 | 0.1 | 1,024 |
| **Haiku 5.5, prompt up to 100k** | **0.10** | **0.125** | **0.20** | **0.01** | **0.50** | **0.1** | **512** |
| **Haiku 5.5, prompt over 100k** | **0.50** | **0.625** | **1.00** | **0.05** | **2.50** | **0.1** | **512** |
| Haiku 4.5 | 1.00 | 1.25 | 2.00 | 0.10 | 5.00 | 0.1 | 4,096 |

*Limited availability.

```
cost = p × ( plain + 1.25·w5m + 2·w1h + r·read + 5·out ) ÷ 1,000,000
         × modifiers
     + $0.01 per web search
```

On Haiku 5.5, `p` depends on the request's prompt size, `plain + w5m + w1h + read`: $0.10 up to 100,000 tokens, $0.50 over, for every token of the request, its output included.

| Modifier | Value | What to know |
|---|---|---|
| Batch API | × 0.5 | Most batches finish within an hour; requests not done in 24 hours expire, unbilled. Cache hits are best-effort, so put a shared prefix on the 1-hour cache |
| Fast mode | × 2 | Opus 5.5 ($8 / $40), Opus 5 and Opus 4.8 ($10 / $50), up to 2.5× faster output. Claude API only: not on Bedrock, Google Cloud, Microsoft Foundry or Claude Platform on AWS, and not in batches. On a subscription it's always paid from usage credits. Turning it on mid-conversation re-writes the context at fast prices (§8) |
| US-only inference | × 1.1 | `inference_geo: "us"`, on the request or as the workspace default, on the Claude API and Claude Platform on AWS (on Foundry, a US Data Zone deployment); Claude 4.6 and later |
| Regional endpoints | + 10% | Bedrock and Google Cloud regional endpoints (and Google Cloud's multi-region ones), Claude 4.5 and later |
| Contract | your rate | A negotiated discount |

The modifiers multiply each other (P7): a 1-hour cache write on Opus 5.5 is $8 a million, $8.80 with US-only inference, and $4.40 in a batch as well. Per-use fees are added last. Claude Platform on AWS and Microsoft Foundry charge the Claude API's prices, billed through the AWS or Azure Marketplace in Claude Consumption Units of $0.01; Bedrock and Google Cloud set their own prices.

**What the table says:** a cache read is 12–80× cheaper than a write, and output is the most expensive token. Three traps:
- **Sonnet 5.5 costs half of Opus 5.5 on every token, cache reads included** ($0.10 against $0.20, since 2026-10-07). Sonnet 5 reads at the same $0.20 as Opus 5.5, so moving a warm conversation from Opus 5.5 to Sonnet 5 saves nothing on its cached part.
- **Fable 5.1 reads at $0.25,** close to Opus 5.5, though everything else costs 2.5× more.
- **Haiku 5.5 has a price cliff at 100,000 tokens of prompt:** past it, every token of the request costs 5×, its output included, so a 100,001-token prompt costs five times a 100,000-token one. Under the line its prices are a tenth of Haiku 4.5's; over it, half (§10).

**The running session.** Claude Code on Opus 5.5, with a subscription's 1-hour cache:
- every request starts with the same 25k tokens: the tool list (~23k) and the system prompt (~2k);
- the first message adds 10k: your text plus what Claude Code attaches to a session's first prompt;
- each later message adds 2k (the previous reply and your new message), and each reply is 1k output tokens.

Its first request sends 35k. Other sessions already have the 25k start cached (P4), so it reads 25k ($0.005), writes 10k at the 1-hour price ($0.080) and outputs 1k ($0.020): **$0.105**. With nothing cached, all 35k are written: $0.30. The second request (37k) reads 35k ($0.007), writes 2k ($0.016) and outputs 1k ($0.020): **$0.043**. Each warm message after that costs a little more, as the conversation it reads back grows (P2).

### 4. Reading a usage record

Every response's `usage` maps onto the formula:

| Field | Variable |
|---|---|
| `input_tokens` | `plain`: only what follows the last breakpoint, **not** the prompt size |
| `cache_read_input_tokens` | `read` |
| `cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` | `w5m` / `w1h`; their sum is `cache_creation_input_tokens` |
| `output_tokens` | `out`, thinking included (`output_tokens_details.thinking_tokens` says how much) |
| `server_tool_use.web_search_requests` | web searches |
| `speed`, `inference_geo` | modifiers: `"fast"` → × 2, `"us"` → × 1.1 |

```
prompt size = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
hit rate    = cache_read_input_tokens ÷ prompt size
```

**On the wire:** the first request of a real Claude Code session on Opus 5.5. Transcripts record each reply's usage, not the request that was sent.

```json
"usage": {
  "input_tokens": 2,
  "cache_read_input_tokens": 23167,
  "cache_creation_input_tokens": 11977,
  "cache_creation": { "ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 11977 },
  "output_tokens": 121,
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

This is the running session's first request, for real: the 23,167 tokens read back are Claude Code's tool list, cached by another session (P4). Hit rate: 66%.

---

## Part 2: Prompt caching

### 5. How the cache works

A request is sent in a fixed order, **tools → system → messages**. The cache stores prefixes of that sequence at marked points (*breakpoints*, `cache_control`, up to 4 per request), and a later request reuses an entry only if its own start matches it byte for byte (P4).

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

What follows from it:
- **A read stops at an entry,** so a change mid-conversation reads back only to the last entry before it.
- **Below the minimum size (§3), nothing is cached**, silently: both cache counts read 0.
- **The lookback is 20 blocks.** A block is one text, image, tool call or tool result; a run of tool calls, or of their results, counts as one. A turn that adds more than 20 blocks can lose the previous entry; a second breakpoint fixes it.
- **Requests sent at the same moment all write,** since none has started its response. Send one first, and the rest once it has (§15).
- **The start is shared.** In Claude Code, the tool list is the same for every session on the same model, version and tools, so other sessions usually keep it warm. The system prompt names the folder's memory paths, so only sessions in the same folder share it. On the wire: a new session read back 23,167 tokens, the tool list; a second session started 8 seconds later in the same folder read back 25,084, the tool list and the system prompt.

**What a request looks like:** a schematic, since transcripts don't keep requests. Claude Code's reads stop at these three points:

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

With the API, a single top-level `cache_control` (*automatic caching*) puts the breakpoint on each request's last block, which suits a growing conversation.

### 6. The cache lifetime

- **5 minutes or 1 hour.** A 1-hour write costs 2× input instead of 1.25×; reads cost the same.
- **The clock starts when a request starts,** and every request that reads or writes the entry restarts it (P5). A 4-minute reply on a 5-minute cache leaves the next request about a minute; an agent run stays warm as long as each step starts within the lifetime.
- **It's a minimum.** Entries are deleted "promptly, though not immediately" afterwards (one was read back half a minute late). Plan on the minimum.
- **Requests you don't see restart it too** (P8), so a countdown worked out from your logs is a lower bound. On the wire: on a 5-minute cache, a message 6½ minutes after the last request read all 36,330 tokens back, because Claude Code's recap had re-read the conversation 3 minutes in.

**When it runs out: the running session at lunch.** At 100k tokens, a warm message reads 100k ($0.020), writes 2k ($0.016) and outputs 1k ($0.020): **$0.056**. After a 90-minute lunch the 1-hour cache has expired. The next message reads only the 25k start, kept warm by other sessions, and writes the other 77k ($0.616): **$0.64**, eleven times more. usdash shows this range before you send: `$0.02 now` while the cache is warm, `up to $0.80` once it has expired.

On the wire: after a 331-second tool on a 5-minute cache, the next request read back only the start (25,209 tokens) and wrote the rest (10,224): $0.0562, against about $0.01 warm.

**Which lifetime Claude Code uses** (P10): the main conversation (your turns, `claude -p` runs, the Agent SDK) gets 1 hour on a subscription within plan usage, and 5 minutes otherwise: on an API key, on usage credits past the plan, or through a cloud provider. Everything else, such as subagents, forks, compaction and titles, gets 5 minutes, except a few helper requests Anthropic picks server-side, which get the main conversation's lifetime (in 2.1.293's defaults, the auto mode classifier and memory recall, Appendix C). `promptCacheTtl` and `subagentPromptCacheTtl`, or their environment variables, override each.

### 7. What breaks the cache

A change re-processes its own layer and everything after it (P4):

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

✓ read from the cache, ✘ written again. Some models place thinking and effort settings ahead of the tools and system prompt, hence "on some models". Effort changed *per message* instead (a beta) keeps the cache, and setting effort to the model's default is the same as leaving it out. After the lifetime, the shared start may still be warm (P4).

**On the wire**, three changes in a row in one Claude Code session:

| What happened | Read back | Written | Cost |
|---|---:|---:|---:|
| Opus 5.5, effort high → low, 18 minutes after the last request | 34,989 of 34,991 | 229 | $0.0091 |
| Then a switch to Opus 5 | 23,167 (Opus 5's tool list, warm from another session) | 12,471 | $0.1368 |
| Then Opus 5, effort low → high | 23,167 | 12,698 | $0.1388 |

Opus 5.5's effort change kept the cache because Claude Code sends it per message (P10). The switch and the Opus 5 effort change wrote the conversation again.

### 8. Claude Code: what the next request costs

Claude Code appends most changes to the end of the conversation, which keeps the cache (P4, P10). The exceptions, and the cases people wrongly expect to break it:

```
What happened since the conversation's last request?
│
├─ Nothing, within the lifetime ──── read everything; write only what's new
├─ More than the lifetime passed ─── read the shared start if other sessions kept it warm; write the rest
├─ Model switch ──────────────────── as if expired, on the new model's cache (its tool list is often warm)
├─ Effort change ─────────────────── kept on Opus 5.5, Sonnet 5.5, Haiku 5.5 and Fable 5.1 (API key or
│                                    subscription); otherwise as a model switch
├─ Fast mode turned on ───────────── the first time only: nothing read, everything written at fast prices
├─ Tool list changed ─────────────── everything written again (MCP tools are deferred by default, so
│                                    connecting a server usually changes nothing)
├─ /compact ──────────────────────── a summarising request, then the short new history is written (§16)
├─ /clear ────────────────────────── free; the shared start stays cached
├─ /rewind ───────────────────────── reads the earlier turn back; later turns kept it warm
├─ --resume ──────────────────────── like any next request: read back within the lifetime (8 of 10 measured)
├─ Waited for a subagent ─────────── the parent's clock kept running; a long subagent can leave it expired
└─ Anything appended ─────────────── cache kept: file edits, skills, plan mode, permission mode, output style
```

**Rarer cases.** `opusplan` entering or leaving plan mode, an automatic model fallback, and a skill that names another model are all model switches. Dropping old images past the image limit re-processes from the first one dropped. Edits to CLAUDE.md apply only after `/clear`, `/compact` or a restart. After a Claude Code upgrade, a new conversation writes from the top, while a resumed one keeps its own system prompt and can still read its cache (2 of 2 measured).

### 9. Caching decisions

**Does caching pay?** A write costs a premium; each read saves most of the input price:

```
reads to break even = (write multiplier − 1) ÷ (1 − r)
```

| | 5-minute | 1-hour |
|---|---:|---:|
| r = 0.1 | 0.28: pays from the **1st** read | 1.11: from the **2nd** |
| Opus 5.5, Sonnet 5.5 (r = 0.05) | 0.26 | 1.05 |
| Fable 5.1 (r = 0.025) | 0.26 | 1.03 |

The running session's first 20 messages (35k → 73k, 1.08M input tokens) cost **$4.72** without caching and **$0.99** with the 1-hour cache, 79% less; output ($0.40) becomes the largest part. A 21st message after the cache expired (75k) costs $0.43 with the start still cached and $0.62 with nothing cached, against $0.05 warm.

**5-minute or 1-hour?** The 1-hour cache costs `0.75p` more per token written. The 5-minute cache costs `(1.25 − r)p` more per token it must write again after a pause the 1-hour cache would have survived. So:

```
the 1-hour cache wins when
    tokens re-written after 5–60 minute pauses  >  0.75 ÷ (1.25 − r)  ×  all tokens written
                                                   (0.65 most models · 0.625 Opus 5.5 and Sonnet 5.5 · 0.61 Fable 5.1)
```

| Typical gap between requests | Choose |
|---|---|
| Under 5 minutes | **5-minute:** every request refreshes it |
| 5–60 minutes | **1-hour:** one such pause near full context size usually pays for it |
| Over an hour | Neither survives it; expect a full re-write |

For example, a day in the running session, 6 bursts of 10 messages at about 100k with 15-minute breaks, costs **$5.88** on the 5-minute cache, where each burst starts by writing all 102k (6 × $0.53 + 54 × $0.050), and **$4.14** on the 1-hour one, where only the first does ($0.84 + 59 × $0.056).

**Keep-alive pings (API).** A request with `max_tokens: 0` reads the prompt without generating anything, restarting a 5-minute entry for one cache read (`r·p` per token). Against the 1-hour premium of `0.75p` per token written, pings win while there are fewer than `0.75 ÷ r` of them: 7.5 on most models (about half an hour of pings every 4½ minutes), 15 on Opus 5.5 and Sonnet 5.5, 30 on Fable 5.1.

**Throughput.** On most models, cache reads don't count toward the input-tokens-per-minute rate limit: at an 80% hit rate, a 2M limit handles 10M input tokens a minute. `max_tokens` doesn't count toward the output limit, so there's no reason to set it low.

---

## Part 3: Other levers

### 10. Model choice and tokenizers

Price per token is half the story; tokens per task is the other half (P9). It depends on the tokenizer (77k tokens on Haiku 4.5 are 100k–104k on Opus 5.5 or Haiku 5.5), on how much the model writes and thinks at the chosen effort, and on how many attempts it needs. Compare models on the same task, per finished result: tokens in and out × price × attempts. For example, reading 10,000 support tickets of ~3,700 tokens each costs about $37 on Haiku 4.5, and about $4.80 on Haiku 5.5, where each counts ~4,800 tokens.

**Haiku 5.5's price cliff.** A Haiku 5.5 request whose prompt passes 100,000 tokens pays 5× for every token, output included (§3). So keep its requests under 100k: split long documents, and compact or clear a conversation before it gets there. A Claude Code session on Haiku 5.5 compacts only near 967k by default, so every request after its context passes 100k pays the higher prices; `/autocompact 100k`, the lowest setting, compacts it there instead. And Haiku 5.5 thinks by default (adaptive thinking, at `medium` effort), where Haiku 4.5 thought only when asked: count that output when you compare them.

### 11. Output, thinking and effort

- **Output is the expensive side:** 5× input. A 2,000-token reply on Opus 5.5 costs $0.04, twice what reading a 100k conversation from the cache costs.
- **Thinking is output,** billed in full even when hidden, and earlier thinking is re-sent as input (§2).
- **Effort** (`low` to `max`) scales all output, tool calls included; it's a signal, not a budget. The API defaults to `medium` on Opus 5.5 and Haiku 5.5 and `high` elsewhere (Haiku 4.5 has none); Claude Code defaults to `medium` on Opus 5.5, Sonnet 5.5 and Haiku 5.5, and `/effort` changes it.
- **Lowering effort on a warm cache** saves at once where the change keeps the cache (§8): in the running session, ~3,000 → ~800 output tokens saves $0.044 a message. Where it doesn't, treat it as a model switch (§14).

### 12. Tools and server tools

- **Tool definitions are input on every request** (P2), plus the API's tool instructions. Tools a client defers (Claude Code's default for MCP tools) cost nothing until loaded.
- **Tool calls are output; tool results are input** in every later request.
- **Web search:** $10 per 1,000 searches plus the results as input; a failed search isn't billed.
- **Web fetch:** only the fetched tokens (a typical page ~2,500; a 500 kB PDF ~125,000).
- **Code execution:** free alongside web search or fetch; otherwise billed by container time (1,550 free hours per organisation a month, which claude.com/pricing gives as 50 a day, then $0.05 an hour per container, 5 minutes minimum).
- **Claude Managed Agents** (agents Anthropic hosts): tokens at the same prices, no batch discount, plus $0.08 per session-hour while a session is running (idle time is free), in place of code execution's container hours.

---

## Part 4: Conversations and agents

### 13. Agent turns

One prompt to an agent is one request per step: the model calls a tool, the result goes back, the model continues. **Every step re-sends the whole context** (P2).

```
turn ≈ steps × context × read price  +  new tokens × write price  +  output × output price
```

**Example:** the running session at 100k, warm, and one prompt that takes 8 tool steps, each adding a 3k-token result and 300 output tokens: re-reading the context (8 × ~110k) costs $0.18, writing the 24k new tokens $0.19, and the 2,400 output tokens $0.05. **$0.42** in all.

In a warm agent loop, the new tokens cost about as much as re-reading the context, and a big tool result is paid twice: written once, then read on every later step. Keep tool output small: filter a log down to its failures, or let a subagent read the big file and return a summary.

### 14. Price now, saving later

Most choices trade a one-time cost for a per-request saving (P6):

```
P = extra cost now, against not changing
s = saving on each later request
m = P ÷ s = requests to pay back          → worth it if more than m requests are left
```

**Example: switching the running session to Sonnet 5.5** at 100k, warm:
- **P = $0.38:** Sonnet writes all 100k into its own cache ($0.40) where Opus would read them ($0.02).
- **s = $0.028:** every token costs half on Sonnet 5.5, cache reads included ($0.056 → $0.028 a message).
- **m = 14 messages.** After a break it flips: both must write everything, $0.40 on Sonnet against $0.80 on Opus, so switching down is cheaper from the first message.

Rules of thumb:
- **Unsure how many requests are left?** Pay the extra per request until it adds up to P, then switch. You never pay more than about twice the best choice in hindsight.
- **A break resets the question:** once the cache has expired, P drops to about zero, so switch or compact before resuming.
- **Switching back within the lifetime** can reuse the old cache, but only within the 20-block lookback; budget a full re-write.

### 15. Breaks, resumes, subagents and parallel requests

- **A break longer than the lifetime** re-writes the whole context, less any shared start (P4, P5): the most common reason a session "suddenly" costs more (lunch: $0.056 → $0.64, §6).
- **`claude --resume`** re-sends the conversation with the system prompt it started with (P10). Within the lifetime it reads back what's cached; after it, it writes it again (P5). On the wire: a resume after 5 minutes read all 35,094 tokens back; one after 22½ hours read back only the 23,167-token tool list.
- **A subagent** is a separate conversation with its own prompt, tools and 5-minute cache. It doesn't read the parent's cache, and the parent's clock keeps running while it waits (P4, P5). On the wire: a parent on a 5-minute cache waited 10 minutes; the subagent's steps each read their own cache back, but the parent then read back only its 23,292-token start and wrote 12,932 tokens again.
- **A fork** inherits the parent's prompt, tools and conversation exactly, so it reads the parent's cache. Check that a feature really is one: a skill run "in a forked subagent" in this project read nothing back and wrote its 44k start again.
- **Parallel requests sharing a prefix:** ten requests sharing a 30k prefix on Sonnet 5.5, sent at once, write it ten times (**$0.75**); one first and the other nine once it has started cost **$0.10** (§5).

### 16. /compact, /clear and /rewind

**`/compact`** replaces the conversation with a summary: a summarising request now, savings on every later message (P6).
- **The summarising request** carries the same prompt, tools and history, plus an instruction, and isn't logged (P8). Warm, it reads what the latest turn's first request cached (everything up to and including your last prompt) and sends the rest as plain input; cold, it reads only the tool list. Its output, the summary, is 1–4k tokens.
- **The next request** sends the shared start, the first prompt's attachments and the summary (within 6% of first prompt + summary in tests), and writes all of it except the shared start.

**Example: the running session at 100k**, its latest turn begun at 90k, with a 4k summary (2.5k output tokens):
- **Warm: $0.108** (90k read, 10k plain, the summary). **Cold: $0.355** (25k read, 75k plain, the summary).
- Afterwards the conversation is 39k, so each message saves **$0.012**, and the first one writes 14k it would otherwise have read ($0.109 more). Pay-back: about **18** messages.
- So compacting a warm 100k conversation just to save money pays back slowly. It pays sooner at larger contexts, and at once before a break: lunch after compacting costs $0.15 instead of $0.64. **Compact before a break, not after.** When one prompt built most of the context, a warm compaction can't read much back and costs about the same as a cold one.

**`/clear`** starts over for free; the shared start usually stays cached.

**`/rewind`** returns to an earlier turn, the cheapest way to abandon a wrong path. The next request reads that turn's entry, which later turns kept warm.

### 17. Requests you don't see

Clients send requests you never typed, and their usage never reaches the transcript (P8). In Claude Code they come in two kinds; Appendix C lists each one, from Claude Code 2.1.293's code.

| Kind | Examples | Cost | Effect on the cache |
|---|---|---|---|
| **Copies of the conversation,** on the session's model and start | Prompt suggestions, memory extraction, recaps, compaction, `/btw` | About one cache read of the whole context (550k on Opus 5.5: $0.11), plus the answer | Each reads the session's cache, restarting its clock (P5) |
| **Separate small requests** | Session titles, WebFetch and WebSearch processing, memory recall | Usually a fraction of a cent; WebSearch adds $10 per 1,000 searches | None on the session's cache |

- **How much logs miss:** Claude Code's transcripts held 41–100% of its own totals (a median of 89%; 84% weighted by cost), and only 28% in a session with four web searches.
- **The client's own total.** At exit, Claude Code writes what it counted, per model, as a `cost-state` record. On the wire, for a session compacted within a minute of its last reply:

  ```json
  "totalCostUSD": 0.2289108,
  "modelUsage": {
    "claude-haiku-4-5-20251001": { "inputTokens": 975, "outputTokens": 15, "costUSD": 0.00105 },
    "claude-opus-5-5": { "inputTokens": 6855, "outputTokens": 1923, "cacheReadInputTokens": 133384,
                         "cacheCreationInputTokens": 16913, "costUSD": 0.2278608 }
  }
  ```

  The transcript's own requests came to $0.1567; the Haiku line matches a session title (Appendix B breaks down the rest).
- **The FinOps rule:** reconcile against the provider's usage report, the client's own total (`/usage`, `cost-state`) or an OpenTelemetry export, not against transcripts alone.

---

## Part 5: Diagnosis and FinOps

### 18. Diagnosing a surprising cost

```
Cost higher than expected?
│
├─ Hit rate low (read ÷ prompt size)?
│   ├─ written ≈ the whole conversation ──── the cache expired (a gap, a long reply, a long subagent),
│   │                                         a model switch, an effort change, the first fast request,
│   │                                         a tool change, a resume after the lifetime (§8)
│   ├─ read stuck at the tool list's size ── the conversation's entry was lost; the shared start survived
│   ├─ read = 0, written = 0 ─────────────── no cache markers reached the API (caching off, a gateway
│   │                                         stripping them), or the prefix is under the minimum size
│   ├─ every request writes it all again ─── something early changes each time (a timestamp, unsorted JSON,
│   │                                         tools loading in a different order), or turns add over 20 blocks
│   └─ parallel requests all writing ─────── none had started its response when the others were sent
│
├─ Hit rate fine, output share high ──────── effort, thinking or long replies are the lever (§11)
├─ Hit rate fine, context just large ─────── big tool results; /compact or /clear (§13, §16)
└─ Bill above your logs ──────────────────── requests you don't see (§17)
```

### 19. Metrics

| Metric | Formula | Signal |
|---|---|---|
| **Hit rate** | read ÷ prompt size | Under ~80% in a conversation: something keeps changing the prefix, or pauses outlast the lifetime |
| **Miss cost** | tokens re-written that could have been read × (write − read price) | What misses cost, by cause |
| **Output share** | output cost ÷ total | If high, the lever is effort and reply length, not caching |
| **Effective input price** | input-side cost ÷ prompt tokens | How close you are to the cache-read price |
| **Cost per unit** | total ÷ units delivered | The number the business sees |
| **Unlogged share** | 1 − logged cost ÷ billed cost | How much your logs miss (§17) |

**For Claude Code:** every reply's usage record is in its transcript (`~/.claude/projects/<folder>/<session>.jsonl`). `/usage` shows the session's hit ratio, miss count, whether the cache is warm and the likely cause of the last miss. usdash's Stats show the first four over 30 days, plus how much of Claude Code's own totals the transcripts hold.

### 20. Unit economics and forecasting

**Cost the unit the business cares about** (a ticket, a document, a pull request, a developer-day) end to end:

```
cost per unit = Σ over its requests of (tokens by kind × price) × modifiers  +  per-use fees  +  infrastructure
```

**To forecast,** estimate requests per unit (tool steps and hidden requests included), context per request and how it grows, the hit rate from the traffic pattern (steady traffic stays warm, bursty traffic re-writes), output per request at the chosen effort, and volume. Run the formula, then **check it against a small pilot's real usage before scaling.**

**Reference points:**
- **Claude Code in enterprises** (Anthropic's figures): about $13 per developer per active day, $150–250 a month, and under $30 a day for 90% of users.
- **A RAG service:** a 50k-token document and 1,000 questions an hour on Sonnet 5.5 (200 tokens in, 400 out) cost **$104 an hour uncached and $9.50 cached**, and each question then counts only 200 tokens toward the rate limit.
- **Offline processing:** 10,000 documents of 3k tokens in and 500 out on Haiku 4.5 cost **$55, or $27.50 in batches**. On Haiku 5.5, where the same text counts ~30% more tokens, about **$7, or $3.60 in batches**, plus any thinking.

### 21. Governance

| Lever | Actions |
|---|---|
| **Budget** | Spend limits per organisation and workspace; per-user limits on team plans; alerts on the daily trend |
| **Structure** | Stable content first; breakpoints on the last stable block; small tool outputs; stable tool sets |
| **Routing** | The cheapest model that meets the quality bar, per task; small models for subagents' side work |
| **Time** | The 1-hour cache for work with pauses; batches for anything that can wait; pre-warming with `max_tokens: 0` where first-response latency matters |
| **Visibility** | Contracted rates in reports (Claude Code's `modelPricing`); OpenTelemetry per user and session; reconciliation with invoices; logs kept long enough for a baseline (Claude Code deletes transcripts after 30 days by default: `cleanupPeriodDays`) |
| **Plans** | Pro, Max and Team bill plan usage, not tokens, so list prices are for comparison. Usage credits past the plan are billed at API rates and drop Claude Code's main conversation to the 5-minute cache; fast mode is always paid from them. Enterprise is $20 a seat a month plus usage at API rates. Max includes $100 or $200 a month of Claude API credits and Team up to $500, which don't cover Claude Code |

---

## Part 6: Practice

### 22. Practice problems

Work each one with the six steps at the top; the answers follow.

**1. A support bot keeps a 6k-token system prompt. Questions arrive every 2 minutes; later, every 20 minutes. Which cache lifetime for each?**
*Every 2 minutes: 5-minute.* Each question refreshes it, so the 1-hour price buys nothing. *Every 20 minutes: 1-hour.* On a 5-minute cache every question re-writes the prompt; the 1-hour cache writes it once and pays back from the second read (P5, §9).

**2. Your agent's hit rate dropped from 95% to 40% after a deploy. Where do you look?**
*At what changed early in the request*, in order: tool definitions, the system prompt (a timestamp, a per-user field), images, `tool_choice`, the thinking or effort setting, the model id (P4, §7). Then check whether the breakpoint sits on a block that changes every request (§5).

**3. A developer asks why a one-line question cost $0.80 after lunch.**
*The cache expired over lunch* (P5). The question re-sent the whole 100k-token conversation at the 1-hour write price on Opus 5.5: up to $0.80, less any start other sessions kept cached (§6).

**4. Should a nightly report job over 50,000 records use the Batch API?**
*Yes*, if the results can wait up to 24 hours: half price on every token, with caching still applying, best-effort (P7, §3).

**5. You fan out 20 subagents that share a 40k-token briefing. How do you cut the input cost?**
*Send one first, and the other 19 once its response has begun,* so they read the briefing instead of each writing it (P4, §15). Or pre-warm the cache with `max_tokens: 0`.

**6. Is moving a warm 200k Opus 5.5 conversation to Sonnet 5.5 worth it to save money?**
*While it's warm, only if more than about 20 messages are left.* Sonnet must write 200k tokens into its own cache ($0.80 at the 1-hour price, $0.76 more than Opus reading them). Then everything costs half, cache reads included: a running-session message at 200k drops from $0.076 to $0.038, and $0.76 ÷ $0.038 = 20. After a break it's different: both must write everything, and Sonnet does it for half (P4, P6, §14).

**7. A session resumed the next morning read 23,167 tokens from the cache and wrote the rest. Why 23,167, and why not 0?**
*The conversation's entry had expired overnight* (P5), *but the start of the request is shared:* 23,167 is Claude Code's tool list, the same in most sessions on that model and version, and another session kept it warm (P4, §5).

**8. A subagent ran for 10 minutes. When it reported back, the parent wrote its conversation again on a 5-minute cache, but not on a 1-hour one. Why?**
*The subagent has its own conversation and cache* (P4), *and the parent sent nothing while it waited*, so its 5-minute entry expired and its 1-hour one didn't (P5, §15).

**9. On a 5-minute cache, a message 6½ minutes after the last request read everything back. Was the lifetime wrong?**
*No. A request you didn't see read the conversation:* Claude Code's recap, 3 minutes in, restarted the clock (P5, P8, §6).

**10. Claude Code's total for an exited session is 30% above what its transcripts show. Which number is right?**
*Both, for what they cover.* The transcripts leave out titles, prompt suggestions, memory extraction, recaps, compaction and `/btw`, and the client's own total counts them (P8, §17). For a budget, use the client's total, or divide what your logs show by the logged share (§19).

**11. You're at 150k on Opus 5.5 with a warm cache and leaving for lunch. Compact now, or after?**
*Now.* A warm compaction reads most of the conversation from the cache; after lunch it would send nearly all of it as plain input. And after lunch, the first message writes about 40k instead of 150k (P5, P6, §16).

**12. You change effort mid-session. On Opus 5.5 the next message read everything back; on Opus 5 it wrote the conversation again. Why?**
*How the client sends the change* (P10). Claude Code sends Opus 5.5's effort per message, which keeps the cache; on Opus 5 the change is on the request itself, which doesn't (P4, §7).

**13. You turn on fast mode halfway through a 200k Opus 5.5 session. What does it cost, and does turning it off and on again cost more?**
*$3.20, once:* the first fast request writes all 200k at the fast 1-hour price of $16 a million, because Claude Code's fast-mode header is part of the cache key (P4, P10). Later toggles keep the cache (§8).

---

## Appendix A: Reference numbers

The running session: Claude Code on Opus 5.5, 1-hour cache, a 25k shared start, 2k new tokens and 1k output per message.

| Situation | Cost |
|---|---:|
| First request (35k): the start cached elsewhere / nothing cached | $0.105 / $0.30 |
| 20 messages from a fresh start (35k → 73k): no cache / 1-hour cache | $4.72 / $0.99 |
| Warm message at 100k | $0.056 |
| Message at 100k after the cache expired: the start cached elsewhere / nothing cached | $0.64 / $0.84 |
| A day of 6 bursts of 10 messages at ~100k: 5-minute / 1-hour cache | $5.88 / $4.14 |
| One prompt running 8 tool steps at 100k (3k result + 300 output each) | $0.42 |
| Switch to Sonnet 5.5 while warm at 100k | P = $0.38, s = $0.028: pays back after **14** messages |
| `/compact` at 100k: warm / after the cache expired | $0.11 / $0.36; pays back after about **18** messages |
| Lunch break after compacting first | $0.15 instead of $0.64 |
| Turning fast mode on at 200k | $3.20, once |

## Appendix B: Evidence from real sessions

What building and reviewing usdash measured in real Claude Code transcripts (versions 2.1.278–2.1.286), compared with Claude Code's own totals. Re-measure after an upgrade. The regression tests in [`tests/test_real_checks.py`](../tests/test_real_checks.py) hold usdash to the 2026-09-28 sessions in [`tests/fixtures/checks`](../tests/fixtures/checks).

| Question | What was measured |
|---|---|
| Does the next request read back the whole previous prompt? | Yes: 1,938 of 1,939 same-model pairs within the lifetime, to within 100 tokens. The other changed effort on Sonnet 5, which re-writes |
| How big is Claude Code's tool list? | CLI 22–25k tokens; VS Code extension 20.8k; Desktop app 36.3k; `claude -p` 10–20k depending on model and version |
| Do other sessions keep the start of a request cached? | Yes: new sessions typically read back the 23,167-token tool list another session had cached. One started eight seconds after another in the same folder read back 25,084: the tool list and the system prompt the first had just written |
| Is the tool list cached when the conversation's cache isn't? | Usually. Own model on a 1-hour cache: 36 of 39 cold starts read it back, once after 11¾ hours idle. 5-minute cache: 9 of 48 with no other session using the model, 13 of 22 with one. Another model with no session on it: once yes (Sonnet 5), once no (Opus 5.5) |
| Is the lifetime exact? | No, a minimum, but only just. With nothing refreshing it, one 5-minute entry was read back 5.6 minutes after its last use (all but its newest 220 tokens); 2 others were gone at 5.8 and 10.6 minutes |
| Does Claude Code's recap refresh the cache? | Yes, it re-reads the conversation. On a 5-minute cache, the next message, 6½ minutes after the last request and 3½ after a recap, read all of it back (2.1.286). 27 of 40 recaps came within 4 minutes of the last reply; one written 74 minutes into a pause, after the 1-hour cache had run out, wrote it again (2.1.278–2.1.283). Claude Code 2.1.288 skips a recap once 90% of the lifetime has passed |
| Do other unlogged requests touch the conversation's cache? | Sometimes: with no recap in between, 140 of 1,913 next messages within the lifetime read back more than the previous request had sent, so an unlogged request had cached the conversation with its latest reply |
| Tokenizer ratio, old to new? | 0.758 in one switch, 0.74 in another (1.32–1.35× more tokens on the new tokenizer); Anthropic says ~30% more |
| Does an effort change keep the cache? | Opus 5.5 in Claude Code: 7 of 7 read everything back. Opus 5: no; the request after the change read back only the tool list and wrote the conversation again (12,698 of 35,867 tokens in one check) |
| Does fast mode break the cache? | Its first request does: it read nothing back, not even the tool list, in a live session and in two `claude -p` runs (2.1.286). In the live session it wrote all 6,282 tokens at the fast 1-hour price ($16 a million): $0.1024, against $0.0230 for the standard request before it. After `/fast` off, the next request read the conversation back. Within plan usage, fast requests got the 1-hour cache |
| Does a Claude Code upgrade break the cache? | Not for a resumed conversation: resumed across 2.1.284 → 2.1.285 and 2.1.285 → 2.1.286 within the lifetime, both read the whole conversation back |
| Does resuming keep the cache? | Within the lifetime: 8 of 10 resumes read the whole conversation back; the other two, 3½ and 40 minutes after the last request, read back only the tool list. After 92 minutes or more: 31 of 31 re-wrote it |
| What does a warm `/compact` request read? | What the latest turn's first request had cached: 46,885 of 56,584 tokens after a four-turn session, 15,804 of 58,787 when one turn built the context. The rest went at the input price; it wrote only 147–555 tokens |
| And a cold one? | The tool list (13,790), the rest (37,628) at the input price |
| How big is the summary? | Its output: 1,065–3,804 tokens. Its `postTokens`: 3.2–6k for 54–65k conversations, 13,984 at 450k, 16,088 at 972k |
| How big is the conversation right after `/compact`? | 33,335 / 24,987 / 20,315 tokens in three sessions; first prompt + `postTokens` came within 6% |
| What did Claude Code count around a compaction? | For a 40k conversation compacted within a minute of its last reply: 75,114 more tokens read, 6,851 more plain input, 349 more written and 1,296 more output than its transcript. That fits the compaction (about 35k read, 7k plain, a 1.3k summary) plus one more request reading the whole 40k, a prompt suggestion or a memory extraction |
| How much do the transcripts miss? | They held 41–100% of Claude Code's own totals in 34 exited sessions (quartiles 72%, 89%, 99%), least in very short sessions and in ones that ran many subagents; 28% in a session with four web searches; a Desktop app session, $0.16 of $0.23. Weighted by cost, 84% ($278.56 of $332.19): 84% on Opus 5.5, 71% on Sonnet 5, 23% on Haiku 4.5, which Claude Code used for requests of its own (Haiku 5.5 from 2.1.293, on the Anthropic API) |
| Do Claude Code's prices match the list? | Wherever it counted the same tokens as the transcripts, its cost equalled the list-price calculation to the millionth of a dollar: 13 of 13 sessions by 2026-09-30, and since then Fable 5.1, fast mode on Opus 5.5 and Opus 5, and US-only inference ($0.015279 for a run that costs $0.013890 at global prices: exactly 1.1×). One known gap, from its code: 2.1.293's price table still has Sonnet 5.5's cache reads at $0.20, so for Sonnet 5.5 requests after the 2026-10-07 price cut its totals are above the list price |
| A real model switch? | Moving a warm 55.9k conversation from Opus 5.5 to Sonnet 5 cost $0.143, against about $0.02 to stay |

## Appendix C: Claude Code's unlogged requests

From Claude Code 2.1.293's code. Costs are measured where given. *The small model* is Haiku 5.5 on the Anthropic API (Haiku 4.5 before 2.1.293); on a cloud provider it depends on the setup, and `ANTHROPIC_SMALL_FAST_MODEL` sets it.

**Copies of the conversation:** on the session's model and start, so each reads the session's cache and restarts its clock.

| Request | When it runs | In the transcript |
|---|---|---|
| **Prompt suggestion** | After a turn in an interactive session, once the conversation has two replies (in `claude -p` only with `--prompt-suggestions`). Skipped when the cache is cold, the window isn't focused, the last request moved more than ~10k new tokens, in plan mode, or near a usage limit; throttled after a run of unused suggestions. Off by default on cloud providers | Nothing |
| **Memory extraction** | Right after a turn, when auto memory is on and you wrote something new, so always while the cache is warm. Up to 5 steps of its own | Nothing |
| **Recap** | About 3 minutes after the last turn, or as soon as you switch away after that, if the window isn't focused, the session has at least three turns and less than 90% of the lifetime has passed; never twice in a row. Also on `/recap`. Shown when you come back | A record (`away_summary`), no usage |
| **Compaction summary** | `/compact`, automatic compaction (§16), or resuming a large session from a summary | A record (`compact_boundary`), no usage |
| **`/btw` side question** | When you ask one | Nothing; the question is only in the input history |
| **Auto dream** (memory consolidation) | At most once a day, once 5 sessions have run since the last one (the defaults), when auto memory and auto dream are on (`autoDreamEnabled`, else Anthropic's default) | Nothing |
| **Subagent progress summary** | While a subagent runs; it reads the subagent's cache, not the session's | Nothing |
| **A mod's `$.model.fork`** | When an installed [mod](https://code.claude.com/docs/en/plugins/mods/api) asks a question about the conversation | Nothing |

**Separate small requests:** they don't touch the session's cache.

| Request | When it runs | Model and size |
|---|---|---|
| **Session title** | Early in the session | The small model, given a short excerpt. Measured on Haiku 4.5: 893–975 tokens in, 10–15 out, about $0.001; on Haiku 5.5 the same text is ~30% more tokens at a tenth of the price |
| **`/rename` without a name** | When you run it | The small model, given an excerpt (a copy of the conversation instead, when a server flag says so) |
| **Memory recall** | Before each prompt of more than one word, when auto memory is on and a server flag enables it | Sonnet (the `sonnet` alias) picks which memory files to attach, in a small conversation of its own, cached for as long as the main conversation (§6) |
| **WebFetch** | Each fetch | The small model reads the page, cut to 100,000 characters, with your prompt; grows with the page |
| **WebSearch** | Each search | The session's model, or the small one when a server flag says so; no caching, up to 8 searches a call at $10 per 1,000, and the results count as input. Measured on Haiku 4.5: one search came to $0.023263 with its $0.01 fee (12,633 tokens in and 126 out, the session's title included) |
| **Auto mode classifier** | Tool calls that need a decision in auto mode | Its own model and copy of the conversation, cached for as long as the main conversation (§6); not measured |
| **Tool-batch labels** | After tool batches in the main conversation, only when the host sets `CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES`: one-line labels for the mobile app | The small model, no caching |
| **Prompt and agent hooks, mods** | Each time a prompt or agent hook you configured fires, or a mod calls `$.model.complete` | A prompt hook: one request, on the small model by default. An agent hook: a subagent of its own, up to 50 steps. `$.model.complete`: one request, on the model the mod names |
| **Background-agent naming and status** | When background agents run | Small requests |
| **Rare, on demand** | `/insights`, `/feedback`, validating a model name, MCP date parsing, artifact comment replies, triage and edits, auto mode setup and critique, plugin evals, a title when moving a session to the cloud; 1-token checks of an API key, of a subscriber's usage limits (on Haiku 4.5) and, on Google Cloud, of model access | One-off, small |

## Appendix D: Glossary

| Term | Meaning |
|---|---|
| **Token** | The unit a model reads and writes; ~4 characters of English |
| **Context** | Everything a request sends: tools, system prompt, conversation |
| **Prefix** | The start of a request, up to some point; what the cache matches |
| **Layer** | One part of the request's fixed order: tools, then system prompt, then messages |
| **Breakpoint** | A marked block (`cache_control`) where the cache writes an entry |
| **Block** | One piece of a message: a text, an image, a tool call, a tool result |
| **Lookback** | How far back from a breakpoint a read searches for an earlier entry: 20 blocks |
| **Read / written / plain** | Input served from the cache (`r × p`), stored in it (1.25p or 2p), or neither (p) |
| **Lifetime (TTL)** | How long a cache entry lives after the start of the last request that used it: 5 minutes or 1 hour, at least |
| **Hit rate** | The share of input tokens read from the cache |
| **Miss** | A request that writes again what it could have read |
| **Pay-back** | How many later requests it takes for a change's saving to cover its price now |
| **Keep-alive ping** | A `max_tokens: 0` request sent only to restart a cache entry's clock |
| **Unlogged request** | A request the client makes that its own logs don't record |
| **Recap** | The summary Claude Code writes while you're away; its request re-reads the conversation |
| **Compaction** | Replacing a conversation with a summary to shrink its context |
| **Subagent / fork** | A separate conversation a session starts; a fork inherits the parent's history and cache |
| **Effort** | How much work, and output, the model puts into a response |
| **Thinking** | Reasoning the model does before answering; billed as output |
| **Batch** | Asynchronous processing at half price, within 24 hours |

## Appendix E: Sources

- Pricing: https://platform.claude.com/docs/en/about-claude/pricing and https://claude.com/pricing#api (plans: https://claude.com/pricing)
- Release notes (the Sonnet 5.5 cache-read price cut and Haiku 5.5, 2026-10-07): https://platform.claude.com/docs/en/release-notes/overview
- Prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Models overview: https://platform.claude.com/docs/en/models/overview
- Haiku 5.5: https://platform.claude.com/docs/en/models/haiku-5-5/overview
- API credits for Max and Team: https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers
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
- Claude Code model configuration (aliases, effort defaults, auto-compaction): https://code.claude.com/docs/en/model-config
- Claude Code mods calling a model: https://code.claude.com/docs/en/plugins/mods/api
- Claude Code 2.1.293's own code: when each request in Appendix C runs, its model, and whether it reads the session's cache
