"""Approvals (HitL) service backed by SQLite SSOT.

TTL default: 10 minutes (configurable by caller).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from server.services.db import connect, init_db


def _now_iso() -> str:
    return datetime.now().isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest() if text else ""


def create_approval(
    *,
    correlation_id: str,
    requested_by_subject_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    request_summary: str,
    payload: Optional[Dict[str, Any]] = None,
    ttl_seconds: int = 600,
) -> str:
    approval_id = str(uuid.uuid4())
    ts_requested = _now_iso()
    ts_expires = (datetime.now() + timedelta(seconds=int(ttl_seconds))).isoformat()

    payload_json = ""
    payload_sha = ""
    if payload is not None:
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            payload_sha = _sha256_text(payload_json)
        except Exception:
            payload_json = ""
            payload_sha = ""

    conn = connect()
    init_db(conn)
    conn.execute(
        """
        INSERT INTO approvals(
          approval_id, ts_requested, ts_expires, correlation_id, requested_by_subject_id,
          action, resource_type, resource_id, status, request_summary, payload_json, payload_sha256
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            approval_id,
            ts_requested,
            ts_expires,
            correlation_id,
            requested_by_subject_id,
            action,
            resource_type,
            resource_id,
            "pending",
            request_summary,
            payload_json,
            payload_sha,
        ),
    )
    conn.commit()
    conn.close()
    return approval_id


def get_approval(approval_id: str) -> Optional[Dict[str, Any]]:
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def resolve_approval(
    *,
    approval_id: str,
    status: str,
    resolved_by_subject_id: str,
    resolution_note: str = "",
) -> bool:
    if status not in {"approved", "rejected"}:
        raise ValueError("status must be approved|rejected")

    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT status, ts_expires FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
    if not row:
        conn.close()
        return False

    # Expiry check
    try:
        exp = datetime.fromisoformat(row["ts_expires"])
        if datetime.now() > exp:
            conn.execute(
                "UPDATE approvals SET status='expired', ts_resolved=?, resolved_by_subject_id=?, resolution_note=? WHERE approval_id=?",
                (_now_iso(), resolved_by_subject_id, "expired", approval_id),
            )
            conn.commit()
            conn.close()
            return False
    except Exception:
        pass

    if row["status"] != "pending":
        conn.close()
        return False

    conn.execute(
        """
        UPDATE approvals
        SET status=?, ts_resolved=?, resolved_by_subject_id=?, resolution_note=?
        WHERE approval_id=?
        """,
        (status, _now_iso(), resolved_by_subject_id, resolution_note or "", approval_id),
    )
    conn.commit()
    conn.close()
    return True
