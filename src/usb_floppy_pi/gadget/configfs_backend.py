"""Real GadgetBackend that writes to /sys/kernel/config/usb_gadget/.

Cannot be unit-tested. Verified via end-to-end manual testing on the Pi (Task 22).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .backend import GadgetParams

logger = logging.getLogger(__name__)


class ConfigFsBackend:
    """Manages a USB gadget through the Linux configfs interface.

    All ops are idempotent where possible. Methods raise OSError on permission /
    kernel-config issues — callers should catch and surface to the user.
    """

    GADGET_NAME = "floppy"
    CONFIGFS_ROOT = Path("/sys/kernel/config/usb_gadget")

    def __init__(self, configfs_root: Path | None = None) -> None:
        self._root = configfs_root or self.CONFIGFS_ROOT

    @property
    def gadget_dir(self) -> Path:
        return self._root / self.GADGET_NAME

    def create_gadget(self, params: GadgetParams) -> None:
        g = self.gadget_dir
        if g.exists():
            if self._is_well_formed(params):
                logger.info("gadget %s already configured; reusing", g)
                return
            logger.warning("gadget %s exists but appears malformed; rebuilding", g)
            self.destroy_gadget()
        g.mkdir(parents=True)
        _write(g / "idVendor", f"0x{params.id_vendor:04x}")
        _write(g / "idProduct", f"0x{params.id_product:04x}")
        _write(g / "bcdDevice", f"0x{params.bcd_device:04x}")
        _write(g / "bcdUSB", "0x0200")
        # Strings (en-us)
        strings = g / "strings" / "0x409"
        strings.mkdir(parents=True, exist_ok=True)
        _write(strings / "manufacturer", params.manufacturer)
        _write(strings / "product", params.product)
        _write(strings / "serialnumber", params.serial)
        # Function: mass_storage.usb0
        func = g / "functions" / "mass_storage.usb0"
        func.mkdir(parents=True, exist_ok=True)
        _write(func / "stall", "1")
        # LUN 0
        lun = func / "lun.0"
        lun.mkdir(parents=True, exist_ok=True)
        _write(lun / "removable", "1")
        _write(lun / "cdrom", "0")
        _write(lun / "nofua", "0")
        _write(lun / "ro", "0")
        _write(lun / "inquiry_string", params.inquiry_string)
        # Configuration 1
        cfg = g / "configs" / "c.1"
        cfg.mkdir(parents=True, exist_ok=True)
        _write(cfg / "MaxPower", "2")
        _write(cfg / "bmAttributes", "0xC0")
        cfg_strings = cfg / "strings" / "0x409"
        cfg_strings.mkdir(parents=True, exist_ok=True)
        _write(cfg_strings / "configuration", "USB Floppy Config")
        # Bind function to config
        link = cfg / "mass_storage.usb0"
        if not link.exists():
            link.symlink_to(func)
        logger.info("gadget tree created at %s", g)

    def _is_well_formed(self, params: GadgetParams) -> bool:
        """Check key markers that indicate a fully-built gadget."""
        g = self.gadget_dir
        inquiry_path = g / "functions" / "mass_storage.usb0" / "lun.0" / "inquiry_string"
        if not inquiry_path.exists():
            return False
        try:
            current = inquiry_path.read_text().strip()
        except OSError:
            return False
        # configfs trims trailing whitespace; compare prefix to the configured string trimmed
        return current.strip() == params.inquiry_string.strip()

    def destroy_gadget(self) -> None:
        g = self.gadget_dir
        if not g.exists():
            return
        # Detach
        try:
            udc = (g / "UDC").read_text().strip()
            if udc:
                (g / "UDC").write_text("\n")
        except OSError:
            pass
        # Remove function-config symlink
        cfg = g / "configs" / "c.1"
        link = cfg / "mass_storage.usb0"
        if link.is_symlink():
            link.unlink()
        # Remove string subdirs
        for s in (g / "configs" / "c.1" / "strings").iterdir():
            try:
                s.rmdir()
            except OSError:
                pass
        try:
            cfg.rmdir()
        except OSError:
            pass
        # Remove function (lun.0 must go first)
        func = g / "functions" / "mass_storage.usb0"
        if func.exists():
            try:
                (func / "lun.0").rmdir()
            except OSError:
                pass
            try:
                func.rmdir()
            except OSError:
                pass
        # Remove strings
        for s in (g / "strings").iterdir():
            try:
                s.rmdir()
            except OSError:
                pass
        try:
            g.rmdir()
        except OSError as exc:
            logger.warning("could not rmdir gadget root: %s", exc)
        logger.info("gadget destroyed")

    def configure_lun(self, *, file: Path | None, ro: bool) -> None:
        lun = self.gadget_dir / "functions" / "mass_storage.usb0" / "lun.0"
        # Always clear file before changing ro flag (kernel rejects ro change with file attached)
        _write(lun / "file", "")
        _write(lun / "ro", "1" if ro else "0")
        if file is not None:
            _write(lun / "file", str(file))

    def attach_to_udc(self) -> None:
        udc_path = self.gadget_dir / "UDC"
        # Pick the first UDC available
        udcs = sorted(p.name for p in Path("/sys/class/udc").iterdir())
        if not udcs:
            raise OSError("no UDC available — is dwc2 loaded?")
        _write(udc_path, udcs[0])
        logger.info("attached gadget to UDC %s", udcs[0])

    def detach_from_udc(self) -> None:
        udc_path = self.gadget_dir / "UDC"
        if udc_path.exists():
            _write(udc_path, "\n")


def _write(path: Path, value: str) -> None:
    """Write a value to a configfs attribute, creating it if needed."""
    path.write_text(value)
    # configfs is synchronous on write, but a tiny pause helps on slow paths.
    time.sleep(0.005)
