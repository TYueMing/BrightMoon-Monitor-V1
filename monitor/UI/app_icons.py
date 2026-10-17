"""DPI-sized circular app icons, cached for the lifetime of one Tk island."""
from PIL import Image, ImageDraw, ImageOps, ImageTk
from ..src.app_sources import app_source


def circular_icon(path, pixels):
    size = max(1, int(pixels))
    # Mask at 4x before downsampling for a smooth edge at native pixel density.
    with Image.open(path) as source:
        image = ImageOps.fit(source.convert("RGBA"), (size*4, size*4), method=Image.Resampling.LANCZOS)
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size*4-1, size*4-1), fill=255)
    from PIL import ImageChops
    image.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
    return image.resize((size, size), Image.Resampling.LANCZOS)


class AppIconCache:
    def __init__(self, master):
        self.master, self.images = master, {}

    def get(self, app_id, pixels):
        source = app_source(app_id)
        key = (source.app_id, source.icon, int(pixels))
        if key not in self.images:
            try:
                self.images[key] = ImageTk.PhotoImage(circular_icon(source.icon, pixels), master=self.master) if source.icon else None
            except (OSError, ValueError):
                self.images[key] = None
        return self.images[key]
