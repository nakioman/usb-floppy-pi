"""Tests for storage.models — pure dataclasses, no behavior."""
from pathlib import Path

from usb_floppy_pi.storage.models import FloppySet, MountedImage


def test_floppy_set_construction() -> None:
    s = FloppySet(
        name="DOS 6.22",
        path=Path("/home/pi/floppies/DOS 6.22"),
        disks=[Path("/home/pi/floppies/DOS 6.22/DISK001.img")],
        read_only=False,
    )
    assert s.name == "DOS 6.22"
    assert len(s.disks) == 1
    assert s.read_only is False


def test_floppy_set_multi_disk_sorted() -> None:
    """disks list is whatever scanner provides; models don't reorder."""
    base = Path("/home/pi/floppies/DOS 6.22")
    s = FloppySet(
        name="DOS 6.22",
        path=base,
        disks=[base / "DISK002.img", base / "DISK001.img"],
        read_only=False,
    )
    # Models preserve order
    assert s.disks[0].name == "DISK002.img"


def test_mounted_image_construction() -> None:
    m = MountedImage(
        set_name="DOS 6.22",
        disk_filename="DISK001.img",
        backing_path=Path("/home/pi/floppies/DOS 6.22/DISK001.img"),
        read_only=True,
        is_session=False,
    )
    assert m.read_only is True
    assert m.is_session is False
