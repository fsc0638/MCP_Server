"""Phase 1b: Model/provider budget profiles.

Provides max_input_tokens and reserve_output_tokens defaults.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetProfile:
    max_input_tokens: int
    reserve_output_tokens: int


def get_budget_for_model(model: str | None) -> BudgetProfile:
    m = (model or "").lower()

    # Conservative defaults (safe across providers)
    if not m:
        return BudgetProfile(max_input_tokens=8000, reserve_output_tokens=1200)

    # OpenAI common
    if "gpt-4o-mini" in m:
        return BudgetProfile(max_input_tokens=8000, reserve_output_tokens=1200)
    if "gpt-4o" in m:
        return BudgetProfile(max_input_tokens=16000, reserve_output_tokens=2000)

    # Fallback
    return BudgetProfile(max_input_tokens=8000, reserve_output_tokens=1200)
