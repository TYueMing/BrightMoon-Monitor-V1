"""Native canvas clicks cycle whole cards without stealing control actions."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_wallpapers import native_app
from test_rope_personalization import settle, png
from test_tasks_ui import silence, imminent
from test_resident_island import empty_source


def click(island,x,y):
    for event in ("<ButtonPress-1>","<ButtonRelease-1>"):
        island.canvas.event_generate(event,x=round(x*island.scale),y=round(y*island.scale))


def test_idle_text_and_character_click_cycle_second_third_and_capsule(native_app):
    app,_=native_app
    silence(app)
    empty_source(app)
    png(app.store.root/"cartoon"/"fixture.png")
    app.load_cartoon()
    app.toggle_resident(True)
    island=app.island
    settle(island)
    click(island,150,31)
    settle(island)
    assert island.expanded and not island.details
    click(island,180,145)  # Expanded text, not capsule header.
    settle(island)
    assert island.expanded and island.details
    art=island.canvas.bbox(island.canvas.find_withtag("resident_idle_character")[0])
    click(island,(art[0]+art[2])/2/island.scale,(art[1]+art[3])/2/island.scale)
    settle(island)
    assert not island.expanded and not island.details
    app.hub.send.assert_not_called()


@pytest.mark.parametrize("status",["running","completed"])
def test_agent_card_body_preserves_stage_rules(native_app,status):
    app,_=native_app
    silence(app)
    app.hub.read()["runs"][0]["status"]=status
    island=app.island
    island._poll()
    settle(island)
    click(island,150,31)
    settle(island)
    assert island.expanded
    click(island,island.WIDTH-30,80)
    settle(island)
    if status=="completed":
        assert not island.expanded
    else:
        assert island.details
        click(island,island.WIDTH-30,80)
        settle(island)
        assert not island.expanded
    app.hub.send.assert_not_called()


@pytest.mark.parametrize("action",["snooze","acknowledge"])
def test_real_reminder_button_keeps_its_action_after_body_expands(native_app,action):
    app,_=native_app
    silence(app)
    empty_source(app)
    task=app.tasks.store.save(imminent())
    app.tasks_changed()
    island=app.island
    settle(island)
    click(island,150,31)
    settle(island)
    click(island,100,143)
    settle(island)
    assert island.details
    button=next(b for b in island._buttons if b["key"]=="panel:reminder-"+action)
    x1,y1,x2,y2=button["box"]
    click(island,(x1+x2)/2,(y1+y2)/2)
    assert app.tasks.store.state_for(task["id"])["state"]==("snoozed" if action=="snooze" else "acknowledged")
    app.hub.send.assert_not_called()


def test_disabled_controls_and_body_drag_never_trigger_background_toggle(native_app):
    app,_=native_app
    island=app.island
    settle(island)
    click(island,150,31)
    settle(island)
    island._register((100,100,180,140),"disabled-approval",enabled=False)
    click(island,120,120)
    assert island.expanded and not island.details
    sender=Mock()
    island._send_action=sender
    action={"type":"approve","id":"fixture"}
    island._register((200,100,280,140),"approve",action=action)
    click(island,220,120)
    sender.assert_called_once_with(action)
    assert island.expanded and not island.details
    x,y=round(340*island.scale),round(85*island.scale)
    point=lambda dx:SimpleNamespace(x=x+dx,y=y,x_root=x+dx,y_root=y)
    position=island._position
    island._press(point(0))
    island._drag(point(20))
    island._release(point(20))
    assert island.expanded and not island.details and island._position==position
    assert island._hit(-1,85) is None
    assert island._hit(200,island._size[1]+1) is None
