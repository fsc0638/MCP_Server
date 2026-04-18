"""User-scoped document center service."""

from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from main import PROJECT_ROOT
from server.services.auth_session_store import get_auth_session_store
from server.services.file_extractor import extract_file_content
from server.services.session_token_cookie import verify_token

USER_DOCUMENTS_DIR = PROJECT_ROOT / "Agent_workspace" / "user_documents"
USER_DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
TEXT_EXTRACTABLE_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}


def sanitize_filename(filename: str) -> str:
    """Preserve user-facing names while blocking traversal / invalid chars."""
    filename = os.path.basename(filename or "")
    illegal_chars = {"\\", "/", ":", "*", "?", '"', "<", ">", "|", "\x00"}
    filename = "".join("_" if c in illegal_chars else c for c in filename)
    filename = filename.strip(". ").strip()
    return filename or "uploaded_file"


def sanitize_user_key(user_id: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in (user_id or ""))
    safe = safe.strip("_")
    return safe or "anonymous"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class UserDocumentService:
    def __init__(self, root_dir: Path | None = None):
        self.root_dir = Path(root_dir or USER_DOCUMENTS_DIR)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def resolve_user_from_cookie(self, signed_cookie: str) -> Tuple[str, str]:
        """Return (folder_key, raw_user_id) for the current web session."""
        if not signed_cookie:
            raise PermissionError("not_logged_in")
        token = verify_token(signed_cookie)
        if not token:
            raise PermissionError("invalid_session")
        session = get_auth_session_store().get(token)
        if not session or not session.user_id:
            raise PermissionError("session_expired")
        raw_user_id = str(session.user_id)
        return sanitize_user_key(raw_user_id), raw_user_id

    def user_dir(self, user_key: str) -> Path:
        path = self.root_dir / sanitize_user_key(user_key)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def manifest_path(self, user_key: str) -> Path:
        return self.user_dir(user_key) / "manifest.json"

    def _load_manifest(self, user_key: str) -> Dict[str, Any]:
        path = self.manifest_path(user_key)
        if not path.exists():
            return {"documents": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"documents": []}
        documents = data.get("documents")
        if not isinstance(documents, list):
            data["documents"] = []
        return data

    def _save_manifest(self, user_key: str, data: Dict[str, Any]) -> None:
        path = self.manifest_path(user_key)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_document(self, user_key: str, doc_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        manifest = self._load_manifest(user_key)
        for item in manifest.get("documents", []):
            if item.get("doc_id") == doc_id:
                return manifest, item
        raise FileNotFoundError(f"Document '{doc_id}' not found")

    def _replace_document(self, manifest: Dict[str, Any], updated: Dict[str, Any]) -> None:
        documents = manifest.get("documents", [])
        for idx, item in enumerate(documents):
            if item.get("doc_id") == updated.get("doc_id"):
                documents[idx] = updated
                return
        documents.append(updated)

    def _text_cache_path(self, user_key: str, doc_id: str) -> Path:
        return self.user_dir(user_key) / f"{doc_id}.extracted.txt"

    def _build_document_record(
        self,
        raw_user_id: str,
        original_filename: str,
        stored_filename: str,
        size: int,
    ) -> Dict[str, Any]:
        ext = Path(stored_filename).suffix.lower()
        mime_type = mimetypes.guess_type(stored_filename)[0] or "application/octet-stream"
        doc_id = Path(stored_filename).stem
        created_at = now_iso()
        return {
            "doc_id": doc_id,
            "user_id": raw_user_id,
            "original_filename": original_filename,
            "display_name": original_filename,
            "stored_filename": stored_filename,
            "extension": ext,
            "mime_type": mime_type,
            "size": size,
            "created_at": created_at,
            "updated_at": created_at,
            "preview_type": "pdf-inline" if ext == ".pdf" else "text",
            "text_extract_status": "pending" if ext in TEXT_EXTRACTABLE_EXTENSIONS else "unsupported",
        }

    def create_document(self, user_key: str, raw_user_id: str, filename: str, content: bytes) -> Dict[str, Any]:
        safe_name = sanitize_filename(filename)
        ext = Path(safe_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {ext or '(none)'}")

        doc_id = f"doc_{uuid4().hex[:12]}"
        stored_filename = f"{doc_id}{ext}"
        path = self.user_dir(user_key) / stored_filename
        path.write_bytes(content)

        manifest = self._load_manifest(user_key)
        record = self._build_document_record(
            raw_user_id=raw_user_id,
            original_filename=safe_name,
            stored_filename=stored_filename,
            size=len(content),
        )
        manifest["documents"].append(record)
        self._save_manifest(user_key, manifest)
        return record

    def list_documents(self, user_key: str) -> List[Dict[str, Any]]:
        manifest = self._load_manifest(user_key)
        documents = []
        for item in manifest.get("documents", []):
            doc = dict(item)
            cache_exists = self._text_cache_path(user_key, doc.get("doc_id", "")).exists()
            doc["has_text_cache"] = cache_exists
            documents.append(doc)
        documents.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return documents

    def get_document(self, user_key: str, doc_id: str) -> Dict[str, Any]:
        _, item = self._find_document(user_key, doc_id)
        doc = dict(item)
        doc["has_text_cache"] = self._text_cache_path(user_key, doc_id).exists()
        return doc

    def get_document_path(self, user_key: str, doc_id: str) -> Path:
        _, item = self._find_document(user_key, doc_id)
        path = (self.user_dir(user_key) / item["stored_filename"]).resolve()
        if not str(path).startswith(str(self.user_dir(user_key).resolve())):
            raise ValueError("Invalid document path")
        if not path.exists():
            raise FileNotFoundError(f"Document file missing: {doc_id}")
        return path

    def ensure_text_cache(self, user_key: str, doc_id: str) -> Dict[str, Any]:
        manifest, item = self._find_document(user_key, doc_id)
        updated = dict(item)
        if updated.get("extension") not in TEXT_EXTRACTABLE_EXTENSIONS:
            updated["text_extract_status"] = "unsupported"
            self._replace_document(manifest, updated)
            self._save_manifest(user_key, manifest)
            return updated

        path = self.get_document_path(user_key, doc_id)
        text, error = extract_file_content(str(path))
        cache_path = self._text_cache_path(user_key, doc_id)
        if error:
            updated["text_extract_status"] = "failed"
            updated["updated_at"] = now_iso()
            updated["extract_error"] = error
            if cache_path.exists():
                cache_path.unlink(missing_ok=True)
        else:
            cache_path.write_text(text, encoding="utf-8")
            updated["text_extract_status"] = "ready"
            updated["updated_at"] = now_iso()
            updated.pop("extract_error", None)
        self._replace_document(manifest, updated)
        self._save_manifest(user_key, manifest)
        return updated

    def get_text_content(self, user_key: str, doc_id: str) -> Tuple[Dict[str, Any], str]:
        cache_path = self._text_cache_path(user_key, doc_id)
        if not cache_path.exists():
            self.ensure_text_cache(user_key, doc_id)
        document = self.get_document(user_key, doc_id)
        if not cache_path.exists():
            return document, ""
        return document, cache_path.read_text(encoding="utf-8")

    def build_preview_payload(self, user_key: str, doc_id: str, preview_chars: int = 6000) -> Dict[str, Any]:
        document = self.get_document(user_key, doc_id)
        if document.get("preview_type") == "pdf-inline":
            return {
                "status": "success",
                "document": document,
                "preview_type": "pdf-inline",
                "truncated": False,
            }

        _, text = self.get_text_content(user_key, doc_id)
        preview_text = text[:preview_chars]
        return {
            "status": "success",
            "document": document,
            "preview_type": "text",
            "text_preview": preview_text,
            "truncated": len(text) > preview_chars,
        }

    def rename_document(self, user_key: str, doc_id: str, new_display_name: str) -> Dict[str, Any]:
        manifest, item = self._find_document(user_key, doc_id)
        updated = dict(item)
        cleaned = sanitize_filename(new_display_name)
        ext = updated.get("extension", "")
        if ext and not cleaned.lower().endswith(ext):
            cleaned += ext
        updated["display_name"] = cleaned
        updated["updated_at"] = now_iso()
        self._replace_document(manifest, updated)
        self._save_manifest(user_key, manifest)
        return updated

    def delete_document(self, user_key: str, doc_id: str) -> None:
        manifest, item = self._find_document(user_key, doc_id)
        path = self.user_dir(user_key) / item["stored_filename"]
        if path.exists():
            path.unlink()
        cache_path = self._text_cache_path(user_key, doc_id)
        if cache_path.exists():
            cache_path.unlink()
        manifest["documents"] = [doc for doc in manifest.get("documents", []) if doc.get("doc_id") != doc_id]
        self._save_manifest(user_key, manifest)


user_document_service = UserDocumentService()
