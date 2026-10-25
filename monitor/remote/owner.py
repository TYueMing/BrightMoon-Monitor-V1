"""Network-to-owner handoff; SQLite/Tk objects stay on their owning thread."""
from concurrent.futures import Future
import queue
import time
import sqlite3
from pathlib import Path
from ..multi_diverse.protocol import digest
from ..src.task_identity import task_identity


class AgentGate:
    """Shared one-shot claims across phone and peer clients, including restarts.

    Only request-bound decisions are claimed. Once adapter dispatch starts, an
    uncertain outcome is not retried until the source issues a new request.
    """
    def __init__(self, root):
        self.path=Path(root)/'remote'/'agent-claims.sqlite3'

    def claim(self, target, payload):
        if not payload.get('confirmation_id'): return
        key=digest([target.get('app_id'),task_identity(target),payload['confirmation_id'],payload.get('expected_revision')])
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=2)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY, created REAL)')
                if db.execute('SELECT count(*) FROM claims').fetchone()[0]>=100000:raise ValueError('授权记录已达容量，需要维护后继续')
                try:db.execute('INSERT INTO claims VALUES (?,?)',(key,time.time()))
                except sqlite3.IntegrityError:raise ValueError('本次请求已有设备提交决定，请核对来源状态') from None
        finally:db.close()


def execute_agent(hub, action, target, payload, authorized, gate=None):
    """Both peer and phone paths use the full-generation SourceHub dispatch."""
    if not authorized():
        raise ValueError('权限已撤销或操作已过期')
    if gate is not None: gate.claim(target,payload)
    if not authorized(): raise ValueError('权限已撤销或操作已过期')
    hub.send_target(action, target, payload)
    return dict(status='accepted', message='已提交来源，等待实际状态确认')


class OwnerBridge:
    def __init__(self):
        self.queue = queue.Queue(maxsize=32)
        self.closed = False

    def submit(self, operation, payload, authorized):
        future = Future()
        if self.closed:
            future.set_exception(ValueError('应用正在退出'))
            return future
        try:
            self.queue.put_nowait((operation, payload, authorized, time.monotonic()+8, future))
        except queue.Full:
            future.set_exception(ValueError('操作过多，请稍后重试'))
        return future

    def drain(self, app):
        # Bounded work per Tk tick keeps task polling and animations responsive.
        for _ in range(8):
            try: operation, payload, authorized, deadline, future = self.queue.get_nowait()
            except queue.Empty: break
            if not future.set_running_or_notify_cancel(): continue
            def allowed(a=authorized, d=deadline):
                return not self.closed and time.monotonic() <= d and a()
            def perform(op=operation, data=payload, check=allowed, result=future):
                try:
                    if not check(): raise ValueError('权限已撤销或操作已过期')
                    if op == 'agent':
                        value = execute_agent(app.hub, data['operation'], data['target'], data['payload'], check, getattr(app,'agent_gate',None))
                    elif op == 'list_tasks':
                        value = {'tasks': app.tasks.store.list()}
                    elif op == 'save_task':
                        value = {'task': app.tasks.store.save(data['value'], data.get('task_id'),
                                  expected_revision=data.get('expected_revision'))}
                    elif op == 'delete_task':
                        app.tasks.store.delete(data['task_id'], expected_revision=data['expected_revision'])
                        value = {}
                    elif op == 'reminder':
                        app.tasks.store.act(data['id'], data['cycle'], data['action'], command_id=data['command_id'])
                        value = {}
                    else: raise ValueError('不支持此操作')
                    if op not in ('agent', 'list_tasks'): app.tasks_changed()
                    result.set_result(value)
                except Exception as error: result.set_exception(error)
            if operation == 'agent': app.peer_executor.submit(perform)
            else: perform()

    def close(self):
        self.closed = True
        while not self.queue.empty():
            try: self.queue.get_nowait()[-1].cancel()
            except queue.Empty: break
