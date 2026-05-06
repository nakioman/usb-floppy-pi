"""Filesystem scanner: builds FloppySet list from /home/pi/floppies/."""
from __future__ import annotations

from pathlib import Path

from .models import FloppySet


def scan(root: Path) -> list[FloppySet]:
    """Scan a flat directory of floppy sets.

    Each direct subdirectory is one FloppySet. Sets are returned alphabetically.
    Subdirectories without any .img file are skipped.
    """
    if not root.is_dir():
        return []
    sets: list[FloppySet] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        disks = sorted(
            [p for p in child.iterdir() if p.is_file() and p.suffix.lower() == ".img"],
            key=lambda p: p.name,
        )
        if not disks:
            continue
        read_only = (child / "ro").exists()
        sets.append(FloppySet(
            name=child.name,
            path=child,
            disks=disks,
            read_only=read_only,
        ))
    return sets
