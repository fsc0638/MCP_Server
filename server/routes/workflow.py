"""Workflow CRUD + Execution routes."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["Workflow"])
logger = logging.getLogger("MCP_Server.Workflow")


def _workflows_dir() -> Path:
    pr = os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))
    d = Path(pr) / "workspace" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


class WorkflowSaveRequest(BaseModel):
    name: str = "Untitled Flow"
    blocks: list = []
    connections: list = []
    trigger: dict = {}
    context: dict = {}


# ── CRUD ────────────────────────────────────────────────────────────────────

@router.get("/api/workflows")
def list_workflows():
    """List all saved workflows."""
    workflows = []
    for f in sorted(_workflows_dir().glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            workflows.append({
                "id": f.stem,
                "name": data.get("name", f.stem),
                "block_count": len(data.get("blocks", [])),
                "connection_count": len(data.get("connections", [])),
                "updated_at": data.get("updated_at", ""),
            })
        except Exception:
            pass
    return {"total": len(workflows), "workflows": workflows}


@router.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: str):
    """Get a specific workflow."""
    path = _workflows_dir() / f"{workflow_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/workflows/{workflow_id}")
def save_workflow(workflow_id: str, req: WorkflowSaveRequest):
    """Create or update a workflow."""
    path = _workflows_dir() / f"{workflow_id}.json"
    data = {
        "id": workflow_id,
        "name": req.name,
        "blocks": req.blocks,
        "connections": req.connections,
        "trigger": req.trigger,
        "context": req.context,
        "updated_at": datetime.now().isoformat(),
    }
    # Preserve created_at if updating
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            data["created_at"] = old.get("created_at", data["updated_at"])
        except Exception:
            data["created_at"] = data["updated_at"]
    else:
        data["created_at"] = data["updated_at"]

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[Workflow] Saved: {workflow_id} ({len(req.blocks)} blocks, {len(req.connections)} connections)")
    return {"status": "success", "id": workflow_id, "updated_at": data["updated_at"]}


@router.delete("/api/workflows/{workflow_id}")
def delete_workflow(workflow_id: str):
    """Delete a workflow."""
    path = _workflows_dir() / f"{workflow_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    path.unlink()
    logger.info(f"[Workflow] Deleted: {workflow_id}")
    return {"status": "success", "id": workflow_id}


# ── Execution ───────────────────────────────────────────────────────────────

@router.post("/api/workflows/{workflow_id}/execute")
async def execute_workflow(workflow_id: str):
    """Execute a workflow — run skill chain sequentially."""
    path = _workflows_dir() / f"{workflow_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    try:
        flow = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load workflow: {e}")

    blocks = {b["id"]: b for b in flow.get("blocks", [])}
    connections = flow.get("connections", [])

    # Topological sort
    from collections import deque
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

    # Execute each block
    results = []
    accumulated_context = flow.get("context", {}).get("initial_prompt", "")

    for bid in order:
        block = blocks[bid]
        block_type = block.get("type", "")

        # Skip control blocks
        if block_type in ("start", "end", "branch"):
            results.append({"block_id": bid, "type": block_type, "status": "skipped"})
            continue

        # Build skill name
        skill_name = block_type if block_type.startswith("mcp-") else f"mcp-{block_type}"

        try:
            from main import get_uma
            uma = get_uma()

            # Build arguments from accumulated context
            args = {"input": accumulated_context}
            if block.get("config"):
                args.update(block["config"])

            result = uma.execute_tool_call(skill_name, json.dumps(args, ensure_ascii=False))

            # Extract output for next block
            output_text = ""
            if isinstance(result, dict):
                output_text = result.get("output", "") or result.get("guide", "") or json.dumps(result, ensure_ascii=False)
            else:
                output_text = str(result)

            # Accumulate context for next block
            if output_text:
                accumulated_context = output_text[:3000]  # Limit context size

            results.append({
                "block_id": bid,
                "type": block_type,
                "skill": skill_name,
                "status": "success",
                "output_preview": output_text[:200],
            })
            logger.info(f"[Workflow] Block {bid} ({skill_name}) executed successfully")

        except Exception as e:
            results.append({
                "block_id": bid,
                "type": block_type,
                "skill": skill_name,
                "status": "error",
                "error": str(e),
            })
            logger.error(f"[Workflow] Block {bid} ({skill_name}) failed: {e}")
            # Continue execution (don't stop on error)

    return {
        "status": "success",
        "workflow_id": workflow_id,
        "blocks_executed": len(results),
        "results": results,
        "executed_at": datetime.now().isoformat(),
    }
