"""Data models for the storage subsystem (pure dataclasses, no behavior)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FloppySet:
    """A folder under /home/pi/floppies/ representing a set of disks."""
    name: str
    path: Path
    disks: tuple[Path, ...] | list[Path]
    read_only: bool


@dataclass
class MountedImage:
    """The currently mounted disk on the USB gadget."""
    set_name: str
    disk_filename: str
    backing_path: Path
    read_only: bool
    is_session: bool
