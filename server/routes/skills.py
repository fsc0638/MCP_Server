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
from server.schemas.skills import SkillUpdateRequest, SkillDeleteRequest, CreateSkillRequest
from server.services.prompt_cache import invalidate_prompt_cache

router = APIRouter(tags=["Skill Management"])
logger = logging.getLogger("MCP_Server.Router.Skills")


def sanitize_filename(filename: str) -> str:
    filename = Path(filename).name
    illegal_chars = {"\\", "/", ":", "*", "?", '"', "<", ">", "|", "\x00"}
    filename = "".join("_" if c in illegal_chars else c for c in filename)
    filename = filename.strip(". ").strip()
    return filename or "uploaded_file"


def sync_skills_git(message: str, user_name: str = "", user_id: str = ""):
    """
    Synchronize the Agent_skills local repository with the remote.
    Performs: git add ., git commit -m message, git push origin main.
    Commit message includes user info and timestamp for audit trail.
    """
    uma = get_uma()
    skills_home = uma.registry.skills_home.resolve()

    # Find the git repo root — skills_home may be Agent_skills/skills/,
    # but .git lives at Agent_skills/
    git_root = skills_home
    if not (git_root / ".git").exists() and (git_root.parent / ".git").exists():
        git_root = git_root.parent
    if not (git_root / ".git").exists():
        logger.warning(f"Git sync skipped: {git_root} is not a Git repository.")
        return {"status": "skipped", "message": "Not a git repository"}

    # Build commit message with user info and timestamp
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_msg = f"[Skill Mgmt] {message}"
    if user_name or user_id:
        commit_msg += f"\n\nUser: {user_name} ({user_id}) | {now}"
    else:
        commit_msg += f"\n\nUser: system | {now}"

    try:
        # 1. git add .
        subprocess.run(["git", "add", "."], cwd=git_root, check=True, capture_output=True)
        # 2. git commit (allow failure if no changes)
        proc = subprocess.run(["git", "commit", "-m", commit_msg], cwd=git_root, capture_output=True, text=True)
        if proc.returncode != 0 and "nothing to commit" not in proc.stdout.lower():
             logger.error(f"Git commit failed: {proc.stderr}")
             return {"status": "error", "error": f"Commit failed: {proc.stderr}"}

        # 3. git push
        subprocess.run(["git", "push", "origin", "main"], cwd=git_root, check=True, capture_output=True)
        logger.info(f"Git sync successful for Agent_skills: {message} (by {user_name})")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Git sync exception: {e}")
        return {"status": "error", "error": str(e)}


@router.get("/skills/list")
def list_skills():
    uma = get_uma()
    skills: Dict[str, Dict[str, Any]] = {}
    for name, data in uma.registry.skills.items():
        meta = data["metadata"]
        skills[name] = {
            "description": meta.get("description", ""),
            "display_name": meta.get("display_name", ""),
            "version": meta.get("version", "unknown"),
            "ready": meta.get("_env_ready", False),
            "missing_deps": meta.get("_missing_deps", []),
            "path": str(data["path"]),
            "scope": meta.get("_scope", "system"),
            "short_name": meta.get("_short_name", name),
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

        # Auto-analyze complexity and inject recommended_models
        try:
            from server.services.workflow_llm_router import analyze_skill_complexity
            has_refs = (skill_path / "references").exists() or (skill_path / "assets").exists()
            has_scripts = (skill_path / "scripts" / "main.py").exists()
            rec_models = analyze_skill_complexity(new_content, has_references=has_refs, has_scripts=has_scripts)

            # Inject recommended_models into YAML frontmatter if not already set by user
            _parts = new_content.split("---")
            if len(_parts) >= 3:
                _meta = yaml.safe_load(_parts[1]) or {}
                if "recommended_models" not in _meta:
                    # Insert before closing ---
                    _models_yaml = "recommended_models:\n"
                    for _prov, _model in rec_models.items():
                        _models_yaml += f"  {_prov}: {_model}\n"
                    new_content = "---\n" + _parts[1].rstrip() + "\n" + _models_yaml + "---\n" + "---".join(_parts[2:])
                    logger.info(f"[Skills] Auto-recommended models for {skill_name}: {rec_models}")
        except Exception as _ae:
            logger.debug(f"[Skills] Model recommendation skipped: {_ae}")

        skill_md_path.write_text(new_content, encoding="utf-8")
        uma.registry._register_skill(skill_path)
        invalidate_prompt_cache()
        sync_res = sync_skills_git(
            f"Updated skill {skill_name}",
            user_name=req.user_name,
            user_id=req.user_id,
        )
        return {
            "status": "success",
            "message": f"Skill '{skill_name}' updated and backup created.",
            "backup_created": str(bak_path),
            "git_sync": sync_res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/skills/{skill_name}")
def delete_skill(skill_name: str, req: SkillDeleteRequest):
    from server.core.retriever import retriever

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
        invalidate_prompt_cache()
        sync_res = sync_skills_git(
            f"Deleted skill {skill_name} | Reason: {req.reason}",
            user_name=req.user_name,
            user_id=req.user_id,
        )
        return {"status": "success", "message": f"Skill '{skill_name}' deleted.", "git_sync": sync_res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_name}/rename")
def rename_skill(skill_name: str, body: dict):
    """Rename a skill directory. Updates SKILL.md name field and git syncs."""
    new_name = body.get("new_name", "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="new_name is required")
    # Validate name format
    import re as _re
    if not _re.match(r'^mcp-[a-z0-9-]+$', new_name):
        raise HTTPException(status_code=400, detail="Name must match pattern: mcp-{lowercase-ascii-hyphens}")
    if new_name == skill_name:
        return {"status": "success", "message": "Name unchanged"}

    uma = get_uma()
    skills_home = uma.registry.skills_home.resolve()
    old_path = (skills_home / skill_name).resolve()
    new_path = (skills_home / new_name).resolve()

    if not old_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    if new_path.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{new_name}' already exists")

    try:
        old_path.relative_to(skills_home)
        new_path.relative_to(skills_home)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal denied")

    try:
        old_path.rename(new_path)
        # Update name in SKILL.md
        skill_md_path = new_path / "SKILL.md"
        if skill_md_path.exists():
            content = skill_md_path.read_text(encoding="utf-8")
            content = content.replace(f"name: {skill_name}", f"name: {new_name}", 1)
            skill_md_path.write_text(content, encoding="utf-8")
        # Git sync
        sync_res = sync_skills_git(f"Renamed skill: {skill_name} → {new_name}")
        # Re-register
        uma.registry.scan_skills()
        invalidate_prompt_cache()
        return {"status": "success", "old_name": skill_name, "new_name": new_name, "git_sync": sync_res}
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
        invalidate_prompt_cache()
        sync_res = sync_skills_git(f"Rolled back skill {skill_name}")
        return {"status": "success", "message": f"Skill '{skill_name}' rolled back", "git_sync": sync_res}
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
    invalidate_prompt_cache()
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
        sync_res = sync_skills_git(f"Uploaded {file_type} to {skill_name}: {safe_name}")
        return {"status": "success", "filename": safe_name, "path": str(dest_path.relative_to(skills_home)), "git_sync": sync_res}
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
        sync_res = sync_skills_git(f"Deleted {folder} file from {skill_name}: {safe_name}")
        return {"status": "success", "message": f"File {safe_name} deleted", "git_sync": sync_res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/rescan")
def rescan_skills():
    from server.core.retriever import retriever
    from server.services.runtime import delta_index_skills

    uma = get_uma()
    uma.registry.skills.clear()
    uma.registry.validation_cache.clear()
    uma.registry.scan_skills()
    summary = delta_index_skills(uma, retriever)
    invalidate_prompt_cache()
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

    name = req.name.strip().lower().replace("_", "-")
    if not name.startswith("mcp-"):
        name = f"mcp-{name}"
    if not re.match(r"^[a-z0-9-]+$", name):
        raise HTTPException(status_code=422, detail="Skill name must be lowercase ASCII letters, numbers, and hyphens")
    if len(name) < 5 or len(name) > 60:
        raise HTTPException(status_code=422, detail="Skill name length must be between 5 and 60")

    # Resolve target directory based on scope
    scope = (req.scope or "system").lower()
    if scope == "department":
        if not req.owner:
            raise HTTPException(status_code=422, detail="owner (department_code) is required for department scope")
        base_dir = uma.registry.dept_skills_home
        if not base_dir:
            raise HTTPException(status_code=500, detail="DEPT_SKILLS_HOME not configured")
        target_dir = base_dir / req.owner
    elif scope == "personal":
        if not req.owner:
            raise HTTPException(status_code=422, detail="owner (user_id) is required for personal scope")
        base_dir = uma.registry.user_skills_home
        if not base_dir:
            raise HTTPException(status_code=500, detail="USER_SKILLS_HOME not configured")
        target_dir = base_dir / req.owner
    else:
        target_dir = uma.registry.skills_home

    skill_path = (target_dir / name).resolve()
    if skill_path.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{name}' already exists in {scope} scope")

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

# {req.display_name or name}

{req.description}
"""
        (skill_path / "SKILL.md").write_text(skill_md, encoding="utf-8")
        uma.registry.scan_skills()
        invalidate_prompt_cache()
        sync_res = sync_skills_git(f"Created {scope} skill {name}")
        return {"status": "success", "skill_name": name, "scope": scope, "path": str(skill_path), "git_sync": sync_res}
    except Exception as e:
        if skill_path.exists():
            shutil.rmtree(skill_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Skill Promote (scope migration) ────────────────────────────────────────

@router.post("/skills/{skill_name}/promote")
def promote_skill(skill_name: str, target_scope: str = "department", target_owner: str = ""):
    """
    Promote a skill to a higher scope:
    personal → department, department → system.
    Copies the entire skill directory to the target location.
    """
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    src_path = skill["path"].resolve()
    short_name = skill["metadata"].get("_short_name", skill_name)

    # Resolve target directory
    if target_scope == "system":
        dest_base = uma.registry.skills_home
    elif target_scope == "department":
        if not target_owner:
            raise HTTPException(status_code=422, detail="target_owner (dept_code) required for department scope")
        if not uma.registry.dept_skills_home:
            raise HTTPException(status_code=500, detail="DEPT_SKILLS_HOME not configured")
        dest_base = uma.registry.dept_skills_home / target_owner
    else:
        raise HTTPException(status_code=422, detail="target_scope must be 'department' or 'system'")

    dest_path = dest_base / short_name
    if dest_path.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{short_name}' already exists in {target_scope} scope")

    try:
        dest_base.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_path, dest_path)
        uma.registry.scan_skills()
        invalidate_prompt_cache()
        sync_res = sync_skills_git(f"Promoted skill {short_name} to {target_scope}")
        return {"status": "success", "skill_name": short_name, "target_scope": target_scope, "path": str(dest_path), "git_sync": sync_res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Workflow Dashboard API ──────────────────────────────────────────────────

@router.get("/skills/workflow/stats")
async def workflow_stats():
    """Return token analytics for workflow dashboard (by_skill + daily)."""
    import json, os
    # Resolve from project root (same as main.py CWD)
    project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parents[2]))
    summary_path = project_root / "workspace" / "analytics" / "token_summary.json"
    if not summary_path.exists():
        return {"by_skill": {}, "daily": {}, "total": {}, "_debug": str(summary_path)}
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        return {
            "by_skill": data.get("by_skill", {}),
            "daily": data.get("daily", {}),
            "total": data.get("total", {}),
        }
    except Exception as e:
        return {"by_skill": {}, "daily": {}, "total": {}, "_error": str(e)}

