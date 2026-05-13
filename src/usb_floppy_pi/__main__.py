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

from .audio.buzzer import SysfsPWMBuzzer
from .audio.io_events import SysfsIOEventReader
from .audio.loop import AudioLoop
from .audio.renderer import SoundRenderer
from .audio.state_machine import MotorStateMachine
from .core.config import Config, load_config, save_config
from .gadget.backend import GadgetBackend, GadgetParams
from .gadget.configfs_backend import ConfigFsBackend
from .gadget.controller import GadgetController
from .gadget.sysfs_backend import SysfsBackend
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
    audio_loop: AudioLoop | None = None
    audio_buzzer: SysfsPWMBuzzer | None = None

    async def shutdown(self) -> None:
        await self.controller.shutdown()
        await self.library.stop()


def _build_gadget_params() -> GadgetParams:
    """Descriptors for the USB gadget.

    USB device descriptor uses the Linux Foundation Mass Storage IDs, which
    Windows handles cleanly. We attempted to spoof TEAC FD-05PUW (0x0644:0x0000)
    but PID 0x0000 is reserved/test in the USB spec and triggers Code 10 in
    Windows' default Mass Storage driver.

    The SCSI INQUIRY response keeps the TEAC FD-05PUW string — that's what
    retro BIOSes inspect for USB-FDD legacy emulation, so the BIOS-A: trick
    still works while modern Windows sees a clean Linux Foundation device.
    """
    serial = _derive_serial()
    return GadgetParams(
        id_vendor=0x0525,
        id_product=0xA4A5,
        bcd_device=0x0001,
        manufacturer="Linux Foundation",
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
    # Build the gadget tree first, but don't expose it to the host yet — we
    # want to pre-load the last image so the very first thing the host sees
    # is a media-present device. Some hosts (Windows) refuse to recover from
    # an initial "no medium" enumeration.
    await controller.create_only()

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

    # Now expose the gadget to the host (with image already attached if there was one).
    await controller.activate()

    # Phase 2: apply persisted runtime settings to the backend. These are
    # default no-ops on the Phase 1 backends (ConfigFsBackend / MockBackend),
    # so the same code path is safe regardless of which backend is active.
    try:
        gadget_backend.set_speed_preset(cfg.speed_preset)
    except (ValueError, OSError) as exc:
        logger.warning("could not apply speed_preset=%r: %s", cfg.speed_preset, exc)
    try:
        gadget_backend.set_volume(cfg.volume)
    except (ValueError, OSError) as exc:
        logger.warning("could not apply volume=%d: %s", cfg.volume, exc)
    try:
        gadget_backend.set_mute(cfg.mute)
    except OSError as exc:
        logger.warning("could not apply mute=%s: %s", cfg.mute, exc)
    try:
        gadget_backend.set_buzzer_enabled(cfg.buzzer_enabled)
    except OSError as exc:
        logger.warning("could not apply buzzer_enabled=%s: %s", cfg.buzzer_enabled, exc)

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

    controller.mount = _mount  # type: ignore[method-assign]
    controller.eject = _eject  # type: ignore[method-assign]

    # Phase 2.4: build the audio buzzer (None on dev boxes without
    # /sys/class/pwm or /sys/class/usb_floppy). Volume/mute/enabled from
    # config so the buzzer respects persisted user preferences at startup.
    audio_pair = _build_audio_loop(
        volume=cfg.volume,
        mute=cfg.mute,
        enabled=cfg.buzzer_enabled,
    )
    audio_loop = audio_pair[0] if audio_pair else None
    audio_buzzer = audio_pair[1] if audio_pair else None

    def _persist_audio_change(field: str, value) -> None:
        if field == "volume":
            cfg.volume = int(value)
        elif field == "mute":
            cfg.mute = bool(value)
        elif field == "buzzer_enabled":
            cfg.buzzer_enabled = bool(value)
        else:
            return
        try:
            save_config(config_path, cfg)
        except OSError:
            logger.exception("could not persist audio config change")

    app = build_app(
        library=library,
        controller=controller,
        floppy_root=floppy_root,
        audio_buzzer=audio_buzzer,
        on_audio_change=_persist_audio_change,
    )

    return Runtime(
        config=cfg,
        config_path=config_path,
        library=library,
        controller=controller,
        app=app,
        audio_loop=audio_loop,
        audio_buzzer=audio_buzzer,
    )


def _build_sysfs_backend() -> SysfsBackend:
    """Build SysfsBackend with the current.img symlink + blank.img paths
    install.sh sets up. If those paths don't exist (e.g., dev box without
    install.sh having been run), fall back to a backend that doesn't
    manage the symlink — sysfs writes still work."""
    current = Path("/var/lib/usb-floppy-pi/current.img")
    blank = Path("/var/lib/usb-floppy-pi/blank.img")
    if blank.exists() and current.parent.exists():
        return SysfsBackend(
            current_symlink_path=current,
            blank_image_path=blank,
        )
    return SysfsBackend()


def _build_audio_loop(
    *, volume: int, mute: bool, enabled: bool
) -> tuple[AudioLoop, SysfsPWMBuzzer] | None:
    """Construct the audio loop + buzzer if both the kernel I/O event sysfs
    and the PWM sysfs are present. Returns None on any dev-box / pre-T11
    setup where either piece is missing — caller treats None as "no buzzer".

    The PWM channel 0 is exported here if it isn't already. Returns the
    buzzer separately so the web API can hot-reload its mute/volume/enabled.
    """
    pwm_chip = Path("/sys/class/pwm/pwmchip0")
    sysfs_root = Path("/sys/class/usb_floppy/usb-floppy-pi")
    if not pwm_chip.exists() or not sysfs_root.exists():
        logger.info(
            "audio: %s or %s missing — buzzer disabled",
            pwm_chip,
            sysfs_root,
        )
        return None
    if not (pwm_chip / "pwm0").exists():
        try:
            (pwm_chip / "export").write_text("0\n")
        except OSError as exc:
            logger.warning("audio: could not export pwm0 (%s); buzzer disabled", exc)
            return None

    buzzer = SysfsPWMBuzzer(
        pwmchip_path=pwm_chip,
        volume=volume,
        mute=mute,
        enabled=enabled,
    )
    loop = AudioLoop(
        reader=SysfsIOEventReader(sysfs_path=sysfs_root),
        state_machine=MotorStateMachine(),
        renderer=SoundRenderer(buzzer=buzzer),
    )
    logger.info(
        "audio: buzzer initialised (volume=%d, mute=%s, enabled=%s)",
        volume,
        mute,
        enabled,
    )
    return loop, buzzer


def _auto_select_backend() -> GadgetBackend:
    """Pick the best available backend based on what the kernel exposes.

    Priority (override via env USB_FLOPPY_BACKEND=sysfs|configfs|mock):
      1. Phase 2 kernel module sysfs class — preferred when available
      2. Phase 1 configfs gadget tree — fallback for unmigrated installs
      3. Hard error if neither — kernel modules not loaded
    """
    override = os.environ.get("USB_FLOPPY_BACKEND", "").strip().lower()
    if override == "sysfs":
        return _build_sysfs_backend()
    if override == "configfs":
        return ConfigFsBackend()
    if override == "mock":
        from .gadget.backend import MockBackend
        return MockBackend()

    if Path("/sys/class/usb_floppy").exists():
        logger.info("Phase 2 kernel module detected → SysfsBackend")
        return _build_sysfs_backend()
    if Path("/sys/kernel/config").exists():
        logger.info("Phase 1 configfs detected → ConfigFsBackend (legacy)")
        return ConfigFsBackend()
    raise RuntimeError(
        "Neither /sys/class/usb_floppy nor /sys/kernel/config available. "
        "Is the kernel module loaded? (g_floppy for Phase 2, libcomposite "
        "for Phase 1)"
    )


async def _main_async(config_path: Path, floppy_root: Path, port: int) -> None:
    backend = _auto_select_backend()
    runtime = await build_runtime(
        config_path=config_path,
        floppy_root=floppy_root,
        gadget_backend=backend,
    )

    # Audio loop already built by build_runtime — start it as a background
    # task here (only place we have an event loop available).
    audio_task: asyncio.Task | None = None
    if runtime.audio_loop is not None:
        audio_task = asyncio.create_task(runtime.audio_loop.run())

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
        if audio_task is not None:
            audio_task.cancel()
            try:
                await audio_task
            except asyncio.CancelledError:
                pass
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
