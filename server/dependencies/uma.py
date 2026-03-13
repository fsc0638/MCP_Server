"""Dependency provider for UMA singleton."""

from main import get_uma


def get_uma_instance():
    return get_uma()

