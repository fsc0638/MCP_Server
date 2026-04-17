"""Central permission & quota enforcement for skills & workflows.

Rules (as of 2026-04-17):
  - System scope  →  read-only for everyone except `role="admin"`
  - Department scope → members of that department may CRUD (dept_code must match)
  - Personal scope → owner may CRUD (user_id must match)
  - Guest quota (role="guest" OR name="訪客"):
      * max 3 workflows  (counted across dept + personal)
      * max 10 skills    (counted across dept + personal)

All violations raise HTTPException (403 Forbidden or 429 Quota Exceeded).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

logger = logging.getLogger("MCP_Server.Permissions")

GUEST_MAX_WORKFLOWS = 3
GUEST_MAX_SKILLS = 10


# ── User context resolution ────────────────────────────────────────────────

def _project_root() -> Path:
    return Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))


def resolve_caller_context(mcp_session: str) -> Optional[Dict[str, Any]]:
    """Resolve the signed-in user from the mcp_session cookie, returning their
    persisted user_context dict (role/dept/employee_id/...).

    Returns None if cookie is missing or invalid. Permission enforcement code
    should treat None as guest/unauthenticated — safer default than letting
    them through.
    """
    if not mcp_session:
        return None
    try:
        from server.services.session_token_cookie import verify_token
        from server.services.auth_session_store import get_auth_session_store
        token = verify_token(mcp_session)
        if not token:
            return None
        sess = get_auth_session_store().get(token)
        if not sess or not sess.user_id:
            return None
        user_id = sess.user_id
    except Exception as e:
        logger.debug(f"[Permissions] cookie resolve failed: {e}")
        return None

    # Try reading persistent user_context from workspace/users/{id}.json
    users_dir = _project_root() / "workspace" / "users"
    for candidate in [user_id, f"line_{user_id}" if not user_id.startswith("line_") else user_id]:
        path = users_dir / f"{candidate}.json"
        if path.exists():
            try:
                ctx = json.loads(path.read_text(encoding="utf-8"))
                # Ensure user_id is present even if not in file
                ctx.setdefault("user_id", candidate)
                return ctx
            except Exception:
                pass

    # No persisted context → return minimal ctx so we still know who they are
    return {
        "user_id": user_id,
        "name": sess.name or "",
        "role": "",                  # unknown role → treat as limited
        "employee_id": "",
        "department_code": "",
        "onboarding_completed": False,
    }


# ── Identity checks ─────────────────────────────────────────────────────────

def is_guest_user(ctx: Optional[Dict[str, Any]]) -> bool:
    """A user is treated as 'guest' if:
       - their ctx is missing entirely (cookie invalid / anonymous), OR
       - role is explicitly 'guest', OR
       - display name is literally '訪客', OR
       - NO role + NO employee_id + NO onboarding (truly unverified).

    Note: explicit roles (admin/editor/viewer) are NOT treated as guest even
    if onboarding_completed is missing — those are authenticated users.
    """
    if not ctx:
        return True
    role = (ctx.get("role") or "").lower()
    if role == "guest":
        return True
    if ctx.get("name") == "訪客":
        return True
    # Only flag as guest when we genuinely have no identity info at all
    if (
        not role
        and not ctx.get("employee_id", "")
        and not ctx.get("onboarding_completed", False)
    ):
        return True
    return False


def is_admin(ctx: Optional[Dict[str, Any]]) -> bool:
    return bool(ctx) and (ctx.get("role") or "").lower() == "admin"


def _caller_identity(ctx: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """Return (user_id, dept_code) from ctx. Empty strings when unknown."""
    if not ctx:
        return "", ""
    return ctx.get("user_id", "") or ctx.get("employee_id", ""), ctx.get("department_code", "")


def _all_caller_ids(ctx: Optional[Dict[str, Any]]) -> set:
    """Return all identifiers that legitimately refer to this user.

    Frontend/backend can key personal resources by any of these:
      - session user_id (pw_xxx / line_Uxxx)
      - employee_id (from xlsx — e.g. 0337 / 9999)
      - line raw userId (Uxxx, with or without "line_" prefix)
    """
    if not ctx:
        return set()
    ids = set()
    for k in ("user_id", "employee_id", "id"):
        v = ctx.get(k)
        if v:
            ids.add(str(v))
            # Also add the line_-stripped version to match bot upload folder format
            if str(v).startswith("line_"):
                ids.add(str(v)[len("line_"):])
    # Drop empty
    ids.discard("")
    return ids


# ── Scope CRUD permission ──────────────────────────────────────────────────

def check_scope_write_permission(
    scope: str,
    target_owner: str,
    ctx: Optional[Dict[str, Any]],
    *,
    resource_kind: str = "resource",
) -> None:
    """Raise HTTPException if caller is not allowed to write to (create/modify/
    delete) the given (scope, owner).

    Rules:
      - system     → admin only
      - department → anyone whose department_code == target_owner
      - personal   → anyone whose user_id == target_owner
    """
    scope = (scope or "").lower()
    uid, dept = _caller_identity(ctx)

    if scope == "system":
        if not is_admin(ctx):
            raise HTTPException(
                status_code=403,
                detail=f"系統層級的 {resource_kind} 僅限管理員（admin）建立或修改，一般使用者與訪客僅能檢視",
            )
        return

    if scope == "department":
        if not target_owner:
            raise HTTPException(status_code=422, detail="department scope 需要 owner（部門代碼）")
        if is_admin(ctx):
            return  # admin can write any dept
        if not dept:
            raise HTTPException(
                status_code=403,
                detail="您目前未綁定部門，無法建立部門層級的資源。請先完成身分驗證以綁定員工資料",
            )
        if dept != target_owner:
            raise HTTPException(
                status_code=403,
                detail=f"僅限 {target_owner} 部門成員建立/修改該部門的 {resource_kind}",
            )
        return

    if scope == "personal":
        if not target_owner:
            raise HTTPException(status_code=422, detail="personal scope 需要 owner（使用者 ID）")
        if is_admin(ctx):
            return
        caller_ids = _all_caller_ids(ctx)
        if not caller_ids:
            raise HTTPException(status_code=403, detail="未登入，無法建立個人層級的資源")
        # Accept any identifier the user legitimately has (user_id / employee_id / line raw id)
        if target_owner not in caller_ids:
            logger.info(f"[Permissions] personal write denied: target={target_owner} caller_ids={caller_ids}")
            raise HTTPException(
                status_code=403,
                detail=f"僅限本人建立/修改個人 {resource_kind}",
            )
        return

    # Unknown scope
    raise HTTPException(status_code=422, detail=f"未知的 scope: {scope}")


# ── Guest quota ────────────────────────────────────────────────────────────

def _count_workflows_owned_by(user_id: str, dept_code: str, all_ids: set = None) -> int:
    """Count workflows created by this user (personal) + dept (department).

    `all_ids` is the full set of identifiers the user goes by (user_id,
    employee_id, line raw id) — used so we count folders keyed by ANY of them.
    """
    base = _project_root() / "workspace" / "workflows"
    count = 0
    candidates = all_ids or set()
    if user_id:
        candidates.add(user_id)
    # Personal: count files in every folder that matches any of the user's IDs
    personal_base = base / "personal"
    if personal_base.exists():
        for uid in candidates:
            folder = personal_base / uid
            if folder.exists():
                count += len(list(folder.glob("*.json")))
    if dept_code:
        dept = base / "department" / dept_code
        if dept.exists():
            # Only count dept workflows this user created (metadata created_by)
            for f in dept.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    _creator = data.get("created_by") or data.get("owner_user_id") or ""
                    if _creator in candidates:
                        count += 1
                except Exception:
                    pass
    return count


def _count_skills_owned_by(user_id: str, dept_code: str) -> int:
    """Count skills under this user's personal folder + contributed to dept."""
    base = _project_root() / "Agent_skills"
    count = 0
    personal_root = base / "personal_skills"
    if personal_root.exists() and user_id:
        user_dir = personal_root / user_id
        if user_dir.exists():
            # Each subdirectory with SKILL.md counts as one skill
            count += sum(1 for p in user_dir.iterdir() if p.is_dir() and (p / "SKILL.md").exists())
    dept_root = base / "department_skills"
    if dept_root.exists() and dept_code:
        dept_dir = dept_root / dept_code
        if dept_dir.exists():
            # Same convention — each skill subdirectory
            for p in dept_dir.iterdir():
                if p.is_dir() and (p / "SKILL.md").exists():
                    # Only count if created by this user (stored in SKILL.md frontmatter if available)
                    # Simple heuristic: count all since metadata tracking isn't standardized yet
                    count += 1
    return count


def enforce_guest_workflow_quota(ctx: Optional[Dict[str, Any]], *, creating_new: bool = True) -> None:
    """Raise HTTPException(429) if a guest already has >=3 workflows."""
    if not is_guest_user(ctx):
        return
    if not creating_new:
        return
    uid, dept = _caller_identity(ctx)
    count = _count_workflows_owned_by(uid, dept, all_ids=_all_caller_ids(ctx))
    if count >= GUEST_MAX_WORKFLOWS:
        raise HTTPException(
            status_code=429,
            detail=(
                f"訪客帳號最多只能建立 {GUEST_MAX_WORKFLOWS} 個工作流（目前 {count} 個）。"
                f" 請先刪除舊工作流或完成身分驗證以解除限制"
            ),
        )


def enforce_guest_skill_quota(ctx: Optional[Dict[str, Any]], *, creating_new: bool = True) -> None:
    """Raise HTTPException(429) if a guest already has >=10 skills."""
    if not is_guest_user(ctx):
        return
    if not creating_new:
        return
    uid, dept = _caller_identity(ctx)
    count = _count_skills_owned_by(uid, dept)
    if count >= GUEST_MAX_SKILLS:
        raise HTTPException(
            status_code=429,
            detail=(
                f"訪客帳號最多只能建立 {GUEST_MAX_SKILLS} 個 Agent Skill（目前 {count} 個）。"
                f" 請先刪除舊 Skill 或完成身分驗證以解除限制"
            ),
        )
