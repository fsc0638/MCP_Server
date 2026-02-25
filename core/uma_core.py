import os
import json
import yaml
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
import sys
import importlib.util

from core.executor import ExecutionEngine
from core.converter import SchemaConverter

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
            # D-02: Skip knowledge-type skills (no parameters defined)
            params = meta.get("parameters", {})
            if not params or params == {} or not params.get("properties"):
                continue

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


    def execute_tool_call(self, skill_name: str, arguments: str):
        """
        Executes a script based on model tool call arguments.
        
        - If the skill has a Scripts/main.py, execute it (execution-type skill).
        - If NO script exists (knowledge-type skill), return the SKILL.md content as a
          reference guide. The AI can then use another tool (e.g. mcp-python-executor)
          to actually perform the task using the knowledge retrieved.
        """
        # Parse 'arguments' string if needed, or assume it's a dict
        try:
            arg_dict = json.loads(arguments) if isinstance(arguments, str) else arguments
        except:
            arg_dict = {"raw": arguments}
        
        # Check if a runnable script exists for this skill
        # D-03: Use lowercase 'scripts/' for cross-platform compatibility
        script_path = (self.executor.skills_home / skill_name / "scripts" / "main.py")
        if script_path.exists():
            # Execution-type skill: run the script
            return self.executor.run_script(skill_name, "main.py", arg_dict)
        else:
            # Knowledge-type skill: return SKILL.md as a reference guide
            skill_md_path = self.executor.skills_home / skill_name / "SKILL.md"
            if skill_md_path.exists():
                content = skill_md_path.read_text(encoding="utf-8", errors="replace")
                return {
                    "status": "success",
                    "type": "knowledge_guide",
                    "skill": skill_name,
                    "message": (
                        f"This is a knowledge-type skill. No executable script found. "
                        f"Use the following guide to complete the task, then use an "
                        f"execution tool (e.g. mcp-python-executor) to run code if needed."
                    ),
                    "guide": content
                }
            else:
                return {"status": "error", "message": f"Skill '{skill_name}' not found."}


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
                
                # 2. Dependency Validation
                metadata["_env_ready"], metadata["_missing_deps"] = self._check_dependencies(
                    metadata.get("runtime_requirements", [])
                )
                
                # 3. Tag Extraction for dynamic tool selection
                from adapters import extract_tags
                metadata["_tags"] = extract_tags(metadata.get("description", ""))
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
