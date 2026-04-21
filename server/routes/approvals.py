"""Approvals (HitL) API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Cookie, BackgroundTasks

from server.services.permissions import resolve_caller_context
from server.services.approvals_service import get_approval, resolve_approval
from server.services.audit_logger import log_event

router = APIRouter(tags=["Approvals"])


def _role(ctx) -> str:
    return (ctx or {}).get("role", "guest").lower() or "guest"


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
    return {"status": "success", "total": len(rows), "approvals": [dict(r) for r in rows]}


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
    return {"status": "success" if ok else "error", "rejected": ok}
