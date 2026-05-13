"""Sysfs reader for the kernel-side I/O event counters.

Pairs with kernel/floppy_io_events.{c,h}. The kernel publishes four
read-only attributes on /sys/class/usb_floppy/usb-floppy-pi/ that the
buzzer state machine polls at ~50 Hz to decide whether the motor is
running, whether a seek just happened, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IOEvent:
    """Snapshot of the kernel's I/O event counters at the moment of read.

    Atomicity caveat: the kernel attributes are independent atomics, so a
    snapshot can capture inconsistent state across fields. For the buzzer
    this is harmless — the state machine only cares about relative changes
    between successive polls, not absolute consistency.

    ``track_crossings`` is the primary driver of the click stream: every
    increment between two polls represents one real stepper-motor seek
    (computed kernel-side from lba + nblocks per request, so it catches
    crossings *inside* a single multi-sector read that LBA polling misses).
    """

    counter: int            # cumulative blocks transferred
    last_lba: int           # LBA of most recent I/O
    last_is_write: bool     # whether the most recent I/O was a write
    last_us: int            # CLOCK_MONOTONIC microseconds at most recent I/O
    track_crossings: int    # cumulative track-boundary crossings observed


class SysfsIOEventReader:
    """Read the kernel-side I/O event counters from /sys/class/usb_floppy/."""

    def __init__(
        self,
        *,
        sysfs_path: Path = Path("/sys/class/usb_floppy/usb-floppy-pi"),
    ) -> None:
        self._root = Path(sysfs_path)

    def read(self) -> IOEvent:
        return IOEvent(
            counter=int((self._root / "io_counter").read_text().strip()),
            last_lba=int((self._root / "last_io_lba").read_text().strip()),
            last_is_write=(self._root / "last_io_write").read_text().strip() == "1",
            last_us=int((self._root / "last_io_us").read_text().strip()),
            track_crossings=int((self._root / "track_crossings").read_text().strip()),
        )
