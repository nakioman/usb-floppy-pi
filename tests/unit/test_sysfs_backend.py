"""Tests for gadget.sysfs_backend — uses tmp_path to simulate
/sys/class/usb_floppy/usb-floppy-pi/."""

from pathlib import Path

import pytest

from usb_floppy_pi.gadget.backend import GadgetParams
from usb_floppy_pi.gadget.sysfs_backend import SysfsBackend


def _params() -> GadgetParams:
    return GadgetParams(
        id_vendor=0x0525,
        id_product=0xA4A5,
        bcd_device=0x0001,
        manufacturer="Linux Foundation",
        product="USB Floppy",
        serial="0001",
        inquiry_string="TEAC    FD-05PUW         3000",
    )


@pytest.fixture
def fake_sysfs(tmp_path: Path) -> Path:
    """Build a fake /sys/class/usb_floppy/usb-floppy-pi tree with all the
    attributes the live kernel module exposes after a successful load."""
    root = tmp_path / "usb_floppy" / "usb-floppy-pi"
    root.mkdir(parents=True)
    defaults = {
        "lun0_file": "",
        "lun0_ro": "0",
        "lun0_inquiry_string": "TEAC    FD-05PUW         3000",
        "speed_preset": "floppy-real",
        "speed_read_kbps": "50",
        "speed_write_kbps": "30",
        "seek_us": "6000",
    }
    for name, value in defaults.items():
        (root / name).write_text(value + "\n")
    return root


def test_init_raises_if_sysfs_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        SysfsBackend(sysfs_root=missing)


def test_create_and_attach_are_noops(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.create_gadget(_params())  # must not raise
    backend.attach_to_udc()
    backend.detach_from_udc()
    backend.destroy_gadget()


def test_create_gadget_updates_inquiry_if_different(fake_sysfs: Path) -> None:
    (fake_sysfs / "lun0_inquiry_string").write_text("OLD VALUE\n")
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.create_gadget(_params())
    assert (
        (fake_sysfs / "lun0_inquiry_string").read_text().strip()
        == "TEAC    FD-05PUW         3000"
    )


def test_configure_lun_attaches_file(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.configure_lun(file=Path("/home/pi/floppies/X/Y.img"), ro=False)
    assert (
        (fake_sysfs / "lun0_file").read_text().strip()
        == "/home/pi/floppies/X/Y.img"
    )
    assert (fake_sysfs / "lun0_ro").read_text().strip() == "0"


def test_configure_lun_eject_writes_newline(fake_sysfs: Path) -> None:
    """Empty path -> backend writes '\\n' to detach. The kernel handler
    treats trailing-newline-only as 'eject' (the same behavior verified in
    Phase 1's ConfigFsBackend)."""
    (fake_sysfs / "lun0_file").write_text("/some/old.img\n")
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.configure_lun(file=None, ro=False)
    assert (fake_sysfs / "lun0_file").read_text() == "\n"


def test_configure_lun_applies_ro(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.configure_lun(file=Path("/x.img"), ro=True)
    assert (fake_sysfs / "lun0_ro").read_text().strip() == "1"


def test_set_speed_preset(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.set_speed_preset("unthrottled")
    assert (fake_sysfs / "speed_preset").read_text().strip() == "unthrottled"


def test_set_speed_preset_rejects_unknown(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    with pytest.raises(ValueError):
        backend.set_speed_preset("totally-fake-preset")


def test_set_volume_silent_when_buzzer_attr_absent(fake_sysfs: Path) -> None:
    """Phase 2.3 has no buzzer attrs in sysfs yet. set_volume must not raise —
    it just skips. When Phase 2.4 lands and exposes the attr, the same call
    starts having effect with no Python changes."""
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.set_volume(45)  # must not raise


def test_set_volume_writes_when_attr_present(fake_sysfs: Path) -> None:
    (fake_sysfs / "volume").write_text("0\n")
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.set_volume(45)
    assert (fake_sysfs / "volume").read_text().strip() == "45"


def test_set_volume_validates_range(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    with pytest.raises(ValueError):
        backend.set_volume(101)
    with pytest.raises(ValueError):
        backend.set_volume(-1)


def test_set_mute_silent_when_absent(fake_sysfs: Path) -> None:
    SysfsBackend(sysfs_root=fake_sysfs).set_mute(True)


def test_set_mute_writes_when_present(fake_sysfs: Path) -> None:
    (fake_sysfs / "mute").write_text("0\n")
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.set_mute(True)
    assert (fake_sysfs / "mute").read_text().strip() == "1"
    backend.set_mute(False)
    assert (fake_sysfs / "mute").read_text().strip() == "0"


def test_set_buzzer_enabled_silent_when_absent(fake_sysfs: Path) -> None:
    SysfsBackend(sysfs_root=fake_sysfs).set_buzzer_enabled(False)


def test_set_buzzer_enabled_writes_when_present(fake_sysfs: Path) -> None:
    (fake_sysfs / "buzzer").write_text("1\n")
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.set_buzzer_enabled(False)
    assert (fake_sysfs / "buzzer").read_text().strip() == "0"


def test_get_metrics_returns_phase23_attrs(fake_sysfs: Path) -> None:
    (fake_sysfs / "lun0_file").write_text("/x.img\n")
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    metrics = backend.get_metrics()
    assert metrics["lun0_file"] == "/x.img"
    assert metrics["speed_preset"] == "floppy-real"
    assert metrics["speed_read_kbps"] == 50
    assert metrics["speed_write_kbps"] == 30
    assert metrics["seek_us"] == 6000
    # Phase 2.4 attrs absent → reported as None
    assert metrics["volume"] is None
    assert metrics["mute"] is None
    assert metrics["buzzer"] is None


def test_get_metrics_includes_phase24_attrs_when_present(fake_sysfs: Path) -> None:
    (fake_sysfs / "volume").write_text("70\n")
    (fake_sysfs / "mute").write_text("0\n")
    (fake_sysfs / "buzzer").write_text("1\n")
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    metrics = backend.get_metrics()
    assert metrics["volume"] == 70
    assert metrics["mute"] is False
    assert metrics["buzzer"] is True


# --- current.img symlink + blank.img fallback ---------------------------


@pytest.fixture
def deploy_paths(tmp_path: Path) -> dict[str, Path]:
    """Create a fake /var/lib/usb-floppy-pi tree with blank.img + current.img symlink."""
    var = tmp_path / "var" / "lib" / "usb-floppy-pi"
    var.mkdir(parents=True)
    blank = var / "blank.img"
    blank.write_bytes(b"\x00" * 1474560)
    current = var / "current.img"
    current.symlink_to(blank)
    return {"blank": blank, "current": current}


def test_configure_lun_updates_symlink_to_target(
    fake_sysfs: Path, deploy_paths: dict[str, Path], tmp_path: Path
) -> None:
    real = tmp_path / "real.img"
    real.write_bytes(b"\x00" * 1474560)

    backend = SysfsBackend(
        sysfs_root=fake_sysfs,
        current_symlink_path=deploy_paths["current"],
        blank_image_path=deploy_paths["blank"],
    )
    backend.configure_lun(file=real, ro=False)

    # Symlink now points at the real image so the next boot's modprobe loads it.
    assert deploy_paths["current"].is_symlink()
    assert deploy_paths["current"].resolve() == real.resolve()
    # And sysfs got updated too (live kernel detach + reattach).
    assert (fake_sysfs / "lun0_file").read_text().strip() == real.as_posix()


def test_configure_lun_eject_points_symlink_to_blank(
    fake_sysfs: Path, deploy_paths: dict[str, Path], tmp_path: Path
) -> None:
    real = tmp_path / "real.img"
    real.write_bytes(b"\x00" * 1474560)
    # Start: symlink → real (simulating "user has X mounted")
    deploy_paths["current"].unlink()
    deploy_paths["current"].symlink_to(real)

    backend = SysfsBackend(
        sysfs_root=fake_sysfs,
        current_symlink_path=deploy_paths["current"],
        blank_image_path=deploy_paths["blank"],
    )
    backend.configure_lun(file=None, ro=False)

    # Eject → symlink falls back to blank.img so next boot still has media.
    assert deploy_paths["current"].is_symlink()
    assert deploy_paths["current"].resolve() == deploy_paths["blank"].resolve()
    # Sysfs eject (newline write).
    assert (fake_sysfs / "lun0_file").read_text() == "\n"


def test_configure_lun_works_without_symlink_paths(fake_sysfs: Path, tmp_path: Path) -> None:
    """When SysfsBackend is constructed without symlink paths, configure_lun
    only touches sysfs (no filesystem mutation outside /sys)."""
    real = tmp_path / "real.img"
    real.write_bytes(b"\x00" * 1474560)
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.configure_lun(file=real, ro=False)
    assert (fake_sysfs / "lun0_file").read_text().strip() == real.as_posix()
    # No symlink file should have been created in the test workspace.
    assert not (tmp_path / "current.img").exists()
