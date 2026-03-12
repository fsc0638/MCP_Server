"""New application entrypoint.

During migration, this app delegates most endpoints to the legacy app.
"""

from fastapi import FastAPI

from server.routes import models, documents, chat, skills, workspace, resources
from router import app as legacy_app

app = FastAPI(
    title="MCP Agent Console API",
    description="Refactored entrypoint with legacy compatibility bridge",
    version="2.1.0",
)

app.include_router(models.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(skills.router)
app.include_router(workspace.router)
app.include_router(resources.router)
app.mount("/", legacy_app)
