"""Chat service bridge for incremental migration."""

import os

from server.schemas.chat import ChatRequest
from server.services.chat_core import process_chat_native


async def process_chat(req: ChatRequest):
    """Chat entry with switchable native/legacy implementation."""
    force_native = os.getenv("MCP_CHAT_NATIVE", "").strip().lower() in {"1", "true", "yes"}
    auto_native_candidate = True

    if force_native or auto_native_candidate:
        try:
            return await process_chat_native(req)
        except NotImplementedError:
            # Fallback to legacy for unsupported native scenarios.
            pass

    # Lazy import to avoid eager coupling during app startup.
    from router import chat as legacy_chat

    return await legacy_chat(req)
