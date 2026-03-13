"""Chat service bridge for incremental migration."""

from server.schemas.chat import ChatRequest
from server.services.chat_core import process_chat_native


async def process_chat(req: ChatRequest):
    """Chat entrypoint uses the native server path only."""
    return await process_chat_native(req)
