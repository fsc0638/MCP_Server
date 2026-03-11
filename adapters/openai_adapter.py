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

    def get_tools(self, user_query: Optional[str] = None, max_tools: int = 25) -> List[Dict[str, Any]]:
        """
        Get tool definitions in OpenAI format.
        If user_query is provided, uses dynamic tool injection.
        """
        from adapters import select_relevant_tools
        all_tools = self.uma.get_tools_for_model("openai")

        if user_query and len(all_tools) > max_tools:
            return select_relevant_tools(user_query, all_tools, max_tools)

        return all_tools

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
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_img}"
                    }
                }
            except Exception as e:
                logger.error(f"Failed to read and encode image for OpenAI: {e}")
                return None
        return None

    def chat(self, messages: Any, user_query: Optional[str] = None, session_id: Optional[str] = None, attached_file: Optional[str] = None, temperature: float = 0.7, **kwargs) -> Dict[str, Any]:
        """
        Send a chat completion request with tool calling support.
        ARCHITECTURE: System prompt and RAG context are provided by router.py.
                      This adapter is a pure message sender.
        """
        if not self.is_available:
            return {"status": "error", "message": "OpenAI adapter is not available"}

        # Handle list of messages or single string
        if isinstance(messages, str):
            user_query = messages
            messages = [
                {"role": "system", "content": "You are a helpful AI assistant. 請以繁體中文回覆。"},
                {"role": "user", "content": messages}
            ]

        # Ensure user_query is a string for tool selection
        if not user_query and messages:
            last_msg = messages[-1]["content"]
            user_query = self._extract_text(last_msg)
        else:
            user_query = self._extract_text(user_query) if user_query else ""


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
            # Prepend a text label so AI knows the original filename
            all_visual_parts.append({"type": "text", "text": f"[圖片名稱: {display_name}]"})
            res = self._handle_attached_file(doc_path)
            if res:
                all_visual_parts.append(res)

        if all_visual_parts:
            # Find the last user message to attach the images
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    orig_content = messages[i]["content"]
                    if isinstance(orig_content, str):
                        messages[i]["content"] = [{"type": "text", "text": orig_content}] + all_visual_parts
                    elif isinstance(orig_content, list):
                        messages[i]["content"].extend(all_visual_parts)
                    break

        tools = self.get_tools(user_query=user_query)
        tool_calls_made = 0
        MAX_ITERATIONS = 10  # Safety cap

        try:
            for _ in range(MAX_ITERATIONS):
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools if tools else None,
                    tool_choice="auto" if tools else None,
                    temperature=temperature,
                    stream=True
                )

                full_content = ""
                tool_calls_dict = {}
                finish_reason = None

                for chunk in response:
                    choice = chunk.choices[0] if chunk.choices else None
                    if not choice:
                        continue
                        
                    delta = choice.delta
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                        
                    if delta.content is not None:
                        text = delta.content
                        full_content += text
                        yield {"status": "streaming", "content": text}
                        
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_dict:
                                tool_calls_dict[idx] = {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {"name": tc.function.name or "", "arguments": tc.function.arguments or ""}
                                }
                            else:
                                if tc.function.name:
                                    tool_calls_dict[idx]["function"]["name"] += tc.function.name
                                if tc.function.arguments:
                                    tool_calls_dict[idx]["function"]["arguments"] += tc.function.arguments

                # Reconstruct tool_calls list for appending to history
                tool_calls_list = []
                for idx in sorted(tool_calls_dict.keys()):
                    from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
                    tc_dict = tool_calls_dict[idx]
                    tool_calls_list.append(tc_dict)

                # If the model wants to call tools
                if finish_reason == "tool_calls" and tool_calls_list:
                    tool_results = []
                    for tool_call in tool_calls_list:
                        fn_name = tool_call["function"]["name"]
                        
                        import json
                        try:
                            fn_args_str = tool_call["function"]["arguments"]
                            fn_args = json.loads(fn_args_str) if fn_args_str else {}
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse OpenAI tool arguments: {tool_call['function']['arguments']}")
                            fn_args = {}

                        logger.info(f"OpenAI tool call: {fn_name}({fn_args})")
                        yield {"status": "streaming", "content": f"\n\n⚙️ 執行技能: `{fn_name}`\n"}
                        result = self.uma.execute_tool_call(fn_name, fn_args)

                        # Check for human-in-the-loop
                        if result.get("status") == "requires_approval":
                            yield {
                                "status": "requires_approval",
                                "tool_name": fn_name,
                                "risk_description": result.get("risk_description", "High-risk operation detected"),
                                "pending_args": fn_args
                            }
                            return

                        tool_results.append({
                            "tool_call_id": tool_call["id"],
                            "role": "tool",
                            "content": json.dumps(result, ensure_ascii=False)
                        })
                        tool_calls_made += 1

                    # Append assistant's tool call message AND tool results, then loop
                    assistant_msg = {
                        "role": "assistant",
                        "content": full_content if full_content else None,
                        "tool_calls": tool_calls_list
                    }
                    messages.append(assistant_msg)
                    messages.extend(tool_results)
                    # Continue loop — AI may want to call more tools

                else:
                    # Model finished — return the final text response
                    yield {
                        "status": "success",
                        "content": full_content,
                        "tool_calls_made": tool_calls_made
                    }
                    return

            # Safety: exceeded max iterations
            yield {
                "status": "success",
                "content": full_content + f"\n\n(已達最大工具呼叫次數 {MAX_ITERATIONS} 輪，強制結束)",
                "tool_calls_made": tool_calls_made
            }
            return

        except Exception as e:
            logger.error(f"OpenAI chat error: {e}")
            return {"status": "error", "message": str(e)}


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
            response = self.client.chat.completions.create(
                model=self.model,
                messages=session_history,
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
            logger.error(f"OpenAI simple_chat error: {e}")
            yield {"status": "error", "message": str(e)}
