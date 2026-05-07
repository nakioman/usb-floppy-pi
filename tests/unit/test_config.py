"""Tests for core.config."""

import json
from pathlib import Path

from usb_floppy_pi.core.config import DEFAULT_CONFIG, Config, load_config, save_config


def test_load_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg = load_config(cfg_path)
    assert cfg.mute is False
    assert cfg.buzzer_volume == 0.6
    assert cfg.last_mounted is None
    assert cfg.samba_share_name == "floppies"


def test_load_reads_existing_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "mute": True,
                "buzzer_volume": 0.3,
                "last_mounted": {"set": "DOS 6.22", "disk": "DISK001.img"},
                "samba_share_name": "myshare",
                "log_level": "DEBUG",
            }
        )
    )
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


def test_load_returns_defaults_for_binary_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_bytes(b"\xff\xfe\x00\x00binary garbage")
    cfg = load_config(cfg_path)
    assert cfg.mute is False  # defaults


def test_load_returns_defaults_for_non_object_json(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("[1, 2, 3]")
    cfg = load_config(cfg_path)
    assert cfg.mute is False  # defaults

    cfg_path.write_text("null")
    cfg2 = load_config(cfg_path)
    assert cfg2.mute is False


def test_default_config_is_stable() -> None:
    assert DEFAULT_CONFIG == Config()
