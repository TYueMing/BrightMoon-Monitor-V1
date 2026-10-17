"""A child decoration window; it never changes provider layout or agent state."""
from dataclasses import dataclass
import math
import time
import tkinter as tk

from PIL import Image, ImageTk
from .rope_surface import WindowsRopeSurface
from .display import screen_bounds

DELAY_SECONDS = .2
FADE_SECONDS = .2
PULL_SECONDS = .48
PULL_DISTANCE = 4
RETRY_SECONDS = .25
MAX_RETRY_SECONDS = 2.
TRANSPARENT = "#ed01ec"


def horizontal_center(left, width, image_width, scale, position):
    """Place the image edges (not just its anchor) 20 logical px inside."""
    travel = max(0, width - 40*scale - image_width)
    if width < 40*scale + image_width:
        return left + width/2
    return left + 20*scale + image_width/2 + travel*min(100,max(0,position))/100


@dataclass(frozen=True)
class RopeFrame:
    alpha: float = 0
    offset: float = 0
    finished: bool = False


class RopeMotion:
    """Monotonic, testable timing independent of Tk's repaint frequency."""
    def __init__(self):
        self.state = "hidden"
        self.at = 0.
        self.pull_alpha = 1.

    def hide(self):
        self.state = "hidden"

    def reveal(self, now):
        if self.state == "hidden":
            self.state, self.at = "reveal", now

    def pull(self, now):
        frame = self.frame(now)
        if self.state == "pull":
            return True
        if frame.alpha <= 0:
            return False
        self.state, self.at, self.pull_alpha = "pull", now, frame.alpha
        return True

    def frame(self, now):
        elapsed = max(0., now - self.at)
        if self.state == "reveal":
            progress = min(1., max(0., (elapsed - DELAY_SECONDS) / FADE_SECONDS))
            # Smooth endpoints avoid a perceptible jump on the first fade frame.
            return RopeFrame(progress * progress * (3 - 2 * progress))
        if self.state == "pull":
            progress = min(1., elapsed / PULL_SECONDS)
            return RopeFrame(self.pull_alpha,
                             PULL_DISTANCE * (1 - math.cos(4 * math.pi * progress)) / 2,
                             progress >= 1.)
        return RopeFrame()


class RopePendant:
    """Owned transparent surface with independent fade, cancelled on hide/close.

    Keeping it outside the island canvas avoids extra blank space in the card,
    changing approval hit regions, or fading the text along with the pendant.
    """
    def __init__(self, owner, *, on_activate=None):
        self.owner = owner
        self.on_activate = on_activate
        self._press_origin = None
        self.window = None
        self.image = self.photo = None
        self.motion = RopeMotion()
        self.job = None
        self.callback = None
        self.closed = False
        self.unavailable = False
        self._retry_at = 0.
        self._failures = 0
        self._map_requested_at = None
        self.key = None
        self.scale = 1.
        self.opacity = 1.
        self.alpha = 0.
        self.surface = None
        self.rendered_image = None
        self.anchor = (0, 0)

    def set_image(self, image):
        if image is self.image:
            return
        self.hide()
        self.image = image
        self.key = None
        self.unavailable = False
        self._retry_at = 0.
        self._failures = 0

    def _create(self):
        if self.window is not None:
            return True
        window = self.window = tk.Toplevel(self.owner)
        try:
            window.withdraw()
            window.overrideredirect(True)
            window.transient(self.owner)
            window.attributes("-topmost", True)
            window.configure(bg=TRANSPARENT)
            try:
                self.surface = WindowsRopeSurface(window)
            except (OSError, AttributeError):
                window.attributes("-transparentcolor", TRANSPARENT)
                window.attributes("-alpha", 0)
        except (OSError, tk.TclError):
            # Construction may fail after allocating the native toplevel.
            # Dispose it before retrying so no invisible orphan is retained.
            self._dispose_window()
            raise
        self.canvas = tk.Canvas(window, bg=TRANSPARENT, highlightthickness=0,
                                borderwidth=0, takefocus=False)
        self.canvas.pack(fill="both", expand=True)
        # Bind on the toplevel so native layered-window messages and the Tk
        # canvas fallback take the same path, without duplicate callbacks.
        window.bind("<ButtonPress-1>", self._press)
        window.bind("<ButtonRelease-1>", self._release)
        window.bind("<B1-Motion>", self._motion)
        window.bind("<Motion>", self._hover)
        return True

    def _hit(self, event):
        image = self.rendered_image
        if (self.closed or self.unavailable or self.alpha <= 0 or image is None
                or self.window is None or self.window.state() == "withdrawn"):
            return False
        x,y = int(event.x),int(event.y)
        return (0 <= x < image.width and 0 <= y < image.height
                and image.getpixel((x,y))[3] > 0)

    def _press(self, event):
        self._press_origin = (event.x_root,event.y_root) if self._hit(event) else None

    def _motion(self, event):
        if self._press_origin:
            x,y = self._press_origin
            if max(abs(event.x_root-x),abs(event.y_root-y)) > 5*self.scale:
                self._press_origin = None

    def _release(self, event):
        self._motion(event)
        pressed = self._press_origin is not None
        self._press_origin = None
        if pressed and self._hit(event) and self.on_activate:
            self.on_activate()

    def _hover(self, event):
        cursor = "hand2" if self._hit(event) and self.on_activate else ""
        self.window.configure(cursor=cursor)
        self.canvas.configure(cursor=cursor)

    def sync(self, *, ready, anchor, scale, opacity, horizontal_bounds=None, position=50):
        self.anchor, self.scale, self.opacity = anchor, scale, opacity
        # Keep the cartoon below the capsule even near the taskbar. Shrink to
        # the available space, or omit it if that would make it unreadable.
        available = screen_bounds(self.owner)[3]-anchor[1]-PULL_DISTANCE*scale
        height_limit = min(round(76*scale),int(available))
        ready = ready and height_limit >= round(24*scale)
        if self.closed or self.image is None or not ready or opacity <= 0:
            self.hide()
            return
        # run.py restores the image before the island's first Map event. Tk can
        # leave a transient created against that withdrawn owner "normal" but
        # unmapped forever. Create it only after the owner is actually visible.
        if not self.owner.winfo_viewable():
            self.hide()
            return
        if self.unavailable:
            if time.monotonic() < self._retry_at:
                return
            self.unavailable = False
        try:
            created = self._create()
        except (OSError,tk.TclError):
            created = False
        if not created:
            self._disable()
            return
        key = (id(self.image), scale, height_limit)
        if self.key != key:
            # 76 logical pixels keeps the cartoon legible without dwarfing a
            # 62px capsule. Original aspect and alpha are preserved.
            frame = self.image.copy()
            frame.thumbnail((round(80*scale), height_limit), Image.Resampling.LANCZOS)
            self.rendered_image = frame
            if self.surface:
                try:
                    self.surface.set_image(frame)
                except (OSError,tk.TclError):
                    self._disable()
                    return
            else:
                # Color-key fallback cannot composite fractional source alpha.
                # Binary edges avoid mixing the magenta key into the artwork.
                frame = frame.copy()
                frame.putalpha(frame.getchannel("A").point(lambda value:255 if value>=128 else 0))
                self.photo = ImageTk.PhotoImage(frame, master=self.window)
            self.size = frame.size
            self.canvas.delete("all")
            if not self.surface:
                self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
            self.key = key
        if horizontal_bounds:
            self.anchor = (horizontal_center(*horizontal_bounds, self.size[0], scale, position), anchor[1])
        self.motion.reveal(time.monotonic())
        self._paint()

    def request_pull(self, callback):
        if self.closed or self.image is None or self.window is None:
            return False
        if self.motion.state == "pull":
            self.callback = callback
            return True  # A double-click must not queue another expansion.
        if not self.motion.pull(time.monotonic()):
            return False
        self.callback = callback
        self._paint()
        return True

    def _paint(self):
        try:
            self._paint_frame()
        except (OSError,tk.TclError):
            # Decorations must never stop the polling loop or strand a user's
            # request to open an approval card when a graphics resource fails.
            callback = self.callback
            self._disable()
            if callback and not self.closed:
                callback()

    def _disable(self):
        self.hide()
        self.unavailable = True
        self._failures = min(self._failures + 1, 4)
        self._retry_at = time.monotonic() + min(MAX_RETRY_SECONDS, RETRY_SECONDS * 2**(self._failures-1))
        self._dispose_window()

    def _dispose_window(self):
        if self.surface:
            self.surface.close()
            self.surface = None
        if self.window is not None:
            self.window.destroy()
            self.window = None
        self.photo = self.rendered_image = None
        self.key = None
        self._map_requested_at = None

    def _paint_frame(self):
        if self.closed or self.window is None or self.motion.state == "hidden":
            return
        if self.job:
            self.owner.after_cancel(self.job)
            self.job = None
        if not self.owner.winfo_viewable():
            self.hide()
            return
        frame = self.motion.frame(time.monotonic())
        if frame.finished:
            callback = self.callback
            self.hide()
            if callback:
                callback()
            return
        width, height = self.size
        x = round(self.anchor[0] - width/2)
        y = round(self.anchor[1] + frame.offset*self.scale)
        self.window.geometry(f"{width}x{height}{x:+d}{y:+d}")
        self.alpha = frame.alpha*self.opacity
        if frame.alpha > 0:
            # Tk's WM state alone is insufficient: it can say "normal" while
            # the window has not mapped. Retrying this must not restart the fade.
            if not self.window.winfo_viewable():
                if self._map_requested_at is None:
                    self._map_requested_at = time.monotonic()
                if self.surface:
                    self.surface.invalidate()
                self.window.deiconify()
                self.window.update_idletasks()
                if not self.window.winfo_viewable():
                    if time.monotonic() - self._map_requested_at >= 1.:
                        raise OSError("Pendant window did not map")
                    self.job = self.owner.after(16, self._paint)
                    return
            self._map_requested_at = None
            if self.surface:
                self.surface.paint(x,y,self.alpha)
            else:
                self.window.attributes("-alpha", self.alpha)
            self._failures = 0
        else:
            self.window.withdraw()
            # Do not send UpdateLayeredWindow to an unmapped startup wrapper.
            # It may be replaced on Map and its cached pixels are then lost.
            if self.surface:
                self.surface.invalidate()
        if frame.alpha < 1 or self.motion.state == "pull":
            self.job = self.owner.after(16, self._paint)

    def hide(self):
        self._press_origin = None
        if self.job:
            self.owner.after_cancel(self.job)
            self.job = None
        self.callback = None
        self.motion.hide()
        self._map_requested_at = None
        self.alpha = 0.
        if self.window is not None:
            self.window.withdraw()

    def close(self):
        if self.closed:
            return
        self.hide()
        self.closed = True
        self._dispose_window()
        self.photo = self.image = self.rendered_image = None
        self.on_activate = None
