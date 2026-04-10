"""Skill-related request schemas."""

from pydantic import BaseModel


class SkillUpdateRequest(BaseModel):
    yaml_content: str
    user_name: str = "unknown"
    user_id: str = "unknown"


class SkillDeleteRequest(BaseModel):
    reason: str
    user_name: str = "unknown"
    user_id: str = "unknown"


class CreateSkillRequest(BaseModel):
    name: str
    display_name: str
    description: str
    version: str = "1.0.0"
    category: str = ""
    no_script: bool = False

