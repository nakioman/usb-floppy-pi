"""Unit tests for SoundRenderer."""

from __future__ import annotations

from unittest.mock import patch

from usb_floppy_pi.audio.renderer import SoundRenderer
from usb_floppy_pi.audio.state_machine import SoundCommand


class FakeBuzzer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.silenced: bool = True

    def play_tone(self, freq_hz: int, *, volume_scale: float = 1.0) -> None:
        self.silenced = False
        self.calls.append(("play_tone", freq_hz, volume_scale))

    def silence(self) -> None:
        self.silenced = True
        self.calls.append(("silence",))


def test_silence_command_silences_the_buzzer() -> None:
    b = FakeBuzzer()
    SoundRenderer(buzzer=b).render(SoundCommand.silence())
    assert b.silenced is True


def test_seek_burst_renders_expected_number_of_pulses() -> None:
    b = FakeBuzzer()
    r = SoundRenderer(buzzer=b, click_duration_s=0.0)

    r.render(SoundCommand.seek_burst(clicks=3, spacing_us=6000, jitter_seed=7))

    assert [c[0] for c in b.calls] == [
        "play_tone", "silence",
        "play_tone", "silence",
        "play_tone", "silence",
    ]
    assert b.silenced is True


def test_seek_burst_frequency_variation_stays_in_bounds() -> None:
    b = FakeBuzzer()
    r = SoundRenderer(buzzer=b, click_duration_s=0.0)

    r.render(SoundCommand.seek_burst(clicks=10, spacing_us=6000, jitter_seed=123))

    freqs = [c[1] for c in b.calls if c[0] == "play_tone"]
    assert len(freqs) == 10
    assert all(1300 <= f <= 1900 for f in freqs)
    assert len(set(freqs)) > 1


def test_seek_burst_sleeps_for_pulse_and_spacing() -> None:
    b = FakeBuzzer()
    r = SoundRenderer(buzzer=b)

    with patch("usb_floppy_pi.audio.renderer.time.sleep") as mock_sleep:
        r.render(SoundCommand.seek_burst(clicks=2, spacing_us=6000, jitter_seed=5))

    sleeps = [call.args[0] for call in mock_sleep.call_args_list]
    assert len(sleeps) == 3
    assert 0.0004 <= sleeps[0] <= 0.0009
    assert sleeps[1] >= 0.0051
    assert 0.0004 <= sleeps[2] <= 0.0009


def test_activity_tick_is_shorter_and_quieter() -> None:
    b = FakeBuzzer()
    r = SoundRenderer(buzzer=b)

    with patch("usb_floppy_pi.audio.renderer.time.sleep") as mock_sleep:
        r.render(SoundCommand.activity_tick(jitter_seed=99))

    play = next(c for c in b.calls if c[0] == "play_tone")
    assert 700 <= play[1] <= 1100
    assert play[2] == 0.35
    mock_sleep.assert_called_once_with(0.00035)


def test_motor_spin_renders_short_rising_texture() -> None:
    b = FakeBuzzer()
    r = SoundRenderer(buzzer=b)

    with patch("usb_floppy_pi.audio.renderer.time.sleep") as mock_sleep:
        r.render(SoundCommand.motor_spin(jitter_seed=321))

    plays = [c for c in b.calls if c[0] == "play_tone"]
    assert len(plays) == 3
    assert all(750 <= c[1] <= 1250 for c in plays)
    assert all(c[2] == 0.65 for c in plays)
    assert plays[-1][1] >= plays[0][1]
    assert len(mock_sleep.call_args_list) == 5


def test_unknown_command_kind_falls_back_to_silence() -> None:
    """Defensive: an unknown command shouldn't keep an old tone playing."""
    b = FakeBuzzer()
    r = SoundRenderer(buzzer=b, click_duration_s=0.0)
    r.render(SoundCommand.step_click())  # ends silent

    r.render(SoundCommand(kind="garbage"))

    assert b.silenced is True
