"""What the next message costs, and what changing setup would cost, per session.

All formulas are from research/CACHE-DECISIONS.md (§3 for a move, §5 for when to make
it, §6 for /compact). Symbols as there:

    N  tokens in the next prompt (the conversation plus the new message)
    W  of those, what the current setup E has cached (0 once its cache expired)
    S  what the other setup L has cached: the tool list and system prompt if
       any session in the same folder used L within its cache lifetime, else 0
    STAY = W·read(E) + (N−W)·write(E) + output(E)·out(E)
    MOVE = S·read(L) + (N−S)·write(L) + output(L)·out(L)
    P  = MOVE − STAY      the one-time price of moving now
    s  = saving per later message, once both are cached
    m  = P ÷ s            messages until moving pays back
"""
from dataclasses import dataclass
from statistics import median

from .models import convert_tokens, effort_keeps_cache, model_key
from .prices import FIVE_MINUTES, output_cost, prompt_cost
from .sessions import Session, Store

DEFAULT_GROWTH = 1_000  # tokens a message adds, before a session has history
DEFAULT_OUTPUT = 500  # output tokens per message, before any history
# The summary /compact writes, and what it re-sends (research/CACHE-DECISIONS.md §6).
COMPACT_SUMMARY = 3_000


@dataclass
class CacheState:
    warm: bool
    left: int  # seconds until the cache expires
    ttl: int
    size: int  # N
    cached: int  # W
    next_now: float | None  # the next message, sent now
    next_cold: float | None  # the next message, after the cache expired


@dataclass
class Move:
    target: str  # a model id, or "effort:<level>"
    stay: float
    move: float
    penalty: float  # P
    saving: float  # s, per later message
    warm: bool
    left: int

    @property
    def payback(self) -> float | None:
        """m: messages until moving pays back, when it isn't already cheaper."""
        if self.penalty <= 0:
            return 0.0
        return self.penalty / self.saving if self.saving > 0 else None


def growth(session: Session) -> int:
    """Typical tokens one message adds to the conversation: the median rise
    between consecutive main-conversation prompts (kept until the session changes)."""
    if session._growth[0] == session.revision:
        return session._growth[1]
    prompts = [r.prompt for r in session.main_requests()]
    rises = [b - a for a, b in zip(prompts, prompts[1:]) if b > a]
    value = int(median(rises)) if rises else DEFAULT_GROWTH
    session._growth = (session.revision, value)
    return value


def typical_output(store: Store, session: Session, model: str | None, effort: str | None = None,
                   default: float = DEFAULT_OUTPUT) -> float:
    """output(M): the session's own average on this model and effort, else on
    this model, else across all sessions, else `default`."""
    for value in (
        session.average_output(model, effort) if effort else None,
        session.average_output(model),
        store.average_output(model, effort) if effort else None,
        store.average_output(model),
    ):
        if value is not None:
            return value
    return default


def conversation(session: Session, now: float) -> tuple[int, int, bool, int, int]:
    """(N, W, warm, seconds left, ttl) for the main conversation."""
    main, last = session.main, session.last_request
    ttl = main.ttl or FIVE_MINUTES
    if last is None or main.touched is None:
        return 0, 0, False, 0, ttl
    left = int(main.touched + ttl - now)
    warm = left > 0
    if main.compacted:
        # After /compact only the tool list and system prompt are still cached.
        prefix = round(session.prefix_on(last.model))
        size = prefix + (session.compact_post_tokens or COMPACT_SUMMARY)
        return size, prefix if warm else 0, warm, max(left, 0), ttl
    size = last.prompt + growth(session)
    return size, last.prompt if warm else 0, warm, max(left, 0), ttl


def cache_state(store: Store, session: Session, now: float) -> CacheState | None:
    if session.last_request is None:
        return None
    size, cached, warm, left, ttl = conversation(session, now)
    price = store.prices.get(model_key(session.model))
    if price is None:
        return CacheState(warm, left, ttl, size, cached, None, None)
    output = output_cost(price, typical_output(store, session, session.model, session.effort))
    return CacheState(
        warm, left, ttl, size, cached,
        prompt_cost(price, size, cached, ttl) + output,
        prompt_cost(price, size, 0, ttl) + output,
    )


def move_penalty(price_e: dict, price_l: dict, size_e: float, cached_e: float, size_l: float, cached_l: float,
                 out_e: float, out_l: float, add_e: float, add_l: float, ttl: int) -> tuple[float, float, float]:
    """(STAY, MOVE, s) for one message, from the token counts on each side.
    add_* is what one later message adds, for the per-message saving."""
    stay = prompt_cost(price_e, size_e, cached_e, ttl) + output_cost(price_e, out_e)
    move = prompt_cost(price_l, size_l, cached_l, ttl) + output_cost(price_l, out_l)
    later_e = prompt_cost(price_e, size_e + add_e, size_e, ttl) + output_cost(price_e, out_e)
    later_l = prompt_cost(price_l, size_l + add_l, size_l, ttl) + output_cost(price_l, out_l)
    return stay, move, later_e - later_l


def model_move(store: Store, session: Session, target: str, now: float) -> Move | None:
    """Switching the main conversation to another model (research/CACHE-DECISIONS.md §3)."""
    source = session.model
    price_e, price_l = store.prices.get(model_key(source)), store.prices.get(model_key(target))
    if not price_e or not price_l or session.last_request is None:
        return None
    size, cached, warm, left, ttl = conversation(session, now)
    size_l = convert_tokens(size, model_key(source), model_key(target))
    shared = min(store.shared_prefix(session.cwd, target, now), size_l)
    add = growth(session)
    out_e = typical_output(store, session, source, session.effort)
    # Replies on the target: this session's own, if it has used that model; else
    # the same length as now. Other sessions' replies on it come from other
    # tasks (a scripted "say OK" run makes a model look nearly free).
    out_l = session.average_output(target)
    out_l = out_e if out_l is None else out_l
    stay, move, saving = move_penalty(
        price_e, price_l, size, cached, size_l, shared, out_e, out_l,
        add, convert_tokens(add, model_key(source), model_key(target)), ttl,
    )
    return Move(target, stay, move, move - stay, saving, warm, left)


def effort_move(store: Store, session: Session, effort: str, now: float) -> Move | None:
    """Lowering effort on a model that keeps its cache across the change:
    only the output changes, so the saving starts with the next message."""
    model = session.model
    price = store.prices.get(model_key(model))
    if not price or not effort_keeps_cache(model) or session.last_request is None:
        return None
    before = session.average_output(model, session.effort) or store.average_output(model, session.effort)
    after = store.average_output(model, effort)
    if before is None or after is None:
        return None
    size, cached, warm, left, ttl = conversation(session, now)
    base = prompt_cost(price, size, cached, ttl)
    stay, move = base + output_cost(price, before), base + output_cost(price, after)
    return Move(f"effort:{effort}", stay, move, move - stay, stay - move, warm, left)


def compact_cost(store: Store, session: Session, now: float) -> tuple[float, float] | None:
    """(/compact now, /compact after the cache expired). Compaction re-sends the
    whole conversation and writes a summary; it always uses the 5-minute
    cache (research/CACHE-DECISIONS.md §6)."""
    price = store.prices.get(model_key(session.model))
    last = session.last_request
    if not price or last is None or session.main.compacted:
        return None
    size, cached, _, _, _ = conversation(session, now)
    summary = output_cost(price, COMPACT_SUMMARY)
    return (prompt_cost(price, size, cached, FIVE_MINUTES) + summary,
            prompt_cost(price, size, 0, FIVE_MINUTES) + summary)
