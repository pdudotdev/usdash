"""What usdash knows about Claude models besides their prices: where an effort
change keeps the cache, and how their tokenizers compare. From models.yaml,
which ships with usdash. Both only label why a cache missed (sessions.py).
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import model_key, model_version, on_cloud_provider

MODELS = Path(__file__).with_name("models.yaml")


@dataclass(frozen=True)
class Facts:
    effort_keeps: frozenset[str]  # where Claude Code keeps the cache across an effort change
    old_tokenizer_before: tuple[int, int]  # models older than this version use the old tokenizer
    ratio: float  # the old tokenizer's tokens for one of the current tokenizer's
    verified: str = ""

    def effort_keeps_cache(self, model: str | None) -> bool:
        """Whether an effort change on this model keeps the cache (never on a cloud provider)."""
        return model_key(model) in self.effort_keeps and not on_cloud_provider(model)

    def old_tokenizer(self, family: str | None) -> bool:
        version = model_version(family)
        return version is not None and version < self.old_tokenizer_before

    def convert(self, tokens: float, source: str | None, target: str | None) -> float:
        """A token count on one model's tokenizer -> roughly the same text on another's."""
        old_source, old_target = self.old_tokenizer(model_key(source)), self.old_tokenizer(model_key(target))
        if old_source == old_target:
            return tokens
        return tokens * self.ratio if old_target else tokens / self.ratio


def load_facts(path: str | Path = MODELS) -> Facts:
    data = yaml.safe_load(Path(path).read_text())
    major, minor = str(data["old_tokenizer_before"]).split(".")
    return Facts(frozenset(data["effort_keeps_cache"]), (int(major), int(minor)),
                 float(data["old_tokenizer_ratio"]), str(data.get("verified", "")))
