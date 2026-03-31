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
from main import PROJECT_ROOT

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
    try:
        from server.services.id_utils import validate_session_id
        session_id = validate_session_id(session_id)
    except Exception:
        session_id = "default"
    
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

    # Phase 1(A2): Build a token-budgeted outbound prompt (PromptBuilder)
    # - behavior rules are already appended inside dynamic_prompt (Phase 2-A)
    # - session summary + retrieved memory are injected as optional context blocks
    session_summary = ""
    try:
        from server.services.id_utils import is_anonymous_web_session_id
        if not is_anonymous_web_session_id(session_id):
            from server.services.session_summarizer import SessionSummarizer, render_session_summary_injection
            ssum = SessionSummarizer(PROJECT_ROOT).maybe_update(session_id, min_new_messages=6)
            session_summary = render_session_summary_injection(ssum, max_chars=900)
    except Exception:
        pass

    retrieved_memory = ""
    try:
        from server.services.memory_retriever import MemoryRetriever, render_memory_injection
        from server.services.behavior_rule_loader import load_behavior_rule_texts
        br_texts = load_behavior_rule_texts(PROJECT_ROOT, max_each=8)
        mem_items = MemoryRetriever(PROJECT_ROOT).retrieve(req.user_input, max_items=8)
        retrieved_memory = render_memory_injection(mem_items, max_chars=800, exclude_texts=br_texts)
    except Exception:
        pass

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

    # Use PromptBuilder to trim history/context to a fixed token budget
    try:
        from server.services.prompt_builder import Budget, PromptParts, build_prompt_messages
        from server.services.budget_profiles import get_budget_for_model

        sanitized_history = [{k: v for k, v in m.items() if k != "created_at"} for m in history]
        bp = get_budget_for_model(req.model, platform="web")

        outbound_history, prompt_meta = build_prompt_messages(
            model=req.model or "gpt-4o-mini",
            budget=Budget(max_input_tokens=bp.max_input_tokens, reserve_output_tokens=bp.reserve_output_tokens),
            parts=PromptParts(
                system=dynamic_prompt,
                behavior_rules_appendix="",  # already in dynamic_prompt
                session_summary=session_summary,
                retrieved_memory=retrieved_memory,
                history=sanitized_history,
                user=user_content,
            ),
        )

        # Phase 1b: reduce log noise; verbose meta behind env toggle
        import os
        if os.environ.get("PROMPT_DEBUG", "").strip().lower() in ("1", "true", "yes"):
            logger.info(f"[PromptBuilder] meta={prompt_meta}")
            try:
                from server.services.prompt_meta_logger import append_prompt_meta
                # Strong correlation id: prefer session metadata last_response_id
                try:
                    from server.dependencies.session import get_session_manager as _get_session_manager
                    _sm = _get_session_manager()
                    correlation_id = _sm.get_metadata(session_id, "last_response_id") or ""
                except Exception:
                    correlation_id = ""
                if not correlation_id:
                    correlation_id = prompt_meta.get("provider", {}).get("response_id", "")
                append_prompt_meta(PROJECT_ROOT, session_id, prompt_meta, correlation_id=correlation_id)
            except Exception:
                pass
        else:
            slim = {
                "final_total_tokens": prompt_meta.get("included", {}).get("final_total_tokens"),
                "history_messages": prompt_meta.get("included", {}).get("history_messages"),
                "trimmed": prompt_meta.get("trimmed", {}),
            }
            logger.info(f"[PromptBuilder] {slim}")
    except Exception as pb_err:
        logger.warning(f"[PromptBuilder] Fallback to raw history: {pb_err}")
        raw_outbound = history + [{"role": "user", "content": user_content}]
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

                    # Bridge sync: Web → LINE push (user input + assistant reply)
                    try:
                        from server.services.bridge_sync import (
                            get_bridge_state,
                            make_bridge_tag,
                            session_target_from_session_id,
                            should_sync_session,
                        )
                        from main import PROJECT_ROOT
                        if should_sync_session(session_id):
                            st = get_bridge_state(PROJECT_ROOT)
                            if st.throttle_ok(session_id, cooldown_seconds=5):
                                kind, native_id = session_target_from_session_id(session_id)

                                # Group gating: only if known and active within 10 minutes
                                if kind == "group":
                                    if not st.group_can_push(native_id, active_window_seconds=600):
                                        kind = "other"
                                if kind == "room":
                                    if not st.group_can_push(native_id, active_window_seconds=600):
                                        kind = "other"

                                if kind in ("user", "group", "room"):
                                    try:
                                        from server.integrations.line_connector import _get_line_components
                                        from linebot.v3.messaging import TextMessage, PushMessageRequest

                                        _, line_api, _ = _get_line_components()
                                        if line_api:
                                            tag1 = make_bridge_tag(session_id, req.user_input)
                                            tag2 = make_bridge_tag(session_id, final)
                                            msg_user = f"【Web】你：{req.user_input}\n\n{tag1}"
                                            msg_ai = f"【Web】AI：{final}\n\n{tag2}"

                                            # Push target
                                            to = native_id
                                            # Best-effort push; if group/room push fails, mark incapable
                                            try:
                                                line_api.push_message(PushMessageRequest(to=to, messages=[TextMessage(text=msg_user[:5000])]))
                                                line_api.push_message(PushMessageRequest(to=to, messages=[TextMessage(text=msg_ai[:5000])]))
                                            except Exception:
                                                if kind in ("group", "room"):
                                                    st.set_group_push_capable(native_id, False)
                                    except Exception:
                                        pass
                    except Exception:
                        pass

                    yield {"data": json.dumps({"status": "success", "content": final}, ensure_ascii=False)}
                    break
                else:
                    yield {"data": json.dumps(chunk, ensure_ascii=False)}
                    break
        except Exception as e:
            logger.error(f"Chat stream error ({provider}): {e}")
            yield {"data": json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)}

    return EventSourceResponse(event_generator())

