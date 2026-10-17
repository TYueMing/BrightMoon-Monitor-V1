import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

from monitor.settings import SettingsStore
from monitor.UI.settings_panel import compose_background
from monitor.music.play.preview import SoundPreview


def test_panel_transparency_is_real_composition_and_has_round_corners():
    photo = Image.new("RGB",(100,100),(0,0,0))
    half = compose_background((100,100),photo,(10,10,90,90),50)
    assert half.getpixel((50,50)) == (128,128,128)
    assert half.getpixel((10,10))[0] < 10  # round corner shows the photo
    assert half.getpixel((16,10))[0] > 100  # 6px radius reaches the top edge
    clear = compose_background((100,100),photo,(10,10,90,90),100)
    opaque = compose_background((100,100),photo,(10,10,90,90),0)
    assert clear.getpixel((50,50)) == (0,0,0)
    assert opaque.getpixel((50,50)) == (255,255,255)
    white = compose_background((100,100),None,(10,10,90,90),100)
    assert white.getpixel((50,50)) == (255,255,255)


@pytest.mark.parametrize("seconds,minutes",[(5,1),(35,1),(121,3),(3600,10)])
def test_migration_from_seconds_to_whole_minutes(tmp_path,seconds,minutes):
    (tmp_path/"settings.json").write_text(json.dumps({"monitor_hide_seconds":seconds}),encoding="utf-8")
    store = SettingsStore(tmp_path)
    assert store.snapshot()["monitor_hide_minutes"] == minutes
    assert store.snapshot()["panel_transparency"] == 50
    store.update(monitor_hide_minutes=minutes)
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert "monitor_hide_seconds" not in saved


def test_panel_sound_and_minute_preferences_persist_with_photo(tmp_path):
    store = SettingsStore(tmp_path/"data")
    photo = tmp_path/"test.png"
    Image.new("RGB",(20,20),"blue").save(photo)
    store.import_photo(photo)
    assert store.snapshot()["panel_transparency"] == 50
    store.update(panel_transparency=73,monitor_hide_minutes=7,monitor_completion_sound="sound-3")
    store.update(background_mode="white")
    store.import_photo(photo)
    restored = SettingsStore(store.root).snapshot()
    assert restored["panel_transparency"] == 73
    assert restored["monitor_hide_minutes"] == 7
    assert restored["monitor_completion_sound"] == "sound-3"
    for minutes in (0,11,1.5,True):
        with pytest.raises(ValueError):
            store.update(monitor_hide_minutes=minutes)
    with pytest.raises(ValueError):
        store.update(panel_transparency=101)


def test_preview_switch_serializes_and_cancels_previous_clip(monkeypatch):
    preview = SoundPreview()
    entered,exited = threading.Event(),threading.Event()
    second_started = threading.Event()
    calls = []
    def play(path,cancel):
        calls.append(path.stem)
        if path.stem == "sound-1":
            entered.set()
            assert cancel.wait(2)
            exited.set()
        else:
            assert exited.is_set()
            second_started.set()
            cancel.wait(2)
    monkeypatch.setattr(preview,"_windows",play)
    monkeypatch.setattr(preview,"_external",play)
    try:
        preview.play("sound-1")
        assert entered.wait(2)
        preview.play("sound-2")
        assert second_started.wait(2)
        assert calls == ["sound-1","sound-2"]
    finally:
        preview.stop()
        preview._thread.join(timeout=2)
    assert not preview.current


def test_island_appearance_endpoints_and_invalid_settings(tmp_path):
    store = SettingsStore(tmp_path/"data")
    for opacity in (0,27,100):
        store.update(monitor_opacity=opacity,monitor_background_color="#ABCDEF")
        restored = SettingsStore(store.root).snapshot()
        assert restored["monitor_opacity"] == opacity
        assert restored["monitor_background_color"] == "#abcdef"
    original = store.snapshot()
    for opacity in (-1,101,True,12.5):
        with pytest.raises(ValueError):
            store.update(monitor_opacity=opacity)
    for color in ("red","#fff","#gggggg"):
        with pytest.raises(ValueError):
            store.update(monitor_background_color=color)
    assert SettingsStore(store.root).snapshot() == original
