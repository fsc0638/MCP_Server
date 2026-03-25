"""
Pending State Manager — Human-in-the-Loop for LINE Bot

Manages cross-message pending states that allow:
1. Tool approval: "requires_approval" → user confirms/rejects in LINE
2. Choice proposals: AI proposes A/B/C options → user picks one in LINE

States are persisted to disk (JSON) so they survive server restarts
and have a configurable TTL (default 10 minutes).
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# ── Choice parsing helpers ───────────────────────────────────────────────────

# Regex to match [CHOICES] ... [/CHOICES] block in LLM output
_CHOICES_BLOCK_RE = re.compile(
    r"\[CHOICES\]\s*\n(.*?)\n\s*\[/CHOICES\]",
    re.DOTALL,
)

# Each option line: A. description / B. description / C. description ...
_OPTION_LINE_RE = re.compile(
    r"^([A-Z])[.)]\s*(.+)$", re.MULTILINE,
)

# User input patterns for selecting a choice: "A", "a", "選A", "選 A", "方案A"
_CHOICE_INPUT_RE = re.compile(
    r"^(?:選|方案|option|選擇)?\s*([A-Za-z])\s*[.。)）]?\s*$",
    re.IGNORECASE,
)


def extract_choices(llm_output: str) -> Optional[Tuple[str, List[Dict[str, str]]]]:
    """
    Extract a [CHOICES]...[/CHOICES] block from LLM output.

    Returns:
        (preamble_text, [{"key": "A", "text": "..."}, ...])
        or None if no choices block is found.

    The preamble_text is everything BEFORE the [CHOICES] block,
    which is the AI's explanation/analysis leading to the proposal.
    """
    match = _CHOICES_BLOCK_RE.search(llm_output)
    if not match:
        return None

    block_content = match.group(1)
    options = []
    for opt_match in _OPTION_LINE_RE.finditer(block_content):
        options.append({
            "key": opt_match.group(1).upper(),
            "text": opt_match.group(2).strip(),
        })

    if len(options) < 2:
        # Need at least 2 options for a meaningful choice
        return None

    # Preamble: everything before the [CHOICES] marker
    preamble = llm_output[:match.start()].strip()

    return preamble, options


def parse_choice(user_input: str, valid_keys: List[str]) -> Optional[str]:
    """
    Parse user input as a choice selection.

    Args:
        user_input: Raw user text (e.g. "A", "選B", "方案 C")
        valid_keys: List of valid option keys (e.g. ["A", "B", "C"])

    Returns:
        The uppercase key (e.g. "A") if matched, or None.
    """
    cleaned = user_input.strip()

    # Direct key match: "A", "B", "C"
    if cleaned.upper() in valid_keys:
        return cleaned.upper()

    # Pattern match: "選A", "方案B", "option C"
    m = _CHOICE_INPUT_RE.match(cleaned)
    if m:
        key = m.group(1).upper()
        if key in valid_keys:
            return key

    return None


def format_choices_for_line(preamble: str, options: List[Dict[str, str]]) -> str:
    """
    Format a choices proposal into a LINE-friendly message.

    Args:
        preamble: AI's explanation text before the options.
        options: List of {"key": "A", "text": "description"} dicts.

    Returns:
        Formatted string ready to send to LINE user.
    """
    lines = []
    if preamble:
        lines.append(preamble)
        lines.append("")

    lines.append("📋 請選擇方案：")
    lines.append("")
    for opt in options:
        lines.append(f"  {opt['key']}. {opt['text']}")

    lines.append("")
    lines.append("直接回覆選項代碼即可（如「A」或「選B」）")

    return "\n".join(lines)
