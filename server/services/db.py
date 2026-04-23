"""SQLite SSOT (Single Source of Truth) for AgentK.

Phase 1 foundation: approvals + audit events.

Design goals:
- Minimal deps (stdlib sqlite3)
- Safe concurrency for local dev (one process) and typical uvicorn workers (serialized writes via sqlite).
- Easy migrations (create-if-not-exists; schema_version table).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

_DB_LOCK = threading.Lock()


def get_project_root() -> Path:
    return Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))).resolve()


def get_db_path() -> Path:
    # Keep SSOT under workspace so it is environment-local and easy to backup.
    d = get_project_root() / "workspace" / "ssot"
    d.mkdir(parents=True, exist_ok=True)
    return d / "agentk.sqlite"


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    p = str((db_path or get_db_path()).resolve())
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    with _DB_LOCK:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
              version INTEGER NOT NULL
            );
            """
        )
        cur = conn.execute("SELECT COUNT(1) AS n FROM schema_version")
        n = int(cur.fetchone()[0])
        if n == 0:
            conn.execute("INSERT INTO schema_version(version) VALUES (1)")

        # Subjects (minimal for now)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subjects (
              subject_id TEXT PRIMARY KEY,
              provider TEXT NOT NULL DEFAULT 'unknown',
              role TEXT NOT NULL DEFAULT 'guest',
              dept TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )

        # Audit events (append-only)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
              event_id TEXT PRIMARY KEY,
              ts TEXT NOT NULL,
              correlation_id TEXT NOT NULL,
              subject_id TEXT NOT NULL,
              action TEXT NOT NULL,
              resource_type TEXT NOT NULL,
              resource_id TEXT NOT NULL,
              decision TEXT NOT NULL,
              reason_code TEXT NOT NULL DEFAULT '',
              reason TEXT NOT NULL DEFAULT '',
              input_sha256 TEXT NOT NULL DEFAULT '',
              output_sha256 TEXT NOT NULL DEFAULT '',
              error_code TEXT NOT NULL DEFAULT '',
              latency_ms INTEGER NOT NULL DEFAULT 0,
              cost_tokens INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        # Approvals (HitL)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
              approval_id TEXT PRIMARY KEY,
              ts_requested TEXT NOT NULL,
              ts_expires TEXT NOT NULL,
              ts_resolved TEXT NOT NULL DEFAULT '',
              correlation_id TEXT NOT NULL,
              requested_by_subject_id TEXT NOT NULL,
              action TEXT NOT NULL,
              resource_type TEXT NOT NULL,
              resource_id TEXT NOT NULL,
              status TEXT NOT NULL,
              request_summary TEXT NOT NULL,
              payload_json TEXT NOT NULL DEFAULT '',
              payload_sha256 TEXT NOT NULL DEFAULT '',
              resolved_by_subject_id TEXT NOT NULL DEFAULT '',
              resolution_note TEXT NOT NULL DEFAULT ''
            );
            """
        )

        # Workflow checkpoints (resume-safe)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_block_runs (
              run_id TEXT NOT NULL,
              workflow_id TEXT NOT NULL,
              block_id TEXT NOT NULL,
              skill_name TEXT NOT NULL,
              status TEXT NOT NULL,
              output_preview TEXT NOT NULL DEFAULT '',
              vars_sha256 TEXT NOT NULL DEFAULT '',
              context_sha256 TEXT NOT NULL DEFAULT '',
              ts TEXT NOT NULL,
              PRIMARY KEY (run_id, block_id)
            );
            """
        )

        conn.commit()
