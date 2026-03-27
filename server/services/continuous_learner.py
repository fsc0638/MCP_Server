"""Phase 3: Continuous learner (scheduled).

Goal:
- Run a lightweight, periodic learning loop every N minutes.
- Must be safe to run without external API dependencies by default.
- When an LLM callable is available, it can be used to extract structured
  learnings (future Phase 2 memory integration).

Design principles:
- Idempotent per tick (track last_run_at + content hashes).
- Never crash the whole app; log and return.
- Default behavior should be no-op if prerequisites are missing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, List

logger = logging.getLogger("MCP_Server.ContinuousLearner")

LLMCallable = Callable[[str, Dict[str, Any]], Dict[str, Any]]


@dataclass
class ContinuousLearnerConfig:
    interval_minutes: int = 10
    enabled: bool = True


class ContinuousLearner:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)
        self.state_path = self.project_root / "memory" / "continuous_learner_state.json"
        self.buffer_path = self.project_root / "memory" / "continuous_learning_buffer.jsonl"
        self.memory_md_path = self.project_root / "memory" / "MEMORY.md"

    def _utcnow_iso(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def load_state(self) -> dict:
        try:
            if self.state_path.exists():
                return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[ContinuousLearner] Failed to load state: {e}")
        return {}

    def save_state(self, state: dict) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception as e:
            logger.error(f"[ContinuousLearner] Failed to save state: {e}")

    def _load_recent_messages(self, since_ts: int) -> List[dict]:
        """Load recent messages from workspace/sessions/*.json.

        We avoid depending on in-memory SessionManager state so this works even
        in a scheduled background job.
        """
        out: List[dict] = []
        sessions_dir = self.project_root / "workspace" / "sessions"
        if not sessions_dir.exists():
            return out

        # Iterate session files; filter messages by created_at
        for p in sessions_dir.glob("*.json"):
            # Skip meta/msg_cache files if any accidentally match
            if p.name.endswith("_meta.json") or p.name.endswith("_msg_cache.json"):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    continue
                for m in data:
                    if not isinstance(m, dict):
                        continue
                    ts = int(m.get("created_at") or 0)
                    if ts >= since_ts and m.get("role") in ("user", "assistant"):
                        out.append({
                            "session_id": p.stem,
                            "role": m.get("role"),
                            "content": m.get("content", ""),
                            "created_at": ts,
                        })
            except Exception:
                continue

        out.sort(key=lambda x: (x.get("created_at", 0), x.get("session_id", "")))
        return out

    def _append_jsonl(self, record: dict) -> None:
        try:
            self.buffer_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.buffer_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"[ContinuousLearner] Failed to append buffer: {e}")

    def _append_memory_md(self, lines: List[str]) -> None:
        try:
            self.memory_md_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.memory_md_path.exists():
                self.memory_md_path.write_text(
                    "# MCP Server — Session Memory\n\n---\n\n",
                    encoding="utf-8",
                )
            with open(self.memory_md_path, "a", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            logger.error(f"[ContinuousLearner] Failed to append MEMORY.md: {e}")

    def tick(self, llm_callable: Optional[LLMCallable] = None) -> None:
        """One learning tick.

        Phase 3 (step 1):
        - Pull recent session messages since last tick.
        - Write a raw JSONL buffer for replay.
        - Write a short human-readable summary into memory/MEMORY.md.

        This must be safe with llm_callable=None.
        """
        state = self.load_state()
        now_iso = self._utcnow_iso()
        now_ts = int(datetime.now(timezone.utc).timestamp())

        last_ts = int(state.get("last_seen_ts") or 0)
        if last_ts <= 0:
            # On first run, avoid dumping the entire history; only look back a bit.
            last_ts = now_ts - 600  # 10 minutes

        recent = self._load_recent_messages(last_ts)

        state["last_run_at"] = now_iso
        state.setdefault("runs", 0)
        state["runs"] += 1
        state["last_mode"] = "with_llm" if llm_callable else "no_llm"
        state["last_seen_ts"] = now_ts
        state["last_recent_count"] = len(recent)

        # Write buffer records
        if recent:
            self._append_jsonl({
                "type": "continuous_learning_tick",
                "at": now_iso,
                "since_ts": last_ts,
                "count": len(recent),
            })
            for m in recent:
                self._append_jsonl({"type": "message", **m})

            # Write brief MEMORY.md note
            sample_user = next((x for x in reversed(recent) if x["role"] == "user" and x.get("content")), None)
            sample_text = (sample_user.get("content", "")[:80] + "…") if sample_user else "(no user sample)"
            md_lines = [
                f"\n## Continuous Learner Tick — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
                f"- since_ts: {last_ts}\n",
                f"- messages: {len(recent)}\n",
                f"- sample_user: {sample_text}\n",
                "---\n",
            ]
            self._append_memory_md(md_lines)

        self.save_state(state)
        logger.info(
            f"[ContinuousLearner] Tick complete runs={state['runs']} recent={len(recent)} mode={state['last_mode']}"
        )
