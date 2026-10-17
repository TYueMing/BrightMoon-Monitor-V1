"""Wallpaper library, stable reveal geometry, persistence and native controls."""
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageChops, ImageDraw
import pytest

from monitor.settings import SettingsStore
from monitor.src.wallpapers import read_photo
from monitor.UI.wallpapers import IslandWallpaper
from monitor.UI.settings_panel import CARD_WIDTH, CARD_HEIGHT
from monitor.app import MonitorApp


def save_photo(path, color="blue"):
    Image.new("RGB", (480,580), color).save(path)
    return path


def test_history_switch_independent_surfaces_and_move(tmp_path):
    store = SettingsStore(tmp_path / "original" / "data")
    blue = save_photo(tmp_path / "first.png")
    red = save_photo(tmp_path / "second.png", "red")
    a = store.import_photo(blue).relative_to(store.root).as_posix()
    b = store.import_photo(red, target="island").relative_to(store.root).as_posix()
    # Reusing an image neither duplicates it nor changes the other surface.
    store.import_photo(red, target="island")
    assert set(store.list_backgrounds()) == {a,b}
    assert store.snapshot()["background_photo"] == a
    assert store.snapshot()["monitor_background_photo"] == b
    store.update(monitor_opacity=43, panel_transparency=61)
    store.update(background_mode="white", monitor_background_mode="solid")
    store.select_photo(a)
    store.select_photo(b, target="island")
    # The original files are not needed once imported.
    blue.unlink()
    red.unlink()
    moved = tmp_path / "moved"
    shutil.copytree(store.root, moved)
    restored = SettingsStore(moved)
    assert restored.snapshot() == store.snapshot()
    assert restored.background_path(a).is_file() and restored.background_path(b).is_file()
    assert restored.snapshot()["monitor_opacity"] == 43
    assert restored.snapshot()["panel_transparency"] == 61


def test_old_directory_images_are_discovered_and_bad_selection_is_non_destructive(tmp_path):
    store = SettingsStore(tmp_path / "data")
    folder = store.root / "backgrounds"
    folder.mkdir(parents=True)
    save_photo(folder / "older-wallpaper.PNG")
    (folder / "incomplete.tmp").write_text("partial", encoding="utf-8")
    bad = folder / "broken.png"
    bad.write_text("not an image", encoding="utf-8")
    store.select_photo("backgrounds/older-wallpaper.PNG")
    before = store.snapshot()
    assert set(store.list_backgrounds()) == {"backgrounds/older-wallpaper.PNG", "backgrounds/broken.png"}
    for relative in ("backgrounds/../outside.png", "backgrounds/missing.png", "backgrounds/broken.png",
                     "backgrounds/nested/file.png", "backgrounds/file.png:alternate"):
        with pytest.raises((ValueError, OSError)):
            store.select_photo(relative, target="island")
        assert store.snapshot() == before
    for invalid in ("../../x.png", "backgrounds/../x.png", "backgrounds\\x.png", None):
        with pytest.raises(ValueError):
            store.update(monitor_background_photo=invalid)
    with patch("monitor.settings.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            store.select_photo("backgrounds/older-wallpaper.PNG", target="island")
    assert SettingsStore(store.root).snapshot() == before


def test_schema_two_migrates_without_changing_existing_background(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"schema_version":2,
        "background_mode":"photo", "background_photo":"backgrounds/old.png",
        "monitor_opacity":27, "panel_transparency":65}), encoding="utf-8")
    prefs = SettingsStore(tmp_path).snapshot()
    assert prefs["background_photo"] == "backgrounds/old.png"
    assert prefs["monitor_background_mode"] == "solid" and prefs["monitor_background_photo"] == ""
    assert prefs["monitor_opacity"] == 27 and prefs["panel_transparency"] == 65


def test_exif_orientation_is_identical_in_photo_and_thumbnail(tmp_path):
    picture = Image.new("RGB", (120,60), "#5264ab")
    exif = Image.Exif()
    exif[274] = 6
    path = tmp_path / "rotated.jpg"
    picture.save(path, exif=exif)
    assert read_photo(path).size == (60,120)


@pytest.mark.parametrize("scale", [1,1.5,2])
def test_image_reveal_keeps_same_pixels_and_reuses_frames(scale):
    photo = Image.new("RGB", (480,580))
    draw = ImageDraw.Draw(photo)
    for y in range(580):
        draw.line((0,y,479,y), fill=(y % 256, y//3, 200-y//4))
    surface = IslandWallpaper()
    def render(w,h):
        return surface.render(photo, (round(w*scale),round(h*scale)), scale=scale,
                              radius=round(26*scale), color="#141d2c")
    mini, second, third = render(290,51), render(410,233), render(410,569)
    # All stages share exactly the same top image pixels, despite width/height changes.
    for y in (20,30):
        assert mini.getpixel((round(145*scale),round(y*scale))) == third.getpixel((round(205*scale),round(y*scale)))
    box = (round(35*scale),round(35*scale),round(375*scale),round(190*scale))
    assert ImageChops.difference(second.crop(box),third.crop(box)).getbbox() is None
    assert third.getpixel((round(205*scale),round(500*scale))) != mini.getpixel((round(145*scale),round(30*scale)))
    assert render(410,569) is third
    assert third.getpixel((0,0))[3] == 0 and third.getpixel((round(205*scale),round(30*scale)))[3] == 255
    large = render(440,700)
    assert large.getpixel((round(220*scale),round(650*scale)))[:3] != (0,0,0)
    surface.render(None, (1,1), scale=scale, radius=0, color="#141d2c")
    assert surface.source is None and surface.frame is None


@pytest.fixture
def native_app(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    store = SettingsStore(tmp_path / "data")
    store.update(monitor_sound_enabled=False)
    stamp = datetime.now(timezone.utc).isoformat()
    run = {"app_id":"codex", "run_id":"codex:fixture", "turn_id":"fixture", "status":"running",
           "started_at":stamp, "updated_at":stamp, "steps":[], "tools":[], "pending_confirmations":[]}
    hub = Mock()
    hub.read.side_effect = lambda:{"backend_id":"fixture", "preferences":store.snapshot(),
                                   "runs":[run], "actions":[], "source_states":{}}
    app = MonitorApp(root,store,hub)
    errors = []
    root.report_callback_exception = lambda *args:errors.append(args)
    root.deiconify()
    root.update()
    for job in app.island._jobs.values():
        app.island.root.after_cancel(job)
    app.island._jobs.clear()
    app.island._animation = None
    app.island._opening = False
    app.panel.select_tab("personalization")
    yield app, tmp_path
    app.close()
    assert not errors


def test_native_centering_history_picker_and_persistence(native_app):
    app, tmp_path = native_app
    paths = []
    for index in range(8):
        source = save_photo(tmp_path / f"photo-{index}.png", (20+index*20,80,180))
        paths.append(app.store.import_photo(source).relative_to(app.store.root).as_posix())
    app.load_background()
    for width,height in [(812,712),(1050,850)]:
        app.root.geometry(f"{app.s(width)}x{app.s(height)}")
        app.root.update()
        x,y = app.panel.origin
        assert x+CARD_WIDTH*app.scale/2 == pytest.approx(app.panel.winfo_width()/2)
        assert y+CARD_HEIGHT*app.scale/2 == pytest.approx(app.panel.winfo_height()/2)
        for control in app.panel.controls.values():
            a,b,c,d = control.rect
            assert 0 <= a < c <= CARD_WIDTH and 0 <= b < d <= CARD_HEIGHT
    thumbnail = next(c for key,c in app.panel.controls.items() if key.startswith("wallpaper:"))
    x,y = app.panel.point((thumbnail.rect[0]+thumbnail.rect[2])/2, (thumbnail.rect[1]+thumbnail.rect[3])/2)
    assert app.panel.hit(SimpleNamespace(x=x,y=y)).key == thumbnail.key
    thumbnail.callback()
    assert app.store.snapshot()["background_photo"] == thumbnail.key.split(":",1)[1]
    app.panel.controls["island-photo"].callback()
    picker = app.wallpaper_window
    app.root.update()
    assert picker.target == "island" and len(picker.paths) == 8
    assert len(picker.images) == 6
    picker.move(1)
    assert picker.page == 1 and len(picker.images) == 2
    target = picker.paths[6]
    picker.choose(target)
    assert app.wallpaper_window is None and app.island_photo is not None
    assert SettingsStore(app.store.root).snapshot()["monitor_background_photo"] == target
    app.set_background("white")
    assert app.photo is None and app.island_photo is not None
    app.set_island_background("solid")
    assert app.island_photo is None and len(app.wallpapers) == 8


def test_native_photo_all_states_share_alpha_and_cached_image(native_app):
    app,tmp_path = native_app
    photo = save_photo(tmp_path / "island.png", "#7b98c4")
    app.store.import_photo(photo, target="island")
    app.load_background()
    island = app.island
    for expanded,details,w,h in [(False,False,300,62),(True,False,420,244),(True,True,420,580)]:
        island._opening = False
        island.expanded, island.details = expanded,details
        island._size = w,h
        for opacity in (0,50,100):
            island.snapshot["preferences"]["monitor_opacity"] = opacity
            island._apply_appearance()
            island.draw()
            first = island._wallpaper_tk
            assert island.canvas.find_withtag("island_wallpaper")
            assert float(island.root.attributes("-alpha")) == pytest.approx(opacity/100)
            island.draw()
            assert island._wallpaper_tk is first
    # Missing file on a later restart falls back to color without corrupting selection.
    relative = app.store.snapshot()["monitor_background_photo"]
    app.store.background_path(relative).unlink()
    app.load_background()
    assert app.island_photo is None
    assert app.store.snapshot()["monitor_background_photo"] == relative
    assert "无法读取" in app.notice_text
