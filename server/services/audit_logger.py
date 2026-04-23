"""Audit logger (SQLite SSOT).

Append-only audit events for workflows, skills, approvals.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from server.services.db import connect, init_db


def _now_iso() -> str:
    return datetime.now().isoformat()


def _sha256_text(text: str) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def log_event(
    *,
    correlation_id: str,
    subject_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    decision: str,
    reason_code: str = "",
    reason: str = "",
    input_obj: Optional[Dict[str, Any]] = None,
    output_obj: Optional[Dict[str, Any]] = None,
    error_code: str = "",
    latency_ms: int = 0,
    cost_tokens: int = 0,
) -> str:
    """Write an audit event row.

    We store only hashes for input/output by default to reduce sensitive data exposure.
    """
    event_id = str(uuid.uuid4())
    input_sha = ""
    output_sha = ""

    if input_obj is not None:
        try:
            input_sha = _sha256_text(json.dumps(input_obj, ensure_ascii=False, sort_keys=True))
        except Exception:
            input_sha = ""

    if output_obj is not None:
        try:
            output_sha = _sha256_text(json.dumps(output_obj, ensure_ascii=False, sort_keys=True))
        except Exception:
            output_sha = ""

    conn = connect()
    init_db(conn)
    conn.execute(
        """
        INSERT INTO audit_events(
          event_id, ts, correlation_id, subject_id, action, resource_type, resource_id,
          decision, reason_code, reason, input_sha256, output_sha256, error_code, latency_ms, cost_tokens
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event_id,
            _now_iso(),
            correlation_id,
            subject_id,
            action,
            resource_type,
            resource_id,
            decision,
            reason_code,
            reason,
            input_sha,
            output_sha,
            error_code,
            int(latency_ms or 0),
            int(cost_tokens or 0),
        ),
    )
    conn.commit()
    conn.close()
    return event_id


class TimedAudit:
    """Context helper to record latency for a single action."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.t0 = time.time()
        self.event_id: Optional[str] = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        latency_ms = int((time.time() - self.t0) * 1000)
        if exc is not None:
            self.kwargs.setdefault("decision", "error")
            self.kwargs.setdefault("error_code", exc_type.__name__ if exc_type else "Exception")
            self.kwargs.setdefault("reason", str(exc)[:200])
        self.kwargs["latency_ms"] = latency_ms
        self.event_id = log_event(**self.kwargs)
        return False
