"""Phase 2 — Four-layer Gate checking for workflows.

Gate 0: Static check at save time
Gate 1: Pre-execution dynamic check (env / env_ready / inputs)
Gate 2: Per-step check (upstream output / skill still available)
Gate 3: Post-execution logging (run log, error routing, promotion signal)

Design rationale (integrated report §4.3):
  The 4 gates split cost vs coverage across the workflow lifecycle.
  Heavy structural validation runs once at save time; light runtime
  gates keep execution fast. Every failure produces a human-readable
  message so non-technical users can self-serve fixes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("MCP_Server.WorkflowGates")


# Regex to find ${var} or ${GLOBAL.VAR} references in input_map values
_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")


def _extract_var_refs(value: Any) -> List[str]:
    """Recursively find all ${var} references in a value."""
    if isinstance(value, str):
        return _VAR_REF_RE.findall(value)
    if isinstance(value, dict):
        out: List[str] = []
        for v in value.values():
            out.extend(_extract_var_refs(v))
        return out
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_extract_var_refs(v))
        return out
    return []


# ═══════════════════════════════════════════════════════════════════════
# Gate 0 — Static validation at save time
# ═══════════════════════════════════════════════════════════════════════

def gate_0_validate(workflow: Dict[str, Any], uma) -> Tuple[bool, List[str]]:
    """Static structural validation before persistence.

    Strict mode: rejects workflows that can't possibly execute:
      - Zero skill blocks (only control nodes or empty canvas)
      - Missing display_name / using the default placeholder
      - Any schema-level format issue

    Deeper checks:
      - All referenced skill_id exist in UMA registry
      - Parallel branches have merge_output_var set
      - Step_id uniqueness (already done by validate_workflow)
      - Sub-workflow references (soft check — may not exist yet)

    Returns: (ok, errors). errors is a list of human-readable messages.
    """
    errors: List[str] = []

    # Schema-level validation (Pydantic-ish)
    try:
        from server.services.workflow_schema import validate_workflow
        ok, schema_errs = validate_workflow(workflow)
        if not ok:
            errors.extend([f"格式錯誤：{e}" for e in schema_errs])
    except Exception as e:
        errors.append(f"Schema 驗證失敗：{e}")
        return False, errors

    # ── Strict content requirements (drafts are NOT saved) ──
    steps = workflow.get("steps") or []
    blocks = workflow.get("blocks") or []
    skill_block_count = sum(
        1 for b in blocks
        if isinstance(b, dict) and b.get("type") not in ("start", "end", "branch")
    )
    display_name = (workflow.get("display_name") or "").strip()

    if not display_name or display_name in ("新工作流", "Untitled Flow"):
        errors.append("請先為工作流命名（不能使用預設名稱「新工作流」），再儲存")

    if len(steps) == 0 and skill_block_count == 0:
        errors.append("工作流至少需要一個技能節點 — 請從左側 Palette 拖入節點後再儲存")

    # Show the most actionable errors first if basic requirements fail
    if errors:
        return False, errors

    # Check each step's skill exists + parallel branches merge
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = step.get("step_id", "?")
        step_type = step.get("type", "sequential")

        if step_type == "sequential":
            skill_id = step.get("skill_id", "")
            if not skill_id:
                errors.append(f"步驟 {step_id} 缺少 skill_id")
                continue
            # Registry lookup
            if uma and hasattr(uma, "registry"):
                skill = uma.registry.get_skill(skill_id)
                if not skill:
                    errors.append(f"步驟 {step_id} 使用的技能 '{skill_id}' 不存在於技能庫")

        elif step_type == "parallel":
            branches = step.get("branches") or []
            if not branches:
                errors.append(f"並行步驟 {step_id} 缺少 branches（至少一個）")
            if not step.get("merge_output_var"):
                errors.append(f"並行步驟 {step_id} 缺少 merge_output_var — 並行必須匯合")
            # Recurse into branches
            for i, branch in enumerate(branches):
                if isinstance(branch, dict):
                    b_skill = branch.get("skill_id")
                    if b_skill and uma and hasattr(uma, "registry"):
                        if not uma.registry.get_skill(b_skill):
                            errors.append(f"步驟 {step_id} 分支 {i+1} 的技能 '{b_skill}' 不存在")

        elif step_type == "sub_workflow":
            sub_id = step.get("sub_workflow_id")
            if not sub_id:
                errors.append(f"子工作流步驟 {step_id} 缺少 sub_workflow_id")
            # Existence of sub-workflow is checked at Gate 1 (it may be saved later)

    return len(errors) == 0, errors


# ═══════════════════════════════════════════════════════════════════════
# Gate 1 — Pre-execution dynamic check
# ═══════════════════════════════════════════════════════════════════════

def gate_1_pre_execute(
    workflow: Dict[str, Any],
    user_inputs: Optional[Dict[str, Any]] = None,
    uma=None,
) -> Tuple[bool, Dict[str, Any]]:
    """Runtime readiness check before launching the workflow.

    Returns (ok, info) where info contains:
        errors          — hard blockers (server-side config issue)
        missing_inputs  — soft blockers (user can supply via wizard)
        warnings        — informational

    Semantics (integrated report 決策 C):
      - env_requirements missing         → errors (user can't fix)
      - skill env_ready=false            → errors (user can't fix)
      - required global_inputs missing   → missing_inputs (wizard can collect)
      - sub_workflow_id references       → errors if target doesn't exist
    """
    info: Dict[str, Any] = {"errors": [], "missing_inputs": [], "warnings": []}
    user_inputs = user_inputs or {}
    variables = workflow.get("variables") or {}

    # 1.1 Collect all required env vars (workflow-level + per-skill),
    # deduped so the user sees each missing var once.
    missing_envs: set = set()
    for env_name in (variables.get("env_requirements") or []):
        if not os.environ.get(env_name):
            missing_envs.add(env_name)

    # 1.2 Skill-level checks: _env_ready + skill's own env_requirements
    # The registry's _env_ready only checks Python packages + files, NOT
    # OS env vars declared in the skill's SKILL.md frontmatter. We check
    # those here so users don't discover TAVILY_API_KEY missing only
    # after 3 retries inside the executor.
    if uma and hasattr(uma, "registry"):
        for step in workflow.get("steps") or []:
            skill_id = _step_skill_id(step)
            if not skill_id:
                continue
            skill = uma.registry.get_skill(skill_id)
            if not skill:
                info["errors"].append(f"技能 '{skill_id}' 已不存在")
                continue
            meta = skill.get("metadata") or {}
            # Python deps / file deps
            if not meta.get("_env_ready", False):
                missing = meta.get("_missing_deps") or []
                if missing:
                    info["errors"].append(f"技能 '{skill_id}' 環境未就緒（缺少：{', '.join(missing)}）")
                else:
                    info["errors"].append(f"技能 '{skill_id}' 環境未就緒")
            # OS env vars from SKILL.md
            for env_name in (meta.get("env_requirements") or []):
                if not os.environ.get(env_name):
                    missing_envs.add(env_name)

    # Emit env var errors (deduped)
    for env_name in sorted(missing_envs):
        info["errors"].append(f"環境變數 {env_name} 未設定（請聯絡管理員）")

    # 1.3 Required global_inputs provided by caller
    required_inputs = _collect_required_inputs(variables)
    for input_def in required_inputs:
        name = input_def["name"]
        if name not in user_inputs or user_inputs[name] in (None, ""):
            info["missing_inputs"].append(input_def)

    # 1.4 sub_workflow references exist
    # Check against project-root workflows folder
    pr = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
    wf_base = pr / "workspace" / "workflows"
    for step in workflow.get("steps") or []:
        if step.get("type") == "sub_workflow":
            sub_id = step.get("sub_workflow_id", "")
            if not sub_id:
                continue
            found = False
            for scope in ("system", "department", "personal"):
                for p in (wf_base / scope).rglob(f"{sub_id}.json") if (wf_base / scope).exists() else []:
                    found = True
                    break
                if found:
                    break
            if not found:
                info["errors"].append(f"子工作流 '{sub_id}' 不存在")

    # 1.5 Empty workflow sanity check
    if not (workflow.get("steps") or []):
        info["errors"].append("工作流沒有任何步驟 — 請至少加入一個技能節點")

    ok = len(info["errors"]) == 0 and len(info["missing_inputs"]) == 0
    return ok, info


def _step_skill_id(step: Dict[str, Any]) -> Optional[str]:
    """Extract skill_id for a step (sequential or inner branch)."""
    if step.get("type") == "sequential":
        return step.get("skill_id")
    # parallel/sub_workflow handled elsewhere
    return None


def _collect_required_inputs(variables: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build a list of required inputs the user must provide at runtime.

    Sources considered (union):
      1. `global_inputs` — names the user declared as required inputs
      2. `definitions[]` where source=user_input AND required=true AND no default

    This fixes the UX gap where the variables tab stores data in definitions[]
    but Gate 1 used to only inspect global_inputs, making required user_input
    variables invisible and letting workflows execute with empty values.

    Returns one {name, type, description, required} entry per missing input.
    Already-satisfied inputs (with default_value or source=fixed/secret) are skipped.
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()

    definitions = [
        d for d in (variables.get("definitions") or [])
        if isinstance(d, dict) and d.get("name")
    ]
    defs_by_name = {d["name"]: d for d in definitions}

    # Source 1: explicit global_inputs list (plain names)
    for name in (variables.get("global_inputs") or []):
        if name in seen:
            continue
        defn = defs_by_name.get(name) or {}
        if defn.get("default_value"):
            continue
        if defn.get("source") in ("fixed", "secret"):
            continue
        out.append({
            "name": name,
            "type": defn.get("type", "string"),
            "description": defn.get("description", ""),
            "required": True,
        })
        seen.add(name)

    # Source 2: definitions entries that are user-input + required + no default
    for defn in definitions:
        name = defn["name"]
        if name in seen:
            continue
        if (defn.get("source") == "user_input"
                and defn.get("required", False)
                and not defn.get("default_value")):
            out.append({
                "name": name,
                "type": defn.get("type", "string"),
                "description": defn.get("description", ""),
                "required": True,
            })
            seen.add(name)

    return out


# ═══════════════════════════════════════════════════════════════════════
# Gate 2 — Per-step real-time check
# ═══════════════════════════════════════════════════════════════════════

def gate_2_before_step(
    step: Dict[str, Any],
    execution_vars: Dict[str, Any],
    uma,
) -> Tuple[bool, str]:
    """Cheap check right before a step runs.

    Returns (ok, reason). Caller respects step.on_fail on failure:
        abort      — stop the whole workflow
        retry_once — sleep 2s + retry
        continue   — log and move on
        skip       — same as continue, no error
    """
    # 2.1 Skill still env_ready
    skill_id = _step_skill_id(step)
    if skill_id and uma and hasattr(uma, "registry"):
        skill = uma.registry.get_skill(skill_id)
        if not skill:
            return False, f"技能 '{skill_id}' 已被移除"
        meta = skill.get("metadata") or {}
        if not meta.get("_env_ready", False):
            missing = meta.get("_missing_deps") or []
            return False, (
                f"技能 '{skill_id}' 環境已失效"
                + (f"（缺：{', '.join(missing)}）" if missing else "")
            )

    # 2.2 Referenced vars all exist in execution state
    input_map = step.get("input_map") or {}
    for param, value in input_map.items():
        for ref in _extract_var_refs(value):
            if ref.startswith("_"):
                continue  # system vars (always present)
            if ref.startswith("GLOBAL."):
                env_key = ref.split(".", 1)[1]
                if not os.environ.get(env_key):
                    return False, f"全域變數 GLOBAL.{env_key} 未設定"
                continue
            if ref not in execution_vars:
                return False, f"變數 '{ref}' 在前驅步驟中尚未產出"

    return True, ""


# ═══════════════════════════════════════════════════════════════════════
# Gate 3 — Post-execution logging
# ═══════════════════════════════════════════════════════════════════════

def new_run_id() -> str:
    """Generate a unique execution run identifier (visible in logs)."""
    return f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def gate_3_log_run(
    workflow: Dict[str, Any],
    run_id: str,
    result: Dict[str, Any],
    project_root: Optional[Path] = None,
) -> Optional[Path]:
    """Write execution log + handle on_error routing + promotion signal.

    Log path: workspace/workflows/logs/{workflow_id}/{run_id}.json
    Keeps last 50 logs per workflow (oldest auto-pruned).

    Args:
        workflow: The workflow definition that was executed
        run_id:   Unique ID for this execution (generated by new_run_id())
        result:   Execution outcome from WorkflowExecutor with keys like
                  status, started_at, ended_at, duration_ms, blocks_executed,
                  final_output, errors, step_results
        project_root: Override for testing; defaults to PROJECT_ROOT env

    Returns the log file path written, or None on failure.
    """
    if project_root is None:
        project_root = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))

    wf_id = workflow.get("workflow_id") or "unknown_workflow"
    logs_dir = project_root / "workspace" / "workflows" / "logs" / wf_id
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Truncate long outputs to keep log size reasonable
    final_output = result.get("final_output") or ""
    if len(final_output) > 2000:
        final_output = final_output[:2000] + "… (truncated)"

    log_entry = {
        "run_id": run_id,
        "workflow_id": wf_id,
        "display_name": workflow.get("display_name", ""),
        "scope": workflow.get("scope", "personal"),
        "owner": workflow.get("owner", ""),
        "source": workflow.get("source", ""),
        "started_at": result.get("started_at"),
        "ended_at": result.get("ended_at", time.time()),
        "duration_ms": result.get("duration_ms", 0),
        "status": result.get("status", "unknown"),  # success / error / cancelled / partial
        "blocks_executed": result.get("blocks_executed", 0),
        "total_tokens": result.get("total_tokens", 0),
        "final_output": final_output,
        "errors": result.get("errors") or [],
        "step_results": [
            {
                "step_id": s.get("step_id") or s.get("block_id"),
                "skill": s.get("skill"),
                "status": s.get("status"),
                "error": s.get("error") or s.get("reason") or "",
                "output_preview": (s.get("output_preview") or "")[:200],
            }
            for s in (result.get("step_results") or result.get("results") or [])
        ],
    }

    log_path = logs_dir / f"{run_id}.json"
    try:
        log_path.write_text(json.dumps(log_entry, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"[Gate3] Failed to write log {log_path}: {e}")
        return None

    # Prune old logs — keep last 50
    try:
        all_logs = sorted(logs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in all_logs[50:]:
            old.unlink()
    except Exception:
        pass

    # ── Promotion signal for LLM one-shot (Phase 6 will consume) ──
    if workflow.get("source") == "llm_generated" and result.get("status") == "success":
        try:
            promo_path = project_root / "workspace" / "workflows" / "promotion_queue.json"
            promo_path.parent.mkdir(parents=True, exist_ok=True)
            promo_data = []
            if promo_path.exists():
                try:
                    promo_data = json.loads(promo_path.read_text(encoding="utf-8"))
                except Exception:
                    promo_data = []
            promo_data.append({
                "run_id": run_id,
                "workflow_id": wf_id,
                "user_id": workflow.get("metadata", {}).get("created_by"),
                "original_prompt": workflow.get("metadata", {}).get("original_prompt", ""),
                "at": time.time(),
            })
            # Keep last 100 promotion candidates
            promo_data = promo_data[-100:]
            promo_path.write_text(json.dumps(promo_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[Gate3] Promotion signal save failed: {e}")

    return log_path


def list_recent_logs(workflow_id: str, project_root: Optional[Path] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Return the last N run logs for a workflow (newest first)."""
    if project_root is None:
        project_root = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
    logs_dir = project_root / "workspace" / "workflows" / "logs" / workflow_id
    if not logs_dir.exists():
        return []
    out: List[Dict[str, Any]] = []
    files = sorted(logs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:limit]:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out
