from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(slots=True, frozen=True)
class PricingEntry:
    provider: str
    model: str
    input_per_million: float | None
    output_per_million: float | None
    pricing_strategy: str = "fixed"


class PricingCatalog:
    def __init__(self, entries: dict[tuple[str, str], PricingEntry]) -> None:
        self._entries = entries

    @classmethod
    def from_repo_file(cls, relative_path: str = "infra/pricing.yaml") -> PricingCatalog:
        repo_root = Path(__file__).resolve().parents[3]
        raw = yaml.safe_load((repo_root / relative_path).read_text(encoding="utf-8"))
        entries: dict[tuple[str, str], PricingEntry] = {}
        providers = raw.get("providers", {})

        for provider_name, models in providers.items():
            for model_name, values in models.items():
                entry = PricingEntry(
                    provider=provider_name,
                    model=model_name,
                    input_per_million=values.get("input_per_million"),
                    output_per_million=values.get("output_per_million"),
                    pricing_strategy=values.get("pricing_strategy", "fixed"),
                )
                entries[(provider_name, model_name)] = entry

        return cls(entries)

    def cost_for(
        self,
        provider: str,
        model: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> float | None:
        entry = self._entries.get((provider, model))
        if entry is None:
            return None
        if entry.input_per_million is None or entry.output_per_million is None:
            return None
        if prompt_tokens is None or completion_tokens is None:
            return None

        input_cost = (prompt_tokens / 1_000_000) * entry.input_per_million
        output_cost = (completion_tokens / 1_000_000) * entry.output_per_million
        return round(input_cost + output_cost, 8)
