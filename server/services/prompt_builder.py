"""Phase 1 (A1): PromptBuilder with token-based budgeting and dynamic trimming.

Purpose:
- Build outbound chat messages with a fixed token budget.
- Keep high-priority instructions (system + behavior rules) intact.
- Inject optional context (session summary, retrieved memory) only if budget allows.
- Include recent raw history tail up to remaining budget.

Tokenization:
- Uses tiktoken for OpenAI-style token counting.

This module is deterministic and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Budget:
    max_input_tokens: int
    reserve_output_tokens: int = 800

    @property
    def available_input_tokens(self) -> int:
        # Keep some headroom so the model can answer.
        return max(256, self.max_input_tokens - self.reserve_output_tokens)


class TokenCounter:
    def __init__(self, model: str = "gpt-4o-mini"):
        import tiktoken

        self.model = model
        try:
            self.enc = tiktoken.encoding_for_model(model)
        except Exception:
            self.enc = tiktoken.get_encoding("cl100k_base")

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        return len(self.enc.encode(text))

    def count_messages(self, messages: List[Dict[str, str]]) -> int:
        # Approximation: count tokens in role+content.
        # (Exact chat serialization differs by provider/model, but this is stable and conservative.)
        total = 0
        for m in messages:
            total += self.count_text(m.get("role", "")) + self.count_text(m.get("content", "")) + 4
        return total


def _truncate_text_to_tokens(counter: TokenCounter, text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    if counter.count_text(text) <= max_tokens:
        return text
    # Binary search on character length (fast enough for our sizes)
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        cand = text[:mid]
        if counter.count_text(cand) <= max_tokens:
            lo = mid + 1
        else:
            hi = mid
    trimmed = text[: max(0, lo - 1)]
    return trimmed + "\n(…已截斷)\n"


@dataclass
class PromptParts:
    system: str
    user: str
    behavior_rules_appendix: str = ""
    session_summary: str = ""
    retrieved_memory: str = ""
    history: Optional[List[Dict[str, str]]] = None


def build_prompt_messages(
    *,
    model: str,
    budget: Budget,
    parts: PromptParts,
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Return (messages, debug_meta)."""

    counter = TokenCounter(model=model)
    avail = budget.available_input_tokens

    debug: Dict[str, Any] = {
        "model": model,
        "available_input_tokens": avail,
        "included": {},
        "trimmed": {},
    }

    # 1) Hard keep: system + behavior rules appendix (but appendix may be token-trimmed)
    sys_text = parts.system
    br_text = parts.behavior_rules_appendix or ""

    # Allocate a max token slice for behavior rules appendix (cap)
    br_cap = min(300, max(0, avail // 6))  # e.g., ~16% of budget, up to 300 tokens
    br_text_final = _truncate_text_to_tokens(counter, br_text, br_cap) if br_text else ""

    system_full = sys_text + ("\n" + br_text_final if br_text_final else "")

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_full}]

    debug["included"]["system_tokens"] = counter.count_text(system_full)
    debug["included"]["behavior_rules_tokens"] = counter.count_text(br_text_final)

    # 2) Soft keep: session summary + retrieved memory (only if budget remains)
    remaining = avail - counter.count_messages(messages)

    def maybe_add_context(label: str, text: str, cap: int):
        nonlocal remaining
        if not text or remaining <= 0:
            debug["included"][f"{label}_tokens"] = 0
            return
        take = min(cap, remaining)
        final = _truncate_text_to_tokens(counter, text, take)
        if final.strip():
            messages.append({"role": "system", "content": final})
            used = counter.count_text(final) + 8
            remaining -= used
            debug["included"][f"{label}_tokens"] = counter.count_text(final)
            if final.endswith("(…已截斷)\n"):
                debug["trimmed"][label] = True

    maybe_add_context("session_summary", parts.session_summary or "", cap=min(350, avail // 5))
    maybe_add_context("retrieved_memory", parts.retrieved_memory or "", cap=min(350, avail // 5))

    # 3) Raw history tail: fill remaining with newest messages
    history = parts.history or []
    if history:
        # ensure no system messages duplicated
        hist = [m for m in history if m.get("role") in ("user", "assistant")]
        tail: List[Dict[str, str]] = []
        # add from end backwards
        for m in reversed(hist):
            cand = {"role": m.get("role", "user"), "content": m.get("content", "")}
            cand_tokens = counter.count_messages([cand])
            if cand_tokens + 16 > remaining:
                break
            tail.append(cand)
            remaining -= cand_tokens
        tail.reverse()
        messages.extend(tail)
        debug["included"]["history_messages"] = len(tail)
    else:
        debug["included"]["history_messages"] = 0

    # 4) User message (must include; trimmed only if absolutely necessary)
    remaining = avail - counter.count_messages(messages)
    user_text = parts.user
    if remaining <= 0:
        # emergency: truncate user
        user_text = _truncate_text_to_tokens(counter, user_text, max(64, avail // 10))
        debug["trimmed"]["user"] = True
    messages.append({"role": "user", "content": user_text})

    # Final safety trim: if still over budget, drop oldest history until within.
    max_allowed = avail
    while counter.count_messages(messages) > max_allowed and len(messages) > 2:
        # Preserve first system and last user; remove the earliest non-system (index 1)
        messages.pop(1)
        debug["trimmed"]["history_drop"] = True

    debug["included"]["final_total_tokens"] = counter.count_messages(messages)
    return messages, debug
