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
            files=[("files", ("UPLOAD.img", b"\x00" * 1474560, "application/octet-stream"))],
        )
        assert r.status_code == 200
        body = r.json()
        assert body["set"] == "DOS 6.22"
        assert len(body["results"]) == 1
        assert body["results"][0]["kind"] == "passthrough"
        assert body["results"][0]["final_filename"] == "UPLOAD.img"
        assert (tmp_path / "DOS 6.22" / "UPLOAD.img").exists()


def test_post_upload_ima_renamed(app_with_data, tmp_path: Path) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            data={"set": "DOS 6.22"},
            files=[("files", ("UPLOAD.ima", b"\x00" * 1474560, "application/octet-stream"))],
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert results[0]["kind"] == "renamed"
        assert results[0]["final_filename"] == "UPLOAD.img"
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
            files=[("files", ("PACK.imz", buf.read(), "application/zip"))],
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert results[0]["kind"] == "extracted"
        assert results[0]["final_filename"] == "PACK.img"
        assert (tmp_path / "DOS 6.22" / "PACK.img").exists()
        assert not (tmp_path / "DOS 6.22" / "PACK.imz").exists()


def test_post_upload_corrupted_imz_returns_per_file_error(app_with_data, tmp_path: Path) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            data={"set": "DOS 6.22"},
            files=[("files", ("BROKEN.imz", b"not a zip", "application/zip"))],
        )
        # 200 with per-file error in results — partial-success semantics
        assert r.status_code == 200
        results = r.json()["results"]
        assert results[0]["kind"] == "error"
        assert "imz" in results[0]["detail"].lower()


def test_post_upload_oversize_returns_per_file_error(app_with_data, tmp_path: Path) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        # 3 MB .img upload (over the 2MB cap)
        r = client.post(
            "/api/upload",
            data={"set": "DOS 6.22"},
            files=[
                ("files", ("HUGE.img", b"\x00" * (3 * 1024 * 1024), "application/octet-stream"))
            ],
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert results[0]["kind"] == "error"
        assert "max size" in results[0]["detail"]
        # The partial file must not survive
        assert not (tmp_path / "DOS 6.22" / "HUGE.img").exists()


def test_post_upload_multiple_files(app_with_data, tmp_path: Path) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            data={"set": "DOS 6.22"},
            files=[
                ("files", ("DISK01.img", b"\x00" * 1474560, "application/octet-stream")),
                ("files", ("DISK02.img", b"\x00" * 1474560, "application/octet-stream")),
                ("files", ("DISK03.ima", b"\x00" * 1474560, "application/octet-stream")),
            ],
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 3
        assert {res["final_filename"] for res in results} == {
            "DISK01.img",
            "DISK02.img",
            "DISK03.img",
        }
        for name in ("DISK01.img", "DISK02.img", "DISK03.img"):
            assert (tmp_path / "DOS 6.22" / name).exists()


def test_post_upload_partial_success(app_with_data, tmp_path: Path) -> None:
    """One good file, one corrupted .imz — overall 200 with mixed results."""
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            data={"set": "DOS 6.22"},
            files=[
                ("files", ("GOOD.img", b"\x00" * 1474560, "application/octet-stream")),
                ("files", ("BAD.imz", b"not a zip", "application/zip")),
            ],
        )
        assert r.status_code == 200
        results = r.json()["results"]
        kinds = [res["kind"] for res in results]
        assert "passthrough" in kinds
        assert "error" in kinds


def test_post_upload_create_new_set(app_with_data, tmp_path: Path) -> None:
    app, library, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            data={"set": "Brand New", "create_new": "true"},
            files=[("files", ("FIRST.img", b"\x00" * 1474560, "application/octet-stream"))],
        )
        assert r.status_code == 200
        assert r.json()["set"] == "Brand New"
        assert (tmp_path / "Brand New" / "FIRST.img").exists()


def test_post_upload_create_new_required_when_set_missing(app_with_data, tmp_path: Path) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        # Without create_new=true, an unknown set returns 404
        r = client.post(
            "/api/upload",
            data={"set": "Does Not Exist"},
            files=[("files", ("X.img", b"\x00" * 1474560, "application/octet-stream"))],
        )
        assert r.status_code == 404


def test_post_upload_rejects_invalid_set_names(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        # Each of these should be rejected with a 4xx (400 from our validator,
        # or 422 from FastAPI's Form validator for the empty string).
        for bad in ["../escape", "with/slash", "back\\slash", ".hidden", "_trash", ""]:
            r = client.post(
                "/api/upload",
                data={"set": bad, "create_new": "true"},
                files=[("files", ("X.img", b"\x00" * 1474560, "application/octet-stream"))],
            )
            assert 400 <= r.status_code < 500, (
                f"set={bad!r} should be rejected, got {r.status_code}"
            )


def test_post_upload_strips_path_components_from_filename(app_with_data, tmp_path: Path) -> None:
    """A client-supplied filename like 'a/b/EVIL.img' must land as 'EVIL.img'
    inside the chosen set, not escape the set folder."""
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            data={"set": "DOS 6.22"},
            files=[("files", ("../../EVIL.img", b"\x00" * 1474560, "application/octet-stream"))],
        )
        assert r.status_code == 200
        assert (tmp_path / "DOS 6.22" / "EVIL.img").exists()
        assert not (tmp_path / "EVIL.img").exists()


# --- Phase 2 hardware control endpoints ----------------------------------


def test_post_speed_valid_preset(app_with_data) -> None:
    app, _, _, backend = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/speed", json={"preset": "floppy-fast"})
        assert r.status_code == 200
        assert r.json() == {"preset": "floppy-fast"}
        assert "set_speed_preset(floppy-fast)" in backend.ops_log


def test_post_speed_unknown_preset(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/speed", json={"preset": "blast-mode"})
        assert r.status_code == 400


def test_post_volume_valid(app_with_data) -> None:
    app, _, _, backend = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/volume", json={"volume": 45})
        assert r.status_code == 200
        assert r.json() == {"volume": 45}
        assert "set_volume(45)" in backend.ops_log


def test_post_volume_out_of_range(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/volume", json={"volume": 250})
        assert r.status_code == 400
        r = client.post("/api/volume", json={"volume": -5})
        assert r.status_code == 400


def test_post_mute(app_with_data) -> None:
    app, _, _, backend = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/mute", json={"mute": True})
        assert r.status_code == 200
        assert r.json() == {"mute": True}
        assert "set_mute(True)" in backend.ops_log


def test_post_buzzer(app_with_data) -> None:
    app, _, _, backend = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/buzzer", json={"enabled": False})
        assert r.status_code == 200
        assert r.json() == {"enabled": False}
        assert "set_buzzer_enabled(False)" in backend.ops_log


# --- Phase 2.4 hot-reload: audio_buzzer + on_audio_change wire-up ---------

class _FakeAudioBuzzer:
    """Records hot-reload calls without driving real PWM hardware."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_volume(self, volume: int) -> None:
        self.calls.append(("set_volume", volume))

    def set_mute(self, mute: bool) -> None:
        self.calls.append(("set_mute", mute))

    def set_enabled(self, enabled: bool) -> None:
        self.calls.append(("set_enabled", enabled))


@pytest.fixture
def app_with_audio(tmp_path: Path):
    """Like app_with_data but with a fake audio buzzer + change recorder."""
    dos = tmp_path / "DOS 6.22"
    dos.mkdir()
    (dos / "DISK001.img").write_bytes(b"\x00" * 1474560)

    loop = asyncio.new_event_loop()
    library = Library(tmp_path, loop=loop)
    loop.run_until_complete(library.start())
    backend = MockBackend()
    controller = GadgetController(backend, _params())
    loop.run_until_complete(controller.initialize())

    buzzer = _FakeAudioBuzzer()
    changes: list[tuple] = []

    def on_change(field: str, value) -> None:
        changes.append((field, value))

    app = build_app(
        library=library,
        controller=controller,
        floppy_root=tmp_path,
        audio_buzzer=buzzer,
        on_audio_change=on_change,
    )
    yield app, buzzer, changes
    loop.run_until_complete(library.stop())
    loop.close()


def test_post_volume_hot_reloads_audio_buzzer(app_with_audio) -> None:
    app, buzzer, changes = app_with_audio
    with TestClient(app) as client:
        r = client.post("/api/volume", json={"volume": 55})
        assert r.status_code == 200

    assert ("set_volume", 55) in buzzer.calls
    assert ("volume", 55) in changes


def test_post_mute_hot_reloads_audio_buzzer(app_with_audio) -> None:
    app, buzzer, changes = app_with_audio
    with TestClient(app) as client:
        r = client.post("/api/mute", json={"mute": True})
        assert r.status_code == 200

    assert ("set_mute", True) in buzzer.calls
    assert ("mute", True) in changes


def test_post_buzzer_hot_reloads_audio_buzzer(app_with_audio) -> None:
    app, buzzer, changes = app_with_audio
    with TestClient(app) as client:
        r = client.post("/api/buzzer", json={"enabled": False})
        assert r.status_code == 200

    assert ("set_enabled", False) in buzzer.calls
    assert ("buzzer_enabled", False) in changes


def test_get_state_includes_metrics(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.get("/api/state")
        assert r.status_code == 200
        body = r.json()
        assert "metrics" in body
        # MockBackend returns {} so this just confirms the field is present.
        assert isinstance(body["metrics"], dict)
