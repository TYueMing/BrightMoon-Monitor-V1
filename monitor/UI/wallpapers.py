"""Shared thumbnail cache and a top-anchored wallpaper behind the island UI."""
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageOps, ImageTk

from ..src.wallpapers import read_photo


class WallpaperThumbnails:
    def __init__(self, master, store):
        self.master, self.store = master, store
        self.cache = OrderedDict()

    def get(self, relative, size):
        path = self.store.background_path(relative)
        if path is None:
            return None
        try:
            stat = path.stat()
            key = (relative, stat.st_mtime_ns, stat.st_size, size)
            if key not in self.cache:
                photo = read_photo(path, maximum=(640, 640))
                photo = ImageOps.fit(photo, size, method=Image.Resampling.LANCZOS)
                self.cache[key] = ImageTk.PhotoImage(photo, master=self.master)
                while len(self.cache) > 32:
                    self.cache.popitem(last=False)
            self.cache.move_to_end(key)
            return self.cache[key]
        except (OSError, ValueError, Image.DecompressionBombError):
            return None


class IslandWallpaper:
    """The animation reveals a fixed image; changing height never rescales it.

    Prepare one full-size scene and crop from its top for every frame. A compact
    capsule shows the upper strip, an expanded card reveals more, and details
    reveal most of the scene. Horizontal changes crop symmetrically. Only the
    window's existing alpha changes transparency, equally in all three states.
    """
    def __init__(self):
        self.source = None
        self.scene_key = self.frame_key = None
        self.scene = self.frame = None

    def render(self, photo, size, *, scale, radius, color, scene_height=580):
        if photo is None:
            self.source = self.scene = self.frame = None
            self.scene_key = self.frame_key = None
            return None
        width, height = size
        scene_size = (round(480*scale), round(max(360, scene_height)*scale))
        key = (id(photo), scene_size, color)
        if key != self.scene_key:
            self.source = photo  # Keep its identity alive between redraws.
            scene = ImageOps.fit(photo, scene_size, method=Image.Resampling.LANCZOS, centering=(.5, 0))
            # A constant tint preserves readable text without changing brightness
            # or transparency as the user opens/collapses the card.
            self.scene = Image.blend(scene.convert("RGB"), Image.new("RGB", scene_size, color), .60)
            self.scene_key, self.frame_key = key, None
        frame_key = (key, size, radius)
        if frame_key != self.frame_key:
            left = (self.scene.width-width)//2
            self.frame = self.scene.crop((left, 0, left+width, height)).convert("RGBA")
            # Extra-tall provider cards/animation overshoot reveal an extended
            # edge, never stretch or shift the already-visible image.
            if height > self.scene.height:
                strip = self.scene.crop((max(0,left), self.scene.height-1,
                                         min(self.scene.width,left+width), self.scene.height))
                self.frame.paste(strip.resize((width, height-self.scene.height)), (0, self.scene.height))
            mask = Image.new("L", size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, width-1, height-1), radius=radius, fill=255)
            self.frame.putalpha(mask)
            self.frame_key = frame_key
        return self.frame
