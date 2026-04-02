
import re

with open('server/adapters/openai_adapter.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern to find the section to replace:
start_str = "                        response = self.client.responses.create(**kwargs_req)\n                        break # Success, exit retry loop\n                        \n                    except Exception as e:\n                        last_error = e\n                        err_str = str(e)\n                        if \"Rate limit reached\" in err_str or \"rate_limit\" in err_str.lower():\n                            if attempt < MAX_RETRIES:\n                                # Parse recommended wait time from error message\n                                import re as _re\n                                wait_match = _re.search(r'try again in (\d+(?:\\.\d+)?)\s*s', err_str)\n                                wait_sec = float(wait_match.group(1)) + 2 if wait_match else 20\n                                wait_sec = min(wait_sec, 60)  # Cap at 60s\n                                logger.warning(f\"[OpenAI Adapter] Rate limit hit. Wait {wait_sec:.0f}s (Attempt {attempt+1}/{MAX_RETRIES})...\")\n                                yield {\"status\": \"streaming\", \"content\": f\"\\n⏳ 系統繁忙中，{wait_sec:.0f} 秒後自動重試（第 {attempt+1} 次）...\\n\"}\n                                time.sleep(wait_sec)\n                                continue\n                        raise e # Fatal or retries exhausted\n\n                full_content = \"\"\n                tool_calls_dict = {}\n                _turn_usage = {\"input_tokens\": 0, \"output_tokens\": 0, \"total_tokens\": 0}\n                _turn_start_ms = int(time.time() * 1000)\n\n                for chunk in response:\n                    ctype = chunk.type\n                    if ctype == \"response.created\" or ctype == \"response.in_progress\":\n                        current_response_id = chunk.response.id\n                    elif ctype == \"response.completed\":\n                        # Capture token usage from completed response\n                        _resp = getattr(chunk, \"response\", None)\n                        if _resp:\n                            _u = getattr(_resp, \"usage\", None)\n                            if _u:\n                                _turn_usage[\"input_tokens\"] = getattr(_u, \"input_tokens\", 0)\n                                _turn_usage[\"output_tokens\"] = getattr(_u, \"output_tokens\", 0)\n                                _turn_usage[\"total_tokens\"] = getattr(_u, \"total_tokens\", 0)\n                                logger.info(f\"[OpenAI D1] Usage: in={_turn_usage['input_tokens']} out={_turn_usage['output_tokens']} total={_turn_usage['total_tokens']}\")\n                    elif ctype == \"response.output_text.delta\":\n                        text = chunk.delta\n                        full_content += text\n                        yield {\"status\": \"streaming\", \"content\": text}\n                    elif ctype == \"response.function_call_arguments.delta\":\n                        item_id = chunk.item_id\n                        if item_id not in tool_calls_dict:\n                            tool_calls_dict[item_id] = {\"arguments\": \"\", \"name\": \"\", \"call_id\": item_id}\n                        tool_calls_dict[item_id][\"arguments\"] += chunk.delta\n                    elif ctype == \"response.output_item.done\":\n                        item = chunk.item\n                        if getattr(item, 'type', None) == 'function_call':\n                            item_id = item.id\n                            if item_id not in tool_calls_dict:\n                                tool_calls_dict[item_id] = {\"arguments\": \"\", \"name\": item.name, \"call_id\": getattr(item, 'call_id', item.id)}\n                            tool_calls_dict[item_id][\"name\"] = item.name or tool_calls_dict[item_id][\"name\"]\n                            tool_calls_dict[item_id][\"call_id\"] = getattr(item, 'call_id', item.id)\n                            if hasattr(item, 'arguments') and item.arguments:\n                                tool_calls_dict[item_id][\"arguments\"] = item.arguments"

replaced_str = """                        response = self.client.responses.create(**kwargs_req)
                        
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
                        if "Rate limit reached" in err_str or "rate_limit" in err_str.lower():
                            if attempt < MAX_RETRIES:
                                # Parse recommended wait time from error message
                                import re as _re
                                wait_match = _re.search(r'try again in (\\d+(?:\\.\\d+)?)\\s*s', err_str)
                                wait_sec = float(wait_match.group(1)) + 2 if wait_match else 20
                                wait_sec = min(wait_sec, 60)  # Cap at 60s
                                logger.warning(f"[OpenAI Adapter] Rate limit hit. Wait {wait_sec:.0f}s (Attempt {attempt+1}/{MAX_RETRIES})...")
                                yield {"status": "streaming", "content": f"\\n⏳ 系統繁忙中，{wait_sec:.0f} 秒後自動重試（第 {attempt+1} 次）...\\n"}
                                time.sleep(wait_sec)
                                continue
                        raise e # Fatal or retries exhausted"""

if start_str in content:
    new_content = content.replace(start_str, replaced_str)
    with open('server/adapters/openai_adapter.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Successfully patched openai_adapter.py")
else:
    print("Could not find the target string in openai_adapter.py")
