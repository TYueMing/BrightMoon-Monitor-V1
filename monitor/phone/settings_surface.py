"""Scrollable settings text over the exact parent wallpaper composite.

Tk Frames cannot be transparent. A viewport-aligned background crop lets this
embedded Canvas share the main panel's tint without tinting its text or inputs.
"""
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk


class SurfaceLabel:
    def __init__(self, canvas, item):
        self.canvas, self.item = canvas, item

    def configure(self, **kwargs):
        self.canvas.itemconfigure(self.item, **kwargs)


class SettingsSurface:
    def __init__(self, parent, scale):
        self.scale = scale
        self.foreground = "#26364b"
        self.canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0,
                                yscrollincrement=max(1, round(18*scale)))
        self.scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.canvas.yview)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True)
        self.background = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrolled)
        self.canvas.bind("<Configure>", self.resized)
        self.backdrop = None
        self.crop_key = None
        self.origin = (0, 0)
        self.extent = (1, 1)
        self.height = 650
        self.widgets = {}

    def label(self, x, y, text, size=12, bold=False, width=485, group="content"):
        item = self.canvas.create_text(x*self.scale, y*self.scale, anchor="nw", text=text,
            width=width*self.scale, fill=self.foreground,
            font=("Microsoft YaHei UI", -round(size*self.scale), "bold" if bold else "normal"),
            tags=("text", group))
        return SurfaceLabel(self.canvas, item)

    def widget(self, widget, x, y, width, group="content"):
        self.canvas.create_window(x*self.scale, y*self.scale, anchor="nw", window=widget,
                                 width=round(width*self.scale), height=round(30*self.scale), tags=(group,))
        self.widgets.setdefault(group, []).append(widget)
        return widget

    def button(self, x, y, width, text, command, group="content"):
        return self.widget(ttk.Button(self.canvas, text=text, command=command, style="Phone.TButton"),
                           x, y, width, group)

    def checkbox(self, x, y, text, variable):
        # Keep a native keyboard-accessible check box; its caption is transparent.
        check = ttk.Checkbutton(self.canvas, variable=variable)
        self.widget(check, x, y-5, 22)
        caption = self.label(x+28, y, text)
        self.canvas.tag_bind(caption.item, "<Button-1>", lambda _: check.invoke())

    def clear_group(self, group):
        for widget in self.widgets.pop(group, []):
            widget.destroy()
        self.canvas.delete(group)

    def set_theme(self, background, foreground):
        self.foreground = foreground
        self.canvas.configure(bg=background)
        self.canvas.itemconfigure("text", fill=foreground)

    def set_height(self, height):
        self.height = height
        self.canvas.configure(scrollregion=(0, 0, 0, height*self.scale))

    def set_backdrop(self, image, origin, extent):
        self.backdrop, self.origin, self.extent = image, origin, extent
        self.paint_backdrop()

    def resized(self, event):
        self.extent = (event.width, event.height)
        self.paint_backdrop()

    def paint_backdrop(self):
        if self.backdrop is None:
            self.canvas.itemconfigure(self.background, image="")
            self.crop_key = None
            return
        x, y = self.origin
        width, height = self.extent
        key = (id(self.backdrop), x, y, width, height)
        if key != self.crop_key:
            self.crop_key = key
            self.crop = self.backdrop.crop((x, y, x+width, y+height))
            self.photo = ImageTk.PhotoImage(self.crop, master=self.canvas)
            self.canvas.itemconfigure(self.background, image=self.photo)
            self.canvas.tag_lower(self.background)
        self.pin_background()

    def pin_background(self):
        # Scroll content, not wallpaper: retain pixel alignment with the parent.
        self.canvas.coords(self.background, self.canvas.canvasx(0), self.canvas.canvasy(0))

    def scrolled(self, first, last):
        self.scrollbar.set(first, last)
        self.pin_background()
