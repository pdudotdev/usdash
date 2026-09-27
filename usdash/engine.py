"""What continuing each session costs, and what each option would cost.

Exact amounts are the conversation as the last request sent it, at list prices:
the transcript records that size, and the whole of it is cached
(research/CACHE-DECISIONS.md §7). What the next message adds (your text, tool
results, the reply) isn't known yet, so it only goes into per-message
amounts, which are averages from the session's history (≈ on screen).

    C      the conversation the next request re-sends (the last prompt)
    now    re-sending C right now: read back while the cache is warm, else written
    s      the difference per later message, once both setups are cached (≈)
    m      now-difference ÷ s: messages until a switch evens out (≈)

Formulas: research/CACHE-DECISIONS.md (§3 a move, §5 when to make it, §6 /compact).
"""
from dataclasses import dataclass

from .models import LADDER, convert_tokens, effort_keeps_cache, model_key
from .prices import FIVE_MINUTES, output_cost, prompt_cost, write_price
from .sessions import Session, Store

DEFAULT_OUTPUT = 500  # output tokens per message, before any history


@dataclass
class Context:
    """The main conversation as its next request re-sends it."""
    tokens: int  # C
    cached: int  # of those, what its cache holds while warm
    exact: bool  # False right after /compact: the tool list and summary are estimated


@dataclass
class Move:
    target: str
    stay: float  # re-sending C here, now
    move: float  # re-sending C on the target, now (less what it already has cached)
    penalty: float  # move − stay: what switching costs now (negative: it's already cheaper)
    saving: float  # ≈ s: per later message, once both are cached
    exact: bool  # False if another session's cached tool list, or an estimated C, went in

    @property
    def payback(self) -> float | None:
        """≈ m: messages until the switch evens out, when it isn't already cheaper."""
        if self.penalty <= 0:
            return 0.0
        return self.penalty / self.saving if self.saving > 0 else None


@dataclass
class Compact:
    now: float  # ≈ /compact while the cache is warm (the summary's size is a guess)
    after_break: float  # ≈ /compact once it has expired
    saving: float  # ≈ per later message: reading the summary instead of the conversation


def typical_output(store: Store, session: Session, model: str | None, effort: str | None = None) -> float:
    """output(M): the session's own average on this model and effort, else on
    this model, else across interactive sessions, else DEFAULT_OUTPUT."""
    for value in (
        session.average_output(model, effort) if effort else None,
        session.average_output(model),
        store.average_output(model, effort) if effort else None,
        store.average_output(model),
    ):
        if value is not None:
            return value
    return DEFAULT_OUTPUT


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
    ctx, price = context(store, session), store.prices.get(model_key(model))
    if ctx is None or price is None:
        return None
    ttl = session.ttl or FIVE_MINUTES
    if warm and model_key(model) == model_key(session.model):
        return prompt_cost(price, ctx.tokens, ctx.cached, ttl)
    return convert_tokens(ctx.tokens, model_key(session.model), model_key(model)) * write_price(price, ttl) / 1e6


def cheaper(store: Store, model: str | None) -> list[str]:
    """The priced models whose output costs less than `model`'s, most capable first."""
    price = store.prices.get(model_key(model))
    if not price:
        return []
    return [m for m in LADDER if m != model_key(model) and m in store.prices and store.prices[m]["output"] < price["output"]]


def others(store: Store, model: str | None) -> list[str]:
    """Every other priced model in LADDER, most capable first."""
    return [m for m in LADDER if m != model_key(model) and m in store.prices]


def comeback(store: Store, session: Session) -> tuple[Context, list[tuple[str, float]]] | None:
    """What coming back to an idle session costs: re-sending all of C, written
    again, on its own model and on each cheaper one. Other sessions' cached
    tool lists aren't subtracted: a resumed session gets a fresh system
    prompt (git status, date), which may not match theirs."""
    ctx = context(store, session)
    if ctx is None:
        return None
    models = [session.model, *cheaper(store, session.model)]
    costs = [(m, resend(store, session, m, warm=False)) for m in models]
    return ctx, [(m, cost) for m, cost in costs if cost is not None]


def move_penalty(price_e: dict, price_l: dict, size_e: float, cached_e: float, size_l: float, cached_l: float,
                 out_e: float, out_l: float, add_e: float, add_l: float, ttl: int) -> tuple[float, float, float]:
    """(STAY, MOVE, s) for one message, from the token counts on each side
    (research/CACHE-DECISIONS.md §3). add_* is what one later message adds."""
    stay = prompt_cost(price_e, size_e, cached_e, ttl) + output_cost(price_e, out_e)
    move = prompt_cost(price_l, size_l, cached_l, ttl) + output_cost(price_l, out_l)
    later_e = prompt_cost(price_e, size_e + add_e, size_e, ttl) + output_cost(price_e, out_e)
    later_l = prompt_cost(price_l, size_l + add_l, size_l, ttl) + output_cost(price_l, out_l)
    return stay, move, later_e - later_l


def model_move(store: Store, session: Session, target: str, now: float) -> Move | None:
    """Switching the main conversation to `target` now. What it costs now is
    exact (re-sending C there vs reading it back here), less the tool list if
    another session keeps it cached there (then ≈). Per later message (≈): an
    average message's growth and reply on each side; replies on the target
    are this session's own if it has used that model, else the same length as
    now, in the target's tokens. Other sessions' replies come from other tasks."""
    source = session.model
    price_e, price_l = store.prices.get(model_key(source)), store.prices.get(model_key(target))
    ctx = context(store, session)
    if not price_e or not price_l or ctx is None:
        return None
    warm, _, ttl = cache_clock(session, now)
    stay, there = resend(store, session, source, warm), resend(store, session, target, warm=False)
    if stay is None or there is None:
        return None
    size_l = convert_tokens(ctx.tokens, model_key(source), model_key(target))
    shared = min(store.shared_prefix(session, target, now), size_l)
    move = there - shared * (write_price(price_l, ttl) - price_l["cache_read"]) / 1e6
    add = session.growth()
    out_e = typical_output(store, session, source, session.effort)
    out_l = session.average_output(target)
    if out_l is None:
        out_l = convert_tokens(out_e, model_key(source), model_key(target))
    _, _, saving = move_penalty(price_e, price_l, ctx.tokens, ctx.tokens, size_l, size_l, out_e, out_l,
                                add, convert_tokens(add, model_key(source), model_key(target)), ttl)
    return Move(target, stay, move, move - stay, saving, ctx.exact and not shared)


def effort_saving(store: Store, session: Session, effort: str) -> float | None:
    """≈ what a lower effort saves per message, on a model that keeps its cache
    across the change (nothing else changes): the difference in replies.
    Replies at that effort: this session's own, if it has used it; else its
    replies now, scaled by how much shorter they got at that effort in
    sessions that used both. Other sessions' replies alone come from other
    tasks. None without either."""
    model = session.model
    price = store.prices.get(model_key(model))
    if not price or not effort_keeps_cache(model) or session.last_request is None:
        return None
    before = session.average_output(model, session.effort)
    after = session.average_output(model, effort)
    if after is None and before is not None:
        ratio = store.output_ratio(model, session.effort, effort)
        after = before * ratio if ratio is not None else None
    if before is None or after is None:
        return None
    return output_cost(price, before) - output_cost(price, after)


def effort_rewrite(store: Store, session: Session, now: float) -> float | None:
    """What an effort change costs now on a model where it re-writes the
    conversation (all but Opus 5.5 and Fable 5.1, and those on a cloud
    provider): writing C instead of reading it back. None where the cache is
    kept; 0 once the cache has expired anyway."""
    if effort_keeps_cache(session.model):
        return None
    warm, _, _ = cache_clock(session, now)
    stay, rewrite = resend(store, session, session.model, warm), resend(store, session, session.model, False)
    return None if stay is None or rewrite is None else rewrite - stay


def compact(store: Store, session: Session, now: float) -> Compact | None:
    """/compact re-sends the conversation (read back while warm, written after
    a break, with the 5-minute cache even on a subscription) and writes a
    summary; later messages read the tool list and the summary instead of the
    conversation (research/CACHE-DECISIONS.md §6). The summary's size is
    learned from earlier compactions (Store.summary_size), so all three
    amounts are ≈; the difference between now and after a break is exact."""
    price, ctx = store.prices.get(model_key(session.model)), context(store, session)
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
