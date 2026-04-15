"""Workspace routes."""

import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from main import PROJECT_ROOT

router = APIRouter(tags=["Workspace"])
logger = logging.getLogger("MCP_Server.Router.Workspace")
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

_SETTINGS_FILE = WORKSPACE_DIR / ".server_settings.json"


def sanitize_filename(filename: str) -> str:
    filename = os.path.basename(filename)
    illegal_chars = {"\\", "/", ":", "*", "?", '"', "<", ">", "|", "\x00"}
    filename = "".join("_" if c in illegal_chars else c for c in filename)
    filename = filename.strip(". ").strip()
    return filename or "uploaded_file"


@router.post("/workspace/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to workspace for skill testing."""
    try:
        raw_name = file.filename or "uploaded_file"
        safe_name = sanitize_filename(raw_name)
        dest_path = WORKSPACE_DIR / safe_name

        if dest_path.exists():
            base, ext = os.path.splitext(safe_name)
            safe_name = f"{base}_{int(datetime.now().timestamp())}{ext}"
            dest_path = WORKSPACE_DIR / safe_name

        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        return {
            "status": "success",
            "filename": dest_path.name,
            "filepath": str(dest_path.resolve()).replace("\\", "/"),
        }
    except Exception as e:
        logger.error(f"Workspace upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspace/download/{filename}")
def download_file(filename: str):
    """Download a file generated in workspace."""
    try:
        safe_name = sanitize_filename(filename)
        target_path = WORKSPACE_DIR / safe_name
        abs_workspace = os.path.abspath(str(WORKSPACE_DIR))
        abs_target = os.path.abspath(str(target_path))
        if os.path.commonpath([abs_workspace, abs_target]) != abs_workspace:
            raise HTTPException(status_code=403, detail="Invalid path access pattern")
        if not target_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")
        return FileResponse(path=target_path, filename=safe_name, media_type="application/octet-stream")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workspace download error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── fsc backward-compatible download shortcut ─────────────────────────────────
DOWNLOADS_DIR = WORKSPACE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)


@router.get("/downloads/{filename}")
async def download_shortcut(filename: str):
    """
    Serves files from workspace/downloads directory and forces a download prompt.
    (Migrated from legacy router.py for LINE bot compatibility)
    """
    file_path = DOWNLOADS_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream",
    )


# ── Image serving route (proper content-type for LINE ImageMessage) ───────────
_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@router.get("/images/{filename}")
async def serve_image(filename: str):
    """
    Serves image files from workspace/downloads/ with proper image content-type.
    Required for LINE ImageMessage which needs HTTPS URLs returning image/* MIME type.
    """
    safe_name = sanitize_filename(filename)
    file_path = DOWNLOADS_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    ext = file_path.suffix.lower()
    content_type = _IMAGE_CONTENT_TYPES.get(ext, "image/png")
    return FileResponse(
        path=file_path,
        filename=safe_name,
        media_type=content_type,
    )


# ── Server Settings: Log Retention ─────────────────────────────────────────

def _load_settings() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_settings(data: dict):
    _SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class LogRetentionRequest(BaseModel):
    days: int = 30


@router.get("/api/settings/log-retention")
def get_log_retention():
    settings = _load_settings()
    return {"days": settings.get("log_retention_days", 30)}


@router.post("/api/settings/log-retention")
def set_log_retention(req: LogRetentionRequest):
    days = max(req.days, 20)  # Minimum 20 days
    settings = _load_settings()
    settings["log_retention_days"] = days
    _save_settings(settings)
    # Run cleanup immediately
    cleaned = _cleanup_old_logs(days)
    logger.info(f"[Settings] Log retention set to {days} days. Cleaned: {cleaned}")
    return {"status": "success", "days": days, "cleaned_files": cleaned}


def _cleanup_old_logs(retention_days: int) -> list:
    """Delete log files older than retention_days."""
    cleaned = []
    cutoff = time.time() - (retention_days * 86400)
    log_file = PROJECT_ROOT / "uma_server.log"

    # Trim the main log file: keep only lines within retention period
    if log_file.exists() and log_file.stat().st_mtime < cutoff:
        # Entire file is older than cutoff — archive and truncate
        archive = PROJECT_ROOT / f"uma_server.log.{datetime.now().strftime('%Y%m%d')}.bak"
        shutil.copy2(log_file, archive)
        log_file.write_text("", encoding="utf-8")
        cleaned.append(str(archive))

    # Clean old .bak log files
    for f in PROJECT_ROOT.glob("uma_server.log.*.bak"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            cleaned.append(str(f))

    # Clean old analytics JSONL
    analytics_dir = WORKSPACE_DIR / "analytics"
    if analytics_dir.exists():
        for f in analytics_dir.glob("*.jsonl"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                cleaned.append(str(f))

    # Clean old session cache files
    sessions_dir = WORKSPACE_DIR / "sessions"
    if sessions_dir.exists():
        for f in sessions_dir.glob("*_msg_cache.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                cleaned.append(str(f))

    return cleaned


# ── Schedule Management API ────────────────────────────────────────────────

@router.get("/api/schedules")
def list_all_schedules():
    """List all scheduled tasks across all sessions for admin dashboard."""
    schedules_dir = WORKSPACE_DIR / "schedules"
    if not schedules_dir.exists():
        return {"total": 0, "sessions": [], "tasks": []}

    sessions = []
    all_tasks = []
    for f in sorted(schedules_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sid = data.get("session_id", f.stem)
            tasks = data.get("tasks", [])
            active = sum(1 for t in tasks if t.get("enabled"))
            paused = sum(1 for t in tasks if not t.get("enabled"))
            sessions.append({"session_id": sid, "task_count": len(tasks), "active": active, "paused": paused})
            for t in tasks:
                t["_session_id"] = sid
                all_tasks.append(t)
        except Exception:
            pass

    return {
        "total": len(all_tasks),
        "active": sum(1 for t in all_tasks if t.get("enabled")),
        "paused": sum(1 for t in all_tasks if not t.get("enabled")),
        "sessions": sessions,
        "tasks": all_tasks,
    }


@router.post("/api/schedules/{session_id}/{task_id}/toggle")
def toggle_schedule_task(session_id: str, task_id: str):
    """Toggle a task's enabled state (pause/resume)."""
    sched_file = WORKSPACE_DIR / "schedules" / f"{session_id}.json"
    if not sched_file.exists():
        raise HTTPException(status_code=404, detail="Session schedule not found")
    data = json.loads(sched_file.read_text(encoding="utf-8"))
    for t in data.get("tasks", []):
        if t.get("id") == task_id:
            t["enabled"] = not t.get("enabled", True)
            sched_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"status": "success", "task_id": task_id, "enabled": t["enabled"]}
    raise HTTPException(status_code=404, detail="Task not found")


@router.delete("/api/schedules/{session_id}/{task_id}")
def delete_schedule_task(session_id: str, task_id: str):
    """Delete a scheduled task."""
    sched_file = WORKSPACE_DIR / "schedules" / f"{session_id}.json"
    if not sched_file.exists():
        raise HTTPException(status_code=404, detail="Session schedule not found")
    data = json.loads(sched_file.read_text(encoding="utf-8"))
    original_len = len(data.get("tasks", []))
    data["tasks"] = [t for t in data.get("tasks", []) if t.get("id") != task_id]
    if len(data["tasks"]) == original_len:
        raise HTTPException(status_code=404, detail="Task not found")
    sched_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "task_id": task_id}

