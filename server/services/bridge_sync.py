"""Cross-channel sync bridge (Web ↔ LINE).

Implements:
- Web → LINE push of both user input and assistant reply.
- Group gating: only push to known groups where bot exists AND active within window.
- Loop prevention: mark pushed messages with a hidden tag; ignore them on LINE webhook.
- Throttle: per-session cooldown.

This is a minimal, safe MVP. Extend with persistence/redis if needed.
"""

from __future__ import annotations

import json
import time
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


BRIDGE_TAG_PREFIX = "[[bridge:"


def make_bridge_tag(session_id: str, payload: str) -> str:
    """Legacy alias for web-tag."""
    return make_web_bridge_tag(session_id, payload)


def make_web_bridge_tag(session_id: str, payload: str) -> str:
    h = hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"[[bridge:web:{session_id}:{h}]]"


def make_line_bridge_tag(session_id: str, payload: str) -> str:
    h = hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"[[bridge:line:{session_id}:{h}]]"


def has_bridge_tag(text: str) -> bool:
    return BRIDGE_TAG_PREFIX in (text or "")


def strip_bridge_tag(text: str) -> str:
    t = text or ""
    # naive strip: remove any [[bridge:...]] token
    import re
    return re.sub(r"\[\[bridge:[^\]]+\]\]", "", t).strip()


@dataclass
class GroupInfo:
    group_id: str
    last_active_at: int
    push_capable: bool


class BridgeState:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.path = self.project_root / "workspace" / "bridge" / "bridge_state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self.known_groups = {}  # group_id -> GroupInfo dict
        self.cooldowns = {}  # session_id -> last_push_ts
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            kg = data.get("known_groups") or {}
            self.known_groups = kg
        except Exception:
            self.known_groups = {}

    def _save(self):
        try:
            data = {"known_groups": self.known_groups}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    def mark_group_active(self, group_id: str):
        now = int(time.time())
        info = self.known_groups.get(group_id) or {"group_id": group_id, "last_active_at": 0, "push_capable": True}
        info["last_active_at"] = now
        self.known_groups[group_id] = info
        self._save()

    def set_group_push_capable(self, group_id: str, ok: bool):
        info = self.known_groups.get(group_id) or {"group_id": group_id, "last_active_at": int(time.time()), "push_capable": True}
        info["push_capable"] = bool(ok)
        self.known_groups[group_id] = info
        self._save()

    def group_can_push(self, group_id: str, active_window_seconds: int = 600) -> bool:
        info = self.known_groups.get(group_id)
        if not info:
            return False
        if not info.get("push_capable", True):
            return False
        last_active = int(info.get("last_active_at") or 0)
        return (int(time.time()) - last_active) <= active_window_seconds

    def throttle_ok(self, session_id: str, cooldown_seconds: int = 5) -> bool:
        now = time.time()
        last = float(self.cooldowns.get(session_id) or 0)
        if now - last < cooldown_seconds:
            return False
        self.cooldowns[session_id] = now
        return True


_state_singleton: Optional[BridgeState] = None


def get_bridge_state(project_root: Path) -> BridgeState:
    global _state_singleton
    if _state_singleton is None:
        _state_singleton = BridgeState(project_root)
    return _state_singleton


def should_sync_session(session_id: str) -> bool:
    # Default enabled. Future: per-user settings.
    return True


def session_target_from_session_id(session_id: str) -> tuple[str, str]:
    """Return (kind, native_id). kind in {user, group, room, other}."""
    s = (session_id or "").strip()
    if s.startswith("line_group_"):
        return "group", s[len("line_group_"):]
    if s.startswith("line_room_"):
        return "room", s[len("line_room_"):]
    if s.startswith("line_"):
        return "user", s[len("line_"):]
    return "other", s
