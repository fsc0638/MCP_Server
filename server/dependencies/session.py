"""Dependency provider for shared session manager.

Temporary bridge: reuses legacy instance from ``router`` during migration.
"""

from router import _session_mgr


def get_session_manager():
    return _session_mgr

