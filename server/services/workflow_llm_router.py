"""
Workflow LLM Router — Smart parameter mapping between blocks.

Responsibilities:
1. Route parameters between workflow blocks using LLM
2. Select appropriate model per-block based on recommended_models
3. TPM safety: auto-downgrade if budget exceeded
4. Multi-provider: OpenAI / Gemini / Claude
"""

import json
import logging
import os
import time
from typing import Dict, Any, Optional

logger = logging.getLogger("MCP_Server.WorkflowRouter")

# ── Skill Parameter Knowledge Base ──────────────────────────────────────────
# Maps skill names to their required parameters (what main.py reads)
SKILL_PARAM_MAP = {
    "mcp-web-search": {
        "required": ["query"],
        "optional": ["target_url", "search_depth", "include_domains"],
    },
    "mcp-python-executor": {
        "required": ["code"],
        "optional": [],
    },
    "mcp-image-generator": {
        "required": ["prompt"],
        "optional": ["size", "quality"],
    },
    "mcp-schedule-manager": {
        "required": ["action"],
        "optional": ["task_type", "name", "cron", "config", "task_id"],
    },
    "mcp-google-calendar": {
        "required": ["action"],
        "optional": ["start", "end", "title", "location", "description", "event_id"],
    },
    "mcp-meeting-to-notion": {
        "required": ["transcript"],
        "optional": ["language", "department_code", "meeting_date"],
    },
    "mcp-transcribe": {
        "required": ["file_path"],
        "optional": [],
    },
}

# ── Model Downgrade Chains (per provider) ───────────────────────────────────
DOWNGRADE_CHAINS = {
    "openai": ["gpt-4.1", "gpt-4o", "gpt-4.1-mini", "gpt-4.1-nano"],
    "gemini": ["gemini-2.0-flash"],
    "claude": ["claude-sonnet-4-6", "claude-haiku-4-5"],
}

# Safe per-request token budgets (from model_router.py)
MODEL_BUDGETS = {
    "gpt-4.1": 20000,
    "gpt-4o": 20000,
    "gpt-4.1-mini": 25000,
    "gpt-4.1-nano": 25000,
    "o4-mini": 25000,
    "o3": 20000,
    "gemini-2.0-flash": 30000,
    "claude-sonnet-4-6": 20000,
    "claude-haiku-4-5": 25000,
}

# ── System Prompt for Parameter Routing ─────────────────────────────────────
PARAM_ROUTER_SYSTEM = """你是 AgentK Workflow 的參數映射助手。

任務：根據「上一步的輸出」和「下一個技能的需求」，產生下一個技能所需的 JSON 參數。

規則：
1. 只回傳 JSON（不要包含任何解釋文字或 markdown）
2. 參數名稱必須嚴格匹配【必要參數】列表中的 key
3. 如果上一步沒有輸出，根據使用者的初始指令推理
4. 保持簡潔，只填必要參數 + 有明確值的選填參數
5. 字串值直接填入，不要用佔位符"""


def _estimate_tokens(text: str) -> int:
    """Rough token estimation: ~1 token per 3.5 chars."""
    return max(1, len(text) // 3)


def _detect_provider(model: str) -> str:
    """Detect provider from model name."""
    m = model.lower()
    if "gpt" in m or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return "openai"
    elif "gemini" in m:
        return "gemini"
    elif "claude" in m:
        return "claude"
    return "openai"


def _downgrade_model(model: str) -> Optional[str]:
    """Get next cheaper model in the same provider chain."""
    provider = _detect_provider(model)
    chain = DOWNGRADE_CHAINS.get(provider, [])
    try:
        idx = chain.index(model)
        if idx + 1 < len(chain):
            return chain[idx + 1]
    except ValueError:
        pass
    return None


class WorkflowLLMRouter:
    """Routes parameters between workflow blocks using LLM."""

    def __init__(self, default_model: str = None):
        self.default_model = default_model or os.getenv("OPENAI_MODEL", "gpt-4o")
        self._last_call_time = 0
        self._min_interval = 1.0  # seconds between LLM calls (TPM protection)

    def select_model(self, skill_name: str, recommended_models: dict = None,
                     block_override: str = None, input_size: int = 0) -> str:
        """Select the best model for a block.

        Priority:
        1. Block-level override (user set on specific block)
        2. Skill's recommended_models.{provider}
        3. Default model
        + TPM safety downgrade
        """
        # Block override takes highest priority
        if block_override:
            model = block_override
        elif recommended_models:
            provider = _detect_provider(self.default_model)
            model = recommended_models.get(provider, self.default_model)
        else:
            model = self.default_model

        # TPM safety: if estimated input is too large, downgrade
        budget = MODEL_BUDGETS.get(model, 20000)
        estimated = input_size + 500  # +500 for system prompt overhead
        while estimated > budget * 0.8:  # 80% threshold
            downgraded = _downgrade_model(model)
            if not downgraded:
                break
            logger.info(f"[WF Router] Downgrading {model} → {downgraded} (est={estimated} > budget={budget})")
            model = downgraded
            budget = MODEL_BUDGETS.get(model, 20000)

        return model

    async def route_params(self, context: Dict[str, Any], model: str = None) -> Dict[str, Any]:
        """Use LLM to generate parameters for the next skill.

        Args:
            context: {initial_prompt, previous_output, skill_name, skill_description, block_config}
            model: specific model to use

        Returns:
            JSON dict of parameters for the skill
        """
        skill_name = context.get("skill_name", "")
        param_info = SKILL_PARAM_MAP.get(skill_name, {})
        required = param_info.get("required", [])
        optional = param_info.get("optional", [])

        # Build user prompt
        user_prompt = self._build_prompt(context, required, optional)

        # Rate limiting
        elapsed = time.time() - self._last_call_time
        if elapsed < self._min_interval:
            import asyncio
            await asyncio.sleep(self._min_interval - elapsed)

        model = model or self.default_model
        provider = _detect_provider(model)

        try:
            if provider == "openai":
                result = await self._call_openai(model, user_prompt)
            elif provider == "gemini":
                result = await self._call_gemini(model, user_prompt)
            elif provider == "claude":
                result = await self._call_claude(model, user_prompt)
            else:
                result = await self._call_openai(model, user_prompt)

            self._last_call_time = time.time()
            logger.info(f"[WF Router] {skill_name} params via {model}: {list(result.keys())}")
            return result

        except Exception as e:
            logger.warning(f"[WF Router] LLM routing failed ({model}): {e}")
            # Fallback: try to build params from context without LLM
            return self._fallback_params(context, required)

    def _build_prompt(self, ctx: Dict, required: list, optional: list) -> str:
        prev = ctx.get("previous_output", "")
        if len(prev) > 2000:
            prev = prev[:2000] + "\n...(截斷)"

        prompt = f"""【使用者的初始指令】
{ctx.get('initial_prompt', '(無)')}

【上一個節點的輸出】
{prev or '(無，這是第一個執行的技能節點)'}

【下一個技能】
名稱：{ctx.get('skill_name', '')}
描述：{ctx.get('skill_description', '')[:300]}
必要參數：{json.dumps(required, ensure_ascii=False)}
選填參數：{json.dumps(optional, ensure_ascii=False)}

【節點設定】
{json.dumps(ctx.get('block_config', {}), ensure_ascii=False) or '(無額外設定)'}

請產生此技能所需的 JSON 參數："""
        return prompt

    def _fallback_params(self, ctx: Dict, required: list) -> Dict:
        """Generate basic params without LLM when routing fails."""
        prev = ctx.get("previous_output", "")
        init = ctx.get("initial_prompt", "")
        content = prev or init

        params = {}
        for key in required:
            if key in ("query", "prompt", "input", "transcript"):
                params[key] = content[:3000] if content else ""
            elif key == "code":
                params[key] = f"# Auto-generated\nprint({json.dumps(content[:500], ensure_ascii=False)})"
            elif key == "action":
                params[key] = "list"
            elif key == "file_path":
                params[key] = ""
            else:
                params[key] = content[:1000] if content else ""

        # Merge block config
        config = ctx.get("block_config", {})
        if config:
            params.update(config)

        return params

    # ── Provider-specific calls ──────────────────────────────────────────

    async def _call_openai(self, model: str, user_prompt: str) -> Dict:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PARAM_ROUTER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)

    async def _call_gemini(self, model: str, user_prompt: str) -> Dict:
        import google.generativeai as genai
        api_key = os.getenv("GEMINI_API_KEY", "")
        if api_key:
            genai.configure(api_key=api_key)
        gm = genai.GenerativeModel(model)
        resp = gm.generate_content(
            PARAM_ROUTER_SYSTEM + "\n\n" + user_prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0,
                max_output_tokens=500,
                response_mime_type="application/json",
            ),
        )
        return json.loads(resp.text)

    async def _call_claude(self, model: str, user_prompt: str) -> Dict:
        from anthropic import Anthropic
        client = Anthropic()
        resp = client.messages.create(
            model=model,
            system=PARAM_ROUTER_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=500,
            temperature=0,
        )
        text = resp.content[0].text.strip()
        # Extract JSON from possible markdown wrapping
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(text)


# ── Skill Complexity Analyzer ───────────────────────────────────────────────

def analyze_skill_complexity(skill_md_content: str, has_references: bool = False,
                             has_scripts: bool = False) -> Dict[str, str]:
    """Analyze SKILL.md to recommend models per provider.

    Returns: {"openai": "gpt-4.1-mini", "gemini": "gemini-2.0-flash", "claude": "claude-sonnet-4-6"}
    """
    tokens = _estimate_tokens(skill_md_content)
    body_length = len(skill_md_content)

    # Scoring: higher = needs stronger model
    score = 0

    # Body length
    if body_length > 5000:
        score += 3  # Long prompt = complex
    elif body_length > 2000:
        score += 2
    elif body_length > 500:
        score += 1

    # References layer = more context
    if has_references:
        score += 2

    # Executable (has scripts) = simpler routing (just pass params)
    # Semantic (no scripts) = needs full LLM reasoning
    if not has_scripts:
        score += 2  # Semantic mode needs stronger model

    # Complexity keywords
    complexity_keywords = ["多步驟", "推理", "分析", "比對", "深度", "完整", "詳盡",
                           "multi-step", "analysis", "reasoning", "comprehensive"]
    if any(kw in skill_md_content.lower() for kw in complexity_keywords):
        score += 1

    # Map score to model recommendations
    if score >= 6:
        # Advanced
        return {
            "openai": "gpt-4.1",
            "gemini": "gemini-2.0-flash",
            "claude": "claude-sonnet-4-6",
        }
    elif score >= 3:
        # Standard
        return {
            "openai": "gpt-4.1-mini",
            "gemini": "gemini-2.0-flash",
            "claude": "claude-sonnet-4-6",
        }
    else:
        # Light
        return {
            "openai": "gpt-4.1-nano",
            "gemini": "gemini-2.0-flash",
            "claude": "claude-haiku-4-5",
        }
