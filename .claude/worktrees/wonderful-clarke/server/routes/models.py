"""System/model routes extracted from legacy router."""

import os
from fastapi import APIRouter

router = APIRouter(tags=["System"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/api/models")
def get_available_models():
    """Return a list of available models based on environment configuration."""
    models = []

    if os.getenv("OPENAI_API_KEY"):
        m = os.getenv("OPENAI_MODEL", "gpt-4o")
        models.append({"provider": "openai", "model": m, "display_name": f"OpenAI ({m})"})

    if os.getenv("GEMINI_API_KEY"):
        m = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        models.append({"provider": "gemini", "model": m, "display_name": f"Google Gemini ({m})"})

    if os.getenv("ANTHROPIC_API_KEY"):
        m = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
        models.append({"provider": "claude", "model": m, "display_name": f"Anthropic Claude ({m})"})

    if not models:
        models.append(
            {
                "provider": "openai",
                "model": "gpt-4o",
                "display_name": "OpenAI GPT-4o (No Key Set)",
            }
        )

    return {"status": "success", "models": models}

