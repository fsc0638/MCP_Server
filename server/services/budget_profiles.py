"""Phase 1b: Model/provider budget profiles.

Provides max_input_tokens and reserve_output_tokens defaults.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetProfile:
    max_input_tokens: int
    reserve_output_tokens: int


def get_budget_for_model(model: str | None, platform: str | None = None) -> BudgetProfile:
    m = (model or "").lower()

    # Conservative defaults (safe across providers)
    if not m:
        return BudgetProfile(max_input_tokens=8000, reserve_output_tokens=1200)

    plat = (platform or "").lower().strip() or "web"

    # Base defaults by model
    if "gpt-4o" in m and "mini" not in m:
        base = BudgetProfile(max_input_tokens=16000, reserve_output_tokens=2000)
    elif "gpt-4o-mini" in m:
        base = BudgetProfile(max_input_tokens=8000, reserve_output_tokens=1200)
    else:
        base = BudgetProfile(max_input_tokens=8000, reserve_output_tokens=1200)

    # Platform adjustment: LINE is tighter
    if plat == "line":
        # Keep response headroom but reduce input budget
        return BudgetProfile(max_input_tokens=min(base.max_input_tokens, 6000), reserve_output_tokens=base.reserve_output_tokens)

    return base
