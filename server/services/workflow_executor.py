"""Workflow Executor — resolve variables + execute blocks in topological order.

Usage:
    executor = WorkflowExecutor()
    result = await executor.execute(workflow_data, user_input="...", user_context={})
"""

import json
import logging
import os
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger("MCP_Server.WorkflowExecutor")


class WorkflowExecutor:
    """Executes a matched workflow: resolves variables, runs blocks via UMA."""

    def __init__(self):
        pass

    async def execute(
        self,
        workflow: dict,
        user_input: str = "",
        user_context: dict = None,
        model_override: str = None,
    ) -> Dict[str, Any]:
        """
        Execute a workflow end-to-end.

        Args:
            workflow: Full workflow data (blocks, connections, variables, execution, etc.)
            user_input: The user's original message
            user_context: User identity context (name, dept, session_id, etc.)
            model_override: Override model for all blocks

        Returns:
            { status, workflow_id, results: [...], final_output, executed_at }
        """
        workflow_id = workflow.get("id", "unknown")
        variables = workflow.get("variables", [])
        blocks_data = workflow.get("blocks", [])
        connections = workflow.get("connections", [])
        execution = workflow.get("execution", {})
        security = workflow.get("security", {})

        logger.info(f"[WFExec] Start: {workflow_id} ({len(blocks_data)} blocks, {len(variables)} vars)")

        # ── Step 1: Resolve variables ──
        resolved_vars = self._resolve_variables(variables, user_input, user_context)
        logger.info(f"[WFExec] Resolved {len(resolved_vars)} variables")

        # ── Step 2: Topological sort ──
        blocks = {b["id"]: b for b in blocks_data}
        indeg = {bid: 0 for bid in blocks}
        adj = {bid: [] for bid in blocks}
        for c in connections:
            f, t = c.get("from"), c.get("to")
            if f in blocks and t in blocks:
                adj[f].append(t)
                indeg[t] = indeg.get(t, 0) + 1

        queue = deque([bid for bid, d in indeg.items() if d == 0])
        order = []
        while queue:
            bid = queue.popleft()
            order.append(bid)
            for nxt in adj.get(bid, []):
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)

        # ── Step 3: Execute blocks ──
        from server.dependencies.uma import get_uma_instance as get_uma
        uma = get_uma()

        default_model = model_override or execution.get("default_model") or os.getenv("OPENAI_MODEL", "gpt-4o")
        on_error = execution.get("on_error", "stop")
        max_retries = execution.get("max_retries", 3)
        token_budget = execution.get("token_budget", 0)
        total_tokens_used = 0

        results = []
        accumulated_context = user_input
        final_output = ""

        for bid in order:
            block = blocks[bid]
            block_type = block.get("type", "")

            # Skip control blocks
            if block_type in ("start", "end", "branch"):
                results.append({"block_id": bid, "type": block_type, "status": "skipped"})
                continue

            # Build skill name
            skill_name = block_type if block_type.startswith("mcp-") else f"mcp-{block_type}"

            # Per-block config
            block_config = block.get("config", {})
            block_model = block_config.get("model") or default_model
            block_timeout = block_config.get("timeout")
            block_on_error = block_config.get("on_error") or on_error

            # Resolve block parameters
            block_params = self._resolve_block_params(
                block_config.get("params", {}),
                resolved_vars,
                accumulated_context,
                user_input,
            )

            # If no explicit params, use accumulated context as input
            if not block_params:
                block_params = {"input": accumulated_context}

            logger.info(f"[WFExec] Block {bid} ({skill_name}): params={list(block_params.keys())}, model={block_model}")

            # Token budget check
            if token_budget > 0 and total_tokens_used >= token_budget:
                results.append({
                    "block_id": bid, "type": block_type, "skill": skill_name,
                    "status": "skipped", "reason": "token_budget_exceeded",
                })
                logger.warning(f"[WFExec] Block {bid} skipped: token budget exceeded ({total_tokens_used}/{token_budget})")
                continue

            # Execute with retry
            retries = 0
            success = False
            last_error = None

            while retries <= (max_retries if block_on_error == "retry" else 0):
                try:
                    result = uma.execute_tool_call(
                        skill_name,
                        json.dumps(block_params, ensure_ascii=False),
                    )

                    # ── Detect skill-level errors ──
                    # Skills may return {"status": "error", "message": "..."}
                    # even though the subprocess itself exited cleanly.
                    # Treat this as a real execution error so the block
                    # is marked failed and on_error policy is respected.
                    if isinstance(result, dict) and result.get("status") == "error":
                        skill_err_msg = result.get("message", result.get("error", "Skill returned error status"))
                        raise Exception(f"Skill error: {skill_err_msg}")

                    # Extract output text
                    output_text = ""
                    if isinstance(result, dict):
                        output_text = (
                            result.get("output", "")
                            or result.get("guide", "")
                            or result.get("content", "")
                            or json.dumps(result, ensure_ascii=False)
                        )
                    else:
                        output_text = str(result)

                    # Accumulate context for next block
                    if output_text:
                        accumulated_context = output_text[:3000]
                        final_output = output_text

                    results.append({
                        "block_id": bid,
                        "type": block_type,
                        "skill": skill_name,
                        "model_used": block_model,
                        "status": "success",
                        "output_preview": output_text[:300],
                    })
                    success = True
                    break

                except Exception as e:
                    last_error = str(e)
                    retries += 1
                    if retries <= max_retries and block_on_error == "retry":
                        logger.warning(f"[WFExec] Block {bid} retry {retries}/{max_retries}: {e}")
                        continue
                    break

            if not success:
                results.append({
                    "block_id": bid,
                    "type": block_type,
                    "skill": skill_name,
                    "model_used": block_model,
                    "status": "error",
                    "error": last_error,
                })
                logger.error(f"[WFExec] Block {bid} ({skill_name}) failed: {last_error}")

                if block_on_error == "stop":
                    logger.info(f"[WFExec] Stopping execution due to error in block {bid}")
                    break
                # "skip" continues to next block

        exec_result = {
            "status": "success",
            "workflow_id": workflow_id,
            "workflow_name": workflow.get("name", workflow_id),
            "blocks_executed": len(results),
            "results": results,
            "final_output": final_output[:2000],
            "executed_at": datetime.now().isoformat(),
        }

        # Audit log (non-blocking — failures should not affect execution result)
        if security.get("audit_log", True) is not False:
            try:
                from server.services.workflow_audit import log_workflow_execution
                uc = user_context or {}
                log_workflow_execution(
                    workflow_id=workflow_id,
                    workflow_name=workflow.get("name", workflow_id),
                    session_id=uc.get("session_id", ""),
                    result=exec_result,
                    trigger="api",
                    user_input=user_input[:200],
                    user_context=user_context,
                )
            except Exception as audit_err:
                logger.debug(f"[WFExec] Audit log failed: {audit_err}")

        return exec_result

    def _resolve_variables(
        self,
        variables: List[dict],
        user_input: str,
        user_context: dict = None,
    ) -> Dict[str, str]:
        """Resolve workflow variables to concrete values.

        Follows the dual-layer variable naming convention (integrated report §6):
          - _xxx        system reserved (_run_id, _started_at, _group_id, ...)
          - ALL_CAPS    global / shared (environment, API keys)
          - camelCase   execution-time (skill outputs, step results)

        User-defined variables whose name doesn't fit any convention trigger a
        warning in the log so they can be renamed gradually without breaking
        existing workflows.
        """
        from server.services.workflow_schema import classify_variable as _classify

        resolved = {}
        uc = user_context or {}

        # ── System variables (always available) ──
        # Naming follows the _xxx convention per the integrated report.
        now = datetime.now()
        resolved["_current_date"] = now.strftime("%Y-%m-%d")
        resolved["_current_time"] = now.strftime("%H:%M:%S")
        resolved["_started_at"] = now.isoformat()
        resolved["_user_name"] = uc.get("name", "User")
        resolved["_user_dept"] = uc.get("department", uc.get("dept_code", ""))
        resolved["_session_id"] = uc.get("session_id", "")
        resolved["_workflow_name"] = ""  # Will be set by caller
        resolved["_run_id"] = f"run_{int(now.timestamp()*1000)}"
        # Legacy aliases — kept so existing {{current_date}} etc. keep working
        # until all templates are updated in Phase 5. Logged once per run.
        resolved["current_date"] = resolved["_current_date"]
        resolved["current_time"] = resolved["_current_time"]
        resolved["user_name"] = resolved["_user_name"]
        resolved["user_dept"] = resolved["_user_dept"]
        resolved["session_id"] = resolved["_session_id"]
        resolved["workflow_name"] = resolved["_workflow_name"]

        for var in variables:
            name = var.get("name", "")
            if not name:
                continue

            # ── Naming convention check (1.3) ──
            kind = _classify(name)
            if kind == "invalid":
                logger.warning(
                    f"[WFExec] Variable '{name}' doesn't follow naming conventions "
                    f"(_system / ALL_CAPS_GLOBAL / camelCaseExecution). "
                    f"Keeping but this may be flagged by Gate 0 in future."
                )

            source = var.get("source", "user_input")
            default = var.get("default_value", "")

            if source == "fixed":
                resolved[name] = default
            elif source == "system":
                # Map system variable references (legacy syntax: default="{{current_date}}")
                resolved[name] = resolved.get(default.strip("{}"), default)
            elif source == "user_input":
                resolved[name] = user_input or default
            elif source == "previous_step":
                resolved[name] = default
            elif source == "auto":
                resolved[name] = default or user_input
            elif source == "secret":
                env_key = default.upper().replace(" ", "_") if default else name.upper()
                # Secrets MUST be ALL_CAPS per convention — warn if not
                if _classify(env_key) != "global":
                    logger.warning(
                        f"[WFExec] Secret variable '{name}' maps to non-ALL_CAPS env '{env_key}'"
                    )
                resolved[name] = os.getenv(env_key, "")
            else:
                resolved[name] = default

        return resolved

    def _resolve_block_params(
        self,
        param_config: dict,
        resolved_vars: Dict[str, str],
        accumulated_context: str,
        user_input: str,
    ) -> Dict[str, str]:
        """Resolve block parameter mappings to concrete values."""
        result = {}

        for param_name, param_def in param_config.items():
            source = param_def.get("source", "auto") if isinstance(param_def, dict) else "auto"
            value = param_def.get("value", "") if isinstance(param_def, dict) else ""

            if source == "variable":
                # Look up variable (strip {{ }})
                var_name = value.strip("{} ")
                result[param_name] = resolved_vars.get(var_name, value)
            elif source == "fixed":
                result[param_name] = value
            elif source == "previous_step":
                result[param_name] = accumulated_context
            elif source == "auto":
                # Let the value be the accumulated context (LLM will handle)
                result[param_name] = accumulated_context
            else:
                result[param_name] = value or accumulated_context

        return result


# ── Singleton ──
_executor_instance = None


def get_workflow_executor() -> WorkflowExecutor:
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = WorkflowExecutor()
    return _executor_instance
