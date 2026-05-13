"""FlashFloppy-style step detector for the floppy buzzer.

Reads ``IOEvent.track_crossings`` from the kernel: every increment between
two ticks represents one real stepper-motor seek (the kernel computes
crossings exactly from ``lba + nblocks``, including crossings *inside*
a single multi-sector request that LBA polling cannot detect).

For each new crossing the detector schedules one click. Clicks are drained
one per tick (subject to ``click_mask_us``) so a multi-track seek sounds
like a sequence of "chunka-chunka" steps instead of a single muted blip.

Two command kinds emitted:

    silence    — buzzer off this tick
    step_click — one brief click (rendered by SoundRenderer)
"""

from __future__ import annotations

from dataclasses import dataclass

from usb_floppy_pi.audio.io_events import IOEvent


# FlashFloppy uses 2700 µs (3 ms − 10%) as the min between clicks. We use
# the same value — keeps maximum click rate at ~370 Hz so consecutive
# clicks during a long seek still sound mechanical.
_DEFAULT_CLICK_MASK_US = 2700

# Cap on pending clicks. Without a cap, a directory scan that crosses 50
# tracks would keep clicking for 50 ticks = 1 second after the host is
# done. Sounds wrong. Cap means worst-case "long seek tail" is ~10 ticks
# (≈200 ms) regardless of how many crossings happened.
_DEFAULT_MAX_PENDING_CLICKS = 10


@dataclass(frozen=True)
class SoundCommand:
    kind: str

    @classmethod
    def silence(cls) -> "SoundCommand":
        return cls(kind="silence")

    @classmethod
    def step_click(cls) -> "SoundCommand":
        return cls(kind="step_click")


class FloppyStepDetector:
    """Emit one ``step_click`` for each kernel-reported track crossing,
    rate-limited by ``click_mask_us`` and queue-bounded by
    ``max_pending_clicks``."""

    def __init__(
        self,
        *,
        click_mask_us: int = _DEFAULT_CLICK_MASK_US,
        max_pending_clicks: int = _DEFAULT_MAX_PENDING_CLICKS,
    ) -> None:
        self._click_mask_us = click_mask_us
        self._max_pending = max_pending_clicks
        self._last_crossings: int | None = None
        self._pending: int = 0
        self._last_click_us: int = -10**9  # ensure first click fires

    def tick(self, event: IOEvent, *, now_us: int) -> SoundCommand:
        # First observation: establish baseline. Don't fire any pre-existing
        # crossings as clicks — those represent activity that happened
        # before we started listening, no sound to make for them.
        if self._last_crossings is None:
            self._last_crossings = event.track_crossings
            return SoundCommand.silence()

        delta = event.track_crossings - self._last_crossings
        self._last_crossings = event.track_crossings

        if delta > 0:
            self._pending = min(self._pending + delta, self._max_pending)

        if self._pending == 0:
            return SoundCommand.silence()

        if now_us - self._last_click_us < self._click_mask_us:
            return SoundCommand.silence()

        self._pending -= 1
        self._last_click_us = now_us
        return SoundCommand.step_click()


# Back-compat alias — the rest of the code base imported MotorStateMachine
# before the design pivot to FloppyStepDetector.
MotorStateMachine = FloppyStepDetector
