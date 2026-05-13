"""Integration test for AudioLoop.tick().

End-to-end wiring: a real SysfsIOEventReader on a tmp_path tree, a real
FloppyStepDetector, a real SoundRenderer (with zero-duration clicks so
the test doesn't sleep), and a fake buzzer that records calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from usb_floppy_pi.audio.io_events import SysfsIOEventReader
from usb_floppy_pi.audio.loop import AudioLoop
from usb_floppy_pi.audio.renderer import SoundRenderer
from usb_floppy_pi.audio.state_machine import FloppyStepDetector


class FakeBuzzer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.silenced = True

    def play_tone(self, freq_hz: int, *, volume_scale: float = 1.0) -> None:
        self.silenced = False
        self.calls.append(("play_tone", freq_hz, volume_scale))

    def silence(self) -> None:
        self.silenced = True
        self.calls.append(("silence",))


@pytest.fixture
def fake_sysfs(tmp_path: Path) -> Path:
    root = tmp_path / "usb-floppy-pi"
    root.mkdir()
    (root / "io_counter").write_text("0\n")
    (root / "last_io_lba").write_text("0\n")
    (root / "last_io_write").write_text("0\n")
    (root / "last_io_us").write_text("0\n")
    (root / "track_crossings").write_text("0\n")
    return root


def _set_crossings(sysfs: Path, value: int) -> None:
    (sysfs / "track_crossings").write_text(f"{value}\n")


def test_loop_stays_silent_when_no_crossings(fake_sysfs: Path) -> None:
    buzzer = FakeBuzzer()
    loop = AudioLoop(
        reader=SysfsIOEventReader(sysfs_path=fake_sysfs),
        state_machine=FloppyStepDetector(),
        renderer=SoundRenderer(buzzer=buzzer, click_duration_s=0.0),
    )

    loop.tick(now_us=0)
    loop.tick(now_us=20_000)
    loop.tick(now_us=40_000)

    assert buzzer.silenced is True


def test_loop_clicks_on_each_new_crossing(fake_sysfs: Path) -> None:
    buzzer = FakeBuzzer()
    loop = AudioLoop(
        reader=SysfsIOEventReader(sysfs_path=fake_sysfs),
        state_machine=FloppyStepDetector(),
        renderer=SoundRenderer(buzzer=buzzer, click_duration_s=0.0),
    )

    # Baseline tick at crossings=0.
    loop.tick(now_us=0)

    # Crossings jump to 1 → one click rendered.
    _set_crossings(fake_sysfs, 1)
    loop.tick(now_us=20_000)
    play_tone_count = sum(1 for c in buzzer.calls if c[0] == "play_tone")
    assert play_tone_count == 1
    freq = next(c[1] for c in buzzer.calls if c[0] == "play_tone")
    assert 1300 <= freq <= 1900

    # Still at crossings=1 → no further clicks.
    loop.tick(now_us=40_000)
    assert sum(1 for c in buzzer.calls if c[0] == "play_tone") == 1
