"""Real startup event-loop probe; fixture data only, no live source adapters."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import tkinter as tk
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor.app import MonitorApp
from monitor.settings import SettingsStore
from monitor.UI.display import enable_dpi_awareness
from monitor.UI.rope_surface import WindowsRopeSurface


def main(folder, scenario):
    enable_dpi_awareness()
    data = Path(folder)
    (data / "rope").mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (60, 110))
    draw = ImageDraw.Draw(image)
    draw.rectangle((28, 0, 31, 55), fill="orange")
    draw.ellipse((5, 45, 55, 105), fill="orange")
    if not (data / "settings.json").exists():
        image.save(data / "rope/测试角色.png")
        SettingsStore(data).update(rope_enabled=True, rope_image="rope/测试角色.png",
                                   monitor_sound_enabled=False)
    # Restore from disk just as a new run.py process does, before any window.
    store = SettingsStore(data)
    started = time.monotonic()
    errors, failures, observations = [], [], {}
    stamp = datetime.now(timezone.utc).isoformat()
    run = {"app_id": "codex", "run_id": "codex:restart-fixture", "turn_id": "fixture",
           "status": "running", "started_at": stamp, "updated_at": stamp,
           "tools": [], "steps": [], "pending_confirmations": []}
    hub = Mock()
    source_ready = [scenario not in {"delayed", "idle"}]
    hub.read.side_effect = lambda: {"backend_id": "fixture", "runs": [run] if source_ready[0] else [],
                                    "actions": [], "preferences": store.snapshot(), "source_states": {}}
    root = tk.Tk()
    root.withdraw()  # Match run.py; never force animation completion in this test.
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    original = WindowsRopeSurface.paint

    def paint(surface, x, y, alpha):
        if scenario == "recover" and alpha > 0 and not failures:
            failures.append("injected startup graphics failure")
            raise OSError("fixture: first paint cannot use the transient HWND")
        return original(surface, x, y, alpha)

    with patch.object(WindowsRopeSurface, "paint", paint):
        app = MonitorApp(root, store, hub)
        observations["created_before_map"] = app.island.pendant.window is not None
        root.deiconify()

        def check_visible():
            p = app.island.pendant
            if scenario == "idle":
                if p.window is not None:
                    errors.append("Idle source unexpectedly created a pendant window")
                if time.monotonic() - started >= .8:
                    observations["idle_hidden"] = bool(app.island._hidden and p.window is None)
                    root.quit()
                    return
            visible = bool(p.window and p.window.winfo_viewable() and p.alpha > .99 and not p.unavailable)
            if visible and p.surface:
                from ctypes import wintypes as wt
                p.surface.user.IsWindowVisible.argtypes = [wt.HWND]
                p.surface.user.IsWindowVisible.restype = wt.BOOL
                visible = bool(p.surface.user.IsWindowVisible(p.surface.hwnd))
            if visible:
                observations["visible_after"] = round(time.monotonic() - started, 3)
                observations["restored_image"] = store.snapshot()["rope_image"]
                root.quit()
            elif time.monotonic() - started > 4:
                observations.update(state=p.motion.state, unavailable=p.unavailable,
                                    alpha=p.alpha, mapped=bool(p.window and p.window.winfo_ismapped()))
                root.quit()
            else:
                root.after(25, check_visible)

        if scenario == "delayed":
            root.after(250, lambda: source_ready.__setitem__(0, True))
        root.after(25, check_visible)
        try:
            root.mainloop()
            assert not errors, errors
            assert (observations.get("idle_hidden") if scenario == "idle" else "visible_after" in observations), observations
            assert not observations["created_before_map"], observations
            assert not app.island.expanded
            hub.send.assert_not_called()
            if scenario == "recover":
                assert failures, "Expected the first native paint failure to be exercised"
        finally:
            app.close()
        print(json.dumps(observations, ensure_ascii=True))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
