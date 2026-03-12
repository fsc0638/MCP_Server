"""Dependency provider for shared session manager."""

from functools import lru_cache

from main import PROJECT_ROOT
from server.core.session import SessionManager


@lru_cache(maxsize=1)
def get_session_manager():
    return SessionManager(str(PROJECT_ROOT))
