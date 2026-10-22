"""Synthetic-data native previews, no personal settings or live agents."""
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from render_wallpaper_preview import capture,settle
import tkinter as tk
from unittest.mock import Mock
from monitor.app import MonitorApp
from monitor.settings import SettingsStore
from monitor.UI.display import enable_dpi_awareness
from monitor.multi_diverse.protocol import project,remote_run
from monitor.multi_diverse.config import grants
from test_peers import sample

def main():
    out=Path(__file__).resolve().parents[2]/'preview'/'multi_diverse';out.mkdir(parents=True,exist_ok=True)
    enable_dpi_awareness()
    with tempfile.TemporaryDirectory() as temp:
        root=tk.Tk();store=SettingsStore(temp);store.update(monitor_sound_enabled=False,peers={'name':'我的工作站'})
        hub=Mock();hub.read.side_effect=lambda:sample()
        app=MonitorApp(root,store,hub)
        try:
            row=project(sample('remote'),'other',grants({'view_details':True}))[0][0]
            row['summary']='正在检查多端协同交互与界面，测试结果将同步到本机。'
            row['steps']=[dict(name=n,status=s) for n,s in [('读取项目','completed'),('确认计划','completed'),('运行测试','running'),('核对结果','pending')]]
            row['chain_total']=4
            row['tools']=[dict(name='pytest',summary='示例：检查已通过',status='completed')]
            run=remote_run(row,dict(id='other',name='设计笔记本'));run['remote_elapsed_seconds']=204
            app.peers.snapshot=lambda:[run];app.island.phone_status=lambda:dict(connected_device_count=1)
            ui=app.island;ui._poll();settle(app)
            for name,origin,stage in [('01-local-mini','local',1),('02-remote-stage2','remote',2),('03-remote-stage3','remote',3),('04-local-stage2','local',2)]:
                ui.origin=origin;ui.expanded=stage>1;ui.details=stage==3
                ui._origin_amount=ui._origin_target=float(origin=='remote');ui._origin_at=0
                ui._size=ui._target_size();ui._geometry(*ui._size,200,100);ui.draw();root.update()
                capture(ui.root).save(out/(name+'.png'))
            ui.origin='remote';ui._origin_amount=ui._origin_target=1.;ui.details=False;ui.picker=True
            ui._size=ui._target_size();ui._geometry(*ui._size,200,100);ui.draw();root.update();capture(ui.root).save(out/'05-picker.png')
            from test_commands import awaiting
            pending=awaiting();pending['actions'].append({**pending['actions'][0],'type':'deny_tool'})
            approval=project(pending,'other',grants({'agent_approve':True}))[0][0];approval['capability']='fixture'
            run=remote_run(approval,dict(id='other',name='设计笔记本'));run['remote_elapsed_seconds']=42
            app.peers.snapshot=lambda:[run];ui._poll();settle(app);ui.origin='remote';ui.origin_focus['remote']=run['run_id'];ui.picker=False
            ui._origin_amount=ui._origin_target=1.;ui.expanded=True;ui.details=False
            ui._size=ui._target_size();ui._geometry(*ui._size,200,100);ui.draw();root.update();capture(ui.root).save(out/'06-approval.png')
            app.phone.discovery=lambda:[];app.peers.discovery=lambda:[]
            app.peers.snapshot_status=lambda:dict(state='running',message='已开启 · 加密双向连接',address='https://192.168.1.10:18766',pending=[],peers=[dict(id='fixture',name='设计笔记本',address='',online=True,grants=grants(),fingerprint='fixture',omitted_count=0)])
            app.show_phone_settings();root.update();app.phone_dialog.select('peers');root.update()
            capture(root).save(out/'07-settings.png')
        finally:app.close()
if __name__=='__main__':main()
