"""DPI-aware native vector drawing. Coordinates stay in 96-DPI units."""

import os
import tkinter as tk


def enable_dpi_awareness() -> None:
    """Call once in the UI process, before creating any Tk windows."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    try:
        setter = user.SetProcessDpiAwarenessContext
        setter.argtypes = (ctypes.c_void_p,)
        setter.restype = wintypes.BOOL
        if setter(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user.SetProcessDPIAware()


def window_scale(root) -> float:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        try:
            user = ctypes.WinDLL("user32")
            user.GetDpiForWindow.argtypes = (wintypes.HWND,)
            user.GetDpiForWindow.restype = wintypes.UINT
            dpi = user.GetDpiForWindow(root.winfo_id())
            return max(1.0, dpi / 96.0) if dpi else 1.0
        except (AttributeError, OSError):
            pass
    return max(1.0, root.winfo_fpixels("1i") / 96.0) if os.name != "nt" else 1.0


def screen_bounds(root):
    """Primary display work area, in native pixels (including taskbar offsets)."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


class ScaledCanvas(tk.Canvas):
    def __init__(self, master, scale=1.0, **kwargs):
        self.scale = scale
        self.palette = {}
        self.offset_y = 0
        super().__init__(master, **kwargs)

    def _create(self, itemType, args, kw):
        # Canvas internals flatten nested point lists before creating items;
        # fonts are already rasterized at native pixel size, never scaled as a bitmap.
        coordinates = tk._flatten(args)
        if coordinates and isinstance(coordinates[-1], dict):
            options = dict(coordinates[-1])
            coordinates = coordinates[:-1]
            options.update(kw)
            kw = options
        scaled = tuple((float(value)+(self.offset_y if index % 2 else 0))*self.scale for index,value in enumerate(coordinates))
        kw = dict(kw)
        for key in ("fill", "outline"):
            if key in kw:
                kw[key] = self.palette.get(kw[key], kw[key])
        if "width" in kw:
            kw["width"] = float(kw["width"])*self.scale
        return super()._create(itemType, scaled, kw)
