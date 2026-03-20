"""Document-related request schemas."""

from pydantic import BaseModel


class RenameRequest(BaseModel):
    new_name: str


class UrlSourcingRequest(BaseModel):
    url: str


class TextSourcingRequest(BaseModel):
    name: str
    content: str


class ResearchRequest(BaseModel):
    query: str

