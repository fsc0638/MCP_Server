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

    def __init__(self, uma):
        self.uma = uma
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.model = None
        self._uploaded_files_cache = {}

        if GEMINI_AVAILABLE:
            api_key = os.getenv("GEMINI_API_KEY")
            if api_key:
                genai.configure(api_key=api_key)
                logger.info(f"Gemini adapter initialized with model: {self.model_name}")
            else:
                logger.warning("GEMINI_API_KEY not set. Gemini adapter disabled.")

    @property
    def is_available(self) -> bool:
        return GEMINI_AVAILABLE and os.getenv("GEMINI_API_KEY") is not None

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
            parts.append("請仔細觀察上述圖片並詳盡描述內容，然後根據圖片內容對應並呼叫合適的 Skills 進行處理。")
            
        return parts

    def get_tools(self, user_query: Optional[str] = None, max_tools: int = 10) -> List[Dict[str, Any]]:
        """Get tool definitions in Gemini FunctionDeclaration format."""
        from adapters import select_relevant_tools
        all_tools = self.uma.get_tools_for_model("gemini")

        if user_query and len(all_tools) > max_tools:
            return select_relevant_tools(user_query, all_tools, max_tools)

        return all_tools

    def chat(self, messages: Any = None, user_query: Optional[str] = None, user_message: Optional[str] = None, session_id: Optional[str] = None, attached_file: Optional[str] = None) -> Dict[str, Any]:
        """
        Send a chat request with function calling support.
        D-09: Supports multi-turn tool calls (up to MAX_ITERATIONS).
        D-12: Unified interface — accepts messages list + user_query.
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
                    user_query = msg["content"]
                    break

        if not user_query:
            return {"status": "error", "message": "No user query provided"}

        # Dynamic RAG Context Retrieval
        from core.retriever import retriever
        retrieved_context = retriever.search_context(user_query)
        
        if retrieved_context:
            augmented_query = f"""[System Instruction]
請務必根據下方提供的參考資料來回答問題。在回答時，若有引用資料片斷，請嚴格遵守標示出處格式，例如 "[文件名稱#chunk_0:片段]"。
若參考資料未能解答問題，請老實回答不知道或根據您的既有知識依現狀客觀回答。

[Reference Documents]
{retrieved_context}

[User Question]
{user_query}"""
        else:
            augmented_query = user_query

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

            model = genai.GenerativeModel(
                model_name=self.model_name,
                tools=[gemini_tools] if gemini_tools else None
            )

            chat = model.start_chat()
            
            upload_parts = self._handle_attached_file(attached_file, session_id)
            if upload_parts:
                message_parts = upload_parts + [augmented_query]
                response = chat.send_message(message_parts)
            else:
                response = chat.send_message(augmented_query)

            tool_calls_made = 0
            MAX_ITERATIONS = 10

            for _ in range(MAX_ITERATIONS):
                has_function_call = False
                if response.candidates[0].content.parts:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            has_function_call = True
                            fn_call = part.function_call
                            fn_name = fn_call.name
                            fn_args = dict(fn_call.args) if fn_call.args else {}

                            logger.info(f"Gemini function call: {fn_name}({fn_args})")
                            result = self.uma.execute_tool_call(fn_name, fn_args)

                            if result.get("status") == "requires_approval":
                                return {
                                    "status": "requires_approval",
                                    "tool_name": fn_name,
                                    "risk_description": result.get("risk_description", "High-risk operation detected"),
                                    "pending_args": fn_args
                                }

                            # Send function result back and continue loop
                            response = chat.send_message(
                                genai.protos.Content(
                                    parts=[genai.protos.Part(
                                        function_response=genai.protos.FunctionResponse(
                                            name=fn_name,
                                            response={"result": result}
                                        )
                                    )]
                                )
                            )
                            tool_calls_made += 1
                            break  # Re-check the new response for more tool calls

                if not has_function_call:
                    break  # No more tool calls, exit loop

            return {
                "status": "success",
                "content": response.text if hasattr(response, "text") else str(response),
                "tool_calls_made": tool_calls_made
            }

        except Exception as e:
            logger.error(f"Gemini chat error: {e}")
            return {"status": "error", "message": str(e)}

    def simple_chat(self, session_history: list, session_id: Optional[str] = None, attached_file: Optional[str] = None) -> dict:
        """
        Pure LLM conversation — NO tools, NO skill schema injection.
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
                    gemini_history.append({"role": role, "parts": [content]})
                    if role == "user":
                        last_user_msg = content

            model = genai.GenerativeModel(
                model_name=self.model_name
                # NOTE: No tools= passed — strictly isolated
            )
            chat = model.start_chat(history=gemini_history[:-1] if len(gemini_history) > 1 else [])
            
            # Dynamic RAG Context Retrieval
            from core.retriever import retriever
            retrieved_context = retriever.search_context(last_user_msg) if last_user_msg else ""
            
            if retrieved_context:
                augmented_msg = f"""[System Instruction]
請務必根據下方提供的參考資料來回答問題。在回答時，若有引用資料片斷，請嚴格遵守標示出處格式，例如 "[文件名稱#chunk_0:片段]"。
若參考資料未能解答問題，請老實回答不知道。

[Reference Documents]
{retrieved_context}

[User Question]
{last_user_msg}"""
            else:
                augmented_msg = last_user_msg

            upload_parts = self._handle_attached_file(attached_file, session_id)
            if upload_parts:
                message_parts = upload_parts + [augmented_msg]
                response = chat.send_message(message_parts)
            else:
                response = chat.send_message(augmented_msg)
                
            return {"status": "success", "content": response.text}
        except Exception as e:
            logger.error(f"Gemini simple_chat error: {e}")
            return {"status": "error", "message": str(e)}

