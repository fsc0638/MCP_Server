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
from fastapi import FastAPI, HTTPException, Query, Request, Body, File, UploadFile, BackgroundTasks, Form
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

# ─── Skill Hash Registry (Plan A) ───────────────────────────────────────────
import hashlib as _hashlib

_SKILL_HASHES_FILE = Path.home() / ".mcp_faiss" / "skill_hashes.json"

def _load_skill_hashes() -> dict:
    """Load persisted SKILL.md content hashes."""
    try:
        if _SKILL_HASHES_FILE.exists():
            return json.loads(_SKILL_HASHES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_skill_hashes(hashes: dict):
    """Persist SKILL.md content hashes to disk."""
    try:
        _SKILL_HASHES_FILE.parent.mkdir(exist_ok=True)
        _SKILL_HASHES_FILE.write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save skill hashes: {e}")

def _md5(path: Path) -> str:
    """Compute MD5 of a file's content for change detection."""
    return _hashlib.md5(path.read_bytes()).hexdigest()


def _delta_index_skills(uma, retriever) -> dict:
    """
    Plan A: Hash-based delta skill indexing.
    Only re-indexes skills whose SKILL.md has changed since last run.
    Also removes FAISS chunks for deleted skills.

    Returns a summary dict: {added, updated, removed, unchanged, errors}
    """
    stored_hashes = _load_skill_hashes()
    current_skills = {name: data for name, data in uma.registry.skills.items()}
    current_names  = set(current_skills.keys())
    stored_names   = set(stored_hashes.keys())

    summary = {"added": [], "updated": [], "removed": [], "unchanged": [], "errors": []}
    new_hashes = {}

    # Detect removed skills → remove from FAISS
    for removed in sorted(stored_names - current_names):
        try:
            retriever.delete_document(removed)
            summary["removed"].append(removed)
            logger.info(f"[Delta] Removed skill from FAISS: {removed}")
        except Exception as e:
            summary["errors"].append(f"{removed}: {e}")

    # Detect new / changed skills
    for skill_name, skill_data in current_skills.items():
        skill_md = skill_data["path"] / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            current_hash = _md5(skill_md)
            stored_hash  = stored_hashes.get(skill_name)
            new_hashes[skill_name] = current_hash

            if stored_hash is None:
                # New skill — never indexed
                retriever.ingest_skill(skill_name, str(skill_md))
                summary["added"].append(skill_name)
                logger.info(f"[Delta] New skill indexed: {skill_name}")
            elif current_hash != stored_hash:
                # Changed — re-index
                retriever.ingest_skill(skill_name, str(skill_md))
                summary["updated"].append(skill_name)
                logger.info(f"[Delta] Changed skill re-indexed: {skill_name}")
            else:
                # No change — skip
                summary["unchanged"].append(skill_name)
        except Exception as e:
            logger.error(f"[Delta] Failed to process skill {skill_name}: {e}")
            summary["errors"].append(f"{skill_name}: {e}")
            new_hashes.pop(skill_name, None)

    _save_skill_hashes(new_hashes)
    return summary


@app.on_event("startup")
async def index_all_skills():
    """Plan A+C: Async startup — server is immediately available.
    Skill FAISS indexing runs in background using hash-based delta.
    """
    from main import get_uma
    from core.retriever import retriever
    from core.watcher import DirectoryWatcher
    import asyncio

    async def _background_index():
        try:
            uma = get_uma()
            summary = await asyncio.get_event_loop().run_in_executor(
                None, _delta_index_skills, uma, retriever
            )
            logger.info(
                f"[Startup] Delta index complete — "
                f"added:{len(summary['added'])} updated:{len(summary['updated'])} "
                f"removed:{len(summary['removed'])} unchanged:{len(summary['unchanged'])} "
                f"errors:{len(summary['errors'])}"
            )
        except Exception as e:
            logger.error(f"[Startup] Background skill indexing failed: {e}")

    try:
        uma = get_uma()
        # Start Watchdog first so user can use the system immediately
        global __watcher
        __watcher = DirectoryWatcher(str(WORKSPACE_DIR), str(uma.registry.skills_home), retriever)
        __watcher.start()
        logger.info("[Startup] Watchdog started. Skill delta indexing running in background...")

        # Plan C: fire-and-forget background task
        asyncio.create_task(_background_index())

        # Workspace document sync: index any workspace files not yet in FAISS
        async def _sync_workspace_docs():
            try:
                ws_summary = await asyncio.get_event_loop().run_in_executor(
                    None, retriever.sync_workspace, str(WORKSPACE_DIR)
                )
                logger.info(
                    f"[Startup] Workspace sync complete — "
                    f"added:{len(ws_summary['added'])} removed:{len(ws_summary['removed'])} "
                    f"already:{len(ws_summary['already'])}"
                )
            except Exception as e:
                logger.error(f"[Startup] Workspace sync failed: {e}")

        asyncio.create_task(_sync_workspace_docs())

    except Exception as e:
        logger.error(f"Failed to start watcher or schedule indexing: {e}")

@app.on_event("shutdown")
async def shutdown_system():
    global __watcher
    if '__watcher' in globals() and __watcher is not None:
        __watcher.stop()
    # D-07: Flush all in-memory sessions to MEMORY.md before shutdown
    _session_mgr.flush_all_sessions(_make_llm_callable())
    logger.info("Shutdown: all sessions persisted to MEMORY.md")



# ─── D-07: Unified Session Management ─────────────────────────────────────────

from core.session import SessionManager

_session_mgr = SessionManager(str(PROJECT_ROOT))

# ─── System Prompt Cache ─────────────────────────────────────────────────────
_prompt_cache: dict = {}

def invalidate_prompt_cache():
    """Invalidate the system prompt cache. Call after rescan, upload, or delete."""
    _prompt_cache.clear()

def build_system_prompt(selected_docs: list = None) -> str:
    """
    Dynamically build system prompt with live skill list (name + one-liner)
    and knowledge base document count. Result is cached per selection.
    """
    cache_key = tuple(sorted(selected_docs)) if selected_docs is not None else "ALL"
    if cache_key in _prompt_cache:
        return _prompt_cache[cache_key]

    uma = get_uma()
    skill_lines = []
    for name, data in uma.registry.skills.items():
        meta = data["metadata"]
        status = "✅" if meta.get("_env_ready", False) else "⚠"
        # One-liner: first sentence only, max 50 chars to keep prompt concise
        desc = meta.get("description", "").split("\n")[0][:50]
        skill_lines.append(f"  - {name} {status}  {desc}")

    skills_block = "\n".join(skill_lines) if skill_lines else "  （無已安裝技能）"

    all_doc_files = [
        f for f in WORKSPACE_DIR.iterdir()
        if f.is_file() and not f.name.startswith(".")
    ] if WORKSPACE_DIR.exists() else []

    doc_files = [f for f in all_doc_files if selected_docs is None or f.name in selected_docs]

    doc_names = []
    if doc_files:
        try:
            names_file = WORKSPACE_DIR / ".names.json"
            names_map = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
            for f in doc_files:
                original_name = names_map.get(f.name, f.name)
                doc_names.append(f"  - {original_name}")
        except Exception:
            doc_names = [f"  - {f.name}" for f in doc_files]

    doc_block = "\n".join(doc_names) if doc_names else "  （無已上傳文件）"

    prompt = (
        "你是研發組 MCP Agent Console 的 AI 助理。\n"
        "你的職責是回答用戶關於技術、開發、管理或任何其他問題。\n\n"
        f"【即時系統狀態】\n"
        f"已安裝 Agent Skills（共 {len(skill_lines)} 個）：\n{skills_block}\n\n"
        f"知識庫文件清單（共 {len(doc_files)} 份）：\n{doc_block}\n\n"
        "如用戶詢問技能功能或文件詳情，請依據上方資訊準確回答，"
        "詳細技能定義將在用戶附加技能時另行提供。\n"
        "請以繁體中文回覆，保持專業、清晰、簡潔。"
    )
    _prompt_cache[cache_key] = prompt
    return prompt


# ─── RAG Heuristic ────────────────────────────────────────────────────────────
_RAG_KEYWORDS = {
    # 繁體中文
    "工作區", "檔案", "文件", "內容", "資料", "報告", "分析",
    "目前技能", "知識庫", "上傳", "參考", "根據", "文章", "查詢",
    # English
    "file", "doc", "document", "skill", "content", "workspace",
    "knowledge", "reference", "report", "analyze", "based on",
    # 日本語
    "ファイル", "内容", "参考", "ドキュメント", "スキル", "分析",
}

def _should_trigger_rag(user_input: str) -> bool:
    """Heuristic: trigger RAG only when query suggests document/workspace relevance."""
    lowered = user_input.lower()
    return any(kw in lowered for kw in _RAG_KEYWORDS)


def get_session(session_id: str) -> List[Dict[str, Any]]:
    return _session_mgr.get_or_create_conversation(session_id, build_system_prompt())


def _make_llm_callable():
    """Build a lightweight LLM summarizer using the best available adapter."""
    from adapters.openai_adapter import OpenAIAdapter
    uma = get_uma()
    adapter = OpenAIAdapter(uma)
    if adapter.is_available:
        def caller(prompt: str) -> str:
            r = adapter.simple_chat([{"role": "user", "content": prompt}])
            return r.get("content", "") if r.get("status") == "success" else ""
        return caller
    return None  # Fallback: session.py will write turn-count placeholder


# ─── Request / Response Models ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    user_input: str
    session_id: Optional[str] = "default"
    model: Optional[str] = "openai"
    injected_skill: Optional[str] = None  # For "Attach Skill" feature
    execute: Optional[bool] = False       # Switch to agent mode for executing skills
    attached_file: Optional[str] = None   # Absolute path of uploaded workspace file
    selected_docs: Optional[List[str]] = None # List of filenames user selected in UI


class RenameRequest(BaseModel):
    new_name: str


class SkillUpdateRequest(BaseModel):
    yaml_content: str   # Raw YAML frontmatter string to validate + write


class ExecuteRequest(BaseModel):
    skill_name: str
    arguments: Dict[str, Any] = {}


class SearchRequest(BaseModel):
    query: str

class UrlSourcingRequest(BaseModel):
    url: str

class TextSourcingRequest(BaseModel):
    name: str
    content: str

class ResearchRequest(BaseModel):
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
            
        # 5. Persist original filename to .names.json registry for display purposes
        names_file = WORKSPACE_DIR / ".names.json"
        try:
            names = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
            names[hashed_filename] = filename
            names_file.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to update .names.json: {e}")

        # 6. Immediate vectorization via background task (do not rely solely on Watchdog)
        vectorized_status = "unsupported"
        if extension in {".txt", ".md", ".pdf", ".csv", ".docx"}:
            vectorized_status = "indexing"
            from core.retriever import retriever as _upload_retriever
            background_tasks.add_task(_upload_retriever.ingest_document, str(final_path))
            logger.info(f"Background FAISS indexing queued for: {hashed_filename}")

        invalidate_prompt_cache()  # Doc count changed — before return
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


@app.post("/api/documents/url", tags=["Documents"])
async def add_url_source(req: UrlSourcingRequest):
    """
    Scrape a URL, extract title/text, and save as a .md file in workspace/.
    """
    import httpx
    from bs4 import BeautifulSoup
    
    url = req.url
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Remove scripts and styles
        for s in soup(["script", "style"]):
            s.decompose()
            
        title = soup.title.string.strip() if soup.title else "Scraped_Content"
        # Sanitize title for filename
        safe_title = sanitize_filename(title)
        if len(safe_title) > 50: safe_title = safe_title[:50]
        
        text_content = soup.get_text(separator="\n\n").strip()
        
        if not text_content:
            raise HTTPException(status_code=400, detail="Could not extract text from URL")
            
        # Wrap in Markdown
        md_content = f"# {title}\n\nSource: {url}\n\n---\n\n{text_content}"
        
        # Save to workspace
        file_hash = hashlib.sha256(md_content.encode()).hexdigest()
        filename = f"url_{file_hash[:12]}.md"
        final_path = WORKSPACE_DIR / filename
        
        final_path.write_text(md_content, encoding="utf-8")
        
        return {
            "status": "success",
            "filename": filename,
            "title": title,
            "url": url,
            "vectorized": "pending"
        }
        
    except httpx.HTTPError as e:
        logger.error(f"HTTP Error scraping URL {url}: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {str(e)}")
    except Exception as e:
        logger.error(f"Scraping error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def call_google_search(query: str) -> List[Dict[str, Any]]:
    """Helper to call Google Custom Search JSON API."""
    import httpx
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    cse_id = os.getenv("GOOGLE_CSE_ID", "").strip()
    
    if not api_key or not cse_id:
        logger.warning("Google Search API credentials missing in .env")
        return []

    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': api_key,
        'cx': cse_id,
        'q': query,
        'num': 10 # Get top 10 results
    }
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.error(f"Google Search API failed with status {resp.status_code}: {resp.text}")
                return []
            
            data = resp.json()
            items = data.get("items", [])
            sources = []
            for item in items:
                # Extract favicon from pagemap if available
                pagemap = item.get("pagemap", {})
                favicon = ""
                if "cse_image" in pagemap:
                    favicon = pagemap["cse_image"][0].get("src", "")
                elif "metatags" in pagemap:
                    favicon = pagemap["metatags"][0].get("og:image", "")
                
                sources.append({
                    "title": item.get("title", "No Title"),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "favicon": favicon
                })
            return sources
    except Exception as e:
        logger.error(f"Error calling Google Search API: {e}")
        return []

@app.post("/api/research", tags=["Documents"])
async def research_sources(req: ResearchRequest):
    """
    Research sources using Google Search with a fallback to OpenAI internal knowledge.
    """
    from adapters.openai_adapter import OpenAIAdapter
    uma = get_uma()
    adapter = OpenAIAdapter(uma)
    
    # 1. Try Google Search First
    google_sources = await call_google_search(req.query)
    if google_sources:
        logger.info(f"Using Google Search results for query: {req.query}")
        return {"status": "success", "sources": google_sources, "method": "google_search"}

    # 2. Fallback to OpenAI Research
    logger.info(f"Falling back to OpenAI research for query: {req.query}")
    if not adapter.is_available:
        raise HTTPException(status_code=503, detail="OpenAI adapter is not available and Google Search failed.")

    prompt = (
        f"Role: 你現在是一個專業的「網際網路研究專家」。目前的 Google 搜尋系統暫時由你接管，你的任務是針對使用者提出的關鍵字進行深度資源檢索。\n"
        f"Task:\n"
        f"1. 針對使用者的關鍵字：'{req.query}'，運用你廣大且精確的知識庫進行檢索。\n"
        f"2. 篩選出關聯性最高、最具權威性的 15-20 個真實存在的網頁連結。\n"
        f"3. 嚴格禁止只提供首頁連結（如 www.google.com），必須提供能獲取具體資訊的「深度內頁連結」（如具體的文章、技術論壇、官方新聞稿等）。\n"
        f"Constraints:\n"
        f"- 優先選擇具備長久參考價值的深度資料。\n"
        f"- 排除廣告與無關的社群貼文。\n"
        f"- 確保 URL 格式正確且為可存取的長連結，而非縮網址。\n\n"
        f"Format the output as a JSON array of objects, each containing: "
        f"'title' (string), 'url' (string, valid URL), 'snippet' (string, 1-2 sentences summary), "
        f"and 'favicon' (string, optional URL to a favicon or empty string).\n"
        f"Return ONLY the raw JSON array. DO NOT include markdown code blocks or any other text."
    )

    try:
        messages = [
            {"role": "system", "content": "You are a helpful assistant that provides source lists in JSON format. Return only valid JSON."},
            {"role": "user", "content": prompt}
        ]
        result = adapter.simple_chat(messages)
        
        if result.get("status") != "success":
            raise HTTPException(status_code=500, detail=result.get("message", "OpenAI call failed"))

        content = result["content"].strip()
        import re
        match = re.search(r'\[.*\]', content, re.DOTALL)
        json_str = match.group(0) if match else content
        if not match and json_str.startswith("```"):
            json_str = json_str.replace("```json", "", 1).replace("```", "", 1).strip()

        sources = json.loads(json_str)
        if not isinstance(sources, list):
            raise ValueError("LLM did not return a list")

        return {"status": "success", "sources": sources, "method": "llm_research"}

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON from OpenAI research results: {e}")
        raise HTTPException(status_code=500, detail=f"解析研究結果失敗: {str(e)}")
    except Exception as e:
        logger.error(f"Error in /api/research: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/text", tags=["Documents"])
async def add_text_source(req: TextSourcingRequest):
    """
    Save manually pasted text as a .txt file in workspace/.
    """
    try:
        name = req.name.strip() or "Pasted_Text"
        content = req.content.strip()
        
        if not content:
            raise HTTPException(status_code=400, detail="Content cannot be empty")
            
        # Save to workspace
        file_hash = hashlib.sha256(content.encode()).hexdigest()
        safe_name = sanitize_filename(name)
        filename = f"text_{file_hash[:12]}.txt"
        final_path = WORKSPACE_DIR / filename
        
        final_path.write_text(content, encoding="utf-8")
        
        return {
            "status": "success",
            "filename": filename,
            "original_name": name,
            "size": len(content),
            "vectorized": "pending"
        }
    except Exception as e:
        logger.error(f"Error saving text source: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/list", tags=["Documents"])
def list_documents():
    """
    List all uploaded files in workspace/.
    Also reports which files are currently indexed in FAISS.
    Returns original_name (user-facing) alongside the hashed filename.
    """
    from core.retriever import retriever
    indexed = set(retriever.list_indexed_files())

    # Load original filename registry
    names_file = WORKSPACE_DIR / ".names.json"
    try:
        names = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
    except Exception:
        names = {}

    files = []
    for f in sorted(WORKSPACE_DIR.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            files.append({
                "filename": f.name,
                "original_name": names.get(f.name, f.name),  # Fall back to hash name if no record
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
    invalidate_prompt_cache()  # Doc count changed
    return {"status": "success", "message": f"'{filename}' 已刪除"}


@app.post("/api/documents/{filename}/rename", tags=["Documents"])
def rename_document_endpoint(filename: str, req: RenameRequest):
    """
    Rename an uploaded file.
    We just update the `.names.json` registry instead of modifying the real hashed filename.
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    target = (WORKSPACE_DIR / filename).resolve()
    if not str(target).startswith(str(WORKSPACE_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Path traversal denied")

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found")

    new_name = req.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="New name cannot be empty")

    names_file = WORKSPACE_DIR / ".names.json"
    names = {}
    if names_file.exists():
        try:
            names = json.loads(names_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Ensure it keeps original extension from the file
    original_ext = target.suffix
    if not new_name.endswith(original_ext):
        new_name += original_ext

    names[filename] = new_name
    names_file.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
    
    logger.info(f"Renamed file {filename} to {new_name}")
    invalidate_prompt_cache() # Names changed

    return {"status": "success", "message": f"'{filename}' 已重新命名為 '{new_name}'"}



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
            skill_path = uma.registry.skills_home / req.injected_skill
            
            assets_injection = ""
            assets_dir = skill_path / "assets"
            if assets_dir.is_dir() and any(assets_dir.iterdir()):
                assets_injection = f"\n\n[素材庫提醒]\n此技能備有輸出模板或範例素材於 {assets_dir} 中，若需要結構化輸出，請先參考該內容。"

            reasoning_injection = "\n\n[核心原則]\n即使沒有腳本可以執行，你「必須」嚴格遵循下方技能定義中的『思維邏輯 (Reasoning Flow)』來回應用戶。"

            if req.execute:
                # Execute mode: SKILL.md becomes the operational guide
                skill_context = (
                    f"\n\n[技能操作指引 — {req.injected_skill}]\n"
                    f"以下是該技能的完整定義與操作說明，請嚴格依照此指引完成任務。\n"
                    f"你可以使用提供的工具來執行程式碼或操作檔案。\n"
                    f"{reasoning_injection}{assets_injection}\n\n"
                    f"---\n{full_skill_md}\n---\n"
                )
            else:
                # Pure chat mode: SKILL.md becomes knowledge reference
                skill_context = (
                    f"\n\n[技能知識參考 — {req.injected_skill}]\n"
                    f"以下是該技能的完整定義與操作說明，請以此知識為基礎回答問題。\n"
                    f"注意：你目前處於純對話模式，不具有執行工具的能力。\n"
                    f"{reasoning_injection}{assets_injection}\n\n"
                    f"---\n{full_skill_md}\n---\n"
                )
        elif skill_data:
            # Fallback: if SKILL.md not readable, use description
            meta = skill_data["metadata"]
            skill_context = f"\n\n[技能參考 — {req.injected_skill}] {meta.get('description', '無描述')}"

    # 3.5. RAG: Semantic heuristic — Dual check for Workspace Docs and Skills
    from core.retriever import retriever as _retriever
    rag_context = ""
    # Only try to retrieve if at least one doc is unchecked, or if there's no selected_docs array (empty array means no docs allowed)
    should_retrieve_docs = req.selected_docs is None or len(req.selected_docs) > 0
    if _should_trigger_rag(req.user_input):
        doc_results = ""
        if should_retrieve_docs:
            # Dynamic top_k: at least 1 chunk per selected file + 2 extra for depth
            num_docs = len(req.selected_docs) if req.selected_docs else 3
            doc_top_k = max(3, num_docs + 2)
            doc_results = _retriever.search_context(req.user_input, top_k=doc_top_k, filter_type="workspace", allowed_filenames=req.selected_docs)
        skill_results = _retriever.search_context(req.user_input, top_k=2, filter_type="skill")
        
        if doc_results or skill_results:
            rag_context = "\n\n[語意檢索結果]\n"
            if doc_results:
                rag_context += (
                    f"【知識庫文件內容】（請以此為分析基底，如 NotebookLM 重點參照）\n{doc_results}\n\n"
                )
            if skill_results:
                rag_context += (
                    f"【內部技能設定參考】\n{skill_results}\n\n"
                )
            
            rag_context += (
                f"---\n"
                f"【分析與回答準則】\n"
                f"1. NotebookLM 風格：你的回答應「深度依賴」上述【知識庫文件內容】。使用者會依據這些文件來設計工作流程與系統技能，請以這些文件作為分析與評估的首要基底。\n"
                f"2. 允許使用者同時查閱「技能」與「文件」，請根據問題語意自行判斷應查閱與比對哪一部分。\n"
                f"3. 強制引用：引用知識庫內容時，句末必須標示來源，格式：[filename#chunk_x: 引用片段]。\n"
                f"4. 若檢索結果不包含使用者所問的資訊，請明白告知「知識庫中缺乏相關資訊」，絕不可自行編造。\n"
            )

    # 4. Append user message
    user_content = req.user_input + execution_context + skill_context + rag_context
    history_to_pass = history + [{"role": "user", "content": user_content}]

    # Update the dynamic system prompt to reflect newly checked/unchecked files mid-session
    if history_to_pass and history_to_pass[0].get("role") == "system":
        history_to_pass[0]["content"] = build_system_prompt(req.selected_docs)

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
        # D-07: Unified SessionManager handles appending and compressing memory
        _session_mgr.append_message(req.session_id, "user", req.user_input)
        _session_mgr.append_message(req.session_id, "assistant", result["content"])

        # Auto-flush every 5 messages using LLM summarization
        conv_len = len(_session_mgr.get_or_create_conversation(req.session_id))
        if conv_len > 0 and conv_len % 5 == 0:
            _session_mgr.flush_with_llm_summary(req.session_id, _make_llm_callable())

    return result


@app.post("/chat/flush/{session_id}", tags=["Chat"])
def flush_memory(session_id: str):
    """Manually persist a session's conversation to MEMORY.md with LLM summarization."""
    _session_mgr.flush_with_llm_summary(session_id, _make_llm_callable())
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

        # Re-register the skill immediately to refresh metadata and prevent 404 gaps
        uma.registry._register_skill(skill_path)

        logger.info(f"Skill '{skill_name}' updated. Registry refreshed.")
        return {
            "status": "success",
            "message": f"技能 '{skill_name}' 已更新，原始備份已儲存至 SKILL.md.bak",
            "backup_created": str(bak_path)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/skills/{skill_name}", tags=["Skill Management"])
def delete_skill(skill_name: str):
    """
    Permanently delete a skill directory and its content.
    Safety:
      1. Path sanitization (only delete within skills/ home).
      2. Clear FAISS index (prevent ghost skills in RAG).
      3. Invalidate prompt cache.
    """
    from core.retriever import retriever
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    
    # 1. Path Safety & Existence Check
    skills_home = uma.registry.skills_home.resolve()
    if skill:
        skill_path = skill["path"].resolve()
    else:
        # If not in registry (e.g. malformed), try to resolve folder name directly
        skill_path = (skills_home / skill_name).resolve()

    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    try:
        skill_path.relative_to(skills_home)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied: cannot delete outside skills directory")

    try:
        # 2. Clear FAISS index
        retriever.delete_document(skill_name)
        
        # 3. Delete directory
        def remove_readonly(func, path, _):
            import os, stat
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        shutil.rmtree(skill_path, onerror=remove_readonly)
        
        # 4. Cleanup Registry and Cache
        uma.registry.skills.pop(skill_name.lower(), None)
        invalidate_prompt_cache()
        
        logger.info(f"Skill '{skill_name}' permanently deleted.")
        return {"status": "success", "message": f"技能 '{skill_name}' 已永久刪除"}
    except Exception as e:
        logger.error(f"Failed to delete skill {skill_name}: {e}")
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
        # Re-register the skill immediately to refresh metadata and prevent 404 gaps
        uma.registry._register_skill(skill_path)

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

    # Force re-scan of this specific skill so registry refreshes dependency status
    uma.registry._register_skill(skill_path)

    return {
        "status": "done",
        "message": "安裝完成，請點擊「重新掃描」刷新技能狀態",
        "results": results
    }


@app.post("/skills/{skill_name}/upload", tags=["Skill Management"])
async def upload_skill_file(skill_name: str, file: UploadFile = File(...), file_type: str = Form(...)):
    """
    Upload a script, asset, or knowledge file directly into a skill's directory.
    file_type must be either 'script' (saved to scripts/), 'asset' (saved to assets/), or 'knowledge' (saved to references/).
    """
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    valid_types = {"script": "scripts", "asset": "assets", "knowledge": "references"}
    if file_type not in valid_types:
        raise HTTPException(status_code=400, detail="file_type must be 'script', 'asset', or 'knowledge'")
        
    skills_home = uma.registry.skills_home.resolve()
    skill_path = skill["path"].resolve()
    
    # Path safety
    try:
        skill_path.relative_to(skills_home)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied")

    # Determine destination dir
    target_dir = skill_path / valid_types[file_type]
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # Sanitize filename
    safe_name = sanitize_filename(file.filename)
    dest_path = target_dir / safe_name
    
    try:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        logger.info(f"File '{safe_name}' uploaded to {target_dir}")
        return {
            "status": "success",
            "message": f"檔案已成功上傳至技能的 {target_dir.name} 目錄！",
            "filename": safe_name,
            "path": str(dest_path.relative_to(skills_home))
        }
    except Exception as e:
        logger.error(f"Failed to upload {safe_name} to skill {skill_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/skills/{skill_name}/files", tags=["Skill Management"])
async def get_skill_files(skill_name: str):
    """
    Return lists of files found in references/, scripts/, and assets/ for a given skill.
    """
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    skill_path = Path(skill.get("path"))
    result = {"references": [], "scripts": [], "assets": []}
    
    for folder in result.keys():
        dir_path = skill_path / folder
        if dir_path.is_dir():
            # exclude hidden files/folders
            files = [f.name for f in dir_path.iterdir() if f.is_file() and not f.name.startswith('.')]
            result[folder] = sorted(files)
            
    return result


@app.delete("/skills/{skill_name}/files/{folder}/{filename}", tags=["Skill Management"])
async def delete_skill_file(skill_name: str, folder: str, filename: str):
    """
    Delete a specific file from a skill's directory (references, scripts, or assets).
    """
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    valid_folders = ["references", "scripts", "assets"]
    if folder not in valid_folders:
        raise HTTPException(status_code=400, detail=f"Invalid folder. Must be one of: {', '.join(valid_folders)}")

    skills_home = uma.registry.skills_home.resolve()
    skill_path = Path(skill.get("path")).resolve()
    
    # Sanitize filename to prevent directory traversal
    safe_name = sanitize_filename(filename)
    target_file = skill_path / folder / safe_name
    
    # Ensure the target file is actually within the skill's folder directory
    try:
        target_file.relative_to(skill_path / folder)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied")

    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail=f"File '{safe_name}' not found in '{folder}'")

    try:
        target_file.unlink()
        logger.info(f"File '{safe_name}' deleted from {skill_path / folder}")
        return {"status": "success", "message": f"檔案 {safe_name} 已成功刪除"}
    except Exception as e:
        logger.error(f"Failed to delete {safe_name} from skill {skill_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/skills/rescan", tags=["Skill Management"])
def rescan_skills():
    """Re-scan skills directory, hash-check for changes, and delta re-index FAISS.
    Only changed/new/removed skills are processed (Plan A).
    """
    from core.retriever import retriever
    uma = get_uma()
    uma.registry.skills.clear()
    uma.registry.validation_cache.clear()
    uma.registry.scan_skills()

    # Plan A: delta index — only re-process changed skills
    summary = _delta_index_skills(uma, retriever)
    invalidate_prompt_cache()  # Skill list may have changed

    parts = []
    if summary["added"]:     parts.append(f"新增 {len(summary['added'])} 個")
    if summary["updated"]:   parts.append(f"更新 {len(summary['updated'])} 個")
    if summary["removed"]:   parts.append(f"移除 {len(summary['removed'])} 個")
    if summary["unchanged"]: parts.append(f"{len(summary['unchanged'])} 個無異動")
    detail = "，".join(parts) if parts else "無異動"

    return {
        "status": "success",
        "total_skills": len(uma.registry.skills),
        "added":     summary["added"],
        "updated":   summary["updated"],
        "removed":   summary["removed"],
        "unchanged": len(summary["unchanged"]),
        "errors":    summary["errors"],
        "message":   f"技能庫重新掃描完成（{detail}）"
    }


class CreateSkillRequest(BaseModel):
    name: str           # Must be ASCII + hyphens only, e.g. "my-skill"
    display_name: str   # Human-readable name (any language)
    description: str    # Short description (any language)
    version: str = "1.0.0"
    category: str = ""  # Optional category tag
    no_script: bool = False # Whether it's a pure LLM logic skill


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
        (skill_path / "assets").mkdir()

        if req.no_script:
            # specialized template for no-script skills
            skill_md = f"""---
name: {name}
display_name: "{req.display_name}"
description: "{req.description}"
version: "{req.version}"
category: "{req.category}"
runtime_requirements: []
risk_level: "low"
---

# {req.display_name} (純 LLM 邏輯技能)

{req.description}

## 思維邏輯 (Reasoning Flow)

1. [第一步：分析...]
2. [第二步：執行...]
3. [第三步：產出...]

## 操作指南 (Instructions)

- 此技能「無需執行腳本」，完全依賴上述的思維邏輯進行處理。
- 請在此定義 LLM 在處理此任務時應遵循的具體原則。
- 若有參考範本，可置於 `assets/` 目錄，系統會自動參照。
"""
        else:
            # default template with script placeholder
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

## 思維邏輯 (Reasoning Flow)

1. [第一步：分析...]
2. [第二步：執行...]
3. [第三步：產出...]

## 操作指南 (Instructions)

- 此為新建技能，具體的操作步驟與限制請列於此。
- 若無需程式邏輯，僅需完善上方的「思維邏輯」，系統便能依照指示行動。
- 若需執行自訂程式，請於 `scripts/` 目錄下新增 `main.py` 以啟用執行功能。
- 若有參照範本，可置於 `assets/` 目錄。
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
