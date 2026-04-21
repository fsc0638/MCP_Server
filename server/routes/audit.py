"""Audit query API (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Cookie

from server.services.permissions import resolve_caller_context
from server.services.db import connect, init_db

router = APIRouter(tags=["Audit"])


def _role(ctx) -> str:
    return (ctx or {}).get("role", "guest").lower() or "guest"


@router.get("/api/audit/recent")
def recent(limit: int = 50, mcp_session: str = Cookie(default="", alias="mcp_session")):
    ctx = resolve_caller_context(mcp_session)
    if not ctx:
        raise HTTPException(status_code=403, detail="Not signed in")

    role = _role(ctx)
    caller_id = ctx.get("user_id") or ""
    limit = max(1, min(int(limit or 50), 200))

    conn = connect(); init_db(conn)

    if role in {"admin", "manager"}:
        rows = conn.execute(
            """
            SELECT ts, correlation_id, subject_id, action, resource_type, resource_id, decision, reason_code, reason
            FROM audit_events
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT ts, correlation_id, subject_id, action, resource_type, resource_id, decision, reason_code, reason
            FROM audit_events
            WHERE subject_id=?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (caller_id, limit),
        ).fetchall()

    conn.close()
    return {"status": "success", "total": len(rows), "events": [dict(r) for r in rows]}
