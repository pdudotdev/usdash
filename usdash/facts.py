"""What usdash knows about Claude models besides their prices: which ones to
compare, where an effort change keeps the cache, and how their tokenizers
compare. Each fact comes from, first to last: what this machine's
transcripts show (Store.facts), Anthropic's docs read at start (docs.py),
and models.yaml, which ships with usdash.
"""
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median

import yaml

from .models import model_key, model_version, name_key, on_cloud_provider

MODELS = Path(__file__).with_name("models.yaml")
SEEN_ENOUGH = 2  # observations, all agreeing, before the transcripts overrule a default


@dataclass(frozen=True)
class Facts:
    lineup: list[str]  # the current models, most capable first
    effort_keeps: frozenset[str]  # where Claude Code keeps the cache across an effort change (models.yaml)
    old_tokenizer_before: tuple[int, int]  # models older than this version use the old tokenizer
    old_ratio: float  # the old tokenizer's tokens for one of the current tokenizer's (models.yaml)
    verified: str = "?"
    effort_possible: frozenset[str] | None = None  # the docs: where the API can keep it (None: unknown)
    effort_seen: dict[str, tuple[int, int]] = field(default_factory=dict)  # transcripts: model -> (kept, re-wrote)
    ratio_seen: tuple[float, ...] = ()  # transcripts: old tokens / current tokens, at model switches

    def effort_keeps_cache(self, model: str | None) -> bool:
        """Whether an effort change on this model keeps the cache: not where the
        API can't, nor on a cloud provider; else as this machine's transcripts
        show, once they agree; else models.yaml."""
        family = model_key(model)
        if on_cloud_provider(model) or (self.effort_possible is not None and family not in self.effort_possible):
            return False
        kept, rewrote = self.effort_seen.get(family, (0, 0))
        if kept >= SEEN_ENOUGH and not rewrote:
            return True
        if rewrote >= SEEN_ENOUGH and not kept:
            return False
        return family in self.effort_keeps

    def old_tokenizer(self, family: str | None) -> bool:
        version = model_version(family)
        return version is not None and version < self.old_tokenizer_before

    @property
    def ratio(self) -> float:
        """Old tokens per current token: the median of this machine's switches, else models.yaml's."""
        return median(self.ratio_seen) if len(self.ratio_seen) >= SEEN_ENOUGH else self.old_ratio

    def convert(self, tokens: float, source: str | None, target: str | None) -> float:
        """A token count on one model's tokenizer -> roughly the same text on another's."""
        old_source, old_target = self.old_tokenizer(model_key(source)), self.old_tokenizer(model_key(target))
        if old_source == old_target:
            return tokens
        return tokens * self.ratio if old_target else tokens / self.ratio

    def with_docs(self, lineup: list[str] | None, effort_possible: set[str] | None) -> "Facts":
        return replace(self, lineup=lineup or self.lineup,
                       effort_possible=frozenset(effort_possible) if effort_possible is not None else None)

    def with_seen(self, effort_seen: dict[str, tuple[int, int]], ratio_seen: list[float]) -> "Facts":
        return replace(self, effort_seen=dict(effort_seen), ratio_seen=tuple(ratio_seen))


def load_facts(path: str | Path = MODELS) -> Facts:
    data = yaml.safe_load(Path(path).read_text())
    major, minor = str(data["old_tokenizer_before"]).split(".")
    return Facts(list(data["lineup"]), frozenset(data["effort_keeps_cache"]), (int(major), int(minor)),
                 float(data["old_tokenizer_ratio"]), str(data.get("verified", "?")))


# --- Anthropic's docs ------------------------------------------------------------


def parse_lineup(text: str) -> list[str]:
    """The models overview (Markdown) -> the current models, most capable
    first: the columns of its "Compare models" table, by their API alias.
    Raises ValueError unless each column's name and alias agree."""
    at = text.find("\n## Compare models\n")
    if at < 0:
        raise ValueError("no 'Compare models' section")
    rows = {}
    for line in text[at:].splitlines()[1:]:
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            rows.setdefault(cells[0], cells[1:])
        elif rows:
            break
    names, aliases = rows.get("Feature"), rows.get("Claude API alias")
    if not names or not aliases or len(names) != len(aliases) or len(names) < 2:
        raise ValueError("the comparison table changed")
    lineup = []
    for name, alias in zip(names, aliases):
        match = re.fullmatch(r"`([a-z0-9-]+)`", alias)
        if not match or model_key(match.group(1)) != name_key(name):
            raise ValueError(f"{name!r} and its alias {alias!r} don't agree")
        lineup.append(model_key(match.group(1)))
    return lineup


def parse_effort_models(text: str) -> set[str]:
    """The effort page (Markdown) -> the models where the API can change
    effort mid-conversation and keep the cache. Raises ValueError unless the
    sentence that names them is there, naming only models."""
    name = r"Claude [A-Z][a-z]+ \d+(?:\.\d+)?"
    match = re.search(rf"\bOn ((?:{name}(?:,? and |, ))*{name}), use a per-message effort change, "
                      r"which keeps the prompt cache\.", text)
    if not match:
        raise ValueError("the sentence naming the models changed")
    keys = {name_key(found) for found in re.findall(name, match.group(1))}
    if None in keys:
        raise ValueError(f"not all models: {match.group(1)!r}")
    return keys
