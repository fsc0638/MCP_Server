"""
Session Manager (Phase 5)
Manages session lifecycle, cleanup jobs, and MEMORY.md persistence.
"""
import os
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("MCP_Server.Session")


class SessionManager:
    """
    Manages individual user sessions with cleanup and memory sync.
    """

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.memory_dir = self.project_root / "memory"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.temp_dir = self.project_root / "temp"
        self.active_sessions: Dict[str, Dict[str, Any]] = {}

        # Ensure directories exist
        self.memory_dir.mkdir(exist_ok=True)
        self.temp_dir.mkdir(exist_ok=True)

        # Initialize MEMORY.md if not exists
        if not self.memory_file.exists():
            self.memory_file.write_text(
                "# MCP Server — Session Memory\n\n"
                "This file records summaries of past sessions for Agent continuity.\n\n"
                "---\n\n",
                encoding="utf-8"
            )

    def create_session(self, user_id: str = "default") -> str:
        """Creates a new session and returns the session ID."""
        session_id = str(uuid.uuid4())[:8]
        session = {
            "id": session_id,
            "user_id": user_id,
            "started_at": datetime.now().isoformat(),
            "tool_calls": [],
            "temp_files": []
        }
        self.active_sessions[session_id] = session
        logger.info(f"Session created: {session_id} for user: {user_id}")
        return session_id

    def record_tool_call(self, session_id: str, tool_name: str, status: str, summary: str = ""):
        """Records a tool call within a session for memory sync."""
        session = self.active_sessions.get(session_id)
        if session:
            session["tool_calls"].append({
                "tool": tool_name,
                "status": status,
                "summary": summary,
                "timestamp": datetime.now().isoformat()
            })

    def register_temp_file(self, session_id: str, file_path: str):
        """Registers a temporary file for cleanup on session end."""
        session = self.active_sessions.get(session_id)
        if session:
            session["temp_files"].append(file_path)

    def end_session(self, session_id: str, summary: str = ""):
        """
        Ends a session:
        1. Cleanup temporary files
        2. Sync session summary to MEMORY.md
        """
        session = self.active_sessions.get(session_id)
        if not session:
            logger.warning(f"Session {session_id} not found")
            return

        # 1. Cleanup temporary files
        cleaned = 0
        for temp_file in session.get("temp_files", []):
            try:
                fp = Path(temp_file)
                if fp.exists():
                    fp.unlink()
                    cleaned += 1
            except Exception as e:
                logger.error(f"Failed to clean temp file {temp_file}: {e}")

        logger.info(f"Session {session_id}: cleaned {cleaned} temp files")

        # 2. Sync to MEMORY.md
        self._sync_memory(session, summary)

        # 3. Remove from active sessions
        del self.active_sessions[session_id]
        logger.info(f"Session {session_id} ended")

    def _sync_memory(self, session: Dict[str, Any], summary: str):
        """Appends session summary to MEMORY.md."""
        try:
            tool_count = len(session.get("tool_calls", []))
            tools_used = set(tc["tool"] for tc in session.get("tool_calls", []))

            entry = (
                f"## Session: {session['id']}\n"
                f"- **Date**: {session['started_at']}\n"
                f"- **User**: {session['user_id']}\n"
                f"- **Tool Calls**: {tool_count}\n"
                f"- **Tools Used**: {', '.join(tools_used) if tools_used else 'None'}\n"
                f"- **Summary**: {summary or 'No summary provided'}\n\n"
                f"---\n\n"
            )

            with open(self.memory_file, "a", encoding="utf-8") as f:
                f.write(entry)

            logger.info(f"Memory synced for session {session['id']}")

        except Exception as e:
            logger.error(f"Failed to sync memory: {e}")

    def cleanup_all_temp(self):
        """Emergency cleanup: removes all files in the temp directory."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
            self.temp_dir.mkdir()
            logger.info("Emergency cleanup: all temp files removed")
