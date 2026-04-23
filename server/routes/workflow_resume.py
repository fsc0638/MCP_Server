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
        # Rebuild user_context with session_id from approval payload so
        # workflow_executor / any downstream skill can still access the
        # originating chat session (and so HitL-β bypass works).
        _uc = {
            "user_id": ap.get("requested_by_subject_id") or caller_id,
            "role": caller_role,
        }
        _orig_session_id = payload.get("session_id", "")
        if _orig_session_id:
            _uc["session_id"] = _orig_session_id

        result = await executor.execute(
            workflow=wf,
            user_input=payload.get("user_input", ""),
            user_context=_uc,
            model_override=None,
            user_inputs=payload.get("user_inputs") or {},
        )

        # ── Push completion message back to originating chat session ──
        # Web chat polls /chat/session/{id} for new messages; by writing a
        # final assistant turn we make the user aware that their paused
        # workflow finished (or failed) without requiring any real-time
        # push channel.
        if _orig_session_id:
            try:
                from server.dependencies.session import get_session_manager
                sess_mgr = get_session_manager()
                _overall = result.get("status", "")
                _wf_name = wf.get("display_name") or wf.get("name") or workflow_id
                if _overall == "success":
                    _final = result.get("final_output") or f"工作流已完成（{result.get('blocks_executed', 0)} 個節點）"
                    _msg = f"✅ 已批准並完成工作流「{_wf_name}」\n\n{_final}"
                elif _overall == "requires_approval":
                    # Shouldn't happen post-β, but keep graceful message
                    _msg = f"⏸️ 工作流「{_wf_name}」需要再次審批（仍有高風險步驟）"
                elif _overall == "error":
                    _errs = result.get("errors") or []
                    _msg = f"❌ 工作流「{_wf_name}」續跑失敗：{'; '.join(str(e) for e in _errs[:2]) or '未知錯誤'}"
                else:
                    _msg = f"工作流「{_wf_name}」續跑結束（狀態：{_overall or '未知'}）"
                sess_mgr.append_message(_orig_session_id, "assistant", _msg)
                logger.info(f"[WFResume] Pushed completion message to session {_orig_session_id} ({len(_msg)} chars)")
            except Exception as _push_err:
                logger.warning(f"[WFResume] Failed to push completion msg to session: {_push_err}")

        return {"status": "success", "resumed": True, "result": result}
    finally:
        workflow_gates.new_run_id = orig_new_run_id
