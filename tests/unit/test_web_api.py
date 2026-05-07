"""Tests for web.api — uses FastAPI TestClient + mock controller."""

import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from usb_floppy_pi.gadget.backend import GadgetParams, MockBackend
from usb_floppy_pi.gadget.controller import GadgetController
from usb_floppy_pi.storage.library import Library
from usb_floppy_pi.web.api import build_app


def _params() -> GadgetParams:
    return GadgetParams(
        id_vendor=0x0644,
        id_product=0x0000,
        bcd_device=0x3000,
        manufacturer="TEAC",
        product="USB Floppy",
        serial="0001",
        inquiry_string="TEAC    FD-05PUW         3000",
    )


@pytest.fixture
def app_with_data(tmp_path: Path):
    """Build an app with one writable set and one read-only set."""
    # writable
    dos = tmp_path / "DOS 6.22"
    dos.mkdir()
    (dos / "DISK001.img").write_bytes(b"\x00" * 1474560)
    (dos / "DISK002.img").write_bytes(b"\x00" * 1474560)
    # read-only
    quake = tmp_path / "Quake"
    quake.mkdir()
    (quake / "ro").write_text("")
    (quake / "DISK1.img").write_bytes(b"\x00" * 1474560)

    loop = asyncio.new_event_loop()
    library = Library(tmp_path, loop=loop)
    loop.run_until_complete(library.start())
    backend = MockBackend()
    controller = GadgetController(backend, _params())
    loop.run_until_complete(controller.initialize())

    app = build_app(library=library, controller=controller, floppy_root=tmp_path)
    yield app, library, controller, backend
    loop.run_until_complete(library.stop())
    loop.close()


def test_get_sets_lists_all_sets(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.get("/api/sets")
        assert r.status_code == 200
        data = r.json()
        names = sorted(s["name"] for s in data["sets"])
        assert names == ["DOS 6.22", "Quake"]
        dos = next(s for s in data["sets"] if s["name"] == "DOS 6.22")
        assert dos["read_only"] is False
        assert sorted(dos["disks"]) == ["DISK001.img", "DISK002.img"]
        quake = next(s for s in data["sets"] if s["name"] == "Quake")
        assert quake["read_only"] is True


def test_get_state_when_nothing_mounted(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.get("/api/state")
        assert r.status_code == 200
        assert r.json()["mounted"] is None


def test_post_mount_writable_set(app_with_data) -> None:
    app, _, _, backend = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/mount", json={"set": "DOS 6.22", "disk": "DISK001.img"})
        assert r.status_code == 200
        assert r.json()["mounted"]["disk_filename"] == "DISK001.img"
        assert backend.lun_file is not None
        assert backend.lun_ro is False


def test_post_mount_readonly_set_applies_ro(app_with_data) -> None:
    app, _, _, backend = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/mount", json={"set": "Quake", "disk": "DISK1.img"})
        assert r.status_code == 200
        assert backend.lun_ro is True


def test_post_mount_missing_set_returns_404(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/mount", json={"set": "Nonexistent", "disk": "x.img"})
        assert r.status_code == 404


def test_post_eject(app_with_data) -> None:
    app, _, _, backend = app_with_data
    with TestClient(app) as client:
        client.post("/api/mount", json={"set": "DOS 6.22", "disk": "DISK001.img"})
        r = client.post("/api/eject")
        assert r.status_code == 200
        assert backend.lun_file is None


def test_post_readonly_creates_ro_marker(app_with_data, tmp_path: Path) -> None:
    app, library, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/sets/DOS 6.22/readonly", json={"ro": True})
        assert r.status_code == 200
        assert (tmp_path / "DOS 6.22" / "ro").exists()


def test_post_readonly_removes_ro_marker(app_with_data, tmp_path: Path) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/sets/Quake/readonly", json={"ro": False})
        assert r.status_code == 200
        assert not (tmp_path / "Quake" / "ro").exists()


def test_post_upload_img_passthrough(app_with_data, tmp_path: Path) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            data={"set": "DOS 6.22"},
            files={"file": ("UPLOAD.img", b"\x00" * 1474560, "application/octet-stream")},
        )
        assert r.status_code == 200
        assert r.json()["kind"] == "passthrough"
        assert (tmp_path / "DOS 6.22" / "UPLOAD.img").exists()


def test_post_upload_ima_renamed(app_with_data, tmp_path: Path) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            data={"set": "DOS 6.22"},
            files={"file": ("UPLOAD.ima", b"\x00" * 1474560, "application/octet-stream")},
        )
        assert r.status_code == 200
        assert r.json()["kind"] == "renamed"
        assert r.json()["final_filename"] == "UPLOAD.img"
        assert (tmp_path / "DOS 6.22" / "UPLOAD.img").exists()
        assert not (tmp_path / "DOS 6.22" / "UPLOAD.ima").exists()


def test_post_upload_imz_extracted(app_with_data, tmp_path: Path) -> None:
    app, _, _, _ = app_with_data
    inner_data = b"floppy" + b"\x00" * (1474560 - 6)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("inside.ima", inner_data)
    buf.seek(0)
    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            data={"set": "DOS 6.22"},
            files={"file": ("PACK.imz", buf.read(), "application/zip")},
        )
        assert r.status_code == 200
        assert r.json()["kind"] == "extracted"
        assert r.json()["final_filename"] == "PACK.img"
        assert (tmp_path / "DOS 6.22" / "PACK.img").exists()
        assert not (tmp_path / "DOS 6.22" / "PACK.imz").exists()


def test_post_upload_corrupted_imz_returns_400(app_with_data, tmp_path: Path) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            data={"set": "DOS 6.22"},
            files={"file": ("BROKEN.imz", b"not a zip", "application/zip")},
        )
        assert r.status_code == 400
