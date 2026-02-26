"""
MCP Skill Server — Router (Redefined Architecture)

Two strictly isolated layers:
  1. /chat         → Pure LLM conversation (NO skill execution, NO tools injection)
  2. /skills/*     → Skill management CRUD (read, update, validate, install deps)

The agent-mode tool-calling endpoints (/execute, old SSE /chat) are preserved
but kept separate and NOT connected to the chat panel.
"""
import os
import sys
import json
import shutil
import asyncio
import logging
import subprocess
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Body, File, UploadFile, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from main import get_uma, PROJECT_ROOT

logger = logging.getLogger("MCP_Server.Router")

WORKSPACE_DIR = PROJECT_ROOT / "workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="MCP Agent Console API",
    description="Skill Management + Pure LLM Chat — Strictly Isolated",
    version="2.0.0"
)

static_dir = PROJECT_ROOT / "static"
if not static_dir.exists():
    static_dir.mkdir()
app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="static")

@app.on_event("startup")
async def index_all_skills():
    """Sprint 3/4: Index all skills and start directory watcher."""
    from main import get_uma
    from core.retriever import retriever
    from core.watcher import DirectoryWatcher
    try:
        uma = get_uma()
        count = 0
        for skill_name, skill_data in uma.registry.skills.items():
            skill_md = skill_data["path"] / "SKILL.md"
            if skill_md.exists():
                retriever.ingest_skill(skill_name, str(skill_md))
                count += 1
        logger.info(f"Startup SKILL indexing complete. Indexed {count} skills.")

        # Start Watchdog
        global __watcher
        __watcher = DirectoryWatcher(str(WORKSPACE_DIR), str(uma.registry.skills_home), retriever)
        __watcher.start()

    except Exception as e:
        logger.error(f"Failed to start indexing or watcher on startup: {e}")

@app.on_event("shutdown")
async def shutdown_system():
    global __watcher
    if '__watcher' in globals() and __watcher is not None:
        __watcher.stop()



# ─── D-07: Unified Session Management ─────────────────────────────────────────

from core.session import SessionManager

_session_mgr = SessionManager(str(PROJECT_ROOT))

SYSTEM_PROMPT = (
    "你是研發組 MCP Agent Console 的 AI 助理。\n"
    "你的職責是回答用戶關於技術、開發、管理或任何其他問題。\n"
    "你沒有存取任何外部工具或技能執行的能力。\n"
    "請以繁體中文回覆，保持專業、清晰、簡潔。"
)


def get_session(session_id: str) -> List[Dict[str, Any]]:
    return _session_mgr.get_or_create_conversation(session_id, SYSTEM_PROMPT)


def flush_session_to_memory(session_id: str):
    """Persist conversation history to MEMORY.md via SessionManager."""
    _session_mgr.flush_conversation_to_memory(session_id)


# ─── Request / Response Models ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    user_input: str
    session_id: Optional[str] = "default"
    model: Optional[str] = "openai"
    injected_skill: Optional[str] = None  # For "Attach Skill" feature
    execute: Optional[bool] = False       # Switch to agent mode for executing skills
    attached_file: Optional[str] = None   # Absolute path of uploaded workspace file


class SkillUpdateRequest(BaseModel):
    yaml_content: str   # Raw YAML frontmatter string to validate + write


class ExecuteRequest(BaseModel):
    skill_name: str
    arguments: Dict[str, Any] = {}


class SearchRequest(BaseModel):
    query: str


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health_check():
    """System health: skill scan summary."""
    uma = get_uma()
    skills_status = {}
    for name, data in uma.registry.skills.items():
        meta = data["metadata"]
        skills_status[name] = {
            "version": meta.get("version", "unknown"),
            "ready": meta.get("_env_ready", False),
            "missing_deps": meta.get("_missing_deps", [])
        }
    return {
        "status": "healthy",
        "total_skills": len(skills_status),
        "skills": skills_status
    }


# ─── UBER FILE UPLOAD MGR ─────────────────────────────────────────────────────

@app.post("/api/documents/upload", tags=["Documents"])
async def upload_document(file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    """
    Handle document uploads, check hash for deduplication, and sanitize path.
    """
    try:
        # 1. Sanitize filename to prevent directory traversal
        filename = file.filename or "unknown_file"
        safe_path = (WORKSPACE_DIR / filename).resolve()
        if not str(safe_path).startswith(str(WORKSPACE_DIR.resolve())):
            raise HTTPException(status_code=400, detail="Invalid filename (Directory Traversal Detected)")

        # 2. Read content and compute SHA-256 hash
        content = await file.read()
        file_hash = hashlib.sha256(content).hexdigest()
        
        # 3. Check extensions & Create extension-preserved hashed filename
        extension = Path(filename).suffix.lower()
        allowed_exts = {".jpg", ".jpeg", ".png", ".pdf", ".txt", ".md", ".csv", ".xlsx", ".docx", ".webm", ".wav", ".mp3", ".mp4"}
        if extension not in allowed_exts:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {extension}")
            
        hashed_filename = f"{file_hash[:16]}{extension}"
        final_path = (WORKSPACE_DIR / hashed_filename).resolve()
        
        # 4. Save file if it doesn't exist
        if not final_path.exists():
            final_path.write_bytes(content)
            logger.info(f"File saved: {final_path.name} (Original: {filename})")
        else:
            logger.info(f"File already exists (Hash match): {final_path.name} (Original: {filename})")
            
        # 5. Background vectorization is now fully handled by the Watchdog in core/watcher.py
        # no manual background_tasks.add_task needed here.
        vectorized_status = "pending" if extension in {".txt", ".md", ".pdf", ".csv"} else "unsupported"
            
        return {
            "status": "success",
            "filename": hashed_filename,
            "original_filename": filename,
            "hash": file_hash,
            "path": str(final_path),
            "vectorized": vectorized_status
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/list", tags=["Documents"])
def list_documents():
    """
    List all uploaded files in workspace/.
    Also reports which files are currently indexed in FAISS.
    """
    from core.retriever import retriever
    indexed = set(retriever.list_indexed_files())
    files = []
    for f in sorted(WORKSPACE_DIR.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            files.append({
                "filename": f.name,
                "size": f.stat().st_size,
                "indexed": f.name in indexed
            })
    return {"total": len(files), "files": files}


@app.delete("/api/documents/{filename}", tags=["Documents"])
def delete_document_endpoint(filename: str):
    """
    Delete an uploaded file and remove its FAISS index chunks.
    """
    from core.retriever import retriever
    # Security: only allow plain filenames (no path traversal)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    target = (WORKSPACE_DIR / filename).resolve()
    if not str(target).startswith(str(WORKSPACE_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Path traversal denied")

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found")

    # Remove from FAISS
    retriever.delete_document(filename)

    # Remove from disk
    target.unlink()
    logger.info(f"Deleted file and FAISS index: {filename}")
    return {"status": "success", "message": f"'{filename}' 已刪除"}



# ─── PURE CHAT (Isolation Wall) ───────────────────────────────────────────────

@app.post("/chat", tags=["Chat"])
async def chat(req: ChatRequest):
    """
    LLM conversation endpoint.
    If req.execute is False -> PURE CHAT (Isolation Guarantee)
      - Does NOT pass tools/schema to the LLM
    If req.execute is True -> AGENT MODE
      - Passes skill tools to the LLM to execute
      - Can inject attached file path via System Prompt
    """
    from adapters.openai_adapter import OpenAIAdapter
    from adapters.gemini_adapter import GeminiAdapter
    from adapters.claude_adapter import ClaudeAdapter

    uma = get_uma()

    # 1. Get or create session history
    history = get_session(req.session_id)

    # 2. Dynamic execution context (File Attachment)
    execution_context = ""
    if req.execute and req.attached_file:
        execution_context = f"\n\n[系統提醒：目前工作區已有檔案，其絕對路徑為 {req.attached_file}。請主動使用此檔案進行操作。]"

    # 3. D-11: Skill knowledge injection — load FULL SKILL.md content as prompt/context
    #    Design Principle #2: "觸發 skill 時，參照 skill 定義，成為提示詞去執行任務"
    skill_context = ""
    if req.injected_skill:
        # Load the complete SKILL.md content (not just description)
        full_skill_md = uma.get_skill_knowledge(req.injected_skill)
        skill_data = uma.registry.get_skill(req.injected_skill)
        if full_skill_md and skill_data:
            meta = skill_data["metadata"]
            if req.execute:
                # Execute mode: SKILL.md becomes the operational guide
                skill_context = (
                    f"\n\n[技能操作指引 — {req.injected_skill}]\n"
                    f"以下是該技能的完整定義與操作說明，請嚴格依照此指引完成任務。\n"
                    f"你可以使用提供的工具來執行程式碼或操作檔案。\n\n"
                    f"---\n{full_skill_md}\n---\n"
                )
            else:
                # Pure chat mode: SKILL.md becomes knowledge reference
                skill_context = (
                    f"\n\n[技能知識參考 — {req.injected_skill}]\n"
                    f"以下是該技能的完整定義與操作說明，請以此知識為基礎回答問題。\n"
                    f"注意：你目前處於純對話模式，不具有執行工具的能力。\n\n"
                    f"---\n{full_skill_md}\n---\n"
                )
        elif skill_data:
            # Fallback: if SKILL.md not readable, use description
            meta = skill_data["metadata"]
            skill_context = f"\n\n[技能參考 — {req.injected_skill}] {meta.get('description', '無描述')}"

    # 4. Append user message
    user_content = req.user_input + execution_context + skill_context
    history_to_pass = history + [{"role": "user", "content": user_content}]

    # 5. Select adapter and call appropriate mode (chat vs simple_chat)
    try:
        if req.model == "openai":
            adapter = OpenAIAdapter(uma)
        elif req.model == "gemini":
            adapter = GeminiAdapter(uma)
        else:
            adapter = ClaudeAdapter(uma)

        if not adapter.is_available:
            return JSONResponse(status_code=503, content={
                "status": "error",
                "message": f"模型 {req.model} 無法使用，請確認 API Key 是否設定於 .env"
            })

        kwargs = {
            "session_id": req.session_id,
            "attached_file": req.attached_file
        }

        if req.execute:
            # Agent Mode -> full chat with tool calling
            # D-12: Unified interface — all adapters receive agent_history + user_query
            agent_history = [m for m in history_to_pass if m.get("role") != "system"]
            result = adapter.chat(messages=agent_history, user_query=user_content, **kwargs)
        else:
            # Pure Chat Mode -> no tools
            result = adapter.simple_chat(history_to_pass, **kwargs)

    except Exception as e:
        logger.error(f"Chat error: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

    # 6. Append assistant reply to history Manager
    if result.get("status") == "success":
        # Sprint 2/5 D-07: Unified SessionManager handles appending and compressing memory
        _session_mgr.append_message(req.session_id, "user", req.user_input)
        _session_mgr.append_message(req.session_id, "assistant", result["content"])
        
        # Auto-flush every 20 messages to prevent memory loss
        # Note: we use _session_mgr._conversations size for accurate count
        conv_len = len(_session_mgr.get_or_create_conversation(req.session_id))
        if conv_len > 0 and conv_len % 20 == 0:
            flush_session_to_memory(req.session_id)

    return result


@app.post("/chat/flush/{session_id}", tags=["Chat"])
def flush_memory(session_id: str):
    """Manually persist a session's conversation to MEMORY.md."""
    flush_session_to_memory(session_id)
    return {"status": "success", "message": f"Session '{session_id}' flushed to MEMORY.md"}


@app.delete("/chat/session/{session_id}", tags=["Chat"])
def clear_session(session_id: str):
    """Clear a conversation session (reset context)."""
    _session_mgr.clear_conversation(session_id)
    return {"status": "success", "message": f"Session '{session_id}' cleared"}


# ─── SKILL MANAGEMENT ─────────────────────────────────────────────────────────

@app.get("/skills/list", tags=["Skill Management"])
def list_skills():
    """Full skill list with metadata and dependency status."""
    uma = get_uma()
    skills = {}
    for name, data in uma.registry.skills.items():
        meta = data["metadata"]
        skills[name] = {
            "description": meta.get("description", ""),
            "version": meta.get("version", "unknown"),
            "ready": meta.get("_env_ready", False),
            "missing_deps": meta.get("_missing_deps", []),
            "path": str(data["path"])
        }
    return {"total": len(skills), "skills": skills}


@app.get("/skills/{skill_name}", tags=["Skill Management"])
def get_skill(skill_name: str):
    """
    Read a skill's SKILL.md raw content and backup status.
    Returns both the YAML frontmatter and the markdown body.
    """
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    skill_md_path = skill["path"] / "SKILL.md"
    bak_path = skill["path"] / "SKILL.md.bak"

    try:
        content = skill_md_path.read_text(encoding="utf-8")
        has_backup = bak_path.exists()
        backup_time = None
        if has_backup:
            backup_time = datetime.fromtimestamp(bak_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "skill_name": skill_name,
            "raw_content": content,
            "has_backup": has_backup,
            "backup_modified": backup_time,
            "metadata": {k: v for k, v in skill["metadata"].items() if not k.startswith("_")}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/skills/{skill_name}", tags=["Skill Management"])
def update_skill(skill_name: str, req: SkillUpdateRequest):
    """
    Update a skill's SKILL.md.
    Safety measures:
      1. Path sanitization — only writes within skills/ directory
      2. YAML format validation — rejects malformed frontmatter
      3. Auto-backup — saves SKILL.md.bak before overwriting
    """
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    skill_path = skill["path"].resolve()
    skills_home = uma.registry.skills_home.resolve()

    # 1. Path sanitization
    try:
        skill_path.relative_to(skills_home)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied: skill path is outside skills directory")

    skill_md_path = skill_path / "SKILL.md"
    bak_path = skill_path / "SKILL.md.bak"

    # 2. YAML format validation
    new_content = req.yaml_content
    if not new_content.startswith("---"):
        raise HTTPException(status_code=422, detail="YAML 格式錯誤：SKILL.md 必須以 '---' 開頭")
    try:
        parts = new_content.split("---")
        if len(parts) < 3:
            raise ValueError("Missing closing '---' for frontmatter")
        yaml.safe_load(parts[1])  # Validate YAML
    except yaml.YAMLError as e:
        raise HTTPException(status_code=422, detail=f"YAML 格式錯誤：{str(e)}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # 3. Backup original
    try:
        if skill_md_path.exists():
            shutil.copy2(skill_md_path, bak_path)

        skill_md_path.write_text(new_content, encoding="utf-8")

        # Clear registry cache so next scan picks up changes
        uma.registry.skills.pop(skill_name, None)

        logger.info(f"Skill '{skill_name}' updated. Backup saved to SKILL.md.bak")
        return {
            "status": "success",
            "message": f"技能 '{skill_name}' 已更新，原始備份已儲存至 SKILL.md.bak",
            "backup_created": str(bak_path)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/skills/{skill_name}/rollback", tags=["Skill Management"])
def rollback_skill(skill_name: str):
    """
    Rollback a skill's SKILL.md to its last backup (SKILL.md.bak).
    """
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)

    # Also check path directly even if not in registry
    skills_home = uma.registry.skills_home
    skill_path = skills_home / skill_name
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    bak_path = skill_path / "SKILL.md.bak"
    skill_md_path = skill_path / "SKILL.md"

    if not bak_path.exists():
        raise HTTPException(status_code=404, detail=f"找不到備份檔案 (SKILL.md.bak)，無法回退")

    try:
        shutil.copy2(bak_path, skill_md_path)
        uma.registry.skills.pop(skill_name, None)

        logger.info(f"Skill '{skill_name}' rolled back from SKILL.md.bak")
        return {"status": "success", "message": f"技能 '{skill_name}' 已回退至上次備份版本"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/skills/{skill_name}/install", tags=["Skill Management"])
def install_skill_deps(skill_name: str):
    """
    Trigger pip install for a degraded skill's missing dependencies.
    Only installs packages listed in the skill's runtime_requirements.
    """
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    missing = skill["metadata"].get("_missing_deps", [])
    if not missing:
        return {"status": "already_ready", "message": "此技能的依賴已全部就緒，無需安裝"}

    results = []
    for pkg in missing:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", pkg],
                capture_output=True, text=True, timeout=120
            )
            if proc.returncode == 0:
                results.append({"package": pkg, "status": "installed"})
                logger.info(f"Installed {pkg} for skill '{skill_name}'")
            else:
                results.append({"package": pkg, "status": "failed", "error": proc.stderr[:300]})
                logger.error(f"Failed to install {pkg}: {proc.stderr[:300]}")
        except Exception as e:
            results.append({"package": pkg, "status": "error", "error": str(e)})

    # Force re-scan so registry refreshes
    uma.registry.skills.pop(skill_name, None)

    return {
        "status": "done",
        "message": "安裝完成，請點擊「重新掃描」刷新技能狀態",
        "results": results
    }


@app.post("/skills/rescan", tags=["Skill Management"])
def rescan_skills():
    """Re-scan the skills directory and refresh the registry."""
    uma = get_uma()
    uma.registry.skills.clear()
    uma.registry.validation_cache.clear()
    uma.registry.scan_skills()
    return {
        "status": "success",
        "total_skills": len(uma.registry.skills),
        "message": "技能庫重新掃描完成"
    }


class CreateSkillRequest(BaseModel):
    name: str           # Must be ASCII + hyphens only, e.g. "my-skill"
    display_name: str   # Human-readable name (any language)
    description: str    # Short description (any language)
    version: str = "1.0.0"
    category: str = ""  # Optional category tag


import re as _re

@app.post("/skills/create", tags=["Skill Management"])
def create_skill(req: CreateSkillRequest):
    """
    Create a new skill directory with a SKILL.md template.
    Safety:
      - Name must match ^[a-zA-Z0-9-]+$ (LLM tool name compatibility)
      - Name is auto-prefixed with 'mcp-' if not already
      - Refuses to overwrite an existing skill
    """
    uma = get_uma()
    skills_home = uma.registry.skills_home

    # 1. Normalize and validate name
    name = req.name.strip().lower().replace("_", "-")
    if not name.startswith("mcp-"):
        name = f"mcp-{name}"

    if not _re.match(r'^[a-z0-9-]+$', name):
        raise HTTPException(
            status_code=422,
            detail="技能識別碼只能包含英文小寫字母、數字和連字號（例：my-skill）"
        )
    if len(name) < 5 or len(name) > 60:
        raise HTTPException(status_code=422, detail="技能識別碼長度需在 5–60 字元之間")

    # 2. Check for duplicates
    skill_path = (skills_home / name).resolve()
    try:
        skill_path.relative_to(skills_home.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="路徑不合法")

    if skill_path.exists():
        raise HTTPException(status_code=409, detail=f"技能「{name}」已存在，請使用其他識別碼")

    # 3. Scaffold directory + SKILL.md template
    try:
        skill_path.mkdir(parents=True)
        (skill_path / "scripts").mkdir()
        (skill_path / "references").mkdir()

        skill_md = f"""---
name: {name}
display_name: "{req.display_name}"
description: "{req.description}"
version: "{req.version}"
category: "{req.category}"
runtime_requirements: []
risk_level: "low"
---

# {req.display_name}

{req.description}

## 使用方式

說明此技能的使用方法、接受的輸入與回傳的輸出格式。

## 注意事項

- 此為新建技能，尚未設定執行腳本
- 請在 `scripts/` 目錄下新增 `main.py` 以啟用執行功能
"""
        (skill_path / "SKILL.md").write_text(skill_md, encoding="utf-8")

        # Auto-rescan so it shows up immediately
        uma.registry.scan_skills()

        logger.info(f"New skill created: {name}")
        return {
            "status": "success",
            "skill_name": name,
            "path": str(skill_path),
            "message": f"技能「{name}」已建立，請在左側列表中查看"
        }
    except Exception as e:
        # Cleanup on failure
        if skill_path.exists():
            shutil.rmtree(skill_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))



# ─── LEGACY AGENT ENDPOINTS (preserved, not used by chat panel) ────────────────

@app.post("/execute", tags=["Agent (Legacy)"])
def execute_tool(request: ExecuteRequest):
    """Execute a skill script directly (agent mode, not connected to chat panel)."""
    uma = get_uma()
    skill = uma.registry.get_skill(request.skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{request.skill_name}' not found")
    if not skill["metadata"].get("_env_ready", False):
        return {
            "status": "error",
            "message": f"Skill '{request.skill_name}' environment is not ready (missing dependencies)"
        }

    try:
        logger.info(f"Executing skill '{request.skill_name}' via legacy /execute")
        result = uma.execute_tool_call(request.skill_name, request.arguments)
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"Error executing skill {request.skill_name}: {e}")
        return {"status": "error", "message": str(e)}

# ─── WORKSPACE (Skill Testing Sandbox) ────────────────────────────────────────

import re

def sanitize_filename(filename: str) -> str:
    """Preserve Unicode/CJK filenames while removing path-traversal and Windows-illegal chars."""
    filename = os.path.basename(filename)
    # Windows illegal filename characters (explicit set, NOT regex to avoid stripping Unicode)
    ILLEGAL_CHARS = {'\\', '/', ':', '*', '?', '"', '<', '>', '|', '\x00'}
    filename = ''.join('_' if c in ILLEGAL_CHARS else c for c in filename)
    filename = filename.strip('. ').strip()
    if not filename:
        filename = 'uploaded_file'
    return filename

@app.post("/workspace/upload", tags=["Workspace"])
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to the workspace directory to test tools."""
    try:
        raw_name = file.filename
        logger.info(f"[UPLOAD] Raw filename from browser (repr): {repr(raw_name)}")
        safe_name = sanitize_filename(raw_name)
        logger.info(f"[UPLOAD] After sanitize_filename: {repr(safe_name)}")
        dest_path = WORKSPACE_DIR / safe_name
        
        # Append timestamp if duplicate (to not override test data)
        if dest_path.exists():
            logger.info(f"[UPLOAD] File exists, adding timestamp")
            base, ext = os.path.splitext(safe_name)
            safe_name = f"{base}_{int(datetime.now().timestamp())}{ext}"
            dest_path = WORKSPACE_DIR / safe_name

        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        logger.info(f"[UPLOAD] Saved to: {dest_path}")
        return {
            "status": "success",
            "filename": dest_path.name,
            "filepath": str(dest_path.resolve()).replace('\\', '/')
        }
    except Exception as e:
        logger.error(f"[UPLOAD] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/workspace/download/{filename}", tags=["Workspace"])
def download_file(filename: str):
    """Download a file generated by a tool in the workspace."""
    try:
        safe_name = sanitize_filename(filename)
        target_path = WORKSPACE_DIR / safe_name
        
        # Path traversal guard using strict commonpath validation
        abs_workspace = os.path.abspath(str(WORKSPACE_DIR))
        abs_target = os.path.abspath(str(target_path))
        if os.path.commonpath([abs_workspace, abs_target]) != abs_workspace:
            raise HTTPException(status_code=403, detail="Invalid path access pattern")

        if not target_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")

        return FileResponse(
            path=target_path,
            filename=safe_name,
            media_type='application/octet-stream'
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workspace download error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/tools", tags=["Agent (Legacy)"])
def list_tools(model: str = Query("openai")):
    """List tool definitions for agent mode (not used by chat panel)."""
    uma = get_uma()
    tools = uma.get_tools_for_model(model)
    return {"model": model, "tool_count": len(tools), "tools": tools}


@app.get("/resources/{skill_name}/{file_name}", tags=["Resources"])
def read_resource(skill_name: str, file_name: str, limit: int = Query(500, ge=0)):
    uma = get_uma()
    result = uma.executor.read_resource(skill_name, file_name)
    if result["status"] != "success":
        raise HTTPException(status_code=404, detail=result.get("message"))
    content = result["content"]
    truncated = limit > 0 and len(content) > limit
    if truncated:
        content = content[:limit]
    return {"status": "success", "content": content, "truncated": truncated}


@app.post("/search/{skill_name}/{file_name}", tags=["Resources"])
def search_resource(skill_name: str, file_name: str, request: SearchRequest):
    uma = get_uma()
    result = uma.executor.search_resource(skill_name, file_name, request.query)
    if result["status"] != "success":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result
