"""Discover local MP3 cues without exposing arbitrary filesystem paths."""

from pathlib import Path
import re
from urllib.parse import quote

MUSIC_DIR = Path(__file__).resolve().parents[1]
DEFAULT_COMPLETION_SOUND = "sound-1"


def sound_label(name: str) -> str:
    match = re.fullmatch(r"sound-(\d+)", name)
    return f"提示音 {int(match.group(1))}" if match else name


def sound_files(directory: Path | None = None) -> dict[str, Path]:
    root = (directory or MUSIC_DIR).resolve()
    try:
        files = [p for p in root.iterdir() if p.suffix.casefold() == ".mp3"
                 and p.is_file() and not p.is_symlink() and p.resolve().parent == root]
    except OSError:
        return {}
    def natural(path):
        return tuple((1, int(s)) if s.isdigit() else (0, s.casefold())
                     for s in re.split(r"(\d+)", path.name))
    return {p.stem: p for p in sorted(files, key=natural)}


def sound_catalog() -> list[dict]:
    return [{"id": name, "label": name, "url": "/api/monitor/sounds/" + quote(name, safe="")}
            for name in sound_files()]


def default_sound(files: dict[str, Path]) -> str:
    return DEFAULT_COMPLETION_SOUND if DEFAULT_COMPLETION_SOUND in files else next(iter(files), DEFAULT_COMPLETION_SOUND)
