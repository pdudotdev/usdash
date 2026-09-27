"""What to do in each live session, and what each option would cost.

One action per session, the first that applies:
  1. ⚡ /compact before a break: a big conversation whose cache expires soon, if compacting saves enough
  2. ⚡ switch now: a cheaper model is already cheaper, even with the re-write
  3. 💡 switch now: staying has cost as much as switching would (rent-or-buy)
  4. 💡 try a lower /effort: it keeps the cache, and this session's replies show the saving
  5. 💡 stay for now: switching is free after the next break that outlasts the cache
Then one row per option: stay, every other model (↑ more capable, ↓ cheaper),
/compact and /effort. Amounts for now are exact; per-message amounts (≈)
assume messages and replies stay as they have been (engine.py).

Cost isn't the only goal: a stronger model can finish in fewer messages, and
whether a cheaper one is good enough is the user's call. The rows show the
dollars; the user decides.
"""
from dataclasses import dataclass, field

from .engine import cache_clock, cheaper, compact, context, effort_rewrite, effort_saving, model_move, others, resend
from .models import model_key, pretty_model
from .prices import ONE_HOUR, request_cost
from .sessions import Session, Store

EFFORTS = ["max", "xhigh", "high", "medium", "low"]
COMPACT_FROM = 100_000  # context size worth compacting
COMPACT_WARN = 600  # seconds of warm cache left when the /compact action appears
MIN_SAVING = 0.005  # per message; below this, a switch or effort change isn't worth an action


@dataclass
class Option:
    label: str  # "stay", "/compact", "/effort", "/effort low", or a model's name
    text: str
    arrow: str = ""  # "↑" a more capable model, "↓" a cheaper one
    model: str | None = None  # the model a row is about, for its colour


@dataclass
class Advice:
    action: str | None  # what to do, if anything is worth doing
    urgent: bool = False  # act before the cache expires
    options: list[Option] = field(default_factory=list)


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


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def stint_start(session: Session) -> float | None:
    """When the conversation was last (re-)written on its current model: the
    cache it reads from now dates from then."""
    start = None
    for request in reversed(session.main_requests()):
        if model_key(request.model) != model_key(session.model):
            break
        start = request.start
        if request.reason:
            break
    return start


class Memory:
    """When each switch first became worth considering, per session and
    target: the rent-or-buy rule counts what staying costs from then, not from
    the start of the session (research/CACHE-DECISIONS.md §5)."""

    def __init__(self) -> None:
        self.since: dict[tuple[str, str], float] = {}

    def start(self, session: Session, target: str, now: float) -> float:
        key, stint = (session.id, target), stint_start(session) or now
        if key not in self.since or self.since[key] < stint:
            self.since[key] = max(now, stint)
        return self.since[key]


def held_extra(store: Store, session: Session, target: str, since: float) -> float:
    """R: what the main conversation's requests since `since` cost beyond the
    same tokens on `target` (converted to its tokenizer)."""
    price_l = store.prices.get(target)
    if not price_l:
        return 0.0
    ratio = store.facts.convert(1.0, session.model, target)
    extra = 0.0
    for request in reversed(session.main_requests()):
        if request.start < since:
            break
        if request.cost is None:
            continue
        if model_key(request.model) != model_key(session.model):
            continue
        usage = {
            "input_tokens": request.usage["fresh"] * ratio,
            "cache_read_input_tokens": request.usage["read"] * ratio,
            "cache_creation": {
                "ephemeral_5m_input_tokens": request.usage["write_5m"] * ratio,
                "ephemeral_1h_input_tokens": request.usage["write_1h"] * ratio,
            },
            "output_tokens": request.usage["output"] * ratio,
        }
        extra += request.cost - request_cost(usage, price_l)
    return extra


def lower_effort(store: Store, session: Session) -> tuple[str, float] | None:
    """The first lower effort that saves at least MIN_SAVING a message, and ≈ how much."""
    if session.effort not in EFFORTS:
        return None
    for lower in EFFORTS[EFFORTS.index(session.effort) + 1:]:
        saving = effort_saving(store, session, lower)
        if saving is not None and saving >= MIN_SAVING:
            return lower, saving
    return None


def advise(store: Store, session: Session, now: float, memory: Memory | None = None) -> Advice | None:
    """The action and options for a live session (cache warm, not closed); None otherwise."""
    warm, left, ttl = cache_clock(session, now)
    ctx = context(store, session)
    if session.ended or ctx is None or not warm:
        return None
    advice = Advice(None)
    if model_key(session.model) not in store.prices:
        return advice  # nothing to price
    memory = memory if memory is not None else Memory()
    here = pretty_model(session.model)
    moves = {t: m for t in others(store, session.model) if (m := model_move(store, session, t, now))}
    worth = [moves[t] for t in cheaper(store, session.model) if t in moves and moves[t].saving >= MIN_SAVING]
    # Rent-or-buy: what staying has cost, per cheaper model, since switching to it was first worth it.
    extra = {m.target: held_extra(store, session, m.target, memory.start(session, m.target, now)) for m in worth}
    costs = compact(store, session, now)
    effort = lower_effort(store, session)

    already = [m for m in worth if m.penalty <= 0]
    held = [m for m in worth if 0 < m.penalty <= extra[m.target]]
    if costs and costs.saving >= MIN_SAVING and ctx.tokens >= COMPACT_FROM and left <= min(COMPACT_WARN, ttl // 2):
        advice.action, advice.urgent = (f"Taking a break? /compact first: ≈{money(costs.now)} now, "
                                        f"≈{money(costs.after_break)} once the cache expires in {clock(left)}."), True
    elif already:
        best = max(already, key=lambda m: m.saving)
        advice.action, advice.urgent = f"Switch to {pretty_model(best.target)} now: it's already cheaper.", True
    elif held:
        best = max(held, key=lambda m: m.saving)
        there = pretty_model(best.target)
        advice.action = (f"Switch to {there} now: it would have saved {money(extra[best.target])} by now; "
                         f"switching costs {money(best.penalty)}.")
    elif effort:
        advice.action = f"Try /effort {effort[0]}: ≈{money(effort[1])} less a message, at no cost now."
    elif worth:
        brk = "1-hour" if ttl >= ONE_HOUR else f"{ttl // 60}-minute"
        advice.action = f"Stay on {here} for now; switching is free after your next {brk} break."
    advice.options = options(store, session, now, ctx, moves, costs, effort)
    return advice


def options(store: Store, session: Session, now: float, ctx, moves: dict, costs, effort) -> list[Option]:
    approx = "" if ctx.exact else "≈"
    rows = []
    stay_now, stay_cold = resend(store, session, session.model, True), resend(store, session, session.model, False)
    if stay_now is not None and stay_cold is not None:
        rows.append(Option("stay", f"{resends(ctx)}: {approx}{money(stay_now)} now, "
                                   f"{approx}{money(stay_cold)} after a break"))
    down = cheaper(store, session.model)
    for target, move in moves.items():
        mark = "" if move.exact else "≈"
        text = f"{mark}{money(move.penalty)} {'more' if move.penalty >= 0 else 'less'} now"
        text += f", then ≈{money(move.saving)} {'less' if move.saving >= 0 else 'more'} a message"
        if move.penalty > 0 and move.saving > 0 and move.payback is not None:
            text += f" · evens out after ≈{plural(max(1, round(move.payback)), 'message')}"
        rows.append(Option(pretty_model(target), text, "↓" if target in down else "↑", target))
    if costs and costs.saving >= MIN_SAVING:
        rows.append(Option("/compact", f"≈{money(costs.now)} now, ≈{money(costs.after_break)} after a break; "
                                       f"then ≈{money(costs.saving)} less a message"))
    if store.facts.effort_keeps_cache(session.model):
        if effort:
            rows.append(Option(f"/effort {effort[0]}", f"costs nothing now, then ≈{money(effort[1])} less a message"))
        else:
            rows.append(Option("/effort", "costs nothing now"))
    elif (rewrite := effort_rewrite(store, session, now)) is not None:
        rows.append(Option("/effort", f"{resends(ctx)}: {approx}{money(rewrite)} more now"))
    return rows
