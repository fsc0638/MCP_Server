"""Workflow CRUD + Execution routes."""

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["Workflow"])
logger = logging.getLogger("MCP_Server.Workflow")


def _workflows_base() -> Path:
    pr = os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))
    return Path(pr) / "workspace" / "workflows"


def _workflows_dir(scope: str = "personal", owner: str = "default") -> Path:
    """Resolve workflow directory based on scope: system / department / personal."""
    base = _workflows_base()
    if scope == "system":
        d = base / "system"
    elif scope == "department":
        d = base / "department" / (owner or "default")
    else:
        d = base / "personal" / (owner or "default")
    d.mkdir(parents=True, exist_ok=True)
    return d


class WorkflowSaveRequest(BaseModel):
    name: str = "Untitled Flow"
    description: str = ""
    icon: str = ""
    tags: list = []
    trigger_keywords: list = []
    # variables accepts BOTH the legacy list format and the v2 dict format
    # (migrate_legacy normalizes to dict before persisting). Union is used
    # because Pydantic v1 rejects dict body when type is declared as list.
    variables: Any = []
    blocks: list = []
    connections: list = []
    trigger: dict = {}
    execution: dict = {}
    security: dict = {}
    context: dict = {}
    scope: str = "personal"
    owner: str = ""


class WorkflowDeleteRequest(BaseModel):
    reason: str = ""
    user_name: str = ""
    user_id: str = ""


# ── Git Sync (main repo — workspace/workflows) ───────────────────────────────

def _get_project_root() -> Path:
    return Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))


def _sync_workflow_git(
    message: str,
    user_name: str = "",
    user_id: str = "",
    changed_paths: list = None,
) -> dict:
    """Commit & push workflow changes to the main project repo (not Agent_skills submodule)."""
    git_root = _get_project_root()
    if not (git_root / ".git").exists():
        logger.warning("[Workflow Git] Skipped: not a git repository")
        return {"status": "skipped", "message": "Not a git repository"}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    actor = f"{user_name} ({user_id})" if (user_name or user_id) else "system"
    commit_msg = f"[Workflow] {message}\n\nUser: {actor} | {now}"

    _enc = {"text": True, "encoding": "utf-8", "errors": "replace"}
    # Stage only system/department/personal dirs — NOT logs/ or versions/.
    # These directories are un-ignored via .gitignore negation (`!workspace/workflows/**`
    # after `workspace/*`), so regular git add works without -f.
    wf_base = git_root / "workspace" / "workflows"
    _scope_dirs = [
        str(wf_base / "system"),
        str(wf_base / "department"),
        str(wf_base / "personal"),
    ]
    try:
        # Stage new/modified files in scope directories
        for d in _scope_dirs:
            subprocess.run(["git", "add", d], cwd=git_root, capture_output=True, **_enc)
        # Stage deletions of tracked files across all scope dirs
        for d in _scope_dirs:
            subprocess.run(["git", "add", "-u", d], cwd=git_root, capture_output=True, **_enc)

        proc = subprocess.run(["git", "commit", "-m", commit_msg], cwd=git_root, capture_output=True, **_enc)
        combined_out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 and "nothing to commit" not in combined_out.lower():
            logger.error(f"[Workflow Git] Commit failed: {proc.stderr or proc.stdout}")
            return {"status": "error", "error": f"Commit failed: {proc.stderr or proc.stdout}"}

        # Push to current tracking branch
        push_proc = subprocess.run(
            ["git", "push"], cwd=git_root, capture_output=True, **_enc
        )
        if push_proc.returncode != 0:
            logger.warning(f"[Workflow Git] Push warning: {push_proc.stderr}")
            return {"status": "committed", "message": "Committed but push failed", "push_error": push_proc.stderr}

        logger.info(f"[Workflow Git] OK: {message} (by {actor})")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"[Workflow Git] Exception: {e}")
        return {"status": "error", "error": str(e)}


# ── CRUD ────────────────────────────────────────────────────────────────────

@router.get("/api/workflows")
def list_workflows(scope: str = "", owner: str = "", dept_code: str = ""):
    """List workflows. If scope is empty, list across all accessible scopes.

    Args:
        scope:     When set, list only that scope.
        owner:     User's employee_id — used as personal workflow directory key.
        dept_code: User's department code — used as department workflow directory key.
                   Kept separate from 'owner' because the two directories use different keys.
    """
    workflows = []

    def _scan_dir(directory: Path, wf_scope: str):
        if not directory.exists():
            return
        for f in sorted(directory.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                _ctx = data.get("context", {})
                workflows.append({
                    "id": f.stem,
                    "name": data.get("name", f.stem),
                    "description": data.get("description", ""),
                    "icon": data.get("icon", ""),
                    "tags": data.get("tags", []),
                    "trigger_keywords": data.get("trigger_keywords", []),
                    "workflow_key": _ctx.get("workflow_key", ""),
                    "block_count": len(data.get("blocks", [])),
                    "connection_count": len(data.get("connections", [])),
                    "variables_count": len(data.get("variables", [])),
                    "has_trigger": bool(data.get("trigger", {}).get("enabled")),
                    "updated_at": data.get("updated_at", ""),
                    "scope": wf_scope,
                })
            except Exception:
                pass

    base = _workflows_base()

    if scope:
        # List a specific scope — pick the right directory key
        if scope == "department":
            dir_key = dept_code or owner  # prefer explicit dept_code
        else:
            dir_key = owner
        _scan_dir(_workflows_dir(scope, dir_key), scope)
    else:
        # List all scopes: system + department (by dept_code) + personal (by owner)
        _scan_dir(base / "system", "system")
        if dept_code:
            _scan_dir(base / "department" / dept_code, "department")
        if owner:
            _scan_dir(base / "personal" / owner, "personal")
        if not owner and not dept_code:
            # Legacy: scan flat root for backward compatibility
            _scan_dir(base, "personal")

    return {"total": len(workflows), "workflows": workflows}


@router.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: str, scope: str = "personal", owner: str = "default"):
    """Get a specific workflow."""
    path = _workflows_dir(scope, owner) / f"{workflow_id}.json"
    if not path.exists():
        # Fallback: try legacy flat path
        legacy = _workflows_base() / f"{workflow_id}.json"
        if legacy.exists():
            path = legacy
        else:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["scope"] = scope
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/workflows/{workflow_id}")
def save_workflow(
    workflow_id: str,
    req: WorkflowSaveRequest,
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    """Create or update a workflow.

    Permission enforcement:
      - System scope: admin only
      - Department scope: member of that dept only
      - Personal scope: owner only
      - Guest: max 3 workflows total
    """
    from server.services.permissions import (
        check_scope_write_permission,
        enforce_guest_workflow_quota,
        resolve_caller_context,
    )

    caller_ctx = resolve_caller_context(mcp_session)
    check_scope_write_permission(req.scope, req.owner, caller_ctx, resource_kind="工作流")

    path = _workflows_dir(req.scope, req.owner) / f"{workflow_id}.json"
    # Quota only applies to NEW workflow creation (not updates of existing)
    if not path.exists():
        enforce_guest_workflow_quota(caller_ctx, creating_new=True)

    data = {
        "id": workflow_id,
        "name": req.name,
        "description": req.description,
        "icon": req.icon,
        "tags": req.tags,
        "trigger_keywords": req.trigger_keywords,
        "variables": req.variables,
        "blocks": req.blocks,
        "connections": req.connections,
        "trigger": req.trigger,
        "execution": req.execution,
        "security": req.security,
        "context": req.context,
        "scope": req.scope,
        "owner": req.owner,
        "updated_at": datetime.now().isoformat(),
    }

    # Phase 1.5: Upgrade incoming data to v2 format before persistence.
    # This lets both the legacy block-editor UI (sends old format) and any
    # future v2-native callers (wizard / LLM generator) share the same store.
    try:
        from server.services.workflow_schema import is_legacy, migrate_legacy
        if is_legacy(data):
            data = migrate_legacy(data)
            # Preserve fields the editor UI updated (name/desc may have changed)
            data["display_name"] = req.name or data.get("display_name", workflow_id)
            data["description"] = req.description or data.get("description", "")
            data["scope"] = req.scope
            data["owner"] = req.owner
    except Exception as _mig_err:
        logger.warning(f"[WF Save] Schema upgrade failed (non-fatal): {_mig_err}")

    # Track the creator for dept-scope quota counting
    _uid = (caller_ctx or {}).get("user_id", "")
    if _uid:
        data["created_by"] = _uid
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            data["created_at"] = old.get("created_at", data["updated_at"])
            # Preserve original creator if already set
            if old.get("created_by"):
                data["created_by"] = old["created_by"]
        except Exception:
            data["created_at"] = data["updated_at"]
    else:
        data["created_at"] = data["updated_at"]

    # Save version snapshot before overwrite (for version management)
    if path.exists():
        try:
            ver_dir = _workflows_base() / "versions" / workflow_id
            ver_dir.mkdir(parents=True, exist_ok=True)
            ver_name = datetime.now().strftime("v%Y%m%d_%H%M%S")
            ver_data = json.loads(path.read_text(encoding="utf-8"))
            ver_data["saved_at"] = datetime.now().isoformat()
            (ver_dir / f"{ver_name}.json").write_text(
                json.dumps(ver_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Keep only last 20 versions
            ver_files = sorted(ver_dir.glob("*.json"), reverse=True)
            for old_ver in ver_files[20:]:
                old_ver.unlink()
        except Exception as ver_err:
            logger.debug(f"[Workflow] Version snapshot failed: {ver_err}")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[Workflow] Saved: {workflow_id} (scope={req.scope}, {len(req.blocks)} blocks)")
    return {"status": "success", "id": workflow_id, "scope": req.scope, "updated_at": data["updated_at"]}


@router.delete("/api/workflows/{workflow_id}")
def delete_workflow(
    workflow_id: str,
    scope: str = "personal",
    owner: str = "default",
    req: WorkflowDeleteRequest = None,
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    """Delete a workflow and commit the deletion to git with reason + actor info.

    Permission: only the owner (or admin) may delete. System scope needs admin.
    """
    from server.services.permissions import (
        check_scope_write_permission,
        resolve_caller_context,
    )

    if req is None:
        req = WorkflowDeleteRequest()

    caller_ctx = resolve_caller_context(mcp_session)
    check_scope_write_permission(scope, owner, caller_ctx, resource_kind="工作流")

    path = _workflows_dir(scope, owner) / f"{workflow_id}.json"
    if not path.exists():
        legacy = _workflows_base() / f"{workflow_id}.json"
        if legacy.exists():
            path = legacy
        else:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    # Read display name before deletion (for commit message)
    try:
        _meta = json.loads(path.read_text(encoding="utf-8"))
        wf_display_name = _meta.get("name", workflow_id)
    except Exception:
        wf_display_name = workflow_id

    path.unlink()
    logger.info(f"[Workflow] Deleted: {workflow_id} (scope={scope}, by={req.user_name})")

    # Git commit + push
    reason_part = f" | Reason: {req.reason}" if req.reason.strip() else ""
    git_res = _sync_workflow_git(
        f"Deleted workflow '{wf_display_name}' (id={workflow_id}, scope={scope}){reason_part}",
        user_name=req.user_name,
        user_id=req.user_id,
        changed_paths=[path.parent],
    )
    return {"status": "success", "id": workflow_id, "git_sync": git_res}


@router.post("/api/workflows/{workflow_id}/clone")
def clone_workflow(workflow_id: str, scope: str = "system", owner: str = "", target_scope: str = "personal", target_owner: str = ""):
    """Clone a workflow from one scope to another (e.g. system template → personal copy)."""
    src_path = _workflows_dir(scope, owner) / f"{workflow_id}.json"
    if not src_path.exists():
        legacy = _workflows_base() / f"{workflow_id}.json"
        if legacy.exists():
            src_path = legacy
        else:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    data = json.loads(src_path.read_text(encoding="utf-8"))
    new_id = f"{workflow_id}-copy-{datetime.now().strftime('%H%M%S')}"
    data["id"] = new_id
    data["name"] = data.get("name", workflow_id) + " (副本)"
    data["scope"] = target_scope
    data["owner"] = target_owner
    data["created_at"] = datetime.now().isoformat()
    data["updated_at"] = data["created_at"]

    dest_dir = _workflows_dir(target_scope, target_owner)
    dest_path = dest_dir / f"{new_id}.json"
    dest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[Workflow] Cloned: {workflow_id} → {new_id} (scope={target_scope})")
    return {"status": "success", "id": new_id, "scope": target_scope, "path": str(dest_path)}


# ── Matching ──────────────────────────────────────────────────────────────

class WorkflowMatchRequest(BaseModel):
    user_input: str
    owner: str = ""
    dept_code: str = ""


@router.post("/api/workflows/match")
def match_workflow(req: WorkflowMatchRequest):
    """Test workflow matching against user input."""
    try:
        from server.services.workflow_matcher import get_workflow_matcher
        matcher = get_workflow_matcher()
        user_ctx = {}
        if req.owner:
            user_ctx["employee_id"] = req.owner
        if req.dept_code:
            user_ctx["department_code"] = req.dept_code
        result = matcher.match(req.user_input, user_context=user_ctx if user_ctx else None)
        if result:
            return {
                "matched": True,
                "workflow_id": result["workflow_id"],
                "workflow_name": result["workflow"].get("name", ""),
                "score": result["score"],
                "method": result["method"],
                "trigger_mode": result["workflow"].get("trigger_mode", "auto"),
            }
        return {"matched": False}
    except Exception as e:
        logger.error(f"[Workflow] Match failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Audit Logs ─────────────────────────────────────────────────────────────

@router.get("/api/workflows/{workflow_id}/logs")
def get_workflow_logs_api(workflow_id: str, limit: int = 50):
    """Get execution audit logs for a workflow."""
    try:
        from server.services.workflow_audit import get_workflow_logs
        logs = get_workflow_logs(workflow_id, limit=limit)
        return {"workflow_id": workflow_id, "total": len(logs), "logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/workflows/logs/recent")
def get_recent_logs_api(limit: int = 100):
    """Get recent execution logs across all workflows."""
    try:
        from server.services.workflow_audit import get_all_recent_logs
        logs = get_all_recent_logs(limit=limit)
        return {"total": len(logs), "logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Execution ───────────────────────────────────────────────────────────────

class WorkflowExecuteRequest(BaseModel):
    model: Optional[str] = None  # User-specified model override for entire flow
    initial_prompt: Optional[str] = ""  # User's intent for this execution


@router.post("/api/workflows/{workflow_id}/execute")
async def execute_workflow(workflow_id: str, req: WorkflowExecuteRequest = None, scope: str = "personal", owner: str = "default"):
    """Execute a workflow using the WorkflowExecutor."""
    if req is None:
        req = WorkflowExecuteRequest()

    # Try to find workflow in specified scope, fallback to all scopes
    path = _workflows_dir(scope, owner) / f"{workflow_id}.json"
    if not path.exists():
        # Try legacy flat path
        legacy = _workflows_base() / f"{workflow_id}.json"
        if legacy.exists():
            path = legacy
        else:
            # Search all scopes
            found = False
            for s in ["system", "department", "personal"]:
                for p in (_workflows_base() / s).rglob(f"{workflow_id}.json"):
                    path = p
                    found = True
                    break
                if found:
                    break
            if not found:
                raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    try:
        flow = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load workflow: {e}")

    try:
        from server.services.workflow_executor import get_workflow_executor
        executor = get_workflow_executor()
        result = await executor.execute(
            workflow=flow,
            user_input=req.initial_prompt or "",
            model_override=req.model,
        )
        return result
    except Exception as e:
        logger.error(f"[Workflow] Execution failed: {e}")
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {str(e)}")


# ── Webhook Trigger ────────────────────────────────────────────────────────

class WorkflowTriggerRequest(BaseModel):
    input: str = ""
    variables: dict = {}


@router.post("/api/workflows/{workflow_id}/trigger")
async def trigger_workflow(workflow_id: str, req: WorkflowTriggerRequest = None):
    """External webhook trigger — allows third-party systems to execute a workflow."""
    if req is None:
        req = WorkflowTriggerRequest()

    # Search all scopes
    path = None
    for scope_name in ["system", "department", "personal"]:
        scope_dir = _workflows_base() / scope_name
        if scope_dir.exists():
            for p in scope_dir.rglob(f"{workflow_id}.json"):
                path = p
                break
        if path:
            break
    if not path:
        legacy = _workflows_base() / f"{workflow_id}.json"
        if legacy.exists():
            path = legacy
    if not path:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    try:
        flow = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load workflow: {e}")

    try:
        from server.services.workflow_executor import get_workflow_executor
        executor = get_workflow_executor()
        result = await executor.execute(
            workflow=flow,
            user_input=req.input or "",
            user_context={"session_id": "webhook"},
        )
        # Audit log
        try:
            from server.services.workflow_audit import log_workflow_execution
            log_workflow_execution(
                workflow_id, flow.get("name", workflow_id),
                "webhook", result, trigger="webhook", user_input=req.input
            )
        except Exception:
            pass
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Webhook execution failed: {str(e)}")


# ── Import / Export ────────────────────────────────────────────────────────

@router.get("/api/workflows/{workflow_id}/export")
def export_workflow(workflow_id: str, scope: str = "personal", owner: str = "default"):
    """Export a workflow as JSON (for download)."""
    path = _workflows_dir(scope, owner) / f"{workflow_id}.json"
    if not path.exists():
        legacy = _workflows_base() / f"{workflow_id}.json"
        if legacy.exists():
            path = legacy
        else:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(path),
        media_type="application/json",
        filename=f"{workflow_id}.json",
        headers={"Content-Disposition": f'attachment; filename="{workflow_id}.json"'},
    )


class WorkflowImportRequest(BaseModel):
    data: dict
    scope: str = "personal"
    owner: str = ""


@router.post("/api/workflows/import")
def import_workflow(req: WorkflowImportRequest):
    """Import a workflow from JSON data."""
    data = req.data
    if not data or not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid workflow data")

    # Generate a new ID to avoid conflicts
    wf_id = data.get("id", f"wf-import-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    # Ensure unique ID
    target_dir = _workflows_dir(req.scope, req.owner)
    if (target_dir / f"{wf_id}.json").exists():
        wf_id = f"{wf_id}-{datetime.now().strftime('%H%M%S')}"

    data["id"] = wf_id
    data["scope"] = req.scope
    data["owner"] = req.owner
    data["imported_at"] = datetime.now().isoformat()
    data["updated_at"] = datetime.now().isoformat()

    path = target_dir / f"{wf_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[Workflow] Imported: {wf_id} (scope={req.scope})")
    return {"status": "success", "id": wf_id, "scope": req.scope}


# ── Version Management ─────────────────────────────────────────────────────

@router.get("/api/workflows/{workflow_id}/versions")
def list_workflow_versions(workflow_id: str, scope: str = "personal", owner: str = "default"):
    """List saved versions of a workflow."""
    versions_dir = _workflows_base() / "versions" / workflow_id
    if not versions_dir.exists():
        return {"workflow_id": workflow_id, "versions": []}

    versions = []
    for f in sorted(versions_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            versions.append({
                "version": f.stem,
                "saved_at": data.get("saved_at", ""),
                "block_count": len(data.get("blocks", [])),
                "connection_count": len(data.get("connections", [])),
            })
        except Exception:
            pass
    return {"workflow_id": workflow_id, "versions": versions[:20]}


@router.post("/api/workflows/{workflow_id}/versions/{version}/restore")
def restore_workflow_version(workflow_id: str, version: str, scope: str = "personal", owner: str = "default"):
    """Restore a workflow to a specific version."""
    version_path = _workflows_base() / "versions" / workflow_id / f"{version}.json"
    if not version_path.exists():
        raise HTTPException(status_code=404, detail=f"Version '{version}' not found")

    target_path = _workflows_dir(scope, owner) / f"{workflow_id}.json"
    data = json.loads(version_path.read_text(encoding="utf-8"))
    data["restored_from"] = version
    data["updated_at"] = datetime.now().isoformat()
    target_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[Workflow] Restored: {workflow_id} to version {version}")
    return {"status": "success", "id": workflow_id, "restored_from": version}
