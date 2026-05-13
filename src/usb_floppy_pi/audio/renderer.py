"""Translate ``SoundCommand`` into concrete buzzer activity.

FlashFloppy-style: each track step → one short PWM pulse (3 ms of tone).
No sustained tones, no chirps. The brief duration is what makes it sound
like a *click* rather than a tone — the piezo transient response dominates
over the steady-state.

Render is called from the audio polling loop ~50× per second; sleeping
briefly to bound the click length is acceptable (the rest of the system
runs on independent async tasks).
"""

from __future__ import annotations

import time
from typing import Protocol

from usb_floppy_pi.audio.state_machine import SoundCommand


class BuzzerHardware(Protocol):
    def play_tone(self, freq_hz: int) -> None: ...
    def silence(self) -> None: ...


# Tunables — picked to match the FlashFloppy `speaker_pulse()` character
# as closely as a piezo allows. 2 kHz is near the EMAKERS piezo's resonance
# (≈2048 Hz) for max efficiency, and 0.5 ms is one full cycle — the buzzer
# barely has time to oscillate, producing a transient *click* rather than
# a tone. Longer durations on this piezo blur into a brief beep.
_DEFAULT_CLICK_HZ = 2000
_DEFAULT_CLICK_DURATION_S = 0.0005  # 0.5 ms — one cycle at 2 kHz


class SoundRenderer:
    def __init__(
        self,
        *,
        buzzer: BuzzerHardware,
        click_hz: int = _DEFAULT_CLICK_HZ,
        click_duration_s: float = _DEFAULT_CLICK_DURATION_S,
    ) -> None:
        self._buzzer = buzzer
        self._click_hz = click_hz
        self._click_duration_s = click_duration_s

    def render(self, cmd: SoundCommand) -> None:
        if cmd.kind == "step_click":
            self._buzzer.play_tone(self._click_hz)
            time.sleep(self._click_duration_s)
            self._buzzer.silence()
            return
        # Everything else (including unknown kinds) → silent. The renderer
        # deliberately fails closed: a stuck-on tone is worse than a missed
        # click.
        self._buzzer.silence()
