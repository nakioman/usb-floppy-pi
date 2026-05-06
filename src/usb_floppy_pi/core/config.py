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
