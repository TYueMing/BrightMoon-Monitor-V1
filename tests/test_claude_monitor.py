"""Claude Code integration contract tests; never touch a real Claude profile."""
import json
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from monitor.claude_monitor import spool,installer
from monitor.claude_monitor.adapter import ClaudeSource
from monitor.claude_monitor.events import normalize,SessionProjection
from monitor.claude_monitor.hook import handle
from monitor.src.source_config import normalize_sources
from monitor.settings import SettingsStore


def event(name,session='s',prompt='p',**kw):
    return dict(hook_event_name=name,session_id=session,prompt_id=prompt,**kw)


def emit(source,name,**kw):
    spool.append_event(normalize(event(name,**kw)),source.root)
    return source.poll()


@pytest.fixture
def source(tmp_path,monkeypatch):
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(tmp_path/'claude-config'))
    s=ClaudeSource({'data_path':str(tmp_path/'mail'),'allow_approvals':True})
    yield s
    s.close()


def test_stage_tool_result_compaction_subagent_and_summary(source):
    emit(source,'UserPromptSubmit',prompt='p',prompt_text='not retained')
    emit(source,'PreToolUse',tool_name='Read',tool_use_id='a',tool_input={'file_path':'app.py'})
    snap=emit(source,'PostToolUse',tool_name='Read',tool_use_id='a',tool_response={'message':'读取完成'})
    assert snap['runs'][0]['tools'][0]['result']=='读取完成'
    emit(source,'PreToolUse',tool_name='Bash',tool_use_id='b',tool_input={'command':'pytest -q'})
    assert source.poll()['runs'][0]['claude']['stage']=='运行验证'
    snap=emit(source,'PostToolUseFailure',tool_name='Bash',tool_use_id='b',error='one test failed')
    assert snap['runs'][0]['status']=='running'
    assert snap['runs'][0]['tools'][-1]['status']=='failed'
    emit(source,'PreCompact');emit(source,'PostCompact')
    emit(source,'SubagentStart',agent_id='child',agent_type='Explore')
    emit(source,'SubagentStop',agent_id='child',agent_type='Explore',last_assistant_message='子任务完成')
    assert source.poll()['runs'][0]['status']!='completed'
    snap=emit(source,'Stop',last_assistant_message='完成修改与验证')
    run=snap['runs'][0]
    assert run['status']=='completed' and run['summary_preview']=='完成修改与验证'
    assert run['claude']['compactions']==1
    assert run['claude']['subagents']==1 and run['ended_at']


def test_repeated_turns_have_new_identity_and_late_event_stays_old(source):
    a=emit(source,'UserPromptSubmit')['runs'][0]
    emit(source,'Stop',last_assistant_message='done')
    b=emit(source,'UserPromptSubmit',prompt='p2')['runs'][0]
    assert a['run_id']!=b['run_id'] and a['turn_id']!=b['turn_id']
    emit(source,'PostToolUse',prompt='p',tool_name='Read',tool_use_id='late')
    assert next(r for r in source.poll()['runs'] if r['run_id']==a['run_id'])['status']=='completed'


def test_old_hook_without_prompt_id_gets_new_turn(source):
    a=emit(source,'UserPromptSubmit',prompt='')['runs'][0]
    emit(source,'Stop',prompt='')
    b=emit(source,'UserPromptSubmit',prompt='')['runs'][0]
    assert a['run_id']!=b['run_id']


def test_api_failure_interrupt_and_background_stop(source):
    emit(source,'UserPromptSubmit')
    assert emit(source,'StopFailure',error='rate_limit')['runs'][0]['status']=='failed'
    emit(source,'UserPromptSubmit',prompt='p2')
    snap=emit(source,'Stop',prompt='p2',background_tasks=[{'id':'shell'}])
    assert next(r for r in snap['runs'] if r['turn_id']=='p2')['status']=='paused'
    emit(source,'UserPromptSubmit',prompt='p3')
    snap=emit(source,'PostToolUseFailure',prompt='p3',is_interrupt=True,tool_name='Bash')
    assert next(r for r in snap['runs'] if r['turn_id']=='p3')['status']=='interrupted'


def wait_request(source):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        snap=source.poll()
        if snap['actions']:return snap
        time.sleep(.02)
    raise AssertionError('Hook request not delivered')


@pytest.mark.parametrize('action,behavior',[('approve_tool','allow'),('deny_tool','deny')])
def test_real_hook_subprocess_approval_roundtrip(source,action,behavior):
    emit(source,'UserPromptSubmit')
    entry=Path(installer.__file__).with_name('hook_entry.py')
    proc=subprocess.Popen([sys.executable,str(entry),'--spool',str(source.root),'--wait','5'],
                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        proc.stdin.write(json.dumps(event('PermissionRequest',tool_name='Read',tool_input={'file_path':'中文.py'})).encode())
        proc.stdin.close();proc.stdin=None
        snap=wait_request(source);selected=next(a for a in snap['actions'] if a['type']==action)
        stable=source.poll()['actions']
        assert stable==snap['actions']  # Animation/polls must not change request identity.
        source.send(action,selected['run_id'],selected)
        with pytest.raises(ValueError):source.send(action,selected['run_id'],selected)
        out,err=proc.communicate(timeout=5)
        assert not err and proc.returncode==0
        answer=json.loads(out)
        assert answer['hookSpecificOutput']['decision']['behavior']==behavior
        assert 'updatedPermissions' not in answer['hookSpecificOutput']['decision']
        assert source.poll()['actions']==[]
    finally:
        if proc.poll() is None:proc.kill();proc.wait()


def test_cross_run_revision_and_closed_monitor_reject(source):
    emit(source,'UserPromptSubmit')
    with ThreadPoolExecutor() as pool:
        future=pool.submit(handle,event('PermissionRequest',tool_name='Read',tool_input={'path':'x'}),source.root,3)
        a=wait_request(source)['actions'][0]
        with pytest.raises(ValueError):source.send(a['type'],'other-run',a)
        with pytest.raises(ValueError):source.send(a['type'],a['run_id'],{**a,'expected_revision':'wrong'})
        source.close()
        assert future.result(timeout=3) is None


def test_monitor_off_and_timeout_never_allow(tmp_path,source):
    start=time.monotonic()
    assert handle(event('PermissionRequest'),tmp_path/'absent',1) is None
    assert time.monotonic()-start<.5
    emit(source,'UserPromptSubmit')
    assert handle(event('PermissionRequest',tool_name='Bash',tool_input={'command':'echo test'}),source.root,.2) is None
    assert source.poll()['actions']==[]


def test_observation_only_and_large_parameters_have_no_action(source):
    emit(source,'UserPromptSubmit')
    handle(event('PermissionRequest',tool_name='Read',tool_input={'file_path':'x'*7000}),source.root,.2)
    snap=source.poll()
    assert not snap['actions'] and snap['runs'][0]['status']=='waiting_confirmation'
    assert snap['runs'][0]['pending_confirmations'][0]['truncated']


def test_event_dedup_and_concurrent_writes(source):
    e=normalize(event('UserPromptSubmit'))
    spool.append_event(e,source.root);spool.append_event(e,source.root)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i:spool.append_event(normalize(event('PreToolUse',tool_name='Read',tool_use_id=str(i))),source.root),range(20)))
    snap=source.poll()
    assert len(snap['runs'])==1 and snap['runs'][0]['claude']['events']==21
    assert len(snap['runs'][0]['tools'])==20


def test_private_payload_not_persisted():
    raw=event('PreToolUse',tool_name='Read',tool_input={'api_key':'do-not-store','content':'private-file-body'},
              transcript_path='private-transcript')
    raw['prompt']='private prompt'
    value=json.dumps(normalize(raw))
    assert all(x not in value for x in ('do-not-store','private-file-body','private-transcript','private prompt'))


def test_install_repair_uninstall_preserves_user_hooks_and_settings(tmp_path):
    path=tmp_path/'settings.json';root=tmp_path/'有 空格'
    original={'permissions':{'deny':['Bash(rm *)']},'hooks':{'Stop':[{'hooks':[{'type':'command','command':'echo user-hook'}]}]}}
    path.write_text(json.dumps(original),encoding='utf-8')
    installer.install(path,root);installer.install(path,root)
    value=json.loads(path.read_text(encoding='utf-8'))
    assert value['permissions']==original['permissions']
    assert sum(installer.owned(h) for row in value['hooks']['Stop'] for h in row['hooks'])==1
    assert installer.status(path,root)['installed']
    command=next(h['command'] for row in value['hooks']['Stop'] for h in row['hooks'] if installer.owned(h))
    args=shlex.split(command);assert str(root.resolve()).replace('\\','/') in args
    assert all('metadata' not in h for row in value['hooks']['Stop'] for h in row['hooks'])
    installer.uninstall(path)
    assert json.loads(path.read_text())==original
    assert list(tmp_path.glob('*.solo-backup-*'))


def test_install_invalid_json_and_disabled_hooks_preserved(tmp_path):
    path=tmp_path/'settings.json';path.write_text('{broken')
    with pytest.raises(ValueError):installer.install(path,tmp_path/'mail')
    assert path.read_text()=='{broken'
    path.write_text('{"disableAllHooks":true}')
    with pytest.raises(ValueError):installer.install(path,tmp_path/'mail')
    assert path.read_text()=='{"disableAllHooks":true}'


def test_settings_persist_and_source_registered(tmp_path):
    store=SettingsStore(tmp_path/'data');store.set_source('claude',enabled=True,allow_approvals=True)
    assert SettingsStore(store.root).snapshot()['monitor_sources']['claude']['allow_approvals']
    from monitor.src.source_hub import source_factories
    from monitor.UI.detail_panels import panel_for
    from monitor.claude_monitor.panel import ClaudePanel
    assert source_factories()['claude'] is ClaudeSource
    assert isinstance(panel_for('claude'),ClaudePanel)
    with pytest.raises(ValueError):normalize_sources({'claude':{'allow_approvals':'yes'}},strict=True)


def test_native_settings_and_three_state_detail_layout(tmp_path,monkeypatch):
    import tkinter as tk
    from monitor.app import MonitorApp
    from monitor.src.source_hub import SourceHub
    from monitor.claude_monitor.events import stamp
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(tmp_path/'claude'))
    try:root=tk.Tk()
    except tk.TclError:pytest.skip('Tk display unavailable')
    store=SettingsStore(tmp_path/'settings');store.update(monitor_sound_enabled=False)
    store.set_source('claude',enabled=True,data_path=str(tmp_path/'mail'))
    hub=SourceHub(store.snapshot,start_worker=False)
    app=MonitorApp(root,store,hub)
    errors=[];root.report_callback_exception=lambda *args:errors.append(args)
    try:
        root.update();hub.poll_sources()
        assert 'source:claude' in app.panel.controls
        assert app.panel.controls['source:claude'].rect[3]<app.panel.controls['source:codeagent'].rect[1]
        app.source_dialog('claude');root.update()
        controls=app.dialog.claude_controls
        controls['approvals'].set(True);controls['install']();root.update()
        assert installer.is_installed()
        controls['save']();hub.poll_sources()
        source=hub.workers['claude'].source
        emit(source,'UserPromptSubmit')
        for i in range(15):
            emit(source,'PreToolUse',tool_name='Read',tool_use_id=str(i),tool_input={'file_path':'file'+str(i)+'.py'})
            emit(source,'PostToolUse',tool_name='Read',tool_use_id=str(i),tool_response={'message':'result'})
        hub.poll_sources();app.island._poll();root.update()
        ui=app.island
        ui.expanded=True;ui.details=False
        ui._animation=None;ui._opening=False;ui._size=ui._target_size();ui._geometry(*ui._size,40,40)
        ui.draw();root.update()
        assert ui._run()['app_id']=='claude'
        panel=__import__('monitor.UI.detail_panels',fromlist=['panel_for']).panel_for('claude')
        assert panel.size(ui)[1]==244
        ui.details=True;ui._size=panel.size(ui);ui._geometry(*ui._size,40,40);ui.draw();root.update()
        assert any(b['key']=='panel:claude:next' for b in ui._buttons)
        assert panel.scroll(ui,4)
        emit(source,'Stop',last_assistant_message='任务摘要：完成了修改和验证。')
        hub.poll_sources();ui._poll();ui.expanded=True;ui.details=False;ui._size=panel.size(ui);ui.draw();root.update()
        assert any('任务摘要' in ui.canvas.itemcget(i,'text') for i in ui.canvas.find_all() if ui.canvas.type(i)=='text')
        ui._completion_rows[ui._run()['run_id']]=True
        ui._size=panel.size(ui);ui.draw();root.update()
        assert panel.opened(ui)
        ui._apply_toggle();assert not ui.expanded  # Completed task header collapses entire island.
        assert not errors
    finally:
        app.close()


def test_native_live_approval_is_stable_and_click_routes_to_hook(tmp_path,monkeypatch):
    import tkinter as tk
    from monitor.app import MonitorApp
    from monitor.src.source_hub import SourceHub
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(tmp_path/'claude'))
    try:root=tk.Tk()
    except tk.TclError:pytest.skip('Tk display unavailable')
    store=SettingsStore(tmp_path/'settings');store.update(monitor_sound_enabled=False)
    store.set_source('claude',enabled=True,allow_approvals=True,data_path=str(tmp_path/'mail'))
    hub=SourceHub(store.snapshot,start_worker=False);app=MonitorApp(root,store,hub)
    try:
        hub.poll_sources();source=hub.workers['claude'].source
        emit(source,'UserPromptSubmit')
        with ThreadPoolExecutor() as pool:
            future=pool.submit(handle,event('PermissionRequest',tool_name='Read',tool_input={'file_path':'sample.py'}),source.root,8)
            wait_request(source);hub.poll_sources();app.island._poll();root.update()
            ui=app.island;ui.expanded=True;ui._animation=None;ui._opening=False
            from monitor.UI.detail_panels import panel_for
            ui._size=panel_for('claude').size(ui);ui._geometry(*ui._size,40,40);ui.draw();root.update()
            assert ui._approval_details._visible
            original=ui._approval_details.key
            ui._approval_details.text.yview_moveto(.5)
            for _ in range(5):ui.draw()
            assert ui._approval_details.key==original
            button=next(b for b in ui._buttons if (b.get('action') or {}).get('type')=='approve_tool')
            assert button['enabled']
            action=button['action']
            hub.send(action['type'],action['run_id'],action)
            result=future.result(timeout=5)
            assert result['hookSpecificOutput']['decision']['behavior']=='allow'
    finally:app.close()


def test_restart_never_replays_finished_spool(tmp_path,monkeypatch):
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(tmp_path/'profile'))
    root=tmp_path/'mail'
    for i in range(600):
        spool.append_event(normalize(event('UserPromptSubmit',prompt=str(i))),root)
    spool.append_event(normalize(event('Stop',prompt='599',last_assistant_message='old')),root)
    s=ClaudeSource({'data_path':str(root)})
    try:
        assert s.poll()['runs']==[]
        snap=emit(s,'UserPromptSubmit',prompt='new')
        assert len(snap['runs'])==1 and snap['runs'][0]['turn_id']=='new'
    finally:s.close()


def test_only_one_live_monitor_lease_and_reacquire_after_release(source):
    with pytest.raises(ValueError):ClaudeSource({'data_path':str(source.root)})
    source.close()
    other=ClaudeSource({'data_path':str(source.root)})
    other.close()


def test_dead_hook_request_expires_and_stale_task_retires(source):
    emit(source,'UserPromptSubmit')
    e=normalize(event('PermissionRequest',tool_name='Bash',tool_input={'command':'echo x'}))
    e['request']=spool.new_request(source.root,e,60);spool.append_event(e,source.root)
    assert source.poll()['actions']
    with spool.connection(source.root) as db:db.execute('UPDATE requests SET heartbeat=?',(time.time()-5,))
    snap=source.poll();assert not snap['actions'] and snap['runs'][0]['status']=='paused'


def test_process_exit_is_interrupted_with_detection_timestamp(source,monkeypatch):
    emit(source,'UserPromptSubmit')
    p=next(iter(source.sessions.values()));p.run['claude']['process']={'pid':123,'birth':'456'}
    monkeypatch.setattr('monitor.claude_monitor.process.alive',lambda _:False)
    snap=source.poll();run=snap['runs'][0]
    assert run['status']=='interrupted' and abs(time.time()-__import__('datetime').datetime.fromisoformat(run['ended_at']).timestamp())<1


def test_source_hub_and_phone_peer_projection(source,tmp_path):
    from monitor.src.source_hub import SourceHub
    from monitor.phone.projector import PhoneProjector
    from monitor.multi_diverse.config import grants
    from monitor.multi_diverse.protocol import project
    prefs={'monitor_sources':{'claude':{'enabled':True}}}
    hub=SourceHub(lambda:prefs,factories={'claude':lambda _:source},start_worker=False)
    try:
        emit(source,'UserPromptSubmit');emit(source,'PreCompact');emit(source,'PostCompact')
        hub.poll_sources();snap=hub.read();run=snap['runs'][0]
        phone=PhoneProjector(b'test').task(run,False,0)
        assert phone['app_id']=='claude' and phone['chain']['kind']=='observed'
        assert any(b['id']=='metrics' for b in phone['detail_blocks'])
        public,targets=project(snap,'owner',grants({'view_status':True,'view_details':True,'sources':['claude']}))
        assert public and public[0]['source']=='claude'
        assert 'process' not in json.dumps(public)
    finally:hub.close()


def test_uninstall_keeps_empty_and_mixed_unowned_groups(tmp_path):
    path=tmp_path/'settings.json';root=tmp_path/'mail'
    installer.install(path,root)
    value=json.loads(path.read_text(encoding='utf-8'))
    value['hooks']['Stop'][0]['hooks'].append({'type':'command','command':'echo keep'})
    value['hooks']['Custom']=[{'hooks':[]}]
    value['hooks']['Other']=[]
    path.write_text(json.dumps(value),encoding='utf-8')
    installer.uninstall(path)
    value=json.loads(path.read_text(encoding='utf-8'))
    assert value['hooks']['Custom']==[{'hooks':[]}]
    assert value['hooks']['Other']==[]
    assert value['hooks']['Stop'][0]['hooks']==[{'type':'command','command':'echo keep'}]


def test_multiline_authorization_parameters_preserved():
    e=normalize(event('PermissionRequest',tool_name='Write',tool_input={'content':'line 1\nline 2\t中文'}))
    assert e['arguments']['content']=='line 1\nline 2\t中文' and not e['truncated']
    e=normalize(event('PermissionRequest',tool_input={'api_key':'not-for-display'}))
    assert e['truncated'] and 'not-for-display' not in json.dumps(e)


def test_main_hook_dispatch_does_not_open_desktop(tmp_path):
    run=Path(__file__).parents[1]/'run.py'
    result=subprocess.run([sys.executable,str(run),'--claude-hook','--spool',str(tmp_path/'missing')],
                          input=json.dumps(event('UserPromptSubmit')),encoding='utf-8',capture_output=True,timeout=5)
    assert result.returncode==0 and result.stdout=='' and result.stderr==''


def test_stop_hook_continuation_reopens_on_real_tool_start(source):
    emit(source,'UserPromptSubmit')
    old=emit(source,'Stop',last_assistant_message='response ended')['runs'][0]
    new=emit(source,'PreToolUse',tool_name='Bash',tool_use_id='continued',tool_input={'command':'pytest'})['runs'][0]
    assert new['status']=='running' and new['run_id']!=old['run_id']
    assert new['claude']['time_basis']=='observed'
    final=emit(source,'Stop',last_assistant_message='now done')['runs'][0]
    assert final['run_id']==new['run_id'] and final['status']=='completed'
