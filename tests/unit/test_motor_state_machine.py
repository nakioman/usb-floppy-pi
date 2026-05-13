"""Unit tests for FloppyStepDetector.

Reads ``IOEvent.track_crossings`` (kernel-side counter that increments
once per real seek), enqueues one pending click per delta, drains them
one per tick subject to a 2.7 ms mask. Pure logic — no I/O.
"""

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

    assert cmd == SoundCommand.step_click()


def test_multi_track_seek_drains_clicks_across_ticks() -> None:
    """5 crossings in one tick → 5 clicks spread over 5 successive ticks."""
    det = FloppyStepDetector()
    det.tick(_evt(crossings=0), now_us=0)

    # Mass crossing event (e.g., long seek).
    cmds = [det.tick(_evt(crossings=5), now_us=20_000)]
    # Subsequent ticks have no new crossings but should drain the queue.
    for i in range(2, 7):
        cmds.append(det.tick(_evt(crossings=5), now_us=20_000 + i * 20_000))

    # First 5 ticks drain the queue → step_click each.
    assert cmds[0] == SoundCommand.step_click()
    assert cmds[1] == SoundCommand.step_click()
    assert cmds[2] == SoundCommand.step_click()
    assert cmds[3] == SoundCommand.step_click()
    assert cmds[4] == SoundCommand.step_click()
    # Queue drained — last tick is silent.
    assert cmds[5] == SoundCommand.silence()


def test_click_mask_suppresses_too_fast_clicks() -> None:
    """Even with pending clicks, the mask enforces minimum spacing — fits
    real-floppy step cadence."""
    det = FloppyStepDetector(click_mask_us=2700)
    det.tick(_evt(crossings=0), now_us=0)

    cmd1 = det.tick(_evt(crossings=2), now_us=10_000)   # fires
    cmd2 = det.tick(_evt(crossings=2), now_us=11_000)   # masked (1ms after)
    cmd3 = det.tick(_evt(crossings=2), now_us=14_000)   # fires (4ms after)

    assert cmd1 == SoundCommand.step_click()
    assert cmd2 == SoundCommand.silence()
    assert cmd3 == SoundCommand.step_click()


def test_pending_clicks_capped_to_avoid_runaway() -> None:
    """A flood of crossings (e.g., directory scan crossing 100 tracks)
    shouldn't keep clicking for seconds afterward. Cap = max_pending."""
    det = FloppyStepDetector(max_pending_clicks=3)
    det.tick(_evt(crossings=0), now_us=0)

    # 100 crossings arrive in one tick.
    det.tick(_evt(crossings=100), now_us=20_000)

    # Now drain — only `max_pending_clicks` should come out total.
    click_count = 1  # the one we just fired
    for i in range(2, 20):
        cmd = det.tick(_evt(crossings=100), now_us=20_000 + i * 20_000)
        if cmd == SoundCommand.step_click():
            click_count += 1

    assert click_count == 3
