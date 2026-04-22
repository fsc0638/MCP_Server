"""Workflow Audit Logger — records every workflow execution to JSONL.

Log path: workspace/workflows/logs/{workflow_id}.jsonl
Each line is a JSON object with execution metadata.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("MCP_Server.WorkflowAudit")


def _logs_dir() -> Path:
    pr = os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))
    d = Path(pr) / "workspace" / "workflows" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_workflow_execution(
    workflow_id: str,
    workflow_name: str,
    session_id: str,
    result: Dict[str, Any],
    trigger: str = "manual",
    user_input: str = "",
    user_context: dict = None,
) -> None:
    """Append a workflow execution record to the audit log.

    Args:
        workflow_id: The workflow ID
        workflow_name: Human-readable workflow name
        session_id: Who triggered it
        result: WorkflowExecutor result dict
        trigger: "manual" | "keyword" | "semantic" | "schedule" | "line_command" | "api"
        user_input: The original user input
        user_context: User identity info
    """
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "session_id": session_id,
            "trigger": trigger,
            "user_input": (user_input or "")[:200],
            "blocks_executed": result.get("blocks_executed", 0),
            "status": result.get("status", "unknown"),
            "results_summary": _summarize_results(result.get("results", [])),
            "final_output_preview": (result.get("final_output", "") or "")[:200],
            "executed_at": result.get("executed_at", ""),
        }
        if user_context:
            entry["user_name"] = user_context.get("name", "")
            entry["user_id"] = user_context.get("employee_id", user_context.get("id", ""))

        log_path = _logs_dir() / f"{workflow_id}.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.info(f"[WFAudit] Logged execution: {workflow_id} (trigger={trigger}, blocks={entry['blocks_executed']})")
    except Exception as e:
        logger.warning(f"[WFAudit] Failed to log: {e}")


def _summarize_results(results: list) -> list:
    """Create a compact summary of block execution results."""
    summary = []
    for r in results:
        s = {
            "block_id": r.get("block_id"),
            "type": r.get("type"),
            "status": r.get("status"),
        }
        if r.get("skill"):
            s["skill"] = r["skill"]
        if r.get("error"):
            s["error"] = r["error"][:100]
        summary.append(s)
    return summary


def get_workflow_logs(workflow_id: str, limit: int = 50) -> list:
    """Read recent execution logs for a workflow.

    Args:
        workflow_id: The workflow ID
        limit: Max number of entries to return (most recent first)

    Returns:
        List of log entries (newest first)
    """
    log_path = _logs_dir() / f"{workflow_id}.jsonl"
    if not log_path.exists():
        return []

    entries = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        logger.warning(f"[WFAudit] Failed to read log: {e}")
        return []

    # Return newest first, limited
    entries.reverse()
    return entries[:limit]


def aggregate_daily_counts() -> Dict[str, int]:
    """Count workflow executions grouped by date (YYYY-MM-DD).

    Scans every `{workflow_id}.jsonl` under workspace/workflows/logs/ and
    returns { "2026-04-22": 12, "2026-04-21": 8, ... }. Used by
    /skills/workflow/stats to expose a `workflow_calls` series for the
    admin dashboard trend chart and KPI cards.

    Cheap enough to call on every stats request — typical fleet logs are
    <10 MB total even at multi-month retention.
    """
    counts: Dict[str, int] = {}
    logs_dir = _logs_dir()
    for log_file in logs_dir.glob("*.jsonl"):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp", "")
                    if len(ts) >= 10:
                        day = ts[:10]
                        counts[day] = counts.get(day, 0) + 1
        except Exception as e:
            logger.debug(f"[WFAudit] aggregate: skip {log_file.name}: {e}")
    return counts


def get_all_recent_logs(limit: int = 100) -> list:
    """Read recent execution logs across all workflows.

    Returns:
        List of log entries (newest first)
    """
    logs_dir = _logs_dir()
    all_entries = []

    for log_file in logs_dir.glob("*.jsonl"):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            all_entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass

    # Sort by timestamp descending
    all_entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return all_entries[:limit]
