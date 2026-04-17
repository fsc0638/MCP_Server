"""Dependency provider for the shared task registry."""

from functools import lru_cache

from main import PROJECT_ROOT
from server.core.task_registry import TaskRegistry


@lru_cache(maxsize=1)
def get_task_registry():
    return TaskRegistry(str(PROJECT_ROOT))
