"""Phase 3: Learning policy (mixing/priority rules).

User preference (2026-03-27): Priority A
- profiles > sessions > uploads

This module provides a small, testable policy layer that other services
(ContinuousLearner, future memory pipelines) can consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any


@dataclass(frozen=True)
class LearningPolicy:
    # Larger weight wins in conflicts
    w_profiles: int = 3
    w_sessions: int = 2
    w_uploads: int = 1

    def choose(self, a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
        """Choose between two learning candidates.

        Candidates are dicts with at least: {"source": "profiles"|"sessions"|"uploads"}
        """
        wa = self._weight(a.get("source"))
        wb = self._weight(b.get("source"))
        return a if wa >= wb else b

    def _weight(self, source: str | None) -> int:
        if source == "profiles":
            return self.w_profiles
        if source == "sessions":
            return self.w_sessions
        if source == "uploads":
            return self.w_uploads
        return 0
