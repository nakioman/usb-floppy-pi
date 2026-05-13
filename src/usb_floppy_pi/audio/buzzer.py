"""Userspace piezo buzzer driver.

Writes to /sys/class/pwm/pwmchip0/pwm0/ to play tones on a passive piezo
wired to GPIO 18 via the EMAKERS buzzer module (or equivalent transistor
buffer). See deploy/boot/config.txt.append for the `dtoverlay=pwm,pin=18`
that routes the pin to the PWM controller.

This file deliberately keeps the hardware driver separate from any state
machine or polling logic — the latter will live in sibling modules and
consume this via the ``BuzzerHardware`` shape (play_tone / silence).
"""

from __future__ import annotations

from pathlib import Path


class SysfsPWMBuzzer:
    """Drive a piezo via /sys/class/pwm/pwmchip0/pwm0/.

    The default ``pwmchip_path`` matches the live Pi after the dtoverlay
    is applied. Tests pass a temporary directory pre-populated with the
    same file layout (export/unexport/npwm/pwm0/{period,duty_cycle,enable,
    polarity}).
    """

    def __init__(
        self,
        *,
        pwmchip_path: Path = Path("/sys/class/pwm/pwmchip0"),
        volume: int = 100,
    ) -> None:
        self._chip = Path(pwmchip_path)
        self._pwm = self._chip / "pwm0"
        self._volume = max(0, min(100, volume))

    def set_volume(self, volume: int) -> None:
        """Set output volume 0..100. Takes effect on the next play_tone."""
        self._volume = max(0, min(100, volume))

    def play_tone(self, freq_hz: int) -> None:
        """Drive the piezo at ``freq_hz`` scaled by current volume.

        At volume=100 the duty cycle is 50% (loudest — maximum spectral
        energy at the fundamental for a piezo). Volume scales linearly
        down to 0% duty at volume=0, which is equivalent to silence.
        """
        if self._volume == 0:
            self.silence()
            return

        period_ns = 1_000_000_000 // freq_hz
        # volume=100 → duty = period/2 (50%); volume=v → duty = period*v/200.
        duty_ns = (period_ns * self._volume) // 200

        # The kernel requires duty <= period at all times. When lowering
        # frequency (longer period) we'd briefly violate that if we wrote
        # period first while the previous duty is still in place. Easiest
        # safe sequence: disable, then set period, then duty, then enable.
        (self._pwm / "enable").write_text("0\n")
        (self._pwm / "period").write_text(f"{period_ns}\n")
        (self._pwm / "duty_cycle").write_text(f"{duty_ns}\n")
        (self._pwm / "enable").write_text("1\n")

    def silence(self) -> None:
        """Stop driving the piezo (sets enable=0). Idempotent."""
        (self._pwm / "enable").write_text("0\n")
