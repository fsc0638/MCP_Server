"""Workflow CRUD + Execution routes."""

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

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

                # v2-aware field reads with legacy fallbacks
                _display = data.get("display_name") or data.get("name") or f.stem

                # variables: v2 dict {global_inputs, env_requirements, definitions}
                _vars = data.get("variables") or []
                if isinstance(_vars, dict):
                    _vars_count = len(_vars.get("definitions") or [])
                else:
                    _vars_count = len(_vars)

                # trigger keywords: v2 moves to trigger.patterns
                _trig = data.get("trigger") or {}
                _keywords = _trig.get("patterns") if isinstance(_trig, dict) else None
                if not _keywords:
                    _keywords = data.get("trigger_keywords") or []  # legacy fallback

                workflows.append({
                    "id": data.get("workflow_id") or f.stem,
                    "name": _display,
                    "display_name": _display,  # explicit v2 field
                    "workflow_id": data.get("workflow_id") or f.stem,
                    "description": data.get("description", ""),
                    "icon": data.get("icon", ""),
                    "tags": data.get("tags", []),
                    "trigger_keywords": _keywords,
                    "workflow_key": _ctx.get("workflow_key", ""),
                    "block_count": len(data.get("blocks", [])),
                    "connection_count": len(data.get("connections", [])),
                    "variables_count": _vars_count,
                    "has_trigger": bool(_trig.get("enabled")) if isinstance(_trig, dict) else False,
                    "updated_at": data.get("updated_at") or data.get("metadata", {}).get("updated_at", ""),
                    "source": data.get("source", ""),
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

    # The URL workflow_id is what the client uses to locate the file.
    # May be a legacy Chinese name (existing files) or a v2 slug (new writes).
    # We honor it as the filename for this save but the internal workflow_id
    # field inside the JSON is ALWAYS a v2 slug (derived by migrate_legacy).
    path = _workflows_dir(req.scope, req.owner) / f"{workflow_id}.json"
    is_new = not path.exists()

    if is_new:
        enforce_guest_workflow_quota(caller_ctx, creating_new=True)

    data = {
        "id": workflow_id,  # Retained temporarily for migrate_legacy to derive slug
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

    # Preserve pre-existing metadata (created_at, run_count, promoted_from, etc.)
    existing_data = {}
    if not is_new:
        try:
            existing_data = json.loads(path.read_text(encoding="utf-8"))
            if existing_data.get("metadata"):
                data.setdefault("metadata", {}).update(existing_data["metadata"])
            # Carry across scope/owner if file moved
            if existing_data.get("source"):
                data["source"] = existing_data["source"]
        except Exception:
            pass

    # Track creator (goes into metadata via migrate_legacy)
    _uid = (caller_ctx or {}).get("user_id", "")
    if _uid and not existing_data.get("metadata", {}).get("created_by"):
        data["created_by"] = _uid

    # Phase 1.1-1.7: Upgrade incoming data to v2 format before persistence.
    # - mark_as_source=user_defined (NOT legacy): this is a UI save, not the
    #   startup migration sweep
    # - resync_steps=True: always rebuild steps[] from current blocks so canvas
    #   edits stay in sync with the executable list
    try:
        from server.services.workflow_schema import migrate_legacy
        data = migrate_legacy(
            data,
            mark_as_source=existing_data.get("source") or "user_defined",
            resync_steps=True,
        )
        # Preserve fields the editor UI just updated (overriding any stale values)
        data["display_name"] = req.name or data.get("display_name", workflow_id)
        data["description"] = req.description or data.get("description", "")
        data["scope"] = req.scope
        data["owner"] = req.owner
    except Exception as _mig_err:
        logger.warning(f"[WF Save] Schema upgrade failed (non-fatal): {_mig_err}")

    # ── Phase 2 Gate 0: static validation (hard block) ──
    # Draft workflows (steps=[]) are exempt from skill_id checks but still
    # must pass schema validation. Any error blocks the save with HTTP 422.
    try:
        from server.services.workflow_gates import gate_0_validate
        from main import get_uma
        _uma = get_uma()
        ok0, errs0 = gate_0_validate(data, _uma)
        if not ok0:
            logger.warning(f"[WF Gate0] Rejected save of {workflow_id}: {errs0[:3]}")
            raise HTTPException(status_code=422, detail={
                "gate": 0,
                "errors": errs0,
                "message": "工作流無法儲存：檢查規則未通過",
            })
    except HTTPException:
        raise
    except Exception as _g0_err:
        # Never block save on Gate-0 internal errors (fail-open for safety);
        # user can retry or contact admin.
        logger.error(f"[WF Gate0] Internal error for {workflow_id}: {_g0_err}")

    # ── Phase 1.5: Align filename with internal workflow_id (slug) ──
    # If the URL path differs from the v2 slug, rename the file so the two
    # stay in sync (enables sub-workflow lookup by workflow_id).
    # This runs ONLY when:
    #   - migration produced a different slug than the URL path, AND
    #   - the new filename is safe (ASCII slug, no filesystem issues)
    final_slug = data.get("workflow_id", workflow_id)
    final_path = path
    renamed = False
    if final_slug and final_slug != workflow_id:
        # Guard: only switch if slug looks safe (pure slug pattern incl. hyphen)
        import re as _re
        if _re.match(r"^[a-z][a-z0-9_-]{2,63}$", final_slug):
            final_path = _workflows_dir(req.scope, req.owner) / f"{final_slug}.json"
            renamed = True

    # Version snapshot before overwrite (use the FINAL path's version dir)
    if not is_new:
        try:
            ver_dir = _workflows_base() / "versions" / final_slug
            ver_dir.mkdir(parents=True, exist_ok=True)
            ver_name = datetime.now().strftime("v%Y%m%d_%H%M%S")
            (ver_dir / f"{ver_name}.json").write_text(
                json.dumps(existing_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            ver_files = sorted(ver_dir.glob("*.json"), reverse=True)
            for old_ver in ver_files[20:]:
                old_ver.unlink()
        except Exception as ver_err:
            logger.debug(f"[Workflow] Version snapshot failed: {ver_err}")

    final_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Delete old file if we renamed
    if renamed and path.exists() and path != final_path:
        try:
            path.unlink()
            logger.info(f"[Workflow] Renamed: {workflow_id}.json → {final_slug}.json")
        except Exception as rm_err:
            logger.warning(f"[Workflow] Failed to remove old file {path}: {rm_err}")

    logger.info(
        f"[Workflow] Saved: {final_slug} "
        f"(scope={req.scope}, {len(req.blocks)} blocks → {len(data.get('steps') or [])} steps, "
        f"source={data.get('source')})"
    )
    return {
        "status": "success",
        "id": final_slug,
        "scope": req.scope,
        "updated_at": data["updated_at"],
        "renamed": renamed,
    }


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
    inputs: Dict[str, Any] = {}  # global_inputs supplied by user / wizard


@router.post("/api/workflows/{workflow_id}/execute")
async def execute_workflow(workflow_id: str, req: WorkflowExecuteRequest = None, scope: str = "personal", owner: str = "default"):
    """Execute a workflow using the WorkflowExecutor.

    Phase 2 Gate 1 (pre-execution check):
      - Environment variables must be satisfied → HTTP 422 (hard block)
      - All referenced skills must be env_ready → HTTP 422
      - Required global_inputs must be provided → HTTP 428 (wizard prompt)
      - Sub-workflow references must resolve → HTTP 422

    428 Precondition Required is used specifically for the "need user input"
    case so the frontend can distinguish "cannot run" from "need more info".
    """
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

    # ── Phase 2 Gate 1: pre-execution check ──
    try:
        from server.services.workflow_gates import gate_1_pre_execute
        from main import get_uma
        _uma = get_uma()
        ok1, info1 = gate_1_pre_execute(flow, user_inputs=req.inputs or {}, uma=_uma)
        if not ok1:
            if info1.get("errors"):
                # Hard block — server-side issue user can't fix directly
                logger.warning(f"[WF Gate1] {workflow_id} rejected: {info1['errors'][:3]}")
                raise HTTPException(status_code=422, detail={
                    "gate": 1,
                    "errors": info1["errors"],
                    "missing_inputs": info1.get("missing_inputs", []),
                    "message": "工作流尚無法執行，請先排除以下問題",
                })
            if info1.get("missing_inputs"):
                # Soft block — UI should show a wizard to collect
                logger.info(f"[WF Gate1] {workflow_id} needs inputs: {[i['name'] for i in info1['missing_inputs']]}")
                raise HTTPException(status_code=428, detail={
                    "gate": 1,
                    "missing_inputs": info1["missing_inputs"],
                    "message": "工作流需要以下輸入才能執行",
                })
    except HTTPException:
        raise
    except Exception as _g1_err:
        logger.error(f"[WF Gate1] Internal error: {_g1_err}")
        # Fail-open: let executor attempt the run; Gate 2 still protects each step

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


# ── Phase 6: One-shot LLM workflow promotion ───────────────────────────────

class PromoteRequest(BaseModel):
    run_id: str = ""            # The run_id from Gate 3's promotion_queue.json
    display_name: str = ""      # User-chosen name for the persisted workflow
    description: str = ""
    target_scope: str = "personal"
    target_owner: str = ""


@router.get("/api/workflows/oneshot/pending")
def list_promotion_candidates(
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    """List recent one-shot LLM-generated executions available for promotion.

    Returns only items the current user originated (by created_by).
    Phase 3 Gate 3 writes these to workspace/workflows/promotion_queue.json
    when a source='llm_generated' workflow executes successfully.
    """
    from server.services.permissions import resolve_caller_context
    caller_ctx = resolve_caller_context(mcp_session)
    caller_id = (caller_ctx or {}).get("user_id", "") or (caller_ctx or {}).get("employee_id", "")

    promo_path = _workflows_base() / "promotion_queue.json"
    if not promo_path.exists():
        return {"candidates": []}

    try:
        data = json.loads(promo_path.read_text(encoding="utf-8"))
    except Exception:
        return {"candidates": []}

    # Filter by caller (safety + relevance)
    mine = [c for c in data if c.get("user_id") == caller_id or not caller_id]
    # Newest first
    mine.sort(key=lambda c: c.get("at", 0), reverse=True)
    return {"candidates": mine[:20]}


@router.post("/api/workflows/promote")
def promote_oneshot(
    req: PromoteRequest,
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    """Save an LLM one-shot workflow into the user's scope as a persistent
    template. The one-shot's JSON is stored under workspace/workflows/oneshot/
    by WorkflowExecutor when source=llm_generated; we copy + relabel here.
    """
    from server.services.permissions import (
        resolve_caller_context, check_scope_write_permission,
    )
    caller_ctx = resolve_caller_context(mcp_session)

    # Resolve empty owner from caller context (frontend sends "" for convenience)
    target_owner = req.target_owner or ""
    if not target_owner:
        if req.target_scope == "personal":
            target_owner = (caller_ctx or {}).get("user_id", "") or (caller_ctx or {}).get("employee_id", "")
        elif req.target_scope == "department":
            target_owner = (caller_ctx or {}).get("department_code", "")

    check_scope_write_permission(req.target_scope, target_owner, caller_ctx, resource_kind="工作流")

    if not req.run_id or not req.display_name:
        raise HTTPException(status_code=400, detail="需要 run_id 與 display_name")

    # The one-shot source file is expected at workspace/workflows/oneshot/{run_id}.json
    src = _workflows_base() / "oneshot" / f"{req.run_id}.json"
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"One-shot 工作流 '{req.run_id}' 不存在")

    try:
        wf = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read source: {e}")

    # Relabel for persistence
    wf["display_name"] = req.display_name
    wf["description"] = req.description or wf.get("description", "")
    wf["scope"] = req.target_scope
    wf["owner"] = target_owner
    wf["source"] = "llm_generated"  # preserved for analytics
    meta = wf.get("metadata") or {}
    meta["promoted_from"] = req.run_id
    meta["promoted_at"] = datetime.now().isoformat()
    wf["metadata"] = meta

    # Migrate to full v2 shape + validate
    try:
        from server.services.workflow_schema import migrate_legacy, validate_workflow
        wf = migrate_legacy(wf, mark_as_source="llm_generated", resync_steps=True)
        ok, errs = validate_workflow(wf)
        if not ok:
            raise HTTPException(status_code=422, detail={"errors": errs[:5]})
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[Promote] Validation failed: {e}")

    dest_id = wf.get("workflow_id", f"promoted_{req.run_id}")
    dest_path = _workflows_dir(req.target_scope, target_owner) / f"{dest_id}.json"
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")

    # Remove from promotion_queue.json
    try:
        promo_path = _workflows_base() / "promotion_queue.json"
        if promo_path.exists():
            queue = json.loads(promo_path.read_text(encoding="utf-8"))
            queue = [c for c in queue if c.get("run_id") != req.run_id]
            promo_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    logger.info(f"[Promote] {req.run_id} → {dest_id} (scope={req.target_scope})")
    return {
        "status": "success",
        "workflow_id": dest_id,
        "scope": req.target_scope,
        "path": str(dest_path),
    }


@router.get("/api/workflows/oneshot/accessible-skills")
def list_accessible_skills(
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    """Return the skill whitelist available to the current user for LLM one-shot
    workflow generation. Only skills the caller can actually execute are listed,
    so the LLM generator won't propose skills the user can't run at execution time.

    Each entry: { skill_id, display_name, description, parameters, env_ready }.
    """
    from server.services.permissions import resolve_caller_context
    from server.dependencies.uma import get_uma_instance
    caller_ctx = resolve_caller_context(mcp_session)
    uma = get_uma_instance()

    out: List[Dict[str, Any]] = []
    try:
        registry = uma.skill_registry
        skills_dict = getattr(registry, "skills", {}) or {}
    except Exception as e:
        logger.warning(f"[AccessibleSkills] registry access failed: {e}")
        skills_dict = {}

    for registry_key, entry in skills_dict.items():
        try:
            meta = (entry or {}).get("metadata") or {}
            skill_id = meta.get("name") or registry_key
            # Permission gate — reuse registry's access check if present
            if hasattr(registry, "can_access"):
                try:
                    if not registry.can_access(skill_id, caller_ctx):
                        continue
                except Exception:
                    pass
            out.append({
                "skill_id": skill_id,
                "display_name": meta.get("display_name") or skill_id,
                "description": (meta.get("description") or "")[:400],
                "parameters": meta.get("parameters") or {},
                "env_ready": bool(meta.get("_env_ready", True)),
                "risk_level": meta.get("risk_level", "low"),
            })
        except Exception as _e:
            logger.debug(f"[AccessibleSkills] skip entry: {_e}")
            continue

    # Stable ordering
    out.sort(key=lambda x: x.get("skill_id", ""))
    return {"skills": out, "count": len(out)}


# ── Phase 5: 5-question Wizard ──────────────────────────────────────────────

class WizardRequest(BaseModel):
    purpose: str = ""          # Q1 — what are you doing? (新聞 / 資料整理 / 會議整理 / 備忘)
    input_source: str = ""     # Q2 — what's the input? (文字 / 檔案 / 網址 / 日曆 / LINE)
    output_target: str = ""    # Q3 — where should it go? (摘要 / Notion / LINE 推播 / Email)
    schedule: str = ""         # Q4 — when should it run? (手動 / 每日 / 每週 / 每月)
    on_fail: str = "retry"     # Q5 — what on failure? (retry / skip / notify)


@router.post("/api/workflows/wizard")
def workflow_wizard(req: WizardRequest):
    """Build a workflow JSON from 5 natural-language answers (no LLM).

    Deterministically picks the closest template based on the user's answers,
    then returns a fully-formed v2 workflow that can be reviewed and saved.
    """
    import re as _re
    templates_dir = _workflows_base() / "templates"
    if not templates_dir.exists():
        raise HTTPException(status_code=500, detail="模板庫尚未建立")

    candidates: List[Dict[str, Any]] = []
    for f in sorted(templates_dir.glob("*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            score = _score_template(t, req)
            candidates.append({"template": t, "score": score, "file": f.name})
        except Exception as e:
            logger.debug(f"[Wizard] skip {f.name}: {e}")

    if not candidates:
        raise HTTPException(status_code=500, detail="沒有可用的模板")

    candidates.sort(key=lambda c: c["score"], reverse=True)
    top = candidates[0]
    best_tpl = top["template"]

    # Merge wizard answers into workflow data
    wf_data = dict(best_tpl.get("workflow") or {})
    wf_data.setdefault("display_name", best_tpl.get("display_name", "新工作流"))
    wf_data.setdefault("description", best_tpl.get("description", ""))
    wf_data.setdefault("icon", best_tpl.get("icon", ""))
    wf_data["source"] = "template"
    wf_data.setdefault("metadata", {})["promoted_from"] = f"template:{best_tpl.get('template_id')}"

    # Apply on_fail strategy to all steps
    if req.on_fail:
        on_fail_map = {"retry": "retry_once", "skip": "skip", "notify": "continue",
                       "retry_once": "retry_once", "abort": "abort", "continue": "continue"}
        mapped = on_fail_map.get(req.on_fail, "abort")
        # Apply to legacy execution.on_error (used by current executor)
        wf_data.setdefault("execution", {})["on_error"] = \
            {"retry_once": "retry", "continue": "skip", "skip": "skip", "abort": "abort", "retry": "retry"}.get(mapped, "retry")
        # Also apply to each step's on_fail (for v2 steps[] when executor path uses it)
        for step in (wf_data.get("steps") or []):
            step["on_fail"] = mapped

    return {
        "status": "success",
        "template_id": best_tpl.get("template_id"),
        "template_match_score": top["score"],
        "alternative_templates": [
            {"id": c["template"].get("template_id"),
             "name": c["template"].get("display_name"),
             "score": c["score"]}
            for c in candidates[1:4]
        ],
        "workflow": wf_data,
    }


def _score_template(template: Dict[str, Any], req) -> int:
    """Rank templates against the wizard answers. Each matching dimension
    adds 10 points; substring match adds 5; unmatched adds 0."""
    score = 0
    qm = template.get("question_match") or {}

    def _match(dimension: str, user_val: str) -> int:
        if not user_val:
            return 0
        expected = qm.get(dimension) or []
        if not expected:
            return 0
        for e in expected:
            if user_val == e:
                return 10
            if e in user_val or user_val in e:
                return 5
        return 0

    score += _match("purpose", req.purpose)
    score += _match("input_source", req.input_source)
    score += _match("output_target", req.output_target)
    score += _match("schedule", req.schedule)
    return score


@router.get("/api/workflows/templates")
def list_templates():
    """List available wizard templates for the UI."""
    templates_dir = _workflows_base() / "templates"
    if not templates_dir.exists():
        return {"templates": []}
    out = []
    for f in sorted(templates_dir.glob("*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "id": t.get("template_id"),
                "display_name": t.get("display_name"),
                "description": t.get("description"),
                "icon": t.get("icon"),
                "category": t.get("category"),
                "question_match": t.get("question_match"),
            })
        except Exception:
            pass
    return {"templates": out, "total": len(out)}


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
