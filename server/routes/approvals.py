"""Approvals (HitL) API routes."""

from __future__ import annotations

import json as _json
import os as _os
from pathlib import Path as _Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Cookie, BackgroundTasks

from server.services.permissions import resolve_caller_context
from server.services.approvals_service import get_approval, resolve_approval
from server.services.audit_logger import log_event

router = APIRouter(tags=["Approvals"])


def _role(ctx) -> str:
    return (ctx or {}).get("role", "guest").lower() or "guest"


# ─────────────────────────────────────────────────────────────
# Display enrichment — turn raw DB fields into user-friendly labels
# ─────────────────────────────────────────────────────────────

_WF_NAME_CACHE: Dict[str, str] = {}


def _lookup_skill_display_name(skill_name: str) -> str:
    """Get SKILL.md's display_name; fall back to skill_name."""
    if not skill_name:
        return ""
    try:
        from server.dependencies.uma import get_uma_instance as _get_uma
        uma = _get_uma()
        data = uma.registry.get_skill(skill_name)
        if data:
            meta = data.get("metadata", {}) or {}
            dn = meta.get("display_name")
            if dn and isinstance(dn, str) and dn.strip():
                return dn.strip()
    except Exception:
        pass
    return skill_name


def _lookup_workflow_name(workflow_id: str) -> str:
    """Find workspace/workflows/**/{id}.json and return its display_name/name."""
    if not workflow_id:
        return ""
    if workflow_id in _WF_NAME_CACHE:
        return _WF_NAME_CACHE[workflow_id]
    try:
        pr = _Path(_os.getenv("PROJECT_ROOT", str(_Path(__file__).resolve().parents[2])))
        base = pr / "workspace" / "workflows"
        for scope in ("system", "department", "personal"):
            scope_dir = base / scope
            if not scope_dir.exists():
                continue
            for f in scope_dir.rglob(f"{workflow_id}.json"):
                try:
                    d = _json.loads(f.read_text(encoding="utf-8"))
                    name = d.get("display_name") or d.get("name") or workflow_id
                    _WF_NAME_CACHE[workflow_id] = name
                    return name
                except Exception:
                    continue
    except Exception:
        pass
    _WF_NAME_CACHE[workflow_id] = workflow_id
    return workflow_id


def _lookup_requester_display(subject_id: str) -> str:
    """Format as '<員編> - <姓名>' when possible; fall back to raw id."""
    if not subject_id:
        return ""
    try:
        pr = _Path(_os.getenv("PROJECT_ROOT", str(_Path(__file__).resolve().parents[2])))
        # Web / LINE sessions are keyed by session id in workspace/users/
        sess_path = pr / "workspace" / "users" / f"{subject_id}.json"
        if sess_path.exists():
            d = _json.loads(sess_path.read_text(encoding="utf-8"))
            emp_id = (d.get("employee_id") or "").strip()
            name = (d.get("name") or "").strip()
            if emp_id and name:
                return f"{emp_id} - {name}"
            if name:
                return name
    except Exception:
        pass
    # Second chance: subject_id might BE the employee_id
    try:
        from server.services.employee_lookup import lookup_by_employee_id
        emp = lookup_by_employee_id(subject_id)
        if emp:
            return f"{emp.get('employee_id','')} - {emp.get('name','')}".strip(" -")
    except Exception:
        pass
    return subject_id


def _enrich_approval_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Attach display_* fields. Also parse workflow_id out of resource_id
    (stored as '{workflow_id}:{block_id}' by workflow_executor HitL branch)."""
    r = dict(row)
    # Skill display name (action = skill_name for workflow-originated approvals)
    r["skill_display_name"] = _lookup_skill_display_name(r.get("action") or "")
    # Workflow name — resource_id is '{wf_id}:{block_id}' for workflow_block
    _rid = r.get("resource_id") or ""
    _wf_id = _rid.split(":", 1)[0] if _rid else ""
    r["workflow_id"] = _wf_id
    r["workflow_name"] = _lookup_workflow_name(_wf_id)
    # Requester display (員編 - 姓名)
    r["requester_display"] = _lookup_requester_display(r.get("requested_by_subject_id") or "")
    return r


@router.get("/api/approvals")
def list_approvals(status: str = "pending", limit: int = 50, mcp_session: str = Cookie(default="")):
    """List approvals.

    Security:
    - Only admin/manager can list globally.
    - Requester can list their own approvals via requester_only.
    """
    from server.services.db import connect, init_db

    ctx = resolve_caller_context(mcp_session)
    if not ctx:
        raise HTTPException(status_code=403, detail="Not signed in")

    role = (ctx.get("role") or "guest").lower()
    caller_id = ctx.get("user_id") or ""

    conn = connect()
    init_db(conn)

    limit = max(1, min(int(limit or 50), 200))

    if role in {"admin", "manager"}:
        rows = conn.execute(
            """
            SELECT approval_id, ts_requested, ts_expires, ts_resolved, correlation_id,
                   requested_by_subject_id, action, resource_type, resource_id, status, request_summary,
                   resolved_by_subject_id, resolution_note
            FROM approvals
            WHERE status=?
            ORDER BY ts_requested DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    else:
        # requester-only
        rows = conn.execute(
            """
            SELECT approval_id, ts_requested, ts_expires, ts_resolved, correlation_id,
                   requested_by_subject_id, action, resource_type, resource_id, status, request_summary,
                   resolved_by_subject_id, resolution_note
            FROM approvals
            WHERE status=? AND requested_by_subject_id=?
            ORDER BY ts_requested DESC
            LIMIT ?
            """,
            (status, caller_id, limit),
        ).fetchall()

    conn.close()
    return {
        "status": "success",
        "total": len(rows),
        "approvals": [_enrich_approval_row(r) for r in rows],
    }


@router.get("/api/approvals/{approval_id}")
def read_approval(approval_id: str):
    ap = get_approval(approval_id)
    if not ap:
        raise HTTPException(status_code=404, detail="Approval not found")

    # Do not return payload_json to frontend by default.
    ap.pop("payload_json", None)
    ap.pop("payload_sha256", None)
    return {"status": "success", "approval": ap}


@router.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str, background_tasks: BackgroundTasks, mcp_session: str = Cookie(default="")):
    ctx = resolve_caller_context(mcp_session)
    if not ctx:
        raise HTTPException(status_code=403, detail="Not signed in")

    ap = get_approval(approval_id)
    if not ap:
        raise HTTPException(status_code=404, detail="Approval not found")

    caller_id = (ctx or {}).get("user_id") or ""
    caller_role = _role(ctx)

    # B: requester OR manager/admin can approve
    if caller_id != ap.get("requested_by_subject_id") and caller_role not in {"admin", "manager"}:
        raise HTTPException(status_code=403, detail="Forbidden")

    ok = resolve_approval(approval_id=approval_id, status="approved", resolved_by_subject_id=caller_id)
    log_event(
        correlation_id=ap.get("correlation_id", ""),
        subject_id=caller_id,
        action="approval.approve",
        resource_type="approval",
        resource_id=approval_id,
        decision="allow" if ok else "deny",
        reason_code="OK" if ok else "NOT_PENDING_OR_EXPIRED",
        reason="Approved" if ok else "Approval not pending or expired",
    )

    # Option 2: server-orchestrated resume (best-effort background task).
    if ok:
        try:
            from server.routes.workflow_resume import resume_workflow
            background_tasks.add_task(resume_workflow, approval_id, mcp_session)
        except Exception:
            # Fail-open: approval stays approved; manual resume endpoint remains available.
            pass

    return {"status": "success" if ok else "error", "approved": ok, "resume_scheduled": bool(ok)}


@router.post("/api/approvals/{approval_id}/reject")
def reject(approval_id: str, mcp_session: str = Cookie(default="")):
    ctx = resolve_caller_context(mcp_session)
    if not ctx:
        raise HTTPException(status_code=403, detail="Not signed in")

    ap = get_approval(approval_id)
    if not ap:
        raise HTTPException(status_code=404, detail="Approval not found")

    caller_id = (ctx or {}).get("user_id") or ""
    caller_role = _role(ctx)

    if caller_id != ap.get("requested_by_subject_id") and caller_role not in {"admin", "manager"}:
        raise HTTPException(status_code=403, detail="Forbidden")

    ok = resolve_approval(approval_id=approval_id, status="rejected", resolved_by_subject_id=caller_id)
    log_event(
        correlation_id=ap.get("correlation_id", ""),
        subject_id=caller_id,
        action="approval.reject",
        resource_type="approval",
        resource_id=approval_id,
        decision="allow" if ok else "deny",
        reason_code="OK" if ok else "NOT_PENDING_OR_EXPIRED",
        reason="Rejected" if ok else "Approval not pending or expired",
    )

    # ── Push rejection message back to originating chat session ──
    # Same pattern as workflow_resume: user saw "⏸️ 等待審批" in chat;
    # after rejection they should see a final status message.
    if ok:
        try:
            import json as _json
            from server.services.db import connect as _dbc, init_db as _dbi
            _conn = _dbc(); _dbi(_conn)
            _row = _conn.execute(
                "SELECT payload_json FROM approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            _conn.close()
            _payload = {}
            if _row and _row["payload_json"]:
                try:
                    _payload = _json.loads(_row["payload_json"])
                except Exception:
                    _payload = {}
            _session_id = _payload.get("session_id", "")
            if _session_id:
                from server.dependencies.session import get_session_manager
                _sm = get_session_manager()
                _wf_id = _payload.get("workflow_id", "")
                _skill = _payload.get("skill_name", "")
                _msg = (
                    f"❌ 已拒絕高風險步驟，工作流「{_wf_id or '未知'}」已終止。"
                    f"\n\n被拒絕的技能：{_skill or '未知'}"
                )
                _sm.append_message(_session_id, "assistant", _msg)
        except Exception:
            # Non-blocking — rejection itself already persisted
            pass

    return {"status": "success" if ok else "error", "rejected": ok}
