"""Sysfs reader for the kernel-side I/O event counters.

Pairs with kernel/floppy_io_events.{c,h}. The kernel publishes
read-only attributes on /sys/class/usb_floppy/usb-floppy-pi/ that the
buzzer state machine consumes:

  - io_counter / last_io_lba / last_io_write / last_io_us  (general I/O)
  - track_crossings                                        (seek events)

Modern kernel modules also call ``sysfs_notify(kobj, NULL,
"track_crossings")`` on every seek, so userspace can ``poll(POLLPRI)``
the attribute file and sleep at ~0% CPU between events instead of
busy-polling. ``open_notify_fd()`` returns a file descriptor primed for
exactly that pattern.
"""

from __future__ import annotations

import os
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

    def open_notify_fd(self) -> int:
        """Open ``track_crossings`` for ``poll(POLLPRI)`` event-driven use.

        After the kernel calls ``sysfs_notify`` on the attribute, a
        ``poll()`` registered with POLLPRI will wake — letting the
        audio loop sleep at ~0% CPU between real seek events. The
        caller is responsible for ``os.close()``-ing the returned fd.

        Sysfs requires the FD to be "primed" once: read it once after
        open so subsequent POLLPRI events represent fresh notifies
        rather than the initial readable state.
        """
        fd = os.open(self._root / "track_crossings", os.O_RDONLY)
        try:
            os.read(fd, 32)  # prime — consume initial readable state
        except OSError:
            os.close(fd)
            raise
        return fd
