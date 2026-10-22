"""Real TLS commands use fake agents and isolated persistent directories."""
import time
from copy import deepcopy
import pytest
from test_peers import free_port,until,sample
from monitor.multi_diverse.service import PeerService
from monitor.multi_diverse.protocol import digest,project,validate_items
from monitor.multi_diverse.config import grants
from monitor.phone.network import Interface

@pytest.fixture
def peers(tmp_path):
    iface=Interface('test','Loopback','127.0.0.1',8,'Private')
    a=PeerService(tmp_path/'a',discovery=lambda:[iface]);b=PeerService(tmp_path/'b',discovery=lambda:[iface])
    try:
        for name,service in [('A',a),('B',b)]:
            service.start(dict(enabled=True,interface_id='test',port=free_port(),name=name))
            until(lambda:service.snapshot_status()['state']=='running')
        a.command('invite');b.command('join',until(lambda:a.snapshot_status().get('invite')))
        for service in (a,b):
            pending=until(lambda:service.snapshot_status()['pending'])[0]
            service.command('approve',dict(id=pending['id'],grants=grants({'view_details':True,'agent_approve':True,'agent_stop':True,'reminder_act':True})))
        until(lambda:a.snapshot_status()['peers'] and b.snapshot_status()['peers'])
        yield a,b
    finally:
        for service in (a,b):service.stop()
        for service in (a,b):
            if service.thread:service.thread.join(8)
            assert not service.thread.is_alive()

def awaiting():
    data=sample(state='waiting_confirmation')
    data['runs'][0]['pending_confirmations']=[dict(id='request1',status='pending',tool_name='检查',arguments={'command':'pytest'},reason='运行项目测试')]
    data['actions']=[dict(app_id='codex',run_id='same',type='approve_tool',confirmation_id='request1',expected_revision=7)]
    return data

def shared(a,b,data):
    a.publish_local(data);b.publish_local(sample('receiver'))
    return until(lambda:next((r for r in b.snapshot() if r['remote_entity'].get('capability')),None))

def receipt(service,id):
    return until(lambda:next((r['command_receipt'] for r in service.snapshot() if r.get('command_receipt',{}).get('command_id')==id),None))

def test_command_dispatch_receipt_replay_conflict_and_stale(peers):
    a,b=peers;data=awaiting();run=shared(a,b,data)
    payload=dict(confirmation_id='request1',expected_revision=7)
    result=b.submit(run,'approve_tool',payload);cmd=until(lambda:None if a.owner_commands.empty() else a.owner_commands.get_nowait())
    assert a.authorized(cmd) and cmd['target']['run_id']=='same'
    assert cmd['deadline']>time.monotonic() and 'expires' not in cmd
    a.command('result',dict(id=cmd['id'],peer=cmd['peer'],result=dict(status='accepted',message='fake adapter accepted')))
    assert receipt(b,result['command_id'])['status']=='accepted'
    # Replaying the SAME ID yields its durable receipt, without dispatch twice.
    wire=dict(peer=run['remote_origin'],id=result['command_id'],entity=run['remote_entity']['id'],revision=run['remote_entity']['revision'],operation='approve_tool',payload=payload,capability=run['remote_entity']['capability'])
    b.command('send',wire);time.sleep(.25);assert a.owner_commands.empty()
    # Reusing ID with changed content cannot reuse a prior success.
    b.command('send',{**wire,'payload':{**payload,'expected_revision':8}})
    until(lambda:receipt(b,result['command_id'])['status']=='rejected')
    changed=deepcopy(data);changed['runs'][0]['message']='new facts';a.publish_local(changed)
    stale=b.submit(run,'approve_tool',payload)
    assert receipt(b,stale['command_id'])['status']=='rejected' and a.owner_commands.empty()

def test_permissions_revoked_before_owner_dispatch(peers):
    a,b=peers;run=shared(a,b,awaiting());sent=b.submit(run,'approve_tool',dict(confirmation_id='request1',expected_revision=7))
    cmd=until(lambda:None if a.owner_commands.empty() else a.owner_commands.get_nowait());assert a.authorized(cmd)
    a.command('grants',dict(id=b.identity.id,grants=grants({'view_details':True})))
    until(lambda:not a.authorized(cmd))
    a.command('result',dict(id=cmd['id'],peer=cmd['peer'],result=dict(status='rejected',message='revoked')))
    assert not a.authorized(cmd)

def test_capability_forgery_and_payload_confusion(peers):
    a,b=peers;run=shared(a,b,awaiting());wrong=deepcopy(run);wrong['remote_entity']['capability']='forged'
    sent=b.submit(wrong,'approve_tool',dict(confirmation_id='request1',expected_revision=7))
    assert receipt(b,sent['command_id'])['status']=='rejected'
    sent=b.submit(run,'approve_tool',dict(confirmation_id='different',expected_revision=7))
    assert receipt(b,sent['command_id'])['status']=='rejected' and a.owner_commands.empty()

def test_receiver_rejects_malformed_nested_details():
    rows=project(sample(),'owner',grants({'view_details':True}))[0]
    rows[0]['tools']=[{'name':{'execute':'bad'}}]
    with pytest.raises(ValueError):validate_items(rows)

def test_source_scope_and_anonymous_title():
    assert project(sample(),'owner',grants({'sources':['solo_task']}))[0]==[]
    rows=project(sample(),'owner',grants({'anonymous_titles':True}))[0]
    assert rows[0]['title']=='Agent 任务' and not rows[0]['actions']


def test_reminder_owner_transaction_and_replica_removal(peers,tmp_path):
    from datetime import datetime,timedelta
    from monitor.tasks.store import TaskStore
    from monitor.tasks.service import TaskService
    a,b=peers;tasks=TaskStore(tmp_path/'tasks');now=time.time();future=datetime.fromtimestamp(now)+timedelta(minutes=4)
    try:
        task=tasks.save(dict(title='测试提醒',body='仅测试数据',start_date=future.date().isoformat(),time_of_day=future.strftime('%H:%M'),weekdays=[],lead_minutes=5,enabled=True),now=now)
        occurrence=tasks.poll(now)[0];local=TaskService._run(occurrence,now)
        data=dict(runs=[local],actions=[],preferences={})
        run=shared(a,b,data);assert run['remote_source']=='solo_task'
        sent=b.submit(run,'local_reminder',dict(cycle=0,action='acknowledge'))
        cmd=until(lambda:None if a.owner_commands.empty() else a.owner_commands.get_nowait())
        assert a.authorized(cmd)
        tasks.act(cmd['target']['run_id'][5:],cmd['payload']['cycle'],cmd['payload']['action'],command_id=cmd['id'])
        a.command('result',dict(id=cmd['id'],peer=cmd['peer'],result=dict(status='succeeded',message='acknowledged')))
        assert receipt(b,sent['command_id'])['status']=='succeeded'
        # Same reminder transaction ID is safely repeatable even after it is no longer active.
        tasks.act(occurrence['id'],0,'acknowledge',command_id=cmd['id'])
        assert not tasks.poll(now+1)
        a.publish_local(dict(runs=[],actions=[],preferences={}))
        until(lambda:not b.snapshot())
    finally:tasks.close()


def test_reconnect_and_restart_do_not_reopen_historical_completion(peers):
    from datetime import datetime,timezone
    a,b=peers;shared(a,b,awaiting())
    finished=sample(state='completed');finished['runs'][0]['ended_at']=datetime.now(timezone.utc).isoformat()
    a.publish_local(finished);until(lambda:b.snapshot() and b.snapshot()[0]['status']=='completed')
    config=b.config.copy();b.stop();b.thread.join(8);b.start(config)
    until(lambda:b.snapshot_status()['state']=='running')
    b.publish_local(sample('receiver'));a.publish_local(finished)
    until(lambda:b.snapshot_status()['peers'] and b.snapshot_status()['peers'][0]['online'])
    time.sleep(.6);assert not b.snapshot()


def test_task_timezone_and_daylight_saving(tmp_path,monkeypatch):
    from datetime import datetime,timezone,date
    from monitor.tasks.schedule import validate,due_on,next_due
    from monitor.tasks.store import TaskStore
    value=dict(title='timezone',body='',start_date='2030-03-10',time_of_day='02:30',weekdays=[6],lead_minutes=5,timezone='America/New_York')
    task=validate(value)
    assert due_on(task,date(2030,3,10)) is None  # DST spring gap is skipped.
    task=validate({**value,'start_date':'2030-11-03','time_of_day':'01:30'})
    assert datetime.fromtimestamp(due_on(task,date(2030,11,3)),timezone.utc).hour==5 # fold=0 once.
    store=TaskStore(tmp_path)
    try:
        saved=store.save({**value,'start_date':'2031-01-01','time_of_day':'12:00','weekdays':[]},now=0)
        monkeypatch.setattr('monitor.tasks.schedule.local_zone',lambda:'Asia/Tokyo')
        updated=store.save({k:v for k,v in saved.items() if k!='timezone'},saved['id'],now=0)
        assert updated['timezone']=='America/New_York'
    finally:store.close()


def test_harness_capability_stays_local_and_question_mapping(monkeypatch):
    from concurrent.futures import Future
    from monitor.multi_diverse.owner_controls import send_harness
    source=sample();run=source['runs'][0];run['app_id']='deepseek_harness';run['status']='waiting_input'
    request=dict(id='q1',revision=2,kind='question',control_key='PRIVATE-CONTROL',instance='PRIVATE-INSTANCE',session_id='s',generation='g',questions=[dict(id='question-a',question='选择环境',multiSelect=False,options=[{'label':'测试'}])])
    run['deepseek_interactions']=[request]
    rows=project(source,'owner',grants({'agent_answer':True}))[0]
    assert rows[0]['actions'][0]['type']=='answer_question'
    assert 'PRIVATE-CONTROL' not in str(rows) and 'PRIVATE-INSTANCE' not in str(rows)
    calls=[]
    def submit(actual,action,answer):
        calls.append((actual,action,answer));future=Future();future.set_result({'status':'accepted'});return future
    monkeypatch.setattr('monitor.multi_diverse.owner_controls.bridge_client.submit',submit)
    send_harness(run,'answer_question',dict(confirmation_id='q1',expected_revision=2,answers=[['测试']]))
    assert calls[0][2]=={'answers':[{'id':'question-a','selected':['测试']}]}
    with pytest.raises(ValueError):send_harness(run,'answer_question',dict(confirmation_id='q1',expected_revision=3,answers=[['测试']]))
    assert len(calls)==1


def test_bad_local_row_isolated_and_current_view_bounded():
    source=sample();source['runs'] += [dict(run_id='broken',app_id='codex',steps='bad',tools=None)]
    rows=project(source,'owner',grants({'view_details':True}))[0]
    assert len(rows)==1 and rows[0]['title']=='测试'
    source['runs']=[{**sample(str(i))['runs'][0],'message':'a'*1000} for i in range(250)]
    rows,targets=project(source,'owner',grants({'view_details':True}))
    assert len(rows)<=200 and len(targets)==250
    import json
    assert len(json.dumps(rows,ensure_ascii=False).encode())<180*1024


def test_pause_only_one_sharing_direction(peers):
    a,b=peers;shared(a,b,awaiting());until(lambda:a.snapshot())
    a.command('pause',b.identity.id);until(lambda:not b.snapshot())
    assert a.snapshot() and a.snapshot_status()['peers'][0]['paused']
    a.command('pause',b.identity.id)
    a.publish_local(awaiting());b.publish_local(sample('receiver'))
    until(lambda:b.snapshot());assert a.snapshot()


def test_tls_pin_authentication_and_browser_origin_are_enforced(peers):
    import asyncio
    from aiohttp import ClientSession,Fingerprint,ServerFingerprintMismatch
    a,b=peers
    async def check():
        async with ClientSession() as client:
            with pytest.raises(ServerFingerprintMismatch):
                await client.get(a.address+'/peer/v1/ws',ssl=Fingerprint(bytes(32)))
            response=await client.get(a.address+'/peer/v1/ws',ssl=Fingerprint(bytes.fromhex(a.identity.fingerprint)),headers={'X-Solo-Device':b.identity.id,'Authorization':'Bearer invalid'})
            assert response.status==401
            response=await client.get(a.address+'/peer/v1/ws',ssl=Fingerprint(bytes.fromhex(a.identity.fingerprint)),headers={'Origin':'https://untrusted.invalid'})
            assert response.status==403
    asyncio.run(check())
