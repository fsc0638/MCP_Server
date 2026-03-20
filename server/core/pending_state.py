"""
Pending State Manager — Human-in-the-Loop for LINE Bot

Manages cross-message pending states that allow:
1. Tool approval: "requires_approval" → user confirms/rejects in LINE
2. Future: Choice proposals (A/B/C options) before execution

States are persisted to disk (JSON) so they survive server restarts
and have a configurable TTL (default 10 minutes).
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("MCP_Server.PendingState")

# TTL for pending states (seconds)
DEFAULT_TTL = 600  # 10 minutes


class PendingStateManager:
    """
    Manages pending approval/choice states between LINE messages.

    Storage: workspace/sessions/pending_{chat_id}.json
    Each chat_id can have at most ONE pending state at a time.
    """

    def __init__(self, sessions_dir: str):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _pending_path(self, chat_id: str) -> Path:
        """Get the file path for a chat's pending state."""
        safe_id = chat_id.replace("/", "_").replace("\\", "_")
        return self.sessions_dir / f"pending_{safe_id}.json"

    def set_pending(
        self,
        chat_id: str,
        pending_type: str,
        data: Dict[str, Any],
        ttl: int = DEFAULT_TTL,
    ):
        """
        Save a pending state for a chat.

        Args:
            chat_id: LINE chat ID (user or group)
            pending_type: "approval" | "choice" (future)
            data: State-specific payload
            ttl: Time-to-live in seconds
        """
        now = time.time()
        state = {
            "type": pending_type,
            "created_at": now,
            "expires_at": now + ttl,
            **data,
        }
        path = self._pending_path(chat_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            logger.info(
                f"[PendingState] Saved {pending_type} for chat={chat_id}, "
                f"TTL={ttl}s, tool={data.get('tool_name', 'N/A')}"
            )
        except Exception as e:
            logger.error(f"[PendingState] Failed to save: {e}")

    def get_pending(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve the pending state for a chat.
        Returns None if no state exists or if it has expired.
        Expired states are auto-cleaned.
        """
        path = self._pending_path(chat_id)
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception as e:
            logger.error(f"[PendingState] Failed to read: {e}")
            return None

        # Check TTL
        if time.time() > state.get("expires_at", 0):
            logger.info(f"[PendingState] Expired for chat={chat_id}, auto-clearing")
            self.clear_pending(chat_id)
            return None

        return state

    def clear_pending(self, chat_id: str):
        """Remove the pending state for a chat."""
        path = self._pending_path(chat_id)
        if path.exists():
            try:
                path.unlink()
                logger.info(f"[PendingState] Cleared for chat={chat_id}")
            except Exception as e:
                logger.error(f"[PendingState] Failed to clear: {e}")

    def has_pending(self, chat_id: str) -> bool:
        """Check if a non-expired pending state exists."""
        return self.get_pending(chat_id) is not None


# ── Confirmation parsing helpers ──────────────────────────────────────────────

# Keywords that mean "approve / go ahead"
_APPROVE_KEYWORDS = {
    "確認", "確定", "好", "好的", "是", "執行", "同意", "ok", "yes", "y",
    "proceed", "approve", "go", "確認執行", "沒問題",
}

# Keywords that mean "reject / cancel"
_REJECT_KEYWORDS = {
    "取消", "不要", "否", "no", "n", "cancel", "reject", "算了", "不用",
    "放棄", "停止", "stop",
}


def parse_confirmation(user_input: str) -> Optional[str]:
    """
    Parse user input as a confirmation response.

    Returns:
        "approve"  — user confirmed
        "reject"   — user rejected
        None       — input is not a confirmation (treat as new message)
    """
    cleaned = user_input.strip().lower()

    # Exact match first (highest confidence)
    if cleaned in _APPROVE_KEYWORDS:
        return "approve"
    if cleaned in _REJECT_KEYWORDS:
        return "reject"

    # Fuzzy: check if input STARTS with a keyword (e.g. "確認，請執行")
    for kw in _APPROVE_KEYWORDS:
        if cleaned.startswith(kw):
            return "approve"
    for kw in _REJECT_KEYWORDS:
        if cleaned.startswith(kw):
            return "reject"

    return None
