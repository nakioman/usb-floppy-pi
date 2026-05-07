"""Tests for gadget.controller using MockBackend."""
from pathlib import Path

import pytest

from usb_floppy_pi.gadget.backend import GadgetParams, MockBackend
from usb_floppy_pi.gadget.controller import (
    FLOPPY_SIZE_BYTES,
    DiskTooLargeError,
    GadgetController,
)
from usb_floppy_pi.storage.models import FloppySet, MountedImage


def _params() -> GadgetParams:
    return GadgetParams(
        id_vendor=0x0644, id_product=0x0000, bcd_device=0x3000,
        manufacturer="TEAC", product="USB Floppy", serial="0001",
        inquiry_string="TEAC    FD-05PUW         3000",
    )


def _make_set(tmp_path: Path, name: str, ro: bool, disks: list[tuple[str, int]]) -> FloppySet:
    base = tmp_path / name
    base.mkdir(exist_ok=True)
    if ro:
        (base / "ro").write_text("")
    paths = []
    for filename, size in disks:
        f = base / filename
        f.write_bytes(b"\x00" * size)
        paths.append(f)
    return FloppySet(name=name, path=base, disks=paths, read_only=ro)


@pytest.mark.asyncio
async def test_initialize_creates_and_attaches_gadget() -> None:
    backend = MockBackend()
    ctrl = GadgetController(backend, _params())
    await ctrl.initialize()
    assert backend.created is True
    assert backend.attached is True
    assert backend.lun_file is None


@pytest.mark.asyncio
async def test_mount_sets_lun_and_records_state(tmp_path: Path) -> None:
    backend = MockBackend()
    ctrl = GadgetController(backend, _params())
    await ctrl.initialize()
    fset = _make_set(tmp_path, "DOS", ro=False, disks=[("DISK1.img", FLOPPY_SIZE_BYTES)])
    mounted = await ctrl.mount(fset, fset.disks[0])
    assert backend.lun_file == fset.disks[0]
    assert backend.lun_ro is False
    assert isinstance(mounted, MountedImage)
    assert mounted.set_name == "DOS"
    assert mounted.disk_filename == "DISK1.img"
    assert mounted.is_session is False
    assert ctrl.current is mounted


@pytest.mark.asyncio
async def test_mount_applies_ro_flag(tmp_path: Path) -> None:
    backend = MockBackend()
    ctrl = GadgetController(backend, _params())
    await ctrl.initialize()
    fset = _make_set(tmp_path, "Quake", ro=True, disks=[("DISK1.img", FLOPPY_SIZE_BYTES)])
    await ctrl.mount(fset, fset.disks[0])
    assert backend.lun_ro is True


@pytest.mark.asyncio
async def test_mount_pads_undersize_image(tmp_path: Path) -> None:
    backend = MockBackend()
    ctrl = GadgetController(backend, _params())
    await ctrl.initialize()
    fset = _make_set(tmp_path, "DOS", ro=False, disks=[("DISK1.img", 100_000)])
    await ctrl.mount(fset, fset.disks[0])
    assert fset.disks[0].stat().st_size == FLOPPY_SIZE_BYTES


@pytest.mark.asyncio
async def test_mount_rejects_oversize_image(tmp_path: Path) -> None:
    backend = MockBackend()
    ctrl = GadgetController(backend, _params())
    await ctrl.initialize()
    fset = _make_set(tmp_path, "Big", ro=False, disks=[("BIG.img", FLOPPY_SIZE_BYTES + 1)])
    with pytest.raises(DiskTooLargeError):
        await ctrl.mount(fset, fset.disks[0])
    assert backend.lun_file is None


@pytest.mark.asyncio
async def test_eject_clears_lun(tmp_path: Path) -> None:
    backend = MockBackend()
    ctrl = GadgetController(backend, _params())
    await ctrl.initialize()
    fset = _make_set(tmp_path, "DOS", ro=False, disks=[("DISK1.img", FLOPPY_SIZE_BYTES)])
    await ctrl.mount(fset, fset.disks[0])
    await ctrl.eject()
    assert backend.lun_file is None
    assert ctrl.current is None


@pytest.mark.asyncio
async def test_swap_disk_within_set(tmp_path: Path) -> None:
    backend = MockBackend()
    ctrl = GadgetController(backend, _params())
    await ctrl.initialize()
    fset = _make_set(tmp_path, "DOS", ro=False, disks=[
        ("DISK1.img", FLOPPY_SIZE_BYTES),
        ("DISK2.img", FLOPPY_SIZE_BYTES),
    ])
    await ctrl.mount(fset, fset.disks[0])
    await ctrl.mount(fset, fset.disks[1])
    assert backend.lun_file == fset.disks[1]
    # Verify swap sequence: configure to None then to new file
    seq = [op for op in backend.ops_log if op.startswith("configure_lun")]
    assert "configure_lun(file=None, ro=False)" in seq
    assert any("DISK2.img" in op for op in seq)


@pytest.mark.asyncio
async def test_session_mount_uses_temp_copy(tmp_path: Path) -> None:
    backend = MockBackend()
    ctrl = GadgetController(backend, _params(), session_dir=tmp_path / "sessions")
    await ctrl.initialize()
    fset = _make_set(tmp_path, "DOS", ro=False, disks=[("DISK1.img", FLOPPY_SIZE_BYTES)])
    fset.disks[0].write_bytes(b"original" + b"\x00" * (FLOPPY_SIZE_BYTES - 8))
    mounted = await ctrl.mount(fset, fset.disks[0], session=True)
    assert mounted.is_session is True
    assert backend.lun_file != fset.disks[0]
    assert backend.lun_file is not None
    assert backend.lun_file.read_bytes()[:8] == b"original"
    # Eject removes the temp file
    await ctrl.eject()
    assert backend.lun_file is None
