"""FastAPI application factory."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..gadget.controller import DiskTooLargeError, GadgetController
from ..storage.library import Library
from ..storage.models import FloppySet
from ..storage.normalizer import normalize_arrived_file

logger = logging.getLogger(__name__)


class MountRequest(BaseModel):
    set: str
    disk: str
    session: bool = False


class ReadOnlyRequest(BaseModel):
    ro: bool


def build_app(
    *,
    library: Library,
    controller: GadgetController,
    floppy_root: Path,
) -> FastAPI:
    """Build the FastAPI app, with all dependencies injected."""
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
        return {
            "mounted": (
                asdict(m)
                | {
                    "backing_path": str(m.backing_path),
                }
            )
            if m
            else None
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

    @app.post("/api/sets/{set_name}/readonly")
    def post_readonly(set_name: str, req: ReadOnlyRequest) -> dict:
        s = _set_by_name(set_name)
        marker = s.path / "ro"
        if req.ro:
            marker.write_text("")
        else:
            if marker.exists():
                marker.unlink()
        return {"set": set_name, "read_only": req.ro}

    @app.post("/api/upload")
    async def post_upload(
        set: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
    ) -> dict:
        s = _set_by_name(set)
        if file.filename is None:
            raise HTTPException(status_code=400, detail="missing filename")
        target = s.path / file.filename
        target.write_bytes(await file.read())
        result = normalize_arrived_file(target)
        if result.kind == "error":
            raise HTTPException(status_code=400, detail=result.detail)
        return {"final_filename": result.final_path.name, "kind": result.kind}

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/")
        def root() -> FileResponse:
            return FileResponse(static_dir / "index.html")

    return app
