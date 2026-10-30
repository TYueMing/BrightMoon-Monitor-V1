"""Embedded ESP32 settings on the same wallpaper surface as remote devices."""
import queue
import threading
import tkinter as tk
from tkinter import ttk

from ..multi_diverse.UI.surface import PeerSurface
from .serial_service import list_ports


class ESP32Settings:
    def __init__(self, app, parent):
        self.app = app
        self.window = tk.Frame(parent)
        self.surface = s = PeerSurface(self.window, app.scale)
        self.closed = False
        self.scanning = False
        self.ports = []
        self.results = queue.Queue()
        self.network_results=queue.Queue();self.interfaces=[];self.wifi_networks=[]
        self.note = ''
        self.job = None
        self.wheel_binding = app.root.bind('<MouseWheel>', self.wheel, add='+')
        cfg = app.store.snapshot()['esp32']
        s.label(12, 8, 'ESP32 桌面伴侣', 22, True)
        s.label(12, 44, '一根 USB 线，把任务进展带到桌面小屏。', 11)
        s.card(8, 78, 480, 250)
        s.label(24, 94, 'USB 屏幕连接', 14, True)
        s.label(24, 127, '设备 / 串口（刷新只扫描列表，不打开其他设备）', 11)
        self.port = ttk.Combobox(s.canvas, state='readonly', style='Peer.TCombobox')
        s.widget(self.port, 24, 153, 306)
        s.button(345, 153, 120, '刷新设备', self.scan)
        self.private = tk.BooleanVar(value=cfg['privacy_mode'] == 'status_only')
        s.checkbox(24, 202, '仅显示状态，隐藏任务标题和摘要', self.private)
        self.control = tk.BooleanVar(value=cfg.get('allow_control',False))
        s.checkbox(24, 236, '允许设备审核与处理待办（USB / Wi-Fi）', self.control)
        s.button(24, 280, 145, '保存并连接', self.start, accent=True)
        s.button(185, 280, 125, '停止设备连接', self.stop)
        self.message = s.label(12, 349, '正在读取设备…', 12, width=470)
        s.label(12, 425, '连接指南', 15, True)
        s.label(12, 459, '① 上传 ESP32-S3-Island 固件，使用支持数据传输的 USB 线。\n'
                '② 刷新并选择板卡串口，点击“保存并连接”。\n'
                '③ 已保存设备重新接入后自动重连；烧录前先断开此连接。', 12, width=465)
        s.label(12, 544, '控制需要新版固件与来源支持；同意仅作用于当前请求。\n'
                '此页面不自动烧录固件。SD 素材传输及音频播放尚未开放。', 11, width=465)
        s.card(8,620,480,590)
        s.label(24,638,'无线连接 · USB 配置一次',16,True)
        self.wifi_enabled=tk.BooleanVar(value=cfg.get('wifi_enabled',False))
        s.checkbox(24,680,'开启 ESP32 无线服务（HTTPS 加密）',self.wifi_enabled)
        self.interface=ttk.Combobox(s.canvas,state='readonly',style='Peer.TCombobox')
        s.widget(self.interface,24,718,300)
        s.button(338,718,125,'刷新网卡',self.scan_interfaces)
        s.button(24,758,180,'保存无线服务设置',self.save_wireless,accent=True)
        self.ssid=ttk.Combobox(s.canvas,style='Peer.TCombobox')
        s.widget(self.ssid,24,806,300)
        s.button(338,806,125,'扫描 Wi-Fi',self.scan_wifi)
        self.password=tk.StringVar()
        entry=ttk.Entry(s.canvas,textvariable=self.password,show='•',style='Peer.TEntry')
        s.widget(entry,24,850,440)
        s.label(24,883,'输入 2.4GHz Wi-Fi 密码；也可手动填写隐藏网络名称。',10,width=440)
        s.button(24,916,180,'配置并连接 Wi-Fi',self.connect_wifi,accent=True)
        s.button(224,916,180,'删除选中设备绑定',self.revoke_wifi)
        self.wifi_message=s.label(24,963,'正在读取无线服务…',11,width=440)
        s.label(24,1040,'已保存的板卡（可离线删除绑定）',11)
        self.bound=ttk.Combobox(s.canvas,state='readonly',style='Peer.TCombobox')
        s.widget(self.bound,24,1070,440)
        s.label(24,1122,'首次使用 USB 配置；之后通电自动重连。\n仅列出 Windows 专用网络。防火墙需允许 SOLO\n专用网络入站 TCP 18769 / UDP 18770。',10,width=440)
        s.set_height(1240)
        self.scan_interfaces()
        self.scan()
        self.refresh()

    def scan(self):
        if self.scanning: return
        self.scanning = True
        self.note = '正在扫描串口…'
        results = self.results
        def work():
            # Worker holds only a queue, never a Tk-owned panel/StringVar.
            try: results.put((list_ports(), ''))
            except (ImportError, OSError) as error: results.put(([], '无法扫描：'+str(error)))
        threading.Thread(target=work, daemon=True).start()

    def start(self):
        index = self.port.current()
        if index < 0:
            self.note = '请先连接设备并刷新，选择板卡串口。'
            return
        selected = self.ports[index]
        config = dict(self.app.store.snapshot()['esp32'],enabled=True, port=selected['port'], serial_number=selected['serial_number'],
                      privacy_mode='status_only' if self.private.get() else 'summary',allow_control=self.control.get())
        try:
            self.app.store.update(esp32=config)
            self.app.esp32.configure(config)
            self.note = ''
        except (ValueError, OSError) as error: self.note = str(error)

    def stop(self):
        try:
            cfg = {**self.app.store.snapshot()['esp32'], 'enabled': False}
            self.app.store.update(esp32=cfg)
            self.app.esp32.configure(cfg)
            self.note = ''
        except (ValueError, OSError) as error: self.note = str(error)

    def refresh(self):
        if self.closed: return
        try: ports, error = self.results.get_nowait()
        except queue.Empty: pass
        else:
            selected = self.ports[self.port.current()]['port'] if self.port.current() >= 0 else self.app.store.snapshot()['esp32']['port']
            self.ports = ports
            self.scanning = False
            self.port.configure(values=[p['port']+' · '+p['name'] for p in ports])
            self.port.set('')
            if ports: self.port.current(next((i for i, p in enumerate(ports) if p['port'] == selected), 0))
            self.note = error or ('' if ports else '未发现串口。请检查 USB 数据线、驱动和设备电源。')
        state = self.app.esp32.snapshot_status()
        saved = self.app.store.snapshot()['esp32']
        mode = ('仅状态：屏幕不显示请求详情' if saved['privacy_mode']=='status_only' else
                '控制已开启：支持的请求可在屏幕审核' if saved.get('allow_control') else
                '只读监控：审核需勾选上方选项并保存')
        self.message.configure(text=self.note or state['message']+'\n'+state['port']+' · '+mode)
        try:
            interfaces=self.network_results.get_nowait()
            self.interfaces=interfaces
            self.interface.configure(values=[i.name+' · '+i.address for i in interfaces])
            if interfaces:self.interface.current(next((i for i,v in enumerate(interfaces) if v.id==saved.get('wifi_interface')),0))
        except queue.Empty:pass
        wireless=self.app.esp32.wireless_status()
        networks=wireless.get('networks',[])
        if networks!=self.wifi_networks:
            self.wifi_networks=networks;self.ssid.configure(values=[n['ssid']+' · '+str(n.get('rssi','?'))+' dBm'+
                (' · 不支持企业认证' if n.get('supported') is False else ' · 开放' if n.get('open') else '') for n in networks])
            if networks and not self.ssid.get():self.ssid.current(0)
        server=wireless.get('server',{})
        ids=[d['id'] for d in server.get('devices',[])]
        if tuple(self.bound.cget('values'))!=tuple(ids):
            previous=self.bound.get();self.bound.configure(values=ids)
            if ids:self.bound.set(previous if previous in ids else ids[0])
            else:self.bound.set('')
        online=any(d['online'] for d in server.get('devices',[]))
        self.wifi_message.configure(text=(server.get('message','')+'\n'+wireless.get('message','')+
            '\n'+('Wi-Fi 在线' if online else 'Wi-Fi 尚未连通')+' · 板卡 '+(wireless.get('board') or '待识别')))
        self.job = self.window.after(500, self.refresh)

    def scan_interfaces(self):
        from ..phone.network import discover
        results=self.network_results
        def work():
            try:results.put(discover())
            except Exception:results.put([])
        threading.Thread(target=work,daemon=True).start()

    def save_wireless(self):
        try:
            index=self.interface.current()
            if self.wifi_enabled.get() and index<0:raise ValueError('请先选择专用网络网卡')
            cfg=dict(self.app.store.snapshot()['esp32'],wifi_enabled=self.wifi_enabled.get(),
                     wifi_interface=self.interfaces[index].id if index>=0 else '')
            if cfg['wifi_enabled']:cfg['enabled']=True
            self.app.store.update(esp32=cfg);self.app.esp32.configure(cfg);self.note='无线服务设置已保存'
        except (ValueError,OSError) as error:self.note=str(error)

    def scan_wifi(self):
        try:self.app.esp32.wifi_scan();self.note='请等待板卡扫描网络'
        except ValueError as error:self.note=str(error)

    def connect_wifi(self):
        try:
            index=self.ssid.current();ssid=self.ssid.get()
            if index>=0:
                network=self.wifi_networks[index]
                if network.get('supported') is False:raise ValueError('暂不支持企业认证网络，请选择普通 2.4GHz Wi-Fi')
                ssid=network['ssid']
            self.app.esp32.wifi_configure(ssid,self.password.get());self.password.set('');self.note='配网已提交，进度见下方'
        except (ValueError,OSError) as error:self.note=str(error)

    def revoke_wifi(self):
        try:
            board=self.bound.get()
            if not board:raise ValueError('请先选择已保存的板卡')
            self.app.esp32.wireless.registry.revoke(board)
            if self.app.esp32.control:self.app.esp32.control.disconnect()
            self.note='无线绑定已删除；USB 连接不受影响'
        except (ValueError,OSError) as error:self.note=str(error)

    def wheel(self, event):
        if not self.closed and self.window.winfo_ismapped():
            self.surface.canvas.yview_scroll(-int(event.delta/120), 'units')

    def layout(self, x, y, width, height, background, foreground, backdrop=None):
        self.window.configure(bg=background)
        self.surface.set_theme(background, foreground)
        self.window.place(x=round(x), y=round(y), width=round(width), height=round(height))
        self.surface.set_backdrop(backdrop, (round(x), round(y)), (round(width), round(height)))

    def close(self):
        self.closed = True
        if self.job: self.window.after_cancel(self.job)
        self.app.root.unbind('<MouseWheel>', self.wheel_binding)
        self.window.destroy()
        self.surface.buttons.clear()
        self.surface.widgets.clear()
