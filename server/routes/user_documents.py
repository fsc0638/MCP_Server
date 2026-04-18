"""User-scoped document center routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, BackgroundTasks, Cookie, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from server.schemas.user_documents import UserDocumentRenameRequest
from server.services.user_document_service import user_document_service

router = APIRouter(prefix="/api/user-documents", tags=["User Documents"])


def _resolve_current_user(mcp_session: str) -> tuple[str, str]:
    try:
        return user_document_service.resolve_user_from_cookie(mcp_session)
    except PermissionError as exc:
        reason = str(exc)
        if reason == "not_logged_in":
            raise HTTPException(status_code=401, detail=reason)
        if reason in {"invalid_session", "session_expired"}:
            raise HTTPException(status_code=401, detail=reason)
        raise HTTPException(status_code=500, detail=reason)


@router.post("/upload")
async def upload_user_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    user_key, raw_user_id = _resolve_current_user(mcp_session)
    try:
        content = await file.read()
        document = user_document_service.create_document(
            user_key=user_key,
            raw_user_id=raw_user_id,
            filename=file.filename or "uploaded_file",
            content=content,
        )
        if document.get("text_extract_status") == "pending":
            background_tasks.add_task(user_document_service.ensure_text_cache, user_key, document["doc_id"])
        return {"status": "success", "document": document}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("")
def list_user_documents(mcp_session: str = Cookie(default="", alias="mcp_session")):
    user_key, _ = _resolve_current_user(mcp_session)
    documents = user_document_service.list_documents(user_key)
    return {"status": "success", "total": len(documents), "documents": documents}


@router.get("/{doc_id}")
def get_user_document(doc_id: str, mcp_session: str = Cookie(default="", alias="mcp_session")):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        document = user_document_service.get_document(user_key, doc_id)
        return {"status": "success", "document": document}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{doc_id}/preview")
def preview_user_document(doc_id: str, mcp_session: str = Cookie(default="", alias="mcp_session")):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        payload = user_document_service.build_preview_payload(user_key, doc_id)
        payload["inline_url"] = f"/api/user-documents/{doc_id}/file?disposition=inline"
        payload["download_url"] = f"/api/user-documents/{doc_id}/file?disposition=attachment"
        payload["content_url"] = f"/api/user-documents/{doc_id}/content"
        return payload
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{doc_id}/content")
def get_user_document_content(
    doc_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50000, ge=1, le=200000),
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        document, text = user_document_service.get_text_content(user_key, doc_id)
        sliced = text[offset : offset + limit]
        return {
            "status": "success",
            "document": document,
            "content": sliced,
            "offset": offset,
            "limit": limit,
            "total_chars": len(text),
            "truncated": offset + limit < len(text),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{doc_id}/file")
def open_user_document_file(
    doc_id: str,
    disposition: str = Query(default="attachment"),
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        document = user_document_service.get_document(user_key, doc_id)
        path = user_document_service.get_document_path(user_key, doc_id)
        filename = document.get("display_name") or document.get("original_filename") or os.path.basename(str(path))
        filename = filename.replace('"', "_")
        safe_disposition = "inline" if disposition == "inline" else "attachment"
        headers = {"Content-Disposition": f'{safe_disposition}; filename="{filename}"'}
        return FileResponse(
            path=path,
            media_type=document.get("mime_type") or "application/octet-stream",
            headers=headers,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{doc_id}/rename")
def rename_user_document(
    doc_id: str,
    req: UserDocumentRenameRequest,
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        document = user_document_service.rename_document(user_key, doc_id, req.display_name)
        return {"status": "success", "document": document}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/{doc_id}")
def delete_user_document(doc_id: str, mcp_session: str = Cookie(default="", alias="mcp_session")):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        user_document_service.delete_document(user_key, doc_id)
        return {"status": "success", "doc_id": doc_id}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
