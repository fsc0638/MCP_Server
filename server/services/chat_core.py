"""Native chat service placeholder.

This module is the target for fully replacing legacy chat flow.
"""

import json
from typing import AsyncGenerator

from sse_starlette.sse import EventSourceResponse

from main import get_uma
from core.retriever import retriever
from server.adapters.openai_adapter import OpenAIAdapter
from server.dependencies.session import get_session_manager
from server.schemas.chat import ChatRequest


async def process_chat_native(req: ChatRequest):
    """
    Native chat baseline implementation.
    Scope:
      - OpenAI provider path
      - Supports selected docs context and injected skill knowledge
      - execute/attached_file still falls back to legacy
    """
    if req.execute or req.attached_file:
        raise NotImplementedError("Native chat currently does not support execute/attached_file.")

    provider = (req.provider or "").strip().lower()
    model = (req.model or "openai").strip().lower()
    if provider and provider != "openai":
        raise NotImplementedError("Native chat currently supports provider=openai only.")
    if model.startswith("gemini") or model.startswith("claude"):
        raise NotImplementedError("Native chat currently supports OpenAI-style models only.")

    uma = get_uma()
    adapter = OpenAIAdapter(uma=uma, model=req.model, api_base=req.api_base, api_key=req.api_key)
    if not adapter.is_available:
        return {"status": "error", "message": "OpenAI adapter is not available"}

    session_mgr = get_session_manager()
    session_id = req.session_id or "default"
    history = session_mgr.get_or_create_conversation(
        session_id,
        "You are MCP Agent Console assistant. Answer clearly and concisely.",
    )
    user_content = req.user_input

    # Optional document context injection
    if req.selected_docs is not None:
        if len(req.selected_docs) == 0:
            doc_context = ""
        else:
            doc_context = retriever.search_context(
                req.user_input,
                top_k=max(3, len(req.selected_docs) + 2),
                filter_type="workspace",
                allowed_filenames=req.selected_docs,
            )
        if doc_context:
            user_content += f"\n\n[Document Context]\n{doc_context}"

    # Optional injected skill knowledge (non-execute reference)
    if req.injected_skill:
        skill_knowledge = uma.get_skill_knowledge(req.injected_skill)
        if skill_knowledge:
            user_content += f"\n\n[Skill Knowledge: {req.injected_skill}]\n{skill_knowledge}"

    outbound_history = history + [{"role": "user", "content": user_content}]

    async def event_generator() -> AsyncGenerator[dict, None]:
        session_mgr.append_message(session_id, "user", req.user_input)
        final_content = ""
        for chunk in adapter.simple_chat(outbound_history, temperature=req.temperature or 0.7):
            status = chunk.get("status")
            if status == "streaming":
                text = chunk.get("content", "")
                final_content += text
                yield {"data": json.dumps({"status": "streaming", "content": text}, ensure_ascii=False)}
            elif status == "success":
                session_mgr.append_message(session_id, "assistant", final_content)
                yield {"data": json.dumps({"status": "success", "content": final_content}, ensure_ascii=False)}
                break
            else:
                yield {"data": json.dumps(chunk, ensure_ascii=False)}
                break

    return EventSourceResponse(event_generator())
