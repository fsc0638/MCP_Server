import os
import sys
import subprocess
import shlex
from pathlib import Path
from typing import Dict, Any, Optional

class ExecutionEngine:
    """
    Handles the execution of Skill scripts with security enforcement.
    """
    def __init__(self, skills_home: str):
        self.skills_home = Path(skills_home).resolve()
        
    def sanitize_path(self, target_path: str) -> Path:
        """
        Prevents directory traversal attacks by ensuring the path is within skills_home.
        """
        # Resolve the absolute path
        abs_path = (self.skills_home / target_path).resolve()
        
        # Check if it starts with skills_home
        if not str(abs_path).startswith(str(self.skills_home)):
            raise PermissionError(f"Security Violation: Path '{target_path}' is outside of {self.skills_home}")
        
        return abs_path

    def read_resource(self, skill_name: str, resource_name: str) -> Dict[str, Any]:
        """
        Reads a file from the References/ directory.
        """
        try:
            res_path = self.sanitize_path(Path(skill_name) / "References" / resource_name)
            if not res_path.exists():
                return {"status": "error", "message": f"Resource not found: {resource_name}"}
            
            with open(res_path, "r", encoding="utf-8") as f:
                return {"status": "success", "content": f.read()}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def search_resource(self, skill_name: str, resource_name: str, query: str) -> Dict[str, Any]:
        """
        Searches for a query string in a resource file (Grep-like).
        """
        try:
            res_path = self.sanitize_path(Path(skill_name) / "References" / resource_name)
            if not res_path.exists():
                return {"status": "error", "message": f"Resource not found: {resource_name}"}

            results = []
            with open(res_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    if query.lower() in line.lower():
                        results.append({"line": line_no, "content": line.strip()})
            
            return {"status": "success", "matches": results[:50]} # Cap at 50 matches
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def cleanup_temp_files(self, temp_dir: str = "temp"):
        """
        Cleanup Job to remove temporary data.
        """
        temp_path = self.skills_home.parent / temp_dir
        if temp_path.exists() and temp_path.is_dir():
            import shutil
            shutil.rmtree(temp_path)
            temp_path.mkdir()

    def run_script(self, skill_name: str, script_relative_path: str, args: Dict[str, Any], env_vars: Optional[Dict[str, str]] = None):
        """
        Executes a script within a skill bundle.
        """
        # 1. Sanitize the skill directory and script path
        try:
            skill_dir = self.sanitize_path(skill_name)
            script_path = self.sanitize_path(Path(skill_name) / "Scripts" / script_relative_path)
            
            if not script_path.exists():
                return {"status": "error", "message": f"Script not found: {script_relative_path}"}

            # 2. Context Injection (Merge system env with injected env)
            current_env = os.environ.copy()
            if env_vars:
                current_env.update(env_vars)
            
            # Inject standardized project variables
            current_env["SKILLS_HOME"] = str(self.skills_home)
            current_env["CURRENT_SKILL_DIR"] = str(skill_dir)

            # 3. Execution (Subprocess with boundary awareness)
            # We use sys.executable to ensure we use the same Python environment (venv)
            cmd = [sys.executable, str(script_path)]
            
            # Convert args to CLI arguments or pass via env/stdin if needed.
            # For simplicity in this initial version, we pass them as environment variables starting with SKILL_PARAM_
            for key, val in args.items():
                current_env[f"SKILL_PARAM_{key.upper()}"] = str(val)

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=current_env,
                text=True,
                encoding='utf-8'
            )

            try:
                stdout, stderr = process.communicate(timeout=30) # Default 30s timeout
                
                if process.returncode == 0:
                    return {
                        "status": "success",
                        "output": stdout.strip(),
                        "exit_code": 0
                    }
                else:
                    return {
                        "status": "failed",
                        "message": "Script execution returned non-zero exit code.",
                        "stdout": stdout.strip(),
                        "stderr": stderr.strip(),
                        "exit_code": process.returncode
                    }

            except subprocess.TimeoutExpired:
                process.kill()
                return {"status": "error", "message": "Execution Timeout (30s)"}

        except PermissionError as e:
            return {"status": "security_violation", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    # Internal test core
    engine = ExecutionEngine(skills_home="./skills")
    # This should fail if it tries to escape
    try:
        print(engine.sanitize_path("../../etc/passwd"))
    except PermissionError as e:
        print(f"Caught expected security error: {e}")
