"""
MCP Server - Main Entry Point (Phase 1)
Unified initialization with dependency pre-check and degraded mode.
"""
import os
import sys
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables early for all modules
load_dotenv(PROJECT_ROOT / ".env", override=True)
from server.core.uma_core import UMA

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / "uma_server.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("MCP_Server")


def startup():
    """
    System startup sequence:
    1. Load environment variables
    2. Initialize UMA (scan skills, validate dependencies)
    3. Report degraded skills
    """
    # 1. Load .env
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
        logger.info("Environment variables loaded from .env (with override)")
    else:
        logger.warning("No .env file found. Using system environment variables only.")

    # 2. Initialize UMA
    # Default to the new Monorepo structure: Agent_skills/skills
    skills_home = os.getenv("SKILLS_HOME", str(PROJECT_ROOT / "Agent_skills" / "skills"))
    logger.info(f"Initializing UMA with SKILLS_HOME: {skills_home}")

    uma = UMA(skills_home=skills_home)
    uma.initialize()

    # 3. Report skill status (degraded mode check)
    total = len(uma.registry.skills)
    ready = 0
    degraded = 0

    for skill_name, data in uma.registry.skills.items():
        meta = data["metadata"]
        if meta.get("_env_ready", False):
            ready += 1
            logger.info(f"  [OK] {skill_name} v{meta.get('version', '?')} -- READY")
        else:
            degraded += 1
            missing = meta.get("_missing_deps", [])
            logger.warning(f"  [!!] {skill_name} v{meta.get('version', '?')} -- DEGRADED (missing: {', '.join(missing)})")

    logger.info(f"Skill scan complete: {total} total, {ready} ready, {degraded} degraded")

    return uma


from server.dependencies.uma import get_uma_instance as get_uma

# ─── FastAPI Application Factory / Launcher ──────────────────────────────────
# The FastAPI app is exposed and imported lazily to avoid circular dependencies
if __name__ == "__main__":
    import uvicorn
    # Auto-initialize UMA on startup
    uma = get_uma()
    
    logger.info("=" * 60)
    logger.info("MCP Server & LINE Webhook — Starting Up")
    logger.info("=" * 60)
    logger.info("Server is available on http://127.0.0.1:8500")
    
    # Run the uvicorn server directly when executing `python main.py`
    # New entrypoint uses server.app and mounts the legacy router for compatibility.
    uvicorn.run(
        "server.app:app",
        host="0.0.0.0", 
        port=8500, 
        reload=False  # Disabled to prevent watchfiles loop triggered by app logs & memory
    )
