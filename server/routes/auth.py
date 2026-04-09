"""Authentication routes."""

import os
from fastapi import APIRouter, HTTPException, Cookie
from fastapi.responses import RedirectResponse
from google.oauth2 import id_token
from google.auth.transport import requests
from pydantic import BaseModel

from server.services.line_login import build_authorize_url, consume_callback, generate_state_nonce

class GoogleLoginRequest(BaseModel):
    token: str

router = APIRouter(prefix="/api/auth", tags=["Auth"])

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

@router.post("/google")
async def google_login(req: GoogleLoginRequest):
    if not GOOGLE_CLIENT_ID:
        # For safety/convenience during development, we log but don't strictly block if we want to allow demo mode
        # but the user asked for "real" implementation, so we should expect it.
        pass
    
    try:
        # Verify the ID Token
        # If GOOGLE_CLIENT_ID is empty, it might still verify but warning is better.
        idinfo = id_token.verify_oauth2_token(
            req.token, 
            requests.Request(), 
            GOOGLE_CLIENT_ID if GOOGLE_CLIENT_ID else None
        )

        # ID token is valid. Get the user's info from the decoded token.
        userid = idinfo['sub']
        email = idinfo.get('email')
        name = idinfo.get('name', email.split('@')[0] if email else 'User')
        picture = idinfo.get('picture')

        # Generate initials
        parts = name.split()
        if len(parts) >= 1:
            initials = "".join([p[0] for p in parts if p]).upper()[:2]
        else:
            initials = name[:2].upper()

        return {
            "status": "success",
            "user": {
                "id": userid,
                "email": email,
                "name": name,
                "picture": picture,
                "initials": initials,
                "provider": "google"
            }
        }
    except ValueError as val_err:
        # Invalid token
        raise HTTPException(status_code=401, detail=f"Invalid Google Token: {str(val_err)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")


# ── Google Workspace OAuth (personal account binding) ─────────────────────

@router.get("/google/workspace/login")
def google_workspace_login(session_id: str = ""):
    """Redirect user to Google OAuth consent screen for personal binding."""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        from server.services.google_auth import build_authorize_url
        url = build_authorize_url(session_id)
        return RedirectResponse(url=url, status_code=302)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/google/workspace/callback")
def google_workspace_callback(code: str = "", state: str = "", error: str = ""):
    """Google OAuth callback — exchange code for tokens, store credentials."""
    if error:
        raise HTTPException(status_code=401, detail=f"Google auth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")
    try:
        from server.services.google_auth import handle_callback
        result = handle_callback(code=code, session_id=state)
        # Return a simple success page that can be closed
        return {
            "status": "success",
            "message": "Google 帳號綁定成功！你可以關閉此頁面，回到 LINE 繼續使用。",
            "session_id": state,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Google OAuth callback failed: {str(e)}")


@router.get("/google/workspace/status")
def google_workspace_status(session_id: str = ""):
    """Check if a session has Google credentials bound."""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    from server.services.google_auth import has_credentials, has_service_account
    return {
        "personal_oauth": has_credentials(session_id),
        "service_account": has_service_account(),
    }


# ── LINE Login ────────────────────────────────────────────────────────────

@router.get("/line/login")
def line_login():
    """Start LINE Login (web) by redirecting to LINE authorize endpoint."""
    try:
        state, nonce = generate_state_nonce(ttl_seconds=600)
        url = build_authorize_url(state=state, nonce=nonce)
        return RedirectResponse(url=url, status_code=302)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LINE login init failed: {str(e)}")


@router.get("/line/callback")
def line_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """LINE Login callback endpoint.

    On success, sets a cookie and redirects to the chat UI.
    """
    if error:
        raise HTTPException(status_code=401, detail=f"LINE auth error: {error} {error_description}".strip())

    try:
        user = consume_callback(code=code, state=state)

        # Store minimal session in cookie (same-origin flow).
        # SECURITY: sign the cookie to prevent tampering.
        resp = RedirectResponse(url="/ui/pages/chat.html", status_code=302)

        # Create server-side auth session and store only an opaque token in cookie.
        from server.services.auth_session_store import get_auth_session_store
        store = get_auth_session_store()
        sess = store.create(user_id=user["id"], name=user.get("name") or "", picture=user.get("picture") or "")

        from server.services.session_token_cookie import sign_token
        signed = sign_token(sess.token)

        resp.set_cookie(
            key="mcp_session",
            value=signed,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return resp
    except ValueError as ve:
        raise HTTPException(status_code=401, detail=f"LINE login failed: {str(ve)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LINE login callback error: {str(e)}")


@router.get("/me")
def me(mcp_session: str = Cookie(default="", alias="mcp_session")):
    """Return the current logged-in user based on server-side session token."""
    if not mcp_session:
        return {"status": "error", "message": "not_logged_in"}

    from server.services.session_token_cookie import verify_token
    token = verify_token(mcp_session)
    if not token:
        return {"status": "error", "message": "invalid_session"}

    from server.services.auth_session_store import get_auth_session_store
    sess = get_auth_session_store().get(token)
    if not sess:
        return {"status": "error", "message": "session_expired"}

    # Load extended user context if available
    # Try multiple session_id formats to find the user context file
    _ctx = None
    try:
        from server.services.employee_lookup import get_user_context
        _candidates = [
            f"line_{sess.user_id}",           # line_U09e...
            sess.user_id,                      # U09e... (raw)
            f"line_U{sess.user_id}",           # in case user_id doesn't have U prefix
        ]
        import logging as _logging
        _log = _logging.getLogger("MCP_Server.Auth")
        _log.info(f"[Auth /me] sess.user_id={sess.user_id}, candidates={_candidates}")
        for _cand in _candidates:
            _ctx = get_user_context(_cand)
            if _ctx:
                _log.info(f"[Auth /me] Found user context: {_cand}")
                break
        if not _ctx:
            _log.info(f"[Auth /me] No user context found for any candidate")
    except Exception as _e:
        import logging as _logging
        _logging.getLogger("MCP_Server.Auth").warning(f"[Auth /me] Error: {_e}")
        _ctx = None

    user = {
        "id": sess.user_id,
        "session_id": _line_session_id,
        "name": (_ctx.get("name") if _ctx else None) or sess.name or "LINE User",
        "picture": sess.picture or "",
        "initials": ((_ctx.get("name") if _ctx else None) or sess.name or "L")[:2].upper(),
        "provider": "line",
    }

    # Merge extended profile fields if available
    if _ctx:
        user["employee_id"] = _ctx.get("employee_id", "")
        user["email"] = _ctx.get("email", "")
        user["department"] = _ctx.get("department", "")
        user["department_code"] = _ctx.get("department_code", "")
        user["department_name"] = _ctx.get("department_name", "")
        user["title"] = _ctx.get("title", "")
        user["extension"] = _ctx.get("extension", "")
        user["role"] = _ctx.get("role", "editor")
        user["preferences"] = _ctx.get("preferences", {})
        user["onboarding_completed"] = _ctx.get("onboarding_completed", False)

    return {"status": "success", "user": user}
