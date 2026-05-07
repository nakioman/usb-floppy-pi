"""Tests for storage.scanner."""

from pathlib import Path

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
    assert [d.name for d in sets[0].disks] == ["DISK001.img", "DISK002.img", "DISK003.img"]


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
