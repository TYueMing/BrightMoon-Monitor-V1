"""User-owned cutout artwork. Imports never reference external files afterwards."""
import hashlib
import os
from pathlib import Path, PurePosixPath
from uuid import uuid4

from PIL import Image, ImageOps

EXTENSIONS = {".png", ".webp"}


def valid_cartoon_reference(value):
    if not isinstance(value, str):
        return False
    if not value:
        return True
    path = PurePosixPath(value)
    return (len(path.parts)==2 and path.parts[0]=="cartoon"
            and path.suffix.lower() in EXTENSIONS and "\\" not in value
            and ":" not in value and value==path.as_posix())


def read_cutout(path):
    if path.stat().st_size > 40*1024*1024:
        raise ValueError("请选择小于 40 MB 的抠图")
    with Image.open(path) as source:
        if source.format not in {"PNG", "WEBP"} or source.width*source.height > 16_000_000:
            raise ValueError("请选择不超过 1600 万像素的 PNG 或 WebP 抠图")
        image = ImageOps.exif_transpose(source).convert("RGBA")
        alpha = image.getchannel("A")
        bounds = alpha.getbbox()
        if bounds is None or alpha.getextrema()[0] == 255:
            raise ValueError("请上传已抠图、带透明背景的图像；不能完全透明")
        image = image.crop(bounds)
        image.thumbnail((768,768),Image.Resampling.LANCZOS)
        return image


class CartoonCatalog:
    def __init__(self, data_root):
        self.root = Path(data_root).resolve()
        self._images = {}

    def path(self, reference):
        if not reference or not valid_cartoon_reference(reference):
            raise ValueError("请选择 data/cartoon 中的图像")
        path = (self.root/reference).resolve()
        if not path.is_relative_to(self.root/"cartoon") or not path.is_file():
            raise ValueError("空闲角色文件不存在或路径无效")
        return path

    def read(self, reference):
        path = self.path(reference)
        stat = path.stat()
        key = (reference,stat.st_mtime_ns,stat.st_size)
        if key not in self._images:
            image = read_cutout(path)
            self._images = {k:v for k,v in self._images.items() if k[0]!=reference}
            self._images[key] = image
        return self._images[key]

    def list(self):
        try:
            names = sorted((p.name for p in (self.root/"cartoon").iterdir()
                            if p.suffix.lower() in EXTENSIONS),key=str.casefold)
        except OSError:
            return []
        result = []
        for name in names:
            reference = "cartoon/"+name
            try:
                self.read(reference)
            except (OSError,ValueError,RuntimeError,Image.DecompressionBombError):
                continue
            result.append(reference)
        self._images = {k:v for k,v in self._images.items() if k[0] in result}
        return result

    def import_image(self, source):
        source = Path(source)
        image = read_cutout(source)  # Validate before copying or changing selection.
        folder = self.root/"cartoon"
        folder.mkdir(parents=True,exist_ok=True)
        if not folder.resolve().is_relative_to(self.root):
            raise ValueError("空闲角色目录不能指向数据目录外")
        # Keep the user's name; collisions get a content suffix, never overwrite.
        name = source.stem[:80]
        if any(c in name for c in '<>:"/\\|?*') or name.rstrip(". ")!=name or not name:
            name = "自定义角色"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:10]
        reference = f"cartoon/{name}-{digest}.png"
        destination = self.root/reference
        if destination.exists():
            self.read(reference)
            return reference
        temp = folder/f"import-{uuid4().hex}.tmp"
        try:
            image.save(temp,format="PNG")
            os.replace(temp,destination)
        finally:
            temp.unlink(missing_ok=True)
        return reference

    @staticmethod
    def label(reference):
        return PurePosixPath(reference).stem
