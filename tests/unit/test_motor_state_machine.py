"""Unit tests for FloppyStepDetector."""

from __future__ import annotations

from usb_floppy_pi.audio.io_events import IOEvent
from usb_floppy_pi.audio.state_machine import FloppyStepDetector, SoundCommand


def _evt(crossings: int = 0, counter: int = 0) -> IOEvent:
    """Build an IOEvent with the only fields the detector reads."""
    return IOEvent(
        counter=counter,
        last_lba=0,
        last_is_write=False,
        last_us=0,
        track_crossings=crossings,
    )


def test_first_tick_returns_silence_no_baseline_yet() -> None:
    """First observation establishes the crossings baseline — no clicks
    fire for crossings that happened before we started listening."""
    det = FloppyStepDetector()

    cmd = det.tick(_evt(crossings=42), now_us=0)

    assert cmd == SoundCommand.silence()


def test_no_new_crossings_stays_silent() -> None:
    det = FloppyStepDetector()
    det.tick(_evt(crossings=5), now_us=0)

    cmd = det.tick(_evt(crossings=5), now_us=20_000)

    assert cmd == SoundCommand.silence()


def test_single_crossing_fires_one_click() -> None:
    det = FloppyStepDetector()
    det.tick(_evt(crossings=0), now_us=0)

    cmd = det.tick(_evt(crossings=1), now_us=20_000)

    assert cmd.kind == "seek_burst"
    assert cmd.clicks == 1
    assert cmd.spacing_us == 6000


def test_multi_track_seek_returns_one_burst() -> None:
    """5 crossings in one tick become one burst with real seek spacing."""
    det = FloppyStepDetector()
    det.tick(_evt(crossings=0), now_us=0)

    cmd = det.tick(_evt(crossings=5), now_us=20_000)

    assert cmd.kind == "seek_burst"
    assert cmd.clicks == 5
    assert cmd.spacing_us == 6000
    assert det.tick(_evt(crossings=5), now_us=40_000) == SoundCommand.silence()


def test_click_mask_clamps_burst_spacing() -> None:
    det = FloppyStepDetector(click_mask_us=2700)
    det.tick(_evt(crossings=0), now_us=0)

    cmd = det.tick(_evt(crossings=2), now_us=10_000)

    assert cmd.kind == "seek_burst"
    assert cmd.spacing_us >= 2700


def test_burst_clicks_capped_to_avoid_runaway() -> None:
    """A flood of crossings (e.g., directory scan crossing 100 tracks)
    shouldn't render a huge blocking burst."""
    det = FloppyStepDetector(max_pending_clicks=3)
    det.tick(_evt(crossings=0), now_us=0)

    cmd = det.tick(_evt(crossings=100), now_us=20_000)

    assert cmd.kind == "seek_burst"
    assert cmd.clicks == 3


def test_counter_delta_without_seek_after_idle_emits_motor_spin() -> None:
    det = FloppyStepDetector(activity_interval_us=80_000)
    det.tick(_evt(crossings=0, counter=0), now_us=0)

    cmd = det.tick(_evt(crossings=0, counter=1), now_us=80_000)

    assert cmd.kind == "motor_spin"
    assert cmd.clicks == 3
    assert cmd.spacing_us == 5000


def test_activity_ticks_are_bounded_and_stop_when_idle() -> None:
    det = FloppyStepDetector(activity_interval_us=80_000)
    det.tick(_evt(crossings=0, counter=0), now_us=0)

    assert det.tick(_evt(crossings=0, counter=1), now_us=10_000).kind == "motor_spin"
    assert det.tick(_evt(crossings=0, counter=2), now_us=20_000) == SoundCommand.silence()
    assert det.tick(_evt(crossings=0, counter=2), now_us=120_000) == SoundCommand.silence()


def test_sustained_same_track_io_emits_bounded_activity_tick() -> None:
    det = FloppyStepDetector(activity_interval_us=80_000)
    det.tick(_evt(crossings=0, counter=0), now_us=0)
    det.tick(_evt(crossings=0, counter=1), now_us=10_000)  # motor spin

    cmd = det.tick(_evt(crossings=0, counter=2), now_us=100_000)

    assert cmd == SoundCommand.activity_tick(jitter_seed=(100_000 ^ 2 ^ 0) & 0xFFFF)
