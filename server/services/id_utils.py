"""ID helpers to keep canonical session keys consistent and safe.

Canonical rules (Phase 0):
- canonical_user_id = id_token.sub (OIDC)
- canonical session_id for LINE identities: line_{sub}

Also provides safe normalization for filesystem paths.
"""

import re

_SESSION_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,128}$")


def canonical_line_session_id(sub: str) -> str:
    sub = (sub or "").strip()
    if not sub:
        raise ValueError("Missing LINE sub")
    # LINE userId/sub are typically like Uxxxxxxxx..., but don't assume only U.
    return f"line_{sub}"


def is_canonical_line_session_id(session_id: str) -> bool:
    s = (session_id or "").strip()
    return s.startswith("line_") and len(s) > 5


def is_anonymous_web_session_id(session_id: str) -> bool:
    s = (session_id or "").strip()
    return s.startswith("web-")


def safe_bucket_key(key: str) -> str:
    """Return a sanitized key safe for directory/file naming."""
    k = (key or "").strip()
    if not k:
        return "unknown"
    # Replace disallowed chars with underscore
    k = re.sub(r"[^A-Za-z0-9_\-:.]", "_", k)
    return k[:128]


def validate_session_id(session_id: str) -> str:
    s = (session_id or "").strip()
    if not s:
        raise ValueError("Missing session_id")
    if not _SESSION_RE.match(s):
        raise ValueError("Invalid session_id")
    return s
