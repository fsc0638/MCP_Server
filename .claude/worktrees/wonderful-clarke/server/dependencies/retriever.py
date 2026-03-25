"""Dependency provider for retriever singleton."""

from server.core.retriever import retriever


def get_retriever():
    return retriever


