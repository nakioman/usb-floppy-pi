"""Integration smoke test for __main__.run() with mocked gadget backend."""
import asyncio
import json
from pathlib import Path

import pytest

from usb_floppy_pi.__main__ import build_runtime
from usb_floppy_pi.gadget.backend import MockBackend


@pytest.mark.asyncio
async def test_runtime_starts_with_no_floppies(tmp_path: Path) -> None:
    floppies = tmp_path / "floppies"
    floppies.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"samba_share_name": "floppies"}))
    runtime = await build_runtime(
        config_path=cfg,
        floppy_root=floppies,
        gadget_backend=MockBackend(),
    )
    try:
        assert runtime.controller.current is None
        assert runtime.library.sets == []
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_runtime_restores_last_mounted(tmp_path: Path) -> None:
    floppies = tmp_path / "floppies"
    set_dir = floppies / "DOS"
    set_dir.mkdir(parents=True)
    (set_dir / "DISK1.img").write_bytes(b"\x00" * 1474560)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "last_mounted": {"set": "DOS", "disk": "DISK1.img"},
    }))
    runtime = await build_runtime(
        config_path=cfg,
        floppy_root=floppies,
        gadget_backend=MockBackend(),
    )
    try:
        assert runtime.controller.current is not None
        assert runtime.controller.current.set_name == "DOS"
        assert runtime.controller.current.disk_filename == "DISK1.img"
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_runtime_skips_missing_last_mounted(tmp_path: Path) -> None:
    floppies = tmp_path / "floppies"
    floppies.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "last_mounted": {"set": "Gone", "disk": "x.img"},
    }))
    runtime = await build_runtime(
        config_path=cfg,
        floppy_root=floppies,
        gadget_backend=MockBackend(),
    )
    try:
        assert runtime.controller.current is None
    finally:
        await runtime.shutdown()
