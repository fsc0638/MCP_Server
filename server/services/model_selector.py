"""
Unified Model Selector — single source of truth for all model selection.

Used by:
  - LINE Bot (openai_adapter.py → before tool call execution)
  - Web UI (chat_core.py → before LLM call)
  - Workflow Engine (workflow.py → per-block execution)

Priority (high → low):
  1. Block/call-level override (user set on specific block or API param)
  2. Skill's recommended_models.{provider}
  3. User's default model (from settings/session)
  4. .env default model

+ TPM safety: auto-downgrade if estimated tokens exceed budget
"""

import logging
import os
from typing import Optional, Dict

logger = logging.getLogger("MCP_Server.ModelSelector")

# ── Model Tiers per Provider ────────────────────────────────────────────────
# Ordered from strongest (most expensive) to weakest (cheapest)
DOWNGRADE_CHAINS = {
    "openai": ["gpt-4.1", "gpt-4o", "gpt-4.1-mini", "gpt-4.1-nano"],
    "gemini": ["gemini-2.0-flash"],
    "claude": ["claude-sonnet-4-6", "claude-haiku-4-5"],
}

# Safe per-request token budgets
MODEL_BUDGETS = {
    "gpt-4.1": 20000,
    "gpt-4o": 20000,
    "gpt-4.1-mini": 25000,
    "gpt-4.1-nano": 25000,
    "o4-mini": 25000,
    "o3": 20000,
    "gemini-2.0-flash": 30000,
    "claude-sonnet-4-6": 20000,
    "claude-haiku-4-5": 25000,
}


def detect_provider(model: str) -> str:
    """Detect provider from model name."""
    m = (model or "").lower()
    if "gpt" in m or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return "openai"
    elif "gemini" in m:
        return "gemini"
    elif "claude" in m:
        return "claude"
    return "openai"


def _model_strength(model: str) -> int:
    """Get model strength index (lower = stronger). Used for comparison."""
    provider = detect_provider(model)
    chain = DOWNGRADE_CHAINS.get(provider, [])
    try:
        return chain.index(model)
    except ValueError:
        return 0  # Unknown model treated as strongest


def downgrade_model(model: str) -> Optional[str]:
    """Get next cheaper model in the same provider chain."""
    provider = detect_provider(model)
    chain = DOWNGRADE_CHAINS.get(provider, [])
    try:
        idx = chain.index(model)
        if idx + 1 < len(chain):
            return chain[idx + 1]
    except ValueError:
        pass
    return None


def select_model_for_skill(
    skill_name: str,
    skill_metadata: dict = None,
    user_default_model: str = None,
    override_model: str = None,
    estimated_input_tokens: int = 0,
) -> str:
    """Unified model selection for any context (LINE Bot / Web UI / Workflow).

    Priority:
      1. override_model (block-level or API param)
      2. skill's recommended_models.{provider} (if stronger than user default)
      3. user_default_model
      4. .env default

    Then apply TPM safety downgrade.

    Args:
        skill_name: e.g., "mcp-web-search"
        skill_metadata: skill's metadata dict (has recommended_models)
        user_default_model: user's selected model (from settings/session)
        override_model: explicit override (highest priority)
        estimated_input_tokens: estimated input size for TPM check

    Returns:
        Selected model name string
    """
    # Step 0: Determine base model
    env_default = os.getenv("OPENAI_MODEL", "gpt-4o")
    base_model = user_default_model or env_default

    # Step 1: Override takes highest priority
    if override_model:
        model = override_model
    else:
        # Step 2: Check skill's recommended_models
        recommended = {}
        if skill_metadata:
            recommended = skill_metadata.get("recommended_models", {})

        if recommended:
            provider = detect_provider(base_model)
            skill_recommended = recommended.get(provider)

            if skill_recommended:
                base_strength = _model_strength(base_model)
                skill_strength = _model_strength(skill_recommended)

                if skill_strength > base_strength:
                    # Skill recommends WEAKER (cheaper) model → use it (save tokens)
                    model = skill_recommended
                    logger.debug(f"[ModelSelector] {skill_name}: downgrade to {skill_recommended} (skill says cheaper is enough)")
                elif skill_strength < base_strength:
                    # Skill recommends STRONGER (more expensive) model → only upgrade for semantic skills
                    _is_semantic = not skill_metadata.get("_has_scripts", True)
                    if _is_semantic or skill_metadata.get("_force_upgrade"):
                        model = skill_recommended
                        logger.debug(f"[ModelSelector] {skill_name}: upgrade to {skill_recommended} (semantic skill needs it)")
                    else:
                        # Executable skill doesn't need stronger model — keep router's choice
                        model = base_model
                        logger.debug(f"[ModelSelector] {skill_name}: keep {base_model} (executable skill, no upgrade needed)")
                else:
                    model = skill_recommended
            else:
                model = base_model
        else:
            model = base_model

    # Step 3: TPM safety downgrade
    budget = MODEL_BUDGETS.get(model, 20000)
    estimated = estimated_input_tokens + 500  # overhead
    while estimated > budget * 0.8:
        downgraded = downgrade_model(model)
        if not downgraded:
            break
        logger.info(f"[ModelSelector] TPM downgrade: {model} → {downgraded} "
                     f"(est={estimated} > 80% of {budget})")
        model = downgraded
        budget = MODEL_BUDGETS.get(model, 20000)

    logger.info(f"[ModelSelector] {skill_name} → {model}")
    return model


def get_recommended_models_for_display(skill_metadata: dict) -> Dict[str, str]:
    """Get recommended models dict for UI display.
    Returns: {"openai": "gpt-4.1-mini", "gemini": "gemini-2.0-flash", "claude": "..."}
    """
    return skill_metadata.get("recommended_models", {}) if skill_metadata else {}
