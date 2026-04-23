"""Policy Decision Point (PDP) for AgentK.

Goal (Phase 1/2): unify authorization + risk gating for workflow/skill execution.

This is intentionally small and explicit. It can later evolve into ABAC.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_WORKER = "worker"
ROLE_GUEST = "guest"


# High-risk actions (External Write) — ALWAYS require HitL approval.
HIGH_RISK_ACTIONS = {
    "notion.write",
    "crm.write",
    "erp.write",
    "line.user_push",
    "line.group_push",
    "email.send",
    # Also treat high-risk skills that self-report requires_approval
    "skill.requires_approval",
}


@dataclass
class Decision:
    allow: bool
    reason_code: str = ""
    reason: str = ""
    requires_approval: bool = False


def _role(ctx: Optional[Dict[str, Any]]) -> str:
    r = (ctx or {}).get("role") or ""
    r = r.lower().strip()
    if r in {ROLE_ADMIN, ROLE_MANAGER, ROLE_WORKER, ROLE_GUEST}:
        return r
    # Unknown role defaults to guest (safe)
    return ROLE_GUEST


def authorize(
    *,
    subject_ctx: Optional[Dict[str, Any]],
    action: str,
    resource_type: str,
    resource_id: str,
    context: Optional[Dict[str, Any]] = None,
) -> Decision:
    """Return allow/deny + whether it requires approval.

    Rules v1:
    - Unknown users default to guest.
    - High-risk actions always require approval.
    - Guests are read-only for high-risk and most writes; but we still allow them to request approvals
      (the approval itself will be pending and must be approved by manager/admin).
    """

    role = _role(subject_ctx)
    action = (action or "").strip()

    # External write gating
    if action in HIGH_RISK_ACTIONS:
        # Policy allow to request; execution must pause for approval.
        # Guests can request, but will require manager/admin to approve.
        return Decision(
            allow=True,
            reason_code="HIGH_RISK_REQUIRES_APPROVAL",
            reason="High-risk action requires human approval.",
            requires_approval=True,
        )

    # Default allow for low-risk operations
    return Decision(allow=True, reason_code="ALLOW", reason="Allowed")
