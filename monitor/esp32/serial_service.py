"""One bounded worker owns the serial port; Tk never waits for USB I/O.

Only the explicitly chosen port is opened. Enumeration never probes devices.
SOLO1 remains a read-only display contract. SOLO2 controls require an explicit
setting, complete paged proposals, and the existing owner-side operation gate.
"""
from copy import deepcopy
import json
import threading
import time
import sqlite3
import secrets
from collections import deque

from .config import normalize
from .compact import CompactProjector, frame


def list_ports():
    from serial.tools import list_ports as ports
    return [dict(port=p.device, name=p.description or p.device, serial_number=p.serial_number or '')
            for p in ports.comports()]


def open_port(port):
    import serial
    stream = serial.Serial(port=None, baudrate=115200, timeout=.1, write_timeout=.7)
    # Avoid intentionally toggling the ESP32's reset/boot control lines.
    stream.dtr = False
    stream.rts = False
    stream.port = port
    stream.open()
    return stream


class SerialService:
    def __init__(self, opener=open_port, enumerate_ports=list_ports, *, owner=None, data_root=None):
        self.lock = threading.RLock()
        self.config = normalize()
        self.projector = CompactProjector()
        from .control import ControlBroker
        self.control = ControlBroker(owner, data_root) if owner is not None and data_root is not None else None
        self.latest = None
        self.published_at = 0
        self.generation = 0
        self.thread = None
        self.shutdown = threading.Event()
        self.opener, self.enumerate_ports = opener, enumerate_ports
        self.status = dict(state='disabled', message='USB 屏幕未开启', port='')
        self.provision_queue=deque(maxlen=4)
        self.provision_status=dict(board='',capable=False,state='idle',message='',networks=[])
        self.provision_request='';self.provision_deadline=0
        from .wireless import WirelessService
        self.wireless=WirelessService(self,data_root) if data_root is not None else None

    def configure(self, value):
        config = normalize(value, strict=True)
        with self.lock:
            self.config = config
            self.generation += 1
            self.provision_queue.clear();self.provision_request='';self.provision_deadline=0
            self.provision_status.update(capable=False,state='idle',message='')
            # A privacy change must not transmit a cached full-content frame.
            self.latest = None
            if self.control: self.control.reset()
            self.status = dict(state='connecting' if config['enabled'] else 'disabled',
                               message='等待连接所选设备' if config['enabled'] else 'USB 屏幕未开启', port=config['port'])
            if self.thread is None and config['enabled']:
                self.thread = threading.Thread(target=self._run, name='solo-esp32', daemon=True)
                self.thread.start()
        if self.wireless:self.wireless.configure(config)

    def wireless_status(self):
        with self.lock:
            return {**deepcopy(self.provision_status),'server':self.wireless.snapshot() if self.wireless else {}}

    def wifi_scan(self):
        return self._provision('SCAN',{})

    def wifi_configure(self, ssid, password):
        if not isinstance(ssid,str) or not 1<=len(ssid.encode())<=32 or any(ord(c)<32 for c in ssid):raise ValueError('Wi-Fi 名称须为 1–32 字节')
        if not isinstance(password,str) or not (password=='' or 8<=len(password.encode())<=63 or (len(password)==64 and all(c in '0123456789abcdefABCDEF' for c in password))):raise ValueError('密码须为 8–63 字节，或 64 位十六进制 PSK')
        with self.lock:
            board=self.provision_status['board']
            if not self.provision_status['capable']:raise ValueError('请先连接支持 USB 配网的新版固件')
            config=self.wireless.provisioning(board) if self.wireless else None
            if not config:raise ValueError('无线服务未开启')
            return self._provision('CONFIG',{**config,'ssid':ssid,'pass':password})

    def _provision(self, verb, data):
        with self.lock:
            if not self.provision_status['capable'] or self.status['state']!='connected':raise ValueError('请先通过 USB 连接新版板卡')
            if self.provision_status['state'] in ('scanning','configuring'):raise ValueError('上一次配置还在进行，请稍后再试')
            rid=secrets.token_hex(16);self.provision_request=rid;self.provision_deadline=time.monotonic()+65
            wire=b'SOLO3 '+verb.encode()+b' '+json.dumps(dict(id=rid,**data),ensure_ascii=False).encode()+b'\n'
            if len(wire)>8000:raise ValueError('配置超过设备上限')
            self.provision_queue.append(wire)
            self.provision_status.update(state='scanning' if verb=='SCAN' else 'configuring',message='正在扫描 2.4GHz 网络' if verb=='SCAN' else '正在通过 USB 安全配置 Wi-Fi')
            return rid

    def publish(self, snapshot, visible, focus, desktop_visible):
        with self.lock:
            if not self.config['enabled']: return
            try:
                handles = self.control.publish(snapshot, visible, lambda r:self.projector.phone.key(r)[:23],
                    self.config['allow_control'] and self.config['privacy_mode']=='summary') if self.control else {}
                self.latest = frame(self.projector.project(snapshot, visible, focus, desktop_visible,
                    self.config['privacy_mode'], handles, control_enabled=self.config['allow_control']))
                self.published_at = time.monotonic()
            except (ValueError, TypeError, KeyError, OverflowError):
                self.latest = None
                if self.control: self.control.reset()
                self.status['message'] = '状态无法投影，等待来源恢复'

    def snapshot_status(self):
        with self.lock: return deepcopy(self.status)

    def _status(self, generation, state, message, port=''):
        with self.lock:
            if generation == self.generation:
                self.status = dict(state=state, message=message, port=port)

    def _resolve(self, config):
        if not config['serial_number']: return config['port']
        matches = [p['port'] for p in self.enumerate_ports() if p['serial_number'] == config['serial_number']]
        if len(matches) != 1: raise OSError('未找到唯一的已保存设备；请接入或刷新后重选')
        return matches[0]

    @staticmethod
    def _write(stream, data):
        if stream.write(data) != len(data): raise OSError('串口写入不完整')

    def _session(self, stream, generation, port):
        ready = False
        control_ready = False
        if self.control: self.control.new_session()
        began = time.monotonic()
        last_hello = last_send = last_ack = 0
        sent_revisions = set()
        buffer = bytearray()
        dropping = False
        while not self.shutdown.is_set():
            with self.lock:
                if generation != self.generation: return
                payload = self.latest if time.monotonic()-self.published_at < 4 else None
                if self.provision_deadline and time.monotonic()>self.provision_deadline:
                    self.provision_deadline=0;self.provision_queue.clear()
                    self.provision_status.update(state='error',message='配置超时，请检查网络或重新连接 USB')
                provisioning=self.provision_queue.popleft() if ready and self.provision_queue else None
            if provisioning:self._write(stream,provisioning)
            now = time.monotonic()
            if self.control:
                for result in self.control.collect():
                    if control_ready: self._write(stream,b'SOLO2 RESULT '+json.dumps(result,ensure_ascii=False).encode()+b'\n')
            if not ready and now-last_hello >= 1:
                self._write(stream, b'SOLO1 HELLO\n'); last_hello = now
            if ready and payload and now-last_send >= 1:
                self._write(stream, payload); last_send = now
                sent_revisions.add(json.loads(payload[12:])['rev'])
                if len(sent_revisions) > 12: sent_revisions.remove(min(sent_revisions))
            for char in stream.read(1024):
                if char == 10:
                    line = bytes(buffer).strip() if not dropping else b''
                    buffer.clear(); dropping = False
                    if line.startswith(b'SOLO3 '):
                        try:
                            _,verb,raw=line.split(b' ',2);value=json.loads(raw)
                            if not isinstance(value,dict):continue
                            with self.lock:
                                if verb==b'CAPS':
                                    from .wireless import BOARD
                                    board=value.get('board','')
                                    if isinstance(board,str) and BOARD.fullmatch(board):self.provision_status.update(board=board,capable=value.get('wifi') is True)
                                elif value.get('id')==self.provision_request:
                                    if verb==b'SCAN':
                                        networks=value.get('networks',[])
                                        if not isinstance(networks,list):continue
                                        self.provision_status.update(state='ready',message='请选择网络；仅支持 2.4GHz',networks=[n for n in networks[:20] if isinstance(n,dict) and isinstance(n.get('ssid'),str) and len(n['ssid'].encode())<=32])
                                        self.provision_deadline=0
                                    elif verb==b'WIFI':
                                        state=value.get('state')
                                        messages={'connecting':'正在连接 Wi-Fi','verifying':'已连上 Wi-Fi，正在验证 SOLO','saved':'无线连接成功并保存，可以改用独立供电','error':'连接失败，已恢复原配置；检查密码、私人网络及防火墙'}
                                        if state in messages:
                                            self.provision_status.update(state='ready' if state=='saved' else 'error' if state=='error' else 'configuring',message=messages[state])
                                            if state in ('saved','error'):self.provision_deadline=0
                        except (ValueError,TypeError,KeyError,UnicodeError):pass
                    elif line == b'SOLO2 READY v2':
                        control_ready = True
                    elif line.startswith(b'SOLO2 ') and control_ready and self.control:
                        value = {}
                        try:
                            _, verb, raw = line.split(b' ',2)
                            value=json.loads(raw)
                            if not isinstance(value,dict): raise ValueError('命令格式无效')
                            result=self.control.handle(verb.decode('ascii'),value)
                            kind=b'DETAIL' if verb==b'GET' else b'RESULT'
                        except (ValueError,KeyError,TypeError,UnicodeError):
                            value=value if isinstance(value,dict) else {}
                            result=dict(id=str(value.get('id',''))[:32],cmd=str(value.get('cmd',''))[:32],status='rejected',message='请求已失效或操作不可用')
                            kind=b'RESULT'
                        self._write(stream,b'SOLO2 '+kind+b' '+json.dumps(result,ensure_ascii=False).encode()+b'\n')
                    elif line == b'SOLO1 READY v1':
                        first_ready = not ready
                        ready = True
                        if first_ready:
                            self._status(generation, 'syncing', '板卡已识别，正在同步', port)
                    elif line.startswith(b'SOLO1 ACK '):
                        parts = line.split()
                        if len(parts) == 4 and parts[2].isdigit() and int(parts[2]) in sent_revisions:
                            last_ack = now
                            self._status(generation, 'connected', 'USB 已连接 · 内容实时同步', port)
                    elif line.startswith(b'SOLO1 CMD '):
                        result = {'ok': False, 'message': '此连接为只读屏幕，请在电脑处理授权'}
                        self._write(stream, b'SOLO1 RESULT '+json.dumps(result, ensure_ascii=False).encode()+b'\n')
                elif not dropping:
                    buffer.append(char)
                    if len(buffer) > 8000: buffer.clear(); dropping = True
            if now-(last_ack or began) > 7:
                raise OSError('板卡未确认状态，请检查固件、USB 线和端口占用')

    def _run(self):
        while not self.shutdown.is_set():
            with self.lock: config, generation = dict(self.config), self.generation
            if not config['enabled']:
                self.shutdown.wait(.2); continue
            stream = None
            try:
                port = self._resolve(config)
                if not port: raise OSError('请选择 ESP32 的串口')
                self._status(generation, 'connecting', '正在连接并确认 SOLO 固件', port)
                stream = self.opener(port)
                self._session(stream, generation, port)
            except (OSError, ImportError, ValueError, sqlite3.Error) as error:
                self._status(generation, 'retrying', f'{error}；稍后自动重试', config['port'])
            finally:
                with self.lock:
                    self.provision_queue.clear();self.provision_status['capable']=False
                if self.control and stream: self.control.disconnect()
                if stream:
                    try: stream.close()
                    except OSError: pass
            self.shutdown.wait(1)

    def stop(self):
        self.shutdown.set()
        if self.control: self.control.reset()
        if self.thread: self.thread.join(timeout=2)
        if self.wireless:self.wireless.stop()
