"""Phase 3: Behavior rule extractor.

Goal:
- Derive lightweight, actionable behavior rules (禁忌/回覆風格/群組規則)
  from the learning snapshot.
- Deterministic, no external API required.

Outputs:
- memory/behavior_rules.json
- memory/behavior_rules.md

New (source tracking):
- Each rule includes its source (profile file path or session snippet).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("MCP_Server.BehaviorRuleExtractor")


@dataclass
class RuleItem:
    text: str
    source_type: str  # profiles | sessions
    source: str       # file path or session_id
    evidence: str     # snippet / raw line


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

    def _normalize_item(self, s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""

        # Unify separators
        s = s.replace("：", ":")
        s = re.sub(r"\s+", " ", s)

        # Normalize label prefixes
        s = re.sub(r"^(禁忌|不要|不可|避免)\s*:?\s*", "禁忌: ", s)
        s = re.sub(r"^(風格|語氣|回覆|偏好)\s*:?\s*", "風格: ", s)
        s = re.sub(r"^(群組規則|群規|群組)\s*:?\s*", "群組規則: ", s)

        return s.strip()

    def _to_bucket(self, norm: str) -> Optional[str]:
        if norm.startswith("風格:"):
            return "style"
        if norm.startswith("禁忌:"):
            return "taboos"
        if norm.startswith("群組規則:"):
            return "group_rules"
        return None

    def _extract_from_profile_previews(self, snapshot: Dict[str, Any]) -> List[RuleItem]:
        out: List[RuleItem] = []
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

            for line in preview.splitlines():
                raw = line.strip()
                if not raw:
                    continue
                # Only keep likely rule lines
                if not re.search(r"(禁忌|不要|不可|避免|風格|語氣|回覆|偏好|群組規則|群規|群組)", raw, flags=re.I):
                    continue

                norm = self._normalize_item(raw)
                if not self._to_bucket(norm):
                    continue

                out.append(
                    RuleItem(
                        text=norm,
                        source_type="profiles",
                        source=path,
                        evidence=raw[:200],
                    )
                )
        return out

    def _extract_from_sessions(self, snapshot: Dict[str, Any]) -> List[RuleItem]:
        out: List[RuleItem] = []
        msgs = snapshot.get("messages") or []
        # walk backwards for recency
        for m in msgs[::-1]:
            if m.get("role") != "user":
                continue
            txt = (m.get("content") or "").strip()
            if not txt:
                continue

            session_id = m.get("session_id") or "(unknown_session)"
            evidence = txt[:200]

            # group rules
            if any(k in txt for k in ["群組", "room", "group"]):
                if any(k in txt for k in ["忽略", "不要回", "不回覆", "ignore"]):
                    out.append(RuleItem(text="群組規則: 群組/room 訊息忽略或不回覆", source_type="sessions", source=session_id, evidence=evidence))

            # taboos
            if any(k in txt for k in ["不要", "禁止", "避免", "不可"]):
                out.append(RuleItem(text=self._normalize_item("禁忌: " + txt), source_type="sessions", source=session_id, evidence=evidence))

            # style
            if any(k in txt for k in ["語氣", "風格", "用詞", "繁體"]):
                out.append(RuleItem(text=self._normalize_item("風格: " + txt), source_type="sessions", source=session_id, evidence=evidence))

            if len(out) > 30:
                break

        return out

    def build(self) -> Dict[str, Any]:
        snap = self.load_snapshot()

        candidates: List[RuleItem] = []
        # Priority A: profiles first, then sessions
        candidates.extend(self._extract_from_profile_previews(snap))
        candidates.extend(self._extract_from_sessions(snap))

        buckets = {"style": [], "taboos": [], "group_rules": []}
        seen = set()

        for item in candidates:
            key = item.text.lower()
            if key in seen:
                continue
            seen.add(key)
            b = self._to_bucket(item.text)
            if not b:
                continue
            buckets[b].append(
                {
                    "text": item.text,
                    "source_type": item.source_type,
                    "source": item.source,
                    "evidence": item.evidence,
                }
            )

        out = {
            "generated_at": None,
            "style": buckets["style"][:25],
            "taboos": buckets["taboos"][:40],
            "group_rules": buckets["group_rules"][:40],
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
        for r in out["style"]:
            md.append(f"- {r['text']}  (src={r['source_type']}:{r['source']})\n")
        md.append("\n## Taboos\n")
        for r in out["taboos"]:
            md.append(f"- {r['text']}  (src={r['source_type']}:{r['source']})\n")
        md.append("\n## Group Rules\n")
        for r in out["group_rules"]:
            md.append(f"- {r['text']}  (src={r['source_type']}:{r['source']})\n")

        self.out_md.write_text("".join(md), encoding="utf-8")
        logger.info("[BehaviorRuleExtractor] Wrote behavior rules")
        return out
