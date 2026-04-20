"""Workflow checkpoint store (SQLite SSOT).

Stores per-block execution results so we can safely resume after HitL approvals.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional

from server.services.db import connect, init_db


def _now_iso() -> str:
    return datetime.now().isoformat()


def _sha(obj: Any) -> str:
    if obj is None:
        return ""
    try:
        s = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    except Exception:
        s = str(obj)
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def upsert_block_run(
    *,
    run_id: str,
    workflow_id: str,
    block_id: str,
    skill_name: str,
    status: str,
    output_preview: str = "",
    resolved_vars: Optional[Dict[str, Any]] = None,
    accumulated_context: str = "",
) -> None:
    conn = connect()
    init_db(conn)
    conn.execute(
        """
        INSERT INTO workflow_block_runs(
          run_id, workflow_id, block_id, skill_name, status, output_preview,
          vars_sha256, context_sha256, ts
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id, block_id) DO UPDATE SET
          status=excluded.status,
          output_preview=excluded.output_preview,
          vars_sha256=excluded.vars_sha256,
          context_sha256=excluded.context_sha256,
          ts=excluded.ts
        """,
        (
            run_id,
            workflow_id,
            block_id,
            skill_name,
            status,
            (output_preview or "")[:500],
            _sha(resolved_vars or {}),
            _sha(accumulated_context or ""),
            _now_iso(),
        ),
    )
    conn.commit()
    conn.close()


def get_run_block_status_map(run_id: str) -> Dict[str, str]:
    conn = connect()
    init_db(conn)
    rows = conn.execute(
        "SELECT block_id, status FROM workflow_block_runs WHERE run_id=?",
        (run_id,),
    ).fetchall()
    conn.close()
    return {r["block_id"]: r["status"] for r in rows}
