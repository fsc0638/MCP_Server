"""Adapter factory for centralized model/provider resolution."""

from server.adapters.openai_adapter import OpenAIAdapter
from server.adapters.gemini_adapter import GeminiAdapter
from server.adapters.claude_adapter import ClaudeAdapter


def create_adapter(provider: str, uma, model: str | None = None, **kwargs):
    p = (provider or "").strip().lower()
    if p == "openai":
        return OpenAIAdapter(uma, model=model, **kwargs)
    if p == "gemini":
        return GeminiAdapter(uma, model=model)
    if p == "claude":
        return ClaudeAdapter(uma, model=model)
    raise ValueError(f"Unknown provider: {provider}")

