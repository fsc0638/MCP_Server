"""
Token Tracker Service — Phase D
Records per-request token usage and generates aggregated summaries.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MCP_Server.TokenTracker")

# ── Constants ─────────────────────────────────────────────────────────────────
USAGE_LOG_RETENTION_DAYS = 90


class TokenTracker:
    """Append-only token usage logger with scheduled aggregation."""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.analytics_dir = self.project_root / "workspace" / "analytics"
        self.analytics_dir.mkdir(parents=True, exist_ok=True)
        self.usage_path = self.analytics_dir / "token_usage.jsonl"
        self.summary_path = self.analytics_dir / "token_summary.json"

    # ─── Instant Recording (D1) ───────────────────────────────────────────────

    def record_usage(
        self,
        session_id: str,
        user_id: str = "",
        chat_type: str = "personal",
        chat_id: str = "",
        skill: str = "",
        model: str = "",
        tier: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        skill_internal_tokens: int = 0,
        duration_ms: int = 0,
        status: str = "success",
    ):
        """Append a single usage record to the JSONL log."""
        record = {
            "ts": datetime.now().isoformat(),
            "session_id": session_id,
            "user_id": user_id,
            "chat_type": chat_type,
            "chat_id": chat_id,
            "skill": skill,
            "model": model,
            "tier": tier,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "skill_internal_tokens": skill_internal_tokens,
            "duration_ms": duration_ms,
            "status": status,
        }
        try:
            with open(self.usage_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[TokenTracker] Failed to record usage: {e}")

    # ─── Scheduled Aggregation (D2) ───────────────────────────────────────────

    def rebuild_summary(self):
        """Rebuild the aggregated summary from the usage log."""
        if not self.usage_path.exists():
            logger.info("[TokenTracker] No usage log found, skipping summary rebuild.")
            return

        cutoff = (datetime.now() - timedelta(days=USAGE_LOG_RETENTION_DAYS)).isoformat()
        records = []
        retained_lines = []

        try:
            with open(self.usage_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        if r.get("ts", "") >= cutoff:
                            records.append(r)
                            retained_lines.append(line)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"[TokenTracker] Failed to read usage log: {e}")
            return

        # Rewrite log with only retained records (cleanup)
        try:
            with open(self.usage_path, "w", encoding="utf-8") as f:
                for line in retained_lines:
                    f.write(line + "\n")
        except Exception:
            pass

        # Build summary
        summary = {
            "last_updated": datetime.now().isoformat(),
            "total": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "skill_internal_tokens": 0,
                "skill_calls": 0,
            },
            "by_user": {},
            "by_skill": {},
            "by_group": {},
            "daily": {},
        }

        for r in records:
            inp = r.get("input_tokens", 0)
            out = r.get("output_tokens", 0)
            tot = r.get("total_tokens", 0)
            si = r.get("skill_internal_tokens", 0)
            skill = r.get("skill", "")
            user = r.get("user_id", "")
            chat_type = r.get("chat_type", "personal")
            chat_id = r.get("chat_id", "")
            status = r.get("status", "")
            duration = r.get("duration_ms", 0)
            day = r.get("ts", "")[:10]

            # Total
            summary["total"]["input_tokens"] += inp
            summary["total"]["output_tokens"] += out
            summary["total"]["total_tokens"] += tot
            summary["total"]["skill_internal_tokens"] += si
            if skill:
                summary["total"]["skill_calls"] += 1

            # By user
            if user:
                if user not in summary["by_user"]:
                    summary["by_user"][user] = {"total_tokens": 0, "skill_calls": 0, "by_skill": {}}
                u = summary["by_user"][user]
                u["total_tokens"] += tot + si
                if skill:
                    u["skill_calls"] += 1
                    if skill not in u["by_skill"]:
                        u["by_skill"][skill] = {"calls": 0, "total_tokens": 0}
                    u["by_skill"][skill]["calls"] += 1
                    u["by_skill"][skill]["total_tokens"] += tot + si

            # By skill
            if skill:
                if skill not in summary["by_skill"]:
                    summary["by_skill"][skill] = {
                        "calls": 0, "total_tokens": 0,
                        "total_duration_ms": 0, "errors": 0,
                    }
                s = summary["by_skill"][skill]
                s["calls"] += 1
                s["total_tokens"] += tot + si
                s["total_duration_ms"] += duration
                if status != "success":
                    s["errors"] += 1

            # By group
            if chat_type == "group" and chat_id:
                gk = f"line_group_{chat_id}" if not chat_id.startswith("line_group_") else chat_id
                if gk not in summary["by_group"]:
                    summary["by_group"][gk] = {"total_tokens": 0, "skill_calls": 0}
                summary["by_group"][gk]["total_tokens"] += tot + si
                if skill:
                    summary["by_group"][gk]["skill_calls"] += 1

            # Daily
            if day:
                if day not in summary["daily"]:
                    summary["daily"][day] = {"total_tokens": 0, "skill_calls": 0}
                summary["daily"][day]["total_tokens"] += tot + si
                if skill:
                    summary["daily"][day]["skill_calls"] += 1

        # Calculate averages
        for sk, sv in summary["by_skill"].items():
            if sv["calls"] > 0:
                sv["avg_tokens"] = sv["total_tokens"] // sv["calls"]
                sv["avg_duration_ms"] = sv["total_duration_ms"] // sv["calls"]
                sv["error_rate"] = round(sv["errors"] / sv["calls"], 3)
            del sv["total_duration_ms"]  # Intermediate field

        # Compute avg_tokens for by_user.by_skill
        for uk, uv in summary["by_user"].items():
            for sk, sv in uv["by_skill"].items():
                if sv["calls"] > 0:
                    sv["avg_tokens"] = sv["total_tokens"] // sv["calls"]

        # Save summary
        try:
            with open(self.summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            logger.info(
                f"[TokenTracker] Summary rebuilt: {summary['total']['skill_calls']} calls, "
                f"{summary['total']['total_tokens']} tokens, {len(records)} records retained."
            )
        except Exception as e:
            logger.error(f"[TokenTracker] Failed to write summary: {e}")

    def get_summary(self) -> Optional[dict]:
        """Load the latest summary."""
        if self.summary_path.exists():
            try:
                return json.loads(self.summary_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None
