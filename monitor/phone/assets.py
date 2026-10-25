"""Image IDs resolve only to explicitly selected, re-encoded local resources."""
import hashlib
import io
from pathlib import Path
from PIL import Image

from ..src.app_sources import ICON_ROOT

ICONS = {"codex": "Codex_logo.png", "deepseek_harness": "Deepseek_logo.png",
         "opencode": "Opencode_logo.png", "claude": "claude_logo.png", "solo_task": "LOGO_EXE.png", "unknown": "LOGO.png"}


def icon(name):
    filename = ICONS.get(name)
    return (ICON_ROOT/filename).read_bytes() if filename else None


def selected_images(root, preferences):
    """Runs off the Tk thread; no ImageTk objects or arbitrary request paths."""
    result, ids = {}, {}
    for kind, field, directory, enabled in (
        ("page_wallpaper", "background_photo", "backgrounds", preferences.get("background_mode") == "photo"),
        ("wallpaper", "monitor_background_photo", "backgrounds", preferences.get("monitor_background_mode") == "photo"),
        ("rope", "rope_image", "rope", preferences.get("rope_enabled")),
        ("cartoon", "cartoon_image", "cartoon", preferences.get("cartoon_enabled")),
    ):
        ref = preferences.get(field)
        if not enabled or not isinstance(ref, str) or not ref:
            continue
        try:
            expected = Path(root).resolve()/directory
            path = (Path(root)/ref).resolve()
            # Resolving the expected directory itself would bless a junction.
            if not path.is_relative_to(expected) or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} or path.stat().st_size > 40*1024*1024:
                continue
            with Image.open(path) as original:
                if original.width*original.height > 16_000_000:
                    continue
                image = original.convert("RGBA")
                image.thumbnail((1280, 1280))
                output = io.BytesIO()
                image.save(output, "WEBP", quality=80)
            body = output.getvalue()
            if len(body) > 1024*1024:
                continue
            key = hashlib.sha256(body).hexdigest()
            result[key] = body
            ids[kind] = key
        except (OSError, ValueError, RuntimeError, Image.DecompressionBombError):
            continue
    return result, ids
