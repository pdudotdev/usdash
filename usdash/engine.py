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
from .prices import FIVE_MINUTES, output_cost, prompt_cost, write_price
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


def context(store: Store, session: Session) -> Context | None:
    """C: the last prompt, measured. Right after /compact, before the next
    request, the conversation is the tool list and system prompt plus the
    summary instead, and only the first part is still cached."""
    last = session.last_request
    if last is None:
        return None
    if session.main.compacted:
        prefix = round(store.tool_list(session, last.model))
        summary = session.compact_post_tokens or store.summary_size(last.prompt)
        return Context(prefix + summary, prefix, False)
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
    """What coming back to an expired or exited session costs, on each model: ≈ cold_resend.
    Resuming re-writes all of the conversation even minutes later: a resumed
    session sends a fresh system prompt, and only the tool list before it
    can still be read back."""
    ctx = context(store, session)
    if ctx is None:
        return None
    costs = [(m, cold_resend(store, session, m, now)) for m in models_for(store, session)]
    return ctx, [(m, cost) for m, cost in costs if cost is not None]


def compact(store: Store, session: Session, now: float) -> Compact | None:
    """/compact re-sends the conversation (read back while warm, written after
    a break, with the 5-minute cache even on a subscription) and writes a
    summary; later messages read the tool list and the summary instead of the
    conversation (research/CACHE-DECISIONS.md §6). The summary's size is
    learned from earlier compactions (Store.summary_size), so all three
    amounts are ≈; the difference between now and once the cache expires is exact."""
    price, ctx = store.price(session.model, session), context(store, session)
    if not price or ctx is None or session.main.compacted:
        return None
    warm, _, _ = cache_clock(session, now)
    summary = store.summary_size(ctx.tokens)
    written = output_cost(price, summary)
    after_break = prompt_cost(price, ctx.tokens, 0, FIVE_MINUTES) + written
    now_cost = prompt_cost(price, ctx.tokens, ctx.cached, FIVE_MINUTES) + written if warm else after_break
    remaining = store.tool_list(session, session.model) + summary
    saving = max(0.0, (ctx.tokens - remaining) * price["cache_read"] / 1e6)
    return Compact(now_cost, after_break, saving)
