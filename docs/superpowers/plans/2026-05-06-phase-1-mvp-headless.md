# USB Floppy Pi — Phase 1 (MVP headless) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working USB floppy drive emulator on a Raspberry Pi Zero 2W that a retro PC sees as a 1.44 MB floppy (mounted as `A:`), with image swapping via web UI and image management via a Samba share.

**Architecture:** Single asyncio Python process that creates a USB Mass Storage gadget via Linux configfs, watches `/home/pi/floppies/` with inotify, normalizes uploaded `.ima`/`.imz` files to `.img`, and serves a FastAPI-based web UI on port 80. Samba runs as a separate system service. State persisted as JSON; the filesystem layout is the source of truth (no DB).

**Tech Stack:** Python 3.11+, asyncio, FastAPI + uvicorn, watchdog (inotify), pytest + pytest-asyncio, Samba, systemd, Linux gadget configfs.

**Spec reference:** `docs/superpowers/specs/2026-05-06-usb-floppy-pi-design.md`

**Scope:** Phase 1 only. LCD display, push buttons, buzzer audio, host I/O activity detection, and LBA-aware seek sound emulation are deferred to Phase 2/3 plans. This phase delivers a fully functional headless floppy emulator controlled via web + Samba.

**Out of scope for Phase 1:**
- Anything in `src/usb_floppy_pi/hardware/` (LCD, buttons, audio)
- `src/usb_floppy_pi/gadget/activity.py` (host I/O detection — only used by audio)
- `src/usb_floppy_pi/ui/menus.py` (LCD state machine)
- `core.event_bus` (only consumed by Phase 2/3 modules — defer to keep Phase 1 lean)

---

## File Structure

```
usb-floppy-pi/
├── src/usb_floppy_pi/
│   ├── __init__.py
│   ├── __main__.py                  # entry point, asyncio orchestration
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                # JSON config load/save (atomic)
│   │   └── state.py                 # AppState dataclass
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── models.py                # FloppySet, MountedImage dataclasses
│   │   ├── scanner.py               # scan /home/pi/floppies/ → list[FloppySet]
│   │   ├── normalizer.py            # .ima → .img, .imz → extract → .img
│   │   ├── watcher.py               # inotify watcher (watchdog wrapper)
│   │   └── library.py               # facade: scanner + watcher + normalizer
│   ├── gadget/
│   │   ├── __init__.py
│   │   ├── backend.py               # GadgetBackend Protocol + MockBackend
│   │   ├── configfs_backend.py      # real ConfigFsBackend (writes to /sys)
│   │   └── controller.py            # mount/eject/swap operations
│   └── web/
│       ├── __init__.py
│       ├── api.py                   # FastAPI app + endpoints
│       └── static/
│           ├── index.html
│           └── app.js
├── tests/
│   ├── __init__.py
│   ├── conftest.py                  # shared fixtures
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_state.py
│   │   ├── test_scanner.py
│   │   ├── test_normalizer.py
│   │   ├── test_watcher.py
│   │   ├── test_library.py
│   │   ├── test_gadget_backend.py
│   │   ├── test_gadget_controller.py
│   │   └── test_web_api.py
│   └── integration/
│       └── test_main_smoke.py
├── deploy/
│   ├── systemd/usb-floppy-pi.service
│   ├── samba/smb.conf.j2
│   ├── boot/
│   │   ├── config.txt.append
│   │   └── cmdline.txt.append
│   └── install.sh
├── docs/superpowers/
│   ├── specs/2026-05-06-usb-floppy-pi-design.md   (already exists)
│   └── plans/2026-05-06-phase-1-mvp-headless.md   (this file)
├── pyproject.toml
├── README.md
└── .gitignore
```

**File responsibilities:**

| File | Responsibility |
|------|---------------|
| `core/config.py` | Load and atomically save `/etc/usb-floppy-pi/config.json`. Defaults when file missing. |
| `core/state.py` | `AppState` dataclass holding sets list, currently mounted image, mute flag. |
| `storage/models.py` | `FloppySet`, `MountedImage` dataclasses. |
| `storage/scanner.py` | One function `scan(root: Path) -> list[FloppySet]`. Pure — no side effects. |
| `storage/normalizer.py` | Functions to rename `.ima → .img` and extract `.imz`. Pure (filesystem) — no app state. |
| `storage/watcher.py` | Async inotify watcher emitting events when `/home/pi/floppies/` changes. |
| `storage/library.py` | High-level facade: holds the current `list[FloppySet]`, runs watcher, applies normalization, exposes "current sets" property. |
| `gadget/backend.py` | `GadgetBackend` Protocol + `MockBackend` (records ops to memory) for tests. |
| `gadget/configfs_backend.py` | `ConfigFsBackend` (writes to `/sys/kernel/config/...`). Untested in unit tests; verified in manual integration. |
| `gadget/controller.py` | High-level `mount(set, disk)`, `eject()`, `swap_disk(disk)`. Padding logic for sub-1.44MB images. |
| `web/api.py` | FastAPI app: `/api/sets`, `/api/state`, `/api/mount`, `/api/eject`, `/api/upload`, `/api/sets/{name}/readonly`, plus serving `static/`. |
| `web/static/index.html` + `app.js` | Vanilla JS UI: list sets, mount/eject buttons, upload form. No build step. |
| `__main__.py` | Wire everything together: load config, init storage library, init gadget controller, mount last image, start uvicorn. |

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `src/usb_floppy_pi/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Initialize git repo**

```bash
cd D:/Projects/Personal/usb-floppy-pi
git init
git branch -M main
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
*.egg-info/
.pytest_cache/
.ruff_cache/
.venv/
venv/
.env
.coverage
.idea/
.vscode/
*.swp
.DS_Store
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "usb-floppy-pi"
version = "0.1.0"
description = "USB floppy emulator for Raspberry Pi Zero 2W"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "python-multipart>=0.0.9",
    "watchdog>=4.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    "ruff>=0.4",
]

[project.scripts]
usb-floppy-pi = "usb_floppy_pi.__main__:main"

[tool.hatch.build.targets.wheel]
packages = ["src/usb_floppy_pi"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "ASYNC"]
ignore = ["E501"]
```

- [ ] **Step 4: Write minimal `README.md`**

```markdown
# usb-floppy-pi

USB floppy drive emulator for Raspberry Pi Zero 2W. Presents as a 1.44 MB USB floppy to a host PC (DOS / Win9x / modern OS) via the legacy emulation of the BIOS.

See `docs/superpowers/specs/` for design and `docs/superpowers/plans/` for implementation phases.

## Status

Phase 1 (MVP headless) — in progress.
```

- [ ] **Step 5: Create empty package and test files**

```bash
mkdir -p src/usb_floppy_pi tests/unit tests/integration
touch src/usb_floppy_pi/__init__.py
touch tests/__init__.py
touch tests/unit/__init__.py
touch tests/integration/__init__.py
```

Write `tests/conftest.py`:

```python
"""Shared pytest fixtures."""
```

- [ ] **Step 6: Set up venv and install**

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows-bash
pip install -e ".[dev]"
```

Note: the developer machine is Windows. When deploying to the Pi, the install.sh script (Task 22) handles the Linux venv. Tests run cross-platform except for the configfs backend (Task 13) and inotify (Task 8 — watchdog abstracts the OS difference).

- [ ] **Step 7: Verify pytest runs (no tests yet)**

Run: `pytest`
Expected: `no tests ran in X.XXs` exit code 5 (acceptable for empty test dir). If exit code is non-zero in CI, adjust later.

- [ ] **Step 8: Commit**

```bash
git add .gitignore pyproject.toml README.md src tests docs
git commit -m "chore: project scaffolding for usb-floppy-pi phase 1"
```

---

## Task 2: core.config — JSON config with atomic writes

**Files:**
- Create: `src/usb_floppy_pi/core/__init__.py`
- Create: `src/usb_floppy_pi/core/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_config.py`:

```python
"""Tests for core.config."""
import json
from pathlib import Path

import pytest

from usb_floppy_pi.core.config import Config, DEFAULT_CONFIG, load_config, save_config


def test_load_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg = load_config(cfg_path)
    assert cfg.mute is False
    assert cfg.buzzer_volume == 0.6
    assert cfg.last_mounted is None
    assert cfg.samba_share_name == "floppies"


def test_load_reads_existing_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "mute": True,
        "buzzer_volume": 0.3,
        "last_mounted": {"set": "DOS 6.22", "disk": "DISK001.img"},
        "samba_share_name": "myshare",
        "log_level": "DEBUG",
    }))
    cfg = load_config(cfg_path)
    assert cfg.mute is True
    assert cfg.buzzer_volume == 0.3
    assert cfg.last_mounted == {"set": "DOS 6.22", "disk": "DISK001.img"}
    assert cfg.samba_share_name == "myshare"
    assert cfg.log_level == "DEBUG"


def test_load_fills_missing_keys_with_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"mute": True}))
    cfg = load_config(cfg_path)
    assert cfg.mute is True
    assert cfg.buzzer_volume == 0.6  # default kept


def test_save_persists_values(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg = Config(mute=True, buzzer_volume=0.9)
    save_config(cfg_path, cfg)
    re_read = load_config(cfg_path)
    assert re_read.mute is True
    assert re_read.buzzer_volume == 0.9


def test_save_is_atomic(tmp_path: Path) -> None:
    """save_config should never leave a partial file even if interrupted."""
    cfg_path = tmp_path / "config.json"
    cfg = Config(mute=True)
    save_config(cfg_path, cfg)
    # No tmp file should remain after successful save
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


def test_load_returns_defaults_for_corrupted_json(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{ this is not valid json")
    cfg = load_config(cfg_path)
    # Falls back to defaults rather than crashing
    assert cfg.mute is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_config.py -v`
Expected: ImportError / ModuleNotFoundError on `usb_floppy_pi.core.config`

- [ ] **Step 3: Write the implementation**

Write `src/usb_floppy_pi/core/__init__.py`:

```python
"""Core modules: config and state."""
```

Write `src/usb_floppy_pi/core/config.py`:

```python
"""Application configuration: JSON-backed, atomic writes."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Config:
    mute: bool = False
    buzzer_volume: float = 0.6
    last_mounted: dict[str, str] | None = None
    samba_share_name: str = "floppies"
    log_level: str = "INFO"


DEFAULT_CONFIG = Config()


def load_config(path: Path) -> Config:
    """Load config from JSON file. Returns defaults if missing or corrupted."""
    if not path.exists():
        return Config()
    try:
        data: dict[str, Any] = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("config %s unreadable (%s); using defaults", path, exc)
        return Config()
    defaults = asdict(DEFAULT_CONFIG)
    merged = {**defaults, **data}
    # Drop unknown keys to avoid Config() TypeError
    known_keys = {f.name for f in Config.__dataclass_fields__.values()}
    filtered = {k: v for k, v in merged.items() if k in known_keys}
    return Config(**filtered)


def save_config(path: Path, cfg: Config) -> None:
    """Write config atomically: tmp + fsync + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(asdict(cfg), indent=2)
    with open(tmp, "w") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_config.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/usb_floppy_pi/core tests/unit/test_config.py
git commit -m "feat(core): JSON config with atomic writes"
```

---

## Task 3: storage.models — FloppySet and MountedImage dataclasses

**Files:**
- Create: `src/usb_floppy_pi/storage/__init__.py`
- Create: `src/usb_floppy_pi/storage/models.py`
- Test: `tests/unit/test_state.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_state.py`:

```python
"""Tests for storage.models — pure dataclasses, no behavior."""
from pathlib import Path

from usb_floppy_pi.storage.models import FloppySet, MountedImage


def test_floppy_set_construction() -> None:
    s = FloppySet(
        name="DOS 6.22",
        path=Path("/home/pi/floppies/DOS 6.22"),
        disks=[Path("/home/pi/floppies/DOS 6.22/DISK001.img")],
        read_only=False,
    )
    assert s.name == "DOS 6.22"
    assert len(s.disks) == 1
    assert s.read_only is False


def test_floppy_set_multi_disk_sorted() -> None:
    """disks list is whatever scanner provides; models don't reorder."""
    base = Path("/home/pi/floppies/DOS 6.22")
    s = FloppySet(
        name="DOS 6.22",
        path=base,
        disks=[base / "DISK002.img", base / "DISK001.img"],
        read_only=False,
    )
    # Models preserve order
    assert s.disks[0].name == "DISK002.img"


def test_mounted_image_construction() -> None:
    m = MountedImage(
        set_name="DOS 6.22",
        disk_filename="DISK001.img",
        backing_path=Path("/home/pi/floppies/DOS 6.22/DISK001.img"),
        read_only=True,
        is_session=False,
    )
    assert m.read_only is True
    assert m.is_session is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_state.py -v`
Expected: ModuleNotFoundError on `usb_floppy_pi.storage.models`

- [ ] **Step 3: Write the implementation**

Write `src/usb_floppy_pi/storage/__init__.py`:

```python
"""Storage subsystem: filesystem-backed floppy library."""
```

Write `src/usb_floppy_pi/storage/models.py`:

```python
"""Data models for the storage subsystem (pure dataclasses, no behavior)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FloppySet:
    """A folder under /home/pi/floppies/ representing a set of disks."""
    name: str
    path: Path
    disks: tuple[Path, ...] | list[Path]
    read_only: bool


@dataclass
class MountedImage:
    """The currently mounted disk on the USB gadget."""
    set_name: str
    disk_filename: str
    backing_path: Path
    read_only: bool
    is_session: bool
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_state.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/usb_floppy_pi/storage tests/unit/test_state.py
git commit -m "feat(storage): FloppySet and MountedImage models"
```

---

## Task 4: storage.scanner — scan filesystem to FloppySet list

**Files:**
- Create: `src/usb_floppy_pi/storage/scanner.py`
- Test: `tests/unit/test_scanner.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_scanner.py`:

```python
"""Tests for storage.scanner."""
from pathlib import Path

import pytest

from usb_floppy_pi.storage.scanner import scan


def test_scan_empty_root(tmp_path: Path) -> None:
    assert scan(tmp_path) == []


def test_scan_missing_root_returns_empty(tmp_path: Path) -> None:
    assert scan(tmp_path / "does-not-exist") == []


def test_scan_single_set_one_disk(tmp_path: Path) -> None:
    set_dir = tmp_path / "Win98 Boot"
    set_dir.mkdir()
    (set_dir / "boot.img").write_bytes(b"")
    sets = scan(tmp_path)
    assert len(sets) == 1
    assert sets[0].name == "Win98 Boot"
    assert sets[0].read_only is False
    assert [d.name for d in sets[0].disks] == ["boot.img"]


def test_scan_multi_disk_sorted_alphabetically(tmp_path: Path) -> None:
    set_dir = tmp_path / "DOS 6.22"
    set_dir.mkdir()
    (set_dir / "DISK002.img").write_bytes(b"")
    (set_dir / "DISK001.img").write_bytes(b"")
    (set_dir / "DISK003.img").write_bytes(b"")
    sets = scan(tmp_path)
    assert [d.name for d in sets[0].disks] == [
        "DISK001.img", "DISK002.img", "DISK003.img"
    ]


def test_scan_ro_marker(tmp_path: Path) -> None:
    set_dir = tmp_path / "Quake"
    set_dir.mkdir()
    (set_dir / "ro").write_text("")
    (set_dir / "DISK1.img").write_bytes(b"")
    sets = scan(tmp_path)
    assert sets[0].read_only is True


def test_scan_set_with_no_imgs_is_skipped(tmp_path: Path) -> None:
    set_dir = tmp_path / "Empty"
    set_dir.mkdir()
    (set_dir / "readme.txt").write_text("hi")
    sets = scan(tmp_path)
    assert sets == []


def test_scan_files_at_root_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "loose.img").write_bytes(b"")
    set_dir = tmp_path / "DOS 6.22"
    set_dir.mkdir()
    (set_dir / "DISK001.img").write_bytes(b"")
    sets = scan(tmp_path)
    assert len(sets) == 1
    assert sets[0].name == "DOS 6.22"


def test_scan_nested_dirs_are_ignored(tmp_path: Path) -> None:
    """Structure is flat: only direct children of root are sets."""
    deep = tmp_path / "Games" / "Quake"
    deep.mkdir(parents=True)
    (deep / "DISK1.img").write_bytes(b"")
    (tmp_path / "Games" / "DISK1.img").write_bytes(b"")
    sets = scan(tmp_path)
    # "Games" is a set (has DISK1.img directly), "Quake" subdir is ignored
    assert [s.name for s in sets] == ["Games"]
    assert [d.name for d in sets[0].disks] == ["DISK1.img"]


def test_scan_sets_sorted_alphabetically(tmp_path: Path) -> None:
    for name in ["Zork", "Apple", "Mac"]:
        d = tmp_path / name
        d.mkdir()
        (d / "x.img").write_bytes(b"")
    sets = scan(tmp_path)
    assert [s.name for s in sets] == ["Apple", "Mac", "Zork"]


def test_scan_only_includes_dot_img_files(tmp_path: Path) -> None:
    """Scanner only sees .img — .ima / .imz are normalized BEFORE scanning."""
    set_dir = tmp_path / "Mixed"
    set_dir.mkdir()
    (set_dir / "DISK1.img").write_bytes(b"")
    (set_dir / "DISK2.ima").write_bytes(b"")
    (set_dir / "DISK3.imz").write_bytes(b"")
    sets = scan(tmp_path)
    assert [d.name for d in sets[0].disks] == ["DISK1.img"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_scanner.py -v`
Expected: ModuleNotFoundError on `usb_floppy_pi.storage.scanner`

- [ ] **Step 3: Write the implementation**

Write `src/usb_floppy_pi/storage/scanner.py`:

```python
"""Filesystem scanner: builds FloppySet list from /home/pi/floppies/."""
from __future__ import annotations

from pathlib import Path

from .models import FloppySet


def scan(root: Path) -> list[FloppySet]:
    """Scan a flat directory of floppy sets.

    Each direct subdirectory is one FloppySet. Sets are returned alphabetically.
    Subdirectories without any .img file are skipped.
    """
    if not root.is_dir():
        return []
    sets: list[FloppySet] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        disks = sorted(
            [p for p in child.iterdir() if p.is_file() and p.suffix.lower() == ".img"],
            key=lambda p: p.name,
        )
        if not disks:
            continue
        read_only = (child / "ro").exists()
        sets.append(FloppySet(
            name=child.name,
            path=child,
            disks=disks,
            read_only=read_only,
        ))
    return sets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_scanner.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/usb_floppy_pi/storage/scanner.py tests/unit/test_scanner.py
git commit -m "feat(storage): filesystem scanner for floppy sets"
```

---

## Task 5: storage.normalizer — handle .ima/.imz on arrival

**Files:**
- Create: `src/usb_floppy_pi/storage/normalizer.py`
- Test: `tests/unit/test_normalizer.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_normalizer.py`:

```python
"""Tests for storage.normalizer."""
import zipfile
from pathlib import Path

import pytest

from usb_floppy_pi.storage.normalizer import (
    NormalizationResult,
    is_image_file,
    normalize_arrived_file,
)


def test_is_image_file_recognizes_extensions(tmp_path: Path) -> None:
    for ext in ["img", "IMG", "ima", "IMA", "imz", "IMZ"]:
        f = tmp_path / f"x.{ext}"
        f.write_bytes(b"x")
        assert is_image_file(f) is True


def test_is_image_file_rejects_other_extensions(tmp_path: Path) -> None:
    for ext in ["txt", "iso", "tar", ""]:
        f = tmp_path / f"x.{ext}" if ext else tmp_path / "x"
        f.write_bytes(b"")
        assert is_image_file(f) is False


def test_normalize_img_is_passthrough(tmp_path: Path) -> None:
    f = tmp_path / "DISK1.img"
    f.write_bytes(b"hello")
    result = normalize_arrived_file(f)
    assert result.kind == "passthrough"
    assert result.final_path == f
    assert f.exists()


def test_normalize_ima_is_renamed_to_img(tmp_path: Path) -> None:
    f = tmp_path / "DISK1.ima"
    f.write_bytes(b"hello")
    result = normalize_arrived_file(f)
    assert result.kind == "renamed"
    assert result.final_path == tmp_path / "DISK1.img"
    assert result.final_path.exists()
    assert result.final_path.read_bytes() == b"hello"
    assert not f.exists()


def test_normalize_uppercase_ima_renamed(tmp_path: Path) -> None:
    f = tmp_path / "DISK1.IMA"
    f.write_bytes(b"x")
    result = normalize_arrived_file(f)
    assert result.final_path == tmp_path / "DISK1.img"
    assert result.final_path.exists()


def test_normalize_imz_extracts_inner_image(tmp_path: Path) -> None:
    inner = tmp_path / "tmp_inner.ima"
    inner.write_bytes(b"floppy-content")
    archive = tmp_path / "DISK1.imz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(inner, arcname="DISK1.ima")
    inner.unlink()
    result = normalize_arrived_file(archive)
    assert result.kind == "extracted"
    assert result.final_path == tmp_path / "DISK1.img"
    assert result.final_path.read_bytes() == b"floppy-content"
    assert not archive.exists()


def test_normalize_imz_with_name_conflict_appends_suffix(tmp_path: Path) -> None:
    existing = tmp_path / "DISK1.img"
    existing.write_bytes(b"original")
    archive = tmp_path / "DISK1.imz"
    inner = tmp_path / "_inner.ima"
    inner.write_bytes(b"new-content")
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(inner, arcname="DISK1.ima")
    inner.unlink()
    result = normalize_arrived_file(archive)
    assert result.final_path == tmp_path / "DISK1 (1).img"
    assert result.final_path.read_bytes() == b"new-content"
    assert existing.read_bytes() == b"original"
    assert not archive.exists()


def test_normalize_imz_picks_first_valid_image(tmp_path: Path) -> None:
    archive = tmp_path / "MULTI.imz"
    big = tmp_path / "_big.ima"
    big.write_bytes(b"x" * 100)
    readme = tmp_path / "_readme.txt"
    readme.write_text("manual")
    valid = tmp_path / "_valid.ima"
    valid.write_bytes(b"valid-content")
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(readme, arcname="readme.txt")
        zf.write(valid, arcname="DISK01.ima")
        zf.write(big, arcname="other.ima")
    big.unlink(); readme.unlink(); valid.unlink()
    result = normalize_arrived_file(archive)
    assert result.kind == "extracted"
    assert result.final_path.read_bytes() == b"valid-content"


def test_normalize_corrupted_imz_leaves_alone(tmp_path: Path) -> None:
    archive = tmp_path / "CORRUPT.imz"
    archive.write_bytes(b"not a zip")
    result = normalize_arrived_file(archive)
    assert result.kind == "error"
    assert archive.exists()


def test_normalize_imz_with_no_image_files_inside(tmp_path: Path) -> None:
    archive = tmp_path / "NOIMG.imz"
    readme = tmp_path / "_readme.txt"
    readme.write_text("only docs")
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(readme, arcname="readme.txt")
    readme.unlink()
    result = normalize_arrived_file(archive)
    assert result.kind == "error"
    assert archive.exists()


def test_normalize_imz_oversize_inner_rejected(tmp_path: Path) -> None:
    """Images larger than 1.44MB inside an .imz are rejected."""
    archive = tmp_path / "BIG.imz"
    inner = tmp_path / "_big.ima"
    inner.write_bytes(b"x" * (1474560 + 1))
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(inner, arcname="big.ima")
    inner.unlink()
    result = normalize_arrived_file(archive)
    assert result.kind == "error"
    assert archive.exists()


def test_normalize_non_image_file_is_ignored(tmp_path: Path) -> None:
    f = tmp_path / "readme.txt"
    f.write_text("hi")
    result = normalize_arrived_file(f)
    assert result.kind == "ignored"
    assert f.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_normalizer.py -v`
Expected: ModuleNotFoundError on `usb_floppy_pi.storage.normalizer`

- [ ] **Step 3: Write the implementation**

Write `src/usb_floppy_pi/storage/normalizer.py`:

```python
"""Normalize arriving image files to canonical .img.

- .img → passthrough
- .ima → rename to .img
- .imz → extract inner image, write as .img, delete .imz
- anything else → ignored
"""
from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 1474560  # 1.44 MB
IMAGE_EXTS = {".img", ".ima", ".imz"}
EXTRACTABLE_INNER_EXTS = {".img", ".ima"}

ResultKind = Literal["passthrough", "renamed", "extracted", "error", "ignored"]


@dataclass
class NormalizationResult:
    kind: ResultKind
    final_path: Path
    detail: str = ""


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def _next_available(target: Path) -> Path:
    """Return target if free, else target with " (1)", " (2)", ... suffix."""
    if not target.exists():
        return target
    stem = target.stem
    parent = target.parent
    n = 1
    while True:
        candidate = parent / f"{stem} ({n}).img"
        if not candidate.exists():
            return candidate
        n += 1


def normalize_arrived_file(path: Path) -> NormalizationResult:
    """Apply normalization based on extension. Idempotent for .img."""
    if not path.is_file():
        return NormalizationResult("ignored", path, "not a file")
    suffix = path.suffix.lower()
    if suffix == ".img":
        return NormalizationResult("passthrough", path)
    if suffix == ".ima":
        target = path.with_suffix(".img")
        target = _next_available(target)
        path.rename(target)
        logger.info("renamed %s → %s", path, target)
        return NormalizationResult("renamed", target)
    if suffix == ".imz":
        return _extract_imz(path)
    return NormalizationResult("ignored", path, "unrecognized extension")


def _extract_imz(archive: Path) -> NormalizationResult:
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            members = zf.infolist()
            inner_member = None
            for m in members:
                inner_name = Path(m.filename).name
                if not inner_name:
                    continue
                inner_suffix = Path(inner_name).suffix.lower()
                if inner_suffix not in EXTRACTABLE_INNER_EXTS:
                    continue
                if m.file_size > MAX_IMAGE_BYTES:
                    continue
                inner_member = m
                break
            if inner_member is None:
                return NormalizationResult(
                    "error", archive,
                    "no valid image inside .imz (need .img/.ima ≤1.44MB)",
                )
            target = archive.with_suffix(".img")
            target = _next_available(target)
            with zf.open(inner_member) as src, open(target, "wb") as dst:
                dst.write(src.read())
    except (zipfile.BadZipFile, OSError) as exc:
        return NormalizationResult("error", archive, f"corrupted .imz: {exc}")
    archive.unlink()
    logger.info("extracted %s → %s", archive, target)
    return NormalizationResult("extracted", target)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_normalizer.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/usb_floppy_pi/storage/normalizer.py tests/unit/test_normalizer.py
git commit -m "feat(storage): .ima/.imz normalization to .img"
```

---

## Task 6: storage.watcher — async inotify watcher

**Files:**
- Create: `src/usb_floppy_pi/storage/watcher.py`
- Test: `tests/unit/test_watcher.py`

The watcher uses `watchdog` for cross-platform file events. We wrap its threading observer behind an async-friendly callback that runs on the asyncio loop.

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_watcher.py`:

```python
"""Tests for storage.watcher."""
import asyncio
from pathlib import Path

import pytest

from usb_floppy_pi.storage.watcher import LibraryWatcher


@pytest.mark.asyncio
async def test_watcher_fires_on_file_created(tmp_path: Path) -> None:
    events: list[Path] = []
    loop = asyncio.get_running_loop()

    def callback(p: Path) -> None:
        events.append(p)

    watcher = LibraryWatcher(tmp_path, callback, loop=loop)
    watcher.start()
    try:
        await asyncio.sleep(0.1)  # let observer settle
        set_dir = tmp_path / "S1"
        set_dir.mkdir()
        (set_dir / "DISK1.img").write_bytes(b"")
        # poll up to 2s for the event
        for _ in range(40):
            await asyncio.sleep(0.05)
            if events:
                break
        assert len(events) >= 1
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_fires_on_file_deleted(tmp_path: Path) -> None:
    set_dir = tmp_path / "S1"
    set_dir.mkdir()
    target = set_dir / "DISK1.img"
    target.write_bytes(b"")

    events: list[Path] = []
    loop = asyncio.get_running_loop()

    watcher = LibraryWatcher(tmp_path, lambda p: events.append(p), loop=loop)
    watcher.start()
    try:
        await asyncio.sleep(0.1)
        target.unlink()
        for _ in range(40):
            await asyncio.sleep(0.05)
            if events:
                break
        assert len(events) >= 1
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_stop_is_idempotent(tmp_path: Path) -> None:
    watcher = LibraryWatcher(tmp_path, lambda p: None, loop=asyncio.get_running_loop())
    watcher.start()
    watcher.stop()
    watcher.stop()  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_watcher.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: Write the implementation**

Write `src/usb_floppy_pi/storage/watcher.py`:

```python
"""Async wrapper around watchdog for filesystem change events."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

ChangeCallback = Callable[[Path], None]


class _Handler(FileSystemEventHandler):
    def __init__(self, callback: ChangeCallback, loop: asyncio.AbstractEventLoop) -> None:
        self._callback = callback
        self._loop = loop

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory and event.event_type not in {"created", "deleted", "moved"}:
            return
        path = Path(event.src_path)
        # Marshal the callback onto the asyncio loop so consumers can mutate state safely.
        self._loop.call_soon_threadsafe(self._callback, path)


class LibraryWatcher:
    """Watch a directory tree (recursively) and call back on changes."""

    def __init__(
        self,
        root: Path,
        callback: ChangeCallback,
        *,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._root = root
        self._callback = callback
        self._loop = loop
        self._observer: Observer | None = None

    def start(self) -> None:
        if self._observer is not None:
            return
        self._root.mkdir(parents=True, exist_ok=True)
        observer = Observer()
        handler = _Handler(self._callback, self._loop)
        observer.schedule(handler, str(self._root), recursive=True)
        observer.start()
        self._observer = observer
        logger.info("library watcher started on %s", self._root)

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=2.0)
        self._observer = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_watcher.py -v`
Expected: 3 passed

If any tests are flaky on Windows (watchdog timing), add a longer settle delay or rerun. The observer is real here — we are exercising actual filesystem events.

- [ ] **Step 5: Commit**

```bash
git add src/usb_floppy_pi/storage/watcher.py tests/unit/test_watcher.py
git commit -m "feat(storage): async filesystem watcher (watchdog)"
```

---

## Task 7: storage.library — facade combining scan + watch + normalize

**Files:**
- Create: `src/usb_floppy_pi/storage/library.py`
- Test: `tests/unit/test_library.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_library.py`:

```python
"""Tests for storage.library — high-level facade."""
import asyncio
from pathlib import Path

import pytest

from usb_floppy_pi.storage.library import Library


@pytest.mark.asyncio
async def test_library_initial_scan(tmp_path: Path) -> None:
    set_dir = tmp_path / "DOS"
    set_dir.mkdir()
    (set_dir / "DISK1.img").write_bytes(b"")
    lib = Library(tmp_path, loop=asyncio.get_running_loop())
    await lib.start()
    try:
        sets = lib.sets
        assert len(sets) == 1
        assert sets[0].name == "DOS"
    finally:
        await lib.stop()


@pytest.mark.asyncio
async def test_library_refreshes_on_file_added(tmp_path: Path) -> None:
    lib = Library(tmp_path, loop=asyncio.get_running_loop())
    await lib.start()
    try:
        assert lib.sets == []
        set_dir = tmp_path / "Win98"
        set_dir.mkdir()
        (set_dir / "boot.img").write_bytes(b"")
        # wait for inotify + debounce
        for _ in range(40):
            await asyncio.sleep(0.1)
            if lib.sets:
                break
        assert len(lib.sets) == 1
    finally:
        await lib.stop()


@pytest.mark.asyncio
async def test_library_normalizes_ima_on_arrival(tmp_path: Path) -> None:
    set_dir = tmp_path / "Mixed"
    set_dir.mkdir()
    lib = Library(tmp_path, loop=asyncio.get_running_loop())
    await lib.start()
    try:
        # drop a .ima file
        (set_dir / "DISK1.ima").write_bytes(b"x")
        for _ in range(40):
            await asyncio.sleep(0.1)
            if lib.sets and lib.sets[0].disks:
                break
        assert (set_dir / "DISK1.img").exists()
        assert not (set_dir / "DISK1.ima").exists()
        assert lib.sets[0].disks[0].name == "DISK1.img"
    finally:
        await lib.stop()


@pytest.mark.asyncio
async def test_library_subscribers_notified_on_change(tmp_path: Path) -> None:
    lib = Library(tmp_path, loop=asyncio.get_running_loop())
    notifications: list[None] = []
    lib.on_change(lambda: notifications.append(None))
    await lib.start()
    try:
        set_dir = tmp_path / "DOS"
        set_dir.mkdir()
        (set_dir / "DISK1.img").write_bytes(b"")
        for _ in range(40):
            await asyncio.sleep(0.1)
            if notifications:
                break
        assert len(notifications) >= 1
    finally:
        await lib.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_library.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: Write the implementation**

Write `src/usb_floppy_pi/storage/library.py`:

```python
"""Library facade: scan + watch + normalize. Holds the current FloppySet list."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from .models import FloppySet
from .normalizer import is_image_file, normalize_arrived_file
from .scanner import scan
from .watcher import LibraryWatcher

logger = logging.getLogger(__name__)

ChangeListener = Callable[[], None]


class Library:
    """Maintains an in-memory list of FloppySets backed by `root`.

    Watches the filesystem; normalizes incoming .ima/.imz; rescans on any change.
    Coalesces rapid changes via a 200ms debounce.
    """

    DEBOUNCE_S = 0.2

    def __init__(self, root: Path, *, loop: asyncio.AbstractEventLoop) -> None:
        self._root = root
        self._loop = loop
        self._watcher = LibraryWatcher(root, self._on_fs_event, loop=loop)
        self._sets: list[FloppySet] = []
        self._listeners: list[ChangeListener] = []
        self._rescan_task: asyncio.Task[None] | None = None
        self._stopped = False

    @property
    def sets(self) -> list[FloppySet]:
        return list(self._sets)

    def on_change(self, listener: ChangeListener) -> None:
        self._listeners.append(listener)

    async def start(self) -> None:
        self._stopped = False
        self._root.mkdir(parents=True, exist_ok=True)
        self._sets = scan(self._root)
        self._watcher.start()

    async def stop(self) -> None:
        self._stopped = True
        self._watcher.stop()
        if self._rescan_task is not None:
            self._rescan_task.cancel()
            try:
                await self._rescan_task
            except asyncio.CancelledError:
                pass
            self._rescan_task = None

    def _on_fs_event(self, path: Path) -> None:
        # First, normalize any newly arrived image files.
        if path.is_file() and is_image_file(path):
            try:
                normalize_arrived_file(path)
            except Exception:
                logger.exception("normalize failed for %s", path)
        self._schedule_rescan()

    def _schedule_rescan(self) -> None:
        if self._stopped:
            return
        if self._rescan_task is not None and not self._rescan_task.done():
            return  # already scheduled
        self._rescan_task = self._loop.create_task(self._debounced_rescan())

    async def _debounced_rescan(self) -> None:
        await asyncio.sleep(self.DEBOUNCE_S)
        if self._stopped:
            return
        new_sets = scan(self._root)
        if new_sets != self._sets:
            self._sets = new_sets
            for listener in list(self._listeners):
                try:
                    listener()
                except Exception:
                    logger.exception("library change listener raised")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_library.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/usb_floppy_pi/storage/library.py tests/unit/test_library.py
git commit -m "feat(storage): library facade with debounced rescan"
```

---

## Task 8: gadget.backend — Protocol and MockBackend

**Files:**
- Create: `src/usb_floppy_pi/gadget/__init__.py`
- Create: `src/usb_floppy_pi/gadget/backend.py`
- Test: `tests/unit/test_gadget_backend.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_gadget_backend.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_gadget_backend.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: Write the implementation**

Write `src/usb_floppy_pi/gadget/__init__.py`:

```python
"""USB gadget subsystem: configfs interaction + mount controller."""
```

Write `src/usb_floppy_pi/gadget/backend.py`:

```python
"""GadgetBackend protocol + MockBackend for testing."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class GadgetParams:
    id_vendor: int
    id_product: int
    bcd_device: int
    manufacturer: str
    product: str
    serial: str
    inquiry_string: str  # 28 chars: 8 vendor + 16 product + 4 revision


class GadgetBackend(Protocol):
    def create_gadget(self, params: GadgetParams) -> None: ...
    def destroy_gadget(self) -> None: ...
    def configure_lun(self, *, file: Path | None, ro: bool) -> None: ...
    def attach_to_udc(self) -> None: ...
    def detach_from_udc(self) -> None: ...


class MockBackend:
    """Records ops to memory for unit tests."""

    def __init__(self) -> None:
        self.created: bool = False
        self.attached: bool = False
        self.params: GadgetParams | None = None
        self.lun_file: Path | None = None
        self.lun_ro: bool = False
        self.ops_log: list[str] = []

    def create_gadget(self, params: GadgetParams) -> None:
        self.created = True
        self.params = params
        self.ops_log.append(f"create({params.product})")

    def destroy_gadget(self) -> None:
        self.created = False
        self.attached = False
        self.lun_file = None
        self.lun_ro = False
        self.ops_log.append("destroy")

    def configure_lun(self, *, file: Path | None, ro: bool) -> None:
        if not self.created:
            raise RuntimeError("configure_lun called before create_gadget")
        self.lun_file = file
        self.lun_ro = ro
        self.ops_log.append(f"configure_lun(file={file}, ro={ro})")

    def attach_to_udc(self) -> None:
        if not self.created:
            raise RuntimeError("attach_to_udc called before create_gadget")
        self.attached = True
        self.ops_log.append("attach")

    def detach_from_udc(self) -> None:
        self.attached = False
        self.ops_log.append("detach")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_gadget_backend.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/usb_floppy_pi/gadget/__init__.py src/usb_floppy_pi/gadget/backend.py tests/unit/test_gadget_backend.py
git commit -m "feat(gadget): backend protocol and MockBackend"
```

---

## Task 9: gadget.controller — high-level mount/eject/swap

**Files:**
- Create: `src/usb_floppy_pi/gadget/controller.py`
- Test: `tests/unit/test_gadget_controller.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_gadget_controller.py`:

```python
"""Tests for gadget.controller using MockBackend."""
import asyncio
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_gadget_controller.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: Write the implementation**

Write `src/usb_floppy_pi/gadget/controller.py`:

```python
"""High-level USB gadget operations: mount, eject, swap, session-mount."""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from ..storage.models import FloppySet, MountedImage
from .backend import GadgetBackend, GadgetParams

logger = logging.getLogger(__name__)

FLOPPY_SIZE_BYTES = 1474560  # 1.44 MB
SWAP_DELAY_S = 0.15  # let the host process the eject before remount


class DiskTooLargeError(ValueError):
    """Raised when an .img file exceeds 1.44 MB."""


class GadgetController:
    """Mount/eject/swap operations on top of a GadgetBackend.

    Owns the logic of: padding undersize images, rejecting oversize images,
    applying the read-only flag, and creating session-mode temp copies.
    """

    def __init__(
        self,
        backend: GadgetBackend,
        params: GadgetParams,
        *,
        session_dir: Path | None = None,
    ) -> None:
        self._backend = backend
        self._params = params
        self._session_dir = session_dir or Path("/tmp/usb-floppy-pi-sessions")
        self._current: MountedImage | None = None

    @property
    def current(self) -> MountedImage | None:
        return self._current

    async def initialize(self) -> None:
        self._backend.create_gadget(self._params)
        self._backend.attach_to_udc()

    async def shutdown(self) -> None:
        try:
            self._backend.detach_from_udc()
        finally:
            self._backend.destroy_gadget()
            self._cleanup_session()

    async def mount(
        self,
        floppy_set: FloppySet,
        disk: Path,
        *,
        session: bool = False,
    ) -> MountedImage:
        if disk not in floppy_set.disks:
            raise ValueError(f"{disk} not in set {floppy_set.name}")

        size = disk.stat().st_size
        if size > FLOPPY_SIZE_BYTES:
            raise DiskTooLargeError(
                f"{disk} is {size} bytes; max is {FLOPPY_SIZE_BYTES}"
            )
        if size < FLOPPY_SIZE_BYTES:
            self._pad_to_full(disk)

        backing = disk
        if session:
            self._cleanup_session()
            self._session_dir.mkdir(parents=True, exist_ok=True)
            backing = self._session_dir / "session.img"
            shutil.copyfile(disk, backing)

        # If already mounted, do "eject + delay + remount" so the host sees a media change.
        if self._current is not None:
            self._backend.configure_lun(file=None, ro=False)
            await asyncio.sleep(SWAP_DELAY_S)

        ro = floppy_set.read_only and not session
        self._backend.configure_lun(file=backing, ro=ro)

        self._current = MountedImage(
            set_name=floppy_set.name,
            disk_filename=disk.name,
            backing_path=backing,
            read_only=ro,
            is_session=session,
        )
        logger.info("mounted %s/%s (ro=%s, session=%s)",
                    floppy_set.name, disk.name, ro, session)
        return self._current

    async def eject(self) -> None:
        self._backend.configure_lun(file=None, ro=False)
        self._cleanup_session()
        self._current = None

    def _pad_to_full(self, path: Path) -> None:
        size = path.stat().st_size
        with open(path, "ab") as f:
            f.write(b"\x00" * (FLOPPY_SIZE_BYTES - size))
        logger.info("padded %s from %d to %d bytes", path, size, FLOPPY_SIZE_BYTES)

    def _cleanup_session(self) -> None:
        if self._session_dir.exists():
            for child in self._session_dir.iterdir():
                try:
                    child.unlink()
                except OSError:
                    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_gadget_controller.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/usb_floppy_pi/gadget/controller.py tests/unit/test_gadget_controller.py
git commit -m "feat(gadget): controller with mount/eject/swap/session"
```

---

## Task 10: gadget.configfs_backend — real ConfigFs implementation

**Files:**
- Create: `src/usb_floppy_pi/gadget/configfs_backend.py`

This module writes to `/sys/kernel/config/usb_gadget/` and cannot be unit-tested without root access on a Linux machine with `configfs` and `dwc2`. Verified manually in Task 23.

- [ ] **Step 1: Write the implementation**

Write `src/usb_floppy_pi/gadget/configfs_backend.py`:

```python
"""Real GadgetBackend that writes to /sys/kernel/config/usb_gadget/.

Cannot be unit-tested. Verified via end-to-end manual testing on the Pi (Task 23).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from .backend import GadgetParams

logger = logging.getLogger(__name__)


class ConfigFsBackend:
    """Manages a USB gadget through the Linux configfs interface.

    All ops are idempotent where possible. Methods raise OSError on permission /
    kernel-config issues — callers should catch and surface to the user.
    """

    GADGET_NAME = "floppy"
    CONFIGFS_ROOT = Path("/sys/kernel/config/usb_gadget")

    def __init__(self, configfs_root: Path | None = None) -> None:
        self._root = configfs_root or self.CONFIGFS_ROOT

    @property
    def gadget_dir(self) -> Path:
        return self._root / self.GADGET_NAME

    def create_gadget(self, params: GadgetParams) -> None:
        g = self.gadget_dir
        if g.exists():
            logger.info("gadget %s already exists; reusing", g)
            return
        g.mkdir(parents=True)
        _write(g / "idVendor", f"0x{params.id_vendor:04x}")
        _write(g / "idProduct", f"0x{params.id_product:04x}")
        _write(g / "bcdDevice", f"0x{params.bcd_device:04x}")
        _write(g / "bcdUSB", "0x0200")
        # Strings (en-us)
        strings = g / "strings" / "0x409"
        strings.mkdir(parents=True, exist_ok=True)
        _write(strings / "manufacturer", params.manufacturer)
        _write(strings / "product", params.product)
        _write(strings / "serialnumber", params.serial)
        # Function: mass_storage.usb0
        func = g / "functions" / "mass_storage.usb0"
        func.mkdir(parents=True, exist_ok=True)
        _write(func / "stall", "1")
        # LUN 0
        lun = func / "lun.0"
        lun.mkdir(parents=True, exist_ok=True)
        _write(lun / "removable", "1")
        _write(lun / "cdrom", "0")
        _write(lun / "nofua", "0")
        _write(lun / "ro", "0")
        _write(lun / "inquiry_string", params.inquiry_string)
        # Configuration 1
        cfg = g / "configs" / "c.1"
        cfg.mkdir(parents=True, exist_ok=True)
        _write(cfg / "MaxPower", "2")
        _write(cfg / "bmAttributes", "0xC0")
        cfg_strings = cfg / "strings" / "0x409"
        cfg_strings.mkdir(parents=True, exist_ok=True)
        _write(cfg_strings / "configuration", "USB Floppy Config")
        # Bind function to config
        link = cfg / "mass_storage.usb0"
        if not link.exists():
            link.symlink_to(func)
        logger.info("gadget tree created at %s", g)

    def destroy_gadget(self) -> None:
        g = self.gadget_dir
        if not g.exists():
            return
        # Detach
        try:
            udc = (g / "UDC").read_text().strip()
            if udc:
                (g / "UDC").write_text("\n")
        except OSError:
            pass
        # Remove function-config symlink
        cfg = g / "configs" / "c.1"
        link = cfg / "mass_storage.usb0"
        if link.is_symlink():
            link.unlink()
        # Remove string subdirs
        for s in (g / "configs" / "c.1" / "strings").iterdir():
            try:
                s.rmdir()
            except OSError:
                pass
        try:
            cfg.rmdir()
        except OSError:
            pass
        # Remove function (lun.0 must go first)
        func = g / "functions" / "mass_storage.usb0"
        if func.exists():
            try:
                (func / "lun.0").rmdir()
            except OSError:
                pass
            try:
                func.rmdir()
            except OSError:
                pass
        # Remove strings
        for s in (g / "strings").iterdir():
            try:
                s.rmdir()
            except OSError:
                pass
        try:
            g.rmdir()
        except OSError as exc:
            logger.warning("could not rmdir gadget root: %s", exc)
        logger.info("gadget destroyed")

    def configure_lun(self, *, file: Path | None, ro: bool) -> None:
        lun = self.gadget_dir / "functions" / "mass_storage.usb0" / "lun.0"
        # Always clear file before changing ro flag (kernel rejects ro change with file attached)
        _write(lun / "file", "")
        _write(lun / "ro", "1" if ro else "0")
        if file is not None:
            _write(lun / "file", str(file))

    def attach_to_udc(self) -> None:
        udc_path = self.gadget_dir / "UDC"
        # Pick the first UDC available
        udcs = sorted(p.name for p in Path("/sys/class/udc").iterdir())
        if not udcs:
            raise OSError("no UDC available — is dwc2 loaded?")
        _write(udc_path, udcs[0])
        logger.info("attached gadget to UDC %s", udcs[0])

    def detach_from_udc(self) -> None:
        udc_path = self.gadget_dir / "UDC"
        if udc_path.exists():
            _write(udc_path, "\n")


def _write(path: Path, value: str) -> None:
    """Write a value to a configfs attribute, creating it if needed."""
    path.write_text(value)
    # configfs is synchronous on write, but a tiny pause helps on slow paths.
    time.sleep(0.005)
```

- [ ] **Step 2: Sanity check that the file imports**

Run: `python -c "from usb_floppy_pi.gadget.configfs_backend import ConfigFsBackend; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/usb_floppy_pi/gadget/configfs_backend.py
git commit -m "feat(gadget): real ConfigFsBackend (untestable until on Pi)"
```

---

## Task 11: web.api — FastAPI app skeleton with state and sets endpoints

**Files:**
- Create: `src/usb_floppy_pi/web/__init__.py`
- Create: `src/usb_floppy_pi/web/api.py`
- Test: `tests/unit/test_web_api.py`

The web API takes the `Library` and `GadgetController` as constructor params for testability.

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_web_api.py`:

```python
"""Tests for web.api — uses FastAPI TestClient + mock controller."""
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from usb_floppy_pi.gadget.backend import GadgetParams, MockBackend
from usb_floppy_pi.gadget.controller import GadgetController
from usb_floppy_pi.storage.library import Library
from usb_floppy_pi.web.api import build_app


def _params() -> GadgetParams:
    return GadgetParams(
        id_vendor=0x0644, id_product=0x0000, bcd_device=0x3000,
        manufacturer="TEAC", product="USB Floppy", serial="0001",
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_web_api.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: Write the implementation**

Write `src/usb_floppy_pi/web/__init__.py`:

```python
"""Web API and static frontend."""
```

Write `src/usb_floppy_pi/web/api.py`:

```python
"""FastAPI application factory."""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
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
        return {"sets": [
            {
                "name": s.name,
                "read_only": s.read_only,
                "disks": [d.name for d in s.disks],
            }
            for s in library.sets
        ]}

    @app.get("/api/state")
    def get_state() -> dict:
        m = controller.current
        return {"mounted": (asdict(m) | {
            "backing_path": str(m.backing_path),
        }) if m else None}

    @app.post("/api/mount")
    async def post_mount(req: MountRequest) -> dict:
        s = _set_by_name(req.set)
        d = _disk_in_set(s, req.disk)
        try:
            mounted = await controller.mount(s, d, session=req.session)
        except DiskTooLargeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
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
        set: str = Form(...),
        file: UploadFile = File(...),
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
```

- [ ] **Step 4: Create empty static directory so the mount doesn't fail**

```bash
mkdir -p src/usb_floppy_pi/web/static
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_web_api.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add src/usb_floppy_pi/web tests/unit/test_web_api.py
git commit -m "feat(web): FastAPI endpoints for sets/state/mount/eject/readonly/upload"
```

---

## Task 12: web.api — upload endpoint test for .ima/.imz normalization

**Files:**
- Modify: `tests/unit/test_web_api.py:end` — add upload tests

The endpoint exists from Task 11. Now we add tests that exercise the `.ima`/`.imz` normalization path through the upload endpoint.

- [ ] **Step 1: Add the tests**

Append to `tests/unit/test_web_api.py`:

```python
import io
import zipfile


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
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/unit/test_web_api.py -v`
Expected: 12 passed (8 from Task 11 + 4 new ones)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_web_api.py
git commit -m "test(web): upload endpoint .ima/.imz normalization paths"
```

---

## Task 13: web/static — minimal HTML + vanilla JS UI

**Files:**
- Create: `src/usb_floppy_pi/web/static/index.html`
- Create: `src/usb_floppy_pi/web/static/app.js`

This is a single-page UI with no build step. Manually verified by opening it in a browser against the running server (Task 17 onwards).

- [ ] **Step 1: Write the HTML**

Write `src/usb_floppy_pi/web/static/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>USB Floppy Pi</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            margin: 0;
            padding: 1rem;
            background: #1a1a1a;
            color: #e0e0e0;
            max-width: 720px;
            margin: 0 auto;
        }
        h1 { color: #ffaa00; font-size: 1.4rem; }
        .status {
            background: #2a2a2a;
            padding: 0.75rem 1rem;
            border-radius: 6px;
            margin-bottom: 1rem;
            border-left: 4px solid #ffaa00;
        }
        .status.mounted { border-left-color: #4caf50; }
        .set {
            background: #252525;
            padding: 0.75rem 1rem;
            margin-bottom: 0.5rem;
            border-radius: 6px;
        }
        .set h3 { margin: 0 0 0.5rem 0; font-size: 1rem; }
        .ro-badge { color: #ff9800; font-size: 0.8rem; margin-left: 0.5rem; }
        .disk { display: flex; gap: 0.5rem; margin: 0.25rem 0; align-items: center; }
        .disk-name { flex-grow: 1; font-family: monospace; }
        button {
            background: #ffaa00;
            color: #1a1a1a;
            border: none;
            padding: 0.4rem 0.8rem;
            border-radius: 4px;
            cursor: pointer;
            font-weight: 600;
        }
        button:hover { background: #ffbb33; }
        button.danger { background: #e53935; color: white; }
        button.session { background: #5c6bc0; color: white; }
        button.mounted { background: #4caf50; color: white; }
        .upload-form {
            background: #2a2a2a;
            padding: 1rem;
            border-radius: 6px;
            margin-top: 1rem;
        }
        select, input[type=file] {
            background: #1a1a1a;
            color: #e0e0e0;
            border: 1px solid #444;
            padding: 0.4rem;
            border-radius: 4px;
        }
        .error { color: #e53935; margin-top: 0.5rem; }
    </style>
</head>
<body>
    <h1>USB Floppy Pi</h1>
    <div id="status" class="status">Loading...</div>
    <div id="sets-container"></div>

    <div class="upload-form">
        <h3 style="margin-top: 0">Upload image</h3>
        <select id="upload-set"></select>
        <input type="file" id="upload-file" accept=".img,.ima,.imz">
        <button onclick="doUpload()">Upload</button>
        <div id="upload-error" class="error"></div>
    </div>

    <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write the JavaScript**

Write `src/usb_floppy_pi/web/static/app.js`:

```javascript
async function fetchJson(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
        const txt = await r.text();
        throw new Error(txt || r.statusText);
    }
    return r.json();
}

async function refresh() {
    const [sets, state] = await Promise.all([
        fetchJson("/api/sets"),
        fetchJson("/api/state"),
    ]);
    renderStatus(state);
    renderSets(sets.sets, state.mounted);
    populateUploadDropdown(sets.sets);
}

function renderStatus(state) {
    const el = document.getElementById("status");
    if (state.mounted) {
        el.className = "status mounted";
        const m = state.mounted;
        const sess = m.is_session ? " (session)" : "";
        const ro = m.read_only ? " [RO]" : " [RW]";
        el.textContent = `Mounted: ${m.set_name} / ${m.disk_filename}${ro}${sess}`;
    } else {
        el.className = "status";
        el.textContent = "No image mounted";
    }
}

function renderSets(sets, mounted) {
    const container = document.getElementById("sets-container");
    container.innerHTML = "";
    if (sets.length === 0) {
        container.innerHTML = "<p>No floppy sets yet. Upload an image or copy folders to the Samba share <code>\\\\floppy\\floppies</code>.</p>";
        return;
    }
    for (const s of sets) {
        const div = document.createElement("div");
        div.className = "set";
        const roBadge = s.read_only ? `<span class="ro-badge">[RO]</span>` : "";
        const roButton = s.read_only
            ? `<button onclick="setReadOnly('${escape(s.name)}', false)">Make writable</button>`
            : `<button onclick="setReadOnly('${escape(s.name)}', true)">Make read-only</button>`;
        let html = `<h3>${escapeHtml(s.name)}${roBadge}</h3>`;
        html += s.disks.map(d => {
            const isMounted = mounted && mounted.set_name === s.name && mounted.disk_filename === d;
            const cls = isMounted ? "mounted" : "";
            return `<div class="disk">
                <span class="disk-name">${escapeHtml(d)}</span>
                <button class="${cls}" onclick="mount('${escape(s.name)}', '${escape(d)}', false)">${isMounted ? "Mounted" : "Mount"}</button>
                <button class="session" onclick="mount('${escape(s.name)}', '${escape(d)}', true)">Session</button>
            </div>`;
        }).join("");
        html += `<div style="margin-top: 0.5rem">${roButton}`;
        if (mounted) {
            html += ` <button class="danger" onclick="eject()">Eject</button>`;
        }
        html += `</div>`;
        div.innerHTML = html;
        container.appendChild(div);
    }
}

function populateUploadDropdown(sets) {
    const sel = document.getElementById("upload-set");
    const current = sel.value;
    sel.innerHTML = "";
    for (const s of sets) {
        const opt = document.createElement("option");
        opt.value = s.name;
        opt.textContent = s.name;
        sel.appendChild(opt);
    }
    if (current) sel.value = current;
}

async function mount(setName, disk, session) {
    try {
        await fetchJson("/api/mount", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({set: setName, disk: disk, session: session}),
        });
        await refresh();
    } catch (e) { alert("Mount failed: " + e.message); }
}

async function eject() {
    try {
        await fetchJson("/api/eject", {method: "POST"});
        await refresh();
    } catch (e) { alert("Eject failed: " + e.message); }
}

async function setReadOnly(setName, ro) {
    try {
        await fetchJson(`/api/sets/${encodeURIComponent(setName)}/readonly`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ro}),
        });
        await refresh();
    } catch (e) { alert("Set readonly failed: " + e.message); }
}

async function doUpload() {
    const setSel = document.getElementById("upload-set");
    const fileInput = document.getElementById("upload-file");
    const errEl = document.getElementById("upload-error");
    errEl.textContent = "";
    if (!fileInput.files.length) { errEl.textContent = "Pick a file first"; return; }
    const fd = new FormData();
    fd.append("set", setSel.value);
    fd.append("file", fileInput.files[0]);
    try {
        await fetchJson("/api/upload", {method: "POST", body: fd});
        fileInput.value = "";
        await refresh();
    } catch (e) {
        errEl.textContent = "Upload failed: " + e.message;
    }
}

function escapeHtml(s) {
    return s.replace(/[<>&"']/g, c => ({
        "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;"
    })[c]);
}

// Refresh every 3 seconds + on load
refresh().catch(e => {
    document.getElementById("status").textContent = "Error: " + e.message;
});
setInterval(refresh, 3000);
```

- [ ] **Step 3: Commit**

```bash
git add src/usb_floppy_pi/web/static/
git commit -m "feat(web): vanilla JS frontend for set browsing and uploads"
```

---

## Task 14: __main__.py — orchestrate everything

**Files:**
- Create: `src/usb_floppy_pi/__main__.py`
- Test: `tests/integration/test_main_smoke.py`

The entry point loads config, builds the library and gadget controller, restores the last-mounted image, and starts uvicorn.

- [ ] **Step 1: Write the failing smoke test**

Write `tests/integration/test_main_smoke.py`:

```python
"""Integration smoke test for __main__.run() with mocked gadget backend."""
import asyncio
import json
from pathlib import Path

import pytest

from usb_floppy_pi.__main__ import build_runtime
from usb_floppy_pi.gadget.backend import MockBackend


@pytest.mark.asyncio
async def test_runtime_starts_with_no_floppies(tmp_path: Path) -> None:
    floppies = tmp_path / "floppies"
    floppies.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"samba_share_name": "floppies"}))
    runtime = await build_runtime(
        config_path=cfg,
        floppy_root=floppies,
        gadget_backend=MockBackend(),
    )
    try:
        assert runtime.controller.current is None
        assert runtime.library.sets == []
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_runtime_restores_last_mounted(tmp_path: Path) -> None:
    floppies = tmp_path / "floppies"
    set_dir = floppies / "DOS"
    set_dir.mkdir(parents=True)
    (set_dir / "DISK1.img").write_bytes(b"\x00" * 1474560)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "last_mounted": {"set": "DOS", "disk": "DISK1.img"},
    }))
    runtime = await build_runtime(
        config_path=cfg,
        floppy_root=floppies,
        gadget_backend=MockBackend(),
    )
    try:
        assert runtime.controller.current is not None
        assert runtime.controller.current.set_name == "DOS"
        assert runtime.controller.current.disk_filename == "DISK1.img"
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_runtime_skips_missing_last_mounted(tmp_path: Path) -> None:
    floppies = tmp_path / "floppies"
    floppies.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "last_mounted": {"set": "Gone", "disk": "x.img"},
    }))
    runtime = await build_runtime(
        config_path=cfg,
        floppy_root=floppies,
        gadget_backend=MockBackend(),
    )
    try:
        assert runtime.controller.current is None
    finally:
        await runtime.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_main_smoke.py -v`
Expected: ImportError on `usb_floppy_pi.__main__`

- [ ] **Step 3: Write the implementation**

Write `src/usb_floppy_pi/__main__.py`:

```python
"""Entry point: load config, init storage + gadget, restore last mount, run web server."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from .core.config import Config, load_config, save_config
from .gadget.backend import GadgetBackend, GadgetParams
from .gadget.configfs_backend import ConfigFsBackend
from .gadget.controller import GadgetController
from .storage.library import Library
from .web.api import build_app

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("/etc/usb-floppy-pi/config.json")
DEFAULT_FLOPPY_ROOT = Path("/home/pi/floppies")


@dataclass
class Runtime:
    config: Config
    config_path: Path
    library: Library
    controller: GadgetController
    app: object  # FastAPI

    async def shutdown(self) -> None:
        await self.controller.shutdown()
        await self.library.stop()


def _build_gadget_params() -> GadgetParams:
    serial = _derive_serial()
    return GadgetParams(
        id_vendor=0x0644,         # TEAC
        id_product=0x0000,        # FD-05PUW
        bcd_device=0x3000,
        manufacturer="TEAC",
        product="USB Floppy",
        serial=serial,
        inquiry_string="TEAC    FD-05PUW         3000",
    )


def _derive_serial() -> str:
    """Derive a stable serial from the host's MAC, falling back to hostname."""
    try:
        with open("/sys/class/net/wlan0/address") as f:
            return f.read().strip().replace(":", "").upper()
    except OSError:
        return socket.gethostname()[:12].upper() or "FLOPPY00"


async def build_runtime(
    *,
    config_path: Path,
    floppy_root: Path,
    gadget_backend: GadgetBackend,
) -> Runtime:
    """Build all components and restore last-mounted image. Used by tests + main()."""
    cfg = load_config(config_path)
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    loop = asyncio.get_running_loop()
    library = Library(floppy_root, loop=loop)
    await library.start()

    controller = GadgetController(gadget_backend, _build_gadget_params())
    await controller.initialize()

    if cfg.last_mounted is not None:
        target_set_name = cfg.last_mounted.get("set")
        target_disk_name = cfg.last_mounted.get("disk")
        for s in library.sets:
            if s.name != target_set_name:
                continue
            for d in s.disks:
                if d.name == target_disk_name:
                    try:
                        await controller.mount(s, d)
                    except Exception:
                        logger.exception("could not restore last mount")
                    break
            break
        if controller.current is None:
            logger.info("last_mounted %s/%s no longer present", target_set_name, target_disk_name)

    # Subscribe to changes so we persist last_mounted whenever it changes.
    def _persist_last_mounted() -> None:
        m = controller.current
        cfg.last_mounted = (
            {"set": m.set_name, "disk": m.disk_filename}
            if m is not None and not m.is_session
            else None
        )
        try:
            save_config(config_path, cfg)
        except OSError:
            logger.exception("could not persist config")

    # Wrap controller to persist on every successful mount/eject.
    original_mount = controller.mount
    original_eject = controller.eject

    async def _mount(*args, **kwargs):
        result = await original_mount(*args, **kwargs)
        _persist_last_mounted()
        return result

    async def _eject():
        await original_eject()
        _persist_last_mounted()

    controller.mount = _mount       # type: ignore[method-assign]
    controller.eject = _eject       # type: ignore[method-assign]

    app = build_app(library=library, controller=controller, floppy_root=floppy_root)

    return Runtime(
        config=cfg,
        config_path=config_path,
        library=library,
        controller=controller,
        app=app,
    )


async def _main_async(config_path: Path, floppy_root: Path, port: int) -> None:
    backend = ConfigFsBackend()
    runtime = await build_runtime(
        config_path=config_path,
        floppy_root=floppy_root,
        gadget_backend=backend,
    )
    config = uvicorn.Config(
        runtime.app,
        host="0.0.0.0",
        port=port,
        log_level=runtime.config.log_level.lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await runtime.shutdown()


def main() -> None:
    config_path = Path(os.environ.get("USB_FLOPPY_CONFIG", DEFAULT_CONFIG_PATH))
    floppy_root = Path(os.environ.get("USB_FLOPPY_ROOT", DEFAULT_FLOPPY_ROOT))
    port = int(os.environ.get("USB_FLOPPY_PORT", "80"))
    try:
        asyncio.run(_main_async(config_path, floppy_root, port))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_main_smoke.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full test suite to verify nothing is broken**

Run: `pytest -v`
Expected: all tests pass (config + state + scanner + normalizer + watcher + library + gadget_backend + gadget_controller + web_api + main_smoke)

- [ ] **Step 6: Commit**

```bash
git add src/usb_floppy_pi/__main__.py tests/integration/test_main_smoke.py
git commit -m "feat: __main__ orchestration with last-mounted restore"
```

---

## Task 15: deploy/systemd unit

**Files:**
- Create: `deploy/systemd/usb-floppy-pi.service`

- [ ] **Step 1: Write the unit file**

Write `deploy/systemd/usb-floppy-pi.service`:

```ini
[Unit]
Description=USB Floppy Pi
After=network-online.target smbd.service systemd-modules-load.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/usb-floppy-pi
Environment=USB_FLOPPY_CONFIG=/etc/usb-floppy-pi/config.json
Environment=USB_FLOPPY_ROOT=/home/pi/floppies
Environment=USB_FLOPPY_PORT=80
ExecStartPre=/bin/sh -c 'mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config'
ExecStart=/opt/usb-floppy-pi/.venv/bin/python -m usb_floppy_pi
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

> **Why root:** writing to `/sys/kernel/config/usb_gadget/` and binding to port 80 both require root. We accept this for an appliance device. If we later want to drop privileges, we can use `CAP_SYS_ADMIN` + `CAP_NET_BIND_SERVICE` and a setcap workflow — out of scope for Phase 1.

- [ ] **Step 2: Commit**

```bash
git add deploy/systemd/usb-floppy-pi.service
git commit -m "deploy: systemd unit for usb-floppy-pi"
```

---

## Task 16: deploy/samba/smb.conf.j2 — Samba share template

**Files:**
- Create: `deploy/samba/smb.conf.j2`

- [ ] **Step 1: Write the Jinja template**

Write `deploy/samba/smb.conf.j2`:

```ini
[global]
   workgroup = WORKGROUP
   server string = USB Floppy Pi
   netbios name = FLOPPY
   security = user
   map to guest = bad user
   server min protocol = SMB2
   log file = /var/log/samba/log.%m
   max log size = 1000

[{{ share_name }}]
   path = /home/pi/floppies
   browseable = yes
   read only = no
   writable = yes
   guest ok = no
   valid users = {{ samba_user }}
   create mask = 0664
   directory mask = 0775
   force user = pi
   force group = pi
```

The `install.sh` script renders this template by simple variable substitution (no Jinja2 dependency needed at install time — see Task 18).

- [ ] **Step 2: Commit**

```bash
git add deploy/samba/smb.conf.j2
git commit -m "deploy: Samba share template"
```

---

## Task 17: deploy/boot config patches

**Files:**
- Create: `deploy/boot/config.txt.append`
- Create: `deploy/boot/cmdline.txt.append`

- [ ] **Step 1: Write the config.txt append fragment**

Write `deploy/boot/config.txt.append`:

```
# === usb-floppy-pi additions ===
dtoverlay=dwc2
dtparam=i2c_arm=on
dtparam=audio=off
# === end usb-floppy-pi ===
```

- [ ] **Step 2: Write the cmdline.txt append fragment**

Write `deploy/boot/cmdline.txt.append`:

```
modules-load=dwc2
```

> Note: `cmdline.txt` is a single-line file. The install script appends this to the existing line as `<existing> modules-load=dwc2`.

- [ ] **Step 3: Commit**

```bash
git add deploy/boot/
git commit -m "deploy: boot config fragments for dwc2 and i2c"
```

---

## Task 18: deploy/install.sh — installation script

**Files:**
- Create: `deploy/install.sh`

- [ ] **Step 1: Write the script**

Write `deploy/install.sh`:

```bash
#!/usr/bin/env bash
# USB Floppy Pi installer for Raspberry Pi OS Lite (Bookworm).
# Run as root on the Pi. Idempotent.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must be run as root (use sudo)" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR=/opt/usb-floppy-pi
CONFIG_DIR=/etc/usb-floppy-pi
FLOPPY_ROOT=/home/pi/floppies
BOOT_FW=/boot/firmware

echo "==> usb-floppy-pi installer"
echo "    repo : $REPO_DIR"
echo "    target: $INSTALL_DIR"
echo

# === I2C level warning (LCD) — only relevant once Phase 2 is added, but flag now ===
echo "NOTE: When you add an LCD1602 backpack (Phase 2), the PCF8574 I2C pull-ups"
echo "      go to 5V while the Pi GPIO is 3V3 (not 5V tolerant). See spec §5.2."
echo

# === Apt packages ===
echo "==> Installing apt packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip samba smbclient git

# === Copy / sync code to /opt ===
echo "==> Syncing code to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
    --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
    "$REPO_DIR/" "$INSTALL_DIR/"

# === Python venv ===
echo "==> Creating venv and installing"
if [[ ! -d $INSTALL_DIR/.venv ]]; then
    python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

# === Default config ===
echo "==> Default config at $CONFIG_DIR"
mkdir -p "$CONFIG_DIR"
if [[ ! -f $CONFIG_DIR/config.json ]]; then
    cat > "$CONFIG_DIR/config.json" <<'JSON'
{
  "mute": false,
  "buzzer_volume": 0.6,
  "last_mounted": null,
  "samba_share_name": "floppies",
  "log_level": "INFO"
}
JSON
fi

# === Floppy root + ownership ===
mkdir -p "$FLOPPY_ROOT"
chown -R pi:pi "$FLOPPY_ROOT"

# === Samba ===
echo "==> Configuring Samba share"
SAMBA_USER="${SAMBA_USER:-floppy}"
SHARE_NAME="${SHARE_NAME:-floppies}"

# Render template by simple sed
cp "$INSTALL_DIR/deploy/samba/smb.conf.j2" /etc/samba/smb.conf
sed -i "s|{{ share_name }}|$SHARE_NAME|g" /etc/samba/smb.conf
sed -i "s|{{ samba_user }}|$SAMBA_USER|g" /etc/samba/smb.conf

# Create samba user if missing
if ! pdbedit -L | grep -q "^$SAMBA_USER:"; then
    if ! id -u "$SAMBA_USER" >/dev/null 2>&1; then
        useradd --no-create-home --shell /usr/sbin/nologin "$SAMBA_USER"
    fi
    echo "==> Set Samba password for user '$SAMBA_USER':"
    smbpasswd -a "$SAMBA_USER"
fi

systemctl restart smbd

# === Boot config ===
echo "==> Patching $BOOT_FW/config.txt and cmdline.txt"
if ! grep -q "usb-floppy-pi additions" "$BOOT_FW/config.txt"; then
    cat "$INSTALL_DIR/deploy/boot/config.txt.append" >> "$BOOT_FW/config.txt"
    echo "    config.txt patched"
else
    echo "    config.txt already patched"
fi

if ! grep -q "modules-load=dwc2" "$BOOT_FW/cmdline.txt"; then
    # cmdline.txt is a single line; append in-place
    sed -i 's|$| modules-load=dwc2|' "$BOOT_FW/cmdline.txt"
    echo "    cmdline.txt patched"
else
    echo "    cmdline.txt already patched"
fi

# === systemd unit ===
echo "==> Installing systemd unit"
cp "$INSTALL_DIR/deploy/systemd/usb-floppy-pi.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable usb-floppy-pi.service

echo
echo "==> Installation complete."
echo "    Reboot for the dwc2 + cmdline changes to take effect:"
echo "      sudo reboot"
echo
echo "    After reboot:"
echo "      - Connect the Pi micro-USB-DATA port to the host PC"
echo "      - Visit http://floppy.local (or the Pi's IP) for the web UI"
echo "      - Mount the Samba share at \\\\floppy\\$SHARE_NAME"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x deploy/install.sh
```

- [ ] **Step 3: Commit**

```bash
git add deploy/install.sh
git commit -m "deploy: install.sh — apt + venv + samba + boot patches + systemd"
```

---

## Task 19: README — finalize for Phase 1

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the README with a Phase 1-complete version**

Overwrite `README.md`:

```markdown
# usb-floppy-pi

USB floppy drive emulator for Raspberry Pi Zero 2W. Connects to a retro PC via USB and presents itself as a 1.44 MB floppy drive (`A:` via BIOS legacy emulation, no host drivers required).

Designed for a 2010-era PC running DOS / Win98 SE dual-boot, but works as a generic USB floppy on any host.

## Phase 1 status

✅ USB Mass Storage gadget (configfs)
✅ Web UI for browsing, mounting, ejecting, uploading
✅ Samba share for drag-and-drop image management from any machine on the LAN
✅ `.img`/`.ima`/`.imz` upload formats (auto-normalized to `.img`)
✅ Last-mounted image restored on boot
✅ Read-only and session mount modes

⏳ LCD + buttons + buzzer audio — Phase 2/3 (separate plans)

## Hardware (Phase 1)

- Raspberry Pi Zero 2W
- microSD ≥ 8 GB
- USB-A ↔ micro-USB **data** cable (not charge-only)

## Install

1. Flash Raspberry Pi OS Lite (Bookworm 64-bit) with `rpi-imager`. In the imager's advanced settings, enable SSH and configure WiFi.
2. SSH into the Pi.
3. Clone this repo and run the installer:

```bash
git clone <repo-url> usb-floppy-pi
cd usb-floppy-pi
sudo ./deploy/install.sh
```

4. Reboot: `sudo reboot`

## Use

- Connect the Pi's micro-USB **data** port (the one closer to the HDMI port — the other is power-only) to the retro PC's USB.
- Connect the Pi's power port to a 5V power source — or, if you accept the small risk of corruption on PC power-off, you can power the Pi from the same data cable (see spec §5.6).
- From any device on the LAN: open `http://floppy.local` for the web UI.
- From any device: mount `\\floppy\floppies` for drag-and-drop image management.

## Layout

```
/home/pi/floppies/
├── DOS 6.22/                 ← each subdirectory is one "set"
│   ├── ro                    ← optional marker file = whole set is read-only
│   ├── DISK001.img
│   └── DISK002.img
└── Win98 Boot/
    └── boot.img
```

When a set has more than one disk, the web UI lets you swap between disks (useful for multi-disk installers).

## Development

Run the test suite (cross-platform, no Pi needed):

```bash
python -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest -v
```

The configfs gadget backend (`src/usb_floppy_pi/gadget/configfs_backend.py`) is verified manually on the Pi (see Task 23 of the Phase 1 plan).

## Spec & plans

- Spec: `docs/superpowers/specs/2026-05-06-usb-floppy-pi-design.md`
- Phase 1 plan: `docs/superpowers/plans/2026-05-06-phase-1-mvp-headless.md`
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README for Phase 1"
```

---

## Task 20: Local mock-mode dry run

Before deploying to a real Pi, run the system end-to-end on the development machine using `MockBackend`.

**Files:**
- Create: `scripts/dev-run.py` (helper for local runs)

- [ ] **Step 1: Write the dev launcher**

Create directory and file:

```bash
mkdir -p scripts
```

Write `scripts/dev-run.py`:

```python
"""Local dev runner with MockBackend. Usage: python scripts/dev-run.py"""
import asyncio
import json
import os
from pathlib import Path

import uvicorn

from usb_floppy_pi.__main__ import build_runtime
from usb_floppy_pi.gadget.backend import MockBackend


async def main() -> None:
    workspace = Path("./.dev-state").resolve()
    workspace.mkdir(exist_ok=True)
    floppies = workspace / "floppies"
    floppies.mkdir(exist_ok=True)
    cfg = workspace / "config.json"
    if not cfg.exists():
        cfg.write_text(json.dumps({}, indent=2))

    runtime = await build_runtime(
        config_path=cfg,
        floppy_root=floppies,
        gadget_backend=MockBackend(),
    )
    print(f"Floppy root: {floppies}")
    print(f"Drop a folder with .img/.ima/.imz files into it.")
    print(f"Open http://localhost:8080")
    config = uvicorn.Config(runtime.app, host="127.0.0.1", port=8080, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it**

```bash
python scripts/dev-run.py
```

Expected: server starts on `http://localhost:8080`. Open that in a browser.

- [ ] **Step 3: Manual smoke test**

In another terminal/window:

```bash
mkdir -p .dev-state/floppies/TEST
python -c "open('.dev-state/floppies/TEST/DISK1.img','wb').write(b'\x00'*1474560)"
```

Refresh the browser. The set "TEST" with disk "DISK1.img" should appear within ~1 second.

Click "Mount" — the status should change to "Mounted: TEST / DISK1.img [RW]".
Click "Eject" — status returns to "No image mounted".

Upload a `.img` from the browser to the "TEST" set. Verify it appears.

- [ ] **Step 4: Commit the helper**

```bash
git add scripts/dev-run.py
git commit -m "chore: dev launcher with MockBackend for local testing"
```

---

## Task 21: Lint and type sanity

**Files:**
- (none — just verify code quality)

- [ ] **Step 1: Run ruff lint**

```bash
ruff check src tests
```

Expected: no errors. If there are any, fix them inline (most will be unused imports or unused variables).

- [ ] **Step 2: Run ruff format check**

```bash
ruff format --check src tests
```

If files need formatting, apply: `ruff format src tests`

- [ ] **Step 3: Run the full test suite once more**

```bash
pytest -v
```

Expected: all green.

- [ ] **Step 4: Commit any lint/format fixes**

```bash
git add -u
git diff --cached --quiet || git commit -m "chore: lint and format pass"
```

---

## Task 22: End-to-end manual integration on real Pi

This is a manual checklist. Cannot be automated without dedicated hardware in CI.

**Hardware setup:**
- Raspberry Pi Zero 2W with microSD flashed with Raspberry Pi OS Lite (Bookworm 64-bit)
- WiFi credentials configured via rpi-imager
- USB-A to micro-USB **data** cable
- Retro PC: Biostar H55 + i3 540 with DOS / Win98SE dual-boot

- [ ] **Step 1: Flash and boot the Pi**

- [ ] **Step 2: SSH in, clone repo, install**

```bash
git clone <repo-url> usb-floppy-pi
cd usb-floppy-pi
sudo ./deploy/install.sh
sudo reboot
```

- [ ] **Step 3: After reboot, verify services**

```bash
sudo systemctl status usb-floppy-pi
sudo systemctl status smbd
```

Expected: both `active (running)`.

- [ ] **Step 4: Verify web UI from another machine**

Open `http://floppy.local` (or the Pi's IP). The page should load and show "No floppy sets yet."

- [ ] **Step 5: Upload a test .img via web**

Pick any small .img (e.g., a DOS boot floppy from internet archive). It should appear under the chosen set after upload.

- [ ] **Step 6: Verify Samba share**

From another PC: open `\\floppy\floppies` (Windows) or `smb://floppy.local/floppies` (mac/Linux). Authenticate as user `floppy` with the password set during install. Drop a `DOS 6.22/` folder containing a few `.img` files. Within a couple of seconds, refresh the web UI — the set should appear.

- [ ] **Step 7: Connect the Pi to the retro PC**

Power off the retro PC. Plug the Pi's **data** micro-USB into a USB port on the retro PC. Power on the retro PC.

Mount an image via web UI. The retro PC's BIOS should detect the USB floppy. In Win98 SE, `A:` should be present and contain the contents of the mounted image. In DOS, `dir A:` should list the files.

- [ ] **Step 8: Test image swap**

While the retro PC is running and mounted, mount a different image from the web UI. The host should detect "media change" and on next access show the new contents (in DOS, run `dir A:` again).

- [ ] **Step 9: Test write (writable set)**

In Win98 SE, copy a small file to `A:`. Eject. Re-mount the same image. Verify the file persists.

- [ ] **Step 10: Test read-only**

Toggle a set to read-only via web UI. Mount one of its disks. In Win98 SE, attempt to copy a file to `A:` — should fail with "Cannot copy: drive is write-protected" (or similar).

- [ ] **Step 11: Test session mount**

Click "Session" on a writable set. Write a file in Win98. Eject. Re-mount the same disk normally — the file should NOT be there (session changes were discarded).

- [ ] **Step 12: Test last-mounted persistence**

Mount an image, leave it mounted, reboot the Pi (`sudo reboot`). After reboot, verify the same image is mounted at boot.

- [ ] **Step 13: Document any issues**

Update the README's "Phase 1 status" with any caveats discovered during real-hardware testing. Open follow-up issues for anything that needs fixing.

- [ ] **Step 14: Commit any fixes**

If any code changes were needed during integration:

```bash
git add <changed files>
git commit -m "fix: <issue discovered in integration> "
```

- [ ] **Step 15: Tag Phase 1 complete**

```bash
git tag -a v0.1.0-phase1 -m "Phase 1 MVP headless complete"
```

---

## Self-Review (filled out)

**Spec coverage check:**

| Spec section | Implemented in |
|--------------|---------------|
| §2.1 USB Mass Storage gadget | Tasks 8-10, 14 |
| §2.2 Capacity 1.44MB + .img/.ima/.imz formats | Tasks 5, 9 (padding/oversize), 12 |
| §2.3 ro / rw / session modes | Tasks 4, 9 (controller), 11 (web ro endpoint), 13 (session button) |
| §2.4 Filesystem-as-DB layout | Tasks 4, 6, 7 |
| §2.5 last_mounted on boot | Task 14 |
| §2.6 Power from PC USB only | (Hardware concern, no code) |
| §2.7 config.json with atomic writes | Task 2 |
| §3.1 Stack | Tasks 1, 11, 14 |
| §3.2 Single asyncio process | Task 14 |
| §3.3-3.5 Module responsibilities + state + layout | Tasks 2-14 |
| §5.5 Gadget configfs (TEAC IDs, removable, nofua, inquiry) | Tasks 9, 10 |
| §6.1-6.3 Web API + UI | Tasks 11, 12, 13 |
| §7 Samba | Tasks 16, 18 |
| §8 Phase 1 deliverable | Tasks 14-22 |
| §9 Testability with backends | Tasks 8, 9, 14 (MockBackend; ConfigFsBackend integration-tested in 22) |

**Items deferred (not bugs — explicitly out of Phase 1 scope):**

- `core.event_bus` — only consumed by Phase 2/3 modules
- `core.state.AppState` (full version with cursor_screen, mute, etc.) — minimal here; Phase 2 expands
- `hardware.lcd / buttons / audio` — Phase 2/3
- `gadget.activity` — Phase 3
- `ui.menus` — Phase 2

**Placeholder scan:** No "TBD", "TODO", or "implement later" in any task. Each task has full code.

**Type/method consistency check:**
- `GadgetController.mount(set, disk, *, session)` — used identically in Tasks 9, 11, 14
- `Library.sets` property — used in Tasks 7, 11, 14
- `MountedImage` shape — defined in Task 3, consumed in Tasks 9, 11
- `NormalizationResult.kind` enum-strings (`passthrough`/`renamed`/`extracted`/`error`/`ignored`) — consistent in Tasks 5 and 12
- `Config` field names — defined in Task 2, used in Tasks 14, 18

No inconsistencies found.
