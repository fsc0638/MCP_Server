"""Task registry for web chat background jobs and approvals."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("MCP_Server.TaskRegistry")


class TaskRegistry:
    """Persist and manage task-scoped chat execution state."""

    ACTIVE_STATUSES = {"running", "tool_call", "requires_approval", "approved"}
    TERMINAL_STATUSES = {"completed", "error", "rejected"}

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.tasks_dir = self.project_root / "workspace" / "chat_tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._load_existing_tasks()

    def _task_path(self, task_id: str) -> Path:
        safe_id = str(task_id).replace("/", "_").replace("\\", "_")
        return self.tasks_dir / f"{safe_id}.json"

    def _load_existing_tasks(self) -> None:
        with self._lock:
            for path in self.tasks_dir.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning(f"[TaskRegistry] Failed to load task file {path.name}: {exc}")
                    continue

                task_id = str(payload.get("task_id") or "").strip()
                if not task_id:
                    continue
                self._tasks[task_id] = payload

    def _persist(self, task: Dict[str, Any]) -> None:
        self._task_path(task["task_id"]).write_text(
            json.dumps(task, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _touch(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task["updated_at"] = time.time()
        self._persist(task)
        return task

    def create_task(
        self,
        *,
        session_id: str,
        turn_id: str,
        provider: str,
        model: str,
        user_input: str,
    ) -> Dict[str, Any]:
        now = time.time()
        task = {
            "task_id": uuid.uuid4().hex,
            "session_id": session_id,
            "turn_id": turn_id,
            "provider": provider,
            "model": model,
            "user_input": user_input,
            "status": "running",
            "tool_name": "",
            "tool_message": "",
            "risk_description": "",
            "pending_args": {},
            "partial_text": "",
            "final_text": "",
            "error": "",
            "assistant_message_persisted": False,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._tasks[task["task_id"]] = task
            self._persist(task)
        logger.info(
            "[TaskRegistry] Created task=%s session=%s provider=%s model=%s",
            task["task_id"],
            session_id,
            provider,
            model,
        )
        return dict(task)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def update_task(self, task_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.update(updates)
            return dict(self._touch(task))

    def append_partial_text(self, task_id: str, text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return self.get_task(task_id)
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task["partial_text"] = (task.get("partial_text") or "") + text
            if task.get("status") != "requires_approval":
                task["status"] = "running"
            return dict(self._touch(task))

    def mark_tool_call(self, task_id: str, tool_name: str, message: str = "") -> Optional[Dict[str, Any]]:
        return self.update_task(
            task_id,
            status="tool_call",
            tool_name=tool_name or "",
            tool_message=message or "",
        )

    def mark_requires_approval(
        self,
        task_id: str,
        *,
        tool_name: str,
        risk_description: str,
        pending_args: Optional[Dict[str, Any]] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        updates: Dict[str, Any] = {
            "status": "requires_approval",
            "tool_name": tool_name or "",
            "risk_description": risk_description or "",
            "pending_args": pending_args or {},
        }
        if provider:
            updates["provider"] = provider
        if model:
            updates["model"] = model
        return self.update_task(task_id, **updates)

    def mark_approved(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.update_task(task_id, status="approved", error="")

    def mark_completed(
        self,
        task_id: str,
        *,
        final_text: str,
        assistant_message_persisted: bool,
    ) -> Optional[Dict[str, Any]]:
        return self.update_task(
            task_id,
            status="completed",
            partial_text=final_text,
            final_text=final_text,
            error="",
            assistant_message_persisted=assistant_message_persisted,
        )

    def mark_error(self, task_id: str, message: str) -> Optional[Dict[str, Any]]:
        return self.update_task(task_id, status="error", error=message or "Unknown error")

    def mark_rejected(self, task_id: str, message: str = "") -> Optional[Dict[str, Any]]:
        return self.update_task(task_id, status="rejected", error=message or "")

    def list_tasks_for_session(
        self,
        session_id: str,
        *,
        include_terminal: bool = True,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            tasks = [
                dict(task)
                for task in self._tasks.values()
                if task.get("session_id") == session_id
                and (include_terminal or task.get("status") not in self.TERMINAL_STATUSES)
            ]
        tasks.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        if limit > 0:
            tasks = tasks[:limit]
        tasks.sort(key=lambda item: item.get("created_at", 0))
        return tasks

    def get_active_task_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        tasks = self.list_tasks_for_session(session_id, include_terminal=False, limit=1)
        return tasks[0] if tasks else None
