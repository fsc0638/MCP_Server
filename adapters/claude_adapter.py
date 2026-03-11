"""
Claude Adapter (Phase 4)
Handles communication with Anthropic Claude models using tool use.
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("MCP_Server.Adapter.Claude")

try:
    import anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False
    logger.warning("anthropic package not installed. Claude adapter will be unavailable.")


class ClaudeAdapter:
    """Adapter for Anthropic Claude models with tool use support."""

    def __init__(self, uma):
        self.uma = uma
        self.client = None
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

        if CLAUDE_AVAILABLE:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if api_key:
                self.client = anthropic.Anthropic(api_key=api_key)
                logger.info(f"Claude adapter initialized with model: {self.model}")
            else:
                logger.warning("ANTHROPIC_API_KEY not set. Claude adapter disabled.")

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

    def get_tools(self, user_query: Optional[str] = None, max_tools: int = 10) -> List[Dict[str, Any]]:
        """Get tool definitions in Claude format."""
        from adapters import select_relevant_tools
        all_tools = self.uma.get_tools_for_model("openai")  # Claude uses similar format

        if user_query and len(all_tools) > max_tools:
            all_tools = select_relevant_tools(user_query, all_tools, max_tools)

        # Convert OpenAI format to Claude format
        claude_tools = []
        for tool in all_tools:
            fn = tool.get("function", tool)
            claude_tools.append({
                "name": fn.get("name", "unknown"),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {
                    "type": "object",
                    "properties": {},
                    "required": []
                })
            })
        return claude_tools

    def _handle_attached_file(self, attached_file: Optional[str]) -> Optional[Dict[str, Any]]:
        """Handles image encoding for Claude Vision."""
        if not attached_file:
            return None

        import mimetypes
        import base64
        
        mime_type, _ = mimetypes.guess_type(attached_file)
        if not mime_type or not mime_type.startswith("image/"):
            return None

        try:
            with open(attached_file, "rb") as f:
                base64_img = base64.b64encode(f.read()).decode('utf-8')
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": base64_img
                }
            }
        except Exception as e:
            logger.error(f"Failed to read image for Claude Vision: {e}")
            return None

    def chat(self, messages: Any = None, user_query: Optional[str] = None,
             user_message: Optional[str] = None, system_prompt: str = "", **kwargs) -> Dict[str, Any]:
        """
        Send a message to Claude with tool use support.
        D-10: Supports multi-turn tool calls (up to MAX_ITERATIONS).
        D-12: Unified interface — accepts messages list + user_query.
        """
        if not self.is_available:
            return {"status": "error", "message": "Claude adapter is not available"}

        # Handle both unified interface (messages+user_query) and legacy (user_message)
        if user_message and not user_query:
            user_query = user_message
        if isinstance(messages, str):
            user_query = messages
            messages = None

        # Extract latest user query from messages if not provided
        if not user_query and messages:
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user_query = self._extract_text(msg["content"])
                    break
        
        # Ensure user_query is a string for tool selection
        user_query = self._extract_text(user_query) if user_query else ""

        if not user_query:
            return {"status": "error", "message": "No user query provided"}

        tools = self.get_tools(user_query=user_query)

        # D-12: Use agent system prompt (not the pure-chat one)
        agent_system = system_prompt or (
            "You are a helpful AI assistant with access to tools. "
            "Use the provided tools to complete tasks. 請以繁體中文回覆。"
        )

        # Dynamic RAG Context Retrieval
        from core.retriever import retriever
        retrieved_context = retriever.search_context(user_query)
        
        if retrieved_context:
            user_query = f"""[System Instruction]
請務必根據下方提供的參考資料來回答問題。在回答時，若有引用資料片斷，請嚴格遵守標示出處格式，例如 "[文件或技能名稱#chunk_0:片段]"。
注意：資料來源標註為 'File [...]' 表實體文件；'Skill [...]' 表您的技能手冊。

[Reference Context]
{retrieved_context}

[User Question]
{user_query}"""

        try:
            # Build initial user message with potential image
            img_part = self._handle_attached_file(attached_file)
            
            # Start with provided history or just current query
            if messages:
                claude_messages = []
                for m in messages:
                    role = m["role"]
                    if role == "assistant":
                        claude_messages.append({"role": "assistant", "content": m["content"]})
                    elif role == "user":
                        claude_messages.append({"role": "user", "content": m["content"]})
                
                # Update the LAST user message with augmented query (RAG)
                for i in range(len(claude_messages)-1, -1, -1):
                    if claude_messages[i]["role"] == "user":
                        orig_c = claude_messages[i]["content"]
                        # If it's a list (multimodal), update the text part
                        if isinstance(orig_c, list):
                            for p in orig_c:
                                if p.get("type") == "text":
                                    p["text"] = user_query
                        else:
                            claude_messages[i]["content"] = user_query
                        break

                # Attach image to the LAST user message if applicable
                if img_part:
                    for i in range(len(claude_messages)-1, -1, -1):
                        if claude_messages[i]["role"] == "user":
                            orig_c = claude_messages[i]["content"]
                            new_content = [{"type": "text", "text": orig_c}] if isinstance(orig_c, str) else orig_c
                            new_content.append(img_part)
                            claude_messages[i]["content"] = new_content
                            break
            else:
                user_content = [{"type": "text", "text": user_query}]
                if img_part:
                    user_content.append(img_part)
                claude_messages = [{"role": "user", "content": user_content}]
            
            tool_calls_made = 0
            MAX_ITERATIONS = 10

            for _ in range(MAX_ITERATIONS):
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=agent_system,
                    messages=claude_messages,
                    tools=tools if tools else [],
                    stream=True
                )

                tool_calls_dict = {}
                full_content = ""
                stop_reason = None
                
                for event in response:
                    if event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            idx = event.index
                            tool_calls_dict[idx] = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "input": ""
                            }
                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            full_content += event.delta.text
                            yield {"status": "streaming", "content": event.delta.text}
                        elif event.delta.type == "input_json_delta":
                            idx = event.index
                            if idx in tool_calls_dict:
                                tool_calls_dict[idx]["input"] += event.delta.partial_json
                    elif event.type == "message_delta":
                        if hasattr(event.delta, "stop_reason"):
                            stop_reason = event.delta.stop_reason

                if stop_reason == "tool_use" and tool_calls_dict:
                    # prepare tool_use objects
                    tool_results = []
                    content_to_append = []
                    if full_content:
                        content_to_append.append({"type": "text", "text": full_content})
                        
                    for idx, tc in tool_calls_dict.items():
                        fn_name = tc["name"]
                        fn_args_str = tc["input"]
                        
                        import json
                        try:
                            fn_args = json.loads(fn_args_str) if fn_args_str else {}
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse Claude tool arguments: {fn_args_str}")
                            fn_args = {}
                            
                        content_to_append.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": fn_name,
                            "input": fn_args
                        })
                        
                        logger.info(f"Claude tool call: {fn_name}({fn_args})")
                        yield {"status": "streaming", "content": f"\n\n⚙️ 執行技能: `{fn_name}`\n"}
                        result = self.uma.execute_tool_call(fn_name, fn_args)

                        if result.get("status") == "requires_approval":
                            yield {
                                "status": "requires_approval",
                                "tool_name": fn_name,
                                "risk_description": result.get("risk_description", "High-risk operation detected"),
                                "pending_args": fn_args
                            }
                            return
                            
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": json.dumps(result, ensure_ascii=False)
                        })
                        tool_calls_made += 1

                    # Append assistant + tool results, then loop for more
                    claude_messages.append({"role": "assistant", "content": content_to_append})
                    claude_messages.append({"role": "user", "content": tool_results})
                else:
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
            logger.error(f"Claude chat error: {e}")
            yield {"status": "error", "message": str(e)}

    def simple_chat(self, session_history: list, **kwargs) -> dict:
        """
        Pure LLM conversation — NO tools, NO skill schema injection.
        Strictly isolated from skill execution.

        Args:
            session_history: List of {role, content} dicts (OpenAI format).
                             System messages are extracted automatically.
            kwargs: Accepts session_id and attached_file for compatibility.
        Returns:
            {status: 'success'|'error', content: str}
        """
        if not self.is_available:
            return {"status": "error", "message": "Claude adapter is not available. Check ANTHROPIC_API_KEY."}

        try:
            system_content = ""
            messages = []
            for msg in session_history:
                role = msg.get("role")
                content = msg.get("content", "")
                if role == "system":
                    system_content = content
                elif role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_content or "你是研發組的 AI 助理，請以繁體中文回覆。",
                messages=messages,
                stream=True
                # NOTE: No tools= passed — strictly isolated
            )
            full_content = ""
            for event in response:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    full_content += event.delta.text
                    yield {"status": "streaming", "content": event.delta.text}

            yield {"status": "success", "content": full_content}
        except Exception as e:
            logger.error(f"Claude simple_chat error: {e}")
            yield {"status": "error", "message": str(e)}

