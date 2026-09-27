"""Advice, one line per session when there's something worth saying, in dollars.

Rules, most reliable first:
  1. switching model: now (warm) vs after the cache expires, by the rent-or-buy rule
  2. /compact timing: before a break, while the cache is still warm
  3. lowering effort, where that keeps the cache (Opus 5.5, Fable 5.1)

Costs are estimates (output per message is learned from history), and cost
isn't the only goal: a stronger model or more effort can finish in fewer
messages. The advice shows the dollars; the user decides.
"""
from dataclasses import dataclass

from .engine import cache_state, compact_cost, effort_move, model_move
from .models import convert_tokens, model_key, pretty_model
from .prices import request_cost
from .sessions import Session, Store

# Cheaper-model candidates, most capable first.
LADDER = ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"]
EFFORTS = ["max", "xhigh", "high", "medium", "low"]
COMPACT_FROM = 100_000  # context size worth compacting
COMPACT_WARN = 600  # seconds of warm cache left when compaction advice appears
MIN_SAVING = 0.005  # per message; below this, advice is noise


@dataclass
class Advice:
    session: Session
    kind: str  # "switch", "compact", "effort"
    text: str
    urgent: bool = False  # act before the cache expires


def money(value: float) -> str:
    """Dollars to the cent; an amount under half a cent shows as <$0.01, not $0.00."""
    value = abs(value)
    return "<$0.01" if 0 < value < 0.005 else f"${value:,.2f}"


def clock(seconds: int) -> str:
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"


def targets(store: Store, model: str | None) -> list[str]:
    """Cheaper models to consider moving to: those whose output costs less."""
    price = store.prices.get(model_key(model))
    if not price:
        return []
    return [t for t in LADDER if t != model_key(model) and store.prices[t]["output"] < price["output"]]


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
    """When each switch was first shown, per session and target: the rent-or-buy
    rule counts what staying costs from the moment moving became worth
    considering, not from the start of the session (research/CACHE-DECISIONS.md §5)."""

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
    ratio = convert_tokens(1.0, model_key(session.model), target)
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


def switch_advice(store: Store, session: Session, now: float, memory: Memory) -> Advice | None:
    """Rule 1. Cold: moving costs nothing extra. Warm: move when it's already
    cheaper, or once staying has cost as much as moving would (rent-or-buy);
    until then, say what moving costs now and that it's free after a break."""
    here = pretty_model(session.model)
    moves = [m for m in (model_move(store, session, t, now) for t in targets(store, session.model)) if m]
    moves = [m for m in moves if m.saving >= MIN_SAVING]
    if not moves:
        return None
    nearest = moves[0]
    if not nearest.warm:
        options = ", ".join(f"≈{money(m.move)} on {pretty_model(m.target)}" for m in moves)
        return Advice(session, "switch",
                      f"Cache expired, so switching model costs nothing extra now: next message {options}, "
                      f"vs {money(nearest.stay)} on {here}.")
    cheaper = [m for m in moves if m.penalty <= 0]
    if cheaper:
        best = max(cheaper, key=lambda m: m.saving)
        return Advice(session, "switch",
                      f"Switch to {pretty_model(best.target)} now: already cheaper "
                      f"(saves {money(-best.penalty)} now, {money(best.saving)} per message).", urgent=True)
    since = memory.start(session, nearest.target, now)
    extra = held_extra(store, session, nearest.target, since)
    there = pretty_model(nearest.target)
    if extra > 0 and extra >= nearest.penalty:
        return Advice(session, "switch",
                      f"Switch to {there} now: since this tip appeared, staying on {here} has cost {money(extra)} "
                      f"more than {there} would have, which covers the {money(nearest.penalty)} switch.")
    back = ""
    if nearest.payback is not None:
        n = max(1, round(nearest.payback))
        back = f"; its cheaper messages make that back in ~{n} message{'' if n == 1 else 's'}"
    return Advice(session, "switch",
                  f"Switching to {there} now costs {money(nearest.penalty)} extra{back}. "
                  f"Switching is free once {here}'s cache expires, in {clock(nearest.left)}.")


def compact_advice(store: Store, session: Session, now: float, memory: Memory) -> Advice | None:
    """Rule 2. /compact reads the cache while it's warm, and re-writes
    everything after a break: compact before stepping away."""
    state = cache_state(store, session, now)
    costs = compact_cost(store, session, now)
    if state is None or costs is None or state.size < COMPACT_FROM or not state.warm:
        return None
    if state.left > min(COMPACT_WARN, state.ttl // 2):
        return None
    warm, cold = costs
    return Advice(session, "compact",
                  f"Context {state.size / 1000:.0f}k, cache expires in {clock(state.left)}: /compact now ≈{money(warm)}; "
                  f"after a break ≈{money(cold)}.", urgent=True)


def effort_advice(store: Store, session: Session, now: float, memory: Memory) -> Advice | None:
    """Rule 3. On Opus 5.5 and Fable 5.1 an effort change keeps the cache, so
    a lower effort saves from the very next message."""
    if session.effort not in EFFORTS:
        return None
    for lower in EFFORTS[EFFORTS.index(session.effort) + 1:]:
        move = effort_move(store, session, lower, now)
        if move is None:
            continue
        if move.saving < MIN_SAVING:
            continue  # a still lower effort may save enough
        return Advice(session, "effort",
                      f"Lower /effort to {lower}: ≈{money(move.saving)} less per message, "
                      f"no cache cost on {pretty_model(session.model)}.")
    return None


RULES = (switch_advice, compact_advice, effort_advice)


def advise(store: Store, session: Session, now: float, memory: Memory | None = None) -> list[Advice]:
    if session.ended or session.last_request is None:
        return []
    memory = memory if memory is not None else Memory()
    return [advice for rule in RULES if (advice := rule(store, session, now, memory))]
