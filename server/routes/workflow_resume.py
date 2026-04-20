"""Workflow resume API.

Approach (most safe): re-run the workflow with the same run_id and skip blocks
already checkpointed as success.

This endpoint is intended to be called after an approval is approved.
"""

from __future__ import annotations

import json
import logging
from fastapi import APIRouter, HTTPException, Cookie

from server.services.approvals_service import get_approval
from server.services.permissions import resolve_caller_context
from server.services.audit_logger import log_event

logger = logging.getLogger("MCP_Server.WorkflowResume")
router = APIRouter(tags=["Workflow"])


@router.post("/api/workflows/resume/{approval_id}")
async def resume_workflow(approval_id: str, mcp_session: str = Cookie(default="", alias="mcp_session")):
    """Resume a workflow run after approval.

    Security:
    - caller must be requester OR manager/admin
    - approval must be approved

    This re-runs the workflow with the same run_id so checkpoint skipping kicks in.
    """
    ctx = resolve_caller_context(mcp_session)
    if not ctx:
        raise HTTPException(status_code=403, detail="Not signed in")

    ap = get_approval(approval_id)
    if not ap:
        raise HTTPException(status_code=404, detail="Approval not found")

    if ap.get("status") != "approved":
        raise HTTPException(status_code=409, detail=f"Approval status is {ap.get('status')}")

    caller_id = ctx.get("user_id") or ""
    caller_role = (ctx.get("role") or "guest").lower()
    if caller_id != ap.get("requested_by_subject_id") and caller_role not in {"admin", "manager"}:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Load payload from DB (internal) via direct connect to keep it server-side
    from server.services.db import connect, init_db
    conn = connect(); init_db(conn)
    row = conn.execute("SELECT payload_json FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
    conn.close()
    payload_json = (row["payload_json"] if row else "") or ""

    try:
        payload = json.loads(payload_json) if payload_json else {}
    except Exception:
        payload = {}

    workflow_id = payload.get("workflow_id")
    run_id = payload.get("run_id") or ap.get("correlation_id")

    if not workflow_id or not run_id:
        raise HTTPException(status_code=422, detail="Missing workflow_id/run_id in approval payload")

    # Load workflow JSON by id
    from server.routes.workflow import _workflows_base

    wf = None
    base = _workflows_base()
    # Search known scope locations
    for scope_dir in [base / "system", base / "department", base / "personal", base]:
        if not scope_dir.exists():
            continue
        for f in scope_dir.rglob(f"{workflow_id}.json"):
            try:
                wf = json.loads(f.read_text(encoding="utf-8"))
                break
            except Exception:
                continue
        if wf:
            break
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    # Execute with same run_id by patching gate new_run_id for this call.
    from server.services import workflow_gates
    orig_new_run_id = workflow_gates.new_run_id
    workflow_gates.new_run_id = lambda: run_id

    try:
        from server.services.workflow_executor import get_workflow_executor
        executor = get_workflow_executor()
        log_event(
            correlation_id=run_id,
            subject_id=caller_id,
            action="workflow.resume",
            resource_type="workflow",
            resource_id=workflow_id,
            decision="allow",
            reason_code="OK",
            reason=f"resume via approval {approval_id}",
        )
        result = await executor.execute(
            workflow=wf,
            user_input=payload.get("user_input", ""),
            user_context={"user_id": ap.get("requested_by_subject_id") or caller_id, "role": caller_role},
            model_override=None,
            user_inputs=payload.get("user_inputs") or {},
        )
        return {"status": "success", "resumed": True, "result": result}
    finally:
        workflow_gates.new_run_id = orig_new_run_id
