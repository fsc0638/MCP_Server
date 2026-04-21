"""Workflow Executor — resolve variables + execute blocks in topological order.

Usage:
    executor = WorkflowExecutor()
    result = await executor.execute(workflow_data, user_input="...", user_context={})
"""

import asyncio
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


# ─────────────────────────────────────────────────────────────────────
# Output formatting — skills return JSON; we pretty-print to markdown for
# display in chat. Keeps the raw JSON intact in resolved_vars (downstream
# blocks still see it), but the "最終輸出" area uses markdown.
# ─────────────────────────────────────────────────────────────────────

_SKILL_FRIENDLY_NAMES = {
    "mcp-web-search":              "🔍 網路搜尋",
    "mcp-google-calendar":         "📅 Google 日曆",
    "mcp-notion-crud":             "📝 Notion ToDo",
    "mcp-schedule-manager":        "⏰ 排程管理",
    "mcp-python-executor":         "🐍 Python 執行",
    "mcp-image-generator":         "🎨 圖像生成",
    "mcp-meeting-analyzer":        "🎙️ 會議分析",
    "mcp-meeting-to-notion":       "📋 會議→Notion",
    "mcp-pdf-llm-analyzer":        "📕 PDF 分析",
    "mcp-docx-llm-analyzer":       "📘 Word 分析",
    "mcp-txt-llm-analyzer":        "📄 文字檔分析",
    "mcp-spreadsheet-llm-analyzer":"📊 試算表分析",
    "mcp-transcribe":              "🎤 音訊逐字稿",
    "mcp-gai-worksheet-facilitator":"📋 GAI 學習單",
    "mcp-groovenauts-meeting-analyst":"🎙️ 跨國會議分析",
}


def _skill_friendly_name(skill_id: str) -> str:
    return _SKILL_FRIENDLY_NAMES.get(skill_id, skill_id or "步驟")


def _format_skill_output_as_markdown(skill_id: str, raw_output: str) -> str:
    """Convert a skill's raw JSON output into readable markdown.

    Per-skill formatters handle the common shapes (web-search results,
    calendar events, notion items). Unknown JSON falls back to a generic
    key:value listing. Non-JSON strings are returned as-is.
    """
    if not raw_output:
        return ""
    # Non-JSON → return as-is, but trim
    stripped = raw_output.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return stripped[:3000]
    try:
        data = json.loads(stripped)
    except Exception:
        return stripped[:3000]

    # Per-skill dispatcher
    formatter = _SKILL_MARKDOWN_FORMATTERS.get(skill_id)
    if formatter:
        try:
            md = formatter(data)
            if md:
                return md
        except Exception as e:
            logger.debug(f"[Output format] {skill_id} formatter failed: {e}")

    # Generic fallback
    return _format_generic_json(data)


def _format_web_search(data) -> str:
    if not isinstance(data, dict):
        return ""
    if data.get("status") == "error":
        return f"❌ 搜尋失敗：{data.get('message', '未知錯誤')}"
    results = data.get("results") or []
    if not results:
        msg = data.get("message") or "沒有找到結果"
        return f"*{msg}*"
    mode = data.get("mode", "search")
    lines = [f"_模式：{mode} · 共 {len(results)} 筆結果_", ""]
    for i, r in enumerate(results, 1):
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip().replace("\n", " ")
        url = r.get("url") or ""
        content = (r.get("content") or "").strip()
        # Clean noisy markdown: strip images ![alt](url), strip long link
        # lists (navigation menus), collapse blank lines, then pick the first
        # meaningful paragraph so the user gets a real summary not a ToC.
        content = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", content)       # drop images
        content = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", content)   # flatten [text](url) → text
        # Collapse repeating nav/menu lines (short bullet-like items)
        content_lines = [ln.strip() for ln in content.splitlines()]
        content_lines = [ln for ln in content_lines if ln]
        # Find the first line that looks like real content (longer than 30 chars)
        body = []
        for ln in content_lines:
            if len(ln) < 30 and (ln.startswith(("•", "*", "-", "#"))
                                 or ln.endswith(("│", "|"))):
                continue
            body.append(ln)
        content = "\n".join(body).strip()
        if len(content) > 400:
            content = content[:400] + "…"
        if title and url:
            lines.append(f"**{i}. [{title}]({url})**")
        elif title:
            lines.append(f"**{i}. {title}**")
        else:
            lines.append(f"**{i}. {url or '(無標題)'}**")
        if content:
            lines.append(content)
        lines.append("")
    return "\n".join(lines).strip()


def _format_calendar(data) -> str:
    if not isinstance(data, dict):
        return ""
    if data.get("status") == "error":
        return f"❌ 日曆查詢失敗：{data.get('message', '未知錯誤')}"
    # Calendar skill often returns formatted_text already
    ft = data.get("formatted_text")
    if ft:
        count = data.get("count")
        head = f"_共 {count} 個行程_\n\n" if count is not None else ""
        return head + str(ft).strip()
    events = data.get("events") or data.get("items") or []
    if not events:
        return "*沒有行程*"
    lines = [f"_共 {len(events)} 個行程_", ""]
    for ev in events:
        if not isinstance(ev, dict):
            continue
        title = ev.get("summary") or ev.get("title") or "(無標題)"
        start = ev.get("start") or ev.get("startTime") or ""
        end   = ev.get("end")   or ev.get("endTime")   or ""
        time_str = f"{start}" + (f" ~ {end}" if end else "")
        lines.append(f"- **{title}**" + (f" — {time_str}" if time_str else ""))
    return "\n".join(lines)


def _format_notion_crud(data) -> str:
    if not isinstance(data, dict):
        return ""
    if data.get("status") == "error":
        return f"❌ Notion 操作失敗：{data.get('message', '未知錯誤')}"
    action = data.get("action", "")
    items = data.get("items") or data.get("tasks") or data.get("results") or []

    # summary action
    if action == "summary":
        summary = data.get("summary") or data.get("message") or ""
        return str(summary)

    # Preview / create / update / delete — just show the message
    if action in ("create", "create_batch", "update", "update_batch", "delete", "delete_batch") and not items:
        msg = data.get("message") or f"{action} 操作完成"
        return f"✅ {msg}"

    # list / find_duplicates — render items as a table-ish markdown
    if not items:
        msg = data.get("message") or "目前沒有符合條件的項目"
        return f"*{msg}*"
    total = data.get("total", len(items))
    returned = data.get("returned", len(items))
    lines = [f"_共 {total} 筆" + (f"（顯示 {returned}）" if returned != total else "") + "_", ""]
    def _pick(d, *keys):
        for k in keys:
            v = d.get(k)
            if v not in (None, "", []):
                return v
        return ""
    def _fmt(v):
        if isinstance(v, list):
            return "、".join(str(x) for x in v if x)
        return str(v)
    for i, it in enumerate(items[:15], 1):
        if not isinstance(it, dict):
            lines.append(f"{i}. {it}")
            continue
        title = _pick(it, "ToDo", "title", "name", "todo_title", "任務") or "(無標題)"
        status = _pick(it, "status", "狀態")
        assignee = _pick(it, "assignee", "指派", "負責人 / PM", "負責人", "執行人")
        due = _pick(it, "due_date", "到期日")
        project = _pick(it, "project", "專案")
        hours = _pick(it, "hours", "工時")
        parts = [f"**{i}. {title}**"]
        meta = []
        if status:   meta.append(f"狀態：{_fmt(status)}")
        if assignee: meta.append(f"👤 {_fmt(assignee)}")
        if due:      meta.append(f"📅 {_fmt(due)}")
        if project:  meta.append(f"📂 {_fmt(project)}")
        if hours:    meta.append(f"⏱ {_fmt(hours)}h")
        if meta:
            parts.append(" · ".join(meta))
        lines.append("  \n".join(parts))
    if len(items) > 15:
        lines.append(f"\n_… 另外還有 {len(items) - 15} 筆未顯示_")
    return "\n\n".join(lines)


def _format_python_executor(data) -> str:
    if not isinstance(data, dict):
        return str(data)
    if data.get("status") == "error":
        err = data.get("message") or data.get("error") or "Python 執行失敗"
        return f"❌ {err}"
    out = data.get("output") or data.get("stdout") or ""
    file_path = data.get("file_path") or ""
    parts = []
    if out:
        trimmed = out.strip()[:1500]
        parts.append(f"```\n{trimmed}\n```")
    if file_path:
        parts.append(f"📎 產生檔案：`{file_path}`")
    return "\n\n".join(parts) if parts else "_(無輸出)_"


def _format_image_generator(data) -> str:
    if not isinstance(data, dict):
        return ""
    if data.get("status") == "error":
        return f"❌ 圖像生成失敗：{data.get('message', '未知錯誤')}"
    fp = data.get("file_path") or data.get("url") or ""
    if not fp:
        return "_(未取得圖片路徑)_"
    return f"🖼️ 圖片已生成：`{fp}`"


def _format_generic_json(data) -> str:
    """Fallback: render any JSON as readable markdown."""
    if isinstance(data, list):
        if not data:
            return "_(空清單)_"
        lines = []
        for i, item in enumerate(data[:15], 1):
            if isinstance(item, (dict, list)):
                lines.append(f"{i}. `{json.dumps(item, ensure_ascii=False)[:200]}`")
            else:
                lines.append(f"{i}. {item}")
        if len(data) > 15:
            lines.append(f"_… 另外還有 {len(data) - 15} 項未顯示_")
        return "\n".join(lines)
    if not isinstance(data, dict):
        return str(data)
    # Skip noisy internal keys
    skip = {"status", "_internal_hash", "call_id", "response_id", "instruction"}
    lines = []
    for k, v in data.items():
        if k in skip:
            continue
        if isinstance(v, (dict, list)):
            # Short-form list/dict
            vs = json.dumps(v, ensure_ascii=False)
            if len(vs) > 300:
                vs = vs[:300] + "…"
            lines.append(f"- **{k}**：`{vs}`")
        elif v in (None, ""):
            continue
        else:
            sv = str(v)
            if len(sv) > 400:
                sv = sv[:400] + "…"
            lines.append(f"- **{k}**：{sv}")
    return "\n".join(lines) if lines else "_(無結構化資料)_"


_SKILL_MARKDOWN_FORMATTERS = {
    "mcp-web-search":       _format_web_search,
    "mcp-google-calendar":  _format_calendar,
    "mcp-notion-crud":      _format_notion_crud,
    "mcp-python-executor":  _format_python_executor,
    "mcp-image-generator":  _format_image_generator,
}


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
        user_inputs: dict = None,
    ) -> Dict[str, Any]:
        """
        Execute a workflow end-to-end.

        Args:
            workflow: Full workflow data (blocks, connections, variables, execution, etc.)
            user_input: The user's original free-text message
            user_context: User identity context (name, dept, session_id, etc.)
            model_override: Override model for all blocks
            user_inputs: Per-variable values collected by the Gate 1 wizard
                (e.g. {searchQuery: "台股新聞"}). Takes precedence over
                user_input for variables matched by name.

        Returns:
            { status, workflow_id, results: [...], final_output, executed_at }
        """
        user_inputs = user_inputs or {}
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
        resolved_vars = self._resolve_variables(variables, user_input, user_context, user_inputs)
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
        # Full (un-truncated) per-block output — used by the Markdown formatter
        # at end of run. resolved_vars gets truncated for performance
        # (downstream blocks don't usually need 50k char blobs) but the
        # formatter needs the complete JSON to parse correctly.
        block_full_outputs: Dict[Any, str] = {}

        # ── Wave execution: blocks with satisfied predecessors run concurrently ──
        # This is the natural interpretation of the canvas: a fan-out (one block
        # with multiple outgoing arrows) means its children run in parallel; a
        # fan-in (one block with multiple incoming arrows) makes that block wait
        # for all of its upstreams. No more "parallel block" type needed — the
        # topology itself expresses concurrency. The legacy `type: parallel`
        # block handler below still works for old saved workflows.
        completed_ids: set = set()
        should_stop = False
        wave_no = 0
        preds_of: Dict[Any, List[Any]] = {bid: [] for bid in blocks}
        for c in connections:
            f, t = c.get("from"), c.get("to")
            if f in blocks and t in blocks:
                preds_of[t].append(f)

        while not should_stop and len(completed_ids) < len(blocks):
            # Find all blocks whose predecessors are all completed
            ready_ids = [
                bid for bid in blocks
                if bid not in completed_ids
                and all(p in completed_ids for p in preds_of[bid])
            ]
            if not ready_ids:
                # Cycle or disconnected — shouldn't happen after topo sort
                logger.warning(
                    f"[WFExec] Wave loop stalled at {len(completed_ids)}/{len(blocks)} — "
                    f"remaining: {[b for b in blocks if b not in completed_ids]}"
                )
                break
            wave_no += 1
            if len(ready_ids) > 1:
                names = [f"{bid}({blocks[bid].get('type','?')})" for bid in ready_ids]
                logger.info(f"[WFExec] Wave {wave_no}: {len(ready_ids)} blocks concurrently → {names}")

            # For each ready block, schedule its execution. Control blocks
            # (start/end/branch) complete immediately without touching UMA.
            async def _runner(bid):
                block = blocks[bid]
                block_type = block.get("type", "")
                # Control nodes — synthetic immediate completion
                if block_type in ("start", "end", "branch"):
                    return {"entry": {"block_id": bid, "type": block_type, "status": "skipped"}, "output_text": "", "should_stop": False}
                # Delegate real work to the per-block helper
                ctx = {
                    "uma": uma,
                    "resolved_vars": resolved_vars,
                    "accumulated_context": accumulated_context,
                    "user_input": user_input,
                    "user_context": user_context,
                    "model_override": model_override,
                    "default_model": default_model,
                    "on_error": on_error,
                    "max_retries": max_retries,
                    "preds_of": preds_of,
                }
                return await self._execute_one_block_async(bid, block, ctx)

            # Run all ready blocks concurrently
            wave_results = await asyncio.gather(
                *[_runner(bid) for bid in ready_ids],
                return_exceptions=True,
            )

            for bid, r in zip(ready_ids, wave_results):
                completed_ids.add(bid)
                if isinstance(r, Exception):
                    logger.error(f"[WFExec] Block {bid} exception: {r}")
                    results.append({
                        "block_id": bid, "type": blocks[bid].get("type", ""),
                        "status": "error", "error": str(r),
                    })
                    if on_error == "stop":
                        should_stop = True
                    continue
                if not r:
                    continue
                entry = r.get("entry")
                if entry:
                    results.append(entry)
                out_txt = r.get("output_text") or ""
                if out_txt:
                    # Per-block output variable — downstream blocks reference
                    # via ${step_N_output} in their input_map. Keep truncated
                    # for cheap propagation.
                    resolved_vars[f"step_{bid}_output"] = out_txt[:5000]
                    # Keep the full output for final display formatting (JSON
                    # parsing needs complete payload — truncated JSON blows
                    # up json.loads and falls back to raw text).
                    block_full_outputs[bid] = out_txt
                    accumulated_context = out_txt[:3000]
                if r.get("should_stop"):
                    should_stop = True

        # ── Build final_output ──
        # Aggregate ALL successful skill outputs into a labeled markdown
        # summary so the user sees everything, not just whichever branch
        # finished first. Each skill's raw JSON is passed through a per-skill
        # markdown formatter for readability.
        def _extract_text(block_result) -> str:
            if not isinstance(block_result, dict):
                return ""
            bid = block_result.get("block_id")
            if bid is not None:
                # Prefer the UN-truncated copy so JSON formatters can parse
                full = block_full_outputs.get(bid)
                if full:
                    return full
                trimmed = resolved_vars.get(f"step_{bid}_output")
                if trimmed:
                    return trimmed
            return block_result.get("output_preview") or ""

        success_entries = [r for r in results if r.get("status") == "success"
                           and r.get("type") not in ("start", "end", "branch")]
        if len(success_entries) == 0:
            final_output = ""
        elif len(success_entries) == 1:
            e = success_entries[0]
            final_output = _format_skill_output_as_markdown(
                e.get("skill") or e.get("type", ""), _extract_text(e),
            )
        else:
            _parts = []
            for e in success_entries:
                skill = e.get("skill") or e.get("type") or f"block_{e.get('block_id')}"
                label = e.get("label") or _skill_friendly_name(skill)
                md = _format_skill_output_as_markdown(skill, _extract_text(e))
                if md:
                    _parts.append(f"### {label}\n\n{md}")
            final_output = "\n\n---\n\n".join(_parts)

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
            # Allow more room for multi-skill aggregated output (was 2000)
            "final_output": final_output[:8000],
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

    async def _execute_one_block_async(self, bid, block, ctx):
        """Execute a single non-control block and return {entry, output_text, should_stop}.

        Used by the wave execution loop so any pair of blocks whose
        predecessors are done can run concurrently. Handles:
          - sub-workflow (recursive execute)
          - legacy `parallel` block type (backward compat with old saved
            workflows; new workflows express concurrency via topology)
          - regular skill block (Gate 2 → params resolve → subprocess call)

        ctx carries all the per-run state the wave loop owns:
          uma, resolved_vars, accumulated_context, user_input, user_context,
          model_override, default_model, on_error, max_retries, preds_of.
        """
        uma = ctx["uma"]
        resolved_vars = ctx["resolved_vars"]
        user_input = ctx["user_input"]
        user_context = ctx["user_context"]
        model_override = ctx["model_override"]
        default_model = ctx["default_model"]
        on_error = ctx["on_error"]
        max_retries = ctx["max_retries"]
        preds_of = ctx.get("preds_of") or {}

        block_type = block.get("type", "")

        # ── Convergence: if this block has multiple upstreams, build a
        # merged "accumulated_context" from all of them so the block has
        # access to everything that led to it, not just one arbitrary path.
        upstream_ids = preds_of.get(bid) or []
        upstream_outputs = [
            resolved_vars.get(f"step_{uid}_output", "")
            for uid in upstream_ids
        ]
        non_empty_upstream = [o for o in upstream_outputs if o]
        if len(non_empty_upstream) > 1:
            accumulated_context = json.dumps(
                {f"step_{uid}_output": resolved_vars.get(f"step_{uid}_output", "")
                 for uid in upstream_ids},
                ensure_ascii=False,
            )[:3000]
        elif non_empty_upstream:
            accumulated_context = non_empty_upstream[0][:3000]
        else:
            accumulated_context = ctx["accumulated_context"]

        # ── Sub-workflow invocation ──
        if block_type in ("sub-workflow", "sub_workflow"):
            cfg = block.get("config") or {}
            sub_id = cfg.get("sub_workflow_id") or block.get("label")
            pass_vars_list = cfg.get("pass_vars") or []
            if not sub_id:
                return {"entry": {"block_id": bid, "type": block_type, "status": "error",
                                   "error": "缺少 sub_workflow_id"},
                        "output_text": "", "should_stop": on_error == "stop"}
            try:
                sub_flow = _load_workflow_by_id(sub_id)
                if not sub_flow:
                    return {"entry": {"block_id": bid, "type": block_type, "status": "error",
                                       "error": f"找不到子工作流 '{sub_id}'"},
                            "output_text": "", "should_stop": on_error == "stop"}
                pass_data = {k: resolved_vars.get(k, "") for k in pass_vars_list}
                child_input = json.dumps(pass_data, ensure_ascii=False) if pass_data else accumulated_context
                child_result = await self.execute(
                    sub_flow, user_input=child_input,
                    user_context=user_context, model_override=model_override,
                )
                child_status = child_result.get("status", "error")
                child_output = child_result.get("final_output", "")
                return {
                    "entry": {
                        "block_id": bid, "type": block_type, "skill": f"sub:{sub_id}",
                        "status": "success" if child_status == "success" else "error",
                        "output_preview": child_output[:300] if child_output else "",
                        "sub_run_id": child_result.get("run_id"),
                        "error": "; ".join(child_result.get("errors") or []) if child_status != "success" else "",
                    },
                    "output_text": child_output or "",
                    "should_stop": (child_status != "success" and on_error == "stop"),
                }
            except Exception as sub_err:
                logger.error(f"[WFExec] Sub-workflow {sub_id} failed: {sub_err}")
                return {"entry": {"block_id": bid, "type": block_type, "status": "error",
                                   "error": f"子工作流執行例外：{sub_err}"},
                        "output_text": "", "should_stop": on_error == "stop"}

        # ── Legacy parallel block (kept for backward compat with old saved
        # workflows; new workflows express fan-out via topology) ──
        if block_type in ("parallel", "parallel-branch"):
            branches = (block.get("config") or {}).get("branches") or []
            merge_var = (block.get("config") or {}).get("merge_output_var") or f"block_{bid}_merged"
            if not branches:
                return {"entry": {"block_id": bid, "type": block_type, "status": "skipped",
                                   "reason": "並行節點未設定 branches"},
                        "output_text": "", "should_stop": False}
            try:
                branch_results = await self._run_parallel_branches(
                    branches, resolved_vars, accumulated_context, uma, user_input,
                )
                merged = [br.get("output") for br in branch_results]
                resolved_vars[merge_var] = json.dumps(merged, ensure_ascii=False)
                _any_err = any(br.get("status") == "error" for br in branch_results)
                return {
                    "entry": {
                        "block_id": bid, "type": "parallel",
                        "skill": f"parallel({len(branches)} branches)",
                        "status": "error" if _any_err else "success",
                        "output_preview": resolved_vars[merge_var][:300],
                        "branches": branch_results,
                    },
                    "output_text": resolved_vars[merge_var],
                    "should_stop": (_any_err and on_error == "stop"),
                }
            except Exception as par_err:
                logger.error(f"[WFExec] Parallel block {bid} failed: {par_err}")
                return {"entry": {"block_id": bid, "type": "parallel", "status": "error",
                                   "error": f"並行執行例外：{par_err}"},
                        "output_text": "", "should_stop": on_error == "stop"}

        # ── Regular skill block ──
        skill_name = block_type if block_type.startswith("mcp-") else f"mcp-{block_type}"

        # Gate 2 per-step safety check
        _step_for_gate = {
            "step_id": f"block_{bid}", "type": "sequential", "skill_id": skill_name,
            "input_map": (block.get("config") or {}).get("params", {}),
            "on_fail": block.get("config", {}).get("on_error") or "abort",
        }
        try:
            from server.services.workflow_gates import gate_2_before_step
            g2_ok, g2_reason = gate_2_before_step(_step_for_gate, resolved_vars, uma)
        except Exception as _g2_err:
            logger.warning(f"[WFExec Gate2] Internal error for block {bid}: {_g2_err}")
            g2_ok, g2_reason = True, ""
        if not g2_ok:
            _on_fail = _step_for_gate["on_fail"]
            logger.warning(f"[WFExec Gate2] Block {bid} ({skill_name}) blocked: {g2_reason} (on_fail={_on_fail})")
            if _on_fail in ("abort", "retry_once"):
                return {"entry": {"block_id": bid, "type": block_type, "skill": skill_name,
                                   "status": "error", "error": g2_reason, "gate": 2},
                        "output_text": "", "should_stop": True}
            return {"entry": {"block_id": bid, "type": block_type, "skill": skill_name,
                               "status": "skipped", "reason": f"gate2: {g2_reason}"},
                    "output_text": "", "should_stop": False}

        # Resolve params
        block_config = block.get("config", {})
        block_model = block_config.get("model") or default_model
        block_on_error = block_config.get("on_error") or on_error
        block_params = self._resolve_block_params(
            block_config.get("params", {}), resolved_vars, accumulated_context, user_input,
        )
        if not block_params:
            fallback_key = self._infer_primary_param_name(uma, skill_name) or "input"
            block_params = {fallback_key: accumulated_context}
            if fallback_key != "input":
                logger.info(f"[WFExec] Block {bid} ({skill_name}): no params, bound accumulated_context → {fallback_key}")
            else:
                logger.warning(f"[WFExec] Block {bid} ({skill_name}): no params, fallback to 'input'")
        logger.info(f"[WFExec] Block {bid} ({skill_name}): params={list(block_params.keys())}, model={block_model}")

        # Execute with retry — use run_in_executor so the sync subprocess
        # call doesn't block other concurrent blocks in the same wave.
        loop = asyncio.get_event_loop()
        retries = 0
        last_error = None
        while retries <= (max_retries if block_on_error == "retry" else 0):
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: uma.execute_tool_call(skill_name, json.dumps(block_params, ensure_ascii=False)),
                )
                if isinstance(result, dict) and result.get("status") == "error":
                    raise Exception(f"Skill error: {result.get('message', result.get('error', 'unknown'))}")
                if isinstance(result, dict):
                    output_text = (
                        result.get("output") or result.get("guide") or result.get("content")
                        or json.dumps(result, ensure_ascii=False)
                    )
                else:
                    output_text = str(result)
                return {
                    "entry": {
                        "block_id": bid, "type": block_type, "skill": skill_name,
                        "model_used": block_model, "status": "success",
                        "output_preview": (output_text or "")[:300],
                    },
                    "output_text": output_text or "",
                    "should_stop": False,
                }
            except Exception as e:
                last_error = str(e)
                retries += 1
                if retries <= max_retries and block_on_error == "retry":
                    logger.warning(f"[WFExec] Block {bid} retry {retries}/{max_retries}: {e}")
                    continue
                break
        logger.error(f"[WFExec] Block {bid} ({skill_name}) failed: {last_error}")
        return {
            "entry": {
                "block_id": bid, "type": block_type, "skill": skill_name,
                "model_used": block_model, "status": "error", "error": last_error,
            },
            "output_text": "",
            "should_stop": (block_on_error == "stop"),
        }

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
        user_inputs: dict = None,
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
        ui = user_inputs or {}

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
                # Priority: wizard-collected value (exact name match) > free-text
                # user_input fallback > default. This lets multi-input workflows
                # bind each variable to the wizard field the user filled in.
                wizard_val = ui.get(name)
                if wizard_val not in (None, ""):
                    resolved[name] = wizard_val
                else:
                    resolved[name] = user_input or default
            elif source == "previous_step":
                resolved[name] = default
            elif source == "auto":
                resolved[name] = ui.get(name) or default or user_input
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

    def _infer_primary_param_name(self, uma, skill_name: str) -> Optional[str]:
        """Best-guess the most likely primary string parameter name for a skill.

        Used ONLY when a workflow block was saved without any params configured
        — we still want to run the skill with the accumulated context, but
        mapping it to the right parameter name (e.g. `query`, `text`) is far
        more useful than blindly sending `input` and watching the skill fail.
        Returns None when no reasonable guess exists; caller falls back to
        `input` and logs a warning.
        """
        try:
            skill = uma.registry.get_skill(skill_name) if uma and hasattr(uma, "registry") else None
        except Exception:
            skill = None
        if not skill:
            return None
        meta = skill.get("metadata") or {}
        # Pass 1 — input_schema: first required string field
        inp = meta.get("input_schema")
        if isinstance(inp, dict):
            # required first
            for k, v in inp.items():
                if isinstance(v, dict) and v.get("required") is True and v.get("type", "string") == "string":
                    return k
            # then well-known names
            for alias in ("query", "text", "prompt", "content", "message", "input"):
                if alias in inp and isinstance(inp[alias], dict) and inp[alias].get("type", "string") == "string":
                    return alias
            # fallback — first string-typed field
            for k, v in inp.items():
                if isinstance(v, dict) and v.get("type", "string") == "string":
                    return k
        # Pass 2 — legacy JSON-Schema parameters.required[0]
        params = meta.get("parameters") or {}
        if isinstance(params, dict):
            req = params.get("required") or []
            if isinstance(req, list) and req:
                return req[0]
        return None

    def _resolve_block_params(
        self,
        param_config: dict,
        resolved_vars: Dict[str, str],
        accumulated_context: str,
        user_input: str,
    ) -> Dict[str, str]:
        """Resolve block parameter mappings to concrete values.

        Simple-mode improvement: fixed / auto values with `${varName}` or
        `{{varName}}` placeholders are transparently interpolated. This lets
        non-programmer users just write `${searchQuery}` in a normal text
        input — no need to flip the source dropdown to "variable".
        """
        def _interpolate(raw: Any) -> Any:
            if not isinstance(raw, str) or "{" not in raw:
                return raw
            # Support both ${name} and {{name}} placeholders
            out = re.sub(
                r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}|\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}",
                lambda m: str(resolved_vars.get(m.group(1) or m.group(2), m.group(0))),
                raw,
            )
            return out

        result = {}
        for param_name, param_def in param_config.items():
            source = param_def.get("source", "auto") if isinstance(param_def, dict) else "auto"
            value = param_def.get("value", "") if isinstance(param_def, dict) else param_def

            if source == "variable":
                # Explicit variable binding — value is the bare var name or wrapped in {{}}/${}
                var_name = str(value).strip("{}$ ")
                result[param_name] = resolved_vars.get(var_name, _interpolate(value))
            elif source == "fixed":
                # Fixed value — but still interpolate ${x}/{{x}} placeholders
                # so users who just write `${topic}` inline get variable
                # substitution without having to learn the "variable" source.
                result[param_name] = _interpolate(value) if isinstance(value, str) else value
            elif source == "previous_step":
                result[param_name] = accumulated_context
            elif source == "auto":
                # If user typed something, interpolate; otherwise fall back to
                # accumulated_context (legacy behaviour)
                if value not in (None, ""):
                    result[param_name] = _interpolate(value) if isinstance(value, str) else value
                else:
                    result[param_name] = accumulated_context
            else:
                result[param_name] = _interpolate(value) if isinstance(value, str) else (value or accumulated_context)

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
