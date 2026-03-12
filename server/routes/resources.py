"""Legacy tools/resources routes (migration bridge)."""

from fastapi import APIRouter, Query

from router import (
    SearchRequest,
    list_tools as legacy_list_tools,
    read_resource as legacy_read_resource,
    search_resource as legacy_search_resource,
)

router = APIRouter(tags=["Resources"])


@router.get("/tools")
def list_tools(model: str = Query("openai")):
    return legacy_list_tools(model)


@router.get("/resources/{skill_name}/{file_name}")
def read_resource(skill_name: str, file_name: str, limit: int = Query(500, ge=0)):
    return legacy_read_resource(skill_name, file_name, limit)


@router.post("/search/{skill_name}/{file_name}")
def search_resource(skill_name: str, file_name: str, request: SearchRequest):
    return legacy_search_resource(skill_name, file_name, request)

