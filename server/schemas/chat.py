"""Chat-related request schemas."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_input: str
    session_id: Optional[str] = "default"
    turn_id: Optional[str] = None
    user_id: Optional[str] = None
    model: Optional[str] = "openai"
    provider: Optional[str] = None
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    injected_skill: Optional[str] = None
    execute: Optional[bool] = False
    attached_file: Optional[str] = None
    user_document_id: Optional[str] = None
    user_document_action: Optional[str] = None
    upload_handoff: Optional[bool] = False
    selected_docs: Optional[list[str]] = None
    temperature: Optional[float] = 0.7
    language: Optional[str] = "繁體中文"
    detail_level: Optional[str] = "適中"


class ExecuteRequest(BaseModel):
    skill_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class TitleSummaryRequest(BaseModel):
    user_input: str
    assistant_output: str
    provider: Optional[str] = None
    model: Optional[str] = None
    language: Optional[str] = "繁體中文"
