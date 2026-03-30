"""Chat routes."""

import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from server.dependencies.uma import get_uma_instance as get_uma
from server.dependencies.session import get_session_manager
from server.schemas.chat import ChatRequest, ExecuteRequest
from server.services.chat_service import process_chat

logger = logging.getLogger("MCP_Server.Chat")
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


# ─── Phase 3: Approve / Reject pending high-risk tool calls ───────────────────

@router.post("/chat/approve/{session_id}")
async def approve_tool_call(session_id: str):
    """
    Phase 3-B: Resume endpoint.
    Called by the frontend after the user approves a high-risk tool call.
    Executes the stored pending call (bypassing the risk gate),
    then streams the LLM's second-analysis response via SSE.
    """
    session_mgr = get_session_manager()
    pending = session_mgr.get_pending_approval(session_id)
    if not pending:
        raise HTTPException(status_code=404, detail="No pending approval found for this session")

    tool_name = pending["tool_name"]
    tool_args = pending["args"]
    uma = get_uma()

    async def resume_generator() -> AsyncGenerator[dict, None]:
        try:
            # 1. Execute the tool (bypass risk gate — user already approved)
            executor = uma.executor
            script_path = executor.skills_home / tool_name / "scripts" / "main.py"
            if script_path.exists():
                result = executor.run_script(tool_name, "main.py", tool_args)
            else:
                result = {"status": "error", "message": f"Skill '{tool_name}' has no executable script."}

            result_summary = json.dumps(result, ensure_ascii=False)

            # 2. Clear pending approval
            session_mgr.clear_pending_approval(session_id)

            # 3. Let the LLM summarize the result via a new conversation turn
            from server.adapters.factory import create_adapter

            provider = pending.get("provider", "openai")
            model = pending.get("model", "gpt-4o")
            adapter = create_adapter(provider=provider, uma=uma, model=model)

            if not adapter.is_available:
                yield {"data": json.dumps({"status": "error", "message": f"{provider} adapter not available"}, ensure_ascii=False)}
                return

            history = session_mgr.get_or_create_conversation(session_id)
            follow_up_msg = (
                f"[使用者已授權執行高風險技能「{tool_name}」]\n"
                f"執行結果如下，請根據結果給使用者一個清晰的總結：\n\n"
                f"```json\n{result_summary}\n```"
            )
            outbound = [
                {k: v for k, v in m.items() if k != "created_at"}
                for m in history
            ] + [{"role": "user", "content": follow_up_msg}]

            final_content = ""
            for chunk in adapter.chat(messages=outbound, session_id=session_id, tools_enabled=False):
                status = chunk.get("status")
                if status == "streaming":
                    text = chunk.get("content", "")
                    final_content += text
                    yield {"data": json.dumps({"status": "streaming", "content": text}, ensure_ascii=False)}
                elif status == "success":
                    final = chunk.get("content", final_content) or final_content
                    session_mgr.append_message(session_id, "assistant", final)
                    yield {"data": json.dumps({"status": "success", "content": final}, ensure_ascii=False)}
                    break
                else:
                    yield {"data": json.dumps(chunk, ensure_ascii=False)}
                    break

        except Exception as e:
            logger.error(f"[Approve] Error resuming tool call for session={session_id}: {e}")
            yield {"data": json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)}

    return EventSourceResponse(resume_generator())


@router.post("/chat/reject/{session_id}")
def reject_tool_call(session_id: str):
    """
    Phase 3-B: Reject endpoint.
    Called by the frontend when the user declines a high-risk tool call.
    Clears the pending approval store.
    """
    session_mgr = get_session_manager()
    pending = session_mgr.get_pending_approval(session_id)
    tool_name = pending.get("tool_name", "unknown") if pending else "unknown"
    session_mgr.clear_pending_approval(session_id)
    logger.info(f"[Reject] User rejected tool call '{tool_name}' for session={session_id}")
    return {"status": "rejected", "tool_name": tool_name, "message": "使用者已拒絕執行此高風險操作。"}
