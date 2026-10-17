"""Capture fixture-only native controls and pendant animation to preview/."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw, ImageGrab

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monitor.app import MonitorApp
from monitor.settings import SettingsStore
from monitor.UI.display import enable_dpi_awareness
from render_wallpaper_preview import artwork, capture, settle


def cutout(image):
    """Remove only the color-key exterior returned by native PrintWindow."""
    image = image.convert("RGBA")
    # Transparent corners may be returned as either black or the color key.
    for point in [(0,0),(image.width-1,0),(0,image.height-1),(image.width-1,image.height-1)]:
        if image.getpixel(point)[:3] in {(0,0,0),(237,1,236)}:
            ImageDraw.floodfill(image,point,(0,0,0,0))
    return image


def main():
    enable_dpi_awareness()
    project = Path(__file__).resolve().parents[1]
    output = project/"preview"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="solo-rope-preview-") as temp:
        data = Path(temp)/"data"
        shutil.copytree(project/"data"/"rope",data/"rope")
        store = SettingsStore(data)
        photo = Path(temp)/"mountain.png"
        artwork(photo)
        store.import_photo(photo)
        store.update(panel_color="#172233",panel_transparency=20,monitor_sound_enabled=False,
                     rope_enabled=True,rope_image="rope/gpt.png")
        stamp = datetime.now(timezone.utc).isoformat()
        run = {"app_id":"codex","run_id":"codex:preview-rope","turn_id":"fixture","status":"running",
               "started_at":stamp,"updated_at":stamp,"message":"正在验证界面与动画",
               "task_summary":"完善监控体验","pending_confirmations":[],"tools":[],
               "steps":[{"name":"读取项目","status":"completed"},{"name":"验证界面","status":"running"}],
               "codex":{"mode":"logs","model":"fixture","cwd":"demo-project"}}
        hub = Mock()
        hub.read.side_effect=lambda:{"backend_id":"rope-preview","status":"running","runs":[run],
                                    "preferences":store.snapshot(),"actions":[],"source_states":{}}
        root = tk.Tk()
        app = MonitorApp(root,store,hub)
        errors = []
        root.report_callback_exception=lambda *args:errors.append(args)
        try:
            root.geometry(f"{app.s(900)}x{app.s(760)}+30+30")
            root.deiconify()
            settle(app)
            app.island.root.withdraw()
            app.island.pendant.hide()
            app.panel.select_tab("personalization")
            capture(root).save(output/"solo-personalization-panel-dark.png")
            app.panel.select_personalization("rope")
            capture(root).save(output/"solo-rope-picker.png")
            app.save_panel_color("#ffffff")
            capture(root).save(output/"solo-rope-picker-rounded.png")
            app.panel.pack_forget()
            root.configure(bg="#e7ebf0")
            root.attributes("-topmost",True)
            # A neutral stage is assembled solely from our two test windows.
            island = app.island
            island.pendant.hide()
            clock = [100.]
            frames = []
            with patch("monitor.UI.rope_pendant.time.monotonic",side_effect=lambda:clock[0]):
                island._size=(300,62)
                island.expanded=island.details=False
                island._geometry(300,62,400,80)
                island.root.deiconify()
                island.draw()
                did_open=did_details=did_close=False
                for index in range(53):
                    elapsed=index*.08
                    clock[0]=100+elapsed
                    if elapsed>=.48 and not did_open:
                        pendant=island.pendant
                        x,y=pendant.size[0]//2,pendant.size[1]*3//4
                        pendant.canvas.event_generate("<ButtonPress-1>",x=x,y=y)
                        pendant.canvas.event_generate("<ButtonRelease-1>",x=x,y=y)
                        assert pendant.motion.state=="pull", "Character click must start the rope prelude"
                        did_open=True
                    if elapsed>=1.92 and not did_details:
                        island._toggle()
                        did_details=True
                    if elapsed>=2.8 and not did_close:
                        island._toggle()
                        did_close=True
                    island.pendant._paint()
                    island._animate()
                    island.draw()
                    frame=Image.new("RGBA",(round(540*island.scale),round(610*island.scale)),"#e7ebf0")
                    image=cutout(capture(island.root))
                    x=round((frame.width-image.width)/2)
                    frame.alpha_composite(image,(x,48))
                    pendant=island.pendant
                    if pendant.window and pendant.window.state()!="withdrawn":
                        # PrintWindow discards layered-window alpha. Composite
                        # the very RGBA frame submitted to UpdateLayeredWindow.
                        image=pendant.rendered_image.copy()
                        alpha=pendant.alpha
                        image.putalpha(image.getchannel("A").point(lambda value:round(value*alpha)))
                        px=round(frame.width/2-image.width/2)
                        # Tk's reported y may briefly belong to a replaced
                        # wrapper. Use the position actually sent to DWM.
                        offset=pendant.motion.frame(clock[0]).offset*pendant.scale
                        py=48+round(pendant.anchor[1]+offset)-island._position[1]
                        frame.alpha_composite(image,(px,py))
                    label="Capsule" if not island.expanded else "Details" if island.details else "Progress"
                    ImageDraw.Draw(frame).text((20,16),f"{elapsed:.2f}s / {label}",fill="#243448")
                    frames.append(frame.convert("RGB"))
                    if index==5:
                        # Validate real DWM composition as well, using only a
                        # rectangle fully covered by our neutral fixture window.
                        root.lift()
                        island.root.lift()
                        island.pendant.window.lift()
                        island.pendant._paint()
                        root.update()
                        time.sleep(.15)
                        x,y=island.root.winfo_rootx(),island.root.winfo_rooty()
                        ImageGrab.grab(bbox=(x-12,y-12,x+island.root.winfo_width()+12,
                            y+round(145*island.scale))).save(output/"solo-rope-native.png")
                frames[5].save(output/"solo-rope-island.png")
                frames[0].save(output/"solo-rope-motion.gif",save_all=True,append_images=frames[1:],
                               duration=80,loop=0,disposal=2)
            if errors:
                raise RuntimeError(errors)
            print("Saved panel, picker, capsule and animation previews:",output)
        finally:
            app.close()


if __name__=="__main__":
    main()
