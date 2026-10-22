"""Local transactional mailbox for concurrent hooks and one monitor owner."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

LEASE_SECONDS = 5
REQUEST_LEASE = 3


@contextmanager
def connection(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'events.sqlite3', timeout=.3)
    try:
        db.execute('PRAGMA busy_timeout=300')
        db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, at REAL, body TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS lease (id INTEGER PRIMARY KEY, owner TEXT, until REAL, approvals INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, revision TEXT, session TEXT, prompt TEXT, state TEXT, deadline REAL, heartbeat REAL, decision TEXT)')
        yield db
        db.commit()
    finally:
        db.close()


def heartbeat(root, owner, approvals):
    now = time.time()
    with connection(root) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT owner,until FROM lease WHERE id=1').fetchone()
        if row and row[0] != owner and row[1] > now:
            raise ValueError('此 Claude 事件目录正在被另一个监控器使用')
        db.execute('INSERT OR REPLACE INTO lease VALUES (1,?,?,?)', (owner, now+LEASE_SECONDS, int(approvals)))


def release(root, owner):
    with connection(root) as db:
        count = db.execute('DELETE FROM lease WHERE owner=?', (owner,)).rowcount
        if count:
            db.execute("UPDATE requests SET state='expired' WHERE state IN ('pending','decided')")


def active(root):
    if not (Path(root)/'events.sqlite3').is_file():
        return False, False
    with connection(root) as db:
        row = db.execute('SELECT until,approvals FROM lease WHERE id=1').fetchone()
    return bool(row and row[0] > time.time()), bool(row and row[0] > time.time() and row[1])


def append_event(event, root):
    value = dict(event)
    value.setdefault('event_id', uuid4().hex)
    value.setdefault('timestamp', time.time())
    body = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if len(body.encode('utf-8')) > 128*1024:
        raise ValueError('Claude 监控事件过大')
    with connection(root) as db:
        db.execute('INSERT OR IGNORE INTO events(id,at,body) VALUES(?,?,?)', (value['event_id'], time.time(), body))
    return value


def read_events(root, after=0):
    with connection(root) as db:
        rows = db.execute('SELECT seq,at,body FROM events WHERE seq>? ORDER BY seq LIMIT 512', (after,)).fetchall()
    return [(seq, at, json.loads(body)) for seq, at, body in rows]


def new_request(root, event, seconds):
    ident, revision, now = uuid4().hex, uuid4().hex, time.time()
    with connection(root) as db:
        db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)',
                   (ident, revision, event['session_id'], event.get('prompt_id',''), 'pending', now+seconds, now, ''))
    return dict(id=ident, revision=revision, expires_at=now+seconds)


def requests(root):
    now = time.time()
    with connection(root) as db:
        db.execute("UPDATE requests SET state='expired' WHERE state IN ('pending','decided') AND (deadline<=? OR heartbeat<?)", (now, now-REQUEST_LEASE))
        return {row[0]: dict(revision=row[1], state=row[2], deadline=row[3]) for row in
                db.execute('SELECT id,revision,state,deadline FROM requests WHERE deadline>?', (now-120,))}


def decide(root, ident, revision, behavior):
    if behavior not in ('allow', 'deny'):
        raise ValueError('无效授权决定')
    now = time.time()
    with connection(root) as db:
        count = db.execute("UPDATE requests SET state='decided',decision=? WHERE id=? AND revision=? AND state='pending' AND deadline>? AND heartbeat>?",
                           (behavior, ident, revision, now, now-REQUEST_LEASE)).rowcount
        if count != 1:
            raise ValueError('此授权已处理或过期，请在 Claude 中核对')


def consume(root, ident):
    now = time.time()
    with connection(root) as db:
        db.execute("UPDATE requests SET heartbeat=? WHERE id=? AND state IN ('pending','decided')", (now, ident))
        row = db.execute('SELECT state,decision,deadline FROM requests WHERE id=?', (ident,)).fetchone()
        if row and row[0] == 'decided' and row[2] > now:
            count = db.execute("UPDATE requests SET state='consumed' WHERE id=? AND state='decided'", (ident,)).rowcount
            return row[1] if count else None
    return None


def expire(root, ident):
    with connection(root) as db:
        db.execute("UPDATE requests SET state='expired' WHERE id=? AND state IN ('pending','decided')", (ident,))


def expire_session(root, session, prompt=None):
    with connection(root) as db:
        sql = "UPDATE requests SET state='expired' WHERE session=? AND state IN ('pending','decided')"
        args = [session]
        if prompt is not None:
            sql += ' AND prompt=?'
            args.append(prompt)
        db.execute(sql, args)


def prune(root):
    with connection(root) as db:
        db.execute('DELETE FROM requests WHERE deadline<?', (time.time()-86400,))
        # Transport retention is bounded; permanent display receipts live elsewhere.
        db.execute('DELETE FROM events WHERE seq < (SELECT COALESCE(MAX(seq),0)-20000 FROM events) AND at<?', (time.time()-86400,))
