"""Workflow run status API (checkpoint-backed)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Cookie

from server.services.permissions import resolve_caller_context
from server.services.db import connect, init_db

router = APIRouter(tags=["Workflow"])


def _role(ctx) -> str:
    return (ctx or {}).get("role", "guest").lower() or "guest"


@router.get("/api/workflows/runs/{run_id}")
def get_run(run_id: str, mcp_session: str = Cookie(default="", alias="mcp_session")):
    ctx = resolve_caller_context(mcp_session)
    if not ctx:
        raise HTTPException(status_code=403, detail="Not signed in")

    role = _role(ctx)
    caller_id = ctx.get("user_id") or ""

    conn = connect(); init_db(conn)

    # Security model (v1):
    # - admin/manager: can view any run
    # - others: must be run owner (requested_by_subject_id) via approvals correlation_id
    if role not in {"admin", "manager"}:
        row = conn.execute(
            "SELECT requested_by_subject_id FROM approvals WHERE correlation_id=? ORDER BY ts_requested DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if not row or (row["requested_by_subject_id"] != caller_id):
            conn.close()
            raise HTTPException(status_code=403, detail="Forbidden")

    rows = conn.execute(
        """
        SELECT block_id, skill_name, status, output_preview, ts
        FROM workflow_block_runs
        WHERE run_id=?
        ORDER BY ts ASC
        """,
        (run_id,),
    ).fetchall()

    conn.close()
    blocks = [dict(r) for r in rows]

    # Derive overall
    statuses = [b.get("status") for b in blocks]
    if any(s == "requires_approval" for s in statuses):
        overall = "requires_approval"
    elif any(s == "error" for s in statuses):
        overall = "error"
    elif any(s == "success" for s in statuses):
        overall = "success"
    else:
        overall = "unknown"

    return {"status": "success", "run_id": run_id, "overall": overall, "blocks": blocks}
