"""Validated standalone appearance and completion lifetime."""

import re
import math
from .source_config import normalize_sources
from ..music.play.catalog import sound_files, default_sound, DEFAULT_COMPLETION_SOUND

DEFAULT_BACKGROUND = "#141d2c"
DEFAULT_OPACITY = 100
DEFAULT_HIDE_MINUTES = 1


def normalize_preferences(value: dict | None, *, strict: bool = False) -> dict:
    value = value if isinstance(value, dict) else {}
    color = str(value.get("monitor_background_color", DEFAULT_BACKGROUND)).strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", color):
        if strict:
            raise ValueError("Monitor background color must be a six-digit hex color")
        color = DEFAULT_BACKGROUND
    try:
        raw = value.get("monitor_opacity", DEFAULT_OPACITY)
        opacity = int(raw)
        if isinstance(raw, bool) or opacity != float(raw) or not 0 <= opacity <= 100:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        if strict:
            raise ValueError("灵动岛不透明度须为 0 到 100 的整数") from None
        opacity = DEFAULT_OPACITY
    try:
        raw = value.get("monitor_hide_minutes", float(value.get("monitor_hide_seconds", 60)) / 60)
        minutes = float(raw)
        if isinstance(raw, bool) or not math.isfinite(minutes):
            raise ValueError()
        if strict and (not minutes.is_integer() or not 1 <= minutes <= 10):
            raise ValueError()
        # Migrate the former seconds / 20-minute preferences on load. Round up
        # so migration does not unexpectedly shorten a fractional minute.
        hide_minutes = min(10, max(1, math.ceil(minutes)))
    except (ValueError, TypeError, OverflowError):
        if strict:
            raise ValueError("任务保留时间须为 1 到 10 分钟的整数") from None
        hide_minutes = DEFAULT_HIDE_MINUTES
    sound = str(value.get("monitor_completion_sound", DEFAULT_COMPLETION_SOUND))
    files = sound_files()
    if sound not in files:
        if strict:
            # An empty installation still permits saving unrelated preferences.
            if sound != DEFAULT_COMPLETION_SOUND:
                raise ValueError("Monitor completion sound is unavailable")
        sound = default_sound(files)
    enabled = value.get("monitor_sound_enabled", True)
    if not isinstance(enabled, bool):
        if strict:
            raise ValueError("Monitor sound enabled must be a boolean")
        enabled = True
    return {
        "monitor_background_color": color,
        "monitor_opacity": opacity,
        "monitor_hide_minutes": hide_minutes,
        "monitor_completion_sound": sound,
        "monitor_sound_enabled": enabled,
        "monitor_sources": normalize_sources(value.get("monitor_sources"), strict=strict),
    }
