"""Local assets, persistence, contrast, animation timing and native UI lifecycle."""
import json
from pathlib import Path
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw
import pytest

from monitor.settings import SettingsStore
from monitor.src.rope_assets import RopeCatalog
from monitor.UI.rope_pendant import RopeMotion, PULL_SECONDS
from monitor.UI.rope_surface import premultiplied_bgra
from monitor.UI.panel_palette import panel_palette, contrast
from monitor.UI.settings_panel import compose_background, CARD_WIDTH, CARD_HEIGHT
from test_wallpapers import native_app


def png(path, color="#e3a859"):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (80,120))
    draw = ImageDraw.Draw(image)
    draw.rectangle((27,0,31,72),fill=color)
    draw.ellipse((10,45,69,115),fill=color)
    image.save(path,format="PNG")
    return path


def test_preferences_migrate_persist_and_failed_write_is_atomic(tmp_path):
    (tmp_path/"settings.json").write_text(json.dumps({"schema_version":3,"panel_transparency":38}),encoding="utf-8")
    store = SettingsStore(tmp_path)
    prefs = store.snapshot()
    assert prefs["panel_color"] == "#ffffff" and not prefs["rope_enabled"] and prefs["rope_image"] == ""
    assert prefs["panel_transparency"] == 38
    store.update(panel_color="#1B293A",rope_enabled=True,rope_image="rope/中文角色.PNG")
    assert SettingsStore(tmp_path).snapshot() == store.snapshot()
    before = store.snapshot()
    for change in ({"panel_color":"red"},{"panel_color":"#fff"},{"rope_enabled":"false"},
                   {"rope_image":"rope/../escape.png"},{"rope_image":"rope\\a.png"},
                   {"rope_image":"rope/a.png:stream"},{"rope_image":"../rope/a.png"},
                   {"rope_image":"rope//a.png"},{"rope_image":"rope/sub/a.png"},
                   {"rope_image":"C:/outside.png"}):
        with pytest.raises(ValueError):
            store.update(**change)
        assert store.snapshot() == before
    with patch("monitor.settings.os.replace",side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            store.update(rope_image="rope/new.png",panel_color="#ffffff")
    assert SettingsStore(tmp_path).snapshot() == before
    raw = SettingsStore.normalize({"panel_color":None,"rope_enabled":1,"rope_image":None})
    assert raw["panel_color"] == "#ffffff" and not raw["rope_enabled"]


def test_catalog_discovery_alpha_cache_and_relocation(tmp_path):
    import shutil
    catalog = RopeCatalog(tmp_path/"data")
    assert catalog.list() == []
    folder = catalog.root/"rope"
    png(folder/"cat.PNG")
    png(folder/"中文.png")
    (folder/"bad.png").write_bytes(b"incomplete")
    (folder/"notes.txt").write_text("ignored",encoding="utf-8")
    Image.new("RGBA",(1,1)).save(folder/"empty.png")
    Image.new("RGB",(4,4)).save(folder/"pretending.png",format="JPEG")
    assert catalog.list() == ["rope/cat.PNG","rope/中文.png"]
    image = catalog.read("rope/cat.PNG")
    assert image.mode == "RGBA" and image.getchannel("A").getextrema() == (0,255)
    assert image.size == (60,116) and catalog.read("rope/cat.PNG") is image
    png(folder/"new.png")
    assert "rope/new.png" in catalog.list()
    (folder/"cat.PNG").unlink()
    with pytest.raises(ValueError):
        catalog.read("rope/cat.PNG")
    assert "rope/cat.PNG" not in catalog.list()
    shutil.copytree(catalog.root,tmp_path/"moved")
    assert RopeCatalog(tmp_path/"moved").list() == catalog.list()
    assert catalog.label("rope/中文.png") == "中文"


def test_catalog_rejects_link_escaping_local_folder(tmp_path):
    catalog = RopeCatalog(tmp_path/"data")
    external = png(tmp_path/"outside.png")
    # Path.resolve is mocked here so this case also runs without Windows'
    # CreateSymbolicLink privilege; the containment check is identical.
    with patch.object(Path,"resolve",return_value=external):
        with pytest.raises(ValueError):
            catalog.read("rope/linked.png")


@pytest.mark.parametrize("color",["#ffffff","#172233","#765f6d","#7894a1"])
def test_panel_color_composition_and_contrast(color):
    backdrop = Image.new("RGB",(100,100),"#466776")
    for transparency in (0,50,100):
        rendered = compose_background((100,100),backdrop,(10,10,90,90),transparency,color=color)
        if transparency == 0:
            assert rendered.getpixel((50,50)) == tuple(int(color[i:i+2],16) for i in (1,3,5))
        if transparency == 100:
            assert rendered.getpixel((50,50)) == (70,103,118)
        palette = panel_palette(rendered,(20,20,80,80))
        surface = "#"+"".join(f"{v:02x}" for v in rendered.getpixel((50,50)))
        for token in ("#1c2940","#697589","#426de7"):
            assert contrast(surface,palette[token]) >= 4.5


def test_rope_delays_fades_then_two_four_pixel_pulls():
    motion = RopeMotion()
    motion.reveal(10)
    assert motion.frame(10.199).alpha == 0
    assert motion.frame(10.3).alpha == pytest.approx(.5)
    assert motion.frame(10.4).alpha == pytest.approx(1)
    motion.reveal(10.4)  # Repaint must not restart the fade timer.
    assert motion.frame(11).alpha == 1
    assert motion.pull(11)
    for fraction,offset in [(0,0),(.25,4),(.5,0),(.75,4),(1,0)]:
        frame = motion.frame(11+PULL_SECONDS*fraction)
        assert frame.offset == pytest.approx(offset,abs=1e-6)
        assert frame.finished is (fraction==1)
    motion.hide()
    assert motion.frame(30).alpha == 0 and not motion.pull(30)
    motion.reveal(30)
    assert motion.frame(30.19).alpha == 0  # Collapse starts a fresh delay.


def test_png_translucent_edges_are_premultiplied_without_color_key_fringe():
    source = Image.new("RGBA",(3,1))
    source.putdata([(255,100,20,0),(255,100,20,128),(255,100,20,255)])
    assert premultiplied_bgra(source) == bytes([0,0,0,0,10,50,128,128,20,100,255,255])


def settle(island):
    for job in island._jobs.values():
        island.root.after_cancel(job)
    island._jobs.clear()
    island._animation = None
    island._opening = False
    island._size = island._target_size()
    island._geometry(*island._size,*island._position)
    island.draw()


def test_native_palette_picker_pagination_keyboard_and_saved_choice(native_app, monkeypatch):
    app,_ = native_app
    for index in range(11):
        png(app.store.root/"rope"/f"character-{index:02}.png")
    app.panel.controls["personalization:rope"].callback()
    assert len(app.rope_items) == 11 and not any(k.startswith("rope:") for k in app.panel.controls)
    app.panel.controls["rope-enabled"].callback()
    assert len(app.panel.rope_images) == 6
    assert app.rope_photo is not None
    for control in app.panel.controls.values():
        a,b,c,d = control.rect
        assert 0<=a<c<=CARD_WIDTH and 0<=b<d<=CARD_HEIGHT
    app.panel.controls["rope-next"].callback()
    assert app.panel.rope_page == 1 and len(app.panel.rope_images)==5
    choice = "rope/character-10.png"
    key = "rope:"+choice
    control = app.panel.controls[key]
    x,y = app.panel.point((control.rect[0]+control.rect[2])/2,(control.rect[1]+control.rect[3])/2)
    assert app.panel.hit(SimpleNamespace(x=x,y=y)).key == key
    app.panel.focus_key = key
    app.panel.key(SimpleNamespace(keysym="Return"))
    assert SettingsStore(app.store.root).snapshot()["rope_image"] == choice
    app.panel.controls["rope-enabled"].callback()
    assert not app.store.snapshot()["rope_enabled"] and app.rope_photo is None
    app.panel.controls["rope-enabled"].callback()
    assert app.store.snapshot()["rope_image"] == choice
    app.panel.controls["personalization:appearance"].callback()
    original = app.store.snapshot()["panel_color"]
    choose = patch("monitor.app.colorchooser.askcolor",return_value=(None,None))
    with choose:
        app.panel.controls["panel-color-custom"].callback()
    assert app.store.snapshot()["panel_color"] == original
    with patch("monitor.app.colorchooser.askcolor",return_value=((23,34,51),"#172233")):
        app.panel.controls["panel-color-custom"].callback()
    assert SettingsStore(app.store.root).snapshot()["panel_color"] == "#172233"
    assert app.panel.palette["#1c2940"] == "#f4f7fc"
    assert app.store.snapshot()["monitor_background_color"] != "#172233"
    assert "panel-color-custom" in app.panel.controls


def test_native_rope_animation_order_task_navigation_hide_and_close(native_app, monkeypatch):
    app,_ = native_app
    png(app.store.root/"rope"/"cat.png")
    clock = [100.]
    monkeypatch.setattr("monitor.UI.rope_pendant.time.monotonic",lambda:clock[0])
    island = app.island
    settle(island)
    app.toggle_rope(True)
    pendant = island.pendant
    assert pendant.motion.state == "reveal" and pendant.window.state()=="withdrawn"
    for elapsed,alpha in [(.19,0),(.3,.5),(.401,1)]:
        clock[0] = 100+elapsed
        pendant._paint()
        assert pendant.alpha == pytest.approx(
            alpha*app.store.snapshot()["monitor_opacity"]/100,abs=.001)
    island.root.update()
    baseline = pendant.window.winfo_y()
    island._toggle()
    island._toggle()  # Double-click still means one expansion.
    assert not island.expanded and pendant.motion.state == "pull"
    for fraction,offset in [(.25,4),(.5,0),(.75,4)]:
        clock[0] = 100.401+PULL_SECONDS*fraction
        pendant._paint()
        island.root.update_idletasks()
        assert pendant.window.winfo_y() == baseline+round(offset*island.scale)
        assert not island.expanded
    clock[0] = 101
    pendant._paint()
    assert island.expanded and not island.details
    assert pendant.window.state()=="withdrawn" and pendant.callback is None
    settle(island)
    island._toggle()
    assert island.details and pendant.motion.state=="hidden"
    settle(island)
    island._toggle()  # Third stage -> capsule.
    assert not island.expanded and pendant.motion.state=="hidden"
    clock[0] = 102
    settle(island)
    assert pendant.motion.frame(102.19).alpha==0
    clock[0] = 102.5
    pendant._paint()
    assert island.select_task(island._run()["run_id"])
    assert not island.expanded and pendant.motion.state=="pull"
    island._escape()
    clock[0] = 104
    pendant._paint()
    assert not island.expanded and pendant.callback is None
    island.draw()
    clock[0] = 105
    pendant._paint()
    island._toggle()
    app.toggle_rope(False)
    clock[0] = 106
    pendant._paint()
    assert not island.expanded and pendant.window.state()=="withdrawn"
    app.toggle_rope(True)
    clock[0] = 107
    pendant._paint()
    island._toggle()
    island._hidden = True
    island.draw()
    clock[0] = 108
    pendant._paint()
    assert pendant.callback is None and not island.expanded
    app.hub.send.assert_not_called()
    window = pendant.window
    app.close()
    assert pendant.closed and pendant.job is None and pendant.window is None


def test_native_missing_selection_and_drag_do_not_leave_orphan(native_app,monkeypatch):
    app,_ = native_app
    path = png(app.store.root/"rope"/"cat.png")
    now = [200.]
    monkeypatch.setattr("monitor.UI.rope_pendant.time.monotonic",lambda:now[0])
    island = app.island
    settle(island)
    app.toggle_rope(True)
    now[0] += 1
    island.pendant._paint()
    island._toggle()
    x,y = island._position
    island._drag_origin = (0,0,x,y)
    island._drag(SimpleNamespace(x_root=30,y_root=20))
    assert island.pendant.callback is None and island.pendant.window.state()=="withdrawn"
    island._release(SimpleNamespace(x=-100,y=-100))
    now[0] += 1
    island.pendant._paint()
    island.root.update_idletasks()
    assert island.pendant.window.winfo_y() == round(island._position[1]+56*island.scale)
    assert not island.expanded
    path.unlink()
    app.load_rope()
    assert app.rope_photo is None and island.pendant.window.state()=="withdrawn"
    assert app.store.snapshot()["rope_image"] == "rope/cat.png"
    png(path)
    app.load_rope()
    assert app.rope_photo is not None


def test_native_dpi_opacity_screen_edge_and_surface_release(native_app,monkeypatch):
    app,_ = native_app
    png(app.store.root/"rope"/"cat.png")
    clock=[300.]
    monkeypatch.setattr("monitor.UI.rope_pendant.time.monotonic",lambda:clock[0])
    island=app.island
    settle(island)
    app.toggle_rope(True)
    pendant=island.pendant
    clock[0]+=1
    for scale in (1,1.5,2):
        pendant.sync(ready=True,anchor=(300,150),scale=scale,opacity=.35)
        assert pendant.size[1]==min(pendant.image.height,round(76*scale))
        assert pendant.alpha==pytest.approx(.35)
        if pendant.surface:
            native=pendant.surface
            assert native.hwnd==native.user.GetAncestor(pendant.window.winfo_id(),2)
            style = native.user.GetWindowLongW(native.hwnd,-20)
            assert style&0x08080000==0x08080000
            assert not style&0x20  # The character is clickable; alpha-zero pixels pass through.
    pendant.sync(ready=True,anchor=(300,150),scale=1,opacity=0)
    assert pendant.window.state()=="withdrawn" and pendant.callback is None
    with patch("monitor.UI.rope_pendant.screen_bounds",return_value=(0,0,800,400)):
        pendant.sync(ready=True,anchor=(300,350),scale=1,opacity=1)
        assert pendant.size[1]==46
        pendant.sync(ready=True,anchor=(300,380),scale=1,opacity=1)
        assert pendant.window.state()=="withdrawn"
    native=pendant.surface
    app.close()
    if native:
        assert native.dc is None and native.bitmap is None


def test_native_approval_navigation_and_graphics_failure_do_not_stop_monitor(native_app,monkeypatch):
    app,_ = native_app
    png(app.store.root/"rope"/"cat.png")
    clock=[400.]
    monkeypatch.setattr("monitor.UI.rope_pendant.time.monotonic",lambda:clock[0])
    island=app.island
    settle(island)
    app.toggle_rope(True)
    clock[0]+=1
    island.pendant._paint()
    state=app.hub.read()
    run=state["runs"][0]
    approval={"id":"fixture-approval","status":"pending","requested_at":run["started_at"],
              "controllable":True,"revision":3,"tool_name":"read_file","arguments":{"path":"fixture.txt"}}
    run.update(status="waiting_confirmation",pending_confirmations=[approval],confirmations=[approval])
    state["actions"]=[{"id":"fixture-approve","type":"approve_tool","run_id":run["run_id"],
                       "confirmation_id":approval["id"],"expected_revision":3}]
    app.hub.read.side_effect=lambda:state
    island._poll()
    settle(island)
    island._toggle()
    assert not island.expanded and island.pendant.motion.state=="pull"
    # Simulate a failed graphics device; the queued expansion must continue,
    # and the monitor and real approval routing must remain usable.
    surface=island.pendant.surface
    if surface:
        with patch.object(surface,"paint",side_effect=OSError("fixture graphics failure")):
            island.pendant._paint()
    else:
        clock[0]+=1
        island.pendant._paint()
    assert island.expanded
    settle(island)
    assert island._approval_details._visible
    button=next(b for b in island._buttons if b["key"]=="fixture-approve")
    assert button["enabled"]
    island._activate(button)
    app.hub.send.assert_called_once_with("approve_tool",run["run_id"],
        {"confirmation_id":"fixture-approval","expected_revision":3})
    island._poll()
    assert "poll" in island._jobs and not island._hidden


def test_native_character_click_uses_same_pull_then_expansion(native_app,monkeypatch):
    from ctypes import wintypes as wt
    from monitor.UI.rope_surface import POINT

    app,_ = native_app
    png(app.store.root/"rope"/"cat.png")
    clock = [500.]
    monkeypatch.setattr("monitor.UI.rope_pendant.time.monotonic",lambda:clock[0])
    island = app.island
    settle(island)
    app.toggle_rope(True)
    pendant = island.pendant
    clock[0] += 1
    pendant._paint()
    app.root.update()
    x,y = pendant.size[0]//2,pendant.size[1]*3//4
    assert pendant.rendered_image.getpixel((x,y))[3] == 255
    if pendant.surface:
        # Verify Windows hit testing independently of Tk event dispatch: the
        # character receives pointer input, while transparent pixels do not.
        pendant.window.lift()
        pendant._paint()
        user = pendant.surface.user
        user.WindowFromPoint.argtypes,user.WindowFromPoint.restype = [POINT],wt.HWND
        left,top = round(pendant.anchor[0]-pendant.size[0]/2),round(pendant.anchor[1])
        hit = user.WindowFromPoint(POINT(left+x,top+y))
        assert user.GetAncestor(hit,2)==pendant.surface.hwnd
        blank = user.WindowFromPoint(POINT(left,top))
        assert user.GetAncestor(blank,2)!=pendant.surface.hwnd
    def click():
        pendant.canvas.event_generate("<ButtonPress-1>",x=x,y=y)
        pendant.canvas.event_generate("<ButtonRelease-1>",x=x,y=y)
    click()
    assert pendant.motion.state=="pull" and not island.expanded
    click()
    assert not island.expanded
    clock[0] += .25
    pendant._paint()
    assert not island.expanded
    clock[0] += .25
    pendant._paint()
    assert island.expanded and not island.details
    assert pendant.window.state()=="withdrawn"
    # A stale character event cannot advance an already expanded card.
    island._activate_pendant()
    assert not island.details
    app.hub.send.assert_not_called()


def test_native_character_blank_drag_and_hidden_release_do_not_expand(native_app,monkeypatch):
    app,_ = native_app
    png(app.store.root/"rope"/"cat.png")
    clock = [600.]
    monkeypatch.setattr("monitor.UI.rope_pendant.time.monotonic",lambda:clock[0])
    island = app.island
    settle(island)
    app.toggle_rope(True)
    pendant = island.pendant
    clock[0] += 1
    pendant._paint()
    x,y = pendant.size[0]//2,pendant.size[1]*3//4
    visible = SimpleNamespace(x=x,y=y,x_root=300,y_root=300)
    blank = SimpleNamespace(x=0,y=0,x_root=300,y_root=300)
    assert not pendant._hit(blank) and pendant._hit(visible)
    pendant._press(blank)
    pendant._release(visible)
    assert pendant.motion.state=="reveal" and not island.expanded
    pendant._press(visible)
    pendant._motion(SimpleNamespace(x_root=300+10*pendant.scale,y_root=300))
    pendant._release(visible)
    assert pendant.motion.state=="reveal" and not island.expanded
    pendant._press(visible)
    pendant.hide()
    pendant._release(visible)
    assert pendant.callback is None and not island.expanded
    pendant.close()
    pendant._release(visible)
    assert not island.expanded


def test_transient_creation_failure_retries_without_busy_loop_or_orphans(native_app, monkeypatch):
    app, _ = native_app
    png(app.store.root/"rope"/"cat.png")
    clock = [700.]
    monkeypatch.setattr("monitor.UI.rope_pendant.time.monotonic", lambda: clock[0])
    settle(app.island)
    pendant = app.island.pendant
    # Fail after Toplevel allocation: cleanup must include that partial window.
    with patch("monitor.UI.rope_pendant.tk.Canvas", side_effect=tk.TclError("fixture: temporary creation failure")) as create_canvas:
        app.toggle_rope(True)
        for _ in range(20):
            app.island._sync_pendant()
        assert create_canvas.call_count == 1
        assert pendant.unavailable and pendant.window is None and pendant.surface is None
        assert pendant.job is None
        clock[0] += .26
        app.island._sync_pendant()
        assert create_canvas.call_count == 2 and pendant.window is None
        for _ in range(20):
            app.island._sync_pendant()
        assert create_canvas.call_count == 2
    clock[0] += .51
    app.island._sync_pendant()
    clock[0] += .41
    pendant._paint()
    assert not pendant.unavailable and pendant.window.winfo_viewable()
    assert pendant.alpha == 1
    pendant.close()
    clock[0] += 10
    app.island._sync_pendant()
    assert pendant.window is None and pendant.job is None
    app.hub.send.assert_not_called()


def test_remapping_same_character_restores_pixels_and_keeps_fade_deadline(native_app, monkeypatch):
    app, _ = native_app
    png(app.store.root/"rope"/"cat.png")
    clock = [800.]
    monkeypatch.setattr("monitor.UI.rope_pendant.time.monotonic", lambda: clock[0])
    settle(app.island)
    app.toggle_rope(True)
    pendant = app.island.pendant
    # Frequent source/UI refreshes must not push back the 200ms reveal delay.
    for i in range(10):
        clock[0] = 800 + i*.015
        app.island._sync_pendant()
    assert pendant.motion.at == 800
    clock[0] = 800.41
    pendant._paint()
    assert pendant.window.winfo_viewable() and pendant.alpha == 1
    window = pendant.window
    surface = pendant.surface
    pendant.hide()
    assert not window.winfo_viewable()
    # The native wrapper and geometry can remain identical across hide/show;
    # the paint cache must still be invalidated for its first mapped frame.
    clock[0] = 801
    app.island._sync_pendant()
    if surface:
        assert surface._last_paint is None
    clock[0] += .41
    pendant._paint()
    assert pendant.window is window and window.winfo_viewable() and pendant.alpha == 1
    if surface:
        assert surface._last_paint[-1] & 0xff == 255  # ctypes BYTE can expose a signed value.
