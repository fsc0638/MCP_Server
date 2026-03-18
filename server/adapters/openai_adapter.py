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

    def get_tools(self, user_query: str = "", max_tools: int = 10) -> List[Dict[str, Any]]:
        """
        Fetches tools from UMA and formats them for OpenAI.
        D-06 Filtered/Dynamic injection.
        """
        if not self.is_available:
            return []

        from server.adapters import select_relevant_tools

        all_tools = self.uma.get_tools_for_model("openai")

        if user_query and len(all_tools) > max_tools:
            all_tools = select_relevant_tools(user_query, all_tools, max_tools)
            
        # P-03: Flatten the tool schema for Responses API compatibility
        # D-07: V2 Optimization - Minimize schemas for knowledge-type tools to save tokens
        responses_api_tools = []
        core_execution_tools = ["mcp-python-executor", "mcp-builder", "mcp-skill-builder"]
        
        for t in all_tools:
            if t.get("type") == "function" and "function" in t:
                fn = t["function"]
                fn_name = fn.get("name")
                
                # Keep parameters if they exist, otherwise use empty object
                params = fn.get("parameters", {"type": "object", "properties": {}})
                
                responses_api_tools.append({
                    "type": "function",
                    "name": fn_name,
                    "description": fn.get("description", ""),
                    "parameters": params
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

    def chat(self, messages: Any, user_query: Optional[str] = None, session_id: Optional[str] = None, attached_file: Optional[str] = None, temperature: float = 0.7, **kwargs) -> Dict[str, Any]:
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

        tools = self.get_tools(user_query=user_query)
        tool_calls_made = 0
        MAX_ITERATIONS = 10  # Safety cap
        
        # We will track the latest response id generated in this multi-round loop
        current_response_id = prev_response_id

        try:
            import time
            for _ in range(MAX_ITERATIONS):
                MAX_RETRIES = 2
                last_error = None
                response = None
                
                for attempt in range(MAX_RETRIES + 1):
                    try:
                        kwargs_req = {
                            "model": self.model,
                            "input": input_payload,
                            "stream": True,
                            "temperature": temperature
                        }
                        if current_response_id:
                            kwargs_req["previous_response_id"] = current_response_id
                        if tools:
                            kwargs_req["tools"] = tools

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
                        break # Success, exit retry loop
                        
                    except Exception as e:
                        last_error = e
                        if "Rate limit reached" in str(e) or "rate_limit" in str(e).lower():
                            if attempt < MAX_RETRIES:
                                logger.warning(f"[OpenAI Adapter] Rate limit hit. Sleep 5s (Attempt {attempt+1}/{MAX_RETRIES})...")
                                yield {"status": "streaming", "content": "\n(系統繁忙中，稍後將自動重試...)\n"}
                                time.sleep(5)
                                continue
                        raise e # Fatal or retries exhausted

                full_content = ""
                tool_calls_dict = {}
                
                for chunk in response:
                    ctype = chunk.type
                    if ctype == "response.created" or ctype == "response.in_progress":
                        current_response_id = chunk.response.id
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

                if tool_calls_dict:
                    tool_results = []
                    for item_id, tc_data in tool_calls_dict.items():
                        fn_name = tc_data.get("name")
                        fn_args_str = tc_data.get("arguments", "{}")
                        call_id = tc_data.get("call_id") or item_id
                        
                        import json
                        try:
                            fn_args = json.loads(fn_args_str) if fn_args_str else {}
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse tool args: {fn_args_str}")
                            fn_args = {}

                        logger.info(f"Tool call: {fn_name}({fn_args})")
                        yield {"status": "streaming", "content": f"\n\n⚙️ 執行技能: `{fn_name}`\n"}
                        result = self.uma.execute_tool_call(fn_name, fn_args)

                        if result.get("status") == "requires_approval":
                            yield {
                                "status": "requires_approval",
                                "tool_name": fn_name,
                                "risk_description": result.get("risk_description", "High-risk operation"),
                                "pending_args": fn_args
                            }
                            return

                        tool_results.append({
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result, ensure_ascii=False)
                        })
                        tool_calls_made += 1

                    input_payload = tool_results
                    continue 

                else:
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
            logger.error(f"OpenAI chat error: {e}")
            yield {"status": "error", "message": str(e)}


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
            logger.error(f"OpenAI simple_chat error: {e}")
            yield {"status": "error", "message": str(e)}
