"""Skill-related request schemas."""

from pydantic import BaseModel


class SkillUpdateRequest(BaseModel):
    yaml_content: str


class CreateSkillRequest(BaseModel):
    name: str
    display_name: str
    description: str
    version: str = "1.0.0"
    category: str = ""
    no_script: bool = False

