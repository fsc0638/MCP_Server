"""Chat routes (migration bridge)."""

from fastapi import APIRouter

from router import (
    ChatRequest,
    ExecuteRequest,
    chat as legacy_chat,
    flush_memory as legacy_flush_memory,
    clear_session as legacy_clear_session,
    get_session_history as legacy_get_session_history,
    execute_tool as legacy_execute_tool,
)

router = APIRouter(tags=["Chat"])


@router.post("/chat")
async def chat(req: ChatRequest):
    return await legacy_chat(req)


@router.post("/chat/flush/{session_id}")
def flush_memory(session_id: str):
    return legacy_flush_memory(session_id)


@router.delete("/chat/session/{session_id}")
def clear_session(session_id: str):
    return legacy_clear_session(session_id)


@router.get("/chat/session/{session_id}")
def get_session_history(session_id: str):
    return legacy_get_session_history(session_id)


@router.post("/execute")
def execute_tool(request: ExecuteRequest):
    return legacy_execute_tool(request)

