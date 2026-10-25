"""Event-loop-owned, ephemeral pairing and bounded viewer sessions."""
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from ..remote.permissions import normalize
import hashlib
import secrets
import time


class PairError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass
class Session:
    id: str
    name: str
    created: float
    touched: float
    streams: int = 0
    remembered: bool = False
    cookie_confirmed: bool = False
    grants: dict = field(default_factory=normalize)
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    policy_revision: int = 0


class Pairing:
    def __init__(self, clock=time.monotonic, *, registry=None):
        self.clock = clock
        self.code = ""
        self.deadline = 0
        self.failures = 0
        self.attempts = OrderedDict()
        self.sessions = {}
        self.registry = registry
        if registry:
            now = clock()
            self.sessions = {digest: Session(row["id"], row["name"], now, now, remembered=True, grants=normalize(row.get('grants')))
                             for digest, row in registry.read().items()}

    def open(self):
        self.code = f"{secrets.randbelow(100_000_000):08d}"
        self.deadline = self.clock() + 120
        self.failures = 0
        return self.code

    def pair(self, code, name, ip):
        now = self.clock()
        self.prune()
        history = self.attempts.setdefault(ip, deque())
        self.attempts.move_to_end(ip)
        while len(self.attempts) > 256:
            self.attempts.popitem(last=False)
        while history and history[0] <= now - 60:
            history.popleft()
        if len(history) >= 5:
            raise PairError("尝试过多，请一分钟后重试。", 429)
        if not self.code or now >= self.deadline:
            raise PairError("请在电脑端生成新的验证码。", 401)
        normalized = code.replace(" ", "") if isinstance(code, str) else ""
        if len(normalized) != 8 or not normalized.isascii() or not normalized.isdigit() or not secrets.compare_digest(normalized, self.code):
            history.append(now)
            self.failures += 1
            if self.failures >= 20:
                self.code = ""
            raise PairError("验证码错误或已失效。", 401)
        if len(self.sessions) >= 3:
            raise PairError("最多连接 3 台设备，请在电脑端撤销旧设备。", 409)
        token = secrets.token_urlsafe(32)
        self.code = ""  # No await between comparison and consumption.
        session = Session(secrets.token_hex(12), str(name or "手机")[:32], now, now)
        self.sessions[self.hash(token)] = session
        return token, session

    @staticmethod
    def hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def prune(self):
        now = self.clock()
        self.sessions = {key: s for key, s in self.sessions.items()
                         if s.remembered or (now - s.created < 28800 and now - s.touched < 1800)}

    def authenticate(self, token, *, touch=True):
        self.prune()
        session = self.sessions.get(self.hash(token or ""))
        if session and touch:
            session.touched = self.clock()
        return session

    def revoke(self, device_id):
        if self.registry:
            self.registry.remove(device_id)  # Persist first: failed deletion must not look successful.
        self.sessions = {k: s for k, s in self.sessions.items() if s.id != device_id}

    def remember(self, device_id):
        if not self.registry:
            raise ValueError("长期设备保存不可用。")
        self.prune()
        for digest, session in self.sessions.items():
            if session.id == device_id:
                self.registry.remember(digest, session.id, session.name, session.grants)
                session.remembered = True
                return
        raise ValueError("临时配对已失效，请重新连接手机。")

    def set_grants(self, device_id, grants):
        for session in self.sessions.values():
            if session.id == device_id:
                if not session.remembered: raise ValueError('请先长期保存设备')
                self.registry.set_grants(device_id, grants)
                session.grants = normalize(grants)
                session.policy_revision += 1
                return
        raise ValueError('设备已撤销')

    def close(self):
        self.code = ""
        self.sessions.clear()
        self.attempts.clear()
