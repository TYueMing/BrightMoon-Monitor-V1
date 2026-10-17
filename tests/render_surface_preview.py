"""Native, fixture-only screenshots of borderless surfaces and smooth glows."""
from datetime import datetime,timezone
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import Mock

from PIL import Image,ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monitor.app import MonitorApp
from monitor.settings import SettingsStore
from monitor.UI.display import enable_dpi_awareness
from render_wallpaper_preview import artwork,settle,capture


def main():
    enable_dpi_awareness()
    output=Path(__file__).resolve().parents[1]/"preview"
    with tempfile.TemporaryDirectory(prefix="solo-surface-preview-") as temp:
        work=Path(temp)
        store=SettingsStore(work/"data")
        photo=work/"mountains.png"
        artwork(photo)
        with Image.open(photo) as original:
            patterned=original.convert("RGB")
        for y in range(-100,160,40):
            ImageDraw.Draw(patterned).line((0,y,640,y+150),fill="#b6ccd7",width=10)
        patterned.save(photo)
        store.import_photo(photo,target="island")
        store.update(monitor_sound_enabled=False)
        stamp=datetime.now(timezone.utc).isoformat()
        run={"app_id":"deepseek_harness","run_id":"deepseek:surface-preview","turn_id":"fixture",
             "status":"running","started_at":stamp,"updated_at":stamp,
             "pending_confirmations":[],"steps":[],"tools":[],"message":"正在检查界面显示"}
        hub=Mock()
        hub.read.side_effect=lambda:{"backend_id":"surface-preview","status":"running",
            "runs":[run],"preferences":store.snapshot(),"actions":[],"source_states":{}}
        root=tk.Tk()
        app=MonitorApp(root,store,hub)
        errors=[]
        root.report_callback_exception=lambda *args:errors.append(args)
        try:
            root.geometry("900x980+40+40")
            app.panel.pack_forget()
            root.configure(bg="#ffffff")
            root.attributes("-topmost",True)
            root.deiconify()
            samples=[]
            island=app.island
            for label,mode,color,opacity,status in [
                ("Rose / 60% opacity / Approval","solid","#724b5c",60,"waiting_confirmation"),
                ("Light / 100% opacity / Running","solid","#e9e4ed",100,"running"),
                ("Dark / 100% opacity / Running","solid","#141d2c",100,"running"),
                ("Wallpaper / 100% opacity / Approval","photo","#724b5c",100,"waiting_confirmation"),
                ("Wallpaper / 50% opacity / Running","photo","#334155",50,"running")]:
                store.update(monitor_background_mode=mode,monitor_background_color=color,monitor_opacity=opacity)
                run["status"]=status
                run["pending_confirmations"]=[{"id":"fixture-only","status":"pending","requested_at":stamp}]
                app.load_background()
                island._poll()
                settle(app)
                island.expanded=island.details=False
                island._size=(300,62)
                island.task_strip.focus_levels={run["run_id"]:1.}
                island._geometry(300,62,180,180)
                island.draw()
                island.root.deiconify()
                root.lift()
                island.root.lift()
                root.update()
                # PrintWindow isolates fixture pixels from other desktop windows.
                # It omits global alpha, so composite that verified setting over
                # our own white backdrop using the actual silhouette mask.
                native=capture(island.root).convert("RGB")
                mask=Image.new("L",native.size,0)
                mask.paste(island._surface_frame.getchannel("A"),
                           (round(5*island.scale),round(5*island.scale)))
                mask=mask.point(lambda value:round(value*opacity/100))
                frame=Image.new("RGB",native.size,"white")
                frame.paste(native,(0,0),mask)
                samples.append((label,frame))
            width=max(im.width for _,im in samples)+40
            sheet=Image.new("RGB",(width,sum(im.height+38 for _,im in samples)+18),"white")
            draw=ImageDraw.Draw(sheet)
            y=12
            for label,frame in samples:
                draw.text((20,y),label,fill="#344155")
                sheet.paste(frame,(20,y+22))
                y+=frame.height+38
            sheet.save(output/"solo-borderless-islands.png")
            if errors:
                raise RuntimeError(errors)
            print("Saved borderless native previews:",output/"solo-borderless-islands.png")
        finally:
            app.close()


if __name__=="__main__":
    main()
