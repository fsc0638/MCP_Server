"""Phase 1-3: Low-cost session summarizer with cache.

Goals:
- Avoid re-sending long history / repeated summarization each turn.
- Deterministic by default (no external API).
- Cache per session_id under workspace/sessions/{session_id}_summary.json

Summary is meant for prompt injection and quick context, not archival.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class SessionSummary:
    session_id: str
    updated_at: str
    message_count: int
    summary: str


class SessionSummarizer:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)
        self.sessions_dir = self.project_root / "workspace" / "sessions"

    def _summary_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}_summary.json"

    def _load_messages(self, session_id: str) -> List[Dict[str, Any]]:
        p = self.sessions_dir / f"{session_id}.json"
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    def load_summary(self, session_id: str) -> Dict[str, Any]:
        p = self._summary_path(session_id)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _deterministic_summary(self, messages: List[Dict[str, Any]], limit_msgs: int = 12) -> str:
        # Take the last N user/assistant messages, compress to a few bullets.
        ua = [m for m in messages if m.get("role") in ("user", "assistant")]
        tail = ua[-limit_msgs:]

        bullets = []
        for m in tail:
            role = m.get("role")
            content = (m.get("content") or "").strip().replace("\n", " ")
            if not content:
                continue
            content = content[:160] + ("…" if len(content) > 160 else "")
            bullets.append(f"- {role}: {content}")
        if not bullets:
            return ""
        return "\n".join(bullets)

    def maybe_update(self, session_id: str, min_new_messages: int = 6) -> Dict[str, Any]:
        messages = self._load_messages(session_id)
        msg_count = len(messages)

        prev = self.load_summary(session_id)
        prev_count = int(prev.get("message_count") or 0)

        # Only rebuild if enough new messages accumulated
        if msg_count - prev_count < min_new_messages and prev.get("summary"):
            return prev

        summary = self._deterministic_summary(messages)
        out = {
            "session_id": session_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "message_count": msg_count,
            "summary": summary,
        }

        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._summary_path(session_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._summary_path(session_id))
        return out


def render_session_summary_injection(summary_obj: Dict[str, Any], max_chars: int = 900) -> str:
    s = (summary_obj.get("summary") or "").strip()
    if not s:
        return ""
    text = "\n【對話摘要（快取）】\n" + s + "\n"
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n(…已截斷)\n"
    return text
