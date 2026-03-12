"""Chat-related request schemas."""

from typing import Any, Dict, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    user_input: str
    session_id: Optional[str] = "default"
    model: Optional[str] = "openai"
    provider: Optional[str] = None
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    injected_skill: Optional[str] = None
    execute: Optional[bool] = False
    attached_file: Optional[str] = None
    selected_docs: Optional[list[str]] = None
    temperature: Optional[float] = 0.7


class ExecuteRequest(BaseModel):
    skill_name: str
    arguments: Dict[str, Any] = {}

