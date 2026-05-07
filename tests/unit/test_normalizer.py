"""Tests for storage.normalizer."""
import zipfile
from pathlib import Path

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
