"""Async polling loop that drives the floppy buzzer.

Composes the four other audio components (reader, state machine, renderer,
buzzer-hardware) into a single ``tick()`` step plus an async ``run()`` that
calls tick() on a fixed interval.

``tick()`` is deliberately synchronous and externally clocked so unit tests
can stop time and step deterministically. ``run()`` is just the boilerplate
that turns it into an async coroutine; it has no logic of its own.
"""

from __future__ import annotations

import asyncio
import logging
import time

from usb_floppy_pi.audio.io_events import SysfsIOEventReader
from usb_floppy_pi.audio.renderer import SoundRenderer
from usb_floppy_pi.audio.state_machine import MotorStateMachine, SoundCommand

logger = logging.getLogger(__name__)


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

    async def run(self, *, interval_s: float = 0.02) -> None:
        """Run tick() at the requested cadence until cancelled.

        Default 20 ms (50 Hz) — fast enough to feel responsive to a
        Windows host polling the floppy, slow enough that one polling
        thread on the Pi Zero 2W is negligible CPU.
        """
        try:
            while True:
                self.tick(now_us=time.monotonic_ns() // 1000)
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            # Make sure the buzzer is left silent on shutdown — otherwise
            # the tone may continue forever after the service stops.
            self._renderer.render(SoundCommand.silence())
            raise
