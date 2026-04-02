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
    def __init__(self):
        self._sessions: Dict[str, AuthSession] = {}

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
        return sess

    def get(self, token: str) -> Optional[AuthSession]:
        if not token:
            return None
        sess = self._sessions.get(token)
        if not sess:
            return None
        if int(time.time()) > sess.expires_at:
            self._sessions.pop(token, None)
            return None
        return sess

    def delete(self, token: str) -> None:
        if token:
            self._sessions.pop(token, None)


_store = AuthSessionStore()


def get_auth_session_store() -> AuthSessionStore:
    return _store
