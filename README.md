# 💲 usdash

[![Version](https://img.shields.io/badge/ver.-0.1.0-1a1a2e)](https://github.com/pdudotdev/usdash/releases)
[![License](https://img.shields.io/badge/license-GPLv3-1a1a2e)](LICENSE)
[![Last Commit](https://img.shields.io/github/last-commit/pdudotdev/usdash?color=1a1a2e)](https://github.com/pdudotdev/usdash/commits/master/)

A live terminal dashboard for **your own Claude Code costs**. It reads the transcripts Claude Code already writes on your machine and shows, for each session, whether its prompt cache is still warm, what the session has cost, what your next message will cost, and when switching model, lowering effort or compacting saves money.

It only reads files. Nothing is routed through it, nothing leaves your machine, and it changes nothing in Claude Code.

▫️ **Same idea as an ARP cache:**
- [x] **A fresh entry** → reused without asking again. Here: the conversation is read back from the prompt cache at a tenth of the input price, or less
- [x] **The aging timer** → restarted by every use, expires when idle. Here: 5 minutes or 1 hour after the last request started
- [x] **A miss** → a full lookup. Here: the whole conversation is written again, at 1.25× to 2× the input price

```
    terminal (claude)      VS Code extension      Desktop app Code tab      scripts (claude -p)
__________▼_______________________▼________________________▼_________________________▼__________
\                     ~/.claude/projects/<folder>/<session>.jsonl  (+ subagents/)              /
 \            the transcripts Claude Code writes anyway: every reply with its token usage     /
  \__________________________________________________________________________________________/
                                               │
                                               ▼
                    usdash: one request per reply → cost at list price → re-writes and why
                            → a cache clock per session → next-message price → advice
                                               │
                     ┌─────────────────┬───────┴─────────┬──────────────────┐
                     ▼                 ▼                 ▼                  ▼
                   today's         sessions:          advice,          every request,
                  spend and      name, where,       in dollars         re-writes marked
                  hit rate      cache countdown
```

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

usdash is a small Python program you keep open in a terminal next to your Claude Code sessions. It has four panes: today's spend, the sessions, advice, and a scrollable feed of requests.

▫️ **What it looks like** (made-up sessions, 150 columns wide):
```
╭─ 💲 usdash · live ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ TODAY $1.033 est.  ·  56% of input read from cache  ·  re-writes cost $0.087 extra (model switch $0.087)                                           │
│ API-equivalent $ (a subscription isn't billed per token)  ·  list prices of 2026-09-26  ·  reads a little low: background requests aren't logged   │
╰────────────────────────────────────────────────────────────── history from Mon 14:00 ──────────────────────────────────────────────────────────────╯
╭─ sessions active in the last 3h · sub: 1h cache · api: 5m, billed per token ───────────────────────────────────────────────────────────────────────╮
│ ID    SESSION                    WHERE              MODEL          CACHE    NEXT MESSAGE              CONTEXT   TODAY   TOTAL                      │
│ 3f9a  Fix checkout rounding sub  shop@checkout-fix  Opus 5.5 high  ● 35:00  $0.078 now · $0.484 cold      52k  $0.690  $0.690                      │
│       └ 25m ago · "run the suite and tell me what fails"                                                                                           │
│ b21e  Release notes vscode api   shop               Sonnet 5 high  ○ cold   $0.118 (re-writes 44k)        42k  $0.343  $0.343                      │
│       └ 25m ago · "add a note about the database migration"                                                                                        │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ advice · estimates; cost isn't the only goal ─────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ 💡 3f9a Fix checkout rounding · shop@checkout-fix: Warm on Opus 5.5 for 35:00 more. Switching to Sonnet 5 now costs +$0.164 (pays back after ~5    │
│ messages); after that it's free.  (uses less of your plan)                                                                                         │
│ 💡 b21e Release notes · shop: Cache expired, so switching model costs nothing extra now: next message ≈$0.045 on Haiku 4.5, vs $0.118 on Sonnet 5. │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ requests ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ TIME      ID    SESSION                MODEL          PROMPT  CACHED   OUT    COST  NOTE                                                           │
│ 14:06:00  3f9a  Fix checkout rounding  Opus 5.5 high     52k     94%  1.8k  $0.070                                                                 │
│ 14:05:20  b21e  Release notes          Sonnet 5 high     42k     96%  0.7k  $0.019                                                                 │
│ 14:04:30  3f9a  Fix checkout rounding  Opus 5.5 high     16k     87%  0.4k  $0.021  🤖 subagent                                                    │
│ 14:04:10  3f9a  Fix checkout rounding  Opus 5.5 high     14k      0%  0.6k  $0.082  🤖 subagent                                                    │
│ 14:04:00  3f9a  Fix checkout rounding  Opus 5.5 high     49k     92%  1.8k  $0.077                                                                 │
│ 14:03:20  b21e  Release notes          Sonnet 5 high     41k      0%  0.9k  $0.110  ⟳ re-wrote 38k: model switch from Opus 5.5 (+$0.087)           │
│ 14:02:00  3f9a  Fix checkout rounding  Opus 5.5 high     45k     91%  1.8k  $0.076                                                                 │
│ 14:00:30  b21e  Release notes          Opus 5.5 high     38k      0%  1.2k  $0.214                                                                 │
│ 14:00:00  3f9a  Fix checkout rounding  Opus 5.5 high     41k      0%  1.8k  $0.364                                                                 │
╰────────────────────────────────────────────────────────────────────────────── ↑↓/wheel/j/k: scroll · space/b: page · g/G: newest/oldest · q: quit ─╯
```

▫️ **The header:** today's spend at list prices, the share of all input that was read back from the cache, and what re-writes cost today, by cause. On a subscription account, the dollars are what the same tokens would cost on an API key. Its bottom edge says how far back the loaded history goes.

▫️ **Each session, and how to tell which window it is:**

| Shown | What it is |
|---|---|
| **ID** | The first 4 characters of the session id (as in `/status` and `claude --resume`), in a fixed colour per session |
| **SESSION** | Your `/rename`, else the Desktop app's sidebar title, else Claude Code's automatic title, else the first thing you typed. Tagged with where it runs (`vscode`, `desktop`, `sdk-cli`; none for the terminal) and how it's billed (`sub` or `api`) |
| **WHERE** | The project folder, plus `@branch` unless it's main, master or a detached HEAD. `Desktop (no folder)` for a Desktop session without one |
| **└ line** | What you last typed in that window, and how long ago |
| **MODEL** | The model and effort of the last request |
| **CACHE** | `● mm:ss` while the prompt cache is warm, `○ cold` once it has expired, `closed` after `/exit` |
| **NEXT MESSAGE** | While warm: what the next message costs now, and after the cache expires. Once cold: what it costs, and how much of the conversation it writes again. Once closed: what it would cost if resumed |
| **CONTEXT** · **TODAY** · **TOTAL** | The conversation's size, and its cost today and in all. A closed session also shows Claude Code's own total, `(CC $…)` |

▫️ **Advice:** one line per session when there's something worth saying. `⚡` means act before the cache expires; `💡` is worth knowing. The rules are in [How It Works](#-how-it-works).

▫️ **Every request, newest first:**

| Shown | What it is |
|---|---|
| **TIME** | When the request was sent |
| **ID** · **SESSION** · **MODEL** | Which session sent it, on which model and effort |
| **PROMPT** | Tokens sent: the whole conversation so far |
| **CACHED** | The share of them read back from the cache: green from 80%, yellow from 30%, red below |
| **OUT** · **COST** | Output tokens (thinking included), and the request's cost at list prices |
| **NOTE** | `🤖 subagent` for a subagent's request; `⟳ re-wrote 38k: model switch from Opus 5.5 (+$0.087)` when a request had to write the conversation again, with the likely cause and what that cost beyond reading it back |

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
5. The session's cache clock, next-message price and advice are recomputed

> ⚠️ **NOTE:** The transcript format is internal to Claude Code and can change with any release. usdash reads it leniently (missing fields are "unknown", not a crash) and counts record types it doesn't know. If the header reports unknown records after a Claude Code update, check usdash with the [manual sanity suite](tests/sanity/manual.py).

▫️ **Advice rules:**

| Rule | Shown when | Example |
|---|---|---|
| **Switch model** | A cheaper model saves at least $0.005 per message | Cold: *"Cache expired, so switching model costs nothing extra now: next message ≈$0.045 on Haiku 4.5, vs $0.118 on Sonnet 5."* Warm: *"Warm on Opus 5.5 for 35:00 more. Switching to Sonnet 5 now costs +$0.164 (pays back after ~5 messages); after that it's free."* |
| **Compact before a break** | The conversation is 100k+ tokens, the cache is warm, and it expires within 10 minutes (within half its lifetime, if that's shorter) | *"Context 140k, cache expires in 5:00: /compact now ≈$0.093; after a break ≈$0.760."* |
| **Lower effort** | On Opus 5.5 or Fable 5.1 (they keep the cache when effort changes), a lower effort saves at least $0.005 per message. The saving comes from this session's own replies at that effort, or, if it has none, from how much shorter replies got in sessions that used both efforts | *"Lower /effort to medium: ≈$0.044 less per message, no cache cost on Opus 5.5."* |

The switch rule is the rent-or-buy rule (see [Concepts 101](#-concepts-101)). While the cache is warm, it also says *"Switch to Haiku 4.5 now: already cheaper"* when moving costs less even now (for example right after `/compact`, if another session in the same folder keeps Haiku's copy of the tool list cached), and *"Switching to Sonnet 5 pays off now"* once staying has cost as much as switching would.

## 🧪 Example Session

| # | What you do | What usdash shows |
|---|---|---|
| 1 | Start `claude` in `shop` on branch `checkout-fix` and ask a question | A new row: your first words as its name, `shop@checkout-fix`, `● 59:5x` on a subscription (`● 4:5x` on an API key) |
| 2 | Wait for Claude Code to title the session | The name changes to that title |
| 3 | `/rename checkout bug` | The name becomes `checkout bug` |
| 4 | Keep working on Opus 5.5 | Advice: *"Warm on Opus 5.5 for mm:ss more. Switching to Sonnet 5 now costs +$… (pays back after ~N messages); after that it's free."* |
| 5 | `/model sonnet`, then send a message | A red feed row: `⟳ re-wrote 53k: model switch from Opus 5.5 (+$…)`, and the header's re-write total goes up |
| 6 | Step away until the cache expires | CACHE shows `○ cold`; advice says switching now costs nothing extra |
| 7 | Come back and keep going until the context is large; then, with 8 minutes of cache left, look at the advice | `⚡` *"Context 140k, cache expires in 8:00: /compact now ≈$0.093; after a break ≈$0.760."* |
| 8 | `/exit` | CACHE shows `closed`, and TOTAL adds Claude Code's own figure `(CC $…)` |

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
usdash                    # today's sessions, then live
```

> ⚠️ **NOTE:** The `usdash` command lives in the virtual environment, so a new terminal says `command not found` until you run `source .venv/bin/activate` in the `usdash` folder. To run it from anywhere, link it onto your PATH once, from that folder: `mkdir -p ~/.local/bin && ln -s "$PWD/.venv/bin/usdash" ~/.local/bin/usdash` (and add `~/.local/bin` to your PATH if it isn't there).

| Option | Meaning | Default |
|---|---|---|
| `--since 24h` | How much history to load first (`m`, `h` or `d`); never less than `--window` | since midnight |
| `--window 8h` | Show sessions active this recently | `3h` |
| `--projects DIR` | Where Claude Code keeps its transcripts | `$CLAUDE_CONFIG_DIR/projects`, else `~/.claude/projects` |
| `--once` | Print one screen and exit | off |

Scroll the request feed with ↑/↓, the mouse wheel or `j`/`k`; `space`/`b` move a page, `g`/`G` jump to the newest/oldest, `q` quits. While you're scrolled back, the rows in view stay put, and the title counts the new rows above them.

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
- These sessions are tagged `api` (billed per token), which is right, though they aren't billed through an API key
- Not yet checked with a real Bedrock or Google Cloud transcript: whether Claude Code records the provider's model id or the plain model name. If it's the plain name, the effort tip would wrongly say an effort change keeps the cache there

▫️ **Advice is an estimate:**
It predicts the next message from the session's history: how long its replies are, and how much each message adds. Other things are inferred too: how many tokens the same text is on another model's tokenizer (×0.77 on Haiku 4.5, on average), and what another session keeps cached. The advice shows the dollars; cost isn't the only goal, and a stronger model can finish in fewer messages.

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

Switching model costs a one-time re-write; staying costs a little extra on every message. usdash suggests switching when it's already cheaper, when the cache has expired (then it's free), or once staying has cost as much as switching would. That never costs more than about twice the best choice in hindsight.

Example (from a real run): moving a warm 56k-token conversation from Opus 5.5 to Sonnet 5 cost $0.143; staying on Opus cost about $0.02.

▫️ **The full math**

[`research/CACHE-DECISIONS.md`](research/CACHE-DECISIONS.md) has the formulas, worked examples in dollars, the compact/clear/effort decisions, how each input is read from the transcripts, and the checks against real data.

## 📂 Project Files

| File | Role |
|---|---|
| [`usdash/`](usdash/) | The dashboard: transcript reader, sessions, cost engine, advice, screen |
| [`usdash/pricing.yaml`](usdash/pricing.yaml) | Anthropic's list prices, with the date they were verified |
| [`research/CACHE-DECISIONS.md`](research/CACHE-DECISIONS.md) | The math behind every number and piece of advice |
| [`scripts/make_fixture.py`](scripts/make_fixture.py) | Copies a real transcript into the test fixtures with its text removed |
| [`tests/`](tests/) | Automated tests on redacted real transcripts and synthetic ones, plus the manual sanity suite in [`tests/sanity/`](tests/sanity/manual.py) |
| [`.github/workflows/tests.yml`](.github/workflows/tests.yml) | Runs the automated tests on every push and pull request |

Run the tests with `pip install -r requirements-dev.txt && pytest -q`, and print the manual suite with `python3 tests/sanity/manual.py`.

## ⬆️ Planned Upgrades
- [x] Live cache countdown, next-message price and dollar advice per session
- [ ] Bedrock and Google Cloud prices: the 10% regional premium, and inference-profile ARNs mapped to their model
- [ ] Advice for `/clear` on a new topic, and for a subagent instead of `/model` on a side task
- [ ] An opt-in Claude Code hook (`PreModelSwitch`) that shows usdash's numbers before a switch

## 📄 Disclaimer
usdash shows estimates at list prices. Your real bill comes from Anthropic, your cloud provider, or your plan. It isn't an official Anthropic tool.

## 📜 License
Licensed under the [**GNU General Public License v3.0**](LICENSE).

## 📧 Hi
Wanna say hello? DM me on [**LinkedIn**](https://www.linkedin.com/in/tmihaicatalin/).
