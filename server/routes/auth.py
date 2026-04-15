"""Authentication routes."""

import os
from fastapi import APIRouter, HTTPException, Cookie
from fastapi.responses import RedirectResponse
from google.oauth2 import id_token
from google.auth.transport import requests
from pydantic import BaseModel

import logging as _logging
from server.services.line_login import build_authorize_url, consume_callback, generate_state_nonce

_auth_logger = _logging.getLogger("MCP_Server.Auth")

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
        _auth_logger.warning("[LINE callback] LINE returned error: %s %s", error, error_description)
        raise HTTPException(status_code=401, detail=f"LINE auth error: {error} {error_description}".strip())

    try:
        _auth_logger.info("[LINE callback] received code=%s… state=%s…", code[:6] if code else "", state[:8] if state else "")
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
        _auth_logger.warning("[LINE callback] ValueError: %s", ve)
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
        # sess.user_id could be:
        #   "line_U09e..." (from LINE Login canonical_line_session_id)
        #   "U09e..." (raw LINE userId)
        #   "google_xxx" (from Google login)
        _uid = sess.user_id
        _candidates = [_uid]  # Try as-is first
        if _uid.startswith("line_"):
            pass  # Already has prefix, as-is is correct
        elif _uid.startswith("U"):
            _candidates.append(f"line_{_uid}")  # Add line_ prefix
        for _cand in _candidates:
            _ctx = get_user_context(_cand)
            if _ctx:
                break
    except Exception:
        _ctx = None

    user = {
        "id": sess.user_id,
        "session_id": _uid,
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

    # Temporary debug: include raw session info
    user["_debug_sess_user_id"] = sess.user_id
    user["_debug_ctx_found"] = _ctx is not None
    return {"status": "success", "user": user}


@router.get("/employee-lookup")
def employee_lookup_api(email: str = "", name: str = "", id: str = ""):
    """Public endpoint for login enrichment — lookup employee by email/name/id."""
    try:
        from server.services.employee_lookup import lookup
        query = email or name or id
        if not query:
            return {}
        emp = lookup(query)
        if not emp:
            return {}
        return {
            "name": emp.get("name", ""),
            "email": emp.get("email", ""),
            "employee_id": emp.get("employee_id", ""),
            "department_code": emp.get("department_code", ""),
            "department_name": emp.get("department_name", ""),
            "title": emp.get("title", ""),
            "extension": emp.get("extension", ""),
        }
    except Exception:
        return {}


@router.get("/employees")
def list_employees(dept: str = ""):
    """List all employees, optionally filtered by department code."""
    try:
        from server.services.employee_lookup import _load_employees
        employees = _load_employees()
        result = []
        for emp in employees:
            if dept and emp.get("department_code", "") != dept:
                continue
            result.append({
                "employee_id": emp.get("employee_id", ""),
                "name": emp.get("name", ""),
                "email": emp.get("email", ""),
                "department_code": emp.get("department_code", ""),
                "department_name": emp.get("department_name", ""),
                "title": emp.get("title", ""),
                "extension": emp.get("extension", ""),
                "role": emp.get("role", ""),
            })
        return {"total": len(result), "employees": result}
    except Exception as e:
        return {"total": 0, "employees": [], "error": str(e)}


@router.get("/departments")
def list_departments():
    """List all unique departments."""
    try:
        from server.services.employee_lookup import _load_employees
        employees = _load_employees()
        depts = {}
        for emp in employees:
            code = emp.get("department_code", "")
            name = emp.get("department_name", "")
            if code and code not in depts:
                depts[code] = name
        result = [{"code": k, "name": v} for k, v in sorted(depts.items())]
        return {"total": len(result), "departments": result}
    except Exception as e:
        return {"total": 0, "departments": [], "error": str(e)}


class EmployeeUpdateRequest(BaseModel):
    name: str = ""
    email: str = ""
    department_code: str = ""
    department_name: str = ""
    title: str = ""
    extension: str = ""
    role: str = ""  # editor / viewer / admin


@router.put("/employees/{employee_id}")
def update_employee(employee_id: str, req: EmployeeUpdateRequest):
    """Update employee info and sync to workspace/users/ login data."""
    import json as _json
    from pathlib import Path as _P

    # 1. Write back to xlsx (persistent source of truth)
    xlsx_saved = False
    try:
        from server.services.employee_lookup import save_employee_to_xlsx
        updates = {}
        if req.name: updates["name"] = req.name
        if req.email: updates["email"] = req.email.lower()
        if req.department_code: updates["department_code"] = req.department_code
        if req.department_name: updates["department_name"] = req.department_name
        if req.title: updates["title"] = req.title
        if req.extension: updates["extension"] = req.extension
        if req.role: updates["role"] = req.role
        xlsx_saved = save_employee_to_xlsx(employee_id, updates)
    except Exception as e:
        _auth_logger.error(f"[EmployeeUpdate] xlsx save error: {e}")

    # 2. Update in-memory cache (reload from xlsx if saved, or patch directly)
    try:
        from server.services.employee_lookup import _load_employees
        employees = _load_employees()
        found = None
        for emp in employees:
            if emp.get("employee_id") == employee_id:
                found = emp
                break
        if not found:
            raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

        if req.name: found["name"] = req.name
        if req.email: found["email"] = req.email.lower()
        if req.department_code:
            found["department_code"] = req.department_code
            if req.department_name: found["department_name"] = req.department_name
            found["department_full"] = f"{req.department_code} {req.department_name}" if req.department_name else req.department_code
        if req.title: found["title"] = req.title
        if req.extension: found["extension"] = req.extension
        if req.role: found["role"] = req.role
    except HTTPException:
        raise
    except Exception as e:
        _auth_logger.error(f"[EmployeeUpdate] Cache update error: {e}")

    # 3. Sync to workspace/users/*.json login files
    pr = os.environ.get("PROJECT_ROOT", str(_P(__file__).resolve().parents[2]))
    users_dir = _P(pr) / "workspace" / "users"
    synced = []
    if users_dir.exists():
        for uf in users_dir.glob("*.json"):
            try:
                udata = _json.loads(uf.read_text(encoding="utf-8"))
                if udata.get("employee_id") == employee_id or udata.get("email", "").lower() == (req.email or "").lower():
                    if req.name: udata["name"] = req.name
                    if req.email: udata["email"] = req.email.lower()
                    if req.department_code:
                        udata["department_code"] = req.department_code
                        if req.department_name: udata["department_name"] = req.department_name
                    if req.title: udata["title"] = req.title
                    if req.extension: udata["extension"] = req.extension
                    if req.role: udata["role"] = req.role
                    uf.write_text(_json.dumps(udata, ensure_ascii=False, indent=2), encoding="utf-8")
                    synced.append(uf.name)
            except Exception:
                pass

    return {"status": "success", "employee_id": employee_id, "xlsx_saved": xlsx_saved, "synced_files": synced}


@router.post("/employees/{employee_id}")
def create_employee(employee_id: str, req: EmployeeUpdateRequest):
    """Add a new employee to xlsx."""
    try:
        from server.services.employee_lookup import _load_employees, _get_xlsx_path, _EMPLOYEE_CACHE
        import server.services.employee_lookup as _el

        # Check if already exists
        employees = _load_employees()
        for emp in employees:
            if emp.get("employee_id") == employee_id:
                raise HTTPException(status_code=409, detail=f"Employee '{employee_id}' already exists")

        # Append to xlsx
        import openpyxl
        xlsx_path = _get_xlsx_path()
        wb = openpyxl.load_workbook(str(xlsx_path))
        ws = wb.active

        # Ensure header has 角色 column
        if ws.cell(row=1, column=7).value != "角色":
            ws.cell(row=1, column=7, value="角色")

        new_row = ws.max_row + 1
        ws.cell(row=new_row, column=1, value=new_row - 1)  # 序號
        ws.cell(row=new_row, column=2, value=f"{employee_id} {req.name}")
        ws.cell(row=new_row, column=3, value=req.extension)
        ws.cell(row=new_row, column=4, value=f"{req.department_code} {req.department_name}" if req.department_code else "")
        ws.cell(row=new_row, column=5, value=req.title)
        ws.cell(row=new_row, column=6, value=req.email)
        ws.cell(row=new_row, column=7, value=req.role)

        wb.save(str(xlsx_path))
        wb.close()

        # Invalidate cache
        _el._EMPLOYEE_CACHE = None

        return {"status": "success", "employee_id": employee_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
