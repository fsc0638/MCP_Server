"""Workflow Schema v2 — validation / migration / defaults.

Integration of Claude + OpenClaw + MAGELLAN BLOCKS analysis reports.
This module is the single source of truth for what a valid workflow
document looks like.

Lifecycle:
    - Web UI saves workflow → routes/workflow.py calls validate_workflow()
      (Phase 2 Gate 0: static schema check)
    - Server boot → auto-migrate legacy files via migrate_on_startup()
    - New workflow wizard / block-editor → scaffolds from default_workflow()
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("MCP_Server.WorkflowSchema")

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "workflow_v2.schema.json"
_SCHEMA_CACHE: Optional[Dict[str, Any]] = None


# ── Schema loading ──────────────────────────────────────────────────────────

def get_schema() -> Dict[str, Any]:
    """Load (and cache) the JSON Schema document."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
    return _SCHEMA_CACHE


# ── Validation (Phase 2 Gate 0) ─────────────────────────────────────────────

def validate_workflow(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Check workflow JSON against v2 schema.

    Returns (ok, errors) where errors is a list of human-readable messages.
    Fast-fails on the first blocker so the user gets one clear message rather
    than a wall of validation noise.
    """
    errors: List[str] = []

    if not isinstance(data, dict):
        return False, ["workflow 必須是 JSON 物件"]

    # JSON Schema structural check
    try:
        import jsonschema
        validator = jsonschema.Draft7Validator(get_schema())
        for err in sorted(validator.iter_errors(data), key=lambda e: e.path):
            path = ".".join(str(p) for p in err.path) or "(root)"
            errors.append(f"{path}: {err.message}")
    except ImportError:
        # jsonschema not available — fall back to manual required-field check
        for field in ("workflow_id", "display_name", "version", "steps"):
            if field not in data:
                errors.append(f"缺少必填欄位: {field}")

    # ── Additional semantic checks that JSON Schema can't express ──

    steps = data.get("steps") or []
    if isinstance(steps, list) and steps:
        # Each step_id must be unique within the workflow
        seen_step_ids = set()
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            sid = step.get("step_id") or f"(no step_id at index {i})"
            if sid in seen_step_ids:
                errors.append(f"steps: 重複的 step_id '{sid}'")
            seen_step_ids.add(sid)

        # Topological order: input_map references must exist in prior output_vars
        # (allowing ${GLOBAL.X} and user-provided global_inputs)
        global_inputs = set((data.get("variables") or {}).get("global_inputs") or [])
        known_outputs = set(global_inputs)
        # Also treat any explicit variable definitions as known
        for vd in ((data.get("variables") or {}).get("definitions") or []):
            if vd.get("name"):
                known_outputs.add(vd["name"])

        for step in steps:
            if not isinstance(step, dict):
                continue
            input_map = step.get("input_map") or {}
            for param, value in input_map.items():
                refs = _extract_var_refs(value)
                for ref in refs:
                    if ref.startswith("GLOBAL.") or ref.startswith("_"):
                        continue  # system/global — not tracked here
                    if ref not in known_outputs:
                        errors.append(
                            f"steps.{step.get('step_id')}.input_map.{param}: "
                            f"變數 '${{{ref}}}' 在前驅步驟的 output_var 中不存在"
                        )
            # After this step, its output becomes available
            if step.get("output_var"):
                known_outputs.add(step["output_var"])
            if step.get("merge_output_var"):
                known_outputs.add(step["merge_output_var"])
            # Parallel branch outputs also become visible
            for branch in (step.get("branches") or []):
                if isinstance(branch, dict) and branch.get("output_var"):
                    known_outputs.add(branch["output_var"])

        # Max steps enforced by schema; double-check constraint block exists
        max_steps = ((data.get("constraints") or {}).get("max_steps")) or 10
        if len(steps) > max_steps:
            errors.append(f"steps 數量 {len(steps)} 超過 max_steps={max_steps}")

    return (len(errors) == 0), errors


_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")


def _extract_var_refs(value: Any) -> List[str]:
    """Find all ${var} references in a value (recurses dicts/lists)."""
    if isinstance(value, str):
        return _VAR_REF_RE.findall(value)
    if isinstance(value, dict):
        out: List[str] = []
        for v in value.values():
            out.extend(_extract_var_refs(v))
        return out
    if isinstance(value, list):
        out: List[str] = []
        for v in value:
            out.extend(_extract_var_refs(v))
        return out
    return []


# ── Variable naming convention (1.3) ────────────────────────────────────────

_SYSTEM_VAR_RE = re.compile(r"^_[a-z][a-z0-9_]*$")      # _workflow_id, _run_id
_GLOBAL_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")        # OPENAI_API_KEY, MY_VAR
_EXECUTION_VAR_RE = re.compile(r"^[a-z][a-zA-Z0-9]*$")   # searchResult, summary


def classify_variable(name: str) -> str:
    """Return 'system' | 'global' | 'execution' | 'invalid'.

    Based on MAGELLAN BLOCKS's convention:
        _xxx  → system reserved (_workflow_id, _run_id, _started_at)
        XXX   → global shared (API keys, env constants)
        xxx   → execution-time (skill outputs, step outputs)
    """
    if _SYSTEM_VAR_RE.match(name):
        return "system"
    if _GLOBAL_VAR_RE.match(name):
        return "global"
    if _EXECUTION_VAR_RE.match(name):
        return "execution"
    return "invalid"


# ── Legacy migration (1.4) ──────────────────────────────────────────────────

def is_legacy(data: Dict[str, Any]) -> bool:
    """Detect a pre-v2 workflow document."""
    if not isinstance(data, dict):
        return False
    # v2 must have a version field with the format "X.Y"
    ver = data.get("version")
    if not ver or not re.match(r"^\d+\.\d+$", str(ver)):
        return True
    # v2 requires workflow_id + display_name
    if not data.get("workflow_id") or not data.get("display_name"):
        return True
    return False


def migrate_legacy(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an old-format workflow JSON to v2 in place.

    Old format typically has:
        id, name, blocks, connections, trigger, execution, ...
    New format adds:
        workflow_id, display_name, version, source="legacy", steps (derived),
        metadata block.

    Canvas `blocks` + `connections` are preserved so visual editor keeps
    working; a synthesized `steps[]` is also added for the new executor.
    """
    if not isinstance(data, dict):
        return data

    # Shallow copy so we don't mutate caller's dict if they care
    out = dict(data)

    # 1. version
    if not out.get("version") or not re.match(r"^\d+\.\d+$", str(out.get("version", ""))):
        out["version"] = "1.0"

    # 2. workflow_id — derive from old 'id' field, sanitized
    if not out.get("workflow_id"):
        legacy_id = str(out.get("id") or "").strip()
        slug = _slugify(legacy_id or out.get("name") or "legacy_workflow")
        out["workflow_id"] = slug or "legacy_workflow"

    # 3. display_name — preserve old 'name'
    if not out.get("display_name"):
        out["display_name"] = out.get("name") or out["workflow_id"]

    # 4. source
    if not out.get("source"):
        out["source"] = "legacy"

    # 5. metadata
    meta = out.get("metadata") or {}
    now = datetime.now().isoformat()
    meta.setdefault("created_at", out.get("created_at") or now)
    meta.setdefault("updated_at", out.get("updated_at") or now)
    meta.setdefault("run_count", 0)
    out["metadata"] = meta

    # 6. Derive steps[] from legacy blocks+connections if steps missing
    if not out.get("steps") and out.get("blocks"):
        out["steps"] = _steps_from_blocks(out.get("blocks") or [], out.get("connections") or [])

    # 7. constraints default
    if not out.get("constraints"):
        out["constraints"] = {"max_steps": 10, "timeout_seconds": 300, "skill_whitelist": None}

    # 8. Auto-register any ${xxx} refs used in steps as global_inputs.
    # Old workflows didn't formally declare variables; without this, the
    # migrated workflow would fail Gate 0 validation. Users can always
    # trim the list later via the settings UI.
    vars_block = out.get("variables") or {}
    globals_set = set(vars_block.get("global_inputs") or [])
    definitions = {d.get("name") for d in (vars_block.get("definitions") or []) if isinstance(d, dict)}
    used_names = set()
    for step in (out.get("steps") or []):
        for pv in (step.get("input_map") or {}).values():
            used_names.update(_extract_var_refs(pv))
    for ref in used_names:
        if ref.startswith("_") or ref.startswith("GLOBAL."):
            continue
        if ref not in globals_set and ref not in definitions:
            globals_set.add(ref)
    vars_block["global_inputs"] = sorted(globals_set)
    vars_block.setdefault("env_requirements", [])
    vars_block.setdefault("definitions", [])
    out["variables"] = vars_block

    return out


def _slugify(text: str) -> str:
    """Convert arbitrary text to lowercase ASCII slug.

    If all chars are non-ASCII (e.g. Chinese), generate a fallback id from hash
    so the result is always a valid workflow_id.
    """
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or len(s) < 3:
        # Fallback: hash-based id
        import hashlib
        digest = hashlib.md5((text or "legacy").encode("utf-8")).hexdigest()[:10]
        s = f"wf_{digest}"
    # Ensure starts with letter
    if not s[0].isalpha():
        s = "wf_" + s
    return s[:64]


def _steps_from_blocks(blocks: List[Dict], connections: List[Dict]) -> List[Dict]:
    """Synthesize steps[] from canvas blocks+connections (topological order).

    Skips `start` and `end` control nodes. Each skill block becomes a
    sequential step. This is best-effort: complex graphs with parallel
    branches won't auto-convert — the user will need to re-edit.
    """
    # Build adjacency
    by_id = {b["id"]: b for b in blocks if isinstance(b, dict) and "id" in b}
    outgoing: Dict[Any, List[Any]] = {bid: [] for bid in by_id}
    indeg: Dict[Any, int] = {bid: 0 for bid in by_id}
    for c in connections:
        if isinstance(c, dict) and c.get("from") in by_id and c.get("to") in by_id:
            outgoing[c["from"]].append(c["to"])
            indeg[c["to"]] = indeg.get(c["to"], 0) + 1

    # Kahn's topological sort (ignores start/end)
    queue = [bid for bid, d in indeg.items() if d == 0]
    order: List[Any] = []
    while queue:
        bid = queue.pop(0)
        order.append(bid)
        for nxt in outgoing.get(bid, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)

    steps: List[Dict[str, Any]] = []
    idx = 1
    for bid in order:
        block = by_id[bid]
        btype = block.get("type", "")
        if btype in ("start", "end", "branch"):
            continue  # control nodes don't map to executor steps

        skill_id = btype if btype.startswith("mcp-") else f"mcp-{btype}"
        params = (block.get("config") or {}).get("params") or {}
        # Convert new-style {source,value} entries to ${...} expressions
        input_map: Dict[str, Any] = {}
        for pname, pv in params.items():
            if isinstance(pv, dict):
                src = pv.get("source")
                val = pv.get("value", "")
                if src == "variable":
                    # value is already "${xxx}" or "{{xxx}}"; normalize to ${xxx}
                    val = str(val).replace("{{", "${").replace("}}", "}")
                    input_map[pname] = val
                elif src == "fixed":
                    input_map[pname] = val
                elif src == "previous_step":
                    input_map[pname] = "${_previous_output}"
                else:
                    input_map[pname] = val  # auto / unknown
            else:
                input_map[pname] = pv

        steps.append({
            "step_id": f"step_{idx}",
            "type": "sequential",
            "skill_id": skill_id,
            "label": block.get("label") or btype,
            "input_map": input_map,
            "output_var": f"step_{idx}_output",
            "on_fail": "abort",
        })
        idx += 1

    return steps


# ── Defaults (for wizard / new workflow) ────────────────────────────────────

def default_workflow(
    workflow_id: str = "",
    display_name: str = "",
    scope: str = "personal",
    owner: str = "",
) -> Dict[str, Any]:
    """Return a minimal valid v2 workflow document (with Start/End blocks)."""
    now = datetime.now().isoformat()
    wid = workflow_id or "new_workflow"
    return {
        "version": "1.0",
        "workflow_id": wid,
        "display_name": display_name or wid,
        "description": "",
        "icon": "",
        "tags": [],
        "source": "user_defined",
        "scope": scope,
        "owner": owner,
        "trigger": {"enabled": False, "mode": "confirm", "priority": 10, "patterns": [], "schedule": ""},
        "variables": {"global_inputs": [], "env_requirements": [], "definitions": []},
        "steps": [],
        "on_error": {"workflow_id": "", "pass_vars": []},
        "constraints": {"max_steps": 10, "timeout_seconds": 300, "skill_whitelist": None},
        "execution": {"default_model": "", "timeout": 120, "on_error": "retry", "max_retries": 3},
        "security": {"require_auth": True, "allowed_roles": [], "rate_limit": 0, "audit_log": True},
        "context": {},
        "metadata": {
            "created_at": now,
            "updated_at": now,
            "run_count": 0,
            "last_run": None,
            "promoted_from": None,
        },
        "blocks": [],
        "connections": [],
    }


# ── Startup migration sweep (1.4) ───────────────────────────────────────────

def migrate_on_startup(project_root: Path) -> Dict[str, Any]:
    """Scan workspace/workflows, back up and migrate any legacy JSON files.

    Returns a summary dict suitable for logging.
    """
    base = project_root / "workspace" / "workflows"
    if not base.exists():
        return {"scanned": 0, "migrated": 0, "backed_up": 0}

    backup_dir = base / "legacy_backup"
    migrated_count = 0
    scanned = 0
    errors: List[str] = []

    for scope_name in ("system", "department", "personal"):
        scope_dir = base / scope_name
        if not scope_dir.exists():
            continue
        for f in scope_dir.rglob("*.json"):
            # Skip logs/versions/backup subtrees
            if any(part in ("versions", "logs", "legacy_backup") for part in f.parts):
                continue
            scanned += 1
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                errors.append(f"{f.name}: {e}")
                continue

            if not is_legacy(data):
                continue

            # Back up the original
            try:
                rel = f.relative_to(base)
                bak_path = backup_dir / rel
                bak_path.parent.mkdir(parents=True, exist_ok=True)
                if not bak_path.exists():
                    shutil.copy2(str(f), str(bak_path))
            except Exception as e:
                errors.append(f"backup failed for {f.name}: {e}")
                continue

            # Migrate in place
            try:
                migrated = migrate_legacy(data)
                # Validate the migrated result — warn if still invalid but don't fail
                ok, errs = validate_workflow(migrated)
                if not ok:
                    logger.warning(f"[WFSchema] Migrated {f.name} still has issues: {errs[:3]}")
                f.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
                migrated_count += 1
                logger.info(f"[WFSchema] Migrated {f.name} (workflow_id={migrated.get('workflow_id')})")
            except Exception as e:
                errors.append(f"migrate failed for {f.name}: {e}")

    summary = {
        "scanned": scanned,
        "migrated": migrated_count,
        "backed_up": migrated_count,
        "errors": errors,
    }
    if migrated_count:
        logger.info(f"[WFSchema] Startup migration: {summary}")
    return summary
