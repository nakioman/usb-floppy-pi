"""Local dev runner with MockBackend. Usage: python scripts/dev-run.py"""

import asyncio
import json
from pathlib import Path

import uvicorn

from usb_floppy_pi.__main__ import build_runtime
from usb_floppy_pi.gadget.backend import MockBackend


def setup_workspace() -> tuple[Path, Path]:
    workspace = Path("./.dev-state").resolve()
    workspace.mkdir(exist_ok=True)
    floppies = workspace / "floppies"
    floppies.mkdir(exist_ok=True)
    cfg = workspace / "config.json"
    if not cfg.exists():
        cfg.write_text(json.dumps({}, indent=2))
    return floppies, cfg


async def main(floppies: Path, cfg: Path) -> None:
    runtime = await build_runtime(
        config_path=cfg,
        floppy_root=floppies,
        gadget_backend=MockBackend(),
    )
    print(f"Floppy root: {floppies}")
    print("Drop a folder with .img/.ima/.imz files into it.")
    print("Open http://localhost:8080")
    config = uvicorn.Config(runtime.app, host="127.0.0.1", port=8080, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await runtime.shutdown()


if __name__ == "__main__":
    _floppies, _cfg = setup_workspace()
    asyncio.run(main(_floppies, _cfg))
