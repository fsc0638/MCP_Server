"""Chat routes."""

from fastapi import APIRouter, HTTPException

from main import get_uma
from server.dependencies.session import get_session_manager
from server.schemas.chat import ChatRequest, ExecuteRequest
from server.services.chat_service import process_chat

router = APIRouter(tags=["Chat"])


@router.post("/chat")
async def chat(req: ChatRequest):
    return await process_chat(req)


@router.post("/chat/flush/{session_id}")
def flush_memory(session_id: str):
    from server.services.runtime import make_llm_callable

    session_mgr = get_session_manager()
    session_mgr.flush_with_llm_summary(session_id, make_llm_callable())
    return {"status": "success", "message": f"Session '{session_id}' flushed to MEMORY.md"}


@router.delete("/chat/session/{session_id}")
def clear_session(session_id: str):
    session_mgr = get_session_manager()
    session_mgr.clear_conversation(session_id)
    return {"status": "success", "message": f"Session '{session_id}' cleared"}


@router.get("/chat/session/{session_id}")
def get_session_history(session_id: str):
    session_mgr = get_session_manager()
    history = session_mgr.get_or_create_conversation(session_id)
    chat_history = [m for m in history if m.get("role") != "system"]
    return {"status": "success", "history": chat_history}


@router.post("/execute")
def execute_tool(request: ExecuteRequest):
    uma = get_uma()
    skill = uma.registry.get_skill(request.skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{request.skill_name}' not found")
    if not skill["metadata"].get("_env_ready", False):
        return {"status": "error", "message": f"Skill '{request.skill_name}' environment is not ready"}
    try:
        result = uma.execute_tool_call(request.skill_name, request.arguments)
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}
