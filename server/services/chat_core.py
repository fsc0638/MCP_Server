"""Native chat service placeholder.

This module is the target for fully replacing legacy chat flow.
"""

import json
import logging
from typing import AsyncGenerator

from sse_starlette.sse import EventSourceResponse

from server.dependencies.uma import get_uma_instance as get_uma
from server.core.retriever import retriever
from server.adapters.openai_adapter import OpenAIAdapter
from server.dependencies.session import get_session_manager
from server.schemas.chat import ChatRequest

logger = logging.getLogger("MCP_Server.ChatCore")


async def process_chat_native(req: ChatRequest):
    """
    Native chat baseline implementation.
    Scope:
      - OpenAI provider path
      - Supports selected docs context and injected skill knowledge
      - Supports attached_file for non-execute path
      - execute=true supported via OpenAI adapter tool-calling path
    """

    provider = (req.provider or "").strip().lower()
    
    # Auto-resolve provider if missing but model is classic
    if not provider:
        m = (req.model or "").lower()
        if m.startswith("gpt-"): provider = "openai"
        elif m.startswith("gemini-"): provider = "gemini"
        elif m.startswith("claude-"): provider = "claude"
        else: provider = "openai" # Default

    from server.adapters.factory import create_adapter
    uma = get_uma()
    adapter = create_adapter(
        provider=provider, 
        uma=uma, 
        model=req.model, 
        api_base=req.api_base, 
        api_key=req.api_key
    )
    
    if not adapter.is_available:
        return {"status": "error", "message": f"{provider.capitalize()} adapter is not available"}

    from server.services.runtime import get_universal_system_prompt
    
    session_mgr = get_session_manager()
    session_id = req.session_id or "default"
    
    # Use dynamic universal prompt to align with LINE bot behavior (time awareness, etc.)
    logger.info(f"Chat Request: [Model: {req.model}] [Lang: {req.language}] [Detail: {req.detail_level}]")
    dynamic_prompt = get_universal_system_prompt(
        platform="web", 
        language=req.language or "繁體中文", 
        detail_level=req.detail_level or "適中"
    )
    logger.info(f"Generated Dynamic Prompt (Sample): {dynamic_prompt[:100]}... [MID] ...{dynamic_prompt[-100:]}")
    history = session_mgr.get_or_create_conversation(session_id, dynamic_prompt)
    
    # Force update system prompt to ensure latest time, language and style are injected
    session_mgr._update_system_prompt(session_id, dynamic_prompt)
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

    if req.injected_skill:
        skill_knowledge = uma.get_skill_knowledge(req.injected_skill)
        if skill_knowledge:
            user_content += f"\n\n[Skill Knowledge: {req.injected_skill}]\n{skill_knowledge}"

    # Hidden language hint to force compliance on every turn (especially first turn and cards)
    if req.language and req.language != "自動偵測":
        user_content += f"\n\n(System Note: Respond strictly in {req.language}. If input is in another language, translate your answer.)"

    raw_outbound = history + [{"role": "user", "content": user_content}]
    
    # Sanitize history for API compatibility
    outbound_history = []
    for m in raw_outbound:
        clean_msg = {k: v for k, v in m.items() if k != "created_at"}
        outbound_history.append(clean_msg)

    async def event_generator() -> AsyncGenerator[dict, None]:
        session_mgr.append_message(session_id, "user", req.user_input)
        final_content = ""

        try:
            # Unify all chat paths to the robust adapter.chat which handles instructions, tools and vision
            chunk_iter = adapter.chat(
                messages=outbound_history,
                user_query=user_content,
                session_id=session_id,
                attached_file=req.attached_file,
                temperature=req.temperature or 0.7,
                visual_docs=req.selected_docs or []
            )

            for chunk in chunk_iter:
                status = chunk.get("status")
                if status == "streaming":
                    text = chunk.get("content", "")
                    final_content += text
                    yield {"data": json.dumps({"status": "streaming", "content": text}, ensure_ascii=False)}
                elif status == "success":
                    final = chunk.get("content", final_content)
                    if not final:
                        final = final_content
                    session_mgr.append_message(session_id, "assistant", final)
                    yield {"data": json.dumps({"status": "success", "content": final}, ensure_ascii=False)}
                    break
                else:
                    yield {"data": json.dumps(chunk, ensure_ascii=False)}
                    break
        except Exception as e:
            logger.error(f"Chat stream error ({provider}): {e}")
            yield {"data": json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)}

    return EventSourceResponse(event_generator())

