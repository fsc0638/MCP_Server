"""User-scoped document center routes."""

from __future__ import annotations

import html
import os
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Cookie, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from server.schemas.user_documents import UserDocumentRenameRequest
from server.services.docx_preview import render_docx_to_html
from server.services.user_document_service import ExpiredDocumentError, user_document_service

router = APIRouter(prefix="/api/user-documents", tags=["User Documents"])


VIEWER_PAGE_CSS = """
  :root {
    color-scheme: light;
    --page-bg: #edf3fb;
    --card-bg: rgba(255,255,255,0.94);
    --card-border: rgba(148,163,184,0.22);
    --text-main: #0f172a;
    --text-muted: #475569;
    --accent: #18409b;
    --accent-soft: #e8f0ff;
    --table-head: #eef4ff;
    --table-border: #dbe4f0;
    --shadow: 0 20px 45px rgba(15, 23, 42, 0.08);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background:
      radial-gradient(circle at top left, rgba(24,64,155,0.10), transparent 38%),
      linear-gradient(180deg, #f5f8ff 0%, var(--page-bg) 100%);
    font-family: "Segoe UI", "Noto Sans TC", Arial, sans-serif;
    color: var(--text-main);
  }
  main { max-width: 1120px; margin: 0 auto; padding: 28px 20px 36px; }
  .viewer-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 24px;
    box-shadow: var(--shadow);
    overflow: hidden;
    backdrop-filter: blur(10px);
  }
  .viewer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    padding: 22px 24px;
    border-bottom: 1px solid var(--card-border);
    background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(246,249,255,0.96));
  }
  .viewer-title {
    margin: 0;
    font-size: 1.5rem;
    line-height: 1.25;
    letter-spacing: -0.02em;
  }
  .viewer-subtitle {
    margin: 8px 0 0;
    color: var(--text-muted);
    font-size: 0.92rem;
  }
  .viewer-actions {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }
  .viewer-action {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 42px;
    padding: 0 16px;
    border-radius: 999px;
    text-decoration: none;
    font-weight: 700;
    font-size: 0.92rem;
    border: 1px solid var(--card-border);
    color: var(--text-main);
    background: #fff;
  }
  .viewer-action.is-primary {
    background: var(--accent);
    color: #fff;
    border-color: transparent;
  }
  .viewer-action.is-selected {
    background: var(--accent-soft);
    color: var(--accent);
    border-color: rgba(24,64,155,0.18);
  }
  .viewer-body {
    padding: 24px;
  }
  .docx-preview-shell {
    background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(248,250,252,0.98));
    border: 1px solid var(--table-border);
    border-radius: 18px;
    padding: 20px;
  }
  .docx-preview-tip {
    margin-bottom: 16px;
    padding: 12px 14px;
    border-radius: 14px;
    background: var(--accent-soft);
    color: var(--text-muted);
    font-size: 0.86rem;
    line-height: 1.6;
  }
  .docx-preview-fragment {
    color: var(--text-main);
    font-size: 1rem;
    line-height: 1.75;
  }
  .docx-preview-fragment h1,
  .docx-preview-fragment h2,
  .docx-preview-fragment h3,
  .docx-preview-fragment h4,
  .docx-preview-fragment h5,
  .docx-preview-fragment h6 {
    margin: 1.25em 0 0.45em;
    line-height: 1.3;
    letter-spacing: -0.02em;
  }
  .docx-preview-fragment h1:first-child,
  .docx-preview-fragment h2:first-child,
  .docx-preview-fragment h3:first-child {
    margin-top: 0;
  }
  .docx-preview-title {
    margin-top: 0;
    margin-bottom: 0.4em;
    font-size: 1.95rem;
  }
  .docx-preview-subtitle {
    margin-top: 0;
    color: var(--text-muted);
    font-size: 1.02rem;
  }
  .docx-preview-fragment p {
    margin: 0 0 0.95em;
  }
  .docx-preview-fragment ul,
  .docx-preview-fragment ol {
    margin: 0 0 1.1em;
    padding-left: 1.5em;
  }
  .docx-preview-fragment li + li {
    margin-top: 0.35em;
  }
  .docx-preview-spacer {
    height: 0.9rem;
  }
  .docx-preview-table-wrap {
    margin: 1.1em 0 1.3em;
    overflow-x: auto;
    border: 1px solid var(--table-border);
    border-radius: 16px;
    background: #fff;
  }
  .docx-preview-table {
    width: 100%;
    border-collapse: collapse;
    min-width: 420px;
  }
  .docx-preview-table th,
  .docx-preview-table td {
    padding: 12px 14px;
    border-bottom: 1px solid var(--table-border);
    border-right: 1px solid var(--table-border);
    vertical-align: top;
    text-align: left;
    line-height: 1.65;
    font-size: 0.94rem;
  }
  .docx-preview-table th:last-child,
  .docx-preview-table td:last-child {
    border-right: none;
  }
  .docx-preview-table tr:last-child td {
    border-bottom: none;
  }
  .docx-preview-table thead th {
    background: var(--table-head);
    font-weight: 700;
  }
  @media (max-width: 720px) {
    main { padding: 16px 12px 24px; }
    .viewer-card { border-radius: 18px; }
    .viewer-header {
      flex-direction: column;
      align-items: flex-start;
      padding: 18px;
    }
    .viewer-body {
      padding: 16px;
    }
    .docx-preview-shell {
      padding: 14px;
    }
    .docx-preview-title {
      font-size: 1.55rem;
    }
  }
"""


def _ascii_header_filename(filename: str) -> str:
    cleaned = (filename or "").replace('"', "_").replace("\\", "_").replace("/", "_")
    cleaned = cleaned.replace("\r", " ").replace("\n", " ").strip().strip(". ")
    ext = os.path.splitext(cleaned)[1]
    ascii_name = cleaned.encode("ascii", "ignore").decode("ascii").strip().strip(". ")

    if not ascii_name or ascii_name == ext:
        if ext and ext.isascii():
            return f"document{ext}"
        return "document"

    if ascii_name.startswith("."):
        return f"document{ascii_name}"
    return ascii_name


def _build_content_disposition(disposition: str, filename: str) -> str:
    safe_disposition = "inline" if disposition == "inline" else "attachment"
    safe_filename = (filename or "document").replace('"', "_").replace("\r", " ").replace("\n", " ")
    fallback = _ascii_header_filename(safe_filename)
    encoded = quote(safe_filename, safe="")
    return f"{safe_disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


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


def _build_text_pre_block(text: str) -> str:
    return (
        '<pre style="white-space:pre-wrap;word-break:break-word;line-height:1.75;'
        'font-size:15px;color:#0f172a;background:#f8fafc;border:1px solid #dbe4f0;'
        'padding:20px;border-radius:16px;overflow:auto;max-height:78vh;">'
        f'{html.escape(text or "這份文件目前沒有可顯示的內容。")}</pre>'
    )


def _build_error_block(title: str, message: str) -> str:
    safe_title = html.escape(title)
    safe_message = html.escape(message or "目前無法完成這個預覽。")
    return (
        '<section style="padding:18px 20px;border-radius:18px;'
        'background:linear-gradient(180deg,#fff7ed,#fff);border:1px solid rgba(249,115,22,0.18);">'
        f'<h2 style="margin:0 0 8px;font-size:1.05rem;color:#9a3412;">{safe_title}</h2>'
        f'<p style="margin:0;color:#7c2d12;line-height:1.7;">{safe_message}</p>'
        "</section>"
    )


def _build_viewer_actions(doc_id: str, mode: str, is_docx: bool) -> str:
    if is_docx:
        actions = [
            (f"/api/user-documents/{doc_id}/viewer", "HTML 預覽", "is-selected" if mode != "pdf" else ""),
            (f"/api/user-documents/{doc_id}/viewer?mode=pdf", "PDF 預覽", "is-selected" if mode == "pdf" else ""),
            (f"/api/user-documents/{doc_id}/file?disposition=attachment", "下載原檔", "is-primary"),
        ]
    else:
        actions = [
            (f"/api/user-documents/{doc_id}/file?disposition=inline", "原檔開啟", ""),
            (f"/api/user-documents/{doc_id}/file?disposition=attachment", "下載原檔", "is-primary"),
        ]

    html_parts: list[str] = []
    for href, label, css_class in actions:
        class_name = "viewer-action"
        if css_class:
            class_name += f" {css_class}"
        html_parts.append(f'<a class="{class_name}" href="{href}">{label}</a>')
    return "".join(html_parts)


def _build_viewer_page(title: str, subtitle: str, body: str, actions_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>{VIEWER_PAGE_CSS}</style>
</head>
<body>
  <main>
    <section class="viewer-card">
      <header class="viewer-header">
        <div>
          <h1 class="viewer-title">{title}</h1>
          <p class="viewer-subtitle">{subtitle}</p>
        </div>
        <div class="viewer-actions">{actions_html}</div>
      </header>
      <div class="viewer-body">
        {body}
      </div>
    </section>
  </main>
</body>
</html>"""


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
def list_user_documents(
    include_expired: bool = Query(default=False),
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    user_key, _ = _resolve_current_user(mcp_session)
    documents = user_document_service.list_documents(user_key, include_expired=include_expired)
    return {"status": "success", "total": len(documents), "documents": documents}


@router.get("/{doc_id}")
def get_user_document(doc_id: str, mcp_session: str = Cookie(default="", alias="mcp_session")):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        document = user_document_service.get_document(user_key, doc_id)
        return {"status": "success", "document": document}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ExpiredDocumentError as exc:
        raise HTTPException(status_code=410, detail=str(exc))


@router.get("/{doc_id}/preview")
def preview_user_document(doc_id: str, mcp_session: str = Cookie(default="", alias="mcp_session")):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        payload = user_document_service.build_preview_payload(user_key, doc_id)
        payload["viewer_url"] = f"/api/user-documents/{doc_id}/viewer"
        if payload.get("preview_type") == "html-inline":
            payload["inline_url"] = payload["viewer_url"]
            payload["pdf_inline_url"] = f"/api/user-documents/{doc_id}/pdf-preview"
            payload["pdf_viewer_url"] = f"/api/user-documents/{doc_id}/viewer?mode=pdf"
        else:
            payload["inline_url"] = f"/api/user-documents/{doc_id}/file?disposition=inline"
        payload["download_url"] = f"/api/user-documents/{doc_id}/file?disposition=attachment"
        payload["content_url"] = f"/api/user-documents/{doc_id}/content"
        return payload
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ExpiredDocumentError as exc:
        raise HTTPException(status_code=410, detail=str(exc))


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
    except ExpiredDocumentError as exc:
        raise HTTPException(status_code=410, detail=str(exc))


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
        headers = {"Content-Disposition": _build_content_disposition(disposition, filename)}
        return FileResponse(
            path=path,
            media_type=document.get("mime_type") or "application/octet-stream",
            headers=headers,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ExpiredDocumentError as exc:
        raise HTTPException(status_code=410, detail=str(exc))


@router.get("/{doc_id}/pdf-preview")
def open_user_document_pdf_preview(doc_id: str, mcp_session: str = Cookie(default="", alias="mcp_session")):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        document = user_document_service.get_document(user_key, doc_id)
        pdf_path = user_document_service.ensure_docx_pdf_preview(user_key, doc_id)
        display_name = document.get("display_name") or document.get("original_filename") or f"{doc_id}.docx"
        pdf_name = os.path.splitext(display_name)[0] + ".pdf"
        headers = {"Content-Disposition": _build_content_disposition("inline", pdf_name)}
        return FileResponse(path=pdf_path, media_type="application/pdf", headers=headers)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ExpiredDocumentError as exc:
        raise HTTPException(status_code=410, detail=str(exc))


@router.get("/{doc_id}/viewer", response_class=HTMLResponse)
def view_user_document(
    doc_id: str,
    mode: str = Query(default="auto"),
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        payload = user_document_service.build_preview_payload(user_key, doc_id)
        document = payload["document"]
        title = html.escape(document.get("display_name") or document.get("original_filename") or "Document Viewer")
        subtitle = "User Document Center Preview"
        is_docx = payload.get("preview_type") == "html-inline"
        mode = "pdf" if (is_docx and mode == "pdf") else "html"

        if payload.get("preview_type") == "pdf-inline":
            body = (
                f'<iframe src="/api/user-documents/{doc_id}/file?disposition=inline" '
                'style="width:100%;height:78vh;border:none;border-radius:16px;background:#fff;"></iframe>'
            )
        elif payload.get("preview_type") == "html-inline":
            if mode == "pdf":
                try:
                    user_document_service.ensure_docx_pdf_preview(user_key, doc_id)
                    subtitle = "DOCX PDF Preview"
                    body = (
                        f'<iframe src="/api/user-documents/{doc_id}/pdf-preview" '
                        'style="width:100%;height:78vh;border:none;border-radius:16px;background:#fff;"></iframe>'
                    )
                except RuntimeError as exc:
                    subtitle = "DOCX PDF Preview Unavailable"
                    body = _build_error_block("暫時無法產生 PDF 預覽", str(exc))
            else:
                try:
                    path = user_document_service.get_document_path(user_key, doc_id)
                    rendered_html = render_docx_to_html(str(path))
                    subtitle = "DOCX HTML Preview"
                    body = (
                        '<section class="docx-preview-shell">'
                        '<div class="docx-preview-tip">已將 Word 文件轉為服務內 HTML 預覽，保留段落與表格結構。</div>'
                        f"{rendered_html}"
                        "</section>"
                    )
                except Exception as exc:
                    _, text = user_document_service.get_text_content(user_key, doc_id)
                    subtitle = "DOCX Text Preview (HTML fallback)"
                    body = _build_error_block("DOCX HTML 預覽失敗，已改用文字內容", str(exc))
                    body += _build_text_pre_block(text)
        else:
            _, text = user_document_service.get_text_content(user_key, doc_id)
            body = _build_text_pre_block(text)

        actions_html = _build_viewer_actions(doc_id, mode, is_docx)
        return HTMLResponse(_build_viewer_page(title, subtitle, body, actions_html))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ExpiredDocumentError as exc:
        raise HTTPException(status_code=410, detail=str(exc))


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
    except ExpiredDocumentError as exc:
        raise HTTPException(status_code=410, detail=str(exc))


@router.delete("/{doc_id}")
def delete_user_document(doc_id: str, mcp_session: str = Cookie(default="", alias="mcp_session")):
    user_key, _ = _resolve_current_user(mcp_session)
    try:
        user_document_service.delete_document(user_key, doc_id)
        return {"status": "success", "doc_id": doc_id}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ExpiredDocumentError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
