"""Chat service bridge for incremental migration."""

from server.schemas.chat import ChatRequest


async def process_chat(req: ChatRequest):
    """Temporary bridge to legacy chat implementation."""
    # Lazy import to avoid eager coupling during app startup.
    from router import chat as legacy_chat

    return await legacy_chat(req)
