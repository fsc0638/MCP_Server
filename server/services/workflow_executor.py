"""Workflow Executor — resolve variables + execute blocks in topological order.

Usage:
    executor = WorkflowExecutor()
    result = await executor.execute(workflow_data, user_input="...", user_context={})
"""

import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger("MCP_Server.WorkflowExecutor")


class WorkflowExecutor:
    """Executes a matched workflow: resolves variables, runs blocks via UMA.

    Phase 4 additions:
      - Parallel step support (type=parallel, branches run concurrently)
      - Sub-workflow invocation (type=sub_workflow) with cycle detection
      - Execution call stack tracks workflow_ids currently running to
        prevent infinite recursion (MAGELLAN BLOCKS's warning).
    """

    # Class-level execution stack — tracks in-flight workflow_ids across
    # the whole call tree, so a sub_workflow that tries to call its own
    # ancestor gets rejected.
    _execution_stack: List[str] = []
    _MAX_STACK_DEPTH = 5  # matches integrated report §4 recommendation

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
        # Phase 1 v2 fields preferred; fall back to legacy for compat
        workflow_id = workflow.get("workflow_id") or workflow.get("id", "unknown")
        raw_vars = workflow.get("variables", [])
        # v2 variables is dict {definitions, global_inputs, env_requirements};
        # the resolver still takes a list of definitions, so normalize here.
        if isinstance(raw_vars, dict):
            variables = raw_vars.get("definitions") or []
        else:
            variables = raw_vars or []
        blocks_data = workflow.get("blocks", [])
        connections = workflow.get("connections", [])
        execution = workflow.get("execution", {})
        security = workflow.get("security", {})

        # Phase 2 Gate 3 bookkeeping
        from server.services.workflow_gates import new_run_id
        run_id = new_run_id()
        started_at = time.time()

        # Phase 4: cycle detection. Push this workflow onto the class-level
        # execution stack. If it's already there, we have a cycle → abort.
        if workflow_id in WorkflowExecutor._execution_stack:
            cycle = " → ".join(WorkflowExecutor._execution_stack + [workflow_id])
            logger.error(f"[WFExec] Cycle detected: {cycle}")
            return {
                "status": "error",
                "workflow_id": workflow_id,
                "run_id": run_id,
                "errors": [f"偵測到工作流循環呼叫：{cycle}"],
                "blocks_executed": 0,
                "final_output": "",
                "results": [],
                "step_results": [],
            }
        if len(WorkflowExecutor._execution_stack) >= WorkflowExecutor._MAX_STACK_DEPTH:
            depth = len(WorkflowExecutor._execution_stack)
            logger.error(f"[WFExec] Stack depth {depth} exceeds {WorkflowExecutor._MAX_STACK_DEPTH}")
            return {
                "status": "error",
                "workflow_id": workflow_id,
                "run_id": run_id,
                "errors": [f"子工作流巢狀深度 {depth} 超過上限 {WorkflowExecutor._MAX_STACK_DEPTH}"],
                "blocks_executed": 0,
                "final_output": "",
                "results": [],
                "step_results": [],
            }
        WorkflowExecutor._execution_stack.append(workflow_id)

        logger.info(f"[WFExec] Start: {workflow_id} run={run_id} ({len(blocks_data)} blocks, {len(variables)} vars, stack_depth={len(WorkflowExecutor._execution_stack)})")

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

            # ── Phase 4: sub-workflow invocation ──
            # Block type "sub-workflow" or "sub_workflow" triggers recursive
            # execute(). Cycle detection done via _execution_stack in nested call.
            if block_type in ("sub-workflow", "sub_workflow"):
                sub_id = (block.get("config") or {}).get("sub_workflow_id") or block.get("label")
                pass_vars_list = (block.get("config") or {}).get("pass_vars") or []
                if not sub_id:
                    results.append({
                        "block_id": bid, "type": block_type, "status": "error",
                        "error": "缺少 sub_workflow_id",
                    })
                    continue
                try:
                    sub_flow = _load_workflow_by_id(sub_id)
                    if not sub_flow:
                        results.append({
                            "block_id": bid, "type": block_type, "status": "error",
                            "error": f"找不到子工作流 '{sub_id}'",
                        })
                        continue
                    # Build pass_vars payload for child workflow
                    pass_data = {k: resolved_vars.get(k, "") for k in pass_vars_list}
                    child_input = json.dumps(pass_data, ensure_ascii=False) if pass_data else accumulated_context
                    child_result = await self.execute(sub_flow, user_input=child_input, user_context=user_context, model_override=model_override)
                    child_status = child_result.get("status", "error")
                    child_output = child_result.get("final_output", "")
                    if child_output:
                        accumulated_context = child_output[:3000]
                        final_output = child_output
                    results.append({
                        "block_id": bid, "type": block_type, "skill": f"sub:{sub_id}",
                        "status": "success" if child_status == "success" else "error",
                        "output_preview": child_output[:300] if child_output else "",
                        "sub_run_id": child_result.get("run_id"),
                        "error": "; ".join(child_result.get("errors") or []) if child_status != "success" else "",
                    })
                except Exception as sub_err:
                    logger.error(f"[WFExec] Sub-workflow {sub_id} failed: {sub_err}")
                    results.append({
                        "block_id": bid, "type": block_type, "status": "error",
                        "error": f"子工作流執行例外：{sub_err}",
                    })
                continue  # next block

            # ── Phase 4: parallel branches ──
            # Block type "parallel" has config.branches=[{skill_id, input_map, output_var}]
            # and config.merge_output_var. All branches run concurrently.
            if block_type in ("parallel", "parallel-branch"):
                branches = (block.get("config") or {}).get("branches") or []
                merge_var = (block.get("config") or {}).get("merge_output_var") or f"block_{bid}_merged"
                if not branches:
                    results.append({
                        "block_id": bid, "type": block_type, "status": "skipped",
                        "reason": "並行節點未設定 branches",
                    })
                    continue
                try:
                    branch_results = await self._run_parallel_branches(branches, resolved_vars, accumulated_context, uma, user_input)
                    # Merge outputs as a list into the named variable
                    merged = [br.get("output") for br in branch_results]
                    resolved_vars[merge_var] = json.dumps(merged, ensure_ascii=False)
                    accumulated_context = resolved_vars[merge_var][:3000]
                    final_output = resolved_vars[merge_var]
                    _any_err = any(br.get("status") == "error" for br in branch_results)
                    results.append({
                        "block_id": bid, "type": "parallel",
                        "skill": f"parallel({len(branches)} branches)",
                        "status": "error" if _any_err else "success",
                        "output_preview": resolved_vars[merge_var][:300],
                        "branches": branch_results,
                    })
                except Exception as par_err:
                    logger.error(f"[WFExec] Parallel block {bid} failed: {par_err}")
                    results.append({
                        "block_id": bid, "type": "parallel", "status": "error",
                        "error": f"並行執行例外：{par_err}",
                    })
                continue

            # Build skill name (standard sequential skill block)
            skill_name = block_type if block_type.startswith("mcp-") else f"mcp-{block_type}"

            # ── Phase 2 Gate 2: per-step safety check ──
            # Build a pseudo-step matching gate_2's expected shape
            _step_for_gate = {
                "step_id": f"block_{bid}",
                "type": "sequential",
                "skill_id": skill_name,
                "input_map": (block.get("config") or {}).get("params", {}),
                "on_fail": block.get("config", {}).get("on_error") or "abort",
            }
            try:
                from server.services.workflow_gates import gate_2_before_step
                g2_ok, g2_reason = gate_2_before_step(_step_for_gate, resolved_vars, uma)
            except Exception as _gate2_err:
                logger.warning(f"[WFExec Gate2] Internal error for block {bid}: {_gate2_err}")
                g2_ok, g2_reason = True, ""  # fail-open

            if not g2_ok:
                _on_fail = _step_for_gate["on_fail"]
                logger.warning(f"[WFExec Gate2] Block {bid} ({skill_name}) blocked: {g2_reason} (on_fail={_on_fail})")
                if _on_fail in ("abort", "retry_once"):
                    # abort = stop workflow; retry_once doesn't help when env is missing
                    results.append({
                        "block_id": bid, "type": block_type, "skill": skill_name,
                        "status": "error", "error": g2_reason, "gate": 2,
                    })
                    break
                else:
                    # continue / skip
                    results.append({
                        "block_id": bid, "type": block_type, "skill": skill_name,
                        "status": "skipped", "reason": f"gate2: {g2_reason}",
                    })
                    continue

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

        # Determine overall status based on step results
        _any_error = any(r.get("status") == "error" for r in results)
        _any_ok = any(r.get("status") == "success" for r in results)
        if _any_error and _any_ok:
            overall_status = "partial"
        elif _any_error:
            overall_status = "error"
        else:
            overall_status = "success"

        ended_at = time.time()
        exec_result = {
            "status": overall_status,
            "workflow_id": workflow_id,
            "workflow_name": workflow.get("display_name") or workflow.get("name", workflow_id),
            "run_id": run_id,                      # Phase 2 Gate 3
            "blocks_executed": len(results),
            "results": results,
            "step_results": results,               # alias for Gate 3
            "final_output": final_output[:2000],
            "executed_at": datetime.now().isoformat(),
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": int((ended_at - started_at) * 1000),
            "total_tokens": total_tokens_used,
            "errors": [r.get("error") for r in results if r.get("error")],
        }

        # ── Phase 2 Gate 3: write run log ──
        try:
            from server.services.workflow_gates import gate_3_log_run
            gate_3_log_run(workflow, run_id, exec_result)
        except Exception as g3_err:
            logger.warning(f"[WFExec Gate3] Log write failed: {g3_err}")

        # ── on_error workflow routing (if main failed and handler defined) ──
        if overall_status == "error":
            _on_err_block = workflow.get("on_error") or {}
            _on_err_wf_id = _on_err_block.get("workflow_id")
            if _on_err_wf_id:
                try:
                    logger.info(f"[WFExec] Triggering on_error workflow: {_on_err_wf_id}")
                    # Look up error workflow file and execute with pass_vars
                    pr = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
                    err_wf_path = None
                    for scope_dir in ("system", "department", "personal"):
                        for p in (pr / "workspace" / "workflows" / scope_dir).rglob(f"{_on_err_wf_id}.json") if (pr / "workspace" / "workflows" / scope_dir).exists() else []:
                            err_wf_path = p
                            break
                        if err_wf_path:
                            break
                    if err_wf_path:
                        err_flow = json.loads(err_wf_path.read_text(encoding="utf-8"))
                        # Build pass_vars from current execution state
                        pass_vars = {}
                        for var_name in (_on_err_block.get("pass_vars") or []):
                            pass_vars[var_name] = resolved_vars.get(var_name, "")
                        pass_vars["_error_message"] = "; ".join(exec_result["errors"][:3])
                        pass_vars["_failed_workflow_id"] = workflow_id
                        pass_vars["_failed_run_id"] = run_id
                        # Note: this creates a SECOND execution — its own Gate 3 log
                        await self.execute(err_flow, user_input=json.dumps(pass_vars, ensure_ascii=False), user_context=user_context)
                except Exception as on_err:
                    logger.warning(f"[WFExec] on_error routing failed: {on_err}")

        # Audit log (legacy — non-blocking)
        if security.get("audit_log", True) is not False:
            try:
                from server.services.workflow_audit import log_workflow_execution
                uc = user_context or {}
                log_workflow_execution(
                    workflow_id=workflow_id,
                    workflow_name=workflow.get("display_name") or workflow.get("name", workflow_id),
                    session_id=uc.get("session_id", ""),
                    result=exec_result,
                    trigger="api",
                    user_input=user_input[:200],
                    user_context=user_context,
                )
            except Exception as audit_err:
                logger.debug(f"[WFExec] Audit log failed: {audit_err}")

        # Phase 4: pop this workflow off the execution stack
        try:
            if WorkflowExecutor._execution_stack and WorkflowExecutor._execution_stack[-1] == workflow_id:
                WorkflowExecutor._execution_stack.pop()
        except Exception:
            pass

        return exec_result

    async def _run_parallel_branches(
        self,
        branches: List[dict],
        resolved_vars: Dict[str, str],
        accumulated_context: str,
        uma,
        user_input: str = "",
    ) -> List[Dict[str, Any]]:
        """Run N skill branches concurrently, return list of results.

        Each branch = {skill_id, input_map, output_var}. Results preserve
        branch order so caller can correlate inputs ↔ outputs.
        """
        import asyncio

        async def _run_one(idx: int, branch: dict) -> Dict[str, Any]:
            skill_id = branch.get("skill_id") or branch.get("skill") or ""
            if not skill_id.startswith("mcp-"):
                skill_id = f"mcp-{skill_id}"
            params = self._resolve_block_params(
                branch.get("input_map") or {},
                resolved_vars,
                accumulated_context,
                user_input,
            )
            try:
                # uma.execute_tool_call is synchronous; wrap in run_in_executor
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: uma.execute_tool_call(skill_id, json.dumps(params, ensure_ascii=False))
                )
                if isinstance(result, dict) and result.get("status") == "error":
                    return {"idx": idx, "skill": skill_id, "status": "error",
                            "error": result.get("message", "skill error"), "output": ""}
                out_text = ""
                if isinstance(result, dict):
                    out_text = result.get("output") or result.get("guide") or result.get("content") or ""
                else:
                    out_text = str(result)
                # Stash per-branch output_var if specified
                if branch.get("output_var"):
                    resolved_vars[branch["output_var"]] = out_text
                return {"idx": idx, "skill": skill_id, "status": "success",
                        "output": out_text[:2000], "output_var": branch.get("output_var")}
            except Exception as e:
                return {"idx": idx, "skill": skill_id, "status": "error",
                        "error": str(e), "output": ""}

        coros = [_run_one(i, b) for i, b in enumerate(branches)]
        results = await asyncio.gather(*coros, return_exceptions=False)
        return results

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
def _load_workflow_by_id(workflow_id: str) -> Optional[Dict[str, Any]]:
    """Find a workflow JSON by workflow_id across all scopes (Phase 4 helper).

    Used by sub_workflow blocks to look up the target workflow. Searches
    system → department → personal in that order.
    """
    import os as _os
    pr = Path(_os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
    base = pr / "workspace" / "workflows"
    for scope in ("system", "department", "personal"):
        scope_dir = base / scope
        if not scope_dir.exists():
            continue
        # Look for {workflow_id}.json in any subdirectory
        for match in scope_dir.rglob(f"{workflow_id}.json"):
            try:
                return json.loads(match.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


_executor_instance = None


def get_workflow_executor() -> WorkflowExecutor:
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = WorkflowExecutor()
    return _executor_instance
