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
        Returns executable skills (those with defined parameters) as model-specific tool definitions.
        Knowledge-type skills (no parameters/scripts) are NOT registered as tools.
        They are injected as context via get_skill_knowledge() instead.
        """
        tools = []
        for skill_name, data in self.registry.skills.items():
            meta = data["metadata"].copy()
            # D-02: Include knowledge-type skills (no parameters defined) as reference tools
            params = meta.get("parameters")
            if not params:
                meta["parameters"] = {"type": "object", "properties": {}}
            
            # Check dependency readiness
            if not meta.get("_env_ready", False):
                meta["description"] = meta.get("description", "") + " [UNAVAILABLE: Missing dependencies]"

            if model_type.lower() == "openai":
                tools.append(self.converter.to_openai(meta))
            elif model_type.lower() == "gemini":
                tools.append(self.converter.to_gemini(meta))
            elif model_type.lower() == "claude":
                tool_def = self.converter.to_openai(meta)
                fn = tool_def.get("function", tool_def)
                tools.append({
                    "name": fn.get("name"),
                    "description": fn.get("description"),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}})
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

        # Check for main.py (case-insensitive for cross-platform: scripts/ or Scripts/)
        for candidate in [scripts_dir, skill_dir / "Scripts"]:
            main_py = candidate / "main.py"
            if main_py.exists():
                return "executable"

        # Check for any .py files in scripts/
        for candidate in [scripts_dir, skill_dir / "Scripts"]:
            if candidate.exists() and any(candidate.rglob("*.py")):
                return "code"

        return "semantic"

    def execute_tool_call(self, skill_name: str, arguments: str):
        """
        Executes a skill based on its auto-detected execution mode:
        - executable: scripts/main.py exists → run the script directly
        - code:       scripts/ has reference .py files → return guide + instruct LLM to use python-executor
        - semantic:   no scripts at all → return guide + instruct LLM to process directly
        """
        # Parse 'arguments' string if needed, or assume it's a dict
        try:
            arg_dict = json.loads(arguments) if isinstance(arguments, str) else arguments
        except:
            arg_dict = {"raw": arguments}

        mode = self._detect_execution_mode(skill_name)

        # === Executable mode: run scripts/main.py directly ===
        if mode == "executable":
            # Per-skill timeout: read from SKILL.md metadata, default 30s
            skill_data = self.registry.get_skill(skill_name)
            skill_timeout = 30
            if skill_data:
                skill_timeout = int(skill_data["metadata"].get("execution_timeout", 30))
            return self.executor.run_script(skill_name, "main.py", arg_dict, timeout=skill_timeout)

        # === Knowledge modes (code / semantic): return SKILL.md as guide ===
        skill_md_path = self.executor.skills_home / skill_name / "SKILL.md"
        if not skill_md_path.exists():
            return {"status": "error", "message": f"Skill '{skill_name}' not found."}

        content = skill_md_path.read_text(encoding="utf-8", errors="replace")

        if mode == "code":
            # Has reference scripts → LLM should use mcp-python-executor
            message = (
                f"INTERNAL REFERENCE RETRIEVED: This is a technical guide for '{skill_name}'. "
                f"DO NOT repeat this guide to the user. Instead, use the 'mcp-python-executor' tool "
                f"to write and run the Python code needed to process the user's file, "
                f"following the logic described below."
            )
        else:
            # Semantic mode → LLM processes directly with language capabilities
            message = (
                f"SKILL ACTIVATED: '{skill_name}'. "
                f"Follow the instructions in the guide below to directly process "
                f"the user's request using your language capabilities. "
                f"Do NOT call mcp-python-executor unless the user explicitly asks for code execution."
            )

        return {
            "status": "success",
            "type": "knowledge_guide",
            "skill": skill_name,
            "execution_mode": mode,
            "message": message,
            "guide": content
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


