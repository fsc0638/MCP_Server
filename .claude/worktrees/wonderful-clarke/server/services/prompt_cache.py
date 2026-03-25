"""Prompt cache service for migration period."""

import sys


def invalidate_prompt_cache():
    """Invalidate prompt cache from legacy module when available."""
    try:
        router_mod = sys.modules.get("router")
        if router_mod is not None:
            legacy_invalidate_prompt_cache = getattr(router_mod, "invalidate_prompt_cache", None)
            if callable(legacy_invalidate_prompt_cache):
                legacy_invalidate_prompt_cache()
    except Exception:
        # During migration we tolerate missing legacy cache implementation.
        pass
