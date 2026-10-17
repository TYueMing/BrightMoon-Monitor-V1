"""Portable wallpaper references and bounded, orientation-aware image loading."""
from pathlib import Path

from PIL import Image, ImageOps

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_BYTES = 40 * 1024 * 1024
MAX_PIXELS = 40_000_000


def valid_reference(value):
    if not isinstance(value, str):
        return False
    if not value:
        return True
    parts = value.split("/")
    return (len(parts) == 2 and parts[0] == "backgrounds" and parts[1] not in {"", ".", ".."}
            and not any(c in value for c in "\\:") and not any(ord(c) < 32 for c in value)
            and Path(parts[1]).suffix.lower() in EXTENSIONS)


def read_photo(path, *, maximum=(2400, 2400)):
    """Read a local image once; callers retain the decoded image, not the file."""
    if path is None:
        raise ValueError("壁纸文件不存在")
    if Path(path).stat().st_size > MAX_BYTES:
        raise ValueError("请选择小于 40 MB 的照片")
    with Image.open(path) as image:
        if image.format not in {"JPEG", "PNG", "WEBP", "BMP"}:
            raise ValueError("请选择 JPG、PNG、WebP 或 BMP 照片")
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("照片分辨率过大，请缩小到 4000 万像素以内")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail(maximum, Image.Resampling.LANCZOS)
        return image
