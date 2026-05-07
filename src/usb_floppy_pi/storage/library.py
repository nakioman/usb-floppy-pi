"""Library facade: scan + watch + normalize. Holds the current FloppySet list."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from .models import FloppySet
from .normalizer import is_image_file, normalize_arrived_file
from .scanner import scan
from .watcher import LibraryWatcher

logger = logging.getLogger(__name__)

ChangeListener = Callable[[], None]


class Library:
    """Maintains an in-memory list of FloppySets backed by `root`.

    Watches the filesystem; normalizes incoming .ima/.imz; rescans on any change.
    Coalesces rapid changes via a 200ms debounce.
    """

    DEBOUNCE_S = 0.2

    def __init__(self, root: Path, *, loop: asyncio.AbstractEventLoop) -> None:
        self._root = root
        self._loop = loop
        self._watcher = LibraryWatcher(root, self._on_fs_event, loop=loop)
        self._sets: list[FloppySet] = []
        self._listeners: list[ChangeListener] = []
        self._rescan_task: asyncio.Task[None] | None = None
        self._stopped = False

    @property
    def sets(self) -> list[FloppySet]:
        return list(self._sets)

    def on_change(self, listener: ChangeListener) -> None:
        self._listeners.append(listener)

    async def start(self) -> None:
        self._stopped = False
        self._root.mkdir(parents=True, exist_ok=True)
        self._sets = scan(self._root)
        self._watcher.start()

    async def stop(self) -> None:
        self._stopped = True
        self._watcher.stop()
        if self._rescan_task is not None:
            self._rescan_task.cancel()
            try:
                await self._rescan_task
            except asyncio.CancelledError:
                pass
            self._rescan_task = None

    def _on_fs_event(self, path: Path) -> None:
        # First, normalize any newly arrived image files.
        if path.is_file() and is_image_file(path):
            try:
                normalize_arrived_file(path)
            except Exception:
                logger.exception("normalize failed for %s", path)
        self._schedule_rescan()

    def _schedule_rescan(self) -> None:
        if self._stopped:
            return
        if self._rescan_task is not None and not self._rescan_task.done():
            return  # already scheduled
        self._rescan_task = self._loop.create_task(self._debounced_rescan())

    async def _debounced_rescan(self) -> None:
        await asyncio.sleep(self.DEBOUNCE_S)
        if self._stopped:
            return
        new_sets = scan(self._root)
        if new_sets != self._sets:
            self._sets = new_sets
            for listener in list(self._listeners):
                try:
                    listener()
                except Exception:
                    logger.exception("library change listener raised")
