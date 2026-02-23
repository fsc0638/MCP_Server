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

    def chat(self, user_message: str, system_prompt: str = "") -> Dict[str, Any]:
        """
        Send a message to Claude with tool use support.
        """
        if not self.is_available:
            return {"status": "error", "message": "Claude adapter is not available"}

        tools = self.get_tools(user_query=user_message)

        try:
            messages = [{"role": "user", "content": user_message}]

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt or "You are a helpful assistant with access to tools.",
                messages=messages,
                tools=tools if tools else []
            )

            # Check for tool use
            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        fn_name = block.name
                        fn_args = block.input

                        logger.info(f"Claude tool call: {fn_name}({fn_args})")
                        result = self.uma.execute_tool_call(fn_name, fn_args)

                        if result.get("status") == "requires_approval":
                            return {
                                "status": "requires_approval",
                                "tool_name": fn_name,
                                "risk_description": result.get("risk_description", "High-risk operation detected"),
                                "pending_args": fn_args
                            }

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, ensure_ascii=False)
                        })

                # Send results back
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

                final_response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=system_prompt or "You are a helpful assistant with access to tools.",
                    messages=messages
                )

                return {
                    "status": "success",
                    "content": final_response.content[0].text,
                    "tool_calls_made": len(tool_results)
                }
            else:
                return {
                    "status": "success",
                    "content": response.content[0].text,
                    "tool_calls_made": 0
                }

        except Exception as e:
            logger.error(f"Claude chat error: {e}")
            return {"status": "error", "message": str(e)}

    def simple_chat(self, session_history: list) -> dict:
        """
        Pure LLM conversation — NO tools, NO skill schema injection.
        Strictly isolated from skill execution.

        Args:
            session_history: List of {role, content} dicts (OpenAI format).
                             System messages are extracted automatically.
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
                messages=messages
                # NOTE: No tools= passed — strictly isolated
            )
            return {"status": "success", "content": response.content[0].text}
        except Exception as e:
            logger.error(f"Claude simple_chat error: {e}")
            return {"status": "error", "message": str(e)}

