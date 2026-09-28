"""What a live session shows under its row: what its next message costs on
each model (✅ on the cheapest), and a warning to /compact before a big
conversation's cache expires, when that applies. Which model is good enough
for the task is the user's call; the dollars are there to decide with.
"""
from dataclasses import dataclass, field

from .engine import Context, cache_clock, compact, context, models_for, next_message
from .models import model_key
from .sessions import Session, Store

COMPACT_FROM = 100_000  # context size worth compacting
COMPACT_WARN = 600  # seconds of warm cache left when the /compact warning appears
MIN_SAVING = 0.005  # per message: below this, /compact isn't worth a warning


@dataclass
class Advice:
    ctx: Context
    own: str | None  # the session's model
    prices: list[tuple[str, float, bool]] = field(default_factory=list)  # (model, next message, exact), most capable first
    warning: str | None = None  # ⚡ /compact before the cache expires

    @property
    def cheapest(self) -> str | None:
        """The model with the cheapest next message (the session's own, on a tie)."""
        if not self.prices:
            return None
        return min(self.prices, key=lambda p: (p[1], model_key(p[0]) != model_key(self.own)))[0]


def money(value: float) -> str:
    """Dollars to the cent; an amount under half a cent shows as <$0.01, not $0.00."""
    value = abs(value)
    return "<$0.01" if 0 < value < 0.005 else f"${value:,.2f}"


def tokens_text(value: float | None) -> str:
    """504,113 -> '504k', 9,500 -> '9.5k'."""
    if value is None:
        return "?"
    return f"{value / 1000:.0f}k" if value >= 10_000 else f"{value / 1000:.1f}k"


def resends(ctx) -> str:
    """'re-sends 42k tokens': the conversation the next request sends, ≈ right after /compact."""
    return f"re-sends {'' if ctx.exact else '≈'}{tokens_text(ctx.tokens)} tokens"


def clock(seconds: int) -> str:
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"


def plural(n: int, word: str, words: str | None = None) -> str:
    """'1 message', '6 messages'; `words` for an irregular plural ('web searches')."""
    return f"{n} {word}" if n == 1 else f"{n} {words or word + 's'}"


def advise(store: Store, session: Session, now: float) -> Advice | None:
    """What a live session (cache warm, not exited) shows; None otherwise."""
    warm, left, ttl = cache_clock(session, now)
    ctx = context(store, session)
    if session.ended or ctx is None or not warm:
        return None
    advice = Advice(ctx, session.model)
    if next_message(store, session, session.model, now) is None:
        return advice  # staying has no known price: nothing to compare the others with
    for model in models_for(store, session):
        if (found := next_message(store, session, model, now)) is not None:
            advice.prices.append((model, *found))
    costs = compact(store, session, now)
    if costs and costs.saving >= MIN_SAVING and ctx.tokens >= COMPACT_FROM and left <= min(COMPACT_WARN, ttl // 2):
        advice.warning = (f"Taking a break? /compact first: ≈{money(costs.now)} now, "
                          f"≈{money(costs.after_break)} once the cache expires in {clock(left)}.")
    return advice
