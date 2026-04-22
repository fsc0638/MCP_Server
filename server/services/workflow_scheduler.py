"""Workflow-native scheduler — bridges APScheduler and WorkflowExecutor.

When a workflow has `trigger.enabled=true` AND `trigger.schedule="<cron>"`,
this service registers an APScheduler cron job that invokes
WorkflowExecutor.execute() at the configured times. The fired workflow
runs its full pipeline and (if `trigger.push_target` is set) pushes the
final_output to that LINE chat.

Design goals:
  - Native workflow scheduling: no need to embed mcp-schedule-manager as
    a step just to get recurring behaviour
  - Hot-reload: save/delete a workflow updates APScheduler jobs in place
  - Idempotent: sync_all_workflow_schedules() can be called anytime to
    reconcile APScheduler state with disk

Separation from scheduled_push.py:
  - scheduled_push.py handles type=news / reminder / custom / language
    tasks stored in workspace/schedules/ (LINE-centric, per-user file)
  - this module handles workflow-native cron triggers stored inside each
    workflow JSON's trigger.schedule field (scope-agnostic)
  - a type=workflow task in scheduled_push is still supported (when
    schedule-manager step is used), but LLM-gen should prefer this
    module's native scheduling from now on
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("MCP_Server.WorkflowScheduler")


def _project_root() -> Path:
    return Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))


def _workflows_base() -> Path:
    return _project_root() / "workspace" / "workflows"


def _job_id(workflow_id: str) -> str:
    """APScheduler job id — prefixed so we don't collide with other jobs."""
    return f"wf_cron_{workflow_id}"


def _parse_cron_fields(cron_str: str) -> Optional[Dict[str, str]]:
    """Parse a 5-field cron into APScheduler CronTrigger kwargs.

    Returns None for unsupported forms (once +Nm / every +Nm — those belong
    in scheduled_push, not APScheduler cron).
    """
    if not cron_str or not isinstance(cron_str, str):
        return None
    s = cron_str.strip()
    if s.startswith("once") or s.startswith("every"):
        return None  # interval / one-shot not representable as APScheduler cron
    parts = s.split()
    if len(parts) != 5:
        return None
    minute, hour, day, month, day_of_week = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }


def _scheduler():
    """Fetch the global APScheduler instance from app module.

    Module-level `__scheduler` with double-underscore prefix + suffix is a
    regular attribute (no name mangling at module level), BUT the variable
    defaults to None at import time and only gets assigned inside
    _setup_scheduler(). We re-check on every call to account for:
      - startup timing (refresh_workflow_schedule called before setup finished)
      - hot reloads during development
    """
    try:
        import server.app as app_module
        sched = app_module.__dict__.get("_App__scheduler")  # Try mangled first (wouldn't apply to module but safe)
        if sched is None:
            sched = app_module.__dict__.get("__scheduler")
        if sched is None:
            sched = getattr(app_module, "__scheduler", None)
        if sched is not None:
            try:
                state = getattr(sched, "state", "?")
                logger.debug(f"[WFScheduler] scheduler id={id(sched)} state={state}")
            except Exception:
                pass
        else:
            logger.warning("[WFScheduler] app_module.__scheduler is None — startup may not have run")
        return sched
    except Exception as e:
        logger.warning(f"[WFScheduler] _scheduler() failed: {e}")
        return None


def _load_workflow(workflow_id: str) -> Optional[Dict[str, Any]]:
    """Find a workflow JSON across all scopes (system/department/personal)."""
    base = _workflows_base()
    for scope in ("system", "department", "personal"):
        d = base / scope
        if not d.exists():
            continue
        for p in d.rglob(f"{workflow_id}.json"):
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def _run_scheduled_workflow(workflow_id: str):
    """APScheduler callback — load workflow, run executor, push result.

    This runs in the APScheduler thread. Must not raise; all errors logged.
    """
    try:
        wf = _load_workflow(workflow_id)
        if not wf:
            logger.warning(f"[WFScheduler] Job fired but workflow '{workflow_id}' not found — may have been deleted. Removing job.")
            remove_workflow_schedule(workflow_id)
            return
        trigger = wf.get("trigger") or {}
        if not trigger.get("enabled"):
            logger.info(f"[WFScheduler] Skipping '{workflow_id}' — trigger disabled")
            return

        logger.info(f"[WFScheduler] Firing scheduled workflow '{workflow_id}' at {datetime.now().strftime('%H:%M:%S')}")

        # Run workflow via executor (bridging sync → async)
        from server.services.workflow_executor import get_workflow_executor
        executor = get_workflow_executor()

        push_target = trigger.get("push_target") or wf.get("metadata", {}).get("created_by", "")
        user_ctx = {"session_id": push_target}

        # Get or create event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("closed")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        result = loop.run_until_complete(
            executor.execute(
                workflow=wf,
                user_input=f"排程觸發 {wf.get('display_name', workflow_id)}",
                user_context=user_ctx,
            )
        )
        status = result.get("status", "unknown")
        output = result.get("final_output", "")

        # Push to LINE if push_target is a line_ session id
        if push_target and push_target.startswith("line_") and output:
            try:
                from server.integrations.line_connector import _get_line_components, _send_status_push
                _, line_api, _ = _get_line_components()
                chat_id = push_target[len("line_"):]
                if chat_id.startswith("group_"):
                    chat_id = chat_id[len("group_"):]
                header = f"⏰ 定時工作流 — {wf.get('display_name', workflow_id)}\n{'─' * 20}\n\n"
                _send_status_push(line_api, chat_id, header + output[:3500])
                logger.info(f"[WFScheduler] Pushed '{workflow_id}' result to {chat_id}")
            except Exception as push_err:
                logger.warning(f"[WFScheduler] Push failed for '{workflow_id}': {push_err}")

        # Audit log
        try:
            from server.services.workflow_audit import log_workflow_execution
            log_workflow_execution(
                workflow_id, wf.get("display_name", ""), push_target, result, trigger="cron"
            )
        except Exception:
            pass

        logger.info(f"[WFScheduler] '{workflow_id}' done: status={status}, blocks={result.get('blocks_executed', 0)}")
    except Exception as e:
        logger.error(f"[WFScheduler] Scheduled run failed for '{workflow_id}': {e}")


def refresh_workflow_schedule(workflow_id: str) -> Dict[str, Any]:
    """(Re)register the APScheduler job for one workflow based on its current
    trigger.schedule + trigger.enabled. Idempotent — safe to call on save.

    Returns { status, action, cron, reason? }
    """
    sched = _scheduler()
    if not sched:
        return {"status": "skipped", "reason": "APScheduler not available"}

    wf = _load_workflow(workflow_id)
    if not wf:
        return {"status": "not_found", "workflow_id": workflow_id}

    trigger = wf.get("trigger") or {}
    enabled = bool(trigger.get("enabled"))
    cron_str = (trigger.get("schedule") or "").strip()
    job_id = _job_id(workflow_id)

    if not enabled or not cron_str:
        # Remove any existing job for this workflow
        try:
            sched.remove_job(job_id)
            logger.info(f"[WFScheduler] Removed job '{job_id}' (trigger disabled or no schedule)")
            return {"status": "removed", "action": "disabled"}
        except Exception:
            return {"status": "noop", "action": "no_existing_job"}

    cron_kwargs = _parse_cron_fields(cron_str)
    if not cron_kwargs:
        # Non-cron schedules (once +Nm / every +Nm) aren't supported here —
        # use mcp-schedule-manager for those. Remove any stale job.
        try:
            sched.remove_job(job_id)
        except Exception:
            pass
        return {"status": "skipped", "reason": f"Unsupported schedule format: {cron_str!r}"}

    try:
        from apscheduler.triggers.cron import CronTrigger
        cron_trigger = CronTrigger(**cron_kwargs)

        # Sanity check: if scheduler is not running, add_job will add to
        # pending queue and never fire. Force-start if needed.
        _state = getattr(sched, "state", 0)
        if _state == 0:
            logger.warning(
                f"[WFScheduler] Scheduler is STOPPED (state=0) when registering "
                f"'{workflow_id}' — attempting to start it now"
            )
            try:
                sched.start()
                logger.info("[WFScheduler] Scheduler force-started successfully")
            except Exception as start_err:
                logger.error(f"[WFScheduler] Force-start failed: {start_err}")
                return {"status": "error", "error": f"Scheduler not running and failed to start: {start_err}"}

        sched.add_job(
            _run_scheduled_workflow,
            cron_trigger,
            args=[workflow_id],
            id=job_id,
            name=f"Workflow: {wf.get('display_name', workflow_id)}",
            replace_existing=True,
            coalesce=True,           # skip missed runs if multiple elapsed
            misfire_grace_time=300,  # up to 5 minutes late is OK
        )
        _after_state = getattr(sched, "state", 0)
        logger.info(
            f"[WFScheduler] Registered cron for '{workflow_id}': {cron_str} "
            f"(scheduler state={_after_state})"
        )
        return {"status": "registered", "cron": cron_str, "action": "upsert"}
    except Exception as e:
        logger.error(f"[WFScheduler] Failed to register '{workflow_id}': {e}")
        return {"status": "error", "error": str(e)}


def remove_workflow_schedule(workflow_id: str) -> Dict[str, Any]:
    """Remove the APScheduler job for a workflow (call on delete)."""
    sched = _scheduler()
    if not sched:
        return {"status": "skipped", "reason": "APScheduler not available"}
    job_id = _job_id(workflow_id)
    try:
        sched.remove_job(job_id)
        logger.info(f"[WFScheduler] Removed job '{job_id}'")
        return {"status": "removed"}
    except Exception:
        return {"status": "noop"}  # job didn't exist


def sync_all_workflow_schedules() -> Dict[str, Any]:
    """Scan all workflows on disk, reconcile APScheduler state.

    Call at server startup so existing workflows with trigger.schedule get
    their jobs registered.
    """
    sched = _scheduler()
    if not sched:
        return {"status": "skipped", "reason": "APScheduler not available"}

    stats = {"registered": 0, "removed": 0, "skipped": 0, "errors": 0}
    base = _workflows_base()
    if not base.exists():
        return {"status": "ok", **stats}

    seen_job_ids = set()
    for scope in ("system", "department", "personal"):
        d = base / scope
        if not d.exists():
            continue
        for wf_path in d.rglob("*.json"):
            try:
                wf = json.loads(wf_path.read_text(encoding="utf-8"))
                wf_id = wf.get("workflow_id") or wf.get("id")
                if not wf_id:
                    continue
                res = refresh_workflow_schedule(wf_id)
                if res.get("status") == "registered":
                    stats["registered"] += 1
                    seen_job_ids.add(_job_id(wf_id))
                elif res.get("status") in ("removed", "noop", "skipped"):
                    stats["skipped"] += 1
                elif res.get("status") == "error":
                    stats["errors"] += 1
            except Exception as e:
                logger.debug(f"[WFScheduler] Sync failed for {wf_path}: {e}")
                stats["errors"] += 1

    # Remove orphan jobs (APScheduler has a wf_cron_XXX job but no matching
    # workflow file on disk anymore — e.g. file deleted while server was down)
    try:
        for job in list(sched.get_jobs()):
            if job.id.startswith("wf_cron_") and job.id not in seen_job_ids:
                sched.remove_job(job.id)
                stats["removed"] += 1
                logger.info(f"[WFScheduler] Removed orphan job '{job.id}'")
    except Exception as e:
        logger.debug(f"[WFScheduler] Orphan cleanup failed: {e}")

    logger.info(
        f"[WFScheduler] Startup sync: registered={stats['registered']} "
        f"removed={stats['removed']} skipped={stats['skipped']} errors={stats['errors']}"
    )
    return {"status": "ok", **stats}
