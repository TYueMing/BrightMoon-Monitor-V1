"""Render fixture-only wallpaper/settings previews without touching user data."""
from datetime import datetime, timezone
from pathlib import Path
import os
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import Mock

from PIL import Image, ImageDraw, ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor.app import MonitorApp
from monitor.settings import SettingsStore
from monitor.UI.display import enable_dpi_awareness


def artwork(path, variation=0):
    image = Image.new("RGB", (640,800))
    draw = ImageDraw.Draw(image)
    for y in range(800):
        t = y/800
        draw.line((0,y,640,y),fill=(int(105+75*t),int(143+35*t),int(188+15*t)))
    draw.ellipse((420,82,498,160),fill="#f8e6bc")
    draw.polygon([(0,530),(145,280+variation*20),(335,525),(465,355),(640,540),(640,800),(0,800)],fill="#728b9b")
    draw.polygon([(0,650),(250,430+variation*10),(400,605),(540,460),(640,600),(640,800),(0,800)],fill="#466776")
    draw.polygon([(0,755),(170,610),(420,745),(550,670),(640,720),(640,800),(0,800)],fill="#254857")
    image.save(path)


def settle(app):
    app.root.update()
    for job in app.island._jobs.values():
        app.island.root.after_cancel(job)
    app.island._jobs.clear()
    app.island._animation = None
    app.island._opening = False


def capture(window):
    window.update()
    window.lift()
    time.sleep(.12)
    if os.name == "nt":
        # Capture only this test window. Desktop screenshots can accidentally
        # include a taskbar tooltip or another application covering it.
        import ctypes as ct
        from ctypes import wintypes as wt
        user, gdi = ct.windll.user32, ct.windll.gdi32
        user.GetDC.argtypes, user.GetDC.restype = [wt.HWND], wt.HDC
        user.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        user.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
        gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [wt.HDC], wt.HDC
        gdi.CreateCompatibleBitmap.argtypes, gdi.CreateCompatibleBitmap.restype = [wt.HDC,ct.c_int,ct.c_int], wt.HBITMAP
        gdi.SelectObject.argtypes, gdi.SelectObject.restype = [wt.HDC,wt.HGDIOBJ], wt.HGDIOBJ
        gdi.DeleteObject.argtypes = [wt.HGDIOBJ]
        gdi.DeleteDC.argtypes = [wt.HDC]
        class Header(ct.Structure):
            _fields_ = [("size",wt.DWORD),("width",wt.LONG),("height",wt.LONG),
                        ("planes",wt.WORD),("bits",wt.WORD),("compression",wt.DWORD),
                        ("image_size",wt.DWORD),("xppm",wt.LONG),("yppm",wt.LONG),
                        ("used",wt.DWORD),("important",wt.DWORD)]
        gdi.GetDIBits.argtypes = [wt.HDC,wt.HBITMAP,wt.UINT,wt.UINT,ct.c_void_p,ct.c_void_p,wt.UINT]
        width,height = window.winfo_width(),window.winfo_height()
        hwnd = window.winfo_id()
        dc = user.GetDC(hwnd)
        memory = gdi.CreateCompatibleDC(dc)
        bitmap = gdi.CreateCompatibleBitmap(dc,width,height)
        old = gdi.SelectObject(memory,bitmap)
        try:
            if not user.PrintWindow(hwnd,memory,3):
                raise RuntimeError("Could not capture fixture window")
            gdi.SelectObject(memory,old)
            header = Header(ct.sizeof(Header),width,-height,1,32,0,0,0,0,0,0)
            buffer = ct.create_string_buffer(width*height*4)
            if not gdi.GetDIBits(dc,bitmap,0,height,buffer,ct.byref(header),0):
                raise RuntimeError("Could not read fixture pixels")
            return Image.frombytes("RGB",(width,height),buffer.raw,"raw","BGRX")
        finally:
            gdi.SelectObject(memory,old)
            gdi.DeleteObject(bitmap)
            gdi.DeleteDC(memory)
            user.ReleaseDC(hwnd,dc)
    x,y = window.winfo_rootx(),window.winfo_rooty()
    return ImageGrab.grab(bbox=(x,y,x+window.winfo_width(),y+window.winfo_height())).convert("RGB")


def main():
    enable_dpi_awareness()
    output = Path(__file__).resolve().parents[1]/"preview"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="solo-wallpaper-preview-") as temp:
        work = Path(temp)
        store = SettingsStore(work/"data")
        for index in range(8):
            path = work/f"mountains-{index}.png"
            artwork(path,index)
            store.import_photo(path)
        store.select_photo(store.list_backgrounds()[0],target="island")
        store.update(monitor_sound_enabled=False,panel_transparency=25)
        stamp = datetime.now(timezone.utc).isoformat()
        run = {"app_id":"codex","run_id":"codex:preview","turn_id":"preview","status":"running",
            "started_at":stamp,"updated_at":stamp,"message":"正在验证修改与测试结果",
            "task_summary":"完善桌面监控体验","pending_confirmations":[],
            "steps":[{"name":"读取项目","status":"completed"},{"name":"验证修改","status":"running"},
                     {"name":"整理结果","status":"pending"}],
            "tools":[{"name":"pytest","status":"completed","detail":"9 项壁纸与 UI 测试通过"}],
            "codex":{"mode":"logs","model":"fixture","cwd":"demo-project","compactions":0}}
        hub = Mock()
        hub.read.side_effect = lambda:{"backend_id":"preview","status":"running","preferences":store.snapshot(),
                                      "runs":[run],"actions":[],"source_states":{}}
        root = tk.Tk()
        app = MonitorApp(root,store,hub)
        try:
            width = min(app.s(980),root.winfo_screenwidth()-60)
            height = min(app.s(820),root.winfo_screenheight()-140)
            root.geometry(f"{width}x{height}+30+30")
            root.attributes("-topmost",True)
            app.panel.select_tab("personalization")
            settle(app)
            app.island.root.withdraw()
            capture(root).save(output/"solo-wallpaper-settings.png")
            app.wallpaper_dialog("island")
            capture(app.wallpaper_window).save(output/"solo-wallpaper-gallery.png")
            app.close_wallpaper_dialog()
            # A neutral test-owned backdrop avoids capturing other applications.
            app.panel.pack_forget()
            root.configure(bg="#e7ebf0")
            samples = []
            island = app.island
            for label,expanded,details,size in [
                ("1 / Capsule",False,False,(300,62)),
                ("2 / Progress",True,False,(420,244)),
                ("3 / Details",True,True,(420,580))]:
                settle(app)
                island.expanded,island.details = expanded,details
                island._size = size
                island._geometry(*size,root.winfo_rootx()+app.s(200),root.winfo_rooty()+app.s(30))
                island.root.deiconify()
                island.draw()
                rendered = capture(island.root)
                # PrintWindow represents the color-keyed exterior as black.
                # Fill only that border-connected region with the test backdrop.
                if os.name == "nt" and rendered.getpixel((0,0)) == (0,0,0):
                    ImageDraw.floodfill(rendered,(0,0),(231,235,240))
                samples.append((label,rendered))
            sheet = Image.new("RGB",(sum(im.width for _,im in samples)+80,max(im.height for _,im in samples)+70),"#e7ebf0")
            draw = ImageDraw.Draw(sheet)
            x = 20
            for label,im in samples:
                draw.text((x,15),label,fill="#233040")
                sheet.paste(im,(x,40))
                x += im.width+20
            sheet.save(output/"solo-wallpaper-island-stages.png")
            print("Saved settings, gallery and three-stage previews:",output)
        finally:
            app.close()


if __name__ == "__main__":
    main()
