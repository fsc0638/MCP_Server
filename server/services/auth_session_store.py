"""Server-side session store for web authentication.

Goals:
- Do NOT trust client-provided identity (no raw user id cookie).
- Keep UI able to display: name / picture / user id.
- Simple in-memory store with short TTL (dev). For production, replace with Redis/DB.

Cookie holds only an opaque session token (signed).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class AuthSession:
    token: str
    user_id: str
    name: str
    picture: str
    created_at: int
    expires_at: int


class AuthSessionStore:
    """In-memory session store with disk persistence for dev (survives --reload)."""

    def __init__(self):
        self._sessions: Dict[str, AuthSession] = {}
        self._disk_path = self._get_disk_path()
        self._load_from_disk()

    @staticmethod
    def _get_disk_path():
        import os
        from pathlib import Path
        pr = os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))
        p = Path(pr) / "workspace" / ".auth_sessions.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _load_from_disk(self):
        """Load persisted sessions on startup."""
        import json
        if not self._disk_path.exists():
            return
        try:
            data = json.loads(self._disk_path.read_text(encoding="utf-8"))
            now = int(time.time())
            for token, s in data.items():
                if s.get("expires_at", 0) > now:
                    self._sessions[token] = AuthSession(**s)
        except Exception:
            pass

    def _persist_to_disk(self):
        """Write current sessions to disk."""
        import json
        data = {}
        now = int(time.time())
        for token, sess in self._sessions.items():
            if sess.expires_at > now:
                data[token] = {
                    "token": sess.token,
                    "user_id": sess.user_id,
                    "name": sess.name,
                    "picture": sess.picture,
                    "created_at": sess.created_at,
                    "expires_at": sess.expires_at,
                }
        try:
            self._disk_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def create(self, user_id: str, name: str = "", picture: str = "", ttl_seconds: int = 60 * 60 * 24 * 7) -> AuthSession:
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        sess = AuthSession(
            token=token,
            user_id=user_id,
            name=name or "",
            picture=picture or "",
            created_at=now,
            expires_at=now + ttl_seconds,
        )
        self._sessions[token] = sess
        self._persist_to_disk()
        return sess

    def get(self, token: str) -> Optional[AuthSession]:
        if not token:
            return None
        sess = self._sessions.get(token)
        if not sess:
            return None
        if int(time.time()) > sess.expires_at:
            self._sessions.pop(token, None)
            self._persist_to_disk()
            return None
        return sess

    def delete(self, token: str) -> None:
        if token:
            self._sessions.pop(token, None)
            self._persist_to_disk()


_store = AuthSessionStore()


def get_auth_session_store() -> AuthSessionStore:
    return _store
