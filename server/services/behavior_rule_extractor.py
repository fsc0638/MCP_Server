"""Phase 3: Behavior rule extractor.

Goal:
- Derive lightweight, actionable behavior rules (禁忌/回覆風格/群組規則)
  from the learning snapshot.
- Deterministic, no external API required.

Outputs:
- memory/behavior_rules.json
- memory/behavior_rules.md

Strategy (initial):
- Primarily trust workspace/profiles (highest priority).
- Supplement with simple heuristics from recent sessions (keywords).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger("MCP_Server.BehaviorRuleExtractor")


@dataclass
class BehaviorRules:
    style: List[str]
    taboos: List[str]
    group_rules: List[str]


class BehaviorRuleExtractor:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)
        self.snapshot_path = self.project_root / "memory" / "learning_snapshot.json"
        self.out_json = self.project_root / "memory" / "behavior_rules.json"
        self.out_md = self.project_root / "memory" / "behavior_rules.md"

    def load_snapshot(self) -> Dict[str, Any]:
        if not self.snapshot_path.exists():
            return {}
        return json.loads(self.snapshot_path.read_text(encoding="utf-8"))

    def _extract_from_profile_previews(self, snapshot: Dict[str, Any]) -> Dict[str, List[str]]:
        style: List[str] = []
        taboos: List[str] = []
        group_rules: List[str] = []

        files = snapshot.get("files") or []
        for f in files:
            if f.get("source") != "profiles":
                continue
            path = f.get("path", "")
            if "/profiles/" not in path.replace("\\", "/"):
                continue
            preview = f.get("preview") or ""
            if not preview:
                continue

            # Very simple keyword extraction for now.
            # Common Chinese labels we expect in profiles content.
            for m in re.finditer(r"(禁忌|不要|不可|避免)[:：]?\s*(.+)", preview):
                item = m.group(0).strip()
                if item and item not in taboos:
                    taboos.append(item)

            for m in re.finditer(r"(風格|語氣|回覆|偏好)[:：]?\s*(.+)", preview):
                item = m.group(0).strip()
                if item and item not in style:
                    style.append(item)

            for m in re.finditer(r"(群組規則|群規|群組|room|group)[:：]?\s*(.+)", preview, flags=re.I):
                item = m.group(0).strip()
                if item and item not in group_rules:
                    group_rules.append(item)

        return {"style": style[:20], "taboos": taboos[:30], "group_rules": group_rules[:30]}

    def _extract_from_sessions(self, snapshot: Dict[str, Any]) -> Dict[str, List[str]]:
        # Heuristic: if recent user messages contain directives like "群組忽略" etc.
        style: List[str] = []
        taboos: List[str] = []
        group_rules: List[str] = []

        msgs = snapshot.get("messages") or []
        for m in msgs[::-1]:
            if m.get("role") != "user":
                continue
            txt = (m.get("content") or "").strip()
            if not txt:
                continue
            if any(k in txt for k in ["群組", "room", "group"]):
                if any(k in txt for k in ["忽略", "不要回", "不回覆", "ignore"]):
                    group_rules.append(f"from_session: {txt[:160]}")
            if any(k in txt for k in ["不要", "禁止", "避免", "不可"]):
                taboos.append(f"from_session: {txt[:160]}")
            if any(k in txt for k in ["語氣", "風格", "用詞", "繁體"]):
                style.append(f"from_session: {txt[:160]}")
            if len(style) + len(taboos) + len(group_rules) > 30:
                break

        return {"style": style[:10], "taboos": taboos[:10], "group_rules": group_rules[:10]}

    def _normalize_item(self, s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""

        # Normalize common prefixes
        s = re.sub(r"^from_session:\s*", "", s, flags=re.I)

        # Unify separators
        s = s.replace("：", ":")
        s = re.sub(r"\s+", " ", s)

        # Normalize label prefixes
        s = re.sub(r"^(禁忌|不要|不可|避免)\s*:?\s*", "禁忌: ", s)
        s = re.sub(r"^(風格|語氣|回覆|偏好)\s*:?\s*", "風格: ", s)
        s = re.sub(r"^(群組規則|群規|群組)\s*:?\s*", "群組規則: ", s)

        return s.strip()

    def build(self) -> Dict[str, Any]:
        snap = self.load_snapshot()
        base = self._extract_from_profile_previews(snap)
        sup = self._extract_from_sessions(snap)

        # Merge with de-dup + normalization, profiles have priority.
        def merge(a: List[str], b: List[str], limit: int) -> List[str]:
            out: List[str] = []
            seen = set()
            for raw in a + b:
                norm = self._normalize_item(raw)
                if not norm:
                    continue
                key = norm.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(norm)
                if len(out) >= limit:
                    break
            return out

        rules = BehaviorRules(
            style=merge(base["style"], sup["style"], 25),
            taboos=merge(base["taboos"], sup["taboos"], 40),
            group_rules=merge(base["group_rules"], sup["group_rules"], 40),
        )

        out = {
            "generated_at": None,
            "style": rules.style,
            "taboos": rules.taboos,
            "group_rules": rules.group_rules,
        }
        return out

    def write(self) -> Dict[str, Any]:
        out = self.build()
        out["generated_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")

        self.out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

        md = [
            "# Behavior Rules\n\n",
            f"generated_at: {out['generated_at']}\n\n",
            "## Style\n",
        ]
        for s in out["style"]:
            md.append(f"- {s}\n")
        md.append("\n## Taboos\n")
        for s in out["taboos"]:
            md.append(f"- {s}\n")
        md.append("\n## Group Rules\n")
        for s in out["group_rules"]:
            md.append(f"- {s}\n")

        self.out_md.write_text("".join(md), encoding="utf-8")
        logger.info("[BehaviorRuleExtractor] Wrote behavior rules")
        return out
