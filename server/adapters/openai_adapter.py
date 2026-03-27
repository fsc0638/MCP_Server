"""
OpenAI Adapter
Handles communication with OpenAI GPT models.
Provides two modes:
  - simple_chat(): Pure LLM conversation (no tools, for Agent Console chat panel)
  - chat(): Full tool-calling agent mode (for future use)
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("MCP_Server.Adapter.OpenAI")

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("openai package not installed. OpenAI adapter will be unavailable.")


class OpenAIAdapter:
    """Adapter for OpenAI GPT models with tool calling support."""

    def __init__(self, uma, model: Optional[str] = None, api_base: Optional[str] = None, api_key: Optional[str] = None):
        self.uma = uma
        self.client = None

        # 1. Resolve Model
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o")

        # 1b. Resolve Token Limits (configurable via env)
        self.max_output_tokens = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "16384"))
        
        # 2. Resolve Credentials / Endpoint
        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        resolved_base = api_base or os.getenv("OPENAI_BASE_URL")

        if OPENAI_AVAILABLE:
            if resolved_key or resolved_base:
                # Initialize OpenAI client with explicit kwargs.  If None is passed, OpenAI SDK falls back to internal env var logic.
                kwargs = {}
                if resolved_key:
                    kwargs["api_key"] = resolved_key
                else:
                    # Provide dummy key if base URL is used but no key (common for Local models like Ollama)
                    kwargs["api_key"] = "dummy_key_for_local_endpoint"
                    
                if resolved_base:
                    kwargs["base_url"] = resolved_base
                    
                self.client = OpenAI(**kwargs)
                logger.info(f"OpenAI adapter initialized with model: {self.model} (Base URL: {resolved_base or 'Default API'})")
            else:
                logger.warning("OPENAI_API_KEY or OPENAI_BASE_URL not set. OpenAI adapter disabled.")

    @property
    def is_available(self) -> bool:
        return self.client is not None

    def _extract_text(self, content: Any) -> str:
        """Extracts plain text from either string or OpenAI-style multi-modal content list."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    text_parts.append(part)
            return " ".join(text_parts)
        return str(content)

    def get_tools(self, user_query: str = "", max_tools: int = 10) -> List[Dict[str, Any]]:
        """
        Fetches tools from UMA (name + description only) and converts to Responses API format.
        Full SKILL.md + references are injected on-demand via execute_tool_call().
        D-06: Filtered/Dynamic injection — always filters when user_query is provided.
        """
        if not self.is_available:
            return []

        from server.adapters import select_relevant_tools

        all_tools = self.uma.get_tools_for_model("openai")

        if user_query:
            all_tools = select_relevant_tools(user_query, all_tools, max_tools)

        # P-03: Convert Chat Completions format → Responses API format (flatten function wrapper)
        responses_api_tools = []
        for t in all_tools:
            if t.get("type") == "function" and "function" in t:
                fn = t["function"]
                responses_api_tools.append({
                    "type": "function",
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}})
                })
        return responses_api_tools

    def _handle_attached_file(self, attached_file: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Multimodal Parity: Read file and convert to Base64 for OpenAI Vision.
        Currently supports images.
        """
        if not attached_file:
            return None

        import mimetypes
        import base64
        
        mime_type, _ = mimetypes.guess_type(attached_file)
        if not mime_type:
            return None

        if mime_type.startswith("image/"):
            try:
                with open(attached_file, "rb") as f:
                    base64_img = base64.b64encode(f.read()).decode('utf-8')
                return {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{base64_img}"
                }
            except Exception as e:
                logger.error(f"Failed to read and encode image for OpenAI: {e}")
                return None
        return None

    def chat(self, messages: Any, user_query: Optional[str] = None, session_id: Optional[str] = None, attached_file: Optional[str] = None, temperature: float = 0.7, tools_enabled: bool = True, **kwargs) -> Dict[str, Any]:
        """
        Send a chat completion request with tool calling support.
        P-03 Architecture: Uses the stateful Responses API (`client.responses.create`).
        """
        if not self.is_available:
            return {"status": "error", "message": "OpenAI adapter is not available"}

        from server.dependencies.session import get_session_manager
        _session_mgr = get_session_manager()

        # 1. State Resolution (DISABLED for better reliability with dynamic instructions)
        prev_response_id = None # Force stateless for now to ensure system prompt overrides
        
        # 2. Input Resolution
        # Filter out custom metadata that OpenAI doesn't support (like created_at)
        input_payload = []
        for msg in messages:
            clean_msg = {k: v for k, v in msg.items() if k != "created_at"}
            input_payload.append(clean_msg)
        
        # Log roles for debugging
        roles = [m.get("role") for m in input_payload]
        logger.info(f"[OpenAI Adapter] Turn start. Message Sequence: {roles}")

        # Multimodal Vision (NotebookLM Style)
        visual_docs = kwargs.get("visual_docs", [])
        visual_docs_display_names = kwargs.get("visual_docs_display_names", {})
        import os
        all_visual_parts = []
        
        # 1. Attached file (Legacy)
        img_part = self._handle_attached_file(attached_file)
        if img_part:
            all_visual_parts.append(img_part)
            
        # 2. Selected Docs (New: visual_docs)
        for doc_path in visual_docs:
            display_name = visual_docs_display_names.get(doc_path, os.path.basename(doc_path))
            all_visual_parts.append({"type": "input_text", "text": f"[圖片名稱: {display_name}]"})
            res = self._handle_attached_file(doc_path)
            if res:
                all_visual_parts.append(res)

        if all_visual_parts:
            # Find the last user message in the input_payload to attach the images
            for i in range(len(input_payload) - 1, -1, -1):
                if input_payload[i].get("role") == "user":
                    orig_content = input_payload[i]["content"]
                    if isinstance(orig_content, str):
                        input_payload[i]["content"] = [{"type": "input_text", "text": orig_content}] + all_visual_parts
                    elif isinstance(orig_content, list):
                        new_content = []
                        for c in orig_content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                new_content.append({"type": "input_text", "text": c.get("text")})
                            else:
                                new_content.append(c)
                        input_payload[i]["content"] = new_content + all_visual_parts
                    break

        _max_tools = kwargs.get("max_tools", 10)
        tools = self.get_tools(user_query=user_query, max_tools=_max_tools) if tools_enabled else []
        tool_calls_made = 0
        MAX_ITERATIONS = 10  # Safety cap

        # Detect file-creation intent → force tool use via tool_choice
        # NOTE: Only force when user has specified a concrete format.
        #       Vague requests (e.g. "做一份報告") should NOT force tool use,
        #       so the LLM can propose choices via [CHOICES] protocol first.
        _file_creation_keywords = [
            "製作", "建立", "產生", "生成", "建一個", "做一個", "寫一個",
            "存檔", "輸出檔", "下載", "匯出",
            "create", "generate", "make", "write", "export", "save as",
        ]
        # Format indicators: if user already specified a format, force tool use
        _format_specified_keywords = [
            "pdf", "docx", "doc", "word", "xlsx", "excel", "csv", "txt",
            "pptx", "ppt", "md", "markdown", "json", "html",
            ".pdf", ".docx", ".txt", ".md", ".xlsx", ".csv", ".pptx",
        ]
        has_creation_intent = (
            tools_enabled
            and tools
            and user_query
            and any(kw in user_query for kw in _file_creation_keywords)
        )
        _query_lower = user_query.lower() if user_query else ""
        _matched_formats = [kw for kw in _format_specified_keywords if kw in _query_lower]
        has_format_specified = bool(_matched_formats)
        # Deduplicate format families (e.g. "pdf"+".pdf" = 1 format, "pdf"+"docx" = 2)
        _format_families = set()
        for kw in _matched_formats:
            _clean = kw.lstrip(".").replace("doc", "docx").replace("word", "docx").replace("excel", "xlsx").replace("ppt", "pptx")
            _format_families.add(_clean)
        _multi_format = len(_format_families) >= 2

        # Only force tool use when BOTH creation intent AND format are specified
        # BUT: if user requests ≥2 different formats, do NOT force — let LLM plan sequentially
        force_tool_use = has_creation_intent and has_format_specified and not _multi_format

        logger.info(
            f"[OpenAI Adapter] Tools: {len(tools)} injected "
            f"({[t.get('name') for t in tools]}), "
            f"force_tool_use={force_tool_use}"
        )

        # We will track the latest response id generated in this multi-round loop
        current_response_id = prev_response_id
        # Guard: prevent semantic/code skills from being called repeatedly (infinite loop)
        _knowledge_guide_skills_called = set()

        try:
            import time
            for _ in range(MAX_ITERATIONS):
                MAX_RETRIES = 3
                last_error = None
                response = None

                for attempt in range(MAX_RETRIES + 1):
                    try:
                        kwargs_req = {
                            "model": self.model,
                            "input": input_payload,
                            "stream": True,
                            "temperature": temperature,
                            "max_output_tokens": self.max_output_tokens,
                            "truncation": "auto",  # Auto-truncate input when approaching context limit
                        }
                        if current_response_id:
                            kwargs_req["previous_response_id"] = current_response_id
                        if tools:
                            kwargs_req["tools"] = tools
                            # Force model to call a tool for file-creation queries
                            # (only on first iteration; after tool results, let model decide)
                            if force_tool_use and tool_calls_made == 0:
                                kwargs_req["tool_choice"] = "required"

                        # --- DEBUG LOGGING ---
                        import copy
                        debug_payload = copy.deepcopy(kwargs_req)
                        try:
                            for inp in debug_payload.get("input", []):
                                if isinstance(inp.get("content"), list):
                                    for c in inp["content"]:
                                        if c.get("type") == "input_image" and "image_url" in c:
                                            c["image_url"] = c["image_url"][:30] + "...[truncated]..."
                            logger.info(f"[OpenAI Adapter] Turn start. Payload: {debug_payload}")
                        except: pass
                        
                        response = self.client.responses.create(**kwargs_req)
                        
                        full_content = ""
                        tool_calls_dict = {}
                        _turn_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                        _turn_start_ms = int(time.time() * 1000)

                        for chunk in response:
                            ctype = chunk.type
                            if ctype == "response.created" or ctype == "response.in_progress":
                                current_response_id = chunk.response.id
                            elif ctype == "response.completed":
                                # Capture token usage from completed response
                                _resp = getattr(chunk, "response", None)
                                if _resp:
                                    _u = getattr(_resp, "usage", None)
                                    if _u:
                                        _turn_usage["input_tokens"] = getattr(_u, "input_tokens", 0)
                                        _turn_usage["output_tokens"] = getattr(_u, "output_tokens", 0)
                                        _turn_usage["total_tokens"] = getattr(_u, "total_tokens", 0)
                                        logger.info(f"[OpenAI D1] Usage: in={_turn_usage['input_tokens']} out={_turn_usage['output_tokens']} total={_turn_usage['total_tokens']}")
                            elif ctype == "response.output_text.delta":
                                text = chunk.delta
                                full_content += text
                                yield {"status": "streaming", "content": text}
                            elif ctype == "response.function_call_arguments.delta":
                                item_id = chunk.item_id
                                if item_id not in tool_calls_dict:
                                    tool_calls_dict[item_id] = {"arguments": "", "name": "", "call_id": item_id}
                                tool_calls_dict[item_id]["arguments"] += chunk.delta
                            elif ctype == "response.output_item.done":
                                item = chunk.item
                                if getattr(item, 'type', None) == 'function_call':
                                    item_id = item.id
                                    if item_id not in tool_calls_dict:
                                        tool_calls_dict[item_id] = {"arguments": "", "name": item.name, "call_id": getattr(item, 'call_id', item.id)}
                                    tool_calls_dict[item_id]["name"] = item.name or tool_calls_dict[item_id]["name"]
                                    tool_calls_dict[item_id]["call_id"] = getattr(item, 'call_id', item.id)
                                    if hasattr(item, 'arguments') and item.arguments:
                                        tool_calls_dict[item_id]["arguments"] = item.arguments
                        
                        break # Success, exit retry loop
                        
                    except Exception as e:
                        last_error = e
                        err_str = str(e)
                        _is_rate_limit = "Rate limit reached" in err_str or "rate_limit" in err_str.lower()
                        _is_server_error = any(code in err_str for code in ["500", "502", "503", "server_error", "overloaded"])
                        if (_is_rate_limit or _is_server_error) and attempt < MAX_RETRIES:
                            if _is_rate_limit:
                                import re as _re
                                wait_match = _re.search(r'try again in (\d+(?:\.\d+)?)\s*s', err_str)
                                wait_sec = float(wait_match.group(1)) + 2 if wait_match else 20
                                wait_sec = min(wait_sec, 60)
                            else:
                                wait_sec = 3 * (attempt + 1)  # 3s, 6s, 9s for server errors
                            _reason = "流量已滿" if _is_rate_limit else "伺服器忙碌"
                            logger.warning(f"[OpenAI Adapter] {'Rate limit' if _is_rate_limit else 'Server error'} hit. Wait {wait_sec:.0f}s (Attempt {attempt+1}/{MAX_RETRIES})...")
                            yield {"status": "streaming", "content": f"\n⏳ {_reason}，{wait_sec:.0f} 秒後自動重試（第 {attempt+1} 次）...\n"}
                            time.sleep(wait_sec)
                            continue
                        raise e  # Fatal or retries exhausted

                if tool_calls_dict:
                    # ── Serial execution: process only ONE tool call per round ──
                    # If the LLM emits multiple function_calls in a single response
                    # (e.g. "produce PDF and DOCX"), execute only the first one now
                    # and defer the rest to the next iteration. This reduces API
                    # complexity per round and avoids OpenAI 500 errors.
                    _tc_items = list(tool_calls_dict.items())
                    if len(_tc_items) > 1:
                        logger.info(f"[OpenAI Adapter] {len(_tc_items)} tool calls in one round — serialising: execute first, defer rest")
                    _deferred_calls = _tc_items[1:]  # Will be sent as stub results

                    tool_results = []
                    for item_id, tc_data in _tc_items[:1]:  # Execute only the first
                        fn_name = tc_data.get("name")
                        fn_args_str = tc_data.get("arguments", "{}")
                        call_id = tc_data.get("call_id") or item_id
                        
                        import json
                        try:
                            fn_args = json.loads(fn_args_str) if fn_args_str else {}
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse tool args: {fn_args_str}")
                            fn_args = {}

                        # ── Original file injection for ALL skills ──
                        # When a file was previously uploaded, inject its path and
                        # content so any skill (executable or semantic) can access it.
                        _orig_path = getattr(self, "_original_file_path", None)
                        _orig_date = getattr(self, "_original_file_date", None)
                        if _orig_path:
                            import os as _os
                            if _os.path.exists(_orig_path):
                                # Always inject file path for all skills
                                fn_args.setdefault("_original_file_path", _orig_path)
                                fn_args.setdefault("_original_filename", _session_mgr.get_metadata(session_id, "last_original_filename") or "")
                                if _orig_date:
                                    fn_args.setdefault("meeting_date", _orig_date)
                                # For meeting-to-notion: inject full transcript text
                                if fn_name == "mcp-meeting-to-notion":
                                    try:
                                        with open(_orig_path, "r", encoding="utf-8") as _f:
                                            _original_text = _f.read()
                                        if _original_text and len(_original_text) > len(fn_args.get("transcript", "")):
                                            fn_args["transcript"] = _original_text
                                            logger.info(f"[Adapter] Injected original file ({len(_original_text)} chars) into mcp-meeting-to-notion transcript")
                                    except Exception as _e:
                                        logger.warning(f"[Adapter] Failed to inject original file: {_e}")

                        # Inject session context for schedule-manager skill
                        if fn_name == "mcp-schedule-manager" and session_id:
                            os.environ["SESSION_ID"] = session_id
                            # Derive chat_id: line_U09e... → U09e... / line_group_Cf8ce... → Cf8ce...
                            _sid = session_id
                            if _sid.startswith("line_group_"):
                                os.environ["CHAT_ID"] = _sid[len("line_group_"):]
                            elif _sid.startswith("line_"):
                                os.environ["CHAT_ID"] = _sid[len("line_"):]
                            else:
                                os.environ["CHAT_ID"] = _sid
                            # Pass original user query so schedule-manager can store it
                            if user_query:
                                os.environ["USER_ORIGINAL_REQUEST"] = user_query

                        # Guard: if this semantic/code skill was already called, skip to prevent infinite loop
                        if fn_name in _knowledge_guide_skills_called:
                            logger.warning(f"[Adapter] Blocked repeated call to knowledge_guide skill: {fn_name}")
                            result = {
                                "status": "error",
                                "message": f"技能 '{fn_name}' 已在本輪執行過，其指南已提供。請根據指南內容直接完成任務，不要重複呼叫同一技能。"
                            }
                        else:
                            logger.info(f"Tool call: {fn_name}({fn_args})")
                            yield {"status": "streaming", "content": f"\n\n⚙️ 執行技能: `{fn_name}`\n"}
                            result = self.uma.execute_tool_call(fn_name, fn_args)
                            # Track knowledge_guide skills to prevent re-invocation
                            if isinstance(result, dict) and result.get("type") == "knowledge_guide":
                                _knowledge_guide_skills_called.add(fn_name)

                        if result.get("status") == "requires_approval":
                            yield {
                                "status": "requires_approval",
                                "tool_name": fn_name,
                                "risk_description": result.get("risk_description", "High-risk operation"),
                                "pending_args": fn_args
                            }
                            return

                        # ── Truncate tool output to prevent token explosion ──
                        result_str = json.dumps(result, ensure_ascii=False)
                        _MAX_TOOL_OUTPUT_CHARS = 8000  # ~2K tokens per result
                        if len(result_str) > _MAX_TOOL_OUTPUT_CHARS:
                            result_str = result_str[:_MAX_TOOL_OUTPUT_CHARS] + '..."（結果已截斷，請根據已有資料繼續執行任務）"}'
                            logger.info(f"[Adapter] Truncated tool output from {len(json.dumps(result, ensure_ascii=False))} to {_MAX_TOOL_OUTPUT_CHARS} chars")

                        tool_results.append({
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": result_str
                        })
                        tool_calls_made += 1

                        # ── Phase D1: Token Usage Tracking ─────────────────────
                        try:
                            from server.services.token_tracker import TokenTracker
                            from pathlib import Path as _Path
                            _tracker = TokenTracker(str(_Path(os.getcwd())))
                            _skill_internal = 0
                            if isinstance(result, dict) and "_usage" in result:
                                _skill_internal = result["_usage"].get("skill_total_tokens", 0)
                            # Derive context from session_id
                            _sid = session_id or ""
                            _d1_chat_type = "group" if "group" in _sid else "personal"
                            _d1_duration = int(time.time() * 1000) - _turn_start_ms
                            _tracker.record_usage(
                                session_id=_sid,
                                chat_type=_d1_chat_type,
                                skill=fn_name,
                                model=self.model,
                                input_tokens=_turn_usage.get("input_tokens", 0),
                                output_tokens=_turn_usage.get("output_tokens", 0),
                                total_tokens=_turn_usage.get("total_tokens", 0),
                                skill_internal_tokens=_skill_internal,
                                duration_ms=_d1_duration,
                                status=result.get("status", "unknown") if isinstance(result, dict) else "unknown",
                            )
                        except Exception as _d1e:
                            logger.debug(f"[D1] Token tracking failed: {_d1e}")

                    # ── Deferred tool calls: return placeholder so LLM re-plans ──
                    for _def_id, _def_tc in _deferred_calls:
                        _def_call_id = _def_tc.get("call_id") or _def_id
                        tool_results.append({
                            "type": "function_call_output",
                            "call_id": _def_call_id,
                            "output": json.dumps({"status": "deferred", "message": "此工具呼叫已排入下一輪執行，請先處理目前的結果，再繼續呼叫。"}, ensure_ascii=False)
                        })

                    input_payload = tool_results
                    continue

                else:
                    # ── D1: Track non-tool-call response tokens too ────────
                    try:
                        from server.services.token_tracker import TokenTracker
                        from pathlib import Path as _Path
                        _tracker = TokenTracker(str(_Path(os.getcwd())))
                        _sid = session_id or ""
                        _d1_chat_type = "group" if "group" in _sid else "personal"
                        _d1_duration = int(time.time() * 1000) - _turn_start_ms
                        _tracker.record_usage(
                            session_id=_sid,
                            chat_type=_d1_chat_type,
                            skill="(chat)",
                            model=self.model,
                            input_tokens=_turn_usage.get("input_tokens", 0),
                            output_tokens=_turn_usage.get("output_tokens", 0),
                            total_tokens=_turn_usage.get("total_tokens", 0),
                            duration_ms=_d1_duration,
                            status="success",
                        )
                    except Exception:
                        pass

                    if session_id and current_response_id:
                        _session_mgr.set_latest_response_id(session_id, current_response_id)
                    yield {"status": "success", "content": full_content, "tool_calls_made": tool_calls_made}
                    return

            # Safety: exceeded max iterations
            if session_id and current_response_id:
                _session_mgr.set_latest_response_id(session_id, current_response_id)
                
            yield {
                "status": "success",
                "content": full_content + f"\n\n(已達最大工具呼叫次數 {MAX_ITERATIONS} 輪，強制結束)",
                "tool_calls_made": tool_calls_made
            }
            return

        except Exception as e:
            err_str = str(e)
            logger.error(f"OpenAI chat error: {e}")
            if "Rate limit reached" in err_str or "rate_limit" in err_str.lower():
                yield {"status": "error", "message": "⚠️ OpenAI API 目前流量已滿，請稍候 30 秒後再試一次。"}
            elif any(code in err_str for code in ["500", "502", "503", "server_error", "overloaded"]):
                yield {"status": "error", "message": "⚠️ AI 服務暫時忙碌，請稍候再試一次。"}
            else:
                yield {"status": "error", "message": f"⚠️ 處理時發生錯誤，請稍後再試。\n(技術細節：{err_str[:120]})"}


    def simple_chat(self, session_history: list, temperature: float = 0.7, **kwargs) -> dict:
        """
        Pure LLM conversation — NO tools, NO skill schema injection.
        Strictly isolated from skill execution.

        Args:
            session_history: Full conversation so far as list of
                             {role, content} dicts. Must include system msg.
            kwargs: Accepts session_id and attached_file for compatibility.
        Returns:
            {status: 'success'|'error', content: str, message: str}
        """
        if not self.is_available:
            return {"status": "error", "message": "OpenAI adapter is not available. Check OPENAI_API_KEY."}

        try:
            # Debug: Log the system prompt being sent
            system_msg = next((m for m in session_history if m.get("role") == "system"), None)
            if system_msg:
                logger.info(f"[OpenAI Adapter] Sending System Prompt (Stateless): {system_msg['content'][:200]}...")
            
            # Filter out custom metadata
            clean_history = []
            for msg in session_history:
                clean_msg = {k: v for k, v in msg.items() if k != "created_at"}
                clean_history.append(clean_msg)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=clean_history,
                temperature=temperature,
                stream=True
                # NOTE: No 'tools' or 'tool_choice' passed — strictly isolated
            )
            full_content = ""
            for chunk in response:
                choice = chunk.choices[0] if chunk.choices else None
                if choice and choice.delta.content is not None:
                    text = choice.delta.content
                    full_content += text
                    yield {"status": "streaming", "content": text}
                    
            yield {"status": "success", "content": full_content}
        except Exception as e:
            err_str = str(e)
            logger.error(f"OpenAI simple_chat error: {e}")
            if "Rate limit reached" in err_str or "rate_limit" in err_str.lower():
                yield {"status": "error", "message": "⚠️ OpenAI API 目前流量已滿，請稍候 30 秒後再試一次。"}
            else:
                yield {"status": "error", "message": err_str}
