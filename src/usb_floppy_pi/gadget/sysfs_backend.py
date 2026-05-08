"""Backend that talks to the kernel-side sysfs interface (Phase 2).

Implements GadgetBackend Protocol via /sys/class/usb_floppy/usb-floppy-pi/.
The kernel module owns gadget creation, UDC attachment, the throttle, and
(when Phase 2.4 lands) the buzzer. Python here only writes runtime config
to sysfs attributes.

Designed to work BOTH while Phase 2.3 is the latest deployed (only speed
attrs available) AND after Phase 2.4 lands (volume/mute/buzzer attrs
appear). Calls targeting attrs that aren't present yet are silent no-ops.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .backend import GadgetParams

logger = logging.getLogger(__name__)

VALID_SPEED_PRESETS = {"floppy-real", "floppy-fast", "unthrottled"}


class SysfsBackend:
    """Backend for the Phase 2 kernel module."""

    DEFAULT_ROOT = Path("/sys/class/usb_floppy/usb-floppy-pi")
    # Defaults match the install.sh layout. When current_symlink_path and
    # blank_image_path are both provided, configure_lun updates the symlink
    # so the next boot's modprobe (which reads /etc/modprobe.d/...
    # file=/var/lib/usb-floppy-pi/current.img) sees the same image.
    DEFAULT_CURRENT_SYMLINK = Path("/var/lib/usb-floppy-pi/current.img")
    DEFAULT_BLANK_IMAGE = Path("/var/lib/usb-floppy-pi/blank.img")

    def __init__(
        self,
        sysfs_root: Path | None = None,
        *,
        current_symlink_path: Path | None = None,
        blank_image_path: Path | None = None,
    ) -> None:
        self._root = sysfs_root or self.DEFAULT_ROOT
        if not self._root.exists():
            raise FileNotFoundError(
                f"Kernel module sysfs not found at {self._root}; "
                "is g_floppy.ko loaded?"
            )
        # Symlink + blank are optional (off by default to keep tests pure).
        # Both must be set for the symlink-update path to activate.
        self._current_symlink = current_symlink_path
        self._blank = blank_image_path
        self._symlink_managed = (
            self._current_symlink is not None and self._blank is not None
        )

    # --- GadgetBackend Protocol ---------------------------------------------

    def create_gadget(self, params: GadgetParams) -> None:
        """The kernel module created the gadget at module load time. We just
        verify the inquiry string matches what the controller wants and
        update it if it diverged."""
        cur = (self._root / "lun0_inquiry_string").read_text().strip()
        if cur != params.inquiry_string.strip():
            self._write("lun0_inquiry_string", params.inquiry_string)
            logger.info("updated inquiry_string to %r", params.inquiry_string)

    def destroy_gadget(self) -> None:
        # Module unloads independently (rmmod). No runtime API for it.
        pass

    def configure_lun(self, *, file: Path | None, ro: bool) -> None:
        """Mount/eject the LUN. The kernel rejects ro changes while a file
        is attached, so the safe pattern is: detach, brief settle, set ro,
        attach new file. Mirrors the dance ConfigFsBackend does in Phase 1.

        Also updates current.img symlink (if managed) so the next boot's
        modprobe loads with the same target — avoids the "first enumerate
        without media → Windows demotes to USB Drive" trap.
        """
        # 1. Update the symlink first so even if the rest fails halfway, the
        #    next boot reflects the user's last intent.
        if self._symlink_managed:
            self._update_current_symlink(file)

        # 2. Detach the current backing file. We write "\n" instead of ""
        #    because the kernel handler ignores zero-byte writes.
        self._write("lun0_file", "\n")
        if file is None:
            return
        time.sleep(0.05)  # let kernel release the LUN before flipping ro
        self._write("lun0_ro", "1" if ro else "0")
        # as_posix() so Windows-side tests don't write backslashes;
        # the kernel always interprets these paths as POSIX.
        self._write("lun0_file", file.as_posix())

    def _update_current_symlink(self, target: Path | None) -> None:
        """Point current.img at `target` (or blank.img if None). Idempotent."""
        link = self._current_symlink
        new_target = target if target is not None else self._blank
        # Replace the symlink atomically: unlink then symlink_to.
        # If link is a regular file (manual install error), preserve it by
        # renaming aside.
        if link.exists() or link.is_symlink():
            if link.is_symlink():
                link.unlink()
            else:
                link.rename(link.with_suffix(link.suffix + ".bak"))
        link.symlink_to(new_target)
        logger.debug("current.img → %s", new_target)

    def attach_to_udc(self) -> None:
        # Kernel auto-attaches at module load. No runtime API.
        pass

    def detach_from_udc(self) -> None:
        # Idem.
        pass

    # --- Phase 2 capabilities -----------------------------------------------

    def set_speed_preset(self, preset: str) -> None:
        if preset not in VALID_SPEED_PRESETS:
            raise ValueError(
                f"unknown speed_preset '{preset}'; "
                f"valid: {sorted(VALID_SPEED_PRESETS)}"
            )
        self._write("speed_preset", preset)

    def set_volume(self, volume: int) -> None:
        if not 0 <= volume <= 100:
            raise ValueError(f"volume must be 0..100, got {volume}")
        self._write_optional("volume", str(volume))

    def set_mute(self, mute: bool) -> None:
        self._write_optional("mute", "1" if mute else "0")

    def set_buzzer_enabled(self, enabled: bool) -> None:
        self._write_optional("buzzer", "1" if enabled else "0")

    def get_metrics(self) -> dict:
        return {
            "lun0_file": self._read_str("lun0_file"),
            "lun0_ro": self._read_bool("lun0_ro"),
            "speed_preset": self._read_str("speed_preset"),
            "speed_read_kbps": self._read_int("speed_read_kbps"),
            "speed_write_kbps": self._read_int("speed_write_kbps"),
            "seek_us": self._read_int("seek_us"),
            # Phase 2.4 attrs — None until kernel exposes them
            "volume": self._read_int_optional("volume"),
            "mute": self._read_bool_optional("mute"),
            "buzzer": self._read_bool_optional("buzzer"),
        }

    # --- low-level sysfs I/O ------------------------------------------------

    def _write(self, attr: str, value: str) -> None:
        (self._root / attr).write_text(value)

    def _write_optional(self, attr: str, value: str) -> None:
        """Write only if the attribute exists. Used for Phase 2.4 attrs that
        may or may not be present depending on which kernel module version
        is loaded."""
        path = self._root / attr
        if not path.exists():
            logger.debug("sysfs attr %s not present, skipping write", attr)
            return
        path.write_text(value)

    def _read_str(self, attr: str) -> str:
        return (self._root / attr).read_text().strip()

    def _read_int(self, attr: str) -> int:
        return int(self._read_str(attr))

    def _read_bool(self, attr: str) -> bool:
        return self._read_str(attr) == "1"

    def _read_int_optional(self, attr: str) -> int | None:
        path = self._root / attr
        if not path.exists():
            return None
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError):
            return None

    def _read_bool_optional(self, attr: str) -> bool | None:
        path = self._root / attr
        if not path.exists():
            return None
        try:
            return path.read_text().strip() == "1"
        except OSError:
            return None
