"""Workflow-First Matcher — keyword + FAISS semantic matching.

Usage:
    matcher = WorkflowMatcher(workflows_base_path)
    match = matcher.match(user_input, user_context={"owner": "1665"})
    if match:
        print(match["workflow_id"], match["score"], match["method"])
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger("MCP_Server.WorkflowMatcher")

# Feature flag — set WF_FIRST_ENABLED=0 to disable workflow-first routing
_WF_FIRST_ENABLED = os.getenv("WF_FIRST_ENABLED", "1").strip() not in ("0", "false", "no")

# Thresholds
_KEYWORD_BOOST = 1.0        # Keyword match = score 1.0 (perfect match)
_SEMANTIC_THRESHOLD = 0.75  # Minimum FAISS cosine similarity
_SEMANTIC_MAX_K = 5         # Top-K candidates for semantic search


class WorkflowMatcher:
    """Matches user input against workflow trigger_keywords and descriptions."""

    def __init__(self, workflows_base: str = None):
        pr = os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))
        self._base = Path(workflows_base) if workflows_base else Path(pr) / "workspace" / "workflows"
        self._cache: Dict[str, dict] = {}
        self._cache_ts = 0
        self._embedding_fn = None  # Lazy-loaded

    def _scan_workflows(self) -> List[dict]:
        """Scan all workflow JSON files and cache them."""
        import time
        now = time.time()
        # Cache for 30 seconds
        if self._cache and now - self._cache_ts < 30:
            return list(self._cache.values())

        self._cache.clear()
        for scope_dir in [self._base / "system", self._base / "department", self._base / "personal"]:
            if not scope_dir.exists():
                continue
            for json_file in scope_dir.rglob("*.json"):
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    wf_id = data.get("id", json_file.stem)
                    trigger = data.get("trigger", {})
                    self._cache[wf_id] = {
                        "id": wf_id,
                        "name": data.get("name", wf_id),
                        "description": data.get("description", ""),
                        "trigger_keywords": data.get("trigger_keywords", []),
                        "trigger_enabled": trigger.get("enabled", False),
                        "trigger_mode": trigger.get("mode", "auto"),
                        "trigger_priority": trigger.get("priority", 10),
                        "scope": data.get("scope", "personal"),
                        "owner": data.get("owner", ""),
                        "variables": data.get("variables", []),
                        "blocks": data.get("blocks", []),
                        "connections": data.get("connections", []),
                        "execution": data.get("execution", {}),
                        "security": data.get("security", {}),
                        "path": str(json_file),
                    }
                except Exception as e:
                    logger.debug(f"[WFMatcher] Skip {json_file}: {e}")

        # Also scan legacy flat directory
        for json_file in self._base.glob("*.json"):
            if json_file.name.startswith("."):
                continue
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                wf_id = data.get("id", json_file.stem)
                if wf_id not in self._cache:
                    trigger = data.get("trigger", {})
                    self._cache[wf_id] = {
                        "id": wf_id,
                        "name": data.get("name", wf_id),
                        "description": data.get("description", ""),
                        "trigger_keywords": data.get("trigger_keywords", []),
                        "trigger_enabled": trigger.get("enabled", False),
                        "trigger_mode": trigger.get("mode", "auto"),
                        "trigger_priority": trigger.get("priority", 10),
                        "scope": data.get("scope", "personal"),
                        "owner": data.get("owner", ""),
                        "variables": data.get("variables", []),
                        "blocks": data.get("blocks", []),
                        "connections": data.get("connections", []),
                        "execution": data.get("execution", {}),
                        "security": data.get("security", {}),
                        "path": str(json_file),
                    }
            except Exception:
                pass

        self._cache_ts = now
        return list(self._cache.values())

    def match(self, user_input: str, user_context: dict = None) -> Optional[Dict[str, Any]]:
        """
        Match user input against workflows.
        Returns: { workflow_id, workflow, score, method } or None.

        Priority:
        1. Exact keyword match (0 tokens, score=1.0)
        2. FAISS semantic match (0 tokens from LLM, score=cosine similarity)
        """
        if not _WF_FIRST_ENABLED:
            return None

        workflows = self._scan_workflows()
        if not workflows:
            return None

        # Filter by trigger_enabled (only match workflows with trigger on)
        candidates = [wf for wf in workflows if wf["trigger_enabled"]]
        if not candidates:
            return None

        # Filter by user access (scope-based)
        if user_context:
            owner = user_context.get("employee_id") or user_context.get("id") or ""
            dept = user_context.get("department_code") or user_context.get("dept_code") or ""
            visible = []
            for wf in candidates:
                if wf["scope"] == "system":
                    visible.append(wf)
                elif wf["scope"] == "department" and wf["owner"] == dept:
                    visible.append(wf)
                elif wf["scope"] == "personal" and wf["owner"] == owner:
                    visible.append(wf)
            candidates = visible

        if not candidates:
            return None

        input_lower = user_input.strip().lower()

        # Collect scores for ALL candidates so we can report rejected ones
        # (per the integrated report §4.4 "explain why not matched").
        all_scores: list = []

        # ── Phase 1: Keyword match (0 token cost) ──
        keyword_matches = []
        for wf in candidates:
            hit_keyword = None
            for kw in (wf.get("trigger_keywords") or []):
                kw_lower = kw.strip().lower()
                if not kw_lower:
                    continue
                if kw_lower in input_lower or input_lower in kw_lower:
                    hit_keyword = kw
                    break
            if hit_keyword:
                entry = {
                    "workflow_id": wf["id"],
                    "workflow": wf,
                    "confidence": _KEYWORD_BOOST,
                    "score": _KEYWORD_BOOST,  # backward compat
                    "method": "keyword",
                    "matched_keyword": hit_keyword,
                    "match_reason": f"命中關鍵字「{hit_keyword}」",
                }
                keyword_matches.append(entry)
                all_scores.append(entry)

        if keyword_matches:
            keyword_matches.sort(key=lambda m: m["workflow"]["trigger_priority"])
            best = keyword_matches[0]
            logger.info(
                f"[WFMatcher] Keyword match: '{best['matched_keyword']}' → "
                f"{best['workflow_id']} (confidence={best['confidence']:.2f})"
            )
            # Build rejected_candidates list (other keyword matches with lower priority)
            rejected = [
                {
                    "id": m["workflow_id"],
                    "confidence": m["confidence"],
                    "reason": f"優先級較低 (priority={m['workflow']['trigger_priority']})",
                }
                for m in keyword_matches[1:]
            ]
            best["rejected_candidates"] = rejected
            return best

        # ── Phase 2: FAISS semantic match (0 LLM token cost) ──
        try:
            semantic_result = self._semantic_match_with_scores(input_lower, candidates)
            if semantic_result:
                best = semantic_result[0]
                # Build rejected list from 2nd..N
                rejected = []
                for item in semantic_result[1:]:
                    why = (
                        f"相似度 {item['confidence']:.2f} 低於門檻 {_SEMANTIC_THRESHOLD}"
                        if item['confidence'] < _SEMANTIC_THRESHOLD
                        else f"未達最高分"
                    )
                    rejected.append({
                        "id": item["workflow_id"],
                        "confidence": item["confidence"],
                        "reason": why,
                    })
                best["rejected_candidates"] = rejected
                if best["confidence"] >= _SEMANTIC_THRESHOLD:
                    logger.info(
                        f"[WFMatcher] Semantic match: {best['workflow_id']} "
                        f"(confidence={best['confidence']:.3f}, rejected={len(rejected)})"
                    )
                    return best
                else:
                    # Even the best candidate isn't confident enough — fall through to None
                    logger.info(
                        f"[WFMatcher] Best semantic candidate {best['workflow_id']} "
                        f"confidence={best['confidence']:.3f} < threshold {_SEMANTIC_THRESHOLD}, not matched"
                    )
        except Exception as e:
            logger.warning(f"[WFMatcher] Semantic match failed: {e}")

        return None

    def _semantic_match_with_scores(self, user_input: str, candidates: List[dict]) -> List[Dict[str, Any]]:
        """Return all candidates sorted by semantic similarity (descending).

        The highest-scored one exceeding _SEMANTIC_THRESHOLD is the match;
        the rest are potential rejected_candidates for explainability.
        """
        try:
            if self._embedding_fn is None:
                from langchain_huggingface import HuggingFaceEmbeddings
                self._embedding_fn = HuggingFaceEmbeddings(
                    model_name="paraphrase-multilingual-MiniLM-L12-v2"
                )
        except ImportError:
            return []

        import numpy as np

        texts = []
        for wf in candidates:
            desc = wf.get("description", "") or wf.get("name", "")
            kws = " ".join(wf.get("trigger_keywords", []))
            texts.append(f"{desc} {kws}".strip())

        if not texts:
            return []

        query_embedding = np.array(self._embedding_fn.embed_query(user_input))
        doc_embeddings = np.array(self._embedding_fn.embed_documents(texts))

        norms_q = np.linalg.norm(query_embedding)
        norms_d = np.linalg.norm(doc_embeddings, axis=1)
        if norms_q == 0:
            return []

        sims = np.dot(doc_embeddings, query_embedding) / (norms_d * norms_q + 1e-10)
        order = np.argsort(-sims)  # descending

        out: List[Dict[str, Any]] = []
        for i in order[:5]:  # top-5 only
            wf = candidates[int(i)]
            conf = float(sims[int(i)])
            # Build a human reason: what tokens matched the description?
            desc = (wf.get("description") or "").lower()
            hits = [t for t in user_input.split() if t in desc and len(t) >= 2]
            reason_bits = []
            if hits:
                reason_bits.append(f"描述含「{'、'.join(hits[:3])}」")
            kws = wf.get("trigger_keywords") or []
            kw_hits = [k for k in kws if k.lower() in user_input.lower() or user_input.lower() in k.lower()]
            if kw_hits:
                reason_bits.append(f"觸發詞「{'、'.join(kw_hits[:2])}」部分命中")
            reason = "、".join(reason_bits) if reason_bits else f"語義相似度 {conf:.2f}"
            out.append({
                "workflow_id": wf["id"],
                "workflow": wf,
                "confidence": conf,
                "score": conf,
                "method": "semantic",
                "match_reason": reason,
            })
        return out

    def _semantic_match(self, user_input: str, candidates: List[dict]) -> Optional[Dict[str, Any]]:
        """Use sentence embeddings for semantic similarity matching."""
        try:
            if self._embedding_fn is None:
                from langchain_huggingface import HuggingFaceEmbeddings
                self._embedding_fn = HuggingFaceEmbeddings(
                    model_name="paraphrase-multilingual-MiniLM-L12-v2"
                )
        except ImportError:
            logger.debug("[WFMatcher] HuggingFaceEmbeddings not available, skip semantic match")
            return None

        import numpy as np

        # Build candidate texts (description + keywords combined)
        texts = []
        for wf in candidates:
            desc = wf.get("description", "") or wf.get("name", "")
            kws = " ".join(wf.get("trigger_keywords", []))
            texts.append(f"{desc} {kws}".strip())

        if not texts:
            return None

        # Embed user input and candidates
        query_embedding = np.array(self._embedding_fn.embed_query(user_input))
        doc_embeddings = np.array(self._embedding_fn.embed_documents(texts))

        # Cosine similarity
        norms_q = np.linalg.norm(query_embedding)
        norms_d = np.linalg.norm(doc_embeddings, axis=1)
        if norms_q == 0:
            return None

        similarities = np.dot(doc_embeddings, query_embedding) / (norms_d * norms_q + 1e-10)

        # Find best match above threshold
        best_idx = np.argmax(similarities)
        best_score = float(similarities[best_idx])

        if best_score >= _SEMANTIC_THRESHOLD:
            wf = candidates[best_idx]
            return {
                "workflow_id": wf["id"],
                "workflow": wf,
                "score": best_score,
                "method": "semantic",
            }

        return None


# ── Singleton ──
_matcher_instance = None


def get_workflow_matcher() -> WorkflowMatcher:
    global _matcher_instance
    if _matcher_instance is None:
        _matcher_instance = WorkflowMatcher()
    return _matcher_instance
