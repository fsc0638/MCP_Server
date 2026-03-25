"""Adapter base typing contract."""

from typing import Protocol, Iterable, Dict, Any


class AdapterProtocol(Protocol):
    @property
    def is_available(self) -> bool:
        ...

    def chat(self, *args, **kwargs) -> Iterable[Dict[str, Any]]:
        ...

