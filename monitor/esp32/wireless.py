"""USB-established device trust and a private-LAN TLS endpoint for the same board.

Passwords only pass through the serial queue. Bearers are DPAPI-protected on
disk; Wi-Fi passwords are never stored. Discovery binds a derived key and nonce.
"""
import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import threading
import time

from aiohttp import web
from ..multi_diverse.identity import atomic, protect
from ..phone.network import discover, choose
from ..remote.tls import ServerIdentity

HTTPS_PORT = 18769
DISCOVERY_PORT = 18770
BOARD = re.compile(r'^[a-f0-9]{12}$')


class DeviceRegistry:
    def __init__(self, root, codec=protect):
        self.codec=codec
        self.path = Path(root)/'esp32'/'wireless-devices.json'
        self.lock = threading.RLock()
        self.error = ''
        try:
            if self.path.stat().st_size > 65536:
                raise ValueError('oversized registry')
            self.rows = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.rows,dict) or len(self.rows)>8 or any(
                not BOARD.fullmatch(k) or not isinstance(v,dict)
                or not re.fullmatch(r'[a-f0-9]{64}',str(v.get('digest','')))
                or not re.fullmatch(r'(?:[a-f0-9]{2})+',str(v.get('secret','')))
                for k,v in self.rows.items()):
                raise ValueError('invalid registry')
        except FileNotFoundError:
            self.rows = {}
        except (OSError, ValueError):
            # Fail closed for wireless without preventing USB/the desktop from starting.
            # Never silently replace a damaged trust store with an empty one.
            self.rows = {}
            self.error = 'ESP32 设备记录不可读或损坏，未覆盖原文件；USB 仍可使用'

    def bind(self, board):
        if self.error: raise ValueError(self.error)
        if not BOARD.fullmatch(board): raise ValueError('板卡身份无效，请更新固件')
        token = secrets.token_hex(32)
        with self.lock:
            if board in self.rows:
                return self.codec(bytes.fromhex(self.rows[board]['secret']),True).decode()
            if board not in self.rows and len(self.rows)>=8: raise ValueError('最多保存 8 块板卡，请删除旧设备')
            self.rows[board] = dict(digest=hashlib.sha256(token.encode()).hexdigest(),secret=self.codec(token.encode()).hex())
            self._save()
        return token

    def _save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        atomic(self.path,json.dumps(self.rows).encode())

    def revoke(self, board):
        if self.error: raise ValueError(self.error)
        with self.lock:
            self.rows.pop(board,None);self._save()

    def authenticated(self, board, token):
        if not isinstance(token,str) or len(token)!=64:return False
        with self.lock: row=self.rows.get(board)
        return bool(row and hmac.compare_digest(row['digest'],hashlib.sha256(token.encode()).hexdigest()))

    def discovery_reply(self, value, server_id, address):
        if not isinstance(value,dict) or value.get('server')!=server_id:return None
        nonce=value.get('nonce','');board=value.get('board','')
        if not isinstance(nonce,str) or not re.fullmatch(r'[a-f0-9]{32}',nonce):return None
        with self.lock: row=self.rows.get(board) if isinstance(board,str) else None
        if not row:return None
        message=f'{server_id}|{nonce}|{address}|{HTTPS_PORT}'
        return dict(server=server_id,nonce=nonce,host=address,port=HTTPS_PORT,
                    proof=hmac.new(bytes.fromhex(row['digest']),message.encode(),hashlib.sha256).hexdigest())


def make_app(service):
    @web.middleware
    async def guard(request, handler):
        try:
            board=request.headers.get('X-Solo-Board','')
            if (not request.secure or service.stop_event.is_set() or not service.interface.allows(request.remote or '')
                    or request.headers.get('Origin') or request.headers.get('Sec-Fetch-Site')):
                raise web.HTTPForbidden()
            if not service.registry.authenticated(board,request.headers.get('X-Solo-ESP32','')):
                raise web.HTTPUnauthorized()
            if not service.serial.config['enabled'] or not service.serial.config['wifi_enabled']:raise web.HTTPForbidden()
            service.seen(board)
            response=await handler(request)
        except web.HTTPException as exc:
            response=web.json_response({'error':'设备未授权或服务不可用'},status=exc.status)
        except (ValueError,TypeError,KeyError):
            response=web.json_response({'error':'请求已失效或格式无效'},status=400)
        response.headers['Cache-Control']='no-store'
        return response

    async def state(request):
        with service.serial.lock:
            payload=service.serial.latest
            fresh=time.monotonic()-service.serial.published_at<4
        if not payload or not fresh:raise web.HTTPServiceUnavailable()
        return web.Response(body=payload[12:].strip(),content_type='application/json')

    async def control(request):
        if request.content_type!='application/json':raise web.HTTPUnsupportedMediaType()
        body=await request.json()
        if not isinstance(body,dict) or set(body)!={'verb','value'} or body['verb'] not in ('GET','DO','QUERY') or not isinstance(body['value'],dict):
            raise ValueError('控制格式无效')
        broker=service.serial.control
        if broker is None:raise web.HTTPServiceUnavailable()
        # Same journal for USB and Wi-Fi: a transport change cannot execute twice.
        broker.collect()
        board=request.headers.get('X-Solo-Board','');token=request.headers.get('X-Solo-ESP32','')
        reply=broker.handle(body['verb'],body['value'],authorized=lambda:
            not service.stop_event.is_set() and service.serial.config['wifi_enabled']
            and service.registry.authenticated(board,token))
        return web.json_response(dict(kind='DETAIL' if body['verb']=='GET' else 'RESULT',value=reply))

    app=web.Application(middlewares=[guard],client_max_size=2048)
    app.router.add_get('/api/esp32/v1/state',state)
    app.router.add_post('/api/esp32/v1/control',control)
    return app


class WirelessService:
    def __init__(self, serial, root, discovery=discover):
        self.serial=serial;self.root=Path(root);self.discovery=discovery
        self.registry=DeviceRegistry(root);self.lock=threading.RLock()
        self.stop_event=threading.Event();self.thread=None;self.config={}
        self.status=dict(state='disabled',message='无线服务未开启',devices=[])
        self.identity=None;self.interface=None;self.last_seen={}

    def seen(self, board):
        with self.lock:self.last_seen[board]=time.monotonic()

    def report(self, **changes):
        with self.lock:self.status.update(changes)

    def snapshot(self):
        with self.lock, self.registry.lock:
            return {**self.status,'devices':[dict(id=k,online=time.monotonic()-self.last_seen.get(k,0)<6)
                                           for k in self.registry.rows]}

    def configure(self, config):
        with self.lock:self.config=dict(config)
        if self.registry.error:
            self.report(state='error',message=self.registry.error)
            return
        if self.thread is None and config['wifi_enabled']:
            self.thread=threading.Thread(target=self._thread,name='solo-esp32-wifi',daemon=True);self.thread.start()

    def provisioning(self, board):
        with self.lock:
            if self.status['state']!='running' or not self.identity:raise ValueError('请先开启无线服务，等待私人网络就绪')
            identity, interface=self.identity,self.interface
            token=self.registry.bind(board)
            return dict(host=interface.address,port=HTTPS_PORT,token=token,tls=True,
                        ca=identity.public_path.read_text(encoding='utf-8'),server_id=identity.id)

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=2)

    def _thread(self):
        asyncio.run(self._run())

    async def _run(self):
        while not self.stop_event.is_set():
            with self.lock:cfg=dict(self.config)
            if not cfg.get('wifi_enabled') or not cfg.get('enabled'):
                self.report(state='disabled',message='无线服务未开启');await asyncio.sleep(.3);continue
            try:
                interface=choose({'interface_id':cfg['wifi_interface']},await asyncio.to_thread(self.discovery))
                await self._serve(cfg,interface)
            except Exception:
                self.report(state='waiting',message='等待私人网络或端口可用；将自动重试')
            for _ in range(20):
                if self.stop_event.is_set():break
                await asyncio.sleep(.1)

    async def _serve(self, cfg, interface):
        # Dedicated CA prevents phone certificate settings from replacing board trust.
        identity=await asyncio.to_thread(ServerIdentity,self.root/'esp32',interface.address)
        self.interface=interface;self.identity=identity
        runner=web.AppRunner(make_app(self),access_log=None,shutdown_timeout=.5)
        await runner.setup();udp=None
        service=self
        class Discovery(asyncio.DatagramProtocol):
            def connection_made(self, transport):self.transport=transport;self.last={}
            def datagram_received(self, data, addr):
                if len(data)>512 or not interface.allows(addr[0]):return
                now=time.monotonic()
                if now-self.last.get(addr[0],0)<1:return
                self.last={k:v for k,v in self.last.items() if now-v<10};self.last[addr[0]]=now
                try:reply=service.registry.discovery_reply(json.loads(data),identity.id,interface.address)
                except (ValueError,TypeError):return
                if reply:self.transport.sendto(json.dumps(reply).encode(),addr)
        try:
            await web.TCPSite(runner,interface.address,HTTPS_PORT,ssl_context=identity.context).start()
            udp,_=await asyncio.get_running_loop().create_datagram_endpoint(Discovery,local_addr=('0.0.0.0',DISCOVERY_PORT))
            self.report(state='running',message='无线服务已开启 · HTTPS',address=interface.address,port=HTTPS_PORT)
            next_check=0
            while not self.stop_event.is_set():
                with self.lock:current=dict(self.config)
                if not current.get('enabled') or not current.get('wifi_enabled') or current.get('wifi_interface')!=cfg['wifi_interface']:break
                if time.monotonic()>next_check:
                    if choose({'interface_id':cfg['wifi_interface']},await asyncio.to_thread(self.discovery))!=interface:break
                    next_check=time.monotonic()+5
                if self.serial.control and self.serial.snapshot_status()['state']!='connected':self.serial.control.collect()
                await asyncio.sleep(.2)
        finally:
            if udp:udp.close()
            self.report(state='waiting',message='网络变化，正在自动重新连接')
            await runner.cleanup()
