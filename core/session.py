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
    D-07: Unified session manager for both Web Console and CLI.
    Manages conversation history, cleanup, and MEMORY.md persistence.
    """

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.memory_dir = self.project_root / "memory"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.temp_dir = self.project_root / "temp"
        self.active_sessions: Dict[str, Dict[str, Any]] = {}

        # D-07: Conversation history store (session_id → list of {role, content})
        self._conversations: Dict[str, list] = {}

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

    # ─── Conversation History (D-07: for Web Console) ─────────────────────────

    def get_or_create_conversation(self, session_id: str, system_prompt: str = "") -> list:
        """Get or create a conversation history list for a session."""
        if session_id not in self._conversations:
            history = []
            if system_prompt:
                history.append({"role": "system", "content": system_prompt})
            self._conversations[session_id] = history
        return self._conversations[session_id]

    def append_message(self, session_id: str, role: str, content: str):
        """Append a message to a session's conversation history with auto-compression trigger."""
        import re
        history = self._conversations.get(session_id, [])
        history.append({"role": role, "content": content})
        
        # Sprint 2: 記憶持久化：攔截引用標籤並同步寫入 MEMORY.md
        # Sprint 2/4: Memory persistence with Chunk Offsets for Vector RAG
        if role == "assistant" or role == "model":
            # Match formats like: [filename#chunk_0: snippet] or [filename: snippet]
            citations = re.findall(r'\[\s*(.+?\.[a-zA-Z0-9]+)(?:#chunk_(\d+))?\s*:\s*(.+?)\s*\]', content)
            if citations:
                unique_citations = set(citations)
                for filename, chunk_idx, snippet in unique_citations:
                    snip = snippet.strip()[:60] + "..." if len(snippet.strip()) > 60 else snippet.strip()
                    offset_str = f" (Offset: chunk_{chunk_idx})" if chunk_idx else ""
                    msg = f"Citation Grounding: Agent 記憶了文獻來源 `{filename}`{offset_str} (內容: {snip})"
                    self._log_compression_event(session_id, msg)
                    logger.info(f"Persisted citation memory: {filename}{offset_str}")

        self._check_and_compress(session_id)

    def _check_and_compress(self, session_id: str):
        """
        Adaptive Memory Compression Framework.
        Monitors conversation length (estimated by rounds).
        Triggers summarization framework when approaching limits.
        """
        history = self._conversations.get(session_id, [])
        
        # Threshold: > 40 messages (20 rounds)
        MAX_MESSAGES = 40
        if len(history) > MAX_MESSAGES:
            logger.info(f"Session {session_id} exceeded {MAX_MESSAGES} messages. Triggering adaptive compression.")
            self._compress_history(session_id)

    def _compress_history(self, session_id: str):
        """
        Performs the compression. Prepared for LLM-based abstractive summarization.
        Currently retains global system prompts, compresses the older 50% into a summary token,
        and keeps the most recent 50% intact.
        """
        history = self._conversations.get(session_id, [])
        if len(history) <= 4:
            return

        system_msgs = [m for m in history if m["role"] == "system"]
        chat_msgs = [m for m in history if m["role"] != "system"]

        # 50% division
        midpoint = max(1, len(chat_msgs) // 2)
        old_msgs = chat_msgs[:midpoint]
        new_msgs = chat_msgs[midpoint:]

        # FUTURE(LLM Summarization): Pass `old_msgs` to an adapter for dense summarization.
        summary_content = f"[System Memory: Previously discussed {len(old_msgs)} messages. Context compressed to preserve token head room.]"
        
        compressed_history = system_msgs + [{"role": "system", "content": summary_content}] + new_msgs
        self._conversations[session_id] = compressed_history
        
        # Flush the summary node to persistent MEMORY.md
        self._log_compression_event(session_id, summary_content)

    def _log_compression_event(self, session_id: str, summary: str):
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lines = [
                f"\n## Session {session_id} (Memory Compressed) — {timestamp}\n",
                f"**Engine**: {summary}\n\n"
            ]
            with open(self.memory_file, "a", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            logger.error(f"Failed to log compression event: {e}")

    def flush_conversation_to_memory(self, session_id: str):
        """Persist a web session's conversation history to MEMORY.md."""
        history = self._conversations.get(session_id, [])
        if len(history) <= 1:  # Only system msg or empty
            return
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lines = [f"\n## Session: {session_id} — {timestamp}\n"]
            for msg in history:
                if msg["role"] == "system":
                    continue
                role_label = "👤 User" if msg["role"] == "user" else "🤖 Assistant"
                lines.append(f"**{role_label}**: {msg['content']}\n\n")

            with open(self.memory_file, "a", encoding="utf-8") as f:
                f.writelines(lines)
            logger.info(f"Session {session_id} conversation flushed to MEMORY.md")
        except Exception as e:
            logger.error(f"Failed to flush conversation to memory: {e}")

    def clear_conversation(self, session_id: str):
        """Clear a conversation session (flush first, then remove)."""
        self.flush_conversation_to_memory(session_id)
        self._conversations.pop(session_id, None)

    # ─── CLI Session Lifecycle ────────────────────────────────────────────────

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
