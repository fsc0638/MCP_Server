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
            user_inputs=req.inputs or {},
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


@router.post("/api/workflows/_actions/promote")
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

    # Resolve empty owner from caller context (frontend sends "" for convenience).
    # IMPORTANT: personal folders are keyed by employee_id (e.g. "1665") to match
    # the rest of the system — NOT by user_id (which is "line_Uxxx...", the raw
    # LINE identifier). user_id is only a last-resort fallback for users who
    # never bound an employee record (rare edge case).
    target_owner = req.target_owner or ""
    if not target_owner:
        if req.target_scope == "personal":
            ctx = caller_ctx or {}
            target_owner = (
                ctx.get("employee_id")
                or ctx.get("id")
                or ctx.get("user_id", "")
            )
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
        registry = uma.registry
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


# ── Phase 6: LLM One-shot Workflow Generator ────────────────────────────────
# Turns a free-text user description ("搜尋台灣股市新聞 5 則並存到 Notion")
# into a v2 workflow JSON, optionally executes it immediately, and lets the
# existing Gate 3 promotion pipeline write the oneshot snapshot so the chat
# promotion card can surface. Accessible-skills whitelist keeps generated
# workflows safe (can't propose skills the user doesn't have permission for).

class LLMGenerateRequest(BaseModel):
    prompt: str                  # Natural-language description of the task
    execute: bool = False        # If true, run immediately after generation
    display_name: str = ""       # Optional override; LLM picks one by default
    max_steps: int = 6           # Hard cap — avoid runaway multi-step workflows


def _build_llm_generator_messages(prompt: str, skills: List[Dict[str, Any]], max_steps: int) -> List[Dict[str, str]]:
    """System+user messages steering the LLM to output strict v2 workflow JSON."""
    skill_lines = []
    for s in skills[:80]:  # cap size to keep prompt lean
        desc = (s.get("description") or "").replace("\n", " ")[:180]
        # Expand each param with type + enum + default so LLM picks correctly
        props = (s.get("parameters") or {}).get("properties", {}) or {}
        req_set = set((s.get("parameters") or {}).get("required", []))
        param_lines = []
        for pname, pdef in props.items():
            if not isinstance(pdef, dict):
                continue
            star = "*" if pname in req_set else ""
            type_ = pdef.get("type", "string")
            enum_ = pdef.get("enum")
            default_ = pdef.get("default")
            detail_parts = [type_]
            if enum_: detail_parts.append(f"enum={enum_}")
            if default_ is not None: detail_parts.append(f"default={default_!r}")
            param_lines.append(f"{pname}{star}: {' '.join(detail_parts)}")
        params_str = " | ".join(param_lines) if param_lines else "(none)"
        skill_lines.append(
            f"- `{s['skill_id']}` ({s.get('display_name','')}): {desc}\n    params: {params_str}"
        )
    skills_text = "\n".join(skill_lines)

    system_text = f"""你是工作流設計助手。根據使用者描述，產生一份**可真正執行**的 v2 Workflow JSON。

【可用技能白名單 — 絕對不可使用白名單外的技能】
{skills_text}

【輸出規格（必須嚴格遵守）】
回傳格式為純 JSON（不要 code fence、不要註解、不要 markdown）：
{{
  "display_name": "簡短的工作流名稱（6-20 字）",
  "description": "一句話說明用途",
  "variables": {{
    "definitions": [
      {{"name": "camelCaseName", "type": "string", "source": "user_input|fixed",
        "required": true|false, "default_value": "", "description": "..."}}
    ]
  }},
  "steps": [
    {{"step_id": "step_1", "type": "sequential", "skill_id": "mcp-xxx",
      "label": "步驟中文名", "input_map": {{"param_name": "${{camelCaseName}}"}},
      "output_var": "meaningfulName", "on_fail": "abort"}}
  ]
}}

【核心規則】
1. steps ≤ {max_steps}，每個 skill_id 必須在白名單內
2. 變數名用 camelCase，**不要用中文或連字號**（searchQuery✓，search-query✗，搜尋詞✗）
3. 每個 step 的 output_var 取有意義的英文名（newsResults、summaryText...），不要用 step_N_output
4. **下一步引用上一步輸出** → 直接用 ${{上一步的 output_var}}（不要用 step_N_output）
5. enum 型別參數只能選 enum 裡列出的值（例：action 只能選白名單給的）
6. 固定值 → input_map 直接寫字面值；要引用變數 → ${{varName}}
7. 不要產生 workflow_id / blocks / connections / trigger — 後端會補
8. 只回傳 JSON，不要前後加任何解釋文字

【Python / PDF 生成 — 極重要】
當步驟要用 `mcp-python-executor` 生成檔案，code 字串必須：
a. 透過環境變數 SKILL_PARAM_* 讀取上游資料，**不要**在 code 字串中直接插入 ${{var}}，
   因為那會把 JSON 字面塞進 Python 語法造成 SyntaxError。
   正確作法：把上游資料放在 input_map 其他 param (例如 input_map.text = "${{upstreamVar}}")，
   Python code 裡用 os.environ['SKILL_PARAM_TEXT'] 讀取。
b. 生成 PDF 必須用系統預設的 ChinesePDF 輔助類別（支援中文），禁止用 FPDF / pdfkit / reportlab，
   會缺字體或亂碼：
   ```python
   import sys, os
   sys.path.insert(0, r'C:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/workspace')
   from pdf_helper import ChinesePDF
   DOWNLOADS = r'C:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/workspace/downloads'
   os.makedirs(DOWNLOADS, exist_ok=True)
   content = os.environ.get('SKILL_PARAM_TEXT', '')
   pdf = ChinesePDF(); pdf.add_page()
   pdf.chapter_title('報告標題')
   pdf.chapter_body(content)
   pdf.output(os.path.join(DOWNLOADS, '輸出檔名.pdf'))
   print('OK: 輸出檔名.pdf')
   ```
c. 檔案一定要存到 DOWNLOADS 目錄，否則下載連結 404
d. 檔案命名有意義且 ≤ 15 字中文或 30 字英數

【排程相關 — mcp-schedule-manager】⚠️ **先判斷使用者意圖屬哪類**

情境 A：要「每天早上自動跑整個流程（搜尋 + 總結 + PDF）」——**循環工作流**
  這種情況整個工作流本體（web-search、txt-analyzer、python-executor 等）
  就是要被排程反覆執行的內容。LLM 應該：
  1. 把真正的步驟（web-search → txt-analyzer → python-executor）放在工作流
     前面正常執行順序，**不要**在後面加 schedule-manager step
  2. 在 LAST 位置（或前面任何地方）放**一個** schedule-manager step：
       action: add
       type:   "workflow"        ← 注意是 "workflow" 不是 "news"
       cron:   "0 8 * * 1-5"     ← 要的時間
       name:   流程名稱
       config: {{"workflow_id":"__self__","original_request":"<完整原始需求>"}}
     (後端會把 "__self__" 替換成當前 workflow_id，所以 LLM 不用知道實際 ID)
  3. 排程觸發時，scheduled_push 會用 WorkflowExecutor 重跑整個工作流
  4. 第一次使用者按執行時，也會跑完所有步驟 + 建立排程；之後每次到時間
     就自動重跑

情境 B：只要定時推送一段內容（新聞摘要、工作提醒、語言學習），**不用多步驟**
  工作流只有 [開始] → [schedule-manager] → [結束]，schedule-manager 用：
    type: "news" (新聞) / "reminder" (一次性提醒) / "work_summary" / "language" / "custom"
  scheduled_push 會用 skill 本身的邏輯產內容（不會呼叫 workflow）

通用規則：
- action=add 必須帶 name / cron / type / original_request
- cron 格式：
    '0 8 * * 1-5'  = 週一到五上午 8 點
    '0 9 * * *'    = 每天上午 9 點
    'every +10m'   = 每 10 分鐘（interval）
    'once +30m'    = 30 分鐘後一次性
- 不要用 time / frequency 這類非標準欄位
- **必須**帶 original_request = 使用者的完整原始描述
- content 可以帶格式提示（例：'${{pdfFilePath}}'），但別依賴它做跨日的檔案傳遞
  — 排程下次觸發時 workflow 是從頭重跑，舊路徑不會保留

【錯誤避免清單】
✗ `"code": "print(${{newsSummaries}})"` ← ${{}} 插到 Python 字串中會爆
✓ 讓 python-executor 的 input_map 多一個 param 接上游：
   `"input_map": {{"code": "print(os.environ['SKILL_PARAM_TEXT'])", "text": "${{newsSummaries}}"}}`

✗ `"skill_id": "notion"` ← 必須完整名稱 mcp-notion-crud
✗ `"action": "create_daily"` ← 必須是白名單內的 enum 值

【設計習慣】
- 先想「資料流」：每個 step 產出什麼 (output_var)，下一步需要什麼 (input_map)
- 步驟盡量少：能一步搞定就別拆兩步
- 若任務是「定期推送」類，最後一步用 mcp-schedule-manager 設排程，不要自己寫 while 迴圈

【搜尋技巧 — mcp-web-search】
- query 要用使用者描述的原語言 + 具體關鍵字：
  中文任務 → 中文 query（例：「今日 台灣 經濟新聞」，不要用 "latest economic news"）
  需要即時性 → 加「今日」「最新」「本週」等時效詞
- max_results：用使用者指定的數量，沒指定預設 5；Tavily 可能回少於該數
- search_depth="advanced" 回傳的內文更豐富（但比較慢），適合要「詳細摘要」的任務
- include_domains 可指定可信來源，例：
    台灣財經：['cnyes.com','ltn.com.tw','money.udn.com','wealth.com.tw']
    台灣新聞：['udn.com','ltn.com.tw','cna.com.tw','ltn.com.tw']
  若任務提到「台灣」「本地」可用此機制過濾雜訊"""

    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": f"任務描述：\n{prompt}"},
    ]


def _llm_generate_workflow_json(prompt: str, skills: List[Dict[str, Any]], max_steps: int) -> Dict[str, Any]:
    """Call OpenAI with json_object response_format, parse, return the workflow dict."""
    from openai import OpenAI
    import os as _os
    api_key = _os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="未設定 OPENAI_API_KEY，無法產生一次性工作流")

    messages = _build_llm_generator_messages(prompt, skills, max_steps)
    model = _os.getenv("OPENAI_MODEL_WF_GENERATE") or _os.getenv("OPENAI_MODEL") or "gpt-4o"

    client = OpenAI(api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,                         # steady, reproducible
            response_format={"type": "json_object"}, # force JSON output
            max_tokens=2000,
        )
    except Exception as e:
        logger.error(f"[LLM-Gen] OpenAI call failed: {e}")
        raise HTTPException(status_code=502, detail=f"LLM 呼叫失敗：{e}")

    raw = (resp.choices[0].message.content or "").strip()
    logger.info(f"[LLM-Gen] Raw output ({len(raw)} chars): {raw[:200]}...")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"LLM 輸出非合法 JSON：{e}")
    return data


@router.post("/api/workflows/_actions/llm-generate")
async def llm_generate_workflow(
    req: LLMGenerateRequest,
    mcp_session: str = Cookie(default="", alias="mcp_session"),
):
    """Generate a one-shot v2 workflow from a natural-language prompt.

    Returns the v2 JSON. If execute=true, also runs it via WorkflowExecutor
    and returns the run result (Gate 3 will write an oneshot snapshot +
    queue a promotion candidate automatically, so the chat promotion card
    appears on next refresh).
    """
    from server.services.permissions import resolve_caller_context
    caller_ctx = resolve_caller_context(mcp_session) or {}
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 不可為空")
    if len(prompt) > 2000:
        raise HTTPException(status_code=413, detail="prompt 太長，請精簡到 2000 字以內")
    if req.max_steps < 1 or req.max_steps > 10:
        raise HTTPException(status_code=422, detail="max_steps 必須在 1-10 之間")

    # 1) Resolve accessible skills (reuse Phase 6 whitelist endpoint logic)
    from server.dependencies.uma import get_uma_instance
    uma = get_uma_instance()
    accessible: List[Dict[str, Any]] = []
    for registry_key, entry in (uma.registry.skills or {}).items():
        meta = (entry or {}).get("metadata") or {}
        sid = meta.get("name") or registry_key
        # Permission check
        if hasattr(uma.registry, "can_access"):
            try:
                if not uma.registry.can_access(sid, caller_ctx):
                    continue
            except Exception:
                pass
        # Skip risky / unavailable
        if not meta.get("_env_ready", True):
            continue
        if (meta.get("risk_level") or "low") == "high":
            continue  # High-risk skills require explicit approval — don't let LLM chain them
        accessible.append({
            "skill_id": sid,
            "display_name": meta.get("display_name") or sid,
            "description": (meta.get("description") or "")[:400],
            "parameters": meta.get("parameters") or {},
        })

    if not accessible:
        raise HTTPException(status_code=503, detail="目前沒有可用技能")

    # 2) Call LLM
    raw_workflow = _llm_generate_workflow_json(prompt, accessible, req.max_steps)

    # 3) Normalize + validate
    wf = dict(raw_workflow or {})
    wf["source"] = "llm_generated"
    if req.display_name:
        wf["display_name"] = req.display_name
    wf["metadata"] = wf.get("metadata") or {}
    wf["metadata"]["original_prompt"] = prompt
    wf["metadata"]["created_by"] = caller_ctx.get("user_id") or caller_ctx.get("employee_id", "")
    wf["metadata"]["created_at"] = datetime.now().isoformat()

    # Safety: strip any skill_id not in whitelist
    allowed_ids = {s["skill_id"] for s in accessible}
    safe_steps: List[Dict[str, Any]] = []
    stripped = 0
    for step in (wf.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if step.get("type") == "sequential":
            sid = step.get("skill_id") or ""
            if sid not in allowed_ids:
                stripped += 1
                continue
        safe_steps.append(step)
    if stripped:
        logger.warning(f"[LLM-Gen] Stripped {stripped} step(s) using non-whitelisted skills")
    wf["steps"] = safe_steps[: req.max_steps]
    if not wf["steps"]:
        raise HTTPException(status_code=422, detail="LLM 產生的工作流無可用步驟（可能全部引用到白名單外技能）")

    # ── Auto-inject original_request + resolve __self__ workflow_id ──
    # 1) Schedule-manager refuses add without original_request; supply the
    #    user's prompt if LLM forgot.
    # 2) For "type=workflow" tasks we let LLM use the placeholder "__self__"
    #    as workflow_id (since it doesn't know the ID yet). Replace it with
    #    the actual workflow_id generated during migrate_legacy below. For
    #    now we write a placeholder marker that the post-migrate step
    #    rewrites once wf["workflow_id"] is set.
    for step in wf["steps"]:
        if not isinstance(step, dict):
            continue
        if step.get("skill_id") == "mcp-schedule-manager":
            im = step.setdefault("input_map", {})
            if not im.get("original_request"):
                im["original_request"] = prompt
            # Parse config (may be JSON string or dict)
            _cfg = im.get("config")
            if isinstance(_cfg, str):
                try:
                    _cfg_d = json.loads(_cfg)
                except Exception:
                    _cfg_d = {}
            elif isinstance(_cfg, dict):
                _cfg_d = _cfg
            else:
                _cfg_d = {}
            # If LLM used "__self__" or left workflow_id empty for type=workflow,
            # mark for later substitution after migrate_legacy assigns the id
            if im.get("type") == "workflow" or _cfg_d.get("workflow_id") == "__self__":
                _cfg_d["workflow_id"] = "__SELF_WORKFLOW_ID__"  # sentinel
                im["config"] = _cfg_d

    # ── Synthesize blocks[] + connections[] from steps[] for the executor.
    # The executor walks blocks (canvas representation), not steps, so an
    # LLM-generated workflow must have both shapes. Layout is top-to-bottom
    # linear: start → step_1 → step_2 → ... → end.
    blocks_out: List[Dict[str, Any]] = []
    connections_out: List[Dict[str, Any]] = []
    bid = 1
    blocks_out.append({"id": bid, "type": "start", "x": 100, "y": 60, "label": "開始", "config": {}})
    prev_id = bid
    bid += 1
    y = 180
    for step in wf["steps"]:
        stype = step.get("type", "sequential")
        label = step.get("label") or step.get("skill_id") or stype
        cfg: Dict[str, Any] = {}
        if stype == "sequential":
            block_type = (step.get("skill_id") or "").replace("mcp-", "", 1) or "noop"
            # Flatten input_map values back into UI params shape ({source,value})
            params_ui = {}
            for pname, pv in (step.get("input_map") or {}).items():
                if isinstance(pv, str) and pv.startswith("${") and pv.endswith("}"):
                    params_ui[pname] = {"source": "variable", "value": "{{" + pv[2:-1] + "}}"}
                else:
                    params_ui[pname] = {"source": "fixed", "value": pv}
            cfg["params"] = params_ui
            # Propagate output_var so executor can store result under that name
            # (enables downstream Gate 2 + ${output_var} interpolation)
            if step.get("output_var"):
                cfg["output_var"] = step["output_var"]
        elif stype == "parallel":
            block_type = "parallel"
            cfg["branches"] = step.get("branches") or []
            cfg["merge_output_var"] = step.get("merge_output_var") or f"step_{bid}_merged"
            cfg["on_fail"] = step.get("on_fail", "abort")
        elif stype == "sub_workflow":
            block_type = "sub-workflow"
            cfg["sub_workflow_id"] = step.get("sub_workflow_id", "")
            cfg["pass_vars"] = step.get("pass_vars") or []
            cfg["output_var"] = step.get("output_var", f"step_{bid}_sub_output")
            cfg["on_fail"] = step.get("on_fail", "abort")
        else:
            block_type = stype
        blocks_out.append({"id": bid, "type": block_type, "x": 100, "y": y, "label": label, "config": cfg})
        connections_out.append({"from": prev_id, "to": bid})
        prev_id = bid
        bid += 1
        y += 120
    blocks_out.append({"id": bid, "type": "end", "x": 100, "y": y, "label": "結束", "config": {}})
    connections_out.append({"from": prev_id, "to": bid})
    wf["blocks"] = blocks_out
    wf["connections"] = connections_out

    # Migrate + validate
    from server.services.workflow_schema import migrate_legacy, validate_workflow
    try:
        wf = migrate_legacy(wf, mark_as_source="llm_generated", resync_steps=False)
        ok, errs = validate_workflow(wf)
        if not ok:
            logger.warning(f"[LLM-Gen] validation failed: {errs[:3]}")
            # Don't hard-block — surface errors but let user decide. The schema
            # validator is strict about slug format etc. which migrate_legacy
            # should have handled, so validation failure here is rare.
    except Exception as e:
        logger.error(f"[LLM-Gen] migration failed: {e}")
        raise HTTPException(status_code=500, detail=f"Schema 遷移失敗：{e}")

    # ── Post-migrate: resolve __SELF_WORKFLOW_ID__ sentinel ──
    # Now that migrate_legacy has assigned the final workflow_id, rewrite any
    # schedule-manager step that wanted to register itself as a recurring
    # workflow task. Also update the corresponding block.config.params.
    _self_id = wf.get("workflow_id", "")
    if _self_id:
        def _rewrite_in_dict(d):
            for k, v in list(d.items()):
                if isinstance(v, str) and v == "__SELF_WORKFLOW_ID__":
                    d[k] = _self_id
                elif isinstance(v, dict):
                    _rewrite_in_dict(v)
                elif isinstance(v, str) and "__SELF_WORKFLOW_ID__" in v:
                    d[k] = v.replace("__SELF_WORKFLOW_ID__", _self_id)
        for step in (wf.get("steps") or []):
            _rewrite_in_dict(step)
        for blk in (wf.get("blocks") or []):
            _rewrite_in_dict(blk)

    result: Dict[str, Any] = {
        "status": "success",
        "workflow": wf,
        "accessible_skills_count": len(accessible),
    }

    # 4) Optional immediate execution — Gate 3 auto-writes promotion snapshot
    if req.execute:
        try:
            from server.services.workflow_executor import get_workflow_executor
            executor = get_workflow_executor()
            exec_result = await executor.execute(
                workflow=wf,
                user_input=prompt,
                user_context=caller_ctx,
            )
            result["execution"] = exec_result
            result["run_id"] = exec_result.get("run_id", "")
        except Exception as e:
            logger.error(f"[LLM-Gen] execution failed: {e}")
            result["execution"] = {"status": "error", "message": str(e)}

    return result


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
