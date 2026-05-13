"""FlashFloppy-style activity detector for the floppy buzzer.

Reads ``IOEvent.track_crossings`` from the kernel: every increment between
two ticks represents one real stepper-motor seek (the kernel computes
crossings exactly from ``lba + nblocks``, including crossings *inside*
a single multi-sector request that LBA polling cannot detect).

For each new crossing the detector emits one seek burst immediately. That
lets the renderer produce real seek cadence inside a single 50 Hz polling
tick instead of smearing a long seek over many ticks.

Command kinds emitted:

    silence       — buzzer off this tick
    seek_burst    — one or more track-step clicks at realistic spacing
    motor_spin    — short spindle-start texture for same-track I/O
    activity_tick — quiet transfer texture when I/O happens without seeking
"""

from __future__ import annotations

from dataclasses import dataclass

from usb_floppy_pi.audio.io_events import IOEvent

# FlashFloppy uses 2700 µs (3 ms - 10%) as a minimum between clicks. The
# kernel throttle uses 6 ms seek timing for floppy-real, so that is the
# default burst cadence while 2700 us remains the lower safety bound.
_DEFAULT_MIN_CLICK_SPACING_US = 2700
_DEFAULT_SEEK_SPACING_US = 6000

_DEFAULT_MAX_BURST_CLICKS = 10
_DEFAULT_ACTIVITY_INTERVAL_US = 80_000
_DEFAULT_MOTOR_IDLE_US = 250_000


@dataclass(frozen=True)
class SoundCommand:
    kind: str
    clicks: int = 0
    spacing_us: int = 0
    base_hz: int = 0
    jitter_seed: int = 0

    @classmethod
    def silence(cls) -> SoundCommand:
        return cls(kind="silence")

    @classmethod
    def step_click(cls) -> SoundCommand:
        return cls.seek_burst(clicks=1, spacing_us=_DEFAULT_SEEK_SPACING_US)

    @classmethod
    def seek_burst(
        cls,
        *,
        clicks: int,
        spacing_us: int = _DEFAULT_SEEK_SPACING_US,
        base_hz: int = 0,
        jitter_seed: int = 0,
    ) -> SoundCommand:
        return cls(
            kind="seek_burst",
            clicks=max(0, clicks),
            spacing_us=max(_DEFAULT_MIN_CLICK_SPACING_US, spacing_us),
            base_hz=base_hz,
            jitter_seed=jitter_seed,
        )

    @classmethod
    def activity_tick(
        cls,
        *,
        base_hz: int = 1100,
        jitter_seed: int = 0,
    ) -> SoundCommand:
        return cls(kind="activity_tick", clicks=1, base_hz=base_hz, jitter_seed=jitter_seed)

    @classmethod
    def motor_spin(
        cls,
        *,
        clicks: int = 3,
        spacing_us: int = 5000,
        base_hz: int = 900,
        jitter_seed: int = 0,
    ) -> SoundCommand:
        return cls(
            kind="motor_spin",
            clicks=max(1, clicks),
            spacing_us=max(_DEFAULT_MIN_CLICK_SPACING_US, spacing_us),
            base_hz=base_hz,
            jitter_seed=jitter_seed,
        )


class FloppyStepDetector:
    """Translate kernel I/O counters into compact sound commands."""

    def __init__(
        self,
        *,
        seek_spacing_us: int = _DEFAULT_SEEK_SPACING_US,
        min_click_spacing_us: int = _DEFAULT_MIN_CLICK_SPACING_US,
        max_burst_clicks: int = _DEFAULT_MAX_BURST_CLICKS,
        activity_interval_us: int = _DEFAULT_ACTIVITY_INTERVAL_US,
        motor_idle_us: int = _DEFAULT_MOTOR_IDLE_US,
        click_mask_us: int | None = None,
        max_pending_clicks: int | None = None,
    ) -> None:
        # click_mask_us / max_pending_clicks are kept as compatibility names
        # for older tests/callers; they now map to burst spacing and cap.
        if click_mask_us is not None:
            min_click_spacing_us = click_mask_us
        if max_pending_clicks is not None:
            max_burst_clicks = max_pending_clicks
        self._seek_spacing_us = max(min_click_spacing_us, seek_spacing_us)
        self._min_click_spacing_us = min_click_spacing_us
        self._max_burst = max_burst_clicks
        self._activity_interval_us = activity_interval_us
        self._motor_idle_us = motor_idle_us
        self._last_crossings: int | None = None
        self._last_counter: int | None = None
        self._last_activity_us: int = -10**9
        self._last_io_us: int = -10**9

    def has_pending(self) -> bool:
        """Whether the detector has work the audio loop must drain across
        multiple ticks.

        With the burst-style design (each kernel-detected seek emits an
        entire ``seek_burst`` rendered inline in one ``render()`` call),
        there's no inter-tick state to drain — the loop is purely event-
        driven, blocking on ``sysfs_notify`` between bursts. Returning
        False keeps the loop in its idle-block mode at all times, which
        is exactly what we want for the kernel-notify CPU win.
        """
        return False

    def tick(self, event: IOEvent, *, now_us: int) -> SoundCommand:
        # First observation: establish baseline. Don't fire any pre-existing
        # crossings/activity as sound — those represent I/O that happened
        # before we started listening, no sound to make for them.
        if self._last_crossings is None:
            self._last_crossings = event.track_crossings
            self._last_counter = event.counter
            return SoundCommand.silence()

        delta = event.track_crossings - self._last_crossings
        counter_delta = event.counter - (
            self._last_counter if self._last_counter is not None else event.counter
        )
        self._last_crossings = event.track_crossings
        self._last_counter = event.counter

        if delta > 0:
            self._last_io_us = now_us
            return SoundCommand.seek_burst(
                clicks=min(delta, self._max_burst),
                spacing_us=max(self._seek_spacing_us, self._min_click_spacing_us),
                jitter_seed=(now_us ^ event.last_lba ^ event.track_crossings) & 0xFFFF,
            )

        if counter_delta > 0:
            was_idle = now_us - self._last_io_us >= self._motor_idle_us
            self._last_io_us = now_us
            if was_idle:
                self._last_activity_us = now_us
                return SoundCommand.motor_spin(
                    jitter_seed=(now_us ^ event.counter ^ event.last_lba) & 0xFFFF
                )
            if now_us - self._last_activity_us < self._activity_interval_us:
                return SoundCommand.silence()
            self._last_activity_us = now_us
            return SoundCommand.activity_tick(
                jitter_seed=(now_us ^ event.counter ^ event.last_lba) & 0xFFFF
            )

        return SoundCommand.silence()


# Back-compat alias — the rest of the code base imported MotorStateMachine
# before the design pivot to FloppyStepDetector.
MotorStateMachine = FloppyStepDetector
