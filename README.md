# 💲 usdash

[![Version](https://img.shields.io/badge/ver.-0.1.0-1a1a2e)](https://github.com/pdudotdev/usdash/releases)
[![License](https://img.shields.io/badge/license-GPLv3-1a1a2e)](LICENSE)
[![Last Commit](https://img.shields.io/github/last-commit/pdudotdev/usdash?color=1a1a2e)](https://github.com/pdudotdev/usdash/commits/master/)

A live terminal dashboard for **your own Claude Code costs**. It reads the transcripts Claude Code already writes on your machine and shows, for each session, whether its prompt cache is still warm, what the session has cost, what your next message will cost, and when switching model, lowering effort or compacting saves money.

It only reads files. Nothing is routed through it, nothing leaves your machine, and it changes nothing in Claude Code.

▫️ **Why the prompt cache matters:**
- [x] **While it's warm**, each message reads the conversation back at a tenth of the input price, or less
- [x] **It expires** 5 minutes or 1 hour after the last request started, and each request restarts that clock
- [x] **After a miss**, the whole conversation is written again, at 1.25× to 2× the input price

![How usdash works: Claude Code in the terminal, VS Code, the Desktop app and scripts writes transcripts to ~/.claude/projects; usdash turns each reply into a request with its cost, cache misses, a cache clock per session, what re-sending each conversation costs and advice, shown as a header, live sessions, idle sessions and, on a key, every request](docs/how-it-works.svg)

## 📖 **Table of Contents**
- 💲 **usdash**
  - [🔭 Overview](#-overview)
  - [🔀 How It Works](#-how-it-works)
  - [🧪 Example Session](#-example-session)
  - [🚀 Installation & Usage](#-installation--usage)
  - [⚠️ Limitations](#️-limitations)
  - [💡 Concepts 101](#-concepts-101)
  - [📂 Project Files](#-project-files)
  - [⬆️ Planned Upgrades](#️-planned-upgrades)
  - [📄 Disclaimer](#-disclaimer)
  - [📜 License](#-license)
  - [📧 Hi](#-hi)

## 🔭 Overview

usdash is a small Python program you keep open in a terminal next to your Claude Code sessions. At the top: today's spend. Below it, every session from the last 24 hours: the ones you're in, with what to do and what each option costs, and the idle ones, with what coming back to them costs. Press `r` for the list of every request.

▫️ **What it looks like** (made-up sessions, drawn by usdash's own screen code):

![usdash: today's spend; a live session with its cache countdown, the action to take and the price of each option; an idle session, three folded script runs and a closed session, each with what coming back to it costs](docs/dashboard.svg)

▫️ **The header:** today's spend at list prices, the share of all input that was read back from the cache, and what cache misses added today, by cause: requests that had to send the conversation again at full price instead of reading it back (the `⟳` rows in the request list). The second line says what the dollars are: API list prices, and on a subscription account, what the same use would cost on an API key. The bottom edge says how far back the loaded history goes.

▫️ **Each session, and how to tell which window it is:**

| Shown | What it is |
|---|---|
| **ID** | The first 4 characters of the session id (as in `/status` and `claude --resume`), in a fixed colour per session |
| **SESSION** | Your `/rename`, else the Desktop app's sidebar title, else Claude Code's automatic title, else the first thing you typed. Tagged with where it runs: `vscode`, `desktop`, or a script's entrypoint such as `sdk-cli`; nothing for the terminal |
| **WHERE** | The project folder, plus `@branch` unless it's main, master or a detached HEAD. `Desktop (no folder)` for a Desktop session without one |
| **MODEL** | The model and effort of the last request |
| **CACHE** | `● mm:ss` while the prompt cache is warm, `○ cold · 2h` once it has expired, `closed · 3h` after `/exit`, with how long ago it was last used |
| **CONTEXT TOKENS** | The conversation's size: everything the next message sends again (the tool list, the system prompt and every message so far). `≈` right after `/compact`, until the next message shows the new size |
| **COST TODAY** · **COST TOTAL** | What the session has cost today (`—` if nothing), and since it started (a resumed session counts its earlier days too). Once it closes, Claude Code's own figure follows: `(CC $…)` |

▫️ **A live session** (its cache is still warm, and it isn't closed) opens up:
- `└` what you last typed in that window, and how long ago
- the action: what to do now (`⚡` when acting right away is clearly cheaper)
- one row per option, with what it costs:

```
     stay       re-sends 182k tokens: $0.04 now, $1.46 after a break
   ↑ Fable 5.1  $3.60 more now, then ≈$0.49 more a message
   ↓ Sonnet 5   $0.69 more now, then ≈$0.16 less a message · evens out after ≈4 messages
   ↓ Haiku 4.5  $0.24 more now, then ≈$0.28 less a message · evens out after ≈1 message
     /compact   ≈$0.15 now, ≈$1.02 after a break; then ≈$0.03 less a message
     /effort    costs nothing now
```

`stay` is what continuing costs: the conversation read back from the cache now, or written again after a break. Each other model (`↑` more capable, `↓` cheaper) shows what switching costs now, how much more or less each later message costs, and after how many messages a cheaper one evens out. `/effort` costs nothing on Opus 5.5 and Fable 5.1, which keep the cache; elsewhere it shows what re-writing the conversation costs.

▫️ **An idle session** (cold or closed) gets one line, with what coming back to it costs on its own model and on each cheaper one:

```
└ resuming re-sends 420k tokens: $3.36 on Opus 5.5, $1.68 on Sonnet 5, $0.65 on Haiku 4.5
```

(`continuing` for a session you haven't closed.) A folder's closed script runs (`claude -p`, SDKs) fold into one row, so a loop of them doesn't bury your sessions.

▫️ **Exact or ≈:** an amount without `≈` is exact: the conversation's size as its last request sent it (the transcript records it), at list prices. What your next message adds (your text, tool results, the reply) isn't known yet, so it's left out of those. Amounts with `≈` are averages from the session's history, or depend on a size usdash can only estimate; see [Limitations](#️-limitations).

▫️ **Every request** (press `r`), newest first:

| Shown | What it is |
|---|---|
| **TIME** | When the request was sent |
| **ID** · **SESSION** · **MODEL** | Which session sent it, on which model and effort |
| **PROMPT** | Tokens sent: the whole conversation so far |
| **CACHED** | The share of them read back from the cache: green from 80%, yellow from 30%, red below |
| **OUT** · **COST** | Output tokens (thinking included), and the request's cost at list prices |
| **NOTE** | `🤖 subagent` for a subagent's request; `⟳ re-wrote 38k: model switch from Opus 5.5 (+$0.09)` when a request had to write the conversation again, with the likely cause and what that cost beyond reading it back |

▫️ **Key characteristics:**
- [x] **Read-only and local:** it reads Claude Code's transcript files and nothing else, except two fields of your account record (subscription or not) and the Desktop app's session titles
- [x] **Every surface on the machine:** terminal, VS Code extension, Desktop app Code tab, scripts
- [x] **Subscription or API key:** the cache lifetime of each session is read from its own usage data (1 hour or 5 minutes)
- [x] **Advice in dollars:** switching model, lowering effort, `/compact`, with the rent-or-buy rule deciding when a switch pays off
- [x] **Re-writes explained:** each request that had to write the conversation again is marked, with a likely cause
- [x] **Checked against Claude Code's own numbers:** tested on real (redacted) transcripts; see [Limitations](#️-limitations) for how close the totals are

## 🔀 How It Works

▫️ **Where the data comes from:**
- [x] Claude Code writes every session to `~/.claude/projects/<folder>/<session-id>.jsonl`, and each subagent to `<session-id>/subagents/agent-<id>.jsonl`, one JSON line per record
- [x] Each reply carries its model, effort and token usage: uncached input, cache reads, cache writes (5-minute or 1-hour) and output
- [x] usdash reads new lines about once a second, looks for new sessions and subagents every 5 seconds, and redraws at most twice a second

▫️ **Every reply:**
1. Several records of one streamed reply are merged into one request, keyed by `message.id` (not `requestId`, which some sessions don't record)
2. The request's start is the time of the message it answers: your prompt, or the tool result before it. The cache clock counts from there
3. Its cost is its tokens at list prices from [`usdash/pricing.yaml`](usdash/pricing.yaml)
4. It's compared with the conversation's previous request. If it failed to read back at least 30% of what it could have (and 5,000 tokens or more), it's a **re-write**, with the likely cause: model switch, cache expired, `/compact`, Claude Code upgrade, or effort change
5. The session's cache clock, what re-sending it costs, and its advice are recomputed

> ⚠️ **NOTE:** The transcript format is internal to Claude Code and can change with any release. usdash reads it leniently (missing fields are "unknown", not a crash) and counts record types it doesn't know. If the header reports unknown records after a Claude Code update, check usdash with the [manual sanity suite](tests/sanity/manual.py).

▫️ **The action a live session gets** (the first that applies):

| Action | When | Example |
|---|---|---|
| ⚡ **Compact before a break** | The conversation is 100k+ tokens, the cache expires within 10 minutes (within half its lifetime, if that's shorter), and compacting saves at least $0.005 a message | *"Taking a break? /compact first: ≈$0.11 now, ≈$0.78 once the cache expires in 5:00."* |
| ⚡ **Switch now** | A cheaper model is cheaper even counting the re-write | *"Switch to Haiku 4.5 now: it's already cheaper."* |
| 💡 **Switch now** | Staying has cost as much as switching would (the rent-or-buy rule) | *"Switch to Haiku 4.5 now: it would have saved $0.21 by now; switching costs $0.08."* |
| 💡 **Lower the effort** | On Opus 5.5 or Fable 5.1 (they keep the cache when effort changes), a lower effort saves at least $0.005 a message. The saving comes from this session's own replies at that effort, or, if it has none, from how much shorter replies got in sessions that used both | *"Try /effort medium: ≈$0.04 less a message, at no cost now."* |
| 💡 **Stay** | A cheaper model saves at least $0.005 a message, but switching now costs extra | *"Stay on Opus 5.5 for now; switching is free after your next 1-hour break."* |

▫️ **How the switch advice decides:**
- **While the cache is warm**, switching costs extra: the other model has to write the whole conversation into its own cache. So usdash says to stay for now and switch after your next break that outlasts the cache (an hour on a subscription, 5 minutes on an API key). The next message after such a break writes everything again on any model, so switching then is free.
- **If you keep going without a break**, usdash adds up what staying costs compared with each cheaper model. Once that passes the cost of switching to one of them, it says to switch. This is the rent-or-buy rule (see [Concepts 101](#-concepts-101)).
- **When switching is cheaper even now**, it says so straight away. That happens, for example, right after `/compact`, if another session in the same folder keeps the other model's copy of the tool list cached.
- Whether the cheaper model is good enough for the task is your call: the option rows show the dollars for each.

## 🧪 Example Session

| # | What you do | What usdash shows |
|---|---|---|
| 1 | Start `claude` in `shop` on branch `checkout-fix` and ask a question | A new live session: your first words as its name, `shop@checkout-fix`, `● 59:5x` on a subscription (`● 4:5x` on an API key) |
| 2 | Wait for Claude Code to title the session | The name changes to that title |
| 3 | `/rename checkout bug` | The name becomes `checkout bug` |
| 4 | Keep working on Opus 5.5 | *"Stay on Opus 5.5 for now; switching is free after your next 1-hour break."*, and a price for each option |
| 5 | `/model sonnet`, then send a message | Press `r`: a red row `⟳ re-wrote 53k: model switch from Opus 5.5 (+$…)`. The header's `cache misses added` total goes up |
| 6 | Step away until the cache expires | The session moves to IDLE, `○ cold · 1h`, with a line saying what continuing costs on each model |
| 7 | Come back and keep going until the context is large; then, with 8 minutes of cache left, look again | `⚡` *"Taking a break? /compact first: ≈$… now, ≈$… once the cache expires in 8:00."* |
| 8 | `/exit` | `closed · 1m`, Claude Code's own figure `(CC $…)` after the session's cost, and what resuming costs |

## 🚀 Installation & Usage

▫️ **Prerequisites:**

| | macOS | Ubuntu |
|---|---|---|
| Python 3.11+ | `brew install python` | `sudo apt install python3 python3-venv` |
| [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) | ✓ | ✓ |

▫️ **Step 1 - Clone and install:**
```
git clone https://github.com/pdudotdev/usdash
cd usdash
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

▫️ **Step 2 - Run it next to your sessions:**
```
usdash                    # the last 24 hours, then live
```

> ⚠️ **NOTE:** The `usdash` command lives in the virtual environment, so a new terminal says `command not found` until you run `source .venv/bin/activate` in the `usdash` folder. To run it from anywhere, link it onto your PATH once, from that folder: `mkdir -p ~/.local/bin && ln -s "$PWD/.venv/bin/usdash" ~/.local/bin/usdash` (and add `~/.local/bin` to your PATH if it isn't there).

| Option | Meaning | Default |
|---|---|---|
| `--since 2d` | How much history to load first (`m`, `h` or `d`); never less than `--window` | `--window`, or since midnight if that's earlier |
| `--window 8h` | Show sessions active this recently | `24h` |
| `--projects DIR` | Where Claude Code keeps its transcripts | `$CLAUDE_CONFIG_DIR/projects`, else `~/.claude/projects` |
| `--once` | Print one screen and exit | off |

↑/↓, the mouse wheel or `j`/`k` scroll the sessions a session at a time; `space`/`b` move a page, `g`/`G` jump to the top or the bottom. `r` switches to every request and back; there the same keys scroll the list, and while you're scrolled back its rows stay put and the title counts the new ones above them. `q` quits.

> ⚠️ **NOTE:** usdash shows the sessions **of the machine it runs on**. With VS Code Remote-SSH, Claude Code runs on the server, so run usdash in a terminal on that server.

## ⚠️ Limitations

▫️ **Where it works:**

| You use Claude Code in… | Covered? |
|---|---|
| A terminal, the VS Code extension, the Desktop app's Code tab, scripts (`claude -p`) | Yes |
| VS Code Remote-SSH or a terminal on a server | Yes, with usdash running on that server |
| claude.ai/code and other cloud sessions | No: their transcripts aren't on your machine |
| Amazon Bedrock, Google Cloud | Partly: see below |
| Windows | Not tested; use WSL |

▫️ **Totals read a little low:**
Some requests Claude Code makes never appear in its transcripts: session titles, prompt suggestions, and `/compact`'s own summarising request. When a session closes, Claude Code writes its own total, shown as `(CC $…)`. On the machine usdash was built on, usdash's totals were 6–18% below Claude Code's for most sessions, and 35% below for one long session with many subagents.

▫️ **Amazon Bedrock and Google Cloud:**
Sessions appear, their cache lifetime and costs are read the same way, and Bedrock model ids like `us.anthropic.claude-opus-5-5` are recognised. But:
- Prices are Anthropic's list prices, which match the **global** endpoints (`global.`). Claude Code's default Bedrock ids use geographic prefixes (`us.`, `eu.`, `apac.`) in most regions, and those cost **10% more**. The same 10% applies to Google Cloud's regional and multi-region endpoints
- Application inference profile ARNs aren't mapped to a model, so those requests show no cost
- Not yet checked with a real Bedrock or Google Cloud transcript: whether Claude Code records the provider's model id or the plain model name. If it's the plain name, the effort tip would wrongly say an effort change keeps the cache there

▫️ **What's exact and what's an estimate (`≈`):**
- **Exact:** re-sending a conversation, now or after a break, on its own model or another: its size as the last request sent it (the whole of it is cached, and resuming re-sends all of it), times list prices. The one approximation is the tokenizer: the same text is ~0.77× the tokens on Haiku 4.5, so Haiku amounts can be off by a few percent
- **≈ per message:** what a later message costs more or less on another model or effort. It assumes messages and replies stay as long as they have been in this session (averages over its recent messages); a stronger model or a lower effort may write more or less
- **≈ /compact:** the summary's size is learned from earlier compactions on this machine (3.8k tokens typical, 14–16k for very long conversations). What compacting now saves over compacting after a break is exact
- **≈ another session's cache:** when another session in the same folder keeps a model's tool list cached, switching there costs less; how much is inferred
- **Resuming soon after `/exit`:** within a cache lifetime, a resumed session may still read its cache; the idle line shows the full re-send, the most it can cost
- Cost isn't the only goal: a stronger model can finish in fewer messages. The dollars are there to decide with

▫️ **Some causes are invisible:**
A gateway or proxy that changes the model behind Claude Code's back shows up as `cause unknown`.

## 💡 Concepts 101

▫️ **Prompt caching**

Every message re-sends the whole conversation. Anthropic caches the start of each request, so the unchanged part is read back cheaply and only the new part costs full price:

| Request | Contents | From cache | New |
|---|---|---|---|
| Turn 1 | tools + system + **msg 1** | nothing | everything |
| Turn 2 | … + msg 1 + reply 1 + **msg 2** | up to msg 1 | reply 1 + msg 2 |
| Turn 3 | … + msg 2 + reply 2 + **msg 3** | up to msg 2 | reply 2 + msg 3 |

- **Price:** a cache read costs 0.1× the input price (0.05× on Opus 5.5, 0.025× on Fable 5.1). A cache write costs 1.25× (5-minute cache) or 2× (1-hour cache)
- **Per model:** each model has its own cache, so a model switch writes the whole conversation again. Opus 5.5 and Sonnet 5 even read the cache at the same price, so moving a cached conversation from Opus to Sonnet saves almost nothing on its cached part
- **Reset by:** a model switch; an effort change (except on Opus 5.5 and Fable 5.1 with an API key or subscription); `/compact`; a Claude Code upgrade

▫️ **Cache lifetime**

| | Subscription (within plan usage) | API key, usage credits, cloud provider |
|---|---|---|
| Main conversation | 1 hour | 5 minutes |
| Subagents and compaction | 5 minutes | 5 minutes |

`promptCacheTtl` (or `CLAUDE_CODE_PROMPT_CACHE_TTL`) changes the main conversation's lifetime; usdash reads the lifetime each session really uses from its usage data. The clock restarts at the **start** of every request that uses the cache:
- A long skill run stays warm, since each tool step is a new request, unless a single step or tool runs longer than the lifetime
- A subagent refreshes its own cache, not the parent's: a parent waiting on a long subagent can go cold

▫️ **The rent-or-buy rule**

Switching model costs a one-time re-write; staying costs a little extra on every message. usdash says to switch when it's already cheaper, or once staying has cost as much as switching would; until then it says to stay, and that switching is free after a break long enough for the cache to expire. That never costs more than about twice the best choice in hindsight.

Example (from a real run): moving a warm 56k-token conversation from Opus 5.5 to Sonnet 5 cost $0.143; staying on Opus cost about $0.02.

▫️ **The full math**

[`research/CACHE-DECISIONS.md`](research/CACHE-DECISIONS.md) has the formulas, worked examples in dollars, the compact/clear/effort decisions, how each input is read from the transcripts, and the checks against real data.

## 📂 Project Files

| File | Role |
|---|---|
| [`usdash/`](usdash/) | The dashboard: transcript reader, sessions, cost engine, advice, screen |
| [`usdash/pricing.yaml`](usdash/pricing.yaml) | Anthropic's list prices, with the date they were verified |
| [`research/CACHE-DECISIONS.md`](research/CACHE-DECISIONS.md) | The math behind every number and piece of advice |
| [`research/REDESIGN-PLAN.md`](research/REDESIGN-PLAN.md) | The plan for the session-first screen, and its assumptions checked against real transcripts |
| [`scripts/make_fixture.py`](scripts/make_fixture.py) | Copies a real transcript into the test fixtures with its text removed |
| [`scripts/readme_screen.py`](scripts/readme_screen.py) | Draws the README's picture of the dashboard, [`docs/dashboard.svg`](docs/dashboard.svg), from made-up sessions |
| [`docs/`](docs/) | The README's pictures: the dashboard and how usdash works |
| [`tests/`](tests/) | Automated tests on redacted real transcripts and synthetic ones, plus the manual sanity suite in [`tests/sanity/`](tests/sanity/manual.py) |
| [`.github/workflows/tests.yml`](.github/workflows/tests.yml) | Runs the automated tests on every push and pull request |

Run the tests with `pip install -r requirements-dev.txt && pytest -q`, and print the manual suite with `python3 tests/sanity/manual.py`.

## ⬆️ Planned Upgrades
- [x] Live cache countdown, the price of continuing and dollar advice per session
- [x] A session-first screen: the last 24 hours, what coming back to each session costs
- [ ] Bedrock and Google Cloud prices: the 10% regional premium, and inference-profile ARNs mapped to their model
- [ ] Advice for `/clear` on a new topic, and for a subagent instead of `/model` on a side task
- [ ] An opt-in Claude Code hook (`PreModelSwitch`) that shows usdash's numbers before a switch

## 📄 Disclaimer
usdash shows estimates at list prices. Your real bill comes from Anthropic, your cloud provider, or your plan. It isn't an official Anthropic tool.

## 📜 License
Licensed under the [**GNU General Public License v3.0**](LICENSE).

## 📧 Hi
Wanna say hello? DM me on [**LinkedIn**](https://www.linkedin.com/in/tmihaicatalin/).
