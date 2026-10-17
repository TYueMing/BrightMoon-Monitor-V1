"""Smooth status lighting on solid/wallpaper silhouettes, without extra rims."""
from copy import deepcopy

from PIL import Image, ImageChops, ImageDraw
import pytest

from monitor.UI.island_surface import IslandSurface
from monitor.UI.wallpapers import IslandWallpaper
from test_wallpapers import native_app


@pytest.mark.parametrize("scale",[1,1.5,2])
def test_glow_fades_continuously_and_clips_to_real_wallpaper(scale):
    surface = IslandSurface()
    mask = surface.glow_mask(16,scale)
    center = mask.width//2
    falloff = [mask.getpixel((center+round(distance*scale),center)) for distance in range(16,29)]
    assert falloff[0]==255 and falloff[-1]==0
    assert all(a>=b for a,b in zip(falloff,falloff[1:]))
    assert max(a-b for a,b in zip(falloff,falloff[1:]))<70
    photo = Image.new("RGB",(480,580))
    draw = ImageDraw.Draw(photo)
    for x in range(480):
        draw.line((x,0,x,580),fill=(x%255,80+x//4,120))
    base = IslandWallpaper().render(photo,(round(290*scale),round(51*scale)),
        scale=scale,radius=round(27*scale),color="#724b5c")
    requests = [(29,26,16,"#ffb638",1)]
    result = surface.halos(base,requests,scale)
    assert ImageChops.difference(result.getchannel("A"),base.getchannel("A")).getbbox() is None
    assert result.getpixel((0,0))[3]==0
    untouched=(round(70*scale),0,result.width,result.height)
    assert ImageChops.difference(result.crop(untouched).convert("RGB"),
                                base.crop(untouched).convert("RGB")).getbbox() is None
    assert ImageChops.difference(result.convert("RGB"),base.convert("RGB")).getbbox() is not None
    assert surface.halos(base,requests,scale) is result
    other = surface.solid(base.size,round(27*scale),"#ffffff")
    assert surface.halos(other,requests,scale) is not result
    assert surface.halos(other,[],scale) is None


def test_native_multi_task_glows_share_one_layer_below_logos(native_app):
    app,_ = native_app
    island = app.island
    state = app.hub.read()
    original = state["runs"][0]
    state["runs"] = [dict(deepcopy(original),run_id=f"codex:light-{index}",turn_id=f"light-{index}")
                     for index in range(5)]
    app.hub.read.side_effect=lambda:state
    island._poll()
    for job in island._jobs.values():
        island.root.after_cancel(job)
    island._jobs.clear()
    island._animation=None
    island._opening=False
    island._size=(440,62)
    island.task_strip.focus_levels = {r["run_id"]:.7 for r in island.selection.runs}
    island.draw()
    canvas=island.canvas
    halos=canvas.find_withtag("task_ring_halo")
    assert len(halos)==1 and canvas.type(halos[0])=="image"
    order=canvas.find_all()
    surface=canvas.find_withtag("island_surface")[0]
    assert order.index(surface)<order.index(halos[0])
    for item in canvas.find_withtag("app_logo")+canvas.find_withtag("task_ring"):
        assert order.index(halos[0])<order.index(item)
    assert len(canvas.find_withtag("app_logo"))==3
    app.hub.send.assert_not_called()
