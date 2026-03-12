"""Resource-related request schemas."""

from pydantic import BaseModel


class SearchRequest(BaseModel):
    query: str

