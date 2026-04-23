"""Native chat service implementation with task-scoped streaming state."""
import json
import logging
import os
import re
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

_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".webm"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


def _is_audio_file_path(file_path: str | None) -> bool:
    if not file_path:
        return False
    try:
        return os.path.splitext(file_path)[1].lower() in _AUDIO_EXTENSIONS
    except Exception:
        return False


def _detect_media_type_label(file_path: str | None) -> str:
    if not file_path:
        return "file"
    try:
        ext = os.path.splitext(file_path)[1].lower()
    except Exception:
        ext = ""
    if ext in _AUDIO_EXTENSIONS:
        return "audio file"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    if ext in _VIDEO_EXTENSIONS:
        return "video"
    return "file"


def _extract_file_path_from_text(text: str) -> str | None:
    if not text:
        return None
    patterns = [
        r"file_path\s*[:=]\s*(.+)",
        r"檔案(?:絕對)?路徑\s*[:：]\s*(.+)",
        r"文件(?:絕對)?路徑\s*[:：]\s*(.+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if not m:
            continue
        candidate = m.group(1).splitlines()[0].strip()
        candidate = candidate.strip("`\"' ")
        candidate = candidate.rstrip("，。)]}>")
        if candidate and os.path.exists(candidate):
            return os.path.abspath(candidate)
    return None


def _register_original_file_context(session_mgr, session_id: str, file_path: str | None) -> str | None:
    if not file_path:
        return None
    try:
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return None
        session_mgr.set_metadata(session_id, "last_original_file", abs_path)
        fname = os.path.basename(abs_path)
        session_mgr.set_metadata(session_id, "last_original_filename", fname)
        m = re.search(r"(\d{8})", fname)
        if m:
            session_mgr.set_metadata(session_id, "last_original_file_date", m.group(1))
        return abs_path
    except Exception:
        return None


def _needs_meeting_todo_pipeline(user_text: str, file_path: str | None = None) -> bool:
    text = (user_text or "").lower()
    has_audio_signal = _is_audio_file_path(file_path) or any(
        kw in text for kw in ("錄音", "音檔", "audio", "transcribe", "逐字稿")
    )
    has_todo_signal = any(
        kw in text for kw in ("todo", "to do", "待辦", "notion", "上傳", "寫入", "匯入")
    )
    return has_audio_signal and has_todo_signal


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

    # Always inject session_id so downstream (workflow_executor / HitL approval
    # payload / workflow_resume) can write completion messages back to this
    # session's history. Without this, background resume has nowhere to
    # notify the originating Web chat UI.
    if _user_context is None:
        _user_context = {}
    if not _user_context.get("session_id"):
        _user_context["session_id"] = _sid

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

    # Register file context for downstream tools (WebUI parity with LINE pipeline).
    active_original_file = None
    for candidate in (
        (req.attached_file or "").strip() or None,
        _extract_file_path_from_text(req.user_input or ""),
    ):
        saved = _register_original_file_context(session_mgr, session_id, candidate)
        if saved:
            active_original_file = saved

    if not active_original_file:
        try:
            stored = session_mgr.get_metadata(session_id, "last_original_file")
            if stored and os.path.exists(stored):
                active_original_file = stored
        except Exception:
            pass

    # Adapter-level context injection (used by openai_adapter tool arg enrichment).
    setattr(
        adapter,
        "_original_file_path",
        active_original_file if active_original_file and os.path.exists(active_original_file) else None,
    )
    setattr(adapter, "_original_file_date", session_mgr.get_metadata(session_id, "last_original_file_date"))

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
    _upload_handoff = bool(req.upload_handoff)

    # ── Workflow-First Matching ─────────────────────────────────────
    # Before LLM call, check if a workflow matches the user input.
    # If matched, execute the workflow directly (saves 93%+ tokens).
    if os.getenv("WF_FIRST_ENABLED", "1").strip() not in ("0", "false", "no"):
        try:
            from server.services.workflow_matcher import get_workflow_matcher
            wf_matcher = get_workflow_matcher()
            wf_match = wf_matcher.match(req.user_input, user_context=_user_context)
            if wf_match:
                wf_mode = wf_match["workflow"].get("trigger_mode", "auto")
                wf_conf = wf_match.get("confidence", wf_match.get("score", 0))
                wf_reason = wf_match.get("match_reason", "")
                wf_rejected = wf_match.get("rejected_candidates", [])
                logger.info(f"[WF-First] Matched: {wf_match['workflow_id']} "
                            f"(conf={wf_conf:.2f}, method={wf_match['method']}, mode={wf_mode}, reason={wf_reason})")

                # Phase 3: build explainable match_info for frontend/debug
                _match_info = {
                    "id": wf_match["workflow_id"],
                    "name": wf_match["workflow"].get("display_name") or wf_match["workflow"].get("name", ""),
                    "method": wf_match["method"],
                    "confidence": round(wf_conf, 3),
                    "match_reason": wf_reason,
                    "rejected_candidates": wf_rejected[:3],  # top 3 only
                }

                if wf_mode == "auto":
                    # Auto-execute: run workflow and return result as SSE
                    async def _wf_event_generator():
                        yield {"data": json.dumps({"status": "task_started", "task_id": task_id,
                                                   "session_id": session_id, "turn_id": turn_id}, ensure_ascii=False)}
                        session_mgr.append_message(session_id, "user", req.user_input)
                        try:
                            from server.services.workflow_executor import get_workflow_executor
                            executor = get_workflow_executor()
                            wf_result = await executor.execute(
                                workflow=wf_match["workflow"],
                                user_input=req.user_input,
                                user_context=_user_context,
                            )
                            # Phase 2 HitL: workflow paused — tell the user clearly
                            # instead of silently falling through to "執行完成".
                            if wf_result.get("status") == "requires_approval":
                                _req_entries = [r for r in wf_result.get("results", []) if r.get("status") == "requires_approval"]
                                _skill_names = sorted({r.get("skill", "") for r in _req_entries if r.get("skill")})
                                final_text = (
                                    f"⏸️ 工作流「{_match_info['name']}」包含高風險技能"
                                    f"（{', '.join(_skill_names) or '未知'}），執行已暫停等待人工審批。\n\n"
                                    f"請至 **管理後台 → 審核中心** 批准後，工作流會自動繼續；"
                                    f"或點擊「拒絕」終止本次執行。"
                                )
                            else:
                                final_text = wf_result.get("final_output", "")
                                if not final_text:
                                    final_text = f"工作流 {_match_info['name']} 執行完成（{wf_result.get('blocks_executed', 0)} 個節點）"
                            # Phase 6: enrich match_info with source + run_id for promotion card
                            _match_info["source"] = wf_match["workflow"].get("source", "")
                            _match_info["run_id"] = wf_result.get("run_id", "")
                            session_mgr.append_message(session_id, "assistant", final_text)
                            task_registry.mark_completed(task_id, final_text=final_text, assistant_message_persisted=True)
                            # Phase 2 HitL: flag paused workflows so chat.js can start a
                            # polling loop to pick up the completion / rejection message
                            # that workflow_resume / approvals.reject will later append.
                            _payload = {"status": "success", "content": final_text,
                                        "workflow_match": _match_info,
                                        "task_id": task_id, "session_id": session_id,
                                        "turn_id": turn_id}
                            if wf_result.get("status") == "requires_approval":
                                _payload["workflow_status"] = "requires_approval"
                                _payload["run_id"] = wf_result.get("run_id", "")
                            yield {"data": json.dumps(_payload, ensure_ascii=False)}
                        except Exception as e:
                            logger.error(f"[WF-First] Execution failed: {e}")
                            error_msg = f"工作流執行失敗: {str(e)}"
                            session_mgr.append_message(session_id, "assistant", error_msg)
                            task_registry.mark_completed(task_id, final_text=error_msg, assistant_message_persisted=True)
                            yield {"data": json.dumps({"status": "error", "content": error_msg,
                                                       "task_id": task_id, "session_id": session_id}, ensure_ascii=False)}
                    return EventSourceResponse(_wf_event_generator(), media_type="text/event-stream")

                elif wf_mode == "confirm":
                    # Confirm mode: explainable message with match details + step preview
                    wf_name = _match_info["name"] or wf_match["workflow_id"]
                    # Build step preview from v2 steps[] or legacy blocks[]
                    _steps = wf_match["workflow"].get("steps") or []
                    if _steps:
                        _step_names = [s.get("skill_id", s.get("step_id", "?")) for s in _steps[:5]]
                    else:
                        _step_names = [
                            b.get("type", "?") for b in (wf_match["workflow"].get("blocks") or [])
                            if b.get("type") not in ("start", "end", "branch")
                        ][:5]
                    _steps_preview = " → ".join(_step_names) if _step_names else "(流程未設定)"

                    confirm_msg = (
                        f"🎯 找到匹配的工作流「**{wf_name}**」\n\n"
                        f"• 匹配方式：{wf_match['method']}（信心分數 {wf_conf:.2f}）\n"
                        f"• 原因：{wf_reason or '相似度達門檻'}\n"
                        f"• 流程預覽：{_steps_preview}\n\n"
                        f"是否要執行？請回覆「確認」或繼續提問。"
                    )
                    # Store pending workflow in session metadata
                    session_mgr.set_metadata(session_id, "pending_workflow", {
                        "workflow_id": wf_match["workflow_id"],
                        "workflow": wf_match["workflow"],
                        "user_input": req.user_input,
                    })
                    async def _wf_confirm_gen():
                        yield {"data": json.dumps({"status": "task_started", "task_id": task_id,
                                                   "session_id": session_id, "turn_id": turn_id}, ensure_ascii=False)}
                        session_mgr.append_message(session_id, "user", req.user_input)
                        session_mgr.append_message(session_id, "assistant", confirm_msg)
                        task_registry.mark_completed(task_id, final_text=confirm_msg, assistant_message_persisted=True)
                        yield {"data": json.dumps({"status": "success", "content": confirm_msg,
                                                   "workflow_confirm": True,
                                                   "task_id": task_id, "session_id": session_id,
                                                   "turn_id": turn_id}, ensure_ascii=False)}
                    return EventSourceResponse(_wf_confirm_gen(), media_type="text/event-stream")
        except Exception as wf_err:
            logger.warning(f"[WF-First] Match check failed (fallback to LLM): {wf_err}")

    # ── Check for pending workflow confirmation ──
    # Accept any short affirmative reply (好、好的、OK、可以、go、執行...).
    # Guard against false positives with length + positive/negative detection:
    #   - reply < 10 chars
    #   - contains at least one affirmative token
    #   - does NOT contain a negative token ("不", "取消", "算了"...)
    _reply = (req.user_input or "").strip().lower()
    _affirmative_tokens = (
        "確認", "確定", "確認執行", "執行", "好", "好的", "好喔", "可以",
        "是", "是的", "對", "沒錯", "ok", "okay", "yes", "y",
        "go", "開始", "跑", "run", "批准", "同意", "執行吧",
    )
    _negative_tokens = ("不", "否", "no", "取消", "算了", "等等", "稍等", "先不要", "別")
    _is_confirm = (
        _reply and len(_reply) <= 10
        and any(t in _reply for t in _affirmative_tokens)
        and not any(t in _reply for t in _negative_tokens)
    )
    # If user declines (sent a negative reply) and there's pending workflow,
    # clear it so the LLM handles the new turn normally.
    _is_decline = (
        _reply and len(_reply) <= 10
        and any(t in _reply for t in _negative_tokens)
    )
    if _is_decline:
        try:
            if session_mgr.get_metadata(session_id, "pending_workflow"):
                session_mgr.set_metadata(session_id, "pending_workflow", None)
                logger.info(f"[WF-First] User declined pending workflow (reply='{_reply}')")
        except Exception:
            pass

    if _is_confirm:
        try:
            pending_wf = session_mgr.get_metadata(session_id, "pending_workflow")
            if pending_wf:
                session_mgr.set_metadata(session_id, "pending_workflow", None)
                logger.info(f"[WF-First] User confirmed pending workflow: {pending_wf.get('workflow_id')} (reply='{_reply}')")
                async def _wf_exec_gen():
                    yield {"data": json.dumps({"status": "task_started", "task_id": task_id,
                                               "session_id": session_id, "turn_id": turn_id}, ensure_ascii=False)}
                    session_mgr.append_message(session_id, "user", req.user_input)
                    try:
                        from server.services.workflow_executor import get_workflow_executor
                        executor = get_workflow_executor()
                        wf_result = await executor.execute(
                            workflow=pending_wf["workflow"],
                            user_input=pending_wf.get("user_input", ""),
                            user_context=_user_context,
                        )
                        # Phase 2 HitL: workflow paused — show explicit waiting message
                        if wf_result.get("status") == "requires_approval":
                            _req_entries = [r for r in wf_result.get("results", []) if r.get("status") == "requires_approval"]
                            _skill_names = sorted({r.get("skill", "") for r in _req_entries if r.get("skill")})
                            _wf_name = pending_wf.get("name") or pending_wf.get("workflow_id") or "工作流"
                            final_text = (
                                f"⏸️ 工作流「{_wf_name}」包含高風險技能"
                                f"（{', '.join(_skill_names) or '未知'}），執行已暫停等待人工審批。\n\n"
                                f"請至 **管理後台 → 審核中心** 批准後，工作流會自動繼續；"
                                f"或點擊「拒絕」終止本次執行。"
                            )
                        else:
                            final_text = wf_result.get("final_output", "")
                            if not final_text:
                                final_text = f"工作流執行完成（{wf_result.get('blocks_executed', 0)} 個節點）"
                        session_mgr.append_message(session_id, "assistant", final_text)
                        task_registry.mark_completed(task_id, final_text=final_text, assistant_message_persisted=True)
                        # Phase 2 HitL: flag paused workflows (confirm-mode path)
                        _payload = {"status": "success", "content": final_text,
                                    "task_id": task_id, "session_id": session_id,
                                    "turn_id": turn_id}
                        if wf_result.get("status") == "requires_approval":
                            _payload["workflow_status"] = "requires_approval"
                            _payload["run_id"] = wf_result.get("run_id", "")
                        yield {"data": json.dumps(_payload, ensure_ascii=False)}
                    except Exception as e:
                        error_msg = f"工作流執行失敗: {str(e)}"
                        session_mgr.append_message(session_id, "assistant", error_msg)
                        task_registry.mark_completed(task_id, final_text=error_msg, assistant_message_persisted=True)
                        yield {"data": json.dumps({"status": "error", "content": error_msg,
                                                   "task_id": task_id, "session_id": session_id}, ensure_ascii=False)}
                return EventSourceResponse(_wf_exec_gen(), media_type="text/event-stream")
        except Exception:
            pass

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

    if _upload_handoff:
        _file_name = os.path.basename(active_original_file) if active_original_file else "uploaded file"
        _media_type = _detect_media_type_label(active_original_file)
        user_content += (
            "\n\n[Handoff Instruction]\n"
            f"User has just uploaded {_media_type} '{_file_name}'. "
            "First, acknowledge receipt and ask what they want to do next.\n"
            "Offer 2-4 concise options relevant to this media type.\n"
            "Do not execute any tool in this turn, and do not ask the user to provide absolute path again."
        )
        logger.info(
            f"[ChatCore Upload] handoff requested. session={session_id} file={active_original_file or 'N/A'}"
        )

    _meeting_todo_pipeline = (not _upload_handoff) and _needs_meeting_todo_pipeline(user_content, active_original_file)
    if _meeting_todo_pipeline:
        user_content += (
            "\n\n[流程指令：若使用者需求是把錄音整理成會議待辦，請按順序執行]\n"
            "1) 呼叫 mcp-transcribe（必填 file_path，使用系統通知或使用者提供的絕對路徑）\n"
            "2) 呼叫 mcp-meeting-analyzer（transcript 使用逐字稿全文）\n"
            "3) 呼叫 mcp-notion-crud（action=create_batch，並傳 cleaned_text + org_data_json）\n"
            "4) 最後回報上傳結果與建立筆數\n"
            "請勿跳步、請勿只做口頭摘要。"
        )
        logger.info(f"[ChatCore Pipeline] meeting_todo intent detected. session={session_id}")

    _tools_enabled = not _upload_handoff
    _max_tools = 0 if _upload_handoff else (15 if _meeting_todo_pipeline else 10)

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
                    tools_enabled=_tools_enabled,
                    max_tools=_max_tools,
                )
            ):
                # ── Cancellation check (user pressed stop) ──
                # Cheap (in-memory dict lookup); polled per chunk so we stop
                # within one streaming delta or one tool call.
                if task_registry.is_cancelled(task_id):
                    logger.info(f"[ChatCore] task={task_id} cancelled by user, breaking stream")
                    # Persist any partial text accumulated so far
                    try:
                        if final_content:
                            session_mgr.append_message(session_id, "assistant", final_content + "\n\n[已中止]")
                    except Exception:
                        pass
                    yield {
                        "data": json.dumps(
                            wrap_payload({
                                "status": "cancelled",
                                "content": final_content,
                                "message": "已中止",
                            }),
                            ensure_ascii=False,
                        )
                    }
                    break

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
                                        # Bridge tag appended as invisible suffix (zero-width chars wrap it)
                                        _hide = "\u200b\u200b\u200b"  # zero-width spaces to separate from content
                                        msg_user = f"[Web User]\n{req.user_input}{_hide}{tag_user}"
                                        msg_ai = f"[Web AgentK]\n{final}{_hide}{tag_ai}"
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
