"""Dependency provider for retriever singleton."""

from core.retriever import retriever


def get_retriever():
    return retriever

