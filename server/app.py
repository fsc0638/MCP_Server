"""New application entrypoint."""

from fastapi import FastAPI

from server.routes import models, documents, chat, skills, workspace, resources
from server.integrations.line_connector import router as line_router
from router import index_all_skills as legacy_startup_index_all_skills
from router import shutdown_system as legacy_shutdown_system

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
app.include_router(line_router)


@app.on_event("startup")
async def startup():
    await legacy_startup_index_all_skills()


@app.on_event("shutdown")
async def shutdown():
    await legacy_shutdown_system()
