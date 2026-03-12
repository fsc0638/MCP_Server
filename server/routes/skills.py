"""Skill management routes."""

import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import yaml
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from main import get_uma
from server.schemas.skills import SkillUpdateRequest, CreateSkillRequest

router = APIRouter(tags=["Skill Management"])
logger = logging.getLogger("MCP_Server.Router.Skills")


def sanitize_filename(filename: str) -> str:
    filename = Path(filename).name
    illegal_chars = {"\\", "/", ":", "*", "?", '"', "<", ">", "|", "\x00"}
    filename = "".join("_" if c in illegal_chars else c for c in filename)
    filename = filename.strip(". ").strip()
    return filename or "uploaded_file"


def _try_invalidate_prompt_cache():
    try:
        from router import invalidate_prompt_cache

        invalidate_prompt_cache()
    except Exception:
        pass


@router.get("/skills/list")
def list_skills():
    uma = get_uma()
    skills: Dict[str, Dict[str, Any]] = {}
    for name, data in uma.registry.skills.items():
        meta = data["metadata"]
        skills[name] = {
            "description": meta.get("description", ""),
            "version": meta.get("version", "unknown"),
            "ready": meta.get("_env_ready", False),
            "missing_deps": meta.get("_missing_deps", []),
            "path": str(data["path"]),
        }
    return {"total": len(skills), "skills": skills}


@router.get("/skills/{skill_name}")
def get_skill(skill_name: str):
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
            "metadata": {k: v for k, v in skill["metadata"].items() if not k.startswith("_")},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/skills/{skill_name}")
def update_skill(skill_name: str, req: SkillUpdateRequest):
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    skill_path = skill["path"].resolve()
    skills_home = uma.registry.skills_home.resolve()
    try:
        skill_path.relative_to(skills_home)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied: skill path is outside skills directory")

    skill_md_path = skill_path / "SKILL.md"
    bak_path = skill_path / "SKILL.md.bak"
    new_content = req.yaml_content
    if not new_content.startswith("---"):
        raise HTTPException(status_code=422, detail="SKILL.md must start with '---'")
    try:
        parts = new_content.split("---")
        if len(parts) < 3:
            raise ValueError("Missing closing '---' for frontmatter")
        yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        raise HTTPException(status_code=422, detail=f"YAML validation failed: {str(e)}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        if skill_md_path.exists():
            shutil.copy2(skill_md_path, bak_path)
        skill_md_path.write_text(new_content, encoding="utf-8")
        uma.registry._register_skill(skill_path)
        _try_invalidate_prompt_cache()
        return {
            "status": "success",
            "message": f"Skill '{skill_name}' updated and backup created.",
            "backup_created": str(bak_path),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/skills/{skill_name}")
def delete_skill(skill_name: str):
    from core.retriever import retriever

    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    skills_home = uma.registry.skills_home.resolve()
    if skill:
        skill_path = skill["path"].resolve()
    else:
        skill_path = (skills_home / skill_name).resolve()
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    try:
        skill_path.relative_to(skills_home)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied")

    try:
        retriever.delete_document(skill_name)

        def remove_readonly(func, path, _):
            import os
            import stat

            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        shutil.rmtree(skill_path, onerror=remove_readonly)
        uma.registry.skills.pop(skill_name.lower(), None)
        _try_invalidate_prompt_cache()
        return {"status": "success", "message": f"Skill '{skill_name}' deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_name}/rollback")
def rollback_skill(skill_name: str):
    uma = get_uma()
    skills_home = uma.registry.skills_home
    skill_path = skills_home / skill_name
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    bak_path = skill_path / "SKILL.md.bak"
    skill_md_path = skill_path / "SKILL.md"
    if not bak_path.exists():
        raise HTTPException(status_code=404, detail="Backup SKILL.md.bak not found")
    try:
        shutil.copy2(bak_path, skill_md_path)
        uma.registry._register_skill(skill_path)
        _try_invalidate_prompt_cache()
        return {"status": "success", "message": f"Skill '{skill_name}' rolled back"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_name}/install")
def install_skill_deps(skill_name: str):
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    missing = skill["metadata"].get("_missing_deps", [])
    if not missing:
        return {"status": "already_ready", "message": "No missing dependencies"}
    results = []
    for pkg in missing:
        try:
            proc = subprocess.run([sys.executable, "-m", "pip", "install", pkg], capture_output=True, text=True, timeout=120)
            if proc.returncode == 0:
                results.append({"package": pkg, "status": "installed"})
            else:
                results.append({"package": pkg, "status": "failed", "error": proc.stderr[:300]})
        except Exception as e:
            results.append({"package": pkg, "status": "error", "error": str(e)})

    skill_path = skill["path"]
    uma.registry._register_skill(skill_path)
    _try_invalidate_prompt_cache()
    return {"status": "done", "results": results}


@router.post("/skills/{skill_name}/upload")
async def upload_skill_file(skill_name: str, file: UploadFile = File(...), file_type: str = Form(...)):
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    valid_types = {"script": "scripts", "asset": "assets", "knowledge": "references"}
    if file_type not in valid_types:
        raise HTTPException(status_code=400, detail="file_type must be 'script', 'asset', or 'knowledge'")

    skills_home = uma.registry.skills_home.resolve()
    skill_path = skill["path"].resolve()
    try:
        skill_path.relative_to(skills_home)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied")

    target_dir = skill_path / valid_types[file_type]
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(file.filename or "uploaded_file")
    dest_path = target_dir / safe_name
    try:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {"status": "success", "filename": safe_name, "path": str(dest_path.relative_to(skills_home))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/{skill_name}/files")
async def get_skill_files(skill_name: str):
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    skill_path = Path(skill.get("path"))
    result = {"references": [], "scripts": [], "assets": []}
    for folder in result.keys():
        dir_path = skill_path / folder
        if dir_path.is_dir():
            files = [f.name for f in dir_path.iterdir() if f.is_file() and not f.name.startswith(".")]
            result[folder] = sorted(files)
    return result


@router.delete("/skills/{skill_name}/files/{folder}/{filename}")
async def delete_skill_file(skill_name: str, folder: str, filename: str):
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    valid_folders = ["references", "scripts", "assets"]
    if folder not in valid_folders:
        raise HTTPException(status_code=400, detail=f"Invalid folder. Must be one of: {', '.join(valid_folders)}")

    skill_path = Path(skill.get("path")).resolve()
    safe_name = sanitize_filename(filename)
    target_file = skill_path / folder / safe_name
    try:
        target_file.relative_to(skill_path / folder)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied")
    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail=f"File '{safe_name}' not found in '{folder}'")
    try:
        target_file.unlink()
        return {"status": "success", "message": f"File {safe_name} deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/rescan")
def rescan_skills():
    from core.retriever import retriever
    from server.services.runtime import delta_index_skills

    uma = get_uma()
    uma.registry.skills.clear()
    uma.registry.validation_cache.clear()
    uma.registry.scan_skills()
    summary = delta_index_skills(uma, retriever)
    _try_invalidate_prompt_cache()
    return {
        "status": "success",
        "total_skills": len(uma.registry.skills),
        "added": summary["added"],
        "updated": summary["updated"],
        "removed": summary["removed"],
        "unchanged": len(summary["unchanged"]),
        "errors": summary["errors"],
    }


@router.post("/skills/create")
def create_skill(req: CreateSkillRequest):
    uma = get_uma()
    skills_home = uma.registry.skills_home

    name = req.name.strip().lower().replace("_", "-")
    if not name.startswith("mcp-"):
        name = f"mcp-{name}"
    if not re.match(r"^[a-z0-9-]+$", name):
        raise HTTPException(status_code=422, detail="Skill name must be lowercase ASCII letters, numbers, and hyphens")
    if len(name) < 5 or len(name) > 60:
        raise HTTPException(status_code=422, detail="Skill name length must be between 5 and 60")

    skill_path = (skills_home / name).resolve()
    try:
        skill_path.relative_to(skills_home.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid target path")
    if skill_path.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{name}' already exists")

    try:
        skill_path.mkdir(parents=True)
        (skill_path / "scripts").mkdir()
        (skill_path / "references").mkdir()
        (skill_path / "assets").mkdir()
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
"""
        (skill_path / "SKILL.md").write_text(skill_md, encoding="utf-8")
        uma.registry.scan_skills()
        _try_invalidate_prompt_cache()
        return {"status": "success", "skill_name": name, "path": str(skill_path)}
    except Exception as e:
        if skill_path.exists():
            shutil.rmtree(skill_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))
