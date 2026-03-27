"""Phase 2-A: Load behavior rules and render as a compact prompt appendix."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any, Optional


def _compact_lines(items: List[Dict[str, Any]], limit: int) -> List[str]:
    out: List[str] = []
    for it in items[:limit]:
        txt = (it.get("text") or "").strip()
        if not txt:
            continue
        out.append(txt)
    return out


def render_behavior_rules_appendix(project_root: str | Path, max_each: int = 8) -> str:
    """Return a small text block to append to system prompt.

    Safe: returns empty string if file missing or invalid.
    """
    root = Path(project_root)
    path = root / "memory" / "behavior_rules.json"
    if not path.exists():
        return ""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        style = _compact_lines(data.get("style") or [], max_each)
        taboos = _compact_lines(data.get("taboos") or [], max_each)
        groups = _compact_lines(data.get("group_rules") or [], max_each)
    except Exception:
        return ""

    if not (style or taboos or groups):
        return ""

    lines: List[str] = []
    lines.append("\n【行為規則（自動注入）】\n")
    if style:
        lines.append("- 風格：\n")
        for s in style:
            lines.append(f"  - {s}\n")
    if taboos:
        lines.append("- 禁忌：\n")
        for s in taboos:
            lines.append(f"  - {s}\n")
    if groups:
        lines.append("- 群組規則：\n")
        for s in groups:
            lines.append(f"  - {s}\n")

    return "".join(lines)
