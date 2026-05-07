"""Tests for gadget.backend.MockBackend."""
from pathlib import Path

import pytest

from usb_floppy_pi.gadget.backend import GadgetParams, MockBackend


def test_mock_create_records_params() -> None:
    backend = MockBackend()
    params = GadgetParams(
        id_vendor=0x0644,
        id_product=0x0000,
        bcd_device=0x3000,
        manufacturer="TEAC",
        product="USB Floppy",
        serial="0001",
        inquiry_string="TEAC    FD-05PUW         3000",
    )
    backend.create_gadget(params)
    assert backend.created is True
    assert backend.params == params
    assert backend.lun_file is None
    assert backend.lun_ro is False
    assert backend.attached is False


def test_mock_configure_lun() -> None:
    backend = MockBackend()
    backend.create_gadget(_default_params())
    backend.configure_lun(file=Path("/x.img"), ro=True)
    assert backend.lun_file == Path("/x.img")
    assert backend.lun_ro is True


def test_mock_eject_clears_lun_file() -> None:
    backend = MockBackend()
    backend.create_gadget(_default_params())
    backend.configure_lun(file=Path("/x.img"), ro=False)
    backend.configure_lun(file=None, ro=False)
    assert backend.lun_file is None


def test_mock_attach_detach() -> None:
    backend = MockBackend()
    backend.create_gadget(_default_params())
    backend.attach_to_udc()
    assert backend.attached is True
    backend.detach_from_udc()
    assert backend.attached is False


def test_mock_destroy_resets() -> None:
    backend = MockBackend()
    backend.create_gadget(_default_params())
    backend.destroy_gadget()
    assert backend.created is False
    assert backend.lun_file is None


def test_mock_configure_lun_before_create_raises() -> None:
    backend = MockBackend()
    with pytest.raises(RuntimeError):
        backend.configure_lun(file=Path("/x.img"), ro=False)


def _default_params() -> GadgetParams:
    return GadgetParams(
        id_vendor=0x0644, id_product=0x0000, bcd_device=0x3000,
        manufacturer="TEAC", product="USB Floppy", serial="0001",
        inquiry_string="TEAC    FD-05PUW         3000",
    )
