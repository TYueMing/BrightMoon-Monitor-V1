"""Windows per-pixel alpha for PNG edges (Tk color keys create magenta halos)."""
import ctypes as ct
from ctypes import wintypes as wt
import os


class POINT(ct.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class SIZE(ct.Structure):
    _fields_ = [("cx", wt.LONG), ("cy", wt.LONG)]


class BLENDFUNCTION(ct.Structure):
    _fields_ = [("operation", wt.BYTE), ("flags", wt.BYTE),
                ("alpha", wt.BYTE), ("format", wt.BYTE)]


class BITMAPINFOHEADER(ct.Structure):
    _fields_ = [("size", wt.DWORD), ("width", wt.LONG), ("height", wt.LONG),
                ("planes", wt.WORD), ("bits", wt.WORD), ("compression", wt.DWORD),
                ("image_size", wt.DWORD), ("xppm", wt.LONG), ("yppm", wt.LONG),
                ("used", wt.DWORD), ("important", wt.DWORD)]


def premultiplied_bgra(image):
    """UpdateLayeredWindow expects color channels multiplied by source alpha."""
    result = bytearray(image.width*image.height*4)
    rgba = image.convert("RGBA").tobytes()
    for index in range(0,len(rgba),4):
        r,g,b,a = rgba[index:index+4]
        result[index:index+4] = bytes(((b*a+127)//255, (g*a+127)//255, (r*a+127)//255, a))
    return bytes(result)


class WindowsRopeSurface:
    """One reusable DIB/DC; no desktop capture or external rendering process."""
    def __init__(self, window):
        if os.name != "nt":
            raise OSError("Per-pixel window alpha requires Windows")
        self.window = window
        self.user = ct.WinDLL("user32", use_last_error=True)
        self.gdi = ct.WinDLL("gdi32", use_last_error=True)
        user, gdi = self.user, self.gdi
        user.GetAncestor.argtypes, user.GetAncestor.restype = [wt.HWND, wt.UINT], wt.HWND
        user.GetWindowLongW.argtypes, user.GetWindowLongW.restype = [wt.HWND, ct.c_int], wt.LONG
        user.SetWindowLongW.argtypes, user.SetWindowLongW.restype = [wt.HWND, ct.c_int, wt.LONG], wt.LONG
        user.UpdateLayeredWindow.argtypes = [wt.HWND,wt.HDC,ct.POINTER(POINT),ct.POINTER(SIZE),
            wt.HDC,ct.POINTER(POINT),wt.DWORD,ct.POINTER(BLENDFUNCTION),wt.DWORD]
        user.UpdateLayeredWindow.restype = wt.BOOL
        gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [wt.HDC],wt.HDC
        gdi.CreateDIBSection.argtypes = [wt.HDC,ct.c_void_p,wt.UINT,ct.POINTER(ct.c_void_p),wt.HANDLE,wt.DWORD]
        gdi.CreateDIBSection.restype = wt.HBITMAP
        gdi.SelectObject.argtypes, gdi.SelectObject.restype = [wt.HDC,wt.HGDIOBJ],wt.HGDIOBJ
        gdi.DeleteObject.argtypes, gdi.DeleteObject.restype = [wt.HGDIOBJ],wt.BOOL
        gdi.DeleteDC.argtypes, gdi.DeleteDC.restype = [wt.HDC],wt.BOOL
        window.update_idletasks()
        self.hwnd = None
        self.dc = self.bitmap = self.old = None
        self.image = None
        self._last_paint = None

    def set_image(self, image):
        if self.image is image:
            return
        self._release_bitmap()
        width,height = image.size
        header = BITMAPINFOHEADER(ct.sizeof(BITMAPINFOHEADER),width,-height,1,32,0,0,0,0,0,0)
        self.dc = self.gdi.CreateCompatibleDC(None)
        if not self.dc:
            raise ct.WinError(ct.get_last_error())
        pixels = ct.c_void_p()
        self.bitmap = self.gdi.CreateDIBSection(self.dc,ct.byref(header),0,ct.byref(pixels),None,0)
        if not self.bitmap:
            self._release_bitmap()
            raise ct.WinError(ct.get_last_error())
        self.old = self.gdi.SelectObject(self.dc,self.bitmap)
        ct.memmove(pixels,premultiplied_bgra(image),width*height*4)
        self.image = image

    def invalidate(self):
        """A remapped wrapper needs its pixels even if geometry/alpha match."""
        self._last_paint = None

    def paint(self,x,y,alpha):
        if self.image is None:
            return
        # Tk may replace its wrapper HWND when a transient toplevel first maps
        # or is lifted. Never keep painting the now-invisible former wrapper.
        self.window.update_idletasks()
        self.hwnd = self.user.GetAncestor(self.window.winfo_id(),2)
        style = self.user.GetWindowLongW(self.hwnd,-20)
        # Keep no-activate/tool-window behavior, but allow clicks on the PNG.
        # Windows still passes clicks through its fully transparent pixels.
        desired = (style | 0x00080000 | 0x08000000 | 0x00000080) & ~0x20
        if desired != style:
            self.user.SetWindowLongW(self.hwnd,-20,desired)
            self._last_paint = None
        target, source = POINT(x,y), POINT(0,0)
        size = SIZE(*self.image.size)
        blend = BLENDFUNCTION(0,0,round(max(0,min(1,alpha))*255),1)
        key = (self.hwnd,x,y,blend.alpha)
        if key == self._last_paint:
            return
        if not self.user.UpdateLayeredWindow(self.hwnd,None,ct.byref(target),ct.byref(size),
                self.dc,ct.byref(source),0,ct.byref(blend),2):
            raise ct.WinError(ct.get_last_error())
        self._last_paint = key

    def _release_bitmap(self):
        if self.old and self.dc:
            self.gdi.SelectObject(self.dc,self.old)
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap)
        if self.dc:
            self.gdi.DeleteDC(self.dc)
        self.dc = self.bitmap = self.old = None
        self.image = None
        self._last_paint = None

    def close(self):
        self._release_bitmap()
