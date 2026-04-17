"""Helpers for bridging blocking synchronous work into async routes."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterable
from typing import Any, AsyncGenerator


_END = object()


class _RaisedFromThread:
    def __init__(self, exc: BaseException):
        self.exc = exc


async def iterate_blocking_generator(
    factory: Callable[[], Iterable[Any]],
) -> AsyncGenerator[Any, None]:
    """Iterate a synchronous generator on a background thread without blocking the event loop."""

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()

    def _emit(item: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def _runner() -> None:
        try:
            for item in factory():
                _emit(item)
        except BaseException as exc:  # pragma: no cover - defensive relay
            _emit(_RaisedFromThread(exc))
        finally:
            _emit(_END)

    threading.Thread(target=_runner, daemon=True).start()

    while True:
        item = await queue.get()
        if item is _END:
            break
        if isinstance(item, _RaisedFromThread):
            raise item.exc
        yield item
