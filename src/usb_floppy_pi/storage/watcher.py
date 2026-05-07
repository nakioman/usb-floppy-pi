"""Async wrapper around watchdog for filesystem change events."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

ChangeCallback = Callable[[Path], None]


class _Handler(FileSystemEventHandler):
    def __init__(self, callback: ChangeCallback, loop: asyncio.AbstractEventLoop) -> None:
        self._callback = callback
        self._loop = loop

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory and event.event_type not in {"created", "deleted", "moved"}:
            return
        path = Path(event.src_path)
        # Marshal the callback onto the asyncio loop so consumers can mutate state safely.
        self._loop.call_soon_threadsafe(self._callback, path)


class LibraryWatcher:
    """Watch a directory tree (recursively) and call back on changes."""

    def __init__(
        self,
        root: Path,
        callback: ChangeCallback,
        *,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._root = root
        self._callback = callback
        self._loop = loop
        self._observer: object | None = None

    def start(self) -> None:
        if self._observer is not None:
            return
        self._root.mkdir(parents=True, exist_ok=True)
        observer = Observer()
        handler = _Handler(self._callback, self._loop)
        observer.schedule(handler, str(self._root), recursive=True)
        observer.start()
        self._observer = observer
        logger.info("library watcher started on %s", self._root)

    def stop(self) -> None:
        if self._observer is None:
            return
        obs = self._observer
        self._observer = None
        obs.stop()  # type: ignore[union-attr]
        obs.join(timeout=2.0)  # type: ignore[union-attr]
