"""Async polling loop that drives the floppy buzzer.

Composes the four other audio components (reader, state machine, renderer,
buzzer-hardware) into a single ``tick()`` step plus an async ``run()`` that
wakes whenever the kernel reports activity (or falls back to plain timer
polling on systems without the kernel notifier).

``tick()`` is deliberately synchronous and externally clocked so unit tests
can stop time and step deterministically. ``run()`` does the live-system
glue: open the kernel notifier fd, block on ``select.poll(POLLPRI)``,
short-timeout while there are pending clicks to drain, long-timeout when
the buzzer is idle. CPU stays at ~0% during idle.
"""

from __future__ import annotations

import asyncio
import logging
import os
import select
import time

from usb_floppy_pi.audio.io_events import SysfsIOEventReader
from usb_floppy_pi.audio.renderer import SoundRenderer
from usb_floppy_pi.audio.state_machine import MotorStateMachine, SoundCommand

logger = logging.getLogger(__name__)


# Timeout used by the kernel-notifier-based loop while the state machine
# has pending clicks to drain. Each tick fires at most one click, so this
# is effectively the inter-click spacing for a multi-track seek burst.
_ACTIVE_TIMEOUT_S = 0.020   # 20 ms

# Timeout used when there's nothing pending. The kernel will wake us up
# via sysfs_notify when real I/O happens, so this is just a safety net
# for missed events or systems without the notifier.
_IDLE_TIMEOUT_S = 1.0


class AudioLoop:
    def __init__(
        self,
        *,
        reader: SysfsIOEventReader,
        state_machine: MotorStateMachine,
        renderer: SoundRenderer,
    ) -> None:
        self._reader = reader
        self._sm = state_machine
        self._renderer = renderer

    def tick(self, *, now_us: int) -> None:
        evt = self._reader.read()
        cmd = self._sm.tick(evt, now_us=now_us)
        self._renderer.render(cmd)

    def _open_notifier(self) -> tuple[int | None, "select.poll | None"]:
        """Try to set up the kernel notifier. Returns (None, None) if the
        attribute doesn't exist or can't be primed (old kernel module
        without the sysfs_notify hook, or test environment) — caller
        falls back to pure timer polling in that case.
        """
        try:
            fd = self._reader.open_notify_fd()
        except (OSError, AttributeError) as exc:
            logger.info(
                "audio: kernel notifier unavailable (%s); falling back "
                "to %d ms polling", exc, int(_ACTIVE_TIMEOUT_S * 1000)
            )
            return None, None
        poller = select.poll()
        poller.register(fd, select.POLLPRI | select.POLLERR)
        logger.info("audio: using kernel sysfs_notify on track_crossings")
        return fd, poller

    @staticmethod
    def _drain_notify(fd: int) -> None:
        """Consume a POLLPRI event so the next poll() will block until
        the kernel fires another sysfs_notify. Sysfs requires lseek-to-0
        before each re-read for the POLLPRI cycle to restart."""
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            os.read(fd, 32)
        except OSError:
            # Reading the cached value should never block, but be safe.
            pass

    async def run(self, *, interval_s: float = _ACTIVE_TIMEOUT_S) -> None:
        """Run tick()s driven by kernel sysfs_notify events.

        Falls back to plain ``asyncio.sleep`` polling at ``interval_s``
        if the kernel notifier can't be opened (e.g. old module, dev box
        without /sys/class/usb_floppy).
        """
        notify_fd, poller = self._open_notifier()
        loop = asyncio.get_event_loop()

        try:
            while True:
                try:
                    self.tick(now_us=time.monotonic_ns() // 1000)
                except Exception as exc:
                    logger.warning(
                        "audio tick failed; retrying after %.3fs: %s",
                        interval_s, exc,
                    )
                    try:
                        self._renderer.render(SoundCommand.silence())
                    except Exception:
                        logger.debug(
                            "audio silence after failure also failed",
                            exc_info=True,
                        )

                # Decide the next wait. Pending clicks → drain fast.
                pending = self._sm.has_pending()
                timeout_s = interval_s if pending else _IDLE_TIMEOUT_S

                if poller is not None and not pending:
                    # Block in the default thread pool so the asyncio
                    # event loop stays free for other tasks (web API etc.).
                    # We only do this when idle — when actively draining
                    # clicks, a plain asyncio.sleep is cheaper than a
                    # thread hop.
                    timeout_ms = int(timeout_s * 1000)
                    await loop.run_in_executor(
                        None, poller.poll, timeout_ms
                    )
                    if notify_fd is not None:
                        self._drain_notify(notify_fd)
                else:
                    await asyncio.sleep(timeout_s)
        except asyncio.CancelledError:
            # Leave the buzzer silent on shutdown so the tone doesn't
            # keep playing after the service stops.
            try:
                self._renderer.render(SoundCommand.silence())
            except Exception:
                logger.debug(
                    "audio silence on shutdown failed", exc_info=True
                )
            raise
        finally:
            if notify_fd is not None:
                try:
                    os.close(notify_fd)
                except OSError:
                    pass
