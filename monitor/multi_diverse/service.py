"""Opt-in WSS. Network state/SQLite stay on one worker; Tk owns local commands."""
import asyncio
from copy import deepcopy
import ipaddress
import json
from pathlib import Path
import queue
import secrets
import threading
import time
import uuid
from urllib.parse import urlsplit
from aiohttp import web, ClientSession, ClientTimeout, Fingerprint, WSMsgType, ClientWSTimeout
from .config import normalize, grants
from .identity import Identity
from .store import Store
from .protocol import validate, project, remote_run, digest, MAX_MESSAGE, FINAL, OPERATIONS, validate_items
from ..phone.network import discover, choose


class PeerService:
    def __init__(self, root, *, discovery=discover, identity_factory=Identity):
        self.root=Path(root)/'multi_diverse'; self.discovery=discovery; self.identity_factory=identity_factory
        self.lock=threading.RLock(); self.thread=None; self.stop_event=threading.Event()
        self.mailbox=queue.Queue(maxsize=32); self.owner_commands=queue.Queue(maxsize=32)
        self.local={}; self.sample_at=0.; self.remote=[]; self.current_tasks=[]; self.results={}
        self.state=dict(state='stopped',message='电脑协同未开启',peers=[],pending=[])

    def snapshot_status(self):
        with self.lock: return deepcopy(self.state)

    def report(self, **changes):
        with self.lock: self.state.update(changes)

    def snapshot(self):
        with self.lock: return deepcopy(self.remote)

    def catalog_snapshot(self):
        with self.lock:return deepcopy(self.current_tasks)

    def publish_local(self, snapshot):
        with self.lock: self.local=deepcopy(snapshot); self.sample_at=time.monotonic()

    def start(self, config):
        if self.thread and self.thread.is_alive(): raise ValueError('请先关闭电脑协同，等待服务退出')
        self.config=normalize(config,strict=True); self.stop_event.clear()
        self.generation=uuid.uuid4().hex
        for channel in (self.mailbox,self.owner_commands):
            while not channel.empty():channel.get_nowait()
        self.report(peers=[],pending=[],invite='',address='')
        self.report(state='starting',message='正在验证网络与设备身份')
        self.thread=threading.Thread(target=self._thread,name='solo-peers',daemon=True); self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock: self.remote=[];self.current_tasks=[]
        self.report(state='stopped',message='电脑协同已关闭',pending=[],invite='',address='')

    def command(self, kind, value=None):
        if not self.thread or not self.thread.is_alive() or self.stop_event.is_set(): raise ValueError('请先开启电脑协同')
        try: self.mailbox.put_nowait((kind,deepcopy(value)))
        except queue.Full: raise ValueError('操作过于频繁') from None

    def submit(self, run, operation, payload):
        id=uuid.uuid4().hex
        self.command('send',dict(peer=run['remote_origin'],id=id,entity=run['remote_entity']['id'],
            revision=run['remote_entity']['revision'],operation=operation,payload=payload,capability=run['remote_entity'].get('capability','')))
        return dict(command_id=id,status='accepted')

    def _thread(self):
        try: asyncio.run(self._run())
        except Exception as e:
            self.report(state='error',message=str(e) if isinstance(e,(ValueError,OSError)) else '电脑协同失败，请检查依赖、端口和设备数据')
        finally:
            with self.lock: self.remote=[];self.current_tasks=[]

    def _address(self, address):
        parsed=urlsplit(address)
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise ValueError('请输入邀请中的局域网 HTTPS 地址')
        ip=ipaddress.ip_address(parsed.hostname)
        if ip.version!=4 or not self.interface.allows(str(ip)): raise ValueError('设备不在所选局域网')
        if not parsed.port or not 1024<=parsed.port<=65535: raise ValueError('端口无效')
        return address.rstrip('/')

    def _peer(self):
        return dict(id=self.identity.id,name=self.config['name'],fingerprint=self.identity.fingerprint,address=self.address)

    def _summary(self):
        now=time.monotonic(); rows=[]; runs=[];current_tasks=[]
        for id,p in self.store.peers().items():
            online=bool(id in self.connections and now-self.received.get(id,0)<15)
            rows.append(dict(id=id,name=p['name'],address=p['address'],online=online,grants=p['grants'],fingerprint=p['fingerprint'],
                omitted_count=getattr(self,'omitted',{}).get(id,0),paused=p.get('paused',False)))
            age=now-self.received.get(id,now)
            for item in self.catalog.get(id,[]):
                since=self.completed.setdefault((id,item['id']),now) if item['status'] in FINAL else now
                run=remote_run({**item,'capability':self.remote_capabilities.get(id,{}).get(item['id'],'')},p,offline=not online or self.producer_age.get(id,0)>5)
                run['remote_connection_message']='来源失联' if not online else '来源刷新暂停'
                if item['status'] in FINAL:
                    from datetime import datetime,timezone
                    # Receiver retention uses its own observation clock. Owner
                    # timestamps remain available inside remote_entity.
                    run['ended_at']=datetime.fromtimestamp(time.time()-(now-self.completed[(id,item['id'])]),timezone.utc).isoformat()
                clock=self.timers.setdefault((id,item['id']),[item.get('elapsed_ms',0)/1000,now,item.get('elapsed_ms',0)/1000])
                if not run['connection_error'] and item['status'] not in FINAL:clock[2]=clock[0]+max(0,now-clock[1])
                run['remote_elapsed_seconds']=int(clock[2])
                if run['run_id'] in self.results:run['command_receipt']=self.results[run['run_id']]
                current_tasks.append(run)
                if age>=75 or item['id'] in self.baseline.get(id,set()) or (item['status'] in FINAL and now-since>=self.hide_seconds):continue
                runs.append(run)
        with self.lock:self.remote=runs;self.current_tasks=current_tasks
        self.report(peers=rows,pending=[dict(id=k,name=v['peer']['name'],code=v['code'],direction=v['direction'],approved=v.get('approved',False)) for k,v in self.pending.items() if not v.get('saved')])

    async def _run(self):
        self.interface=choose(self.config,await asyncio.to_thread(self.discovery))
        self.identity=await asyncio.to_thread(self.identity_factory,self.root)
        self.store=Store(self.root/'peers.sqlite3',codec=self.identity.codec)
        self.address=f'https://{self.interface.address}:{self.config["port"]}'
        self.authority=urlsplit(self.address).netloc
        self.connections={}; self.directions={}; self.received={}; self.catalog={}; self.baseline={}; self.completed={}; self.producer_age={}; self.timers={}; self.pending_out={}; self.command_busy=set(); self.capabilities={}; self.remote_capabilities={}; self.rate={}; self.dialing=set()
        self.pending={}; self.invitation=None; self.hide_seconds=60; self.jobs=set(); self.retry={}; self.command_waiters={}
        self.omitted={}
        self.session=ClientSession(timeout=ClientTimeout(total=8),trust_env=False)
        runner=web.AppRunner(self._app(),access_log=None,shutdown_timeout=.5); await runner.setup()
        try:
            await web.TCPSite(runner,self.interface.address,self.config['port'],ssl_context=self.identity.context).start()
            self.report(state='running',message='已开启 · 加密双向连接',address=self.address,device=self._peer())
            checked=time.monotonic()
            while not self.stop_event.is_set():
                now=time.monotonic()
                if now-checked>10:
                    current=choose(self.config,await asyncio.to_thread(self.discovery))
                    if current!=self.interface: raise ValueError('私人网络已改变，请重新选择网卡')
                    checked=now
                while not self.mailbox.empty():
                    kind,value=self.mailbox.get_nowait()
                    try: await self._control(kind,value)
                    except (ValueError,OSError,KeyError): self.report(message='操作失败：请检查邀请、设备状态或权限')
                for key in list(self.pending):
                    if now>self.pending[key]['expires']: self.pending.pop(key,None)
                for id,p in self.store.peers().items():
                    if id not in self.connections and id not in self.dialing and now>=self.retry.get(id,0):
                        self.retry[id]=now+5+secrets.randbelow(1000)/1000
                        if self.identity.id>id and id not in self.received:
                            self.received[id]=now;continue
                        self.dialing.add(id); self._spawn(self._dial(p))
                for command,(peer,entity,sent,revision) in list(self.pending_out.items()):
                    if now-sent>16:
                        self.pending_out.pop(command,None)
                        with self.lock:self.results['peer:'+peer+':'+entity]=dict(command_id=command,entity_revision=revision,status='outcome_unknown',message='结果待来源核对，请勿重复提交')
                self._summary()
                await asyncio.sleep(.1)
        finally:
            self.stop_event.set()
            for ws in list(self.connections.values()): await ws.close()
            for job in list(self.jobs): job.cancel()
            await asyncio.gather(*list(self.jobs),return_exceptions=True)
            await runner.cleanup(); await self.session.close(); self.store.close()

    def _spawn(self,coro):
        job=asyncio.create_task(coro); self.jobs.add(job)
        def done(task):
            self.jobs.discard(task)
            if not task.cancelled(): task.exception()
        job.add_done_callback(done)
        return job

    def _app(self):
        @web.middleware
        async def guard(request,handler):
            try:
                if self.stop_event.is_set(): raise web.HTTPServiceUnavailable()
                if request.host!=self.authority or not self.interface.allows(request.remote or '') or request.headers.get('Origin'):
                    raise web.HTTPForbidden()
                key=(request.remote or '',request.path)
                moment=time.monotonic();bucket=self.rate.setdefault(key,[moment,0])
                if moment-bucket[0]>10:bucket[:]=[moment,0]
                bucket[1]+=1
                if bucket[1]>30:raise web.HTTPTooManyRequests()
                if len(self.rate)>256:self.rate={k:v for k,v in self.rate.items() if moment-v[0]<10}
                return await handler(request)
            except (ValueError,KeyError,TypeError): return web.json_response({'error':'请求无效或已过期'},status=400)
        app=web.Application(middlewares=[guard],client_max_size=16384)
        app.router.add_post('/peer/v1/pair',self._pair)
        app.router.add_post('/peer/v1/confirm',self._confirm)
        app.router.add_get('/peer/v1/ws',self._accept)
        return app

    async def _pair(self,request):
        body=validate(await request.json()); invite=self.invitation
        if not invite or time.monotonic()>invite['expires'] or not secrets.compare_digest(str(body.get('invite','')),invite['token']): raise ValueError()
        if len(self.store.peers())>=5 or len(self.pending)>=5: raise ValueError()
        peer=body['peer']; self._address(peer['address'])
        self._validate_peer(peer)
        if peer['id']==self.identity.id or peer['id'] in self.store.peers(): raise ValueError()
        if not isinstance(body.get('token'),str) or not 32<=len(body['token'])<=128:raise ValueError()
        bytes.fromhex(peer['fingerprint'])
        key=secrets.token_urlsafe(24); code=digest([self.identity.id,peer['id'],self.identity.fingerprint,peer['fingerprint'],key])[:12].upper()
        self.pending[key]=dict(peer=peer,code=code,direction='incoming',expires=time.monotonic()+120,
                              outbound=body['token'],inbound=secrets.token_urlsafe(32),approved=False)
        self.invitation=None; self.report(invite='')
        return web.json_response(dict(ticket=key,code=code,peer=self._peer()))

    async def _confirm(self,request):
        body=validate(await request.json()); v=self.pending.get(body.get('ticket'))
        if not v or v['direction']!='incoming' or time.monotonic()>v['expires']: raise ValueError()
        if not v['approved']: return web.json_response(dict(ready=False))
        p={**v['peer'],'outbound':v['outbound'],'inbound_hash':digest(v['inbound']),'grants':v['grants']}
        if not v.get('saved'):self.store.save_peer(p);v['saved']=True
        return web.json_response(dict(ready=True,token=v['inbound'],peer=self._peer()))

    async def _control(self,kind,value):
        if kind=='invite':
            token=secrets.token_urlsafe(32); self.invitation=dict(token=token,expires=time.monotonic()+120)
            self.report(invite=json.dumps({**self._peer(),'invite':token},ensure_ascii=False),message='邀请 120 秒有效；请通过可信渠道发送')
        elif kind=='join': self._spawn(self._join(value))
        elif kind=='approve':
            v=self.pending[value['id']]; v['grants']=grants(value.get('grants')); v['approved']=True
            if v['direction']=='outgoing' and not v.get('finishing'):
                v['finishing']=True;self._spawn(self._finish_pair(value['id']))
        elif kind=='reject': self.pending.pop(value,None)
        elif kind=='remove':
            self.store.remove_peer(value); self.catalog.pop(value,None); self.capabilities.pop(value,None); self._summary()
            self.pending={k:v for k,v in self.pending.items() if v['peer']['id']!=value}
            ws=self.connections.get(value)
            if ws: await ws.send_json(dict(type='revoked')); await ws.close()
        elif kind=='grants':
            p=self.store.peers()[value['id']]; p['grants']=grants(value['grants']); self.store.save_peer(p)
            self.store.clear_projection(p['id']);self.capabilities.pop(p['id'],None);self._summary()
            ws=self.connections.get(p['id'])
            if ws: await ws.send_json(dict(type='policy.changed')); await ws.close()
        elif kind=='pause':
            p=self.store.peers()[value];p['paused']=not p.get('paused',False);self.store.save_peer(p)
            self.store.clear_projection(value);self.capabilities.pop(value,None);self._summary()
        elif kind=='send':
            ws=self.connections.get(value['peer'])
            if not ws: raise ValueError('来源离线')
            if len(self.pending_out)>=32:raise ValueError('操作队列已满')
            self.pending_out[value['id']]=(value['peer'],value['entity'],time.monotonic(),value['revision'])
            await ws.send_json(dict(type='command',**value))
        elif kind=='result':
            future=self.command_waiters.get((value.get('peer'),value['id']))
            if future and not future.done(): future.set_result(value['result'])

    async def _join(self,raw):
        try:
            if len(self.pending)>=5 or len(self.store.peers())>=5:raise ValueError()
            invitation=validate(json.loads(raw)); self._validate_peer(invitation); address=self._address(invitation['address'])
            pin=Fingerprint(bytes.fromhex(invitation['fingerprint'])); token=secrets.token_urlsafe(32)
            async with self.session.post(address+'/peer/v1/pair',json=dict(invite=invitation['invite'],peer=self._peer(),token=token),ssl=pin) as response:
                if response.status!=200: raise ValueError()
                data=await response.json()
            if data['peer']['id']!=invitation['id'] or data['peer']['fingerprint']!=invitation['fingerprint']: raise ValueError()
            self.pending[data['ticket']]=dict(peer=data['peer'],code=data['code'],direction='outgoing',
                expires=time.monotonic()+120,inbound=token,approved=False)
            self.report(message='请在两台电脑核对相同校验码，然后分别确认共享权限')
        except Exception: self.report(message='加入失败：邀请过期、指纹不符或设备不可达')

    async def _finish_pair(self,key):
        v=self.pending[key]; peer=v['peer']
        try:
            while time.monotonic()<v['expires'] and key in self.pending:
                async with self.session.post(self._address(peer['address'])+'/peer/v1/confirm',json={'ticket':key},ssl=Fingerprint(bytes.fromhex(peer['fingerprint']))) as response:
                    if response.status!=200: raise ValueError()
                    data=await response.json()
                if data['ready']:
                    self.store.save_peer({**peer,'outbound':data['token'],'inbound_hash':digest(v['inbound']),'grants':v['grants']})
                    self.pending.pop(key,None); self.report(message='配对完成 · 两个共享方向独立授权'); return
                await asyncio.sleep(1)
        except Exception: self.report(message='配对未完成，请重新生成邀请')

    async def _accept(self,request):
        id=request.headers.get('X-Solo-Device'); p=self.store.peers().get(id)
        token=request.headers.get('Authorization','').removeprefix('Bearer ')
        if not p or not secrets.compare_digest(p['inbound_hash'],digest(token)): raise web.HTTPUnauthorized()
        if id in self.connections and (self.identity.id<id or not self.directions.get(id)):raise web.HTTPConflict()
        ws=web.WebSocketResponse(heartbeat=5,receive_timeout=15,max_msg_size=MAX_MESSAGE)
        await ws.prepare(request)
        await ws.send_json(dict(type='authenticated',token=p['outbound'],device=self.identity.id))
        await self._connection(ws,p)
        return ws

    async def _dial(self,p):
        try:
            async with self.session.ws_connect(self._address(p['address'])+'/peer/v1/ws',ssl=Fingerprint(bytes.fromhex(p['fingerprint'])),
                headers={'X-Solo-Device':self.identity.id,'Authorization':'Bearer '+p['outbound']},heartbeat=5,timeout=ClientWSTimeout(ws_receive=15,ws_close=2),max_msg_size=MAX_MESSAGE) as ws:
                hello=await ws.receive_json(timeout=5)
                if hello.get('device')!=p['id'] or not secrets.compare_digest(digest(hello.get('token','')),p['inbound_hash']): raise ValueError()
                await self._connection(ws,p,outgoing=True)
        except Exception: pass
        finally:self.dialing.discard(p['id'])

    async def _connection(self,ws,p,*,outgoing=False):
        id=p['id']
        previous=self.connections.get(id)
        preferred=(self.identity.id<id)==outgoing
        if previous and (not preferred or self.directions.get(id)==outgoing):await ws.close();return
        self.connections[id]=ws; self.received[id]=time.monotonic()
        self.directions[id]=outgoing
        if previous:await previous.close()
        self.pending={k:v for k,v in self.pending.items() if not(v['peer']['id']==id and v.get('saved'))}
        sender=self._spawn(self._publish(ws,p))
        messages=[]
        try:
            async for msg in ws:
                if msg.type!=WSMsgType.TEXT: break
                now=time.monotonic();messages=[t for t in messages if now-t<5];messages.append(now)
                if len(messages)>50:raise ValueError('消息过于频繁')
                value=validate(json.loads(msg.data))
                if not isinstance(value,dict):raise ValueError()
                kind=value.get('type')
                if kind in ('heartbeat','snapshot'):
                    age=value.get('producer_age',0)
                    if type(age) not in (int,float) or age<0:raise ValueError()
                    caps=value.get('capabilities',{})
                    if not isinstance(caps,dict) or len(caps)>200 or any(not isinstance(v,str) or len(v)>128 for v in caps.values()):raise ValueError()
                    self.remote_capabilities[id]=caps
                self.received[id]=time.monotonic()
                if kind=='snapshot':
                    if value.get('origin')!=id or value.get('schema')!=1: raise ValueError()
                    rows=validate_items(value['items'])
                    if type(value.get('seq')) is not int or value['seq']<0 or not isinstance(value.get('epoch'),str) or len(value['epoch'])!=32:raise ValueError()
                    policy=value.get('policy_revision')
                    if type(policy) is not int or policy<1:raise ValueError()
                    omitted=value.get('omitted_count',0)
                    if type(omitted) is not int or omitted<0:raise ValueError()
                    self.store.receive(id,value['epoch']+':'+str(policy),value['seq'],rows)
                    self.omitted[id]=omitted
                    if id not in self.baseline: self.baseline[id]={r['id'] for r in rows if r['status'] in FINAL}
                    self.catalog[id]=rows; self.producer_age[id]=value.get('producer_age',0)
                    live_ids={r['id'] for r in rows}
                    self.timers={key:v for key,v in self.timers.items() if key[0]!=id or key[1] in live_ids}
                    self.completed={key:v for key,v in self.completed.items() if key[0]!=id or key[1] in live_ids}
                    for row in rows:self.timers[(id,row['id'])]=[row.get('elapsed_ms',0)/1000,time.monotonic(),row.get('elapsed_ms',0)/1000]
                    await ws.send_json(dict(type='receipt',seq=value['seq']))
                elif kind=='heartbeat': self.producer_age[id]=value.get('producer_age',0)
                elif kind=='command':
                    if id in self.command_busy:
                        await ws.send_json(dict(type='command.result',id=value.get('id'),entity=value.get('entity'),
                            result=dict(status='outcome_unknown',message='来源正在处理操作，请先核对当前状态')))
                        continue
                    self.command_busy.add(id)
                    self._spawn(self._execute(ws,p,value))
                elif kind=='command.result':
                    pending=self.pending_out.get(value.get('id'))
                    if not pending or pending[:2]!=(id,value.get('entity')):continue
                    self.pending_out.pop(value['id'],None)
                    result=value.get('result')
                    if not isinstance(result,dict) or result.get('status') not in ('accepted','succeeded','rejected','outcome_unknown','conflict','expired') or not isinstance(result.get('message',''),str):raise ValueError()
                    with self.lock:
                        if len(self.results)>=200:self.results.pop(next(iter(self.results)))
                        self.results['peer:'+id+':'+value['entity']]=dict(command_id=value['id'],entity_revision=pending[3],status=result['status'],message=result.get('message','')[:500])
                elif kind in ('revoked','policy.changed'):
                    self.catalog.pop(id,None);self.remote_capabilities.pop(id,None)
                    if kind=='revoked':self.store.remove_peer(id)
                    break
                elif kind!='receipt': raise ValueError()
        except (ValueError,KeyError,TypeError): pass
        finally:
            sender.cancel(); await asyncio.gather(sender,return_exceptions=True)
            if self.connections.get(id) is ws: self.connections.pop(id,None)
            await ws.close()

    async def _publish(self,ws,p):
        last=None; heartbeat=0
        while not ws.closed:
            current=self.store.peers().get(p['id'])
            if not current: return
            with self.lock: snapshot=deepcopy(self.local); sample=self.sample_at
            self.hide_seconds=snapshot.get('preferences',{}).get('monitor_hide_minutes',1)*60
            rows,targets=project(snapshot,self.identity.id,{} if current.get('paused') else current['grants'])
            seq,_=self.store.publish(p['id'],rows)
            now=time.monotonic(); age=now-sample
            capabilities=self._issue_capabilities(p['id'],rows) if age<5 else {}
            omitted=max(0,len(targets)-len(rows))
            if seq!=last:
                await asyncio.wait_for(ws.send_json(dict(type='snapshot',schema=1,origin=self.identity.id,epoch=self.identity.epoch,policy_revision=current.get('policy_revision',1),seq=seq,items=rows,producer_age=age,capabilities=capabilities,omitted_count=omitted,truncated=bool(omitted))),5)
                last=seq; heartbeat=now
            elif now-heartbeat>=2:
                await asyncio.wait_for(ws.send_json(dict(type='heartbeat',producer_age=age,capabilities=capabilities)),5); heartbeat=now
            await asyncio.sleep(.4)

    @staticmethod
    def _validate_peer(peer):
        if not isinstance(peer,dict):raise ValueError()
        for key,n in (('id',32),('fingerprint',64)):
            if not isinstance(peer.get(key),str) or len(peer[key])!=n:raise ValueError()
            bytes.fromhex(peer[key])
        if not isinstance(peer.get('name'),str) or not 1<=len(peer['name'])<=80 or any(ord(c)<32 for c in peer['name']):raise ValueError()

    def _issue_capabilities(self,peer,rows):
        """Owner monotonic leases; client clocks cannot extend an approval."""
        now=time.monotonic();previous=self.capabilities.get(peer,{})
        current={}
        for row in rows:
            if not row['actions']:continue
            old=previous.get(row['id'])
            if not old or old['revision']!=row['revision'] or now-old['issued']>10:
                old=dict(token=secrets.token_urlsafe(32),revision=row['revision'],issued=now)
            current[row['id']]=old
        self.capabilities[peer]=current
        return {key:v['token'] for key,v in current.items()}

    def authorized(self,command):
        """Rechecked on the owner immediately before adapter dispatch."""
        if self.stop_event.is_set() or command.get('generation')!=self.generation or time.monotonic()>command['deadline']:return False
        with self.lock:
            if time.monotonic()-self.sample_at>5:return False
        peer=next((p for p in self.snapshot_status().get('peers',[]) if p['id']==command['peer']),None)
        return bool(peer and peer['online'] and not peer.get('paused') and peer['grants'].get(OPERATIONS.get(command['operation'],'')))

    async def _execute(self,ws,p,value):
        result=dict(status='rejected',message='请求失效或没有权限')
        id=value.get('id','');entity=value.get('entity','')
        try:
            if not isinstance(id,str) or len(id)!=32 or len(json.dumps(value).encode())>16384:raise ValueError()
            current=self.store.peers().get(p['id']);op=value['operation']
            if not current or current.get('paused') or not current['grants'].get(OPERATIONS.get(op,'')):raise ValueError()
            payload=value['payload']
            if not isinstance(payload,dict) or set(payload)-{'confirmation_id','expected_revision','cycle','action','answers'}:raise ValueError()
            signature=digest({k:value[k] for k in ('entity','revision','operation','payload')})
            old=self.store.command_result(p['id'],id,signature)
            if old is not None:result=old
            else:
                cap=self.capabilities.get(p['id'],{}).get(entity)
                if not cap or not secrets.compare_digest(cap['token'],str(value.get('capability',''))) or time.monotonic()-cap['issued']>20:raise ValueError()
                with self.lock:snapshot=deepcopy(self.local);age=time.monotonic()-self.sample_at
                if age>5:raise ValueError()
                rows,targets=project(snapshot,self.identity.id,current['grants']);row=next((r for r in rows if r['id']==entity),None)
                if not row or row['revision']!=value['revision']:raise ValueError()
                actions=[a for a in row['actions'] if a['type']==op]
                if not any(all(payload.get(k)==a.get(k) for k in ('confirmation_id','expected_revision','cycle')) for a in actions):raise ValueError()
                if op=='local_reminder' and payload.get('action') not in ('acknowledge','snooze'):raise ValueError()
                if op=='answer_question':
                    questions=row.get('questions',[]);answers=payload.get('answers')
                    if not questions or not isinstance(answers,list) or len(answers)!=len(questions):raise ValueError()
                    for question,answer in zip(questions,answers):
                        if not isinstance(answer,list) or not answer or len(answer)>12 or any(not isinstance(v,str) or not v.strip() or len(v)>2000 for v in answer):raise ValueError()
                        if not question['multiple'] and len(answer)!=1:raise ValueError()
                        if question.get('custom') is False and any(v not in question['options'] for v in answer):raise ValueError()
                self.store.begin_command(p['id'],id,signature)
                future=asyncio.get_running_loop().create_future();self.command_waiters[(p['id'],id)]=future
                command=dict(id=id,peer=p['id'],target=deepcopy(targets[entity]),entity=entity,revision=value['revision'],operation=op,
                    payload=payload,deadline=time.monotonic()+10,generation=self.generation)
                try:self.owner_commands.put_nowait(command)
                except queue.Full:raise ValueError()
                try:result=await asyncio.wait_for(future,12)
                except asyncio.TimeoutError:result=dict(status='outcome_unknown',message='结果待来源核对，请勿重复提交')
                finally:self.command_waiters.pop((p['id'],id),None)
                self.store.finish_command(p['id'],id,result)
        except (ValueError,KeyError,TypeError):pass
        finally:self.command_busy.discard(p['id'])
        if not ws.closed:await ws.send_json(dict(type='command.result',id=id,entity=entity,result=result))
