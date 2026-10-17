"""Custom idle artwork, local ownership, persistence and real Tk selection."""
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pytest

from monitor.settings import SettingsStore
from monitor.src.cartoon_assets import CartoonCatalog
from test_wallpapers import native_app
from test_rope_personalization import png, settle
from test_resident_island import empty_source
from test_tasks_ui import imminent, silence


def test_catalog_scan_import_alpha_cache_and_portability(tmp_path):
    root=tmp_path/"data"
    catalog=CartoonCatalog(root)
    assert catalog.list()==[]
    original=png(tmp_path/"Jay.png")
    before=original.read_bytes()
    ref=catalog.import_image(original)
    assert ref.startswith("cartoon/Jay") and catalog.list()==[ref]
    assert original.read_bytes()==before
    image=catalog.read(ref)
    assert image is catalog.read(ref) and image.getchannel("A").getextrema()[0]==0
    assert catalog.import_image(original)==ref
    Image.new("RGB",(20,20),"white").save(tmp_path/"opaque.png")
    with pytest.raises(ValueError,match="抠图"):
        catalog.import_image(tmp_path/"opaque.png")
    Image.new("RGBA",(20,20)).save(tmp_path/"blank.png")
    with pytest.raises(ValueError):
        catalog.import_image(tmp_path/"blank.png")
    (root/"cartoon"/"broken.png").write_bytes(b"invalid")
    assert catalog.list()==[ref]
    for bad in ("../Jay.png","cartoon/../Jay.png","cartoon/a/b.png","cartoon\\Jay.png",str(original)):
        with pytest.raises(ValueError):
            catalog.path(bad)
    import shutil
    shutil.copytree(root,tmp_path/"relocated")
    assert CartoonCatalog(tmp_path/"relocated").read(ref).size==image.size
    store=SettingsStore(root)
    store.update(cartoon_enabled=False,cartoon_image=ref)
    assert SettingsStore(root).snapshot()["cartoon_image"]==ref
    assert not SettingsStore(root).snapshot()["cartoon_enabled"]
    for change in ({"cartoon_enabled":1},{"cartoon_image":"../x.png"}):
        with pytest.raises(ValueError):
            store.update(**change)


def test_cartoon_ui_picker_upload_switch_and_reminder_layout(native_app):
    app,tmp_path=native_app
    silence(app)
    state=empty_source(app)
    for i in range(8):
        png(app.store.root/"cartoon"/f"角色{i}.png")
    app.panel.select_personalization("cartoon")
    assert len(app.cartoon_items)==8
    assert len(app.panel.find_withtag("cartoon_preview"))==6
    app.panel.controls["cartoon-next"].callback()
    assert len(app.panel.find_withtag("cartoon_preview"))==2
    app.panel.controls["cartoon:cartoon/角色7.png"].callback()
    assert app.store.snapshot()["cartoon_image"]=="cartoon/角色7.png"
    upload=png(tmp_path/"My Jay.png","#123abc")
    with patch("monitor.app.filedialog.askopenfilename",return_value=str(upload)):
        app.choose_cartoon()
    assert app.store.snapshot()["cartoon_image"].startswith("cartoon/My Jay")
    app.toggle_resident(True)
    island=app.island
    settle(island)
    assert not island.canvas.find_withtag("resident_idle_character")
    island._toggle()
    settle(island)
    assert island.canvas.find_withtag("resident_idle_character")
    for text in island.canvas.find_all():
        if island.canvas.type(text)=="text":
            box=island.canvas.bbox(text)
            art=island.canvas.bbox(island.canvas.find_withtag("resident_idle_character")[0])
            assert box[2]<=art[0] or box[0]>=art[2] or box[3]<=art[1] or box[1]>=art[3]
    app.toggle_cartoon(False)
    assert not island.canvas.find_withtag("resident_idle_character")
    app.toggle_cartoon(True)
    assert island.canvas.find_withtag("resident_idle_character")
    future=datetime.now()+timedelta(minutes=40)
    app.tasks.store.save(imminent(start_date=future.date().isoformat(),time_of_day=future.strftime("%H:%M")))
    island._poll()
    settle(island)
    assert island.snapshot["next_reminder"]
    assert not island.canvas.find_withtag("resident_idle_character")
    island._toggle()
    settle(island)
    assert island.details and island.canvas.find_withtag("resident_idle_character")
    app.select_cartoon("cartoon/角色1.png")
    assert island.resident_view._character_source is app.cartoon_photo
    app.cartoon_catalog.path("cartoon/角色1.png").rename(app.store.root/"missing.png")
    app.load_cartoon()
    assert not island.canvas.find_withtag("resident_idle_character")
    assert app.store.snapshot()["cartoon_image"]=="cartoon/角色1.png"
    app.hub.send.assert_not_called()


def test_release_bundles_curated_cartoon_not_user_uploads():
    import sys
    root=Path(__file__).resolve().parents[1]
    with patch.object(sys,"path",[str(root/"packaging"),*sys.path]):
        from release_files import resource_files
        files=resource_files()
    cartoons=[Path(path).name for path,dest in files if dest=="defaults/cartoon"]
    assert cartoons==["月光星尘.png"]
    assert not any(Path(path).is_relative_to(root/"data") for path,_ in files)
