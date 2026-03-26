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
    def __init__(self, skills_home: str):
        self.registry = SkillRegistry(skills_home)
        self.executor = ExecutionEngine(skills_home)
        self.converter = SchemaConverter()
        
    def initialize(self):
        self.registry.scan_skills()

    def get_tools_for_model(self, model_type: str) -> List[Dict[str, Any]]:
        """
        Returns only name + description per skill for LLM tool selection.
        Full SKILL.md + references layer are injected on-demand via execute_tool_call()
        when the LLM actually decides to invoke a specific skill.
        This applies to all platforms (LINE Bot, Web UI, etc.).
        """
        tools = []
        # Open schema — LLM passes args based on description; full spec arrives via execute_tool_call
        _open_params = {"type": "object", "properties": {}, "additionalProperties": True}

        for skill_name, data in self.registry.skills.items():
            meta = data["metadata"]
            desc = meta.get("description", "")
            if not meta.get("_env_ready", False):
                desc += " [UNAVAILABLE: Missing dependencies]"

            if model_type.lower() == "openai":
                tools.append({
                    "type": "function",
                    "function": {
                        "name": skill_name,
                        "description": desc,
                        "parameters": _open_params
                    }
                })
            elif model_type.lower() == "gemini":
                tools.append({
                    "name": skill_name,
                    "description": desc,
                    "parameters": {"type": "object", "properties": {}}
                })
            elif model_type.lower() == "claude":
                tools.append({
                    "name": skill_name,
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


    def _detect_execution_mode(self, skill_name: str) -> str:
        """
        Auto-detect skill execution mode based on scripts/ directory content.
        - 'executable': scripts/main.py exists → run directly
        - 'code':       scripts/ has .py files (but no main.py) → reference guide + python-executor
        - 'semantic':   no scripts/ or empty → LLM processes directly with language capabilities
        """
        skill_dir = self.executor.skills_home / skill_name
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
        Loads the references/ layer content for a skill.
        Text files (.md, .txt) are injected as full content.
        Binary files (.docx, .xlsx, .pdf) are listed as available template paths.
        """
        refs_dir = self.executor.skills_home / skill_name / "references"
        if not refs_dir.exists():
            return ""
        content = "\n\n---\n【REFERENCE LAYER — 必須嚴格依照以下知識文件作答，禁止自行設計格式或內容】\n"
        for ref_file in sorted(refs_dir.iterdir()):
            if ref_file.suffix.lower() in (".md", ".txt"):
                content += f"\n=== {ref_file.name} ===\n"
                content += ref_file.read_text(encoding="utf-8", errors="replace")
            elif ref_file.suffix.lower() in (".docx", ".xlsx", ".pdf"):
                content += (
                    f"\n[TEMPLATE FILE]: {ref_file.name} "
                    f"(binary — absolute path: {ref_file}; "
                    f"use mcp-python-executor to load as template)\n"
                )
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

        # Phase 3: Risk-level gate
        skill_data = self.registry.get_skill(skill_name)
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
        skill_dir = self.executor.skills_home / skill_name

        # === Executable: run script directly ===
        if mode == "executable":
            skill_timeout = int(skill_data["metadata"].get("execution_timeout", 30)) if skill_data else 30
            return self.executor.run_script(skill_name, "main.py", arg_dict, timeout=skill_timeout)

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

        return {
            "status": "success",
            "type": "knowledge_guide",
            "skill": skill_name,
            "execution_mode": mode,
            "message": message,
            "guide": skill_content + reference_content
        }


class SkillRegistry:
    """
    Manages discovery, metadata parsing, and caching of GitHub Skills.
    """
    def __init__(self, skills_home: str):
        self.skills_home = Path(skills_home).resolve()
        self.skills: Dict[str, Dict[str, Any]] = {}
        self.schema_cache: Dict[str, Dict[str, Any]] = {}
        self.validation_cache: Dict[str, bool] = {}

    def scan_skills(self):
        """
        Scans the skills_home directory for valid Skill Bundles.
        D-01/D-13: Auto-regenerates skills_manifest.json after scanning.
        """
        if not self.skills_home.exists():
            return

        for skill_dir in self.skills_home.iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    self._register_skill(skill_dir)

        # D-01/D-13: Keep manifest in sync as SSOT
        self._regenerate_manifest()

    def _register_skill(self, skill_dir: Path):
        """
        Parses SKILL.md and registers it into the registry.
        """
        skill_name = skill_dir.name.lower()  # Case-insensitive: cross-platform consistency
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
                # Here we generate a hash of the directory content as a Version ID
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

                self.skills[skill_name] = {
                    "path": skill_dir,
                    "metadata": metadata,
                    "raw_md": parts[2].strip()
                }
                
                # Mark as validated
                self.validation_cache[skill_name] = True
                
        except Exception as e:
            print(f"Error registering skill {skill_name}: {e}")

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
                "parameters": meta.get("parameters", {})
            })
        manifest_path = self.skills_home.parent / "skills_manifest.json"
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                _json.dump(manifest, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # Non-critical: don't crash startup if manifest write fails

    def get_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        return self.skills.get(skill_name.lower())

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


