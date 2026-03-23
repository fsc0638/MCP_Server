"""
Session Manager (Phase 5)
Manages session lifecycle, cleanup jobs, and MEMORY.md persistence.
"""
import os
import uuid
import time
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

        # New: storage for session persistence
        self.sessions_dir = self.project_root / "workspace" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        # D-07: Conversation history store (session_id → list of {role, content})
        self._conversations: Dict[str, list] = {}
        
        # P-03: Responses API Memory Map (session_id → response.id)
        self._latest_response_ids: Dict[str, str] = {}

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
        """Get or create a conversation history list for a session. Loads from disk if available."""
        if session_id not in self._conversations:
            # 1. Try to load from JSON first
            history = self._load_conversation_from_disk(session_id)
            
            # 2. If not on disk, create new
            if history is None:
                history = []
                if system_prompt:
                    history.append({"role": "system", "content": system_prompt, "created_at": int(time.time())})
            
            self._conversations[session_id] = history
            
        return self._conversations[session_id]

    def _load_conversation_from_disk(self, session_id: str) -> Optional[list]:
        """Loads conversation history from JSON file."""
        import json
        json_path = self.sessions_dir / f"{session_id}.json"
        if json_path.exists():
            try:
                # Use file mtime as base for missing timestamps
                fallback_time = int(json_path.stat().st_mtime)
                with open(json_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
                    
                needs_save = False
                if isinstance(history, list):
                    for i, msg in enumerate(history):
                        if "created_at" not in msg:
                            # Heuristic: space out messages slightly before the file's last modified time
                            msg["created_at"] = fallback_time - (len(history) - 1 - i)
                            needs_save = True
                
                if needs_save:
                    # Save back with timestamps so they are "hard-locked"
                    self._conversations[session_id] = history
                    self._save_conversation_to_disk(session_id)
                
                return history
            except Exception as e:
                logger.error(f"Failed to load session {session_id} from disk: {e}")
        return None

    def _save_conversation_to_disk(self, session_id: str):
        """Saves conversation history to JSON file."""
        import json
        history = self._conversations.get(session_id)
        if history is not None:
            json_path = self.sessions_dir / f"{session_id}.json"
            try:
                # Use a background-safe approach (shadow write if needed, but simple open is fine for low concurrency)
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(history, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"Failed to save session {session_id} to disk: {e}")

    def reset_openai_state(self, session_id: str):
        """
        Clears the OpenAI stateful response ID while keeping message history.
        Ensures a fresh system prompt injection on the next turn.
        """
        if session_id in self._latest_response_ids:
            old_id = self._latest_response_ids.pop(session_id)
            logger.info(f"Reset OpenAI state for session {session_id} (Internal ID: {old_id})")

    def _update_system_prompt(self, session_id: str, new_system_prompt: str):
        """Update the system prompt for an existing session to keep dynamic info (like date) fresh."""
        history = self._conversations.get(session_id, [])
        if not history:
            return
            
        # Find the first system prompt and update it
        for i, msg in enumerate(history):
            if msg.get("role") == "system":
                history[i]["content"] = new_system_prompt
                return
                
        # If no system prompt exists, insert at the beginning
        history.insert(0, {"role": "system", "content": new_system_prompt, "created_at": int(time.time())})

    def append_message(self, session_id: str, role: str, content: str):
        """Append a message to a session's conversation history with auto-compression trigger."""
        import re
        history = self._conversations.get(session_id, [])
        history.append({"role": role, "content": content, "created_at": int(time.time())})
        
        # Persistent save to disk
        self._save_conversation_to_disk(session_id)
        
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

    def remove_chunk_entries(self, session_id: str, filename: str):
        """
        Remove intermediate chunk headers and their summaries from session history.
        Called after chunked file processing completes so that old chunk entries
        don't pollute future conversations (preventing LLM from pattern-matching
        on stale '三段摘要' text).

        Removes messages matching: [文件分段 N/M：filename] and their paired assistant responses.
        """
        import re
        history = self._conversations.get(session_id, [])
        if not history:
            return

        # Build pattern to match chunk headers for this specific file
        # e.g. "[文件分段 1/3：Groovenauts日經案例新聞.docx]"
        chunk_pattern = re.compile(r'^\[文件分段 \d+/\d+[：:]' + re.escape(filename) + r'\]$')

        cleaned = []
        skip_next_assistant = False
        removed_count = 0

        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if skip_next_assistant and role == "assistant":
                # This is the summary paired with a chunk header — skip it too
                skip_next_assistant = False
                removed_count += 1
                continue

            skip_next_assistant = False

            if role == "user" and chunk_pattern.match(content.strip()):
                # This is an intermediate chunk header — skip it
                skip_next_assistant = True  # Also skip the paired assistant summary
                removed_count += 1
                continue

            cleaned.append(msg)

        if removed_count > 0:
            self._conversations[session_id] = cleaned
            self._save_conversation_to_disk(session_id)
            logger.info(f"Removed {removed_count} chunk entries for '{filename}' from session {session_id}")

    def clear_conversation(self, session_id: str):
        """Clear a conversation session (flush first, then remove)."""
        self.flush_conversation_to_memory(session_id)
        self._conversations.pop(session_id, None)
        self._latest_response_ids.pop(session_id, None)

    # ─── New: Responses API Stateful Tracking (P-03) ──────────────────────────

    def set_latest_response_id(self, session_id: str, response_id: str):
        """Store the response ID from the latest OpenAI Responses API turn."""
        if response_id:
            self._latest_response_ids[session_id] = response_id
            
    def get_latest_response_id(self, session_id: str) -> Optional[str]:
        """Retrieve the response ID from the latest OpenAI Responses API turn."""
        return self._latest_response_ids.get(session_id)

    # ─── Per-session Metadata (Key-Value) ─────────────────────────────────────

    def set_metadata(self, session_id: str, key: str, value):
        """Store arbitrary key-value metadata scoped to a session."""
        if not hasattr(self, "_session_metadata"):
            self._session_metadata = {}
        self._session_metadata.setdefault(session_id, {})[key] = value

    def get_metadata(self, session_id: str, key: str, default=None):
        """Retrieve session-scoped metadata by key."""
        if not hasattr(self, "_session_metadata"):
            return default
        return self._session_metadata.get(session_id, {}).get(key, default)

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
