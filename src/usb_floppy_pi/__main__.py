"""Entry point: load config, init storage + gadget, restore last mount, run web server."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from .core.config import Config, load_config, save_config
from .gadget.backend import GadgetBackend, GadgetParams
from .gadget.configfs_backend import ConfigFsBackend
from .gadget.controller import GadgetController
from .storage.library import Library
from .web.api import build_app

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("/etc/usb-floppy-pi/config.json")
DEFAULT_FLOPPY_ROOT = Path("/home/pi/floppies")


@dataclass
class Runtime:
    config: Config
    config_path: Path
    library: Library
    controller: GadgetController
    app: object  # FastAPI

    async def shutdown(self) -> None:
        await self.controller.shutdown()
        await self.library.stop()


def _build_gadget_params() -> GadgetParams:
    serial = _derive_serial()
    return GadgetParams(
        id_vendor=0x0644,         # TEAC
        id_product=0x0000,        # FD-05PUW
        bcd_device=0x3000,
        manufacturer="TEAC",
        product="USB Floppy",
        serial=serial,
        inquiry_string="TEAC    FD-05PUW         3000",
    )


def _derive_serial() -> str:
    """Derive a stable serial from the host's MAC, falling back to hostname."""
    try:
        with open("/sys/class/net/wlan0/address") as f:
            return f.read().strip().replace(":", "").upper()
    except OSError:
        return socket.gethostname()[:12].upper() or "FLOPPY00"


async def build_runtime(
    *,
    config_path: Path,
    floppy_root: Path,
    gadget_backend: GadgetBackend,
) -> Runtime:
    """Build all components and restore last-mounted image. Used by tests + main()."""
    cfg = load_config(config_path)
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    loop = asyncio.get_running_loop()
    library = Library(floppy_root, loop=loop)
    await library.start()

    controller = GadgetController(gadget_backend, _build_gadget_params())
    await controller.initialize()

    if cfg.last_mounted is not None:
        target_set_name = cfg.last_mounted.get("set")
        target_disk_name = cfg.last_mounted.get("disk")
        for s in library.sets:
            if s.name != target_set_name:
                continue
            for d in s.disks:
                if d.name == target_disk_name:
                    try:
                        await controller.mount(s, d)
                    except Exception:
                        logger.exception("could not restore last mount")
                    break
            break
        if controller.current is None:
            logger.info("last_mounted %s/%s no longer present", target_set_name, target_disk_name)

    # Subscribe to changes so we persist last_mounted whenever it changes.
    def _persist_last_mounted() -> None:
        m = controller.current
        cfg.last_mounted = (
            {"set": m.set_name, "disk": m.disk_filename}
            if m is not None and not m.is_session
            else None
        )
        try:
            save_config(config_path, cfg)
        except OSError:
            logger.exception("could not persist config")

    # Wrap controller to persist on every successful mount/eject.
    original_mount = controller.mount
    original_eject = controller.eject

    async def _mount(*args, **kwargs):
        result = await original_mount(*args, **kwargs)
        _persist_last_mounted()
        return result

    async def _eject():
        await original_eject()
        _persist_last_mounted()

    controller.mount = _mount       # type: ignore[method-assign]
    controller.eject = _eject       # type: ignore[method-assign]

    app = build_app(library=library, controller=controller, floppy_root=floppy_root)

    return Runtime(
        config=cfg,
        config_path=config_path,
        library=library,
        controller=controller,
        app=app,
    )


async def _main_async(config_path: Path, floppy_root: Path, port: int) -> None:
    backend = ConfigFsBackend()
    runtime = await build_runtime(
        config_path=config_path,
        floppy_root=floppy_root,
        gadget_backend=backend,
    )
    config = uvicorn.Config(
        runtime.app,
        host="0.0.0.0",
        port=port,
        log_level=runtime.config.log_level.lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await runtime.shutdown()


def main() -> None:
    config_path = Path(os.environ.get("USB_FLOPPY_CONFIG", DEFAULT_CONFIG_PATH))
    floppy_root = Path(os.environ.get("USB_FLOPPY_ROOT", DEFAULT_FLOPPY_ROOT))
    port = int(os.environ.get("USB_FLOPPY_PORT", "80"))
    try:
        asyncio.run(_main_async(config_path, floppy_root, port))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
