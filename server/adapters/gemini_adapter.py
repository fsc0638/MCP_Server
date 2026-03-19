"""
Gemini Adapter (Phase 4)
Handles communication with Google Gemini models using function calling.
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("MCP_Server.Adapter.Gemini")

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("google-generativeai package not installed. Gemini adapter will be unavailable.")


class GeminiAdapter:
    """Adapter for Google Gemini models with function calling support."""

    def __init__(self, uma, model: Optional[str] = None):
        self.uma = uma
        # 1. Resolve Model: use passed model or fallback to env var
        self.model_name = model or os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        self.model = None
        self._uploaded_files_cache = {}

        if GEMINI_AVAILABLE:
            api_key = os.getenv("GEMINI_API_KEY")
            if api_key:
                genai.configure(api_key=api_key)
                logger.info(f"Gemini adapter initialized with model: {self.model_name}")
                print(f"[RELOAD] GeminiAdapter initialized (Model: {self.model_name})")
            else:
                logger.warning("GEMINI_API_KEY not set. Gemini adapter disabled.")

    @property
    def is_available(self) -> bool:
        return GEMINI_AVAILABLE and os.getenv("GEMINI_API_KEY") is not None

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

    def _build_gemini_history(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Builds a valid Gemini history from OpenAI-style messages.
        - Filters out 'system' roles (used in model init).
        - Merges consecutive messages with the same role.
        - Ensures roles alternate (user -> model -> user).
        - Ensures parts are never empty.
        """
        if not messages:
            return []

        cleaned_history = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                continue

            gemini_role = "model" if role == "assistant" else "user"
            parts = self._to_gemini_parts(m.get("content", ""))

            # Ensure parts is never empty
            if not parts:
                parts = ["(no text content)"]

            if cleaned_history and cleaned_history[-1]["role"] == gemini_role:
                # Merge consecutive same-role messages
                cleaned_history[-1]["parts"].extend(parts)
            else:
                cleaned_history.append({"role": gemini_role, "parts": parts})

        # Gemini history MUST start with 'user'
        while cleaned_history and cleaned_history[0]["role"] != "user":
            cleaned_history.pop(0)

        return cleaned_history

    def _to_gemini_parts(self, content: Any) -> list:
        """Converts OpenAI-style multi-modal content to Gemini parts list."""
        if isinstance(content, str):
            return [content]
        if isinstance(content, list):
            gemini_parts = []
            for part in content:
                if isinstance(part, str):
                    gemini_parts.append(part)
                elif isinstance(part, dict):
                    if part.get("type") == "text":
                        gemini_parts.append(part.get("text", ""))
                    # Note: image_url or blocks are handled separately via upload in Gemini
                    # but we keep text for history context
            return gemini_parts
        return [str(content)]

    def _handle_attached_file(self, attached_file: Optional[str], session_id: Optional[str]) -> list:
        """Uploads file to Google AI (or uses cached) and returns parts list."""
        if not attached_file or not session_id or not GEMINI_AVAILABLE:
            return []

        # Check cache
        cache_key = f"{session_id}_{attached_file}"
        cached_file = self._uploaded_files_cache.get(cache_key)

        if not cached_file:
            import mimetypes
            mime_type, _ = mimetypes.guess_type(attached_file)
            if not mime_type:
                mime_type = "text/plain"

            logger.info(f"Uploading {attached_file} to Gemini ({mime_type})...")
            try:
                uploaded_file = genai.upload_file(path=attached_file, mime_type=mime_type)
                self._uploaded_files_cache[cache_key] = uploaded_file
                cached_file = uploaded_file
            except Exception as e:
                logger.error(f"Failed to upload file to Gemini: {e}")
                return []

        parts = [cached_file]

        # Proactively inject prompt if it's an image
        lower_path = attached_file.lower()
        if lower_path.endswith(('.png', '.jpg', '.jpeg', '.webp')):
            parts.append("請詳細描述這張圖片的內容，包括場景、文字、人物等所有可見元素。如果與已載入的 Skills 相關，請一併說明。")

        return parts

    def get_tools(self, user_query: Optional[str] = None, max_tools: int = 10) -> List[Dict[str, Any]]:
        """Get tool definitions in Gemini FunctionDeclaration format."""
        from server.adapters import select_relevant_tools
        all_tools = self.uma.get_tools_for_model("gemini")

        if user_query and len(all_tools) > max_tools:
            return select_relevant_tools(user_query, all_tools, max_tools)

        return all_tools

    def chat(self, messages: Any = None, user_query: Optional[str] = None, user_message: Optional[str] = None, session_id: Optional[str] = None, attached_file: Optional[str] = None, temperature: float = 0.7, **kwargs) -> Dict[str, Any]:
        """
        Send a chat request with function calling support.
        D-09: Supports multi-turn tool calls (up to MAX_ITERATIONS).
        D-12: Unified interface - accepts messages list + user_query.
        ARCHITECTURE: Uses router.py's pre-built RAG context and system prompt.
                      Does NOT do its own RAG retrieval.
        """
        if not self.is_available:
            return {"status": "error", "message": "Gemini adapter is not available"}

        # Handle both unified interface (messages+user_query) and legacy (user_message)
        if user_message and not user_query:
            user_query = user_message
        if isinstance(messages, str):
            user_query = messages
            messages = None

        # Extract the latest user query from messages if not provided
        if not user_query and messages:
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user_query = self._extract_text(msg["content"])
                    break

        # Ensure user_query is a string for RAG and tool selection
        user_query = self._extract_text(user_query) if user_query else ""

        if not user_query:
            return {"status": "error", "message": "No user query provided"}

        # Extract system instruction - priority:
        # 1. system_prompt passed by router.py (contains full skill list from build_system_prompt)
        # 2. system message from history (fallback)
        # 3. built-in default
        # 1. Priority: extract from messages (chat_core guarantees messages[0] is system)
        system_instruction_text = ""
        if messages:
            for msg in messages:
                if msg.get("role") == "system":
                    system_instruction_text = self._extract_text(msg.get("content", ""))
                    break

        # 2. Fallback: kwargs or hardcoded default
        if not system_instruction_text:
            system_instruction_text = kwargs.get("system_prompt") or (
                "You are a high-performance AI Assistant. 請使用繁體中文回覆。\n"
                "回覆時請盡量簡潔、結構清晰，並優先使用系統已載入的技能。\n"
                "如果使用者的問題涉及已載入的知識庫或文件，請優先引用相關內容作為回覆依據。"
            )

        visual_parts = []
        visual_docs = kwargs.get("visual_docs", [])
        visual_docs_display_names = kwargs.get("visual_docs_display_names", {})
        import os as _os

        # Process attached_file (Legacy/Upload)
        if attached_file:
            visual_parts.extend(self._handle_attached_file(attached_file, session_id))

        # Process visual_docs (New: NotebookLM Style selected docs)
        for doc_path in visual_docs:
            display_name = visual_docs_display_names.get(doc_path, _os.path.basename(doc_path))
            visual_parts.append(f"[參考文件: {display_name}]")
            visual_parts.extend(self._handle_attached_file(doc_path, session_id))

        # Use user_query as-is since RAG context is already embedded by router.py
        augmented_query = user_query

        # Multi-modal injection: ONLY add images when visual_docs is present
        # (do not let images pollute non-image queries)
        if visual_parts:
            augmented_query = visual_parts + [augmented_query]

        tools = self.get_tools(user_query=user_query)

        try:
            # Build Gemini tool declarations
            function_declarations = []
            for tool_def in tools:
                function_declarations.append(
                    genai.protos.FunctionDeclaration(
                        name=tool_def["name"],
                        description=tool_def["description"],
                        parameters=tool_def.get("parameters", {})
                    )
                )

            gemini_tools = genai.protos.Tool(function_declarations=function_declarations) if function_declarations else None

            # === DEBUG: Print system_instruction summary ===
            logger.debug(f"[GEMINI] system_instruction length={len(system_instruction_text)}")
            logger.debug(f"[GEMINI] system_instruction preview: {system_instruction_text[:300]}")
            logger.debug(f"[GEMINI] visual_parts count={len(visual_parts)}, user_query[:80]={user_query[:80]}")
            # === END DEBUG ===

            model = genai.GenerativeModel(
                model_name=self.model_name,
                tools=[gemini_tools] if gemini_tools else None,
                system_instruction=system_instruction_text,
                generation_config={"temperature": temperature}
            )

            # Build Gemini history from messages (excluding the last one which is current turn)
            # Also strip system message since it's handled by system_instruction
            gemini_history = []
            if messages and len(messages) > 1:
                non_system_msgs = [m for m in messages if m.get("role") != "system"]
                gemini_history = self._build_gemini_history(non_system_msgs[:-1])

            chat = model.start_chat(history=gemini_history)

            upload_parts = self._handle_attached_file(attached_file, session_id)
            message_parts = upload_parts + [augmented_query] if upload_parts else augmented_query

            response = chat.send_message(message_parts, stream=True)
            logger.info(f"Gemini: Message sent. Parts={len(message_parts) if isinstance(message_parts, list) else 1}")

            tool_calls_made = 0
            MAX_ITERATIONS = 10

            full_content = ""
            for _ in range(MAX_ITERATIONS):
                has_function_call = False
                pending_calls = []

                try:
                    # 1. Consume current stream exhaustively
                    for chunk in response:
                        if not chunk.candidates:
                            continue

                        cand = chunk.candidates[0]
                        if cand.content and cand.content.parts:
                            for part in cand.content.parts:
                                # Handle text
                                if hasattr(part, "text") and part.text:
                                    full_content += part.text
                                    yield {"status": "streaming", "content": part.text}

                                # Handle function calls
                                fn_call = getattr(part, "function_call", None)
                                if fn_call:
                                    has_function_call = True
                                    fn_name = fn_call.name if hasattr(fn_call, "name") else fn_call.get("name")
                                    fn_args = dict(fn_call.args) if hasattr(fn_call, "args") else dict(fn_call.get("args", {}))
                                    pending_calls.append((fn_name, fn_args))

                                    logger.info(f"Gemini detected tool: {fn_name}")
                                    yield {"status": "streaming", "content": f"\n\n\u2699\ufe0f \u57f7\u884c\u6280\u80fd: `{fn_name}`\n"}

                    # 2. If no function calls, we are done
                    if not has_function_call:
                        yield {
                            "status": "success",
                            "content": full_content,
                            "tool_calls_made": tool_calls_made
                        }
                        return

                    # 3. Execute all detected calls
                    tool_results_parts = []
                    for fn_name, fn_args in pending_calls:
                        result = self.uma.execute_tool_call(fn_name, fn_args)

                        # Check for approval requirement
                        if result.get("status") == "requires_approval":
                            yield {
                                "status": "requires_approval",
                                "tool_name": fn_name,
                                "risk_description": result.get("risk_description", "High-risk operation"),
                                "pending_args": fn_args
                            }
                            return

                        tool_results_parts.append(
                            genai.protos.Part(
                                function_response=genai.protos.FunctionResponse(
                                    name=fn_name,
                                    response={"result": result}
                                )
                            )
                        )

                    # 4. Send all results back in one go
                    response = chat.send_message(
                        genai.protos.Content(parts=tool_results_parts),
                        stream=True
                    )
                    tool_calls_made += 1

                except Exception as stream_err:
                    import traceback
                    logger.error(f"Gemini stream error: {stream_err}\n{traceback.format_exc()}")
                    if "SAFETY" in str(stream_err):
                        yield {"status": "error", "message": "\u5167\u5bb9\u89f8\u767c Gemini \u5b89\u5168\u904e\u6ffe\u6a5f\u5236\u3002"}
                        return
                    yield {"status": "error", "message": f"Gemini Error: {str(stream_err)}"}
                    return

            yield {
                "status": "success",
                "content": full_content + f"\n\n(\u5df2\u9054\u6700\u5927\u5de5\u5177\u547c\u53eb\u6b21\u6578 {MAX_ITERATIONS} \u8f2a\uff0c\u5f37\u5236\u7d50\u675f)",
                "tool_calls_made": tool_calls_made
            }
            return

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"Gemini chat error: {e}\n{error_details}")
            yield {"status": "error", "message": f"Gemini Error: {str(e)}"}

    def simple_chat(self, session_history: list, session_id: Optional[str] = None, attached_file: Optional[str] = None, temperature: float = 0.7) -> dict:
        """
        Pure LLM conversation - NO tools, NO skill schema injection.
        Strictly isolated from skill execution.

        Args:
            session_history: List of {role, content} dicts.
                             role must be 'user' or 'model' for Gemini.
        Returns:
            {status: 'success'|'error', content: str}
        """
        if not self.is_available:
            return {"status": "error", "message": "Gemini adapter is not available. Check GEMINI_API_KEY."}

        try:
            # Convert OpenAI-style roles to Gemini roles
            gemini_history = []
            last_user_msg = ""
            for msg in session_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    # Prepend system instructions to the first user message
                    continue
                elif role == "assistant":
                    role = "model"
                if content:
                    gemini_history.append({"role": role, "parts": self._to_gemini_parts(content)})
                    if role == "user":
                        last_user_msg = self._extract_text(content)

            model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config={"temperature": temperature}
                # NOTE: No tools= passed - strictly isolated
            )
            chat = model.start_chat(history=gemini_history[:-1] if len(gemini_history) > 1 else [])

            # Dynamic RAG Context Retrieval
            from server.core.retriever import retriever
            retrieved_context = retriever.search_context(last_user_msg) if last_user_msg else ""

            if retrieved_context:
                augmented_msg = f"""[System Instruction]
\u8acb\u6839\u64da\u4ee5\u4e0b\u53c3\u8003\u6587\u4ef6\u56de\u7b54\u4f7f\u7528\u8005\u7684\u554f\u984c\u3002\u5982\u6709\u5f15\u7528\uff0c\u8acb\u5728\u56de\u8986\u4e2d\u6a19\u8a3b\u4f86\u6e90\uff0c\u4f8b\u5982\uff1a"[\u4f86\u6e90\u6587\u4ef6#chunk_0:\u6458\u8981]"\u3002
\u82e5\u53c3\u8003\u6587\u4ef6\u8207\u554f\u984c\u7121\u95dc\uff0c\u8acb\u76f4\u63a5\u4f9d\u64da\u4f60\u7684\u77e5\u8b58\u56de\u8986\u3002

[Reference Documents]
{retrieved_context}

[User Question]
{last_user_msg}"""
            else:
                augmented_msg = last_user_msg

            upload_parts = self._handle_attached_file(attached_file, session_id)
            if upload_parts:
                message_parts = upload_parts + [augmented_msg]
                response = chat.send_message(message_parts, stream=True)
            else:
                response = chat.send_message(augmented_msg, stream=True)

            full_content = ""
            for chunk in response:
                if chunk.text:
                    full_content += chunk.text
                    yield {"status": "streaming", "content": chunk.text}

            yield {"status": "success", "content": full_content}
        except Exception as e:
            logger.error(f"Gemini simple_chat error: {e}")
            yield {"status": "error", "message": str(e)}
