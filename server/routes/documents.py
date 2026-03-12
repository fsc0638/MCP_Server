"""Document routes (migration bridge).

These handlers delegate to legacy implementations in ``router.py`` so we can
move route ownership first, then internal logic in later phases.
"""

from fastapi import APIRouter, BackgroundTasks, File, UploadFile

from router import (
    UrlSourcingRequest,
    ResearchRequest,
    TextSourcingRequest,
    RenameRequest,
    upload_document as legacy_upload_document,
    add_url_source as legacy_add_url_source,
    research_sources as legacy_research_sources,
    add_text_source as legacy_add_text_source,
    list_documents as legacy_list_documents,
    delete_document_endpoint as legacy_delete_document_endpoint,
    rename_document_endpoint as legacy_rename_document_endpoint,
)

router = APIRouter(tags=["Documents"])


@router.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    return await legacy_upload_document(file=file, background_tasks=background_tasks)


@router.post("/api/documents/url")
async def add_url_source(req: UrlSourcingRequest):
    return await legacy_add_url_source(req)


@router.post("/api/research")
async def research_sources(req: ResearchRequest):
    return await legacy_research_sources(req)


@router.post("/api/documents/text")
async def add_text_source(req: TextSourcingRequest):
    return await legacy_add_text_source(req)


@router.get("/api/documents/list")
def list_documents():
    return legacy_list_documents()


@router.delete("/api/documents/{filename}")
def delete_document(filename: str):
    return legacy_delete_document_endpoint(filename)


@router.post("/api/documents/{filename}/rename")
def rename_document(filename: str, req: RenameRequest):
    return legacy_rename_document_endpoint(filename, req)

