"""Native chat service implementation with task-scoped streaming state."""
import json
import logging
import uuid
from typing import AsyncGenerator

from sse_starlette.sse import EventSourceResponse

from main import PROJECT_ROOT
from server.core.retriever import retriever
from server.dependencies.session import get_session_manager
from server.dependencies.task_registry import get_task_registry
from server.dependencies.uma import get_uma_instance as get_uma
from server.schemas.chat import ChatRequest
from server.services.async_bridge import iterate_blocking_generator

logger = logging.getLogger("MCP_Server.ChatCore")


async def process_chat_native(req: ChatRequest):
    """Handle a web chat turn through the active provider adapter."""

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

    # Load user context for three-tier skill filtering
    _user_context = None
    _sid = req.session_id or "default"
    try:
        import json as _json
        from pathlib import Path as _P
        _uc_path = _P(os.getenv("PROJECT_ROOT", ".")) / "workspace" / "users" / f"{_sid}.json"
        if _uc_path.exists():
            _user_context = _json.loads(_uc_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    adapter = create_adapter(
        provider=provider,
        uma=uma,
        model=req.model,
        user_context=_user_context,
        api_base=req.api_base,
        api_key=req.api_key,
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

    task_registry = get_task_registry()
    resolved_model = (
        req.model
        or getattr(adapter, "model", None)
        or getattr(adapter, "model_name", None)
        or ""
    )
    turn_id = (req.turn_id or "").strip() or f"turn-{uuid.uuid4().hex}"
    task = task_registry.create_task(
        session_id=session_id,
        turn_id=turn_id,
        provider=provider,
        model=resolved_model,
        user_input=req.user_input,
    )
    task_id = task["task_id"]

    logger.info(f"Chat Request: [Model: {req.model}] [Lang: {req.language}] [Detail: {req.detail_level}]")
    dynamic_prompt = get_universal_system_prompt(
        platform="web",
        language=req.language or "繁體中文",
        detail_level=req.detail_level or "詳細",
    )
    logger.info(f"Generated Dynamic Prompt (Sample): {dynamic_prompt[:100]}... [MID] ...{dynamic_prompt[-100:]}")
    history = session_mgr.get_or_create_conversation(session_id, dynamic_prompt)
    session_mgr._update_system_prompt(session_id, dynamic_prompt)

    user_content = req.user_input

    session_summary = ""
    try:
        from server.services.id_utils import is_anonymous_web_session_id

        if not is_anonymous_web_session_id(session_id):
            from server.services.session_summarizer import (
                SessionSummarizer,
                render_session_summary_injection,
            )

            summary = SessionSummarizer(PROJECT_ROOT).maybe_update(session_id, min_new_messages=6)
            session_summary = render_session_summary_injection(summary, max_chars=900)
    except Exception:
        pass

    retrieved_memory = ""
    try:
        from server.services.behavior_rule_loader import load_behavior_rule_texts
        from server.services.memory_retriever import MemoryRetriever, render_memory_injection

        behavior_rule_texts = load_behavior_rule_texts(PROJECT_ROOT, max_each=8)
        memory_items = MemoryRetriever(PROJECT_ROOT).retrieve(req.user_input, max_items=8)
        retrieved_memory = render_memory_injection(
            memory_items,
            max_chars=800,
            exclude_texts=behavior_rule_texts,
        )
    except Exception:
        pass

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

    if req.language and req.language != "自動偵測":
        user_content += (
            f"\n\n(System Note: Respond strictly in {req.language}. "
            "If input is in another language, translate your answer.)"
        )

    try:
        from server.services.budget_profiles import get_budget_for_model
        from server.services.prompt_builder import Budget, PromptParts, build_prompt_messages

        sanitized_history = [{k: v for k, v in msg.items() if k != "created_at"} for msg in history]
        budget_profile = get_budget_for_model(req.model, platform="web")

        outbound_history, prompt_meta = build_prompt_messages(
            model=req.model or "gpt-4o-mini",
            budget=Budget(
                max_input_tokens=budget_profile.max_input_tokens,
                reserve_output_tokens=budget_profile.reserve_output_tokens,
            ),
            parts=PromptParts(
                system=dynamic_prompt,
                behavior_rules_appendix="",
                session_summary=session_summary,
                retrieved_memory=retrieved_memory,
                history=sanitized_history,
                user=user_content,
            ),
        )

        import os

        if os.environ.get("PROMPT_DEBUG", "").strip().lower() in ("1", "true", "yes"):
            logger.info(f"[PromptBuilder] meta={prompt_meta}")
            try:
                from server.services.prompt_meta_logger import append_prompt_meta

                try:
                    correlation_id = session_mgr.get_metadata(session_id, "last_response_id") or ""
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
    except Exception as prompt_error:
        logger.warning(f"[PromptBuilder] Fallback to raw history: {prompt_error}")
        raw_outbound = history + [{"role": "user", "content": user_content}]
        outbound_history = []
        for msg in raw_outbound:
            clean_msg = {k: v for k, v in msg.items() if k != "created_at"}
            outbound_history.append(clean_msg)

    async def event_generator() -> AsyncGenerator[dict, None]:
        def wrap_payload(payload: dict) -> dict:
            enriched = dict(payload)
            enriched.setdefault("task_id", task_id)
            enriched.setdefault("session_id", session_id)
            enriched.setdefault("turn_id", turn_id)
            return enriched

        yield {"data": json.dumps(wrap_payload({"status": "task_started"}), ensure_ascii=False)}

        session_mgr.append_message(session_id, "user", req.user_input)
        final_content = ""
        saw_success = False
        last_status = None

        try:
            async for chunk in iterate_blocking_generator(
                lambda: adapter.chat(
                    messages=outbound_history,
                    user_query=user_content,
                    session_id=session_id,
                    attached_file=req.attached_file,
                    temperature=req.temperature or 0.7,
                    visual_docs=req.selected_docs or [],
                )
            ):
                status = chunk.get("status")
                last_status = status

                if status == "streaming":
                    text = chunk.get("content", "")
                    final_content += text
                    task_registry.append_partial_text(task_id, text)
                    yield {
                        "data": json.dumps(
                            wrap_payload({"status": "streaming", "content": text}),
                            ensure_ascii=False,
                        )
                    }
                    continue

                if status == "success":
                    saw_success = True
                    final = chunk.get("content", final_content) or final_content
                    session_mgr.append_message(session_id, "assistant", final)
                    task_registry.mark_completed(
                        task_id,
                        final_text=final,
                        assistant_message_persisted=True,
                    )

                    logger.info(f"[Bridge] Enter success branch for session={session_id}")
                    try:
                        from server.services.bridge_sync import (
                            get_bridge_state,
                            session_target_from_session_id,
                            should_sync_session,
                        )
                        from server.services.bridge_sync import make_web_bridge_tag

                        sync_ok = should_sync_session(session_id)
                        logger.info(f"[Bridge] should_sync_session={sync_ok} session={session_id}")

                        if sync_ok:
                            bridge_state = get_bridge_state(PROJECT_ROOT)
                            if not bridge_state.throttle_ok(session_id, cooldown_seconds=5):
                                logger.info(f"[Bridge] Throttled for session={session_id}")
                            else:
                                kind, native_id = session_target_from_session_id(session_id)
                                logger.info(
                                    f"[Bridge] Sync attempt kind={kind} native_id={native_id} session={session_id}"
                                )

                                if kind == "group" and not bridge_state.group_can_push(
                                    native_id,
                                    active_window_seconds=600,
                                ):
                                    logger.info(f"[Bridge] Skip group push (not active/known): group_id={native_id}")
                                    kind = "other"
                                if kind == "room" and not bridge_state.group_can_push(
                                    native_id,
                                    active_window_seconds=600,
                                ):
                                    logger.info(f"[Bridge] Skip room push (not active/known): room_id={native_id}")
                                    kind = "other"

                                if kind in ("user", "group", "room"):
                                    from linebot.v3.messaging import PushMessageRequest, TextMessage
                                    from server.integrations.line_connector import _get_line_components

                                    _, line_api, _ = _get_line_components()
                                    if not line_api:
                                        logger.warning("[Bridge] LINE API not available; cannot push")
                                    else:
                                        tag_user = make_web_bridge_tag(session_id, req.user_input)
                                        tag_ai = make_web_bridge_tag(session_id, final)
                                        msg_user = f"[Web User]\n{req.user_input}\n\n{tag_user}"
                                        msg_ai = f"[Web AI]\n{final}\n\n{tag_ai}"
                                        try:
                                            line_api.push_message(
                                                PushMessageRequest(
                                                    to=native_id,
                                                    messages=[TextMessage(text=msg_user[:5000])],
                                                )
                                            )
                                            line_api.push_message(
                                                PushMessageRequest(
                                                    to=native_id,
                                                    messages=[TextMessage(text=msg_ai[:5000])],
                                                )
                                            )
                                            logger.info(f"[Bridge] Pushed 2 messages for kind={kind} to={native_id}")
                                        except Exception as exc:
                                            logger.error(f"[Bridge] Push failed for kind={kind} to={native_id}: {exc}")
                                            if kind in ("group", "room"):
                                                bridge_state.set_group_push_capable(native_id, False)
                                else:
                                    logger.info(f"[Bridge] Skip push (kind={kind}) for session={session_id}")
                    except Exception as exc:
                        logger.exception(f"[Bridge] unexpected error: {exc}")

                    yield {
                        "data": json.dumps(
                            wrap_payload({"status": "success", "content": final}),
                            ensure_ascii=False,
                        )
                    }
                    break

                if status == "provider_meta":
                    yield {"data": json.dumps(wrap_payload(chunk), ensure_ascii=False)}
                    continue

                if status == "tool_call":
                    task_registry.mark_tool_call(
                        task_id,
                        chunk.get("tool_name", ""),
                        chunk.get("message", ""),
                    )
                    yield {"data": json.dumps(wrap_payload(chunk), ensure_ascii=False)}
                    continue

                if status == "requires_approval":
                    task_registry.mark_requires_approval(
                        task_id,
                        tool_name=chunk.get("tool_name", ""),
                        risk_description=chunk.get("risk_description", ""),
                        pending_args=chunk.get("pending_args", {}),
                        provider=chunk.get("provider") or provider,
                        model=chunk.get("model") or resolved_model,
                    )
                    yield {"data": json.dumps(wrap_payload(chunk), ensure_ascii=False)}
                    break

                if status == "error":
                    task_registry.mark_error(task_id, chunk.get("message", "Unknown error"))
                    yield {"data": json.dumps(wrap_payload(chunk), ensure_ascii=False)}
                    break

                yield {"data": json.dumps(wrap_payload(chunk), ensure_ascii=False)}
                break
        except Exception as exc:
            logger.error(f"Chat stream error ({provider}): {exc}")
            task_registry.mark_error(task_id, str(exc))
            yield {
                "data": json.dumps(
                    wrap_payload({"status": "error", "message": str(exc)}),
                    ensure_ascii=False,
                )
            }
        finally:
            if not saw_success:
                logger.warning(
                    f"[ChatCore] Stream ended without success: session={session_id} "
                    f"last_status={last_status} final_len={len(final_content)} "
                    f"provider={provider} model={req.model}"
                )
                if last_status not in {"requires_approval", "error"}:
                    task_registry.mark_error(
                        task_id,
                        f"Stream ended without success (last_status={last_status or 'unknown'})",
                    )

    return EventSourceResponse(event_generator())
