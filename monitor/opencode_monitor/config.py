"""Small, dependency-free configuration storage for the opencode monitor.

Window position plus the appearance preferences understood by the Dynamic
Island (`monitor.src.preferences.normalize_preferences`).  Only supported keys
are persisted; unknown keys in the file are ignored.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from ..src.paths import data_root

SUPPORTED = (
    "x", "y",
    "monitor_background_color", "monitor_opacity", "monitor_hide_minutes",
    "monitor_completion_sound", "monitor_sound_enabled",
)

DEFAULTS: dict[str, Any] = {
    "x": None,
    "y": None,
    "monitor_background_color": "#141d2c",
    "monitor_opacity": 100,
    "monitor_hide_minutes": 1,
    "monitor_completion_sound": "sound-1",
    "monitor_sound_enabled": True,
}


def config_path() -> Path:
    return data_root() / "settings.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or config_path()
    data: dict[str, Any] = dict(DEFAULTS)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for key in SUPPORTED:
                value = raw.get(key)
                if key in ("x", "y"):
                    if isinstance(value, int) and not isinstance(value, bool):
                        data[key] = value
                elif value is not None:
                    data[key] = value
    except (OSError, ValueError, TypeError):
        pass
    return data


def save_config(config: dict[str, Any], path: Path | None = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: config.get(key) for key in SUPPORTED}
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
