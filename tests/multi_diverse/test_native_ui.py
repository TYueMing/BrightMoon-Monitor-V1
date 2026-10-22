import tkinter as tk
from unittest.mock import Mock
from monitor.app import MonitorApp
from monitor.settings import SettingsStore
from monitor.multi_diverse.protocol import project,remote_run
from monitor.multi_diverse.config import grants
from monitor.UI.display import enable_dpi_awareness
from test_peers import sample

def test_native_split_same_width_and_phone_icon(tmp_path):
    enable_dpi_awareness();root=tk.Tk();store=SettingsStore(tmp_path)
    store.update(monitor_sound_enabled=False)
    hub=Mock();hub.read.side_effect=lambda:sample()
    app=MonitorApp(root,store,hub)
    try:
        item=project(sample('remote'),'other',grants({'view_details':True}))[0][0]
        run=remote_run(item,dict(id='other',name='测试笔记本'))
        app.peers.snapshot=lambda:[run]
        app.island.phone_status=lambda:dict(state='running',connected_device_count=1)
        app.island._poll();root.update()
        ui=app.island
        for job in ui._jobs.values():ui.root.after_cancel(job)
        ui._jobs.clear();ui._opening=False;ui._animation=None
        ui._size=(420,256);ui.expanded=True;ui.details=False
        ui._geometry(420,256,200,100);ui.draw();root.update()
        assert ui._split() and ui._target_size()==(420,256)
        assert ui._surface_frame.size==(round(420*ui.scale),round(256*ui.scale))
        for y in range(62,74):assert ui._surface_frame.getpixel((round(100*ui.scale),round(y*ui.scale)))[3]==0
        for x in range(348,358):assert ui._surface_frame.getpixel((round(x*ui.scale),round(31*ui.scale)))[3]==0
        assert ui.canvas.find_withtag('phone_connected')
        # Gap must be transparent even with wallpaper/halos.
        assert ui._surface_frame.getpixel((round(100*ui.scale),round(68*ui.scale)))[3]==0
        ui._activate(dict(key='peer:origin:remote'))
        ui._origin_at-=1;ui._animation=None;ui.draw()
        assert ui.expanded and ui._target_size()==(420,256)
        assert not ui.canvas.find_withtag('phone_connected')
        assert ui._run()['remote_origin']=='other'
        before=set(ui.root.winfo_children());ui.open_picker();ui.draw()
        assert set(ui.root.winfo_children())==before and ui.picker
        ui._escape();assert not ui.picker and ui.expanded
        ui._activate(dict(key='peer:details'));assert ui._target_size()==(420,592)
        ui._activate(dict(key='peer:origin:remote'));assert not ui.expanded
        app.peers.snapshot=lambda:[];ui._poll();assert not ui._split()
        assert ui._target_size()==(300,62)
    finally:app.close()


def test_native_peer_approval_preserves_widget_and_source_dimensions(tmp_path):
    from test_commands import awaiting
    from monitor.UI.detail_panels import panel_for
    from monitor.multi_diverse.UI.geometry import SizeContext
    root=tk.Tk();store=SettingsStore(tmp_path);store.update(monitor_sound_enabled=False)
    hub=Mock();hub.read.side_effect=lambda:sample();app=MonitorApp(root,store,hub)
    try:
        data=awaiting();entity=project(data,'other',grants({'agent_approve':True}))[0][0]
        entity['capability']='fixture';run=remote_run(entity,dict(id='other',name='Approval fixture'))
        app.peers.snapshot=lambda:[run];ui=app.island;ui._poll();root.update()
        for job in ui._jobs.values():ui.root.after_cancel(job)
        ui._jobs.clear();ui._opening=False;ui._animation=None
        ui.origin='remote';ui.expanded=True;ui._size=ui._target_size();ui._geometry(*ui._size,200,100);ui.draw()
        assert ui._target_size()==(420,452) and ui._approval_details._visible
        details=ui._approval_details;key=details.key;geometry=details._geometry
        details.hide=Mock(wraps=details.hide)
        for _ in range(3):ui.draw()
        assert details.key==key and details._geometry==geometry and details.hide.call_count==0
        assert any(b['key'].startswith('peer:act:approve_tool') and b['enabled'] for b in ui._buttons)
        for source in ('codex','deepseek_harness','opencode','solo_task'):
            data=sample();data['runs'][0]['app_id']=source
            if source=='solo_task':
                import time
                data['runs'][0]['reminder']=dict(id='task',cycle=0,revision=1,due_at=time.time()+200,remind_at=time.time(),expires_at=time.time()+200)
            item=project(data,'other',grants({'view_details':True}))[0][0]
            active=remote_run(item,dict(id='other',name='Source fixture'))
            ui.all_visible={active['run_id']:active};ui.origin_focus['remote']=active['run_id']
            for stage in (False,True):
                ui.details=stage
                expected={'codex':(244,580),'deepseek_harness':(212,286),'opencode':(242,410),'solo_task':(282,412)}
                assert ui._target_size()==(420,expected[source][int(stage)]+12)
    finally:app.close()
