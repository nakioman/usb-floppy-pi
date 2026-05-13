"""Unit tests for SoundRenderer.

step_click → brief tone pulse + silence (clicky transient).
silence    → buzzer off.
Anything else → buzzer off (fail closed).
"""

from __future__ import annotations

from unittest.mock import patch

from usb_floppy_pi.audio.renderer import SoundRenderer
from usb_floppy_pi.audio.state_machine import SoundCommand


class FakeBuzzer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.silenced: bool = True

    def play_tone(self, freq_hz: int) -> None:
        self.silenced = False
        self.calls.append(("play_tone", freq_hz))

    def silence(self) -> None:
        self.silenced = True
        self.calls.append(("silence",))


def test_silence_command_silences_the_buzzer() -> None:
    b = FakeBuzzer()
    SoundRenderer(buzzer=b).render(SoundCommand.silence())
    assert b.silenced is True


def test_step_click_plays_brief_tone_then_silences() -> None:
    b = FakeBuzzer()
    r = SoundRenderer(buzzer=b, click_hz=2000, click_duration_s=0.0)  # zero sleep for test speed

    r.render(SoundCommand.step_click())

    # Sequence must be: tone first, then silence — so the buzzer is left
    # quiet between clicks rather than humming the click frequency.
    assert b.calls == [("play_tone", 2000), ("silence",)]
    assert b.silenced is True


def test_step_click_respects_configured_frequency() -> None:
    b = FakeBuzzer()
    r = SoundRenderer(buzzer=b, click_hz=3500, click_duration_s=0.0)

    r.render(SoundCommand.step_click())

    assert b.calls[0] == ("play_tone", 3500)


def test_step_click_sleeps_for_configured_duration() -> None:
    b = FakeBuzzer()
    r = SoundRenderer(buzzer=b, click_duration_s=0.007)

    with patch("usb_floppy_pi.audio.renderer.time.sleep") as mock_sleep:
        r.render(SoundCommand.step_click())

    mock_sleep.assert_called_once_with(0.007)


def test_unknown_command_kind_falls_back_to_silence() -> None:
    """Defensive: an unknown command shouldn't keep an old tone playing."""
    b = FakeBuzzer()
    r = SoundRenderer(buzzer=b, click_duration_s=0.0)
    r.render(SoundCommand.step_click())  # ends silent

    r.render(SoundCommand(kind="garbage"))

    assert b.silenced is True
