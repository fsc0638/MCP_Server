"""Workspace routes."""

import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Cookie, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from main import PROJECT_ROOT

router = APIRouter(tags=["Workspace"])
logger = logging.getLogger("MCP_Server.Router.Workspace")
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

# Shared upload pool for both LINE Bot and Web UI — all user uploads live here
# under a per-user folder so Agent can find the right file regardless of source.
LINE_UPLOADS_DIR = PROJECT_ROOT / "Agent_workspace" / "line_uploads"
LINE_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

_SETTINGS_FILE = WORKSPACE_DIR / ".server_settings.json"


def _resolve_user_id_from_cookie(mcp_session: str) -> str:
    """Look up the logged-in user from the signed cookie. Returns 'anonymous'
    if not logged in (should rarely happen since chat requires login).

    Normalizes the user_id to match the LINE Bot's raw upload folder format:
      - `line_U09abc...`  → `U09abc...`  (strip prefix, matches Bot)
      - `pw_user_at_xxx`  → unchanged   (password login gets its own folder)
    """
    if not mcp_session:
        return "anonymous"
    try:
        from server.services.session_token_cookie import verify_token
        from server.services.auth_session_store import get_auth_session_store
        token = verify_token(mcp_session)
        if not token:
            return "anonymous"
        sess = get_auth_session_store().get(token)
        if not sess or not sess.user_id:
            return "anonymous"
        uid = sess.user_id
        # Strip "line_" prefix so LINE Bot and Web share the same folder per user
        if uid.startswith("line_"):
            uid = uid[len("line_"):]
        # sanitize for filesystem use (defensive — should already be safe)
        safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in uid)
        return safe or "anonymous"
    except Exception as e:
        logger.warning(f"[upload] user_id resolution failed: {e}")
        return "anonymous"


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


@router.post("/api/upload/personal")
async def upload_personal_file(
    file: UploadFile = File(...),
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    """Generic file upload for Web UI — stored under the user's personal folder
    at Agent_workspace/line_uploads/{user_id}/ so both LINE Bot uploads and
    Web UI uploads share the same filesystem location.

    Auto-creates the user folder if it doesn't exist.
    """
    try:
        user_id = _resolve_user_id_from_cookie(mcp_session)
        user_dir = LINE_UPLOADS_DIR / user_id
        user_dir.mkdir(parents=True, exist_ok=True)

        raw_name = file.filename or "uploaded_file"
        safe_name = sanitize_filename(raw_name)
        dest_path = user_dir / safe_name

        # Collision: append timestamp
        if dest_path.exists():
            base, ext = os.path.splitext(safe_name)
            safe_name = f"{base}_{int(datetime.now().timestamp())}{ext}"
            dest_path = user_dir / safe_name

        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file_size = dest_path.stat().st_size
        logger.info(f"[upload/personal] user={user_id} file={safe_name} size={file_size}")
        return {
            "status": "success",
            "user_id": user_id,
            "filename": dest_path.name,
            "filepath": str(dest_path.resolve()).replace("\\", "/"),
            "size": file_size,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[upload/personal] error: {e}")
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
    # Parse timestamp from each line (format: "2026-04-10 15:30:00,123")
    if log_file.exists():
        cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
        try:
            original_size = log_file.stat().st_size
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            retained = []
            for line in lines:
                # Keep lines whose timestamp >= cutoff_date, or lines without timestamp (continuation)
                if len(line) >= 10 and line[:4].isdigit() and line[4] == "-":
                    if line[:10] >= cutoff_date:
                        retained.append(line)
                elif retained:  # continuation line (stack trace, etc.) — keep if previous was kept
                    retained.append(line)
            log_file.write_text("\n".join(retained) + "\n" if retained else "", encoding="utf-8")
            new_size = log_file.stat().st_size
            if original_size != new_size:
                cleaned.append(f"uma_server.log: {original_size//1048576}MB → {new_size//1048576}MB")
        except Exception as e:
            logger.error(f"[LogCleanup] Failed to trim log: {e}")

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

@router.get("/api/session-names")
def get_session_names():
    """Resolve session IDs to human-readable names (user name or group name)."""
    users_dir = WORKSPACE_DIR / "users"
    names = {}
    # 1. From workspace/users/*.json (LINE users)
    if users_dir.exists():
        for f in users_dir.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                sid = f.stem
                name = d.get("name", "")
                if name:
                    names[sid] = name
                    # Also map without prefix
                    if sid.startswith("line_"):
                        names[sid.replace("line_", "")] = name
            except Exception:
                pass

    # 2. From workspace/profiles/*group*.profile.md (LINE groups — extract from profile title)
    profiles_dir = WORKSPACE_DIR / "profiles"
    if profiles_dir.exists():
        for f in profiles_dir.glob("*group*.profile.md"):
            try:
                sid = f.stem.replace(".profile", "")
                content = f.read_text(encoding="utf-8")
                # Try to find group name in profile (look for 群組名稱 or first heading)
                for line in content.split("\n"):
                    if "群組" in line and "：" in line:
                        gname = line.split("：", 1)[1].strip()
                        if gname:
                            names[sid] = gname
                            break
                if sid not in names:
                    names[sid] = sid.replace("line_group_", "群組 ")[:20]
            except Exception:
                pass

    return {"names": names}


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


# ── System Health + Activity Feed ──────────────────────────────────────────

@router.get("/api/system/health")
def system_health():
    """Return system health indicators for admin dashboard."""
    import subprocess as _sp
    pr = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))

    # Log file size
    log_path = pr / "uma_server.log"
    log_size_mb = round(log_path.stat().st_size / 1048576, 1) if log_path.exists() else 0

    # Uptime (server start time from log first line)
    uptime_str = "unknown"
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
            if first and len(first) > 19:
                uptime_str = first[:19]
    except Exception:
        pass

    # Skills count
    skills_count = 0
    try:
        from main import get_uma
        uma = get_uma()
        skills_count = len(uma.registry.skills)
    except Exception:
        pass

    # Schedules
    sched_dir = WORKSPACE_DIR / "schedules"
    sched_active = 0
    sched_total = 0
    if sched_dir.exists():
        for f in sched_dir.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                for t in d.get("tasks", []):
                    sched_total += 1
                    if t.get("enabled"): sched_active += 1
            except Exception:
                pass

    return {
        "log_size_mb": log_size_mb,
        "server_start": uptime_str,
        "skills_count": skills_count,
        "schedules_active": sched_active,
        "schedules_total": sched_total,
    }


@router.get("/api/activity-feed")
def activity_feed(limit: int = 15):
    """Aggregate recent activity from token_usage + schedules."""
    pr = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
    activities = []

    # 1. From token_usage.jsonl (last N calls)
    usage_path = WORKSPACE_DIR / "analytics" / "token_usage.jsonl"
    if usage_path.exists():
        try:
            lines = usage_path.read_text(encoding="utf-8", errors="replace").strip().split("\n")
            for line in lines[-20:]:
                try:
                    r = json.loads(line)
                    skill = r.get("skill", "(chat)")
                    user = r.get("user_id", "")[:12]
                    ts = r.get("ts", "")[:19].replace("T", " ")
                    tokens = r.get("total_tokens", 0)
                    activities.append({
                        "type": "token",
                        "text": f"{skill} ({tokens} tokens)",
                        "user": user,
                        "time": ts,
                        "status": r.get("status", "success"),
                    })
                except Exception:
                    pass
        except Exception:
            pass

    # 2. From git log (Agent_skills recent commits)
    try:
        from server.routes.skills import _get_git_root
        git_root = _get_git_root()
        if (git_root / ".git").exists():
            proc = _sp.run(
                ["git", "log", "--oneline", "--format=%s|%ci", "-5"],
                cwd=git_root, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            for line in proc.stdout.strip().split("\n"):
                if "|" in line:
                    msg, ts = line.rsplit("|", 1)
                    activities.append({
                        "type": "git",
                        "text": msg.strip()[:60],
                        "user": "",
                        "time": ts.strip()[:19],
                        "status": "success",
                    })
    except Exception:
        pass

    # Sort by time descending, limit
    activities.sort(key=lambda a: a.get("time", ""), reverse=True)
    return {"activities": activities[:limit]}

