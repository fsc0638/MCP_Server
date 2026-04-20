"""Native chat service implementation with task-scoped streaming state."""
import json
import logging
import os
import re
import uuid
from typing import AsyncGenerator

from fastapi import HTTPException
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
_TEXT_EXTRACTABLE_DOCUMENT_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
_INLINE_SELECTED_DOCUMENT_ACTIONS = {"meeting_notes", "transcript", "todo"}
_USER_DOCUMENT_ACTION_ALIASES = {
    "meeting_notes": "meeting_notes",
    "meeting": "meeting_notes",
    "minutes": "meeting_notes",
    "transcript": "transcript",
    "transcribe": "transcript",
    "todo": "todo",
    "tasks": "todo",
}
_ALLOWED_CHAT_LANGUAGES = {
    "繁體中文",
    "简体中文",
    "English",
    "日本語",
    "한국어",
    "自動偵測",
}
_LANGUAGE_CANONICAL_MAP = {
    "繁體中文": "繁體中文",
    "繁体中文": "繁體中文",
    "traditional chinese": "繁體中文",
    "zh-tw": "繁體中文",
    "zh-hant": "繁體中文",
    "简体中文": "简体中文",
    "simplified chinese": "简体中文",
    "zh-cn": "简体中文",
    "zh-hans": "简体中文",
    "english": "English",
    "en": "English",
    "日本語": "日本語",
    "japanese": "日本語",
    "ja": "日本語",
    "한국어": "한국어",
    "korean": "한국어",
    "ko": "한국어",
    "自動偵測": "自動偵測",
    "自动侦测": "自動偵測",
    "auto": "自動偵測",
    "auto-detect": "自動偵測",
}
_DEFAULT_CHAT_LANGUAGE = "繁體中文"


def _canonicalize_language(value: str | None) -> str | None:
    if not value:
        return None
    token = str(value).strip()
    if not token:
        return None
    if token in _ALLOWED_CHAT_LANGUAGES:
        return token
    mapped = _LANGUAGE_CANONICAL_MAP.get(token.lower())
    if mapped in _ALLOWED_CHAT_LANGUAGES:
        return mapped
    return None


def _extract_profile_language(user_context: dict | None) -> str | None:
    if not isinstance(user_context, dict):
        return None
    prefs = user_context.get("preferences")
    if isinstance(prefs, dict):
        lang = _canonicalize_language(prefs.get("language"))
        if lang:
            return lang
    return _canonicalize_language(user_context.get("language"))


def _resolve_response_language(requested_language: str | None, user_context: dict | None) -> tuple[str, str]:
    requested = _canonicalize_language(requested_language)
    if requested and requested != "自動偵測":
        return requested, "request"

    profile = _extract_profile_language(user_context)
    if profile and profile != "自動偵測":
        return profile, "profile"

    return _DEFAULT_CHAT_LANGUAGE, "default"


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


def _infer_user_document_selection_from_chat(req: ChatRequest) -> None:
    if (req.user_document_id or "").strip():
        return

    raw_user_id = (req.user_id or "").strip()
    if not raw_user_id:
        return

    try:
        from server.services.user_document_chat import resolve_document_task_request
        from server.services.user_document_service import sanitize_user_key, user_document_service

        user_key = sanitize_user_key(raw_user_id)
        inferred = resolve_document_task_request(
            req.user_input,
            user_document_service.list_documents(user_key),
        )
        if not inferred:
            return

        document = inferred.get("document") or {}
        doc_id = (document.get("doc_id") or "").strip()
        if not doc_id:
            return

        req.user_document_id = doc_id
        if not (req.user_document_action or "").strip():
            req.user_document_action = inferred.get("action")
        logger.info(
            "[ChatCore] Inferred user document selection from chat. doc_id=%s action=%s",
            doc_id,
            req.user_document_action,
        )
    except Exception as exc:
        logger.warning(f"[ChatCore] Failed to infer user document selection: {exc}")


def _normalize_user_document_action(action: str | None) -> str | None:
    token = (action or "").strip().lower()
    if not token:
        return None
    return _USER_DOCUMENT_ACTION_ALIASES.get(token)


def _build_selected_user_document_content_block(
    doc_name: str,
    doc_text: str,
    max_chars: int = 24000,
) -> str:
    content = (doc_text or "").strip()
    if not content:
        return ""

    snippet = content
    truncated = False
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars].rstrip()
        truncated = True

    block = (
        "\n\n[Selected Document Content]\n"
        f"File: {doc_name}\n"
        "Use the extracted text below as the primary source for this request.\n\n"
        f"{snippet}"
    )
    if truncated:
        block += f"\n\n[文件內容較長，本輪先附上前 {max_chars} 字。]"
    return block


def _build_user_document_action_instruction(
    action: str | None,
    doc_name: str,
    media_type: str,
) -> str:
    base = (
        "\n\n[Document Center Selection]\n"
        f"User selected '{doc_name}' from document center as the input {media_type} for this turn.\n"
        "Use existing file context directly, and do not ask the user to upload again or provide an absolute path.\n"
    )
    if action == "meeting_notes":
        return (
            base
            + "Target output: meeting notes with agenda, key decisions, open issues, and action items.\n"
            + "If this is audio, transcribe first before summarizing.\n"
            + "Preferred tool order when available: mcp-transcribe -> mcp-meeting-analyzer."
        )
    if action == "transcript":
        return (
            base
            + "Target output: a clean transcript with speaker separation and readable paragraph breaks.\n"
            + "If timestamps are available, include them.\n"
            + "Preferred tool when available: mcp-transcribe."
        )
    if action == "todo":
        return (
            base
            + "Target output: TODO/task list extracted from the content.\n"
            + "If this is audio, run transcript -> meeting analysis -> TODO extraction flow.\n"
            + "Preferred tool order when available: mcp-transcribe -> mcp-meeting-analyzer -> mcp-notion-crud."
        )
    return base.strip()


def _resolve_selected_user_document(req: ChatRequest) -> tuple[str, dict, str | None]:
    doc_id = (req.user_document_id or "").strip()
    if not doc_id:
        raise HTTPException(status_code=400, detail="missing_user_document_id")

    raw_user_id = (req.user_id or "").strip()
    if not raw_user_id:
        raise HTTPException(status_code=401, detail="user_document_requires_login")

    from server.services.user_document_service import (
        ExpiredDocumentError,
        sanitize_user_key,
        user_document_service,
    )

    user_key = sanitize_user_key(raw_user_id)
    try:
        document = user_document_service.get_document(user_key, doc_id)
        path = user_document_service.get_document_path(user_key, doc_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ExpiredDocumentError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Document file missing: {doc_id}")

    return str(path), document, _normalize_user_document_action(req.user_document_action)


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

    from server.services.runtime import get_universal_system_prompt

    session_mgr = get_session_manager()
    session_id = req.session_id or "default"
    try:
        from server.services.id_utils import validate_session_id

        session_id = validate_session_id(session_id)
    except Exception:
        session_id = "default"

    from server.services.identity_context import resolve_identity_context

    resolved_user_id, _user_context = resolve_identity_context(
        session_id=session_id,
        explicit_user_id=(req.user_id or "").strip(),
        session_mgr=session_mgr,
        persist_binding=True,
        allow_session_binding=True,
    )
    if resolved_user_id:
        req.user_id = resolved_user_id

    adapter = create_adapter(
        provider=provider,
        uma=uma,
        model=req.model,
        user_context=_user_context,
        api_base=req.api_base,
        api_key=req.api_key,
    )
    if not adapter.is_available:
        raise HTTPException(
            status_code=503,
            detail=f"{provider.capitalize()} adapter is not available",
        )

    _infer_user_document_selection_from_chat(req)

    selected_user_doc_path = ""
    selected_user_doc: dict = {}
    selected_user_doc_action = None
    if (req.user_document_id or "").strip():
        selected_user_doc_path, selected_user_doc, selected_user_doc_action = _resolve_selected_user_document(req)

    # Register file context for downstream tools (WebUI parity with LINE pipeline).
    active_original_file = None
    for candidate in (
        selected_user_doc_path or None,
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

    def _make_immediate_success_response(final_text: str, extra_payload: dict | None = None):
        async def _event_generator():
            yield {
                "data": json.dumps(
                    {"status": "task_started", "task_id": task_id, "session_id": session_id, "turn_id": turn_id},
                    ensure_ascii=False,
                )
            }
            session_mgr.append_message(session_id, "user", req.user_input)
            session_mgr.append_message(session_id, "assistant", final_text)
            task_registry.mark_completed(task_id, final_text=final_text, assistant_message_persisted=True)
            success_payload = {
                "status": "success",
                "content": final_text,
                "task_id": task_id,
                "session_id": session_id,
                "turn_id": turn_id,
            }
            if extra_payload:
                success_payload.update(extra_payload)
            yield {
                "data": json.dumps(
                    success_payload,
                    ensure_ascii=False,
                )
            }

        return EventSourceResponse(_event_generator(), media_type="text/event-stream")

    try:
        from server.services.user_document_chat import (
            PENDING_CANDIDATES_KEY,
            PENDING_DOCUMENT_KEY,
            resolve_document_turn,
        )
        from server.services.user_document_service import sanitize_user_key, user_document_service

        raw_user_id = (req.user_id or "").strip()
        if raw_user_id:
            user_doc_key = sanitize_user_key(raw_user_id)
            documents = user_document_service.list_documents(user_doc_key)
            documents_by_id = {doc.get("doc_id"): doc for doc in documents}

            # If this turn explicitly targets a Document Center file for downstream actions
            # (e.g. transcript / todo pipeline), skip deterministic preview/list interception.
            explicit_doc_action_turn = bool((req.user_document_id or "").strip())
            if explicit_doc_action_turn:
                session_mgr.set_metadata(session_id, PENDING_DOCUMENT_KEY, None)
                session_mgr.set_metadata(session_id, PENDING_CANDIDATES_KEY, [])
                raise RuntimeError("skip_document_turn_interception")

            pending_doc_meta = session_mgr.get_metadata(session_id, PENDING_DOCUMENT_KEY, default=None)
            pending_doc_id = pending_doc_meta.get("doc_id") if isinstance(pending_doc_meta, dict) else ""
            pending_document = documents_by_id.get(pending_doc_id) if pending_doc_id else None

            pending_candidate_ids = session_mgr.get_metadata(session_id, PENDING_CANDIDATES_KEY, default=[]) or []
            pending_candidates = [documents_by_id[doc_id] for doc_id in pending_candidate_ids if doc_id in documents_by_id]

            doc_turn = resolve_document_turn(
                req.user_input,
                documents,
                pending_document=pending_document,
                pending_candidates=pending_candidates,
            )
            if doc_turn:
                if doc_turn.get("clear_pending") or doc_turn.get("clear_pending_document"):
                    session_mgr.set_metadata(session_id, PENDING_DOCUMENT_KEY, None)
                if doc_turn.get("clear_pending") or doc_turn.get("clear_pending_candidates"):
                    session_mgr.set_metadata(session_id, PENDING_CANDIDATES_KEY, [])

                selected_doc = doc_turn.get("set_pending_document")
                if selected_doc:
                    session_mgr.set_metadata(
                        session_id,
                        PENDING_DOCUMENT_KEY,
                        {
                            "doc_id": selected_doc.get("doc_id"),
                            "display_name": selected_doc.get("display_name") or selected_doc.get("original_filename"),
                        },
                    )

                selected_candidates = doc_turn.get("set_pending_candidates")
                if selected_candidates:
                    session_mgr.set_metadata(
                        session_id,
                        PENDING_CANDIDATES_KEY,
                        [doc.get("doc_id") for doc in selected_candidates if doc.get("doc_id")],
                    )

                action = doc_turn.get("action", "")
                final_text = doc_turn.get("message", "")
                document = doc_turn.get("document") or {}
                doc_id = document.get("doc_id", "")
                doc_name = document.get("display_name") or document.get("original_filename") or doc_id or "文件"
                response_meta = None

                if action == "show_preview" and doc_id:
                    final_text = (
                        f"已直接為你開啟「{doc_name}」的服務內預覽。\n"
                        f"[在服務內預覽](/api/user-documents/{doc_id}/viewer)\n"
                        f"[下載原檔](/api/user-documents/{doc_id}/file?disposition=attachment)"
                    )
                    response_meta = {
                        "document_action": {
                            "type": "open_preview",
                            "doc_id": doc_id,
                            "display_name": doc_name,
                        }
                    }
                elif action == "show_link" and doc_id:
                    final_text = (
                        f"以下是「{doc_name}」可直接開啟的連結：\n"
                        f"[服務內預覽](/api/user-documents/{doc_id}/viewer)\n"
                        f"[下載原檔](/api/user-documents/{doc_id}/file?disposition=attachment)"
                    )
                elif action == "show_text" and doc_id:
                    _, doc_text = user_document_service.get_text_content(user_doc_key, doc_id)
                    snippet = (doc_text or "").strip()
                    if len(snippet) > 8000:
                        snippet = snippet[:8000].rstrip() + "\n\n[內容較長，先顯示前 8000 字。]"
                    final_text = (
                        f"以下是「{doc_name}」的文字內容：\n\n"
                        f"{snippet or '目前沒有可讀取的文字內容。'}"
                    )

                return _make_immediate_success_response(final_text, extra_payload=response_meta)
    except Exception as doc_turn_error:
        if str(doc_turn_error) == "skip_document_turn_interception":
            logger.info("[DocTurn] bypassed due to explicit user_document_id action turn")
        else:
            logger.warning(f"[DocTurn] Fallback to normal chat due to error: {doc_turn_error}")

    requested_language = req.language
    profile_language = _extract_profile_language(_user_context)
    resolved_language, language_source = _resolve_response_language(requested_language, _user_context)
    req.language = resolved_language

    logger.info(
        "Chat Request: [Model: %s] [Lang: %s] [Detail: %s] [LangReq: %s] [LangProfile: %s] [LangSource: %s]",
        req.model,
        resolved_language,
        req.detail_level,
        requested_language,
        profile_language or "-",
        language_source,
    )
    dynamic_prompt = get_universal_system_prompt(
        platform="web",
        language=resolved_language,
        detail_level=req.detail_level or "詳細",
    )
    logger.info(f"Generated Dynamic Prompt (Sample): {dynamic_prompt[:100]}... [MID] ...{dynamic_prompt[-100:]}")
    history = session_mgr.get_or_create_conversation(session_id, dynamic_prompt)
    session_mgr._update_system_prompt(session_id, dynamic_prompt)

    user_content = req.user_input
    if selected_user_doc:
        _doc_name = (
            selected_user_doc.get("display_name")
            or selected_user_doc.get("original_filename")
            or selected_user_doc.get("doc_id")
            or "document"
        )
        _media_type = _detect_media_type_label(active_original_file or selected_user_doc_path)
        user_content += _build_user_document_action_instruction(
            selected_user_doc_action,
            _doc_name,
            _media_type,
        )
        if (
            selected_user_doc_action in _INLINE_SELECTED_DOCUMENT_ACTIONS
            and (selected_user_doc.get("extension") or "").lower() in _TEXT_EXTRACTABLE_DOCUMENT_EXTENSIONS
        ):
            try:
                from server.services.user_document_service import sanitize_user_key, user_document_service

                user_doc_key = sanitize_user_key((req.user_id or "").strip())
                _, selected_doc_text = user_document_service.get_text_content(
                    user_doc_key,
                    selected_user_doc.get("doc_id", ""),
                )
                user_content += _build_selected_user_document_content_block(
                    _doc_name,
                    selected_doc_text,
                )
            except Exception as exc:
                logger.warning(
                    "[ChatCore] Failed to inline selected document content for %s: %s",
                    _doc_name,
                    exc,
                )

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
                logger.info(f"[WF-First] Matched: {wf_match['workflow_id']} "
                            f"(score={wf_match['score']:.2f}, method={wf_match['method']}, mode={wf_mode})")

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
                            final_text = wf_result.get("final_output", "")
                            if not final_text:
                                final_text = f"工作流 {wf_match['workflow'].get('name', '')} 執行完成（{wf_result.get('blocks_executed', 0)} 個節點）"
                            session_mgr.append_message(session_id, "assistant", final_text)
                            task_registry.mark_completed(task_id, final_text=final_text, assistant_message_persisted=True)
                            yield {"data": json.dumps({"status": "success", "content": final_text,
                                                       "workflow_match": {"id": wf_match["workflow_id"],
                                                                         "method": wf_match["method"],
                                                                         "score": wf_match["score"]},
                                                       "task_id": task_id, "session_id": session_id,
                                                       "turn_id": turn_id}, ensure_ascii=False)}
                        except Exception as e:
                            logger.error(f"[WF-First] Execution failed: {e}")
                            error_msg = f"工作流執行失敗: {str(e)}"
                            session_mgr.append_message(session_id, "assistant", error_msg)
                            task_registry.mark_completed(task_id, final_text=error_msg, assistant_message_persisted=True)
                            yield {"data": json.dumps({"status": "error", "content": error_msg,
                                                       "task_id": task_id, "session_id": session_id}, ensure_ascii=False)}
                    return EventSourceResponse(_wf_event_generator(), media_type="text/event-stream")

                elif wf_mode == "confirm":
                    # Confirm mode: tell user a workflow was matched, ask for confirmation
                    wf_name = wf_match["workflow"].get("name", wf_match["workflow_id"])
                    confirm_msg = f"找到匹配的工作流「{wf_name}」（匹配方式：{wf_match['method']}，分數：{wf_match['score']:.2f}）。\n\n是否要執行此工作流？請回覆「確認」或繼續提問。"
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
    if req.user_input.strip() in ("確認", "確定", "執行", "是", "yes", "confirm"):
        try:
            pending_wf = session_mgr.get_metadata(session_id, "pending_workflow")
            if pending_wf:
                session_mgr.set_metadata(session_id, "pending_workflow", None)
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
                        final_text = wf_result.get("final_output", "")
                        if not final_text:
                            final_text = f"工作流執行完成（{wf_result.get('blocks_executed', 0)} 個節點）"
                        session_mgr.append_message(session_id, "assistant", final_text)
                        task_registry.mark_completed(task_id, final_text=final_text, assistant_message_persisted=True)
                        yield {"data": json.dumps({"status": "success", "content": final_text,
                                                   "task_id": task_id, "session_id": session_id,
                                                   "turn_id": turn_id}, ensure_ascii=False)}
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
                    attached_file=active_original_file or req.attached_file,
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
