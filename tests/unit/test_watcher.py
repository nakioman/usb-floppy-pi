"""Tests for storage.watcher."""

import asyncio
from pathlib import Path

import pytest

from usb_floppy_pi.storage.watcher import LibraryWatcher


@pytest.mark.asyncio
async def test_watcher_fires_on_file_created(tmp_path: Path) -> None:
    events: list[Path] = []
    loop = asyncio.get_running_loop()

    def callback(p: Path) -> None:
        events.append(p)

    watcher = LibraryWatcher(tmp_path, callback, loop=loop)
    watcher.start()
    try:
        await asyncio.sleep(0.1)  # let observer settle
        set_dir = tmp_path / "S1"
        set_dir.mkdir()
        (set_dir / "DISK1.img").write_bytes(b"")
        # poll up to 2s for the event
        for _ in range(40):
            await asyncio.sleep(0.05)
            if events:
                break
        assert len(events) >= 1
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_fires_on_file_deleted(tmp_path: Path) -> None:
    set_dir = tmp_path / "S1"
    set_dir.mkdir()
    target = set_dir / "DISK1.img"
    target.write_bytes(b"")

    events: list[Path] = []
    loop = asyncio.get_running_loop()

    watcher = LibraryWatcher(tmp_path, lambda p: events.append(p), loop=loop)
    watcher.start()
    try:
        await asyncio.sleep(0.1)
        target.unlink()
        for _ in range(40):
            await asyncio.sleep(0.05)
            if events:
                break
        assert len(events) >= 1
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_stop_is_idempotent(tmp_path: Path) -> None:
    watcher = LibraryWatcher(tmp_path, lambda p: None, loop=asyncio.get_running_loop())
    watcher.start()
    watcher.stop()
    watcher.stop()  # should not raise
