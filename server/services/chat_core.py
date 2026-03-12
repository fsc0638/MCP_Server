"""Native chat service placeholder.

This module is the target for fully replacing legacy chat flow.
"""

from server.schemas.chat import ChatRequest


async def process_chat_native(req: ChatRequest):
    raise NotImplementedError("Native chat pipeline is not implemented yet.")

