# AI tokenomics: a field guide

How the cost of using a large language model is built, why it moves, and how to reason about any new case from first principles. Written around Claude and Claude Code, where every number here was checked. The principles carry over to any provider that bills by the token and caches prompts.

**Checked on 2026-09-28** (Sonnet 5.5 added, and Claude Code's caching doc re-read, on 2026-09-29) against Anthropic's docs (sources at the end) and against real Claude Code 2.1.283 sessions measured while building usdash (Appendix B). Prices change. The principles don't, and Appendix A says where to re-check the numbers.

---

## How to use this guide

You don't need to memorise cases. Almost every question ("is this switch worth it?", "why did yesterday cost double?", "should we batch this?") comes down to four questions:

1. **Which requests will actually be sent?**
2. **For each request, which tokens are read from cache, which are written, which are plain input, and how many come out?**
3. **What does each kind of token cost, after modifiers?**
4. **Over what horizon do I compare the options?**

Part 1 gives the mental model. Parts 2 and 3 cover the levers that change the answers. Part 4 applies them to conversations and agents, where the surprises live. Part 5 turns them into FinOps practice. Part 6 is the method, with practice problems.

---

# Part 1: The mental model

## 1. Nine principles

These are the whole subject in one page. Everything later is one of them applied.

1. **You pay for tokens moved, in and out.** Everything the model reads is input: instructions, tool definitions, the whole conversation, files, tool results, images. Everything it writes is output, including thinking you never see.
2. **The model remembers nothing.** Every request re-sends the entire context. The main cost driver is *context size × number of requests*, not the length of your last message.
3. **Know one number per model: its input price.** Everything else is a fixed multiple of it. Output costs 5×, a cache write 1.25× (5-minute) or 2× (1-hour), and a cache read 0.1×, or less on some models.
4. **The cache is a prefix.** It serves only an exact byte-for-byte match from the start of the request, on the same model, within its lifetime. Change anything early and everything after it is paid for again.
5. **Time is money.** The cache expires a fixed time after the start of the last request that used it. A pause longer than that turns the next cheap request into an expensive one.
6. **Every change has a price now and a saving later.** Switching models, compacting, changing cache lifetime: pay-back = one-time price ÷ saving per later request. Whether it's worth it depends on how many requests are left.
7. **Discounts multiply.** Batch, cache, data residency and fast mode are multipliers on each other, not additions.
8. **What you can't see still costs.** Clients send requests you never typed: titles, summaries, suggestions, sub-requests for tools. Reconcile against the bill, not only against your logs.
9. **Optimise cost per outcome, not per token.** A cheaper model or lower effort that needs more steps, or more retries, can cost more for the same result.

## 2. What a token is, and what you pay for

A **token** is the unit a model reads and writes: roughly 4 characters or ¾ of a word of English text. Code, other languages and unusual formatting take more tokens per character.

**The tokenizer belongs to the model.** Claude Opus 4.7 and later models (including Opus 5.5, Sonnet 5.5, Sonnet 5 and Fable 5.1) use a newer tokenizer that produces about 30% more tokens for the same text than earlier models (Haiku 4.5, Sonnet 4.6, Opus 4.6 and before). So the same conversation is about 0.74–0.77× the tokens on an older model. That means:
- **A price per token isn't comparable across tokenizers.** Compare the cost of the same text.
- **Token counts don't transfer.** A count measured on one model is wrong on the other; recount with the target model.

**What counts as input** (all of it, every request):
- The tool definitions, plus a small system prompt the API adds when tools are present (about 300–700 tokens, depending on the model)
- The system prompt
- Every earlier message: yours, the model's replies, tool calls and tool results, images and documents
- On Opus 4.5 and later, Sonnet 4.6 and later, and the Fable models, the model's earlier thinking blocks, which stay in the conversation and are re-sent as input (Haiku models drop them)

**What counts as output:** the reply, tool calls, and **thinking**. Thinking is billed in full even when the API only shows you a summary of it.

**The context window** is the most one request can hold: input plus output. It's 1M tokens on the current Opus, Sonnet and Fable models (200k on Haiku 4.5), with up to 128k output (64k on Haiku 4.5). On Claude 4.6 and later models, a 900k-token request costs the same per token as a 9k-token one: no long-context premium.

## 3. The cost equation

One request costs:

```
cost = input  × (plain input tokens)
     + 1.25 × input × (tokens written to the 5-minute cache)
     + 2    × input × (tokens written to the 1-hour cache)
     + r    × input × (tokens read from the cache)
     + 5    × input × (output tokens, thinking included)
     + per-use fees (web search: $10 per 1,000 searches)
all  × modifiers (batch 0.5 · US-only inference 1.1 · fast mode · contract discount)
```

Here `input` is the model's base input price, and `r` is its cache-read multiplier: 0.1 on most models, 0.05 on Opus 5.5, 0.025 on Fable 5.1.

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

## 4. Reading a usage record

Every response reports what it consumed. With caching, the input comes in three parts that add up to the whole prompt:

```
total input = input_tokens               (after the last cache breakpoint: plain input)
            + cache_read_input_tokens    (read back)
            + cache_creation_input_tokens (written; split into ephemeral_5m / ephemeral_1h)
```

`input_tokens` alone is **not** the prompt size. With a 200k cached document and a 50-token question it reads 50. `output_tokens` includes thinking; `output_tokens_details.thinking_tokens` says how much of it was thinking. `server_tool_use.web_search_requests` counts billable searches. `speed` and `inference_geo` tell you whether fast mode or US-only pricing applied.

**The cache hit rate** of a request or a day is `read ÷ total input`. It's the single most useful health metric (§20).

---

# Part 2: Prompt caching, the biggest lever

## 5. How the cache works

A request is sent in a fixed order: **tools → system → messages**. The cache stores the model's processed state for a prefix of that sequence, keyed by a hash of every byte up to a marked point (a *breakpoint*). The next request reuses it only if its own beginning is byte-for-byte identical.

The rules that matter:

| Rule | Consequence |
|---|---|
| Matching is exact, from the start | Appending is cheap. Any change earlier re-processes everything after it |
| Writes happen only at breakpoints (up to 4 per request) | Put the breakpoint on the last block that stays the same, not on one that changes every time (a timestamp, the new question) |
| A read looks back at most 20 blocks from each breakpoint for an earlier write | A turn that adds more than 20 blocks can miss the previous entry; a second breakpoint fixes it (a run of parallel tool calls counts as one block) |
| Each model has its own cache | A model switch re-sends everything as new |
| There's a minimum cacheable size: 512 tokens on Opus 5.5, Sonnet 5.5 and Fable 5.1, 1,024 on Sonnet 5, 4,096 on Haiku 4.5 | Below it, nothing is cached and no error says so; both cache counts read 0 |
| Caches are isolated per workspace (per organisation on Bedrock and Google Cloud) | Identical prompts in two workspaces don't share |
| An entry exists only once the first response has begun | Parallel requests sent at the same instant all write; none reads |

**Automatic caching** (one `cache_control` at the top level of the request) moves the breakpoint to the last block of each request, which suits a growing conversation. Claude Code manages all of this for you.

## 6. When caching pays

Writing costs a premium over plain input; each read saves most of the input price. So a cached prefix pays for itself after this many reads:

```
reads to break even = (write multiplier − 1) ÷ (1 − r)
```

| | 5-minute cache | 1-hour cache |
|---|---|---|
| Most models (r = 0.1) | 0.28: **the first read** | 1.11: **the second read** |
| Opus 5.5 (r = 0.05) | 0.26 | 1.05 |

**Example (statelessness plus caching).** A 20-turn Sonnet 5.5 conversation starts with a 20k-token prefix (tools and instructions) and grows by 2k tokens a turn:
- **Without caching:** 820,000 input tokens are sent over the 20 requests, **$1.64**.
- **With caching**, each request reads the previous one back (760,000 tokens) and writes only what's new (60,000 tokens): **$0.30**, 82% less.
- **The trap:** a 21st request after the cache has expired re-writes all 62k tokens, **$0.16**, more than half of what the whole cached conversation cost.

## 7. The cache lifetime

- **Two lifetimes:** 5 minutes (the default) or 1 hour. The 1-hour cache costs 2× input to write instead of 1.25×; reads cost the same.
- **The clock starts at the start of the request** that wrote or read the entry, not when the reply ends. Generation time eats into it: after a 4-minute reply on a 5-minute cache, the next request has about a minute.
- **Every read restarts the clock.** A long agent run stays warm as long as each step starts within the lifetime of the previous step's start.
- **The lifetime is a minimum.** Entries are deleted "promptly, though not immediately" after it. In real sessions a 5-minute entry was still there 6¾ minutes later on some requests and gone after 5⅔ on others (Appendix B). Plan on the minimum; treat anything longer as luck.

**Choosing between them.** The 1-hour cache costs an extra 0.75 × input for every token written. A pause of between 5 and 60 minutes on a 5-minute cache costs re-writing the whole context. So the 1-hour cache wins when:

```
tokens re-written after 5–60 min pauses  >  0.75 ÷ (1.25 − r)  ×  all tokens written
                                            (0.65 on most models, 0.625 on Opus 5.5)
```

The rule of thumb: **one 5–60 minute pause once the context is near its full size is enough for the 1-hour cache to win.** For example, a day on Opus 5.5 with a steady 80k context, 6 bursts of 10 messages and 15-minute breaks between them costs **$3.48 with the 5-minute cache and $1.92 with the 1-hour one**. The five breaks each re-write 82k tokens on the 5-minute cache.

**Who gets which, in Claude Code:** on a subscription within plan usage, the main conversation uses 1 hour. On an API key, usage credits or a cloud provider it uses 5 minutes, unless you set `promptCacheTtl` or `CLAUDE_CODE_PROMPT_CACHE_TTL`. Subagents, compaction and titles use 5 minutes by default; `subagentPromptCacheTtl` (or `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`) sets theirs.

## 8. What breaks the cache

The principle: **anything that changes bytes early in the request re-processes everything after it.** The layer it sits in says how much.

| Change | Tools | System | Messages |
|---|---|---|---|
| Tool definitions added, removed or edited | ✘ | ✘ | ✘ |
| Turning web search or citations on or off; switching fast mode (API) | ✓ | ✘ | ✘ |
| `tool_choice`; adding or removing images | ✓ | ✓ | ✘ |
| Thinking configuration or effort level (on most models) | model-specific | model-specific | ✘ |
| **A different model** | all new | all new | all new |
| **The lifetime passing** | gone | gone | gone |

What doesn't break it: appending messages, tool calls and results. Mid-conversation additions that are sent as new messages (a system message, a skill's instructions) leave the cached prefix intact.

**Where the API and a client differ, check the client.** Examples from Claude Code:
- **Effort:** the API keeps the cache across an effort change on Fable 5.1, Mythos 5.1, Opus 5.5, Sonnet 5.5 and Opus 5, when the change is sent as a per-message setting. Claude Code keeps it on Opus 5.5, Sonnet 5.5 and Fable 5.1 (with an API key or subscription; not on Bedrock or Google Cloud). On Opus 5 it re-wrote the conversation in real sessions (Appendix B).
- **Fast mode:** the API table says switching speed invalidates system and messages. Claude Code sends the fast-mode header once per conversation, so only turning it on the first time costs a re-write. Turning it off and on again later keeps the cache.
- **Resuming:** Claude Code keeps the system prompt a conversation started with, so a resumed session reads back whatever is still within the lifetime (6 of 6 real resumes did; Appendix B).
- **MCP servers:** connecting or removing one changes the tool list only if its tools aren't deferred; by default they are.
- **Editing CLAUDE.md mid-session:** doesn't break the cache, and doesn't take effect until `/clear`, `/compact` or a restart.

## 9. Caching and rate limits

Rate limits count requests per minute, input tokens per minute (ITPM) and output tokens per minute (OTPM). **On most models, tokens read from the cache don't count toward ITPM**; only plain input and cache writes do. With an 80% hit rate, a 2M ITPM limit processes 10M input tokens a minute.

Two more details:
- `max_tokens` doesn't count toward OTPM, so there's no rate-limit reason to set it low.
- Caching is a throughput lever as much as a cost lever.

---

# Part 3: The other levers

## 10. Model choice and tokenizers

Price per token is half the story. The other half is tokens per task, which depends on:
- **The tokenizer.** The same text is ~30% more tokens on Opus 4.7 and later. 77,000 tokens on Haiku 4.5 are 100,000–104,000 on Opus 5.5 or Sonnet 5.5.
- **How much the model writes and thinks** for this task at this effort.
- **How many attempts it takes.** A cheaper model that needs a second pass costs two passes.

**Compare models on the same task, per finished result:** tokens in and out, times price, times attempts. The docs' own example: 10,000 support tickets of ~3,700 tokens each on Haiku 4.5 cost about $37.

## 11. Output, thinking and effort

- **Output is the expensive side:** 5× input on every current model. A 2,000-token reply on Opus 5.5 costs $0.04; reading a 100k conversation from its cache costs $0.02.
- **Thinking is output.** You pay for all of it, even when the response shows a summary or nothing. `thinking_tokens` in the usage says how much.
- **Effort** (`low`, `medium`, `high`, `xhigh`, `max`) scales every output token, including thinking, tool calls and explanations. Lower effort means fewer and terser tool calls too. It's a behavioural signal, not a hard budget. Defaults: `medium` on Opus 5.5, `high` on most others.
- **Earlier thinking stays in context** on Opus 4.5+, Sonnet 4.6+ and the Fable models, and is re-sent as input every request. That's cheap when read from the cache, expensive after a miss.

**The effort decision on a warm cache:** where an effort change keeps the cache, the saving is immediate. It's output before minus output after, times the output price. For example, Opus 5.5 going from ~3,000 to ~800 output tokens per message saves $0.044 a message. Where it doesn't keep the cache, treat it as a model switch (§15).

## 12. Batch, fast mode, data residency, cloud endpoints

All four are multipliers, and they stack with each other and with the cache multipliers.

| Lever | Effect | Notes |
|---|---|---|
| **Batch API** | ×0.5 on input and output | Asynchronous: most batches finish within an hour, all within 24 hours or they expire unbilled. Caching works, best-effort. For a shared prefix, write it once to the 1-hour cache, then submit the rest |
| **Fast mode** | Opus 5.5: $8 / $40 (2×); Opus 5 and 4.8: $10 / $50 (2×) | Up to 2.5× faster output. Not with Batch. The first fast request re-writes the context at fast prices: 200k tokens on Opus 5.5 cost $3.20 to switch deep into a session, $0.32 at a 20k start |
| **US-only inference** (`inference_geo: "us"`) | ×1.1 on everything | Claude 4.6 and later |
| **Bedrock and Google Cloud regional endpoints** | +10% over global | Claude 4.5 and later |

**Stacking example:** a 1-hour cache write on Opus 5.5 lists at $8/M. With US-only inference it's $8.80, and in a batch $4.40.

## 13. Tools and server tools

- **Tool definitions are input on every request.** Each tool's name, description and schema, plus the tool-use system prompt. Tools the client defers until needed (Claude Code's default for MCP tools) cost nothing until loaded.
- **Tool calls are output; tool results are input** in the request that follows, and in every request after it.
- **Web search:** $10 per 1,000 searches, plus its results as input tokens. A failed search isn't billed.
- **Web fetch:** only the fetched content's tokens. An average web page is ~2,500 tokens; a 500 kB PDF ~125,000.
- **Code execution:** free alongside web search or web fetch. Otherwise it's billed by container time (1,550 free hours per organisation per month, then $0.05 an hour per container, at least 5 minutes each time).

---

# Part 4: Conversations and agents

## 14. Anatomy of an agent turn

One prompt to an agent is not one request. It's one request per step: the model calls a tool, the tool result goes back, the model continues. **Every step re-sends the whole context.**

**Example:** Opus 5.5 with a warm 1-hour cache, a 100k-token context, and one prompt that takes 8 tool steps. Each step adds a 3k-token tool result and produces 300 output tokens. The turn costs **$0.42**:
- **Re-reading the context** (8 × ~110k tokens): $0.18
- **Writing the new tokens** (24k, at the 1-hour price): $0.19
- **Output** (2,400 tokens): $0.05

So in a warm agent loop, **the new tokens you add cost as much as the context you re-read**, and big tool results are expensive twice: once when written, then as reads on every later step. Keeping tool output small is one of the best levers you have. Examples: filter a test log down to its failures before the model sees it, or let a subagent read the big file and return a summary.

Cost per turn, in general:

```
turn ≈ steps × context × read price  +  new tokens × write price  +  output × output price
```

## 15. Decisions with a price now and a saving later

Most choices in a session trade a one-time cost for a per-request saving. Name them:
- **P** is what the change costs now, compared with not changing.
- **s** is what it saves on each later request.
- **m = P ÷ s** is how many requests until it has paid for itself.

**Example: switching model mid-conversation.** The conversation is 100k tokens on Opus 5.5, warm, 1-hour cache. Each later message adds 3k new tokens and 1.5k output.
- **Now:** Sonnet 5.5 must write all 100k into its own cache, $0.40, where Opus would read them for $0.02. P = **$0.38**.
- **Later:** both models read the 100k at the same $0.20, so the saving is only on the new tokens and output: $0.074 vs $0.047 a message. s = **$0.027**.
- **Pay-back:** 14 messages.
- **After a break**, the cache has expired and both models must write everything (less the tool list, if something keeps it cached). The same switch costs $0.40 on Sonnet against $0.80 to stay on Opus: switching down is then cheaper from the first message.

**When you don't know how many requests are left**, use the rent-or-buy rule. Keep paying the extra per request (renting) until what you've paid adds up to P, then switch (buy). You never pay more than twice what hindsight would have cost, plus one request. And a break resets the question, because after the cache expires P usually drops to zero or below.

**Round trips:** switching away and coming back within the lifetime can reuse the first model's cache. The lookback reaches at most 20 blocks, though, so after many turns only the start is reused. Budget a full re-write and treat reuse as a bonus.

## 16. `/compact`, `/clear` and `/rewind`

**`/compact`** replaces the conversation with a summary. It costs a summarising request now, and saves on every later message.

- **The summarising request:** Claude Code sends it with the same system prompt, tools and history, and it doesn't cache what it sends. Measured on five real compactions (Appendix B):
  - **While warm,** it read back what the latest turn's first request had cached, and sent the rest at the plain input price.
  - **After a break,** it read back only the tool list, and sent the rest at the input price.
  - **Its output** (the summary, with any thinking) was 1,065–3,804 tokens.
- **After it**, the next request sends the tool list, the system prompt, what Claude Code attaches at the start of every session (environment, listings, re-read files) and the summary. That's about the session's first prompt plus the summary, within 6% on three real compactions. The next request writes this; later ones read it.

**Example:** Opus 5.5, a 150k-token conversation whose last prompt started at 130k, a 25k tool list, a 35k first prompt, a 4k summary (~2.5k output tokens).
- **Compacting now** costs **$0.16**: 130k read, 20k plain input, the summary written.
- **Compacting after the cache expired** costs **$0.56**: 25k read, 125k plain input, the summary.
- **Each later message** reads 39k instead of 150k, saving **$0.022**.
- **The first message after** writes 14k it would otherwise have read, $0.11.
- **Pay-back:** about 12 messages. Compact before a break, not after it. When one prompt built most of the conversation (a single "read all these files" turn), the warm compaction can't read much back, and the two cost nearly the same.

**`/clear`** starts over and costs nothing itself. The tool list and system prompt usually stay cached. Use it when the topic changes.

**`/rewind`** goes back to an earlier turn whose prefix is already cached: the cheapest way to abandon a wrong path.

## 17. Breaks, resumes, subagents and parallel requests

- **A break longer than the lifetime** makes the next request re-write the whole context, at the write price for that model. It's the most common reason a session "suddenly" costs more.
- **Resuming** a session (`claude --resume`) re-sends the whole conversation. Within the lifetime it reads back what's still cached. After it, it re-writes it.
- **A subagent** is a separate conversation with its own prompt and its own cache (5 minutes by default). It doesn't read the parent's cache, and the parent's cache doesn't refresh while it waits. A long subagent can leave the parent to re-write when it returns.
- **A fork** (a subagent that inherits the parent's system prompt, tools and conversation exactly) reads the parent's cache on its first request. Check that a feature really is a fork: a skill run in a forked subagent in this project's review read nothing back and wrote its 44k-token start again.
- **Parallel requests sharing a prefix:** an entry exists only once the first response has begun. Ten requests sharing a 30k prefix on Sonnet 5.5, sent at once, write it ten times: **$0.75**. Sending one first and the other nine once it has started: **$0.13**.

## 18. Costs you don't see

Clients make requests you don't type, and not all of them appear in session logs:
- **Claude Code:** session titles, prompt suggestions (mostly cache reads), `/compact`'s summarising request, WebSearch's sub-requests, background summaries for `--resume`.
- **How much, in real sessions:** the transcripts held 56–98% of what Claude Code itself billed, 85–95% for most sessions. In one interactive session with a `/compact` they held 87%. In one `claude -p` session with four web searches they held only 28%: the searches' own requests were 72% of the cost.
- **The FinOps rule:** reconcile against the provider's usage report, the client's own total (`/usage`, `cost-state`), or an OpenTelemetry export, not only against your logs.

---

# Part 5: FinOps practice

## 19. Unit economics and forecasting

**Pick the unit the business cares about** (per ticket, per document, per pull request, per developer-day) and cost it end to end:

```
cost per unit = Σ over the requests the unit needs of (tokens by kind × price) × modifiers
              + per-use fees + infrastructure (containers, runtime)
```

**To forecast a workload**, estimate:
1. Requests per unit, including tool steps and hidden requests
2. Context per request, and how it grows
3. Cache hits, from the traffic pattern: steady traffic stays warm; bursty traffic with long gaps re-writes
4. Output per request, including thinking at the chosen effort
5. Volume

Then run the equation, and **check it against a small pilot's real usage before scaling.**

**Reference points:**
- **Claude Code across enterprise deployments** (Anthropic's figures): about $13 per developer per active day, $150–250 per developer a month, under $30 a day for 90% of users.
- **A RAG service:** a 50k-token document and 1,000 questions an hour on Sonnet 5.5 (200 tokens in, 400 out) costs **$104 an hour without caching and $15 with it**. Each question then counts only 200 tokens toward the input rate limit.
- **Offline processing:** 10,000 documents of 3k tokens in and 500 out on Haiku 4.5 cost **$55, or $27.50 through the Batch API**.

## 20. Metrics to watch

| Metric | Formula | What it tells you |
|---|---|---|
| **Cache hit rate** | read ÷ total input | Below ~80% in a conversational workload means something keeps changing the prefix, or pauses outlast the lifetime |
| **Miss cost** | tokens re-written that could have been read × (write − read price) | What misses cost you, by cause: model switch, expiry, resume, config change |
| **Output share** | output cost ÷ total cost | High means effort, thinking or verbose replies are the lever, not caching |
| **Effective input price** | input-side cost ÷ total input tokens | How close you are to the cache-read price |
| **Cost per unit** | total ÷ units delivered | The only number the business sees |
| **Unlogged share** | 1 − logged cost ÷ billed cost | How much your own logs miss |

For your own Claude Code use, usdash's Stats view shows the first four: the share read from cache, cache misses by cause, spend by kind of token, and what input costs on average.

## 21. Governance levers

| Where it acts | Levers |
|---|---|
| **Budget** | Spend limits per organisation and workspace; per-user limits on team plans; alerts on the daily trend |
| **Structure** | Stable content first in the prompt; breakpoints on the last stable block; tool outputs kept small; stable tool sets |
| **Routing** | The cheapest model that meets the quality bar, measured per task; subagents on small models for side work |
| **Time** | The 1-hour cache for work with pauses; batch for anything that can wait a day; pre-warming with `max_tokens: 0` when first-response latency matters |
| **Visibility** | Contracted rates in the reporting (Claude Code's `modelPricing`); OpenTelemetry per user and session; reconciliation against invoices |
| **Subscription vs API** | A subscription bills plan usage, not tokens; list-price estimates are for comparison. Going past the plan onto usage credits also drops Claude Code's main conversation to the 5-minute cache |

---

# Part 6: Solving any new question

## 22. The method

1. **List the requests.** Every request the scenario sends, including tool steps, retries, subagents and the client's own background requests.
2. **For each request, find its prefix and ask four questions.** Is it the same model? The same bytes up to here? Within the lifetime of the last request that used it? At least the minimum cacheable size? The answers split its tokens into read, written (5m or 1h) and plain input.
3. **Add output**, thinking included, at the chosen effort.
4. **Price it**: the equation in §3, the modifiers in §12, fees in §13.
5. **Compare options over a horizon.** Separate one-time costs from per-request ones; pay-back is one ÷ the other (§15).
6. **Check against reality.** The usage fields of a real request (§4) are the ground truth; a pilot beats a spreadsheet.

## 23. Practice problems

Answers follow each one; work them out first.

**1. A support bot keeps a 6k-token system prompt and gets a question every 2 minutes, all day. 5-minute or 1-hour cache?**
*5 minutes.* Every question arrives within the lifetime and refreshes it for free, so the 1-hour cache's higher write price buys nothing (§7).

**2. The same bot, but questions come every 20 minutes.**
*1 hour.* On a 5-minute cache every question re-writes the 6k prompt. On a 1-hour cache it's written once and read at 0.1×. It breaks even on the second read (§6).

**3. Your agent's cache hit rate dropped from 95% to 40% after a deploy. Where do you look?**
*At what changed early in the request*, in order: tool definitions, the system prompt (a timestamp, a per-user field), images, tool_choice, the thinking or effort configuration, the model id (§8). Then check whether the breakpoint is on a block that changes every request (§5).

**4. A developer asks why a one-line question cost $0.80 after lunch.**
*The cache expired over lunch.* The one-line question re-sent the whole 100k-token conversation at the 1-hour write price on Opus 5.5: $0.80 (§7, §17).

**5. Should a nightly report job over 50,000 records use the Batch API?**
*Yes*, if the results can wait up to 24 hours: half price on every token, and caching still applies, best-effort (§12).

**6. You fan out 20 subagents that share a 40k-token briefing. How do you cut the input cost?**
*Send one first, and the other 19 once its response has begun,* so they read the briefing instead of each writing it (§5, §17). Or pre-warm the cache with `max_tokens: 0`.

**7. Is moving a warm 200k Opus 5.5 conversation to Sonnet 5.5 worth it to save money?**
*Rarely, while it's warm.* Sonnet must write 200k tokens (~$0.80 at the 1-hour price), and both models read at the same $0.20/M afterwards. Only the new tokens and output get cheaper. After a break it's a different answer: both must re-write, and Sonnet does it for half (§15).

---

# Appendix A: Price sheet

Per million tokens, from Anthropic's [pricing page](https://platform.claude.com/docs/en/about-claude/pricing) on 2026-09-29. **Re-check there before using these numbers for anything that matters.**

| Model | Input | 5-min write | 1-hour write | Cache read | Output | Batch in / out |
|---|---|---|---|---|---|---|
| Fable 5.1 | $10 | $12.50 | $20 | $0.25 | $50 | $5 / $25 |
| Fable 5 | $10 | $12.50 | $20 | $1 | $50 | $5 / $25 |
| Opus 5.5 | $4 | $5 | $8 | $0.20 | $20 | $2 / $10 |
| Opus 5, 4.8, 4.7, 4.6, 4.5 | $5 | $6.25 | $10 | $0.50 | $25 | $2.50 / $12.50 |
| Sonnet 5.5, Sonnet 5 | $2 | $2.50 | $4 | $0.20 | $10 | $1 / $5 |
| Sonnet 4.6, 4.5 | $3 | $3.75 | $6 | $0.30 | $15 | $1.50 / $7.50 |
| Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 | $0.50 / $2.50 |

| Multiplier | Value |
|---|---|
| 5-minute cache write | 1.25 × input |
| 1-hour cache write | 2 × input |
| Cache read | 0.1 × input (0.05 on Opus 5.5; 0.025 on Fable 5.1 and Mythos 5.1) |
| Output | 5 × input on every model above |
| Batch API | × 0.5 |
| US-only inference, Claude 4.6+ | × 1.1 |
| Bedrock / Google Cloud regional or multi-region endpoints | + 10% |
| Fast mode | Opus 5.5 $8 / $40; Opus 5 and 4.8 $10 / $50; cache multipliers apply on top |
| Web search | $10 per 1,000 searches |
| Long context (up to 1M on Claude 4.6+) | No premium |

# Appendix B: Evidence from real sessions

What building and reviewing usdash measured in real Claude Code transcripts, compared with Claude Code's own totals. It's evidence for one client's behaviour at one version (2.1.278–2.1.283). Re-measure after an upgrade. The regression tests in [`tests/test_real_checks.py`](../tests/test_real_checks.py) hold usdash to the 2026-09-28 sessions in [`tests/fixtures/checks`](../tests/fixtures/checks): the next message reading the last prompt back, resuming within the lifetime, a model switch across tokenizers, and the size after `/compact` waiting for the next request.

| Question | What was measured |
|---|---|
| Does the next request read back the whole previous prompt? | Yes: in 103 consecutive same-model pairs, to within 100 tokens (the uncached remainder was at most 0.04% of a prompt) |
| How big is Claude Code's tool list? | CLI 22–25k tokens; VS Code extension 20.8k; Desktop app 36.3k; `claude -p` 10–20k depending on model and version |
| Is the tool list cached when the conversation's cache isn't? | Usually. Own model on a 1-hour cache: 36 of 39 cold starts read it back, once after 11¾ hours idle. 5-minute cache: 9 of 48 with no other session using the model, 13 of 22 with one. Another model with no session on it: once yes (Sonnet 5), once no (Opus 5.5) |
| Is the lifetime exact? | No, a minimum: a 5-minute entry read back after 6.7 minutes on some requests, missed from 5.65 minutes on others |
| Tokenizer ratio, old to new? | 0.758 in one switch, 0.74 in another; Anthropic says ~30% more tokens (0.77) |
| Does an effort change keep the cache? | Opus 5.5 in Claude Code: 7 of 7 read everything back. Opus 5 in Claude Code: 7% read back |
| Does resuming keep the cache? | Within the lifetime: 6 of 6 resumes read the whole conversation back (1-hour and 5-minute caches, one after a file edit). After ≥ 92 minutes: 31 of 31 re-wrote it |
| What does a warm `/compact` request read? | What the latest turn's first request had cached: 46,885 of 56,584 tokens after a four-turn session, 15,804 of 58,787 when one turn built the context. The rest went at the input price; it wrote only 147–555 tokens |
| And a cold one? | The tool list (13,790), the rest (37,628) at the input price |
| How big is the summary? | Its output: 1,065–3,804 tokens. Its `postTokens`: 3.2–6k for 54–65k conversations, 13,984 at 450k, 16,088 at 972k |
| How big is the conversation right after `/compact`? | 33,335 / 24,987 / 20,315 tokens in three sessions. Tool list + `postTokens` was 13–32% short; first prompt + `postTokens` came within 6% |
| How much do the transcripts miss? | They held 56–98% of Claude Code's own totals (85–95% for most); 87% with a `/compact`; 28% in a session with four web searches |
| A real model switch? | Moving a warm 55.9k conversation from Opus 5.5 to Sonnet 5 cost $0.143, against about $0.02 to stay |

# Appendix C: Glossary

| Term | Meaning |
|---|---|
| **Token** | The unit a model reads and writes; ~4 characters of English |
| **Context** | Everything a request sends: tools, system prompt, conversation |
| **Prefix** | The start of a request, up to some point; what the cache matches |
| **Breakpoint** | A marked block (`cache_control`) where the cache writes an entry |
| **Cache write / read** | Storing a prefix (1.25× or 2× input) / reusing one (0.025–0.1× input) |
| **TTL (lifetime)** | How long a cache entry lives after the start of the last request that used it: 5 minutes or 1 hour, at least |
| **Hit rate** | Share of input tokens read from the cache |
| **Miss** | A request that re-writes what it could have read |
| **Effort** | How much work (and output) the model puts into a response |
| **Thinking** | Reasoning the model does before answering; billed as output |
| **Compaction** | Replacing a conversation with a summary to shrink its context |
| **Subagent / fork** | A separate conversation a session starts; a fork inherits the parent's history and cache |
| **Batch** | Asynchronous processing at half price, within 24 hours |
| **ITPM / OTPM** | Input / output tokens per minute: rate limits; cached reads don't count toward ITPM on most models |

# Appendix D: Sources

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
