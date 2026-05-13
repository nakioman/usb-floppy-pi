"""FastAPI application factory."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..gadget.controller import DiskTooLargeError, GadgetController
from ..storage.library import Library
from ..storage.models import FloppySet
from ..storage.normalizer import normalize_arrived_file

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB — generous for .imz of a 1.44MB floppy
MAX_SET_NAME_LEN = 64
SAFE_SET_NAME_RE = re.compile(r"^[A-Za-z0-9 ._\-()]+$")


class MountRequest(BaseModel):
    set: str
    disk: str
    session: bool = False


class ReadOnlyRequest(BaseModel):
    ro: bool


class SpeedRequest(BaseModel):
    preset: str


class VolumeRequest(BaseModel):
    volume: int


class MuteRequest(BaseModel):
    mute: bool


class BuzzerRequest(BaseModel):
    enabled: bool


VALID_SPEED_PRESETS = {"floppy-real", "floppy-fast", "unthrottled"}


def build_app(
    *,
    library: Library,
    controller: GadgetController,
    floppy_root: Path,
    audio_buzzer: object | None = None,
    on_audio_change: object | None = None,
) -> FastAPI:
    """Build the FastAPI app, with all dependencies injected.

    Phase 2.4 hot-reload: ``audio_buzzer`` is an optional
    ``SysfsPWMBuzzer``-shaped object (any duck with set_volume/set_mute/
    set_enabled) the volume/mute/buzzer endpoints will drive in addition
    to ``controller.backend``. ``on_audio_change(field, value)`` is a
    callback fired on each successful change so the caller can persist
    to config — both kwargs are optional and ignored if None.
    """
    app = FastAPI(title="USB Floppy Pi")

    static_dir = Path(__file__).parent / "static"

    def _set_by_name(name: str) -> FloppySet:
        for s in library.sets:
            if s.name == name:
                return s
        raise HTTPException(status_code=404, detail=f"set not found: {name}")

    def _disk_in_set(s: FloppySet, filename: str) -> Path:
        for d in s.disks:
            if d.name == filename:
                return d
        raise HTTPException(status_code=404, detail=f"disk not found: {filename}")

    def _audio_metric(name: str):
        if audio_buzzer is None:
            return None
        return getattr(audio_buzzer, name, None)

    def _metrics_with_audio_state(metrics: dict) -> dict:
        if audio_buzzer is None:
            return metrics
        return metrics | {
            "audio_buzzer": True,
            "volume": _audio_metric("volume"),
            "mute": _audio_metric("mute"),
            "buzzer": _audio_metric("enabled"),
        }

    def _hot_reload_audio(method: str, value) -> None:
        if audio_buzzer is None:
            return
        try:
            getattr(audio_buzzer, method)(value)
        except OSError as exc:
            logger.warning("audio hot-reload %s=%r failed: %s", method, value, exc)

    def _persist_audio_change(field: str, value) -> None:
        if on_audio_change is None:
            return
        try:
            on_audio_change(field, value)
        except OSError as exc:
            logger.warning("could not persist audio change %s=%r: %s", field, value, exc)

    @app.get("/api/sets")
    def get_sets() -> dict:
        return {
            "sets": [
                {
                    "name": s.name,
                    "read_only": s.read_only,
                    "disks": [d.name for d in s.disks],
                }
                for s in library.sets
            ]
        }

    @app.get("/api/state")
    def get_state() -> dict:
        m = controller.current
        try:
            metrics = controller.backend.get_metrics()
        except Exception as exc:
            logger.debug("get_metrics failed (%s); returning empty", exc)
            metrics = {}
        metrics = _metrics_with_audio_state(metrics)
        return {
            "mounted": (
                asdict(m)
                | {
                    "backing_path": str(m.backing_path),
                }
            )
            if m
            else None,
            "metrics": metrics,
        }

    @app.post("/api/mount")
    async def post_mount(req: MountRequest) -> dict:
        s = _set_by_name(req.set)
        d = _disk_in_set(s, req.disk)
        try:
            mounted = await controller.mount(s, d, session=req.session)
        except DiskTooLargeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"mounted": asdict(mounted) | {"backing_path": str(mounted.backing_path)}}

    @app.post("/api/eject")
    async def post_eject() -> dict:
        await controller.eject()
        return {"mounted": None}

    # --- Phase 2 hardware controls -----------------------------------------

    @app.post("/api/speed")
    def post_speed(req: SpeedRequest) -> dict:
        if req.preset not in VALID_SPEED_PRESETS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown preset {req.preset!r}; "
                f"valid: {sorted(VALID_SPEED_PRESETS)}",
            )
        try:
            controller.backend.set_speed_preset(req.preset)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"preset": req.preset}

    @app.post("/api/volume")
    def post_volume(req: VolumeRequest) -> dict:
        if not 0 <= req.volume <= 100:
            raise HTTPException(
                status_code=400, detail="volume must be 0..100"
            )
        try:
            controller.backend.set_volume(req.volume)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        _hot_reload_audio("set_volume", req.volume)
        _persist_audio_change("volume", req.volume)
        return {"volume": req.volume}

    @app.post("/api/mute")
    def post_mute(req: MuteRequest) -> dict:
        try:
            controller.backend.set_mute(req.mute)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        _hot_reload_audio("set_mute", req.mute)
        _persist_audio_change("mute", req.mute)
        return {"mute": req.mute}

    @app.post("/api/buzzer")
    def post_buzzer(req: BuzzerRequest) -> dict:
        try:
            controller.backend.set_buzzer_enabled(req.enabled)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        _hot_reload_audio("set_enabled", req.enabled)
        _persist_audio_change("buzzer_enabled", req.enabled)
        return {"enabled": req.enabled}

    @app.post("/api/sets/{set_name}/readonly")
    async def post_readonly(set_name: str, req: ReadOnlyRequest) -> dict:
        s = _set_by_name(set_name)
        marker = s.path / "ro"
        if req.ro:
            marker.write_text("")
        elif marker.exists():
            marker.unlink()

        # If a non-session disk from this set is currently mounted, re-mount
        # with the new ro flag so the host sees the change without waiting for
        # the next manual mount. The kernel rejects changing ro while a file
        # is attached, so the controller's mount() does eject + delay + remount.
        current = controller.current
        if current is not None and current.set_name == set_name and not current.is_session:
            updated_set = FloppySet(
                name=s.name,
                path=s.path,
                disks=s.disks,
                read_only=req.ro,
            )
            disk_path = next((d for d in s.disks if d.name == current.disk_filename), None)
            if disk_path is not None:
                try:
                    await controller.mount(updated_set, disk_path)
                except Exception as exc:
                    logger.exception("could not re-mount with new ro flag")
                    raise HTTPException(status_code=500, detail=f"remount failed: {exc}") from exc

        return {"set": set_name, "read_only": req.ro}

    def _resolve_target_set(set_name: str, allow_create: bool) -> Path:
        """Look up an existing set by name, or create a new folder if allowed.

        Validates the set name to prevent path traversal and shell-unfriendly
        characters. Returns the absolute path of the set folder.
        """
        name = set_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="set name is empty")
        if len(name) > MAX_SET_NAME_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"set name too long (max {MAX_SET_NAME_LEN} chars)",
            )
        if not SAFE_SET_NAME_RE.fullmatch(name):
            raise HTTPException(
                status_code=400,
                detail="set name may contain letters, digits, spaces, dots, "
                "underscores, hyphens, and parentheses only",
            )
        if name.startswith(".") or name in {"_trash"}:
            raise HTTPException(status_code=400, detail=f"reserved name: {name}")

        # If a set with this name already exists in the library, use that folder.
        for s in library.sets:
            if s.name == name:
                return s.path

        target = floppy_root / name
        if target.exists() and target.is_dir():
            return target
        if not allow_create:
            raise HTTPException(status_code=404, detail=f"set not found: {name}")

        # Create a brand-new set folder.
        target.mkdir(parents=True, exist_ok=False)
        logger.info("created new set folder %s", target)
        return target

    async def _stream_upload_to_set(set_path: Path, file: UploadFile) -> dict:
        """Stream one upload into `set_path`, then normalize. Returns per-file
        result dict; never raises — always returns a status row for the UI."""
        if file.filename is None:
            return {"filename": None, "kind": "error", "detail": "missing filename"}
        # Strip any path components the client tried to send.
        bare = Path(file.filename).name
        if not bare:
            return {"filename": file.filename, "kind": "error", "detail": "invalid filename"}
        target = set_path / bare
        total = 0
        try:
            async with await anyio.open_file(target, "wb") as out:
                while True:
                    chunk = await file.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        await out.aclose()
                        target.unlink(missing_ok=True)
                        return {
                            "filename": bare,
                            "kind": "error",
                            "detail": f"exceeds max size ({MAX_UPLOAD_BYTES} bytes)",
                        }
                    await out.write(chunk)
        except OSError as exc:
            logger.exception("write failed for %s", target)
            target.unlink(missing_ok=True)
            return {"filename": bare, "kind": "error", "detail": f"write failed: {exc}"}

        result = normalize_arrived_file(target)
        return {
            "filename": bare,
            "final_filename": result.final_path.name,
            "kind": result.kind,
            "detail": result.detail or None,
        }

    @app.post("/api/upload")
    async def post_upload(
        set: Annotated[str, Form()],
        files: Annotated[list[UploadFile], File()],
        create_new: Annotated[bool, Form()] = False,
    ) -> dict:
        """Upload one or more images to an existing or new set.

        Form fields:
          - `set`: the destination set name. May be an existing set or, if
            `create_new=true`, a brand-new folder name to create.
          - `files`: one or more image files (.img/.ima/.imz). Each is
            normalized after upload.
          - `create_new`: when true, create the folder if it does not exist.
            When false (default), reject with 404 if the set is unknown.
        """
        if not files:
            raise HTTPException(status_code=400, detail="no files provided")
        target_dir = _resolve_target_set(set, allow_create=create_new)
        results = [await _stream_upload_to_set(target_dir, f) for f in files]
        return {"set": target_dir.name, "results": results}

    if static_dir.exists():
        # Static assets change frequently during development; serve them with
        # no-cache so browsers always pull the latest after a deploy. The
        # asset volume is tiny (one HTML + one JS), so caching wins nothing.
        no_cache_headers = {"Cache-Control": "no-cache, no-store, must-revalidate"}

        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.middleware("http")
        async def _no_cache_static(request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/static") or request.url.path == "/":
                for k, v in no_cache_headers.items():
                    response.headers[k] = v
            return response

        @app.get("/")
        def root() -> FileResponse:
            return FileResponse(static_dir / "index.html", headers=no_cache_headers)

    return app
