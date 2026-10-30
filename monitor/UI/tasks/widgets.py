"""Small themed controls for the task editor; no native square button frame."""
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk
from ..focus import focus_path


class RoundedButton(tk.Canvas):
    def __init__(self, editor, x, y, label, command, *, fill, ink):
        self.scale, self.command = editor.app.scale, command
        self.fill, self.ink, self.label = fill, ink, label
        self.armed = self.hovered = False
        width, height = editor.s(94), editor.s(34)
        self.backdrop = editor.background.crop((editor.s(x), editor.s(y), editor.s(x)+width, editor.s(y)+height))
        super().__init__(editor, width=width, height=height, highlightthickness=0,
                         borderwidth=0, takefocus=True, cursor="hand2")
        self.font = editor.app.font
        editor.canvas.create_window(editor.s(x), editor.s(y), anchor="nw", window=self)
        self.bind("<Enter>", lambda _: self._hover(True))
        self.bind("<Leave>", lambda _: self._hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", lambda _: self.draw())
        self.bind("<FocusOut>", lambda _: self.draw())
        for key in ("Return", "space"):
            self.bind("<"+key+">", lambda _: self.invoke())
        self.draw()

    def draw(self):
        factor = 3
        width, height = self.backdrop.size
        image = self.backdrop.resize((width*factor, height*factor), Image.Resampling.BICUBIC)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width*factor-1, height*factor-1), radius=6*self.scale*factor,
                               fill=self.fill, outline=self.ink if focus_path(self)==str(self) or self.hovered else None,
                               width=max(1, round(self.scale*factor)))
        self.rendered = image.resize((width,height), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(self.rendered, master=self)
        self.delete("all")
        self.create_image(0, 0, image=self.photo, anchor="nw")
        self.create_text(width/2, height/2, text=self.label, fill=self.ink, font=self.font)

    def _hover(self, hovered):
        self.hovered = hovered
        self.draw()

    def _press(self, _):
        self.focus_set()
        self.armed = True

    def _release(self, event):
        armed, self.armed = self.armed, False
        if armed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        self.command()
        return "break"
