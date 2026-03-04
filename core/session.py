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
        """Legacy: Persist raw conversation to MEMORY.md. Use flush_with_llm_summary() for semantic logging."""
        self.flush_with_llm_summary(session_id, llm_callable=None)

    def flush_with_llm_summary(self, session_id: str, llm_callable=None):
        """
        D-07 (Updated): Flush session to MEMORY.md with LLM-generated semantic summary
        and citation grounding.

        Args:
            session_id:    Session to persist.
            llm_callable:  Optional callable(prompt: str) -> str for LLM summarisation.
                           If None or fails, a turn-count placeholder is written instead.
        """
        import re
        history = self._conversations.get(session_id, [])
        chat_msgs = [m for m in history if m.get("role") in ("user", "assistant")]
        if not chat_msgs:
            return

        # 1. Extract citation tags from all assistant turns
        all_citations = []
        for msg in chat_msgs:
            if msg.get("role") == "assistant":
                found = re.findall(
                    r'\[\s*(.+?\.[a-zA-Z0-9]+)(?:#chunk_(\d+))?\s*:\s*(.+?)\s*\]',
                    msg.get("content", "")
                )
                all_citations.extend(found)

        # 2. Generate LLM semantic summary
        summary_text = None
        if llm_callable and len(chat_msgs) >= 2:
            summary_prompt = (
                "請以 2-3 句話（繁體中文）摘要以下對話的核心要點，"
                "包含：主要討論主題、提及的具體名詞或數據、達成的結論。"
                "禁止包含問候語或無意義填充詞。\n\n"
            )
            for m in chat_msgs[-20:]:  # Last 20 messages to stay within token budget
                content_preview = str(m.get("content", ""))[:300]
                summary_prompt += f"[{m['role']}]: {content_preview}\n"
            try:
                summary_text = llm_callable(summary_prompt)
            except Exception as e:
                logger.warning(f"LLM summary generation failed for {session_id}: {e}")

        if not summary_text:
            turn_count = len(chat_msgs) // 2
            summary_text = f"對話共 {turn_count} 輪（語義摘要未生成）"

        # 3. Build and write MEMORY.md entry
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"\n## Session: {session_id} — {timestamp}\n",
            f"**摘要**: {summary_text}\n\n",
        ]

        # Append citation grounding block if documents were referenced
        if all_citations:
            unique_citations = {(fn, ci, sn) for fn, ci, sn in all_citations}
            lines.append("**來源文件**:\n")
            for filename, chunk_idx, snippet in sorted(unique_citations):
                offset = f"#chunk_{chunk_idx}" if chunk_idx else ""
                snip = snippet.strip()[:60] + "..." if len(snippet.strip()) > 60 else snippet.strip()
                lines.append(f"- `{filename}{offset}` — {snip}\n")
            lines.append("\n")

        lines.append("---\n\n")

        try:
            with open(self.memory_file, "a", encoding="utf-8") as f:
                f.writelines(lines)
            logger.info(f"Session {session_id} flushed with LLM summary to MEMORY.md")
        except Exception as e:
            logger.error(f"Failed to write session memory for {session_id}: {e}")

    def flush_all_sessions(self, llm_callable=None):
        """
        D-07: Flush ALL active in-memory sessions to MEMORY.md.
        Called on server shutdown to prevent data loss.
        """
        session_ids = list(self._conversations.keys())
        for sid in session_ids:
            self.flush_with_llm_summary(sid, llm_callable)
        logger.info(f"flush_all_sessions: persisted {len(session_ids)} sessions to MEMORY.md")

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
