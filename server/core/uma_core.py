import os
import json
import yaml
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
import sys
import importlib.util

from server.core.executor import ExecutionEngine
from server.core.converter import SchemaConverter

class UMA:
    """
    The main interface for Unified Model Adapter.
    Integrates Registry, Converter, and Executor.
    """
    def __init__(self, skills_home: str, dept_skills_home: str = None, personal_skills_home: str = None, project_root: str = None):
        self.registry = SkillRegistry(skills_home, dept_skills_home, personal_skills_home)
        self.executor = ExecutionEngine(skills_home)
        self.converter = SchemaConverter()
        self.project_root = project_root or str(Path(skills_home).resolve().parents[1])
        
    def initialize(self):
        self.registry.scan_skills()

    def get_tools_for_model(self, model_type: str, user_context: dict = None) -> List[Dict[str, Any]]:
        """
        Returns only name + description per skill for LLM tool selection.
        Full SKILL.md + references layer are injected on-demand via execute_tool_call().

        Three-tier visibility filtering via user_context:
        - user_context=None → return ALL skills (backward compatible, Phase 1)
        - user_context provided → system + user's dept + user's personal (Phase 2)
        """
        tools = []
        _open_params = {"type": "object", "properties": {}, "additionalProperties": True}
        by_name: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []

        for skill_key, data in self.registry.skills.items():
            meta = data["metadata"]
            scope = meta.get("_scope", "system")

            # Phase 2: user_context-based visibility filtering
            if user_context is not None:
                if scope.startswith("dept:"):
                    dept_code = scope.split(":")[1]
                    if dept_code != user_context.get("department_code", ""):
                        continue
                elif scope.startswith("user:"):
                    owner_id = scope.split(":")[1]
                    if owner_id != user_context.get("user_id", ""):
                        continue
                # system scope → always visible

            # Use the short name for LLM tool calls (LLM calls "mcp-web-search", not "system:mcp-web-search")
            tool_name = meta.get("_short_name", skill_key)
            desc = meta.get("description", "")
            if not meta.get("_env_ready", False):
                desc += " [UNAVAILABLE: Missing dependencies]"

            # Dedupe by short tool name. Prefer the most specific scope:
            # user > department > system.
            if scope.startswith("user:"):
                scope_priority = 3
            elif scope.startswith("dept:"):
                scope_priority = 2
            else:
                scope_priority = 1

            current = by_name.get(tool_name)
            if current is None:
                by_name[tool_name] = {
                    "description": desc,
                    "scope_priority": scope_priority,
                }
                order.append(tool_name)
            elif scope_priority > current["scope_priority"]:
                current["description"] = desc
                current["scope_priority"] = scope_priority

        for tool_name in order:
            desc = by_name[tool_name]["description"]
            if model_type.lower() == "openai":
                tools.append({
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": desc,
                        "parameters": _open_params
                    }
                })
            elif model_type.lower() == "gemini":
                tools.append({
                    "name": tool_name,
                    "description": desc,
                    "parameters": {"type": "object", "properties": {}}
                })
            elif model_type.lower() == "claude":
                tools.append({
                    "name": tool_name,
                    "description": desc,
                    "input_schema": _open_params
                })
        return tools

    def get_skill_knowledge(self, skill_name: str) -> Optional[str]:
        """
        D-11: Returns the full SKILL.md content for a skill.
        Used to inject complete skill knowledge into the LLM context/prompt,
        enabling 'skill definition becomes the prompt' per design principle #2.
        """
        skill = self.registry.get_skill(skill_name)
        if not skill:
            return None
        skill_md_path = skill["path"] / "SKILL.md"
        if skill_md_path.exists():
            return skill_md_path.read_text(encoding="utf-8", errors="replace")
        return None

    def _normalize_executable_result(self, result: Any) -> Any:
        """
        Executable skills are expected to print JSON to stdout. Parse that JSON
        so adapters see the real tool payload instead of the subprocess wrapper.
        """
        if not isinstance(result, dict):
            return result
        if result.get("status") != "success":
            return result

        output = result.get("output")
        if not isinstance(output, str) or not output.strip():
            return result

        try:
            return json.loads(output)
        except Exception:
            return result

    def _resolve_skill_runtime_paths(self, skill_name: str) -> tuple[Optional[Dict[str, Any]], Path]:
        """
        Resolve the concrete skill directory for execution/knowledge loading.
        Priority: registry path (supports system/dept/personal) -> legacy system path fallback.
        """
        skill_data = self.registry.get_skill(skill_name)
        if skill_data and skill_data.get("path"):
            return skill_data, Path(skill_data["path"]).resolve()
        return skill_data, (self.executor.skills_home / skill_name).resolve()


    def _detect_execution_mode(self, skill_name: str) -> str:
        """
        Auto-detect skill execution mode based on scripts/ directory content.
        - 'executable': scripts/main.py exists → run directly
        - 'code':       scripts/ has .py files (but no main.py) → reference guide + python-executor
        - 'semantic':   no scripts/ or empty → LLM processes directly with language capabilities
        """
        _, skill_dir = self._resolve_skill_runtime_paths(skill_name)
        scripts_dir = skill_dir / "scripts"

        for candidate in [scripts_dir, skill_dir / "Scripts"]:
            main_py = candidate / "main.py"
            if main_py.exists():
                return "executable"

        for candidate in [scripts_dir, skill_dir / "Scripts"]:
            if candidate.exists() and any(candidate.rglob("*.py")):
                return "code"

        return "semantic"

    def _load_references(self, skill_name: str) -> str:
        """
        Loads the references/ or assets/ layer content for a skill.
        Text files (.md, .txt) are injected as full content.
        Binary files (.docx, .xlsx, .pdf) are listed as available system templates.
        """
        _, skill_dir = self._resolve_skill_runtime_paths(skill_name)
        refs_dir = None
        for candidate in ["references", "assets"]:
            d = skill_dir / candidate
            if d.exists():
                refs_dir = d
                break
        if not refs_dir:
            return ""

        text_files = []
        template_files = []
        for ref_file in sorted(refs_dir.iterdir()):
            if ref_file.suffix.lower() in (".md", ".txt"):
                text_files.append(ref_file)
            elif ref_file.suffix.lower() in (".docx", ".xlsx", ".pdf"):
                template_files.append(ref_file)

        content = "\n\n---\n【REFERENCE LAYER — 必須嚴格依照以下知識文件作答，禁止自行設計格式或內容】\n"
        for ref_file in text_files:
            content += f"\n=== {ref_file.name} ===\n"
            content += ref_file.read_text(encoding="utf-8", errors="replace")

        if template_files:
            content += "\n\n【SYSTEM TEMPLATES — 系統內建範本，非使用者上傳檔案】\n"
            content += "以下是此技能內建的範本檔案，當使用者要求匯出時應使用這些範本：\n"
            for i, ref_file in enumerate(template_files, 1):
                content += f"  {i}. {ref_file.name}\n"
            content += "範本路徑規則：SKILLS_HOME/{skill_name}/assets/{filename}\n"
            content += "使用方式：透過 mcp-python-executor 以 python-docx 開啟範本並填入資料。\n"
            content += "若有多個範本，請先詢問使用者要使用哪一個，列出檔案名稱供選擇。\n"

        return content

    def execute_tool_call(self, skill_name: str, arguments: str):
        """
        Two-phase skill execution:
        1. LLM selects a skill based on name + description (lightweight listing).
        2. On invocation, full SKILL.md + references/ layer are returned so LLM
           has complete context before acting. Applies to all platforms.

        Execution modes:
        - executable (scripts/main.py exists): run script directly.
        - code (scripts/*.py, no main.py): return SKILL.md guide + instruct LLM to use python-executor.
        - semantic (no scripts): return SKILL.md guide + LLM processes; SKILL.md may instruct python-executor.

        Phase 3: risk_level: high → requires_approval gate.
        """
        try:
            arg_dict = json.loads(arguments) if isinstance(arguments, str) else arguments
        except:
            arg_dict = {"raw": arguments}

        # Resolve concrete skill location across scopes (system/dept/personal)
        skill_data, skill_dir = self._resolve_skill_runtime_paths(skill_name)

        # Phase 3: Risk-level gate
        if skill_data:
            meta = skill_data.get("metadata", {})
            if meta.get("risk_level", "").lower() == "high":
                return {
                    "status": "requires_approval",
                    "tool_name": skill_name,
                    "risk_description": meta.get(
                        "risk_description",
                        f"技能「{skill_name}」被標記為高風險操作，需要使用者授權後才可執行。"
                    ),
                    "pending_args": arg_dict,
                }

        mode = self._detect_execution_mode(skill_name)

        # === Executable: run script directly ===
        if mode == "executable":
            skill_timeout = int(skill_data["metadata"].get("execution_timeout", 30)) if skill_data else 30
            workspace_dir = str(Path(self.project_root) / "workspace")
            env_vars = {"WORKSPACE_DIR": workspace_dir, "PROJECT_ROOT": str(self.project_root)}

            # Inject Google credentials for Google Workspace skills
            if skill_name.startswith("mcp-google-"):
                try:
                    from server.services.google_auth import get_credentials_env, get_service_account_path
                    _session_id = os.environ.get("SESSION_ID", "")
                    _google_env = None
                    # Try session-specific credentials first (personal OAuth or SA via session)
                    if _session_id:
                        _google_env = get_credentials_env(_session_id)
                    # Fallback: Service Account (global, no session needed)
                    if not _google_env:
                        _sa_path = get_service_account_path()
                        if _sa_path:
                            _google_env = {
                                "GOOGLE_CREDENTIALS_PATH": str(_sa_path),
                                "GOOGLE_CREDENTIAL_TYPE": "service_account",
                            }
                    if _google_env:
                        env_vars.update(_google_env)
                    # Inject calendar ID for SA mode (SA sees its own empty calendar by default)
                    _cal_id = os.environ.get("GOOGLE_CALENDAR_ID", "")
                    if _cal_id:
                        env_vars["GOOGLE_CALENDAR_ID"] = _cal_id
                except Exception as _e:
                    import logging
                    logging.getLogger("MCP_Server.UMA").debug(f"Google cred injection skipped: {_e}")

            raw_result = self.executor.run_script(
                skill_name, "main.py", arg_dict,
                env_vars=env_vars,
                timeout=skill_timeout,
                skills_home_override=str(skill_dir.parent),
            )
            return self._normalize_executable_result(raw_result)

        # === Knowledge modes (code / semantic): return full SKILL.md + references ===
        skill_md_path = skill_dir / "SKILL.md"
        if not skill_md_path.exists():
            return {"status": "error", "message": f"Skill '{skill_name}' not found."}

        skill_content = skill_md_path.read_text(encoding="utf-8", errors="replace")
        reference_content = self._load_references(skill_name)

        if mode == "code":
            message = (
                f"SKILL ACTIVATED: '{skill_name}' (code mode). "
                f"Follow the guide below. Use 'mcp-python-executor' to run the required Python logic. "
                f"DO NOT repeat this guide to the user."
            )
        else:
            message = (
                f"SKILL ACTIVATED: '{skill_name}' (semantic mode). "
                f"Use your language capabilities to process the user's request following the guide below. "
                f"If a REFERENCE LAYER is present, it is the authoritative standard — follow it strictly."
            )

        # Inject original file info if provided in arguments
        _file_context = ""
        _orig_file = arg_dict.get("_original_file_path", "")
        _orig_name = arg_dict.get("_original_filename", "")
        if _orig_file:
            _file_context = (
                f"\n\n---\n【UPLOADED FILE CONTEXT — 極重要】\n"
                f"⚠️ 使用者先前已上傳檔案《{_orig_name}》，對話歷史中已包含此檔案的分析內容。\n"
                f"你必須直接使用對話歷史中已有的內容來完成任務，嚴禁再次要求使用者提供檔案或逐字稿。\n"
                f"如需讀取原始完整內容，可用 mcp-python-executor 開啟：{_orig_file}\n"
            )

        return {
            "status": "success",
            "type": "knowledge_guide",
            "skill": skill_name,
            "execution_mode": mode,
            "message": message,
            "guide": skill_content + reference_content + _file_context
        }


class SkillRegistry:
    """
    Manages discovery, metadata parsing, and caching of GitHub Skills.
    """
    def __init__(self, skills_home: str, dept_skills_home: str = None, personal_skills_home: str = None):
        self.skills_home = Path(skills_home).resolve()
        self.dept_skills_home = Path(dept_skills_home).resolve() if dept_skills_home else None
        self.personal_skills_home = Path(personal_skills_home).resolve() if personal_skills_home else None
        self.skills: Dict[str, Dict[str, Any]] = {}
        self.schema_cache: Dict[str, Dict[str, Any]] = {}
        self.validation_cache: Dict[str, bool] = {}

    def scan_skills(self):
        """
        Scans three-tier skill directories for valid Skill Bundles:
        1. skills_home (system) — available to all users
        2. dept_skills_home/{dept_code}/ (department) — available to department members
        3. personal_skills_home/{user_id}/ (personal) — available to owner only
        D-01/D-13: Auto-regenerates skills_manifest.json after scanning.
        """
        # 1. System skills
        if self.skills_home.exists():
            self._scan_directory(self.skills_home, scope="system")

        # 2. Department skills (two-level: dept_code/skill_name)
        if self.dept_skills_home and self.dept_skills_home.exists():
            for dept_dir in self.dept_skills_home.iterdir():
                if dept_dir.is_dir() and dept_dir.name != ".gitkeep":
                    self._scan_directory(dept_dir, scope=f"dept:{dept_dir.name}")

        # 3. Personal skills (two-level: user_id/skill_name)
        if self.personal_skills_home and self.personal_skills_home.exists():
            for user_dir in self.personal_skills_home.iterdir():
                if user_dir.is_dir() and user_dir.name != ".gitkeep":
                    self._scan_directory(user_dir, scope=f"user:{user_dir.name}")

        # D-01/D-13: Keep manifest in sync as SSOT
        self._regenerate_manifest()

    def _scan_directory(self, directory: Path, scope: str = "system"):
        """Scan a single directory for skill bundles, tagging each with scope."""
        for skill_dir in directory.iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    self._register_skill(skill_dir, scope=scope)

    def _register_skill(self, skill_dir: Path, scope: str = "system"):
        """
        Parses SKILL.md and registers it into the registry.
        Key format: scope:skill_name (e.g. "system:mcp-web-search", "dept:A100:mcp-custom")
        """
        skill_name = skill_dir.name.lower()  # Case-insensitive: cross-platform consistency
        registry_key = f"{scope}:{skill_name}" if scope != "system" else skill_name
        skill_md_path = skill_dir / "SKILL.md"

        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                content = f.read()
                if not content.startswith("---"):
                    return

                parts = content.split("---")
                if len(parts) < 3:
                    return

                metadata = yaml.safe_load(parts[1])

                # 1. Version Pinning (Simulated: in real GitHub scenario, we'd record Git Hash)
                metadata["_internal_hash"] = self._generate_dir_hash(skill_dir)

                # 2. Dependency Validation (Python + File dependencies)
                env_ready, missing_reqs = self._check_dependencies(
                    metadata.get("runtime_requirements", [])
                )

                # Check file dependencies defined in 'dependencies' tag
                file_ready, missing_files = self._check_file_dependencies(
                    skill_dir, metadata.get("dependencies", {})
                )

                metadata["_env_ready"] = env_ready and file_ready
                metadata["_missing_deps"] = missing_reqs + missing_files

                # 3. Tag Extraction for dynamic tool selection (multilingual + weighted)
                from server.adapters import extract_tags
                metadata["_tags"] = extract_tags(
                    metadata.get("description", ""),
                    name=metadata.get("name", skill_name)
                )
                metadata["_description_raw"] = metadata.get("description", "")

                # 4. Scope metadata for three-tier classification
                metadata["_scope"] = scope
                metadata["_short_name"] = skill_name

                self.skills[registry_key] = {
                    "path": skill_dir,
                    "metadata": metadata,
                    "raw_md": parts[2].strip()
                }

                # Mark as validated
                self.validation_cache[registry_key] = True

        except Exception as e:
            print(f"Error registering skill {registry_key}: {e}")

    def _check_dependencies(self, requirements: List[Optional[str]]) -> (bool, List[str]):
        """
        Checks if the required Python libraries are installed.
        """
        if not requirements:
            return True, []
        
        missing = []
        for req in requirements:
            if not req: continue
            # Basic check: remove comments and versions for checking import
            clean_req = req.split("#")[0].split("==")[0].split(">=")[0].strip()
            if not clean_req: continue
            
            if importlib.util.find_spec(clean_req) is None:
                missing.append(clean_req)
        return len(missing) == 0, missing

    def _check_file_dependencies(self, skill_dir: Path, dependencies: Dict[str, List[str]]) -> (bool, List[str]):
        """
        Checks if the required files (scripts, assets, references) specified in SKILL.md actually exist.
        """
        if not dependencies:
            return True, []
            
        missing = []
        for folder, files in dependencies.items():
            if not files: continue
            for filename in files:
                file_path = skill_dir / folder / filename
                if not file_path.exists():
                    missing.append(f"{folder}/{filename}")
                    
        return len(missing) == 0, missing

    def _generate_dir_hash(self, dir_path: Path) -> str:
        """
        Generates a hash of the directory structure and files to ensure consistency.
        """
        hash_obj = hashlib.sha256()
        for root, dirs, files in os.walk(dir_path):
            for name in sorted(files):
                file_path = Path(root) / name
                hash_obj.update(name.encode())
                try:
                    with open(file_path, "rb") as f:
                        hash_obj.update(f.read())
                except:
                    pass
        return hash_obj.hexdigest()

    def _regenerate_manifest(self):
        """
        D-01/D-13: Auto-regenerate skills_manifest.json after scanning.
        This ensures the manifest always reflects the current Registry state (SSOT).
        External systems (LINE Bridge, CLI, etc.) can read this file for up-to-date skill info.
        """
        import json as _json
        manifest = {"version": "1.0.0", "skills": []}
        for skill_name, data in self.skills.items():
            meta = data["metadata"]
            manifest["skills"].append({
                "id": skill_name,
                "name": meta.get("name", skill_name),
                "version": meta.get("version", "1.0.0"),
                "description": meta.get("description", "").strip(),
                "tags": meta.get("_tags", []),
                "runtime_requirements": meta.get("runtime_requirements", []),
                "estimated_tokens": meta.get("estimated_tokens", 500),
                "requires_venv": meta.get("requires_venv", False),
                # "parameters" removed — not used by system (Phase 1 uses open schema)
            })
        manifest_path = self.skills_home.parent / "skills_manifest.json"
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                _json.dump(manifest, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # Non-critical: don't crash startup if manifest write fails

    def get_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """
        Look up a skill by name. Supports both scoped keys and short names.
        Priority for short name: system → dept → user (first match wins).
        """
        key = skill_name.lower()
        # 1. Exact match (scoped key like "dept:A100:mcp-custom" or system short name)
        if key in self.skills:
            return self.skills[key]
        # 2. Short name fallback — search all scopes with priority
        for prefix in ("", "dept:", "user:"):
            for k, v in self.skills.items():
                if prefix and not k.startswith(prefix):
                    continue
                if v["metadata"].get("_short_name", "") == key:
                    return v
        return None

    def list_tools_for_model(self, model_type: str) -> List[Dict[str, Any]]:
        """
        Placeholder for SchemaConverter integration.
        Returns tools formatted for specific models.
        """
        # This will be refined when SchemaConverter is implemented
        return [self.skills[s]["metadata"] for s in self.skills]

if __name__ == "__main__":
    registry = SkillRegistry(skills_home="./skills")
    registry.scan_skills()
    for name, data in registry.skills.items():
        ready_status = "READY" if data["metadata"]["_env_ready"] else f"MISSING: {data['metadata']['_missing_deps']}"
        print(f"Skill: {name} | Version: {data['metadata']['version']} | Env: {ready_status}")


