import os
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
        Returns all registered skills as model-specific tool definitions.
        """
        tools = []
        for skill_name, data in self.registry.skills.items():
            # Check dependency readiness
            if not data["metadata"].get("_env_ready", False):
                # Optionally skip or flag as unavailable in description
                data["metadata"]["description"] += " [UNAVAILABLE: Missing dependencies]"
            
            if model_type.lower() == "openai":
                tools.append(self.converter.to_openai(data["metadata"]))
            elif model_type.lower() == "gemini":
                tools.append(self.converter.to_gemini(data["metadata"]))
        return tools

    def execute_tool_call(self, skill_name: str, arguments: str):
        """
        Executes a script based on model tool call arguments.
        """
        # Parse 'arguments' string if needed, or assume it's a dict
        try:
            arg_dict = json.loads(arguments) if isinstance(arguments, str) else arguments
        except:
            arg_dict = {"raw": arguments}
            
        return self.executor.run_script(skill_name, "main.py", arg_dict)

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
        """
        if not self.skills_home.exists():
            return

        for skill_dir in self.skills_home.iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    self._register_skill(skill_dir)

    def _register_skill(self, skill_dir: Path):
        """
        Parses SKILL.md and registers it into the registry.
        """
        skill_name = skill_dir.name
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

    def get_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        return self.skills.get(skill_name)

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
