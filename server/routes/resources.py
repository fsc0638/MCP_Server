"""Tools/resources routes."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from main import get_uma

router = APIRouter(tags=["Resources"])


class SearchRequest(BaseModel):
    query: str


@router.get("/tools")
def list_tools(model: str = Query("openai")):
    """List tool definitions for agent mode."""
    uma = get_uma()
    tools = uma.get_tools_for_model(model)
    return {"model": model, "tool_count": len(tools), "tools": tools}


@router.get("/resources/{skill_name}/{file_name}")
def read_resource(skill_name: str, file_name: str, limit: int = Query(500, ge=0)):
    uma = get_uma()
    result: Dict[str, Any] = uma.executor.read_resource(skill_name, file_name)
    if result.get("status") != "success":
        raise HTTPException(status_code=404, detail=result.get("message"))
    content = result.get("content", "")
    truncated = limit > 0 and len(content) > limit
    if truncated:
        content = content[:limit]
    return {"status": "success", "content": content, "truncated": truncated}


@router.post("/search/{skill_name}/{file_name}")
def search_resource(skill_name: str, file_name: str, request: SearchRequest):
    uma = get_uma()
    result: Dict[str, Any] = uma.executor.search_resource(skill_name, file_name, request.query)
    if result.get("status") != "success":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result
