"""Translate ``SoundCommand`` into concrete buzzer activity.

FlashFloppy-style: each track step becomes a short PWM pulse. Multi-track
seeks are rendered as tight bursts so they sound like a mechanical head
stepping. Same-track I/O gets a short motor-start texture, then sparse
low-volume ticks while transfer continues.

Render is called from the audio polling loop ~50× per second; sleeping
briefly to shape a seek burst is acceptable (the rest of the system runs
on independent async tasks).
"""

from __future__ import annotations

import time
from typing import Protocol

from usb_floppy_pi.audio.state_machine import SoundCommand


class BuzzerHardware(Protocol):
    def play_tone(self, freq_hz: int, *, volume_scale: float = 1.0) -> None: ...
    def silence(self) -> None: ...


_DEFAULT_SEEK_HZ = 1600
_DEFAULT_SEEK_DURATION_S = 0.0006
_DEFAULT_ACTIVITY_HZ = 900
_DEFAULT_ACTIVITY_DURATION_S = 0.00035
_DEFAULT_ACTIVITY_VOLUME = 0.35
_DEFAULT_MOTOR_SPIN_VOLUME = 0.65


class SoundRenderer:
    def __init__(
        self,
        *,
        buzzer: BuzzerHardware,
        click_hz: int = _DEFAULT_SEEK_HZ,
        click_duration_s: float = _DEFAULT_SEEK_DURATION_S,
        activity_hz: int = _DEFAULT_ACTIVITY_HZ,
        activity_duration_s: float = _DEFAULT_ACTIVITY_DURATION_S,
        activity_volume_scale: float = _DEFAULT_ACTIVITY_VOLUME,
        motor_spin_volume_scale: float = _DEFAULT_MOTOR_SPIN_VOLUME,
    ) -> None:
        self._buzzer = buzzer
        self._click_hz = click_hz
        self._click_duration_s = click_duration_s
        self._activity_hz = activity_hz
        self._activity_duration_s = activity_duration_s
        self._activity_volume_scale = activity_volume_scale
        self._motor_spin_volume_scale = motor_spin_volume_scale

    def render(self, cmd: SoundCommand) -> None:
        if cmd.kind in {"step_click", "seek_burst"}:
            clicks = max(1, cmd.clicks or 1)
            spacing_s = max(0.0, cmd.spacing_us / 1_000_000)
            for i in range(clicks):
                duration_s = self._seek_duration(cmd.jitter_seed, i)
                self._pulse(
                    self._seek_hz(cmd.base_hz or self._click_hz, cmd.jitter_seed, i),
                    duration_s,
                )
                if i != clicks - 1:
                    time.sleep(max(0.0, spacing_s - duration_s))
            return
        if cmd.kind == "activity_tick":
            self._pulse(
                self._activity_tick_hz(cmd.base_hz or self._activity_hz, cmd.jitter_seed),
                self._activity_duration_s,
                volume_scale=self._activity_volume_scale,
            )
            return
        if cmd.kind == "motor_spin":
            clicks = max(1, cmd.clicks or 3)
            spacing_s = max(0.0, cmd.spacing_us / 1_000_000)
            for i in range(clicks):
                duration_s = 0.0009 + (i * 0.00015)
                self._pulse(
                    self._motor_spin_hz(cmd.base_hz or self._activity_hz, cmd.jitter_seed, i),
                    duration_s,
                    volume_scale=self._motor_spin_volume_scale,
                )
                if i != clicks - 1:
                    time.sleep(max(0.0, spacing_s - duration_s))
            return
        # Everything else (including unknown kinds) → silent. The renderer
        # deliberately fails closed: a stuck-on tone is worse than a missed
        # click.
        self._buzzer.silence()

    def _pulse(self, freq_hz: int, duration_s: float, *, volume_scale: float = 1.0) -> None:
        self._buzzer.play_tone(freq_hz, volume_scale=volume_scale)
        time.sleep(duration_s)
        self._buzzer.silence()

    def _seek_hz(self, base_hz: int, seed: int, index: int) -> int:
        jitter = self._jitter(seed, index, width=601) - 300
        return max(1300, min(1900, base_hz + jitter))

    def _seek_duration(self, seed: int, index: int) -> float:
        if self._click_duration_s == 0:
            return 0.0
        jitter_us = self._jitter(seed ^ 0x5A5A, index, width=501)
        return (400 + jitter_us) / 1_000_000

    def _activity_tick_hz(self, base_hz: int, seed: int) -> int:
        jitter = self._jitter(seed ^ 0xA5A5, 0, width=401) - 200
        return max(700, min(1100, base_hz + jitter))

    def _motor_spin_hz(self, base_hz: int, seed: int, index: int) -> int:
        jitter = self._jitter(seed ^ 0x3333, index, width=151) - 75
        ramp = index * 90
        return max(750, min(1250, base_hz + ramp + jitter))

    @staticmethod
    def _jitter(seed: int, index: int, *, width: int) -> int:
        value = (seed + index * 1103515245 + 12345) & 0x7FFFFFFF
        return value % width
