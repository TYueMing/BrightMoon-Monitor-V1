"""Native Tk integration with fixture sources; never operates a real agent."""
import tkinter as tk
from unittest.mock import Mock
from types import SimpleNamespace
import time

from PIL import Image
import pytest

from monitor.app import MonitorApp
from monitor.settings import SettingsStore
from monitor.src.source_hub import SourceHub


def test_native_toggle_lifetime_background_and_restart(tmp_path,monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display unavailable")
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *args:errors.append(args)
    store = SettingsStore(tmp_path / "data")
    store.update(monitor_sound_enabled=False)
    source = Mock()
    result = {"status":"ready","runs":[{"run_id":"codex:fixture","app_id":"codex","status":"running",
                "started_at":time.time(),"task_summary":"Fixture task"}],"actions":[]}
    source.poll.side_effect = lambda:result
    hub = SourceHub(store.snapshot,factories={"codex":lambda _:source},start_worker=False)
    app = MonitorApp(root,store,hub)
    try:
        root.update()
        assert app.island._hidden
        assert app.display_history.path == store.root / "display_history.sqlite3"
        assert app.display_history.path.is_file()
        assert app.panel.active_tab == "monitor"
        assert "sounds" not in app.panel.controls
        app.panel.controls["source:codex"].callback()
        hub.poll_sources()
        app.island._poll()
        root.update()
        assert not app.island._hidden
        assert app.island.selection.focused()["run_id"] == "codex:fixture"
        app.panel.controls["tab:personalization"].callback()
        assert "delay" not in app.panel.controls
        assert "source:codex" not in app.panel.controls
        app.panel.controls["island-color:#3f3c6d"].callback()
        assert app.island.canvas.palette["#141d2c"] == "#3f3c6d"
        slider = app.panel.controls["island-transparency"]
        x = app.panel.origin[0]+(slider.rect[0]+.73*(slider.rect[2]-slider.rect[0]))*app.scale
        app.panel.set_slider(slider,x)
        root.update()
        assert float(app.island.root.attributes("-alpha")) == pytest.approx(.27)
        assert SettingsStore(store.root).snapshot()["monitor_opacity"] == 27
        choose = Mock(return_value=(None,None))
        monkeypatch.setattr("monitor.app.colorchooser.askcolor",choose)
        app.choose_island_color()
        assert store.snapshot()["monitor_background_color"] == "#3f3c6d"
        choose.return_value = ((35,98,176),"#2362b0")
        app.choose_island_color()
        assert app.island.canvas.palette["#141d2c"] == "#2362b0"
        assert SettingsStore(store.root).snapshot()["monitor_background_color"] == "#2362b0"
        app.panel.controls["tab:monitor"].callback()
        app.save_delay(1)
        result["runs"][0].update(status="completed",ended_at=time.time()-61)
        hub.poll_sources()
        app.island._poll()
        root.update()
        assert app.island._hidden
        app.panel.controls["source:codex"].callback()
        assert not store.snapshot()["monitor_sources"]["codex"]["enabled"]
        assert app.island.snapshot["runs"] == []
        assert not app.panel.controls["source:codeagent"].enabled
        assert not store.snapshot()["monitor_sources"]["codeagent"]["enabled"]
        image = tmp_path / "sample.png"
        Image.new("RGB",(200,120),"#2362b0").save(image)
        store.import_photo(image)
        app.load_background()
        root.update()
        assert app.photo is not None
        assert root.title() == "Bright Moon Monitor"
        assert store.snapshot()["panel_transparency"] == 50
        app.panel.controls["tab:personalization"].callback()
        slider = app.panel.controls["transparency"]
        x = app.panel.origin[0] + (slider.rect[0]+.72*(slider.rect[2]-slider.rect[0]))*app.scale
        app.panel.set_slider(slider,x)
        assert SettingsStore(store.root).snapshot()["panel_transparency"] == 72
        app.panel.controls["tab:monitor"].callback()
        app.panel.focus_key = "delay"
        app.panel.key(SimpleNamespace(keysym="End"))
        assert SettingsStore(store.root).snapshot()["monitor_hide_minutes"] == 10
        app.panel.key(SimpleNamespace(keysym="Left"))
        assert store.snapshot()["monitor_hide_minutes"] == 9
        app.panel.controls["tab:personalization"].callback()
        app.panel.select_personalization("sounds")
        app.preview = Mock(current="",error="")
        app.sound_dialog()
        root.update()
        app.preview_sound("sound-2")
        app.preview.play.assert_called_once_with("sound-2")
        assert store.snapshot()["monitor_completion_sound"] == "sound-1"
        app.select_sound("sound-3")
        assert "已选择：提示音 3" in [app.panel.itemcget(i,"text") for i in app.panel.find_all()
                                   if app.panel.type(i)=="text"]
        app.close_sounds()
        assert SettingsStore(store.root).snapshot()["monitor_completion_sound"] == "sound-3"
        assert app.preview.stop.called
        image.unlink()
        assert SettingsStore(store.root).photo_path().is_file()
        app.set_background("white")
        assert app.photo is None
        assert not errors
    finally:
        app.close()
    source.close.assert_called_once()
    restarted_root = tk.Tk()
    restarted_root.withdraw()
    restored_store = SettingsStore(store.root)
    restarted = MonitorApp(restarted_root,restored_store,
                           SourceHub(restored_store.snapshot,factories={},start_worker=False))
    try:
        assert restarted.panel.active_tab == "monitor"
        assert restarted.island.canvas.palette["#141d2c"] == "#2362b0"
        assert restarted.island._appearance["monitor_opacity"] == 27
    finally:
        restarted.close()
