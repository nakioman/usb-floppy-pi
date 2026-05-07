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
