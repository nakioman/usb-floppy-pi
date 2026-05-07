"""Tests for storage.library — high-level facade."""

import asyncio
from pathlib import Path

import pytest

from usb_floppy_pi.storage.library import Library


@pytest.mark.asyncio
async def test_library_initial_scan(tmp_path: Path) -> None:
    set_dir = tmp_path / "DOS"
    set_dir.mkdir()
    (set_dir / "DISK1.img").write_bytes(b"")
    lib = Library(tmp_path, loop=asyncio.get_running_loop())
    await lib.start()
    try:
        sets = lib.sets
        assert len(sets) == 1
        assert sets[0].name == "DOS"
    finally:
        await lib.stop()


@pytest.mark.asyncio
async def test_library_refreshes_on_file_added(tmp_path: Path) -> None:
    lib = Library(tmp_path, loop=asyncio.get_running_loop())
    await lib.start()
    try:
        assert lib.sets == []
        set_dir = tmp_path / "Win98"
        set_dir.mkdir()
        (set_dir / "boot.img").write_bytes(b"")
        # wait for inotify + debounce
        for _ in range(40):
            await asyncio.sleep(0.1)
            if lib.sets:
                break
        assert len(lib.sets) == 1
    finally:
        await lib.stop()


@pytest.mark.asyncio
async def test_library_normalizes_ima_on_arrival(tmp_path: Path) -> None:
    set_dir = tmp_path / "Mixed"
    set_dir.mkdir()
    lib = Library(tmp_path, loop=asyncio.get_running_loop())
    await lib.start()
    try:
        # drop a .ima file
        (set_dir / "DISK1.ima").write_bytes(b"x")
        for _ in range(40):
            await asyncio.sleep(0.1)
            if lib.sets and lib.sets[0].disks:
                break
        assert (set_dir / "DISK1.img").exists()
        assert not (set_dir / "DISK1.ima").exists()
        assert lib.sets[0].disks[0].name == "DISK1.img"
    finally:
        await lib.stop()


@pytest.mark.asyncio
async def test_library_subscribers_notified_on_change(tmp_path: Path) -> None:
    lib = Library(tmp_path, loop=asyncio.get_running_loop())
    notifications: list[None] = []
    lib.on_change(lambda: notifications.append(None))
    await lib.start()
    try:
        set_dir = tmp_path / "DOS"
        set_dir.mkdir()
        (set_dir / "DISK1.img").write_bytes(b"")
        for _ in range(40):
            await asyncio.sleep(0.1)
            if notifications:
                break
        assert len(notifications) >= 1
    finally:
        await lib.stop()
