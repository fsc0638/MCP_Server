"""Workspace routes."""

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from main import PROJECT_ROOT

router = APIRouter(tags=["Workspace"])
logger = logging.getLogger("MCP_Server.Router.Workspace")
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)


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

