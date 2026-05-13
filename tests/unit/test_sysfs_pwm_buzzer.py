"""Unit tests for the SysfsPWMBuzzer hardware driver.

The real driver writes to /sys/class/pwm/pwmchip0/{export, pwm0/period,
pwm0/duty_cycle, pwm0/enable}. We simulate that by pre-creating the
same file tree under a tmp_path — the kernel auto-creates pwm0/ on the
first export write, but for tests we just put it there from the start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from usb_floppy_pi.audio.buzzer import SysfsPWMBuzzer


@pytest.fixture
def fake_pwmchip(tmp_path: Path) -> Path:
    """Pre-create the sysfs files SysfsPWMBuzzer expects.

    Returns the pwmchip directory; pwm0/ is already exported.
    """
    chip = tmp_path / "pwmchip0"
    chip.mkdir()
    (chip / "export").write_text("")
    (chip / "unexport").write_text("")
    (chip / "npwm").write_text("2\n")
    pwm0 = chip / "pwm0"
    pwm0.mkdir()
    (pwm0 / "period").write_text("0")
    (pwm0 / "duty_cycle").write_text("0")
    (pwm0 / "enable").write_text("0")
    (pwm0 / "polarity").write_text("normal")
    return chip


def test_play_tone_sets_period_duty_and_enables_output(fake_pwmchip: Path) -> None:
    buzzer = SysfsPWMBuzzer(pwmchip_path=fake_pwmchip)

    buzzer.play_tone(1000)  # 1 kHz

    pwm0 = fake_pwmchip / "pwm0"
    assert pwm0.joinpath("period").read_text().strip() == "1000000"  # 1 ms in ns
    duty = int(pwm0.joinpath("duty_cycle").read_text().strip())
    assert 0 < duty < 1000000  # some positive value below period
    assert pwm0.joinpath("enable").read_text().strip() == "1"


def test_silence_disables_output(fake_pwmchip: Path) -> None:
    buzzer = SysfsPWMBuzzer(pwmchip_path=fake_pwmchip)
    buzzer.play_tone(1000)

    buzzer.silence()

    assert (fake_pwmchip / "pwm0" / "enable").read_text().strip() == "0"


def test_volume_scales_duty_cycle_linearly(fake_pwmchip: Path) -> None:
    """At volume=100 duty=50% of period (loudest for piezo); at volume=50
    duty=25%; at volume=0 duty=0% (silent regardless of enable)."""
    buzzer = SysfsPWMBuzzer(pwmchip_path=fake_pwmchip, volume=100)
    buzzer.play_tone(1000)
    period = int((fake_pwmchip / "pwm0" / "period").read_text().strip())
    duty_full = int((fake_pwmchip / "pwm0" / "duty_cycle").read_text().strip())
    assert duty_full == period // 2

    buzzer.set_volume(50)
    buzzer.play_tone(1000)
    duty_half = int((fake_pwmchip / "pwm0" / "duty_cycle").read_text().strip())
    assert duty_half == period // 4


def test_volume_zero_keeps_output_silent(fake_pwmchip: Path) -> None:
    buzzer = SysfsPWMBuzzer(pwmchip_path=fake_pwmchip, volume=0)
    buzzer.play_tone(1000)
    # At zero volume, duty_cycle is 0 AND enable is 0 — no current draw.
    assert (fake_pwmchip / "pwm0" / "enable").read_text().strip() == "0"
