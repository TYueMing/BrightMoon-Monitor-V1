"""A bounded, keyboard-accessible local wallpaper gallery for either surface."""
import tkinter as tk
from math import ceil

from .wallpapers import WallpaperThumbnails


class WallpaperPicker(tk.Toplevel):
    PAGE_SIZE = 6

    def __init__(self, app, target):
        super().__init__(app.root)
        self.app, self.target, self.page = app, target, 0
        self.paths = app.store.list_backgrounds()
        self.thumbnails = WallpaperThumbnails(self, app.store)
        self.images = []
        self.title("灵动岛壁纸" if target == "island" else "历史壁纸")
        self.configure(bg="white")
        self.transient(app.root)
        self.resizable(False, False)
        body = tk.Frame(self, bg="white", padx=app.s(20), pady=app.s(18))
        body.pack()
        app.label(body, "灵动岛壁纸" if target == "island" else "历史壁纸", font=app.bold).pack(anchor="w")
        app.label(body, "选择已保存的照片，也可以导入新的壁纸。", font=app.small, color="#697589").pack(anchor="w", pady=app.s(8))
        self.grid_frame = tk.Frame(body, bg="white", width=app.s(526), height=app.s(278))
        self.grid_frame.pack()
        self.grid_frame.pack_propagate(False)
        self.grid_frame.grid_propagate(False)
        nav = tk.Frame(body, bg="white")
        nav.pack(fill="x", pady=app.s(8))
        self.prev = app.button(nav, "上一页", lambda:self.move(-1))
        self.prev.pack(side="left")
        self.page_label = app.label(nav, "", font=app.small)
        self.page_label.pack(side="left", expand=True)
        self.next = app.button(nav, "下一页", lambda:self.move(1))
        self.next.pack(side="right")
        footer = tk.Frame(body, bg="white")
        footer.pack(fill="x")
        app.button(footer, "导入照片…", self.import_photo, accent=True).pack(side="left")
        app.button(footer, "关闭", app.close_wallpaper_dialog).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", app.close_wallpaper_dialog)
        self.bind("<Escape>", lambda _:app.close_wallpaper_dialog())
        self.bind("<MouseWheel>", lambda e:self.move(-1 if e.delta > 0 else 1))
        self.draw_page()
        app.center_dialog(self)
        self.grab_set()

    def move(self, direction):
        self.page = max(0, min(max(0, ceil(len(self.paths)/self.PAGE_SIZE)-1), self.page+direction))
        self.draw_page()

    def draw_page(self):
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.images.clear()
        prefs = self.app.store.snapshot()
        prefix = "background" if self.target == "app" else "monitor_background"
        current = prefs[prefix+"_photo"] if prefs[prefix+"_mode"] == "photo" else ""
        page_paths = self.paths[self.page*self.PAGE_SIZE:(self.page+1)*self.PAGE_SIZE]
        for index, relative in enumerate(page_paths):
            photo = self.thumbnails.get(relative, (self.app.s(154), self.app.s(88)))
            self.images.append(photo)
            label = ("已选择" if relative == current else f"壁纸 {self.page*self.PAGE_SIZE+index+1}") if photo else "文件不可读取"
            button = tk.Button(self.grid_frame, image=photo, text=label, compound="top",
                font=self.app.small, bg="#eaf0ff" if relative == current else "#f3f5f9",
                fg="#426de7" if relative == current else "#1c2940", relief="flat", bd=0,
                padx=self.app.s(6), pady=self.app.s(6), highlightthickness=1,
                highlightbackground="#426de7" if relative == current else "#e3e8f1",
                command=lambda p=relative:self.choose(p), state="normal" if photo else "disabled")
            button.grid(row=index//3, column=index%3, padx=self.app.s(5), pady=self.app.s(6), sticky="nsew")
        if not page_paths:
            self.app.label(self.grid_frame, "还没有历史壁纸，点击下方导入照片。", color="#697589").place(relx=.5,rely=.5,anchor="center")
        pages = max(1, ceil(len(self.paths)/self.PAGE_SIZE))
        self.page_label.configure(text=f"{self.page+1} / {pages} · 共 {len(self.paths)} 张")
        self.prev.configure(state="normal" if self.page else "disabled")
        self.next.configure(state="normal" if self.page+1 < pages else "disabled")

    def choose(self, relative):
        if self.app.select_background(relative, target=self.target):
            self.app.close_wallpaper_dialog()

    def import_photo(self):
        if self.app.choose_photo(target=self.target, parent=self):
            self.app.close_wallpaper_dialog()
