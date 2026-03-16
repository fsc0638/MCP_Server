"""New application entrypoint."""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.routes import models, documents, chat, skills, workspace, resources, auth
from server.integrations.line_connector import router as line_router
from main import PROJECT_ROOT
from server.dependencies.uma import get_uma_instance as get_uma
from server.core.retriever import retriever
from server.core.watcher import DirectoryWatcher
from server.dependencies.session import get_session_manager
from server.services.runtime import delta_index_skills, make_llm_callable

logger = logging.getLogger("MCP_Server.App")
__watcher = None

app = FastAPI(
    title="MCP Agent Console API",
    description="Refactored entrypoint",
    version="2.1.0",
)

app.include_router(models.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(skills.router)
app.include_router(workspace.router)
app.include_router(resources.router)
app.include_router(auth.router)
app.include_router(line_router)

frontend_dir = PROJECT_ROOT / "frontend"
app.mount("/ui", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


@app.on_event("startup")
async def startup():
    async def _background_index():
        try:
            uma = get_uma()
            summary = await asyncio.get_event_loop().run_in_executor(None, delta_index_skills, uma, retriever)
            logger.info(
                f"[Startup] Delta index complete - added:{len(summary['added'])} "
                f"updated:{len(summary['updated'])} removed:{len(summary['removed'])} "
                f"unchanged:{len(summary['unchanged'])} errors:{len(summary['errors'])}"
            )
        except Exception as e:
            logger.error(f"[Startup] Background skill indexing failed: {e}")

    async def _sync_workspace_docs():
        try:
            ws_summary = await asyncio.get_event_loop().run_in_executor(None, retriever.sync_workspace, str(PROJECT_ROOT / "workspace"))
            logger.info(
                f"[Startup] Workspace sync complete - added:{len(ws_summary['added'])} "
                f"removed:{len(ws_summary['removed'])} already:{len(ws_summary['already'])}"
            )
        except Exception as e:
            logger.error(f"[Startup] Workspace sync failed: {e}")

    try:
        uma = get_uma()
        global __watcher
        __watcher = DirectoryWatcher(str(PROJECT_ROOT / "workspace"), str(uma.registry.skills_home), retriever)
        __watcher.start()
        asyncio.create_task(_background_index())
        asyncio.create_task(_sync_workspace_docs())
    except Exception as e:
        logger.error(f"[Startup] Failed to initialize background services: {e}")


@app.on_event("shutdown")
async def shutdown():
    global __watcher
    if __watcher is not None:
        __watcher.stop()
    session_mgr = get_session_manager()
    session_mgr.flush_all_sessions(make_llm_callable())


