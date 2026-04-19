"""Skill management routes."""

import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

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


def _validate_skill_path(skill_path: Path):
    """Ensure skill_path is within one of the three-tier skill directories (security check)."""
    uma = get_uma()
    allowed_roots = [uma.registry.skills_home.resolve()]
    if uma.registry.dept_skills_home:
        allowed_roots.append(uma.registry.dept_skills_home.resolve())
    if uma.registry.personal_skills_home:
        allowed_roots.append(uma.registry.personal_skills_home.resolve())
    resolved = skill_path.resolve()
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return  # OK
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail="Path traversal denied: skill path is outside allowed directories")


def _get_git_root() -> Path:
    """Find the Agent_skills git repo root."""
    uma = get_uma()
    git_root = uma.registry.skills_home.resolve()
    if not (git_root / ".git").exists() and (git_root.parent / ".git").exists():
        git_root = git_root.parent
    return git_root


def _extract_user(request: Request) -> tuple:
    """Extract (user_name, user_id) from the request cookie session."""
    try:
        from server.routes.auth import verify_token
        from server.services.auth_session_store import get_auth_session_store
        token = request.cookies.get("mcp_session")
        if token:
            verified = verify_token(token)
            if verified:
                store = get_auth_session_store()
                session = store.get(verified)
                if session:
                    return (getattr(session, "name", "") or "", getattr(session, "user_id", "") or "")
    except Exception:
        pass
    return ("", "")


def sync_skills_git(message: str, user_name: str = "", user_id: str = "", changed_paths: list = None):
    """
    Synchronize the Agent_skills local repository with the remote.
    - Stages only specified paths (or all if changed_paths is None)
    - Commits with user info and timestamp
    - Pushes to origin main
    - Personal skills are NOT pushed to Git (user-managed locally)
    """
    # Skip git for personal skills — they are not tracked in the repo
    if changed_paths:
        uma = get_uma()
        _personal_root = uma.registry.personal_skills_home
        if _personal_root:
            _pr = str(_personal_root.resolve())
            _all_personal = all(str(Path(p).resolve()).startswith(_pr) for p in changed_paths)
            if _all_personal:
                logger.info(f"[Git] Skipped (personal skill): {message}")
                return {"status": "skipped", "message": "Personal skills are not pushed to Git"}

    git_root = _get_git_root()
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
        _enc = {"text": True, "encoding": "utf-8", "errors": "replace"}

        # 1. git add — only changed paths, not everything
        if changed_paths:
            for p in changed_paths:
                rel = str(Path(p).relative_to(git_root)) if Path(p).is_absolute() else str(p)
                subprocess.run(["git", "add", rel], cwd=git_root, check=True, capture_output=True, **_enc)
            # Also stage deletions
            subprocess.run(["git", "add", "-u"], cwd=git_root, capture_output=True, **_enc)
        else:
            subprocess.run(["git", "add", "."], cwd=git_root, check=True, capture_output=True, **_enc)

        # 2. git commit (allow failure if no changes)
        proc = subprocess.run(["git", "commit", "-m", commit_msg], cwd=git_root, capture_output=True, **_enc)
        if proc.returncode != 0 and "nothing to commit" not in proc.stdout.lower():
             logger.error(f"Git commit failed: {proc.stderr}")
             return {"status": "error", "error": f"Commit failed: {proc.stderr}"}

        # 3. git push
        subprocess.run(["git", "push", "origin", "main"], cwd=git_root, check=True, capture_output=True, **_enc)
        logger.info(f"Git sync successful for Agent_skills: {message} (by {user_name or 'system'})")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Git sync exception: {e}")
        return {"status": "error", "error": str(e)}


@router.get("/skills/list")
def list_skills(request: Request, dept: str = "", uid: str = ""):
    uma = get_uma()

    # Load user context for department filtering
    _user_ctx = None
    try:
        _un, _uid = _extract_user(request)
        if _uid:
            import json as _json
            # Try workspace/users/ files (LINE users)
            for _prefix in [f"line_{_uid}", _uid]:
                _uc_path = Path(os.getenv("PROJECT_ROOT", ".")) / "workspace" / "users" / f"{_prefix}.json"
                if _uc_path.exists():
                    _user_ctx = _json.loads(_uc_path.read_text(encoding="utf-8"))
                    break
            # Fallback: lookup from employee list (Web login users)
            if not _user_ctx:
                try:
                    from server.services.employee_lookup import lookup, build_user_context
                    _emp = lookup(_un) or lookup(_uid)
                    if _emp:
                        _user_ctx = build_user_context(_uid, _emp)
                except Exception:
                    pass
    except Exception:
        pass

    # Fallback: use query params from frontend (for password-login users without server session)
    if not _user_ctx and (dept or uid):
        _user_ctx = {"department_code": dept, "user_id": uid, "employee_id": uid}

    skills: Dict[str, Dict[str, Any]] = {}
    for name, data in uma.registry.skills.items():
        meta = data["metadata"]
        scope = meta.get("_scope", "system")

        # Filter: no user context → only system skills visible (safe default)
        if scope.startswith("dept:"):
            if not _user_ctx:
                continue
            dept_code = scope.split(":")[1]
            if dept_code != _user_ctx.get("department_code", ""):
                continue
        elif scope.startswith("user:"):
            if not _user_ctx:
                continue
            owner_id = scope.split(":")[1]
            if owner_id != _user_ctx.get("user_id", "") and owner_id != _user_ctx.get("employee_id", ""):
                continue

        # Determine edit permission:
        # System → only admin | Department → same dept members | Personal → owner only | Guest → none
        _editable = False
        _role = _user_ctx.get("role", "") if _user_ctx else ""
        if scope == "system":
            _editable = (_role == "admin")
        elif scope.startswith("dept:"):
            _editable = bool(_user_ctx)  # same dept (already filtered above)
        elif scope.startswith("user:"):
            _editable = bool(_user_ctx)  # owner (already filtered above)
        # Guest (no _user_ctx) → _editable stays False

        skills[name] = {
            "description": meta.get("description", ""),
            "display_name": meta.get("display_name", ""),
            "version": meta.get("version", "unknown"),
            "ready": meta.get("_env_ready", False),
            "missing_deps": meta.get("_missing_deps", []),
            "path": str(data["path"]),
            "scope": scope,
            "short_name": meta.get("_short_name", name),
            "editable": _editable,
        }
    _is_guest = _user_ctx is None
    return {"total": len(skills), "skills": skills, "guest": _is_guest}


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
    _validate_skill_path(skill_path)

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
            changed_paths=[skill_path],
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
def delete_skill(skill_name: str, req: SkillDeleteRequest, request: Request):
    from server.core.retriever import retriever
    from server.services.permissions import (
        check_scope_write_permission,
        resolve_caller_context,
    )

    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    skill_path = skill["path"].resolve()
    _validate_skill_path(skill_path)

    # ── Permission enforcement ──
    # Derive (scope, owner) from the skill's metadata _scope field:
    #   "system"           → system
    #   "dept:Y200"        → department, owner=Y200
    #   "user:U09abc..."   → personal, owner=U09abc...
    _meta_scope = skill["metadata"].get("_scope", "system")
    if _meta_scope.startswith("dept:"):
        _scope, _owner = "department", _meta_scope.split(":", 1)[1]
    elif _meta_scope.startswith("user:"):
        _scope, _owner = "personal", _meta_scope.split(":", 1)[1]
    else:
        _scope, _owner = "system", ""

    _mcp_cookie = request.cookies.get("mcp_session", "")
    _caller_ctx = resolve_caller_context(_mcp_cookie)
    check_scope_write_permission(_scope, _owner, _caller_ctx, resource_kind="Agent Skill")

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

        _parent = skill_path.parent
        shutil.rmtree(skill_path, onerror=remove_readonly)
        uma.registry.skills.pop(skill_name.lower(), None)
        invalidate_prompt_cache()
        sync_res = sync_skills_git(
            f"Deleted skill {skill_name} | Reason: {req.reason}",
            user_name=req.user_name,
            user_id=req.user_id,
            changed_paths=[_parent],
        )
        return {"status": "success", "message": f"Skill '{skill_name}' deleted.", "git_sync": sync_res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_name}/rename")
def rename_skill(skill_name: str, body: dict, request: Request):
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
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    old_path = skill["path"].resolve()
    # Rename within same parent directory (same scope)
    new_path = (old_path.parent / new_name).resolve()
    if new_path.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{new_name}' already exists")

    try:
        old_path.rename(new_path)
        # Update name in SKILL.md
        skill_md_path = new_path / "SKILL.md"
        if skill_md_path.exists():
            content = skill_md_path.read_text(encoding="utf-8")
            content = content.replace(f"name: {skill_name}", f"name: {new_name}", 1)
            skill_md_path.write_text(content, encoding="utf-8")
        # Git sync
        _un, _uid = _extract_user(request)
        sync_res = sync_skills_git(f"Renamed skill: {skill_name} → {new_name}", user_name=_un, user_id=_uid, changed_paths=[new_path])
        # Re-register
        uma.registry.scan_skills()
        invalidate_prompt_cache()
        return {"status": "success", "old_name": skill_name, "new_name": new_name, "git_sync": sync_res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_name}/rollback")
def rollback_skill(skill_name: str, request: Request):
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    skill_path = skill["path"].resolve()
    bak_path = skill_path / "SKILL.md.bak"
    skill_md_path = skill_path / "SKILL.md"
    if not bak_path.exists():
        raise HTTPException(status_code=404, detail="Backup SKILL.md.bak not found")
    try:
        shutil.copy2(bak_path, skill_md_path)
        uma.registry._register_skill(skill_path)
        invalidate_prompt_cache()
        _un, _uid = _extract_user(request)
        sync_res = sync_skills_git(f"Rolled back skill {skill_name}", user_name=_un, user_id=_uid, changed_paths=[skill_path])
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
async def upload_skill_file(skill_name: str, request: Request, file: UploadFile = File(...), file_type: str = Form(...)):
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    valid_types = {"script": "scripts", "asset": "assets", "knowledge": "references"}
    if file_type not in valid_types:
        raise HTTPException(status_code=400, detail="file_type must be 'script', 'asset', or 'knowledge'")

    skill_path = skill["path"].resolve()
    _validate_skill_path(skill_path)

    target_dir = skill_path / valid_types[file_type]
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(file.filename or "uploaded_file")
    dest_path = target_dir / safe_name
    try:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        _un, _uid = _extract_user(request)
        sync_res = sync_skills_git(f"Uploaded {file_type} to {skill_name}: {safe_name}", user_name=_un, user_id=_uid, changed_paths=[dest_path])
        return {"status": "success", "filename": safe_name, "path": str(dest_path), "git_sync": sync_res}
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
async def delete_skill_file(skill_name: str, folder: str, filename: str, request: Request):
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
        _un, _uid = _extract_user(request)
        sync_res = sync_skills_git(f"Deleted {folder} file from {skill_name}: {safe_name}", user_name=_un, user_id=_uid, changed_paths=[skill_path / folder])
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
def create_skill(req: CreateSkillRequest, request: Request):
    uma = get_uma()

    # ── Permission enforcement ──
    # - system scope: admin only
    # - department scope: member of that dept (or admin)
    # - personal scope: owner (or admin)
    # - guest users: max 10 skills total
    from server.services.permissions import (
        check_scope_write_permission,
        enforce_guest_skill_quota,
        resolve_caller_context,
    )
    _mcp_cookie = request.cookies.get("mcp_session", "")
    _caller_ctx = resolve_caller_context(_mcp_cookie)
    check_scope_write_permission(
        req.scope or "system",
        req.owner or "",
        _caller_ctx,
        resource_kind="Agent Skill",
    )
    enforce_guest_skill_quota(_caller_ctx, creating_new=True)

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
        base_dir = uma.registry.personal_skills_home
        if not base_dir:
            raise HTTPException(status_code=500, detail="PERSONAL_SKILLS_HOME not configured")
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
        _un, _uid = _extract_user(request)
        sync_res = sync_skills_git(f"Created {scope} skill {name}", user_name=_un, user_id=_uid, changed_paths=[skill_path])
        return {"status": "success", "skill_name": name, "scope": scope, "path": str(skill_path), "git_sync": sync_res}
    except Exception as e:
        if skill_path.exists():
            shutil.rmtree(skill_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Skill Promote (scope migration) ────────────────────────────────────────

@router.post("/skills/{skill_name}/move")
def move_skill(skill_name: str, request: Request, target_scope: str = "system", target_owner: str = ""):
    """
    Move a skill to a different scope (system / department / personal).
    Moves the entire skill directory from current location to the target.
    """
    uma = get_uma()
    skill = uma.registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    src_path = skill["path"].resolve()
    short_name = skill["metadata"].get("_short_name", skill_name)
    old_scope = skill["metadata"].get("_scope", "system")

    # Resolve target directory
    if target_scope == "system":
        dest_base = uma.registry.skills_home
    elif target_scope == "department":
        if not target_owner:
            raise HTTPException(status_code=422, detail="target_owner (dept_code) required for department scope")
        if not uma.registry.dept_skills_home:
            raise HTTPException(status_code=500, detail="DEPT_SKILLS_HOME not configured")
        dest_base = uma.registry.dept_skills_home / target_owner
    elif target_scope == "personal":
        if not target_owner:
            raise HTTPException(status_code=422, detail="target_owner (user_id) required for personal scope")
        if not uma.registry.personal_skills_home:
            raise HTTPException(status_code=500, detail="PERSONAL_SKILLS_HOME not configured")
        dest_base = uma.registry.personal_skills_home / target_owner
    else:
        raise HTTPException(status_code=422, detail="target_scope must be 'system', 'department', or 'personal'")

    dest_path = dest_base / short_name
    if dest_path.resolve() == src_path:
        return {"status": "success", "message": "Already in target scope"}

    if dest_path.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{short_name}' already exists in {target_scope} scope")

    try:
        dest_base.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dest_path))
        # Remove old registry entry and rescan
        uma.registry.skills = {k: v for k, v in uma.registry.skills.items() if v["path"].resolve() != src_path}
        uma.registry.scan_skills()
        invalidate_prompt_cache()
        _un, _uid = _extract_user(request)
        sync_res = sync_skills_git(
            f"Moved skill {short_name}: {old_scope} → {target_scope}",
            user_name=_un, user_id=_uid,
        )
        return {"status": "success", "skill_name": short_name, "target_scope": target_scope, "path": str(dest_path), "git_sync": sync_res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Workflow Dashboard API ──────────────────────────────────────────────────

@router.get("/skills/workflow/stats")
async def workflow_stats(live: bool = True):
    """Return token analytics. live=True reads JSONL directly for real-time data."""
    import json as _json
    project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parents[2]))

    if live:
        # Real-time: parse token_usage.jsonl directly
        usage_path = project_root / "workspace" / "analytics" / "token_usage.jsonl"
        if not usage_path.exists():
            return {"by_skill": {}, "daily": {}, "monthly": {}, "total": {}}
        try:
            total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "skill_calls": 0, "chat_calls": 0}
            by_skill = {}
            daily = {}
            monthly = {}

            for line in usage_path.read_text(encoding="utf-8", errors="replace").strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    r = _json.loads(line)
                except Exception:
                    continue

                inp = r.get("input_tokens", 0)
                out = r.get("output_tokens", 0)
                tot = r.get("total_tokens", 0)
                skill = r.get("skill", "")
                day = r.get("ts", "")[:10]
                month = day[:7] if day else ""
                is_chat = (skill == "(chat)")

                total["input_tokens"] += inp
                total["output_tokens"] += out
                total["total_tokens"] += tot
                if is_chat:
                    total["chat_calls"] += 1
                elif skill:
                    total["skill_calls"] += 1

                if skill:
                    if skill not in by_skill:
                        by_skill[skill] = {"calls": 0, "total_tokens": 0}
                    by_skill[skill]["calls"] += 1
                    by_skill[skill]["total_tokens"] += tot

                if day:
                    if day not in daily:
                        daily[day] = {"total_tokens": 0, "skill_calls": 0, "chat_calls": 0}
                    daily[day]["total_tokens"] += tot
                    if is_chat:
                        daily[day]["chat_calls"] += 1
                    elif skill:
                        daily[day]["skill_calls"] += 1

                if month:
                    if month not in monthly:
                        monthly[month] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "skill_calls": 0, "chat_calls": 0}
                    monthly[month]["input_tokens"] += inp
                    monthly[month]["output_tokens"] += out
                    monthly[month]["total_tokens"] += tot
                    if is_chat:
                        monthly[month]["chat_calls"] += 1
                    elif skill:
                        monthly[month]["skill_calls"] += 1

            return {"by_skill": by_skill, "daily": daily, "monthly": monthly, "total": total}
        except Exception as e:
            return {"by_skill": {}, "daily": {}, "monthly": {}, "total": {}, "_error": str(e)}

    # Fallback: read cached summary
    summary_path = project_root / "workspace" / "analytics" / "token_summary.json"
    if not summary_path.exists():
        return {"by_skill": {}, "daily": {}, "monthly": {}, "total": {}}
    try:
        data = _json.loads(summary_path.read_text(encoding="utf-8"))
        return {
            "by_skill": data.get("by_skill", {}),
            "daily": data.get("daily", {}),
            "monthly": data.get("monthly", {}),
            "total": data.get("total", {}),
        }
    except Exception as e:
        return {"by_skill": {}, "daily": {}, "monthly": {}, "total": {}, "_error": str(e)}

