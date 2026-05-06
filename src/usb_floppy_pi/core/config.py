"""Application configuration: JSON-backed, atomic writes."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
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
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("config %s unreadable (%s); using defaults", path, exc)
        return Config()
    if not isinstance(data, dict):
        logger.warning("config %s is not a JSON object; using defaults", path)
        return Config()
    defaults = asdict(DEFAULT_CONFIG)
    merged = {**defaults, **data}
    known_keys = set(Config.__dataclass_fields__)
    filtered = {k: v for k, v in merged.items() if k in known_keys}
    return Config(**filtered)


def save_config(path: Path, cfg: Config) -> None:
    """Write config atomically: tmp + fsync + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(asdict(cfg), indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
