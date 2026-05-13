"""Unit tests for SysfsIOEventReader.

Reads the kernel's RO attrs into an IOEvent snapshot for the buzzer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from usb_floppy_pi.audio.io_events import SysfsIOEventReader


@pytest.fixture
def fake_sysfs(tmp_path: Path) -> Path:
    root = tmp_path / "usb-floppy-pi"
    root.mkdir()
    (root / "io_counter").write_text("0\n")
    (root / "last_io_lba").write_text("0\n")
    (root / "last_io_write").write_text("0\n")
    (root / "last_io_us").write_text("0\n")
    (root / "track_crossings").write_text("0\n")
    return root


def test_reader_returns_zeroed_snapshot_for_fresh_sysfs(fake_sysfs: Path) -> None:
    reader = SysfsIOEventReader(sysfs_path=fake_sysfs)

    evt = reader.read()

    assert evt.counter == 0
    assert evt.last_lba == 0
    assert evt.last_is_write is False
    assert evt.last_us == 0
    assert evt.track_crossings == 0


def test_reader_parses_populated_snapshot(fake_sysfs: Path) -> None:
    (fake_sysfs / "io_counter").write_text("12345\n")
    (fake_sysfs / "last_io_lba").write_text("718\n")
    (fake_sysfs / "last_io_write").write_text("1\n")
    (fake_sysfs / "last_io_us").write_text("987654321\n")
    (fake_sysfs / "track_crossings").write_text("47\n")

    evt = SysfsIOEventReader(sysfs_path=fake_sysfs).read()

    assert evt.counter == 12345
    assert evt.last_lba == 718
    assert evt.last_is_write is True
    assert evt.last_us == 987654321
    assert evt.track_crossings == 47
