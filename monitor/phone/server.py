"""Authenticated LAN transport: v1 viewing and versioned v2 device operations."""
import asyncio
import json
import hashlib
import time
import secrets
from pathlib import Path
from aiohttp import web
from .pairing import PairError
from .assets import icon
from .presence import Presence

WEB_ROOT = Path(__file__).with_name("web")
STATIC = {"app.js": "text/javascript", "transport.js": "text/javascript", "styles.css": "text/css", 'remote.js':'text/javascript'}
SESSION = web.RequestKey("phone_session", object)
CREDENTIAL = web.RequestKey('phone_credential', str)
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
           "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
           "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
           "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}


def make_app(host):
    host.presence = Presence()
    secure = getattr(host, 'secure', False)
    @web.middleware
    async def guard(request, handler):
        try:
            origin_url = getattr(host, 'origin', 'http://'+host.authority)
            if host.closing:
                raise web.HTTPServiceUnavailable()
            if secure and not request.secure: raise web.HTTPForbidden()
            if request.host != host.authority or not host.interface.allows(request.remote or ""):
                raise web.HTTPForbidden()
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                raise web.HTTPForbidden()
            origin = request.headers.get("Origin")
            if origin and origin != origin_url:
                raise web.HTTPForbidden()
            native = secure and request.headers.get('X-Solo-Client') == 'native' and not origin
            if request.method == "POST" and ((origin != origin_url and not native) or request.content_type != "application/json"):
                raise web.HTTPForbidden()
            if request.path.startswith("/api/") and request.path not in ("/api/v1/pair", '/api/v2/pair'):
                credential = request.cookies.get(host.cookie, "")
                if native:
                    auth=request.headers.get('Authorization','')
                    credential=auth[7:] if auth.startswith('Bearer ') else ''
                session = host.pairing.authenticate(credential)
                if session is None:
                    raise web.HTTPUnauthorized()
                request[SESSION] = session
                request[CREDENTIAL] = credential
                if request.path.startswith('/api/v2/') and request.method=='POST' and not native:
                    if not secrets.compare_digest(request.headers.get('X-Solo-CSRF',''),session.csrf): raise web.HTTPForbidden()
            response = await handler(request)
        except PairError as error:
            response = web.json_response({"error": str(error)}, status=error.status)
        except web.HTTPException as error:
            response = web.json_response({"error": {401: "会话已失效，请重新配对。", 403: "访问来源不允许。", 413: "请求过大。", 404: "接口不存在。", 405: "不支持此操作。", 429: "连接过多，请关闭其他标签页。"}.get(error.status, "请求无法完成。")}, status=error.status)
        except (ValueError, TypeError, KeyError, UnicodeError):
            response = web.json_response({"error": "请求格式无效。"}, status=400)
        except Exception:
            response = web.json_response({"error": "服务暂时无法处理请求。"}, status=500)
        if not response.prepared:
            for key, value in HEADERS.items():
                response.headers.setdefault(key, value)
        return response

    async def index(request):
        return web.Response(body=(WEB_ROOT/"index.html").read_bytes(), content_type="text/html")

    async def static(request):
        name = request.match_info["name"]
        if name not in STATIC:
            raise web.HTTPNotFound()
        return web.Response(body=(WEB_ROOT/name).read_bytes(), content_type=STATIC[name])

    async def icons(request):
        body = icon(request.match_info["name"])
        if body is None:
            raise web.HTTPNotFound()
        # Revalidate the brand after an app upgrade, retaining ETag/304 caching.
        cache = "public, no-cache" if request.match_info["name"] == "unknown" else "public, max-age=86400"
        headers = {"Cache-Control": cache, "ETag": '"'+hashlib.sha256(body).hexdigest()+'"'}
        if request.headers.get("If-None-Match") == headers["ETag"]:
            return web.Response(status=304, headers=headers)
        return web.Response(body=body, content_type="image/png", headers=headers)

    async def pair(request):
        if len(await request.read()) > 2048: raise web.HTTPRequestEntityTooLarge(max_size=2048,actual_size=2049)
        body = await request.json()
        if not isinstance(body, dict) or set(body)-{"code", "name"}:
            raise ValueError()
        name = body.get("name", "手机")
        if not isinstance(name, str) or len(name) > 32:
            raise ValueError()
        token, session = host.pairing.pair(body.get("code"), name, request.remote)
        native = secure and request.path=='/api/v2/pair' and request.headers.get('X-Solo-Client')=='native' and not request.headers.get('Origin')
        response = web.json_response({"mode": "viewer", "device_id": session.id, **({'credential':token} if native else {})})
        response.set_cookie(host.cookie, token, secure=secure, httponly=True, samesite="Strict", path="/")
        return response

    async def session(request):
        s = request[SESSION]
        response = web.json_response({"mode": "viewer", "device_id": s.id, "name": s.name,
                                  "remembered": s.remembered,
                                  "remaining_seconds": None if s.remembered else max(0, int(28800-(host.pairing.clock()-s.created)))})
        if s.remembered:
            # Desktop alone grants trust. Renew a browser-persistent HttpOnly
            # cookie only after that grant; never return its value to JS.
            response.set_cookie(host.cookie, request[CREDENTIAL], max_age=400*86400,
                                secure=secure, httponly=True, samesite="Strict", path="/")
            s.cookie_confirmed = True
        return response

    async def logout(request):
        host.pairing.revoke(request[SESSION].id)
        response = web.json_response({"ok": True})
        response.del_cookie(host.cookie, path="/")
        return response

    def snapshot_value():
        value = host.publisher.current()
        value.setdefault("appearance", {}).update(images=host.asset_ids)
        value["asset_revision"] = host.asset_revision
        return value

    async def viewers(request):
        body = await request.json()
        key = host.presence.register(request[SESSION], body.get("viewer_id"))
        return web.json_response({"viewer_id": key})

    async def presence(request):
        body = await request.json()
        host.presence.acknowledge(request[SESSION], body["viewer_id"], body["challenge"])
        return web.json_response({"ok": True})

    def receipt(request):
        key = request.query.get("viewer_id")
        return host.presence.challenge(request[SESSION], key) if key else None

    async def snapshot(request):
        value = snapshot_value()
        proof = receipt(request)
        etag = f'"{value["epoch"]}:{value["revision"]}:{host.asset_revision}"'
        headers = {"ETag": etag, "X-Producer-Age": str(value["producer_age_ms"]), "X-Server-Time": str(value["server_time_ms"])}
        if proof:
            headers["X-Viewer-Challenge"] = proof["challenge"]
            headers["X-Viewer-Id"] = proof["viewer_id"]
            value["presence"] = proof
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers=headers)
        return web.json_response(value, headers=headers)

    async def assets(request):
        body = host.assets.get(request.match_info["key"])
        if body is None:
            raise web.HTTPNotFound()
        etag = '"'+request.match_info["key"]+'"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        # Authentication was already checked by guard, including cached hits.
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers=headers)
        return web.Response(body=body, content_type="image/webp", headers=headers)

    async def events(request):
        s = request[SESSION]
        if s.streams >= 2 or sum(v.streams for v in host.pairing.sessions.values()) >= 6:
            raise web.HTTPTooManyRequests()
        s.streams += 1
        response = web.StreamResponse(headers={**HEADERS, "Content-Type": "text/event-stream", "X-Accel-Buffering": "no"})
        try:
            await response.prepare(request)
            previous, heartbeat = None, 0
            remembered = False
            remote_key = None
            while not host.closing:
                if host.pairing.authenticate(request[CREDENTIAL]) is None:
                    await asyncio.wait_for(response.write(b'event: session-ended\ndata: {}\n\n'), 1)
                    break
                if s.remembered and not remembered:
                    await asyncio.wait_for(response.write(b'event: device-saved\ndata: {}\n\n'), 1)
                    remembered = True
                if hasattr(host,'remote'):
                    current_key=host.remote.change_key(s)
                    if current_key != remote_key:
                        await asyncio.wait_for(response.write(b'event: service-changed\ndata: {}\n\n'),1)
                        remote_key=current_key
                value = snapshot_value()
                key = (value["epoch"], value["revision"], host.asset_revision)
                now = time.monotonic()
                if key != previous:
                    value["presence"] = receipt(request)
                    data = f'event: snapshot\nid: {key[0]}:{key[1]}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n'
                    previous, heartbeat = key, now
                # Faster than the browser's 5 s producer-staleness threshold.
                # Otherwise a healthy, unchanged task appears stale every cycle.
                elif now-heartbeat >= 2:
                    data = 'event: heartbeat\ndata: '+json.dumps({**{k: value[k] for k in ("server_time_ms", "producer_age_ms")}, "presence": receipt(request)})+'\n\n'
                    heartbeat = now
                else:
                    await asyncio.sleep(.5)
                    continue
                await asyncio.wait_for(response.write(data.encode()), 5)
                await asyncio.sleep(.5)
        except (ConnectionError, asyncio.TimeoutError, RuntimeError):
            pass
        finally:
            s.streams = max(0, s.streams-1)
        return response

    app = web.Application(middlewares=[guard], client_max_size=64*1024)
    app.router.add_get("/", index)
    app.router.add_get("/static/{name}", static)
    app.router.add_get("/icons/{name}", icons)
    app.router.add_post("/api/v1/pair", pair)
    app.router.add_post('/api/v2/pair',pair)
    app.router.add_get("/api/v1/session", session)
    app.router.add_post("/api/v1/viewers", viewers)
    app.router.add_post("/api/v1/presence", presence)
    app.router.add_post("/api/v1/logout", logout)
    app.router.add_get("/api/v1/snapshot", snapshot)
    app.router.add_get("/api/v1/events", events, allow_head=False)
    app.router.add_get("/api/v1/assets/{key}", assets)
    if hasattr(host,'remote'): host.remote.routes(app, SESSION)
    return app
