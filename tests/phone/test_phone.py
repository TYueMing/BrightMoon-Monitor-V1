"""LAN viewer tests use loopback fixtures only, never real Agent data."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time
import secrets
import socket

import pytest
from aiohttp import ClientSession, CookieJar, web
from PIL import Image

from monitor.phone.config import normalize
from monitor.phone.network import Interface, choose
from monitor.phone.pairing import Pairing, PairError
from monitor.phone.projector import PhoneProjector
from monitor.phone.publisher import SnapshotPublisher
from monitor.phone.assets import selected_images
from monitor.phone.server import make_app
from monitor.phone.service import PhoneService
from monitor.settings import SettingsStore
from monitor.UI.task_selection import TaskSelection


def run(**changes):
    return {"app_id": "codex", "run_id": "test", "turn_id": "turn-1", "status": "running",
            "started_at": time.time()-12, "task_summary": "整理测试", "message": "分析测试结果",
            "steps": [{"id": "one", "name": "执行测试", "status": "running"}], **changes}


def test_settings_migration_and_no_secret_persistence(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.snapshot()["phone"] == normalize()
    store.update(phone={"port": 12345, "enabled": True, "interface_id": "test", "code": "12345678", "token": "never-save"})
    assert store.snapshot()["schema_version"] == 5
    content = store.path.read_text()
    assert "12345678" not in content and "never-save" not in content
    assert SettingsStore(tmp_path).snapshot()["phone"]["port"] == 12345


@pytest.mark.parametrize("value", [{"port": True}, {"port": 80}, {"port": 65536}, {"enabled": 1}, {"privacy_mode": "admin"}, {"share_personalization_images": "yes"}])
def test_invalid_config(value):
    with pytest.raises(ValueError):
        normalize(value, strict=True)
    normalize(value)


def test_pairing_one_use_expiry_limits_and_revoke():
    now = [1.0]
    pairing = Pairing(lambda: now[0])
    code = pairing.open()
    token, session = pairing.pair(code[:4]+" "+code[4:], "手机", "127.0.0.1")
    assert token not in str(pairing.sessions)
    assert pairing.authenticate(token) is session
    with pytest.raises(PairError):
        pairing.pair(code, "another", "127.0.0.1")
    pairing.revoke(session.id)
    assert pairing.authenticate(token) is None
    code = pairing.open()
    now[0] += 121
    with pytest.raises(PairError):
        pairing.pair(code, "old", "127.0.0.1")
    pairing.open()
    for _ in range(5):
        with pytest.raises(PairError):
            pairing.pair("wrong", "x", "one")
    with pytest.raises(PairError) as error:
        pairing.pair(pairing.code, "x", "one")
    assert error.value.status == 429
    for i in range(15):
        with pytest.raises(PairError):
            pairing.pair("wrong", "x", str(i))
    assert not pairing.code


def test_session_device_cap_idle_and_absolute_expiry():
    now = [0]
    pairing = Pairing(lambda: now[0])
    tokens = [pairing.pair(pairing.open(), "fixture", str(i))[0] for i in range(3)]
    with pytest.raises(PairError):
        pairing.pair(pairing.open(), "fourth", "4")
    now[0] = 1799
    assert pairing.authenticate(tokens[0])
    now[0] = 1801
    assert pairing.authenticate(tokens[1]) is None
    for n in range(2, 18):
        now[0] = n*1600
        assert pairing.authenticate(tokens[0])
    now[0] = 28800
    assert pairing.authenticate(tokens[0]) is None


def test_projector_privacy_identity_and_true_progress():
    projector = PhoneProjector(b"fixture-secret")
    task = run(message="secret-token=abcdef C:/Users/private/project", codex={"usage": {"total": 12345}},
               pending_confirmations=[{"id": "private-id", "arguments": {"password": "NOT FOR PHONE"}}])
    snapshot = {"runs": [task], "preferences": {"password": "NOT FOR PHONE", "monitor_sources": {"secret": "x"}}}
    value = projector.project(snapshot, [task], task["run_id"], True, normalize())
    body = json.dumps(value)
    assert "C:/Users" not in body and "abcdef" not in body and "NOT FOR PHONE" not in body and "private-id" not in body
    private = projector.project(snapshot, [task], "", True, normalize({"privacy_mode": "status_only"}))
    assert "整理测试" not in json.dumps(private, ensure_ascii=False)
    assert value["tasks"][0]["task_key"] != projector.key({**task, "turn_id": "turn-2"})
    metrics = value["tasks"][0]["detail_blocks"][0]["items"]
    assert metrics[0]["label"] == "会话累计 tokens（非当前上下文）"
    harness = projector.task(run(app_id="deepseek_harness"), False, 0)
    assert harness["chain"]["kind"] == "observed" and harness["chain"]["completed"] is None


def test_only_desktop_visible_tasks_are_published_and_retirement_is_not_rearmed():
    now = time.time()
    old = run(status="completed", ended_at=now-120)
    active = run(run_id="active")
    snapshot = {"runs": [old, active], "preferences": {"monitor_hide_minutes": 1}}
    selection = TaskSelection()
    selection.update(snapshot, now=now)
    assert len(selection.runs) == 1
    projector = PhoneProjector(b"s")
    for _ in range(10):
        view = projector.project(snapshot, selection.runs, selection.focus_id, True, normalize())
        assert len(view["tasks"]) == 1 and view["tasks"][0]["task_key"] == projector.key(active)
    assert not selection.lifetime.visible(snapshot, now=now).get("test")


def test_publisher_semantic_revision_and_copy():
    publisher = SnapshotPublisher()
    view = {"tasks": [{"title": "one"}]}
    publisher.publish(view)
    first = publisher.current()
    publisher.publish(view)
    assert publisher.current()["revision"] == first["revision"]
    view["tasks"][0]["title"] = "mutated"
    assert publisher.current()["tasks"][0]["title"] == "one"
    publisher.publish(view)
    assert publisher.current()["revision"] == first["revision"]+1
    publisher.produced -= 20
    assert publisher.current()["producer_age_ms"] >= 20000
    with pytest.raises(ValueError):
        publisher.publish({"huge": "x"*300000})


def test_image_allowlist_reencoding_and_no_traversal(tmp_path):
    (tmp_path/"backgrounds").mkdir()
    Image.new("RGB", (1600, 900), "blue").save(tmp_path/"backgrounds/wall.png")
    assets, ids = selected_images(tmp_path, {"monitor_background_mode": "photo", "monitor_background_photo": "backgrounds/wall.png"})
    assert len(assets) == 1 and ids["wallpaper"] in assets
    import io
    with Image.open(io.BytesIO(assets[ids["wallpaper"]])) as im:
        assert max(im.size) <= 1280 and not im.getexif()
    assert selected_images(tmp_path, {"monitor_background_mode": "photo", "monitor_background_photo": "../outside.png"}) == ({}, {})


async def server_fixture():
    host = SimpleNamespace(interface=Interface("test", "Loopback", "127.0.0.1", 32, "fixture"),
                           authority="", cookie="solo_fixture", closing=False,
                           pairing=Pairing(), publisher=SnapshotPublisher(), assets={"test-image": b"image"},
                           asset_ids={}, asset_revision=0)
    host.publisher.publish({"schema_version": 1, "tasks": [], "mode": "viewer"})
    runner = web.AppRunner(make_app(host), access_log=None, shutdown_timeout=.1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    host.authority = f"127.0.0.1:{port}"
    return host, runner, "http://"+host.authority


def test_http_auth_origin_host_cache_assets_and_readonly():
    async def check():
        host, runner, url = await server_fixture()
        try:
            async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                for route in ("snapshot", "events", "session", "assets/test-image"):
                    async with client.get(url+"/api/v1/"+route) as response:
                        assert response.status == 401
                assert (await client.get(url+"/", headers={"Host": "evil.test"})).status == 403
                assert (await client.get(url+"/", headers={"Origin": "http://evil.test"})).status == 403
                assert (await client.post(url+"/api/v1/pair", json={"code": host.pairing.open()})).status == 403
                headers = {"Origin": url}
                response = await client.post(url+"/api/v1/pair", json={"code": host.pairing.open()}, headers=headers)
                assert response.status == 200
                cookie = response.headers["Set-Cookie"]
                assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
                response = await client.get(url+"/api/v1/snapshot")
                assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
                etag = response.headers["ETag"]
                assert (await client.get(url+"/api/v1/snapshot", headers={"If-None-Match": etag})).status == 304
                assert (await client.get(url+"/api/v1/assets/test-image")).status == 200
                for name in ("approve", "exec", "command", "proxy"):
                    assert (await client.post(url+"/api/v1/"+name, json={}, headers=headers)).status == 404
                assert (await client.post(url+"/api/v1/pair", data='x'*3000, headers={**headers,"Content-Type":"application/json"})).status == 413
                assert (await client.post(url+"/api/v1/logout", json={}, headers=headers)).status == 200
                assert (await client.get(url+"/api/v1/snapshot", headers={"If-None-Match": etag})).status == 401
                assert (await client.get(url+"/api/v1/assets/test-image")).status == 401
        finally:
            host.closing = True
            await runner.cleanup()
    asyncio.run(check())


def test_sse_latest_initial_update_limit_and_revocation():
    async def read_event(response):
        lines = []
        while True:
            line = await asyncio.wait_for(response.content.readline(), 2)
            if not line or line == b"\n":
                return b"".join(lines)
            lines.append(line)
    async def check():
        host, runner, url = await server_fixture()
        try:
            async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                response = await client.post(url+"/api/v1/pair", json={"code": host.pairing.open()}, headers={"Origin": url})
                device = (await response.json())["device_id"]
                stream = await client.get(url+"/api/v1/events")
                assert b"event: snapshot" in await read_event(stream)
                second = await client.get(url+"/api/v1/events")
                assert b"event: snapshot" in await read_event(second)
                assert (await client.get(url+"/api/v1/events")).status == 429
                host.publisher.publish({"schema_version": 1, "tasks": [{"title": "updated"}]})
                assert b"updated" in await read_event(stream)
                host.pairing.revoke(device)
                assert b"session-ended" in await read_event(stream)
                stream.close()
                second.close()
        finally:
            host.closing = True
            await runner.cleanup()
    asyncio.run(check())


def test_network_requires_selected_private_interface():
    interface = Interface("a", "Wi-Fi", "192.168.2.3", 24, "fixture")
    assert interface.allows("192.168.2.4") and not interface.allows("192.168.3.4")
    with pytest.raises(ValueError):
        choose(normalize(), [interface])
    assert choose(normalize({"interface_id": "a"}), [interface]) is interface


def test_real_service_start_stop_port_release_and_no_data_write(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    interface = Interface("fixture", "Loopback", "127.0.0.1", 32, "fixture")
    service = PhoneService(tmp_path, discovery=lambda: [interface])
    service.start(normalize({"enabled": True, "interface_id": "fixture", "port": port}))
    deadline = time.monotonic()+5
    while service.snapshot_status()["state"] == "starting" and time.monotonic() < deadline:
        time.sleep(.02)
    try:
        assert service.snapshot_status()["state"] == "running"
        service.command("pair")
        task = run()
        service.publish({"preferences": {}}, [task], task["run_id"], True)
        assert service.publisher.current()["tasks"]
        assert not list(tmp_path.iterdir())
    finally:
        service.stop()
        service.thread.join(3)
    assert not service.thread.is_alive()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_desktop_publication_uses_filtered_state_and_does_not_break_poll(tmp_path):
    import tkinter as tk
    from unittest.mock import Mock
    from monitor.app import MonitorApp
    from monitor.src.source_hub import SourceHub
    root = tk.Tk()
    root.withdraw()
    store = SettingsStore(tmp_path)
    store.set_source("codex", enabled=True)
    store.update(monitor_sound_enabled=False, task_sound_enabled=False)
    source = Mock()
    source.poll.return_value = {"runs": [run(status="completed", ended_at=time.time()-121), run(run_id="active")], "actions": [], "status": "ready"}
    hub = SourceHub(store.snapshot, factories={"codex": lambda _: source}, start_worker=False)
    app = MonitorApp(root, store, hub)
    try:
        hub.poll_sources()
        publisher = Mock()
        app.island.view_publisher = publisher
        app.island._poll()
        assert [r["run_id"] for r in publisher.call_args.args[1]] == ["active"]
        app.read_snapshot = Mock(side_effect=AssertionError("status page must not resample"))
        app.refresh_status()
        assert app.read_snapshot.call_count == 0
        app.island.view_publisher = Mock(side_effect=ValueError("malformed fixture"))
        app.island._poll()
        assert app.island.publish_error and "poll" in app.island._jobs
        assert "tab:remote" in app.panel.controls
        assert "phone-settings" not in app.panel.controls
        app.phone.discovery = lambda: []
        app.show_phone_settings()
        assert app.panel.active_tab == "remote"
        assert app.phone_dialog.window.winfo_class() == "Frame"
        assert app.phone_dialog.window.master is app.panel
        app.panel.select_tab("monitor")
        assert not app.phone_dialog.window.winfo_ismapped()
    finally:
        app.close()


@pytest.mark.parametrize("raw", [
    '{"password": "secret value"}', "password='secret value'",
    '{"api_key":"secret value"}', 'Authorization=Bearer secretvalue',
])
def test_quoted_credential_redaction(raw):
    from monitor.phone.privacy import text
    assert "secret" not in text(raw)


def test_projector_bounded_unicode_missing_fields_and_attention_identity():
    projector = PhoneProjector(b"test")
    value = projector.project({}, [run(task_summary="汉"*20000,
        steps=[{"name": "汉"*20000, "status": "pending"}]*100,
        tools=[{"name": "汉"*20000, "detail": "字"*20000}]*100) for _ in range(7)], "", True, normalize())
    assert len(value["tasks"]) == 5
    assert len(json.dumps(value, ensure_ascii=False).encode()) <= 256*1024
    assert value["tasks"][0]["chain"]["nodes"][0]["status"] == "queued"
    assert value["tasks"][0]["chain"]["completed"] is None
    one = projector.task(run(status="waiting_confirmation"), False, 0)
    two = projector.task(run(status="waiting_confirmation", run_id="different"), False, 1)
    assert one["attention"]["id"] != two["attention"]["id"]
    assert PhoneProjector(b"other-boot").key(run()) != projector.key(run())
    private = projector.task(run(codex={"usage": {"input": 100}}), True, 0)
    assert not private["detail_blocks"]


def test_sse_heartbeat_keeps_unchanged_producer_fresh():
    async def check():
        host, runner, url = await server_fixture()
        try:
            async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                await client.post(url+"/api/v1/pair", json={"code": host.pairing.open()}, headers={"Origin": url})
                stream = await client.get(url+"/api/v1/events")
                started = time.monotonic()
                data = b""
                while b"event: heartbeat" not in data:
                    data += await asyncio.wait_for(stream.content.readline(), 3)
                assert time.monotonic()-started < 5
                stream.close()
        finally:
            host.closing = True
            await runner.cleanup()
    asyncio.run(check())


def wait_state(service, expected, seconds=4):
    deadline = time.monotonic()+seconds
    while service.snapshot_status()["state"] not in expected and time.monotonic() < deadline:
        time.sleep(.02)
    return service.snapshot_status()["state"]


def test_service_network_change_fails_closed(tmp_path):
    interfaces = [Interface("fixture", "Loopback", "127.0.0.1", 32, "fixture")]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    service = PhoneService(tmp_path, discovery=lambda: list(interfaces))
    service.NETWORK_CHECK_SECONDS = .05
    service.start({"interface_id": "fixture", "port": port})
    try:
        assert wait_state(service, {"running", "error"}) == "running"
        interfaces.clear()
        assert wait_state(service, {"error"}) == "error"
        assert not service.snapshot_status()["code"]
    finally:
        service.stop()
        service.thread.join(3)
    assert not service.thread.is_alive()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_service_stop_during_discovery_and_port_collision(tmp_path):
    import threading
    barrier = threading.Event()
    interface = Interface("fixture", "Loopback", "127.0.0.1", 32, "fixture")
    def discover_later():
        barrier.wait(2)
        return [interface]
    service = PhoneService(tmp_path, discovery=discover_later)
    service.start({"interface_id": "fixture"})
    service.stop()
    barrier.set()
    service.thread.join(3)
    assert service.snapshot_status()["state"] == "stopped"
    assert service.closing and not service.thread.is_alive()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        service.discovery = lambda: [interface]
        service.start({"interface_id": "fixture", "port": occupied.getsockname()[1]})
        try:
            assert wait_state(service, {"error"}, seconds=10) == "error"
            service.thread.join(2)
            assert not service.thread.is_alive()
        finally:
            service.stop()
            service.thread.join(3)



def test_phone_settings_scroll_and_invalid_port_not_saved(tmp_path):
    import tkinter as tk
    from monitor.phone.settings_panel import PhoneSettings
    root = tk.Tk()
    root.withdraw()
    store = SettingsStore(tmp_path)
    interface = Interface("fixture", "Wi-Fi", "192.168.1.5", 24, "Private")
    service = PhoneService(tmp_path, discovery=lambda: [interface])
    app = SimpleNamespace(root=root, store=store, phone=service, phone_dialog=None)
    panel = PhoneSettings(app)
    app.phone_dialog = panel
    try:
        panel.window.place_forget()
        panel.interfaces = [interface]
        panel.interface.configure(values=[interface.name])
        panel.interface.current(0)
        panel.port.set("80")
        panel.start()
        assert panel.local_error and not store.path.exists()
        assert service.thread is None
        service.report(state="running", devices=[{"id": str(i), "name": "测试手机", "online": True} for i in range(3)])
        panel.window.after_cancel(panel.job)
        panel.refresh()
        root.update_idletasks()
        assert len(panel.device_buttons) == 6
        assert all(button.winfo_exists() for button in panel.device_buttons)
    finally:
        panel.close()
        root.destroy()


def test_reminder_weekday_mapping_and_own_presentation():
    result = PhoneProjector(b"fixture").task(run(app_id="solo_task", status="reminding",
        reminder={"due_at": 1780000000, "task": {"weekdays": [0, 6], "time_of_day": "12:00", "lead_minutes": 5}}), False, 0)
    assert result["chain"]["kind"] == "reminder"
    assert "每周 一、日" in result["detail_blocks"][0]["text"]
    assert result["reminder_due_at_ms"] == 1780000000000


def test_source_specific_details_are_readonly_and_do_not_leak_questions_in_private_mode():
    projector = PhoneProjector(b"fixture")
    task = run(app_id="opencode", status="waiting_confirmation",
               child_summary={"total": 3, "completed": 2}, compaction={"active": True},
               pending_confirmations=[{"id": "q", "questions": [{"question": "选择测试环境？"}], "arguments": {"secret": "never-share"}}])
    value = projector.task(task, False, 0)
    body = json.dumps(value, ensure_ascii=False)
    assert "子任务总数" in body and "正在压缩" in body and "选择测试环境" in body
    assert "never-share" not in body and "选择测试环境" not in json.dumps(projector.task(task, True, 0), ensure_ascii=False)
    codex = projector.task(run(codex={"model": "fixture-model", "connection_message": "日志只读"}), False, 0)
    assert "fixture-model" in json.dumps(codex)



def test_trusted_devices_require_desktop_grant_survive_restart_and_revoke(tmp_path):
    from monitor.phone.devices import TrustedDevices
    registry = TrustedDevices(tmp_path)
    now = [0]
    pairing = Pairing(lambda: now[0], registry=registry)
    token, session = pairing.pair(pairing.open(), "手机", "local")
    assert not registry.path.exists() and not session.remembered
    pairing.remember(session.id)
    assert session.remembered
    assert token not in registry.path.read_text(encoding="utf-8")
    temporary, _ = pairing.pair(pairing.open(), "临时", "other")
    pairing.close()
    now[0] = 999999
    restarted = Pairing(lambda: now[0], registry=registry)
    assert restarted.authenticate(token).id == session.id
    assert restarted.authenticate(temporary) is None
    restarted.revoke(session.id)
    assert restarted.authenticate(token) is None
    assert Pairing(registry=registry).authenticate(token) is None


def test_trusted_device_capacity_and_offline_management(tmp_path):
    from monitor.phone.devices import TrustedDevices
    registry = TrustedDevices(tmp_path)
    pairing = Pairing(registry=registry)
    for index in range(3):
        _, session = pairing.pair(pairing.open(), str(index), str(index))
        pairing.remember(session.id)
    restarted = Pairing(registry=registry)
    with pytest.raises(PairError):
        restarted.pair(restarted.open(), "fourth", "4")
    with pytest.raises(ValueError):
        registry.remember("a"*64, "b"*24, "fourth")
    service = PhoneService(tmp_path)
    assert len(service.snapshot_status()["devices"]) == 3
    service.command("revoke", session.id)
    assert len(service.snapshot_status()["devices"]) == 2
    registry.path.write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError):
        Pairing(registry=registry)
    assert registry.path.read_text() == "corrupt"


def test_http_long_term_cookie_cache_authorization_and_logout(tmp_path):
    from monitor.phone.devices import TrustedDevices
    async def check():
        host, runner, url = await server_fixture()
        host.pairing = Pairing(registry=TrustedDevices(tmp_path))
        try:
            async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                reply = await client.post(url+"/api/v1/pair", json={"code": host.pairing.open()}, headers={"Origin": url})
                device_id = (await reply.json())["device_id"]
                assert "Max-Age" not in reply.headers["Set-Cookie"]
                assert (await client.post(url+"/api/v1/remember", json={"device_id": device_id}, headers={"Origin": url})).status == 404
                host.pairing.remember(device_id)
                reply = await client.get(url+"/api/v1/session")
                assert (await reply.json())["remembered"]
                assert "Max-Age=34560000" in reply.headers["Set-Cookie"]
                # Same browser cookie remains valid against a freshly loaded registry.
                host.pairing = Pairing(registry=TrustedDevices(tmp_path))
                assert (await client.get(url+"/api/v1/session")).status == 200
                reply = await client.get(url+"/api/v1/assets/test-image")
                etag = reply.headers["ETag"]
                assert reply.headers["Cache-Control"] == "private, no-cache"
                assert (await client.get(url+"/api/v1/assets/test-image", headers={"If-None-Match": etag})).status == 304
                stream = await client.get(url+"/api/v1/events")
                assert b"device-saved" in await asyncio.wait_for(stream.content.readline(), 2)
                stream.close()
                await client.post(url+"/api/v1/logout", json={}, headers={"Origin": url})
                assert not TrustedDevices(tmp_path).read()
                assert (await client.get(url+"/api/v1/assets/test-image", headers={"If-None-Match": etag})).status == 401
        finally:
            host.closing = True
            await runner.cleanup()
    asyncio.run(check())


def test_page_wallpaper_is_allowlisted_and_deduplicated(tmp_path):
    folder = tmp_path/"backgrounds"
    folder.mkdir()
    Image.new("RGB", (80, 90), "pink").save(folder/"photo.png")
    images, ids = selected_images(tmp_path, {"background_mode": "photo", "background_photo": "backgrounds/photo.png",
                                           "monitor_background_mode": "photo", "monitor_background_photo": "backgrounds/photo.png"})
    assert ids["page_wallpaper"] == ids["wallpaper"] and len(images) == 1



def test_remember_confirmation_is_desktop_only(tmp_path):
    import tkinter as tk
    from unittest.mock import Mock, patch
    from monitor.phone.settings_panel import PhoneSettings
    root = tk.Tk()
    root.withdraw()
    service = PhoneService(tmp_path, discovery=lambda: [])
    service.command = Mock()
    app = SimpleNamespace(root=root, store=SettingsStore(tmp_path), phone=service, phone_dialog=None)
    panel = PhoneSettings(app)
    app.phone_dialog = panel
    try:
        with patch("monitor.phone.settings_panel.messagebox.askyesno", return_value=False):
            panel.remember("fixture")
        service.command.assert_not_called()
        with patch("monitor.phone.settings_panel.messagebox.askyesno", return_value=True):
            panel.remember("fixture")
        service.command.assert_called_once_with("remember", "fixture")
    finally:
        panel.close()
        root.destroy()


def test_failed_device_write_does_not_claim_success(tmp_path):
    from unittest.mock import patch
    from monitor.phone.devices import TrustedDevices
    registry = TrustedDevices(tmp_path)
    pairing = Pairing(registry=registry)
    token, session = pairing.pair(pairing.open(), "phone", "local")
    with patch.object(registry, "_write", side_effect=OSError("fixture disk failure")):
        with pytest.raises(OSError):
            pairing.remember(session.id)
    assert not session.remembered
    pairing.remember(session.id)
    with patch.object(registry, "_write", side_effect=OSError("fixture disk failure")):
        with pytest.raises(OSError):
            pairing.revoke(session.id)
    assert pairing.authenticate(token) is not None
    assert len(TrustedDevices(tmp_path).read()) == 1


def test_remote_background_tracks_parent_crop_scroll_and_theme(tmp_path):
    import tkinter as tk
    from PIL import Image
    from monitor.phone.settings_panel import PhoneSettings
    root = tk.Tk()
    root.geometry("800x650")
    store = SettingsStore(tmp_path)
    service = PhoneService(tmp_path, discovery=lambda: [])
    app = SimpleNamespace(root=root, store=store, phone=service, phone_dialog=None)
    panel = PhoneSettings(app)
    # Vary every row so scrolling the background would produce a visible seam.
    backdrop = Image.new("RGB", (900, 800))
    for y in range(800):
        backdrop.paste((y % 256, 30, 60), (0, y, 900, y+1))
    try:
        panel.layout(100, 80, 540, 450, "#ddeeff", "#123456", backdrop)
        root.update()
        surface = panel.surface
        assert surface.crop.getpixel((5, 5)) == backdrop.getpixel((105, 85))
        photo = surface.photo
        entry = next(w for w in surface.widgets["content"] if w.winfo_class() == "TEntry")
        entry.focus_force()
        panel.port.set("18900")
        panel.canvas.yview_moveto(1)
        root.update()
        assert panel.canvas.canvasy(0) > 0
        assert panel.canvas.coords(surface.background)[1] == panel.canvas.canvasy(0)
        assert surface.photo is photo  # no image work on each scroll/status tick
        panel.layout(120, 60, 550, 470, "#123456", "#ffffff", backdrop)
        root.update()
        assert surface.crop.getpixel((5, 5)) == backdrop.getpixel((125, 65))
        assert panel.port.get() == "18900" and root.focus_get() is entry
        assert panel.canvas.itemcget(panel.message.item, "fill") == "#ffffff"
        updated = Image.new("RGB", (900, 800), "#102030")
        panel.layout(120, 60, 550, 470, "#123456", "#ffffff", updated)
        root.update()
        assert surface.crop.getpixel((5, 5)) == (16, 32, 48)
    finally:
        panel.close()
        root.destroy()
