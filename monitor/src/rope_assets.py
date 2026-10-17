"""Local PNG pendant catalog. Paths remain portable within Solo's data/rope."""
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps, UnidentifiedImageError


def valid_rope_reference(value):
    if not isinstance(value, str):
        return False
    if not value:
        return True
    path = PurePosixPath(value)
    return (len(path.parts) == 2 and path.parts[0] == "rope"
            and path.suffix.lower() == ".png" and "\\" not in value
            and ":" not in value and value == path.as_posix())


class RopeCatalog:
    def __init__(self, data_root):
        self.root = Path(data_root).resolve()
        self._images = {}

    def path(self, reference):
        if not reference or not valid_rope_reference(reference):
            raise ValueError("请选择 data/rope 中的 PNG 图像")
        path = (self.root / reference).resolve()
        if not path.is_relative_to(self.root / "rope") or not path.is_file():
            raise ValueError("悬挂卡通文件不存在或路径无效")
        return path

    def read(self, reference):
        path = self.path(reference)
        stat = path.stat()
        key = (reference, stat.st_mtime_ns, stat.st_size)
        if key not in self._images:
            with Image.open(path) as source:
                if source.format != "PNG" or source.width * source.height > 16_000_000:
                    raise ValueError("悬挂卡通应为不超过 1600 万像素的 PNG")
                image = ImageOps.exif_transpose(source).convert("RGBA")
                box = image.getchannel("A").getbbox()
                if box is None:
                    raise ValueError("悬挂卡通图像完全透明")
                # Trim unused padding so the rope touches the capsule edge.
                image = image.crop(box)
                image.thumbnail((512, 768), Image.Resampling.LANCZOS)
            self._images = {k: v for k, v in self._images.items() if k[0] != reference}
            self._images[key] = image
        return self._images[key]

    def list(self):
        try:
            names = sorted((p.name for p in (self.root / "rope").iterdir()
                            if p.suffix.lower() == ".png"), key=str.casefold)
        except OSError:
            return []
        result = []
        for name in names:
            reference = "rope/" + name
            try:
                self.read(reference)
            except (OSError, ValueError, RuntimeError, UnidentifiedImageError, Image.DecompressionBombError):
                continue
            result.append(reference)
        self._images = {k: v for k, v in self._images.items() if k[0] in result}
        return result

    @staticmethod
    def label(reference):
        return PurePosixPath(reference).stem
