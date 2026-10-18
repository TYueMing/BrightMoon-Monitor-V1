"""One latest snapshot: bounded across threads and slow browser subscribers."""
import hashlib
import json
import threading
import time
import uuid


class SnapshotPublisher:
    def __init__(self):
        self.lock = threading.Lock()
        self.epoch = uuid.uuid4().hex
        self.revision = 0
        self.digest = ""
        self.body = None
        self.produced = 0.0
        self.generated = 0

    def publish(self, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(body) > 256*1024:
            raise ValueError("Phone snapshot too large")
        digest = hashlib.sha256(body).hexdigest()
        with self.lock:
            self.produced = time.monotonic()
            if digest != self.digest:
                self.revision += 1
                self.body, self.digest, self.generated = body, digest, int(time.time()*1000)

    def current(self):
        with self.lock:
            value = json.loads(self.body) if self.body else {"schema_version": 1, "mode": "viewer", "tasks": [], "loading": True}
            value.update(epoch=self.epoch, revision=self.revision, generated_at_ms=self.generated,
                         server_time_ms=int(time.time()*1000),
                         producer_age_ms=int((time.monotonic()-self.produced)*1000) if self.produced else None)
            return value
