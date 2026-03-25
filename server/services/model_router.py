"""
Model Router — LLM-as-a-Router + Token Estimation + Fallback
=============================================================
Uses a cheap nano/mini model as a gateway to classify user intent,
then routes to the optimal model tier based on task complexity.

Architecture:
  User Input → nano (classify) → route decision → actual adapter
                                                   ├─ nano  (casual)
                                                   ├─ mini  (standard)
                                                   └─ full  (complex)

Fallback:
  If estimated input tokens exceed the target model's safe budget,
  automatically downgrade to a cheaper model with higher TPM.

Environment Variables (all optional, sensible defaults provided):
  LINE_MODEL_NANO          = gpt-4.1-nano     (casual chat)
  LINE_MODEL_MINI          = gpt-4.1-mini     (standard tasks)
  LINE_MODEL_FULL          = gpt-4.1          (complex analysis)
  LINE_MODEL_ROUTER        = gpt-4.1-nano     (the classifier itself)
  LINE_ROUTER_ENABLED      = true             (set to false to skip router, use mini as default)
  LINE_MODEL_CHUNK_SUMMARY = gpt-4.1-mini     (chunk intermediate)
  LINE_MODEL_CHUNK_FINAL   = gpt-4.1          (chunk synthesis)
"""

import os
import json
import logging
import time
from typing import Optional

logger = logging.getLogger("MCP_Server.ModelRouter")

# ── Model Tier Configuration ────────────────────────────────────────────────

def _env(key: str, default: str) -> str:
    return os.getenv(key, default).strip()

def get_model_nano() -> str:
    return _env("LINE_MODEL_NANO", "gpt-4.1-nano")

def get_model_mini() -> str:
    return _env("LINE_MODEL_MINI", "gpt-4.1-mini")

def get_model_full() -> str:
    return _env("LINE_MODEL_FULL", "gpt-4.1")

def get_model_router() -> str:
    return _env("LINE_MODEL_ROUTER", "gpt-4.1-nano")

def get_model_chunk_summary() -> str:
    return _env("LINE_MODEL_CHUNK_SUMMARY", "gpt-4.1-mini")

def get_model_chunk_final() -> str:
    return _env("LINE_MODEL_CHUNK_FINAL", "gpt-4.1")

def is_router_enabled() -> bool:
    return _env("LINE_ROUTER_ENABLED", "true").lower() in ("true", "1", "yes")

# ── TPM Safety Budgets ──────────────────────────────────────────────────────
# Approximate safe input token budget per model tier (leaving room for output).
# These are PER-REQUEST budgets, not the TPM limit itself.

_MODEL_TPM_LIMITS = {
    # model-pattern → (TPM limit, safe single-request input budget)
    # IMPORTANT: longer patterns must come first to avoid partial prefix match
    # (e.g. "gpt-4.1-mini" must match before "gpt-4.1")
    "gpt-4.1-mini":    (200_000,  25_000),
    "gpt-4.1-nano":    (200_000,  25_000),
    "gpt-4o-mini":     (200_000,  25_000),
    "o4-mini":         (200_000,  25_000),
    "gpt-4.1":         (30_000,   8_000),
    "gpt-4o":          (30_000,   8_000),
    "o3":              (30_000,   8_000),
}

def _get_safe_budget(model: str) -> int:
    """Return the safe per-request input token budget for a model."""
    for pattern, (_, budget) in _MODEL_TPM_LIMITS.items():
        if pattern in model:
            return budget
    return 8_000  # conservative default


# ── Token Estimation ────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    Fast heuristic token estimation (no tiktoken dependency).
    Rule of thumb: ~1 token per 3 chars for CJK, ~1 token per 4 chars for English.
    We use a blended ratio of 1:3.5 which is conservative enough.
    """
    if not text:
        return 0
    return max(1, len(text) * 10 // 35)  # ≈ len/3.5


def estimate_request_tokens(
    messages: list,
    tool_schemas: list | None = None,
    max_output_tokens: int = 2048,
) -> int:
    """
    Estimate the total token footprint of an OpenAI API request.
    OpenAI TPM counts: input_tokens + max_output_tokens reservation.
    """
    input_tokens = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            input_tokens += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                    input_tokens += estimate_tokens(part.get("text", ""))
                # Images: ~1000 tokens for a typical base64 image
                elif isinstance(part, dict) and part.get("type") == "input_image":
                    input_tokens += 1000

    if tool_schemas:
        # Each tool schema is roughly 100-300 tokens
        schema_text = json.dumps(tool_schemas, ensure_ascii=False)
        input_tokens += estimate_tokens(schema_text)

    return input_tokens + max_output_tokens


# ── LLM-as-a-Router ────────────────────────────────────────────────────────

_ROUTER_SYSTEM_PROMPT = """你是一個任務分類器。根據使用者輸入，判斷任務複雜度並回傳 JSON。

分類規則：
- "nano"：閒聊、打招呼、簡單是非題、表情符號、感謝、一句話問答
- "mini"：一般問答、翻譯、摘要、單一工具呼叫、中等長度分析、畫圖、生成圖片、製作圖表、繪製插圖、設定排程推送、設定提醒、定時推送
- "full"：深度文件分析、多步驟推理、產出報告、跨資料比較、複雜程式碼

重要：
- 任何涉及「畫」「圖」「插圖」「圖表」「生成圖片」的請求，至少歸類為 "mini"（需要工具呼叫）。
- 任何涉及「推送」「排程」「定時」「提醒我」「每天…點」「固定推送」的請求，至少歸類為 "mini"（需要工具呼叫）。

嚴格回傳以下 JSON 格式，不要回傳任何其他文字：
{"tier": "nano"} 或 {"tier": "mini"} 或 {"tier": "full"}"""


def _call_router_llm(user_input: str, openai_client) -> str:
    """
    Use the cheapest model to classify task complexity.
    Returns: "nano", "mini", or "full".
    Falls back to "mini" on any error.
    """
    try:
        t0 = time.time()
        response = openai_client.chat.completions.create(
            model=get_model_router(),
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_input[:500]},  # Cap input to save tokens
            ],
            temperature=0,
            max_tokens=20,  # Only need {"tier":"xxx"}
        )
        raw = response.choices[0].message.content.strip()
        elapsed = time.time() - t0
        logger.info(f"[Router] LLM classification took {elapsed:.2f}s → {raw}")

        # Parse JSON response
        data = json.loads(raw)
        tier = data.get("tier", "mini")
        if tier in ("nano", "mini", "full"):
            return tier
        return "mini"

    except json.JSONDecodeError:
        # Try to extract tier from raw text
        raw_lower = raw.lower() if 'raw' in dir() else ""
        if "nano" in raw_lower:
            return "nano"
        if "full" in raw_lower:
            return "full"
        return "mini"
    except Exception as e:
        logger.warning(f"[Router] LLM classification failed ({e}), defaulting to mini")
        return "mini"


_TIER_TO_MODEL = {
    "nano": get_model_nano,
    "mini": get_model_mini,
    "full": get_model_full,
}


def route_model(
    user_input: str,
    openai_client,
    has_file: bool = False,
    is_chunked_final: bool = False,
) -> tuple[str, str]:
    """
    Main entry point: determine the optimal model for a LINE Bot request.

    Returns:
      (model_name, tier)  where tier ∈ {"nano", "mini", "full", "file", "chunk_final"}

    Priority order:
      1. Chunked final synthesis → always full model
      2. File attachment → mini (decent quality, high TPM)
      3. Router disabled → default to mini
      4. LLM-as-a-Router → nano classifies → route accordingly
    """
    # Chunked final synthesis always needs the best model
    if is_chunked_final:
        model = get_model_chunk_final()
        logger.info(f"[Router] Chunked final → {model}")
        return model, "chunk_final"

    # File attachment: standard tier (needs decent comprehension)
    if has_file:
        model = get_model_mini()
        logger.info(f"[Router] File attached → {model}")
        return model, "file"

    # Router disabled: safe default
    if not is_router_enabled():
        model = get_model_mini()
        logger.info(f"[Router] Disabled, default → {model}")
        return model, "mini"

    # Hard-rule override: tool-dependent intents must not be nano
    _input_lower = user_input.lower()
    _TOOL_KEYWORDS = [
        "畫", "繪", "插圖", "圖表", "製圖", "生成圖", "做成圖",
        "搜尋", "查詢", "建立檔案", "產生報告",
        "推送", "排程", "定時", "提醒我", "每天", "每週", "每日", "固定",
    ]
    _needs_tools = any(kw in _input_lower for kw in _TOOL_KEYWORDS)

    # LLM-as-a-Router
    tier = _call_router_llm(user_input, openai_client)

    # Upgrade nano → mini if tool-dependent keywords detected
    if tier == "nano" and _needs_tools:
        tier = "mini"
        logger.info(f"[Router] Upgraded nano→mini (tool keywords detected in '{user_input[:30]}')")

    model = _TIER_TO_MODEL.get(tier, get_model_mini)()
    logger.info(f"[Router] '{user_input[:40]}...' → tier={tier} → {model}")
    return model, tier


# ── Token-based Fallback ────────────────────────────────────────────────────

_FALLBACK_CHAIN = {
    # model → cheaper fallback with higher TPM
    "gpt-4.1":      "gpt-4.1-mini",
    "gpt-4o":       "gpt-4.1-mini",
    "gpt-4.1-mini": "gpt-4.1-nano",
    "o3":           "o4-mini",
}


def apply_token_fallback(
    model: str,
    messages: list,
    tool_schemas: list | None = None,
    max_output_tokens: int = 2048,
) -> str:
    """
    Pre-flight token estimation. If estimated tokens exceed the model's
    safe per-request budget, downgrade to a cheaper model.
    Returns the (possibly downgraded) model name.
    """
    estimated = estimate_request_tokens(messages, tool_schemas, max_output_tokens)
    budget = _get_safe_budget(model)

    if estimated <= budget:
        logger.info(f"[Fallback] {model}: {estimated} est. tokens ≤ {budget} budget → OK")
        return model

    fallback = _FALLBACK_CHAIN.get(model)
    if fallback:
        fb_budget = _get_safe_budget(fallback)
        logger.warning(
            f"[Fallback] {model}: {estimated} est. tokens > {budget} budget → "
            f"downgrade to {fallback} (budget={fb_budget})"
        )
        return fallback

    # No fallback available, proceed and hope for the best
    logger.warning(
        f"[Fallback] {model}: {estimated} est. tokens > {budget} budget, "
        f"no fallback available — proceeding anyway"
    )
    return model
