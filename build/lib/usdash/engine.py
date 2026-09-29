"""What re-sending each session's conversation costs, from what was measured.

    C       the conversation the next request re-sends: the last prompt, as logged
    now     C read back from the cache, while it's warm: C × the cache-read price
    up to   C written to the cache again, once it has expired: C × the write price

"now" is exact for the next message in a live session: it reads the whole of C
back (research/AI-TOKENOMICS-GUIDE.md, Appendix B). "up to" is an upper bound:
Claude Code's tool list at the start of C often stays cached. What the next
message adds (your text, tool results, the reply) isn't known yet, so it's left out.
"""
from .prices import FIVE_MINUTES, prompt_cost
from .sessions import Session, Store


def cache_clock(session: Session, now: float) -> tuple[bool, int, int]:
    """(warm, seconds left, lifetime) of the main conversation's cache."""
    main = session.main
    ttl = main.ttl or FIVE_MINUTES
    if main.touched is None:
        return False, 0, ttl
    left = int(main.touched + ttl - now)
    return left > 0, max(left, 0), ttl


def context(session: Session) -> int | None:
    """C: the last prompt, measured. None before the first request, and right
    after /compact until the next request measures the new size."""
    last = session.last_request
    if last is None or session.main.compacted:
        return None
    return last.prompt


def resend_costs(store: Store, session: Session, now: float) -> tuple[float | None, float] | None:
    """(now, up to): re-sending C on the session's own model, speed and region.
    `now` only while the cache is warm. None without C or a known price."""
    tokens, price = context(session), store.price(session.model, session)
    if tokens is None or price is None:
        return None
    warm = cache_clock(session, now)[0]
    up_to = prompt_cost(price, tokens, 0, session.ttl or FIVE_MINUTES)
    return (prompt_cost(price, tokens, tokens) if warm else None), up_to
