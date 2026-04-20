"""Identity context resolver for session-scoped chat flows.

Design:
- `session_id` tracks chat/session state.
- `user_id` tracks the authenticated actor and permission scope.
- `workspace/users/{user_id}.json` remains the source of user context.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from server.services.employee_lookup import get_user_context

logger = logging.getLogger("MCP_Server.IdentityContext")


def _is_line_personal_session(session_id: str) -> bool:
    if not session_id.startswith("line_"):
        return False
    return not (session_id.startswith("line_group_") or session_id.startswith("line_room_"))


def resolve_identity_context(
    session_id: str,
    explicit_user_id: str = "",
    session_mgr=None,
    persist_binding: bool = True,
    allow_session_binding: bool = True,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Resolve `(user_id, user_context)` for the current turn.

    Resolution order:
    1) explicit_user_id (from request/auth)
    2) session metadata binding (`bound_user_id`) when allowed
    3) LINE personal session fallback (`session_id` itself)
    4) legacy fallback (`workspace/users/{session_id}.json`) for compatibility
    """
    sid = (session_id or "").strip() or "default"
    resolved_user_id = (explicit_user_id or "").strip()

    if not resolved_user_id and allow_session_binding and session_mgr is not None:
        try:
            resolved_user_id = str(session_mgr.get_metadata(sid, "bound_user_id", "") or "").strip()
        except Exception:
            resolved_user_id = ""

    if not resolved_user_id and _is_line_personal_session(sid):
        resolved_user_id = sid

    if persist_binding and resolved_user_id and session_mgr is not None:
        try:
            existing = str(session_mgr.get_metadata(sid, "bound_user_id", "") or "").strip()
            if existing != resolved_user_id:
                session_mgr.set_metadata(sid, "bound_user_id", resolved_user_id)
        except Exception as exc:
            logger.debug("Failed to persist bound_user_id for session=%s: %s", sid, exc)

    ctx = None
    if resolved_user_id:
        try:
            ctx = get_user_context(resolved_user_id)
        except Exception:
            ctx = None

    # Backward compatibility: older flows may have written by session_id.
    if ctx is None and sid and sid != resolved_user_id:
        try:
            legacy_ctx = get_user_context(sid)
            if legacy_ctx:
                ctx = legacy_ctx
                if not resolved_user_id:
                    resolved_user_id = str(legacy_ctx.get("user_id", "") or "").strip()
        except Exception:
            pass

    if ctx:
        merged = dict(ctx)
        if resolved_user_id:
            merged["user_id"] = resolved_user_id
        merged["session_id"] = sid
        return resolved_user_id, merged

    return resolved_user_id, None
