"""
MCP Server - FastAPI Router (Phase 3)
API endpoints for LLM tool interaction with Token-aware resource access.
"""
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging

from main import get_uma

logger = logging.getLogger("MCP_Server.Router")

app = FastAPI(
    title="MCP Skill Server",
    description="Unified Model Adapter — Bridges LLMs with GitHub Skills",
    version="0.1.0"
)


# --- Request/Response Models ---
class ExecuteRequest(BaseModel):
    skill_name: str
    arguments: Dict[str, Any] = {}


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
