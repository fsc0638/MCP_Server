"""UMA dependency provider."""
import os
import sys
import logging
from pathlib import Path

logger = logging.getLogger("MCP_Server.Deps.UMA")

_uma_instance = None

def get_uma_instance():
    """Global UMA instance provider."""
    global _uma_instance
    if _uma_instance is None:
        # Import UMA inside to avoid top-level circular issues
        from server.core.uma_core import UMA
        from main import PROJECT_ROOT
        
        # Use absolute path for SKILLS_HOME
        skills_home = os.getenv("SKILLS_HOME", str(PROJECT_ROOT / "Agent_skills" / "skills"))
        logger.info(f"Initializing UMA with SKILLS_HOME: {skills_home}")
        
        _uma_instance = UMA(skills_home=skills_home)
        _uma_instance.initialize()
    return _uma_instance
