import asyncio
import json
import socket
import time
from pathlib import Path
import pytest
from aiohttp import ClientSession,Fingerprint
from monitor.multi_diverse.identity import Identity
from monitor.multi_diverse.service import PeerService
from monitor.multi_diverse.protocol import project,validate,remote_run,digest
from monitor.multi_diverse.config import grants
from monitor.multi_diverse.store import Store
from monitor.phone.network import Interface
from monitor.phone.presence import Presence
from monitor.phone.pairing import Pairing,PairError


def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def sample(id='same',state='running'):
    return dict(backend_id='fixture',runs=[dict(run_id=id,app_id='codex',turn_id='turn',status=state,
        task_summary='测试',message='result',started_at='2026-10-20T01:00:00+00:00',steps=[dict(name='检查',status='running')])],actions=[],preferences={})

def until(condition, timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=condition()
        if value:return value
        time.sleep(.05)
    raise AssertionError('condition timed out')

def test_presence_requires_client_receipt_and_dedupes_tabs():
    now=[10.];pair=Pairing(lambda:now[0]);_,s=pair.pair(pair.open(),'fixture','127.0.0.1')
    presence=Presence(lambda:now[0]);a=presence.register(s);b=presence.register(s)
    assert not presence.online(s.id)
    proof=presence.challenge(s,a);presence.acknowledge(s,a,proof['challenge'])
    assert presence.online(s.id)
    with pytest.raises(PairError):presence.acknowledge(s,a,proof['challenge'])
    with pytest.raises(PairError):presence.register(s)
    now[0]+=16;assert not presence.online(s.id)
    presence.prune({});assert not presence.viewers

def test_projector_identity_privacy_and_no_forwarding():
    one,_=project(sample(),'a',grants());two,_=project(sample(),'b',grants())
    assert one[0]['id']!=two[0]['id'] and one[0]['summary']==''
    data=sample();data['runs'][0]['remote_origin']='foreign'
    assert project(data,'a',grants())[0]==[]
    with pytest.raises(ValueError):validate({'x':float('nan')})
    with pytest.raises(ValueError):validate({'x':'x'*17000})

def test_identity_and_store_restart_and_command_conflict(tmp_path):
    identity=Identity(tmp_path);other=Identity(tmp_path)
    assert identity.id==other.id and identity.fingerprint==other.fingerprint
    assert b'PRIVATE KEY' not in (tmp_path/'identity.dpapi').read_bytes()
    store=Store(tmp_path/'peers.sqlite3')
    seq,changed=store.publish('p',[{'id':'1'}]);assert changed
    assert store.publish('p',[{'id':'1'}])==(seq,False)
    store.receive('p','epoch',2,[])
    with pytest.raises(ValueError):store.receive('p','epoch',1,[])
    assert store.begin_command('p','cmd','hash') is None
    assert store.begin_command('p','cmd','hash')['status']=='outcome_unknown'
    with pytest.raises(ValueError):store.begin_command('p','cmd','other')
    store.close()

def test_two_real_tls_peers_pair_sync_revoke_and_no_loop(tmp_path):
    iface=Interface('fixture','Loopback test','127.0.0.1',8,'Private')
    a=PeerService(tmp_path/'a',discovery=lambda:[iface]);b=PeerService(tmp_path/'b',discovery=lambda:[iface])
    try:
        a.start(dict(enabled=True,interface_id='fixture',port=free_port(),name='A'))
        b.start(dict(enabled=True,interface_id='fixture',port=free_port(),name='B'))
        until(lambda:a.snapshot_status()['state']=='running');until(lambda:b.snapshot_status()['state']=='running')
        a.command('invite');inv=until(lambda:a.snapshot_status().get('invite'))
        b.command('join',inv)
        pa=until(lambda:a.snapshot_status()['pending']);pb=until(lambda:b.snapshot_status()['pending'])
        assert pa[0]['code']==pb[0]['code']
        assert not a.snapshot_status()['peers'] and not b.snapshot_status()['peers']
        a.command('approve',dict(id=pa[0]['id'],grants=grants({'view_details':True})))
        b.command('approve',dict(id=pb[0]['id'],grants=grants({'view_details':True})))
        until(lambda:a.snapshot_status()['peers']);until(lambda:b.snapshot_status()['peers'])
        a.publish_local(sample());b.publish_local(sample())
        until(lambda:a.snapshot() and b.snapshot())
        assert a.snapshot()[0]['run_id']!=b.snapshot()[0]['run_id']
        assert a.snapshot()[0]['remote_name']=='B'
        a.publish_local({**sample(),'runs':sample()['runs']+a.snapshot()})
        time.sleep(.6);assert len(b.snapshot())==1
        peerid=a.snapshot_status()['peers'][0]['id'];a.command('remove',peerid)
        until(lambda:not a.snapshot_status()['peers']);until(lambda:not b.snapshot())
    finally:
        a.stop();b.stop()
        if a.thread:a.thread.join(8)
        if b.thread:b.thread.join(8)
        assert not a.thread.is_alive() and not b.thread.is_alive()
