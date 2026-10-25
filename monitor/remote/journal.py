"""Durable idempotency: an interrupted operation is never blindly replayed."""
import json
import sqlite3
import time
from pathlib import Path


class CommandJournal:
    def __init__(self, root):
        self.path = Path(root) / 'remote' / 'commands.sqlite3'

    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        db.execute('CREATE TABLE IF NOT EXISTS commands(device TEXT, id TEXT, signature TEXT, result TEXT, created REAL, PRIMARY KEY(device,id))')
        return db

    def get(self, device, id, signature=None):
        db = self._db()
        try:
            row = db.execute('SELECT signature,result FROM commands WHERE device=? AND id=?', (device,id)).fetchone()
            if not row: return None
            if signature is not None and signature != row[0]: raise ValueError('命令 ID 已用于不同操作')
            return json.loads(row[1]) if row[1] else dict(status='outcome_unknown', message='操作曾提交，结果待核对；不会重复执行')
        finally: db.close()

    def begin(self, device, id, signature):
        db = self._db()
        try:
            with db:
                # Retain receipts rather than deleting IDs and permitting a replay.
                if db.execute('SELECT count(*) FROM commands').fetchone()[0] >= 100000:
                    raise ValueError('操作回执已达容量，请维护数据后再操作')
                db.execute('INSERT INTO commands VALUES (?,?,?,NULL,?)', (device,id,signature,time.time()))
        finally: db.close()

    def finish(self, device, id, result):
        db = self._db()
        try:
            with db: db.execute('UPDATE commands SET result=? WHERE device=? AND id=?',
                                (json.dumps(result,ensure_ascii=False),device,id))
        finally: db.close()
