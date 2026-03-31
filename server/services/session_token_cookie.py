"""Signed cookie for opaque session tokens (server-side auth sessions).

Cookie stores:
  v1.<token>.<ts>.<sig>

Where sig = HMAC_SHA256(secret, f"{token}.{ts}")

Use COOKIE_SIGNING_SECRET in env.
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
        # Dev fallback: keep working without extra env. Still recommended to set.
        s = (os.environ.get("LINE_LOGIN_CHANNEL_SECRET") or "").strip()
    if not s:
        s = os.urandom(32).hex()
    return s.encode("utf-8")


def sign_token(token: str, ts: Optional[int] = None) -> str:
    token = (token or "").strip()
    if not token:
        raise ValueError("Missing token")
    if ts is None:
        ts = int(time.time())
    msg = f"{token}.{ts}".encode("utf-8")
    sig = hmac.new(_get_secret(), msg, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"v1.{token}.{ts}.{sig_b64}"


def verify_token(signed: str, max_age_seconds: int = 60 * 60 * 24 * 7) -> Optional[str]:
    signed = (signed or "").strip()
    if not signed:
        return None
    parts = signed.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        return None
    token, ts_s, sig_b64 = parts[1], parts[2], parts[3]
    try:
        ts = int(ts_s)
    except Exception:
        return None
    now = int(time.time())
    if max_age_seconds and (ts > now + 60 or now - ts > max_age_seconds):
        return None
    msg = f"{token}.{ts}".encode("utf-8")
    expected = hmac.new(_get_secret(), msg, hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).decode("utf-8").rstrip("=")
    if not hmac.compare_digest(expected_b64, sig_b64):
        return None
    return token
