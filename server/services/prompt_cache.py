"""Prompt cache service for migration period."""


def invalidate_prompt_cache():
    """Invalidate prompt cache from legacy module when available."""
    try:
        from router import invalidate_prompt_cache as legacy_invalidate_prompt_cache

        legacy_invalidate_prompt_cache()
    except Exception:
        # During migration we tolerate missing legacy cache implementation.
        pass

