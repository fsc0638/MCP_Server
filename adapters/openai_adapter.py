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

    def __init__(self, uma):
        self.uma = uma
        self.client = None
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")

        if OPENAI_AVAILABLE:
            api_key = os.getenv("OPENAI_API_KEY")
            if api_key:
                self.client = OpenAI(api_key=api_key)
                logger.info(f"OpenAI adapter initialized with model: {self.model}")
            else:
                logger.warning("OPENAI_API_KEY not set. OpenAI adapter disabled.")

    @property
    def is_available(self) -> bool:
        return self.client is not None

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

    def chat(self, messages: Any, user_query: Optional[str] = None) -> Dict[str, Any]:
        """
        Send a chat completion request with tool calling support.
        Handles the tool call loop automatically.
        Accepts either a list of messages or a single string (user query).
        """
        if not self.is_available:
            return {"status": "error", "message": "OpenAI adapter is not available"}

        # 1. Prepare system context if missing
        system_msg = {
            "role": "system",
            "content": (
                "You are a high-performance Autonomous AI Agent (Hand-Brain pattern). "
                "You HAVE access to the local system via specialized 'MCP Skills' (tools). "
                "NEVER say you cannot execute code or access the local environment. "
                "MANDATORY RULES:\n"
                "1. When a user asks you to execute, verify, or calculate, you MUST use the corresponding tool.\n"
                "2. For Python code, ALWAYS use 'mcp-python-executor'. Do not just provide code in text.\n"
                "3. CRITICAL — Design/UI Rule: When the user asks about UI design, CSS styles, colors, or any visual design task, "
                "YOU MUST FIRST call 'mcp-brand-guidelines' to retrieve the official CIS color palette. "
                "NEVER ask the user to 'provide the colors' — you have a tool to look them up. "
                "After calling the tool, use the returned brand colors (e.g., Brand Blue #003366, Brand Orange #FF6600) "
                "to complete the design task.\n"
                "Output your thinking process clearly, then call the tool."
            )
        }

        # Handle simple string input from router
        if isinstance(messages, str):
            user_query = messages
            messages = [
                system_msg,
                {"role": "user", "content": messages}
            ]
        elif not any(m.get("role") == "system" for m in messages):
            messages.insert(0, system_msg)
            user_query = user_query or (messages[-1]["content"] if messages else None)

        tools = self.get_tools(user_query=user_query)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None
            )

            choice = response.choices[0]

            # If the model wants to call tools
            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                tool_results = []
                for tool_call in choice.message.tool_calls:
                    fn_name = tool_call.function.name
                    fn_args = tool_call.function.arguments

                    logger.info(f"OpenAI tool call: {fn_name}({fn_args})")
                    result = self.uma.execute_tool_call(fn_name, fn_args)

                    # Check for human-in-the-loop
                    if result.get("status") == "requires_approval":
                        return {
                            "status": "requires_approval",
                            "tool_name": fn_name,
                            "risk_description": result.get("risk_description", "High-risk operation detected"),
                            "pending_args": fn_args
                        }

                    tool_results.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "content": json.dumps(result, ensure_ascii=False)
                    })

                # Send tool results back to model
                messages.append(choice.message)
                messages.extend(tool_results)

                final_response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages
                )

                return {
                    "status": "success",
                    "content": final_response.choices[0].message.content,
                    "tool_calls_made": len(tool_results)
                }
            else:
                return {
                    "status": "success",
                    "content": choice.message.content,
                    "tool_calls_made": 0
                }

        except Exception as e:
            logger.error(f"OpenAI chat error: {e}")
            return {"status": "error", "message": str(e)}

    def simple_chat(self, session_history: list) -> dict:
        """
        Pure LLM conversation — NO tools, NO skill schema injection.
        Strictly isolated from skill execution.

        Args:
            session_history: Full conversation so far as list of
                             {role, content} dicts. Must include system msg.
        Returns:
            {status: 'success'|'error', content: str, message: str}
        """
        if not self.is_available:
            return {"status": "error", "message": "OpenAI adapter is not available. Check OPENAI_API_KEY."}

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=session_history
                # NOTE: No 'tools' or 'tool_choice' passed — strictly isolated
            )
            content = response.choices[0].message.content
            return {"status": "success", "content": content}
        except Exception as e:
            logger.error(f"OpenAI simple_chat error: {e}")
            return {"status": "error", "message": str(e)}
