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


def load_behavior_rule_texts(project_root: str | Path, max_each: int = 8) -> List[str]:
    """Return normalized rule texts for de-dup across injections."""
    root = Path(project_root)
    path = root / "memory" / "behavior_rules.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        style = _compact_lines(data.get("style") or [], max_each)
        taboos = _compact_lines(data.get("taboos") or [], max_each)
        groups = _compact_lines(data.get("group_rules") or [], max_each)
        return style + taboos + groups
    except Exception:
        return []


def render_behavior_rules_appendix(project_root: str | Path, max_each: int = 8, max_chars: int = 1200, return_texts: bool = False):
    """Return a small text block to append to system prompt.

    Safe: returns empty string if file missing or invalid.

    If return_texts=True, returns a tuple: (text, rule_texts).
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
        return ("", []) if return_texts else ""

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

    text = "".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n(…已截斷)\n"

    rule_texts = style + taboos + groups
    return (text, rule_texts) if return_texts else text
