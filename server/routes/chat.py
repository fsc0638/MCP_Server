"""Chat routes."""

import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator, Dict

from fastapi import APIRouter, Cookie, HTTPException
from sse_starlette.sse import EventSourceResponse

from server.dependencies.session import get_session_manager
from server.dependencies.task_registry import get_task_registry
from server.dependencies.uma import get_uma_instance as get_uma
from server.schemas.chat import ChatRequest, ExecuteRequest, TitleSummaryRequest
from server.services.async_bridge import iterate_blocking_generator
from server.services.chat_service import process_chat

logger = logging.getLogger("MCP_Server.Chat")
router = APIRouter(tags=["Chat"])


def _wrap_task_payload(task_id: str, session_id: str, payload: Dict, turn_id: str = "") -> Dict:
    enriched = dict(payload)
    enriched.setdefault("task_id", task_id)
    enriched.setdefault("session_id", session_id)
    enriched.setdefault("turn_id", turn_id)
    return enriched


def _get_active_task_for_session(session_id: str) -> Dict:
    task_registry = get_task_registry()
    task = task_registry.get_active_task_for_session(session_id)
    if not task:
        raise HTTPException(status_code=404, detail="No active task found for this session")
    return task


@router.post("/chat")
async def chat(req: ChatRequest, mcp_session: str = Cookie(default="", alias="mcp_session")):
    if not req.user_id and mcp_session:
        session = None
        try:
            from server.services.auth_session_store import get_auth_session_store
            from server.services.session_token_cookie import verify_token
            token = verify_token(mcp_session)
            if token:
                session = get_auth_session_store().get(token)
        except Exception:
            session = None
        if session and getattr(session, "user_id", ""):
            req.user_id = session.user_id
    return await process_chat(req)


@router.post("/chat/title-summary")
def summarize_chat_title(req: TitleSummaryRequest):
    provider = (req.provider or "").strip().lower()
    if not provider:
        model_name = (req.model or "").lower()
        if model_name.startswith("gpt-"):
            provider = "openai"
        elif model_name.startswith("gemini-"):
            provider = "gemini"
        elif model_name.startswith("claude-"):
            provider = "claude"
        else:
            provider = "openai"

    from server.adapters.factory import create_adapter

    uma = get_uma()
    adapter = create_adapter(provider=provider, uma=uma, model=req.model)
    if not adapter.is_available:
        raise HTTPException(status_code=503, detail=f"{provider.capitalize()} adapter is not available")

    language = (req.language or "繁體中文").strip() or "繁體中文"
    system_prompt = (
        f"你是一個只負責產生對話標題的助理。請使用{language}。"
        "請根據提供的使用者問題與助手回覆，產生 10 字以內的簡短標題。"
        "只回傳標題文字，不要加引號、說明、標點或多餘句子。"
    )
    user_prompt = (
        "請根據這段問答內容，產生一個 10 字以內的對話標題，只回傳標題文字。\n"
        f"使用者：{req.user_input}\n"
        f"助手：{req.assistant_output}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    final_content = ""
    for chunk in adapter.chat(
        messages=messages,
        user_query=user_prompt,
        session_id=f"title-summary-{uuid.uuid4().hex}",
        temperature=0.2,
        tools_enabled=False,
    ):
        status = chunk.get("status")
        if status == "streaming":
            final_content += chunk.get("content", "")
        elif status == "success":
            final_content = chunk.get("content", final_content) or final_content
            break
        elif status == "error":
            message = chunk.get("message", "Title summary failed")
            raise HTTPException(status_code=502, detail=message)

    clean_title = final_content.replace("'", "").replace('"', "").replace(".", "").replace("!", "").replace("?", "").strip()[:10]
    if not clean_title:
        raise HTTPException(status_code=502, detail="Failed to generate title")

    return {"status": "success", "title": clean_title}


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
    chat_history = [msg for msg in history if msg.get("role") != "system"]
    return {"status": "success", "history": chat_history}


@router.get("/chat/tasks/session/{session_id}")
def get_session_tasks(session_id: str):
    task_registry = get_task_registry()
    tasks = task_registry.list_tasks_for_session(session_id, include_terminal=True, limit=20)
    return {"status": "success", "tasks": tasks}


@router.get("/chat/tasks/{task_id}")
def get_task(task_id: str):
    task_registry = get_task_registry()
    task = task_registry.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "success", "task": task}


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
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/chat/tasks/{task_id}/approve")
async def approve_tool_call_by_task(task_id: str):
    task_registry = get_task_registry()
    session_mgr = get_session_manager()
    task = task_registry.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") != "requires_approval":
        raise HTTPException(status_code=409, detail="Task is not waiting for approval")

    session_id = task.get("session_id") or "default"
    tool_name = task.get("tool_name") or ""
    tool_args = dict(task.get("pending_args") or {})
    provider = task.get("provider") or "openai"
    model = task.get("model") or "gpt-4o"
    turn_id = task.get("turn_id") or ""
    uma = get_uma()

    if tool_name == "mcp-meeting-analyzer" and not tool_args.get("transcript"):
        history = session_mgr.get_or_create_conversation(session_id)
        for msg in reversed(history):
            if msg.get("role") == "user" and msg.get("content"):
                tool_args["transcript"] = msg["content"]
                logger.info(
                    "[Approve] Injected transcript from session history (%s chars)",
                    len(tool_args["transcript"]),
                )
                break

    task_registry.mark_approved(task_id)

    async def resume_generator() -> AsyncGenerator[dict, None]:
        try:
            yield {
                "data": json.dumps(
                    _wrap_task_payload(task_id, session_id, {"status": "task_resumed"}, turn_id=turn_id),
                    ensure_ascii=False,
                )
            }
            task_registry.mark_tool_call(task_id, tool_name, f"Approved tool running: {tool_name}")

            executor = uma.executor
            script_path = executor.skills_home / tool_name / "scripts" / "main.py"
            if script_path.exists():
                skill_data = uma.registry.get_skill(tool_name)
                exec_timeout = skill_data.get("metadata", {}).get("execution_timeout", 30) if skill_data else 30
                result = await asyncio.to_thread(
                    executor.run_script,
                    tool_name,
                    "main.py",
                    tool_args,
                    timeout=exec_timeout,
                )
            else:
                result = {"status": "error", "message": f"Skill '{tool_name}' has no executable script."}

            result_summary = json.dumps(result, ensure_ascii=False)

            from server.adapters.factory import create_adapter

            adapter = create_adapter(provider=provider, uma=uma, model=model)
            if not adapter.is_available:
                message = f"{provider} adapter not available"
                task_registry.mark_error(task_id, message)
                yield {
                        "data": json.dumps(
                        _wrap_task_payload(task_id, session_id, {"status": "error", "message": message}, turn_id=turn_id),
                        ensure_ascii=False,
                    )
                }
                return

            history = session_mgr.get_or_create_conversation(session_id)
            follow_up_msg = (
                f"[Tool Approved and Executed: {tool_name}]\n"
                "Please summarize the result for the user in a clear and concise way.\n\n"
                f"```json\n{result_summary}\n```"
            )
            outbound = [{k: v for k, v in msg.items() if k != "created_at"} for msg in history]
            outbound.append({"role": "user", "content": follow_up_msg})

            final_content = ""
            async for chunk in iterate_blocking_generator(
                lambda: adapter.chat(messages=outbound, session_id=session_id, tools_enabled=False)
            ):
                status = chunk.get("status")
                if status == "streaming":
                    text = chunk.get("content", "")
                    final_content += text
                    task_registry.append_partial_text(task_id, text)
                    yield {
                        "data": json.dumps(
                            _wrap_task_payload(task_id, session_id, {"status": "streaming", "content": text}, turn_id=turn_id),
                            ensure_ascii=False,
                        )
                    }
                elif status == "provider_meta":
                    yield {
                        "data": json.dumps(
                            _wrap_task_payload(task_id, session_id, chunk, turn_id=turn_id),
                            ensure_ascii=False,
                        )
                    }
                elif status == "success":
                    final = chunk.get("content", final_content) or final_content
                    session_mgr.append_message(session_id, "assistant", final)
                    task_registry.mark_completed(
                        task_id,
                        final_text=final,
                        assistant_message_persisted=True,
                    )
                    yield {
                        "data": json.dumps(
                            _wrap_task_payload(task_id, session_id, {"status": "success", "content": final}, turn_id=turn_id),
                            ensure_ascii=False,
                        )
                    }
                    return
                elif status == "error":
                    message = chunk.get("message", "Unknown error")
                    task_registry.mark_error(task_id, message)
                    yield {
                        "data": json.dumps(
                            _wrap_task_payload(task_id, session_id, {"status": "error", "message": message}, turn_id=turn_id),
                            ensure_ascii=False,
                        )
                    }
                    return
                else:
                    yield {
                        "data": json.dumps(
                            _wrap_task_payload(task_id, session_id, chunk, turn_id=turn_id),
                            ensure_ascii=False,
                        )
                    }
                    return
        except Exception as exc:
            logger.error(f"[Approve] Error resuming tool call for task={task_id}: {exc}")
            task_registry.mark_error(task_id, str(exc))
            yield {
                "data": json.dumps(
                    _wrap_task_payload(task_id, session_id, {"status": "error", "message": str(exc)}, turn_id=turn_id),
                    ensure_ascii=False,
                )
            }

    return EventSourceResponse(resume_generator())


@router.post("/chat/tasks/{task_id}/reject")
def reject_tool_call_by_task(task_id: str):
    task_registry = get_task_registry()
    task = task_registry.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    tool_name = task.get("tool_name", "unknown")
    session_id = task.get("session_id", "")
    task_registry.mark_rejected(task_id, f"User rejected tool '{tool_name}'")
    logger.info(f"[Reject] User rejected tool call '{tool_name}' for task={task_id} session={session_id}")
    return {
        "status": "rejected",
        "task_id": task_id,
        "session_id": session_id,
        "tool_name": tool_name,
        "message": f"Rejected '{tool_name}'",
    }


@router.post("/chat/approve/{session_id}")
async def approve_tool_call(session_id: str):
    task = _get_active_task_for_session(session_id)
    return await approve_tool_call_by_task(task["task_id"])


@router.post("/chat/reject/{session_id}")
def reject_tool_call(session_id: str):
    task = _get_active_task_for_session(session_id)
    return reject_tool_call_by_task(task["task_id"])


# ── Stop / Cancel ──────────────────────────────────────────────────────────

@router.post("/chat/stop/{task_id}")
def stop_single_task(task_id: str):
    """Cancel a specific task by id."""
    task_registry = get_task_registry()
    task = task_registry.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") in task_registry.TERMINAL_STATUSES:
        return {"status": "already_terminal", "task_id": task_id, "final_status": task.get("status")}
    task_registry.mark_cancelled(task_id, reason="user_stop")
    logger.info(f"[Stop] task={task_id} session={task.get('session_id')}")
    return {"status": "cancelled", "task_id": task_id, "session_id": task.get("session_id", "")}


@router.post("/chat/stop_all/{session_id}")
def stop_all_for_session(session_id: str):
    """Cancel all active tasks for a session — used when user clicks the
    stop button in the UI to immediately regain control.

    Returns count of cancelled tasks.
    """
    task_registry = get_task_registry()
    active = task_registry.list_active_for_session(session_id)
    cancelled_ids = []
    for t in active:
        tid = t.get("task_id", "")
        if tid:
            task_registry.mark_cancelled(tid, reason="user_stop_all")
            cancelled_ids.append(tid)
    logger.info(f"[StopAll] session={session_id} cancelled {len(cancelled_ids)} task(s)")
    return {
        "status": "success",
        "session_id": session_id,
        "cancelled_count": len(cancelled_ids),
        "cancelled_task_ids": cancelled_ids,
    }
