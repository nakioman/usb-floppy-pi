"""High-level USB gadget operations: mount, eject, swap, session-mount."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from ..storage.models import FloppySet, MountedImage
from .backend import GadgetBackend, GadgetParams

logger = logging.getLogger(__name__)

FLOPPY_SIZE_BYTES = 1474560  # 1.44 MB
SWAP_DELAY_S = 0.15  # let the host process the eject before remount


class DiskTooLargeError(ValueError):
    """Raised when an .img file exceeds 1.44 MB."""


class GadgetController:
    """Mount/eject/swap operations on top of a GadgetBackend.

    Owns the logic of: padding undersize images, rejecting oversize images,
    applying the read-only flag, and creating session-mode temp copies.
    """

    def __init__(
        self,
        backend: GadgetBackend,
        params: GadgetParams,
        *,
        session_dir: Path | None = None,
    ) -> None:
        self._backend = backend
        self._params = params
        self._session_dir = session_dir or Path("/tmp/usb-floppy-pi-sessions")
        self._current: MountedImage | None = None

    @property
    def current(self) -> MountedImage | None:
        return self._current

    async def initialize(self) -> None:
        self._backend.create_gadget(self._params)
        self._backend.attach_to_udc()

    async def shutdown(self) -> None:
        try:
            self._backend.detach_from_udc()
        finally:
            self._backend.destroy_gadget()
            self._cleanup_session()

    async def mount(
        self,
        floppy_set: FloppySet,
        disk: Path,
        *,
        session: bool = False,
    ) -> MountedImage:
        if disk not in floppy_set.disks:
            raise ValueError(f"{disk} not in set {floppy_set.name}")

        size = os.stat(disk).st_size
        if size > FLOPPY_SIZE_BYTES:
            raise DiskTooLargeError(f"{disk} is {size} bytes; max is {FLOPPY_SIZE_BYTES}")

        backing = disk
        needs_pad_to_temp = size < FLOPPY_SIZE_BYTES and floppy_set.read_only and not session

        if session:
            # session mode: temp copy of source, pad if needed
            self._cleanup_session()
            self._session_dir.mkdir(parents=True, exist_ok=True)
            backing = self._session_dir / "session.img"
            shutil.copyfile(disk, backing)
            if size < FLOPPY_SIZE_BYTES:
                self._pad_to_full(backing)
        elif needs_pad_to_temp:
            # RO + undersize: pad in sidecar, don't touch source
            self._cleanup_session()
            self._session_dir.mkdir(parents=True, exist_ok=True)
            backing = self._session_dir / "ro-padded.img"
            shutil.copyfile(disk, backing)
            self._pad_to_full(backing)
        elif size < FLOPPY_SIZE_BYTES:
            # RW + undersize: pad the source in place (same as before)
            self._pad_to_full(disk)

        # If already mounted, do "eject + delay + remount" so the host sees a media change.
        if self._current is not None:
            self._backend.configure_lun(file=None, ro=False)
            await asyncio.sleep(SWAP_DELAY_S)

        ro = floppy_set.read_only and not session
        self._backend.configure_lun(file=backing, ro=ro)

        self._current = MountedImage(
            set_name=floppy_set.name,
            disk_filename=disk.name,
            backing_path=backing,
            read_only=ro,
            is_session=session,
        )
        logger.info("mounted %s/%s (ro=%s, session=%s)", floppy_set.name, disk.name, ro, session)
        return self._current

    async def eject(self) -> None:
        self._backend.configure_lun(file=None, ro=False)
        self._cleanup_session()
        self._current = None

    def _pad_to_full(self, path: Path) -> None:
        size = path.stat().st_size
        with open(path, "ab") as f:
            f.write(b"\x00" * (FLOPPY_SIZE_BYTES - size))
        logger.info("padded %s from %d to %d bytes", path, size, FLOPPY_SIZE_BYTES)

    def _cleanup_session(self) -> None:
        if self._session_dir.exists():
            for child in self._session_dir.iterdir():
                try:
                    child.unlink()
                except OSError:
                    pass
