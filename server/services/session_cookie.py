"""Signed cookie helpers for lightweight auth.

This avoids trusting a raw cookie value like "line_U..." which can be forged.

Format:
  v1.<value>.<ts>.<sig>

Where sig = HMAC_SHA256(secret, f"{value}.{ts}")

Set COOKIE_SIGNING_SECRET in env for production.
Dev fallback uses LINE_LOGIN_CHANNEL_SECRET (better than nothing but still not ideal).
"""

import os
import hmac
import time
import base64
import hashlib
from typing import Optional


def _get_secret() -> bytes:
    s = (os.environ.get("COOKIE_SIGNING_SECRET") or "").strip()
    if not s:
        # Fallback (dev): reuse LINE login secret so we don't require extra config.
        s = (os.environ.get("LINE_LOGIN_CHANNEL_SECRET") or "").strip()
    if not s:
        # Last resort: unstable process secret (will break across restarts)
        s = os.urandom(32).hex()
    return s.encode("utf-8")


def sign_session_cookie(value: str, ts: Optional[int] = None) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("Missing cookie value")

    if ts is None:
        ts = int(time.time())

    msg = f"{value}.{ts}".encode("utf-8")
    sig = hmac.new(_get_secret(), msg, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"v1.{value}.{ts}.{sig_b64}"


def verify_session_cookie(token: str, max_age_seconds: int = 60 * 60 * 24 * 7) -> Optional[str]:
    token = (token or "").strip()
    if not token:
        return None

    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        return None

    value, ts_s, sig_b64 = parts[1], parts[2], parts[3]
    try:
        ts = int(ts_s)
    except Exception:
        return None

    now = int(time.time())
    if max_age_seconds and (ts > now + 60 or now - ts > max_age_seconds):
        return None

    msg = f"{value}.{ts}".encode("utf-8")
    expected = hmac.new(_get_secret(), msg, hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).decode("utf-8").rstrip("=")

    if not hmac.compare_digest(expected_b64, sig_b64):
        return None

    return value
