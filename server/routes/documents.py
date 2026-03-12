"""Document routes."""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel

from main import PROJECT_ROOT, get_uma
from server.adapters.openai_adapter import OpenAIAdapter

router = APIRouter(tags=["Documents"])
logger = logging.getLogger("MCP_Server.Router.Documents")
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)


class RenameRequest(BaseModel):
    new_name: str


class UrlSourcingRequest(BaseModel):
    url: str


class TextSourcingRequest(BaseModel):
    name: str
    content: str


class ResearchRequest(BaseModel):
    query: str


def _try_invalidate_prompt_cache():
    # During transition keep cache behavior aligned with legacy chat prompt cache.
    try:
        from router import invalidate_prompt_cache

        invalidate_prompt_cache()
    except Exception:
        pass


def sanitize_filename(filename: str) -> str:
    """Preserve Unicode/CJK names while blocking traversal and Windows-illegal chars."""
    filename = os.path.basename(filename)
    illegal_chars = {"\\", "/", ":", "*", "?", '"', "<", ">", "|", "\x00"}
    filename = "".join("_" if c in illegal_chars else c for c in filename)
    filename = filename.strip(". ").strip()
    return filename or "uploaded_file"


@router.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    """Upload file to workspace and queue indexing."""
    try:
        filename = file.filename or "unknown_file"
        safe_path = (WORKSPACE_DIR / filename).resolve()
        if not str(safe_path).startswith(str(WORKSPACE_DIR.resolve())):
            raise HTTPException(status_code=400, detail="Invalid filename (Directory Traversal Detected)")

        content = await file.read()
        file_hash = hashlib.sha256(content).hexdigest()

        extension = Path(filename).suffix.lower()
        allowed_exts = {
            ".jpg",
            ".jpeg",
            ".png",
            ".pdf",
            ".txt",
            ".md",
            ".csv",
            ".xlsx",
            ".docx",
            ".webm",
            ".wav",
            ".mp3",
            ".mp4",
        }
        if extension not in allowed_exts:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {extension}")

        hashed_filename = f"{file_hash[:16]}{extension}"
        final_path = (WORKSPACE_DIR / hashed_filename).resolve()

        if not final_path.exists():
            final_path.write_bytes(content)
            logger.info(f"File saved: {final_path.name} (Original: {filename})")
        else:
            logger.info(f"File already exists (Hash match): {final_path.name} (Original: {filename})")

        names_file = WORKSPACE_DIR / ".names.json"
        try:
            names = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
            names[hashed_filename] = filename
            names_file.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to update .names.json: {e}")

        vectorized_status = "unsupported"
        if extension in {".txt", ".md", ".pdf", ".csv", ".docx"}:
            vectorized_status = "indexing"
            from core.retriever import retriever as _upload_retriever

            background_tasks.add_task(_upload_retriever.ingest_document, str(final_path))
            logger.info(f"Background FAISS indexing queued for: {hashed_filename}")

        _try_invalidate_prompt_cache()
        return {
            "status": "success",
            "filename": hashed_filename,
            "original_filename": filename,
            "hash": file_hash,
            "path": str(final_path),
            "vectorized": vectorized_status,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/documents/url")
async def add_url_source(req: UrlSourcingRequest):
    """Scrape URL and save as markdown."""
    import httpx
    from bs4 import BeautifulSoup

    url = req.url
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            }
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for s in soup(["script", "style"]):
            s.decompose()

        title = soup.title.string.strip() if soup.title else "Scraped_Content"
        safe_title = sanitize_filename(title)
        if len(safe_title) > 50:
            safe_title = safe_title[:50]

        text_content = soup.get_text(separator="\n\n").strip()
        if not text_content:
            raise HTTPException(status_code=400, detail="Could not extract text from URL")

        md_content = f"# {title}\n\nSource: {url}\n\n---\n\n{text_content}"
        file_hash = hashlib.sha256(md_content.encode()).hexdigest()
        filename = f"url_{file_hash[:12]}.md"
        final_path = WORKSPACE_DIR / filename
        final_path.write_text(md_content, encoding="utf-8")

        names_file = WORKSPACE_DIR / ".names.json"
        try:
            names = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
            names[filename] = title
            names_file.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to update .names.json for URL: {e}")

        _try_invalidate_prompt_cache()
        return {"status": "success", "filename": filename, "title": title, "url": url, "vectorized": "pending"}
    except httpx.HTTPError as e:
        logger.error(f"HTTP Error scraping URL {url}: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {str(e)}")
    except Exception as e:
        logger.error(f"Scraping error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def call_google_search(query: str) -> List[Dict[str, Any]]:
    """Call Google Custom Search API."""
    import httpx

    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    cse_id = os.getenv("GOOGLE_CSE_ID", "").strip()
    if not api_key or not cse_id:
        logger.warning("Google Search API credentials missing in .env")
        return []

    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": api_key, "cx": cse_id, "q": query, "num": 10}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.error(f"Google Search API failed with status {resp.status_code}: {resp.text}")
                return []
            data = resp.json()
            sources = []
            for item in data.get("items", []):
                pagemap = item.get("pagemap", {})
                favicon = ""
                if "cse_image" in pagemap:
                    favicon = pagemap["cse_image"][0].get("src", "")
                elif "metatags" in pagemap:
                    favicon = pagemap["metatags"][0].get("og:image", "")
                sources.append(
                    {
                        "title": item.get("title", "No Title"),
                        "url": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "favicon": favicon,
                    }
                )
            return sources
    except Exception as e:
        logger.error(f"Error calling Google Search API: {e}")
        return []


@router.post("/api/research")
async def research_sources(req: ResearchRequest):
    """Research sources with Google Search fallback to OpenAI."""
    uma = get_uma()
    adapter = OpenAIAdapter(uma)

    google_sources = await call_google_search(req.query)
    if google_sources:
        return {"status": "success", "sources": google_sources, "method": "google_search"}

    if not adapter.is_available:
        raise HTTPException(status_code=503, detail="OpenAI adapter is not available and Google Search failed.")

    prompt = (
        f"Generate 15-20 high-quality sources for topic: '{req.query}'. "
        "Return a JSON array only with fields: title, url, snippet, favicon."
    )
    try:
        messages = [
            {"role": "system", "content": "You are a helpful assistant that provides source lists in JSON format."},
            {"role": "user", "content": prompt},
        ]
        result = None
        for chunk in adapter.simple_chat(messages):
            if chunk.get("status") in ("success", "error"):
                result = chunk
        if not result or result.get("status") != "success":
            raise HTTPException(status_code=500, detail=(result or {}).get("message", "OpenAI call failed"))
        content = result["content"].strip()
        import re

        match = re.search(r"\[.*\]", content, re.DOTALL)
        json_str = match.group(0) if match else content
        if not match and json_str.startswith("```"):
            json_str = json_str.replace("```json", "", 1).replace("```", "", 1).strip()
        sources = json.loads(json_str)
        if not isinstance(sources, list):
            raise ValueError("LLM did not return a list")
        return {"status": "success", "sources": sources, "method": "llm_research"}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON from OpenAI research results: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to parse research result: {str(e)}")
    except Exception as e:
        logger.error(f"Error in /api/research: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/documents/text")
async def add_text_source(req: TextSourcingRequest):
    """Save pasted text as workspace file."""
    try:
        name = req.name.strip() or "Pasted_Text"
        content = req.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="Content cannot be empty")

        file_hash = hashlib.sha256(content.encode()).hexdigest()
        filename = f"text_{file_hash[:12]}.txt"
        final_path = WORKSPACE_DIR / filename
        final_path.write_text(content, encoding="utf-8")

        names_file = WORKSPACE_DIR / ".names.json"
        try:
            names = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
            names[filename] = name
            names_file.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to update .names.json for pasted text: {e}")

        _try_invalidate_prompt_cache()
        return {"status": "success", "filename": filename, "original_name": name, "size": len(content), "vectorized": "pending"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving text source: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/documents/list")
def list_documents():
    """List workspace files and FAISS index status."""
    from core.retriever import retriever

    indexed = set(retriever.list_indexed_files())
    names_file = WORKSPACE_DIR / ".names.json"
    try:
        names = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
    except Exception:
        names = {}

    files = []
    for f in sorted(WORKSPACE_DIR.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            files.append(
                {
                    "filename": f.name,
                    "original_name": names.get(f.name, f.name),
                    "size": f.stat().st_size,
                    "indexed": f.name in indexed,
                }
            )
    return {"total": len(files), "files": files}


@router.delete("/api/documents/{filename}")
def delete_document(filename: str):
    """Delete workspace file and index entries."""
    from core.retriever import retriever

    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    target = (WORKSPACE_DIR / filename).resolve()
    if not str(target).startswith(str(WORKSPACE_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Path traversal denied")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found")

    retriever.delete_document(filename)
    target.unlink()

    names_file = WORKSPACE_DIR / ".names.json"
    if names_file.exists():
        try:
            names = json.loads(names_file.read_text(encoding="utf-8"))
            if filename in names:
                del names[filename]
                names_file.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to cleanup .names.json during deletion: {e}")

    _try_invalidate_prompt_cache()
    return {"status": "success", "message": f"'{filename}' deleted"}


@router.post("/api/documents/{filename}/rename")
def rename_document(filename: str, req: RenameRequest):
    """Rename user-facing name in names registry."""
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
    original_ext = target.suffix
    if not new_name.endswith(original_ext):
        new_name += original_ext
    names[filename] = new_name
    names_file.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")

    _try_invalidate_prompt_cache()
    return {"status": "success", "message": f"'{filename}' renamed to '{new_name}'"}
