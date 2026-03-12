"""Chat service bridge for incremental migration."""

from server.schemas.chat import ChatRequest
from router import chat as legacy_chat


async def process_chat(req: ChatRequest):
    """Temporary bridge to legacy chat implementation."""
    return await legacy_chat(req)

