"""Phase 2.3: Minimal rollback utility for memory_store.

Allows reverting a behavior rule entry to a previous version using change_log.

This is deterministic and intended for admin/manual operations.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from server.services.memory_store import MemoryStore


def rollback_rule(project_root: str | Path, kind: str, text: str, to: str = "old") -> Dict[str, Any]:
    """Rollback a rule in long_term.behavior_rules.

    Args:
      kind: style|taboos|group_rules
      text: exact rule text
      to: "old" or "new" based on the last overwrite event

    Returns updated store.
    """
    root = Path(project_root)
    ms = MemoryStore(root)
    data = ms.load()

    text_key = (text or "").strip().lower()
    log = data.get("change_log", [])

    target = None
    for ev in reversed(log):
        if ev.get("kind") == kind and (ev.get("text") or "").strip().lower() == text_key and ev.get("type") in ("overwrite", "disable"):
            target = ev
            break

    if not target:
        raise ValueError("No overwrite/disable event found for given rule")

    lt = data.setdefault("long_term", {})
    br = lt.setdefault("behavior_rules", {"style": [], "taboos": [], "group_rules": []})
    arr = br.get(kind, [])

    # Determine rollback payload
    payload = None
    if target.get("type") == "overwrite":
        payload = target.get(to)
    elif target.get("type") == "disable":
        payload = target.get("old")

    if not payload or not isinstance(payload, dict):
        raise ValueError("Rollback payload missing")

    # Replace or insert
    new_arr = [it for it in arr if (it.get("text") or "").strip().lower() != text_key]
    new_arr.insert(0, payload)
    br[kind] = new_arr

    # Remove from disabled list if present
    disabled = data.get("disabled", {})
    if kind in disabled:
        disabled[kind] = [d for d in disabled[kind] if (d.get("text") or "").strip().lower() != text_key]

    # Log rollback
    data.setdefault("change_log", []).append(
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "type": "rollback",
            "kind": kind,
            "text": text,
            "to": to,
            "from_event": target,
            "applied": payload,
        }
    )
    # cap 1000
    data["change_log"] = data["change_log"][-1000:]

    ms.save(data)
    return data
