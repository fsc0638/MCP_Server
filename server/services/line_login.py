"""LINE Login (Web) service helpers.

Phase 1 implementation notes:
- Uses LINE Login v2.1 Authorization Code Flow.
- Verifies id_token via LINE's verify endpoint (recommended) to avoid JWT/JWKS dependencies.
- Stores state->nonce mapping in-memory (dev). For production, switch to Redis/DB.

Docs:
- https://developers.line.biz/en/docs/line-login/integrate-line-login/
- https://developers.line.biz/en/reference/line-login/#verify-id-token
"""

import os
import time
import secrets
import logging
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx

logger = logging.getLogger("MCP_Server.LineLogin")


class _StateStore:
    def __init__(self):
        # state -> (nonce, expires_at)
        self._store: Dict[str, Tuple[str, float]] = {}

    def put(self, state: str, nonce: str, ttl_seconds: int = 600) -> None:
        self._store[state] = (nonce, time.time() + ttl_seconds)

    def pop(self, state: str) -> Optional[str]:
        item = self._store.pop(state, None)
        if not item:
            return None
        nonce, exp = item
        if time.time() > exp:
            return None
        return nonce


_state_store = _StateStore()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


def generate_state_nonce(ttl_seconds: int = 600) -> Tuple[str, str]:
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    _state_store.put(state, nonce, ttl_seconds=ttl_seconds)
    return state, nonce


def build_authorize_url(state: str, nonce: str) -> str:
    client_id = _env("LINE_LOGIN_CHANNEL_ID")
    callback = _env("LINE_LOGIN_CALLBACK_URL")
    scope = _env("LINE_LOGIN_SCOPE", "openid profile")

    if not client_id or not callback:
        raise ValueError("Missing LINE_LOGIN_CHANNEL_ID or LINE_LOGIN_CALLBACK_URL")

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": callback,
        "scope": scope,
        "state": state,
        "nonce": nonce,
    }
    return "https://access.line.me/oauth2/v2.1/authorize?" + urlencode(params)


def exchange_code_for_tokens(code: str) -> dict:
    client_id = _env("LINE_LOGIN_CHANNEL_ID")
    client_secret = _env("LINE_LOGIN_CHANNEL_SECRET")
    callback = _env("LINE_LOGIN_CALLBACK_URL")

    if not client_id or not client_secret or not callback:
        raise ValueError("Missing LINE_LOGIN_CHANNEL_ID/SECRET/CALLBACK_URL")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    with httpx.Client(timeout=10) as client:
        r = client.post("https://api.line.me/oauth2/v2.1/token", data=data)
        if r.status_code != 200:
            logger.warning("[LineLogin] token exchange failed: %s %s", r.status_code, r.text)
            raise ValueError(f"Token exchange failed: HTTP {r.status_code}")
        return r.json()


def verify_id_token(id_token: str, nonce: str) -> dict:
    """Verify id_token via LINE verify endpoint.

    Returns decoded payload JSON (contains sub, name, picture, email if scope allowed).
    """
    client_id = _env("LINE_LOGIN_CHANNEL_ID")
    if not client_id:
        raise ValueError("Missing LINE_LOGIN_CHANNEL_ID")

    data = {
        "id_token": id_token,
        "client_id": client_id,
        "nonce": nonce,
    }

    with httpx.Client(timeout=10) as client:
        r = client.post("https://api.line.me/oauth2/v2.1/verify", data=data)
        if r.status_code != 200:
            logger.warning("[LineLogin] id_token verify failed: %s %s", r.status_code, r.text)
            raise ValueError(f"ID token verify failed: HTTP {r.status_code}")
        return r.json()


def consume_callback(code: str, state: str) -> dict:
    """Full callback handling: validate state, exchange tokens, verify id_token, return user dict."""
    if not state:
        raise ValueError("Missing state")
    if not code:
        raise ValueError("Missing code")

    nonce = _state_store.pop(state)
    if not nonce:
        raise ValueError("Invalid or expired state")

    token_payload = exchange_code_for_tokens(code)
    id_token = token_payload.get("id_token")
    if not id_token:
        raise ValueError("No id_token in token response")

    id_payload = verify_id_token(id_token=id_token, nonce=nonce)

    sub = (id_payload.get("sub") or "").strip()
    if not sub:
        raise ValueError("No sub in verified id_token")

    # canonical web session id
    session_id = f"line_{sub}"

    user = {
        "id": session_id,
        "name": id_payload.get("name") or "LINE User",
        "picture": id_payload.get("picture"),
        "email": id_payload.get("email"),
        "provider": "line",
        # Keep raw for debugging if needed (do not expose in UI by default)
        "_line": {
            "sub": sub,
        },
    }
    return user
