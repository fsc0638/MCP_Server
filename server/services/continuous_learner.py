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
from typing import Any, Callable, Dict, Optional

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

    def tick(self, llm_callable: Optional[LLMCallable] = None) -> None:
        """One learning tick.

        For now this is intentionally conservative: it only records that it ran.
        In Phase 3 we will plug in actual extraction targets.
        """
        state = self.load_state()
        state["last_run_at"] = self._utcnow_iso()
        state.setdefault("runs", 0)
        state["runs"] += 1

        # Placeholder for future:
        # - read recent sessions/messages from session manager
        # - read newly ingested docs
        # - extract patterns/preferences
        # - write distilled learnings into memory store
        if llm_callable is None:
            state["last_mode"] = "no_llm"
        else:
            state["last_mode"] = "with_llm"

        self.save_state(state)
        logger.info(f"[ContinuousLearner] Tick complete runs={state['runs']} mode={state['last_mode']}")
