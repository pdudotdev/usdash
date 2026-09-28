"""What re-sending each session's conversation costs, on each model.

Amounts are the conversation as the last request sent it, at list prices:
the transcript records that size, and the whole of it is cached
(research/CACHE-DECISIONS.md §7). What the next message adds (your text,
tool results, the reply) isn't known yet, so it's left out.

    C      the conversation the next request re-sends (the last prompt)
    now    re-sending C right now: read back while the cache is warm, else written

Formulas: research/CACHE-DECISIONS.md (§3 on another model, §6 /compact).
"""
from dataclasses import dataclass

from .models import model_key
from .prices import FIVE_MINUTES, output_cost, prompt_cost, sent_cost, write_price
from .sessions import Session, Store


@dataclass
class Context:
    """The main conversation as its next request re-sends it."""
    tokens: int  # C
    cached: int  # of those, what its cache holds while warm
    exact: bool  # False right after /compact: the tool list and summary are estimated


@dataclass
class Compact:
    now: float  # ≈ /compact while the cache is warm (the summary's size is a guess)
    after_break: float  # ≈ /compact once it has expired
    saving: float  # ≈ per later message: reading the summary instead of the conversation


def after_compact(store: Store, session: Session, model: str | None) -> tuple[int, int]:
    """(what a request right after /compact sends besides the summary, what of it is
    likely cached): the session's first prompt, since Claude Code attaches what it
    attached at the start again (the environment, the agent, skill and tool
    listings, files read), and the tool list. The tool list alone plus
    `compactMetadata.postTokens` was 13–32% short of the real first prompt after
    three compactions; the first prompt plus postTokens, within 6%
    (research/CACHE-DECISIONS.md §7)."""
    cached = round(store.tool_list(session, model))
    return max(cached, round(store.first_prompt(session, model))), cached


def context(store: Store, session: Session) -> Context | None:
    """C: the last prompt, measured. Right after /compact, before the next
    request, the conversation is after_compact() plus the summary instead, and
    only the tool list is surely still cached."""
    last = session.last_request
    if last is None:
        return None
    if session.main.compacted:
        base, cached = after_compact(store, session, last.model)
        summary = session.compact_post_tokens or store.summary_size(last.prompt)
        return Context(base + summary, cached, False)
    return Context(last.prompt, last.prompt, True)


def cache_clock(session: Session, now: float) -> tuple[bool, int, int]:
    """(warm, seconds left, lifetime) of the main conversation's cache."""
    main = session.main
    ttl = main.ttl or FIVE_MINUTES
    if main.touched is None:
        return False, 0, ttl
    left = int(main.touched + ttl - now)
    return left > 0, max(left, 0), ttl


def resend(store: Store, session: Session, model: str | None, warm: bool) -> float | None:
    """$ to re-send C on `model`. Its cached part is read back only if `warm`
    and `model` is the session's own (no other model holds this
    conversation); everything else is written, with the session's cache
    lifetime."""
    ctx, price = context(store, session), store.price(model, session)
    if ctx is None or price is None:
        return None
    ttl = session.ttl or FIVE_MINUTES
    if warm and model_key(model) == model_key(session.model):
        return prompt_cost(price, ctx.tokens, ctx.cached, ttl)
    return prompt_cost(price, store.facts.convert(ctx.tokens, session.model, model), 0, ttl)


def models_for(store: Store, session: Session) -> list[str]:
    """The priced models to show a session's costs on: the current lineup,
    most capable first, with its own model first if it isn't one of them."""
    own, lineup = model_key(session.model), store.facts.lineup
    return [m for m in (lineup if own in lineup else [own, *lineup]) if m in store.prices]


def cold_resend(store: Store, session: Session, model: str, now: float) -> float | None:
    """≈ $ to re-send C on `model` when the conversation's own cache can't be
    read (it expired, the session exited, or another model): all written
    again, but the tool list read back if it's likely still cached there
    (Store.shared_prefix). Whether it is can't be known, so this is ≈."""
    ctx, price = context(store, session), store.price(model, session)
    cost = resend(store, session, model, warm=False)
    if ctx is None or price is None or cost is None:
        return None
    ttl = session.ttl or FIVE_MINUTES
    shared = min(store.shared_prefix(session, model, now), store.facts.convert(ctx.tokens, session.model, model))
    return cost - shared * (write_price(price, ttl) - price["cache_read"]) / 1e6


def next_message(store: Store, session: Session, model: str, now: float) -> tuple[float, bool] | None:
    """(what re-sending C costs on `model` right now, whether that's exact).
    The session's own model reads its cache back while it's warm: exact
    (but ≈ right after /compact). Otherwise cold_resend: ≈."""
    warm, _, _ = cache_clock(session, now)
    ctx = context(store, session)
    if ctx is not None and warm and model_key(model) == model_key(session.model):
        cost = resend(store, session, model, warm=True)
        return None if cost is None else (cost, ctx.exact)
    cost = cold_resend(store, session, model, now)
    return None if cost is None else (cost, False)


def comeback(store: Store, session: Session, now: float) -> tuple[Context, list[tuple[str, float]]] | None:
    """What coming back to an expired or exited session costs, on each model: ≈
    cold_resend. An exited session whose cache hasn't run out yet reads it back
    on its own model when resumed (6 of 6 resumes within the lifetime did, one
    after a file edit), so there it's priced like a live one's next message,
    but ≈: a changed system prompt (a new day, a changed CLAUDE.md) would re-write it."""
    ctx = context(store, session)
    if ctx is None:
        return None
    warm = cache_clock(session, now)[0]
    costs = [(m, resend(store, session, m, warm=True) if warm and model_key(m) == model_key(session.model)
              else cold_resend(store, session, m, now)) for m in models_for(store, session)]
    return ctx, [(m, cost) for m, cost in costs if cost is not None]


def compact(store: Store, session: Session, now: float) -> Compact | None:
    """/compact's own request re-sends the conversation without caching it, and
    writes a summary; later messages send after_compact() and the summary
    instead of the conversation (research/CACHE-DECISIONS.md §6). What it
    reads back, per Claude Code's own totals around five real compactions:
    while warm, what the latest turn's first request left cached (the
    conversation up to that prompt); after a break, the tool list at most.
    The rest goes at the input price. The summary's size is learned from
    earlier compactions (Store.summary_size), so all three amounts are ≈."""
    price, ctx = store.price(session.model, session), context(store, session)
    if not price or ctx is None or session.main.compacted:
        return None
    warm, left, _ = cache_clock(session, now)
    summary = store.summary_size(ctx.tokens)
    written = output_cost(price, summary)
    # Once the cache has run out, the tool list may still be cached (Store.shared_prefix, as it will be then).
    cold = min(store.shared_prefix(session, session.model, now + left), ctx.tokens)
    after_break = sent_cost(price, ctx.tokens, cold) + written
    turn, family = session.turn_cached or (0, None)
    turn = turn if family == model_key(session.model) else 0
    now_cost = sent_cost(price, ctx.tokens, max(turn, cold)) + written if warm else after_break
    base, _ = after_compact(store, session, session.model)
    saving = max(0.0, (ctx.tokens - base - summary) * price["cache_read"] / 1e6)
    return Compact(now_cost, after_break, saving)
