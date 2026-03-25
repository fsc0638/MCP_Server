"""Adapter factory for centralized model/provider resolution.

Includes cross-provider fallback: if the primary provider fails
(e.g. rate limit exhausted), optionally fall back to an alternative.
"""

import logging
from server.adapters.openai_adapter import OpenAIAdapter
from server.adapters.gemini_adapter import GeminiAdapter
from server.adapters.claude_adapter import ClaudeAdapter

logger = logging.getLogger("MCP_Server.Adapters.Factory")


def create_adapter(provider: str, uma, model: str | None = None, **kwargs):
    """Create an adapter for the given provider."""
    p = (provider or "").strip().lower()
    if p == "openai":
        return OpenAIAdapter(uma, model=model, **kwargs)
    if p == "gemini":
        return GeminiAdapter(uma, model=model)
    if p == "claude":
        return ClaudeAdapter(uma, model=model)
    raise ValueError(f"Unknown provider: {provider}")


def create_adapter_with_fallback(
    provider: str,
    uma,
    model: str | None = None,
    fallback_provider: str | None = None,
    fallback_model: str | None = None,
    **kwargs,
):
    """
    Create a primary adapter. If it's unavailable (missing API key, etc.),
    try the fallback provider/model instead.
    """
    try:
        primary = create_adapter(provider, uma, model=model, **kwargs)
        if primary.is_available:
            return primary
        logger.warning(f"[Factory] Primary adapter {provider}/{model} unavailable, trying fallback")
    except Exception as e:
        logger.warning(f"[Factory] Primary adapter {provider}/{model} failed: {e}")

    if fallback_provider:
        try:
            fallback = create_adapter(fallback_provider, uma, model=fallback_model, **kwargs)
            if fallback.is_available:
                logger.info(f"[Factory] Fallback adapter {fallback_provider}/{fallback_model} active")
                return fallback
        except Exception as e:
            logger.error(f"[Factory] Fallback adapter also failed: {e}")

    # Return primary anyway (caller will see is_available=False)
    return create_adapter(provider, uma, model=model, **kwargs)
