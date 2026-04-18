"""User-scoped document center service."""

from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime, timedelta
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
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def sanitize_filename(filename: str) -> str:
    """Preserve user-facing names while blocking traversal / invalid chars."""
    filename = os.path.basename(filename or "")
    illegal_chars = {"\\", "/", ":", "*", "?", '"', "<", ">", "|", "\x00"}
    filename = "".join("_" if c in illegal_chars else c for c in filename)
    filename = filename.strip(". ").strip()
    if not filename:
        return "uploaded_file"
    stem, ext = os.path.splitext(filename)
    reserved_token = stem.split(".", 1)[0].upper()
    if reserved_token in WINDOWS_RESERVED_NAMES:
        filename = f"{stem}_{ext}"
    return filename or "uploaded_file"


def sanitize_user_key(user_id: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in (user_id or ""))
    safe = safe.strip("_")
    return safe or "anonymous"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


class ExpiredDocumentError(Exception):
    """Raised when a document record exists but is past its retention window."""


class UserDocumentService:
    def __init__(self, root_dir: Path | None = None, ttl_days: int = 14):
        self.root_dir = Path(root_dir or USER_DOCUMENTS_DIR)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_days = max(int(ttl_days or 14), 1)

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
        changed = False
        hydrated = []
        for item in data.get("documents", []):
            doc, item_changed = self._hydrate_document(dict(item))
            hydrated.append(doc)
            changed = changed or item_changed
        data["documents"] = hydrated
        if changed:
            self._save_manifest(user_key, data)
        return data

    def _save_manifest(self, user_key: str, data: Dict[str, Any]) -> None:
        path = self.manifest_path(user_key)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _hydrate_document(self, item: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        changed = False
        created_at = item.get("created_at") or now_iso()
        if item.get("created_at") != created_at:
            item["created_at"] = created_at
            changed = True
        if not item.get("updated_at"):
            item["updated_at"] = created_at
            changed = True
        if not item.get("expires_at"):
            base = parse_iso(created_at) or datetime.now()
            item["expires_at"] = (base + timedelta(days=self.ttl_days)).isoformat(timespec="seconds")
            changed = True
        item["expired"] = self.is_document_expired(item)
        return item, changed

    def _find_document(self, user_key: str, doc_id: str, allow_expired: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        manifest = self._load_manifest(user_key)
        for item in manifest.get("documents", []):
            if item.get("doc_id") == doc_id:
                if item.get("expired") and not allow_expired:
                    raise ExpiredDocumentError(f"Document '{doc_id}' has expired")
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
        doc_id: str | None,
        raw_user_id: str,
        original_filename: str,
        stored_filename: str,
        size: int,
    ) -> Dict[str, Any]:
        ext = Path(stored_filename).suffix.lower()
        mime_type = mimetypes.guess_type(stored_filename)[0] or "application/octet-stream"
        doc_id = doc_id or Path(stored_filename).stem
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
            "expires_at": (datetime.now() + timedelta(days=self.ttl_days)).isoformat(timespec="seconds"),
            "preview_type": "pdf-inline" if ext == ".pdf" else "text",
            "text_extract_status": "pending" if ext in TEXT_EXTRACTABLE_EXTENSIONS else "unsupported",
        }

    def _build_unique_stored_filename(
        self,
        user_key: str,
        preferred_filename: str,
        reserved_names: set[str] | None = None,
    ) -> str:
        safe_name = sanitize_filename(preferred_filename)
        stem = Path(safe_name).stem or "uploaded_file"
        ext = Path(safe_name).suffix
        user_dir = self.user_dir(user_key)
        used_names = {name for name in (reserved_names or set()) if name}

        candidate = safe_name
        counter = 2
        while candidate in used_names or (user_dir / candidate).exists():
            candidate = f"{stem}_{counter}{ext}"
            counter += 1
        return candidate

    def create_document(self, user_key: str, raw_user_id: str, filename: str, content: bytes) -> Dict[str, Any]:
        safe_name = sanitize_filename(filename)
        ext = Path(safe_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {ext or '(none)'}")

        manifest = self._load_manifest(user_key)
        doc_id = f"doc_{uuid4().hex[:12]}"
        stored_filename = self._build_unique_stored_filename(
            user_key,
            safe_name,
            reserved_names={item.get("stored_filename", "") for item in manifest.get("documents", [])},
        )
        path = self.user_dir(user_key) / stored_filename
        path.write_bytes(content)

        record = self._build_document_record(
            doc_id=doc_id,
            raw_user_id=raw_user_id,
            original_filename=safe_name,
            stored_filename=stored_filename,
            size=len(content),
        )
        manifest["documents"].append(record)
        self._save_manifest(user_key, manifest)
        return record

    def is_document_expired(self, document: Dict[str, Any], now: datetime | None = None) -> bool:
        expires_at = parse_iso(document.get("expires_at"))
        if not expires_at:
            return False
        return expires_at <= (now or datetime.now())

    def list_documents(self, user_key: str, include_expired: bool = False) -> List[Dict[str, Any]]:
        manifest = self._load_manifest(user_key)
        documents = []
        for item in manifest.get("documents", []):
            doc = dict(item)
            cache_exists = self._text_cache_path(user_key, doc.get("doc_id", "")).exists()
            doc["has_text_cache"] = cache_exists
            doc["expired"] = self.is_document_expired(doc)
            if doc["expired"] and not include_expired:
                continue
            documents.append(doc)
        documents.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return documents

    def get_document(self, user_key: str, doc_id: str, allow_expired: bool = False) -> Dict[str, Any]:
        _, item = self._find_document(user_key, doc_id, allow_expired=allow_expired)
        doc = dict(item)
        doc["has_text_cache"] = self._text_cache_path(user_key, doc_id).exists()
        return doc

    def get_document_path(self, user_key: str, doc_id: str, allow_expired: bool = False) -> Path:
        _, item = self._find_document(user_key, doc_id, allow_expired=allow_expired)
        path = (self.user_dir(user_key) / item["stored_filename"]).resolve()
        if not str(path).startswith(str(self.user_dir(user_key).resolve())):
            raise ValueError("Invalid document path")
        if not path.exists():
            raise FileNotFoundError(f"Document file missing: {doc_id}")
        return path

    def ensure_text_cache(self, user_key: str, doc_id: str) -> Dict[str, Any]:
        manifest, item = self._find_document(user_key, doc_id, allow_expired=True)
        updated = dict(item)
        if updated.get("extension") not in TEXT_EXTRACTABLE_EXTENSIONS:
            updated["text_extract_status"] = "unsupported"
            self._replace_document(manifest, updated)
            self._save_manifest(user_key, manifest)
            return updated

        path = self.get_document_path(user_key, doc_id, allow_expired=True)
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
        manifest, item = self._find_document(user_key, doc_id, allow_expired=True)
        path = self.user_dir(user_key) / item["stored_filename"]
        if path.exists():
            path.unlink()
        cache_path = self._text_cache_path(user_key, doc_id)
        if cache_path.exists():
            cache_path.unlink()
        manifest["documents"] = [doc for doc in manifest.get("documents", []) if doc.get("doc_id") != doc_id]
        self._save_manifest(user_key, manifest)

    def cleanup_expired_documents(self) -> Dict[str, int]:
        summary = {"removed_documents": 0, "removed_users": 0}
        for user_dir in self.root_dir.iterdir():
            if not user_dir.is_dir():
                continue
            user_key = user_dir.name
            manifest = self._load_manifest(user_key)
            kept_documents = []
            removed_for_user = 0
            for item in manifest.get("documents", []):
                if self.is_document_expired(item):
                    doc_id = item.get("doc_id", "")
                    stored_filename = item.get("stored_filename", "")
                    file_path = user_dir / stored_filename
                    if file_path.exists():
                        file_path.unlink()
                    cache_path = self._text_cache_path(user_key, doc_id)
                    if cache_path.exists():
                        cache_path.unlink()
                    removed_for_user += 1
                    summary["removed_documents"] += 1
                else:
                    kept_documents.append(item)
            manifest["documents"] = kept_documents
            self._save_manifest(user_key, manifest)
            remaining_entries = [entry for entry in user_dir.iterdir() if entry.name != "manifest.json"]
            if removed_for_user and not kept_documents and not remaining_entries:
                manifest_path = self.manifest_path(user_key)
                if manifest_path.exists():
                    manifest_path.unlink()
                user_dir.rmdir()
                summary["removed_users"] += 1
        return summary


user_document_service = UserDocumentService()
