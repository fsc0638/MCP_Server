"""Schemas for the user document center."""

from pydantic import BaseModel


class UserDocumentRenameRequest(BaseModel):
    display_name: str
