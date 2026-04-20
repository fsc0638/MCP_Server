"""Approvals (HitL) API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Cookie

from server.services.permissions import resolve_caller_context
from server.services.approvals_service import get_approval, resolve_approval
from server.services.audit_logger import log_event

router = APIRouter(tags=["Approvals"])


def _role(ctx) -> str:
    return (ctx or {}).get("role", "guest").lower() or "guest"


@router.get("/api/approvals")
def list_approvals(status: str = "pending"):
    # Minimal v1: list not implemented yet (avoid overexposure); UI will fetch individual items.
    raise HTTPException(status_code=501, detail="Not implemented yet")


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
def approve(approval_id: str, mcp_session: str = Cookie(default="")):
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
    return {"status": "success" if ok else "error", "approved": ok}


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
