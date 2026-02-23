from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
import json
import asyncio
from sse_starlette.sse import EventSourceResponse

from main import get_uma, PROJECT_ROOT

logger = logging.getLogger("MCP_Server.Router")

# Reload trigger for CIS Guideline update
app = FastAPI(
    title="MCP Skill Server",
    description="Unified Model Adapter — Bridges LLMs with GitHub Skills",
    version="0.1.0"
)

# --- Static File Serving (Decoupled UI) ---
static_dir = PROJECT_ROOT / "static"
if not static_dir.exists():
    static_dir.mkdir()
app.mount("/ui", StaticFiles(directory=str(static_dir)), name="static")


# --- Request/Response Models ---
class ExecuteRequest(BaseModel):
    skill_name: str
    arguments: Dict[str, Any] = {}


class ChatRequest(BaseModel):
    user_input: str
    session_id: Optional[str] = "web_user"
    model: Optional[str] = "openai"


class SearchRequest(BaseModel):
    query: str


class ExecuteResponse(BaseModel):
    status: str
    output: Optional[str] = None
    message: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    requires_approval: Optional[bool] = None
    risk_description: Optional[str] = None


# --- Routes ---

@app.get("/health")
def health_check():
    """System health check with dependency readiness overview."""
    uma = get_uma()
    skills_status = {}
    for name, data in uma.registry.skills.items():
        meta = data["metadata"]
        skills_status[name] = {
            "version": meta.get("version", "unknown"),
            "ready": meta.get("_env_ready", False),
            "missing_deps": meta.get("_missing_deps", [])
        }
    return {
        "status": "healthy",
        "total_skills": len(skills_status),
        "skills": skills_status
    }


@app.get("/tools")
def list_tools(model: str = Query("openai", description="Model type: openai, gemini, claude")):
    """Returns all registered tool definitions for the specified model, with degraded markers."""
    uma = get_uma()
    tools = uma.get_tools_for_model(model)
    return {"model": model, "tool_count": len(tools), "tools": tools}


@app.post("/execute", response_model=ExecuteResponse)
def execute_tool(request: ExecuteRequest):
    """Executes a skill script. Supports Human-in-the-loop via requires_approval status."""
    uma = get_uma()

    # Verify skill exists
    skill = uma.registry.get_skill(request.skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{request.skill_name}' not found")

    # Check if skill is in degraded mode
    if not skill["metadata"].get("_env_ready", False):
        return ExecuteResponse(
            status="unavailable",
            message=f"Skill '{request.skill_name}' is in DEGRADED mode. Missing: {skill['metadata'].get('_missing_deps', [])}"
        )

    result = uma.execute_tool_call(request.skill_name, request.arguments)

    return ExecuteResponse(
        status=result.get("status", "error"),
        output=result.get("output"),
        message=result.get("message"),
        stderr=result.get("stderr"),
        exit_code=result.get("exit_code"),
        requires_approval=result.get("requires_approval"),
        risk_description=result.get("risk_description")
    )


@app.get("/chat")
async def chat_stream(user_input: str, model: str = "openai", request: Request = None):
    """
    SSE Endpoint for real-time thought streaming.
    Streams events: 'thought', 'tool_start', 'tool_result', 'response', 'memory_sync'.
    """
    uma = get_uma()
    
    # Lazy imports for adapters to keep router light
    from adapters.openai_adapter import OpenAIAdapter
    from adapters.gemini_adapter import GeminiAdapter
    from adapters.claude_adapter import ClaudeAdapter
    
    async def event_generator():
        try:
            # 1. Initialize Adapter
            if model == "openai":
                adapter = OpenAIAdapter(uma)
            elif model == "gemini":
                adapter = GeminiAdapter(uma)
            else:
                adapter = ClaudeAdapter(uma)
                
            if not adapter.is_available:
                yield {"event": "error", "data": f"Model {model} is not available (check API keys)"}
                return

            # 2. Start Thinking
            yield {"event": "thought", "data": json.dumps({"message": f"Thinking about: {user_input}..."})}
            await asyncio.sleep(0.5) # Slight delay for UI feel
            
            # 3. Process with Adapter (this part is currently sync in UMA core)
            # In a real async system, adapter.chat would be async.
            # For now we wrap it or just call it.
            # To simulate "Thought Stream", the adapter itself would ideally yield events.
            # Since UMA is sync, we'll emit a 'processing' event.
            yield {"event": "status", "data": json.dumps({"status": "processing", "tool": "analyzing_intent"})}
            
            # Note: The current UMA adapters handle the tool-execution loop INTERNALLY.
            # This makes streaming granular steps difficult without refactoring UMA.
            # PROPOSAL: We'll wrap the executor to catch calls and emit events.
            
            result = adapter.chat(user_input)
            
            if result.get("status") == "success":
                yield {"event": "response", "data": json.dumps({"content": result.get("content", "")})}
            else:
                yield {"event": "error", "data": json.dumps({"message": result.get("message", "Unknown error")})}

            # 4. Final Memory Sync notification
            yield {"event": "memory_sync", "data": json.dumps({"status": "synced"})}

        except Exception as e:
            logger.error(f"SSE Error: {e}")
            yield {"event": "error", "data": str(e)}

    return EventSourceResponse(event_generator())


@app.get("/resources/{skill_name}/{file_name}")
def read_resource(
    skill_name: str,
    file_name: str,
    limit: int = Query(500, description="Max characters to return. 0 = unlimited.", ge=0)
):
    """
    Read a file from a skill's References/ directory.
    Token-aware: defaults to 500 chars. Use limit=0 for full content.
    """
    uma = get_uma()
    result = uma.executor.read_resource(skill_name, file_name)

    if result["status"] != "success":
        raise HTTPException(status_code=404, detail=result.get("message", "Resource not found"))

    content = result["content"]
    truncated = False

    if limit > 0 and len(content) > limit:
        content = content[:limit]
        truncated = True

    return {
        "status": "success",
        "content": content,
        "truncated": truncated,
        "total_length": len(result["content"]),
        "returned_length": len(content)
    }


@app.post("/search/{skill_name}/{file_name}")
def search_resource(skill_name: str, file_name: str, request: SearchRequest):
    """Grep-like search within a skill's References/ directory. Max 50 matches."""
    uma = get_uma()
    result = uma.executor.search_resource(skill_name, file_name, request.query)

    if result["status"] != "success":
        raise HTTPException(status_code=404, detail=result.get("message", "Resource not found"))

    return result
