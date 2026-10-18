"""Own a single network thread. Tk only publishes values or enqueues commands."""
import asyncio
import queue
import secrets
import threading
import time
from .config import normalize
from .network import discover, choose
from .pairing import Pairing
from .projector import PhoneProjector
from .publisher import SnapshotPublisher
from .assets import selected_images
from .devices import TrustedDevices


class PhoneService:
    NETWORK_CHECK_SECONDS = 10

    def __init__(self, data_root, *, discovery=discover):
        self.data_root = data_root
        self.registry = TrustedDevices(data_root)
        self.discovery = discovery
        self.thread = None
        self.stop_event = threading.Event()
        self.commands = queue.Queue(maxsize=16)
        self.status_lock = threading.Lock()
        self.status = {"state": "stopped", "message": "未开启", "devices": []}
        self.media_lock = threading.Lock()
        self.media_request = {}
        self.config = normalize()
        self.closing = False
        self.publisher = SnapshotPublisher()
        self.projector = PhoneProjector(secrets.token_bytes(32))

    def report(self, **changes):
        with self.status_lock:
            self.status = {**self.status, **changes}

    def snapshot_status(self):
        with self.status_lock:
            status = dict(self.status)
        if status["state"] not in ("running", "starting"):
            try:
                status["devices"] = [{"id": r["id"], "name": r["name"], "remembered": True, "online": False}
                                     for r in self.registry.read().values()]
            except ValueError as error:
                status["message"] = str(error)
        return status

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive() and not self.stop_event.is_set()

    def start(self, config):
        if self.thread and self.thread.is_alive():
            raise ValueError("服务仍在运行或正在关闭，请稍后重试。")
        self.config = normalize(config, strict=True)
        self.stop_event.clear()
        self.closing = False
        self.publisher = SnapshotPublisher()
        self.projector = PhoneProjector(secrets.token_bytes(32))
        self.commands = queue.Queue(maxsize=16)
        with self.media_lock:
            self.media_request = {}
        self.report(state="starting", message="正在验证私人网络…", code="", devices=[], address="")
        self.thread = threading.Thread(target=self._thread_main, name="solo-phone", daemon=True)
        self.thread.start()

    def stop(self):
        self.closing = True  # Reject new requests before the worker next wakes.
        self.stop_event.set()
        self.report(state="stopping" if self.thread and self.thread.is_alive() else "stopped", message="已停止访问", code="", devices=[])

    def command(self, kind, value=None):
        if kind == "revoke" and not (self.thread and self.thread.is_alive()):
            self.registry.remove(value)
            return
        if not self.active or self.snapshot_status()["state"] != "running":
            raise ValueError("请先开启手机访问。")
        try:
            self.commands.put_nowait((kind, value))
        except queue.Full:
            raise ValueError("操作过于频繁，请稍后再试。") from None

    def publish(self, snapshot, visible, focus, desktop_visible):
        if not self.active:
            return
        self.publisher.publish(self.projector.project(snapshot, visible, focus, desktop_visible, self.config))
        prefs = snapshot.get("preferences", {})
        request = {k: prefs.get(k) for k in ("background_mode", "background_photo", "monitor_background_mode", "monitor_background_photo", "rope_enabled", "rope_image", "cartoon_enabled", "cartoon_image")}
        if not self.config["share_personalization_images"]:
            request = {}
        with self.media_lock:
            self.media_request = request

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except ImportError:
            self.report(state="error", message="缺少 aiohttp，请安装最新 requirements.txt。")
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else "服务无法启动或网络已不可用，请检查网卡与端口。"
            self.report(state="error", message=message)
        finally:
            self.stop_event.set()
            self.report(code="", devices=[])
            if self.snapshot_status()["state"] in ("starting", "stopping"):
                self.report(state="stopped", message="手机访问已关闭")

    async def _run(self):
        from aiohttp import web
        from .server import make_app
        self.interface = choose(self.config, await asyncio.to_thread(self.discovery))
        if self.stop_event.is_set():
            return
        self.authority = f'{self.interface.address}:{self.config["port"]}'
        self.cookie = "solo_phone_"+str(self.config["port"])
        self.pairing = Pairing(registry=self.registry)
        self.assets, self.asset_ids, self.asset_revision = {}, {}, 0
        runner = web.AppRunner(make_app(self), access_log=None, shutdown_timeout=.5)
        await runner.setup()
        check = media = None
        try:
            await web.TCPSite(runner, self.interface.address, self.config["port"]).start()
            self.report(state="running", message="已开启 · 只读访问", address="http://"+self.authority+"/")
            last_network, last_media, last_request = time.monotonic(), 0, None
            while not self.stop_event.is_set():
                now = time.monotonic()
                if check and check.done():
                    try:
                        current = choose(self.config, check.result())
                    except Exception:
                        current = None
                    if current != self.interface:
                        raise ValueError("网络地址或类型已改变，访问已停止。请重新选择网卡并配对。")
                    check, last_network = None, now
                if check is None and now-last_network >= self.NETWORK_CHECK_SECONDS:
                    check = asyncio.create_task(asyncio.to_thread(self.discovery))
                while not self.commands.empty():
                    kind, value = self.commands.get_nowait()
                    try:
                        if kind == "pair":
                            self.report(code=self.pairing.open())
                        elif kind == "revoke":
                            self.pairing.revoke(value)
                        elif kind == "remember":
                            self.pairing.remember(value)
                        self.report(command_error="")
                    except (ValueError, OSError) as error:
                        self.report(command_error=str(error))
                self.pairing.prune()
                self.report(code=self.pairing.code if now < self.pairing.deadline else "",
                            code_seconds=max(0, int(self.pairing.deadline-now)),
                            devices=[{"id": s.id, "name": s.name, "online": s.streams > 0,
                                      "remembered": s.remembered, "cookie_confirmed": s.cookie_confirmed,
                                      "last_seen_seconds": int(now-s.touched)} for s in self.pairing.sessions.values()])
                if media and media.done():
                    self.assets, self.asset_ids = media.result()
                    self.asset_revision += 1
                    media = None
                if media is None and now-last_media >= 1:
                    with self.media_lock:
                        request = dict(self.media_request)
                    if request != last_request:
                        # Decoding must not delay revocation or network checks.
                        media = asyncio.create_task(asyncio.to_thread(selected_images, self.data_root, request))
                        last_request = request
                    last_media = now
                await asyncio.sleep(.1)
        finally:
            self.closing = True
            self.pairing.close()
            self.assets, self.asset_ids = {}, {}
            await runner.cleanup()
            pending = [task for task in (check, media) if task is not None]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.report(state="stopped", message="手机访问已关闭", code="", devices=[])
