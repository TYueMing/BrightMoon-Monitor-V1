"""Capture only fixture windows on a neutral backdrop; no live agent access."""
from pathlib import Path
import sys
import time
import tkinter as tk
from unittest.mock import Mock
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor.UI.island import DynamicIsland
from monitor.UI.display import enable_dpi_awareness


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    root.configure(bg="#e7ebf0")
    root.geometry("720x480+30+30")
    root.attributes("-topmost", True)
    window = tk.Toplevel(root)
    value = {"backend_id": "preview", "status": "running", "preferences": {"monitor_sound_enabled": False},
        "runs": [{"run_id": "codex:preview", "app_id": "codex", "turn_id": "preview", "status": "running",
                  "started_at": datetime.fromtimestamp(time.time()-12, timezone.utc).isoformat(),
                  "updated_at": datetime.now(timezone.utc).isoformat(), "agent": "Codex", "message": "正在验证任务结果",
                  "steps": [{"name": "读取项目", "status": "completed"}, {"name": "检查修改", "status": "running"},
                            {"name": "整理结果", "status": "pending"}], "tools": [], "pending_confirmations": []}], "actions": []}
    island = DynamicIsland(window, lambda: value, completion_player=Mock())
    captures = []
    try:
        for label, color, opacity, expanded in [
            ("White / 100% opacity", "#ffffff", 100, False),
            ("Blue / 100% opacity", "#2362b0", 100, False),
            ("Violet / 50% opacity", "#3f3c6d", 50, True),
        ]:
            value["preferences"].update(monitor_background_color=color, monitor_opacity=opacity)
            island._poll()
            for job in island._jobs.values():
                window.after_cancel(job)
            island._jobs.clear()
            island._opening = False
            island._animation = None
            island.expanded = expanded
            width, height = (420, 230) if expanded else (300, 62)
            island._size = (width, height)
            island._geometry(width, height, 100, 100)
            island.draw()
            window.deiconify()
            root.update()
            time.sleep(.08)
            bounds = (window.winfo_rootx(), window.winfo_rooty(),
                      window.winfo_rootx()+window.winfo_width(), window.winfo_rooty()+window.winfo_height())
            captures.append((label, ImageGrab.grab(bbox=bounds).convert("RGB")))
        sheet = Image.new("RGB", (max(im.width for _, im in captures)+48,
                                  sum(im.height+52 for _, im in captures)+24), "#e7ebf0")
        draw = ImageDraw.Draw(sheet)
        top = 18
        for label, capture in captures:
            draw.text((24, top), label, fill="#233040")
            sheet.paste(capture, (24, top+22))
            top += capture.height+52
        target = Path(__file__).resolve().parents[1] / "preview" / "solo-theme-lifecycle.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(target)
        print(target)
    finally:
        island.close()
        root.destroy()


if __name__ == "__main__":
    main()
